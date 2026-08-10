"""구역 하나를 끄고 켜는 길 (이슈 #16).

기기에는 축이 둘이다.

    operationMode     기기 전원. 기기당 하나
    heater.<구역>     { enable, temperature.set 또는 level.set }

**끄는 방법이 「끄기 명령」이 아니라 값이다.** 설정 범위보다 한 칸 아래로 내리면
그 구역이 꺼진다. 카본 매트에서 1단계 아래가 「운전 대기」인 것과 같은 구조다.

    단계형 (1~8, 간격 1)    1 - 1   = 0
    온도형 (28~50, 간격 0.5) 28 - 0.5 = 27.5

앱은 온도와 `enable` 을 **함께** 보내고(`enable = 값 >= rangeMin`), 상태를 읽을
때는 **`enable` 만** 본다. 꺼진 구역이 보고하는 온도는 믿지 않고 화면에 「꺼짐」
으로 덮어쓴다. 그래서 우리도 꺼짐 판단에 `enable` 을 쓴다.

**좌우를 동시에 끌 수 없다.** 기기가 막는다(실기기 확인). 앱도 전원을 대신 꺼주지
않고 안내만 띄운다 — 사용자가 시킨 것은 구역 하나 끄기이지 기기 끄기가 아니다.
"""

from __future__ import annotations

import sys

from harness import Report, make_mat, source

r = Report()

# 실기기 기준값
TEMP = dict(unit="0.5C", range_min=28, range_max=50)   # EME-520 / EMF520
LEVEL = dict(unit="1.0L", range_min=1, range_max=8)    # EME-500 (카본)

MODELS = source("models.py")
CLIMATE = source("climate.py")
SELECT = source("select.py")


def refuse(device, zones) -> str | None:
    try:
        device.build_zone_off(zones)
    except ValueError as err:
        return str(err)
    return None


r.section("꺼짐 값은 최저값에서 한 칸 아래")

temp = make_mat(**TEMP, capacity=2, zones={"left": 33.0, "right": 30.0})
r.ok(temp.heat_control.off_value == 27.5, f"온도형 28 - 0.5 = {temp.heat_control.off_value}")

level = make_mat(**LEVEL, capacity=2, zones={"left": 3, "right": 2})
r.ok(level.heat_control.off_value == 0, f"단계형 1 - 1 = {level.heat_control.off_value}")

# 축을 모르면 값을 지어내지 않는다.
water = make_mat(unit="1.0C", range_min=28, range_max=45, capacity=2,
                 zones={"left": 33.0, "right": 31.0})
r.ok(not water.heat_control.is_known, "1.0C 는 아직 확인 안 된 축이다")
r.ok(water.heat_control.off_value is None, "모르는 축은 꺼짐 값을 안 만든다")


r.section("한쪽 끄기 — 값을 내려 보낸다")

sent = temp.build_zone_off(["right"])
r.ok(sent["heater"]["right"]["temperature"]["set"] == 27.5, "우측을 27.5 로")
r.ok(sent["heater"]["right"]["enable"] is False, "enable 도 함께 내린다")
r.ok(sent["heater"]["left"]["temperature"]["set"] == 33.0, "좌측 33 은 그대로")
r.ok("operationMode" not in sent, "한쪽만 끌 때 전원은 안 건드린다")


r.section("꺼짐 판단은 enable 로 — 화면과 명령이 같은 근거를 본다")

# 꺼진 구역이 정상 범위의 온도를 보고할 수 있다. 앱이 `enable` 을 쓰는 이유다.
odd = make_mat(**TEMP, capacity=2, zones={"left": 30.0, "right": 30.0},
               enables={"left": False})
r.ok(odd.zone_setting("left") == 30.0, "온도는 정상 범위(30)로 보고된다")
r.ok(odd.zone_enabled("left") is False, "그런데 enable 은 꺼짐이다")
r.ok(odd.zone_is_off("left") is True, "enable 을 믿는다 — 꺼진 것으로 안다")

zone_is_off = MODELS.split("def zone_is_off")[1].split("\n    def ")[0]
r.ok("zone_enabled" in zone_is_off, "zone_is_off 가 enable 을 본다")
r.ok("zone_enabled" in CLIMATE, "hvac_mode 도 enable 을 본다 — 두 곳이 같다")


r.section("켜기 — 꺼진 구역은 최저값으로 올린다")

on = odd.build_zone_on(["left"])
r.ok(on["operationMode"] == 1, "기기 전원을 켠다")
r.ok(on["heater"]["left"]["temperature"]["set"] == 28, "꺼져 있던 좌측을 28 로")
r.ok(on["heater"]["left"]["enable"] is True, "enable 도 켠다")
r.ok(on["heater"]["right"]["temperature"]["set"] == 30.0, "켜져 있던 우측은 값 유지")

# **켜라고 한 구역은 반드시 명령에 넣는다.** 기기가 그 구역 온도를 안 보내주면
# 예전에는 통째로 빠져서, 전원만 켜지고 구역은 그대로 남았다.
missing = make_mat(**TEMP, capacity=2, zones={"right": 30.0})
missing.apply_reported({"heater": {"left": {"enable": False}}})
r.ok(missing.zone_setting("left") is None, "좌측 온도를 못 읽는다")
r.ok(missing.zone_is_off("left") is True, "그래도 enable 로 꺼진 걸 안다")
sent = missing.build_zone_on(["left"])
r.ok("left" in sent["heater"], "켜라고 했으면 그 구역을 반드시 넣는다")
r.ok(sent["heater"]["left"]["temperature"]["set"] == 28, "최저값을 실어 보낸다")

# 상태를 한 번도 못 받았으면 값을 지어내지 않는다 — 안내가 나가야 한다.
# 끄기는 보낼 값(꺼짐 값)이 정해져 있어 상태가 없어도 만들 수 있다. 켜기는
# 어디까지 올릴지를 기기 상태에서 가져와야 해서 그럴 수 없다.
blank = make_mat(**TEMP, capacity=2, zones={})
try:
    blank.build_zone_on(["left"])
    guide = ""
except ValueError as err:
    guide = str(err)
r.ok("전원" in guide and "스위치" in guide, "상태가 없으면 전원 스위치를 안내한다")
r.ok("heater" in blank.build_zone_off(["left"]), "끄기는 상태가 없어도 보낼 수 있다")


r.section("마지막 남은 구역은 못 끈다 — 막고 알린다")

half = make_mat(**TEMP, capacity=2, zones={"left": 27.5, "right": 30.0})
r.ok(half.zone_is_off("left") is True, "좌측이 이미 꺼져 있다")
msg = refuse(half, ["right"])
r.ok(msg is not None, "마지막 남은 구역은 거부한다")
r.ok("매트 전원을 종료해" in (msg or ""), f"무엇을 하면 되는지 알린다: {msg}")
r.ok(refuse(half, ["left", "right"]) is not None, "양쪽을 함께 끄는 것도 거부")

single = make_mat(**TEMP, capacity=1, zones={"single": 30.0})
r.ok(refuse(single, ["single"]) is not None, "단일 구역 매트도 마찬가지")

# **전원을 대신 꺼주지 않는다.** 앱도 그렇게 하지 않는다.
build_zone_off = MODELS.split("def build_zone_off")[1].split("\n    def ")[0]
r.ok("operationMode" not in build_zone_off, "끄기가 전원 명령을 만들지 않는다")

# 모르면 막지 않는다 — 보내보고 기기 판단에 맡긴다.
unknown = make_mat(**TEMP, capacity=2, zones={"right": 30.0})
r.ok(unknown.zone_is_off("left") is None, "좌측 상태를 모른다")
r.ok(refuse(unknown, ["right"]) is None, "모르면 막지 않는다")


r.section("단계형은 어느 쪽으로 봐도 답이 같다")

# `level 0` 과 `enable false` 가 함께 움직인다(실기기 확인). 그래서 판단 기준을
# `enable` 로 바꿔도 단계형 사용자에게는 아무 변화가 없다.
for value, want_off in ((0, True), (1, False), (3, False)):
    mat = make_mat(**LEVEL, capacity=2, zones={"left": value, "right": 2})
    by_enable = mat.zone_is_off("left")
    by_value = value <= mat.heat_control.off_value
    r.ok(by_enable is want_off and by_value is want_off,
         f"level {value}: enable 판단 {by_enable} · 값 판단 {by_value}")

mat = make_mat(**LEVEL, capacity=2, zones={"left": 0, "right": 2})
r.ok(mat.build_zone_on(["left"])["heater"]["left"]["level"]["set"] == 1, "대기 → 1단계")
r.ok(mat.build_zone_on(["right"])["heater"]["right"]["level"]["set"] == 2, "켜진 쪽은 유지")
r.ok(refuse(mat, ["right"]) is not None, "좌측이 대기면 우측 끄기 거부")


r.section("엔티티가 서로 다른 축을 쓴다")

r.ok("not control.is_celsius" in CLIMATE, "climate 는 온도형에만 생긴다")
r.ok("not control.is_level" in SELECT, "select 는 단계형에만 생긴다")
r.ok("build_zone_off" in SELECT, "단계형의 「운전 대기」도 같은 자리를 쓴다")


sys.exit(r.finish())
