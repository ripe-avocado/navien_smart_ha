"""난방 단계 (`heatControl.unit == "1.0L"`).

단계는 온도가 아니다. `climate` 로 붙이면 "3도" 로 읽히고, 단계형은
`temperature.current` 를 주지 않아 서모스탯 카드의 현재값이 영구히 빈다.
그래서 `number` 로 노출한다.

온도형(`0.5C`) 기기는 이 플랫폼을 만들지 않는다 — 그쪽은 `climate` 가 맞다.
값 체계를 모르는 `unit` 이면 아무것도 만들지 않고 코디네이터가 로그를 남긴다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .const import ZONE_NAMES
from .coordinator import NavienSmartCoordinator
from .entity import NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NavienSmartLevelNumber] = []
    for device in (coordinator.data or {}).values():
        control = device.heat_control
        if control is None or not control.is_level:
            continue
        entities.extend(
            NavienSmartLevelNumber(coordinator, device, zone) for zone in device.zones
        )
    async_add_entities(entities)


class NavienSmartLevelNumber(NavienSmartEntity, NumberEntity):
    """구역 하나의 난방 단계."""

    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:thermometer-lines"
    _attr_native_unit_of_measurement = "단계"

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
        zone: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._zone = zone
        self._attr_unique_id = f"{device.device_id}_{zone}_level"

        label = device.zone_names.get(zone) or ZONE_NAMES.get(zone, zone)
        self._attr_name = f"{label} 단계" if device.is_double else "난방 단계"

        control = device.heat_control
        assert control is not None  # setup 에서 걸러진다
        self._attr_native_min_value = float(control.range_min or 1)
        self._attr_native_max_value = float(control.range_max or 8)
        self._attr_native_step = control.step

    @property
    def available(self) -> bool:
        """냉방 중이면 손을 뗀다.

        사계절 모델은 `season` 이 여름이면 같은 `heater` 값을 `coolControl` 범위로
        읽어야 한다. 그 값 체계가 확인되지 않았으므로 난방 범위로 표시하지 않는다 —
        냉방 설정을 난방 단계로 보여주면 사용자가 오해한다.
        """
        device = self.device
        return super().available and device is not None and not device.is_cooling

    @property
    def native_value(self) -> float | None:
        device = self.device
        if device is None or device.is_cooling:
            return None
        return device.zone_setting(self._zone)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        control = device.heat_control
        attrs: dict[str, Any] = {
            "zone": self._zone,
            "enabled": device.zone_enabled(self._zone),
        }
        if control is not None and control.safe_value is not None:
            # 고온경고 기준선. 상한이 아니다 — 앱도 이 위로 설정할 수 있다.
            attrs["high_temp_warning_level"] = control.safe_value
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        device = self.device
        if device is None:
            return
        heater = device.build_heater_desired({self._zone: value})
        await self.coordinator.async_send(device, {"heater": heater})
