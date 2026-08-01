"""에어원 목표 습도.

제습·환기제습에서만 쓴다. **범위를 코드에 적지 않는다** — 서버가 운전 조합마다
`additionalData` 의 `min`/`max` 를 알려주므로 그것만 쓴다. 안 알려주면 만들지 않는다.

습도는 단계와 달리 진짜 연속량이라 슬라이더가 맞다 (매트 단계를 `select` 로 바꾼
이유와 반대 방향이다).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .const import AIRONE_HUMIDITY_STEP, AIRONE_OPTION_NONE
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AironeHumidityNumber(coordinator, device)
        for device in coordinator.airone.values()
        # 어떤 조합에서든 서버가 습도 범위를 알려줄 때만 만든다.
        if any(mode.wants_humidity for mode in device.modes)
    )


class AironeHumidityNumber(AironeEntity, NumberEntity):
    """제습 목표 습도(%)."""

    _attr_name = "희망습도"
    _attr_icon = "mdi:water-percent"
    _attr_native_unit_of_measurement = "%"
    # 앱의 −/+ 버튼이 5씩 움직인다. 서버는 간격을 주지 않으므로 앱을 따른다.
    _attr_native_step = AIRONE_HUMIDITY_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_humidity"
        # 슬라이더 눈금은 만들어질 때 한 번 정해진다. 서버가 알려준 범위를 모두
        # 감싸는 폭으로 잡고, 지금 조합에서 벗어난 값은 전송 단계에서 막는다.
        bounds = [
            (mode.humidity_min, mode.humidity_max)
            for mode in device.modes
            if mode.wants_humidity
        ]
        self._attr_native_min_value = min(low for low, _ in bounds if low is not None)
        self._attr_native_max_value = max(high for _, high in bounds if high is not None)

    @property
    def _bounds(self) -> tuple[int, int] | None:
        device = self.device
        if device is None:
            return None
        return device.humidity_bounds(device.mode, device.option)

    @property
    def available(self) -> bool:
        """제습 계열이 아니면 손을 뗀다. 앱도 그때만 습도를 보여준다."""
        return super().available and self._bounds is not None

    @property
    def native_value(self) -> float | None:
        device = self.device
        if device is None:
            return None
        value = device.target_humidity
        return None if value is None else float(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        bounds = self._bounds
        if bounds is None:
            return None
        return {"allowed_min": bounds[0], "allowed_max": bounds[1]}

    async def async_set_native_value(self, value: float) -> None:
        device = self.device
        if device is None or device.mode is None:
            return
        bounds = self._bounds
        if bounds is None:
            return
        # 슬라이더가 5단위여도 자동화는 임의 값을 넣을 수 있다. 5의 배수로 맞춘 뒤
        # 서버가 알려준 범위로 자른다.
        stepped = int(round(value / AIRONE_HUMIDITY_STEP) * AIRONE_HUMIDITY_STEP)
        target = max(bounds[0], min(bounds[1], stepped))
        option = AIRONE_OPTION_NONE if device.option is None else device.option
        await self.coordinator.async_airone_mode(
            device, device.mode, option, humidity=target
        )
