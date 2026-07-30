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
from .const import IOT_ENDPOINT, IOT_REGION, IOT_SERVICE

_LOGGER = logging.getLogger(__name__)

_ACCEPTED_SUFFIX = "/update/accepted"
_RECONNECT_DELAYS = (5, 15, 30, 60, 120, 300)


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

    통과 조건이 두 겹이다 — shadow 토픽이 `/update/accepted` 이고,
    `state.reported` 가 있어야 한다. 반환값은 `(deviceId, reported)`.
    """
    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _LOGGER.debug("JSON 이 아닌 MQTT 메시지 무시: topic=%s", topic)
        return None

    shadow_topic = event.get("topic") or ""
    if not shadow_topic.endswith(_ACCEPTED_SUFFIX):
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
    ) -> None:
        self._hass = hass
        self._home_seq = home_seq
        self._user_seq = user_seq
        self._prefixes = topic_prefixes or {"mate"}
        self._credentials_provider = credentials_provider
        self._on_reported = on_reported
        # 구독이 붙은 뒤에 초기 상태를 요청해야 한다. 순서가 뒤바뀌면 응답을 놓친다.
        self._on_subscribed = on_subscribed
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._attempt = 0
        self.connected = False

    @property
    def topics(self) -> list[str]:
        return [f"{self._home_seq}/{prefix}/+" for prefix in sorted(self._prefixes)]

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
        result = extract_reported(message.payload, message.topic)
        if result is None:
            # 버린 이벤트도 남긴다. 이게 없어서 "상태가 안 온다" 와 "와도 버린다" 를
            # 구별하지 못해 디버깅이 길어졌다.
            _LOGGER.debug("MQTT 이벤트 무시 (reported 없음): %s", message.topic)
            return
        device_id, reported = result
        _LOGGER.debug(
            "MQTT reported 수신: %s heater=%s",
            device_id,
            reported.get("heater"),
        )
        # paho 스레드에서 HA 상태를 직접 건드리면 안 된다.
        self._hass.loop.call_soon_threadsafe(self._on_reported, device_id, reported)
