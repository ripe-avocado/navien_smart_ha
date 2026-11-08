"""통계정보 파일 **전체**에 식별자가 남지 않는가 (v0.25.0).

사용자가 이 파일을 공개 이슈에 그대로 붙인다. 지금까지 새어 나간 것들을 모두
한 파일에 넣고, 최종 결과 문자열에 하나도 안 남는지 본다.

| 샌 것 | 어디에 실렸나 | 언제 막았나 |
| --- | --- | --- |
| MAC · deviceId | `macAddress` · `roomcon-<MAC>` 토픽 | v0.20.0 |
| 시리얼 | `boilerControllerSerialNumber` | v0.20.0 |
| 앱 접속 ID · 회원번호 | 앱 앞 응답 토픽 `mobile-<uuid>-U<번호>` | v0.23.0 (#20) |
| deviceId 뒤 4자 | `additionalValue` | v0.24.0 (#14 제안) |
| 집 번호 | MQTT 토픽 맨 앞 `<homeSeq>/smarttok/` | v0.25.0 |

**HA 의 키 가림을 흉내 낸다.** 스텁의 `async_redact_data` 는 받은 것을 그대로
돌려주므로, 그대로 쓰면 키 가림이 빠진 채로 통과해 버린다.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from harness import Report

import navien_smarthome.diagnostics as diag_mod  # noqa: E402
from navien_smarthome.boiler import BoilerDevice  # noqa: E402

r = Report()


def redact(value, keys):
    """HA `async_redact_data` 와 같은 동작."""
    if isinstance(value, dict):
        return {k: ("**REDACTED**" if k in keys else redact(v, keys)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, keys) for v in value]
    return value


diag_mod.async_redact_data = redact

DEVICE_ID = "A1B2C3D4E5F6ABCD"
MAC = DEVICE_ID[:12]
TAIL = DEVICE_ID[12:]
TOPIC_KEY = "topickey-privacy-test"
SERIAL = "SERIAL-PRIVACY-0001"
OUR_CLIENT = "-".join(["1" * 8, "2" * 4, "3" * 4, "4" * 4, "5" * 12]) + "-U77777"
APP_CLIENT = "-".join(["9" * 8, "8" * 4, "7" * 4, "6" * 4, "a" * 12]) + "-U88888"
HOME = "54321"
NICK = "김철수네 보일러"

raw = {"deviceSeq": 7, "serviceCode": 100, "modelCode": "20", "modelName": "NR-67D",
       "deviceId": DEVICE_ID, "mqttTopicKey": TOPIC_KEY, "homeSeq": int(HOME),
       "regionCode": "REGION-TEST", "Properties": {"nickName": NICK}, "connected": 1}


def message(inner_topic: str, response: dict) -> dict:
    return {"at": 1.0, "topic": f"{HOME}/smarttok/{DEVICE_ID}",
            "body": {"topic": inner_topic,
                     "payload": {"protocolVersion": 1, "clientID": f"mobile-{OUR_CLIENT}",
                                 "requestTopic": f"cmd/20/roomcon-{MAC}/status",
                                 "response": {"macAddress": MAC.lower(), "additionalValue": TAIL,
                                              **response}}}}


feature = {"boilerControllerSerialNumber": SERIAL, "ondolUse": 2, "roomControllerSeq": 4242}
status = {"operationMode": 6, "ondolTemperatureSetting": 80}
boiler = BoilerDevice(raw)
boiler.apply_response({"feature": dict(feature)})
boiler.apply_response({"status": dict(status)})

coord = SimpleNamespace(
    raw_devices=[raw], data={}, airone={}, restored_devices=set(), mqtt_connected=True,
    mqtt_stats={}, mqtt_client_id=OUR_CLIENT, poll_attempts=0, poll_failures=0,
    poll_age=None, poll_last_error=None, update_interval=None, home_seq=int(HOME),
    boiler_targets=[raw], boilers={DEVICE_ID: boiler}, boiler_control_enabled=True,
    boiler_stats={}, boiler_commands=[{"at": 1.0, "mode": "ondol-heat", "param": [80.0]}],
    boiler_probe={DEVICE_ID: {
        "sent": [{"request": "status", "at": 1.0, "result": "ok",
                  "body": {"payload": {"clientID": f"mobile-{OUR_CLIENT}",
                                       "requestTopic": f"cmd/20/roomcon-{MAC}/status",
                                       "responseTopic": f"cmd/20/{TOPIC_KEY}/mobile-{OUR_CLIENT}/res",
                                       "request": {"macAddress": MAC, "additionalValue": TAIL}}}}],
        "first": [
            message(f"cmd/20/{TOPIC_KEY}/mobile-{OUR_CLIENT}/res/start", {"feature": dict(feature)}),
            # 사용자가 휴대폰 앱을 열어 둔 동안 들어오는 앱 앞 응답 (#20)
            message(f"cmd/20/{TOPIC_KEY}/mobile-{APP_CLIENT}/res/gas-meter", {"status": dict(status)}),
        ],
        "received": [],
    }},
)
coord.boiler_controllable = lambda b: True
entry = SimpleNamespace(runtime_data=coord, pref_disable_polling=False)
out = asyncio.run(diag_mod.async_get_config_entry_diagnostics(None, entry))
text = repr(out)

r.section("통계정보 파일 전체에 남지 않는 것")
for label, secret in (
    ("deviceId", DEVICE_ID), ("MAC", MAC), ("MAC 소문자", MAC.lower()),
    ("deviceId 뒤 4자", TAIL), ("mqttTopicKey", TOPIC_KEY), ("시리얼", SERIAL),
    ("우리 접속 ID", OUR_CLIENT), ("앱 접속 ID", APP_CLIENT[:36]), ("회원번호", "U88888"),
    ("우리 회원번호", "U77777"), ("집 번호 토픽", f"{HOME}/smarttok"), ("집 번호", HOME),
    ("별칭", NICK), ("지역 코드", "REGION-TEST"), ("룸콘 순번", "4242"),
):
    r.ok(secret not in text, f"{label} 가 안 남는다")

r.section("지원 판단에 쓰는 것은 남는다")
r.ok("**REDACTED**/smarttok/" in text, "토픽 모양은 남는다")
r.ok(out["boilers"]["devices"][0]["status"]["ondolTemperatureSetting"] == 80, "상태 값은 그대로")
r.ok(out["boilers"]["devices"][0]["modelName"] == "NR-67D", "모델명은 남는다")
r.ok(out["boilers"]["commands"][0]["param"] == [80.0], "보낸 명령 값은 남는다")

sys.exit(r.finish())
