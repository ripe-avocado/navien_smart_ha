"""v0.28.0 — 각방 Hi(NRM-65D) 상태 센서 (#34), 온수를 단으로 맞추는 보일러 (#35).

값 모양은 제보 파일 그대로, 식별자는 가상 값이다.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report

from navien_smarthome.boiler import BoilerDevice  # noqa: E402
from navien_smarthome.const import BOILER_CMD_HOTWATER  # noqa: E402
from navien_smarthome.select import BoilerHotWaterLevelSelect  # noqa: E402
from navien_smarthome.sensor import boiler_sensors  # noqa: E402

r = Report()


def raw(device_id, code, name):
    return {"deviceSeq": 1, "serviceCode": 100, "modelCode": code, "modelName": name,
            "deviceId": device_id, "connected": 1}


r.section("각방 Hi — 방은 status.rooms 에, 값은 ℃×2")

hi = BoilerDevice(raw("A1B2C3D4E5F60120", "120", "NRM-65D"))
first = hi.apply_response({"feature": {"roomCount": 2, "insideUse": 2, "hotWaterTemperatureSettingUse": 2,
                                       "hotWaterTemperatureMin": 60, "hotWaterTemperatureMax": 120}})
hi.apply_response({"status": {"errorCode": 0, "hotWaterTemperature": 53, "hotWaterTemperatureSetting": 90,
                              "rooms": [
                                  {"ordering": 1, "operationMode": 5, "insideTemperature": 45,
                                   "insideTemperatureSetting": 40, "actualInsideTemperature": 227},
                                  {"ordering": 2, "operationMode": 1, "insideTemperature": 44,
                                   "insideTemperatureSetting": 36},
                              ]}})
r.ok(first and hi.is_hi and hi.is_multi_room, "Hi 는 각방으로 다룬다")
r.ok(hi.room_value(1, "insideTemperatureSetting") == 20.0, "방 1 설정 40 → 20℃")
r.ok(hi.room_value(1, "insideTemperature") == 22.5, "방 1 실내 45 → 22.5℃ (실측 227 = 22.7℃ 와 맞다)")
r.ok(hi.value("hotWaterTemperatureSetting") == 45.0, "온수 설정 90 → 45℃")
r.ok(hi.temperature_range("hotWater") == (30.0, 60.0), "온수 범위 60~120 → 30~60℃")
r.ok(hi.room_count == 2, "방 개수는 기능 목록에서")

names = [e._attr_name for e in boiler_sensors(None, hi)]
r.ok(names == ["오류 코드", "거실", "거실 온도", "거실 설정 온도", "방1", "방1 온도", "방1 설정 온도",
               "온수 설정 온도", "온수 온도"], f"센서 — 이름은 앱처럼 거실 · 방1 … (v0.28.1): {names}")
modes = [e for e in boiler_sensors(None, hi) if e._attr_name in ("거실", "방1")]
for e in modes:
    e.coordinator = SimpleNamespace(boilers={hi.device_id: hi})
r.ok([e.native_value for e in modes] == ["실내난방", "꺼짐"], "방 모드: 5 실내난방 · 1 꺼짐")
r.section("외출인 방 (#34) · 안방 이름")

away = BoilerDevice(raw("A1B2C3D4E5F60121", "120", "NRM-65D"))
away.apply_response({"feature": {"roomCount": 2, "mainRoomconLocation": 2}})
away.apply_response({"status": {"rooms": [{"ordering": 1, "operationMode": 5, "insideTemperatureSetting": 40},
                                          {"ordering": 2, "operationMode": 4, "insideTemperatureSetting": 38}]}})
built = {e._attr_name: e for e in boiler_sensors(None, away)}
for e in built.values():
    e.coordinator = SimpleNamespace(boilers={away.device_id: away})
r.ok("안방" in built and "거실" not in built, "주 조절기가 안방(mainRoomconLocation 2)이면 1번은 「안방」")
r.ok(built["방1"].native_value == "외출", "4 는 외출")
r.ok(built["방1 설정 온도"].native_value is None, "외출인 방은 설정 온도를 비운다 (앱: 「외출」만)")
r.ok(built["안방 설정 온도"].native_value == 20.0, "실내난방인 방은 그대로 20℃")
r.ok(built["방1"]._attr_unique_id.endswith("_room2_mode"), "unique_id 는 그대로 — 기존 엔티티가 이어진다")

hot = next(e for e in boiler_sensors(None, hi) if e._attr_name == "온수 설정 온도")
hot.coordinator = SimpleNamespace(boilers={hi.device_id: hi})
r.ok(hot.native_value == 45.0, "온수 설정 온도 센서 45℃")

r.section("NR-40D — 온수가 단(Use 4)")

nr40 = BoilerDevice(raw("A1B2C3D4E5F60003", "003", "NR-40D"))
nr40.apply_response({"feature": {"insideUse": 3, "hotWaterTemperatureSettingUse": 4,
                                 "hotWaterTemperatureMin": 1, "hotWaterTemperatureMax": 3}})
nr40.apply_response({"status": {"operationMode": 1, "hotWaterTemperatureSetting": 3}})
r.ok(nr40.is_level("hotWaterTemperatureSetting") and nr40.level_range("hotWater") == (1, 3),
     "Use 4 · 범위 1~3")
sensors = {e._attr_name: e for e in boiler_sensors(None, nr40)}
r.ok("온수 단계" in sensors and "온수 설정 온도" not in sensors, f"℃ 센서 대신 단계 센서: {list(sensors)}")
level_sensor = sensors["온수 단계"]
level_sensor.coordinator = SimpleNamespace(boilers={nr40.device_id: nr40})
r.ok(level_sensor.native_value == 3 and getattr(level_sensor, "_attr_native_unit_of_measurement", None) is None,
     "값 3, 단위 없음")
r.ok(level_sensor._attr_unique_id.endswith("_level"), "온도 센서와 다른 unique_id — 기록이 안 섞인다")

calls = []


async def control(boiler, command, param, room=None):
    calls.append((command, param))

coord = SimpleNamespace(boilers={nr40.device_id: nr40}, async_boiler_control=control)
select = BoilerHotWaterLevelSelect(coord, nr40)
select.coordinator = coord
r.ok(select._attr_options == ["1단", "2단", "3단"] and select.current_option == "3단", "선택지 1~3단, 지금 3단")
asyncio.run(select.async_select_option("2단"))
r.ok(calls[-1] == (BOILER_CMD_HOTWATER, [2]), f"2단 → hotwater-temperature [2] (정수): {calls[-1]}")
n = len(calls)
asyncio.run(select.async_select_option("9단"))
r.ok(len(calls) == n, "없는 단은 보내지 않는다")

r.section("만드는 조건")

from navien_smarthome import select as select_mod, water_heater as wh_mod  # noqa: E402
import inspect  # noqa: E402
r.ok("not b.is_level(\"hotWaterTemperatureSetting\")" in inspect.getsource(wh_mod),
     "단 보일러에는 온수기(℃ 다이얼)를 안 만든다")
r.ok("not b.is_mgpp" in inspect.getsource(select_mod.async_setup_entry),
     "NR-6x 의 단은 확인 전이라 선택을 안 만든다")

sys.exit(r.finish())
