"""진단 정보 내보내기.

설정 → 기기 및 서비스 → 나비엔 스마트 → ⋮ → `통계정보 다운로드` 로 받는다 (한국어 번역이 「통계」다. 원문 Download diagnostics).
사용자가 무엇을 붙여야 할지 몰라도 되게 하는 것이 목적이다.

**환기청정은 원본을 전부 담는다** — 지원을 넓히려면 그게 유일한 근거다.
범위 밖 기기(보일러·상업용·월패드)는 요약만 남긴다. 쓰지 않을 데이터를
내보낼 이유가 없다.

식별자는 전부 가린다. 별칭도 가린다 — 사람 이름이 들어가는 경우가 많다.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import NavienSmartConfigEntry
from .const import (
    AIRONE_INFERRED_UNITS,
    AIRONE_SENSOR_KINDS,
    IOT_ENDPOINT,
    KNOWN_UNITS,
    REPORT_WANTED_SERVICE_CODES,
    SERVICE_NAMES,
    SUPPORTED_SERVICE_CODES,
)

# 키 이름으로 지운다. `heater.left` / `right` 는 구조라서 건드리면 안 되므로
# 별칭은 상위 키(`nickName`, `userInfo`)를 통째로 가린다.
TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "accessToken",
    "refreshToken",
    "accountId",
    "clientId",
    "defaultClientId",
    "deviceId",
    "eventId",
    "localIp",
    "mac",
    "mqttTopicKey",
    "nickName",
    "regionCode",
    "sessionId",
    "ssid",
    "thingArn",
    "thingId",
    "thingName",
    "userId",
    "userInfo",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NavienSmartConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data

    supported: list[dict[str, Any]] = []
    report_wanted: list[dict[str, Any]] = []
    out_of_scope: list[dict[str, Any]] = []

    for raw in coordinator.raw_devices:
        service_code = raw.get("serviceCode")
        if service_code in SUPPORTED_SERVICE_CODES:
            supported.append(async_redact_data(raw, TO_REDACT))
        elif service_code in REPORT_WANTED_SERVICE_CODES:
            # 지원을 넓힐 대상. 구조를 봐야 하므로 원본을 담는다.
            report_wanted.append(async_redact_data(raw, TO_REDACT))
        else:
            out_of_scope.append(
                {
                    "serviceCode": service_code,
                    "service": SERVICE_NAMES.get(service_code),
                    "modelName": raw.get("modelName"),
                    "modelCode": raw.get("modelCode"),
                }
            )

    return {
        "integration": {
            "iot_endpoint": IOT_ENDPOINT,
            "mqtt_connected": coordinator.mqtt_connected,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "known_control_units": list(KNOWN_UNITS),
            # **「상태가 안 온다」의 원인을 로그 없이 가리는 값.**
            # 받은 개수가 0 이면 안 오는 것이고, 버린 개수가 있으면 와도 못 쓰는
            # 것이다. 후자면 `airone_last_unknown_shape_keys` 가 어느 모양인지
            # 알려준다. 개수와 키 이름뿐이라 개인정보는 없다.
            "mqtt_messages": coordinator.mqtt_stats,
        },
        "counts": {
            "total": len(coordinator.raw_devices),
            "supported": len(supported),
            "report_wanted": len(report_wanted),
            "out_of_scope": len(out_of_scope),
        },
        "entities": [_entity_view(device) for device in (coordinator.data or {}).values()],
        # 에어원은 실기기 미검증이다. 해석 결과를 그대로 담아 제보 근거로 쓴다.
        "airone_entities": [
            _airone_view(device) for device in coordinator.airone.values()
        ],
        "supported_devices": supported,
        # 이 항목이 비어 있지 않으면 이슈에 그대로 붙여 주세요.
        "report_wanted_devices": report_wanted,
        "out_of_scope_devices": out_of_scope,
    }


def _entity_view(device: Any) -> dict[str, Any]:
    """통합이 기기를 어떻게 해석했는지. 오해가 어디서 생겼는지 찾을 때 쓴다."""
    heat = device.heat_control
    cool = device.cool_control
    return {
        "service_code": device.service_code,
        "model_code": device.model_code,
        "model_name": device.model_name,
        "model_type": device.model_type,
        "capacity": device.capacity,
        "zones": list(device.zones),
        "is_double": device.is_double,
        "is_four_season": device.is_four_season,
        "season": device.season,
        "is_cooling": device.is_cooling,
        "has_unknown_season": device.has_unknown_season,
        # 상태가 어느 묶음까지 왔는지. 사계절 모델이 부분 응답을 보내서
        # `season`·`operationMode` 가 빠지는 일이 있었다 (v0.9.0).
        "reported_keys": sorted(device.reported or {}),
        "reported_heater_zones": sorted((device.reported or {}).get("heater") or {}),
        # **냉방을 닫으려면 순서를 봐야 한다.** 「26.0 을 보냈는데 기기가 26.0 을
        # 돌려주는가」, 「한쪽만 보냈는데 양쪽이 따라오는가」, 「`season` 이 실제로
        # 무엇으로 바뀌는가」는 그 순간의 값만으로 알 수 없다.
        # `at` 은 절대 시각이 아니라 **간격을 보기 위한 초 단위 눈금**이다
        # (기기 부팅 이후 흐른 초). 값 자체는 뜻이 없고 줄 사이의 차이만 쓴다.
        # 온도·단계 값뿐이라 개인정보는 없다.
        "command_log": list(device.command_log),
        "state_log": list(device.state_log),
        "available": device.available,
        "operation_mode": device.operation_mode,
        "error_code": device.error_code,
        "heat_control": heat.as_diagnostics() if heat else None,
        "cool_control": cool.as_diagnostics() if cool else None,
        "control_unit_known": bool(heat and heat.is_known),
        "functions": {
            "power_ctrl": device.has_power_ctrl,
            "lock_mode": device.has_lock_mode,
            "power_saving": device.has_power_saving,
            "sleep_mode": device.has_sleep_mode,
            "sleep_durations_minutes": device.sleep_durations,
            "schedule_kinds": list(device.schedule_kinds),
        },
        "zone_state": {
            zone: {
                "setting": device.zone_setting(zone),
                "current": device.zone_current(zone),
                "enabled": device.zone_enabled(zone),
            }
            for zone in device.zones
        },
    }


def _numbers_only(raw: Any) -> dict[str, Any]:
    """딕셔너리에서 **숫자와 참·거짓만** 남긴다.

    이름을 몰라도 값을 볼 수 있게 하는 그물이다. 목표 습도가 어느 키로 오는지
    모르는 상태라 키를 지정할 수 없다.

    문자열을 통째로 빼는 것이 가림 장치다 — 기기ID·SSID·별칭·MAC 은 모두
    문자열이므로 여기 걸리지 않는다. **가릴 키를 나열하는 방식은 새 키가
    생기면 새는데**, 이 방식은 새 문자열 키가 생겨도 안 나간다.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(value, (int, float, bool)) and key not in TO_REDACT
    }


def _airone_view(device: Any) -> dict[str, Any]:
    """에어원을 어떻게 해석했는지.

    **실기기 미검증 구간이라 이 표가 제보의 핵심이다.** 서버가 알려준 조합과
    통합이 만든 선택 항목을 나란히 담아, 어긋난 곳을 바로 볼 수 있게 한다.
    """
    return {
        "service_code": device.service_code,
        "model_code": device.model_code,
        "model_name": device.model_name,
        "odu_model_code": device.odu_model_code,
        "is_v2_generation": device.is_v2_generation,
        # **「상태가 안 온다」와 「와도 못 붙인다」를 가리는 값들.** 값을 담지 않고
        # 관계와 유무만 담는다.
        #
        # 토픽에 쓰는 식별자가 기기목록의 것과 같은지 — 올인원 룸콘과 분리형이
        # 여기서 갈릴 수 있다. 가림 때문에 두 ID 를 눈으로 비교할 수 없어서
        # 같은지 여부를 따로 적는다.
        "physical_id_same_as_device_id": (
            device.physical_device_id == device.device_id
        ),
        # 상태가 한 번이라도 도착했는지, 어느 묶음이 왔는지.
        "reported_received": bool(device.reported),
        "reported_keys": sorted(device.reported or {}),
        "reported_room_controller_keys": sorted(
            (device.reported or {}).get("roomController") or {}
        ),
        # **키 이름만으로는 부족했다.** 목표 습도가 어디에 실려 오는지 찾으려면
        # 값을 봐야 한다. 그래서 **숫자만** 담는다 — 문자열은 통째로 뺀다.
        # 별칭(`zoneNickname`)·식별자·SSID 는 모두 문자열이라 이 그물에 걸리지
        # 않는다. 참·거짓과 정수·소수만 나간다.
        "reported_room_controller_numbers": _numbers_only(
            (device.reported or {}).get("roomController")
        ),
        # `additionalData` 는 자리마다 뜻이 다른 목록이다 (모드 안에서는 습도 40~65,
        # 컨트롤러 수준에서는 0~4). 어느 쪽이 오는지 봐야 한다.
        "reported_additional_data": [
            _numbers_only(item)
            for item in (
                ((device.reported or {}).get("roomController") or {}).get(
                    "additionalData"
                )
                or []
            )
            if isinstance(item, dict)
        ],
        "last_humidity_remembered": device.last_humidity,
        # **순서를 보기 위한 기록.** 「모드를 바꿀 때 습도를 실어 보냈는데 기기가
        # 되돌리는가」는 그 순간의 값만으로 가릴 수 없다.
        # `at` 은 절대 시각이 아니라 간격을 보기 위한 눈금이다. 개인정보는 없다.
        "command_log": list(device.command_log),
        "humidity_log": list(device.humidity_log),
        "available": device.available,
        "zone_id": device.zone_id,
        "running": device.running,
        "running_name": device.running_name,
        "mode": device.mode,
        "option": device.option,
        "mode_label": device.mode_label,
        "air_volume": device.air_volume,
        "wind_label": device.wind_label,
        "target_humidity": device.target_humidity,
        "error_code": device.error_code,
        "filters": list(device.filters),
        "air_sensor_kinds": list(device.sensor_kinds),
        # 단위를 앱에서 뽑지 못해 판단으로 정한 항목. 틀렸다는 제보가 오면 고친다.
        "air_sensor_units": {
            kind: {
                "unit": AIRONE_SENSOR_KINDS[kind][1],
                "inferred": kind in AIRONE_INFERRED_UNITS,
                "value": (device.air_sensors.get(kind) or {}).get("value"),
                "level": (device.air_sensors.get(kind) or {}).get("level"),
            }
            for kind in device.sensor_kinds
        },
        # **직접 만든 표에도 가림을 적용한다.** `supported_devices` 는
        # `async_redact_data` 를 지나지만 이 표는 우리가 조립하므로 그냥 두면
        # 에어모니터 `deviceId` 가 그대로 나간다 — 실제로 나갔다.
        "air_monitors": [
            async_redact_data(monitor, TO_REDACT) for monitor in device.air_monitors
        ],
        "modes_from_server": [
            {
                "mode": mode.mode,
                "option": mode.option,
                "air_volume": mode.air_volume,
                "configurable": mode.configurable,
                "humidity_min": mode.humidity_min,
                "humidity_max": mode.humidity_max,
            }
            for mode in device.modes
        ],
        "selectable_modes": [
            {"mode": m.mode, "option": m.option, "label": m.label}
            for m in device.selectable_modes
        ],
        "fan_choices": {
            f"{m.mode}:{m.option}": [
                {"option": c.option, "air_volume": c.air_volume, "label": c.label}
                for c in device.fan_choices(m.mode, m.option)
            ]
            for m in device.selectable_modes
        },
        "current_fan_label": device.current_fan_label(),
    }
