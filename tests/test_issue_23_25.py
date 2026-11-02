"""온수매트 제보 두 건.

#25 (EQM-580) — `heatControl.unit` 이 `1.0C` 라 온도조절기가 안 생겼다. 앱은 단위
문자열을 표로 가르지 않는다. 앞 숫자를 간격으로 쓰고, 단계형인지는 상태에 `level`
이 오는지로 가른다. 그래서 `1.0C` 는 1도 간격 온도형이다.

#23 (EMW700) — HA 로 조작하면 본체가 소리를 안 냈다. 앱은 「2024년 이후 모델」에
보내는 모든 제어 명령에 `beep: true` 를 싣는다. 우리는 안 싣고 있었다.
"""

from __future__ import annotations

import asyncio
import sys

from harness import Report, make_mat, source

r = Report()

COORDINATOR = source("coordinator.py")

# 제보 진단값 그대로
EQM580 = dict(unit="1.0C", range_min=28, range_max=45, model_code="2",
              model_name="EQM-580")


r.section("#25 — 1.0C 는 1도 간격 온도형")

water = make_mat(**EQM580, capacity=1, zones={"single": 32.0})
control = water.heat_control
r.ok(control.is_known and control.is_celsius, "온도형으로 안다 → climate 가 생긴다")
r.ok(not control.is_level, "단계형(select)으로 만들지 않는다")
r.ok(control.step == 1.0, f"간격 1도 (= {control.step})")
r.ok(control.off_value == 27.0, f"꺼짐 값 28 - 1 = {control.off_value}")

sent = water.build_heater_desired(changes={"single": 33.0})
r.ok(sent["single"]["temperature"]["set"] == 33.0, "temperature.set 으로 보낸다")
r.ok("level" not in sent["single"], "level 로 보내지 않는다")

r.section("모르는 축은 여전히 만들지 않는다")

odd = make_mat(unit="2.0X", range_min=28, range_max=45, capacity=1,
               zones={"single": 32.0})
r.ok(not odd.heat_control.is_known, "2.0X 는 모르는 축")
r.ok(odd.heat_control.off_value is None, "꺼짐 값을 지어내지 않는다")


r.section("#23 — 2024년 이후 모델에만 beep 를 싣는다")

from navien_smarthome.coordinator import NavienSmartCoordinator  # noqa: E402


class _Api:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def async_control(self, home_seq, raw, desired) -> None:
        self.sent.append(desired)


class _Coordinator:
    home_seq = 1

    def __init__(self) -> None:
        self.api = _Api()


def send(model_code: str, desired: dict) -> dict:
    fake = _Coordinator()
    device = make_mat(unit="0.5C", range_min=28, range_max=45, capacity=1,
                      zones={"single": 30.0}, model_code=model_code)
    asyncio.run(NavienSmartCoordinator.async_send(fake, device, desired))
    return fake.api.sent[-1]


new = send("17", {"operationMode": 1})          # EMW700
r.ok(new.get("beep") is True, "EMW700(17) 전원 명령에 beep: true")
r.ok(new.get("operationMode") == 1, "원래 명령은 그대로")

old = send("2", {"operationMode": 1})           # EQM-580
r.ok("beep" not in old, "EQM-580(2) 은 앱도 안 싣는다 → 안 싣는다")

original = {"operationMode": 0}
send("17", original)
r.ok("beep" not in original, "넘겨받은 명령을 고쳐 쓰지 않는다")

# 빈 초기 요청은 async_send 를 지나지 않는다. 지나면 HA 가 켜질 때마다 울린다.
init = COORDINATOR.split("초기 상태를 요청하지 않습니다")[1].split("초기 상태를 요청했습니다")[0]
r.ok("async_control(self.home_seq, device.raw, {})" in init, "초기 요청은 빈 명령 그대로")

sys.exit(r.finish())
