"""보일러 상태 수집.

보일러는 아직 엔티티가 없다. 지원 여부를 가르려면 **상태 요청에 응답이 오는지,
어떤 모양인지**를 봐야 하는데, 제보자에게 도구를 돌려 달라고 할 수 없다. 그래서
통합이 앱과 같은 읽기 요청 두 개(`status/start`, `status`)를 보내고 받은 것을
진단 파일에 담는다.

봉투는 APK 로 확인한 형식이다 (`GetStatusData.RequestControl` / `RequestControlNR35D`).
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report

import navien_smarthome.coordinator as coord_mod  # noqa: E402
from navien_smarthome.api import NavienSmartApi  # noqa: E402
from navien_smarthome.coordinator import NavienSmartCoordinator  # noqa: E402
from navien_smarthome.diagnostics import async_get_config_entry_diagnostics  # noqa: E402

r = Report()

DEVICE_ID = "A1B2C3D4E5F61234"     # 앞 12자 MAC + 뒤 4자 additionalValue
MAC = DEVICE_ID[:12]
TOPIC_KEY = "topickey-0123456789"
CLIENT = "11111111-2222-3333-4444-555555555555-U99"


def boiler(model_code: str, **extra) -> dict:
    return {"deviceSeq": 7, "serviceCode": 100, "modelCode": model_code,
            "modelName": "NR-67D", "deviceId": DEVICE_ID, "mqttTopicKey": TOPIC_KEY,
            **extra}


class _Session:
    user_seq = 99


def body_for(model_code: str, request) -> dict:
    api = NavienSmartApi.__new__(NavienSmartApi)
    sent: list = []

    async def fake(method, path, **kwargs):
        sent.append((method, path, kwargs))
        return {}

    api._require_session = lambda: _Session()
    api._async_authed_request = fake
    return asyncio.run(api.async_boiler_request(1, boiler(model_code), CLIENT, request)), sent


r.section("봉투 — NR-6x (modelCode 20)")

body, sent = body_for("20", coord_mod.BOILER_REQ_START)
p = body["payload"]
r.ok(sent[0][0] == "POST" and sent[0][1] == "/devices/7/control", "REST 제어 주소로 보낸다")
r.ok(body["serviceCode"] == 100, "serviceCode 100")
r.ok(p["protocolVersion"] == 1, "protocolVersion 1")
r.ok(p["clientID"] == f"mobile-{CLIENT}", "clientID 는 대문자 ID, mobile- 접두")
r.ok(p["requestTopic"] == f"cmd/20/roomcon-{MAC}/status/start", f"요청 토픽 {p['requestTopic']}")
r.ok(p["responseTopic"] == f"cmd/20/{TOPIC_KEY}/mobile-{CLIENT}/res/start", "응답 토픽")
req = p["request"]
r.ok(req["macAddress"] == MAC and req["additionalValue"] == "1234", "deviceId 를 12 + 4 로 쪼갠다")
r.ok(req["command"] == 16777217 and req["deviceType"] == 20, "command · deviceType")
r.ok(req["param"] == [] and req["paramStr"] == "", "읽기 요청 — param 비어 있음")
r.ok("mode" not in req, "mode 는 싣지 않는다 (앱도 null)")

r.section("봉투 — NR-40D (modelCode 003)")

body, _ = body_for("003", coord_mod.BOILER_REQ_STATUS)
p = body["payload"]
r.ok("protocolVersion" not in p, "protocolVersion 없음")
r.ok(set(p["request"]) == {"macAddress", "registerAt", "additionalValue", "param"},
     f"request 키 네 개만: {sorted(p['request'])}")
r.ok(p["requestTopic"] == f"cmd/003/roomcon-{MAC}/status", "앞자리 0 을 지키는 토픽")


r.section("대상 고르기")

fake = SimpleNamespace(raw_devices=[
    boiler("20"),
    boiler("003", deviceSeq=8, deviceId="AABBCCDDEEFF0001"),
    boiler("120", deviceSeq=9),                        # 각방 — 모양이 달라 안 묻는다
    boiler("001", deviceSeq=10),                       # 도트 — 안 묻는다
    boiler("20", deviceSeq=11, deviceId="SHORT"),      # deviceId 를 못 쪼갠다
    {"deviceSeq": 12, "serviceCode": 200, "modelCode": "20", "deviceId": DEVICE_ID},
])
targets = NavienSmartCoordinator.boiler_targets.fget(fake)
r.ok([t["deviceSeq"] for t in targets] == [7, 8], f"20 · 003 만 묻는다: {[t['deviceSeq'] for t in targets]}")


r.section("요청 순서 — start 뒤 status")

calls: list = []


async def no_sleep(seconds):
    calls.append(("sleep", seconds))


class _Api:
    async def async_boiler_request(self, home_seq, raw, client_id, request):
        calls.append((request[0], client_id))
        return {"payload": {"requestTopic": f"cmd/20/roomcon-{MAC}/{request[0]}"}}


probe = SimpleNamespace(
    raw_devices=[boiler("20")], api=_Api(), home_seq=1, boiler_probe={},
    _mqtt=SimpleNamespace(client_id=CLIENT),
)
probe.boiler_targets = NavienSmartCoordinator.boiler_targets.fget(probe)
probe._boiler_record = lambda device_id: NavienSmartCoordinator._boiler_record(probe, device_id)
real_sleep = coord_mod.asyncio.sleep
coord_mod.asyncio.sleep = no_sleep
try:
    asyncio.run(NavienSmartCoordinator._async_probe_boilers(probe))
finally:
    coord_mod.asyncio.sleep = real_sleep
r.ok([c[0] for c in calls] == ["status/start", "sleep", "status"], f"순서 {[c[0] for c in calls]}")
r.ok(calls[0][1] == CLIENT, "접속 중인 MQTT clientId 를 싣는다")
sent_log = probe.boiler_probe[DEVICE_ID]["sent"]
r.ok([e["request"] for e in sent_log] == ["status/start", "status"], "보낸 것을 남긴다")
r.ok(all(e["result"] == "ok" for e in sent_log), "결과도 남긴다")


r.section("받은 것을 남긴다 — 해석하지 않는다")

for i in range(12):
    NavienSmartCoordinator._handle_boiler_message(
        probe, f"1/smarttok/{DEVICE_ID}",
        {"topic": f"cmd/20/{TOPIC_KEY}/mobile-{CLIENT}/res",
         "payload": {"response": {"macAddress": MAC.lower(), "status": {"operationMode": 5, "n": i}}}},
    )
NavienSmartCoordinator._handle_boiler_message(probe, "1/smarttok/connected/OTHER", {"x": 1})
received = probe.boiler_probe[DEVICE_ID]["received"]
r.ok(len(received) == coord_mod.BOILER_KEEP_MESSAGES, f"최근 {coord_mod.BOILER_KEEP_MESSAGES}개만")
r.ok(received[-1]["body"]["payload"]["response"]["status"]["n"] == 11, "가장 최근 것이 남는다")
r.ok("unknown" in probe.boiler_probe, "어느 보일러 것인지 모르면 따로 둔다")


r.section("진단 파일에 식별자가 안 남는다")

probe.data = {}
probe.airone = {}
probe.restored_devices = set()
probe.mqtt_connected = True
probe.mqtt_stats = {"boiler_received": 13}
probe.mqtt_client_id = CLIENT
probe.poll_attempts = probe.poll_failures = 0
probe.poll_age = probe.poll_last_error = None
probe.update_interval = None
entry = SimpleNamespace(runtime_data=probe, pref_disable_polling=False)
diag = asyncio.run(async_get_config_entry_diagnostics(None, entry))
text = repr(diag)
for label, secret in (("deviceId", DEVICE_ID), ("MAC", MAC), ("MAC 소문자", MAC.lower()),
                      ("mqttTopicKey", TOPIC_KEY), ("clientId", CLIENT)):
    r.ok(secret not in text, f"{label} 가 안 남는다")
view = diag["boiler_probe"]
r.ok(isinstance(view, list) and view[0]["modelName"] == "NR-67D", "목록으로 담는다 — deviceId 키가 없다")
r.ok(view[0]["received"][-1]["body"]["payload"]["response"]["status"]["operationMode"] == 5,
     "상태 값은 그대로 남는다")

sys.exit(r.finish())
