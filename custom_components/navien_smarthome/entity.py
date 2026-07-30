"""엔티티 공통 베이스."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .airone import AironeDevice
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


class AironeEntity(CoordinatorEntity[NavienSmartCoordinator]):
    """에어원 엔티티 베이스.

    매트와 기기 정보 구성이 다르다 — 실내기(방 컨트롤러)와 실외기가 각자 펌웨어를
    가지므로 둘을 한 줄로 합친다. `modelType` 은 에어원에 없다.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        self._attr_unique_id = f"{device.device_id}"

        firmware = device.rc_version
        if firmware and device.odu_version:
            firmware = f"{firmware} (실외기 {device.odu_version})"
        elif not firmware and device.odu_version:
            firmware = f"실외기 {device.odu_version}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="경동나비엔",
            name=device.nickname,
            model=device.model_name,
            model_id=device.model_code or None,
            serial_number=device.device_id,
            sw_version=firmware,
        )

    @property
    def device(self) -> AironeDevice | None:
        return self.coordinator.airone.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.available


class AironeMonitorEntity(CoordinatorEntity[NavienSmartCoordinator]):
    """에어모니터(공기질 센서 본체) 엔티티 베이스.

    본체와 **별도 기기**로 만든다. 앱에서도 따로 등록·연결하는 부속이고,
    자체 모델명·펌웨어를 가진다. `via_device` 로 본체에 매달아 관계를 남긴다.

    `modelCode` 가 1000 미만이지만(실측 NAA-21DM=35) **제어 대상이 아니라**
    세대 판정과 무관하다.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        monitor: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.device_id
        monitor_id = str(monitor.get("deviceId") or f"{device.device_id}_airmonitor")
        self._monitor_id = monitor_id

        model_code = monitor.get("modelCode")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, monitor_id)},
            manufacturer="경동나비엔",
            name=f"{device.nickname} 에어모니터",
            # 모델명을 코드에 적지 않는다. 서버가 코드만 주면 코드를 보여준다.
            model="에어모니터",
            model_id=str(model_code) if model_code is not None else None,
            serial_number=monitor_id,
            sw_version=monitor.get("version") or None,
            via_device=(DOMAIN, device.device_id),
        )

    @property
    def device(self) -> AironeDevice | None:
        return self.coordinator.airone.get(self._device_id)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.available
