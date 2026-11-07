"""진단 파일이 보일러 식별자를 가리는가.

보일러 `did` 는 매트와 키 이름이 다르다 — `macAddress`, 대문자 `clientID`,
`boilerControllerSerialNumber`. 셋 다 목록에 없어서 제보 이슈에 MAC 과 시리얼이
그대로 공개됐다 (#14, #21 제보자 지적). 토픽 안의 MAC 도 함께 지워져야 한다.
"""

from __future__ import annotations

import sys

from harness import Report

from navien_smarthome.diagnostics import (  # noqa: E402
    TO_REDACT,
    _identifiers,
    _scrub,
)


r = Report()

MAC = "a1b2c3d4e5f6"
SERIAL = "SERIAL-TEST-0001"
boiler = {
    "serviceCode": 100,
    "modelCode": "20",
    "Properties": {
        "did": {
            "clientID": f"roomcon-{MAC}",
            "requestTopic": f"cmd/20/roomcon-{MAC}/status/start",
            "response": {
                "macAddress": MAC,
                "additionalValue": "9F8E",
                "deviceType": 20,
                "feature": {
                    "boilerControllerSerialNumber": SERIAL,
                    "insideTemperatureMin": 20,
                },
            },
        }
    },
}

r.section("보일러 did 의 식별자")


def redact(value, keys):
    """HA `async_redact_data` 와 같은 동작 — 목록에 있는 키의 값을 어느 깊이든 가린다.
    스텁은 받은 것을 그대로 돌려주므로 키 가림을 시험하려면 이게 필요하다."""
    if isinstance(value, dict):
        return {k: ("**REDACTED**" if k in keys else redact(v, keys)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, keys) for v in value]
    return value


out = _scrub(redact(boiler, TO_REDACT), _identifiers([boiler]))
text = repr(out)
r.ok(MAC not in text, "MAC 이 어디에도 안 남는다")
r.ok(SERIAL not in text, "보일러 시리얼이 안 남는다")
r.ok("9F8E" not in text, "deviceId 뒤 4자(additionalValue)도 안 남는다 (#14 제보자 제안)")
topic = out["Properties"]["did"]["requestTopic"]
r.ok(topic.startswith("cmd/20/") and topic.endswith("/status/start"),
     f"토픽 모양은 남는다: {topic}")
feature = out["Properties"]["did"]["response"]["feature"]
r.ok(feature["insideTemperatureMin"] == 20, "기능 값은 그대로")

sys.exit(r.finish())
