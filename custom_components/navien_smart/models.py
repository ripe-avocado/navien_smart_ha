"""기기 응답과 shadow 상태를 통합이 쓰기 쉬운 형태로 정리한다.

실측에서 나온 함정을 여기서 흡수한다.

- `functions` 는 모델마다 키가 빠진다. 없는 기능은 엔티티를 만들지 않는다
- `heater.single` 이 `null` 로 함께 온다. 키 존재 여부로 판단하면 틀린다
- 싱글/더블은 `mcu.capacity` 로 가른다. `mcu.matType` 이 아니다
- `sleepMode` 는 `functions` 쪽과 상태 쪽 구조가 다르다. 섞지 않는다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    CAPACITY_DOUBLE,
    MODE_NAMES,
    MODES_ON,
    SEASON_NAMES,
    SEASON_SUMMER,
    SERVICE_NAMES,
    UNIT_CELSIUS,
    UNIT_LEVEL,
    ZONE_LEFT,
    ZONE_RIGHT,
    ZONE_SINGLE,
)


def _dig(source: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(source, dict):
            return None
        source = source.get(key)
    return source


@dataclass(slots=True)
class HeatControl:
    """`functions.heatControl` 또는 `functions.coolControl`.

    두 구조는 같고, 냉방에만 `fanRPM` 과 `antiCondensation` 이 더 붙는다.
    펠티어 냉각이라 팬으로 열을 빼고 결로를 잡아야 하기 때문이다.
    """

    unit: str | None
    range_min: float | None
    range_max: float | None
    safe_value: float | None
    enable_safe: bool
    fan_rpm: int | None = None
    anti_condensation: bool | None = None

    @property
    def is_level(self) -> bool:
        return self.unit == UNIT_LEVEL

    @property
    def is_celsius(self) -> bool:
        return self.unit == UNIT_CELSIUS

    @property
    def is_known(self) -> bool:
        return self.is_level or self.is_celsius

    @property
    def step(self) -> float:
        """`<간격><축>` 인코딩의 앞쪽 숫자."""
        if self.is_celsius:
            return 0.5
        return 1.0

    @classmethod
    def parse(cls, raw: Any) -> HeatControl | None:
        if not isinstance(raw, dict):
            return None
        return cls(
            unit=raw.get("unit"),
            range_min=raw.get("rangeMin"),
            range_max=raw.get("rangeMax"),
            safe_value=raw.get("safeValue"),
            enable_safe=bool(raw.get("enableSafe")),
            fan_rpm=raw.get("fanRPM"),
            anti_condensation=raw.get("antiCondensation"),
        )

    def as_diagnostics(self) -> dict[str, Any]:
        """제보용. 미지원 축의 실제 값을 사용자가 그대로 붙일 수 있게 노출한다."""
        data: dict[str, Any] = {
            "unit": self.unit,
            "range_min": self.range_min,
            "range_max": self.range_max,
            "safe_value": self.safe_value,
        }
        if self.fan_rpm is not None:
            data["fan_rpm"] = self.fan_rpm
        if self.anti_condensation is not None:
            data["anti_condensation"] = self.anti_condensation
        return data


@dataclass(slots=True)
class NavienDevice:
    """기기 하나. `reported` 는 MQTT 로 들어올 때마다 갈린다."""

    device_seq: int
    device_id: str
    service_code: int
    model_code: str
    model_name: str
    nickname: str
    model_type: str | None
    capacity: int | None
    zone_names: dict[str, str]
    heat_control: HeatControl | None
    cool_control: HeatControl | None
    has_power_ctrl: bool
    has_lock_mode: bool
    has_power_saving: bool
    has_sleep_mode: bool
    sleep_durations: list[int]
    schedule_kinds: tuple[str, ...]
    connected_registry: bool
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    reported: dict[str, Any] = field(repr=False, default_factory=dict)

    # -- 생성 --------------------------------------------------------------

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> NavienDevice | None:
        device_id = raw.get("deviceId")
        device_seq = raw.get("deviceSeq")
        service_code = raw.get("serviceCode")
        if not device_id or device_seq is None or service_code is None:
            return None

        attrs = _dig(raw, "Properties", "registry", "attributes") or {}
        functions = attrs.get("functions") or {}
        mcu = attrs.get("mcu") or {}
        nick = _dig(raw, "Properties", "nickName") or {}
        side = nick.get("side") or {}

        capacity = mcu.get("capacity")
        if capacity == CAPACITY_DOUBLE or side:
            zone_names = {
                ZONE_LEFT: side.get("left") or "좌측",
                ZONE_RIGHT: side.get("right") or "우측",
            }
        else:
            zone_names = {ZONE_SINGLE: "난방"}

        sleep = functions.get("sleepMode") or {}
        schedule = functions.get("schedule") or {}

        return cls(
            device_seq=int(device_seq),
            device_id=str(device_id),
            service_code=int(service_code),
            model_code=str(raw.get("modelCode") or ""),
            model_name=str(raw.get("modelName") or attrs.get("model") or "나비엔"),
            nickname=str(nick.get("mainItem") or raw.get("modelName") or "나비엔 기기"),
            model_type=attrs.get("modelType"),
            capacity=capacity,
            zone_names=zone_names,
            heat_control=HeatControl.parse(functions.get("heatControl")),
            cool_control=HeatControl.parse(functions.get("coolControl")),
            has_power_ctrl=bool(functions.get("powerCtrl")),
            has_lock_mode=bool(functions.get("lockMode")),
            has_power_saving=bool(functions.get("powerSaving")),
            has_sleep_mode=bool(sleep.get("enable")),
            sleep_durations=list(sleep.get("durations") or []),
            schedule_kinds=tuple(
                kind for kind in ("oneTime", "weekly", "personal") if schedule.get(kind)
            ),
            connected_registry=bool(raw.get("connected")),
            raw=raw,
        )

    # -- 상태 --------------------------------------------------------------

    @property
    def zones(self) -> tuple[str, ...]:
        return tuple(self.zone_names)

    @property
    def is_double(self) -> bool:
        return ZONE_LEFT in self.zone_names

    @property
    def available(self) -> bool:
        """기기가 `reported.connected` 로 직접 보고한 값을 우선한다."""
        if "connected" in self.reported:
            return bool(self.reported.get("connected"))
        return self.connected_registry

    @property
    def operation_mode(self) -> int | None:
        value = self.reported.get("operationMode")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def is_on(self) -> bool:
        return self.operation_mode in MODES_ON

    @property
    def mode_name(self) -> str | None:
        mode = self.operation_mode
        if mode is None:
            return None
        return MODE_NAMES.get(mode, f"알 수 없음({mode})")

    @property
    def is_four_season(self) -> bool:
        """`coolControl` 이 오면 사계절 모델이다.

        앱은 `modelCode` 하드코딩 표(`setModelCodeAndFunction`)로 판정하지만,
        그 표는 앱 버전에 묶여 있어 새 모델을 못 잡는다. 서버가 주는
        `coolControl` 유무가 더 오래 버틴다.
        """
        return self.cool_control is not None

    @property
    def season(self) -> int | None:
        value = self.reported.get("season")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def season_name(self) -> str | None:
        season = self.season
        if season is None:
            return None
        return SEASON_NAMES.get(season, f"알 수 없음({season})")

    @property
    def is_cooling(self) -> bool:
        """냉방 모드인가.

        냉방이면 `heater` 의 설정값을 `coolControl` 범위로 읽어야 한다. 그 값
        체계가 확인되지 않았으므로 이 구간에서는 난방 제어를 내보내지 않는다.
        """
        return self.season == SEASON_SUMMER

    @property
    def active_control(self) -> HeatControl | None:
        """지금 적용되는 제어 서술자. 여름이면 `coolControl`."""
        if self.is_cooling and self.cool_control is not None:
            return self.cool_control
        return self.heat_control

    @property
    def error_code(self) -> int | None:
        value = self.reported.get("errorCode")
        return int(value) if isinstance(value, (int, float)) else None

    def zone_state(self, zone: str) -> dict[str, Any] | None:
        """`heater.<zone>`. `null` 은 없는 것으로 취급한다."""
        heater = self.reported.get("heater")
        if not isinstance(heater, dict):
            return None
        state = heater.get(zone)
        return state if isinstance(state, dict) else None

    def zone_setting(self, zone: str) -> float | None:
        """설정값. 단계형이면 `level.set`, 온도형이면 `temperature.set`.

        단계형은 `temperature.current` 를 주지 않는다 — 설정값이 곧 표시값이다.
        """
        state = self.zone_state(zone)
        if state is None:
            return None
        level = state.get("level")
        if isinstance(level, dict) and level.get("set") is not None:
            return float(level["set"])
        temperature = state.get("temperature")
        if isinstance(temperature, dict) and temperature.get("set") is not None:
            return float(temperature["set"])
        return None

    def zone_current(self, zone: str) -> float | None:
        """현재값. 온도형만 온다. 단계형은 `None`."""
        state = self.zone_state(zone)
        if state is None:
            return None
        temperature = state.get("temperature")
        if isinstance(temperature, dict) and temperature.get("current") is not None:
            return float(temperature["current"])
        return None

    def zone_enabled(self, zone: str) -> bool | None:
        state = self.zone_state(zone)
        if state is None:
            return None
        value = state.get("enable")
        return bool(value) if value is not None else None

    @property
    def over_safe_value(self) -> bool:
        """고온경고선을 넘었는지. 제어 상한이 아니라 경고 표시용이다.

        냉방 중에는 판정하지 않는다. `coolControl.safeValue` 가 무엇을 뜻하는지
        확인되지 않았다 — 하한일 수도 있고 결로 기준일 수도 있다. 난방 기준으로
        비교하면 냉방 설정을 과열로 잘못 알린다.
        """
        if self.is_cooling:
            return False
        control = self.heat_control
        if control is None or not control.enable_safe or control.safe_value is None:
            return False
        return any(
            (value := self.zone_setting(zone)) is not None and value > control.safe_value
            for zone in self.zones
        )

    @property
    def service_name(self) -> str:
        return SERVICE_NAMES.get(self.service_code, str(self.service_code))

    def build_heater_desired(
        self,
        changes: dict[str, float] | None = None,
        enables: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """`heater` desired 를 만든다.

        앱은 바뀌지 않은 구역까지 현재값을 함께 보낸다. shadow 병합에 기대지 않고
        같은 방식을 따른다 — 실측으로 검증한 형태다.

        `enables` 로 구역별 `enable` 을 덮어쓸 수 있다. `enable: true` 는 실측으로
        검증했으나 **`false` 는 검증하지 않았다** — 온도형 기기가 없어 확인할 수
        없었다.
        """
        changes = changes or {}
        enables = enables or {}
        if self.is_cooling:
            # 냉방 값 체계가 확인되지 않았다. 추측해서 보내지 않는다.
            raise ValueError(
                "냉방 모드에서는 제어를 보내지 않습니다 (냉방 값 체계 미확인)."
            )
        control = self.heat_control
        if control is None or not control.is_known:
            raise ValueError(f"제어 축을 모르는 기기입니다 (unit={control.unit if control else None})")

        axis = "level" if control.is_level else "temperature"
        heater: dict[str, Any] = {}
        for zone in self.zones:
            value = changes.get(zone, self.zone_setting(zone))
            if value is None:
                continue
            number: Any = int(value) if control.is_level else float(value)
            if zone in enables:
                enabled = enables[zone]
            else:
                current = self.zone_enabled(zone)
                enabled = True if current is None else current
            heater[zone] = {"enable": enabled, axis: {"set": number}}
        if not heater:
            raise ValueError("보낼 구역 값이 없습니다.")
        return heater
