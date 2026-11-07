"""저장소에 실제 기기 식별자가 들어가지 않았는가.

제보 파일에서 옮겨 적은 MAC · deviceId 가 두 번 공개됐다 — v0.9.4 의 CHANGELOG ·
주석, v0.20.0 의 테스트 (#14). 둘 다 「형식을 보여 주려고」 옮기면서 값을 안 바꿨다.
공개 저장소는 커밋하는 순간 히스토리와 포크에 남는다.

**기억에 맡기지 않고 여기서 막는다.** 기기ID 모양의 값(12자리 MAC, 16자리 deviceId)은
아래 가상 값만 허용한다. 실제 값 목록을 적지 않는다 — 적으면 그게 또 새는 것이다.

    A1B2C3D4E5F6…   AABBCCDDEEFF…   같은 글자 반복(000000000000 등)

**시리얼 모양도 본다** (v0.23.1). 숫자와 대문자가 섞인 12~24자 토큰이다. 처음엔
16진수만 봤는데, 같은 테스트에 #14 제보자 보일러의 **시리얼 번호**가 함께 들어 있었고
그건 16진수가 아니라 못 잡았다. 가상 시리얼은 `SERIAL-TEST-0001` 처럼 하이픈을 넣는다.

걸리면 그 값을 가상 값으로 바꾼다. 가상 값을 새로 만들 때도 위 앞자리를 쓴다.
"""

from __future__ import annotations

import re
import subprocess
import sys

from harness import ROOT, Report

r = Report()

PATTERN = re.compile(r"\b[0-9A-Fa-f]{12}(?:[0-9A-Fa-f]{4})?\b")
SERIAL = re.compile(r"\b[0-9A-Z]{12,24}\b")
DUMMY_PREFIXES = ("A1B2C3D4E5F6", "AABBCCDDEEFF")


def is_dummy(value: str) -> bool:
    upper = value.upper()
    return upper.startswith(DUMMY_PREFIXES) or len(set(upper)) == 1


# 아직 `git add` 안 한 새 파일도 본다 — 커밋 직전에 돌리는 시험이라서.
# .gitignore 로 막은 것(notes/ 등)은 공개되지 않으므로 보지 않는다.
files = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    cwd=ROOT, capture_output=True, text=True, check=True,
).stdout.splitlines()

found: list[str] = []
for name in files:
    try:
        text = (ROOT / name).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        continue  # 이미지 등
    for match in PATTERN.finditer(text):
        value = match.group(0)
        # 숫자가 하나도 없으면 영어 낱말일 수 있다 (예: 'deadbeefcafe' 는 없지만 대비)
        if not re.search(r"\d", value) or is_dummy(value):
            continue
        line = text.count("\n", 0, match.start()) + 1
        found.append(f"{name}:{line} {value[:4]}…")
    for match in SERIAL.finditer(text):
        value = match.group(0)
        if not (re.search(r"\d", value) and re.search(r"[A-Z]", value)) or is_dummy(value):
            continue
        if PATTERN.fullmatch(value):
            continue  # 위에서 이미 봤다
        line = text.count("\n", 0, match.start()) + 1
        found.append(f"{name}:{line} {value[:4]}…")

r.section("기기ID 모양의 값은 가상 값만")
r.ok(len(files) > 10, f"git 추적 파일을 읽었다 ({len(files)}개)")
r.ok(not found, "실제로 보이는 값 없음" if not found else "가상 값이 아닌 것: " + ", ".join(found))

r.section("검사기 자체")
r.ok(is_dummy("A1B2C3D4E5F61234") and is_dummy("aabbccddeeff") and is_dummy("555555555555"),
     "가상 값은 통과")
other = "0C1D2E" + "3F4A5B"  # 이어 붙인다 — 그대로 적으면 이 파일이 걸린다
r.ok(not is_dummy(other) and PATTERN.fullmatch(other) is not None, "그 밖의 12자리는 걸린다")
serial = "1234" + "5678" + "AB" + "123456"  # 이어 붙인다 — 그대로 적으면 이 파일이 걸린다
r.ok(SERIAL.fullmatch(serial) is not None and not is_dummy(serial), "시리얼 모양도 걸린다")
r.ok(SERIAL.search("SERIAL-TEST-0001") is None, "하이픈 넣은 가상 시리얼은 통과")
r.ok(PATTERN.search("1bb95b43c1080bd2fa8b9e46ed0da1901217e91c") is None,
     "40자리 커밋 SHA 는 기기ID 로 보지 않는다")

sys.exit(r.finish())
