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
