"""모든 모듈이 실제로 import 되는가.

**문법 검사로는 안 잡히는 것이 있다.** 지운 import 를 클래스 본문에서 계속
참조한다든지, 상수 이름을 바꾸고 한 군데를 빠뜨린다든지 하는 것은 파일을
실제로 불러봐야 나온다. 통합 전체가 설치 단계에서 죽는 종류의 실수다.

가장 값싼 시험이면서 가장 자주 무언가를 잡는다.
"""

from __future__ import annotations

import importlib
import sys

from harness import SRC, Report

r = Report()

r.section("통합 모듈")

modules = sorted(path.stem for path in SRC.glob("*.py"))
for name in modules:
    target = "navien_smarthome" if name == "__init__" else f"navien_smarthome.{name}"
    try:
        importlib.import_module(target)
        r.ok(True, name)
    except Exception as err:  # noqa: BLE001 — 무엇이 터지든 보고한다
        r.ok(False, f"{name} — {type(err).__name__}: {err}")


r.section("공개 도구")

# 이슈 템플릿이 사용자에게 돌려보라고 안내하는 파일이라 항상 살아 있어야 한다.
cli = SRC.parent.parent / "tools" / "navien_cli.py"
r.ok(cli.exists(), "tools/navien_cli.py 가 있다")
try:
    compile(cli.read_text("utf-8"), str(cli), "exec")
    r.ok(True, "문법이 맞다")
except SyntaxError as err:
    r.ok(False, f"문법 오류 — {err}")


sys.exit(r.finish())
