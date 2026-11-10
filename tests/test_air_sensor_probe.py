"""기기목록이 「센서 없음」이라 해도 공기질을 한 번은 묻는다 (v0.26.1, #30).

THE650 은 기기목록에 에어모니터도 룸콘 센서 표도 빈 칸으로 온다. 그래서 v0.26.0
까지는 공기질을 아예 묻지 않았다(`wants_air_sensors: false`). 실제로 `/air-sensor` 를
부르면 9종이 다 온다 — 제보자가 직접 확인했다.

한 번 물어 값이 오면 계속 묻고, 비어 있으면 그때 멈춘다. 센서 없는 전열교환기는
HA 를 켤 때 한 번만 더 묻는다.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report, make_airone, source

from navien_smarthome.coordinator import NavienSmartCoordinator  # noqa: E402

r = Report()


def online():
    device = make_airone(filters=[])
    device.connected_registry = True
    return device

PM = [{"type": "pm2Dot5", "value": 12}, {"type": "co2", "value": 600}]


def run_once(device, response):
    calls = []

    async def get_air(home_seq, device_seq):
        calls.append(device_seq)
        return response

    logged = []
    fake = SimpleNamespace(
        airone={device.device_id: device},
        api=SimpleNamespace(async_get_air_sensor=get_air),
        home_seq=1,
        _skipped_logged=set(),
        _log_skip=lambda *a: None,
    )
    fake._log_no_air_sensors = lambda d: logged.append(d.device_id)
    asyncio.run(NavienSmartCoordinator._async_update_air_sensors(fake))
    return calls, logged


r.section("표가 비어 있는 기기 (THE650 · 모니터 없는 전열교환기)")

device = online()
r.ok(device.declared_sensors == () and not device.air_monitors, "기기목록은 「센서 없음」")
r.ok(device.wants_air_sensors, "그래도 처음에는 묻는다")

r.section("물어보니 값이 왔다 — THE650")

the650 = online()
calls, logged = run_once(the650, PM)
r.ok(calls == [1], "한 번 물었다")
r.ok(set(the650.sensor_kinds) == {"pm2Dot5", "co2"}, f"공기질 종류가 잡혔다: {the650.sensor_kinds}")
r.ok(the650.wants_air_sensors, "값이 있으니 계속 묻는다")
calls, _ = run_once(the650, [])
r.ok(calls == [1], "다음 폴링에도 묻는다")
r.ok(the650.wants_air_sensors, "한 번 비어 와도 멈추지 않는다 — 값은 유지된다")

r.section("물어보니 비어 있다 — 센서 없는 기기")

bare = online()
calls, logged = run_once(bare, [])
r.ok(calls == [1], "처음 한 번은 묻는다")
r.ok(not bare.wants_air_sensors, "비어 있으니 이제 멈춘다")
calls, logged = run_once(bare, [])
r.ok(calls == [] and logged == [bare.device_id], "다음부터 안 묻고, 안 묻는다는 기록을 남긴다")

r.section("조회가 실패하면 다음에 또 묻는다")

from navien_smarthome.api import NavienSmartError  # noqa: E402

flaky = online()


async def boom(home_seq, device_seq):
    raise NavienSmartError("timeout")


fake = SimpleNamespace(airone={flaky.device_id: flaky}, api=SimpleNamespace(async_get_air_sensor=boom),
                       home_seq=1, _skipped_logged=set(), _log_skip=lambda *a: None,
                       _log_no_air_sensors=lambda d: None)
asyncio.run(NavienSmartCoordinator._async_update_air_sensors(fake))
r.ok(flaky.wants_air_sensors, "응답을 못 받았으면 물어본 것으로 치지 않는다")

r.section("폴링마다 새 객체 — 물어본 기록을 이어받는다")

r.ok("device.air_sensor_probed = old.air_sensor_probed" in source("coordinator.py"),
     "coordinator 가 air_sensor_probed 를 이어받는다")

sys.exit(r.finish())
