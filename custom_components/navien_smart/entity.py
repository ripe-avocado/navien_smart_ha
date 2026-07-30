"""엔티티 공통 베이스."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODEL_TYPE_LABELS
from .coordinator import NavienSmartCoordinator
from .models import NavienDevice


class NavienSmartEntity(CoordinatorEntity[NavienSmartCoordinator]):
    """`deviceId` 로 기기를 붙잡는다. `deviceSeq` 는 재등록 시 바뀔 수 있다."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._attr_unique_id = f"{device.device_id}"

        model = device.model_name
        if label := MODEL_TYPE_LABELS.get(device.model_type or ""):
            model = f"{model} ({label})"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="경동나비엔",
            name=device.nickname,
            model=model,
            serial_number=device.device_id,
        )

    @property
    def device(self) -> NavienDevice | None:
        return (self.coordinator.data or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.available
