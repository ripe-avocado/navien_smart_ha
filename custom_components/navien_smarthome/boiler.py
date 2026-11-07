"""보일러 상태 (v0.22.0 베타).

보일러는 매트·에어원과 체계가 다르다. 기기목록에는 상태가 없고, MQTT 로 오는
두 가지 응답에서 모든 것을 읽는다.

- `response.feature` — 무엇을 지원하는지와 온도 범위 (`status/start` 응답)
- `response.status`  — 지금 상태. 요청 응답으로도, 보일러가 스스로 보내는
  방송으로도 온다 (#20: 실내조절기 조작이 수 초 안에 방송됨)

**값 체계는 modelCode 로 갈린다** (실기기 응답으로 확인).

| modelCode | 기기 | 온도 값 | status |
| --- | --- | --- | --- |
| `20` | NR-6x | ℃×2 (80 = 40℃) | dict |
| `002` `003` | NR-35D · NR-40D | ℃ 그대로 | dict |
| `011` | NRM-35D 각방 | ℃ 그대로(0.5칸 소수) | 방마다 하나씩 든 list |

NR-6x 의 ×2 는 `actualInsideTemperature`(×10)와 대조해 확인했다 (#20: 45 ↔ 225).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .const import BOILER_MGPP_MODELS, BOILER_MODE_NAMES, BOILER_MODE_OFF

# 각방. 상태가 방 목록으로 온다 (`GetStatusDataNRM35`).
MULTI_ROOM_MODELS = frozenset({"011"})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass
class BoilerDevice:
    """보일러 한 대. 기기목록 원본과 MQTT 로 받은 기능·상태를 함께 든다."""

    raw: dict[str, Any]
    feature: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    # 각방만. `ordering`(1부터) → 그 방의 상태
    rooms: dict[int, dict[str, Any]] = field(default_factory=dict)
    last_message_at: float | None = None

    # -- 기기목록에서 ------------------------------------------------------

    @property
    def device_id(self) -> str:
        return str(self.raw["deviceId"])

    @property
    def model_code(self) -> str:
        return str(self.raw.get("modelCode") or "")

    @property
    def model_name(self) -> str:
        return str(self.raw.get("modelName") or "보일러")

    @property
    def nickname(self) -> str:
        properties = self.raw.get("Properties") or {}
        return str(properties.get("nickName") or self.model_name)

    @property
    def available(self) -> bool:
        return self.raw.get("connected") != 0

    # -- 값 체계 -----------------------------------------------------------

    @property
    def is_mgpp(self) -> bool:
        return self.model_code in BOILER_MGPP_MODELS

    @property
    def is_multi_room(self) -> bool:
        return self.model_code in MULTI_ROOM_MODELS

    @property
    def scale(self) -> float:
        return 2.0 if self.is_mgpp else 1.0

    def celsius(self, value: Any) -> float | None:
        number = _number(value)
        return None if number is None else number / self.scale

    def encode(self, celsius: float) -> float:
        """보낼 값. NR-6x 는 ℃×2 를 **소수로** 보낸다 — 앱이 `boxFloat` 로 싣는다
        (`DeviceControlViewFragment$onChangeSeekbarListener`). 40℃ → `80.0`.
        """
        return float(round(celsius * self.scale))

    # -- 기능 --------------------------------------------------------------

    def supports(self, name: str) -> bool:
        """`{name}Use` 가 1 이면 미지원이다 (앱이 그 탭을 흐리게 그린다).

        2 는 지원, 4 는 단 조절로 쓰인다. NR-40D 의 3 은 앱 코드에 비교가 없다 —
        1 이 아니므로 지원으로 본다(추정). 기능 목록이 아직 없으면 False.
        """
        use = _number(self.feature.get(f"{name}Use"))
        return use is not None and use != 1

    def temperature_range(self, name: str) -> tuple[float, float] | None:
        """`inside` · `ondol` · `hotWater` 의 설정 범위(℃)."""
        low = self.celsius(self.feature.get(f"{name}TemperatureMin"))
        high = self.celsius(self.feature.get(f"{name}TemperatureMax"))
        if low is None or high is None or low >= high:
            return None
        return low, high

    # -- 상태 --------------------------------------------------------------

    @property
    def operation_mode(self) -> int | None:
        number = _number(self.status.get("operationMode"))
        return None if number is None else int(number)

    @property
    def is_on(self) -> bool | None:
        mode = self.operation_mode
        return None if mode is None else mode != BOILER_MODE_OFF

    @staticmethod
    def mode_name(mode: int | None) -> str | None:
        if mode is None:
            return None
        return BOILER_MODE_NAMES.get(mode, f"알 수 없음({mode})")

    def value(self, key: str) -> float | None:
        """온도 값 하나를 ℃ 로."""
        return self.celsius(self.status.get(key))

    def room_value(self, ordering: int, key: str) -> float | None:
        return self.celsius((self.rooms.get(ordering) or {}).get(key))

    @property
    def room_count(self) -> int:
        count = _number(self.feature.get("roomCount"))
        return int(count) if count else len(self.rooms)

    # -- 받기 --------------------------------------------------------------

    def apply_response(self, response: dict[str, Any]) -> bool:
        """응답 하나를 반영한다. 기능 목록이 **처음** 들어왔으면 True.

        **덮어쓰지 않고 합친다.** 방송이 늘 전체를 싣는다는 보장이 없다 — 일부만
        온 것으로 갈아끼우면 나머지 값이 사라진다 (매트·에어원에서 겪은 일).
        """
        first_feature = False
        feature = response.get("feature")
        if isinstance(feature, dict):
            first_feature = not self.feature
            self.feature.update(feature)

        status = response.get("status")
        if isinstance(status, dict):
            self.status.update(status)
        elif isinstance(status, list):
            for room in status:
                if not isinstance(room, dict):
                    continue
                ordering = _number(room.get("ordering"))
                if ordering is None:
                    continue
                self.rooms.setdefault(int(ordering), {}).update(room)

        if isinstance(feature, dict) or status is not None:
            self.last_message_at = time.monotonic()
        return first_feature

    def carry_over(self, old: BoilerDevice) -> None:
        """폴링마다 새로 만든다. 받아 둔 기능·상태를 잃지 않게 이어받는다."""
        self.feature = old.feature
        self.status = old.status
        self.rooms = old.rooms
        self.last_message_at = old.last_message_at
