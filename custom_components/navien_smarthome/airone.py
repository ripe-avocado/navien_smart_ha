"""에어원(환기청정) 기기 모델.

매트와 **체계가 다르다.** 매트는 AWS shadow(`state.desired` / `state.reported`)를
쓰지만 에어원은 `cmd/rc/v2/...` 토픽에 직접 주고받는다. 그래서 같은 클래스로
묶지 않고 따로 둔다 — 검증이 끝난 매트 경로를 건드리지 않는 것이 우선이다.

여기 있는 필드명과 값은 전부 앱에서 확인했다(`AironeConstants`, `PubSubData`,
`ModeDid`, `RoomControllerStatus`). **상태·제어·목표 습도는 실기기 제보로
확인됐다**(룸콘 분리형 1901, 올인원 룸콘 1900). 그 밖의 모델은 확인된 것이 없다.

값 체계를 모르는 항목은 채우지 않고 비워 둔다.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final

from .const import (
    AIRONE_HUMIDITY_REPORT_TYPE,
    AIRONE_HUMIDITY_TYPE,
    AIRONE_LEVEL_NAMES,
    AIRONE_MODE_NAMES,
    AIRONE_MODE_SLEEP_LABEL,
    AIRONE_MODES_WITH_HUMIDITY,
    AIRONE_OPTION_NAMES,
    AIRONE_MODE_BYPASS,
    AIRONE_OPTION_NONE,
    AIRONE_OPTION_SLEEP,
    LEGACY_NO_BYPASS_MODELS,
    LEGACY_DEFAULT_AIR_VOLUMES,
    LEGACY_EXTRA_FIELDS,
    LEGACY_MODE_DID,
    AIRONE_OPTIONS_WITH_WIND,
    AIRONE_RUN_AWAY,
    AIRONE_RUN_NAMES,
    AIRONE_RUN_OFF,
    AIRONE_RUN_ON,
    AIRONE_SELECTABLE_AIR_VOLUMES,
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


def _strip_capability_fields(incoming: dict[str, Any]) -> dict[str, Any]:
    """상태 응답에 섞여 오는 **능력 서술자**를 걷어낸다.

    `status` 요청에 기기가 **DID 문서 전체**로 답하는 경우가 있다. 그 안의
    `roomController.mode` 는 지금 운전 모드(정수)가 아니라 **지원 조합 배열**이다.
    그대로 겹쳐 쓰면 방금 받은 모드 번호를 배열이 덮어써서 모드가 사라진다.

    제보(#12, `NRT-530Z3`)에서 그대로 보였다.

        mode: 4     ← change-mode 응답
        mode: null  ← 9초 뒤 status 응답이 배열로 덮었다

    그 뒤로는 모드가 계속 비었고, 풍량 select 는 「고를 것이 없다」로 판단해
    **`unavailable`** 이 됐다. 「기기가 죽은 것처럼 보인다」의 정체다.

    능력 목록은 기기목록(REST)에서 이미 읽어 `modes` 로 들고 있다. 여기서는
    버린다 — 상태만 남긴다.

    **`additionalData` 도 같은 함정이다.** 상태로 올 때는 `value` 가 실린 목록인데
    (`{"type": 3, "value": 40}`), DID 로 올 때는 범위표다
    (`{"type": 1, "min": 0, "max": 4}`). 범위표가 덮으면 읽어둔 목표 습도가
    사라진다. **값이 하나도 없는 목록이면 상태가 아니므로 버린다.**
    """
    controller = incoming.get("roomController")
    if not isinstance(controller, dict):
        return incoming

    inner = dict(controller)
    changed = False

    if isinstance(inner.get("mode"), list):
        del inner["mode"]
        changed = True

    extra = inner.get("additionalData")
    if isinstance(extra, list) and not any(
        isinstance(item, dict) and "value" in item for item in extra
    ):
        del inner["additionalData"]
        changed = True

    if not changed:
        return incoming
    trimmed = dict(incoming)
    trimmed["roomController"] = inner
    return trimmed


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
    # **기기가 「나는 이런 센서를 갖고 있다」고 스스로 밝힌 것.**
    #
    #   NRT-530S3 (모니터 없음)  roomController.sensor 에 표가 있다   ← 룸콘 내장
    #   NRT-530Z3 (모니터 있음)  roomController.sensor 는 빈 배열이고
    #                            airMonitor[].sensor 에 표가 있다     ← 별도 기기
    #
    # 둘 다 없는 기기도 있다 — 모니터를 안 산 전열교환기가 그렇다.
    # 그 기기에 공기질을 물어보는 것은 헛일이다 (`wants_air_sensors`).
    declared_sensors: tuple[Any, ...] | None
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
    # **값이 멈춰도 흔적이 남게 한다.** 빈 응답으로 지우지 않기로 한 대가로
    # 「갱신이 안 되는 것」과 「값이 안 바뀐 것」을 구별할 수 없게 됐다.
    # 마지막으로 값이 실제로 바뀐 시점과, 헛돈 횟수를 센다.
    air_sensor_stamp: float | None = field(repr=False, default=None)
    air_sensor_empty: int = field(repr=False, default=0)
    air_sensor_errors: int = field(repr=False, default=0)
    air_sensor_unchanged: int = field(repr=False, default=0)
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
        modes = cls._legacy_modes(
            modes, raw.get("modelCode"), str(raw.get("modelName") or "")
        )

        # **문자열로 올 수도 있다.** 매트는 `{"mainItem": ..., "side": {...}}` 인데
        # 별칭을 안 나눠 쓰는 계정에서 그냥 이름 하나로 오는 경우를 배제할 근거가
        # 없다. 그때 `.get` 을 부르면 통합 전체가 설정 단계에서 죽는다 —
        # 기기 하나가 아니라 **전부** 안 보인다. 문자열이면 이름으로 쓴다.
        raw_nick = _dig(raw, "Properties", "nickName")
        nick = raw_nick if isinstance(raw_nick, dict) else {}
        nick_text = raw_nick.strip() if isinstance(raw_nick, str) else ""
        nickname = (
            nick.get("mainItem")
            or nick_text
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
            # **키가 아예 없으면 「없다」가 아니라 「모른다」다.** 그때는 물어본다.
            declared_sensors=(
                tuple(controller["sensor"])
                if isinstance(controller.get("sensor"), list)
                else None
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

    @staticmethod
    def _legacy_modes(
        modes: tuple[AironeMode, ...], model_code: Any, model_name: str | None = None
    ) -> tuple[AironeMode, ...]:
        """구세대 모드 목록을 앱과 같게 만든다.

        **처음에는 DID 를 능력 목록으로 봤고 그것이 틀렸다.** 앱은 구세대에서
        DID 를 아예 보지 않는다 — `modelCode < 1000` 이면
        `loadlegacyModeDataFromFile()` 로 앱에 내장된 파일을 읽어 모드 목록으로 쓴다
        (`AirOneControlViewModel`). 그 내용이 `LEGACY_MODE_DID` 다.

        확인 경로는 이랬다. 두 기기(`NRT-20DS`·`NRT-20DSW`)의 앱 화면에 모드가
        여섯 개인데 DID 에는 그만큼이 없었다. **DID 로 목록을 만들면 앱보다 적게
        나온다** — 자동운전과 요리가 빠졌다.

        그래서 앱 파일을 기준으로 하고 **DID 가 준 것을 위에 얹는다.** DID 항목은
        그 기기의 실제 값이므로 기본 풍량 같은 것은 DID 쪽이 정확하다.
        """
        code = _as_int(model_code)
        if code is None or code >= AIRONE_V2_MIN_MODEL_CODE:
            return modes

        # **풍량을 고를 수 있는지는 파일이 정한다.** 「option == 1 이면 고를 수
        # 있다」로 짐작했다가 자동운전(12)과 요리(6)에서 틀렸다 — 둘 다
        # `configurable: false` 인데 미풍·약풍·강풍을 만들고 있었다.
        table = {(m, o): (vol, conf) for m, o, vol, conf in LEGACY_MODE_DID}

        # **바이패스가 없다고 확인된 모델에서만 뺀다** (`LEGACY_NO_BYPASS_MODELS`).
        # 모르는 모델은 그대로 둔다 — 넓게 막으면 되던 기기를 깨뜨린다.
        plain = re.sub(r"[^A-Z0-9]", "", (model_name or "").upper())
        if plain in LEGACY_NO_BYPASS_MODELS:
            table.pop((AIRONE_MODE_BYPASS, AIRONE_OPTION_NONE), None)

        have = {(item.mode, item.option) for item in modes}
        merged = list(modes)
        for (mode_code, option), (air_volume, conf) in table.items():
            if (mode_code, option) in have:
                continue
            merged.append(
                AironeMode(
                    mode=mode_code,
                    option=option,
                    air_volume=air_volume,
                    supported_air_volumes=(
                        LEGACY_DEFAULT_AIR_VOLUMES if conf else ()
                    ),
                    configurable=conf,
                    humidity_min=None,
                    humidity_max=None,
                )
            )
        # DID 가 준 조합에도 `supportedAirVolumes` 가 없다. **앱은 구세대에서 DID 를
        # 아예 안 보므로** 파일이 아는 조합이면 파일 쪽 판단으로 덮는다.
        return tuple(
            (
                item
                if item.key not in table or item.supported_air_volumes
                else AironeMode(
                    mode=item.mode,
                    option=item.option,
                    air_volume=item.air_volume,
                    supported_air_volumes=(
                        LEGACY_DEFAULT_AIR_VOLUMES if table[item.key][1] else ()
                    ),
                    configurable=table[item.key][1],
                    humidity_min=item.humidity_min,
                    humidity_max=item.humidity_max,
                )
            )
            for item in merged
        )

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
        incoming = _strip_capability_fields(incoming)
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
    def legacy_extras(self) -> dict[str, Any]:
        """구세대만 싣는 값. 없으면 빈 사전."""
        value = (self.reported or {}).get("legacyExtras")
        return value if isinstance(value, dict) else {}

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

        **번호로만 찾다가 못 찾았다.** 서버 능력 정보는 범위를 `type: 1` 로 주는데,
        기기 상태는 값을 **`type: 3`** 으로 돌려준다. 같은 목록의 `type: 1` 은 범위가
        0~4 인 다른 항목이다. v0.9.1 까지 그것만 뒤져서 화면이 늘 비어 있었다.

        그래서 두 가지를 함께 본다.

        1. **서버가 알려준 그 모드의 범위 안에 있는 값** — 이게 판정 기준이다.
           범위를 안 주는 모드(환기·청정, 터보·절전)에서는 값이 있어도 쓰지 않는다
        2. 후보가 여럿이면 실기기에서 확인된 번호(`3`)를 먼저 쓴다

        번호를 조건으로 걸지 않는 이유는 관측이 한 기기뿐이라서다. 범위 안에 드는
        값이라면 번호가 달라도 읽는다.
        """
        bounds = self.humidity_bounds(self.mode, self.option)
        if bounds is None:
            return None

        candidates: list[tuple[int | None, int]] = []
        for extra in self._controller.get("additionalData") or []:
            if not isinstance(extra, dict):
                continue
            value = _as_int(extra.get("value"))
            if value is None or not bounds[0] <= value <= bounds[1]:
                continue
            candidates.append((_as_int(extra.get("type")), value))

        if not candidates:
            return None
        if len(candidates) > 1:
            # 어느 것이 습도인지 단정할 수 없다. 확인된 번호를 먼저 쓰고 남긴다.
            _LOGGER.debug(
                "에어원 습도 후보가 여럿입니다 (범위 %s): %s", bounds, candidates
            )
        for kind, value in candidates:
            if kind == AIRONE_HUMIDITY_REPORT_TYPE:
                self.last_humidity = value
                return value
        value = candidates[0][1]
        self.last_humidity = value
        return value

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
        # 서버가 같은 조합을 몇 개로 줬는지. 여러 개면 그 나열이 목록이다.
        enumerated: dict[tuple[int, int], int] = {}
        for item in self.modes:
            enumerated[item.key] = enumerated.get(item.key, 0) + 1

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
                # **`configurable` 이 「풍량을 고를 수 있는가」다.** 앱이 그렇게 쓴다
                # (`AirOneControlFragment.allowedWindChoicesFromDids`).
                #
                #     z10 = 그 모드 항목 중 configurable 이 하나라도 true
                #     if (z10) { 미풍·약풍·강풍 을 모두 보여준다 }
                #     else     { airVolume 값이 1·2·3 인 것만 }
                #
                # `supportedAirVolumes` 는 **APK 2.10.4 에 없는 필드**다. 서버가
                # 나중에 추가했고 옛 펌웨어는 안 내려준다. 그것만 믿으면 옛 펌웨어
                # 기기에서 「자동」 하나로 줄어든다 — 실기기 제보(`NRT-530Z3`,
                # 룸콘 10.1)에서 앱은 6개인데 우리는 3개였다.
                #
                # 순서: 서버가 목록을 주면 그것, 아니면 고를 수 있다고 했으니
                # 앱 표 전체, 그것도 아니면 지금 값 하나.
                if item.supported_air_volumes:
                    values: tuple[int, ...] = item.supported_air_volumes
                elif (
                    item.configurable
                    and item.air_volume in AIRONE_SELECTABLE_AIR_VOLUMES
                    and enumerated.get(item.key, 0) == 1
                ):
                    # **한 조합을 한 항목으로만 줬을 때 넓힌다.**
                    #
                    # 서버가 같은 조합을 여러 항목으로 나열하면(`4:1` 이 풍량 1·2·3
                    # 으로 세 번) 그 나열이 곧 목록이다. 그때 넓히면 서버가 빼둔
                    # 값을 되살리게 된다.
                    #
                    # 하나만 준 경우가 다르다. 그건 「지금 값」이고 목록이 아니다.
                    #
                    # **그 값이 네 단 중 하나일 때만 넓힌다.** 기저(5·6)를 주는
                    # 기기까지 넓히면 서버가 알려준 항목을 **잃는다** — 기저가
                    # 사라지고 미풍·약풍·강풍·자동이 대신 나온다. 넓히려다
                    # 있던 것을 빼앗는 셈이라, 모르는 값이면 그대로 둔다.
                    # `airVolume` 이 아예 없는 기기(전열교환기)도 넓히지 않는다 —
                    # 풍량 단 자체가 없고 앱도 터보·절전만 보여준다.
                    values = AIRONE_SELECTABLE_AIR_VOLUMES
                elif item.air_volume is not None:
                    values = (item.air_volume,)
                else:
                    values = ()
                for value in values:
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

        **조합이 정확히 맞을 때만 인정한다.** 서버는 제습·기본풍량(`9:1`)에만 범위를
        주고 터보·절전(`9:2`·`9:3`)에는 주지 않는다.

        v0.9.0 까지는 「범위는 모드의 성질이다」라며 같은 모드의 다른 조합에서
        가져왔다. **추측이었고 앱과 어긋난다** — 앱 리소스에 `humidityAutoText` 와
        `humiditySeekbarNone` 이 있다. 터보·절전에서 앱은 슬라이더를 감추고
        「자동」으로 보여준다. 기기가 알아서 하는 구간이다.

        범위를 만들어내면 사용자가 조절할 수 없는 값을 조절하는 것처럼 보이고,
        그 값이 명령에 실려 나간다.
        """
        if mode is None or mode not in AIRONE_MODES_WITH_HUMIDITY:
            return None
        opt = AIRONE_OPTION_NONE if option is None else option
        for item in self.mode_entries(mode, opt):
            if not item.wants_humidity:
                continue
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
            self.air_sensor_empty += 1
            _LOGGER.debug("공기질 응답이 비어 있어 앞서 받은 값을 유지합니다")
            return unknown

        merged = dict(self.air_sensors)
        merged.update(table)
        changed = merged != self.air_sensors
        self.air_sensors = merged
        self.sensor_kinds = tuple(k for k in AIRONE_SENSOR_KINDS if k in merged)
        if changed:
            self.air_sensor_stamp = time.monotonic()
            self.air_sensor_unchanged = 0
        else:
            # 값이 온 것은 맞는데 앞과 똑같다. 방이 조용한 것일 수도 있고
            # 서버가 옛 값을 계속 주는 것일 수도 있다 — 세어서 판단에 넘긴다.
            self.air_sensor_unchanged += 1
        return unknown

    @property
    def wants_air_sensors(self) -> bool:
        """이 기기에 공기질을 물어볼 이유가 있는가.

        **없다고 확신할 때만 안 묻는다.** 셋 중 하나라도 걸리면 묻는다.

        - 에어모니터가 붙어 있다 → 센서는 거기 있다
        - 룸콘이 센서 표를 내놨다 → 룸콘에 들어 있다
        - 표 자체가 안 왔다 → **모르는 것**이지 없는 것이 아니다

        모니터 없는 전열교환기가 여기 걸린다. 그런 기기에 5분마다 물어봐야
        빈 응답만 오고, v0.12.0 이전에는 그 호출이 늦으면 **폴링 전체가 죽었다.**
        """
        if self.air_monitors:
            return True
        if self.declared_sensors is None:
            return True
        return bool(self.declared_sensors)

    @property
    def air_sensor_age(self) -> float | None:
        """공기질 값이 마지막으로 **바뀐** 뒤 흐른 초."""
        if self.air_sensor_stamp is None:
            return None
        return round(time.monotonic() - self.air_sensor_stamp, 1)

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
