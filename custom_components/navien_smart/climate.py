"""온도형 매트의 온도조절기 (`heatControl.unit == "0.5C"`).

온도형은 `temperature.current` 를 함께 보내주므로 `climate` 가 맞다 — 서모스탯
카드의 현재 온도 칸이 채워진다.

단계형(`1.0L`)은 이 플랫폼을 만들지 않는다. 현재값이 오지 않아 카드 절반이 비고,
단계를 도(°)로 표시하면 사용자가 오해한다. 그쪽은 `number` 슬라이더를 쓴다.

**주의 — 이 플랫폼은 실기기로 검증하지 않았다.** 온도형 매트가 없었다.
구조는 앱 코드로 확정했으나, 특히 `enable: false` 전송은 확인하지 못했다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .const import MODE_HEAT, ZONE_NAMES
from .coordinator import NavienSmartCoordinator
from .entity import NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NavienSmartThermostat] = []
    for device in (coordinator.data or {}).values():
        control = device.heat_control
        if control is None or not control.is_celsius:
            continue
        entities.extend(
            NavienSmartThermostat(coordinator, device, zone) for zone in device.zones
        )
    async_add_entities(entities)


class NavienSmartThermostat(NavienSmartEntity, ClimateEntity):
    """구역 하나의 난방 온도."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
        zone: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._zone = zone
        self._attr_unique_id = f"{device.device_id}_{zone}_thermostat"

        label = device.zone_names.get(zone) or ZONE_NAMES.get(zone, zone)
        self._attr_name = label if device.is_double else "난방"

        control = device.heat_control
        assert control is not None  # setup 에서 걸러진다
        self._attr_min_temp = float(control.range_min or 30)
        self._attr_max_temp = float(control.range_max or 45)
        self._attr_target_temperature_step = control.step

    @property
    def available(self) -> bool:
        """냉방 중이면 손을 뗀다.

        사계절 모델은 여름에 같은 `heater` 값을 `coolControl` 범위로 읽어야 한다.
        그 값 체계가 확인되지 않았다.
        """
        device = self.device
        return super().available and device is not None and not device.is_cooling

    @property
    def current_temperature(self) -> float | None:
        device = self.device
        return None if device is None else device.zone_current(self._zone)

    @property
    def target_temperature(self) -> float | None:
        device = self.device
        return None if device is None else device.zone_setting(self._zone)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """기기 전원과 구역 `enable` 을 함께 본다.

        기기가 꺼져 있으면 구역이 켜져 있어도 난방하지 않는다.
        """
        device = self.device
        if device is None:
            return None
        if not device.is_on:
            return HVACMode.OFF
        enabled = device.zone_enabled(self._zone)
        return HVACMode.HEAT if enabled is not False else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        mode = self.hvac_mode
        if mode is None:
            return None
        return HVACAction.HEATING if mode is HVACMode.HEAT else HVACAction.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {"zone": self._zone}
        control = device.heat_control
        if control is not None and control.safe_value is not None:
            # 고온경고 기준선. 상한이 아니다.
            attrs["high_temp_warning_temperature"] = control.safe_value
        if device.is_four_season:
            attrs["four_season"] = True
            attrs["season"] = device.season
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get("temperature")
        device = self.device
        if device is None or temperature is None:
            return
        heater = device.build_heater_desired(
            changes={self._zone: float(temperature)}, enables={self._zone: True}
        )
        await self.coordinator.async_send(
            device, {"operationMode": MODE_HEAT, "heater": heater}
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        device = self.device
        if device is None:
            return
        if hvac_mode is HVACMode.HEAT:
            heater = device.build_heater_desired(enables={self._zone: True})
            await self.coordinator.async_send(
                device, {"operationMode": MODE_HEAT, "heater": heater}
            )
            return
        # 구역만 끈다. 기기 전원은 별도 스위치가 담당한다.
        heater = device.build_heater_desired(enables={self._zone: False})
        await self.coordinator.async_send(device, {"heater": heater})
