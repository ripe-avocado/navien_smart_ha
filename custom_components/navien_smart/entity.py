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

        # 매트는 MCU 와 Wi-Fi 모듈이 각자 펌웨어를 가진다. HA 의 `DeviceInfo` 에는
        # 펌웨어 칸이 하나뿐이라 둘을 한 줄로 합친다.
        #
        # Wi-Fi 펌웨어를 `hw_version` 에 넣지 않는다 — HA 가 그 칸을 「하드웨어」로
        # 표시하므로 펌웨어를 넣으면 거짓 정보가 된다. 서버는 하드웨어 리비전을
        # 알려주지 않는다.
        firmware = device.mcu_version
        if firmware and device.wifi_version:
            firmware = f"{firmware} (Wi-Fi {device.wifi_version})"
        elif not firmware and device.wifi_version:
            firmware = f"Wi-Fi {device.wifi_version}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="경동나비엔",
            name=device.nickname,
            model=model,
            model_id=device.model_code or None,
            serial_number=device.device_id,
            sw_version=firmware,
        )

    @property
    def device(self) -> NavienDevice | None:
        return (self.coordinator.data or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.available
