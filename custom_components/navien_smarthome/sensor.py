"""운전 상태와 오류 코드."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice, as_number, level_text, text_or_none
from .const import AIRONE_INFERRED_UNITS, AIRONE_SENSOR_KINDS
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity, AironeMonitorEntity, NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device in (coordinator.data or {}).values():
        entities.append(NavienSmartModeSensor(coordinator, device))
        entities.append(NavienSmartErrorSensor(coordinator, device))

    for airone in coordinator.airone.values():
        entities.append(AironeStateSensor(coordinator, airone))
        entities.append(AironeErrorSensor(coordinator, airone))
        # 공기질은 서버가 실제로 값을 준 항목만 만든다. 목록을 미리 정하지 않는다.
        #
        # 에어모니터가 등록돼 있으면 **그 기기 카드에** 붙인다. 앱에서도 별도
        # 부속이고, 본체 카드에 다 몰아넣으면 목록이 길어져 읽기 어렵다.
        monitor = airone.air_monitors[0] if airone.air_monitors else None
        for kind in airone.sensor_kinds:
            if monitor is not None:
                entities.append(AironeMonitorSensor(coordinator, airone, monitor, kind))
            else:
                entities.append(AironeAirSensor(coordinator, airone, kind))
        # 개수는 메타데이터에서 온다. 사용률은 상태에서 오지만, 엔티티는 MQTT 가
        # 붙기 전에 만들어지므로 상태로 세면 하나도 안 생긴다.
        entities.extend(
            AironeFilterSensor(coordinator, airone, index)
            for index in range(len(airone.filter_types))
        )

    async_add_entities(entities)


class NavienSmartModeSensor(NavienSmartEntity, SensorEntity):
    """`operationMode` 를 사람이 읽는 이름으로."""

    _attr_name = "운전 상태"
    _attr_icon = "mdi:bed"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_mode"
        # `options` 를 쓰지 않는다. `device_class = ENUM` 이 함께 필요하고, ENUM 은
        # 값이 반드시 목록 안에 있어야 한다. `mode_name` 은 모르는 모드에
        # `알 수 없음(N)` 을 돌려주므로 목록을 닫을 수 없다 —
        # 나비엔이 새 모드를 추가하면 그때부터 이 센서가 예외로 죽는다.

    @property
    def native_value(self) -> str | None:
        device = self.device
        return None if device is None else device.mode_name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {
            "operation_mode": device.operation_mode,
            "model_code": device.model_code,
            "model_type": device.model_type,
        }
        if device.heat_control is not None:
            attrs["control_unit"] = device.heat_control.unit
        if device.is_four_season:
            # 사계절 냉방은 값 체계가 확인되지 않았다. 제보용으로 그대로 노출한다.
            attrs["four_season"] = True
            attrs["season"] = device.season
            attrs["season_name"] = device.season_name
            attrs["cooling"] = device.is_cooling
            if device.cool_control is not None:
                attrs["cool_control"] = device.cool_control.as_diagnostics()
        if device.has_sleep_mode and device.sleep_durations:
            # 분 단위다. 3~12시간, 30분 간격.
            attrs["sleep_durations_minutes"] = device.sleep_durations
        if device.schedule_kinds:
            attrs["schedule_kinds"] = list(device.schedule_kinds)
        return attrs


class NavienSmartErrorSensor(NavienSmartEntity, SensorEntity):
    """`errorCode`. 0 이면 정상이다."""

    _attr_name = "오류 코드"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_error_code"

    @property
    def native_value(self) -> int | None:
        device = self.device
        return None if device is None else device.error_code


class AironeStateSensor(AironeEntity, SensorEntity):
    """`running` 을 사람이 읽는 이름으로. 운전 / 정지 / 외출."""

    _attr_name = "운전 상태"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_state"

    @property
    def native_value(self) -> str | None:
        device = self.device
        return None if device is None else device.running_name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {
            "running": device.running,
            "mode": device.mode,
            "option": device.option,
            "air_volume": device.air_volume,
            "model_code": device.model_code,
            # 실기기 미검증이라는 사실을 상태에도 남긴다. 자동화를 만들기 전에
            # 사용자가 알 수 있어야 한다.
            "verified_on_hardware": False,
        }
        if device.odu_model_code:
            attrs["odu_model_code"] = device.odu_model_code
        if device.target_humidity is not None:
            attrs["target_humidity"] = device.target_humidity
        return attrs


class AironeErrorSensor(AironeEntity, SensorEntity):
    """오류 코드. 0 이면 정상이다."""

    _attr_name = "오류 코드"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_error_code"

    @property
    def native_value(self) -> int | None:
        device = self.device
        return None if device is None else device.error_code


class _AirSensorMixin:
    """공기질 항목 하나. 본체에 붙일 때와 에어모니터에 붙일 때가 같다."""

    _kind: str
    _numeric: bool

    def _setup_air(self, device: AironeDevice, kind: str, unique_prefix: str) -> None:
        self._kind = kind
        self._attr_unique_id = f"{unique_prefix}_air_{kind}"
        label, unit, device_class = AIRONE_SENSOR_KINDS[kind]
        self._attr_name = label

        # 숫자인지 **첫 값을 보고** 정한다. 종류만 보고 정하면 틀린다 — 앱이
        # tvoc·radon·종합을 등급으로 표시하길래 값이 없다고 봤는데, 실사용
        # 제보로 숫자가 온다는 것이 확인됐다.
        #
        # 엔티티가 만들어질 땐 첫 조회가 끝나 있어서 값이 이미 있다.
        raw = device.air_sensors.get(kind) or {}
        self._numeric = as_number(raw.get("value")) is not None
        if self._numeric:
            self._attr_state_class = "measurement"
            if unit is not None:
                self._attr_native_unit_of_measurement = unit
            # HA 가 아이콘·히스토리 그래프·단위 변환에 쓴다. 단위가 맞아야
            # 붙일 수 있으므로 숫자일 때만 붙인다.
            if device_class is not None:
                self._attr_device_class = device_class

    @property
    def _raw(self) -> dict[str, Any] | None:
        device = self.device  # type: ignore[attr-defined]
        if device is None:
            return None
        return device.air_sensors.get(self._kind)

    @property
    def native_value(self) -> float | str | None:
        raw = self._raw
        if raw is None:
            return None
        if not self._numeric:
            # 등급을 못 읽으면 값이라도 문자열로 낸다.
            return level_text(raw) or text_or_none(raw.get("value"))
        # 숫자로 시작했는데 문자열이 오면 상태를 비운다. 섞어 내면
        # `state_class` 가 붙은 센서가 예외로 죽는다.
        return as_number(raw.get("value"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        raw = self._raw
        if raw is None:
            return None
        attrs: dict[str, Any] = {"kind": self._kind}
        if (level := level_text(raw)) is not None:
            # 숫자 센서에도 등급을 남긴다 — 앱이 보여주는 것이 이쪽이다.
            attrs["grade"] = level
        if self._kind in AIRONE_INFERRED_UNITS:
            # 앱에서 뽑은 단위가 아니라 판단으로 정한 것이다. 밖에서 볼 수 있게 남긴다.
            attrs["unit_inferred"] = True
        return attrs


class AironeAirSensor(_AirSensorMixin, AironeEntity, SensorEntity):
    """공기질 항목. 에어모니터가 없으면 본체 기기에 붙는다."""

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        kind: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._setup_air(device, kind, device.device_id)


class AironeMonitorSensor(_AirSensorMixin, AironeMonitorEntity, SensorEntity):
    """공기질 항목. 에어모니터 기기에 붙는다."""

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        monitor: dict[str, Any],
        kind: str,
    ) -> None:
        super().__init__(coordinator, device, monitor)
        self._setup_air(device, kind, self._monitor_id)


class AironeFilterSensor(AironeEntity, SensorEntity):
    """필터 사용률(%). 실외기가 알려준 필터마다 하나씩."""

    _attr_icon = "mdi:air-filter"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        index: int,
    ) -> None:
        super().__init__(coordinator, device)
        self._index = index
        self._attr_unique_id = f"{device.device_id}_filter_{index}"
        # 필터 `type` 의 뜻이 확인되지 않았다. 이름에 종류를 적지 않고 번호만 쓴다.
        count = len(device.filter_types)
        self._attr_name = "필터 사용률" if count == 1 else f"필터 {index + 1} 사용률"

    @property
    def _raw(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        filters = device.filters
        return filters[self._index] if self._index < len(filters) else None

    @property
    def native_value(self) -> int | None:
        raw = self._raw
        return None if raw is None else raw.get("percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        raw = self._raw
        if raw is None:
            return None
        return {
            "filter_type": raw.get("type"),
            "replace_period": raw.get("replace_period"),
        }
