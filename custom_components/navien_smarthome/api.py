"""나비엔 스마트 REST 클라이언트.

인증 흐름은 두 단계다.

1. `member.naviensmartcontrol.com/member/login` 폼 POST — 쿠키를 물고 리다이렉트를
   따라간 뒤 HTML 의 `var message = {...}` 에서 토큰을 긁는다.
2. `POST /users/secured-sign-in` — home 목록과 **AWS IoT 임시 자격증명**을 받는다.

`accountSeq` 는 1단계 응답의 `userSeq` 이고, 2단계 응답의 `userInfo.userSeq` 와는
다른 값이다. 헷갈리기 쉬우니 이름을 구분해 둔다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    AIRONE_LEGACY_TOPIC_FMT,
    AIRONE_TOPIC_FMT,
    LEGACY_CONTROLLER_TO_REQUEST,
    LEGACY_RUNNING_TO_REQUEST,
    API_URL,
    CODE_NOT_AUTHORIZED,
    CODE_SUCCESS,
    CODE_TOKEN_EXPIRED,
    LOGIN_URL,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

_FAIL_POPUP = 'id="loginFailPopup" style="display:none;"'
_MISMATCH = "입력한 정보가 일치하지 않습니다."
_ATTEMPT_RE = re.compile(r"현재 (\d)회")
_MESSAGE_MARKER = "var message = "


def extract_airs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`/air-sensor` 응답에서 공기질 항목 목록을 꺼낸다.

    실기기 제보로 확인한 형태:
        `data.sensorList[]` → `{ zoneId, updateTime, airMonitor{}, airs[] }`

    처음에 `data.airs` 로 짐작했다가 **값을 하나도 못 읽었다.** 에어모니터가 붙어
    있는데도 공기질 센서가 안 생기던 원인이다. 존이 여럿일 수 있어 목록을 모두
    훑고, 앞선 짐작도 폴백으로 남긴다.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    airs: list[dict[str, Any]] = []
    for entry in data.get("sensorList") or []:
        if isinstance(entry, dict) and isinstance(entry.get("airs"), list):
            airs.extend(item for item in entry["airs"] if isinstance(item, dict))
    if airs:
        return airs

    if isinstance(data.get("airs"), list):
        return [item for item in data["airs"] if isinstance(item, dict)]
    for value in data.values():
        if isinstance(value, dict) and isinstance(value.get("airs"), list):
            return [item for item in value["airs"] if isinstance(item, dict)]
    return []


class NavienSmartError(Exception):
    """통합 내부 공통 예외."""


class NavienSmartAuthError(NavienSmartError):
    """자격증명이 잘못되었거나 세션이 무효해진 경우."""


def _legacy_request(desired: dict[str, Any]) -> dict[str, Any]:
    """신형 `desired` 를 구세대 `request` 로 옮긴다.

    호출자는 세대를 모르고 `roomController` 하나만 만든다. 봉투 차이는 전송
    직전 여기서만 흡수한다 — 세대 분기가 모델까지 번지지 않게 한다.
    """
    controller = desired.get("roomController")
    if not isinstance(controller, dict):
        return {}
    request: dict[str, Any] = {}
    running = controller.get("running")
    if running is not None:
        # 구세대는 운전 값이 반대다. 안 뒤집으면 전원이 거꾸로 나간다.
        request["power"] = LEGACY_RUNNING_TO_REQUEST.get(running, running)
    for source, target in LEGACY_CONTROLLER_TO_REQUEST.items():
        if source in controller:
            request[target] = controller[source]
    return request


class NavienSmartApiError(NavienSmartError):
    """서버가 성공이 아닌 code 를 돌려준 경우."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class AwsCredentials:
    """AWS IoT 접속용 임시 자격증명."""

    access_key_id: str
    secret_key: str
    session_token: str

    @classmethod
    def from_auth_info(cls, info: dict[str, Any]) -> AwsCredentials | None:
        try:
            return cls(info["accessKeyId"], info["secretKey"], info["sessionToken"])
        except KeyError:
            return None


@dataclass(slots=True)
class NavienSmartSession:
    """로그인 결과. `home_seq` 는 사용자가 고른 값으로 덮어쓸 수 있다."""

    access_token: str
    refresh_token: str | None
    user_id: str
    account_seq: int
    user_seq: int
    homes: list[dict[str, Any]]
    aws: AwsCredentials | None


class NavienSmartApi:
    """REST 호출을 담당한다. MQTT 는 `mqtt.py` 가 맡는다."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        self._http = session
        self._username = username
        self._password = password
        self._session: NavienSmartSession | None = None
        self._lock = asyncio.Lock()

    @property
    def session(self) -> NavienSmartSession | None:
        return self._session

    # -- 인증 --------------------------------------------------------------

    async def async_login(self) -> NavienSmartSession:
        """1·2단계를 모두 수행하고 세션을 갈아끼운다."""
        async with self._lock:
            login = await self._async_form_login()
            data = await self._async_secured_sign_in(
                login["accessToken"], login["loginId"], login["userSeq"]
            )

            homes = data.get("home") or []
            if not homes:
                raise NavienSmartAuthError("계정에 등록된 home 이 없습니다.")

            self._session = NavienSmartSession(
                access_token=login["accessToken"],
                refresh_token=login.get("refreshToken"),
                user_id=login["loginId"],
                account_seq=login["userSeq"],
                user_seq=data["userInfo"]["userSeq"],
                homes=homes,
                aws=AwsCredentials.from_auth_info(data.get("authInfo") or {}),
            )
            _LOGGER.debug(
                "로그인 완료: userSeq=%s home %s개",
                self._session.user_seq,
                len(homes),
            )
            return self._session

    async def _async_form_login(self) -> dict[str, Any]:
        """폼 로그인. 쿠키 세션이 필요하므로 전용 ClientSession 을 받아 쓴다."""
        try:
            async with self._http.post(
                f"{LOGIN_URL}/member/login",
                data={"username": self._username, "password": self._password},
                headers={
                    "User-Agent": USER_AGENT,
                    "Origin": LOGIN_URL,
                    "Referer": f"{LOGIN_URL}/member/login",
                },
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                html = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NavienSmartError(f"로그인 요청 실패: {err}") from err

        if _FAIL_POPUP in html:
            raise self._auth_error_from_html(html)

        if "passwordChg" in html:
            # 계정 상태를 바꾸는 요청(`/pwchgLate`)은 통합이 대신 하지 않는다.
            raise NavienSmartAuthError(
                "서버가 비밀번호 변경을 요구합니다. 앱이나 웹에서 먼저 처리해 주세요."
            )

        token_json = self._extract_message_json(html)
        if token_json is None:
            raise NavienSmartAuthError("로그인 응답에서 토큰을 찾지 못했습니다.")
        return token_json

    @staticmethod
    def _auth_error_from_html(html: str) -> NavienSmartAuthError:
        if _MISMATCH not in html:
            return NavienSmartAuthError("아이디가 올바르지 않습니다.")
        match = _ATTEMPT_RE.search(html)
        if match:
            return NavienSmartAuthError(
                f"비밀번호가 올바르지 않습니다. 5회 실패하면 재설정이 필요합니다 "
                f"(현재 {match.group(1)}회)."
            )
        return NavienSmartAuthError(
            "비밀번호가 올바르지 않습니다. 재설정이 필요할 수 있습니다."
        )

    @staticmethod
    def _extract_message_json(html: str) -> dict[str, Any] | None:
        for line in html.splitlines():
            if _MESSAGE_MARKER not in line:
                continue
            start = line.find("{")
            end = line.rfind("}")
            if start == -1 or end <= start:
                continue
            try:
                return json.loads(line[start : end + 1])
            except json.JSONDecodeError:
                continue
        return None

    async def _async_secured_sign_in(
        self, access_token: str, user_id: str, account_seq: int
    ) -> dict[str, Any]:
        payload = await self._async_request(
            "POST",
            "/users/secured-sign-in",
            token=access_token,
            json_body={"userId": user_id, "accountSeq": account_seq},
        )
        data = payload.get("data")
        if not data:
            raise NavienSmartAuthError("secured-sign-in 응답에 data 가 없습니다.")
        return data

    async def async_refresh_aws_credentials(self) -> AwsCredentials | None:
        """AWS 자격증명을 다시 받는다.

        `/auth/token/refresh` 는 accessToken 만 주고 AWS 자격증명을 주지 않는다.
        따라서 `secured-sign-in` 을 다시 부르는 것이 유일한 경로다 — 실측 확인.
        """
        session = self._require_session()
        data = await self._async_secured_sign_in(
            session.access_token, session.user_id, session.account_seq
        )
        session.aws = AwsCredentials.from_auth_info(data.get("authInfo") or {})
        return session.aws

    # -- 요청 --------------------------------------------------------------

    def _require_session(self) -> NavienSmartSession:
        if self._session is None:
            raise NavienSmartError("먼저 async_login() 을 호출해야 합니다.")
        return self._session

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        raw_body: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": token, "User-Agent": USER_AGENT}
        data: bytes | None = None
        if raw_body is not None:
            headers["Content-Type"] = "application/json"
            data = raw_body.encode()
        elif json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode()

        try:
            async with self._http.request(
                method,
                f"{API_URL}{path}",
                params=params,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            # **`TimeoutError` 를 빠뜨리면 아무 기록도 남지 않는다.**
            # `aiohttp.ClientError` 의 하위가 아니라(`OSError` 계열) 여기서
            # 걸리지 않고 통째로 빠져나가, 실패 횟수도 로그도 남지 않았다.
            raise NavienSmartError(f"{path} 요청 실패: {err}") from err

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise NavienSmartError(f"{path} 응답이 JSON 이 아닙니다.") from err

        # **JSON 이라고 다 객체는 아니다.** `null` · 배열 · 숫자도 통과한다.
        # 그대로 `.get` 을 부르면 `AttributeError` 가 나는데, 그건 우리 예외가
        # 아니라 실패 횟수에도 로그에도 안 남고 갱신만 조용히 멈춘다.
        if not isinstance(payload, dict):
            raise NavienSmartError(
                f"{path} 응답이 객체가 아닙니다 ({type(payload).__name__})."
            )

        code = payload.get("code")
        if code == CODE_SUCCESS:
            return payload
        raise NavienSmartApiError(code, payload.get("msg") or f"{path} 실패 (code={code})")

    async def _async_authed_request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        """토큰 만료·세션 탈취를 만나면 한 번 재로그인하고 재시도한다.

        계정당 세션이 하나뿐이라, 사용자가 앱을 열면 `404` 가 온다. 흔한 일이므로
        조용히 복구한다.
        """
        session = self._require_session()
        try:
            return await self._async_request(method, path, token=session.access_token, **kwargs)
        except NavienSmartApiError as err:
            if err.code not in (CODE_TOKEN_EXPIRED, CODE_NOT_AUTHORIZED):
                raise
            _LOGGER.debug("세션 무효(code=%s) — 재로그인 후 재시도", err.code)
            home_seq = session.homes[0].get("homeSeq") if session.homes else None
            refreshed = await self.async_login()
            # 사용자가 고른 home 을 유지한다.
            if home_seq is not None:
                refreshed.homes.sort(key=lambda h: h.get("homeSeq") != home_seq)
            return await self._async_request(
                method, path, token=refreshed.access_token, **kwargs
            )

    # -- 기기 --------------------------------------------------------------

    async def async_get_devices(self, home_seq: int) -> list[dict[str, Any]]:
        session = self._require_session()
        payload = await self._async_authed_request(
            "GET",
            "/devices",
            params={"homeSeq": home_seq, "userSeq": session.user_seq},
        )
        return (payload.get("data") or {}).get("devices") or []

    async def async_control(
        self,
        home_seq: int,
        device: dict[str, Any],
        desired: dict[str, Any],
    ) -> None:
        """`desired` 를 shadow 로 중계한다.

        `event.modelCode` 는 모든 명령에 붙는다. `beep` 는 붙이지 않는다 —
        앱은 2024년 이후 모델에만 붙이고, 없어도 동작하는 것을 실측으로 확인했다.
        """
        session = self._require_session()
        device_seq = device["deviceSeq"]
        topic = f"$aws/things/{device['deviceId']}/shadow/name/status/update"

        body_obj = {
            "serviceCode": device["serviceCode"],
            "topic": "\x00TOPIC\x00",
            "payload": {
                "state": {
                    "desired": {
                        "event": {"modelCode": int(device["modelCode"])},
                        **desired,
                    }
                }
            },
        }
        # 앱은 topic 의 '/' 를 '\/' 로 이스케이프해 보낸다. 서버가 까다로울 수 있어 맞춘다.
        raw = json.dumps(body_obj, ensure_ascii=False).replace(
            '"\\u0000TOPIC\\u0000"', json.dumps(topic).replace("/", "\\/")
        )

        _LOGGER.debug("제어 전송 deviceSeq=%s desired=%s", device_seq, desired)
        await self._async_authed_request(
            "POST",
            f"/devices/{device_seq}/control",
            params={"homeSeq": home_seq, "userSeq": session.user_seq},
            raw_body=raw,
        )

    async def async_request_shadow(
        self, home_seq: int, device: dict[str, Any]
    ) -> None:
        """섀도우에 **저장된 마지막 상태**를 달라고 한다.

        AWS 섀도우는 기기가 마지막으로 보고한 문서를 서버에 들고 있다. 여기에
        빈 본문을 보내면 그 문서를 `.../status/get/accepted` 로 돌려준다.

        **읽기다. 아무것도 바꾸지 않는다.** `desired` 를 쓰는 기존 초기 요청과
        달리 섀도우에 흔적을 남기지 않는다.

        **꺼져 있는 기기도 답한다** — 기기가 아니라 서버가 답하기 때문이다.
        그래서 매트를 붙인 직후, 기기가 아직 아무것도 안 보낸 상태에서도
        설정값을 바로 얻는다. 그게 없으면 `climate` 가 온도를 실을 수 없어
        「보낼 구역 값이 없습니다」로 막힌다.

        **앱은 이 토픽을 보내지 않는다.** 다만 `status/get/accepted` 를 처리하는
        코드는 있고, 실기기 두 대(온라인·오프라인)로 서버가 받아주는 것을
        확인했다 — `code=200` 과 함께 응답이 왔다.
        """
        session = self._require_session()
        device_seq = device["deviceSeq"]
        topic = f"$aws/things/{device['deviceId']}/shadow/name/status/get"

        body_obj = {
            "serviceCode": device["serviceCode"],
            "topic": "\x00TOPIC\x00",
            # **빈 본문이 규격이다.** 값을 넣으면 조회가 아니게 된다.
            "payload": {},
        }
        raw = json.dumps(body_obj, ensure_ascii=False).replace(
            '"\\u0000TOPIC\\u0000"', json.dumps(topic).replace("/", "\\/")
        )

        _LOGGER.debug("섀도우 조회 deviceSeq=%s", device_seq)
        await self._async_authed_request(
            "POST",
            f"/devices/{device_seq}/control",
            params={"homeSeq": home_seq, "userSeq": session.user_seq},
            raw_body=raw,
        )

    # -- 에어원 ------------------------------------------------------------

    async def async_airone_request(
        self,
        home_seq: int,
        device_seq: int,
        service_code: int,
        model_code: str,
        physical_device_id: str,
        command: str,
        client_id: str,
        desired: dict[str, Any] | None = None,
        legacy: bool = False,
    ) -> None:
        """에어원 명령을 중계한다.

        매트와 **봉투가 다르다.** 매트는 최상위에 `topic` 하나를 두고
        `payload.state.desired` 를 넣지만, 에어원은 요청·응답 토픽을 봉투 안에 넣고
        `sessionId` 로 짝을 맞춘다 (`AironePubComm`).

        `desired` 가 `None` 이면 상태 조회다 — `state` 를 아예 넣지 않는다.
        """
        session = self._require_session()
        topic_fmt = AIRONE_LEGACY_TOPIC_FMT if legacy else AIRONE_TOPIC_FMT
        topic = topic_fmt.format(
            model_code=model_code, device_id=physical_device_id, command=command
        )
        payload: dict[str, Any] = {
            "clientId": client_id,
            # 앱은 밀리초 epoch 를 문자열로 넣는다. 서버가 응답을 짝지을 때 쓴다.
            "sessionId": str(int(time.time() * 1000)),
            "requestTopic": topic,
            "responseTopic": f"{topic}/res",
        }
        if desired is not None:
            if legacy:
                payload["request"] = _legacy_request(desired)
            else:
                payload["state"] = {"desired": desired}

        body_obj = {"serviceCode": service_code, "payload": payload}
        # 매트와 같은 이유로 토픽의 '/' 를 이스케이프한다.
        # **본문 전체를 치환하지 않는다** — `desired` 는 호출자가 만든 값이라
        # 나중에 '/' 가 들어오면 조용히 망가진다. 토픽 두 개만 정확히 바꾼다.
        raw = json.dumps(body_obj, ensure_ascii=False)
        for value in (payload["responseTopic"], topic):
            quoted = json.dumps(value)
            raw = raw.replace(quoted, quoted.replace("/", "\\/"))

        _LOGGER.debug(
            "에어원 전송 deviceSeq=%s command=%s desired=%s", device_seq, command, desired
        )
        await self._async_authed_request(
            "POST",
            f"/devices/{device_seq}/control",
            params={"homeSeq": home_seq, "userSeq": session.user_seq},
            raw_body=raw,
        )

    async def async_get_air_sensor(
        self, home_seq: int, device_seq: int
    ) -> list[dict[str, Any]]:
        """공기질 값을 읽는다.

        상태 메시지에는 센서 **종류**만 있고 값이 없다 — 값은 이 엔드포인트에만 있다.
        """
        session = self._require_session()
        payload = await self._async_authed_request(
            "GET",
            f"/devices/{device_seq}/air-sensor",
            params={"homeSeq": home_seq, "userSeq": session.user_seq},
        )
        return extract_airs(payload)
