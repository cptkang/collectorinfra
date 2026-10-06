"""조회용 이벤트 버퍼 — 폴러가 받은 이벤트를 프로세스 메모리에 잠시 둔다 (plans/134 W3 N-11 ·
계약 A-7 · D-274 ⑤ · D-296 ④).

- 폴러가 (소스, 도메인)을 성공적으로 받고 **응답 모양을 알아봤을 때만**(`{result: [...]}` 봉투 —
  빈 목록 포함) **레벨 필터 전** 전 이벤트를 이벤트 조회 행과 같은 마스킹(`buffer_row`)으로 넣고,
  그 조회의 확정 구간(폴러가 정한다 — `[커서, 끝 − 겹침]`)을 남긴다. 폴러 실패·백오프·중지·모르는
  모양은 기록하지 않는다(확정 없음). 재기동 = 빈 버퍼(확정 0).
- 기록은 행을 **전부 만든 뒤 한꺼번에** 반영한다 — 도중에 예외가 나면 아무것도 바꾸지 않고 그 구간도
  확정하지 않는다(건수 불변식 `_total == Σ 키별 건수`를 지킨다).
- 질의는 `gaps`로 확정 구간이 덮지 못한 부분을 받는다 — 덮인 부분만 버퍼 행으로 답하고 나머지는
  호출자가 API로 보충한다(미확인 구간을 0건으로 답하지 않는다).
- 보관(분)·최대 건수는 **메모리 한도**이지 조회 범위 상한이 아니다(밖은 API). 오래된 이벤트부터
  밀어내고, 밀어낸 이벤트 시각까지 그 도메인 확정 구간 시작을 당긴다(밀어낸 구간을 확정으로 남기지
  않는다). 이벤트·확정 구간이 모두 빈 (소스, 도메인) 키는 지운다(만료 키 sweep).
- 시각이 없는 이벤트를 받은 조회는 확정하지 않는다(그 이벤트를 시각 창으로 답할 수 없다).
- 저장 키(`record_key`)는 폴러 멱등 키 칸(`event_key` — 알람 멱등과 같은 규칙 · 바꾸지 않는다)에
  레벨·값·메시지·애플리케이션을 더한 것이다 — txid 없이 같은 시각·유형인 서로 다른 이벤트를 합치지
  않는다. 겹친 재조회의 같은 이벤트는 더하지 않고, 한 응답 안에 같은 레코드가 여럿이면 그 수를
  지킨다(키별 건수 = 응답마다 본 수의 최댓값 — API로 다시 물은 것과 같은 행 수).

이벤트 루프 안에서만 쓴다(메서드에 대기 지점이 없다 — 폴러·조회가 같은 루프에서 번갈아 부른다).
"""

from __future__ import annotations

import bisect
import hashlib
import heapq
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.events import idempotency_key

DomainKey = tuple[str, int]
# 전 키 정리(보관 경계 · 빈 키 sweep) 최소 간격(초) — 폴러는 도메인마다 쓰므로 쓸 때마다 전 키를
# 훑지 않는다(쓴 도메인 자신은 매번 정리한다). 조회·상태 보기는 먼저 전 키를 정리한다.
SWEEP_EVERY_SECONDS = 30.0


def buffer_row(source_id: str, domain_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """이벤트 레코드(어댑터 중립 레코드) → 이벤트 조회 행(`apm_events` 이벤트 행과 같은 칸·같은
    마스킹). 응답에 도메인 id가 없으면 조회한 도메인으로 채운다(폴러 발행과 같은 규칙)."""
    tagged = {
        **event,
        "source_id": source_id,
        "domain_id": event.get("domain_id") if event.get("domain_id") is not None else domain_id,
    }
    return ApmTools._event_row(tagged)


def row_key(row: dict[str, Any]) -> str:
    """이벤트 행의 폴러 멱등 키 칸(소스·도메인·인스턴스·유형·시각·txid) — 알람 멱등과 같은 규칙."""
    ref = row.get("profile_ref") or {}
    return idempotency_key(
        row["source_id"],
        row.get("domain_id"),
        row.get("instance_id"),
        row.get("event_type", ""),
        row.get("time_ms"),
        str(ref.get("txid") or ""),
    )


def event_key(source_id: str, domain_id: int, event: dict[str, Any]) -> str:
    """이벤트 레코드의 폴러 멱등 키 칸 — 그 레코드로 만든 행의 `row_key`와 같은 값(가리기 전에
    본다)."""
    return idempotency_key(
        source_id,
        event.get("domain_id") if event.get("domain_id") is not None else domain_id,
        event.get("instance_id"),
        event.get("event_type", ""),
        event.get("time_ms"),
        str(event.get("txid") or ""),
    )


def _with_content(base: str, level: Any, value: Any, message: Any, application: Any) -> str:
    content = json.dumps([level, value, message, application], ensure_ascii=False, default=str)
    return hashlib.sha256(f"{base}|{content}".encode()).hexdigest()


def record_key(source_id: str, domain_id: int, event: dict[str, Any]) -> str:
    """버퍼 저장 키 — 멱등 키 칸 + 레벨·값·메시지·애플리케이션(원문 — 가리기 전에 본다)."""
    return _with_content(
        event_key(source_id, domain_id, event),
        event.get("level"),
        event.get("value"),
        event.get("message"),
        event.get("application"),
    )


def merge_key(row: dict[str, Any]) -> str:
    """조회 합치기 키(버퍼↔API 겹침 · 정렬 동률) — 멱등 키 칸 + 레벨·값·메시지·애플리케이션
    (행 = 마스킹본)."""
    return _with_content(
        row_key(row), row.get("level"), row.get("value"), row.get("message"), row.get("application")
    )


@dataclass
class _Domain:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)  # 저장 키 → 행
    counts: dict[str, int] = field(default_factory=dict)  # 저장 키 → 건수(응답마다 본 수의 최댓값)
    order: list[tuple[int, str]] = field(default_factory=list)  # (시각, 키) — 키마다 1개 · 시각 순
    spans: list[list[int]] = field(default_factory=list)  # 확정 구간 [시작, 끝] — 닫힌 구간·시작 순
    floor: int = 0  # 이 시각 앞은 확정하지 않는다(밀어낸 이벤트·보관 경계)


class EventBuffer:
    """(소스, 도메인)별 이벤트 행 + 확정 구간."""

    def __init__(
        self,
        *,
        retention_minutes: int = 60,
        max_events: int = 200_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention_minutes < 1 or max_events < 1:
            raise ValueError("이벤트 버퍼 보관 분·최대 건수는 1 이상이어야 한다")
        self.retention_minutes = int(retention_minutes)
        self.max_events = int(max_events)
        self._clock = clock
        self._domains: dict[DomainKey, _Domain] = {}
        self._total = 0  # = Σ 키별 건수(전 도메인)
        self._next_sweep = 0.0
        self.evicted_total = 0

    # ── 쓰기(폴러) ─────────────────────────────────────────

    def record(
        self,
        source_id: str,
        domain_id: int,
        start_ms: int,
        confirmed_end_ms: int,
        events: Iterable[dict[str, Any]],
    ) -> None:
        """폴러 조회 1회 성공(모양을 알아본 응답) — 받은 이벤트 전부(레벨 필터 전)와 확정 구간
        `[start_ms, confirmed_end_ms]`(비면 확정 없음). 행을 다 만든 뒤에 반영한다 — 만들다가
        예외가 나면 그대로 올리고 아무것도 바꾸지 않는다(확정도 없다)."""
        existing = self._domains.get((source_id, domain_id))
        seen: dict[str, int] = {}
        fresh: dict[str, tuple[int, dict[str, Any]]] = {}
        timeless = False
        for event in events:
            time_ms = event.get("time_ms")
            if time_ms is None:
                timeless = True
                continue
            key = record_key(source_id, domain_id, event)
            seen[key] = seen.get(key, 0) + 1
            if key in fresh or (existing is not None and key in existing.rows):
                continue  # 겹친 재조회의 같은 이벤트(다시 가리지 않는다)
            fresh[key] = (int(time_ms), buffer_row(source_id, domain_id, event))
        dom = existing if existing is not None else self._domains.setdefault(
            (source_id, domain_id), _Domain()
        )
        for key, (time_ms, row) in fresh.items():
            dom.rows[key] = row
            dom.order.append((time_ms, key))
        if fresh:
            dom.order.sort()
        for key, count in seen.items():
            before = dom.counts.get(key, 0)
            if count > before:
                dom.counts[key] = count
                self._total += count - before
        if not timeless and start_ms <= confirmed_end_ms:
            self._add_span(dom, int(start_ms), int(confirmed_end_ms))
        self._expire(dom, self._horizon())
        if self._total > self.max_events:
            self._evict(self._total - self.max_events)
        if self._clock() >= self._next_sweep:
            self.sweep()

    @staticmethod
    def _add_span(dom: _Domain, start: int, end: int) -> None:
        spans = [*dom.spans, [start, end]]
        spans.sort()
        merged: list[list[int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        dom.spans = merged
        EventBuffer._clip(dom)

    @staticmethod
    def _clip(dom: _Domain) -> None:
        """확정 구간을 `floor` 뒤로 자른다."""
        dom.spans = [[max(a, dom.floor), b] for a, b in dom.spans if b >= dom.floor]

    def _drop_front(self, dom: _Domain, count: int) -> int:
        """앞(오래된) 키 `count`개를 지운다 → 지운 이벤트 수."""
        dropped = 0
        for _, key in dom.order[:count]:
            dom.rows.pop(key, None)
            dropped += dom.counts.pop(key, 0)
        del dom.order[:count]
        self._total -= dropped
        return dropped

    def _horizon(self) -> int:
        """보관 경계(epoch ms) — 이보다 앞 이벤트·확정 구간은 두지 않는다."""
        return int(self._clock() * 1000) - self.retention_minutes * 60_000

    def _expire(self, dom: _Domain, horizon: int) -> None:
        old = bisect.bisect_left(dom.order, (horizon, ""))
        if old:
            self._drop_front(dom, old)
        if dom.floor < horizon:
            dom.floor = horizon
            self._clip(dom)

    def sweep(self) -> None:
        """전 키 정리 — 보관 경계 밖 이벤트·확정 구간을 지우고, 최대 건수를 넘으면 가장 오래된
        이벤트부터 밀어낸 뒤, 이벤트·확정 구간이 모두 빈 키를 지운다(만료 키 sweep)."""
        horizon = self._horizon()
        for dom in self._domains.values():
            self._expire(dom, horizon)
        if self._total > self.max_events:
            self._evict(self._total - self.max_events)
        for key in [k for k, d in self._domains.items() if not d.order and not d.spans]:
            del self._domains[key]
        self._next_sweep = self._clock() + SWEEP_EVERY_SECONDS

    def _evict(self, excess: int) -> None:
        heap = [(d.order[0][0], k) for k, d in self._domains.items() if d.order]
        heapq.heapify(heap)
        cut: dict[DomainKey, int] = {}
        newest: dict[DomainKey, int] = {}
        while excess > 0 and heap:
            t, key = heapq.heappop(heap)
            dom = self._domains[key]
            n = cut.get(key, 0)
            excess -= dom.counts.get(dom.order[n][1], 0)
            cut[key], newest[key] = n + 1, t
            if n + 1 < len(dom.order):
                heapq.heappush(heap, (dom.order[n + 1][0], key))
        for key, n in cut.items():
            dom = self._domains[key]
            self.evicted_total += self._drop_front(dom, n)
            # 밀어낸 이벤트 시각까지는 확정으로 남기지 않는다(같은 시각의 남은 이벤트도 API로)
            dom.floor = max(dom.floor, newest[key] + 1)
            self._clip(dom)

    # ── 읽기(조회) ─────────────────────────────────────────

    def gaps(
        self, source_id: str, domain_id: int, start_ms: int, end_ms: int
    ) -> list[tuple[int, int]]:
        """`[start_ms, end_ms]` 중 확정 구간이 덮지 못한 닫힌 구간들(시각 순 · 없으면 빈 목록).
        보관 경계 앞은 확정이 아니다(정리 전이라도)."""
        dom = self._domains.get((source_id, domain_id))
        out: list[tuple[int, int]] = []
        cur = start_ms
        horizon = self._horizon()
        if cur < horizon:
            out.append((cur, min(end_ms, horizon - 1)))
            cur = horizon
        for a, b in dom.spans if dom else []:
            if b < cur:
                continue
            if a > end_ms:
                break
            if a > cur:
                out.append((cur, a - 1))
            cur = b + 1
            if cur > end_ms:
                break
        if cur <= end_ms:
            out.append((cur, end_ms))
        return out

    def rows(
        self, source_id: str, domain_id: int, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        """`[start_ms, end_ms]` 안 이벤트 행(시각 순 · 키별 건수만큼 · 저장본 — 호출자가 바꾸지
        않는다)."""
        dom = self._domains.get((source_id, domain_id))
        if dom is None:
            return []
        lo = bisect.bisect_left(dom.order, (start_ms, ""))
        hi = bisect.bisect_right(dom.order, (end_ms, "\U0010ffff"))
        return [
            dom.rows[key] for _, key in dom.order[lo:hi] for _ in range(dom.counts.get(key, 1))
        ]

    def status(self) -> dict[str, Any]:
        """헬스체크용 — 건수 · 키 수 · 확정 도메인 수 · 보관 분 · 최대 건수 · 밀어낸 누적."""
        self.sweep()
        return {
            "enabled": True,
            "events": self._total,
            "domains": len(self._domains),
            "domains_confirmed": sum(1 for d in self._domains.values() if d.spans),
            "retention_minutes": self.retention_minutes,
            "max_events": self.max_events,
            "evicted_total": self.evicted_total,
        }
