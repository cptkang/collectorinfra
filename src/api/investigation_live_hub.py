"""조사 진행 실시간 허브·SSE (plans/187 W14 · §5.6.3 ⓒ · D-341 부기 4 · G-39·G-41·G-42).

본체 프로세스 안 `InvestigationLiveHub`가 미종결 조사마다 폴러 1개로 sre_agent에
`sre_get_investigation(investigation_id, activity_since)`를
`NOISE_INVESTIGATION_LIVE_POLL_SECONDS` 간격으로 **한 번만** 묻는다(보는 사람·탭 수와 무관).
받은 실행 기록은 조사별 누적 버퍼(이벤트 300건 · 발췌·메모 2KB — sre_agent 링 버퍼와 같은 상한)에
쌓고, SSE 스트림(`stream_events`)이 구독자 권한에
맞게 걸러 나눠 보낸다. 기존 `alarm_bus`와 같은 in-process 방식이라 **uvicorn 단일 워커 전제**다 —
워커가 여럿이면 워커마다 폴러가 생긴다(부하 × 워커 수 · 결과는 같다).

- 폴러 수명: 구독(스트림 관심 · 조회 경로 접촉)이 있는 동안만 돈다. 종결을 보면 마지막 1회를 더 읽고
  멈춘다. 구독 0이 `IDLE_STOP_SECONDS`(60초) 이어지면 멈추고, 버퍼는 `BUFFER_TTL_SECONDS`(10분) 뒤
  sweep 한다(데몬 in-memory dict 키 만료). 폴러 예외는 그 조사에만 「갱신 실패 — 사유」를
  알리고 다음 주기에 다시 묻는다(침묵 정지 금지).
- 재기동하면 허브는 비어 있고, 다음 구독 때 조사 저장소의 미종결 레코드에서 커서 0부터 다시 읽는다.
- 종결 시 실행 기록 요약(발췌·AI 메모 제외)을 조사 저장소와 같은 폴더의
  `investigation_activity.jsonl`에 1행 남긴다(G-37). sre_agent 잡이 사라진 뒤의 조회는 그
  요약으로 답한다.
- 사용자 경로: AI 메모(`ai_note`) 이벤트와 `note` 키를 빼고(G-38), 한 줄·발췌 등 문자열을 D-325 ③
  마스킹으로 한 번 더 가린다. 관리자 경로는 sre_agent `mask_secrets` 뒤 원문.
- sre_agent 응답에 `activity`·`phase` 등 키가 없어도(구버전) 깨지지 않는다 — 이벤트 0건 ·
  `source="none"`.

계층: interface(`src/api/`). sre_agent는 poll만 받는다(D-123·D-118 — 브라우저로 미는 일은
본체가 맡는다).
쓰기·취소 경로는 없다(D-003).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.api import investigation_poll

if TYPE_CHECKING:
    from noise_gate.infrastructure.investigation_activity_store import (
        InvestigationActivityStore,
    )
    from noise_gate.infrastructure.investigation_store import InvestigationStore

logger = logging.getLogger(__name__)

#: 조사당 버퍼 이벤트 상한(sre_agent 링 버퍼와 같다 · 넘으면 오래된 것부터 버리고 `truncated`).
ACTIVITY_MAX_EVENTS = 300
#: 이벤트 문자열 한 칸 상한(자) — 발췌·메모 2KB와 같은 차수로 방어적으로 자른다.
TEXT_MAX_CHARS = 2048
#: 구독 0이 이만큼 이어지면 폴러를 멈춘다(초).
IDLE_STOP_SECONDS = 60.0
#: 멈춘(또는 종결된) 조사의 버퍼를 비우는 시간(초).
BUFFER_TTL_SECONDS = 600.0
#: 스트림 ping 간격(초) — `/alarm/notifications/stream`과 같다.
STREAM_PING_SECONDS = 25.0
#: 스트림 1회 연결의 최대 수명(초) — 끝나면 브라우저 EventSource가 `Last-Event-ID`로 이어 붙는다.
STREAM_MAX_SECONDS = 3600.0
#: 재연결 대기(ms) — SSE `retry:` 필드.
STREAM_RETRY_MS = 3000
#: `watch` 항목 상한(하위 탭 수 무제한 G-40이지만 요청 1건의 크기는 묶는다).
WATCH_MAX_ITEMS = 200
#: 폴러 실패 표기 머리말.
POLL_ERROR_PREFIX = "갱신 실패 — "
#: 조사 id 꼴(uuid hex·채팅 조사 id 등) — SSE `id:` 줄에 실리므로 개행·구분자를 막는다.
INVESTIGATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
#: 응답 최상위에 싣는 진행 상태 키(sre_agent 계약 그대로).
STATE_KEYS: tuple[str, ...] = ("phase", "queue_position", "elapsed_s")
#: 사용자 경로 이벤트 키(화이트리스트 — `note`는 싣지 않는다 · G-38 fail-closed).
USER_EVENT_KEYS: tuple[str, ...] = (
    "seq", "at", "engine", "kind", "step", "tool", "label", "status", "elapsed_ms", "rows",
    "excerpt",
)
#: 마스킹하지 않는 이벤트 키(수치·enum·도구 이름).
_UNMASKED_EVENT_KEYS: frozenset[str] = frozenset({
    "seq", "at", "engine", "kind", "step", "tool", "status", "elapsed_ms", "rows",
})
#: 관리자에게만 보내는 이벤트 종류(G-38).
ADMIN_ONLY_KINDS: frozenset[str] = frozenset({"ai_note"})
_CLIP_KEYS: tuple[str, ...] = ("label", "excerpt", "note")
_STATUS_RUNNING = "running"
#: 목록 「엔진」 칸에 싣는 엔진 이름(W14 교정 — 그 밖의 값은 버린다).
LIST_ENGINES: frozenset[str] = frozenset({"playbook", "holmes"})


def _active_statuses() -> frozenset[str]:
    from noise_gate.infrastructure.investigation_store import ACTIVE_STATUSES

    return ACTIVE_STATUSES


def _engines_of(entry: LiveEntry | None) -> list[str]:
    """목록 엔진 칸 — 버퍼 이벤트 `engine`의 정렬 집합.

    버퍼에 없으면 progress의 `engine`/`engines`, 그것도 없으면 빈 리스트.
    """
    if entry is None:
        return []
    seen = {ev.get("engine") for ev in entry.events}
    if not seen & LIST_ENGINES:
        progress = entry.live.get("progress")
        if isinstance(progress, Mapping):
            raw = progress.get("engines", progress.get("engine"))
            seen = set(raw) if isinstance(raw, list | tuple) else {raw}
    return sorted(e for e in seen if isinstance(e, str) and e in LIST_ENGINES)


def _mask_value(value: Any, mask_fn: Callable[[str], str] | None) -> Any:
    """문자열은 줄 단위로 가린다(마스킹 패턴이 값 전체 일치라 줄마다 대조) · 컨테이너는 재귀."""
    if mask_fn is None:
        return value
    if isinstance(value, str):
        return "\n".join(mask_fn(line) for line in value.split("\n"))
    if isinstance(value, list):
        return [_mask_value(v, mask_fn) for v in value]
    if isinstance(value, dict):
        return {k: _mask_value(v, mask_fn) for k, v in value.items()}
    return value


def user_events(
    events: Iterable[Mapping[str, Any]], mask_fn: Callable[[str], str] | None
) -> list[dict[str, Any]]:
    """사용자 경로 이벤트 — AI 메모 제외 · 키 화이트리스트 · 문자열 D-325 ③ 마스킹(순수 함수)."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") in ADMIN_ONLY_KINDS:
            continue
        item: dict[str, Any] = {}
        for key in USER_EVENT_KEYS:
            if key not in ev:
                continue
            value = ev[key]
            item[key] = value if key in _UNMASKED_EVENT_KEYS else _mask_value(value, mask_fn)
        out.append(item)
    return out


def _valid_event(ev: Any) -> bool:
    return isinstance(ev, dict) and isinstance(ev.get("seq"), int) and not isinstance(
        ev.get("seq"), bool
    )


def _clip_event(ev: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(ev)
    for key in _CLIP_KEYS:
        value = out.get(key)
        if isinstance(value, str) and len(value) > TEXT_MAX_CHARS:
            out[key] = value[:TEXT_MAX_CHARS] + "…"
    return out


@dataclass(eq=False)
class LiveEntry:
    """조사 1건의 허브 상태(버퍼·커서·진행 상태·폴러)."""

    investigation_id: str
    rec: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    #: sre_agent에 보낸 `activity_since` 커서 = 받은 최대 `seq`(seq는 1부터라고 본다).
    cursor: int = 0
    truncated: bool = False
    activity_seen: bool = False
    from_archive: bool = False
    status: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    live: dict[str, Any] = field(default_factory=dict)
    state_version: int = 0
    partial_version: int = 0
    poll_error: str | None = None
    error_version: int = 0
    polled_at: float | None = None
    touched_at: float = 0.0
    final: bool = False
    final_at: float | None = None
    subscribers: int = 0
    archived: bool = False
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def running(self) -> bool:
        """폴러가 돌고 있는가."""
        return self.task is not None and not self.task.done()

    @property
    def source(self) -> str:
        """실행 기록 출처 — `live`(sre_agent) · `archive`(요약 JSONL) · `none`(기록 없음·구버전)."""
        if self.from_archive:
            return "archive"
        return "live" if self.activity_seen else "none"

    def events_after(self, since: int) -> list[dict[str, Any]]:
        """`seq > since` 이벤트(오래된 것 먼저)."""
        return [ev for ev in self.events if ev["seq"] > since]


class InvestigationLiveHub:
    """미종결 조사당 폴러 1개 · 조사별 버퍼 · 구독자 관심 집합을 관리하는 in-process 허브."""

    def __init__(
        self,
        ng: Any,
        *,
        store_factory: Callable[[], InvestigationStore | None],
        activity_store: InvestigationActivityStore | None,
        poll_seconds: float | None = None,
        idle_stop_seconds: float | None = None,
        buffer_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """설정(noise_gate)·조사 저장소 팩토리·요약 보관소를 받는다(간격은 설정값 · 주입 가능)."""
        self._ng = ng
        self._store_factory = store_factory
        self._activity_store = activity_store
        self.poll_seconds = float(
            poll_seconds if poll_seconds is not None
            else getattr(ng, "investigation_live_poll_seconds", 5.0)
        )
        self.idle_stop_seconds = float(
            idle_stop_seconds if idle_stop_seconds is not None else IDLE_STOP_SECONDS
        )
        self.buffer_ttl_seconds = float(
            buffer_ttl_seconds if buffer_ttl_seconds is not None else BUFFER_TTL_SECONDS
        )
        self.clock = clock
        self.version = 0
        self._entries: dict[str, LiveEntry] = {}
        self._interest: dict[object, set[str]] = {}
        self._active_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._changed = asyncio.Event()
        self._closed = False

    # ── 조회 ────────────────────────────────────────────────────────────────

    def entry(self, investigation_id: str) -> LiveEntry | None:
        """조사 id의 허브 상태(없으면 None)."""
        return self._entries.get(investigation_id)

    def is_live(self, investigation_id: str) -> bool:
        """그 조사의 폴러가 돌고 있는가(참이면 조회 경로가 sre_agent를 따로 묻지 않는다)."""
        entry = self._entries.get(investigation_id)
        return entry is not None and entry.running

    def active_records(self, *, fresh: bool = False) -> list[dict[str, Any]]:
        """조사 저장소의 미종결 레코드(조사 id별 최신 · 알람 합집합) — poll 간격 동안 캐시한다.

        읽는 김에 만료 버퍼를 sweep 한다. sre_agent는 부르지 않는다.
        """
        now = self.clock()
        cached = self._active_cache
        if not fresh and cached is not None and now - cached[0] < self.poll_seconds:
            return cached[1]
        self.sweep(now)
        store = self._store_factory()
        recs: list[dict[str, Any]] = []
        if store is not None:
            active = _active_statuses()
            for latest in store.latest_records():
                if latest.get("status") not in active:
                    continue
                full = store.get_by_investigation(str(latest.get("investigation_id")))
                if full is not None and full.get("status") in active:
                    recs.append(full)
        self._active_cache = (now, recs)
        return recs

    def current_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        """저장소 최신 레코드 + 허브 진척 키(미종결이면 `progress`·`partial_briefing`, 종결이면
        `progress`만 · 저장 안 함 — 종전 `POLL_LIVE` 자리)."""
        investigation_id = str(rec.get("investigation_id") or "")
        store = self._store_factory()
        stored = store.get_by_investigation(investigation_id) if store is not None else None
        current = stored or rec
        entry = self._entries.get(investigation_id)
        if entry is None:
            return current
        if current.get("status") in _active_statuses():
            base = (
                {**current, "status": _STATUS_RUNNING}
                if entry.status == _STATUS_RUNNING else current
            )
            return {**base, **entry.live} if entry.live else base
        if isinstance(entry.live.get("progress"), dict):
            return {**current, "progress": entry.live["progress"]}
        return current

    def summary_item(
        self,
        rec: Mapping[str, Any],
        *,
        operator: bool,
        mask_fn: Callable[[str], str] | None,
    ) -> dict[str, Any]:
        """목록(진행 중) 한 행 — 상태·phase·대기 순번·진척·경과(사용자: 대상 마스킹·요청자 없음)."""
        investigation_id = str(rec.get("investigation_id") or "")
        entry = self._entries.get(investigation_id)
        state = entry.state if entry is not None else {}
        item: dict[str, Any] = {
            "investigation_id": investigation_id,
            "kind": rec.get("kind") or "alarm",
            "origin": rec.get("origin"),
            "status": (entry.status if entry is not None and entry.status else rec.get("status")),
            "phase": state.get("phase"),
            "queue_position": state.get("queue_position"),
            "elapsed_s": state.get("elapsed_s"),
            "progress": entry.live.get("progress") if entry is not None else None,
            "engines": _engines_of(entry),
            "next_seq": entry.cursor if entry is not None else 0,
            "poll_error": entry.poll_error if entry is not None else None,
            "updated_at": rec.get("ts"),
        }
        subject = rec.get("subject")
        if subject:
            item["subject"] = subject if operator else _mask_value(subject, mask_fn)
        if operator:
            item["alarm_ids"] = list(rec.get("alarm_ids") or [])
            if rec.get("owner_sub"):
                item["owner_sub"] = rec.get("owner_sub")
        return item

    def list_items(
        self,
        recs: Iterable[Mapping[str, Any]],
        *,
        operator: bool,
        mask_fn: Callable[[str], str] | None,
    ) -> list[dict[str, Any]]:
        """미종결 목록 — 허브가 종결을 본 조사는 뺀다(저장소 캐시가 늦어도 목록이 앞선다)."""
        out: list[dict[str, Any]] = []
        for rec in recs:
            entry = self._entries.get(str(rec.get("investigation_id") or ""))
            if entry is not None and entry.final:
                continue
            out.append(self.summary_item(rec, operator=operator, mask_fn=mask_fn))
        return out

    def activity_payload(
        self,
        entry: LiveEntry,
        since: int,
        *,
        operator: bool,
        mask_fn: Callable[[str], str] | None,
    ) -> dict[str, Any]:
        """조회 경로(`activity_since`) 응답에 더할 키 — `activity`와 `STATE_KEYS`."""
        events = entry.events_after(since)
        return {
            "activity": {
                "next_seq": entry.cursor,
                "truncated": entry.truncated,
                "source": entry.source,
                "events": events if operator else user_events(events, mask_fn),
            },
            **{key: entry.state.get(key) for key in STATE_KEYS},
        }

    # ── 구독 ────────────────────────────────────────────────────────────────

    def set_interest(self, token: object, recs: Mapping[str, dict[str, Any]]) -> None:
        """스트림 1개의 관심 조사 집합을 바꾼다 — 새로 든 조사는 버퍼·폴러를 띄운다."""
        now = self.clock()
        new = set(recs)
        old = self._interest.get(token, set())
        for investigation_id in old - new:
            self._release_one(investigation_id, now)
        for investigation_id in new - old:
            self._ensure_entry(recs[investigation_id]).subscribers += 1
        if new:
            self._interest[token] = new
        else:
            self._interest.pop(token, None)
        for investigation_id in new:
            entry = self._entries.get(investigation_id)
            if entry is not None:
                self._ensure_running(entry)

    def release(self, token: object) -> None:
        """스트림 종료 — 관심을 모두 거둔다(동기 — 제너레이터 finally에서 부른다)."""
        self.set_interest(token, {})

    async def snapshot(self, rec: dict[str, Any]) -> LiveEntry:
        """조회 경로(`activity_since`)용 — 버퍼가 없으면 1회 읽고, 미종결이면 폴러를 띄운다.

        폴러는 접촉(`touched_at`) 뒤 `idle_stop_seconds` 동안 돈다. 종결 조사는 sre_agent에 1회
        묻고(잡 TTL 안이면 발췌까지), 잡이 없거나 실패하면 요약 JSONL로 답한다.
        """
        now = self.clock()
        self.sweep(now)
        entry = self._ensure_entry(rec)
        entry.touched_at = now
        if entry.polled_at is None and not entry.final:
            await self._guarded_poll(entry)
        self._ensure_running(entry)
        return entry

    async def wait_changed(self, version: int, timeout: float) -> None:
        """허브 상태가 `version` 뒤로 바뀌거나 `timeout`이 지날 때까지 기다린다."""
        if self.version != version:
            return
        event = self._changed
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return

    def sweep(self, now: float | None = None) -> None:
        """구독·폴러가 없고 TTL이 지난 버퍼를 지운다(키 만료 sweep)."""
        now = self.clock() if now is None else now
        for investigation_id, entry in list(self._entries.items()):
            if entry.subscribers > 0 or entry.running:
                continue
            ref = entry.final_at if entry.final and entry.final_at is not None else entry.touched_at
            if now - ref >= self.buffer_ttl_seconds:
                del self._entries[investigation_id]

    async def aclose(self) -> None:
        """앱 종료 — 폴러를 모두 취소하고 버퍼를 비운다."""
        self._closed = True
        tasks = [e.task for e in self._entries.values() if e.task is not None and e.running]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (Exception, asyncio.CancelledError):  # noqa: BLE001 — 종료 정리
                pass
        self._entries.clear()
        self._interest.clear()

    # ── 내부 ────────────────────────────────────────────────────────────────

    def _notify(self) -> None:
        self.version += 1
        self._changed.set()
        self._changed = asyncio.Event()

    def _ensure_entry(self, rec: Mapping[str, Any]) -> LiveEntry:
        investigation_id = str(rec.get("investigation_id") or "")
        entry = self._entries.get(investigation_id)
        if entry is None:
            entry = LiveEntry(
                investigation_id=investigation_id,
                rec=dict(rec),
                status=str(rec.get("status") or ""),
                touched_at=self.clock(),
            )
            self._entries[investigation_id] = entry
        return entry

    def _release_one(self, investigation_id: str, now: float) -> None:
        entry = self._entries.get(investigation_id)
        if entry is None:
            return
        entry.subscribers = max(0, entry.subscribers - 1)
        if entry.subscribers == 0:
            entry.touched_at = now

    def _ensure_running(self, entry: LiveEntry) -> None:
        if entry.final or entry.running or self._closed:
            return
        entry.task = asyncio.create_task(
            self._run(entry), name=f"investigation-live:{entry.investigation_id}"
        )

    async def _run(self, entry: LiveEntry) -> None:
        """폴러 — 구독이 있고 미종결인 동안 간격마다 1회 묻는다."""
        while not entry.final and not self._closed:
            if (
                entry.subscribers <= 0
                and self.clock() - entry.touched_at >= self.idle_stop_seconds
            ):
                break
            await self._guarded_poll(entry)
            if entry.final:
                break
            await asyncio.sleep(self.poll_seconds)
        self._notify()

    async def _guarded_poll(self, entry: LiveEntry) -> None:
        """1회 poll — 예상 밖 예외도 그 조사에만 실패로 알리고 폴러는 계속 돈다(침묵 금지)."""
        try:
            await self._poll_once(entry)
        except Exception as exc:  # noqa: BLE001 — 폴러를 멈추지 않는다
            logger.warning(
                "조사 실시간 허브 처리 실패: inv=%s", entry.investigation_id, exc_info=True
            )
            self._set_error(entry, f"허브 처리 실패: {type(exc).__name__}")

    async def _poll_once(self, entry: LiveEntry) -> None:
        async with entry.lock:
            if entry.final:
                return
            now = self.clock()
            if entry.polled_at is not None and now - entry.polled_at < self.poll_seconds * 0.5:
                return  # 조회 경로의 즉시 읽기 직후 — 같은 주기에 두 번 묻지 않는다
            entry.polled_at = now
            was_active = entry.rec.get("status") in _active_statuses()
            res, reason = await investigation_poll.fetch_poll(
                self._ng, entry.investigation_id, activity_since=max(entry.cursor, 0)
            )
            if res is None:
                if was_active:
                    self._set_error(entry, reason or "알 수 없는 사유")
                else:
                    self._finish_from_archive(entry)
                return
            self._clear_error(entry)
            status = str(res.get("status") or "")
            if status not in investigation_poll.TERMINAL_POLL_STATUSES:
                self._absorb(entry, res)
                if status and entry.status != _STATUS_RUNNING and was_active:
                    entry.status = _STATUS_RUNNING
                    entry.state_version += 1
                self._notify()
                return
            if not was_active:
                if status == "not_found":
                    self._finish_from_archive(entry)
                    return
                self._absorb(entry, res, terminal=True)
                self._finish(entry, entry.rec)
                # 아무도 안 볼 때 끝난 조사도 잡 TTL 안에 처음 읽으면 요약을 남긴다
                # (이미 보관한 조사는 건너뜀).
                self._archive(entry)
                return
            self._absorb(entry, res, terminal=True)
            # 종결을 보면 마지막 1회를 더 읽는다(종결 직전에 붙은 이벤트까지 받는다).
            tail, _ = await investigation_poll.fetch_poll(
                self._ng, entry.investigation_id, activity_since=max(entry.cursor, 0)
            )
            if tail is not None:
                self._absorb(entry, tail, terminal=True)
            store = self._store_factory()
            stored = (
                investigation_poll.persist_terminal(
                    store, entry.rec, entry.investigation_id, res, status
                )
                if store is not None
                else {**entry.rec, "status": status}
            )
            self._finish(entry, stored)
            self._archive(entry)

    def _absorb(
        self, entry: LiveEntry, res: dict[str, Any], *, terminal: bool = False
    ) -> None:
        """poll 응답의 실행 기록·진행 상태·진척 키를 버퍼에 반영한다(키가 없으면 그대로)."""
        activity = res.get("activity")
        if isinstance(activity, dict):
            entry.activity_seen = True
            if activity.get("truncated") is True:
                entry.truncated = True
            raw = activity.get("events")
            if isinstance(raw, list):
                for ev in sorted((e for e in raw if _valid_event(e)), key=lambda e: e["seq"]):
                    if ev["seq"] <= entry.cursor:
                        continue
                    entry.events.append(_clip_event(ev))
                    entry.cursor = ev["seq"]
                overflow = len(entry.events) - ACTIVITY_MAX_EVENTS
                if overflow > 0:
                    del entry.events[:overflow]
                    entry.truncated = True
        state = {key: res.get(key) for key in STATE_KEYS if key in res}
        if state and state != {key: entry.state.get(key) for key in state}:
            entry.state = {**entry.state, **state}
            entry.state_version += 1
        live = investigation_poll.live_fields(res)
        if terminal:
            # 종결 뒤에는 진척만 남긴다 — 부분 브리핑은 종결 브리핑으로 대체된다(종전 poll과 같다).
            progress = live.get("progress", entry.live.get("progress"))
            live = {"progress": progress} if isinstance(progress, dict) else {}
        elif live.get("partial_briefing") != entry.live.get("partial_briefing"):
            entry.partial_version += 1
        if live.get("progress") != entry.live.get("progress"):
            entry.state_version += 1
        entry.live = live

    def _finish(self, entry: LiveEntry, stored: Mapping[str, Any]) -> None:
        entry.rec = dict(stored)
        entry.status = str(stored.get("status") or entry.status)
        entry.final = True
        entry.final_at = self.clock()
        entry.state_version += 1
        self._notify()

    def _finish_from_archive(self, entry: LiveEntry) -> None:
        """잡이 없거나(TTL 경과) 읽기 실패한 종결 조사 — 요약 JSONL로 버퍼를 채운다."""
        summary = (
            self._activity_store.get_summary(entry.investigation_id)
            if self._activity_store is not None else None
        )
        if summary is not None:
            events = sorted(
                (e for e in summary.get("events") or [] if _valid_event(e)),
                key=lambda e: e["seq"],
            )
            entry.events = events
            entry.cursor = events[-1]["seq"] if events else 0
            entry.truncated = bool(summary.get("truncated"))
            entry.from_archive = True
        self._finish(entry, entry.rec)

    def _archive(self, entry: LiveEntry) -> None:
        """종결 요약 1행(G-37) — 실행 기록을 받은 조사만 · 조사당 1회(파일에 있으면 건너뜀)."""
        if entry.archived or not entry.activity_seen or self._activity_store is None:
            return
        entry.archived = True
        if self._activity_store.get_summary(entry.investigation_id) is not None:
            return  # 이전 프로세스·이전 버퍼가 이미 보관했다 — 조사당 1행
        ok = self._activity_store.record_summary(
            investigation_id=entry.investigation_id,
            status=entry.status,
            events=entry.events,
            truncated=entry.truncated,
        )
        if not ok:
            logger.warning("조사 실행 기록 요약 보관 실패: inv=%s", entry.investigation_id)

    def _set_error(self, entry: LiveEntry, reason: str) -> None:
        message = f"{POLL_ERROR_PREFIX}{reason}"
        if entry.poll_error != message:
            entry.poll_error = message
            entry.error_version += 1
        self._notify()

    def _clear_error(self, entry: LiveEntry) -> None:
        if entry.poll_error is not None:
            entry.poll_error = None
            entry.error_version += 1


# ── SSE ─────────────────────────────────────────────────────────────────────


@dataclass
class StreamContext:
    """스트림 1개의 권한·커서 맥락(라우트가 연결 때 권한을 검사해 만든다)."""

    operator: bool
    mask_fn: Callable[[str], str] | None
    in_scope: Callable[[dict[str, Any]], bool]
    watch: dict[str, dict[str, Any]]
    cursors: dict[str, int]
    denied: list[dict[str, Any]] = field(default_factory=list)


def parse_watch(raw: str | None) -> dict[str, int]:
    """`<id>:<seq>,<id>` → {id: seq}(seq 생략·해석 불가 = -1 · 같은 id는 큰 커서)."""
    out: dict[str, int] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        investigation_id, sep, seq_text = part.rpartition(":")
        if not sep:
            investigation_id, seq = part, -1
        else:
            try:
                seq = max(int(seq_text), -1)
            except ValueError:
                seq = -1
        if not investigation_id:
            continue
        out[investigation_id] = max(out.get(investigation_id, -1), seq)
    return out


def merge_last_event_id(cursors: dict[str, int], last_event_id: str | None) -> None:
    """`Last-Event-ID`(같은 꼴)의 커서를 `watch`에 든 조사에만 반영한다(큰 값이 이긴다)."""
    for investigation_id, seq in parse_watch(last_event_id).items():
        if investigation_id in cursors:
            cursors[investigation_id] = max(cursors[investigation_id], seq)


def cursor_key(cursors: Mapping[str, int]) -> str:
    """SSE `id:` 값 — `watch`와 같은 꼴이라 재연결 때 그대로 이어 받는다."""
    return ",".join(f"{k}:{v}" for k, v in cursors.items())


def sse(event: str, data: Any, event_id: str | None = None) -> str:
    """SSE 프레임 1개."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _watch_frames(
    hub: InvestigationLiveHub,
    ctx: StreamContext,
    investigation_id: str,
    sent: dict[str, dict[str, int]],
) -> tuple[list[str], bool]:
    """watch 조사 1건의 새 프레임(activity·partial·poll_error·done)과 종결 여부."""
    from noise_gate.infrastructure.investigation_store import playbook_user_view

    entry = hub.entry(investigation_id)
    if entry is None or (entry.polled_at is None and not entry.final):
        return [], False
    frames: list[str] = []
    since = ctx.cursors.get(investigation_id, -1)
    new = entry.events_after(since)
    if new or sent["state"].get(investigation_id) != entry.state_version:
        ctx.cursors[investigation_id] = max(since, entry.cursor)
        frames.append(sse("activity", {
            "investigation_id": investigation_id,
            "status": entry.status,
            "next_seq": entry.cursor,
            "truncated": entry.truncated,
            "source": entry.source,
            "events": new if ctx.operator else user_events(new, ctx.mask_fn),
            **{key: entry.state.get(key) for key in STATE_KEYS},
            "progress": entry.live.get("progress"),
        }, cursor_key(ctx.cursors)))
        sent["state"][investigation_id] = entry.state_version
    if entry.partial_version != sent["partial"].get(investigation_id, 0):
        sent["partial"][investigation_id] = entry.partial_version
        partial = entry.live.get("partial_briefing")
        if isinstance(partial, dict):
            view = partial if ctx.operator else playbook_user_view(partial, ctx.mask_fn or str)
            if view is not None:
                frames.append(sse("partial", {
                    "investigation_id": investigation_id, "partial_briefing": view,
                }))
    if entry.error_version != sent["error"].get(investigation_id, 0):
        sent["error"][investigation_id] = entry.error_version
        frames.append(sse("poll_error", {
            "investigation_id": investigation_id, "reason": entry.poll_error,
        }))
    if entry.final:
        frames.append(sse("done", {
            "investigation_id": investigation_id,
            "status": entry.status,
            "next_seq": entry.cursor,
        }, cursor_key(ctx.cursors)))
        return frames, True
    return frames, False


async def stream_events(
    hub: InvestigationLiveHub,
    ctx: StreamContext,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """조사 SSE 제너레이터 — `summary`(목록 범위 · 바뀔 때만) · watch 조사의 `activity`/`partial`/
    `poll_error`/`done` · 권한 없는 watch의 `denied` · 25초 `ping`.

    연결 해제·최대 수명이면 끝난다(브라우저는 `Last-Event-ID`로 이어 붙는다).
    """
    token = object()
    started = hub.clock()
    last_sent = started
    last_summary: list[dict[str, Any]] | None = None
    sent: dict[str, dict[str, int]] = {"state": {}, "partial": {}, "error": {}}
    done: set[str] = set()
    try:
        yield f"retry: {STREAM_RETRY_MS}\n\n"
        for item in ctx.denied:
            yield sse("denied", item)
        while True:
            if await is_disconnected():
                break
            if hub.clock() - started >= STREAM_MAX_SECONDS:
                break
            version = hub.version
            scoped = {
                str(rec.get("investigation_id")): rec
                for rec in hub.active_records()
                if ctx.in_scope(rec)
            }
            interest = dict(scoped)
            interest.update({k: v for k, v in ctx.watch.items() if k not in done})
            hub.set_interest(token, interest)
            frames: list[str] = []
            summary = hub.list_items(scoped.values(), operator=ctx.operator, mask_fn=ctx.mask_fn)
            if summary != last_summary:
                frames.append(sse("summary", {"items": summary}))
                last_summary = summary
            for investigation_id in ctx.watch:
                if investigation_id in done:
                    continue
                more, finished = _watch_frames(hub, ctx, investigation_id, sent)
                frames.extend(more)
                if finished:
                    done.add(investigation_id)
            if frames:
                for frame in frames:
                    yield frame
                last_sent = hub.clock()
            elif hub.clock() - last_sent >= STREAM_PING_SECONDS:
                yield sse("ping", {})
                last_sent = hub.clock()
            await hub.wait_changed(version, timeout=min(hub.poll_seconds, STREAM_PING_SECONDS))
    finally:
        hub.release(token)


__all__ = [
    "ACTIVITY_MAX_EVENTS",
    "ADMIN_ONLY_KINDS",
    "BUFFER_TTL_SECONDS",
    "IDLE_STOP_SECONDS",
    "INVESTIGATION_ID_RE",
    "InvestigationLiveHub",
    "LiveEntry",
    "POLL_ERROR_PREFIX",
    "STREAM_MAX_SECONDS",
    "STREAM_PING_SECONDS",
    "StreamContext",
    "WATCH_MAX_ITEMS",
    "cursor_key",
    "merge_last_event_id",
    "parse_watch",
    "sse",
    "stream_events",
    "user_events",
]
