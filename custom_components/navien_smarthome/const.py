"""나비엔 스마트 통합 상수.

**추측한 값은 없다.** 전부 앱에서 직접 추출했다. 다만 검증 수준이 다르다.

- 매트(200) 관련 값 — 실기기로 검증했다
- 에어원(300) 관련 값 — 앱에서 추출만 했다. **실기기에 보내본 적이 없다**

에어원 절은 그 사실을 각 항목에 적어 두었다.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "navien_smarthome"

# --- 엔드포인트 ------------------------------------------------------------

API_URL: Final = "https://nskr.naviensmartcontrol.com/api/v2.0"
LOGIN_URL: Final = "https://member.naviensmartcontrol.com"

# 앱의 `Constants.getIotEndPoint()` 값. AWS IoT 앞단에 나비엔이 세운 자체 도메인이다.
# 기기 응답의 `network.server.endpoint` 는 **기기가 접속하는 쪽**이라 다르다 —
# 그걸로 붙으면 SNI 불일치로 403 이 난다.
IOT_ENDPOINT: Final = "nskr-iot.naviensmartcontrol.com"
IOT_REGION: Final = "ap-northeast-2"
IOT_SERVICE: Final = "iotdevicegateway"

USER_AGENT: Final = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 APP_NAVIENSMART_IOS"
)

# --- 서버 응답 코드 --------------------------------------------------------

CODE_SUCCESS: Final = 200
CODE_BAD_REQUEST: Final = 400
# 계정당 세션이 하나뿐이다. 사용자가 앱을 열면 이 코드가 온다.
CODE_NOT_AUTHORIZED: Final = 404
CODE_TOKEN_EXPIRED: Final = 407

# --- 기기 종류 ------------------------------------------------------------

# 앱 내부 이름은 `SERVICE_SMARTTOK` 이다 — 보일러용 통신 모듈 브랜드가 스마트톡이다.
SERVICE_BOILER: Final = 100
SERVICE_MATE: Final = 200
SERVICE_AIRONE: Final = 300
SERVICE_SCADA: Final = 400
SERVICE_HOMEAUTO: Final = 500

# 보일러(100)는 매트와 체계가 다르다 — 상태 모델이 컨트롤러 종류별로 갈리고
# (`GetStatusData` 3,500줄 + 1st/2nd/NRM35), 각방 제어가 비트마스크이며 온도값이
# 모델별로 인코딩된다(`decodeTempValueForModel`). 재현을 잘못하면 엉뚱한 온도가 간다.
SUPPORTED_SERVICE_CODES: Final = (SERVICE_MATE, SERVICE_AIRONE)

# 제보를 받아 지원을 넓힐 대상. 진단 내보내기에 **원본을 전부** 담고 제보를 요청한다.
# 지원을 약속하는 목록이 아니다 — 데이터를 모아야 판단이 되는 목록이다.
REPORT_WANTED_SERVICE_CODES: Final = (SERVICE_BOILER,)

# 범위 밖. 제보를 요청하지 않고, 진단에도 요약만 남긴다.
OUT_OF_SCOPE_REASONS: Final = {
    SERVICE_SCADA: "상업용 장비는 이 통합의 범위가 아닙니다",
    SERVICE_HOMEAUTO: "월패드·로비폰은 서버 체계가 완전히 달라 범위가 아닙니다",
}

# 제보를 요청할 때 함께 알릴 현황. 낙관도 비관도 하지 않는다.
REPORT_WANTED_NOTES: Final = {
    SERVICE_BOILER: (
        "상태 모델이 컨트롤러 종류별로 갈리고 각방 제어가 비트마스크로 인코딩되어 "
        "있어 매트보다 오래 걸립니다. 다만 자료를 모으는 중입니다"
    ),
}

SERVICE_NAMES: Final = {
    SERVICE_BOILER: "보일러",
    SERVICE_MATE: "숙면매트",
    SERVICE_AIRONE: "환기청정",
    SERVICE_SCADA: "상업용 SCADA",
    SERVICE_HOMEAUTO: "스마트홈",
}

# MQTT 구독 토픽 접두사. 앱의 `HomeViewModel` 이 `/{접두사}/#` 로 구독한다 —
# `smarttok`(보일러) `mate` `airone` `scada` `homeauto` 다섯 개가 전부다.
# 매트는 실측 확인됨. 보일러는 지원하지 않으므로 넣지 않는다.
#
# 에어원 **제어**는 이 체계가 아니라 `AIRONE_TOPIC_FMT` 를 쓴다. 구독만 여기다.
TOPIC_PREFIX: Final = {SERVICE_MATE: "mate", SERVICE_AIRONE: "airone"}

# --- 에어원 (환기청정) ----------------------------------------------------
#
# 아래 값은 전부 앱에서 직접 확인한 것이다 (`AironeConstants`, `PubSubData`,
# `AironeModeCode`). **다만 실기기에 보내본 적이 없다.**

# `AironeConstants.aironeMqttPayload()` 의 `Integer.parseInt(modelCode) < 1000`
# 분기. 이 하나로 토픽과 봉투가 전부 갈린다. 레거시는 봉투가 달라 같은 코드로
# 못 쏘므로 건너뛴다.
AIRONE_V2_MIN_MODEL_CODE: Final = 1000

AIRONE_TOPIC_FMT: Final = "cmd/rc/v2/{model_code}/{device_id}/remote/{command}"

AIRONE_CMD_STATUS: Final = "status"
AIRONE_CMD_POWER: Final = "power"
AIRONE_CMD_CHANGE_MODE: Final = "change-mode"

# `running` — V2.1 세대. 레거시는 반대(운전=2)다. 세대를 안 가리면 전원이 뒤집힌다.
AIRONE_RUN_ON: Final = 1
AIRONE_RUN_OFF: Final = 2
AIRONE_RUN_AWAY: Final = 3
AIRONE_RUN_NAMES: Final = {
    AIRONE_RUN_ON: "운전",
    AIRONE_RUN_OFF: "정지",
    AIRONE_RUN_AWAY: "외출",
}

# `ROOM_OPERATION_MODE_*` + `AironeModeCode.uiBaseLabel`
AIRONE_MODE_NAMES: Final = {
    0: "없음",
    4: "환기",
    6: "요리",
    8: "청정",
    9: "제습",
    10: "환기제습",
    12: "자동",
    17: "바이패스",
}

# `ROOM_OPERATION_OPTION_*`. 1 은 "옵션 없음" 이라 라벨을 붙이지 않는다.
AIRONE_OPTION_NONE: Final = 1
AIRONE_OPTION_SLEEP: Final = 4
AIRONE_OPTION_NAMES: Final = {
    2: "터보",
    3: "절전",
    AIRONE_OPTION_SLEEP: "숙면",
    5: "기저",
    6: "기저",
}

# `ROOM_OPERATION_WIND_*`. **이 표에 없는 값은 보내지 않는다** —
# `ModeDid.airVolume` 이 비트마스크일 가능성이 남아 있다 (명세 6-5 참조).
AIRONE_WIND_NAMES: Final = {
    1: "미풍",
    2: "약풍",
    3: "강풍",
    4: "자동",
    5: "기저",
    6: "기저",
}

# 앱은 `option != 1` 이면 풍량 대신 옵션 라벨을 보여준다. 숙면(4)만 예외로
# 풍량을 함께 쓴다 (`AironeModeCode.labelFor`).
AIRONE_OPTIONS_WITH_WIND: Final = frozenset({AIRONE_OPTION_NONE, AIRONE_OPTION_SLEEP})

# 제습·환기제습에서만 목표 습도를 보여준다. 범위는 서버 `additionalData` 의
# min/max 를 쓴다 — 여기에 숫자를 적지 않는다.
AIRONE_MODES_WITH_HUMIDITY: Final = frozenset({9, 10})
AIRONE_HUMIDITY_TYPE: Final = 1

# `SENSOR_LEVEL_*`
AIRONE_LEVEL_NAMES: Final = {
    0: "알 수 없음",
    1: "좋음",
    2: "보통",
    3: "나쁨",
    4: "매우나쁨",
}

# `/air-sensor` 의 `airs[].type` — 문자열이다. `SensorDid.type` 의 정수와 다른
# 체계다. 여기 없는 종류는 만들지 않고 로그만 남긴다.
#
# `unit` 이 None 인 항목은 앱도 숫자를 안 보여준다 (`getValueText` 가 `level` 을
# 그대로 반환한다). 숫자를 만들어 붙이지 않는다.
AIRONE_SENSOR_KINDS: Final = {
    "pm1Dot0": ("극초미세먼지", "㎍/㎥"),
    "pm2Dot5": ("초미세먼지", "㎍/㎥"),
    "pm10": ("미세먼지", "㎍/㎥"),
    "co2": ("이산화탄소", "ppm"),
    "tvoc": ("휘발성유기화합물", None),
    "radon": ("라돈", None),
    "temperature": ("온도", "°C"),
    "humidity": ("습도", "%"),
    "total": ("종합 공기질", None),
}


def airone_mode_label(mode: int, option: int) -> str:
    """운전 모드 조합을 앱과 같은 말로 옮긴다."""
    base = AIRONE_MODE_NAMES.get(mode, f"알 수 없음({mode})")
    suffix = AIRONE_OPTION_NAMES.get(option)
    return f"{base} · {suffix}" if suffix else base

# --- 제어 축 (heatControl.unit) -------------------------------------------

# dex 전수 검색 결과 값은 이 둘뿐이다. `<간격><축>` 형식.
UNIT_LEVEL: Final = "1.0L"  # heater.*.level.set — 정수 단계
UNIT_CELSIUS: Final = "0.5C"  # heater.*.temperature.set — 0.5도 간격
KNOWN_UNITS: Final = (UNIT_LEVEL, UNIT_CELSIUS)

# --- operationMode (KDMode.WIFI 맵) ---------------------------------------

MODE_POWER_OFF: Final = 0
MODE_HEAT: Final = 1
MODE_RESERVE: Final = 2
MODE_SLEEP: Final = 3
MODE_STERILIZE: Final = 4
MODE_DISCHARGE: Final = 5
MODE_ERROR: Final = 6
MODE_CUSTOM_SLEEP: Final = 7
MODE_AISLEEP: Final = 8
MODE_CHANGE_HEAT: Final = 99
MODE_BED_DRYING: Final = 129
MODE_HYPER: Final = 130

MODE_NAMES: Final = {
    MODE_POWER_OFF: "전원 꺼짐",
    MODE_HEAT: "난방",
    MODE_RESERVE: "예약",
    MODE_SLEEP: "수면모드",
    MODE_STERILIZE: "살균",
    MODE_DISCHARGE: "배수",
    MODE_ERROR: "오류",
    MODE_CUSTOM_SLEEP: "개인맞춤 수면",
    MODE_AISLEEP: "AI 수면",
    MODE_CHANGE_HEAT: "난방 전환",
    MODE_BED_DRYING: "침대 건조",
    MODE_HYPER: "하이퍼",
}

# 전원이 켜진 것으로 볼 모드. `MODE_ERROR` 는 제외한다.
MODES_ON: Final = frozenset(
    {
        MODE_HEAT,
        MODE_RESERVE,
        MODE_SLEEP,
        MODE_STERILIZE,
        MODE_CUSTOM_SLEEP,
        MODE_AISLEEP,
        MODE_CHANGE_HEAT,
        MODE_BED_DRYING,
        MODE_HYPER,
    }
)

# --- 난방 구역 ------------------------------------------------------------

ZONE_SINGLE: Final = "single"
ZONE_LEFT: Final = "left"
ZONE_RIGHT: Final = "right"

ZONE_NAMES: Final = {ZONE_SINGLE: "난방", ZONE_LEFT: "좌측", ZONE_RIGHT: "우측"}

# `modelType`. 접두사로 추론하면 틀린다 — EMW750 은 EMW 인데 사계절이다.
MODEL_TYPE_LABELS: Final = {"em": "카본", "wm": "온수", "fm": "사계절"}

# --- 계절 (사계절 모델) ---------------------------------------------------

# `Constants.SUMMER_SEASON` / `WINTER_SEASON`. 값 1 의 용도는 미확인.
SEASON_WINTER: Final = 0
SEASON_SUMMER: Final = 2
SEASON_NAMES: Final = {SEASON_WINTER: "난방", SEASON_SUMMER: "냉방"}

# 사계절 모델은 `season` 이 어느 제어 서술자를 쓸지 고른다 —
# 여름이면 `coolControl`, 그 외에는 `heatControl`. 설정값 경로는 `heater` 를 공유한다.
# 냉방 전용 operationMode 는 `KDMode.WIFI` 맵에 없다.
#
# v1 은 사계절 기기에 제어 엔티티를 만들지 않는다. 값 체계가 확인되지 않았고,
# 냉방 중인 기기를 난방 범위로 표시하면 사용자가 오해한다.
# 실기기 제보가 오면 연다.

# `mcu.capacity` 로 싱글/더블을 가른다. `matType` 이 아니다 (실측: 둘 다 1이었다).
CAPACITY_SINGLE: Final = 1
CAPACITY_DOUBLE: Final = 2

# --- 단계형 제어 표기 ---------------------------------------------------

# 0은 숫자가 아니라 상태다. 앱 슬라이더의 맨 왼쪽이 이것이고, 서버가 알려주는
# `heatControl.rangeMin` (실측 1) 보다 낮다. `level 0` + `enable false` 로 함께 온다.
LEVEL_STANDBY: Final = 0
LABEL_STANDBY: Final = "운전 대기"


def level_label(level: int) -> str:
    """단계 값을 앱과 같은 말로 옮긴다."""
    return LABEL_STANDBY if level == LEVEL_STANDBY else f"{level}단계"

# --- 설정 키 --------------------------------------------------------------

CONF_HOME_SEQ: Final = "home_seq"

# 재접속 후 초기 동기화용. 상태는 MQTT 푸시로 오므로 폴링 주기를 짧게 둘 이유가 없다.
UPDATE_INTERVAL_SECONDS: Final = 900

# 에어원이 있으면 짧게 돈다. 공기질 값은 MQTT 로 오지 않고 `/air-sensor` 를 읽어야
# 하는데, 15분마다 갱신되는 미세먼지 수치는 쓸 수가 없다. 앱은 60초마다 읽는다.
AIRONE_UPDATE_INTERVAL_SECONDS: Final = 300
