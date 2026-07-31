"""나비엔 스마트 통합 상수.

**추측한 값은 없다.** 전부 앱에서 직접 추출했다. 다만 검증 수준이 다르다.

- 매트(200) 관련 값 — 실기기로 검증했다. 단계형은 직접, 온도형·사계절은 제보로
- 에어원(300) 관련 값 — 앱에서 추출한 뒤 **제보로 확인했다**. 확인된 모델은
  룸콘 분리형(1901)과 올인원 룸콘(1900) 두 계열이다

**확인의 무게가 다르다.** 직접 눌러본 것과 제보로 들은 것을 섞어 적지 않는다.
각 항목에 어느 쪽인지 적어 두었다.
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

# **요청 하나가 폴링 주기를 통째로 먹지 않게 한다.**
#
# `aiohttp` 기본값은 5분이라 에어원 폴링 주기와 똑같다. 서버가 연결만 받아두고
# 응답하지 않으면 그 한 번이 주기 전체를 잡아먹는다.
REQUEST_TIMEOUT_SECONDS: Final = 30

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

# 구세대는 `v2` 조각이 없다 (CHANGELOG 「구형은 건너뛴다」의 토픽 표).
AIRONE_LEGACY_TOPIC_FMT: Final = "cmd/rc/{model_code}/{device_id}/remote/{command}"

AIRONE_CMD_STATUS: Final = "status"
AIRONE_CMD_POWER: Final = "power"
AIRONE_CMD_CHANGE_MODE: Final = "change-mode"

# 명령을 보낸 뒤 상태를 다시 물어보기까지 기다리는 시간. 기기가 스스로 올려주면
# 그게 먼저 도착하고, 안 올려주면 이 한 번으로 따라잡는다.
# **낙관적 갱신 대신 쓰는 장치다** — 실패를 성공처럼 보이게 하지 않는다.
AIRONE_READBACK_DELAY_SECONDS: Final = 3

# 공기질 조회가 이만큼 연속 실패하면 `WARNING` 으로 알린다. 5분 주기이므로
# 3회면 약 15분이다. 매번 찍으면 로그가 시끄럽고, 아예 안 찍으면 값이 멈춘 것을
# 아무도 모른다.
AIRONE_AIR_ERROR_LOG_EVERY: Final = 3

# 상태를 요청한 뒤 이만큼 지나도 응답이 없으면 로그로 알린다. 매트 실측 왕복이
# 1.4초였으니 넉넉하다 — 조용한 실패를 사용자가 알 수 있어야 한다.
AIRONE_SILENCE_CHECK_SECONDS: Final = 45

# `running` — V2.1 세대. 레거시는 반대(운전=2)다. 세대를 안 가리면 전원이 뒤집힌다.
AIRONE_RUN_ON: Final = 1
AIRONE_RUN_OFF: Final = 2
AIRONE_RUN_AWAY: Final = 3
# 4 — 제습 운전을 끈 뒤 기기가 스스로 내부를 말리는 「자동 건조」 상태. 실기기
# `NRT-530Z3`(모델코드 1901)에서 전원을 끄면 이 값이 관측됐다. 없으면 운전 상태가
# 「알 수 없음(4)」으로 뜬다.
AIRONE_RUN_AUTO_DRY: Final = 4
AIRONE_RUN_NAMES: Final = {
    AIRONE_RUN_ON: "운전",
    AIRONE_RUN_OFF: "정지",
    AIRONE_RUN_AWAY: "외출",
    AIRONE_RUN_AUTO_DRY: "자동 건조중",
}

# 값은 `ROOM_OPERATION_MODE_*` / `OPERATION_MODE_*`, 이름은 앱 제어화면 문자열
# (`STR_FRAGMENT_AIRONE_CONTROL_MODE_*_TITLE`)에서 가져왔다. 「~모드」 접미사는
# 옵션과 붙일 때 어색해져서 뺀다 (환기모드 · 터보 → 환기 터보).
#
# 서버가 주지 않는 모드도 라벨을 둔다. 표시용일 뿐이고 제어는 서버가 알려준
# 조합에서만 나오므로 위험하지 않다 — 모르는 값이 와도 숫자로 보이지 않는다.
AIRONE_MODE_NAMES: Final = {
    0: "없음",
    4: "환기",  # 환기모드
    5: "배기",  # 배기모드
    6: "요리",  # 요리모드
    8: "청정",  # 청정모드
    9: "제습",  # 제습모드
    10: "환기제습",
    12: "자동운전",
    15: "환기(외기)",  # OPERATION_MODE_AERATION — 앱 명칭 미확인
    17: "바이패스",
    18: "음압환기",
}

# --- 구세대 에어원 모드 목록 -----------------------------------------------
#
# **구세대에서 DID 는 능력 목록이 아니다.** 앱이 아예 보지 않는다.
#
#     AirOneControlViewModel:
#         if (modelCode < 1000) { loadlegacyModeDataFromFile(); ... }
#     → assets/jsons/airone_legacy_mode_did.json 을 읽어 modeDidList 로 쓴다
#
# 아래는 그 파일(APK 2.10.4)의 내용을 그대로 옮긴 것이다. 추측이 없다.
#
# **바이패스(17)는 그 파일에 없다. 앱이 기기 능력을 보고 따로 더한다.**
#
#     if (did.supportByPass == 2)          modeDidList.add(ModeDid(17, 1, …))
#     if (did.ventiMode.basalairUse == 2)  modeDidList.add(ModeDid(4, 6, …))   기저
#     if (did.kitchenMode.autoUse == 2)    요리 풍량을 자동(4)으로
#
# **그 플래그를 우리는 읽을 수 없다.** `DidPacket` 에 있고, MQTT 로 DID 를 따로
# 요청해서 받는 응답이다 — 기기목록(REST)에는 없다. 그래서 조건을 평가할 수 없다.
#
# 그럼에도 17 을 넣는 이유는 셋이다.
#
# 1. 실기기 **두 대**(`NRT-20DS` · `NRT-20DSW`)의 앱 화면에 바이패스가 있다
# 2. 기여자가 17 명령이 기기에 먹는 것을 확인했다 — DID 가 안 알려준 기기에서도
# 3. 안 넣으면 두 기기 모두 있는 기능이 사라진다
#
# **정확히 하려면 DID 를 MQTT 로 요청해 `supportByPass` 를 읽어야 한다.** 그게
# 남은 일이고, 기저 환기(4,6)와 요리 풍량 자동은 그때까지 넣지 않는다.
#
# 모델별 표가 아니라 **세대별 표 하나**다. 앱도 모든 구세대에 같은 것을 쓴다.
#
# **파일의 `additionalData` 는 습도가 아니다.** 모든 항목이 `{type:1, min:0, max:4}`
# 인데 이건 풍량 범위(0~4)다. 신형에서 `type:1` 이 습도 범위인 것과 겹치므로
# 그대로 넘기면 「목표 습도 0~4%」가 만들어진다. 여기서는 싣지 않는다.
#
# **`configurable` 을 빠뜨렸었다 (v0.13.1 정정).** 파일에 그 값이 있는데 옮길 때
# 흘렸고, 대신 「option == 1 이면 고를 수 있다」로 짐작했다. 그 짐작이 자동운전과
# 요리에서 틀렸다 — 둘 다 `false` 인데 미풍·약풍·강풍을 만들고 있었다.
# 구형 제보자 화면에 자동운전인데 풍량 네 개가 뜬 것이 이것이다.
#
# (모드, 옵션, 기본 풍량, configurable)
LEGACY_MODE_DID: Final = (
    (12, 1, 4, False),   # 자동운전 — 풍량 고정
    (4, 1, 1, True),     # 환기
    (6, 1, 3, False),    # 요리 — 강풍 고정
    (4, 2, 3, False),    # 환기 터보
    (4, 3, 4, False),    # 환기 절전
    (4, 4, 4, False),    # 숙면 — 앱은 (4,4) 를 별도 모드로 보여준다
    (8, 1, 1, True),     # 청정
    (8, 2, 3, False),    # 청정 터보
    (8, 3, 4, False),    # 청정 절전
    # 바이패스는 내장 파일에 없다. 신형 세 대가 모두 `configurable: true` 라
    # 그것을 따른다 — 유일한 근거다.
    # **모든 구형에 붙이지 않는다.** `LEGACY_BYPASS_MARK` 참조.
    (17, 1, 4, True),
)

# --- 구형 바이패스 — **있다고 확인된 모델만** ----------------------------
#
# 앱은 기기가 알려주는 플래그로 가른다.
#
#     if (did.supportByPass == 2)  modeDidList.add(ModeDid(17, 1, …))
#
# **그 플래그는 MQTT DID 에만 있고 우리는 못 읽는다.** 그래서 v0.11.0~v0.14.2 는
# 모든 구형에 붙였고, `NRT-20D` 제보(#13)로 없는 기기가 있다는 것이 드러났다.
#
# | 모델 | 바이패스 | 근거 |
# | --- | --- | --- |
# | `NRT-20DS` · `NRT-20DSW` | 있음 | 앱 화면 · 17 명령 적용 확인(PR #7) |
# | `NRT-21DS` | 있음 | 같은 계열의 `S` 등급 |
# | `NRT-30` | 있음 | 제품 자료 — 상위 사양 |
# | `NTR-10PW` | 있음 | 제품 자료 — 계열이 다르다 |
# | `NRT-20D` · `NRT-21D` | **없음** | 제보 #13 · 제품 자료 — 기본형 |
#
# **`S` 규칙으로 거를 수 없다.** `NTR-10PW` 가 반례다 — `S` 없이 바이패스가 있다.
# 그래서 이름을 직접 적는다.
#
# **모르는 모델은 안 넣는다.** v0.14.3 은 반대로(확인된 것만 빼기) 갔다가
# 되돌렸다. 구형은 계열이 여섯뿐이라 목록을 관리할 수 있고, **없는데 보이는
# 쪽이 더 나쁘다** — 누르면 실외기가 서고 사용자가 앱으로 되돌려야 한다.
# 있는데 안 보이는 것은 제보 한 줄로 넣으면 된다.
#
# 앞부분만 맞으면 통과시킨다 — `NRT20DS` 로 `NRT20DSW` 까지 걸린다.
# 비교 전에 `[^A-Z0-9]` 를 지운다. 서버가 `NRT20D` 로 줄 수 있다.
LEGACY_BYPASS_MODEL_PREFIXES: Final = ("NRT20DS", "NRT21DS", "NRT30", "NTR10PW")

AIRONE_MODE_BYPASS: Final = 17

# 구세대 DID 에는 `supportedAirVolumes` 가 없다. `configurable` 인 조합에서
# 고를 수 있는 풍량은 앱 표를 그대로 쓴다 — 기여자가 실기기에서 미풍·약풍·강풍·
# 자동이 모두 동작하는 것을 확인했다.
LEGACY_DEFAULT_AIR_VOLUMES: Final = (1, 2, 3, 4)


# 구세대 상태 프레임이 신형에 없는 값을 더 싣는다. 해석해서 제어에 쓰지 않고
# 진단 센서로만 보여준다 — 뜻이 확인된 것만 이름을 붙였다.
# 이름은 앱 표기를 따르고, 뜻이 확인되지 않은 것은 원본 필드명을 괄호에 남긴다 —
# 값이 이상할 때 어느 필드인지 바로 짚을 수 있어야 한다.
# (필드, 표시이름, 값 대응표). 대응표가 있으면 숫자 대신 이름을 보여준다 —
# 앱에서 뽑은 표가 있는 것만이고, **1/2 플래그 체계가 확인되지 않은 항목은
# 표를 붙이지 않는다.** 추측해서 켜짐·꺼짐을 달면 반대로 보일 수 있다.
LEGACY_EXTRA_FIELDS: Final = {
    "radonStageValue": ("radon_stage", "라돈 단계", "level"),
    "freeFilterUsedTime": ("filter_used_time", "필터 사용 시간", None),
    "freeFilterCleanAlarmFlag": ("filter_clean_alarm", "필터 청소 알림", None),
    "hepaFilterCleanAlarmFlag": ("hepa_filter_clean_alarm", "헤파필터 청소 알림", None),
    "deepSleepMode": ("deep_sleep_mode", "숙면 동작", None),
    "bypassOperation": ("bypass_operation", "바이패스 동작", None),
    "connectedSensingBox": ("connected_sensing_box", "센싱박스 연결", None),
    "supportedOperationMode": ("set_operation_mode", "설정 운전모드", "mode"),
    "oduOperationMode": ("odu_operation_mode", "실외기 동작모드", "mode"),
    "desiredAirVolume": ("set_air_volume", "설정 풍량", "wind"),
    "airVolume": ("actual_air_volume", "실제 풍량", "wind"),
    "errorState": ("error_state", "오류 상태", "error"),
}


# --- 구세대(에어원 `modelCode < 1000`) 상태 프레임 -------------------------
#
# 봉투가 `{topic, payload: {...}, serviceCode}` 이고 **`reported` 도 `roomController`
# 도 없는 평평한 구조**다. 필드 이름이 신형과 다를 뿐 아니라 **오해를 부른다** —
# 실기기(NRT-20DSW, modelCode 8)에서 앱으로 바이패스를 걸어놓고 45개 필드를
# 통째로 비교해 확인했다.
#
# | 구세대 필드 | 실제 의미 |
# | --- | --- |
# | `supportedOperationMode` | **설정된** 운전모드 (능력 목록이 아니다) |
# | `oduOperationMode` | 실외기가 **지금 실제로** 도는 모드 (1=정지) |
# | `desiredAirVolume` | **설정된** 풍량 |
# | `airVolume` | **실제** 송풍량 (안 불면 0) |
#
# 동작값을 읽으면 바이패스가 유령 모드로, 자동운전이 관측 불가로 보인다.
# 그래서 **설정값을 읽는다.** 동작값은 진단으로만 남긴다.
LEGACY_STATUS_TO_CONTROLLER: Final = {
    "supportedOperationMode": "mode",
    "optionFunction": "option",
    "desiredAirVolume": "airVolume",
}

# 동작값. 제어 상태로 쓰지 않고 진단에만 담는다.
LEGACY_ACTUAL_FIELDS: Final = ("oduOperationMode", "airVolume")

# `running` 값이 신형과 **반대다** (CHANGELOG 「구형은 건너뛴다」). 세대를 안 가리면
# 전원이 뒤집힌다. 구세대 `isRunning` 2=운전 → 신형 `running` 1=운전 로 맞춘다.
LEGACY_RUNNING_TO_V2: Final = {2: AIRONE_RUN_ON, 1: AIRONE_RUN_OFF}

# 구세대 명령 봉투. 신형은 `payload.state.desired`, 구세대는 `payload.request` 다.
LEGACY_CONTROLLER_TO_REQUEST: Final = {
    "mode": "operationMode",
    "option": "optionMode",
    "airVolume": "windLevel",
}
LEGACY_RUNNING_TO_REQUEST: Final = {AIRONE_RUN_ON: 2, AIRONE_RUN_OFF: 1}

# `ROOM_OPERATION_OPTION_*`. 이름은 앱 제어화면 문자열
# (`STR_FRAGMENT_AIRONE_CONTROL_OPTION_*`). 1 은 "옵션 없음" 이라 라벨이 없다.
AIRONE_OPTION_NONE: Final = 1
AIRONE_OPTION_SLEEP: Final = 4
AIRONE_OPTION_NAMES: Final = {
    2: "터보",
    3: "절전",
    AIRONE_OPTION_SLEEP: "숙면",
    5: "기저",
    6: "기저",
}

# `ROOM_OPERATION_WIND_*` + 앱 문자열 `..._CONTROL_WIND_1~3`.
# **이 표에 없는 값은 보내지 않는다** — `ModeDid.airVolume` 이 비트마스크일
# 가능성이 남아 있다 (명세 6-5 참조).
#
# SCADA(상업용)는 같은 자리에 다른 표를 쓴다 (5=수면풍, 6=절전, 7=터보).
# 섞으면 안 된다 — 여기는 가정용 룸컨트롤러 표다.
AIRONE_WIND_NAMES: Final = {
    1: "미풍",
    2: "약풍",
    3: "강풍",
    4: "자동",
    5: "기저",
    6: "기저",
}

# **서버가 목록을 안 줄 때 고를 수 있는 풍량.**
#
# `supportedAirVolumes` 라는 필드가 목록을 알려주는데 **APK 2.10.4 에는 그 필드가
# 없다.** 서버가 나중에 넣은 것이고, 옛 펌웨어 기기는 지금도 안 내려준다.
# 그때 앱이 무엇을 보고 판단하는지가 `configurable` 이다
# (`AirOneControlFragment.allowedWindChoicesFromDids`).
#
#     z10 = 그 모드 항목 중 configurable 이 하나라도 true
#     if (z10) { 미풍·약풍·강풍 을 모두 보여준다 }
#     else     { airVolume 값이 1·2·3 인 것만 }
AIRONE_SELECTABLE_AIR_VOLUMES: Final = (1, 2, 3, 4)

# 앱은 `option != 1` 이면 풍량 대신 옵션 라벨을 보여준다. 숙면(4)만 예외로
# 풍량을 함께 쓴다 (`AironeModeCode.labelFor`).
AIRONE_OPTIONS_WITH_WIND: Final = frozenset({AIRONE_OPTION_NONE, AIRONE_OPTION_SLEEP})

# 앱은 숙면을 **별도 모드 버튼**으로 둔다 (`STR_..._MODE_SLEEP_TITLE` = 「숙면모드」,
# `AironeModeCode.rawToUi` 가 option 4 일 때 원래 모드를 감추고 1001 을 반환한다).
# 그래서 숙면은 풍량 목록이 아니라 운전 모드 목록에 넣는다.
AIRONE_MODE_SLEEP_LABEL: Final = "숙면"

# 제습·환기제습에서만 목표 습도를 보여준다. 범위는 서버 `additionalData` 의
# min/max 를 쓴다 — 여기에 숫자를 적지 않는다.
AIRONE_MODES_WITH_HUMIDITY: Final = frozenset({9, 10})

# **보낼 때와 받을 때 `type` 번호가 다르다.** 짐작이 아니라 실기기 제보로 확인했다.
#
# 보내기 — `1`. 서버 능력 정보의 범위가 `{"type": 1, "min": 40, "max": 65}` 로 오고,
# 그 번호로 보내면 **앱에도 그 값이 그대로 남는다**(제보자가 앱으로 확인해 주었다).
#
# 받기 — `3`. 기기 상태의 `additionalData` 에 `{"type": 3, "value": 60}` 으로 온다.
# 같은 목록의 `type: 1` 은 값이 `1` 이고 범위가 0~4 인 **다른 항목**이다.
# v0.9.1 까지 그것을 습도로 찾다가 못 찾아 화면이 비어 있었다.
#
# 번호에만 의존하지 않는다 — 서버가 준 범위 안에 있는 값을 함께 본다.
AIRONE_HUMIDITY_TYPE: Final = 1
AIRONE_HUMIDITY_REPORT_TYPE: Final = 3

# 앱의 희망습도 −/+ 버튼이 5씩 움직인다
# (`AirOneControlFragment`: `setProgress(getProgress() ± 5)`). 서버는 min/max 만
# 주고 간격을 주지 않으므로 앱을 따른다.
AIRONE_HUMIDITY_STEP: Final = 5

# `SENSOR_LEVEL_*`
AIRONE_LEVEL_NAMES: Final = {
    0: "알 수 없음",
    1: "좋음",
    2: "보통",
    3: "나쁨",
    4: "매우나쁨",
}

# 서버가 같은 센서를 다른 이름으로 줄 수 있다. 소문자로 맞춘 뒤 이 표로 모은다.
#
# **키를 기계적으로 정규화하면 안 된다.** 구분기호를 지우면 `pm1.0` 이 `pm10` 이
# 되어 **다른 센서와 충돌한다** — PM1.0 과 PM10 은 별개 항목이다. 그래서 규칙을
# 만들지 않고 명시 표로 둔다.
AIRONE_SENSOR_ALIASES: Final = {
    # 대소문자만 다른 표준 이름
    "pm1dot0": "pm1Dot0",
    "pm2dot5": "pm2Dot5",
    # 미세먼지 — `pm1` 계열과 `pm10` 을 섞지 않도록 하나씩 적는다
    "pm1": "pm1Dot0",
    "pm1.0": "pm1Dot0",
    "pm1_0": "pm1Dot0",
    "pm25": "pm2Dot5",
    "pm2.5": "pm2Dot5",
    "pm2_5": "pm2Dot5",
    # 라돈
    "radonvalue": "radon",
    "radon_value": "radon",
    "radonbq": "radon",
    "radonbqm3": "radon",
    "radonstagevalue": "radon",
    "radon_stage_value": "radon",
    "radonconcentration": "radon",
    "radon_concentration": "radon",
    # 휘발성유기화합물
    "voc": "tvoc",
    "t_voc": "tvoc",
    "tvocvalue": "tvoc",
    # 종합 공기질
    "airquality": "total",
    "air_quality": "total",
    "airqualityscore": "total",
    "air_quality_score": "total",
    "totalairquality": "total",
    # 그 밖
    "co2value": "co2",
    "carbondioxide": "co2",
}

# `/air-sensor` 의 `airs[].type` — 문자열이다. `SensorDid.type` 의 정수와 다른
# 체계다. 여기 없는 종류는 만들지 않고 로그만 남긴다.
#
# `unit` 이 None 인 것은 "값이 없다"는 뜻이 아니라 **단위를 확인하지 못했다**는
# 뜻이다. 앱이 `tvoc`·`radon` 을 등급으로 **표시**하길래 값이 없다고 판단했는데,
# 실사용 제보로 숫자가 온다는 것이 확인됐다 (라돈 수치, TVOC 70.0,
# 종합 82.0). `getValueText` 는 표시 함수일 뿐이고 `Air.value` 에는 숫자가 있다.
#
# 단위는 근거 수준을 나눠서 정한다. **앱이 쓰는 단위 문자열은 `"ppm"` 과
# `"㎍/㎥"` 둘뿐이다** (dex 전수 검색). 나머지는 아래 판단을 따른다.
#
# `radon` — `Bq/㎥` 를 쓴다. **추측이 아니라 이 시장에 단위가 하나뿐이다.**
#   국내 실내공기질 기준이 전부 Bq/㎥ 이고(다중이용시설·학교 148 Bq/㎥),
#   `pCi/L` 은 미국만 쓴다. 국내 판매 기기가 라돈을 숫자로 주면 Bq/㎥ 다.
#   앱은 등급만 보여주므로 문자열이 없지만, 값의 단위가 없는 것은 아니다.
#
# `tvoc` — 비운다. `㎍/㎥`(국내 기준 500) / `ppb`(소비자 센서 관행) /
#   지수(0~500) 셋이 경합하고, 관측값 70 이 셋 다에 맞아떨어진다.
#   **동전 던지기라서 비운다.** 라돈과 상황이 다르다.
#
# `total` — 단위가 없다. 기기 화면이 「통합공기질 78」로 점수만 보여준다.
#
# 두 항목의 등급은 `grade` 속성에 있다 — 기기 화면과 같은 값이다.
#
# `device_class` 는 HA 가 아이콘·히스토리 그래프·단위 변환에 쓴다. 라돈에는
# HA 표준 device_class 가 없어 단위만 붙인다.
AIRONE_SENSOR_KINDS: Final = {
    "pm1Dot0": ("극초미세먼지", "㎍/㎥", "pm1"),
    "pm2Dot5": ("초미세먼지", "㎍/㎥", "pm25"),
    "pm10": ("미세먼지", "㎍/㎥", "pm10"),
    "co2": ("이산화탄소", "ppm", "carbon_dioxide"),
    "tvoc": ("휘발성유기화합물", "ppb", None),
    "radon": ("라돈", "Bq/㎥", None),
    "temperature": ("온도", "°C", "temperature"),
    "humidity": ("습도", "%", "humidity"),
    "total": ("종합 공기질", None, None),
}

# 단위를 앱에서 뽑지 않고 판단으로 정한 항목. 제보로 틀린 것이 드러나면 고친다.
# 진단에 남겨서 어느 값이 근거 없이 붙었는지 밖에서 볼 수 있게 한다.
#
# **앱이 화면에 적는 단위는 넷뿐이다.** 리소스 전수 확인 (APK 2.10.4):
#
#     극초미세먼지 PM1.0 (㎍/㎥)   초미세먼지 PM2.5 (㎍/㎥)
#     미세먼지    PM10  (㎍/㎥)   이산화탄소 CO2   (ppm)
#
# TVOC 와 라돈은 **앱도 단위를 안 보여준다.** 설명 화면 제목이
# 「휘발성 유기화합물 TVOC」 · 「라돈 RADON」 으로 끝난다. `ppb` 와 `Bq` 는
# 리소스에도 코드에도 없다.
#
# 그래도 붙이는 이유는 **숫자만 있고 단위가 없으면 읽을 수 없어서**다.
# 둘 다 그 분야에서 쓰는 단위가 사실상 하나다. 틀렸다면 제보로 고친다.
AIRONE_INFERRED_UNITS: Final = frozenset({"radon", "tvoc"})


def airone_mode_label(mode: int, option: int) -> str:
    """운전 모드 조합을 앱과 같은 말로 옮긴다.

    앱은 `숙면` 일 때 원래 모드를 감추고 「숙면」만 보여주지만
    (`AironeModeCode.rawToUi` 가 1001 을 반환한다), 여기서는 감추지 않는다.
    `(9,4)` 와 `(4,4)` 가 둘 다 있으면 목록에 같은 이름이 두 개 생겨서
    사용자가 고를 수 없게 된다.
    """
    base = AIRONE_MODE_NAMES.get(mode, f"알 수 없음({mode})")
    suffix = AIRONE_OPTION_NAMES.get(option)
    return f"{base} {suffix}" if suffix else base

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

# 매트 오류 코드 이름. **제보자가 기기 설명서에서 옮겨 준 것**이다(2026-07-31).
#
# **온도형(`0.5C`) 에만 붙인다.** 물탱크·순환펌프·UV램프·냉각장치·누수가 나오는
# 것으로 보아 온수·사계절 계열 설명서다. 카본(단계형)에 같은 번호가 같은 뜻이라는
# 근거가 없어서, 단계형에는 숫자만 보여준다.
#
# 상태값은 숫자 그대로 두고 **속성으로만** 보여준다. 상태를 글자로 바꾸면 이 값을
# 쓰던 자동화·템플릿이 깨진다.
MAT_ERROR_NAMES: Final = {
    0: "정상",
    2: "물 부족 (Er 02)",
    5: "물탱크 공급 온도 센서 (Er 05)",
    7: "외기 온도 센서 (Er 07)",
    8: "순환펌프 동작 (Er 08)",
    9: "팬 이상 (Er 09)",
    11: "수위 감지 (Er 11)",
    15: "UV램프 (Er 15)",
    16: "물탱크 과열 (Er 16)",
    17: "난방 이상 (Er 17)",
    18: "온도센서 (Er 18)",
    26: "냉각장치 과냉 (Er 26)",
    27: "냉방 이상 (Er 27)",
    28: "누수 (Er 28)",
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

# --- 조작음 음량 ----------------------------------------------------------
#
# **앱 음량 화면에 칸이 넷이다** — 음소거 + 3단계.
# `MateDeviceSettingSoundVolumeFragment` 가 `selectedIndex` 를 0·1·2·3 중 하나로
# 정하고 그대로 `setSoundVolume(int)` 에 넘긴다. 읽을 때도 같은 값으로 갈린다
# (`initializeCurrentVolumeLevel`: 0 이면 음소거 아이콘, 1·2 각각, 나머지는 3).
#
# 보내는 필드는 `Desired.volume` (`Integer`), 명령 이름은 `control-volume`.
# 라벨은 앱 화면 문구가 아니라 아이콘 이름(`mute`, `volumeLevel1~3`)을 따랐다.
MAT_VOLUME_NAMES: Final = {0: "음소거", 1: "1단계", 2: "2단계", 3: "3단계"}

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

# 진단 값을 사람이 읽는 이름으로 옮기는 표. 위 표들이 정의된 뒤에 둔다.
LEGACY_VALUE_TABLES: Final = {
    "mode": AIRONE_MODE_NAMES,
    "wind": AIRONE_WIND_NAMES,
    "level": AIRONE_LEVEL_NAMES,
    "error": {0: "정상"},
}
