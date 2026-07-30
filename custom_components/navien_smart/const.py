"""나비엔 스마트 통합 상수.

여기 있는 값은 전부 앱에서 추출해 실기기로 검증한 것이다. 추측한 값은 없다.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "navien_smart"

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

# v1 은 매트만 지원한다. 나머지는 실기기 검증이 없어 넣지 않는다.
#
# 보일러(100)는 매트와 체계가 다르다 — 상태 모델이 컨트롤러 종류별로 갈리고
# (`GetStatusData` 3,500줄 + 1st/2nd/NRM35), 각방 제어가 비트마스크이며 온도값이
# 모델별로 인코딩된다(`decodeTempValueForModel`). 재현을 잘못하면 엉뚱한 온도가 간다.
#
# 환기청정(300)은 shadow 가 아니라 서버가 지정한 `requestTopic`/`responseTopic` 에
# `sessionId` 를 실어 주고받고, 상태가 패킷 디스크립터로 쪼개져 있다.
SUPPORTED_SERVICE_CODES: Final = (SERVICE_MATE,)

# 제보를 받아 지원을 넓힐 대상. 진단 내보내기에 **원본을 전부** 담고 제보를 요청한다.
# 지원을 약속하는 목록이 아니다 — 데이터를 모아야 판단이 되는 목록이다.
REPORT_WANTED_SERVICE_CODES: Final = (SERVICE_AIRONE, SERVICE_BOILER)

# 범위 밖. 제보를 요청하지 않고, 진단에도 요약만 남긴다.
OUT_OF_SCOPE_REASONS: Final = {
    SERVICE_SCADA: "상업용 장비는 이 통합의 범위가 아닙니다",
    SERVICE_HOMEAUTO: "월패드·로비폰은 서버 체계가 완전히 달라 범위가 아닙니다",
}

# 제보를 요청할 때 함께 알릴 현황. 낙관도 비관도 하지 않는다.
REPORT_WANTED_NOTES: Final = {
    SERVICE_AIRONE: (
        "매트와 통신 방식이 달라(서버가 지정한 요청/응답 토픽 + 패킷 구조) "
        "실기기 정보가 필요합니다"
    ),
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

# MQTT 구독 토픽 접두사. 매트는 실측 확인됨 (`{homeSeq}/mate/{deviceId}`).
# 보일러·에어원 접두사는 미확인이라 넣지 않는다.
TOPIC_PREFIX: Final = {SERVICE_MATE: "mate"}

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

# --- 설정 키 --------------------------------------------------------------

CONF_HOME_SEQ: Final = "home_seq"

# 재접속 후 초기 동기화용. 상태는 MQTT 푸시로 오므로 폴링 주기를 짧게 둘 이유가 없다.
UPDATE_INTERVAL_SECONDS: Final = 900
