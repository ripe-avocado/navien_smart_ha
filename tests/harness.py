"""시험 공통 부품. 경로를 잡고, 통과/실패를 세고, 기기를 만든다.

**HA 를 설치하지 않아도 돌아간다.** `ha_stub` 이 필요한 모듈만 흉내 내므로
`python3 tests/run.py` 한 줄이면 끝이다. 외부 패키지도 쓰지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stub  # noqa: E402,F401  (import 만으로 스텁이 등록된다)

sys.path.insert(0, str(ROOT / "custom_components"))

SRC = ROOT / "custom_components" / "navien_smarthome"


class Report:
    """한 시험 파일의 통과/실패."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def ok(self, cond: bool, label: str) -> None:
        if cond:
            self.passed += 1
            print(f"  ✓ {label}")
        else:
            self.failed += 1
            print(f"  ✗ {label}")

    def section(self, title: str) -> None:
        print(f"\n[{title}]")

    def finish(self) -> int:
        print(f"\n{self.passed} 통과 / {self.failed} 실패")
        return 1 if self.failed else 0


def source(name: str) -> str:
    """통합 소스 파일을 글자 그대로 읽는다.

    주석·docstring 에 **왜 그렇게 했는지**를 적어두는 것이 이 저장소의 규칙이라,
    그 근거가 지워지지 않았는지도 시험한다.
    """
    return (SRC / name).read_text("utf-8")


def make_mat(
    *,
    unit: str,
    range_min: float,
    range_max: float,
    zones: dict[str, float],
    capacity: int,
    power_ctrl: bool = True,
    enables: dict[str, bool] | None = None,
    model_code: str = "258",
    model_name: str = "EME-520",
) -> Any:
    """매트 하나를 만든다.

    **`enable` 을 값에서 유도한다.** 기기가 그렇게 보고하고, 앱도 명령을 보낼 때
    `enable = (값 >= rangeMin)` 을 온도와 함께 싣는다. 꺼짐 값인데 `enable: true`
    같은 조합은 실기기에 없으므로 시험에서도 만들지 않는다.
    """
    from navien_smarthome.models import NavienDevice

    side = {"left": "좌측", "right": "우측"} if capacity == 2 else {}
    nick: dict[str, Any] = {"mainItem": "매트"}
    if side:
        nick["side"] = side

    device = NavienDevice.parse(
        {
            "deviceId": "AABBCCDDEEFF",
            "deviceSeq": 1,
            "serviceCode": 200,
            "modelCode": model_code,
            "modelName": model_name,
            "Properties": {
                "nickName": nick,
                "registry": {
                    "attributes": {
                        "model": model_name,
                        "modelType": "em",
                        "mcu": {"capacity": capacity, "modelCode": int(model_code)},
                        "functions": {
                            "powerCtrl": power_ctrl,
                            "heatControl": {
                                "unit": unit,
                                "rangeMin": range_min,
                                "rangeMax": range_max,
                                "safeValue": 37.5,
                                "enableSafe": True,
                            },
                        },
                    }
                },
            },
        }
    )
    assert device is not None

    axis = "level" if unit.endswith("L") else "temperature"
    step = 0.5 if unit == "0.5C" else 1.0
    off_value = range_min - step
    overrides = enables or {}
    device.apply_reported(
        {
            "operationMode": 1,
            "heater": {
                zone: {
                    "enable": overrides.get(zone, value > off_value),
                    axis: {"set": value},
                }
                for zone, value in zones.items()
            },
        }
    )
    return device


def make_airone(*, filters: list[float | None], model_code: str = "1901") -> Any:
    """환기청정 하나를 만든다. 필터 값만 채운다."""
    from navien_smarthome.airone import AironeDevice

    device = AironeDevice.parse(
        {
            "deviceSeq": 1,
            "serviceCode": 300,
            "deviceId": "AABB",
            "modelCode": model_code,
            "Properties": {
                "nickName": "환기",
                "data": {
                    "did": {
                        "reported": {
                            "odu": {
                                "filter": [{"type": i + 1} for i in range(len(filters))]
                            },
                            "airMonitor": [],
                            "roomController": {"mode": [], "zoneId": 1, "sensor": []},
                        }
                    }
                },
            },
        }
    )
    assert device is not None
    device.apply_reported(
        {
            "odu": {
                "filter": [
                    {"type": i + 1, "usage": {"percent": percent}}
                    for i, percent in enumerate(filters)
                ]
            }
        }
    )
    return device
