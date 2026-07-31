"""기기 목록(REST)과 실시간 상태(MQTT)를 합친다.

폴링 주기가 긴 이유는 게으름이 아니다 — 기기가 상태를 스스로 올리는 것을 실측으로
확인했으므로, 폴링은 **재접속 후 초기 동기화와 기기 목록 변화 감지**만 담당한다.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AwsCredentials,
    NavienSmartApi,
    NavienSmartAuthError,
    NavienSmartError,
)
from homeassistant.helpers.storage import Store

from .airone import AironeDevice, _dig
from .const import (
    AIRONE_AIR_ERROR_LOG_EVERY,
    AIRONE_CMD_CHANGE_MODE,
    AIRONE_CMD_POWER,
    AIRONE_CMD_STATUS,
    AIRONE_READBACK_DELAY_SECONDS,
    AIRONE_SILENCE_CHECK_SECONDS,
    AIRONE_TOPIC_FMT,
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


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _key_map(raw: dict[str, Any], depth: int = 5) -> str:
    """응답의 키 구조만 문자열로. **값은 담지 않는다** — 로그에 개인정보가 남는다."""

    def walk(value: Any, level: int) -> Any:
        if not isinstance(value, dict) or level <= 0:
            return "..." if isinstance(value, dict) else type(value).__name__
        return {key: walk(inner, level - 1) for key, inner in value.items()}

    return str(walk(raw.get("Properties"), depth))


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
        # 구세대는 `remote/status` 요청에 답하지 않는다. 마지막으로 받은 상태를
        # 남겨 두었다가 시작할 때 되살린다 — 그러지 않으면 첫 조작 전까지
        # 전원·모드·풍량이 모두 비어 보인다.
        self._store: Store = Store(hass, 1, f"{DOMAIN}.airone_state")
        # 되살린 기기. 진단에서 「지금 값이 복원된 것인지」를 가릴 수 있어야 한다 —
        # 전원이 「켜짐」으로 보이는데 실제로 꺼져 있을 수 있다.
        self.restored_devices: set[str] = set()
        # 「상태가 안 온다」와 「와도 못 붙인다」를 진단만으로 가리기 위한 집계.
        # 개인정보는 없다 — 개수와 키 이름뿐이다.
        self.drop_counts: dict[str, int] = {"mate_no_device": 0, "airone_no_device": 0}
        self._mqtt: NavienSmartMqtt | None = None
        self._skipped_logged: set[str] = set()
        # **폴링이 돌기는 하는지**를 남긴다.
        #
        # 이슈 #1 에서 공기질 값이 열 시간 동안 그대로였는데, 조회 실패도 빈 응답도
        # 0 이었다. 「방이 조용했다」와 「우리가 아예 못 읽었다」가 같은 모양으로
        # 보였다 — 폴링 쪽 시각이 없어서다.
        self.poll_stamp: float | None = None
        self.poll_failures = 0

    # -- 수집 --------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, NavienDevice]:
        try:
            raw_devices = await self.api.async_get_devices(self.home_seq)
        except NavienSmartAuthError as err:
            self.poll_failures += 1
            raise ConfigEntryAuthFailed(str(err)) from err
        except NavienSmartError as err:
            self.poll_failures += 1
            raise UpdateFailed(str(err)) from err

        previous = self.data or {}
        devices: dict[str, NavienDevice] = {}
        self.raw_devices = raw_devices
        self.unsupported = []

        previous_airone = self.airone
        airone: dict[str, AironeDevice] = {}

        for raw in raw_devices:
            # 매트는 정수로 오는 것을 확인했다. 에어원도 그럴 거라 단정하지 않는다 —
            # 문자열로 오면 비교가 조용히 실패해 기기가 통째로 사라진다.
            service_code = _as_int(raw.get("serviceCode"))
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
            #
            # **기록도 함께 이어받는다.** 기기 객체는 폴링마다 새로 만든다.
            # 이어받을 것을 빠뜨리면 그 값이 조용히 0 으로 돌아간다 — 진단 기록이
            # 그렇게 매번 지워지고 있었다(v0.9.5).
            if (old := previous.get(device.device_id)) is not None:
                device.reported = old.reported
                device.command_log = old.command_log
                device.state_log = old.state_log

            if device.is_four_season:
                self._log_four_season(device)
            if device.has_unknown_season:
                self._log_unknown_season(device)

            devices[device.device_id] = device

        self.airone = airone
        self._tune_interval()
        await self._async_update_air_sensors()
        self.poll_stamp = time.monotonic()
        self.poll_failures = 0
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
                "응답에 deviceId·deviceSeq 가 없어 기기를 만들지 못했습니다 "
                f"(Properties 구조: {_key_map(raw)})",
            )
            self.unsupported.append(raw)
            return None

        if _as_int(device.model_code) is None:
            # 「구세대」로 뭉개면 안 된다. 값을 못 읽은 것과 구세대인 것은 다르다.
            self._log_skip(
                raw,
                f"modelCode '{device.model_code}' 를 숫자로 읽지 못해 세대를 "
                "가릴 수 없습니다. 이 로그를 제보해 주세요",
            )
            self.unsupported.append(raw)
            return None

        if not device.modes:
            # **기기는 만든다.** 전원·운전상태·오류는 상태 응답에서 오므로
            # 메타데이터가 없어도 쓸 수 있다. 고르는 엔티티만 빠진다.
            self._log_skip(
                raw,
                "능력 메타데이터를 찾지 못해 운전 모드·풍량·목표 습도는 만들지 "
                "않습니다. 전원과 상태 엔티티는 만듭니다 "
                f"(찾은 곳: Properties.data.did.reported / 실제 구조: {_key_map(raw)}). "
                "이 로그를 제보해 주시면 바로 넓힐 수 있습니다",
            )

        # 이미 받아둔 실시간 상태와 공기질을 잃지 않는다.
        #
        # **기록과 집계도 함께 이어받는다.** 이걸 빠뜨려서 v0.9.3 에 넣은 공기질
        # 감지가 통째로 헛돌았다 — 폴링마다 새 객체가 되니 실패 횟수가 3에 닿을
        # 수 없고, 15분 경고가 **한 번도 울릴 수 없었다.**
        if (old := previous.get(device.device_id)) is not None:
            device.reported = old.reported
            device.air_sensors = old.air_sensors
            device.sensor_kinds = old.sensor_kinds
            device.last_humidity = old.last_humidity
            device.command_log = old.command_log
            device.humidity_log = old.humidity_log
            device.air_sensor_stamp = old.air_sensor_stamp
            device.air_sensor_empty = old.air_sensor_empty
            device.air_sensor_errors = old.air_sensor_errors
            device.air_sensor_unchanged = old.air_sensor_unchanged

        self._log_airone_found(device)
        return device

    def _log_airone_found(self, device: AironeDevice) -> None:
        """에어원을 찾았다는 것을 한 번만 알린다.

        **v0.9.3 에서 `WARNING` 을 내렸다.** 「실기기로 검증하지 않았습니다 —
        동작하지 않거나 값이 이상할 수 있습니다」라고 찍고 있었다. 두 가지가 틀렸다.

        1. **사실이 아니다.** 상태·제어·목표 습도가 제보로 확인됐다
        2. **`WARNING` 은 문제가 있을 때만 쓴다.** HA 로그 화면은 기본으로
           경고 이상만 보여준다. 그래서 제보자가 이 줄을 오류로 알고 이슈에
           붙였다 — 아무 문제가 없는데 걱정을 만들었다

        모델은 계속 늘어난다. 그래서 검증된 모델 목록을 코드에 적지 않는다.
        무엇을 찾았는지만 남기고, 이상하면 알려 달라고 한다.
        """
        key = f"{device.device_seq}:airone_found"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        _LOGGER.info(
            "환기청정을 찾았습니다 (%s, modelCode=%s). 운전 모드 %d가지를 서버 "
            "정보에서 찾았습니다. 값이 앱과 다르거나 조작이 안 되면 이슈로 "
            "알려 주세요.",
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
                device.air_sensor_errors += 1
                # **조용히 넘기지 않는다.** 빈 응답으로 값을 지우지 않기로 한
                # 뒤로는 조회가 계속 실패해도 화면에 옛 값이 그대로 남는다.
                # 사용자는 「값이 앱과 다르다」로만 보게 되고 원인을 알 수 없다.
                if device.air_sensor_errors % AIRONE_AIR_ERROR_LOG_EVERY == 0:
                    _LOGGER.warning(
                        "%s 공기질을 %d회 연속 못 읽었습니다. 화면에 남아 있는 값은 "
                        "그 전에 받은 것입니다 (%s)",
                        device.nickname,
                        device.air_sensor_errors,
                        err,
                    )
                else:
                    _LOGGER.debug("%s 공기질 조회 실패: %s", device.nickname, err)
                continue
            device.air_sensor_errors = 0
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
        cool = device.cool_control
        _LOGGER.info(
            "사계절 모델을 찾았습니다 (%s, modelCode=%s). 냉방(COOL) 범위는 "
            "%s~%s 입니다. 앱에서 COOL 로 바꾸시면 HA 도 그 범위로 따라갑니다 — "
            "**냉방에서는 좌우가 같은 온도로 동작하므로** 어느 쪽을 조작해도 "
            "양쪽에 같은 값이 갑니다.",
            device.nickname,
            device.model_code,
            cool.range_min if cool else "?",
            cool.range_max if cool else "?",
        )

    def _log_unknown_season(self, device: NavienDevice) -> None:
        """`season` 이 우리가 아는 값이 아닐 때 한 번만 알린다.

        앱 상수는 WARM(0) / COOL(2) 둘뿐인데 규격표에는 `Cool+` 라는 이름도 있다.
        **다른 값이 오면 난방으로 두고 알린다** — 냉방 범위를 잘못 적용하는 것보다
        안전하다.
        """
        key = f"{device.device_seq}:season:{device.season}"
        if key in self._skipped_logged:
            return
        self._skipped_logged.add(key)
        _LOGGER.warning(
            "%s 의 season 값 %s 를 해석하지 못해 난방으로 다룹니다 "
            "(아는 값: 0 난방 / 2 냉방). 냉방 중이신데 이 로그가 보이면 "
            "이 줄과 통계정보를 이슈에 붙여 주세요 — 바로 넓힐 수 있습니다.",
            device.nickname,
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
                continue
            self._schedule_airone_silence_check(airone)

    async def async_stop_mqtt(self) -> None:
        if self._mqtt is not None:
            await self._mqtt.async_stop()
            self._mqtt = None

    @property
    def mqtt_connected(self) -> bool:
        return self._mqtt is not None and self._mqtt.connected

    @property
    def poll_age(self) -> float | None:
        """마지막으로 **끝까지 성공한** 폴링 뒤 흐른 초."""
        if self.poll_stamp is None:
            return None
        return round(time.monotonic() - self.poll_stamp, 1)

    @property
    def mqtt_stats(self) -> dict[str, Any]:
        """받은 개수·버린 개수. 진단에 담아 로그 없이도 가릴 수 있게 한다."""
        stats: dict[str, Any] = dict(self._mqtt.stats) if self._mqtt else {}
        stats.update(self.drop_counts)
        return stats

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
            self.drop_counts["mate_no_device"] += 1
            _LOGGER.debug("모르는 기기의 보고 무시: %s", device_id)
            return
        # **덮어쓰지 않는다.** 사계절 모델이 부분 응답을 보낸다 (`apply_reported`).
        device.apply_reported(reported)
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
            self.drop_counts["airone_no_device"] += 1
            _LOGGER.debug("모르는 에어원의 보고 무시: %s", device_id)
            return
        # **덮어쓰지 않는다.** 명령 응답은 부분 페이로드로 온다 (`apply_reported` 주석).
        device.apply_reported(reported)
        # 기기가 실제로 올린 값이 왔으니 더 이상 복원값이 아니다.
        self.restored_devices.discard(device.device_id)
        self._async_remember_state()
        self.async_set_updated_data(self.data or {})

    def _async_remember_state(self) -> None:
        """마지막 상태를 남긴다. 실패해도 동작을 막지 않는다."""
        snapshot = {
            device_id: device.reported
            for device_id, device in self.airone.items()
            if device.reported
        }
        if snapshot:
            self._store.async_delay_save(lambda: snapshot, 5)

    async def async_restore_state(self) -> None:
        """저장해 둔 마지막 상태를 되살린다.

        **되살린 값은 잠정이다.** 기기가 스스로 올리거나 조작을 하면 바로
        덮인다. 아무것도 안 보이는 것보다는 마지막으로 알던 값이 낫다.
        """
        try:
            stored = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - 저장소 문제로 통합을 막지 않는다
            _LOGGER.debug("에어원 상태 복원 실패: %s", err)
            return
        if not isinstance(stored, dict):
            return
        for device_id, reported in stored.items():
            device = self.airone.get(device_id)
            if device is not None and isinstance(reported, dict) and not device.reported:
                device.apply_reported(reported)
                self.restored_devices.add(device_id)
        if self.restored_devices:
            _LOGGER.debug(
                "에어원 %s대의 마지막 상태를 되살렸습니다. 기기가 새로 올리기 전까지는 "
                "잠정값입니다", len(self.restored_devices)
            )

    # -- 제어 --------------------------------------------------------------

    async def _async_airone_request(
        self,
        device: AironeDevice,
        command: str,
        desired: dict[str, Any] | None,
    ) -> None:
        client_id = self._mqtt.client_id if self._mqtt is not None else ""
        # 무엇을 보냈는지 남긴다. 진단에서 순서를 봐야 가릴 수 있는 문제가 있다.
        device.note_command(command, desired)
        await self.api.async_airone_request(
            self.home_seq,
            device_seq=device.device_seq,
            service_code=device.service_code,
            model_code=device.model_code,
            physical_device_id=device.physical_device_id,
            command=command,
            client_id=client_id,
            desired=desired,
            # 세대 차이는 전송 계층에서만 흡수한다. 위쪽은 세대를 모른다.
            legacy=not device.is_v2_generation,
        )

    async def async_airone_power(self, device: AironeDevice, turn_on: bool) -> None:
        try:
            await self._async_airone_request(
                device, AIRONE_CMD_POWER, device.build_power_desired(turn_on)
            )
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self._schedule_airone_readback(device)

    async def async_airone_mode(
        self,
        device: AironeDevice,
        mode: int,
        option: int,
        air_volume: int | None = None,
        humidity: int | None = None,
    ) -> None:
        # **목표 습도를 나중에 한 번 더 보내는 장치를 v0.9.1 에서 걷어냈다.**
        #
        # v0.9.0 에서 「모드에 들어간 뒤에 다시 보내면 되지 않을까」로 넣었다.
        # 실기기 제보로 두 가지가 드러났다.
        #
        # 1. **기기가 목표 습도를 상태로 돌려주지 않는다.** 관측 여덟 건이 모두
        #    비어 있었다. 그래서 「되돌려졌는지」를 판정할 수가 없고, 재전송은
        #    영원히 성공하지 못한 것으로 취급되어 모드를 바꿀 때마다 한 번 더
        #    나갔다
        # 2. **사용자 조작을 덮었다.** 판정에서 `mode` 만 비교하고 `option` 을
        #    빼먹어, 같은 제습 안에서 풍량만 바꾸면 8초 뒤에 되돌려 놓았다.
        #    제보 기록에 터보 → 기본풍량으로 끌려간 순간이 그대로 남았다
        #
        # 근거 없이 계속 쏘지 않는다. 습도는 모드 변경에 실어 한 번만 보내고,
        # 기기가 그것을 받는지 여부는 진단 기록으로 판단한다.
        desired = device.build_mode_desired(mode, option, air_volume, humidity)
        try:
            await self._async_airone_request(device, AIRONE_CMD_CHANGE_MODE, desired)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self._schedule_airone_readback(device)

    @callback
    def _schedule_airone_silence_check(self, device: AironeDevice) -> None:
        """상태를 요청했는데 끝내 안 오면 알린다.

        **조용한 실패가 가장 잡기 어렵다.** 요청은 성공(HTTP 200)했는데 응답이
        오지 않으면 엔티티가 영구히 「알 수 없음」으로 남고, 사용자는 통합이
        고장난 줄 안다. 어디까지 갔는지 로그에 남겨야 제보로 가릴 수 있다.
        """
        device_id = device.device_id

        async def _check(_now: Any) -> None:
            target = self.airone.get(device_id)
            if target is None or target.reported:
                return
            key = f"{target.device_seq}:silent"
            if key in self._skipped_logged:
                return
            self._skipped_logged.add(key)
            prefix = TOPIC_PREFIX.get(SERVICE_AIRONE)
            _LOGGER.warning(
                "%s 에 상태를 요청했지만 %d초 안에 응답이 오지 않았습니다. "
                "요청은 정상 전송됐습니다 — 보낸 곳: %s, 듣는 곳: %s/%s/#. "
                "엔티티가 「알 수 없음」으로 남습니다. 이 로그와 통계정보를 "
                "이슈에 붙여 주시면 원인을 좁힐 수 있습니다.",
                target.nickname,
                AIRONE_SILENCE_CHECK_SECONDS,
                AIRONE_TOPIC_FMT.format(
                    model_code=target.model_code,
                    device_id=target.physical_device_id,
                    command=AIRONE_CMD_STATUS,
                ),
                self.home_seq,
                prefix,
            )

        async_call_later(self.hass, AIRONE_SILENCE_CHECK_SECONDS, _check)

    @callback
    def _schedule_airone_readback(self, device: AironeDevice) -> None:
        """명령을 보낸 뒤 상태를 한 번 다시 물어본다.

        **낙관적 갱신을 하지 않는다.** 명령이 접수됐다고 UI 를 먼저 바꿔놓으면,
        기기가 거부했을 때 사용자는 "됐다가 되돌아간다" 를 겪는다 — 실패를
        성공처럼 보이게 하는 셈이다. 매트에서 같은 이유로 안 했다.

        대신 실제 상태를 다시 읽는다. 기기가 스스로 올려주는 것이 정상이지만,
        안 올려도 이 한 번으로 따라잡는다.
        """
        device_id = device.device_id

        async def _readback(_now: Any) -> None:
            target = self.airone.get(device_id)
            if target is None or not target.available:
                return
            try:
                await self._async_airone_request(target, AIRONE_CMD_STATUS, None)
            except NavienSmartError as err:
                _LOGGER.debug("%s 상태 재확인 실패: %s", target.nickname, err)

        async_call_later(self.hass, AIRONE_READBACK_DELAY_SECONDS, _readback)

    async def async_send(self, device: NavienDevice, desired: dict[str, Any]) -> None:
        """명령을 보낸 뒤 낙관적 갱신은 하지 않는다.

        기기가 `reported` 를 올려줄 때까지 기다린다. 명령이 shadow 에 들어간 시점의
        이벤트(`reported` 없는 `/accepted`)를 상태로 쓰면 HA 가 기기보다 앞서 나간다.
        """
        # 무엇을 보냈는지 남긴다. 냉방은 「보낸 값이 그대로 돌아오는가」를 봐야
        # 닫히는 구간이라 이 기록이 근거가 된다.
        device.note_command(desired)
        try:
            await self.api.async_control(self.home_seq, device.raw, desired)
        except NavienSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
