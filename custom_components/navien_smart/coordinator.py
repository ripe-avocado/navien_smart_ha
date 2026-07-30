"""기기 목록(REST)과 실시간 상태(MQTT)를 합친다.

폴링 주기가 긴 이유는 게으름이 아니다 — 기기가 상태를 스스로 올리는 것을 실측으로
확인했으므로, 폴링은 **재접속 후 초기 동기화와 기기 목록 변화 감지**만 담당한다.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AwsCredentials,
    NavienSmartApi,
    NavienSmartAuthError,
    NavienSmartError,
)
from .const import (
    DOMAIN,
    OUT_OF_SCOPE_REASONS,
    REPORT_WANTED_NOTES,
    REPORT_WANTED_SERVICE_CODES,
    SERVICE_NAMES,
    SUPPORTED_SERVICE_CODES,
    TOPIC_PREFIX,
    UPDATE_INTERVAL_SECONDS,
)
from .models import NavienDevice
from .mqtt import NavienSmartMqtt

_LOGGER = logging.getLogger(__name__)


class NavienSmartCoordinator(DataUpdateCoordinator[dict[str, NavienDevice]]):
    """`data` 는 `deviceId` → `NavienDevice` 다."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: NavienSmartApi,
        home_seq: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            config_entry=entry,
        )
        self.api = api
        self.home_seq = home_seq
        # 지원하지 않는 기기까지 원본을 들고 있는다. 진단 내보내기에 필요하다 —
        # 환기청정·보일러 사용자가 제보할 때 이게 유일한 근거가 된다.
        self.raw_devices: list[dict[str, Any]] = []
        self.unsupported: list[dict[str, Any]] = []
        self._mqtt: NavienSmartMqtt | None = None
        self._skipped_logged: set[str] = set()

    # -- 수집 --------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, NavienDevice]:
        try:
            raw_devices = await self.api.async_get_devices(self.home_seq)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NavienSmartError as err:
            raise UpdateFailed(str(err)) from err

        previous = self.data or {}
        devices: dict[str, NavienDevice] = {}
        self.raw_devices = raw_devices
        self.unsupported = []

        for raw in raw_devices:
            if raw.get("serviceCode") not in SUPPORTED_SERVICE_CODES:
                self.unsupported.append(raw)
                self._log_unsupported(raw)
                continue

            device = NavienDevice.parse(raw)
            if device is None:
                self._log_skip(raw, "응답에서 기기를 해석하지 못했습니다")
                continue

            control = device.heat_control
            if control is None:
                self._log_skip(
                    raw, "functions.heatControl 이 없어 난방 제어를 만들지 않습니다"
                )
            elif not control.is_known:
                # 값 체계를 모르는 항목은 추측해서 명령을 보내지 않는다.
                self._log_skip(
                    raw,
                    f"heatControl.unit '{control.unit}' 은 확인된 값이 아닙니다. "
                    "난방 제어 엔티티를 만들지 않고 건너뜁니다",
                )

            # 이미 받아둔 실시간 상태를 잃지 않는다.
            if (old := previous.get(device.device_id)) is not None:
                device.reported = old.reported

            if device.is_four_season:
                self._log_four_season(device)

            devices[device.device_id] = device

        return devices

    def _log_unsupported(self, raw: dict[str, Any]) -> None:
        """지원하지 않는 기기를 만나면 이유를 알린다.

        조용히 버리면 사용자는 통합이 고장난 줄 안다. 제보를 받을 대상에만
        제보 경로를 안내하고, 범위 밖 기기에는 헛된 기대를 주지 않는다.
        """
        service_code = raw.get("serviceCode")
        key = f"{raw.get('deviceSeq')}:unsupported"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        name = SERVICE_NAMES.get(service_code, f"serviceCode {service_code}")

        if service_code in REPORT_WANTED_SERVICE_CODES:
            _LOGGER.warning(
                "%s 를 찾았습니다 (modelName=%s). 아직 지원하지 않습니다 — %s. "
                "지원을 원하시면 설정 → 기기 및 서비스 → 나비엔 스마트 → ⋮ → "
                "'진단 정보 다운로드' 로 받은 파일을 이슈에 붙여 주세요. "
                "기기ID·IP·MAC·별칭은 자동으로 가려집니다.",
                name,
                raw.get("modelName"),
                REPORT_WANTED_NOTES.get(service_code, "실기기 정보가 필요합니다"),
            )
            return

        _LOGGER.info(
            "%s (modelName=%s) 는 건너뜁니다 — %s.",
            name,
            raw.get("modelName"),
            OUT_OF_SCOPE_REASONS.get(service_code, "이 통합의 범위가 아닙니다"),
        )

    def _log_four_season(self, device: NavienDevice) -> None:
        """사계절 기기를 만나면 한 번만 알린다.

        난방은 그대로 쓸 수 있다. 냉방은 값 체계가 확인되지 않아 그 구간에서만
        제어를 비활성으로 둔다. 아래 값이 제보로 오면 냉방을 열 수 있다.
        """
        key = f"{device.device_seq}:four_season"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        cool = device.cool_control
        _LOGGER.warning(
            "사계절 모델을 찾았습니다 (%s, modelCode=%s). 난방은 지원하지만 "
            "냉방은 값 체계가 확인되지 않아 냉방 중에는 제어를 비활성으로 둡니다. "
            "제보해 주시면 냉방을 열 수 있습니다 — coolControl=%s, season=%s. "
            "'운전 상태' 센서의 속성에 같은 값이 있습니다.",
            device.nickname,
            device.model_code,
            cool.as_diagnostics() if cool else None,
            device.season,
        )

    def _log_skip(self, raw: dict[str, Any], reason: str) -> None:
        """건너뛴 내용을 설치 로그에 남긴다. 조용히 버리지 않는다."""
        key = f"{raw.get('deviceSeq')}:{reason}"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        _LOGGER.warning(
            "기기 건너뜀 (deviceSeq=%s, modelName=%s): %s",
            raw.get("deviceSeq"),
            raw.get("modelName"),
            reason,
        )

    # -- 실시간 ------------------------------------------------------------

    async def async_start_mqtt(self) -> None:
        prefixes = {
            prefix
            for device in (self.data or {}).values()
            if (prefix := TOPIC_PREFIX.get(device.service_code))
        }
        if not prefixes:
            _LOGGER.debug("구독할 기기가 없어 MQTT 를 시작하지 않습니다")
            return

        session = self.api.session
        self._mqtt = NavienSmartMqtt(
            self.hass,
            home_seq=self.home_seq,
            user_seq=session.user_seq if session else self.home_seq,
            topic_prefixes=prefixes,
            credentials_provider=self._async_aws_credentials,
            on_reported=self._handle_reported,
            on_subscribed=self._async_request_initial_state,
        )
        await self._mqtt.async_start()

    async def _async_request_initial_state(self) -> None:
        """켜져 있는 기기에 상태를 올려달라고 한다.

        shadow 이벤트는 **변화가 있을 때만** 온다. 그래서 구독만 해두면 아무 조작이
        없는 동안 상태가 비어 있다 — 엔티티가 계속 `unknown` 으로 남는다.

        제어 필드 없이 `event.modelCode` 만 담아 보내면 기기가 현재 상태를
        `reported` 로 올린다. 앱도 같은 방식을 쓴다.
        **설정을 바꾸지 않는다** — 보낼 값이 없기 때문이다.

        꺼져 있는 기기에는 보내지 않는다. 응답하지 않고 shadow 에만 쌓인다.
        """
        for device in (self.data or {}).values():
            if not device.available:
                _LOGGER.debug("%s 는 오프라인이라 초기 상태를 요청하지 않습니다", device.nickname)
                continue
            try:
                await self.api.async_control(self.home_seq, device.raw, {})
                _LOGGER.debug("%s 에 초기 상태를 요청했습니다", device.nickname)
            except NavienSmartError as err:
                _LOGGER.warning("%s 초기 상태 요청 실패: %s", device.nickname, err)

    async def async_stop_mqtt(self) -> None:
        if self._mqtt is not None:
            await self._mqtt.async_stop()
            self._mqtt = None

    @property
    def mqtt_connected(self) -> bool:
        return self._mqtt is not None and self._mqtt.connected

    async def _async_aws_credentials(self) -> AwsCredentials | None:
        """접속·재접속 시마다 새 자격증명을 받는다.

        `/auth/token/refresh` 는 AWS 자격증명을 주지 않으므로 `secured-sign-in` 을
        다시 부르는 것이 유일한 경로다.
        """
        try:
            return await self.api.async_refresh_aws_credentials()
        except NavienSmartAuthError:
            session = await self.api.async_login()
            return session.aws

    @callback
    def _handle_reported(self, device_id: str, reported: dict[str, Any]) -> None:
        """MQTT 로 들어온 `reported` 를 반영한다. HA 이벤트 루프에서 불린다."""
        devices = self.data or {}
        device = devices.get(device_id)
        if device is None:
            # 새로 등록된 기기일 수 있다. 다음 폴링에서 잡힌다.
            _LOGGER.debug("모르는 기기의 보고 무시: %s", device_id)
            return
        device.reported = reported
        self.async_set_updated_data(devices)

    # -- 제어 --------------------------------------------------------------

    async def async_send(self, device: NavienDevice, desired: dict[str, Any]) -> None:
        """명령을 보낸 뒤 낙관적 갱신은 하지 않는다.

        기기가 `reported` 를 올려줄 때까지 기다린다. 명령이 shadow 에 들어간 시점의
        이벤트(`reported` 없는 `/accepted`)를 상태로 쓰면 HA 가 기기보다 앞서 나간다.
        """
        try:
            await self.api.async_control(self.home_seq, device.raw, desired)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
