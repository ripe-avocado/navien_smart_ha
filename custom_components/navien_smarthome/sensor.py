"""운전 상태와 오류 코드."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .const import AIRONE_LEVEL_NAMES, AIRONE_SENSOR_KINDS
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity, NavienSmartEntity
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
        entities.extend(
            AironeAirSensor(coordinator, airone, kind) for kind in airone.sensor_kinds
        )
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


class AironeAirSensor(AironeEntity, SensorEntity):
    """공기질 항목 하나.

    `tvoc` 와 `radon` 은 **앱도 숫자를 보여주지 않는다** — 등급만 온다. 그래서
    단위 없는 문자열로 둔다. 숫자를 만들어 붙이지 않는다.
    """

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        kind: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._kind = kind
        self._attr_unique_id = f"{device.device_id}_air_{kind}"
        label, unit = AIRONE_SENSOR_KINDS[kind]
        self._attr_name = label
        self._numeric = unit is not None
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = "measurement"

    @property
    def _raw(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return device.air_sensors.get(self._kind)

    @property
    def native_value(self) -> float | str | None:
        raw = self._raw
        if raw is None:
            return None
        if not self._numeric:
            return _level_text(raw)
        value = raw.get("value")
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        raw = self._raw
        if raw is None:
            return None
        attrs: dict[str, Any] = {"kind": self._kind}
        if (level := _level_text(raw)) is not None:
            attrs["grade"] = level
        return attrs


def _level_text(raw: dict[str, Any]) -> str | None:
    """등급을 한국어로. 서버가 이미 한국어로 주면 그대로 쓴다."""
    level = raw.get("level")
    if level is None or level == "":
        return None
    if isinstance(level, str) and not level.lstrip("-").isdigit():
        return level
    try:
        return AIRONE_LEVEL_NAMES.get(int(level))
    except (TypeError, ValueError):
        return str(level)


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
