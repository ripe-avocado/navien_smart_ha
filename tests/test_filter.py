"""필터 센서는 사용률이 아니라 잔량이다 (PR #18).

기여자가 실기기에서 앱과 대조했다 — 센서 `87`, 앱 「필터 87% 남음」, 사용량 13%.

**앱 코드로 독립 확인했다.** 필터 관리 화면이 퍼센트를 문구로 바꾸는 규칙이다.

    v >= 76        「필터가 충분하네요!」
    41 <= v < 76   「아직은 여유있네요!」
    11 <= v < 41   「곧 필터를 교체해야 해요!」
    v < 11         「필터를 교체해 주세요!」

**값이 높을수록 「충분」, 낮을수록 「교체」.** 사용률이라면 정확히 반대여야 한다.
실기기 대조와 앱 동작이 서로 독립적으로 같은 결론을 낸다.

값을 뒤집지 않은 것도 맞다 — 처음부터 잔량이었으므로 **쌓인 기록이 이미 옳다.**
`100 - x` 로 바꾸면 이 변경을 경계로 같은 숫자가 정반대 뜻이 된다.
"""

from __future__ import annotations

import ast
import sys

from harness import ROOT, Report, make_airone, source

r = Report()

SENSOR = source("sensor.py")
AIRONE = source("airone.py")
README = (ROOT / "README.md").read_text("utf-8")


r.section("이름이 잔량이다")

r.ok("필터 잔량" in SENSOR, "필터가 하나면 「필터 잔량」")
r.ok('f"필터 {index + 1} 잔량"' in SENSOR, "여러 개면 「필터 N 잔량」")
r.ok("사용률" not in SENSOR, "sensor.py 에 「사용률」이 안 남았다")
r.ok("사용률" not in AIRONE, "airone.py 에도 안 남았다")
r.ok("필터 잔량" in README, "README 엔티티 표도 맞다")


r.section("값은 그대로 — 뒤집지 않는다")

device = make_airone(filters=[87, 42])
r.ok(device.filters[0]["percent"] == 87, "87 이 87 로 그대로 온다")
r.ok(device.filters[1]["percent"] == 42, "두 번째도 그대로")
r.ok("100 -" not in SENSOR and "100-" not in SENSOR, "어디서도 100 에서 빼지 않는다")
r.ok('"percent"' in AIRONE, "진단 키 이름을 안 바꿨다 — 출력 형식이 유지된다")


r.section("값이 안 오면 비운다 — 0 으로 채우지 않는다")

# 실외기가 필터 4개를 선언하고 일부만 값을 보내는 기기가 있다(제보 확인).
# 없는 값을 0 으로 채우면 「교체하세요」로 읽혀 거짓이 된다.
partial = make_airone(filters=[87, None, None, None])
r.ok(len(partial.filters) == 4, "선언된 개수만큼 자리를 만든다")
r.ok(partial.filters[1]["percent"] is None, "안 온 값은 None 이다")


r.section("엔티티가 안 갈린다 — 기존 기록이 이어진다")

r.ok(
    'f"{device.device_id}_filter_{index}"' in SENSOR,
    "unique_id 를 안 건드렸다 — 엔티티가 새로 생기지 않는다",
)
r.ok(
    "_attr_has_entity_name" in source("entity.py"),
    "표시 이름만 바뀌고 entity_id 는 유지된다",
)


r.section("근거를 코드에 남겼다")

cls = next(
    node
    for node in ast.walk(ast.parse(SENSOR))
    if isinstance(node, ast.ClassDef) and node.name == "AironeFilterSensor"
)
doc = ast.get_docstring(cls) or ""
r.ok("잔량" in doc, "잔량이라고 적었다")
r.ok("usage.percent" in doc, "필드 이름과 뜻이 반대라는 것을 적었다")
r.ok("87" in doc, "실기기 대조 값을 적었다")


sys.exit(r.finish())
