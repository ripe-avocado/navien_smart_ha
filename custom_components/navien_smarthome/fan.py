"""환기청정기 팬 (v0.27.0).

전원 · 풍량을 엔티티 하나로 묶는다 (#32). 따로 있던 전원 스위치와 풍량 선택은
HA 기본 카드 · 음성 비서 · 홈킷이 한 기기로 알아듣지 못했다. 환기장치를 HA 에서
나타내는 표준이 팬이다.

**풍량을 두 갈래로 나눈다.**

- 미풍 · 약풍 · 강풍 → 속도(퍼센트). 순서가 있는 단이다
- 자동 · 터보 · 절전 · 기저 → 프리셋. 단이 아니라 방식이다 (HA 팬의 프리셋이 원래
  auto · turbo · sleep 같은 것을 담는 자리다)

**운전 모드는 담지 않는다.** 모드마다 고를 수 있는 풍량이 달라서(요리는 강풍 하나,
자동운전은 자동 하나), 모드까지 프리셋에 넣으면 지금 모드에서 못 고르는 풍량이 섞인다.
모드는 그대로 `운전 모드` 선택이 맡는다.

명령은 새로 만들지 않았다. 전원 스위치 · 풍량 선택이 쓰던 것을 그대로 쓴다.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice, AironeFanChoice
from .const import AIRONE_OPTIONS_WITH_WIND
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity

# 순서가 있는 단 (`AIRONE_WIND_NAMES` 의 미풍 · 약풍 · 강풍). 자동(4) · 기저(5 · 6)는 프리셋이다.
SPEED_AIR_VOLUMES = (1, 2, 3)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AironeFan(coordinator, device) for device in coordinator.airone.values()
    )


def _is_speed(choice: AironeFanChoice) -> bool:
    return choice.option in AIRONE_OPTIONS_WITH_WIND and choice.air_volume in SPEED_AIR_VOLUMES


class AironeFan(AironeEntity, FanEntity):
    """전원 · 풍량 단(퍼센트) · 풍량 방식(프리셋)."""

    # 기기 대표 엔티티. 이름은 기기 이름을 그대로 쓴다.
    _attr_name = None

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_fan_entity"

    # -- 지금 모드에서 고를 수 있는 것 ---------------------------------------

    @property
    def _choices(self) -> tuple[AironeFanChoice, ...]:
        device = self.device
        if device is None:
            return ()
        return device.fan_choices(device.mode, device.option)

    @property
    def _speeds(self) -> list[AironeFanChoice]:
        return sorted((c for c in self._choices if _is_speed(c)), key=lambda c: c.air_volume)

    @property
    def _presets(self) -> list[AironeFanChoice]:
        return [c for c in self._choices if not _is_speed(c)]

    @property
    def _current(self) -> AironeFanChoice | None:
        device = self.device
        if device is None:
            return None
        label = device.current_fan_label()
        return next((c for c in self._choices if c.label == label), None)

    # -- 상태 --------------------------------------------------------------

    @property
    def supported_features(self) -> FanEntityFeature:
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._speeds:
            features |= FanEntityFeature.SET_SPEED
        if self._presets:
            features |= FanEntityFeature.PRESET_MODE
        return features

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or device.running is None:
            return None
        return device.is_on

    @property
    def speed_count(self) -> int:
        return max(len(self._speeds), 1)

    @property
    def percentage(self) -> int | None:
        """지금 단의 퍼센트. 꺼져 있으면 0, 프리셋(자동 · 터보 …)이면 None."""
        if self.is_on is False:
            return 0
        speeds = self._speeds
        current = self._current
        if current is None or current not in speeds:
            return None
        return round((speeds.index(current) + 1) * 100 / len(speeds))

    @property
    def preset_modes(self) -> list[str] | None:
        return [c.label for c in self._presets] or None

    @property
    def preset_mode(self) -> str | None:
        current = self._current
        if current is None or _is_speed(current):
            return None
        return current.label

    # -- 제어 --------------------------------------------------------------

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        device = self.device
        if device is None:
            return
        if not device.is_on:
            await self.coordinator.async_airone_power(device, True)
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
        elif percentage:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        device = self.device
        if device is None:
            return
        await self.coordinator.async_airone_power(device, False)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        speeds = self._speeds
        if not speeds:
            return
        index = min(max(math.ceil(percentage * len(speeds) / 100) - 1, 0), len(speeds) - 1)
        await self._async_choose(speeds[index])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        chosen = next((c for c in self._presets if c.label == preset_mode), None)
        if chosen is not None:
            await self._async_choose(chosen)

    async def _async_choose(self, choice: AironeFanChoice) -> None:
        """`풍량` 선택과 같은 명령이다 (`AironeFanSelect.async_select_option`)."""
        device = self.device
        if device is None or device.mode is None:
            return
        await self.coordinator.async_airone_mode(
            device, device.mode, choice.option, air_volume=choice.air_volume
        )
