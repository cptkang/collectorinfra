"""단기 조사 캐시 (Plan 78 W2-8 · §4.5-⑤ 메모리 · **Tier 2**).

> **Tier 2다** — 착수 조건은 W6(관측) 완료다(78 §4.6.2). 히트율을 재지 못하면 TTL이 적정한지
> 판정할 수 없고, 그러면 이 캐시는 "빠른 것 같다"에 머문다.

**장기 메모리는 도입하지 않는다**(§4.5-⑤ 미채택). 같은 대상을 짧은 시간 안에 다시 묻는
복합 질의(선행 조회 → 조사 → 재확인)에서 같은 스냅샷을 두 번 뜨지 않기 위한 것뿐이다.

## 침묵 금지

**히트 시 수집 시각을 응답에 명시**해야 한다 — 60초 전 스냅샷을 "현재"로 보여주면
사용자는 실시간 값으로 오인한다. 캐시는 조용해선 안 된다.

## 키 만료 sweep

in-memory dict는 **값 bound뿐 아니라 키 만료 sweep도** 필요하다(Known Mistakes).
TTL이 지난 항목을 읽을 때만 지우면 다시 조회되지 않는 키가 영원히 남는다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.observability.investigation_metrics import record_cache

logger = logging.getLogger(__name__)

#: 캐시 항목 상한. 넘으면 가장 오래된 것부터 버린다.
_MAX_ENTRIES = 256


@dataclass(frozen=True)
class CachedSnapshot:
    """캐시된 조사 결과."""

    value: Any
    stored_at: float
    captured_at: Optional[str] = None

    def age_seconds(self, *, now: Optional[float] = None) -> float:
        """저장 후 경과 시간. **응답에 표기해야 하는 값**이다(실시간 오인 방지)."""
        return max(0.0, (now if now is not None else time.monotonic()) - self.stored_at)


class InvestigationCache:
    """`(db_id, hostname, profile)` 키의 TTL 캐시.

    `time.monotonic`을 쓴다 — 벽시계는 NTP 보정으로 뒤로 갈 수 있고, 그러면 TTL 판정이 뒤집힌다.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int = _MAX_ENTRIES) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._max = max(1, int(max_entries))
        self._store: dict[tuple[str, str, str], CachedSnapshot] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(db_id: str, hostname: str, profile: str) -> tuple[str, str, str]:
        return (db_id or "", hostname or "", profile or "")

    def _sweep(self, now: float) -> int:
        """만료 키를 **전부** 지운다(호출부는 락을 잡고 있어야 한다).

        읽을 때만 지우면 다시 조회되지 않는 키가 영원히 남아 dict가 단조 증가한다.
        """
        expired = [k for k, v in self._store.items() if now - v.stored_at >= self._ttl]
        for k in expired:
            self._store.pop(k, None)
        return len(expired)

    def get(self, db_id: str, hostname: str, profile: str) -> Optional[CachedSnapshot]:
        """TTL 내 항목을 반환한다. 없거나 만료면 None.

        히트/미스와 **히트 시 데이터 나이**를 지표에 남긴다(W6-4 캐시 축).
        """
        if self._ttl <= 0:
            record_cache(hit=False)
            return None
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            entry = self._store.get(self._key(db_id, hostname, profile))
        if entry is None:
            record_cache(hit=False)
            return None
        record_cache(hit=True, age_seconds=entry.age_seconds(now=now))
        return entry

    def put(
        self, db_id: str, hostname: str, profile: str, value: Any,
        *, captured_at: Optional[str] = None,
    ) -> None:
        """항목을 저장한다. 상한 초과 시 가장 오래된 것부터 버린다."""
        if self._ttl <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            self._store[self._key(db_id, hostname, profile)] = CachedSnapshot(
                value=value, stored_at=now, captured_at=captured_at
            )
            while len(self._store) > self._max:
                oldest = min(self._store.items(), key=lambda kv: kv[1].stored_at)[0]
                self._store.pop(oldest, None)

    def clear(self) -> None:
        """전부 비운다(테스트·재구성용)."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __bool__(self) -> bool:
        """항목 수와 무관하게 **항상 True**.

        `__len__`만 있으면 빈 캐시가 falsy가 되어 `if cache:` 가드가 첫 저장을 영원히
        건너뛴다(실측 2026-08-27 — 이 버그를 테스트가 잡았다). 캐시의 존재 여부와
        내용물의 유무는 다른 질문이다.
        """
        return True


def freshness_note(entry: CachedSnapshot) -> str:
    """캐시 히트를 사용자에게 **드러내는** 문구 (침묵 금지 — 78 W2-8).

    실시간 오인을 막는 것이 목적이므로 "캐시"라는 사실과 나이를 함께 말한다.
    """
    age = int(entry.age_seconds())
    when = entry.captured_at or "미상"
    return f"(수집 시각 {when} · {age}초 전 스냅샷 재사용)"
