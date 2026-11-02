"""도구 안 독립 호출 동시 실행 — W2a 담당 경로(plans/205 W2 · D-356 ①).

바꾼 경로: `apm_runtime_health` 인스턴스×지표(#17) · `apm_resource_pool`(#18) ·
`apm_active_services`(#19) · `apm_events`(#20 — 안쪽 표기 후보 순차 #6은 그대로) ·
`manage_tools._run`(#34 — `apm_config`·`apm_environment`·`apm_users`) · `gather_bounded`
메모·가린 칸 입력 순서 재신고.

- ① 폭 4와 폭 1(종전 순차)의 봉투가 같다 — 행·`limits` 순서·`partial`·오류 봉투(일부 단위 실패 ·
  전부 실패 포함 · 소스 2개).
- ② 지연 가짜 전송에서 소요가 줄어든다(느슨).
- ③ 작업 감사 `api_calls`·`calls_by_source`·예상 호출 수가 같다.
- 응답이 역순으로 도착해도 자격증명 검사 메모(`limits`)·가린 칸 고지 순서가 순차와 같다.
- 위험 R-1(시간 축척 ×0.1): 자리 8 · 초당 50 · timeout 3초 · 지연 0.3/0.5초 × 작업 4개 동시 ×
  도구당 독립 호출 12건 — 자리 대기 초과(`source_unavailable` 「동시 호출 상한 대기 초과」) 0건.

외부 호출 0 — httpx `MockTransport`(지연) · 실 클라이언트·작업 관리자.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import Any

import httpx
import pytest
from apm_gateway.application.jobs import Job, JobManager
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.parallel import gather_bounded
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.call_context import CallScope, current_scope, use_scope
from apm_gateway.domain.errors import ApmError
from conftest import NOW_MS, NOW_S

from apm_gateway.config import load_config

NOT_CONNECTED = {"exception": {"message": "Domain is not connected"}}
HOST = "web-host"
SLOT_TIMEOUT = "동시 호출 상한 대기 초과"


def _deep(depth: int = 40) -> dict[str, Any]:
    """자격증명 검사 깊이 상한(32)을 넘는 중첩 — 검사가 `[한계]` 메모를 남긴다(경로가 메모 문구)."""
    node: dict[str, Any] = {"v": 1}
    for _ in range(depth):
        node = {"x": node}
    return node


class FakeApm:
    """소스(호스트)마다 도메인 1..`domains` · 도메인마다 인스턴스 `per_domain`개(id = 도메인×100+k ·
    hostName 모두 `web-host`). 경로 표지(tag)·단위 키(도메인 또는 인스턴스 id)별 실패 · 지연 ·
    동시 in-flight 측정 · 자격증명 메모 유발(`notes`)."""

    def __init__(
        self,
        domains: int = 3,
        *,
        per_domain: int = 2,
        hosts: tuple[str, ...] = ("bank.test", "common.test"),
    ) -> None:
        self.hosts = hosts
        self.domains = list(range(1, domains + 1))
        self.per_domain = per_domain
        self.fail: set[tuple[str, str, int]] = set()  # (호스트, 표지, 키)
        self.missing_account: dict[str, int] = {}  # 호스트 → 404 또는 0(빈 응답)
        self.delay: Callable[[str, str, int], float] = lambda host, tag, key: 0.0
        self.notes = False
        self.active = 0
        self.peak = 0
        self.calls: list[tuple[str, str, int]] = []

    def env(self, **extra: str) -> dict[str, str]:
        ids = [h.split(".")[0] for h in self.hosts]
        env = {"JENNIFER_SOURCES": json.dumps(ids), "JENNIFER_RATE_LIMIT_PER_SEC": "0", **extra}
        for host, sid in zip(self.hosts, ids, strict=True):
            env[f"JENNIFER_{sid.upper()}_API_URL"] = f"http://{host}"
            env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = f"tok-{sid}-SECRET"
        return env

    @staticmethod
    def _route(path: str, query: dict[str, str]) -> tuple[str, int]:
        """(표지, 단위 키) — 키는 도메인 id(인스턴스 지표는 인스턴스 id · 사용자는 0)."""
        d = int(query.get("domain_id", "0") or 0)
        fixed = {
            "/api/domain": "domain",
            "/api/instance": "instance",
            "/api/realtime/instance": "realtime",
            "/api/activeService/list": "active",
            "/api/dbsearch/event": "event",
            "/api/dbsearch/error": "error",
            "/api/auth/userlist": "users",
            "/restapi/users": "accounts",
        }
        if path in fixed:
            return fixed[path], d
        if path == "/api/dbmetrics/instance":
            return "metric", int(query["instance_id"])
        if path.startswith("/api-v2/manage/db/path/"):
            return "dbpath", int(path.rsplit("/", 1)[1])
        if path.startswith("/api-v2/environment-variable/"):
            return "env", int(path.rsplit("/", 1)[1])
        if path.startswith("/restapi/user/"):
            return "account", 0
        if path.startswith("/api-v2/loaded-class/"):
            return "classes", int(path.rsplit("/", 1)[1])
        return "unknown", d

    def _body(self, host: str, tag: str, key: int, query: dict[str, str]) -> Any:
        sid = host.split(".")[0]
        insts = [key * 100 + k for k in range(self.per_domain)]
        if tag == "domain":
            return {"result": [{"domainId": d, "name": f"{sid}-dom-{d}"} for d in self.domains]}
        if tag == "instance":
            return {
                "result": [
                    {
                        "instanceId": iid,
                        "name": f"{sid}-i{iid}",
                        "hostName": HOST,
                        "ipAddress": "10.0.0.1",
                        "platform": "JAVA",
                        "status": "RUNNING",
                        "version": "5.6.5",
                    }
                    for iid in insts
                ]
            }
        if tag == "realtime":
            recs = [
                {
                    "domainId": key,
                    "instanceId": iid,
                    "instanceName": f"{sid}-i{iid}",
                    "heapUsed": float(iid),
                    "heapCommitted": 4000.0,
                    "gcTimeUsage": 1.0,
                    "threadCurrent": 100 + iid,
                    "activeDBConnection": 3.0,
                    "averageDbPoolConfiguredCount": 20,
                }
                for iid in insts
            ]
            return {"result": recs}
        if tag == "active":
            return {
                "result": [
                    {
                        "domainId": key,
                        "instanceId": iid,
                        "instanceName": f"{sid}-i{iid}",
                        "application": f"/app/{sid}/{iid}",
                        "txid": f"{sid}-{iid}",
                        "elapseTime": iid * 10 + j,
                        "runningMode": "SQL" if j else "",
                        "runningDataSourceName": "ds" if j else "",
                        "startTime": NOW_MS - 1000,
                    }
                    for iid in insts
                    for j in range(2)
                ]
            }
        if tag == "event":
            return {
                "result": [
                    {
                        "time": NOW_MS - 60_000 + iid,
                        "eventLevel": "FATAL",
                        "errorType": "OUTOFMEMORY",
                        "message": f"ev {sid} {iid}",
                        "instanceId": iid,
                        "domainId": key,
                    }
                    for iid in insts
                ]
            }
        if tag == "error":
            want = query.get("error_type")
            if want is not None and want != "ERROR_DB_CONNECTION":  # 표기 후보 둘째에서 맞는다
                return {"result": []}
            return {
                "result": [
                    {
                        "time": NOW_MS - 30_000 + iid,
                        "errorType": want or "SQL_EXCEPTION",
                        "message": f"err {sid} {iid}",
                        "instanceId": iid,
                        "domainId": key,
                    }
                    for iid in insts
                ]
            }
        if tag == "metric":
            return {
                "result": [{"time": NOW_MS - 60_000 * m, "value": float(key + m)} for m in range(3)]
            }
        if tag == "dbpath":
            return {"main": f"/db/{sid}/{key}", "backup": f"/bk/{sid}/{key}"}
        if tag == "env":
            return {str(iid): {"SYSTEM": {"HOME": f"/home/{sid}{iid}"}} for iid in insts}
        if tag == "users":
            return {"result": [{"id": f"{sid}user", "name": "홍길동", "email": "a@example.com"}]}
        if tag == "accounts":
            return [{"id": f"{sid}acct", "name": "관리자", "group": "admin"}]
        if tag == "account":
            return {"id": "canary", "name": "카나리", "group": "ops"}
        if tag == "classes":
            return [{"className": f"com.{sid}.C{key}"}]
        return None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        query = dict(request.url.params)
        tag, key = self._route(path, query)
        self.calls.append((host, tag, key))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            wait = self.delay(host, tag, key)
            if wait:
                await asyncio.sleep(wait)
        finally:
            self.active -= 1
        if (host, tag, key) in self.fail:
            return httpx.Response(500, json=NOT_CONNECTED)
        if tag == "account" and host in self.missing_account:
            status = self.missing_account[host]
            return httpx.Response(404, json={}) if status == 404 else httpx.Response(200, json={})
        body = self._body(host, tag, key, query)
        if body is None:
            return httpx.Response(404, text="no fixture")
        if self.notes and isinstance(body, dict) and tag not in ("domain", "instance"):
            # 깊은 중첩 = 메모 · 비밀 키 = 가린 칸(단위마다 다른 이름 — 고지 문구에 이름이 실린다)
            body = {
                **body,
                f"deep_{host.split('.')[0]}_{tag}_{key}": _deep(),
                f"password_{key}": "p",
            }
        return httpx.Response(200, json=body)


def _tools(fake: FakeApm, tmp_path, fanout: int, **env: str) -> ApmTools:
    cfg = load_config(
        fake.env(
            APM_SPOOL_DIR=str(tmp_path / f"spool{fanout}"),
            APM_FANOUT_CONCURRENCY=str(fanout),
            **env,
        )
    )
    return ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(fake.handler)), cfg, clock=lambda: NOW_S
    )


def _manager(tools: ApmTools) -> JobManager:
    return JobManager.from_config(tools.cfg, envelope=tools.ok, error_envelope=tools.err)


async def _warm(tools: ApmTools) -> None:
    for src in tools.sources:
        await src.resolver.inventory()


async def _as_job(
    mgr: JobManager,
    tools: ApmTools,
    tool: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    owner: str | None = None,
) -> tuple[dict[str, Any], Job]:
    """도구 호출 1회를 작업으로 — 예외는 오류 봉투(서버와 같은 꼴)."""
    jobs: list[Job] = []

    async def guarded() -> dict[str, Any]:
        try:
            return await call()
        except ApmError as e:
            return tools.err(tool, e.code, e.reason)

    out = await mgr.execute(
        tool, guarded, principal="chat", owner=owner, wait_seconds=None, on_job=jobs.append
    )
    return out, jobs[0]


def _audit(job: Job) -> tuple[int, int, dict[str, int], int]:
    return job.api_calls, job.shared_calls, dict(job.calls_by_source), job.expected_calls


Scenario = Callable[[ApmTools], Awaitable[dict[str, Any]]]


async def _compare(
    tmp_path,
    make_fake: Callable[[], FakeApm],
    tool: str,
    scenario: Scenario,
    *,
    delay: float = 0.01,
) -> dict[int, tuple[dict[str, Any], tuple[int, int, dict[str, int], int], int]]:
    """폭 1·4로 같은 시나리오를 작업으로 돌려 (봉투, 감사, 최대 in-flight)를 모은다. 뒤 단위일수록
    빨리 끝나게 지연을 준다(응답 역순 도착)."""
    out = {}
    for fanout in (1, 4):
        fake = make_fake()
        tools = _tools(fake, tmp_path, fanout)
        await _warm(tools)
        fake.delay = lambda host, tag, key: delay * (1 + 1 / (1 + key % 100 + len(host) % 3))
        fake.peak = 0
        env, job = await _as_job(_manager(tools), tools, tool, lambda: scenario(tools))
        out[fanout] = (env, _audit(job), fake.peak)
    return out


def _assert_same(got: dict[int, tuple[dict[str, Any], Any, int]]) -> dict[str, Any]:
    (serial, serial_audit, serial_peak), (fan, fan_audit, fan_peak) = got[1], got[4]
    assert fan == serial
    assert fan_audit == serial_audit
    assert serial_peak == 1 and fan_peak > 1
    return serial


# ── ① 봉투 동일 · ③ 감사 동일 — 일부 실패 · 전부 실패 ─────────────────────


def _two_sources(**kw: Any) -> Callable[[], FakeApm]:
    def make() -> FakeApm:
        fake = FakeApm(3, **kw)
        fake.fail = {
            ("bank.test", "realtime", 2),
            ("common.test", "active", 1),
            ("bank.test", "active", 3),
            ("common.test", "event", 2),
            ("bank.test", "error", 1),
            ("common.test", "metric", 301),
            ("bank.test", "metric", 100),
            ("bank.test", "dbpath", 2),
            ("common.test", "env", 3),
            ("bank.test", "users", 0),
            ("common.test", "classes", 201),
        }
        return fake

    return make


async def test_runtime_health_trend_fanout_matches_serial(tmp_path):
    tool = "apm_runtime_health"
    got = await _compare(
        tmp_path,
        _two_sources(),
        tool,
        lambda t: t.apm_runtime_health(hostname=HOST, lookback_minutes=30),
    )
    env = _assert_same(got)
    assert env["partial"] is True and "error" not in env
    fails = [x for x in env["limits"] if "추세" in x]
    assert len(fails) == 3 * 2  # 실패 인스턴스 2개 × 기본 지표 3종(인스턴스·지표 순서)
    assert got[1][1][3] == 2 * 3 * 2 * 3 + 2 * 3  # 예상 = 추세 36 + 실시간 6


async def test_resource_pool_and_active_services_fanout_match_serial(tmp_path):
    for tool, call in (
        ("apm_resource_pool", lambda t: t.apm_resource_pool(hostname=HOST)),
        ("apm_active_services", lambda t: t.apm_active_services(hostname=HOST, full=True)),
    ):
        env = _assert_same(await _compare(tmp_path / tool, _two_sources(), tool, call))
        assert env["partial"] is True and env["rows"]
        assert sum("액티브 서비스 조회 실패" in x for x in env["limits"]) == 2


async def test_events_fanout_matches_serial_with_error_type_spellings(tmp_path):
    tool = "apm_events"
    for record, etype in (("event", None), ("error", "db_connection")):
        env = _assert_same(
            await _compare(
                tmp_path / record,
                _two_sources(),
                tool,
                lambda t, r=record, e=etype: t.apm_events(
                    hostname=HOST, lookback_minutes=30, record=r, error_type=e, full=True
                ),
            )
        )
        assert env["partial"] is True and env["rows"]
        if etype is not None:  # 표기 후보 순차(#6) 메모가 단위 순서대로 제자리에 있다
            spelled = [x for x in env["limits"] if "API 표기" in x]
            assert len(spelled) == 2 * 3 - 1  # 실패 1단위는 메모 없음


async def test_manage_run_fanout_matches_serial(tmp_path):
    cases: list[tuple[str, Scenario]] = [
        ("apm_config", lambda t: ManageTools(t).apm_config("db_path")),
        ("apm_config", lambda t: ManageTools(t).apm_config("db_path", hostname=HOST)),
        ("apm_config", lambda t: ManageTools(t).apm_config("loaded_classes", hostname=HOST)),
        ("apm_environment", lambda t: ManageTools(t).apm_environment()),
        ("apm_users", lambda t: ManageTools(t).apm_users()),
    ]
    for i, (tool, call) in enumerate(cases):
        env = _assert_same(await _compare(tmp_path / str(i), _two_sources(), tool, call))
        assert "error" in env or env["partial"] is True


async def test_apm_users_absent_keeps_unit_order(tmp_path):
    def make() -> FakeApm:
        fake = FakeApm(1, hosts=("bank.test", "common.test", "card.test"))
        fake.missing_account = {"bank.test": 404, "card.test": 0}
        return fake

    tool = "apm_users"
    env = _assert_same(
        await _compare(
            tmp_path, make, tool, lambda t: ManageTools(t).apm_users(user_id="canary"), delay=0.02
        )
    )
    absent = next(x for x in env["limits"] if "없음:" in x)
    assert absent.index("bank") < absent.index("card")


async def test_all_units_failed_gives_same_error_envelope(tmp_path):
    def make() -> FakeApm:
        fake = FakeApm(3)
        fake.fail = {
            (h, tag, d)
            for h in fake.hosts
            for d in fake.domains
            for tag in ("active", "dbpath", "realtime")
        }
        return fake

    for tool, call in (
        ("apm_active_services", lambda t: t.apm_active_services(hostname=HOST)),
        ("apm_config", lambda t: ManageTools(t).apm_config("db_path")),
        ("apm_resource_pool", lambda t: t.apm_resource_pool(hostname=HOST)),
    ):
        env = _assert_same(await _compare(tmp_path / tool, make, tool, call))
        assert env["error"] == "source_unavailable", env


# ── 메모·가린 칸 입력 순서 재신고 ────────────────────────────────────


async def test_credential_notes_keep_serial_order_when_responses_arrive_reversed(tmp_path):
    def make() -> FakeApm:
        fake = FakeApm(6, hosts=("bank.test",))
        fake.notes = True
        return fake

    for tool, call in (
        ("apm_config", lambda t: ManageTools(t).apm_config("db_path")),
        ("apm_active_services", lambda t: t.apm_active_services(hostname=HOST)),
    ):
        got = await _compare(tmp_path / tool, make, tool, call, delay=0.02)
        env = _assert_same(got)
        deep = [
            m.group(1)
            for x in env["limits"]
            if "자격증명 검사" in x and (m := re.search(r"deep_bank_\w+?_(\d+)", x))
        ]
        assert deep == [str(d) for d in range(1, 7)]  # 단위마다 1건 — 단위(도메인) 순서
        assert any(d["kind"] == "apm_masked_fields" for d in env["disclosures"])


class _Collect(CallScope):
    """메모·가린 칸을 받은 순서대로 적는 맥락(작업 대역) — 그 밖 훅 호출도 적는다."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[Any] = []
        self.widths: list[int] = []
        self.counted: list[str] = []
        self.caller = "owner-1"  # type: ignore[misc]

    def note(self, text: str) -> None:
        self.events.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        self.events.append(sorted(fields))

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self.counted.append(source_id)

    def expect_calls(self, count: int) -> None:
        self.events.append(("expect", count))

    @contextlib.contextmanager
    def parallel(self, width: int) -> Iterator[None]:
        self.widths.append(width)
        yield


async def _reporting(item: int) -> int:
    scope = current_scope()
    await asyncio.sleep(0.005 * (6 - item))  # 뒤 항목이 먼저 끝난다
    scope.note(f"n{item}")
    scope.masked([f"f{item}"])
    if item == 3:
        raise ApmError("apm_api_error", "실패도 메모는 남는다")
    return item


async def test_gather_bounded_reports_notes_in_input_order():
    runs = {}
    for limit in (1, 4):
        scope = _Collect()
        with use_scope(scope):
            out = await gather_bounded(range(6), _reporting, limit)
        runs[limit] = (scope.events, [o if not isinstance(o, ApmError) else "E" for o in out])
    assert runs[4] == runs[1]
    assert runs[1][0][:4] == ["n0", ["f0"], "n1", ["f1"]]


async def test_wrapper_delegates_hooks_and_priority_to_outer():
    scope = _Collect()
    seen: list[tuple[str, str]] = []

    async def fn(item: int) -> int:
        inner = current_scope()
        await inner.before_call(f"s{item}")
        inner.expect_calls(1)
        seen.append((inner.priority, inner.caller))
        with inner.parallel(2):
            pass
        return item

    with use_scope(scope):
        await gather_bounded(range(4), fn, 4)
    assert sorted(scope.counted) == ["s0", "s1", "s2", "s3"]
    assert scope.events.count(("expect", 1)) == 4
    assert set(seen) == {("interactive", "owner-1")}
    assert scope.widths == [4, 2, 2, 2, 2]


async def test_nested_gather_keeps_input_order():
    scope = _Collect()

    async def inner(item: int) -> int:
        return (await gather_bounded([item * 10, item * 10 + 1], _reporting_safe, 2))[0]

    with use_scope(scope):
        await gather_bounded(range(3), inner, 3)
    assert [e for e in scope.events if isinstance(e, str)] == [
        "n0", "n1", "n10", "n11", "n20", "n21"
    ]


async def _reporting_safe(item: int) -> int:
    scope = current_scope()
    await asyncio.sleep(0.002 * (30 - item))
    scope.note(f"n{item}")
    return item


async def test_late_note_after_flush_goes_straight_out():
    scope = _Collect()
    late: list[asyncio.Task[None]] = []

    async def linger() -> None:  # 항목이 띄운 태스크(맥락 상속)가 병렬 블록이 끝난 뒤 신고한다
        await asyncio.sleep(0.01)
        current_scope().note("late")

    async def fn(item: int) -> int:
        if item == 0:
            late.append(asyncio.create_task(linger()))
        return item

    with use_scope(scope):
        await gather_bounded(range(2), fn, 2)
    assert scope.events == []
    await late[0]
    assert scope.events == ["late"]  # 버려지지 않고 바깥 맥락에 바로 간다


async def test_other_exception_still_flushes_finished_notes():
    scope = _Collect()

    async def fn(item: int) -> int:
        if item == 2:
            await asyncio.sleep(0.02)
            raise RuntimeError("버그")
        current_scope().note(f"n{item}")
        return item

    with use_scope(scope), pytest.raises(RuntimeError):
        await gather_bounded(range(4), fn, 4)
    assert scope.events == ["n0", "n1", "n3"]


# ── ② 소요 단축(느슨) ───────────────────────────────────────────


async def test_fanout_shortens_wall_time(tmp_path):
    elapsed = {}
    for fanout in (1, 4):
        fake = FakeApm(8, hosts=("bank.test",))
        tools = _tools(fake, tmp_path, fanout)
        await _warm(tools)
        fake.delay = lambda host, tag, key: 0.05
        started = time.monotonic()
        out = await ManageTools(tools).apm_config("db_path")
        elapsed[fanout] = time.monotonic() - started
        assert out["row_count"] == 8
    print(f"\n[plans/205 W2a 소요] 순차 {elapsed[1]:.3f}초 · 폭 4 {elapsed[4]:.3f}초")
    assert elapsed[1] >= 8 * 0.05 * 0.95
    assert elapsed[4] < elapsed[1] * 0.6


# ── 위험 R-1 — 자리 대기 초과(시간 축척 ×0.1) ─────────────────────────


async def run_r1(
    tmp_path,
    *,
    scale: float,
    fanout: int,
    jobs: int = 4,
    calls: int = 12,
    same_owner: bool = False,
) -> dict[str, Any]:
    """작업 `jobs`개가 동시에 `apm_config(db_path)`(도메인 `calls`개 = 독립 호출 `calls`건)를
    부른다. 자리 8 · 초당 5/`scale` · timeout 30×`scale`초 · 지연 3·5×`scale`초(도메인 홀짝)."""
    fake = FakeApm(calls, hosts=("bank.test",), per_domain=1)
    tools = _tools(
        fake,
        tmp_path,
        fanout,
        JENNIFER_MAX_INFLIGHT_PER_SOURCE="8",
        JENNIFER_RATE_LIMIT_PER_SEC=str(5 / scale),
        JENNIFER_API_TIMEOUT_SECONDS=str(30 * scale),
        APM_JOB_MAX_CONCURRENT=str(jobs),
    )
    await _warm(tools)
    fake.delay = lambda host, tag, key: (3 if key % 2 else 5) * scale
    mgr = _manager(tools)
    started = time.monotonic()
    results = await asyncio.gather(
        *(
            _as_job(
                mgr,
                tools,
                "apm_config",
                lambda: ManageTools(tools).apm_config("db_path"),
                owner="o" if same_owner else f"o{k}",
            )
            for k in range(jobs)
        )
    )
    took = time.monotonic() - started
    texts = [
        str(env.get("reason", "")) + " ".join(env.get("limits") or []) for env, _ in results
    ]
    waits = [job.waits() for _, job in results]
    return {
        "seconds": round(took, 2),
        "slot_timeouts": sum(t.count(SLOT_TIMEOUT) for t in texts),
        "errors": sum("error" in env for env, _ in results),
        "slot_wait_max_ms": max(w["slot_wait_ms"]["max"] for w in waits),
        "throttle_wait_max_ms": max(w["throttle_wait_ms"]["max"] for w in waits),
        "db_path_calls": sum(1 for _, tag, _ in fake.calls if tag == "dbpath"),
        "peak_in_flight": fake.peak,
    }


async def test_r1_no_slot_wait_timeout_scaled(tmp_path):
    got = await asyncio.wait_for(run_r1(tmp_path, scale=0.1, fanout=4), 15)
    print(f"\n[plans/205 W2a R-1 축척 ×0.1] {got}")
    assert got["slot_timeouts"] == 0 and got["errors"] == 0, got
    assert got["db_path_calls"] == 4 * 12
    assert got["peak_in_flight"] == 8  # 자리 상한까지 찼다(상한은 넘지 않는다)
    assert got["slot_wait_max_ms"] < 30 * 0.1 * 1000
