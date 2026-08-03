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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .const import (
    AIRONE_OPTION_SLEEP,
    LEVEL_STANDBY,
    MAT_VOLUME_NAMES,
    SEASON_NAMES,
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
        # 사계절 모델만 계절이 있다. 서버가 `coolControl` 을 줄 때가 그때다.
        if device.is_four_season:
            entities.append(NavienSmartSeasonSelect(coordinator, device))

        # **`functions.beep` 이 있으면 소리를 내는 기기다.** 앱은 이 값을 안 보고
        # 음량 화면을 여는데, 앱의 판단 기준을 찾지 못했다 — 모델별 표에도
        # 음량 항목이 없다. 서버가 스스로 알려주는 값을 쓰는 편이 낫다.
        # 없는 기기에 명령을 보내는 것보다 안 만드는 쪽이 안전하다.
        if device.has_beep:
            entities.append(NavienSmartVolumeSelect(coordinator, device))

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
        # 풍량은 모드에 따라 고를 수 있는 값이 달라진다. 어떤 모드에서든 두 개
        # 이상 고를 수 있을 때만 만든다.
        if any(
            len(airone.fan_choices(mode.mode, mode.option)) > 1
            for mode in airone.selectable_modes
        ):
            entities.append(AironeFanSelect(coordinator, airone))

    async_add_entities(entities)


class NavienSmartSeasonSelect(NavienSmartEntity, SelectEntity):
    """사계절 매트의 계절 — 난방 / 냉방.

    **`climate` 의 난방·냉방으로 만들지 않았다.** 계절은 지금 무엇을 하는지가
    아니라 **기기가 어느 쪽으로 설정돼 있는지**다. 앱도 제어 화면이 아니라 기기
    설정 화면에 두고, 바꾸면 온도 범위가 통째로 갈린다(난방 28~45 / 냉방 20~35).
    `climate` 의 모드 버튼으로 만들면 「지금 난방 중」과 「난방으로 설정됨」이
    같은 자리에 겹친다.

    앱이 쓰는 값 두 개만 쓴다. 서버가 모르는 값을 보내오면 상태를 비운다.
    """

    _attr_icon = "mdi:sun-snowflake-variant"
    _attr_options = list(SEASON_NAMES.values())

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_season"
        self._attr_name = "계절"

    @property
    def current_option(self) -> str | None:
        device = self.device
        if device is None:
            return None
        # 모르는 값이면 비운다. `season_name` 은 「알 수 없음(3)」처럼 목록에 없는
        # 문구를 만들 수 있는데, 그것을 상태로 쓰면 목록과 어긋난다.
        return SEASON_NAMES.get(device.season)

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None:
            return
        for value, label in SEASON_NAMES.items():
            if label == option:
                await self.coordinator.async_send(
                    device, device.build_season_desired(value)
                )
                return


class NavienSmartVolumeSelect(NavienSmartEntity, SelectEntity):
    """조작음 음량 — 음소거 / 1 / 2 / 3 단계.

    앱 음량 화면과 칸이 같다. `MateDeviceSettingSoundVolumeFragment` 가 고른
    `selectedIndex`(0~3)를 그대로 `Desired.volume` 에 싣는다.

    **끄고 켜는 것이 아니라 단계라서 스위치로 만들지 않았다.** 조작음을 통째로
    끄는 `control-beep` 는 따로 있는데, 그건 값 체계가 다르고 앱이
    2024년 이후 모델에만 붙인다 — 손대지 않는다.
    """

    _attr_icon = "mdi:volume-high"
    _attr_options = list(MAT_VOLUME_NAMES.values())
    # **한 번 정하고 안 건드리는 값이라 「설정」으로 내린다.** 기기 페이지에서
    # 줄 아래 칸으로 가고, 기본 「개요」 대시보드에서는 빠진다
    # (프론트엔드 `computeDefaultViewStates` 가 `entity_category` 를 숨긴다).
    #
    # **조작 잠금에는 붙이지 않는다.** 아이 있는 집에서 매일 켜고 끄는 것이라
    # 개요 화면에서 사라지면 안 된다 — v0.13.0 에서 읽기 전용 센서를 굳이
    # 스위치로 승격시킨 이유가 그것이다.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_volume"
        self._attr_name = "조작음 음량"

    @property
    def current_option(self) -> str | None:
        device = self.device
        return device.volume_name if device is not None else None

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None:
            return
        for value, label in MAT_VOLUME_NAMES.items():
            if label == option:
                await self.coordinator.async_send(
                    device, device.build_volume_desired(value)
                )
                return


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

        **단계형은 그대로 막아둔다.** 냉방 값 체계가 확인된 것은 온도형(`0.5C`)
        뿐이다 — 실기기 제보가 EMF520(온도형)이었다. 단계형 사계절 모델이
        냉방에서 어떤 단계 범위를 쓰는지는 아직 모른다.

        목록(`options`)이 만들어질 때 한 번 정해지는 구조라, 범위가 갈리는 것을
        런타임에 반영할 수 없다. 제보가 오면 그때 연다.
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
        control = device.active_control
        off = control.off_value if control is not None else None
        if off is not None and level <= off:
            # **「운전 대기」는 그 구역을 끄는 것이다.** 온도형의 「꺼짐」과 같은
            # 명령이라 같은 자리를 쓴다 (이슈 #16).
            #
            # **한쪽만 대기로 내리는 것은 종전과 똑같다** — `build_zone_off` 가
            # 같은 `heater` 를 만든다. 달라지는 것은 **마지막 남은 구역**을 내릴
            # 때뿐이고, 그때는 기기가 어차피 막으므로 이유를 알린다.
            # (실기기 확인: 좌 0 인 상태에서 우를 0 으로 내리면 `0, 1` 로 남는다)
            await self.coordinator.async_send(
                device, device.build_zone_off([self._zone])
            )
            return
        heater = device.build_heater_desired({self._zone: level})
        await self.coordinator.async_send(device, {"heater": heater})


class AironeModeSelect(AironeEntity, SelectEntity):
    """운전 모드.

    선택 항목을 **서버 메타데이터(`did.roomController.mode`)에서만** 만든다.
    모델 표를 코드에 넣지 않는다 — 매트에서 통한 방식과 같다.

    앱과 같은 축으로 자른다 — 터보·절전·기저는 여기가 아니라 풍량 쪽이다.
    그래야 목록이 짧고, 풍량만 바꾸려고 모드 목록을 뒤지지 않는다.
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
        if device is None or device.mode is None:
            return None
        # 지금 상태가 어느 항목에 해당하는지 찾는다. 숙면은 옵션까지 봐야 갈린다.
        for choice in self._modes:
            if choice.mode != device.mode:
                continue
            if choice.is_sleep != (device.option == AIRONE_OPTION_SLEEP):
                continue
            return choice.label
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {
            "mode": device.mode,
            "option": device.option,
            # 앱이 보여주는 전체 문구. 자동화·템플릿에서 쓸 수 있게 남긴다.
            "full_label": device.mode_label,
        }

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None:
            return
        try:
            chosen = self._modes[(self._attr_options or []).index(option)]
        except ValueError:
            return
        # 모드를 바꿀 때 풍량은 `build_mode_desired` 가 서버 값에서 골라 채운다.
        await self.coordinator.async_airone_mode(device, chosen.mode, chosen.option)


class AironeFanSelect(AironeEntity, SelectEntity):
    """풍량.

    **미풍·약풍·강풍·자동과 터보·절전·기저를 한 축에 둔다.** 앱이 그렇게 다룬다
    (`AironeModeCode.labelFor` 의 두 번째 칸). 기기에 따라 앞쪽만 있거나 뒤쪽만
    있는데, 어느 쪽이든 사용자에게는 「풍량」 하나로 보이는 것이 맞다.

    지금 모드에서 서버가 알려준 조합만 보여준다.
    """

    _attr_translation_key = "airone_fan"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_fan"

    @property
    def _choices(self) -> tuple[Any, ...]:
        device = self.device
        if device is None:
            return ()
        return device.fan_choices(device.mode, device.option)

    @property
    def options(self) -> list[str]:
        return [choice.label for choice in self._choices]

    @property
    def available(self) -> bool:
        """고를 것이 **하나도 없을 때만** 손을 뗀다.

        **하나뿐인 것과 없는 것은 다르다.** 앱은 숙면에서 풍량을 「자동」으로
        보여주면서 못 누르게만 한다 — 숨기지 않는다. 요리(강풍 하나)와
        자동운전(자동 하나)도 같다.

        `> 1` 로 두었더니 그 모드로 바꾸는 순간 엔티티가 통째로 빠졌다.
        사용자에게는 「지금 풍량이 뭔지」가 사라지는 것이라 없느니만 못하다.
        하나뿐이면 그 값을 보여준다 — 골라도 같은 값이라 바뀌는 것이 없다.
        """
        return super().available and bool(self._choices)

    @property
    def current_option(self) -> str | None:
        device = self.device
        if device is None:
            return None
        label = device.current_fan_label()
        return label if label in self.options else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        return {"option": device.option, "air_volume": device.air_volume}

    async def async_select_option(self, option: str) -> None:
        device = self.device
        if device is None or device.mode is None:
            return
        chosen = next((c for c in self._choices if c.label == option), None)
        if chosen is None:
            return
        await self.coordinator.async_airone_mode(
            device, device.mode, chosen.option, air_volume=chosen.air_volume
        )
