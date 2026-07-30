"""운전 상태와 오류 코드."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .const import MODE_NAMES
from .coordinator import NavienSmartCoordinator
from .entity import NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NavienSmartEntity] = []
    for device in (coordinator.data or {}).values():
        entities.append(NavienSmartModeSensor(coordinator, device))
        entities.append(NavienSmartErrorSensor(coordinator, device))
    async_add_entities(entities)


class NavienSmartModeSensor(NavienSmartEntity, SensorEntity):
    """`operationMode` 를 사람이 읽는 이름으로."""

    _attr_name = "운전 상태"
    _attr_icon = "mdi:bed"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_mode"
        self._attr_options = list(MODE_NAMES.values())

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
