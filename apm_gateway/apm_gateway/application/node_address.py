"""노드 주소 조회기 — 캐시·조회 정책·로그인 차단기·상태 산출 (plans/202 §2.1 · D-355 ①⑦).

제니퍼 파드 인스턴스는 Open API에 파드명·파드 IP만 있어 폴스타 서버(노드)와 맞지 않는다. 웹 세션
목록(`adapters/jennifer/web_session.py`)의 노드 주소를 **알람이 난 도메인만** 조회해
`(source_id, sid)` 단위로 메모리에 둔다(전체 도메인 주기 조회 없음 · §0-14).

정책:
- **알람 경로**(`lookup`): 캐시 나이가 최소 간격(`min_refetch_seconds` · 60)을 넘으면 다시 조회한다
  (검토 C — 재스케줄로 노드가 바뀌는 시점이 장애 시점에 몰린다). 이내면 캐시를 쓴다 — 같은 주기·같은
  도메인은 1회만 호출된다(소스별 잠금 안에서 캐시를 다시 본다).
- 조회가 실패하면 TTL(`ttl_seconds` · 900) 안 캐시에 그 인스턴스가 있으면 그 값과 상태 `stale`.
  `stale`은 소비자가 자동 연결 근거로 쓰지 않는다(D-355 ⑦). TTL은 이 상한과 MCP 표면에만 쓴다.
- **도메인 종류 기억**(검토 A): 마지막 성공 조회의 종류(`pod`·`vm`·`mixed`)를 TTL 없이 둔다. 행으로
  판정하지 못할 때(조회 실패·`not_found`) 종류가 `pod`이면 `pod = true`, 그 밖에는 `pod` 키를 싣지
  않는다(VM 회귀 방지).
- **로그인 차단기**: 소스별 연속 로그인 실패 3회 → 30분 정지(`login_blocked` · 네트워크 호출 0).
  조회가 성공하면 초기화한다. 정지 로그에 「비밀번호 변경·초기화 여부 확인」을 안내한다.
- 형태 오류(배열 아님 · 인스턴스 id 없음)는 `error`이고 캐시를 갱신하지 않는다.
- **MCP 표면 일괄 조회**(`domain_snapshots` · plans/210 W9 A): 소스마다 작업 1개(그 안은 직렬 — 소스
  잠금·웹 세션 잠금 그대로 · 뷰서버 부하 소스당 동시 1 불변 · D-355 부기 4) · 소스 간만 병렬. 호출
  시간 예산이 끝나면 그 시점에 돌려준다 — 못 본 도메인은 TTL 안 캐시, 없으면 `budget_exceeded`.
  이미 웹 조회 중인 도메인 1건은 취소하지 않고 뒤에서 끝까지 돌린다(결과가 캐시에 남거나 전송
  실패·로그인 실패가 종전대로 기록되어 다음 호출이 이어받는다 · 그 소스의 다음 도메인은 시작하지
  않는다). 취소하는 것은 잠금 대기·미시작 작업뿐이라 실패 기록에 닿지 않는다.

상태 어휘(`alarm:raw` `apm.host_address_status`): `ok` · `stale` · `not_found`(목록에 인스턴스
없음) · `disabled`(소스 웹 계정 미설정) · `login_failed` · `login_blocked` · `error`. MCP 표면만
`budget_exceeded`(호출 시간 예산 안에 조회하지 못함 · 캐시도 없음)를 더 쓴다.

로그에는 소스·도메인·상태·예외 형 이름만 남긴다(비밀번호·쿠키·응답 본문 없음).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from apm_gateway.adapters.jennifer.web_fields import KIND_POD, domain_kind, is_pod_row
from apm_gateway.adapters.jennifer.web_session import (
    WebNotAllowedError,
    WebSession,
    WebSessionError,
    WebTransportError,
)
from apm_gateway.config import GatewayConfig

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_NOT_FOUND = "not_found"
STATUS_DISABLED = "disabled"
STATUS_LOGIN_FAILED = "login_failed"
STATUS_LOGIN_BLOCKED = "login_blocked"
STATUS_ERROR = "error"
# MCP 표면 전용(plans/210 W9 A) — 호출 시간 예산 안에 조회하지 못했고 TTL 안 캐시도 없다
STATUS_BUDGET_EXCEEDED = "budget_exceeded"

LOGIN_FAILURE_LIMIT = 3
LOGIN_BLOCK_SECONDS = 30 * 60
# 알람 경로 조회 대기 상한(폴러의 이벤트 처리를 막지 않는다 — 넘으면 `error`로 싣고 진행)
LOOKUP_TIMEOUT_SECONDS = 5.0
# 60초 안 캐시에 없는 인스턴스(재기동 직후 새 파드)의 재조회 하한(초) — 실패 간격이 우선한다
NOT_FOUND_REFETCH_SECONDS = 10.0
# 대상 지정 MCP 호출의 전체 웹 조회 예산(초) 기본값 — `JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS`
CALL_BUDGET_SECONDS = 5.0

# 페이로드 `apm` 블록에 가산하는 키(플래그 off면 하나도 싣지 않는다)
KEY_HOST_ADDRESS = "host_address"
KEY_STATUS = "host_address_status"
KEY_POD = "pod"


class WebLister(Protocol):
    """웹 세션의 조회 표면(테스트 대역 주입점)."""

    source_id: str

    @property
    def configured(self) -> bool: ...

    async def fetch_list(self, sid: int) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


@dataclass
class NodeLookup:
    """노드 주소 조회 결과 1건 — `payload_fields()`가 페이로드 칸으로 바꾼다."""

    status: str
    host_address: str = ""
    pod: bool | None = None
    fetched_at: float | None = None

    def payload_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {KEY_HOST_ADDRESS: self.host_address, KEY_STATUS: self.status}
        if self.pod is not None:
            out[KEY_POD] = self.pod
        return out


@dataclass
class DomainSnapshot:
    """도메인 1개의 노드 주소 행(`domain_snapshot`) — 실패면 행 없이 상태만.

    `rows`는 `{인스턴스 id: {host_address, ip, host_name, status}}`(웹 허용 칸만) · `kind`는 마지막
    성공 조회의 도메인 종류(`pod`·`vm`·`mixed` · 모르면 빈 값 — TTL 없이 기억한 값)."""

    status: str
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    kind: str = ""
    fetched_at: float | None = None


@dataclass
class _DomainEntry:
    fetched_at: float
    rows: dict[str, dict[str, Any]]


@dataclass
class _Breaker:
    failures: int = 0
    blocked_until: float = 0.0


@dataclass
class _SourceState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    breaker: _Breaker = field(default_factory=_Breaker)
    # 소스 단위 실패 간격(전송 실패·시간 초과·대기 상한 취소) — 이 시각까지 네트워크 호출 0
    backoff_until: float = 0.0


def instance_key(value: Any) -> str | None:
    """인스턴스·도메인 id 비교 키 — 정수 꼴이면 정수 문자열(1001 · "1001" 같은 키)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(int(text))
    except ValueError:
        return text


def _sid(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class NodeAddressService:
    """소스별 웹 세션을 묶는 노드 주소 조회기. 프로세스에서 하나를 공유한다(폴러·MCP 도구)."""

    def __init__(
        self,
        sessions: Mapping[str, WebLister],
        *,
        ttl_seconds: float = 900,
        min_refetch_seconds: float = 60,
        call_budget_seconds: float | None = CALL_BUDGET_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = dict(sessions)
        self.ttl_seconds = float(ttl_seconds)
        self.min_refetch_seconds = float(min_refetch_seconds)
        # 대상 지정 MCP 호출(`apm_instance_map`·`apm_node_addresses`)의 전체 예산(None = 예산 없음)
        self.call_budget_seconds = call_budget_seconds
        self.clock = clock
        self._cache: dict[tuple[str, int], _DomainEntry] = {}
        self._kinds: dict[tuple[str, int], str] = {}
        self._states: dict[str, _SourceState] = {}
        # (소스, 도메인) → (마지막 실패 시각, 실패 상태) — 실패 뒤 최소 간격 동안 재조회하지 않는다
        self._failures: dict[tuple[str, int], tuple[float, str]] = {}
        # 웹 조회(`fetch_list`) 대기 중인 작업 — 예산 종료 때 취소하지 않고 뒤에서 마저 돌린다
        self._fetching: set[asyncio.Task[Any]] = set()
        # 예산 뒤 마저 도는 소스 작업(강한 참조 — `aclose`에서 정리)
        self._background: set[asyncio.Task[Any]] = set()
        self.fetch_total = 0

    # ── 상태 ──────────────────────────────────────────────

    def _state(self, source_id: str) -> _SourceState:
        state = self._states.get(source_id)
        if state is None:
            state = self._states[source_id] = _SourceState()
        return state

    def _blocked(self, source_id: str) -> bool:
        return self.clock() < self._state(source_id).breaker.blocked_until

    def _login_failed(self, source_id: str) -> None:
        breaker = self._state(source_id).breaker
        breaker.failures += 1
        if breaker.failures >= LOGIN_FAILURE_LIMIT:
            breaker.blocked_until = self.clock() + LOGIN_BLOCK_SECONDS
            breaker.failures = 0
            logger.warning(
                "노드 주소 조회: 소스 %s 웹 로그인 연속 %d회 실패 — %d분 정지(login_blocked)."
                " 웹 계정 비밀번호 변경·초기화 여부 확인 후 .env를 갱신한다",
                source_id,
                LOGIN_FAILURE_LIMIT,
                LOGIN_BLOCK_SECONDS // 60,
            )

    def status(self) -> dict[str, Any]:
        """운영 상태(값 없음 — 소스별 계정 설정 여부·정지 여부·캐시 도메인 수)."""
        now = self.clock()
        return {
            "sources": {
                sid: {
                    "configured": session.configured,
                    "blocked": now < self._state(sid).breaker.blocked_until,
                }
                for sid, session in self._sessions.items()
            },
            "cached_domains": len(self._cache),
            "fetch_total": self.fetch_total,
        }

    # ── 조회 ──────────────────────────────────────────────

    def _fresh(self, entry: _DomainEntry | None, limit: float) -> bool:
        return entry is not None and self.clock() - entry.fetched_at <= limit

    def _held_failure(self, source_id: str, sid: int) -> str | None:
        """실패 간격 안이면 그 실패 상태(네트워크 호출 없이 돌려준다), 아니면 None."""
        now = self.clock()
        if self._blocked(source_id):
            return STATUS_LOGIN_BLOCKED
        if now < self._state(source_id).backoff_until:
            return STATUS_ERROR
        failed = self._failures.get((source_id, sid))
        if failed is not None and now - failed[0] < self.min_refetch_seconds:
            return failed[1]
        return None

    def _record_failure(self, source_id: str, sid: int, status: str, *, source_wide: bool) -> None:
        now = self.clock()
        self._failures[(source_id, sid)] = (now, status)
        if source_wide:
            self._state(source_id).backoff_until = now + self.min_refetch_seconds

    async def _fetch(self, source_id: str, sid: int) -> tuple[_DomainEntry | None, str]:
        """잠금 안에서 부른다. (새 캐시 항목 | None, 실패 상태).

        실패 간격: 로그인 거부는 차단기만 센다(3회 → 30분 정지) · 그 밖 실패는 (소스, 도메인)을
        최소 간격 동안 쉬고, 전송 실패·시간 초과·대기 상한 취소는 소스 전체를 쉰다(웹 무응답 때
        이벤트마다 상한까지 직렬로 밀리지 않게)."""
        held = self._held_failure(source_id, sid)
        if held is not None:
            return None, held
        session = self._sessions[source_id]
        self.fetch_total += 1
        task = asyncio.current_task()
        if task is not None:
            self._fetching.add(task)
        try:
            rows = await session.fetch_list(sid)
        except asyncio.CancelledError:  # 대기 상한(`lookup_fields`) — 웹 무응답으로 본다
            self._record_failure(source_id, sid, STATUS_ERROR, source_wide=True)
            raise
        except WebSessionError as e:
            if e.code == STATUS_LOGIN_FAILED:
                self._login_failed(source_id)
                return None, STATUS_LOGIN_FAILED
            logger.warning(
                "노드 주소 조회: 소스 %s 도메인 %s 실패(%s) — %s", source_id, sid, e.code, e.reason
            )
            self._record_failure(
                source_id, sid, STATUS_ERROR, source_wide=isinstance(e, WebTransportError)
            )
            return None, STATUS_ERROR
        except WebNotAllowedError as e:
            logger.error("노드 주소 조회: 허용목록 밖 요청 차단(게이트웨이 버그) — %s", e)
            self._record_failure(source_id, sid, STATUS_ERROR, source_wide=False)
            return None, STATUS_ERROR
        except ValueError as e:  # 형태 오류(`WebShapeError`) — 캐시 갱신 없음
            logger.warning(
                "노드 주소 조회: 소스 %s 도메인 %s 응답 형태 오류 — %s(웹 내부 API 변경 의심)",
                source_id,
                sid,
                e,
            )
            self._record_failure(source_id, sid, STATUS_ERROR, source_wide=False)
            return None, STATUS_ERROR
        finally:
            if task is not None:
                self._fetching.discard(task)
        state = self._state(source_id)
        state.breaker = _Breaker()
        state.backoff_until = 0.0
        self._failures.pop((source_id, sid), None)
        indexed: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = instance_key(row.get("inst_id"))
            if key is not None:
                indexed[key] = {
                    "host_address": row.get("host_address") or "",
                    "ip": row.get("ip") or "",
                    "host_name": row.get("host_name") or "",
                    "status": row.get("status"),
                }
        entry = _DomainEntry(self.clock(), indexed)
        self._cache[(source_id, sid)] = entry
        kind = domain_kind(rows)
        if kind:
            self._kinds[(source_id, sid)] = kind
        return entry, STATUS_OK

    def _from_row(self, row: dict[str, Any], status: str, fetched_at: float) -> NodeLookup:
        pod = is_pod_row(row)
        return NodeLookup(status, row.get("host_address") or "", pod, fetched_at)

    def _without_row(self, source_id: str, sid: int, status: str) -> NodeLookup:
        pod = True if self._kinds.get((source_id, sid)) == KIND_POD else None
        return NodeLookup(status, "", pod)

    async def lookup(self, source_id: str, domain_id: Any, instance_id: Any) -> NodeLookup:
        """알람 경로 조회 — 캐시 60초 초과면 재조회(캐시에 없는 인스턴스는 10초 하한) · 실패 시
        TTL 안 캐시는 `stale` · 실패 뒤 최소 간격 동안은 네트워크 없이 같은 결과."""
        session = self._sessions.get(source_id)
        if session is None or not session.configured:
            return NodeLookup(STATUS_DISABLED)
        sid = _sid(domain_id)
        inst = instance_key(instance_id)
        if sid is None or inst is None:
            return NodeLookup(STATUS_NOT_FOUND)
        state = self._state(source_id)
        async with state.lock:
            entry = self._cache.get((source_id, sid))
            if self._fresh(entry, self.min_refetch_seconds) and not self._refetch_missing(
                source_id, sid, entry, inst
            ):
                assert entry is not None
                status = STATUS_OK
            else:
                fetched, status = await self._fetch(source_id, sid)
                if fetched is not None:
                    entry = fetched
                elif self._fresh(entry, self.ttl_seconds):
                    assert entry is not None
                    row = entry.rows.get(inst)
                    if row is not None:
                        return self._from_row(row, STATUS_STALE, entry.fetched_at)
                    return self._without_row(source_id, sid, status)
                else:
                    self._cache.pop((source_id, sid), None)
                    return self._without_row(source_id, sid, status)
        row = entry.rows.get(inst)
        if row is None:
            return self._without_row(source_id, sid, STATUS_NOT_FOUND)
        return self._from_row(row, status, entry.fetched_at)

    def _refetch_missing(
        self, source_id: str, sid: int, entry: _DomainEntry | None, inst: str
    ) -> bool:
        """신선한 캐시에 인스턴스가 없을 때 재조회할까 — 하한 10초 · 실패 간격 안이면 아니다."""
        if entry is None or inst in entry.rows:
            return False
        if self.clock() - entry.fetched_at < NOT_FOUND_REFETCH_SECONDS:
            return False
        return self._held_failure(source_id, sid) is None

    async def domain_snapshot(
        self, source_id: str, domain_id: Any, *, fetch: bool
    ) -> DomainSnapshot:
        """MCP 표면용 — TTL 안 캐시의 도메인 행과 그 상태(plans/210 W2 — 상태를 버리지 않는다).
        `fetch`면 캐시 밖일 때 1회 조회한다(차단기·실패 간격 그대로). 상태: 소스 웹 계정 미설정
        `disabled` · 도메인 id 형식 밖 `error` · 조회 실패는 그 실패 상태(`login_failed`·
        `login_blocked`·`error` — 행 없음) · `fetch` 없이 캐시 밖이면 `not_found`(캐시에 없음)."""
        session = self._sessions.get(source_id)
        if session is None or not session.configured:
            return DomainSnapshot(STATUS_DISABLED)
        sid = _sid(domain_id)
        if sid is None:
            return DomainSnapshot(STATUS_ERROR)
        status = STATUS_OK
        state = self._state(source_id)
        async with state.lock:
            entry = self._cache.get((source_id, sid))
            if not self._fresh(entry, self.ttl_seconds):
                entry, status = None, STATUS_NOT_FOUND
                if fetch:
                    entry, status = await self._fetch(source_id, sid)
        kind = self._kinds.get((source_id, sid), "")
        if entry is None:
            return DomainSnapshot(status, kind=kind)
        return DomainSnapshot(STATUS_OK, dict(entry.rows), kind, entry.fetched_at)

    def cached_snapshot(self, source_id: str, domain_id: Any) -> DomainSnapshot:
        """잠금·네트워크 없이 TTL 안 캐시를 읽는다(예산 안에 끝내지 못한 도메인 · 대상 지정
        `apm_instance_map`의 상한 밖 도메인) — 없으면 `budget_exceeded`(행 없음)."""
        session = self._sessions.get(source_id)
        if session is None or not session.configured:
            return DomainSnapshot(STATUS_DISABLED)
        sid = _sid(domain_id)
        if sid is None:
            return DomainSnapshot(STATUS_ERROR)
        kind = self._kinds.get((source_id, sid), "")
        entry = self._cache.get((source_id, sid))
        if entry is None or not self._fresh(entry, self.ttl_seconds):
            return DomainSnapshot(STATUS_BUDGET_EXCEEDED, kind=kind)
        return DomainSnapshot(STATUS_OK, dict(entry.rows), kind, entry.fetched_at)

    async def domain_snapshots(
        self, pairs: Sequence[tuple[str, Any]], *, budget_seconds: float | None
    ) -> tuple[dict[tuple[str, Any], DomainSnapshot], set[tuple[str, Any]]]:
        """(소스, 도메인) 목록의 `domain_snapshot(fetch=True)` — 소스마다 작업 1개(그 안은 입력 순서
        직렬 · 잠금·차단기·실패 간격 그대로) · 소스 간 병렬(plans/210 W9 A).

        `budget_seconds`(None = 예산 없음)가 끝나면 그 시점에 돌려준다 — 끝내지 못한 쌍은 TTL 안
        캐시(상태 `ok`), 없으면 `budget_exceeded`. 웹 조회 중인 소스 작업은 그 도메인 1건만 뒤에서
        끝까지 돌리고(캐시·실패 기록은 종전대로) 다음 도메인은 시작하지 않는다 · 잠금 대기·미시작
        작업은 취소한다(`_fetch`에 닿지 않아 실패 기록 없음). 돌려주는 것: (쌍 → 스냅숏, 예산 안에
        끝내지 못한 쌍). 도메인 하나의 예상 밖 실패는 그 도메인만 `error`다(나머지는 계속)."""
        order = list(dict.fromkeys(pairs))
        by_source: dict[str, list[Any]] = {}
        for source_id, domain_id in order:
            by_source.setdefault(source_id, []).append(domain_id)
        done: dict[tuple[str, Any], DomainSnapshot] = {}
        halt = asyncio.Event()

        async def run(source_id: str, domains: list[Any]) -> None:
            for domain_id in domains:
                if halt.is_set():  # 예산 종료 — 뒤에서 마저 돈 도메인 다음은 시작하지 않는다
                    return
                try:
                    snapshot = await self.domain_snapshot(source_id, domain_id, fetch=True)
                except Exception as e:  # 조회기 결함이 나머지 도메인을 막지 않게 — 형 이름만
                    logger.warning(
                        "노드 주소 조회: 소스 %s 도메인 %s 예상 밖 실패(%s) — error",
                        source_id,
                        domain_id,
                        type(e).__name__,
                    )
                    snapshot = DomainSnapshot(STATUS_ERROR)
                done[(source_id, domain_id)] = snapshot

        tasks = [asyncio.create_task(run(s, d)) for s, d in by_source.items()]
        if tasks:
            try:
                _, pending = await asyncio.wait(tasks, timeout=budget_seconds)
            except BaseException:  # 호출자 취소 — 작업을 남기지 않는다(종전 취소 기록 그대로)
                for task in tasks:
                    task.cancel()
                raise
            halt.set()
            waiting = [task for task in pending if task not in self._fetching]
            for task in pending:
                if task in self._fetching:  # 웹 조회 중 — 뒤에서 그 도메인만 끝낸다
                    self._background.add(task)
                    task.add_done_callback(self._reap)
                else:
                    task.cancel()
            if waiting:
                await asyncio.gather(*waiting, return_exceptions=True)
        missed = [pair for pair in order if pair not in done]
        for source_id in dict.fromkeys(s for s, _ in missed):
            logger.info(
                "노드 주소 조회: 소스 %s 호출 시간 예산 %s초 안에 도메인 %d개 미조회 — 캐시 또는"
                " budget_exceeded",
                source_id,
                budget_seconds,
                sum(1 for s, _ in missed if s == source_id),
            )
        out = {
            pair: done[pair] if pair in done else self.cached_snapshot(*pair) for pair in order
        }
        return out, set(missed)

    def _reap(self, task: asyncio.Task[Any]) -> None:
        """뒤에서 마저 돈 작업 정리 — 예외는 형 이름만 남기고 소비한다."""
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "노드 주소 조회: 예산 뒤 마저 돈 조회 예상 밖 실패(%s)",
                type(task.exception()).__name__,
            )

    async def domain_addresses(
        self, source_id: str, domain_id: Any, *, fetch: bool
    ) -> dict[str, str]:
        """MCP 표면용 — TTL 안 캐시의 `{인스턴스 id: 노드 주소}`. `fetch`면 캐시 밖일 때 1회
        조회한다(실패하면 빈 매핑 — 표면 칸은 빈 값)."""
        snapshot = await self.domain_snapshot(source_id, domain_id, fetch=fetch)
        return {inst: row["host_address"] for inst, row in snapshot.rows.items()}

    async def aclose(self) -> None:
        background = list(self._background)
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        for session in self._sessions.values():
            await session.aclose()


async def lookup_fields(
    service: NodeAddressService,
    source_id: str,
    domain_id: Any,
    instance_id: Any,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """알람 페이로드 칸 — 상한(기본 `LOOKUP_TIMEOUT_SECONDS`) 안에 끝나지 않거나 예상 밖 예외면
    `error`(이벤트 처리는 계속)."""
    if timeout_seconds is None:
        timeout_seconds = LOOKUP_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(
            service.lookup(source_id, domain_id, instance_id), timeout_seconds
        )
    except TimeoutError:
        logger.warning(
            "노드 주소 조회: 소스 %s 도메인 %s 상한 %.0f초 초과 — error로 발행",
            source_id,
            domain_id,
            timeout_seconds,
        )
        return NodeLookup(STATUS_ERROR).payload_fields()
    except Exception as e:  # 조회기 결함이 알람 발행을 막지 않게 — 형 이름만 남긴다
        logger.warning(
            "노드 주소 조회: 소스 %s 예상 밖 실패(%s) — error로 발행", source_id, type(e).__name__
        )
        return NodeLookup(STATUS_ERROR).payload_fields()
    return result.payload_fields()


def build_node_address(
    cfg: GatewayConfig, *, transport: Any = None, clock: Callable[[], float] = time.time
) -> NodeAddressService | None:
    """설정에서 조회기를 만든다. 플래그 off면 None(현행 동일 — 웹 세션을 만들지 않는다)."""
    if not cfg.node_address.enabled:
        return None
    sessions: dict[str, WebLister] = {
        src.source_id: WebSession(
            source_id=src.source_id,
            url=src.web_url,
            user=src.web_user,
            password=src.web_password,
            tls_verify=src.web_tls_verify,
            transport=transport,
        )
        for src in cfg.sources
    }
    return NodeAddressService(
        sessions,
        ttl_seconds=cfg.node_address.ttl_seconds,
        min_refetch_seconds=cfg.node_address.min_refetch_seconds,
        call_budget_seconds=cfg.node_address.call_budget_seconds,
        clock=clock,
    )
