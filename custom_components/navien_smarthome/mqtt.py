"""AWS IoT MQTT-over-WebSocket 구독.

기기가 상태를 스스로 올린다 — 실측 확인. 그래서 폴링 대신 이 구독이 주 경로다.

구독 토픽은 `{homeSeq}/mate/+` 이고 실제로는 `{homeSeq}/mate/{deviceId}` 로 온다.
한 번의 변화에 shadow 이벤트가 최대 세 종류 오는데 **`/update/accepted` 중
`state.reported` 를 가진 것만** 쓴다. 나머지(`/delta`, `/documents`, 그리고
`reported` 없는 `/accepted`)를 반영하면 HA 가 기기보다 앞서 나간다.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import hmac
import json
import logging
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import get_default_context

from .api import AwsCredentials
from .const import (
    IOT_ENDPOINT,
    IOT_REGION,
    IOT_SERVICE,
    LEGACY_ACTUAL_FIELDS,
    LEGACY_EXTRA_FIELDS,
    LEGACY_RUNNING_TO_V2,
    LEGACY_STATUS_TO_CONTROLLER,
)

_LOGGER = logging.getLogger(__name__)

# **두 가지를 받는다.**
#
#   /update/accepted  기기가 상태를 올렸을 때
#   /get/accepted     우리가 섀도우에 저장된 문서를 요청했을 때
#
# 후자는 붙인 직후 한 번 쓴다. 기기가 꺼져 있어도 서버가 답하므로,
# 이게 없으면 기기가 스스로 뭔가 보낼 때까지 상태가 비어 있다.
_ACCEPTED_SUFFIXES = ("/update/accepted", "/get/accepted")
_RECONNECT_DELAYS = (5, 15, 30, 60, 120, 300)

# 에어원 메시지를 매트 메시지와 가르는 기준. 앱도 구독 토픽 문자열로 판별한다
# (`HomeViewModel` 의 `/airone/#` / `/mate/#` 분기).
AIRONE_PREFIX = "airone"


def _uri_encode(value: str) -> str:
    """SigV4 정규화 인코딩. unreserved 문자만 남긴다.

    `X-Amz-Credential` 의 '/' 가 `%2F` 로 가야 한다. `urlencode` 기본값은 '/' 를
    살려두므로 서명이 깨진다.
    """
    return urllib.parse.quote(value, safe="-_.~")


def build_signed_ws_path(creds: AwsCredentials, region: str = IOT_REGION) -> str:
    """AWS IoT WebSocket 용 SigV4 사전서명 경로를 만든다.

    보안 토큰은 **서명 계산 뒤에** 붙인다. AWS IoT 규칙이다.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{IOT_SERVICE}/aws4_request"

    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{creds.access_key_id}/{scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{_uri_encode(key)}={_uri_encode(value)}" for key, value in sorted(query.items())
    )
    empty_hash = hashlib.sha256(b"").hexdigest()
    canonical_request = "\n".join(
        [
            "GET",
            "/mqtt",
            canonical_query,
            f"host:{IOT_ENDPOINT}\n",
            "host",
            empty_hash,
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amzdate,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    key = f"AWS4{creds.secret_key}".encode()
    for part in (datestamp, region, IOT_SERVICE, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    token = urllib.parse.quote(creds.session_token, safe="")
    return (
        f"/mqtt?{canonical_query}"
        f"&X-Amz-Signature={signature}"
        f"&X-Amz-Security-Token={token}"
    )


def extract_reported(payload: bytes, topic: str) -> tuple[str, dict[str, Any]] | None:
    """쓸 이벤트만 통과시킨다.

    통과 조건이 두 겹이다 — shadow 토픽이 `/update/accepted` 나 `/get/accepted`
    이고, `state.reported` 가 있어야 한다. 반환값은 `(deviceId, reported)`.

    `/get/accepted` 응답에는 `desired` 와 `metadata` 도 함께 온다. **`reported`
    만 읽는다** — `desired` 는 「보낸 값」이지 기기가 확인한 값이 아니다.
    """
    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _LOGGER.debug("JSON 이 아닌 MQTT 메시지 무시: topic=%s", topic)
        return None

    shadow_topic = event.get("topic") or ""
    if not shadow_topic.endswith(_ACCEPTED_SUFFIXES):
        return None

    state = ((event.get("payload") or {}).get("state")) or {}
    reported = state.get("reported")
    if not isinstance(reported, dict):
        # 명령이 shadow 에 막 들어간 시점의 이벤트다. 기기는 아직 모른다.
        return None

    device_id = (reported.get("info") or {}).get("deviceId")
    if not device_id:
        # 토픽 마지막 조각이 deviceId 다 — `{homeSeq}/mate/{deviceId}`.
        device_id = topic.rsplit("/", 1)[-1]
    if not device_id:
        return None
    return device_id, reported


def extract_airone_reported(
    payload: bytes, topic: str, stats: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]] | None:
    """에어원 상태 메시지에서 `reported` 를 꺼낸다.

    매트와 봉투가 다르다 — shadow 가 아니므로 `/update/accepted` 도 없고,
    `state` 한 겹도 없다. `{topic, payload: {reported: {...}}}` 형태다
    (`AironeGetStatus.AironeStatusEachRoom`).
    """
    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _LOGGER.debug("JSON 이 아닌 에어원 메시지 무시: topic=%s", topic)
        _bump(stats, "dropped_not_json")
        return None
    if not isinstance(event, dict):
        _bump(stats, "dropped_not_json")
        return None

    inner = event.get("payload")
    reported = inner.get("reported") if isinstance(inner, dict) else None
    if reported is None and isinstance(inner, dict):
        # 구세대는 `reported` 겹이 없는 평평한 프레임이다.
        reported = normalize_legacy_status(inner)
        if reported is not None:
            _bump(stats, "legacy_normalized")
    if not isinstance(reported, dict):
        # 명령을 접수했다는 응답일 수 있다. 상태가 아니면 쓰지 않는다.
        _bump(stats, "dropped_no_reported")
        return None
    # `idu`(실내기)도 받는다. 올인원 룸콘은 룸콘과 실내기가 한 덩어리라 상태를
    # 이쪽으로 올릴 가능성이 있다 — `did` 에 이 필드가 실재한다.
    # 값을 해석하지는 않는다. 받아서 진단에 담아 어떤 모양인지 보고 판단한다.
    if not any(
        key in reported for key in ("roomController", "odu", "airMonitor", "idu")
    ):
        # **조용히 버리지 않는다.** 이걸 DEBUG 로 두었더니 기본 설치에서 아무 흔적이
        # 남지 않아, 상태가 안 오는 원인을 제보로도 가릴 수 없었다.
        # 키 이름만 남긴다 — 값은 남기지 않는다.
        _LOGGER.warning(
            "에어원 상태 메시지의 모양을 알지 못해 쓰지 못했습니다 "
            "(topic 끝=%s, 최상위 키=%s). 이 로그를 이슈에 붙여 주시면 "
            "바로 넓힐 수 있습니다.",
            topic.rsplit("/", 1)[-1],
            sorted(reported),
        )
        _bump(stats, "dropped_unknown_shape")
        if stats is not None:
            stats["last_unknown_shape_keys"] = sorted(reported)
        return None

    # `{homeSeq}/airone/{deviceId}` 의 마지막 조각이 기기목록의 deviceId 다.
    device_id = topic.rsplit("/", 1)[-1]
    if not device_id or device_id == AIRONE_PREFIX:
        controller = reported.get("roomController")
        if isinstance(controller, dict):
            device_id = str(controller.get("deviceId") or "")
    if not device_id:
        _bump(stats, "dropped_no_device_id")
        return None
    _bump(stats, "accepted")
    return device_id, reported


def normalize_legacy_status(payload: dict[str, Any]) -> dict[str, Any] | None:
    """구세대 평평한 상태 프레임을 신형 `reported` 모양으로 옮긴다.

    **가장자리에서만 바꾼다.** 모델과 엔티티는 신형 어휘 하나만 알면 되도록
    두는 편이, 세대마다 분기를 심는 것보다 검증된 신형 경로를 덜 흔든다.

    설정값을 읽는다 — 이유는 `LEGACY_STATUS_TO_CONTROLLER` 위의 표 참조.
    """
    if "isRunning" not in payload:
        return None

    controller: dict[str, Any] = {}
    running = LEGACY_RUNNING_TO_V2.get(payload.get("isRunning"))
    if running is not None:
        controller["running"] = running
    for source, target in LEGACY_STATUS_TO_CONTROLLER.items():
        if source in payload:
            controller[target] = payload[source]

    error_code = payload.get("errorCode")
    if error_code is not None:
        controller["error"] = {"code": error_code}

    # 실외기가 실제로 하는 일. 제어 상태로 쓰지 않고 진단에서만 본다 —
    # 조건에 따라 수시로 바뀌므로 이걸 모드로 읽으면 표시가 요동친다.
    actual = {key: payload[key] for key in LEGACY_ACTUAL_FIELDS if key in payload}
    reported: dict[str, Any] = {"roomController": controller}
    if actual:
        reported["legacyActual"] = actual

    # 신형에 없는 값들. 해석해서 제어에 쓰지 않고 진단으로만 내보낸다.
    extras = {
        key: payload[field]
        for field, (key, _label, _table) in LEGACY_EXTRA_FIELDS.items()
        if payload.get(field) is not None
    }
    if extras:
        reported["legacyExtras"] = extras
    return reported


def _bump(stats: dict[str, Any] | None, key: str) -> None:
    """집계만 올린다. 값은 담지 않는다.

    「안 온다」와 「와서 버린다」를 **로그 없이 통계정보만으로** 가리기 위한 것이다.
    로그를 켜서 붙여 달라고 하면 회신율이 크게 떨어진다.
    """
    if stats is None:
        return
    stats[key] = int(stats.get(key) or 0) + 1


class NavienSmartMqtt:
    """구독 전용 MQTT 클라이언트. 발행하지 않는다 (제어는 REST 로 간다)."""

    def __init__(
        self,
        hass: HomeAssistant,
        home_seq: int,
        user_seq: int,
        topic_prefixes: set[str],
        credentials_provider: Callable[[], Awaitable[AwsCredentials | None]],
        on_reported: Callable[[str, dict[str, Any]], None],
        on_subscribed: Callable[[], Awaitable[None]] | None = None,
        on_airone_reported: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._hass = hass
        # 수신·폐기 집계. 개인정보는 없다 — 개수와 키 이름뿐이다.
        self.stats: dict[str, Any] = {}
        self._home_seq = home_seq
        self._user_seq = user_seq
        self._prefixes = topic_prefixes or {"mate"}
        self._credentials_provider = credentials_provider
        self._on_reported = on_reported
        self._on_airone_reported = on_airone_reported
        # 구독이 붙은 뒤에 초기 상태를 요청해야 한다. 순서가 뒤바뀌면 응답을 놓친다.
        self._on_subscribed = on_subscribed
        self._client_id = ""
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._attempt = 0
        self.connected = False

    @property
    def topics(self) -> list[str]:
        # 앱과 같은 `#` 를 쓴다 (`HomeViewModel` 이 `/{prefix}/#` 로 구독한다).
        # 에어원 응답이 한 단계 더 깊게 올 수 있어 `+` 로는 놓친다.
        return [f"{self._home_seq}/{prefix}/#" for prefix in sorted(self._prefixes)]

    @property
    def client_id(self) -> str:
        """접속 중인 MQTT clientId. 에어원 제어 봉투에 넣어야 한다."""
        return self._client_id

    async def async_start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = self._hass.async_create_background_task(
                self._async_run(), name="navien_smart_mqtt"
            )

    async def async_stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._async_disconnect()

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        self.connected = False
        if client is None:
            return
        await self._hass.async_add_executor_job(self._disconnect_blocking, client)

    @staticmethod
    def _disconnect_blocking(client: Any) -> None:
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()

    async def _async_run(self) -> None:
        """접속을 유지한다. 끊기면 자격증명을 새로 받아 다시 붙는다."""
        while not self._stopping:
            try:
                await self._async_connect_once()
                # `connect()` 는 CONNACK 전에 반환한다. 여기서 기다리지 않으면
                # 아래 감시 루프가 `connected=False` 를 보고 즉시 빠져나가
                # 방금 만든 연결을 스스로 끊고 재접속을 반복한다.
                await self._async_wait_connected()
                self._attempt = 0

                # 구독이 붙은 뒤 초기 상태를 요청한다. shadow 이벤트는 변화가
                # 있을 때만 오므로, 이걸 안 하면 아무 조작이 없는 동안 상태가
                # 영원히 비어 있다.
                if self._on_subscribed is not None:
                    await self._on_subscribed()

                # 접속이 살아 있는 동안은 paho 스레드가 일한다. 끊김만 감시한다.
                while not self._stopping and self.connected:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - 어떤 실패든 재시도로 흡수한다
                _LOGGER.warning("MQTT 접속 실패: %s", err)

            if self._stopping:
                break
            await self._async_disconnect()
            delay = _RECONNECT_DELAYS[min(self._attempt, len(_RECONNECT_DELAYS) - 1)]
            self._attempt += 1
            _LOGGER.debug("%s초 후 MQTT 재접속", delay)
            await asyncio.sleep(delay)

    async def _async_wait_connected(self, timeout: float = 15.0) -> None:
        """CONNACK 을 기다린다. `_on_connect` 콜백이 `connected` 를 세운다."""
        deadline = timeout
        while deadline > 0:
            if self._stopping or self.connected:
                return
            await asyncio.sleep(0.2)
            deadline -= 0.2
        raise TimeoutError(f"{timeout}초 안에 MQTT CONNACK 이 오지 않았습니다.")

    async def _async_connect_once(self) -> None:
        creds = await self._credentials_provider()
        if creds is None:
            raise RuntimeError("AWS 자격증명을 받지 못했습니다.")

        import paho.mqtt.client as mqtt  # 지연 임포트 — HA 부팅을 막지 않는다

        # 앱이 쓰는 형식과 맞춘다 — `{uuid}-U{userSeq}`.
        # `homeSeq` 를 쓰던 것을 고쳤다. A/B 로 확인했을 때 둘 다 구독은 됐지만,
        # 서버 정책이 나중에 clientId 를 보게 되면 앱과 다른 쪽이 먼저 막힌다.
        client_id = f"{uuid.uuid4()}-U{self._user_seq}"
        # 에어원 제어 봉투에 이 값을 넣어야 서버가 응답을 되돌린다.
        self._client_id = client_id
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                transport="websockets",
            )
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=client_id, transport="websockets")

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # `ssl.create_default_context()` 는 인증서를 디스크에서 읽어 이벤트 루프를
        # 막는다. HA 가 부팅 때 만들어 캐시해 둔 컨텍스트를 쓴다.
        client.tls_set_context(get_default_context())
        client.ws_set_options(path=build_signed_ws_path(creds))

        self._client = client
        await self._hass.async_add_executor_job(self._connect_blocking, client)

    @staticmethod
    def _connect_blocking(client: Any) -> None:
        client.connect(IOT_ENDPOINT, 443, keepalive=60)
        client.loop_start()

    # -- paho 콜백. 별도 스레드에서 불린다 --------------------------------

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason: Any, *_: Any) -> None:
        code = getattr(reason, "value", reason)
        if code != 0:
            _LOGGER.warning("MQTT 접속 거부 (code=%s)", code)
            return
        self.connected = True
        for topic in self.topics:
            client.subscribe(topic, qos=0)
        _LOGGER.debug("MQTT 구독 시작: %s", ", ".join(self.topics))

    def _on_disconnect(self, _client: Any, _userdata: Any, *args: Any) -> None:
        self.connected = False
        _LOGGER.debug("MQTT 연결이 끊겼습니다 %s", args[:1])

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        # 구독 토픽으로 갈린다 — 앱도 같은 방식이다. 봉투가 완전히 달라서
        # 한 파서로 둘 다 다루면 한쪽이 조용히 버려진다.
        if f"/{AIRONE_PREFIX}/" in message.topic:
            _bump(self.stats, "airone_received")
            self._handle_airone_message(message)
            return

        _bump(self.stats, "mate_received")
        result = extract_reported(message.payload, message.topic)
        if result is None:
            # 버린 이벤트도 남긴다. 이게 없어서 "상태가 안 온다" 와 "와도 버린다" 를
            # 구별하지 못해 디버깅이 길어졌다.
            _LOGGER.debug("MQTT 이벤트 무시 (reported 없음): %s", message.topic)
            _bump(self.stats, "mate_dropped_no_reported")
            return
        device_id, reported = result
        _LOGGER.debug(
            "MQTT reported 수신: %s heater=%s",
            device_id,
            reported.get("heater"),
        )
        # paho 스레드에서 HA 상태를 직접 건드리면 안 된다.
        self._hass.loop.call_soon_threadsafe(self._on_reported, device_id, reported)

    def _handle_airone_message(self, message: Any) -> None:
        if self._on_airone_reported is None:
            _LOGGER.debug("에어원 메시지 무시 (처리기 없음): %s", message.topic)
            _bump(self.stats, "airone_dropped_no_handler")
            return
        airone_stats: dict[str, Any] = {}
        result = extract_airone_reported(message.payload, message.topic, airone_stats)
        for key, value in airone_stats.items():
            if key == "last_unknown_shape_keys":
                self.stats["airone_last_unknown_shape_keys"] = value
            else:
                _bump(self.stats, f"airone_{key}")
        if result is None:
            _LOGGER.debug("에어원 이벤트 무시 (reported 없음): %s", message.topic)
            return
        device_id, reported = result
        _LOGGER.debug(
            "에어원 reported 수신: %s roomController=%s",
            device_id,
            reported.get("roomController"),
        )
        self._hass.loop.call_soon_threadsafe(
            self._on_airone_reported, device_id, reported
        )
