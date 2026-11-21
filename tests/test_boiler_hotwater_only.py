"""v0.28.2 — 보일러 별칭 묶음 (#34), NR-40D 온수전용 프리셋 (#35)."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report

from navien_smarthome.boiler import BoilerDevice  # noqa: E402
from navien_smarthome.climate import BoilerThermostat  # noqa: E402
from navien_smarthome.const import BOILER_CMD_HOTWATER_ONLY  # noqa: E402

r = Report()


def raw(device_id, code, name, nick=None):
    d = {"deviceSeq": 1, "serviceCode": 100, "modelCode": code, "modelName": name,
         "deviceId": device_id, "connected": 1}
    if nick is not None:
        d["Properties"] = {"nickName": nick}
    return d


r.section("별칭이 묶음으로 온다 (#34)")
r.ok(BoilerDevice(raw("A1B2C3D4E5F60001", "120", "NRM-65D", {"mainItem": "나비엔보일러"})).nickname == "나비엔보일러",
     "{'mainItem': …} → 「나비엔보일러」")
r.ok(BoilerDevice(raw("A1B2C3D4E5F60002", "003", "NR-40D", "거실 보일러")).nickname == "거실 보일러", "글자면 그대로")
r.ok(BoilerDevice(raw("A1B2C3D4E5F60003", "003", "NR-40D", {"mainItem": ""})).nickname == "NR-40D",
     "비어 있으면 모델명")
r.ok(BoilerDevice(raw("A1B2C3D4E5F60004", "003", "NR-40D")).nickname == "NR-40D", "없으면 모델명")

calls = []


async def control(boiler, command, param, room=None):
    calls.append((command, param))


def thermostat(boiler):
    coord = SimpleNamespace(boilers={boiler.device_id: boiler}, async_boiler_control=control)
    t = BoilerThermostat(coord, boiler)
    t.coordinator = coord
    return t


r.section("NR-40D 온수전용 (#35)")
nr40 = BoilerDevice(raw("A1B2C3D4E5F60003", "003", "NR-40D"))
nr40.apply_response({"feature": {"powerUse": 2, "gooutUse": 2, "insideUse": 3, "ondolUse": 3, "hotWaterUse": 2,
                                 "hotWaterTemperatureSettingUse": 4, "hotWaterTemperatureMin": 1,
                                 "hotWaterTemperatureMax": 3, "insideTemperatureMin": 10,
                                 "insideTemperatureMax": 40, "ondolTemperatureMin": 40, "ondolTemperatureMax": 83}})
nr40.apply_response({"status": {"operationMode": 5, "hotWaterTemperatureSetting": 3, "insideTemperatureSetting": 18}})
t = thermostat(nr40)
r.ok(t._presets == ["실내난방", "온돌난방", "외출", "온수전용"], f"프리셋에 온수전용: {t._presets}")
asyncio.run(t.async_set_preset_mode("온수전용"))
r.ok(calls[-1] == (BOILER_CMD_HOTWATER_ONLY, [3]) and isinstance(calls[-1][1][0], int),
     f"only-hotwater 에 지금 온수 값 3 (정수, 앱과 같다): {calls[-1]}")
nr40.apply_response({"status": {"operationMode": 10}})
r.ok(t.preset_mode == "온수전용", "운전 모드 10 이면 프리셋 「온수전용」")
r.ok(t.hvac_action == "idle", "온수전용은 난방 중이 아니다 — 유휴")

half = BoilerDevice(raw("A1B2C3D4E5F60005", "002", "NR-35D"))
half.apply_response({"feature": {"hotWaterUse": 2, "hotWaterTemperatureSettingUse": 2}})
half.apply_response({"status": {"hotWaterTemperatureSetting": 45.5}})
asyncio.run(thermostat(half).async_set_preset_mode("온수전용"))
r.ok(calls[-1] == (BOILER_CMD_HOTWATER_ONLY, [45.5]), f"Use 2 면 소수 그대로: {calls[-1]}")

r.section("NR-6x 에는 열지 않는다")
nr6 = BoilerDevice(raw("A1B2C3D4E5F60020", "20", "NR-67D"))
nr6.apply_response({"feature": {"ondolUse": 2, "gooutUse": 2, "hotWaterUse": 2,
                                "ondolTemperatureMin": 60, "ondolTemperatureMax": 130}})
r.ok("온수전용" not in thermostat(nr6)._presets, "NR-6x 는 운전 모드 4 가 외출 · 온수전용을 함께 뜻해 열지 않는다")

nohot = BoilerDevice(raw("A1B2C3D4E5F60006", "003", "NR-40D"))
nohot.apply_response({"feature": {"insideUse": 3, "hotWaterUse": 1}})
r.ok("온수전용" not in thermostat(nohot)._presets, "온수 미지원(hotWaterUse 1)이면 없음")

sys.exit(r.finish())
