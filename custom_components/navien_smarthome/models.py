"""기기 응답과 shadow 상태를 통합이 쓰기 쉬운 형태로 정리한다.

실측에서 나온 함정을 여기서 흡수한다.

- `functions` 는 모델마다 키가 빠진다. 없는 기능은 엔티티를 만들지 않는다
- `heater.single` 이 `null` 로 함께 온다. 키 존재 여부로 판단하면 틀린다
- 싱글/더블은 `mcu.capacity` 로 가른다. `mcu.matType` 이 아니다
- `sleepMode` 는 `functions` 쪽과 상태 쪽 구조가 다르다. 섞지 않는다
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CAPACITY_DOUBLE,
    MAT_ERROR_NAMES,
    MAT_VOLUME_NAMES,
    MODE_HEAT,
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


# 진단에 남길 기록 개수. 순서를 보는 것이 목적이라 길 필요가 없다.
_LOG_KEEP = 8


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """딕셔너리를 깊이까지 겹쳐 쓴다. 목록과 그 밖의 값은 통째로 바꾼다."""
    merged = dict(base)
    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def _version_text(current: Any) -> str | None:
    """`{major, minor, build}` 를 `14.0.0` 으로 옮긴다.

    매트는 MCU 와 Wi-Fi 모듈이 각자 펌웨어를 가진다 (실측 MCU 14.0.0 / Wi-Fi 5.1.100).
    """
    if not isinstance(current, dict):
        return None
    parts = [current.get(key) for key in ("major", "minor", "build")]
    if any(part is None for part in parts):
        return None
    return ".".join(str(int(part)) for part in parts)


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
    has_beep: bool
    has_lock_mode: bool
    has_power_saving: bool
    has_sleep_mode: bool
    sleep_durations: list[int]
    schedule_kinds: tuple[str, ...]
    connected_registry: bool
    mcu_version: str | None
    wifi_version: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    reported: dict[str, Any] = field(repr=False, default_factory=dict)
    # **무엇을 보냈고 무엇이 돌아왔는지** 짧게 남긴다. 냉방은 값 체계를 실기기로
    # 확인하지 못한 구간이라, 「보낸 값이 그대로 돌아오는가」를 봐야 닫힌다.
    # 개인정보는 담지 않는다 — 모드 번호와 온도·단계 값뿐이다.
    command_log: list[dict[str, Any]] = field(repr=False, default_factory=list)
    state_log: list[dict[str, Any]] = field(repr=False, default_factory=list)

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
        # **문자열로 올 수도 있다.** 매트는 `{"mainItem": ..., "side": {...}}` 인데
        # 별칭을 안 나눠 쓰는 계정에서 그냥 이름 하나로 오는 경우를 배제할 근거가
        # 없다. 그때 `.get` 을 부르면 통합 전체가 설정 단계에서 죽는다 —
        # 기기 하나가 아니라 **전부** 안 보인다. 문자열이면 이름으로 쓴다.
        raw_nick = _dig(raw, "Properties", "nickName")
        nick = raw_nick if isinstance(raw_nick, dict) else {}
        nick_text = raw_nick.strip() if isinstance(raw_nick, str) else ""
        side = nick.get("side") if isinstance(nick.get("side"), dict) else {}

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
            nickname=str(
                nick.get("mainItem") or nick_text or raw.get("modelName") or "나비엔 기기"
            ),
            model_type=attrs.get("modelType"),
            capacity=capacity,
            zone_names=zone_names,
            heat_control=HeatControl.parse(functions.get("heatControl")),
            cool_control=HeatControl.parse(functions.get("coolControl")),
            has_power_ctrl=bool(functions.get("powerCtrl")),
            # 앱의 `Functions` 클래스에는 `beep` 필드가 아예 없다 — 서버는 주는데
            # 앱이 안 읽는다. 우리는 음량 엔티티를 만들지 말지 가르는 데 쓴다.
            has_beep=bool(functions.get("beep")),
            has_lock_mode=bool(functions.get("lockMode")),
            has_power_saving=bool(functions.get("powerSaving")),
            has_sleep_mode=bool(sleep.get("enable")),
            sleep_durations=list(sleep.get("durations") or []),
            schedule_kinds=tuple(
                kind for kind in ("oneTime", "weekly", "personal") if schedule.get(kind)
            ),
            connected_registry=bool(raw.get("connected")),
            mcu_version=_version_text(_dig(mcu, "version", "current")),
            wifi_version=_version_text(_dig(attrs, "wifi", "version", "current")),
            raw=raw,
        )

    # -- 상태 반영 ----------------------------------------------------------

    def apply_reported(self, incoming: dict[str, Any]) -> None:
        """들어온 상태를 **덮어쓰지 않고 겹쳐 쓴다.**

        내 EME-500 두 대는 shadow 가 항상 전체를 준다 — `heater` 에 `single`·`left`·
        `right` 가 늘 함께 온다. 그것을 보고 **매트 전체가 그렇다고 단정했다.**

        틀렸다. 사계절(EMF520) 제보에서 `heater.right` 하나만 담긴 응답이 왔고,
        통째로 갈아끼우는 바람에 `operationMode` · `season` · `heater.left` 가
        사라졌다. 그래서 전원이 「알 수 없음」이 되고 좌우가 번갈아 비었다.

        딕셔너리는 **깊이 상관없이** 겹쳐 쓴다 — `heater.right.temperature` 만 온
        경우에도 `level` 이나 `enable` 을 잃지 않아야 한다.
        목록은 통째로 바꾼다(부분 목록을 항목별로 섞으면 자리가 어긋난다).

        오래된 값이 남을 수 있다는 것은 감수한다. **전부 「알 수 없음」이 되는 것보다
        낫다.**
        """
        self.reported = _merge(self.reported or {}, incoming)
        self._note_state()

    def _note_state(self) -> None:
        """상태가 바뀌면 한 줄 남긴다. 같은 값은 쌓지 않는다.

        사계절 냉방을 닫으려면 **`season` 값이 실제로 무엇인지**와 **보낸 값이
        그대로 돌아오는지**를 봐야 한다. 그 순간의 값만으로는 알 수 없다.
        """
        entry: dict[str, Any] = {
            "operationMode": self.operation_mode,
            "season": self.season,
            "cooling": self.is_cooling,
            "zones": {
                zone: {
                    "set": self._zone_setting_raw(zone),
                    "current": self._zone_current_raw(zone),
                    "enable": self._zone_enabled_raw(zone),
                }
                for zone in self.zones
            },
        }
        if self.state_log and {
            k: v for k, v in self.state_log[-1].items() if k != "at"
        } == entry:
            return
        self.state_log.append({**entry, "at": round(time.monotonic(), 1)})
        del self.state_log[:-_LOG_KEEP]

    def note_command(self, desired: dict[str, Any]) -> None:
        """보낸 명령을 한 줄 남긴다."""
        heater = desired.get("heater") or {}
        self.command_log.append(
            {
                "operationMode": desired.get("operationMode"),
                "cooling_at_send": self.is_cooling,
                "season_at_send": self.season,
                "zones": {
                    zone: {
                        "enable": value.get("enable"),
                        "set": (
                            (value.get("temperature") or value.get("level") or {}).get("set")
                            if isinstance(value, dict)
                            else None
                        ),
                    }
                    for zone, value in heater.items()
                    if isinstance(value, dict)
                },
                "at": round(time.monotonic(), 1),
            }
        )
        del self.command_log[:-_LOG_KEEP]

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
        """운전 상태를 사람이 읽는 이름으로.

        **`operationMode` 는 계절을 모른다.** 난방이든 냉방이든 운전 중이면 1 이고,
        무엇을 하는지는 `season` 이 정한다. 그래서 표를 그대로 쓰면 냉방 중에도
        「난방」으로 보인다 — 실기기 제보로 확인했다(EMF520).

        사계절 모델이 냉방일 때만 바꾼다. 나머지는 표 그대로다.
        """
        mode = self.operation_mode
        if mode is None:
            return None
        if mode == MODE_HEAT and self.is_cooling:
            # 운전 중(1)일 때 무엇을 하는지는 `season` 이 정한다.
            return "냉방"
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
    def child_lock(self) -> bool | None:
        """조작 잠금 상태. `True` 면 잠겨 있다."""
        value = self.reported.get("childLock")
        return bool(value) if isinstance(value, bool) else None

    def build_child_lock_desired(self, locked: bool) -> dict[str, Any]:
        """조작 잠금 desired (`lock-on` / `lock-off`).

        **v0.12.0 에서 「WiFi 로는 못 잠근다」고 판단한 것을 정정한다.** 제보자가
        앱 제어 화면에 자물쇠 버튼이 있는 사진을 보내 다시 뒤졌더니 있었다.

            // MateWifiModelControlViewModel
            String str = !mateInfoData1.getLockState() ? "lock-on" : "lock-off";

            // MateConstants
            r11 = Boolean.valueOf(areEqual(r35, "lock-on"));   // childLock
            r4  = new Desired(r11, new Event(modelCode), null × 12);

        **계절 전환과 같은 모양**이고 토픽도 특별 분기가 없다 — `mateControlDevice`
        의 기본 분기(`.../shadow/name/status/update`)로 간다. 우리가 전원·온도·계절에
        이미 쓰는 그 토픽이다.
        """
        return {"childLock": bool(locked)}

    @property
    def volume(self) -> int | None:
        """조작음 음량. 앱과 같은 0~3."""
        value = self.reported.get("volume")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        number = int(value)
        return number if number in MAT_VOLUME_NAMES else None

    @property
    def volume_name(self) -> str | None:
        volume = self.volume
        return MAT_VOLUME_NAMES.get(volume) if volume is not None else None

    def build_volume_desired(self, volume: int) -> dict[str, Any]:
        """음량 desired (`control-volume`).

        앱 화면에 칸이 넷뿐이고 (`selectedIndex` 0·1·2·3) 그 값이 그대로
        `Desired.volume` 에 실린다. **그 밖의 값은 보내지 않는다.**
        """
        if volume not in MAT_VOLUME_NAMES:
            raise ValueError(f"확인된 음량 값이 아닙니다: {volume}")
        return {"volume": volume}

    @property
    def season_name(self) -> str | None:
        season = self.season
        if season is None:
            return None
        return SEASON_NAMES.get(season, f"알 수 없음({season})")

    @property
    def is_cooling(self) -> bool:
        """냉방(COOL) 모드인가.

        `season` 은 자동 상태가 아니라 **사용자가 앱에서 고르는 모드**다. 앱은
        WARM / COOL 로 부르고 예약 목록도 모드별로 따로 관리한다.

        **`SEASON_SUMMER`(2) 이고 냉방 기능이 있을 때만 냉방으로 본다.**
        모르는 값이 오면 난방으로 두고 로그를 남긴다 — 냉방 범위를 잘못 적용하는
        것보다 안전하다.

        `cool_control` 을 함께 보는 이유는, **냉방을 못 하는 기기가 `season` 을
        보내오면 냉방으로 읽혀서** 운전 상태가 「냉방」으로 보이고 온도 범위도
        엉뚱한 값으로 갈리기 때문이다. 그런 기기가 실제로 있는지는 모르지만,
        없는 기능을 켜는 쪽으로 틀리지 않게 둔다.
        """
        return self.season == SEASON_SUMMER and self.cool_control is not None

    @property
    def error_text(self) -> str | None:
        """오류 코드의 이름. 모르면 `None`.

        제보자가 기기 설명서에서 옮겨 준 표다. **온도형에만 붙인다** — 물탱크·
        순환펌프·누수가 나오는 것으로 보아 온수·사계절 계열 설명서이고, 카본
        (단계형)에 같은 번호가 같은 뜻이라는 근거가 없다.

        **상태값이 아니라 속성이다.** 숫자를 쓰던 자동화를 깨지 않는다.
        """
        control = self.heat_control
        if control is None or not control.is_celsius:
            return None
        code = self.error_code
        return None if code is None else MAT_ERROR_NAMES.get(code)

    @property
    def has_unknown_season(self) -> bool:
        """사계절 모델인데 `season` 값을 해석할 수 없는 상태인가."""
        season = self.season
        return (
            self.is_four_season
            and season is not None
            and season not in SEASON_NAMES
        )

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

    def _mirror_zone(self, zone: str) -> str | None:
        """냉방에서 값을 가져올 반대쪽 구역.

        **냉방은 좌우가 같은 온도로 동작한다** — 앱 도움말에 그렇게 적혀 있다
        (「COOL 모드 … 매트의 좌우가 같은 온도로 동작합니다」). 그래서 서버가
        한쪽만 채워 보내는 일이 있고, 그때 반대쪽 엔티티가 빈 채로 남는다.

        추측이 아니라 **같은 값이라고 문서화된 것**을 옮겨 쓰는 것이다.
        난방에서는 좌우가 독립이므로 절대 하지 않는다.
        """
        if not self.is_cooling or not self.is_double:
            return None
        return ZONE_RIGHT if zone == ZONE_LEFT else ZONE_LEFT

    def zone_setting(self, zone: str) -> float | None:
        """설정값. 단계형이면 `level.set`, 온도형이면 `temperature.set`.

        단계형은 `temperature.current` 를 주지 않는다 — 설정값이 곧 표시값이다.
        """
        value = self._zone_setting_raw(zone)
        if value is None and (mirror := self._mirror_zone(zone)) is not None:
            value = self._zone_setting_raw(mirror)
        return value

    def _zone_setting_raw(self, zone: str) -> float | None:
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
        value = self._zone_current_raw(zone)
        if value is None and (mirror := self._mirror_zone(zone)) is not None:
            value = self._zone_current_raw(mirror)
        return value

    def _zone_current_raw(self, zone: str) -> float | None:
        state = self.zone_state(zone)
        if state is None:
            return None
        temperature = state.get("temperature")
        if isinstance(temperature, dict) and temperature.get("current") is not None:
            return float(temperature["current"])
        return None

    def zone_enabled(self, zone: str) -> bool | None:
        value = self._zone_enabled_raw(zone)
        if value is None and (mirror := self._mirror_zone(zone)) is not None:
            value = self._zone_enabled_raw(mirror)
        return value

    def _zone_enabled_raw(self, zone: str) -> bool | None:
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

    def build_season_desired(self, season: int) -> dict[str, Any]:
        """계절(난방↔냉방) 전환 desired.

        **앱이 하는 것을 그대로 한다.** `MateConstants.mateMqttPayload("seasonSetting")`
        가 만드는 것은 `Desired(event=..., season=...)` 하나뿐이고, 토픽도 우리가
        이미 쓰는 shadow 업데이트와 같다. `event.modelCode` 는 `async_control` 이
        모든 명령에 붙이고 있다 — 실기기로 검증된 경로다.

        **값은 두 개뿐이다.** 앱 계절 설정 화면에 버튼이 둘이고
        (`onWinterIconClick` → 0, `onSummerIconClick` → 2) 세 번째 값은 없다.
        스마트싱스가 보여주는 `coolPlus` 는 그쪽 라벨이고 나비엔 값이 아니다 —
        앱 문자열 전수 검색에서 0건이다.

        그래서 **아는 값만 보낸다.** 모르는 값이 들어오면 거부한다.
        """
        if season not in SEASON_NAMES:
            raise ValueError(f"확인된 계절 값이 아닙니다: {season}")
        return {"season": season}

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
        # 냉방이면 `coolControl` 을 쓴다. 설정값 경로는 난방과 같은
        # `heater.<구역>.temperature.set` 이다 — 실기기 제보에서 냉방 설정값
        # 24.5(냉방 범위 20~35) 가 그 경로로 오는 것을 관측했다.
        #
        # 단계형(`1.0L`) 사계절 모델의 냉방은 아직 모른다. `select` 가 냉방에서
        # 손을 떼므로 여기까지 오지 않는다.
        control = self.active_control
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
            elif control.is_level and zone in changes:
                # 단계형은 `level 0` 과 `enable false` 가 함께 움직인다 — 실측 확인.
                # 앱 슬라이더의 맨 왼쪽 `운전 대기` 가 이 상태다.
                #
                # **바꾸는 구역에만 적용한다.** `zone in changes` 조건이 없으면,
                # 한쪽을 대기로 내릴 때 반대쪽 `enable` 까지 덮어써서 같이 꺼진다.
                enabled = number > 0
            else:
                current = self.zone_enabled(zone)
                enabled = True if current is None else current

            heater[zone] = {"enable": enabled, axis: {"set": number}}
        if not heater:
            raise ValueError("보낼 구역 값이 없습니다.")
        return heater
