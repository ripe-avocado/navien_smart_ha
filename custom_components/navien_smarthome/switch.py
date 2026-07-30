"""전원 스위치.

`functions.powerCtrl` 이 없는 모델에는 만들지 않는다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .const import MODE_HEAT, MODE_POWER_OFF
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity, NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        NavienSmartPowerSwitch(coordinator, device)
        for device in (coordinator.data or {}).values()
        if device.has_power_ctrl
    ]
    entities.extend(
        AironePowerSwitch(coordinator, device) for device in coordinator.airone.values()
    )
    async_add_entities(entities)


class NavienSmartPowerSwitch(NavienSmartEntity, SwitchEntity):
    """`operationMode` 0/1 로 전원을 끄고 켠다."""

    _attr_translation_key = "power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_power"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or device.operation_mode is None:
            return None
        return device.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {"operation_mode": device.operation_mode, "mode": device.mode_name}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_mode(MODE_HEAT)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_mode(MODE_POWER_OFF)

    async def _async_set_mode(self, mode: int) -> None:
        device = self.device
        if device is None:
            return
        await self.coordinator.async_send(device, {"operationMode": mode})


class AironePowerSwitch(AironeEntity, SwitchEntity):
    """`running` 1/2 로 전원을 끄고 켠다.

    구세대는 이 값이 반대다(운전=2). `coordinator` 가 구세대를 걸러내므로 여기서는
    신형 규약만 다룬다 — 세대 판정을 두 곳에 두면 한쪽만 고치는 실수가 난다.
    """

    _attr_translation_key = "power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_power"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or device.running is None:
            return None
        return device.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {"running": device.running, "state": device.running_name}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_power(False)

    async def _async_power(self, turn_on: bool) -> None:
        device = self.device
        if device is None:
            return
        await self.coordinator.async_airone_power(device, turn_on)
