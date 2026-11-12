"""환기청정기 팬 엔티티 (v0.27.0, #32).

전원 · 풍량을 팬 하나로 묶는다. 미풍 · 약풍 · 강풍은 속도(퍼센트), 자동 · 터보 ·
절전 · 기저는 프리셋이다. 운전 모드는 담지 않는다 — 모드마다 고를 수 있는 풍량이
달라서다. 명령은 전원 스위치 · 풍량 선택이 쓰던 것을 그대로 쓴다.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report, source

from navien_smarthome.airone import AironeDevice  # noqa: E402
from navien_smarthome.fan import AironeFan  # noqa: E402

r = Report()

MODES = [
    {"name": 4, "option": 1, "airVolume": 1, "supportedAirVolumes": [1, 2, 3, 4], "configurable": True},
    {"name": 4, "option": 2},  # 환기 터보
    {"name": 4, "option": 3},  # 환기 절전
    {"name": 6, "option": 1, "airVolume": 3},  # 요리 — 강풍 하나
]


def make(running=1, mode=4, option=1, air_volume=2):
    device = AironeDevice.parse({
        "deviceSeq": 1, "serviceCode": 300, "deviceId": "AABB", "modelCode": "1901", "connected": 1,
        "Properties": {"nickName": "환기", "data": {"did": {"reported": {
            "odu": {"filter": []}, "airMonitor": [],
            "roomController": {"mode": MODES, "zoneId": 1, "sensor": []},
        }}}},
    })
    device.apply_reported({"roomController": {"running": running, "mode": mode,
                                              "option": option, "airVolume": air_volume}})
    return device


calls = []


async def power(device, turn_on):
    calls.append(("power", turn_on))


async def mode(device, m, option, air_volume=None, humidity=None):
    calls.append(("mode", m, option, air_volume))


def fan_for(device):
    coord = SimpleNamespace(airone={device.device_id: device}, async_airone_power=power,
                            async_airone_mode=mode)
    fan = AironeFan(coord, device)
    fan.coordinator = coord
    return fan


r.section("환기 · 약풍")
fan = fan_for(make())
r.ok(fan.is_on is True, "켜져 있다")
r.ok([c.label for c in fan._speeds] == ["미풍", "약풍", "강풍"], f"속도 단: {[c.label for c in fan._speeds]}")
r.ok(fan.speed_count == 3 and fan.percentage == 67, f"약풍 = 3단 중 2 → {fan.percentage}%")
r.ok(fan.preset_modes == ["자동", "터보", "절전"], f"프리셋: {fan.preset_modes}")
r.ok(fan.preset_mode is None, "단으로 돌고 있으면 프리셋은 없음")
r.ok(fan.supported_features == 1 | 8 | 16 | 32, "켜기 · 끄기 · 속도 · 프리셋")

r.section("명령은 풍량 선택 · 전원 스위치와 같다")
calls.clear()
asyncio.run(fan.async_set_percentage(100))
r.ok(calls[-1] == ("mode", 4, 1, 3), f"100% → 강풍: {calls[-1]}")
asyncio.run(fan.async_set_percentage(10))
r.ok(calls[-1] == ("mode", 4, 1, 1), f"10% → 미풍: {calls[-1]}")
asyncio.run(fan.async_set_preset_mode("터보"))
r.ok(calls[-1] == ("mode", 4, 2, None), f"터보 → option 2: {calls[-1]}")
asyncio.run(fan.async_set_preset_mode("자동"))
r.ok(calls[-1] == ("mode", 4, 1, 4), f"자동 → airVolume 4: {calls[-1]}")
n = len(calls)
asyncio.run(fan.async_set_preset_mode("없는 것"))
r.ok(len(calls) == n, "모르는 프리셋은 보내지 않는다")
asyncio.run(fan.async_turn_off())
r.ok(calls[-1] == ("power", False), "끄기 → 전원 끔")
asyncio.run(fan.async_set_percentage(0))
r.ok(calls[-1] == ("power", False), "0% → 전원 끔 (HA 규칙)")

r.section("터보 · 자동으로 돌 때")
turbo = fan_for(make(option=2, air_volume=None))
r.ok(turbo.preset_mode == "터보" and turbo.percentage is None, "터보면 프리셋 터보, 퍼센트 없음")
auto = fan_for(make(air_volume=4))
r.ok(auto.preset_mode == "자동" and auto.percentage is None, "자동이면 프리셋 자동")

r.section("꺼져 있을 때")
off = fan_for(make(running=2))
r.ok(off.is_on is False and off.percentage == 0, "꺼짐 → 0%")
calls.clear()
asyncio.run(off.async_turn_on(percentage=100))
r.ok(calls == [("power", True), ("mode", 4, 1, 3)], f"켜면서 강풍: {calls}")
calls.clear()
asyncio.run(off.async_turn_on())
r.ok(calls == [("power", True)], "그냥 켜기는 전원만")

r.section("요리 — 강풍 하나뿐인 모드")
cook = fan_for(make(mode=6, air_volume=3))
r.ok(cook.speed_count == 1 and cook.percentage == 100, "단은 하나, 100%")
r.ok(cook.preset_modes is None and cook.supported_features == 1 | 16 | 32,
     "프리셋 없음 — 지금 모드에서 못 고르는 것을 섞지 않는다")

r.section("기존 엔티티는 남기되 새 설치에는 숨긴다")
SWITCH, SELECT = source("switch.py"), source("select.py")
r.ok(SWITCH.count("_attr_entity_registry_enabled_default = False") == 1, "전원 스위치는 새 설치에서 꺼짐")
r.ok(SELECT.count("_attr_entity_registry_enabled_default = False") == 1, "풍량 선택만 꺼짐 — 운전 모드는 그대로")
r.ok('f"{device.device_id}_power"' in SWITCH and 'f"{device.device_id}_fan"' in SELECT,
     "unique_id 를 안 바꿨다 — 쓰던 사람의 엔티티가 그대로 남는다")
r.ok("Platform.FAN" in source("__init__.py"), "팬 플랫폼을 등록했다")

sys.exit(r.finish())
