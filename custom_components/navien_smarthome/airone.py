"""에어원(환기청정) 기기 모델.

매트와 **체계가 다르다.** 매트는 AWS shadow(`state.desired` / `state.reported`)를
쓰지만 에어원은 `cmd/rc/v2/...` 토픽에 직접 주고받는다. 그래서 같은 클래스로
묶지 않고 따로 둔다 — 검증이 끝난 매트 경로를 건드리지 않는 것이 우선이다.

여기 있는 필드명과 값은 전부 앱에서 확인했다(`AironeConstants`, `PubSubData`,
`ModeDid`, `RoomControllerStatus`). **다만 실기기로 검증하지 않았다.**
값 체계를 모르는 항목은 채우지 않고 비워 둔다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .const import (
    AIRONE_HUMIDITY_TYPE,
    AIRONE_LEVEL_NAMES,
    AIRONE_MODE_NAMES,
    AIRONE_MODE_SLEEP_LABEL,
    AIRONE_MODES_WITH_HUMIDITY,
    AIRONE_OPTION_NAMES,
    AIRONE_OPTION_NONE,
    AIRONE_OPTION_SLEEP,
    AIRONE_OPTIONS_WITH_WIND,
    AIRONE_RUN_NAMES,
    AIRONE_RUN_OFF,
    AIRONE_RUN_ON,
    AIRONE_SENSOR_ALIASES,
    AIRONE_SENSOR_KINDS,
    AIRONE_V2_MIN_MODEL_CODE,
    AIRONE_WIND_NAMES,
    airone_mode_label,
)

_LOGGER = logging.getLogger(__name__)


def _dig(source: Any, *keys: str) -> Any:
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_number(value: Any) -> float | None:
    """숫자로 읽히면 숫자, 아니면 None. 빈 문자열은 값이 없는 것으로 본다.

    공기질 값이 종류마다 숫자거나 문자열이라 판정이 필요하다 —
    `tvoc`·`radon`·`total` 은 앱이 등급으로 **표시**하지만 숫자가 온다.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def level_text(raw: dict[str, Any]) -> str | None:
    """공기질 등급을 한국어로. 서버가 이미 한국어로 주면 그대로 쓴다."""
    level = raw.get("level")
    if level is None or level == "":
        return None
    if isinstance(level, str) and not level.lstrip("-").isdigit():
        return level
    try:
        return AIRONE_LEVEL_NAMES.get(int(level))
    except (TypeError, ValueError):
        return str(level)


def _version_text(current: Any) -> str | None:
    if current is None:
        return None
    text = str(current).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class AironeMode:
    """서버가 알려준 운전 조합 하나 (`ModeDid`).

    `mode` 와 `option` 의 짝이 앱 화면의 버튼 하나에 대응한다. `air_volume` 은
    **단일값인지 비트마스크인지 확인되지 않았다** — 그래서 알려진 표에 없는 값은
    버린다 (명세 6-5).
    """

    mode: int
    option: int
    air_volume: int | None
    configurable: bool
    humidity_min: int | None
    humidity_max: int | None

    @property
    def key(self) -> tuple[int, int]:
        return (self.mode, self.option)

    @property
    def label(self) -> str:
        return airone_mode_label(self.mode, self.option)

    @property
    def wants_wind(self) -> bool:
        return self.option in AIRONE_OPTIONS_WITH_WIND

    @property
    def wants_humidity(self) -> bool:
        return (
            self.mode in AIRONE_MODES_WITH_HUMIDITY
            and self.humidity_min is not None
            and self.humidity_max is not None
            and self.humidity_min < self.humidity_max
        )

    @classmethod
    def parse(cls, raw: Any) -> AironeMode | None:
        if not isinstance(raw, dict):
            return None
        mode = _as_int(raw.get("name"))
        if mode is None:
            return None
        option = _as_int(raw.get("option"))
        wind = _as_int(raw.get("airVolume"))
        if wind is not None and wind not in AIRONE_WIND_NAMES:
            # 비트마스크일 수 있다. 모르는 값을 기기에 쏘지 않는다.
            _LOGGER.debug(
                "에어원 airVolume %s 는 확인된 값이 아니라 무시합니다 (mode=%s)", wind, mode
            )
            wind = None

        low = high = None
        for extra in raw.get("additionalData") or []:
            if not isinstance(extra, dict):
                continue
            if _as_int(extra.get("type")) != AIRONE_HUMIDITY_TYPE:
                continue
            low = _as_int(extra.get("min"))
            high = _as_int(extra.get("max"))
            break

        return cls(
            mode=mode,
            option=AIRONE_OPTION_NONE if option is None else option,
            air_volume=wind,
            # 서버가 안 주면 고를 수 있는 것으로 보지 않는다.
            configurable=bool(raw.get("configurable")),
            humidity_min=low,
            humidity_max=high,
        )


@dataclass(frozen=True, slots=True)
class AironeModeChoice:
    """운전 모드 목록의 항목 하나.

    앱은 모드와 풍량을 **다른 축**으로 다룬다. 터보·절전·기저는 풍량 쪽이고,
    숙면만 예외로 모드 쪽이다 (`AironeModeCode.rawToUi`).
    """

    mode: int
    option: int
    label: str

    @property
    def is_sleep(self) -> bool:
        return self.option == AIRONE_OPTION_SLEEP


@dataclass(frozen=True, slots=True)
class AironeFanChoice:
    """풍량 목록의 항목 하나.

    `option == 1` 이면 `airVolume` 이 풍량을 정하고, 그 밖이면 옵션 자체가
    풍량을 대신한다 (터보·절전·기저). 앱 `labelFor` 와 같은 규칙이다.
    """

    option: int
    air_volume: int | None
    label: str


@dataclass(slots=True)
class AironeDevice:
    """에어원 하나. `reported` 는 MQTT 로 들어올 때마다 갈린다."""

    device_seq: int
    device_id: str
    service_code: int
    model_code: str
    model_name: str
    nickname: str
    # 토픽에 쓰는 식별자. `did.roomController.deviceId` 가 기기목록의 `deviceId` 와
    # 다를 수 있어 따로 둔다.
    physical_device_id: str
    zone_id: int | None
    modes: tuple[AironeMode, ...]
    # 필터 **개수**는 메타데이터에서 온다. 사용률은 상태에서 오는데, 엔티티는
    # MQTT 가 붙기 전에 만들어지므로 개수를 상태에서 읽으면 센서가 하나도 안 생긴다.
    filter_types: tuple[int | None, ...]
    # 공기모니터(에어모니터). 별도 기기로 등록되며 공기질 센서가 여기 붙어 있다.
    # `modelCode` 가 1000 미만이지만(실측 NAA-21DM=35) **제어 대상이 아니라**
    # 세대 판정과 무관하다. 지금은 공기질을 본체 기기에 붙이고, 이 정보는
    # 진단에만 담아 제보로 판정한다 (명세 6-6).
    air_monitors: tuple[dict[str, Any], ...]
    sensor_kinds: tuple[str, ...]
    rc_version: str | None
    odu_version: str | None
    odu_model_code: str | None
    connected_registry: bool
    raw: dict[str, Any] = field(repr=False, default_factory=dict)
    reported: dict[str, Any] = field(repr=False, default_factory=dict)
    air_sensors: dict[str, dict[str, Any]] = field(repr=False, default_factory=dict)

    # -- 생성 --------------------------------------------------------------

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> AironeDevice | None:
        device_id = raw.get("deviceId")
        device_seq = raw.get("deviceSeq")
        service_code = raw.get("serviceCode")
        if not device_id or device_seq is None or service_code is None:
            return None

        did = _dig(raw, "Properties", "data", "did", "reported") or {}
        controller = did.get("roomController")
        if not isinstance(controller, dict):
            # 능력 메타데이터가 없으면 무엇을 만들 수 있는지 알 수 없다.
            return None
        odu = did.get("odu") if isinstance(did.get("odu"), dict) else {}

        modes = tuple(
            mode
            for mode in (AironeMode.parse(item) for item in controller.get("mode") or [])
            if mode is not None
        )

        nick = _dig(raw, "Properties", "nickName") or {}
        nickname = (
            nick.get("mainItem")
            or controller.get("zoneNickname")
            or raw.get("modelName")
            or "나비엔 환기청정"
        )

        return cls(
            device_seq=int(device_seq),
            device_id=str(device_id),
            service_code=int(service_code),
            model_code=str(raw.get("modelCode") or ""),
            model_name=str(raw.get("modelName") or "나비엔 환기청정"),
            nickname=str(nickname),
            physical_device_id=str(controller.get("deviceId") or device_id),
            zone_id=_as_int(controller.get("zoneId")),
            modes=modes,
            filter_types=tuple(
                _as_int(item.get("type"))
                for item in odu.get("filter") or []
                if isinstance(item, dict)
            ),
            air_monitors=tuple(
                {
                    "deviceId": item.get("deviceId"),
                    "modelCode": item.get("modelCode"),
                    "version": item.get("version"),
                    "zoneId": item.get("zoneId"),
                    "sensor": item.get("sensor"),
                }
                for item in did.get("airMonitor") or []
                if isinstance(item, dict)
            ),
            sensor_kinds=(),
            rc_version=_version_text(controller.get("version")),
            odu_version=_version_text(odu.get("version")),
            odu_model_code=(
                str(odu.get("modelCode")) if odu.get("modelCode") is not None else None
            ),
            connected_registry=bool(raw.get("connected")),
            raw=raw,
        )

    # -- 세대 --------------------------------------------------------------

    @property
    def is_v2_generation(self) -> bool:
        """V2.1 세대인가.

        `modelCode < 1000` 은 봉투와 토픽이 전혀 달라(명세 6-5) 같은 코드로 못 쏜다.
        """
        code = _as_int(self.model_code)
        return code is not None and code >= AIRONE_V2_MIN_MODEL_CODE

    # -- 상태 --------------------------------------------------------------

    @property
    def _controller(self) -> dict[str, Any]:
        value = (self.reported or {}).get("roomController")
        return value if isinstance(value, dict) else {}

    @property
    def _odu(self) -> dict[str, Any]:
        value = (self.reported or {}).get("odu")
        return value if isinstance(value, dict) else {}

    @property
    def available(self) -> bool:
        return self.connected_registry

    @property
    def running(self) -> int | None:
        """운전 상태. 방 컨트롤러에 없으면 실외기에서 읽는다.

        `RoomControllerStatus` 와 `OduStatus` **둘 다** `running` 을 가진다. 방
        컨트롤러 쪽만 보다가 그 필드가 비어 오는 기기를 만나면 전원 스위치가
        영구히 `알 수 없음` 이 된다 — 값이 있는데 안 읽는 셈이다.

        방 컨트롤러를 먼저 본다. 사용자가 만지는 것이 그쪽이다.
        """
        value = _as_int(self._controller.get("running"))
        if value is None:
            value = _as_int(self._odu.get("running"))
        return value

    @property
    def running_name(self) -> str | None:
        value = self.running
        if value is None:
            return None
        return AIRONE_RUN_NAMES.get(value, f"알 수 없음({value})")

    @property
    def is_on(self) -> bool:
        return self.running == AIRONE_RUN_ON

    @property
    def mode(self) -> int | None:
        return _as_int(self._controller.get("mode"))

    @property
    def option(self) -> int | None:
        return _as_int(self._controller.get("option"))

    @property
    def air_volume(self) -> int | None:
        return _as_int(self._controller.get("airVolume"))

    @property
    def mode_label(self) -> str | None:
        mode = self.mode
        if mode is None:
            return None
        option = self.option
        return airone_mode_label(mode, AIRONE_OPTION_NONE if option is None else option)

    @property
    def wind_label(self) -> str | None:
        value = self.air_volume
        if value is None:
            return None
        return AIRONE_WIND_NAMES.get(value)

    @property
    def error_code(self) -> int | None:
        code = _as_int(_dig(self._controller, "error", "code"))
        if code is None:
            code = _as_int(_dig(self._odu, "error", "code"))
        return code

    @property
    def has_error(self) -> bool:
        code = self.error_code
        return code is not None and code != 0

    @property
    def target_humidity(self) -> int | None:
        """제습 목표 습도. `additionalData` 에서 습도 항목만 골라 읽는다."""
        for extra in self._controller.get("additionalData") or []:
            if not isinstance(extra, dict):
                continue
            if _as_int(extra.get("type")) == AIRONE_HUMIDITY_TYPE:
                return _as_int(extra.get("value"))
        return None

    @property
    def filters(self) -> tuple[dict[str, Any], ...]:
        """실외기 필터 상태. 메타데이터가 알려준 개수만큼 자리를 지킨다.

        상태가 아직 안 왔으면 `percent` 가 `None` 인 자리를 돌려준다 —
        길이가 흔들리면 엔티티와 자리가 어긋난다.
        """
        reported = [
            item for item in self._odu.get("filter") or [] if isinstance(item, dict)
        ]
        result: list[dict[str, Any]] = []
        for index, kind in enumerate(self.filter_types):
            item = reported[index] if index < len(reported) else {}
            result.append(
                {
                    "type": _as_int(item.get("type")) if item else kind,
                    "percent": _as_int(_dig(item, "usage", "percent")),
                    "replace_period": _as_int(item.get("replacePeriod")),
                }
            )
        return tuple(result)

    # -- 능력 --------------------------------------------------------------

    @property
    def configurable_modes(self) -> tuple[AironeMode, ...]:
        return tuple(item for item in self.modes if item.configurable)

    @property
    def selectable_modes(self) -> tuple[AironeModeChoice, ...]:
        """운전 모드 목록. **서버 순서를 지킨다.**

        앱과 같은 축으로 자른다 — 터보·절전·기저는 여기 넣지 않고 풍량 쪽으로
        보낸다. 숙면만 별도 모드로 올린다.

        서버가 어떤 모드에 `option 1` 을 안 주고 터보만 줄 수도 있다. 그때도 그
        모드가 목록에서 사라지지 않도록 **그 모드의 첫 조합**을 대표로 쓴다.
        """
        result: list[AironeModeChoice] = []
        seen: set[tuple[int, int]] = set()

        for item in self.configurable_modes:
            if item.option == AIRONE_OPTION_SLEEP:
                key = (item.mode, AIRONE_OPTION_SLEEP)
                label = AIRONE_MODE_SLEEP_LABEL
            else:
                # 그 모드의 대표 option — 1 이 있으면 1, 없으면 처음 나온 것.
                options = [
                    other.option
                    for other in self.configurable_modes
                    if other.mode == item.mode and other.option != AIRONE_OPTION_SLEEP
                ]
                option = AIRONE_OPTION_NONE if AIRONE_OPTION_NONE in options else options[0]
                key = (item.mode, option)
                label = AIRONE_MODE_NAMES.get(item.mode) or f"알 수 없음({item.mode})"

            if key in seen:
                continue
            seen.add(key)
            result.append(AironeModeChoice(mode=key[0], option=key[1], label=label))

        # 숙면이 여러 모드에 붙어 있으면 이름이 겹친다. 그때만 모드를 덧붙인다.
        sleeps = [c for c in result if c.is_sleep]
        if len(sleeps) > 1:
            result = [
                AironeModeChoice(
                    c.mode,
                    c.option,
                    f"{AIRONE_MODE_NAMES.get(c.mode, c.mode)} {AIRONE_MODE_SLEEP_LABEL}",
                )
                if c.is_sleep
                else c
                for c in result
            ]
        return tuple(result)

    def mode_entries(self, mode: int, option: int) -> tuple[AironeMode, ...]:
        return tuple(item for item in self.modes if item.key == (mode, option))

    def fan_choices(self, mode: int | None, option: int | None) -> tuple[AironeFanChoice, ...]:
        """지금 모드에서 고를 수 있는 풍량.

        **서버 메타데이터에 실제로 있던 조합만** 돌려준다. 표를 만들어 채우지 않는다.

        - `option == 1` → `airVolume` 이 미풍·약풍·강풍·자동을 정한다
        - `option` 이 터보·절전·기저 → 옵션 자체가 풍량 항목이 된다
        - 숙면 모드 안에서는 그 조합의 `airVolume` 만 쓴다 (앱과 같다)
        """
        if mode is None:
            return ()
        sleeping = option == AIRONE_OPTION_SLEEP
        result: list[AironeFanChoice] = []
        seen: set[str] = set()

        for item in self.configurable_modes:
            if item.mode != mode:
                continue
            if sleeping != (item.option == AIRONE_OPTION_SLEEP):
                # 숙면 모드에서는 숙면 조합만, 그 밖에서는 숙면 아닌 것만 본다.
                continue

            if item.option in AIRONE_OPTIONS_WITH_WIND:
                if item.air_volume is None:
                    continue
                label = AIRONE_WIND_NAMES.get(item.air_volume)
            else:
                label = AIRONE_OPTION_NAMES.get(item.option)
            if not label or label in seen:
                continue
            seen.add(label)
            result.append(AironeFanChoice(item.option, item.air_volume, label))
        return tuple(result)

    def current_fan_label(self) -> str | None:
        """지금 상태에 해당하는 풍량 항목 이름."""
        for choice in self.fan_choices(self.mode, self.option):
            if choice.option != (self.option or AIRONE_OPTION_NONE):
                continue
            if choice.option in AIRONE_OPTIONS_WITH_WIND:
                if choice.air_volume == self.air_volume:
                    return choice.label
                continue
            return choice.label
        return None

    def humidity_bounds(self, mode: int | None, option: int | None) -> tuple[int, int] | None:
        """제습 목표 습도 범위.

        같은 모드라도 옵션에 따라 서버가 범위를 주기도 하고 안 주기도 한다
        (앱은 터보·절전에서 습도를 「자동」으로 보여준다). 정확한 조합을 먼저 보고,
        없으면 같은 모드의 다른 조합에서 찾는다 — 범위 자체는 모드 성질이다.
        """
        if mode is None or mode not in AIRONE_MODES_WITH_HUMIDITY:
            return None
        opt = AIRONE_OPTION_NONE if option is None else option
        exact = [item for item in self.mode_entries(mode, opt) if item.wants_humidity]
        same_mode = [
            item for item in self.modes if item.mode == mode and item.wants_humidity
        ]
        for item in exact or same_mode:
            assert item.humidity_min is not None and item.humidity_max is not None
            return (item.humidity_min, item.humidity_max)
        return None

    # -- 공기질 ------------------------------------------------------------

    def set_air_sensors(self, airs: list[dict[str, Any]]) -> list[str]:
        """`/air-sensor` 응답을 반영하고, 모르는 종류를 돌려준다."""
        unknown: list[str] = []
        table: dict[str, dict[str, Any]] = {}
        for item in airs:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if not isinstance(kind, str) or not kind:
                continue
            # 서버가 다른 이름으로 줄 수 있다. 표준 이름으로 모은다.
            kind = AIRONE_SENSOR_ALIASES.get(kind.strip().lower(), kind)
            if kind not in AIRONE_SENSOR_KINDS:
                unknown.append(kind)
                continue
            table[kind] = item
        self.air_sensors = table
        self.sensor_kinds = tuple(k for k in AIRONE_SENSOR_KINDS if k in table)
        return unknown

    # -- 제어 --------------------------------------------------------------

    def build_power_desired(self, turn_on: bool) -> dict[str, Any]:
        """`power` 명령 본문 (`DesiredPowerRequestData`)."""
        controller: dict[str, Any] = {
            "deviceId": self.physical_device_id,
            "running": AIRONE_RUN_ON if turn_on else AIRONE_RUN_OFF,
        }
        if self.zone_id is not None:
            controller["zoneId"] = self.zone_id
        return {"roomController": controller}

    def build_mode_desired(
        self,
        mode: int,
        option: int,
        air_volume: int | None = None,
        humidity: int | None = None,
    ) -> dict[str, Any]:
        """`change-mode` 명령 본문 (`DesiredChangeModeRequestData`).

        풍량은 **서버가 알려준 값 중에서만** 고른다. 지금 조합에 풍량이 없으면
        필드를 아예 넣지 않는다 — 0 이나 기본값을 만들어 넣지 않는다.
        """
        controller: dict[str, Any] = {"mode": mode, "option": option}

        wind = air_volume
        if wind is not None and wind not in AIRONE_WIND_NAMES:
            # 확인되지 않은 값은 **지정하지 않은 것으로 본다.** 그대로 쏘면 안 되고,
            # 필드를 빼면 기기가 풍량을 0으로 되돌릴 수 있다. 아래에서 서버가 준
            # 값으로 되돌린다.
            _LOGGER.debug("에어원 풍량 %s 는 확인된 값이 아니라 무시합니다", wind)
            wind = None
        if wind is None:
            allowed = [
                choice.air_volume
                for choice in self.fan_choices(mode, option)
                if choice.option == option and choice.air_volume is not None
            ]
            if self.air_volume in allowed:
                wind = self.air_volume
            elif allowed:
                wind = allowed[0]
        if wind is not None:
            controller["airVolume"] = wind

        target = humidity
        bounds = self.humidity_bounds(mode, option)
        if target is None and bounds is not None:
            current = self.target_humidity
            if current is not None and bounds[0] <= current <= bounds[1]:
                target = current
        if target is not None and bounds is not None:
            controller["additionalData"] = {
                "type": AIRONE_HUMIDITY_TYPE,
                "value": max(bounds[0], min(bounds[1], target)),
            }

        return {"roomController": controller}
