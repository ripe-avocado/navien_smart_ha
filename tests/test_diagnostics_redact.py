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

from homeassistant.components.diagnostics import async_redact_data  # noqa: E402

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

out = _scrub(async_redact_data(boiler, TO_REDACT), _identifiers([boiler]))
text = repr(out)
r.ok(MAC not in text, "MAC 이 어디에도 안 남는다")
r.ok(SERIAL not in text, "보일러 시리얼이 안 남는다")
topic = out["Properties"]["did"]["requestTopic"]
r.ok(topic.startswith("cmd/20/") and topic.endswith("/status/start"),
     f"토픽 모양은 남는다: {topic}")
feature = out["Properties"]["did"]["response"]["feature"]
r.ok(feature["insideTemperatureMin"] == 20, "기능 값은 그대로")

sys.exit(r.finish())
