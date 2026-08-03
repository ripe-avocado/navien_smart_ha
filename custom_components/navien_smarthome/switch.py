"""전원 스위치.

**매트에는 언제나 만든다.** `functions.powerCtrl` 로 가르지 않는다 — 그 규칙은
근거가 없었고, 그 때문에 EME-520 사용자는 기기를 끌 방법이 아예 없었다 (이슈 #16).

앱은 그 필드를 **읽지도 않는다.** `ResponseDataSource.getPowerCtrl()` 을 부르는
곳이 앱 전체에 0건이다 (APK 2.10.4 전수 확인). 파싱만 하고 버리는 값이다.

전원은 `operationMode` 로 간다. 이슈 #16 제보자의 EME-520 은 `powerCtrl: false`
인데도 상태 기록에 `operationMode` 가 1↔0 으로 네 번 오갔고, 우리가 보낸
`operationMode: 1` 명령 다섯 건이 전부 반영됐다.
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
    # `has_power_ctrl` 로 거르지 않는다 (모듈 설명 참조). 진단에는 그대로 남겨
    # 두었으니 서버가 무엇을 알려주는지는 계속 볼 수 있다.
    entities: list[SwitchEntity] = [
        NavienSmartPowerSwitch(coordinator, device)
        for device in (coordinator.data or {}).values()
    ]
    # **서버가 잠금 기능을 알려줄 때만 만든다.** 앱은 모델 번호 표로 가르는데
    # (`MateInfoData` 의 `case 257: supportLock = false`) 표에 실린 모델이 다섯
    # 개뿐이라 새 모델을 못 따라간다. 서버 쪽 선언을 쓴다.
    entities.extend(
        NavienSmartChildLockSwitch(coordinator, device)
        for device in (coordinator.data or {}).values()
        if device.has_lock_mode
    )
    entities.extend(
        AironePowerSwitch(coordinator, device) for device in coordinator.airone.values()
    )
    async_add_entities(entities)


class NavienSmartPowerSwitch(NavienSmartEntity, SwitchEntity):
    """`operationMode` 0/1 로 전원을 끄고 켠다."""

    # **기기의 대표 엔티티다.** 이름을 두지 않으면 HA 가 기기 이름을 그대로 쓴다
    # (`Entity.use_device_name` — "the single main feature of a device").
    #
    # 그래서 목록에서 **항상 맨 위**에 온다. 기기 페이지는 표시 이름을 사전순으로
    # 정렬하는데, 한글은 `우(ㅇ)` 가 `좌(ㅈ)` 보다 앞이라 좌우 분리형에서
    # `우측 → 전원 → 좌측` 이라는 이상한 순서가 나왔다.
    #
    # **자격이 있는가** — 전원은 어느 모델이든 기기당 하나다. `operationMode` 가
    # `heater` 바깥에 있고, 좌/우를 아무리 만져도 그 값은 안 움직인다(실측).
    # 좌우 난방 단계는 둘이라 이름을 유지한다. `tplink` 가 쓰는 기준과 같다 —
    # 「기기당 하나면 기기 이름, 여럿이면 자기 이름」.
    #
    # `_attr_name` 이 `translation_key` 를 이긴다 (`Entity._name_internal` 첫 줄).
    # 번역 항목은 에어원 전원이 계속 쓰므로 남겨둔다.
    _attr_name = None
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


class NavienSmartChildLockSwitch(NavienSmartEntity, SwitchEntity):
    """조작 잠금. 기기 본체 버튼을 잠근다.

    **v0.12.0 은 이것을 읽기 전용 센서로 만들었다. 틀렸다.** 앱에 자물쇠 버튼이
    있는데 못 찾았다 — 명령을 문자열 그대로 넘기는 호출만 훑었고, 잠금은
    `"lock-on"`/`"lock-off"` 를 **변수로** 넘긴다.

    켜면 잠근다. 앱 버튼과 같은 방향이다.
    """

    _attr_icon = "mdi:lock"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_child_lock"
        self._attr_name = "조작 잠금"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return device.child_lock if device is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, locked: bool) -> None:
        device = self.device
        if device is None:
            return
        await self.coordinator.async_send(
            device, device.build_child_lock_desired(locked)
        )


class AironePowerSwitch(AironeEntity, SwitchEntity):
    """`running` 1/2 로 전원을 끄고 켠다.

    구세대는 이 값이 반대다(운전=2). `coordinator` 가 구세대를 걸러내므로 여기서는
    신형 규약만 다룬다 — 세대 판정을 두 곳에 두면 한쪽만 고치는 실수가 난다.
    """

    # 매트 전원과 같은 이유로 기기 대표다 (위 `NavienSmartPowerSwitch` 주석).
    # 에어원도 전원은 기기당 하나이고, 운전모드·풍량·희망습도는 여럿이라
    # 각자 이름을 유지한다.
    _attr_name = None
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
