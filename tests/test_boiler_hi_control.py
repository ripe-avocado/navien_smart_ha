"""v0.29.0 — 각방 Hi(NRM-65D) 방별 제어 (실험, #34).

앱 `SmartTokControlFragment`: 전원 · 실내난방 · 외출 명령에 `roomUseSetting` 으로 방을
고른다 (1번 방 `10000000`). 봉투는 NR-6x 와 같고 deviceType 120, 온도는 ℃×2 소수.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from types import SimpleNamespace

from harness import Report

import navien_smarthome.climate as climate_mod  # noqa: E402
from navien_smarthome.api import NavienSmartApi  # noqa: E402
from navien_smarthome.boiler import BoilerDevice  # noqa: E402
from navien_smarthome.climate import BoilerRoomThermostat  # noqa: E402
from navien_smarthome.const import (  # noqa: E402
    BOILER_CMD_GOOUT,
    BOILER_CMD_HOTWATER,
    BOILER_CMD_INSIDE,
    BOILER_CMD_POWER_OFF,
    BOILER_CMD_POWER_ON,
    CONF_BOILER_CONTROL,
    CONF_BOILER_CONTROL_EXPERIMENTAL,
)
from navien_smarthome.coordinator import NavienSmartCoordinator  # noqa: E402
from navien_smarthome.water_heater import BoilerWaterHeater  # noqa: E402

r = Report()

RAW = {"deviceSeq": 9, "serviceCode": 100, "modelCode": "120", "modelName": "NRM-65D",
       "deviceId": "A1B2C3D4E5F60120", "mqttTopicKey": "topickey-hi", "connected": 1}

r.section("방 고르기 값")
r.ok(BoilerDevice.room_bits(1) == "10000000" and BoilerDevice.room_bits(3) == "00100000",
     "1번 방 10000000, 3번 방 00100000 (앱 hiPosition)")

r.section("봉투 — NR-6x 와 같고 roomUseSetting 을 싣는다")
api = NavienSmartApi.__new__(NavienSmartApi)
api._require_session = lambda: SimpleNamespace(user_seq=9)


async def fake_request(*a, **k):
    return None

api._async_authed_request = fake_request
body = asyncio.run(api.async_boiler_request(1, RAW, "client", ("control", "res", 33554437),
                                            mode="inside-heat", param=[44.0], room="01000000"))
inner = body["payload"]["request"]
r.ok(body["payload"].get("protocolVersion") == 1 and inner["deviceType"] == 120 and inner["command"] == 33554437,
     "protocolVersion · command · deviceType 120")
r.ok(inner["roomUseSetting"] == "01000000" and inner["param"] == [44.0] and inner["mode"] == "inside-heat",
     "방 값 · 온도(×2 소수) · 모드")
plain = asyncio.run(api.async_boiler_request(1, {**RAW, "modelCode": "20"}, "client", ("control", "res", 1),
                                             mode="power-on"))
r.ok("roomUseSetting" not in plain["payload"]["request"], "방을 안 주면 싣지 않는다 (NR-6x 그대로)")

r.section("방 하나의 온도조절기")
hi = BoilerDevice(RAW)
hi.apply_response({"feature": {"roomCount": 2, "powerUse": 2, "gooutUse": 2, "insideUse": 2,
                               "insideTemperatureMin": 20, "insideTemperatureMax": 80,
                               "hotWaterTemperatureSettingUse": 2, "hotWaterTemperatureMin": 60,
                               "hotWaterTemperatureMax": 120}})
hi.apply_response({"status": {"hotWaterTemperatureSetting": 90, "rooms": [
    {"ordering": 1, "operationMode": 5, "insideTemperature": 45, "insideTemperatureSetting": 40},
    {"ordering": 2, "operationMode": 4, "insideTemperature": 41, "insideTemperatureSetting": 38},
]}})

calls = []


async def control(boiler, command, param, room=None):
    calls.append((command, param, room))

coord = SimpleNamespace(boilers={hi.device_id: hi}, async_boiler_control=control)


def room(n):
    t = BoilerRoomThermostat(coord, hi, n)
    t.coordinator = coord
    return t

living, room1 = room(1), room(2)
r.ok(living._attr_name == "거실 난방" and room1._attr_name == "방1 난방", "이름은 앱 순서 이름")
r.ok((living._attr_min_temp, living._attr_max_temp, living._attr_target_temperature_step) == (10.0, 40.0, 0.5),
     "범위 10~40℃ · 0.5℃ 칸")
r.ok(living.current_temperature == 22.5 and living.target_temperature == 20.0, "거실 22.5℃ · 설정 20℃")
r.ok(living.hvac_mode == "heat" and living.preset_mode == "실내난방" and living.hvac_action == "heating", "실내난방")
r.ok(room1.preset_mode == "외출" and room1.target_temperature is None and room1.hvac_action == "idle",
     "외출인 방은 설정 온도 비움 · 유휴")

asyncio.run(living.async_set_temperature(temperature=21.5))
r.ok(calls[-1] == (BOILER_CMD_INSIDE, [43.0], "10000000"), f"거실 21.5℃ → inside-heat [43.0] 10000000: {calls[-1]}")
asyncio.run(room1.async_set_preset_mode("외출"))
r.ok(calls[-1] == (BOILER_CMD_GOOUT, [], "01000000"), f"방1 외출 → goout-on [] 01000000: {calls[-1]}")
asyncio.run(room1.async_set_preset_mode("실내난방"))
r.ok(calls[-1] == (BOILER_CMD_INSIDE, [38.0], "01000000"), f"방1 실내난방 → 지금 설정 19℃ 그대로 [38.0]: {calls[-1]}")
asyncio.run(living.async_set_hvac_mode("off"))
r.ok(calls[-1] == (BOILER_CMD_POWER_OFF, [], "10000000"), "거실 끄기 → power-off [] 10000000")
asyncio.run(living.async_set_hvac_mode("heat"))
r.ok(calls[-1] == (BOILER_CMD_POWER_ON, [], "10000000"), "거실 켜기 → power-on [] 10000000")
asyncio.run(living.async_set_temperature(hvac_mode="off", temperature=20))
r.ok(calls[-1] == (BOILER_CMD_POWER_OFF, [], "10000000"), "hvac_mode off 를 같이 주면 끈다")
r.ok(living._attr_unique_id.endswith("_room1_thermostat"), "unique_id 는 방 순서로")

r.section("온수 — 각방 Hi 는 1번 방 값을 싣는다")
heater = BoilerWaterHeater(coord, hi)
heater.coordinator = coord
asyncio.run(heater.async_set_temperature(temperature=46))
r.ok(calls[-1] == (BOILER_CMD_HOTWATER, [92.0], "10000000"), f"온수 46℃ → [92.0] 10000000 (앱과 같다): {calls[-1]}")

r.section("실험 설정을 켜야 열린다")


def cc(control_on, exp):
    c = SimpleNamespace(config_entry=SimpleNamespace(options={CONF_BOILER_CONTROL: control_on,
                                                              CONF_BOILER_CONTROL_EXPERIMENTAL: exp}))
    c.boiler_control_enabled = NavienSmartCoordinator.boiler_control_enabled.fget(c)
    c.boiler_experimental_enabled = NavienSmartCoordinator.boiler_experimental_enabled.fget(c)
    return c

r.ok(not NavienSmartCoordinator.boiler_controllable(cc(True, False), hi), "기본 제어만으로는 안 열린다")
r.ok(NavienSmartCoordinator.boiler_controllable(cc(False, True), hi), "실험 설정을 켜면 열린다")
r.ok("BoilerRoomThermostat(coordinator, b, room)" in inspect.getsource(climate_mod.async_setup_entry)
     and "if b.is_hi" in inspect.getsource(climate_mod.async_setup_entry),
     "각방 Hi 는 보일러 전체 대신 방마다 온도조절기")

sys.exit(r.finish())
