"""plans/134 W6 독립 검증(verify-134-w6 · 2026-10-07) — 실프로세스 종단.

합성 제니퍼 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트) ↔ `python -m apm_gateway`(임시
포트 · 임시 스풀 · 폴러 꺼짐) ↔ 실제 MCP SSE ↔ 본체 2단(가짜 LLM 분해 → `agent_orchestrator` →
`replanner` → `result_aggregator`). 스택·기록기는 W3·W4 검증(`test_plan134_w34_verify_real`)의 것을
쓰고, 합성 제니퍼에 시 단위 애플리케이션 통계(`/api/status/application`)만 더한다.

요청 시간 해석(state `time_resolution` · D-309)은 고정 기준 시각(2026-10-06 화 10:30 KST)으로 원문을
해석해 싣는다 — 입력 파서가 매 턴 싣는 값과 같은 함수(`resolve_query_time`)다.

- E1 M-7: 지난 기간(사흘 전 날짜 · 지난달)이 거부되지 않고 그 창(반개구간)으로 호출된다
- E2 A-1: 「web01 지난주 대비 처리량」 → `apm_period_compare` 두 구간 · 답 줄(N/A · 0 채움 없음)
- E3 A-4: 「어제 응답시간 느린 WAS 5개」 → 기간 순위(인스턴스마다 통계 1호출 · 독립 오라클) ·
  실패 도메인이 섞이면 잠정 · 「전체 순위」 문구 없음 · 기간 전용 지표는 기간 없이 되묻기(호출 0)
- E4 ④: 서버 미지정 → 부하 상위 N만(25개 환경 → 20) · 15개면 전부 · 서버 지정은 순위 없음
- E5 ④: 순위 실패 → 목록 앞 N + 「부하 순이 아님」(조용한 전체 확장 없음)
- 인가: 관측 소스 권한이 없으면 첫 홉 순위 포함 게이트웨이 호출 0

결함 재현은 `xfail(strict=True, reason="V6-n …")`이다(고치면 XPASS로 실패하니 표지를 걷는다).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from src.domain import disclosure as disc
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from tests.test_orchestration.test_plan134_w34_verify_real import (
    _HOSTS,
    _TOKENS,
    _config,
    _final,
    _judgement_block,
    _Model,
    _NoEdge,
    _Recorder,
    _Stack,
    _task,
)
from tests.test_orchestration.test_plan134_w567_body_verify import _fresh, _two_tier_turn

pytestmark = pytest.mark.asyncio

ANCHOR = datetime(2026, 10, 6, 10, 30, tzinfo=KST)  # 화요일 10:30 KST


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


# ── 합성 제니퍼 + 시 단위 애플리케이션 통계 ─────────────────────────────────────────

def _default_stats(sid: str, d: int, iid: int, lo: int, hi: int) -> list[dict] | None:
    """인스턴스 1개 · 구간의 애플리케이션 행(합성) — 시간 수에 비례한 호출 · 인스턴스별 평균."""
    hours = max(1, (hi - lo) // 3_600_000)
    calls = ((iid % 7) + 1) * 10 * hours
    avg = ((iid * 31 + (0 if sid == "bank" else 7)) % 97 + 1) * 10
    return [{"name": "/order", "calls": calls, "failures": iid % 5,
             "responseTime": float(avg), "maxResponseTime": avg * 3,
             "totalResponseTime": calls * avg}]


class _W6Model(_Model):
    """W3·W4 합성 제니퍼 + `/api/status/application`(인스턴스 1개 = 그 인스턴스 행)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stats_fn = _default_stats

    def respond(self, template: str, query: dict[str, str], path: str) -> tuple[int, Any]:
        if template == "/api/status/application":
            d = int(query["domain_id"])
            if d in self.fail.get(template, ()):
                return 500, {"exception": {"message": f"{d} Domain is not connected"}}
            ids = [int(x) for x in query.get("instance_id", "").split(",") if x.strip()]
            lo, hi = int(query["start_time"]), int(query["end_time"])
            known = {i["instanceId"] for i in self.instances.get(d, [])}
            rows: list[dict] = []
            for iid in ids or sorted(known):
                if iid in known:
                    rows += self.stats_fn(self.sid, d, iid, lo, hi) or []
            return 200, {"result": rows}
        return super().respond(template, query, path)


def _models(*, bank_hosts: list[str], bank_domains: int, common_hosts: list[str],
            common_domains: int, hostless: bool = False) -> dict[str, _W6Model]:
    return {"bank": _W6Model("bank", _TOKENS["bank"], hosts=bank_hosts, domains=bank_domains,
                             hostless=hostless),
            "common": _W6Model("common", _TOKENS["common"], hosts=common_hosts,
                               domains=common_domains)}


def _instances(stack: _Stack) -> list[tuple[str, int, int, str]]:
    return [(sid, d, i["instanceId"], i["hostName"]) for sid, m in stack.models.items()
            for d, recs in m.instances.items() for i in recs]


@pytest.fixture(scope="module")
def w6stack(tmp_path_factory):
    pytest.importorskip("mcp")
    s = _Stack(_models(bank_hosts=_HOSTS, bank_domains=6, common_hosts=_HOSTS[:4],
                       common_domains=6), tmp_path_factory.mktemp("v6"), {})
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def live(w6stack, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", rec.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    w6stack.clear()
    for m in w6stack.models.values():
        m.stats_fn = _default_stats
    return w6stack, rec


@pytest.fixture
def sized(tmp_path, monkeypatch):
    """인스턴스 수를 정한 새 스택(④ 첫 홉 — 25개 · 15개)."""
    pytest.importorskip("mcp")
    made: list[_Stack] = []
    rec = _Recorder()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", rec.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))

    def make(**kw: Any) -> tuple[_Stack, _Recorder]:
        where = tmp_path / f"s{len(made)}"
        where.mkdir()
        s = _Stack(_models(**kw), where, {})
        made.append(s)
        return s, rec

    try:
        yield make
    finally:
        for s in made:
            s.close()


async def _turn(stack: _Stack, query: str, tasks: list[dict] | dict, *,
                hosts: tuple[str, ...] = (), top_n: int = 20,
                allowed_sources: list[str] | None = None, anchor: datetime = ANCHOR) -> dict:
    state = _fresh(query, "th-v6")
    state["time_resolution"] = resolve_query_time(query, anchor).to_state()
    if allowed_sources is not None:
        state["allowed_sources"] = allowed_sources
    return await _two_tier_turn(state, query, tasks if isinstance(tasks, list) else [tasks],
                                _config(stack.url, top_n=top_n), hosts=hosts)


def _res(state: dict, tid: str = "t1") -> dict:
    return (state.get("task_results") or {}).get(tid) or {}


def _kinds(res: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in res.get("disclosures") or []:
        out.setdefault(d["kind"], []).append(d["text"])
    return out


def _status_hits(stack: _Stack) -> list[dict]:
    return [h for m in stack.models.values() for h in m.hits_of("/api/status/application")]


# ── E1: M-7 — 지난 기간도 그 창으로 호출 ────────────────────────────────────────────

async def test_e1_three_days_ago_is_queried_with_the_half_open_day(live) -> None:
    """「10월 3일」(기준 시각 사흘 전) 추세 — 거부 없음 · reference_time = 다음 날 0시
    (23:59:59 아님) · lookback 1440분 · 게이트웨이 → 제니퍼 구간이 정확히
    [10-03 00:00, 10-04 00:00)."""
    stack, rec = live
    state = await _turn(stack, "web01 10월 3일 응답시간 추세", _task(["apm.runtime"]),
                        hosts=("web01",))
    res = _res(state)
    (args,) = rec.named("apm_runtime_health")
    assert args["reference_time"] == "2026-10-04T00:00:00+09:00", args
    assert args["lookback_minutes"] == 1440, args
    dm = [h for m in stack.models.values() for h in m.hits_of() if h["path"].startswith(
        "/api/dbmetrics/")]
    assert dm, [h["path"] for m in stack.models.values() for h in m.hits_of()]
    lo = _ms(datetime(2026, 10, 3, tzinfo=KST))
    hi = _ms(datetime(2026, 10, 4, tzinfo=KST))
    assert {(int(h["query"]["start_time"]), int(h["query"]["end_time"])) for h in dm} == {(lo, hi)}
    text = str(res.get("final_response") or "") + _final(state)
    assert "하루 넘게 지난 기간" not in text and "조회하지 않습니다" not in text
    assert res["source_status"][0]["status"] == "ok", res["source_status"]


async def test_e1_last_month_events_are_queried_for_the_whole_month(live) -> None:
    stack, rec = live
    state = await _turn(stack, "web01 지난달 이벤트", _task(["apm.events"]), hosts=("web01",))
    (args,) = rec.named("apm_events")
    assert args["reference_time"] == "2026-10-01T00:00:00+09:00", args
    assert args["lookback_minutes"] == 30 * 1440, args
    ev = [h for m in stack.models.values() for h in m.hits_of("/api/dbsearch/event")]
    assert ev
    lo, hi = _ms(datetime(2026, 9, 1, tzinfo=KST)), _ms(datetime(2026, 10, 1, tzinfo=KST))
    starts = [int(h["query"]["start_time"]) for h in ev]
    ends = [int(h["query"]["end_time"]) for h in ev]
    assert min(starts) == lo and max(ends) == hi, (min(starts), max(ends), lo, hi)
    # 빈 결과(합성 이벤트 0) — 보존 만료로 단정하지 않는다
    res = _res(state)
    notes = " ".join(_kinds(res).get(aq.NOTE_TRACE, []))
    assert aq.EMPTY_PERIOD_NOTE in notes or aq.EMPTY_PERIOD_NOTE in " ".join(
        (res.get("apm_query") or {}).get("notes") or []), res.get("disclosures")
    assert "보존 기간 만료" not in _final(state)


async def test_e1_task_level_period_wins_over_the_request(live) -> None:
    """2단 task별 해석 우선 — 원문에 기간 둘(어제 · 지난달), task 문장마다 자기 기간."""
    stack, rec = live
    query = "web01 어제 응답시간 추세와 지난달 이벤트"
    t1 = {**_task(["apm.runtime"], sub="web01 어제 응답시간 추세"), "task_id": "t1"}
    t2 = {**_task(["apm.events"], sub="web01 지난달 이벤트"), "task_id": "t2", "order": 2}
    await _turn(stack, query, [t1, t2], hosts=("web01",))
    (rt,) = rec.named("apm_runtime_health")
    (ev,) = rec.named("apm_events")
    assert (rt["reference_time"], rt["lookback_minutes"]) == ("2026-10-06T00:00:00+09:00", 1440)
    assert (ev["reference_time"], ev["lookback_minutes"]) == ("2026-10-01T00:00:00+09:00",
                                                              30 * 1440)


async def test_v6_2_recent_hours_window_reaches_the_anchor(live) -> None:
    stack, rec = live
    await _turn(stack, "web01 최근 3시간 응답시간 추세", _task(["apm.runtime"]), hosts=("web01",))
    (args,) = rec.named("apm_runtime_health")
    # 「최근 3시간」 = 07:30~10:30(지금까지) — reference_time null(지금) · 180분
    assert args.get("reference_time") is None and args["lookback_minutes"] == 180, args


# ── E2: A-1 두 기간 비교 ─────────────────────────────────────────────────────────

def _compare_stats(cut: int):
    """기준(구간 시작 < cut) / 비교 구간 — bank web01: 기준 100호출 → 비교 150호출,
    common web01: 기준 통계 행 없음(N/A — 0으로 채우지 않아야 한다) → 비교 50호출."""

    def fn(sid: str, d: int, iid: int, lo: int, hi: int) -> list[dict] | None:
        baseline = lo < cut
        if sid == "common" and baseline:
            return []
        calls = {("bank", True): 100, ("bank", False): 150, ("common", False): 50}[
            (sid, baseline)]
        fails = 2 if (sid, baseline) == ("bank", True) else 3 if sid == "bank" else 0
        avg = 200 if baseline else 300
        return [{"name": "/order", "calls": calls, "failures": fails, "responseTime": float(avg),
                 "maxResponseTime": avg * 4, "totalResponseTime": calls * avg}]

    return fn


async def test_e2_last_week_compare_sends_two_half_open_windows_and_says_na(live) -> None:
    stack, rec = live
    cur_start = datetime(2026, 10, 5, tzinfo=KST)
    for m in stack.models.values():
        m.stats_fn = _compare_stats(_ms(cur_start))
    state = await _turn(stack, "web01 지난주 대비 처리량", _task(["apm.period_compare"]),
                        hosts=("web01",))
    res = _res(state)
    (args,) = rec.named("apm_period_compare")
    # 지난주(9/28~10/5) = 기준 · 비교 = 이번 주 지금까지(정시 내림 10:00) · 기준을 같은 경과로
    assert (args["baseline_start"], args["baseline_end"], args["current_start"],
            args["current_end"]) == ("2026-09-28T00:00:00+09:00", "2026-09-29T10:00:00+09:00",
                                     "2026-10-05T00:00:00+09:00", "2026-10-06T10:00:00+09:00")
    assert args["hostname"] == "web01"
    hits = _status_hits(stack)
    windows = {(int(h["query"]["start_time"]), int(h["query"]["end_time"])) for h in hits}
    assert windows == {(_ms(datetime(2026, 9, 28, tzinfo=KST)),
                        _ms(datetime(2026, 9, 29, 10, tzinfo=KST))),
                       (_ms(cur_start), _ms(datetime(2026, 10, 6, 10, tzinfo=KST)))}, windows
    assert len(hits) == 4, "인스턴스 2(bank·common web01) × 구간 2"
    block = _judgement_block(_final(state))
    line = next((ln for ln in block.splitlines() if "기준" in ln and "비교" in ln), "")
    assert line, block
    # 기준 = bank만(100) · 비교 = 150 + 50 = 200 → +100% (common 기준 0으로 채우지 않음)
    assert "호출 100 → 200(+100.0%)" in line, line
    assert "N/A" not in line.split("호출")[1].split("·")[0], line
    assert res["source_status"][0]["status"] in ("ok", "partial"), res["source_status"]


async def test_e2_zero_baseline_is_na_not_infinite(live) -> None:
    stack, rec = live
    cut = _ms(datetime(2026, 10, 5, tzinfo=KST))

    def fn(sid: str, d: int, iid: int, lo: int, hi: int) -> list[dict] | None:
        calls = 0 if lo < cut else 40
        return [{"name": "/order", "calls": calls, "failures": 0, "responseTime": None,
                 "maxResponseTime": None, "totalResponseTime": 0}]

    for m in stack.models.values():
        m.stats_fn = fn
    state = await _turn(stack, "web01 지난주 대비 처리량", _task(["apm.period_compare"]),
                        hosts=("web01",))
    block = _judgement_block(_final(state))
    line = next((ln for ln in block.splitlines() if "기준" in ln and "비교" in ln), "")
    assert "호출 0 → 80(N/A)" in line, line


async def test_e2_compare_without_a_period_asks_and_opens_nothing(live) -> None:
    stack, rec = live
    mark = stack.log_mark()
    state = await _turn(stack, "web01 처리량 비교해줘", _task(["apm.period_compare"]),
                        hosts=("web01",))
    assert rec.calls == [] and rec.opened == 0
    assert stack.audits(mark) == []
    assert aq.COMPARE_ASK_NOTE in str(_res(state).get("final_response") or _final(state))


# ── E3: A-4 기간 순위 ───────────────────────────────────────────────────────────

def _period_oracle(stack: _Stack, lo: int, hi: int, n: int, *,
                   failed: set[tuple[str, int]] = frozenset()) -> list[tuple[str, int, int, float]]:
    order = {"bank": 0, "common": 1}
    items = []
    for sid, d, iid, _h in _instances(stack):
        if (sid, d) in failed:
            continue
        rows = stack.models[sid].stats_fn(sid, d, iid, lo, hi) or []
        calls = sum(r["calls"] for r in rows)
        if not calls:
            continue
        avg = sum(r["totalResponseTime"] for r in rows) / calls
        items.append((-avg, order[sid], d, iid, sid))
    items.sort()
    return [(sid, d, iid, -neg) for neg, _, d, iid, sid in items[:n]]


async def test_e3_yesterday_slowest_was_is_a_period_ranking(live) -> None:
    stack, rec = live
    state = await _turn(stack, "어제 응답시간 가장 느린 WAS 5개",
                        _task(["apm.ranking"], view_args={"apm.ranking": {
                            "metric": "response_time_avg_ms", "n": 5}}))
    res = _res(state)
    (args,) = rec.named("apm_fleet")
    assert (args["mode"], args["metric"], args["n"], args["reference_time"],
            args["lookback_minutes"]) == ("ranking", "response_time_avg_ms", 5,
                                          "2026-10-06T00:00:00+09:00", 1440), args
    lo, hi = _ms(datetime(2026, 10, 5, tzinfo=KST)), _ms(datetime(2026, 10, 6, tzinfo=KST))
    hits = _status_hits(stack)
    assert len(hits) == len(_instances(stack)), "인스턴스마다 통계 1호출"
    assert all("," not in h["query"]["instance_id"] for h in hits)
    assert {(int(h["query"]["start_time"]), int(h["query"]["end_time"])) for h in hits} == {
        (lo, hi)}
    assert not [h for m in stack.models.values() for h in m.hits_of("/api/realtime/instance")]
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"])
           for r in res["query_results"]]
    assert got == _period_oracle(stack, lo, hi, 5), got
    block = _judgement_block(_final(state))
    assert "기간 순위(시 단위 통계" in block, block
    assert "전체 순위" not in _final(state)
    assert res["source_status"][0]["status"] == "ok"


async def test_e3_failed_domain_makes_the_period_ranking_provisional(live) -> None:
    stack, rec = live
    stack.models["common"].fail["/api/status/application"].add(3)
    try:
        state = await _turn(stack, "어제 응답시간 가장 느린 WAS 5개",
                            _task(["apm.ranking"], view_args={"apm.ranking": {
                                "metric": "response_time_avg_ms", "n": 5}}))
    finally:
        stack.models["common"].fail["/api/status/application"].discard(3)
    res = _res(state)
    lo, hi = _ms(datetime(2026, 10, 5, tzinfo=KST)), _ms(datetime(2026, 10, 6, tzinfo=KST))
    got = [(r["source_id"], r["domain_id"], r["instance_id"], r["value"])
           for r in res["query_results"]]
    assert got == _period_oracle(stack, lo, hi, 5, failed={("common", 3)}), got
    block = _judgement_block(_final(state))
    assert "잠정 순위 — 조회 실패 도메인 1곳 제외" in block, block
    assert "전체 순위" not in _final(state)
    assert disc.APM_PARTIAL_SOURCES in _kinds(res)
    assert res["source_status"][0]["status"] == "partial"


async def test_e3_failure_rate_ranking_with_a_period(live) -> None:
    stack, rec = live
    state = await _turn(stack, "어제 실패율 높은 WAS 5개",
                        _task(["apm.ranking"], view_args={"apm.ranking": {
                            "metric": "failure_rate", "n": 5}}))
    (args,) = rec.named("apm_fleet")
    assert args["metric"] == "failure_rate" and args["lookback_minutes"] == 1440
    rows = _res(state)["query_results"]
    assert len(rows) == 5 and all(0 <= r["value"] <= 1 for r in rows), rows
    assert "0~1 비율" in _judgement_block(_final(state))


@pytest.mark.parametrize("metric", ["calls", "failures", "failure_rate", "max_response_time_ms"])
async def test_e3_period_only_metric_without_period_asks_with_zero_calls(live, metric) -> None:
    stack, rec = live
    mark = stack.log_mark()
    state = await _turn(stack, "실패율 높은 WAS 5개",
                        _task(["apm.ranking"], view_args={"apm.ranking": {
                            "metric": metric, "n": 5}}))
    assert rec.calls == [] and rec.opened == 0, rec.calls
    assert stack.audits(mark) == []
    assert all(m.hits_of() == [] for m in stack.models.values())
    assert aq.PERIOD_ONLY_ASK_NOTE in str(_res(state).get("final_response") or _final(state))


async def test_e3_realtime_only_metric_with_a_period_says_current_value(live) -> None:
    stack, rec = live
    state = await _turn(stack, "어제 힙 사용량 높은 WAS 3개",
                        _task(["apm.ranking"], view_args={"apm.ranking": {
                            "metric": "db_pool_active", "n": 3}}))
    (args,) = rec.named("apm_fleet")
    assert args["lookback_minutes"] == 1440
    assert _status_hits(stack) == [], "기간 통계 0호출 — 현재값"
    block = _judgement_block(_final(state))
    assert "요청 기간의 순위가 아니라 현재값 순위입니다" in block, block


# ── E4·E5: ④ 서버 미지정 = 부하 상위 N ───────────────────────────────────────────

_25 = {"bank_hosts": _HOSTS, "bank_domains": 4, "common_hosts": _HOSTS[:4], "common_domains": 2,
       "hostless": True}  # 12 + 6 + 1 + 4 + 2 = 25
_15 = {"bank_hosts": _HOSTS[:5], "bank_domains": 3, "common_hosts": _HOSTS[:4],
       "common_domains": 2}  # 5 + 4 + 4 + 2 = 15


async def test_e4_untargeted_twenty_five_instances_queries_the_top_twenty(sized) -> None:
    stack, rec = sized(**_25)
    assert len(_instances(stack)) == 25
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]))
    res = _res(state)
    first_name, first = rec.calls[0]
    assert first_name == "apm_fleet"
    assert (first["mode"], first["metric"], first["order"], first["n"]) == (
        "ranking", "tps", "desc", 20)
    assert "lookback_minutes" not in first and "reference_time" not in first
    assert rec.named("apm_instance_map") == []
    (batch,) = rec.named("apm_app_health")
    # 오라클 — 실시간 tps(= instanceId % 50) 내림차순 상위 20
    tps = sorted(((-(iid % 50), {"bank": 0, "common": 1}[sid], d, iid, sid, host)
                  for sid, d, iid, host in _instances(stack)))[:20]
    top_hosts = {host for *_, host in tps if host}
    top_hostless = [(sid, iid) for *_, iid, sid, host in tps if not host]
    sent_hosts = {t["hostname"] for t in batch["targets"] if "hostname" in t}
    sent_hostless = [(t["source_id"], t["instance_id"]) for t in batch["targets"]
                     if "hostname" not in t]
    assert sent_hosts == top_hosts, (sent_hosts ^ top_hosts)
    assert sent_hostless == top_hostless
    kinds = _kinds(res)
    (note,) = kinds[disc.APM_UNTARGETED_SCOPE]
    assert "전체 인스턴스 25개 중 현재 부하(TPS) 상위 20개" in note, note
    assert disc.APM_PARTIAL_SOURCES not in kinds, kinds
    assert res["source_status"][0]["status"] == "ok", res["source_status"]


async def test_v6_3_top_n_results_are_at_most_n_instances(sized) -> None:
    """상위 12 = 호스트 없는 batch-was(tps 49) + bank web12~web02(tps 12~2 — 동률 2는 소스 순서로
    bank). common web02~web04는 순위 밖인데 hostname 대상 web02~web04가 두 소스를 다 부른다."""
    stack, rec = sized(**_25)
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]), top_n=12)
    (note,) = _kinds(_res(state))[disc.APM_UNTARGETED_SCOPE]
    assert "상위 12개" in note, note
    got = {(r["source_id"], r["instance_id"]) for r in _res(state)["query_results"]}
    assert len(got) <= 12, (len(got), str(sorted(got)))


async def test_e4_untargeted_fifteen_instances_queries_everything(sized) -> None:
    stack, rec = sized(**_15)
    assert len(_instances(stack)) == 15
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]))
    res = _res(state)
    (batch,) = rec.named("apm_app_health")
    hosts = {h for *_, h in _instances(stack) if h}
    assert {t["hostname"] for t in batch["targets"]} == hosts
    got = {(r["source_id"], r["instance_id"]) for r in res["query_results"]}
    assert got == {(sid, iid) for sid, _d, iid, _h in _instances(stack)}
    kinds = _kinds(res)
    assert disc.APM_UNTARGETED_SCOPE not in kinds and disc.APM_PARTIAL_SOURCES not in kinds
    assert any("전체 인스턴스 15개" in t for t in kinds.get(aq.NOTE_TRACE, [])), kinds


async def test_e4_named_server_keeps_the_former_path(sized) -> None:
    stack, rec = sized(**_25)
    await _turn(stack, "web03 응답시간", _task(["apm.app_health"]), hosts=("web03",))
    assert [n for n, _ in rec.data_calls()] == ["apm_app_health"]
    assert rec.named("apm_app_health")[0]["hostname"] == "web03"


async def test_e5_ranking_failure_falls_back_to_the_list_head(sized) -> None:
    stack, rec = sized(**_25)
    for m in stack.models.values():  # 순위(실시간 인스턴스) 전 도메인 실패
        m.fail["/api/realtime/instance"].update(range(1, 10))
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]))
    res = _res(state)
    names = [n for n, _ in rec.data_calls()]
    assert names[:2] == ["apm_fleet", "apm_instance_map"], names
    (batch,) = rec.named("apm_app_health")
    step = res["apm_query"]["inserted_steps"][0]
    assert step["mode"] == "list" and step["instances"] == 20 and step["listed"] == 25, step
    assert len(batch["targets"]) == step["hosts"] + step.get("hostless", 0)
    assert len(batch["targets"]) < len({h for *_, h in _instances(stack) if h}) + 1, \
        "조용한 전체 확장 없음"
    kinds = _kinds(res)
    (note,) = [t for t in kinds.get(disc.APM_PARTIAL_SOURCES, []) if "대상 서버 미지정" in t]
    assert "부하 순이 아닙니다" in note and "목록 인스턴스 25개" in note, note


async def test_full_from_decomposition_on_a_full_view_queries_every_instance(sized) -> None:
    stack, rec = sized(**_25)
    state = await _turn(stack, "실행 중 서비스 전부", _task(["apm.active"], view_args={
        "apm.active": {"full": True}}))
    assert rec.named("apm_fleet") == [], "전부 요청이면 순위 첫 홉 없음"
    assert rec.named("apm_instance_map"), rec.calls
    step = _res(state)["apm_query"]["inserted_steps"][0]
    assert step["mode"] == "full" and step["instances"] == 25, step


# ── 인가 ──────────────────────────────────────────────────────────────────────

async def test_authz_denied_source_makes_no_gateway_call_even_for_the_first_hop(sized) -> None:
    stack, rec = sized(**_15)
    mark = stack.log_mark()
    state = await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]),
                        allowed_sources=["polestar"])
    assert rec.opened == 0 and rec.calls == []
    assert stack.audits(mark) == []
    assert all(m.hits_of() == [] for m in stack.models.values())
    assert _res(state).get("degraded_reason") or "권한" in _final(state)


async def test_first_hop_audit_line_has_no_raw_targets(sized) -> None:
    """첫 홉 순위 + 배치 감사 — 감사 줄에 호스트 원문이 실리지 않는다(대상 요약만)."""
    stack, rec = sized(**_25)
    mark = stack.log_mark()
    await _turn(stack, "WAS 응답시간", _task(["apm.app_health"]))
    audits = [a for a in stack.audits(mark) if "tool=apm_job" not in a]
    assert any("tool=apm_fleet" in a for a in audits), audits
    assert not any(h in a for a in audits for h in ("web07", "web11")), audits
    assert not any(tok in stack.log_text() for tok in _TOKENS.values())


async def test_anchor_offsets_used_above() -> None:
    """위 시나리오의 기준 시각 산술(독립 확인) — 지난주 월요일 · 이번 주 경과."""
    monday = ANCHOR.replace(hour=0, minute=0) - timedelta(days=ANCHOR.weekday())
    assert monday == datetime(2026, 10, 5, tzinfo=KST)
    elapsed = ANCHOR.replace(minute=0) - monday
    assert monday - timedelta(days=7) + elapsed == datetime(2026, 9, 29, 10, tzinfo=KST)


# ── 기간 순위 대량 호출 — 호출 계획 신고 · 작업 승격 · 마감 ──────────────────────────

async def test_period_ranking_is_promoted_to_a_job_with_planned_calls(sized) -> None:
    """기간 순위는 인스턴스마다 통계 1호출이다 — 게이트웨이가 `expect_calls`로 계획을 신고하고, 본체
    마감 안에 끝나지 않으면 작업으로 접수(조회 결과로 위장하지 않음)한 뒤 작업 API로 회수된다."""
    import asyncio
    import time

    from src.orchestration import apm_jobs
    from tests.test_orchestration.test_plan134_w34_verify_real import _apm_result

    stack, rec = sized(**_15)
    for m in stack.models.values():
        m.delay["/api/status/application"] = 0.8
    config = _config(stack.url, call_timeout=4.0, query_timeout=5, reserve=2)
    query = "어제 응답시간 가장 느린 WAS 5개"
    state = _fresh(query, "th-v6-job")
    state["time_resolution"] = resolve_query_time(query, ANCHOR).to_state()
    t0 = time.monotonic()
    state = await _two_tier_turn(state, query, [_task(["apm.ranking"], view_args={
        "apm.ranking": {"metric": "response_time_avg_ms", "n": 5}})], config)
    took = time.monotonic() - t0
    res = _apm_result(state)
    (args,) = rec.named("apm_fleet")
    assert args["wait_seconds"] <= 2.0 and args["lookback_minutes"] == 1440, args
    assert res["source_status"][0]["status"] == "accepted", res["source_status"]
    assert took < 5.0 + 1.0, f"마감 초과 {took:.1f}s"
    text = str(res.get("final_response"))
    m = __import__("re").search(r"진행 (\d+)/(\d+) API 호출", text)
    assert m and int(m.group(2)) == len(_instances(stack)), text
    jobs = res.get("accepted_jobs") or (res.get("apm_query") or {}).get("accepted_jobs") or []
    assert len(jobs) == 1, jobs
    service = apm_jobs.ApmJobService(config, store_mod._STORE)
    user = {"sub": "alice", "role": "user"}
    for _ in range(240):
        view = await service.status(jobs[0], user)
        if view["state"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.25)
    assert view["state"] == "completed", view
    assert view["progress"]["done"] == view["progress"]["total"] == len(_instances(stack))
