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
import time
from dataclasses import dataclass, field
from typing import Any, Final

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
    AIRONE_RUN_AWAY,
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

# 진단에 남길 기록 개수. 순서를 보는 것이 목적이라 길 필요가 없다.
_LOG_KEEP = 8


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


# `running` 이 정수로 오는 것은 확인했다. 다만 서버가 기기·펌웨어에 따라 참/거짓이나
# 문자열로 줄 수도 있어 그때도 읽는다 — **읽기 쪽만 넓힌다.** 보낼 때는 정수만 쓴다.
_RUNNING_TEXT: Final = {
    "on": AIRONE_RUN_ON, "run": AIRONE_RUN_ON, "running": AIRONE_RUN_ON,
    "true": AIRONE_RUN_ON, "y": AIRONE_RUN_ON, "yes": AIRONE_RUN_ON,
    "off": AIRONE_RUN_OFF, "stop": AIRONE_RUN_OFF, "stopped": AIRONE_RUN_OFF,
    "false": AIRONE_RUN_OFF, "n": AIRONE_RUN_OFF, "no": AIRONE_RUN_OFF,
    "away": AIRONE_RUN_AWAY, "out": AIRONE_RUN_AWAY,
}


def _as_running(value: Any) -> int | None:
    """운전 상태 값을 정수로. 참/거짓과 문자열도 받는다."""
    if value is None:
        return None
    if isinstance(value, bool):
        return AIRONE_RUN_ON if value else AIRONE_RUN_OFF
    if (number := _as_int(value)) is not None:
        # 0 을 「정지」로 본다. 서버가 쓰는 것은 1/2/3 이지만 0 을 주는 기기가 있어도
        # 「알 수 없음」보다 「정지」가 맞다 — 운전 중이면 1 이 온다.
        return AIRONE_RUN_OFF if number == 0 else number
    if isinstance(value, str):
        return _RUNNING_TEXT.get(value.strip().lower())
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
    # 지금 값(또는 기본값)이다. **고를 수 있는 목록이 아니다** — 실기기 제보로
    # 확인했다. 목록은 `supported_air_volumes` 다.
    air_volume: int | None
    # 서버가 이 조합에서 고를 수 있는 풍량을 알려준다 (`supportedAirVolumes`).
    # APK 클래스에는 없던 필드다. 없으면 빈 tuple.
    supported_air_volumes: tuple[int, ...]
    # **「이 모드를 고를 수 있는가」가 아니다.** 실기기 응답에서 `configurable: true`
    # 인 항목은 정확히 `supportedAirVolumes` 나 `additionalData` 를 가진 것들이었다 —
    # 즉 「이 모드 안에서 풍량·습도를 조절할 수 있는가」다.
    # 자동운전·요리·숙면·터보·절전이 모두 false 로 오는데, 앱에서는 다 고를 수 있다.
    # 그래서 모드 목록을 이 값으로 거르지 않는다. 진단용으로만 남긴다.
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
            # 실기기는 「해당 없음」에 0 을 준다. 확인된 표에 없는 값은 쓰지 않는다.
            _LOGGER.debug(
                "에어원 airVolume %s 는 확인된 값이 아니라 무시합니다 (mode=%s)", wind, mode
            )
            wind = None

        supported = tuple(
            value
            for item in raw.get("supportedAirVolumes") or []
            if (value := _as_int(item)) is not None and value in AIRONE_WIND_NAMES
        )

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
            supported_air_volumes=supported,
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
    # 제습 모드를 벗어나면 기기가 습도를 더 이상 보고하지 않는다. 다시 제습으로
    # 들어갈 때 이 값을 실어 보내지 않으면 **기기가 자기 최소값으로 되돌린다** —
    # 실사용 제보로 확인했다(설정해두고 모드를 왕복하면 40% 로 초기화).
    last_humidity: int | None = field(repr=False, default=None)
    # **무엇을 보냈고 무엇이 돌아왔는지** 짧게 남긴다. 값의 순서를 봐야 가릴 수
    # 있는 문제가 있다 — 「모드를 바꿀 때 습도를 같이 보냈는데 기기가 되돌리는가」는
    # 그 순간의 값만으로는 알 수 없다. 진단에 담아 제보 한 번으로 닫는다.
    # 개인정보는 담지 않는다 — 모드 번호와 습도 값뿐이다.
    command_log: list[dict[str, Any]] = field(repr=False, default_factory=list)
    humidity_log: list[dict[str, Any]] = field(repr=False, default_factory=list)

    # -- 생성 --------------------------------------------------------------

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> AironeDevice | None:
        device_id = raw.get("deviceId")
        device_seq = raw.get("deviceSeq")
        service_code = raw.get("serviceCode")
        if not device_id or device_seq is None or service_code is None:
            return None

        # 구세대는 `did` 아래에 `state` 겹이 하나 더 있다 (실측: NRT-20DSW).
        # 세대를 따지지 않고 있는 쪽을 쓴다 — 어느 세대든 한 곳에만 들어 있다.
        did = (
            _dig(raw, "Properties", "data", "did", "reported")
            or _dig(raw, "Properties", "data", "did", "state", "reported")
            or {}
        )
        controller = did.get("roomController")
        if not isinstance(controller, dict):
            # **기기를 포기하지 않는다.** 능력 메타데이터가 없으면 무엇을 고를 수
            # 있는지 모를 뿐이고, 전원·운전상태·오류는 상태 응답에서 온다.
            #
            # 등록 직후처럼 기기가 아직 `did` 를 올리지 않은 시점이 있다. 여기서
            # `None` 을 돌려주면 그 사용자는 엔티티를 하나도 못 본다 —
            # 「아무것도 안 뜬다」가 그것이다. 없는 것은 안 만들고, 있는 것은 만든다.
            controller = {}
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

    # -- 상태 반영 ----------------------------------------------------------

    def apply_reported(self, incoming: dict[str, Any]) -> None:
        """들어온 상태를 **덮어쓰지 않고 겹쳐 쓴다.**

        에어원은 명령마다 응답이 따로 오고 **그 응답이 부분적이다.** 전원을 켜면
        `{"roomController": {"running": 1}}` 처럼 바뀐 것만 오거나, `odu` 만 오는
        경우도 있다.

        통째로 갈아끼우면 그때 `mode` · `option` · `airVolume` 이 사라지고, 심하면
        `running` 까지 없어져 **전원이 「알 수 없음」으로 빠진다** — 실사용 제보로
        확인했다.

        매트는 shadow 가 항상 전체를 주므로 이 처리가 필요 없다. 여기만 겹쳐 쓴다.

        오래된 값이 남을 수 있다는 것은 감수한다. 기기가 어떤 항목을 더 이상 보내지
        않으면 마지막 값이 남는다. **전부 「알 수 없음」이 되는 것보다 낫다.**
        """
        merged: dict[str, Any] = dict(self.reported or {})
        for key, value in incoming.items():
            current = merged.get(key)
            if isinstance(value, dict) and isinstance(current, dict):
                # `roomController` 안의 바뀐 항목만 갈아끼운다.
                inner = dict(current)
                inner.update(value)
                merged[key] = inner
            else:
                # 목록(`airMonitor`, `filter`)은 통째로 바꾼다. 부분 목록을 항목별로
                # 섞으면 자리가 어긋난다.
                merged[key] = value
        self.reported = merged
        self._note_humidity()

    def _note_humidity(self) -> None:
        """관측된 목표 습도가 바뀌면 한 줄 남긴다. 같은 값은 쌓지 않는다."""
        value = self.target_humidity
        entry = {"mode": self.mode, "option": self.option, "humidity": value}
        if self.humidity_log and {
            k: self.humidity_log[-1].get(k) for k in ("mode", "option", "humidity")
        } == entry:
            return
        self.humidity_log.append({**entry, "at": round(time.monotonic(), 1)})
        del self.humidity_log[:-_LOG_KEEP]

    def note_command(self, command: str, desired: dict[str, Any] | None) -> None:
        """보낸 명령을 한 줄 남긴다. 진단에서 순서를 보려면 이게 있어야 한다."""
        controller = (desired or {}).get("roomController") or {}
        extra = controller.get("additionalData")
        self.command_log.append(
            {
                "command": command,
                "mode": controller.get("mode"),
                "option": controller.get("option"),
                "airVolume": controller.get("airVolume"),
                "running": controller.get("running"),
                # 습도를 실어 보냈는지가 핵심이다.
                "humidity_sent": (extra or {}).get("value") if isinstance(extra, dict) else None,
                "at": round(time.monotonic(), 1),
            }
        )
        del self.command_log[:-_LOG_KEEP]

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
        value = _as_running(self._controller.get("running"))
        if value is None:
            value = _as_running(self._odu.get("running"))
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
        """제습 목표 습도.

        **`additionalData` 의 `type: 1` 이 습도라는 보장이 없다.** 실기기 응답을 보면
        같은 `type: 1` 이 자리마다 뜻이 다르다 — 제습 모드 안에서는 40~65(습도)인데
        컨트롤러 수준에서는 0~4 다. 환기 중인 기기에서 `1` 을 습도로 읽어
        슬라이더가 맨 왼쪽에 붙는 일이 있었다.

        그래서 **서버가 알려준 그 모드의 범위 안에 있을 때만** 습도로 인정한다.
        범위를 안 주는 모드(환기·청정 등)에서는 값이 있어도 쓰지 않는다.
        """
        bounds = self.humidity_bounds(self.mode, self.option)
        if bounds is None:
            return None
        for extra in self._controller.get("additionalData") or []:
            if not isinstance(extra, dict):
                continue
            if _as_int(extra.get("type")) != AIRONE_HUMIDITY_TYPE:
                continue
            value = _as_int(extra.get("value"))
            if value is not None and bounds[0] <= value <= bounds[1]:
                self.last_humidity = value
                return value
            _LOGGER.debug(
                "에어원 습도 %s 가 서버 범위 %s 를 벗어나 쓰지 않습니다", value, bounds
            )
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
        """서버가 「조절 가능」이라고 표시한 조합. 진단용이다.

        **모드 목록을 이것으로 거르지 않는다** — `configurable` 은 모드를 고를 수
        있는지가 아니라 그 안에서 풍량·습도를 조절할 수 있는지를 뜻한다.
        """
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

        for item in self.modes:
            if item.option == AIRONE_OPTION_SLEEP:
                key = (item.mode, AIRONE_OPTION_SLEEP)
                label = AIRONE_MODE_SLEEP_LABEL
            else:
                # 그 모드의 대표 option — 1 이 있으면 1, 없으면 처음 나온 것.
                options = [
                    other.option
                    for other in self.modes
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

        def add(opt: int, wind: int | None, label: str | None) -> None:
            if not label or label in seen:
                return
            seen.add(label)
            result.append(AironeFanChoice(opt, wind, label))

        for item in self.modes:
            if item.mode != mode:
                continue
            if sleeping != (item.option == AIRONE_OPTION_SLEEP):
                # 숙면 모드에서는 숙면 조합만, 그 밖에서는 숙면 아닌 것만 본다.
                continue

            if item.option in AIRONE_OPTIONS_WITH_WIND:
                # **`supportedAirVolumes` 가 고를 수 있는 목록이다.** `airVolume` 은
                # 지금 값이라 그것만 보면 항목이 하나로 줄어든다 (실기기 제보에서
                # 「자동」만 나오던 원인).
                for value in item.supported_air_volumes or (
                    (item.air_volume,) if item.air_volume is not None else ()
                ):
                    add(item.option, value, AIRONE_WIND_NAMES.get(value))
            else:
                add(item.option, item.air_volume, AIRONE_OPTION_NAMES.get(item.option))
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
        """`/air-sensor` 응답을 반영하고, 모르는 종류를 돌려준다.

        **덮어쓰지 않고 겹쳐 쓴다.** 상태 응답과 같은 이유다 (`apply_reported`).
        공기질은 5분마다 다시 읽는데, 한 번 비어서 오거나 일부 항목만 오면
        **그때마다 센서가 「알 수 없음」으로 빠진다.** 에어모니터가 잠깐 끊기거나
        서버가 한 번 거르면 그렇게 된다.

        빈 응답으로는 아무것도 지우지 않는다. 오래된 값이 남는 것이
        전부 사라지는 것보다 낫다.
        """
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

        if not table:
            # 빈 응답이 이미 받은 값을 지우게 하지 않는다.
            _LOGGER.debug("공기질 응답이 비어 있어 앞서 받은 값을 유지합니다")
            return unknown

        merged = dict(self.air_sensors)
        merged.update(table)
        self.air_sensors = merged
        self.sensor_kinds = tuple(k for k in AIRONE_SENSOR_KINDS if k in merged)
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

        # **들어갈 모드**의 범위를 본다. `self.target_humidity` 는 「지금 모드」를
        # 기준으로 읽으므로, 환기에서 제습으로 넘어가는 순간에는 항상 `None` 이다.
        # 그것만 보고 습도를 안 실어 보내면 기기가 자기 최소값으로 되돌린다.
        target = humidity
        bounds = self.humidity_bounds(mode, option)
        if target is None and bounds is not None:
            for candidate in (self.target_humidity, self.last_humidity):
                if candidate is not None and bounds[0] <= candidate <= bounds[1]:
                    target = candidate
                    break
        if target is not None and bounds is not None:
            target = max(bounds[0], min(bounds[1], target))
            # 다음 왕복에서도 쓴다.
            self.last_humidity = target
            controller["additionalData"] = {
                "type": AIRONE_HUMIDITY_TYPE,
                "value": target,
            }

        return {"roomController": controller}
