"""보일러 온수 (v0.23.0). 제어를 켠 NR-6x(기본 켜짐), 실험 설정을 켠 NR-35D · NR-40D.

v0.22.0 에서는 `number`(숫자 입력칸)였다. 난방은 온도조절기 모양인데 온수만
입력칸이라 어색하다는 제보를 받고(#20) HA 의 온수기 엔티티로 옮겼다 — 같은 다이얼로
보이고, 뜻도 맞다.

명령은 그대로다 (`hotwater-temperature`, ℃×2 소수). #20 에서 실기기로 확인했다:
41 → 42 → 41℃ 가 실내조절기와 앱에 반영됐고, 보일러가 2초 뒤 바뀐 값을 보내왔다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_GAS,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .boiler import BoilerDevice
from .const import BOILER_CMD_HOTWATER
from .coordinator import NavienSmartCoordinator
from .entity import BoilerEntity, async_track_boilers


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_track_boilers(
        hass,
        entry,
        coordinator,
        lambda b: (
            [BoilerWaterHeater(coordinator, b)]
            if coordinator.boiler_controllable(b)
            and b.supports("hotWaterTemperatureSetting")
            and b.temperature_range("hotWater")
            else []
        ),
        async_add_entities,
    )


class BoilerWaterHeater(BoilerEntity, WaterHeaterEntity):
    """온수 설정 온도."""

    _attr_name = "온수"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    # 온수기 엔티티는 「운전 방식」을 상태로 보여 준다. 비워 두면 카드에 「알 수 없음」이
    # 뜬다 (#24). 켜짐 · 꺼짐은 보일러 전원과 온수가 같이 움직이는지 확인하지 못해서
    # 쓰지 않고, 사실인 「가스」로 둔다 (v0.26.1).
    _attr_current_operation = STATE_GAS

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_water_heater"
        low, high = boiler.temperature_range("hotWater") or (30.0, 60.0)
        self._attr_min_temp = low
        self._attr_max_temp = high
        # 안 정하면 화면이 ℃ 에서 0.5 칸으로 움직인다. 온수는 앱 · 실내조절기 모두
        # 1℃ 칸이라 0.5 를 고르면 보일러가 무시한다 (#24, NR-40D).
        self._attr_target_temperature_step = boiler.step_for("hotWaterTemperatureSetting")

    @property
    def current_temperature(self) -> float | None:
        """지금 온수 온도. NR-6x 만 보낸다."""
        boiler = self.boiler
        return None if boiler is None else boiler.value("hotWaterTemperature")

    @property
    def target_temperature(self) -> float | None:
        boiler = self.boiler
        return None if boiler is None else boiler.value("hotWaterTemperatureSetting")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        boiler = self.boiler
        temperature = kwargs.get("temperature")
        if boiler is None or temperature is None:
            return
        await self.coordinator.async_boiler_control(
            boiler,
            BOILER_CMD_HOTWATER,
            [boiler.encode_for("hotWaterTemperatureSetting", float(temperature))],
        )
