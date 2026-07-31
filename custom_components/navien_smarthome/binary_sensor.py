"""고온경고와 실시간 연결 상태."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice
from .coordinator import NavienSmartCoordinator
from .entity import AironeEntity, NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for device in (coordinator.data or {}).values():
        control = device.heat_control
        if control is not None and control.enable_safe and control.safe_value is not None:
            entities.append(NavienSmartHighTempWarning(coordinator, device))
        entities.append(NavienSmartErrorProblem(coordinator, device))
        # **서버가 잠금 기능을 알려줄 때만 만든다.** 앱은 모델 번호 표로 가르는데
        # (`MateInfoData` 의 `case 257: supportLock = false`) 표에 실린 모델이
        # 다섯 개뿐이라 새 모델을 못 따라간다. 서버 쪽 선언을 쓴다.
        if device.has_lock_mode:
            entities.append(NavienSmartChildLock(coordinator, device))

    entities.extend(
        AironeErrorProblem(coordinator, airone) for airone in coordinator.airone.values()
    )
    async_add_entities(entities)


class NavienSmartChildLock(NavienSmartEntity, BinarySensorEntity):
    """조작 잠금이 걸려 있는지. **읽기 전용이다.**

    스위치로 만들지 않았다. 앱에 WiFi 로 잠금을 보내는 경로가 없다 —
    `Desired.childLock` 은 `final` 이라 setter 가 없고, 잠금·해제 상수
    (`MATE_WIFI_DEVICE_CONTROL_LOCK`)는 앱 어디에서도 쓰이지 않는다.
    블루투스 모델에만 조작 경로가 있다.

    **눌러도 아무 일이 없는 스위치는 없느니만 못하다.** 상태만 보여준다.
    """

    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_child_lock"
        self._attr_name = "조작 잠금"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None:
            return None
        locked = device.child_lock
        if locked is None:
            return None
        # `LOCK` 는 **켜짐 = 열림**이다. 잠겨 있으면 꺼짐으로 보여야 한다.
        return not locked


class NavienSmartHighTempWarning(NavienSmartEntity, BinarySensorEntity):
    """설정 단계가 고온경고선을 넘었는지.

    제어를 막지 않는다 — 앱도 이 위로 설정할 수 있고, 경고 표시만 한다.
    """

    _attr_name = "고온경고"
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:thermometer-alert"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_high_temp_warning"

    @property
    def available(self) -> bool:
        """냉방 중에는 판정하지 않는다. 냉방의 안전 기준값 의미가 미확인이다."""
        device = self.device
        return super().available and device is not None and not device.is_cooling

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return None if device is None else device.over_safe_value


class NavienSmartErrorProblem(NavienSmartEntity, BinarySensorEntity):
    """`errorCode` 가 0 이 아니면 문제."""

    _attr_name = "오류"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_problem"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or device.error_code is None:
            return None
        return device.error_code != 0


class AironeErrorProblem(AironeEntity, BinarySensorEntity):
    """오류 여부. 방 컨트롤러와 실외기 중 하나라도 오류면 켜진다."""

    _attr_name = "오류"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_problem"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or device.error_code is None:
            return None
        return device.has_error
