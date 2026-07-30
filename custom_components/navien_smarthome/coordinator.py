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
from .airone import AironeDevice
from .const import (
    AIRONE_CMD_CHANGE_MODE,
    AIRONE_CMD_POWER,
    AIRONE_CMD_STATUS,
    AIRONE_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    OUT_OF_SCOPE_REASONS,
    REPORT_WANTED_NOTES,
    REPORT_WANTED_SERVICE_CODES,
    SERVICE_AIRONE,
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
        # 에어원은 매트와 상태 체계가 달라 같은 dict 에 섞지 않는다. 검증이 끝난
        # 매트 경로를 건드리지 않는 것이 우선이다.
        self.airone: dict[str, AironeDevice] = {}
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

        previous_airone = self.airone
        airone: dict[str, AironeDevice] = {}

        for raw in raw_devices:
            service_code = raw.get("serviceCode")
            if service_code not in SUPPORTED_SERVICE_CODES:
                self.unsupported.append(raw)
                self._log_unsupported(raw)
                continue

            if service_code == SERVICE_AIRONE:
                parsed = self._parse_airone(raw, previous_airone)
                if parsed is not None:
                    airone[parsed.device_id] = parsed
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

        self.airone = airone
        self._tune_interval()
        await self._async_update_air_sensors()
        return devices

    def _tune_interval(self) -> None:
        """에어원이 있으면 폴링을 짧게 한다.

        매트 상태는 MQTT 로 오지만 **공기질은 REST 로만 읽을 수 있다.** 매트 기준
        주기(15분)로는 미세먼지 수치가 쓸모없어진다.
        """
        wanted = timedelta(
            seconds=(
                AIRONE_UPDATE_INTERVAL_SECONDS
                if self.airone
                else UPDATE_INTERVAL_SECONDS
            )
        )
        if self.update_interval != wanted:
            _LOGGER.debug("폴링 주기를 %s 로 바꿉니다", wanted)
            self.update_interval = wanted

    def _parse_airone(
        self, raw: dict[str, Any], previous: dict[str, AironeDevice]
    ) -> AironeDevice | None:
        """에어원 하나를 해석한다. 만들 수 없으면 이유를 로그에 남긴다."""
        device = AironeDevice.parse(raw)
        if device is None:
            self._log_skip(
                raw,
                "능력 메타데이터(did.roomController)가 없어 엔티티를 만들지 않습니다. "
                "제보해 주시면 넓힐 수 있습니다",
            )
            self.unsupported.append(raw)
            return None

        if not device.is_v2_generation:
            # 레거시 세대는 봉투와 토픽이 전혀 달라 같은 코드로 못 쏜다.
            self._log_skip(
                raw,
                f"modelCode {device.model_code} 는 구세대 통신을 씁니다. "
                "지금 구현은 신형(modelCode 1000 이상)만 다룹니다",
            )
            self.unsupported.append(raw)
            return None

        if not device.selectable_modes:
            self._log_skip(
                raw,
                "서버가 고를 수 있는 운전 모드를 알려주지 않아 모드 선택을 "
                "만들지 않습니다",
            )

        # 이미 받아둔 실시간 상태와 공기질을 잃지 않는다.
        if (old := previous.get(device.device_id)) is not None:
            device.reported = old.reported
            device.air_sensors = old.air_sensors
            device.sensor_kinds = old.sensor_kinds

        self._log_airone_unverified(device)
        return device

    def _log_airone_unverified(self, device: AironeDevice) -> None:
        """에어원 지원이 실기기 미검증임을 한 번만 알린다.

        규약은 앱에서 그대로 뽑았지만 실기기로 확인한 적이 없다. 조용히 켜두면
        사용자는 되는 줄 알고 자동화를 만든다.
        """
        key = f"{device.device_seq}:airone_unverified"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        _LOGGER.warning(
            "환기청정을 찾았습니다 (%s, modelCode=%s). 지원을 켰지만 **실기기로 "
            "검증하지 않았습니다** — 동작하지 않거나 값이 이상할 수 있습니다. "
            "결과가 어떻든 이슈로 알려 주시면 고칠 수 있습니다. 운전 모드 %d가지를 "
            "서버 메타데이터에서 찾았습니다.",
            device.nickname,
            device.model_code,
            len(device.selectable_modes),
        )

    async def _async_update_air_sensors(self) -> None:
        """공기질 값을 읽는다.

        상태 메시지에는 센서 종류만 있고 값이 없다 — 값은 `/air-sensor` 에만 있다.
        """
        for device in self.airone.values():
            if not device.available:
                continue
            try:
                airs = await self.api.async_get_air_sensor(
                    self.home_seq, device.device_seq
                )
            except NavienSmartError as err:
                _LOGGER.debug("%s 공기질 조회 실패: %s", device.nickname, err)
                continue
            unknown = device.set_air_sensors(airs)
            if unknown:
                self._log_skip(
                    device.raw,
                    "확인되지 않은 공기질 항목은 만들지 않습니다: "
                    + ", ".join(sorted(set(unknown))),
                )

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
                "지원을 원하시면 설정 → 기기 및 서비스 → 나비엔 스마트 → "
                "⋮ 메뉴의 '통계정보 다운로드' 를 이슈에 붙여 주세요. "
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
        _LOGGER.warning(
            "사계절 모델을 찾았습니다 (%s, modelCode=%s). 난방은 지원하지만 "
            "냉방은 값 체계가 확인되지 않아 냉방 중에는 제어를 비활성으로 둡니다. "
            "냉방을 열려면 제보가 필요합니다 — 매트를 냉방으로 켜둔 뒤 설정 → "
            "기기 및 서비스 → 나비엔 스마트 → ⋮ 메뉴의 '통계정보 다운로드' 를 "
            "이슈에 붙여 주세요. 필요한 값이 그 안에 들어 있습니다.",
            device.nickname,
            device.model_code,
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
        if self.airone and (prefix := TOPIC_PREFIX.get(SERVICE_AIRONE)):
            prefixes.add(prefix)
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
            on_airone_reported=self._handle_airone_reported,
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

        for airone in self.airone.values():
            if not airone.available:
                _LOGGER.debug("%s 는 오프라인이라 상태를 요청하지 않습니다", airone.nickname)
                continue
            try:
                await self._async_airone_request(airone, AIRONE_CMD_STATUS, None)
                _LOGGER.debug("%s 에 초기 상태를 요청했습니다", airone.nickname)
            except NavienSmartError as err:
                _LOGGER.warning("%s 초기 상태 요청 실패: %s", airone.nickname, err)

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

    @callback
    def _handle_airone_reported(self, device_id: str, reported: dict[str, Any]) -> None:
        """에어원 상태를 반영한다. HA 이벤트 루프에서 불린다.

        기기목록의 `deviceId` 와 `did.roomController.deviceId` 가 다를 수 있어
        양쪽으로 찾는다.
        """
        device = self.airone.get(device_id)
        if device is None:
            device = next(
                (d for d in self.airone.values() if d.physical_device_id == device_id),
                None,
            )
        if device is None:
            _LOGGER.debug("모르는 에어원의 보고 무시: %s", device_id)
            return
        device.reported = reported
        self.async_set_updated_data(self.data or {})

    # -- 제어 --------------------------------------------------------------

    async def _async_airone_request(
        self,
        device: AironeDevice,
        command: str,
        desired: dict[str, Any] | None,
    ) -> None:
        client_id = self._mqtt.client_id if self._mqtt is not None else ""
        await self.api.async_airone_request(
            self.home_seq,
            device_seq=device.device_seq,
            service_code=device.service_code,
            model_code=device.model_code,
            physical_device_id=device.physical_device_id,
            command=command,
            client_id=client_id,
            desired=desired,
        )

    async def async_airone_power(self, device: AironeDevice, turn_on: bool) -> None:
        try:
            await self._async_airone_request(
                device, AIRONE_CMD_POWER, device.build_power_desired(turn_on)
            )
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

    async def async_airone_mode(
        self,
        device: AironeDevice,
        mode: int,
        option: int,
        air_volume: int | None = None,
        humidity: int | None = None,
    ) -> None:
        desired = device.build_mode_desired(mode, option, air_volume, humidity)
        try:
            await self._async_airone_request(device, AIRONE_CMD_CHANGE_MODE, desired)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

    async def async_send(self, device: NavienDevice, desired: dict[str, Any]) -> None:
        """명령을 보낸 뒤 낙관적 갱신은 하지 않는다.

        기기가 `reported` 를 올려줄 때까지 기다린다. 명령이 shadow 에 들어간 시점의
        이벤트(`reported` 없는 `/accepted`)를 상태로 쓰면 HA 가 기기보다 앞서 나간다.
        """
        try:
            await self.api.async_control(self.home_seq, device.raw, desired)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
