# 시험

```bash
python3 tests/run.py            # 전부
python3 tests/run.py filter     # 이름에 filter 가 들어간 것만
```

**Home Assistant 를 설치하지 않아도 돌아갑니다.** 외부 패키지도 필요 없습니다.
파이썬 3.11 이상이면 됩니다.

## 왜 HA 없이 도나

`tests/ha_stub.py` 가 통합이 쓰는 HA 모듈의 **이름과 모양만** 흉내 냅니다.
동작은 재현하지 않습니다. 그래서 이 시험이 확인하는 것은 이런 것들입니다.

- 모듈이 실제로 import 되는가 (문법 검사로는 안 잡힙니다)
- 기기 응답을 넣으면 **어떤 명령이 만들어지는가**
- 값이 없거나 이상할 때 **터지지 않고 무엇을 하는가**
- 판단 근거가 코드에 적혀 있는가

HA 가 실제로 어떻게 도는지 알아야 하는 시험은 HA 원본을 직접 받아서 씁니다.

## 무엇을 시험하나

| 파일 | |
| --- | --- |
| `test_imports.py` | 전 모듈 import · CLI 문법 |
| `test_zone_onoff.py` | 구역 하나를 끄고 켜는 길 (이슈 #16) |
| `test_filter.py` | 필터 센서가 잔량이라는 것 (PR #18) |

## 새로 쓸 때

`harness.py` 의 `make_mat` · `make_airone` 로 기기를 만들고 `Report` 로 셉니다.

```python
from harness import Report, make_mat

r = Report()
r.section("무엇을 보는가")
mat = make_mat(unit="0.5C", range_min=28, range_max=50, capacity=2,
               zones={"left": 33.0, "right": 30.0})
r.ok(mat.zone_is_off("left") is False, "켜져 있다")
sys.exit(r.finish())
```

**가짜 상태를 실기기에 없는 조합으로 만들지 마세요.** `make_mat` 이 `enable` 을
값에서 유도하는 이유가 그것입니다 — 꺼짐 값인데 `enable: true` 같은 상태는
실기기에 없고, 그런 데이터로 통과한 시험은 아무것도 보장하지 않습니다.

**왜 그렇게 판단했는지를 함께 적어 주세요.** 이 저장소의 주석은 「무엇을」보다
「왜」를 적습니다. 앱을 다시 뜯지 않아도 근거를 따라갈 수 있어야 합니다.
