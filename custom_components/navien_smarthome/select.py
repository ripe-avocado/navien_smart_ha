"""난방 단계 선택 (`heatControl.unit == "1.0L"`).

단계는 연속량이 아니다. 9개의 이산 상태이고, 그중 `0` 은 숫자가 아니라
**운전 대기** 라는 상태다. 슬라이더로 만들면 세 가지가 어긋난다.

- 조준해야 한다 — 좁은 카드 행에서 한 칸 오버슈트가 실제 온도 차이로 이어진다
- 눈금에 이름을 못 붙인다 — 앱은 `운전 대기` 라고 부르는데 `0 단계` 로 보인다
- 서버가 알려주는 `rangeMin` 이 1이라 0을 넣을 수 없다. 표시는 되는데 설정이 안 됐다

단계를 `climate` 로 만들지 않은 이유("단계는 온도가 아니다")와 같은 논리다.
단계는 연속량도 아니다.

온도형(`0.5C`)은 진짜 연속량이므로 `climate` 를 쓴다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .const import (
    AIRONE_OPTION_NONE,
    AIRONE_WIND_NAMES,
    LEVEL_STANDBY,
    ZONE_NAMES,
    level_label,
)
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity, NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for device in (coordinator.data or {}).values():
        control = device.heat_control
        if control is None or not control.is_level:
            continue
        entities.extend(
            NavienSmartLevelSelect(coordinator, device, zone) for zone in device.zones
        )

    for airone in coordinator.airone.values():
        # 서버가 고를 수 있는 조합을 알려주지 않으면 만들지 않는다.
        if airone.selectable_modes:
            entities.append(AironeModeSelect(coordinator, airone))
        # 풍량은 조합에 따라 고를 수 있는 값이 달라진다. 어떤 조합에서든 두 개
        # 이상 고를 수 있을 때만 만든다.
        if any(
            len(airone.wind_choices(mode.mode, mode.option)) > 1
            for mode in airone.selectable_modes
        ):
            entities.append(AironeWindSelect(coordinator, airone))

    async_add_entities(entities)


class NavienSmartLevelSelect(NavienSmartEntity, SelectEntity):
    """구역 하나의 난방 단계."""

    _attr_icon = "mdi:thermometer-lines"

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
        low = int(control.range_min if control.range_min is not None else 1)
        high = int(control.range_max if control.range_max is not None else 8)

        # 서버는 `rangeMin` 을 1로 주지만 기기는 0(운전 대기)을 받는다 — 실측 확인.
        # 그래서 0을 앞에 붙인다. 온도형에는 적용하지 않는다.
        self._levels = [LEVEL_STANDBY, *range(low, high + 1)]
        self._attr_options = [level_label(value) for value in self._levels]

    @property
    def available(self) -> bool:
        """냉방 중이면 손을 뗀다.

        사계절 모델은 여름에 같은 `heater` 값을 `coolControl` 범위로 읽어야 하고,
        그 값 체계가 확인되지 않았다.
        """
        device = self.device
        return super().available and device is not None and not device.is_cooling

    @property
    def current_option(self) -> str | None:
        device = self.device
        if device is None or device.is_cooling:
            return None
        value = device.zone_setting(self._zone)
        if value is None:
            return None
        label = level_label(int(value))
        # 서버가 목록 밖의 값을 보내면 상태를 비운다. 없는 항목을 만들지 않는다.
        return label if label in (self._attr_options or []) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        value = device.zone_setting(self._zone)
        attrs: dict[str, Any] = {
            "zone": self._zone,
            # 숫자로 다뤄야 하는 자동화·템플릿을 위해 값을 그대로 남긴다.
            "level": None if value is None else int(value),
            "enabled": device.zone_enabled(self._zone),
        }
        control = device.heat_control
        if control is not None and control.safe_value is not None:
            # 고온경고 기준선. 상한이 아니다 — 앱도 이 위로 설정할 수 있다.
            attrs["high_temp_warning_level"] = int(control.safe_value)
        return attrs

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None:
            return
        try:
            level = self._levels[(self._attr_options or []).index(option)]
        except ValueError:
            return
        # `level 0` 이면 `enable: false` 가 함께 간다 — `build_heater_desired` 가 처리한다.
        heater = device.build_heater_desired({self._zone: level})
        await self.coordinator.async_send(device, {"heater": heater})


class AironeModeSelect(AironeEntity, SelectEntity):
    """운전 모드.

    선택 항목을 **서버 메타데이터(`did.roomController.mode`)에서만** 만든다.
    모델 표를 코드에 넣지 않는다 — 매트에서 통한 방식과 같다.
    """

    _attr_translation_key = "airone_mode"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_mode"
        self._modes = device.selectable_modes
        self._attr_options = [mode.label for mode in self._modes]

    @property
    def current_option(self) -> str | None:
        device = self.device
        if device is None:
            return None
        label = device.mode_label
        # 목록에 없는 값이면 상태를 비운다. 없는 항목을 만들지 않는다.
        return label if label in (self._attr_options or []) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {"mode": device.mode, "option": device.option}

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None:
            return
        try:
            chosen = self._modes[(self._attr_options or []).index(option)]
        except ValueError:
            return
        await self.coordinator.async_airone_mode(device, chosen.mode, chosen.option)


class AironeWindSelect(AironeEntity, SelectEntity):
    """풍량.

    지금 운전 조합에서 고를 수 있는 값만 보여준다. `airVolume` 이 비트마스크일
    가능성이 남아 있어 **확인된 표(1~6)에 있는 값만** 다룬다 (명세 6-5).
    """

    _attr_translation_key = "airone_wind"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_wind"

    @property
    def _choices(self) -> tuple[int, ...]:
        device = self.device
        if device is None:
            return ()
        return device.wind_choices(device.mode, device.option)

    @property
    def options(self) -> list[str]:
        return [AIRONE_WIND_NAMES[value] for value in self._choices]

    @property
    def available(self) -> bool:
        """지금 조합에서 풍량을 고를 수 없으면 손을 뗀다.

        터보·절전 같은 옵션에서는 앱도 풍량을 보여주지 않는다.
        """
        return super().available and len(self._choices) > 1

    @property
    def current_option(self) -> str | None:
        device = self.device
        if device is None:
            return None
        label = device.wind_label
        return label if label in self.options else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {"air_volume": device.air_volume}

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None or device.mode is None:
            return
        choices = self._choices
        try:
            value = choices[self.options.index(option)]
        except ValueError:
            return
        option_code = (
            AIRONE_OPTION_NONE if device.option is None else device.option
        )
        await self.coordinator.async_airone_mode(
            device, device.mode, option_code, air_volume=value
        )
