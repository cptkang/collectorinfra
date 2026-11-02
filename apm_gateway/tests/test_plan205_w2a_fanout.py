"""plans/205 W2a — 도구 안 독립 API 호출 동시 실행(`application/fanout.py` · D-356 ①).

- `gather_ordered` 단위: 입력 순서 · `ApmError` 수집 · 그 밖 예외 취소·재전파 · 바깥 취소 전파 ·
  `limit=1` 태스크 없이 차례로 · 동시 상한 · 메모·가린 칸 입력 순서 재신고 · 우선순위 승격 반영 ·
  재신고 뒤 늦은 메모 · 호출 직전 훅 직렬화(승격 작업 슬롯 누수 방지).
- 바꾼 경로(#1 인스턴스 명단 · #2 실시간 · #3 X-View 바깥 · #4 시 단위 · #17·#18 · #19·#20 ·
  #34 `ManageTools._run`): `APM_FANOUT_CONCURRENCY` 4와 1의 봉투·작업 감사가 같다 — 가짜 전송이
  뒤 단위를 먼저 끝내게(지연 역순) 해서 순서 보존을 실제로 시험한다(일부 실패 · 전부 실패 포함).
- 소요 단축(느슨) · R-1(자리 대기 초과 0건 — 시간 축척 0.1).

외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.application.fanout import gather_ordered
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    CallScope,
    current_scope,
    use_scope,
)
from apm_gateway.domain.credentials import MAX_DEPTH
from apm_gateway.domain.errors import CONTRACT_VIOLATION, SOURCE_UNAVAILABLE, ApmError
from conftest import NOW_MS, NOW_S, TOKEN, synthetic_fixtures

from apm_gateway.config import JobConfig, load_config

# ── gather_ordered 단위 ─────────────────────────────────────────


class Recorder(CallScope):
    """바깥 맥락 — 훅·메모를 받은 순서대로 적는다."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, Any]] = []
        self.notes: list[str] = []
        self.masked_calls: list[list[str]] = []
        self.hook_inflight = 0
        self.hook_max = 0

    async def before_call(self, source_id: str = "") -> None:
        self.hook_inflight += 1
        self.hook_max = max(self.hook_max, self.hook_inflight)
        try:
            await asyncio.sleep(0.005)
            self.events.append(("before", source_id))
        finally:
            self.hook_inflight -= 1

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self.events.append(("count", (source_id, shared)))

    def after_call(
        self, source_id: str, seconds: float, *, group: str = "", shared: bool = False
    ) -> None:
        self.events.append(("after", (source_id, group, shared)))

    def expect_calls(self, count: int) -> None:
        self.events.append(("expect", count))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def masked(self, fields: Any) -> None:
        self.masked_calls.append(list(fields))


async def test_results_come_back_in_input_order_even_if_later_items_finish_first():
    finished: list[int] = []

    async def call(i: int) -> int:
        await asyncio.sleep((6 - i) * 0.01)
        finished.append(i)
        return i * 10

    out = await gather_ordered(range(6), call, limit=6)
    assert out == [(i, i * 10) for i in range(6)]
    assert finished != sorted(finished)  # 실제로 뒤 항목이 먼저 끝났다


@pytest.mark.parametrize("limit", [1, 4])
async def test_apm_errors_are_collected_per_item(limit):
    async def call(i: int) -> int:
        await asyncio.sleep((5 - i) * 0.002)
        if i % 2:
            raise ApmError(SOURCE_UNAVAILABLE, f"boom {i}")
        return i

    out = await gather_ordered(range(5), call, limit=limit)
    assert [u for u, _ in out] == list(range(5))
    for i, got in out:
        if i % 2:
            assert isinstance(got, ApmError) and got.reason == f"boom {i}"
        else:
            assert got == i


@pytest.mark.parametrize(
    ("limit", "stop", "called"),
    [(1, True, [0, 1, 2]), (1, False, [0, 1, 2, 3, 4]), (4, True, [0, 1, 2]),
     (4, False, [0, 1, 2, 3, 4])],
)
async def test_contract_violation_stops_new_calls(limit, stop, called):
    """계약 위반 항목까지만 부르고 그 항목까지 돌려준다(종전 루프가 그 자리에서 던지던 것과 같은
    호출 수). 동시 경로도 자리를 얻은 뒤 뒤 항목을 띄우지 않는다(여기 호출은 즉시 끝나 이미 떠난
    호출이 없다). `False`면 끝까지."""
    seen: list[int] = []

    async def call(i: int) -> int:
        seen.append(i)
        if i == 2:
            raise ApmError(CONTRACT_VIOLATION, "계약 위반")
        if i == 1:
            raise ApmError(SOURCE_UNAVAILABLE, "미접속")  # 다른 오류는 멈추지 않는다
        return i

    out = await gather_ordered(range(5), call, limit=limit, stop_on_contract_violation=stop)
    assert sorted(seen) == called
    assert [u for u, _ in out] == called
    got = dict(out)[2]
    assert isinstance(got, ApmError) and got.code == CONTRACT_VIOLATION


async def test_concurrent_contract_violation_keeps_earlier_items_and_cuts_at_first():
    """동시 경로 — 계약 위반보다 입력 순서가 앞인 항목은 끝까지 기다려 담고, 뒤에 받은 더 앞
    자리의 계약 위반이 자르는 지점을 앞당긴다. 이미 떠난 호출(2)은 거두지 않는다."""
    seen: list[int] = []

    async def call(i: int) -> int:
        seen.append(i)
        if i == 2:
            raise ApmError(CONTRACT_VIOLATION, "먼저 받은 계약 위반")
        await asyncio.sleep(0.02 if i == 0 else 0.01)
        if i == 1:
            raise ApmError(CONTRACT_VIOLATION, "앞 자리 계약 위반")
        return i

    out = await gather_ordered(range(8), call, limit=3)
    assert sorted(seen) == [0, 1, 2]
    assert [u for u, _ in out] == [0, 1]
    assert dict(out)[0] == 0
    got = dict(out)[1]
    assert isinstance(got, ApmError) and got.reason == "앞 자리 계약 위반"


async def test_other_exception_cancels_rest_and_reraises():
    cancelled: list[int] = []

    async def call(i: int) -> int:
        if i == 1:
            await asyncio.sleep(0.01)
            raise ValueError("bug")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(i)
            raise
        return i

    started = time.monotonic()
    with pytest.raises(ValueError, match="bug"):
        await gather_ordered(range(4), call, limit=4)
    assert time.monotonic() - started < 2
    assert sorted(cancelled) == [0, 2, 3]


async def test_outer_cancellation_propagates_to_items():
    cancelled: list[int] = []
    started = asyncio.Event()

    async def call(i: int) -> int:
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(i)
            raise
        return i

    task = asyncio.create_task(gather_ordered(range(3), call, limit=3))
    await started.wait()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(cancelled) == [0, 1, 2]


async def test_limit_one_runs_in_caller_task_one_after_another():
    outer_task = asyncio.current_task()
    trace: list[tuple[str, int]] = []
    tasks: list[Any] = []
    outer = Recorder()

    async def call(i: int) -> int:
        tasks.append(asyncio.current_task())
        assert current_scope() is outer  # 항목 맥락을 끼우지 않는다(현행 그대로)
        trace.append(("start", i))
        await asyncio.sleep((3 - i) * 0.005)
        trace.append(("end", i))
        return i

    with use_scope(outer):
        out = await gather_ordered(range(3), call, limit=1)
    assert out == [(0, 0), (1, 1), (2, 2)]
    assert all(t is outer_task for t in tasks)
    assert trace == [(k, i) for i in range(3) for k in ("start", "end")]


async def test_concurrency_never_exceeds_limit():
    inflight = 0
    peak = 0

    async def call(i: int) -> int:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.01)
        inflight -= 1
        return i

    await gather_ordered(range(10), call, limit=3)
    assert peak == 3


async def test_notes_and_masked_are_replayed_in_input_order():
    outer = Recorder()
    seen_during: list[int] = []

    async def call(i: int) -> int:
        await asyncio.sleep((5 - i) * 0.005)  # 뒤 항목이 먼저 신고한다
        current_scope().note(f"n{i}")
        current_scope().masked([f"f{i}"])
        seen_during.append(len(outer.notes))
        return i

    with use_scope(outer):
        await gather_ordered(range(5), call, limit=5)
    assert seen_during == [0] * 5  # 끝나기 전에는 바깥에 닿지 않는다
    assert outer.notes == [f"n{i}" for i in range(5)]
    assert outer.masked_calls == [[f"f{i}"] for i in range(5)]


async def test_priority_promotion_is_seen_by_running_items():
    outer = Recorder()
    gate = asyncio.Event()
    seen: list[str] = []

    async def call(i: int) -> int:
        seen.append(current_scope().priority)
        await gate.wait()
        seen.append(current_scope().priority)
        return i

    with use_scope(outer):
        task = asyncio.create_task(gather_ordered(range(3), call, limit=3))
        await asyncio.sleep(0.01)
        outer.priority = PRIORITY_BACKGROUND  # 작업 승격
        gate.set()
        await task
    assert seen[:3] == [PRIORITY_INTERACTIVE] * 3
    assert seen[3:] == [PRIORITY_BACKGROUND] * 3


async def test_hooks_reach_outer_scope():
    outer = Recorder()

    async def call(i: int) -> int:
        scope = current_scope()
        assert scope is not outer  # 항목별 맥락
        scope.expect_calls(1)
        await scope.before_call(f"s{i}")
        scope.after_call(f"s{i}", 0.1, group="/g")
        return i

    with use_scope(outer):
        await gather_ordered(range(4), call, limit=4)
    kinds = [k for k, _ in outer.events]
    assert kinds.count("expect") == 4 and kinds.count("before") == 4
    assert kinds.count("after") == 4
    # 훅은 묶음에서 줄 세우지 않는다 — 슬롯 받기 줄 세우기는 작업(`JobManager.wait_slot`)이 한다
    assert outer.hook_max > 1


async def test_late_note_after_replay_goes_straight_to_outer():
    outer = Recorder()
    late = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    async def noter() -> None:
        await late.wait()
        current_scope().note("late")

    async def call(i: int) -> int:
        if i == 0:  # 공유 적재처럼 항목 맥락을 물려받은 채 더 오래 사는 태스크
            background.append(asyncio.create_task(noter()))
        current_scope().note(f"n{i}")
        return i

    with use_scope(outer):
        await gather_ordered(range(2), call, limit=2)
    assert outer.notes == ["n0", "n1"]
    late.set()
    await background[0]
    assert outer.notes == ["n0", "n1", "late"]


async def test_promoted_queued_job_takes_one_slot_with_concurrent_items(tmp_path):
    """승격돼 슬롯을 기다리는 작업의 동시 항목이 슬롯을 하나만 받는다(누수·교착 없음)."""
    jcfg = JobConfig(spool_dir=tmp_path / "spool", max_concurrent=1)
    jobs = JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=lambda tool, rows, **kw: {"rows": rows, **kw},
        error_envelope=lambda tool, code, reason: {"error": code, "reason": reason},
    )
    release_a = asyncio.Event()
    go_b = asyncio.Event()
    holder: dict[str, Any] = {}

    async def job_a() -> dict[str, Any]:
        await release_a.wait()
        return {"rows": []}

    async def job_b() -> dict[str, Any]:
        await go_b.wait()

        async def call(i: int) -> int:
            await current_scope().before_call("s")
            await asyncio.sleep(0.005)
            return i

        await gather_ordered(range(4), call, limit=4)
        return {"rows": []}

    await jobs.execute("a", job_a, principal="p", wait_seconds=0)
    await jobs.execute(
        "b", job_b, principal="p", wait_seconds=0, on_job=lambda j: holder.setdefault("b", j)
    )
    assert jobs.summary()["slots_in_use"] == 1
    go_b.set()
    await asyncio.sleep(0.02)
    release_a.set()
    await asyncio.wait_for(holder["b"].task, 5)
    assert jobs.summary()["slots_in_use"] == 0
    await jobs.aclose()


def _slot_jobs(tmp_path) -> JobManager:
    jcfg = JobConfig(spool_dir=tmp_path / "spool", max_concurrent=1)
    return JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=lambda tool, rows, **kw: {"rows": rows, **kw},
        error_envelope=lambda tool, code, reason: {"error": code, "reason": reason},
    )


async def test_promoted_job_direct_concurrent_before_calls_take_one_slot(tmp_path):
    """묶음을 거치지 않고 같은 작업 맥락에서 호출 직전 훅을 동시에 불러도(우회 경로) 슬롯은
    하나만 받고 작업이 끝나면 돌려준다 — 작업별 잠금 뒤 다시 검사(`wait_slot`)."""
    jobs = _slot_jobs(tmp_path)
    release_a = asyncio.Event()
    go_b = asyncio.Event()
    holder: dict[str, Any] = {}
    in_use: list[int] = []

    async def job_a() -> dict[str, Any]:
        await release_a.wait()
        return {"rows": []}

    async def job_b() -> dict[str, Any]:
        await go_b.wait()
        scope = current_scope()
        await asyncio.gather(scope.before_call("s"), scope.before_call("s"))
        in_use.append(jobs.summary()["slots_in_use"])
        return {"rows": []}

    await jobs.execute("a", job_a, principal="p", wait_seconds=0)
    await jobs.execute(
        "b", job_b, principal="p", wait_seconds=0, on_job=lambda j: holder.setdefault("b", j)
    )
    go_b.set()
    await asyncio.sleep(0.02)
    assert holder["b"].state == "queued"
    release_a.set()
    await asyncio.wait_for(holder["b"].task, 5)
    assert in_use == [1]
    assert holder["b"].api_calls == 2
    assert jobs.summary()["slots_in_use"] == 0
    await jobs.aclose()


async def test_cancelled_job_waiting_for_slot_with_concurrent_items_leaks_nothing(tmp_path):
    """슬롯을 기다리던 동시 항목들이 작업 취소로 함께 끊겨도 슬롯이 새지 않는다."""
    jobs = _slot_jobs(tmp_path)
    release_a = asyncio.Event()
    go_b = asyncio.Event()
    holder: dict[str, Any] = {}

    async def job_a() -> dict[str, Any]:
        await release_a.wait()
        return {"rows": []}

    async def job_b() -> dict[str, Any]:
        await go_b.wait()

        async def call(i: int) -> int:
            await current_scope().before_call("s")
            return i

        await gather_ordered(range(4), call, limit=4)
        return {"rows": []}

    await jobs.execute("a", job_a, principal="p", wait_seconds=0)
    await jobs.execute(
        "b", job_b, principal="p", wait_seconds=0, on_job=lambda j: holder.setdefault("b", j)
    )
    go_b.set()
    await asyncio.sleep(0.02)
    holder["b"].task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder["b"].task
    release_a.set()
    await asyncio.sleep(0.02)
    assert jobs.summary()["slots_in_use"] == 0
    await jobs.aclose()


# ── 가짜 제니퍼(소스 2 × 도메인 N · 지연 역순) ────────────────────────

HOSTS = ("bank.test", "common.test")
XVIEW = "/api/transaction/time"
NOT_CONNECTED = {"exception": {"message": "Domain is not connected"}}


def _deep(tag: str) -> Any:
    value: Any = "x"
    for _ in range(MAX_DEPTH + 2):
        value = {tag: value}
    return value


def _templates() -> dict[str, list[tuple[str, Any]]]:
    out: dict[str, list[tuple[str, Any]]] = {}
    for fx in synthetic_fixtures():
        out.setdefault(fx["request"]["template"], []).append(
            (fx["label"], fx["response"].get("body_json"))
        )
    return out


class Fake:
    """합성 응답을 (소스, 도메인)별로 바꿔 준다. 인스턴스 id = 도메인×100 + (원 id − 1000) —
    도메인마다 hostName `was-host01` 인스턴스 2개. 응답마다 깊은 중첩 칸(이름에 소스·도메인·경로)을
    넣어 자격증명 검사 메모를 하나씩 만든다(메모 순서 시험). (소스, 도메인) 순서가 뒤일수록 빨리
    답한다."""

    def __init__(self, domains: int = 3, *, unit_delay: float = 0.002) -> None:
        self.domains = list(range(1, domains + 1))
        self.unit_delay = unit_delay
        self.fixed_delay: float | None = None
        self.fail: set[tuple[str, int, str]] = set()  # (소스, 도메인, 경로) → 500
        self.fail_all: set[str] = set()  # 경로 → 전 단위 500
        self.xview_fail_from: dict[tuple[str, int], int] = {}  # 이 시작 시각 이상 조각 → 500
        self.error_first_empty: set[tuple[str, int]] = set()  # 오류 유형 첫 표기 0건
        self.account_404: set[str] = set()
        self.note_inventory = False
        self.t = _templates()
        self.inflight: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.done: dict[str, list[tuple[str, int]]] = {}

    @staticmethod
    def sid(host: str) -> str:
        return host.split(".")[0]

    def env(self, fanout: int) -> dict[str, str]:
        ids = [self.sid(h) for h in HOSTS]
        env = {
            "JENNIFER_SOURCES": '["' + '","'.join(ids) + '"]',
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
            "APM_PARSE_WORKERS": "0",
            "APM_FANOUT_CONCURRENCY": str(fanout),
        }
        for host, sid in zip(HOSTS, ids, strict=True):
            env[f"JENNIFER_{sid.upper()}_API_URL"] = f"http://{host}"
            env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = f"{TOKEN}-{sid}"
        return env

    def delay(self, sid: str, d: int) -> float:
        if self.fixed_delay is not None:
            return self.fixed_delay
        index = [self.sid(h) for h in HOSTS].index(sid)
        if not d:  # 소스 단위 경로(도메인 목록·계정) — 뒤 소스가 먼저 답한다
            return self.unit_delay * 5 * (len(HOSTS) - index)
        total = len(HOSTS) * len(self.domains)
        rank = index * len(self.domains) + d
        return self.unit_delay * (total - rank + 1)

    def _remap(self, rec: dict[str, Any], sid: str, d: int) -> dict[str, Any]:
        out = copy.deepcopy(rec)
        if "domainId" in out:
            out["domainId"] = d
        if isinstance(out.get("instanceId"), int):
            out["instanceId"] = d * 100 + (out["instanceId"] - 1000)
        return out

    def _rows(self, path: str, sid: str, d: int, label: str = "") -> list[dict[str, Any]]:
        cands = self.t.get(path, [])
        body = next((b for lb, b in cands if label and lb == label), cands[0][1])
        rows = [self._remap(r, sid, d) for r in body["result"]]
        tag = "".join(seg[:2] for seg in path.split("/")[2:])
        # 인스턴스 명단 메모는 소스 사이 적재 순서(기존 소스 동시 적재 — 시각순)를 타므로 봉투 비교
        # 시나리오에서는 끈다(명단 메모 순서는 소스 하나로 따로 본다)
        if rows and (path != "/api/instance" or self.note_inventory):
            rows[0][f"n_{sid}{d}_{tag}"] = _deep("k")
        return rows

    async def handler(self, request: httpx.Request) -> httpx.Response:
        sid = self.sid(request.url.host)
        path = request.url.path
        q = dict(request.url.params)
        if "domain_id" in q:
            d = int(q["domain_id"])
        elif path.startswith("/api-v2/environment-variable/"):
            d = int(path.rsplit("/", 1)[-1])
        else:
            d = 0
        key = XVIEW if path == XVIEW else path
        self.inflight[key] = self.inflight.get(key, 0) + 1
        self.peak[key] = max(self.peak.get(key, 0), self.inflight[key])
        try:
            await asyncio.sleep(self.delay(sid, d))
            return self._respond(sid, d, path, q)
        finally:
            self.inflight[key] -= 1
            self.done.setdefault(key, []).append((sid, d))

    def _respond(self, sid: str, d: int, path: str, q: dict[str, str]) -> httpx.Response:
        if path == "/api/domain":
            domains = [{"domainId": x, "name": f"{sid}-dom-{x}"} for x in self.domains]
            return httpx.Response(200, json={"result": domains})
        if path in self.fail_all or (sid, d, path) in self.fail:
            return httpx.Response(500, json=NOT_CONNECTED)
        if path == XVIEW:
            limit = self.xview_fail_from.get((sid, d))
            if limit is not None and int(q["start_time"]) >= limit:
                return httpx.Response(500, json=NOT_CONNECTED)
            rows = self._rows(path, sid, d)
            # txid 없는 거래(중복 제거 키에 `len(txs)`) — 조각마다 하나
            rows.append({**rows[1], "txid": "", "endTime": q["start_time"], "guid": ""})
            return httpx.Response(200, json={"result": rows})
        if path == "/api/status/application":
            rows = self._rows(path, sid, d)
            for r in rows:
                r["name"] = f"{r['name']}#{sid}{d}"
            return httpx.Response(200, json={"result": rows})
        if path == "/api/dbsearch/error":
            spelled = q.get("error_type", "")
            if (sid, d) in self.error_first_empty and spelled and "_" in spelled and not (
                spelled.startswith(("ERROR_", "WARNING_"))
            ):
                return httpx.Response(200, json={"result": []})
            return httpx.Response(200, json={"result": self._rows(path, sid, d)})
        if path == "/api/dbmetrics/instance":
            rows = self._rows(path, sid, d, q.get("metrics", ""))
            iid = int(q["instance_id"])
            for r in rows:
                r["value"] = (r.get("value") or 0) + iid
            return httpx.Response(200, json={"result": rows})
        if path in (
            "/api/instance",
            "/api/realtime/instance",
            "/api/activeService/list",
            "/api/dbsearch/event",
        ):
            return httpx.Response(200, json={"result": self._rows(path, sid, d)})
        if path.startswith("/api-v2/environment-variable/"):
            body = {
                str(d * 100 + k): {
                    "SYSTEM": {"PATH": f"/opt/{sid}/{d}/{k}", "LANG": "ko_KR.UTF-8"},
                    "JAVA": {"java.version": f"17.{d}.{k}", "db.password": "pw-secret"},
                }
                for k in (1, 2)
            }
            return httpx.Response(200, json=body)
        if path.startswith("/restapi/user/"):
            if sid in self.account_404:
                return httpx.Response(404, json={"exception": {"message": "not found"}})
            return httpx.Response(200, json={})
        return httpx.Response(404, text="no fixture")


def _tools(fake: Fake, fanout: int) -> ApmTools:
    cfg = load_config(fake.env(fanout))
    transport = httpx.MockTransport(fake.handler)
    return ApmTools(build_source_set(cfg, transport=transport), cfg, clock=lambda: NOW_S)


async def _run_job(tmp_path, tools: ApmTools, name: str, factory) -> tuple[dict, dict]:
    jcfg = JobConfig(spool_dir=tmp_path / f"spool-{uuid.uuid4().hex[:8]}")
    jobs = JobManager(Spool(jcfg.spool_dir), jcfg, envelope=tools.ok, error_envelope=tools.err)
    holder: dict[str, Any] = {}

    async def call() -> dict[str, Any]:
        try:
            return await factory(tools)
        except ApmError as e:
            return tools.err(name, e.code, e.reason)

    out = await jobs.execute(name, call, principal="t", on_job=lambda j: holder.setdefault("j", j))
    job = holder["j"]
    audit = {
        "api_calls": job.api_calls,
        "shared_calls": job.shared_calls,
        "calls_by_source": dict(job.calls_by_source),
        "expected_calls": job.expected_calls,
        "notes": list(job.notes),
        "masked": sorted(job.masked_fields),
        "progress": job.progress(),
    }
    await jobs.aclose()
    return out, audit


async def _both(tmp_path, setup, name: str, factory) -> tuple[dict, dict, Fake]:
    """동시 4와 순차 1로 같은 시나리오를 돌려 봉투·감사가 같은지 본다 → (봉투, 감사, 동시 쪽
    가짜)."""
    results = []
    fakes = []
    for fanout in (4, 1):
        fake = Fake()
        setup(fake)
        tools = _tools(fake, fanout)
        results.append(await _run_job(tmp_path, tools, name, factory))
        fakes.append(fake)
    (out4, audit4), (out1, audit1) = results
    assert out4 == out1
    assert audit4 == audit1
    return out4, audit4, fakes[0]


def _no_setup(fake: Fake) -> None:
    return None


def _assert_reordered(fake: Fake, path: str) -> None:
    """동시 쪽에서 그 경로의 호출이 실제로 겹쳤고 뒤 단위가 먼저 끝났다."""
    assert fake.peak[path] > 1, fake.peak
    done = fake.done[path]
    assert done != sorted(done), done


# ── #1 · #2 · #3 · #4 — apm_app_health(실시간 · X-View · 시 단위 · 인스턴스 명단) ──────────


async def _app_health(tools: ApmTools) -> dict:
    return await tools.apm_app_health(hostname="was-host01", lookback_minutes=11)


async def test_app_health_same_envelope_all_ok(tmp_path):
    out, audit, fake = await _both(tmp_path, _no_setup, "apm_app_health", _app_health)
    assert "error" not in out and out["rows"]
    assert len(out["rows"]) == 2 * 3 * 2  # 소스 2 × 도메인 3 × 인스턴스 2 — 명단 순서
    assert any("자격증명 검사" in x for x in out["limits"])
    assert audit["expected_calls"] == 6 + 11 * 6 + 6
    for path in ("/api/realtime/instance", XVIEW, "/api/status/application", "/api/instance"):
        _assert_reordered(fake, path)


def _app_health_partial(fake: Fake) -> None:
    fake.fail.add(("bank", 2, "/api/realtime/instance"))
    fake.fail.add(("bank", 3, XVIEW))  # 첫 조각부터 실패
    fake.xview_fail_from[("common", 1)] = NOW_MS - 11 * 60_000 + 60_000  # 두 번째 조각부터
    fake.fail.add(("common", 3, "/api/status/application"))
    fake.fail.add(("common", 2, "/api/instance"))  # 인스턴스 명단 일부 도메인 조회 불가


async def test_app_health_same_envelope_partial(tmp_path):
    out, audit, _ = await _both(tmp_path, _app_health_partial, "apm_app_health", _app_health)
    assert out["partial"] is True
    fails = [x for x in out["limits"] if "조회 실패" in x]
    assert len(fails) == 4
    # 조각 실패 뒤 남은 조각은 묻지 않는다(묶음 안 순차 유지) — 동시·순차 같은 호출 수
    assert audit["api_calls"] < 6 + 11 * 6 + 6


def _app_health_all_fail(fake: Fake) -> None:
    fake.fail_all |= {"/api/realtime/instance", XVIEW, "/api/status/application"}


async def test_app_health_same_error_envelope_when_all_fail(tmp_path):
    out, _, _ = await _both(tmp_path, _app_health_all_fail, "apm_app_health", _app_health)
    assert out["error"] == SOURCE_UNAVAILABLE
    assert "실시간 조회 실패" in out["reason"]


async def test_resolver_inventory_keeps_domain_order(tmp_path):
    invs = []
    for fanout in (4, 1):
        fake = Fake(domains=5)
        fake.note_inventory = True
        fake.fail.add(("bank", 4, "/api/instance"))
        tools = _tools(fake, fanout)
        origin = Recorder()
        with use_scope(origin):  # 공유 적재 맥락(SharedLoadScope)이 바깥 맥락이 된다
            inv = await tools.sources.get("bank").resolver.inventory()
        invs.append((inv.instances, inv.unavailable, origin.notes))
        if fanout == 4:
            _assert_reordered(fake, "/api/instance")
            assert [k for k, v in origin.events if k == "count" and v[1]]  # 공유 적재 호출로 센다
    assert invs[0] == invs[1]
    assert [i["domain_id"] for i in invs[0][0]] == [1, 1, 1, 2, 2, 2, 3, 3, 3, 5, 5, 5]
    assert list(invs[0][1]) == [4]
    assert [n[n.index("n_bank") : n.index("n_bank") + 7] for n in invs[0][2]] == [
        f"n_bank{d}" for d in (1, 2, 3, 5)
    ]


# ── #17 · #18 — 런타임 추세 · 자원 풀 ────────────────────────────────


async def _runtime(tools: ApmTools) -> dict:
    return await tools.apm_runtime_health(hostname="was-host01", lookback_minutes=30)


async def test_runtime_health_same_envelope(tmp_path):
    out, audit, fake = await _both(tmp_path, _no_setup, "apm_runtime_health", _runtime)
    assert all("trend" in r for r in out["rows"])
    assert audit["expected_calls"] == 6 + 12 * 3
    _assert_reordered(fake, "/api/dbmetrics/instance")


def _runtime_partial(fake: Fake) -> None:
    fake.fail.add(("common", 2, "/api/dbmetrics/instance"))
    fake.fail.add(("bank", 1, "/api/realtime/instance"))


async def test_runtime_health_same_envelope_partial(tmp_path):
    out, _, _ = await _both(tmp_path, _runtime_partial, "apm_runtime_health", _runtime)
    assert out["partial"] is True
    assert sum("추세" in x and "조회 실패" in x for x in out["limits"]) == 2 * 3


def _runtime_all_fail(fake: Fake) -> None:
    fake.fail_all |= {"/api/dbmetrics/instance", "/api/realtime/instance"}


async def test_runtime_health_same_error_when_all_fail(tmp_path):
    out, _, _ = await _both(tmp_path, _runtime_all_fail, "apm_runtime_health", _runtime)
    assert out["error"] == SOURCE_UNAVAILABLE


async def _pool(tools: ApmTools) -> dict:
    return await tools.apm_resource_pool(hostname="was-host01")


def _pool_partial(fake: Fake) -> None:
    fake.fail.add(("bank", 3, "/api/activeService/list"))
    fake.fail.add(("common", 1, "/api/activeService/list"))


def _pool_all_fail(fake: Fake) -> None:
    fake.fail_all |= {"/api/activeService/list", "/api/realtime/instance"}


@pytest.mark.parametrize("setup", [_no_setup, _pool_partial, _pool_all_fail])
async def test_resource_pool_same_envelope(tmp_path, setup):
    out, _, fake = await _both(tmp_path, setup, "apm_resource_pool", _pool)
    if setup is _pool_all_fail:
        assert out["error"] == SOURCE_UNAVAILABLE
    elif setup is _pool_partial:
        assert out["partial"] is True
    else:
        _assert_reordered(fake, "/api/activeService/list")


# ── #19 · #20 — 액티브 서비스 · 이벤트·오류 기록 ──────────────────────


async def _active(tools: ApmTools) -> dict:
    return await tools.apm_active_services(hostname="was-host01", full=True)


@pytest.mark.parametrize("setup", [_no_setup, _pool_partial, _pool_all_fail])
async def test_active_services_same_envelope(tmp_path, setup):
    out, _, fake = await _both(tmp_path, setup, "apm_active_services", _active)
    if setup is _pool_all_fail:
        assert out["error"] == SOURCE_UNAVAILABLE
    elif setup is _pool_partial:
        assert out["partial"] is True and out["rows"]
    else:
        _assert_reordered(fake, "/api/activeService/list")


async def _events(tools: ApmTools) -> dict:
    return await tools.apm_events(hostname="was-host01", record="error", full=True)


async def _events_typed(tools: ApmTools) -> dict:
    return await tools.apm_events(
        hostname="was-host01", record="error", error_type="SQL_EXCEPTION", full=True
    )


def _events_partial(fake: Fake) -> None:
    fake.fail.add(("bank", 2, "/api/dbsearch/event"))
    fake.fail.add(("common", 3, "/api/dbsearch/error"))
    fake.error_first_empty |= {("bank", 1), ("common", 2)}


def _events_all_fail(fake: Fake) -> None:
    fake.fail_all |= {"/api/dbsearch/event", "/api/dbsearch/error"}


@pytest.mark.parametrize("setup", [_no_setup, _events_partial, _events_all_fail])
@pytest.mark.parametrize("factory", [_events, _events_typed])
async def test_events_same_envelope(tmp_path, setup, factory):
    out, audit, fake = await _both(tmp_path, setup, "apm_events", factory)
    if setup is _events_all_fail:
        assert out["error"] == SOURCE_UNAVAILABLE
        return
    if setup is _events_partial:
        assert out["partial"] is True
        if factory is _events_typed:  # 표기 후보 고지가 묶음 순서로 실린다
            spelled = [x for x in out["limits"] if "오류 유형 API 표기" in x]
            assert len(spelled) == 5 and audit["expected_calls"] == 12 + 2
    else:
        _assert_reordered(fake, "/api/dbsearch/event")
        _assert_reordered(fake, "/api/dbsearch/error")


# ── #34 — ManageTools._run(apm_environment · apm_users) ─────────────────


async def _environment(tools: ApmTools) -> dict:
    return await ManageTools(tools).apm_environment(hostname="was-host01")


def _env_partial(fake: Fake) -> None:
    fake.fail |= {
        ("bank", 1, "/api-v2/environment-variable/1"),
        ("common", 3, "/api-v2/environment-variable/3"),
    }


def _env_all_fail(fake: Fake) -> None:
    for sid in ("bank", "common"):
        for d in (1, 2, 3):
            fake.fail.add((sid, d, f"/api-v2/environment-variable/{d}"))


@pytest.mark.parametrize("setup", [_no_setup, _env_partial, _env_all_fail])
async def test_manage_run_same_envelope(tmp_path, setup):
    out, _, fake = await _both(tmp_path, setup, "apm_environment", _environment)
    if setup is _env_all_fail:
        assert out["error"] == SOURCE_UNAVAILABLE
    elif setup is _env_partial:
        assert out["partial"] is True
        failed = [x for x in out["limits"] if "조회 실패" in x]
        assert len(failed) == 2 and "bank" in failed[0] and "common" in failed[1]
    else:
        units = list(dict.fromkeys((r["source_id"], r["domain_id"]) for r in out["rows"]))
        assert units == [(s, d) for s in ("bank", "common") for d in (1, 2, 3)]  # 단위 순서
        _assert_reordered(fake, "/api-v2/environment-variable/1")


async def test_users_absent_keeps_source_order(tmp_path):
    def setup(fake: Fake) -> None:
        fake.account_404.add("bank")  # bank(앞 · 느림) 404 · common(뒤 · 빠름) 빈 응답

    async def users(tools: ApmTools) -> dict:
        return await ManageTools(tools).apm_users(user_id="canary")

    out, _, _ = await _both(tmp_path, setup, "apm_users", users)
    absent = next(x for x in out["limits"] if "없음:" in x)
    assert absent.index("bank") < absent.index("common"), absent


# ── 소요 단축(느슨) ───────────────────────────────────────────────


async def test_fanout_shortens_wall_time():
    """(소스, 도메인) 8묶음 × 응답 0.1s(1s 축척 0.1) — 명단은 미리 적재해 도구 호출만 잰다."""
    took = {}
    for fanout in (1, 4):
        fake = Fake(domains=4)
        fake.fixed_delay = 0.1
        tools = _tools(fake, fanout)
        await tools.resolve("was-host01", None, None, None)
        started = time.monotonic()
        out = await tools.apm_active_services(hostname="was-host01")
        took[fanout] = time.monotonic() - started
        assert "error" not in out
    print(f"\nfanout 소요: 순차 {took[1]:.2f}s · 동시 4 {took[4]:.2f}s")
    assert took[1] >= 0.75
    assert took[4] < took[1] * 0.5


# ── R-1 — 동시 작업 × 동시 항목이 소스 자리(8)를 넘어도 자리 대기 초과 0건 ─────────


async def run_r1(scale: float) -> dict[str, Any]:
    """작업 4개 × 도구당 독립 호출 12건(동시 4) — 응답 3s·5s · 자리 8 · 초당 5 · timeout 30s를
    `scale`배 시간으로. (최대 자리 대기, 초과 건수, 호출 수, 소요)."""
    cfg = load_config(
        {
            "JENNIFER_API_URL": "http://apm.test",
            "JENNIFER_API_TOKEN": TOKEN,
            "JENNIFER_RATE_LIMIT_PER_SEC": str(5 / scale),
            "JENNIFER_API_TIMEOUT_SECONDS": str(30 * scale),
            "JENNIFER_MAX_INFLIGHT_PER_SOURCE": "8",
        }
    )
    api_cfg = cfg.sources[0]
    assert api_cfg.max_inflight_per_source == 8

    async def handler(request: httpx.Request) -> httpx.Response:
        d = int(request.url.params["domain_id"])
        await asyncio.sleep((3 if d % 2 else 5) * scale)
        return httpx.Response(200, json={"result": []})

    client = JenniferClient(api_cfg, transport=httpx.MockTransport(handler))
    loop = asyncio.get_running_loop()
    waits: list[float] = []
    original = client._slots.acquire

    async def timed(priority: str = PRIORITY_INTERACTIVE, caller: str = "") -> None:
        started = loop.time()
        try:
            await original(priority, caller)
        finally:
            waits.append(loop.time() - started)

    client._slots.acquire = timed  # type: ignore[method-assign]

    async def tool_call() -> list[Any]:
        return await gather_ordered(
            range(12),
            lambda i: client.get_json("/api/realtime/instance", {"domain_id": i + 1}),
            limit=4,
        )

    started = loop.time()
    outs = await asyncio.gather(*(tool_call() for _ in range(4)))
    elapsed = loop.time() - started
    await client.aclose()
    errors = [got for out in outs for _, got in out if isinstance(got, ApmError)]
    over = [
        e for e in errors if e.code == SOURCE_UNAVAILABLE and "동시 호출 상한 대기 초과" in e.reason
    ]
    return {
        "max_slot_wait": max(waits),
        "over": len(over),
        "errors": len(errors),
        "calls": client.calls_total,
        "elapsed": elapsed,
    }


async def test_r1_no_slot_wait_timeout_with_fanout_four_times_four_jobs():
    result = await run_r1(0.1)
    print(f"\nR-1(축척 0.1): {result}")
    assert result["over"] == 0 and result["errors"] == 0
    assert result["calls"] == 48
    assert result["max_slot_wait"] < 3.0  # timeout(30s × 0.1)
