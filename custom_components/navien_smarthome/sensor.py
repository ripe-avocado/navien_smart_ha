"""운전 상태와 오류 코드."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .airone import AironeDevice, as_number, level_text, text_or_none
from .boiler import BoilerDevice
from .const import (
    AIRONE_INFERRED_UNITS,
    AIRONE_SENSOR_KINDS,
    LEGACY_EXTRA_FIELDS,
    LEGACY_VALUE_TABLES,
)
from .coordinator import NavienSmartCoordinator
from .entity import (
    AironeEntity,
    AironeMonitorEntity,
    BoilerEntity,
    NavienSmartEntity,
    async_track_boilers,
)
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device in (coordinator.data or {}).values():
        entities.append(NavienSmartModeSensor(coordinator, device))
        entities.append(NavienSmartErrorSensor(coordinator, device))

    for airone in coordinator.airone.values():
        entities.append(AironeStateSensor(coordinator, airone))
        entities.append(AironeErrorSensor(coordinator, airone))
        # 구세대만 싣는 값. **상태로 세지 않는다** — 엔티티는 MQTT 가 붙기 전에
        # 만들어지므로 그때 세면 하나도 안 생긴다 (필터와 같은 이유).
        if not airone.is_v2_generation:
            for key, label, table in LEGACY_EXTRA_FIELDS.values():
                entities.append(AironeLegacySensor(coordinator, airone, key, label, table))
        # 공기질은 서버가 실제로 값을 준 항목만 만든다. 목록을 미리 정하지 않는다.
        #
        # 에어모니터가 등록돼 있으면 **그 기기 카드에** 붙인다. 앱에서도 별도
        # 부속이고, 본체 카드에 다 몰아넣으면 목록이 길어져 읽기 어렵다.
        monitor = airone.air_monitors[0] if airone.air_monitors else None
        for kind in airone.sensor_kinds:
            if monitor is not None:
                entities.append(AironeMonitorSensor(coordinator, airone, monitor, kind))
            else:
                entities.append(AironeAirSensor(coordinator, airone, kind))
        # 개수는 메타데이터에서 온다. 잔량은 상태에서 오지만, 엔티티는 MQTT 가
        # 붙기 전에 만들어지므로 상태로 세면 하나도 안 생긴다.
        entities.extend(
            AironeFilterSensor(coordinator, airone, index)
            for index in range(len(airone.filter_types))
        )

    async_add_entities(entities)
    async_track_boilers(
        hass, entry, coordinator, lambda b: boiler_sensors(coordinator, b), async_add_entities
    )


class NavienSmartModeSensor(NavienSmartEntity, SensorEntity):
    """`operationMode` 를 사람이 읽는 이름으로."""

    _attr_name = "운전상태"
    _attr_icon = "mdi:bed"

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_mode"
        # `options` 를 쓰지 않는다. `device_class = ENUM` 이 함께 필요하고, ENUM 은
        # 값이 반드시 목록 안에 있어야 한다. `mode_name` 은 모르는 모드에
        # `알 수 없음(N)` 을 돌려주므로 목록을 닫을 수 없다 —
        # 나비엔이 새 모드를 추가하면 그때부터 이 센서가 예외로 죽는다.

    @property
    def native_value(self) -> str | None:
        device = self.device
        return None if device is None else device.mode_name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {
            "operation_mode": device.operation_mode,
            "model_code": device.model_code,
            "model_type": device.model_type,
        }
        if device.heat_control is not None:
            attrs["control_unit"] = device.heat_control.unit
        if device.is_four_season:
            # 사계절 냉방은 값 체계가 확인되지 않았다. 제보용으로 그대로 노출한다.
            attrs["four_season"] = True
            attrs["season"] = device.season
            attrs["season_name"] = device.season_name
            attrs["cooling"] = device.is_cooling
            if device.cool_control is not None:
                attrs["cool_control"] = device.cool_control.as_diagnostics()
        if device.has_sleep_mode and device.sleep_durations:
            # 분 단위다. 3~12시간, 30분 간격.
            attrs["sleep_durations_minutes"] = device.sleep_durations
        if device.schedule_kinds:
            attrs["schedule_kinds"] = list(device.schedule_kinds)
        return attrs


class NavienSmartErrorSensor(NavienSmartEntity, SensorEntity):
    """`errorCode`. 0 이면 정상이다."""

    _attr_name = "오류 코드"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NavienSmartCoordinator, device: NavienDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_error_code"

    @property
    def native_value(self) -> int | None:
        device = self.device
        return None if device is None else device.error_code

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """오류 이름을 속성으로 붙인다.

        **상태값은 숫자 그대로 둔다.** 글자로 바꾸면 이 값을 쓰던 자동화가 깨진다.
        이름은 제보자가 기기 설명서에서 옮겨 준 표이고 온도형에만 붙는다.
        """
        device = self.device
        if device is None:
            return None
        text = device.error_text
        return None if text is None else {"error_text": text}


class AironeStateSensor(AironeEntity, SensorEntity):
    """`running` 을 사람이 읽는 이름으로. 운전 / 정지 / 외출."""

    _attr_name = "운전상태"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_state"

    @property
    def native_value(self) -> str | None:
        device = self.device
        return None if device is None else device.running_name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {
            "running": device.running,
            "mode": device.mode,
            "option": device.option,
            "air_volume": device.air_volume,
            "model_code": device.model_code,
            # 실기기 미검증이라는 사실을 상태에도 남긴다. 자동화를 만들기 전에
            # 사용자가 알 수 있어야 한다.
            "verified_on_hardware": False,
        }
        if device.odu_model_code:
            attrs["odu_model_code"] = device.odu_model_code
        if device.target_humidity is not None:
            attrs["target_humidity"] = device.target_humidity
        # 자동건조 중일 때만 있다. 상태 문구는 「자동건조」로 두고 숫자는 여기 둔다 —
        # 상태에 섞으면 문자열을 비교하는 자동화가 매번 깨진다.
        if device.auto_dry_percent is not None:
            attrs["auto_dry_percent"] = device.auto_dry_percent
        return attrs


class AironeErrorSensor(AironeEntity, SensorEntity):
    """오류 코드. 0 이면 정상이다."""

    _attr_name = "오류 코드"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NavienSmartCoordinator, device: AironeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_error_code"

    @property
    def native_value(self) -> int | None:
        device = self.device
        return None if device is None else device.error_code


class _AirSensorMixin:
    """공기질 항목 하나. 본체에 붙일 때와 에어모니터에 붙일 때가 같다."""

    _kind: str
    _numeric: bool

    def _setup_air(self, device: AironeDevice, kind: str, unique_prefix: str) -> None:
        self._kind = kind
        self._attr_unique_id = f"{unique_prefix}_air_{kind}"
        label, unit, device_class = AIRONE_SENSOR_KINDS[kind]
        self._attr_name = label

        # 숫자인지 **첫 값을 보고** 정한다. 종류만 보고 정하면 틀린다 — 앱이
        # tvoc·radon·종합을 등급으로 표시하길래 값이 없다고 봤는데, 실사용
        # 제보로 숫자가 온다는 것이 확인됐다.
        #
        # 엔티티가 만들어질 땐 첫 조회가 끝나 있어서 값이 이미 있다.
        raw = device.air_sensors.get(kind) or {}
        self._numeric = as_number(raw.get("value")) is not None
        if self._numeric:
            self._attr_state_class = "measurement"
            if unit is not None:
                self._attr_native_unit_of_measurement = unit
            # HA 가 아이콘·히스토리 그래프·단위 변환에 쓴다. 단위가 맞아야
            # 붙일 수 있으므로 숫자일 때만 붙인다.
            if device_class is not None:
                self._attr_device_class = device_class

    @property
    def _raw(self) -> dict[str, Any] | None:
        device = self.device  # type: ignore[attr-defined]
        if device is None:
            return None
        return device.air_sensors.get(self._kind)

    @property
    def native_value(self) -> float | str | None:
        raw = self._raw
        if raw is None:
            return None
        if not self._numeric:
            # 등급을 못 읽으면 값이라도 문자열로 낸다.
            return level_text(raw) or text_or_none(raw.get("value"))
        # 숫자로 시작했는데 문자열이 오면 상태를 비운다. 섞어 내면
        # `state_class` 가 붙은 센서가 예외로 죽는다.
        return as_number(raw.get("value"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        raw = self._raw
        if raw is None:
            return None
        attrs: dict[str, Any] = {"kind": self._kind}
        if (level := level_text(raw)) is not None:
            # 숫자 센서에도 등급을 남긴다 — 앱이 보여주는 것이 이쪽이다.
            attrs["grade"] = level
        if self._kind in AIRONE_INFERRED_UNITS:
            # 앱에서 뽑은 단위가 아니라 판단으로 정한 것이다. 밖에서 볼 수 있게 남긴다.
            attrs["unit_inferred"] = True
        return attrs


class AironeAirSensor(_AirSensorMixin, AironeEntity, SensorEntity):
    """공기질 항목. 에어모니터가 없으면 본체 기기에 붙는다."""

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        kind: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._setup_air(device, kind, device.device_id)


class AironeMonitorSensor(_AirSensorMixin, AironeMonitorEntity, SensorEntity):
    """공기질 항목. 에어모니터 기기에 붙는다."""

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        monitor: dict[str, Any],
        kind: str,
    ) -> None:
        super().__init__(coordinator, device, monitor)
        self._setup_air(device, kind, self._monitor_id)


class AironeLegacySensor(AironeEntity, SensorEntity):
    """구세대 상태 프레임이 더 싣는 값.

    뜻이 확인된 것만 이름을 붙였고, 값은 해석하지 않고 그대로 보여준다 —
    1/2 플래그 체계가 확인되지 않아 켜짐·꺼짐으로 옮기지 않는다.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-braces"

    def __init__(self, coordinator, device, key: str, label: str, table: str | None) -> None:
        super().__init__(coordinator, device)
        self._key = key
        self._table = LEGACY_VALUE_TABLES.get(table or "")
        self._attr_unique_id = f"{device.device_id}_legacy_{key}"
        self._attr_name = label
        # 필터 사용 시간의 단위를 확인하지 못했다. 시간인지 분인지 모르는 채로
        # 「h」 를 붙이면 60배 틀린 값이 그럴듯하게 보인다. 숫자만 둔다.

    @property
    def native_value(self):
        value = self.device.legacy_extras.get(self._key)
        if self._table is None or value is None:
            return value
        # 표에 없는 값은 숫자를 그대로 보여준다 — 모르는 값을 숨기지 않는다.
        return self._table.get(value, value)


class AironeFilterSensor(AironeEntity, SensorEntity):
    """필터 **잔량**(%). 실외기가 알려준 필터마다 하나씩.

    **필드 이름과 뜻이 반대다.** 값은 `odu.filter[i].usage.percent` 에서 오고
    이름만 보면 「쓴 만큼」 같지만 남은 수명이다 — 87 이면 87% 남았고 13% 썼다.
    실기기에서 나비엔 앱 표시와 대조해 확인했다.
    """

    _attr_icon = "mdi:air-filter"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: AironeDevice,
        index: int,
    ) -> None:
        super().__init__(coordinator, device)
        self._index = index
        self._attr_unique_id = f"{device.device_id}_filter_{index}"
        # 필터 `type` 의 뜻이 확인되지 않았다. 이름에 종류를 적지 않고 번호만 쓴다.
        count = len(device.filter_types)
        self._attr_name = "필터 잔량" if count == 1 else f"필터 {index + 1} 잔량"

    @property
    def _raw(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        filters = device.filters
        return filters[self._index] if self._index < len(filters) else None

    @property
    def native_value(self) -> int | None:
        raw = self._raw
        return None if raw is None else raw.get("percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        raw = self._raw
        if raw is None:
            return None
        return {
            "filter_type": raw.get("type"),
            "replace_period": raw.get("replace_period"),
        }


# -- 보일러 (v0.22.0 베타) --------------------------------------------------

# (키, 이름, `{name}Use` 의 name). Use 가 None 이면 기능과 상관없이 만든다.
_BOILER_SETTINGS = (
    ("insideTemperatureSetting", "실내 설정 온도", "inside"),
    ("ondolTemperatureSetting", "온돌 설정 온도", "ondol"),
    ("hotWaterTemperatureSetting", "온수 설정 온도", "hotWaterTemperatureSetting"),
)


def boiler_sensors(coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> list[SensorEntity]:
    """지원하는 것만 만든다. 기능 목록(`feature`)이 들어온 뒤에 불린다."""
    if boiler.is_multi_room:
        entities: list[SensorEntity] = []
        for room in range(1, boiler.room_count + 1):
            entities.append(BoilerRoomModeSensor(coordinator, boiler, room))
            entities.append(
                BoilerRoomTemperatureSensor(coordinator, boiler, room, "insideTemperature", "온도")
            )
            entities.append(
                BoilerRoomTemperatureSensor(
                    coordinator, boiler, room, "insideTemperatureSetting", "설정 온도"
                )
            )
        if boiler.supports("hotWaterTemperatureSetting"):
            entities.append(
                BoilerRoomTemperatureSensor(
                    coordinator, boiler, 1, "hotWaterTemperatureSetting", "온수 설정 온도", room_label=False
                )
            )
        return entities

    entities = [
        BoilerModeSensor(coordinator, boiler),
        BoilerTemperatureSensor(coordinator, boiler, "insideTemperature", "실내 온도", measured=True),
        BoilerErrorSensor(coordinator, boiler),
    ]
    for key, name, use in _BOILER_SETTINGS:
        if boiler.supports(use):
            entities.append(BoilerTemperatureSensor(coordinator, boiler, key, name))
    # 현재 온수 온도는 NR-6x 만 보낸다 (NR-40D status 에 없음, #24).
    if boiler.is_mgpp:
        entities.append(
            BoilerTemperatureSensor(coordinator, boiler, "hotWaterTemperature", "온수 온도", measured=True)
        )
        # v0.25.0 (#29). 값의 뜻 · 배율을 NR-6x 에서만 확인했다 — NR-40D 는 열지 않는다.
        entities += [
            BoilerOperatingStateSensor(coordinator, boiler),
            BoilerTemperatureSensor(coordinator, boiler, "supplyTemperature", "난방수 공급 온도", measured=True),
            BoilerTemperatureSensor(coordinator, boiler, "returnTemperature", "난방수 환수 온도", measured=True),
            BoilerOutsideTemperatureSensor(coordinator, boiler),
        ]
        if boiler.supports("insideHumidity"):
            entities.append(BoilerHumiditySensor(coordinator, boiler))
    return entities


class BoilerModeSensor(BoilerEntity, SensorEntity):
    """`operationMode` 를 앱과 같은 이름으로."""

    _attr_name = "운전 모드"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_mode"

    @property
    def native_value(self) -> str | None:
        boiler = self.boiler
        return None if boiler is None else boiler.mode_name(boiler.operation_mode)


class BoilerTemperatureSensor(BoilerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        boiler: BoilerDevice,
        key: str,
        name: str,
        measured: bool = False,
    ) -> None:
        super().__init__(coordinator, boiler)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{boiler.device_id}_boiler_{key}"
        if measured:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        boiler = self.boiler
        return None if boiler is None else boiler.value(self._key)


class BoilerErrorSensor(BoilerEntity, SensorEntity):
    _attr_name = "오류 코드"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_error"

    @property
    def native_value(self) -> int | None:
        boiler = self.boiler
        value = None if boiler is None else boiler.status.get("errorCode")
        return value if isinstance(value, int) else None


class BoilerRoomModeSensor(BoilerEntity, SensorEntity):
    """각방의 방 하나. **1 만 꺼짐으로 확정**이다 (앱 `hiStatusInt` 가 1 이 아니면
    켜진 방으로 센다). 나머지 값의 뜻은 확인하지 못해 「켜짐」으로만 보인다.
    """

    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice, room: int) -> None:
        super().__init__(coordinator, boiler)
        self._room = room
        self._attr_name = f"방 {room}"
        self._attr_unique_id = f"{boiler.device_id}_room{room}_mode"

    @property
    def native_value(self) -> str | None:
        boiler = self.boiler
        mode = None if boiler is None else (boiler.rooms.get(self._room) or {}).get("operationMode")
        if not isinstance(mode, (int, float)):
            return None
        return "꺼짐" if mode == 1 else "켜짐"


class BoilerRoomTemperatureSensor(BoilerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        boiler: BoilerDevice,
        room: int,
        key: str,
        name: str,
        room_label: bool = True,
    ) -> None:
        super().__init__(coordinator, boiler)
        self._room = room
        self._key = key
        self._attr_name = f"방 {room} {name}" if room_label else name
        self._attr_unique_id = f"{boiler.device_id}_room{room}_{key}"
        if key == "insideTemperature":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        boiler = self.boiler
        return None if boiler is None else boiler.room_value(self._room, self._key)


class BoilerOperatingStateSensor(BoilerEntity, SensorEntity):
    """연소 · 대기 · 꺼짐 (`operationBusy`). 「운전 모드」는 고른 모드, 이건 지금 하는 일."""

    _attr_name = "운전 상태"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_operating_state"

    @property
    def native_value(self) -> str | None:
        boiler = self.boiler
        return None if boiler is None else boiler.operating_state


class BoilerHumiditySensor(BoilerEntity, SensorEntity):
    """실내조절기가 잰 습도. `insideHumidity` ×10 (#20: 355 → 35.5%)."""

    _attr_name = "실내 습도"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_humidity"

    @property
    def native_value(self) -> float | None:
        boiler = self.boiler
        return None if boiler is None else boiler.tenth("insideHumidity")


class BoilerOutsideTemperatureSensor(BoilerEntity, SensorEntity):
    """외기 온도. **보일러가 잰 값이 아니라 지역 기상 관측값이다** (#29: 같은 시각
    기상청 값과 정확히 같고 늘 정수, 외기 센서가 없는 기기에도 온다). 서버가 날씨를
    넣어 주는 메시지(`control/weather`)도 우리 기록에 있다. `outsideTemperature` ×10.
    """

    _attr_name = "외기 온도 (기상 관측)"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NavienSmartCoordinator, boiler: BoilerDevice) -> None:
        super().__init__(coordinator, boiler)
        self._attr_unique_id = f"{boiler.device_id}_boiler_outside_temperature"

    @property
    def native_value(self) -> float | None:
        boiler = self.boiler
        return None if boiler is None else boiler.tenth("outsideTemperature")
