"""온도형 매트의 온도조절기 (`heatControl.unit == "0.5C"`).

온도형은 `temperature.current` 를 함께 보내주므로 `climate` 가 맞다 — 서모스탯
카드의 현재 온도 칸이 채워진다.

단계형(`1.0L`)은 이 플랫폼을 만들지 않는다. 현재값이 오지 않아 카드 절반이 비고,
단계를 도(°)로 표시하면 사용자가 오해한다. 그쪽은 `number` 슬라이더를 쓴다.

**주의 — 온도형 매트를 직접 눌러본 적은 없다.** 집에 단계형만 있다. 사계절
모델(EMF520)의 난방·냉방 표시와 제어는 **제보로 확인됐다.**
구조는 앱 코드로 확정했으나, 특히 `enable: false` 전송은 확인하지 못했다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienSmartConfigEntry
from .const import MODE_HEAT, ZONE_NAMES
from .coordinator import NavienSmartCoordinator
from .entity import NavienSmartEntity
from .models import NavienDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NavienSmartThermostat] = []
    for device in (coordinator.data or {}).values():
        control = device.heat_control
        if control is None or not control.is_celsius:
            continue
        entities.extend(
            NavienSmartThermostat(coordinator, device, zone) for zone in device.zones
        )
    async_add_entities(entities)


class NavienSmartThermostat(NavienSmartEntity, ClimateEntity):
    """구역 하나의 난방 온도."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: NavienSmartCoordinator,
        device: NavienDevice,
        zone: str,
    ) -> None:
        super().__init__(coordinator, device)
        self._zone = zone
        self._attr_unique_id = f"{device.device_id}_{zone}_thermostat"

        label = device.zone_names.get(zone) or ZONE_NAMES.get(zone, zone)
        self._attr_name = label if device.is_double else "난방"

        # 범위를 `__init__` 에 고정하지 않는다. 사계절 모델은 사용자가 앱에서
        # WARM/COOL 을 바꾸면 **범위 자체가 갈린다** (난방 28~45 / 냉방 20~35).
        # 고정해두면 냉방 설정값이 자기 최소값보다 낮아 카드가 깨진다.
        assert device.heat_control is not None  # setup 에서 걸러진다

    @property
    def _control(self) -> Any:
        """지금 적용되는 제어 서술자. 냉방이면 `coolControl`."""
        device = self.device
        return device.active_control if device is not None else None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """냉방 중에는 냉방만, 그 밖에는 난방만 보여준다.

        `season`(WARM/COOL) 은 **앱에서 고르는 모드**이고 우리가 바꾸는 방법을
        확인하지 못했다. 그래서 HA 에서 난방↔냉방 전환을 제공하지 않는다 —
        고를 수 있는 것처럼 보이면 눌렀을 때 아무 일도 안 일어난다.
        """
        device = self.device
        cooling = device is not None and device.is_cooling
        return [HVACMode.OFF, HVACMode.COOL if cooling else HVACMode.HEAT]

    @property
    def min_temp(self) -> float:
        control = self._control
        return float((control.range_min if control else None) or 20)

    @property
    def max_temp(self) -> float:
        control = self._control
        return float((control.range_max if control else None) or 45)

    @property
    def target_temperature_step(self) -> float:
        control = self._control
        return control.step if control else 0.5

    @property
    def available(self) -> bool:
        """냉방 중에도 쓴다.

        v0.9.0 까지는 냉방이면 손을 뗐다 — 값 체계를 몰랐기 때문이다. 이제
        `coolControl`(범위·간격·고온경고선)이 서버에서 오고, 설정값이 난방과 같은
        `heater.<구역>.temperature.set` 으로 오는 것을 실기기 제보로 관측했다.

        다만 `season` 이 **`SEASON_SUMMER`(2)** 일 때만 냉방으로 다룬다. 값이
        없거나 모르는 값이면 난방으로 두므로, 잘못된 범위를 쓸 일이 없다.
        """
        return super().available

    @property
    def current_temperature(self) -> float | None:
        device = self.device
        return None if device is None else device.zone_current(self._zone)

    @property
    def target_temperature(self) -> float | None:
        device = self.device
        return None if device is None else device.zone_setting(self._zone)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """기기 전원과 구역 `enable` 을 함께 본다.

        기기가 꺼져 있으면 구역이 켜져 있어도 난방하지 않는다.
        """
        device = self.device
        if device is None:
            return None
        # **모르는 것을 「꺼짐」이라 하지 않는다.** `is_on` 은 `operationMode` 가
        # 없을 때 `False` 를 돌려주는데, 그것을 그대로 쓰면 상태가 아직 안 온
        # 기기를 껐다고 단정한다 — 사계절 제보에서 좌우 모두 「꺼짐」으로 보인
        # 원인이다.
        if device.operation_mode is None:
            return None
        if not device.is_on:
            return HVACMode.OFF
        enabled = device.zone_enabled(self._zone)
        running = HVACMode.COOL if device.is_cooling else HVACMode.HEAT
        return running if enabled is not False else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        mode = self.hvac_mode
        if mode is None:
            return None
        if mode is HVACMode.HEAT:
            return HVACAction.HEATING
        if mode is HVACMode.COOL:
            return HVACAction.COOLING
        return HVACAction.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is None:
            return None
        attrs: dict[str, Any] = {"zone": self._zone}
        control = device.heat_control
        if control is not None and control.safe_value is not None:
            # 고온경고 기준선. 상한이 아니다.
            attrs["high_temp_warning_temperature"] = control.safe_value
        if device.is_four_season:
            attrs["four_season"] = True
            attrs["season"] = device.season
        return attrs

    @property
    def _target_zones(self) -> tuple[str, ...]:
        """이 조작이 적용될 구역 — **언제나 이 구역 하나다.**

        v0.9.0~v0.11.0 은 냉방+좌우분리면 두 구역에 같은 값을 보냈다. 근거는 앱
        안내문이었다.

            COOL 모드 — 매트의 좌우가 같은 온도로 동작합니다

        **모델 얘기를 빠뜨린 문구였다.** 나비엔 제품 페이지는 이렇게 적는다.

            0.5℃ 분리 냉난방 기술로 좌우 원하는 온도로
            해당 기능은 **사계절형 Pro 모델에만** 적용됩니다

        즉 Pro 는 냉방에서도 좌우가 따로 가고 Air 는 같이 간다. **모델별 동작을
        코드에 박아넣은 셈이었고**, 그건 이 통합이 하지 않기로 한 것이다.
        서버가 Pro/Air 를 알려주지도 않는다.

        그래서 **누르신 구역에만 보낸다.** 기기가 좌우를 묶어 도는 모델이면
        응답으로 두 값을 같게 돌려줄 것이고, 우리는 그것을 그대로 보여준다.
        **기기가 하는 일을 앞질러 정하지 않는다.**
        """
        return (self._zone,)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get("temperature")
        device = self.device
        if device is None or temperature is None:
            return
        value = float(temperature)
        zones = self._target_zones
        heater = device.build_heater_desired(
            changes={zone: value for zone in zones},
            enables={zone: True for zone in zones},
        )
        await self.coordinator.async_send(
            device, {"operationMode": MODE_HEAT, "heater": heater}
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        device = self.device
        if device is None:
            return
        zones = self._target_zones
        if hvac_mode is not HVACMode.OFF:
            # 난방이든 냉방이든 켜는 방법은 같다. 난방·냉방을 가르는 것은 `season`
            # 이고 그건 앱에서 고른다.
            #
            # **꺼져 있던 구역은 값도 함께 올려야 한다** (이슈 #16). 예전에는
            # `enable: true` 만 보내고 온도는 27.5(=꺼짐) 그대로 다시 보내서,
            # 기기 전원만 켜지고 그 구역은 꺼진 채로 남았다.
            await self.coordinator.async_send(device, device.build_zone_on(zones))
            return
        # **`enable: false` 만으로는 안 꺼진다** (이슈 #16). 값을 `off_value` 까지
        # 내려야 기기가 받는다. 남는 구역이 없으면 전원 끄기로 돌아간다 —
        # 판단은 `build_zone_off` 안에 있다.
        await self.coordinator.async_send(device, device.build_zone_off(zones))
