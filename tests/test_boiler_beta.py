"""보일러 베타 (v0.22.0) — 상태 해석 · 센서 · 실험 제어.

값은 실기기 제보와 **같은 모양**이다 (#20 #26 NR-6x · #24 NR-40D · #28 NRM-35D).
식별자는 모두 가상 값이다 — 제보 파일의 MAC·deviceId 를 옮겨 적지 않는다.

지켜야 할 것.
- NR-6x 는 ℃×2 로 읽고 ×2 를 소수로 보낸다 (40℃ ↔ 80 / 80.0)
- NR-40D · NRM-35D 는 ℃ 그대로
- 일부만 온 상태로 갈아끼우지 않는다 (합친다)
- 제어는 설정을 켠 NR-6x 만. 꺼져 있으면 명령이 나갈 길이 없다
- 꺼진 보일러에 온도를 바꿔 몰래 켜지 않는다
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report

import navien_smarthome.coordinator as coord_mod  # noqa: E402
from navien_smarthome.api import NavienSmartApi  # noqa: E402
from navien_smarthome.boiler import BoilerDevice  # noqa: E402
from navien_smarthome.binary_sensor import BoilerHotWaterInUse  # noqa: E402
from navien_smarthome.climate import BoilerThermostat  # noqa: E402
from navien_smarthome.const import (  # noqa: E402
    BOILER_CMD_ONDOL,
    BOILER_CMD_POWER_OFF,
    CONF_BOILER_CONTROL,
)
from navien_smarthome.coordinator import NavienSmartCoordinator  # noqa: E402
from navien_smarthome.diagnostics import _boilers_view, _scrub  # noqa: E402
from navien_smarthome.sensor import boiler_sensors  # noqa: E402
from navien_smarthome.water_heater import BoilerWaterHeater  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

r = Report()

ID_20 = "A1B2C3D4E5F60020"
ID_40 = "A1B2C3D4E5F60003"
ID_35 = "A1B2C3D4E5F60011"


def raw(device_id: str, code: str, name: str) -> dict:
    return {"deviceSeq": 1, "serviceCode": 100, "deviceId": device_id, "modelCode": code,
            "modelName": name, "mqttTopicKey": "topickey-test", "connected": 1,
            "Properties": {"nickName": "우리집 보일러"}}


# #20 NR-67D — 온돌 전용 (insideUse 1)
FEATURE_20 = {"powerUse": 2, "gooutUse": 1, "insideUse": 1, "insideTemperatureMin": 20,
              "insideTemperatureMax": 80, "ondolUse": 2, "ondolTemperatureMin": 60,
              "ondolTemperatureMax": 130, "hotWaterTemperatureSettingUse": 2,
              "hotWaterTemperatureMin": 60, "hotWaterTemperatureMax": 120,
              "insideHumidityUse": 2, "boilerControllerSerialNumber": "SERIAL-TEST"}
STATUS_20 = {"operationMode": 6, "insideTemperature": 45, "insideTemperatureSetting": 50,
             "ondolTemperatureSetting": 80, "hotWaterTemperature": 71,
             "hotWaterTemperatureSetting": 82, "errorCode": 0, "operationBusy": 2,
             "supplyTemperature": 93, "returnTemperature": 78, "insideHumidity": 355,
             "outsideTemperature": 150, "DHWUse": 1}
# #24 NR-40D
FEATURE_40 = {"powerUse": 2, "gooutUse": 2, "insideUse": 3, "insideTemperatureMin": 10,
              "insideTemperatureMax": 40, "ondolUse": 3, "ondolTemperatureMin": 40,
              "ondolTemperatureMax": 83, "hotWaterTemperatureSettingUse": 3,
              "hotWaterTemperatureMin": 30, "hotWaterTemperatureMax": 60}
STATUS_40 = {"operationMode": 5, "insideTemperature": 25, "insideTemperatureSetting": 24,
             "ondolTemperatureSetting": 45, "hotWaterTemperatureSetting": 42, "errorCode": 0}
# #28 NRM-35D 각방
FEATURE_35 = {"insideUse": 2, "insideTemperatureMin": 10, "insideTemperatureMax": 40,
              "ondolUse": 1, "hotWaterTemperatureSettingUse": 3,
              "hotWaterTemperatureMin": 30, "hotWaterTemperatureMax": 60, "roomCount": 2}
ROOMS_35 = [
    {"ordering": 1, "operationMode": 10, "insideTemperature": 27,
     "insideTemperatureSetting": 24.5, "hotWaterTemperatureSetting": 50},
    {"ordering": 2, "operationMode": 1, "insideTemperature": 26.5,
     "insideTemperatureSetting": 25, "hotWaterTemperatureSetting": 50},
]


def ready(device_id, code, name, feature, status) -> BoilerDevice:
    boiler = BoilerDevice(raw(device_id, code, name))
    boiler.apply_response({"feature": dict(feature)})
    boiler.apply_response({"status": status if isinstance(status, list) else dict(status)})
    return boiler


r.section("값 체계")

b20 = ready(ID_20, "20", "NR-67D", FEATURE_20, STATUS_20)
b40 = ready(ID_40, "003", "NR-40D", FEATURE_40, STATUS_40)
b35 = ready(ID_35, "011", "NRM-35D", FEATURE_35, ROOMS_35)
r.ok(b20.value("ondolTemperatureSetting") == 40.0, "NR-6x 80 → 40℃")
r.ok(b20.value("insideTemperature") == 22.5, "NR-6x 실내 45 → 22.5℃ (actualInsideTemperature 225 와 같다)")
r.ok(b20.encode(40) == 80.0 and isinstance(b20.encode(40), float), "NR-6x 40℃ → 80.0 (소수)")
r.ok(b20.encode(22.5) == 45.0, "NR-6x 22.5℃ → 45.0")
r.ok(b40.value("insideTemperatureSetting") == 24.0, "NR-40D 는 ℃ 그대로")
r.ok(b35.room_value(1, "insideTemperatureSetting") == 24.5, "NRM-35D 방 1 설정 24.5℃")
r.ok(b20.temperature_range("ondol") == (30.0, 65.0), f"NR-6x 온돌 범위 {b20.temperature_range('ondol')}")
r.ok(b40.temperature_range("hotWater") == (30.0, 60.0), "NR-40D 온수 범위")

r.section("기능")

r.ok(not b20.supports("inside") and b20.supports("ondol"), "Use 1 = 미지원, 2 = 지원")
r.ok(b40.supports("inside"), "NR-40D 의 3 도 지원으로 본다")
r.ok(not BoilerDevice(raw(ID_20, "20", "x")).supports("ondol"), "기능 목록 전에는 아무것도 지원하지 않는다")
r.ok(b35.room_count == 2, "방 개수는 roomCount")

r.section("받기 — 합친다")

b = BoilerDevice(raw(ID_20, "20", "NR-67D"))
r.ok(b.apply_response({"feature": dict(FEATURE_20)}) is True, "기능 목록이 처음 오면 True")
r.ok(b.apply_response({"feature": {"powerUse": 2}}) is False, "두 번째부터는 False")
b.apply_response({"status": dict(STATUS_20)})
b.apply_response({"status": {"ondolTemperatureSetting": 82}})
r.ok(b.status["ondolTemperatureSetting"] == 82 and b.status["hotWaterTemperatureSetting"] == 82
     and b.status["operationMode"] == 6, "일부만 온 상태가 나머지를 지우지 않는다")
b35.apply_response({"status": [{"ordering": 2, "operationMode": 5}]})
r.ok(b35.rooms[2]["operationMode"] == 5 and b35.rooms[2]["insideTemperature"] == 26.5,
     "방도 방끼리 합친다")
b35.apply_response({"status": [{"ordering": 2, "operationMode": 1}]})

r.section("코디네이터 — 메시지 반영")

sent_signals: list = []
pushed: list = []
coord = SimpleNamespace(
    boilers={ID_20: BoilerDevice(raw(ID_20, "20", "NR-67D"))},
    hass=None, config_entry=SimpleNamespace(entry_id="E1", options={}),
    boiler_stats={"polls": 0, "control_fail": 0}, data={},
    _async_push_update=lambda data: pushed.append(1),
)
original_send = coord_mod.async_dispatcher_send
coord_mod.async_dispatcher_send = lambda hass, signal, device_id: sent_signals.append((signal, device_id))
try:
    envelope = lambda response, topic="cmd/20/k/mobile-x/res": {"topic": topic, "payload": {"response": response}}
    NavienSmartCoordinator._apply_boiler_message(coord, ID_20, envelope({"feature": dict(FEATURE_20)}))
    NavienSmartCoordinator._apply_boiler_message(coord, ID_20, envelope({"status": dict(STATUS_20)}))
    NavienSmartCoordinator._apply_boiler_message(coord, ID_20, envelope({"feature": dict(FEATURE_20)}))
    NavienSmartCoordinator._apply_boiler_message(coord, ID_20, {"topic": "cmd/20/k/controlfail", "payload": {}})
    NavienSmartCoordinator._apply_boiler_message(coord, "OTHER", envelope({"status": {}}))
finally:
    coord_mod.async_dispatcher_send = original_send
r.ok(sent_signals == [("navien_smarthome_boiler_ready_E1", ID_20)], f"기능 목록이 처음 올 때 한 번만 알린다: {sent_signals}")
r.ok(coord.boilers[ID_20].value("ondolTemperatureSetting") == 40.0, "상태가 반영된다")
r.ok(coord.boiler_stats["control_fail"] == 1, "거절(controlfail)을 센다")
r.ok(len(pushed) == 3, f"응답이 든 메시지마다 엔티티에 알린다: {len(pushed)}")

r.section("코디네이터 — 조용할 때만 묻는다")

asked: list = []


class _Hass:
    def async_create_background_task(self, coro, name=None):
        asked.append(name)
        coro.close()


quiet = SimpleNamespace(
    _boiler_probed=True, mqtt_connected=True, hass=_Hass(), _boiler_asked_at={},
    boiler_stats={"polls": 0},
    boilers={ID_20: BoilerDevice(raw(ID_20, "20", "x")), ID_40: BoilerDevice(raw(ID_40, "003", "y"))},
)
quiet._async_ask_boiler = lambda boiler: asyncio.sleep(0)
now = coord_mod.time.monotonic()
quiet.boilers[ID_20].last_message_at = now - 60       # 1분 전에 받음
quiet.boilers[ID_40].last_message_at = now - 1000     # 16분 넘게 조용
NavienSmartCoordinator._ask_quiet_boilers(quiet)
r.ok(asked == ["navien_smart_boiler_status"], f"조용한 보일러에만 묻는다: {asked}")
NavienSmartCoordinator._ask_quiet_boilers(quiet)
r.ok(len(asked) == 1, "답이 없어도 다음 폴링에 또 묻지 않는다")
quiet.mqtt_connected = False
quiet._boiler_asked_at = {}
NavienSmartCoordinator._ask_quiet_boilers(quiet)
r.ok(len(asked) == 1, "MQTT 가 끊겨 있으면 묻지 않는다 (답을 받을 수 없다)")

r.section("제어 — 설정과 기종")

calls: list = []


class _Api:
    async def async_boiler_request(self, home_seq, device, client_id, request, mode=None, param=None):
        calls.append((request, mode, param))
        return {}


def control_coord(enabled: bool, boilers: dict) -> SimpleNamespace:
    c = SimpleNamespace(
        config_entry=SimpleNamespace(options={CONF_BOILER_CONTROL: enabled}),
        api=_Api(), home_seq=1, mqtt_client_id="client", boiler_commands=[], boilers=boilers,
    )
    c.boiler_control_enabled = NavienSmartCoordinator.boiler_control_enabled.fget(c)
    c.boiler_controllable = lambda boiler: NavienSmartCoordinator.boiler_controllable(c, boiler)
    c.async_boiler_control = lambda boiler, command, param: NavienSmartCoordinator.async_boiler_control(
        c, boiler, command, param
    )
    return c


def raises(coro) -> bool:
    try:
        asyncio.run(coro)
    except HomeAssistantError:
        return True
    return False


default = SimpleNamespace(config_entry=SimpleNamespace(options={}))
r.ok(NavienSmartCoordinator.boiler_control_enabled.fget(default) is True,
     "설정을 건드린 적 없으면 켜짐 (v0.24.0 부터 기본)")
off = control_coord(False, {ID_20: b20})
r.ok(not off.boiler_controllable(b20), "직접 끄면 제어 못 한다")
r.ok(raises(off.async_boiler_control(b20, BOILER_CMD_POWER_OFF, [])) and calls == [],
     "꺼져 있으면 명령이 나가지 않는다")
on = control_coord(True, {ID_20: b20, ID_40: b40, ID_35: b35})
r.ok(on.boiler_controllable(b20), "켜면 NR-6x 는 제어한다")
r.ok(not on.boiler_controllable(b40) and not on.boiler_controllable(b35),
     "NR-40D · 각방은 켜도 아직 제어하지 않는다")
asyncio.run(on.async_boiler_control(b20, BOILER_CMD_ONDOL, [80.0]))
r.ok(calls[-1] == (("control", "res", 33554438), "ondol-heat", [80.0]), f"보내는 것: {calls[-1]}")
r.ok(on.boiler_commands[-1]["mode"] == "ondol-heat", "보낸 명령을 진단에 남긴다")

r.section("봉투 — NR-6x 제어")

api = NavienSmartApi.__new__(NavienSmartApi)
api._require_session = lambda: SimpleNamespace(user_seq=9)


async def _fake(method, path, **kwargs):
    return {}


api._async_authed_request = _fake
body = asyncio.run(api.async_boiler_request(1, raw(ID_20, "20", "x"), "client",
                                            ("control", "res", 33554438), mode="ondol-heat", param=[80.0]))
req = body["payload"]["request"]
r.ok(list(req) == ["macAddress", "registerAt", "additionalValue", "mode", "param", "paramStr",
                   "command", "deviceType"], f"앱과 같은 순서: {list(req)}")
r.ok(req["mode"] == "ondol-heat" and req["param"] == [80.0] and req["command"] == 33554438
     and req["deviceType"] == 20 and req["paramStr"] == "", "모드 · 값 · 명령 · 기종")
r.ok(body["payload"]["requestTopic"].endswith("/control"), "요청 토픽 끝이 control")
read = asyncio.run(api.async_boiler_request(1, raw(ID_20, "20", "x"), "client", ("status", "res", 16777219)))
r.ok("mode" not in read["payload"]["request"], "읽기 요청에는 mode 가 없다 (전과 같다)")

r.section("온도조절기 — NR-6x")


def thermostat(boiler: BoilerDevice) -> BoilerThermostat:
    c = control_coord(True, {boiler.device_id: boiler})
    entity = BoilerThermostat(c, boiler)
    entity.coordinator = c
    return entity


t = thermostat(b20)
r.ok(t._presets == ["온돌난방"], f"지원하는 모드만 프리셋으로: {t._presets}")
r.ok(t.target_temperature == 40.0 and t.current_temperature == 22.5, "온돌 40℃ · 실내 22.5℃")
r.ok((t.min_temp, t.max_temp) == (30.0, 65.0) and t.target_temperature_step == 1.0, "온돌 범위·간격")
r.ok(t.preset_mode == "온돌난방" and t.hvac_mode == "heat", "지금 모드")
calls.clear()
asyncio.run(t.async_set_temperature(temperature=41))
r.ok(calls[-1][1:] == ("ondol-heat", [82.0]), f"온돌 41℃ → ondol-heat 82.0: {calls[-1]}")
asyncio.run(t.async_set_hvac_mode("off"))
r.ok(calls[-1][1:] == ("power-off", []), "끄기 → power-off")

b_inside = ready(ID_20, "20", "NR-6x", {**FEATURE_20, "insideUse": 2, "gooutUse": 2},
                 {**STATUS_20, "operationMode": 5})
t2 = thermostat(b_inside)
r.ok(t2._presets == ["실내난방", "온돌난방", "외출"], f"프리셋: {t2._presets}")
r.ok(t2.target_temperature == 25.0 and t2.target_temperature_step == 0.5, "실내 25℃ · 0.5칸")
calls.clear()
asyncio.run(t2.async_set_temperature(temperature=22.5))
r.ok(calls[-1][1:] == ("inside-heat", [45.0]), f"실내 22.5℃ → inside-heat 45.0: {calls[-1]}")
asyncio.run(t2.async_set_preset_mode("온돌난방"))
r.ok(calls[-1][1:] == ("ondol-heat", [80.0]), "온돌로 바꾸면 지금 온돌 설정값을 그대로 싣는다")
asyncio.run(t2.async_set_preset_mode("외출"))
r.ok(calls[-1][1:] == ("goout-on", []), "외출 → goout-on")

b_off = ready(ID_20, "20", "NR-6x", FEATURE_20, {**STATUS_20, "operationMode": 1})
t3 = thermostat(b_off)
calls.clear()
r.ok(t3.hvac_mode == "off", "operationMode 1 = 꺼짐")
r.ok(raises(t3.async_set_temperature(temperature=45)) and calls == [],
     "꺼져 있으면 온도를 바꿔 몰래 켜지 않는다")
asyncio.run(t3.async_set_temperature(temperature=45, hvac_mode="heat"))
r.ok([c[1] for c in calls] == ["power-on", "ondol-heat"], f"hvac_mode heat 를 주면 켜고 맞춘다: {calls}")

r.section("온수 — NR-6x (v0.23.0 부터 온수기 엔티티)")

c = control_coord(True, {ID_20: b20})
heater = BoilerWaterHeater(c, b20)
heater.coordinator = c
r.ok(heater.target_temperature == 41.0 and heater.current_temperature == 35.5, "설정 82 → 41℃ · 지금 71 → 35.5℃")
r.ok((heater._attr_min_temp, heater._attr_max_temp) == (30.0, 60.0), "범위 30~60℃")
calls.clear()
asyncio.run(heater.async_set_temperature(temperature=42))
r.ok(calls[-1][1:] == ("hotwater-temperature", [84.0]), f"42℃ → 84.0 (#20 실기기와 같은 값): {calls[-1]}")

r.section("센서")


def names(boiler: BoilerDevice) -> list[str]:
    return [e._attr_name for e in boiler_sensors(None, boiler)]


r.ok(names(b20) == ["운전 모드", "실내 온도", "오류 코드", "온돌 설정 온도", "온수 설정 온도", "온수 온도",
                    "운전 상태", "난방수 공급 온도", "난방수 환수 온도", "외기 온도 (기상 관측)", "실내 습도"],
     f"NR-6x (실내난방 없음): {names(b20)}")
r.ok(names(b40) == ["운전 모드", "실내 온도", "오류 코드", "실내 설정 온도", "온돌 설정 온도", "온수 설정 온도"],
     f"NR-40D (현재 온수 온도 없음): {names(b40)}")
r.ok(names(b35) == ["방 1", "방 1 온도", "방 1 설정 온도", "방 2", "방 2 온도", "방 2 설정 온도", "온수 설정 온도"],
     f"NRM-35D 방별: {names(b35)}")

sensors = boiler_sensors(None, b35)
holder = SimpleNamespace(boilers={ID_35: b35})
for sensor in sensors:
    sensor.coordinator = holder
r.ok(sensors[0].native_value == "켜짐" and sensors[3].native_value == "꺼짐", "방 모드는 1 만 꺼짐")
r.ok(sensors[2].native_value == 24.5, "방 1 설정 24.5℃")
mode_sensor = boiler_sensors(None, b20)[0]
mode_sensor.coordinator = SimpleNamespace(boilers={ID_20: b20})
r.ok(mode_sensor.native_value == "온돌난방", "운전 모드 6 = 온돌난방")
r.ok(BoilerDevice.mode_name(99) == "알 수 없음(99)", "모르는 모드는 숫자를 그대로 보인다")

r.section("연소 상태 · 추가 값 (v0.25.0, #29)")

by_name = {e._attr_name: e for e in boiler_sensors(None, b20)}
for entity in by_name.values():
    entity.coordinator = SimpleNamespace(boilers={ID_20: b20})
r.ok(by_name["운전 상태"].native_value == "연소", "operationBusy 2 = 연소")
r.ok(by_name["난방수 공급 온도"].native_value == 46.5 and by_name["난방수 환수 온도"].native_value == 39.0,
     "공급 93 → 46.5℃ · 환수 78 → 39℃ (설정 온도와 같은 ×2)")
r.ok(by_name["실내 습도"].native_value == 35.5, "습도 355 → 35.5% (×10)")
r.ok(by_name["외기 온도 (기상 관측)"].native_value == 15.0, "외기 150 → 15℃ (×10)")
r.ok("실내 습도" not in names(b40) and "운전 상태" not in names(b40), "NR-40D 에는 열지 않는다 (배율 미확인)")

idle = ready(ID_20, "20", "NR-67D", FEATURE_20, {**STATUS_20, "operationBusy": 1})
off_b = ready(ID_20, "20", "NR-67D", FEATURE_20, {**STATUS_20, "operationMode": 1})
odd = ready(ID_20, "20", "NR-67D", FEATURE_20, {**STATUS_20, "operationBusy": 7})
r.ok((idle.operating_state, off_b.operating_state, odd.operating_state) == ("대기", "꺼짐", None),
     "1 = 대기, 꺼진 보일러 = 꺼짐, 모르는 값은 비운다")
r.ok(thermostat(b20).hvac_action == "heating" and thermostat(idle).hvac_action == "idle",
     "난방 표시는 불이 붙었을 때만 「난방 중」, 쉬면 「대기」")
r.ok(thermostat(off_b).hvac_action == "off", "꺼지면 꺼짐")

dhw = BoilerHotWaterInUse(None, b20)
dhw.coordinator = SimpleNamespace(boilers={ID_20: b20})
r.ok(dhw.is_on is False, "DHWUse 1 = 온수 안 씀")
using = ready(ID_20, "20", "NR-67D", FEATURE_20, {**STATUS_20, "DHWUse": 2})
dhw.coordinator = SimpleNamespace(boilers={ID_20: using})
r.ok(dhw.is_on is True, "DHWUse 2 = 온수 사용 중")

r.section("진단")

diag_coord = control_coord(False, {ID_20: b20})
diag_coord.boiler_stats = {"polls": 1, "control_fail": 0}
view = _boilers_view(diag_coord)
text = repr(view)
r.ok(ID_20 not in text and ID_20[:12] not in text, "deviceId · MAC 이 안 남는다")
r.ok(view["devices"][0]["status"]["ondolTemperatureSetting"] == 80, "상태 원본을 담는다")
r.ok(view["control_enabled"] is False and view["devices"][0]["controllable"] is False, "제어 설정을 담는다")

# 휴대폰 앱의 접속 ID — 앱을 열면 앱 앞으로 가는 응답 토픽에 실려 들어온다 (#20).
app_id = "-".join(["9" * 8, "8" * 4, "7" * 4, "6" * 4, "5" * 12]) + "-U12345"
scrubbed = _scrub({"topic": f"cmd/20/k/mobile-{app_id}/res/start"}, [])
r.ok(scrubbed["topic"] == "cmd/20/k/mobile-**REDACTED**/res/start",
     f"남의 접속 ID · 회원번호도 모양으로 지운다: {scrubbed['topic']}")

sys.exit(r.finish())
