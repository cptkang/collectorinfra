"""plans/134 W6 독립 검증(verify-134-w6 · 2026-10-07) — 계약 대조 · 시간 경계 적대 사례 · 감사.

1. 계약: 요청 시간 해석(`time_resolution` — KST aware ISO)이 있을 때 본체가 실제로 만드는 인자
   (range·hourly·compare 보기 · 기간 순위 전 지표 · 첫 홉 순위)를 게이트웨이 실 MCP 입력 스키마와
   jsonschema로 대조한다(모르는 키 · 빠진 필수 · 형 불일치 0). 본체 수동 사본
   `PERIOD_ONLY_RANKING_METRICS` == 게이트웨이 `PERIOD_RANKING_METRICS − RANKING_METRICS`(순서까지)
   드리프트를 본다(패키지 경계상 import 대신 게이트웨이 프로세스에서 읽는다).
2. 시간 경계: 기준 시각이 정시 직후·월말·주초·경계 정각인 A-1 비교 구간(기준·비교 구간이 겹치지
   않는다 · 길이 0이면 되묻기) · aware/naive 혼용 · 해석 None · 망가진 해석 값.
3. 감사: 설정값 N 적대값(음수·0·bool·거대값) · 고지 문구에 사용자 원문·비밀 값이 실리지 않는다 ·
   기간 전용 지표·enum 밖 지표는 호출 0 또는 버림.

결함 재현은 `xfail(strict=True, reason="V6-n …")`이다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.config import CompositeConfig
from src.domain import disclosure as disc
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.routing.registry import get_registry
from tests.test_orchestration.test_plan134_w6_body import _cfg, _Gateway, _ranked, _texts
from tests.test_orchestration.test_plan134_w34_verify_schema import _Capture
from tests.test_orchestration.test_plan134_w34_verify_schema import _cfg as _schema_cfg
from tests.test_orchestration.test_plan134_w34_verify_schema import (  # noqa: F401 — fixture
    contract as contract,
)
from tests.test_orchestration.test_plan134_w567_body_verify import GW_ROOT

ANCHOR = datetime(2026, 10, 6, 10, 30, tzinfo=KST)


def _iso(query: str, *hosts: str, anchor: datetime = ANCHOR) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": None, "limit": None},
            "time_resolution": resolve_query_time(query, anchor).to_state(),
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": query, "user_query": query}


# ── 1. 계약 ────────────────────────────────────────────────────────────────────

_PERIOD_DUMP = """
import json, sys
from apm_gateway.application.fleet_tools import PERIOD_RANKING_METRICS, RANKING_METRICS
sys.stdout.write(json.dumps({"period": list(PERIOD_RANKING_METRICS),
                             "realtime": list(RANKING_METRICS)}))
"""


def test_period_only_metrics_copy_matches_the_gateway() -> None:
    """본체 수동 사본 드리프트 — 게이트웨이가 기간 전용 지표를 늘리거나 바꾸면 여기서 잡는다."""
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))},
           "PYTHONPATH": "."}
    proc = subprocess.run([sys.executable, "-c", _PERIOD_DUMP], cwd=GW_ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    got = json.loads(proc.stdout)
    expected = tuple(m for m in got["period"] if m not in got["realtime"])
    assert aq.PERIOD_ONLY_RANKING_METRICS == expected
    # 레지스트리 enum 끝 = 기간 전용 지표(순서까지 — 분해 프롬프트 선택지)
    view = next(v for v in get_registry().views_of("apm") if v.id == "apm.ranking")
    choices = next(a.choices for a in view.args if a.name == "metric")
    assert tuple(choices[-len(expected):]) == expected


def _windowed_cases() -> list[tuple[str, str, str, dict, tuple[str, ...]]]:
    """(id, 보기, 원문, 조건, 대상) — 기간이 실리는 보기 전부 × 기간 2종 × 대상 모양."""
    out = []
    for view in get_registry().views_of("apm"):
        if view.window not in ("range", "hourly", "compare"):
            continue
        queries = (["web01 지난주 대비"] if view.window == "compare"
                   else ["web01 10월 3일", "web01 지난달"])
        targets: list[tuple[str, tuple[str, ...]]] = (
            [("host1", ("web01",)), ("hosts2", ("web01", "web02")), ("first-hop", ())]
            if view.required_input else [("none", ())])
        args: dict = {}
        if view.reference == "guid":  # GUID 추적 — 사용자가 GUID를 적은 경우만 호출된다
            targets, args = [("host1", ("web01",))], {"guid": "8812345678901234567"}
        for q in queries:
            for tname, hosts in targets:
                out.append((f"{view.id}|{q}|{tname}", view.id, q, args, hosts))
    ranking = next(v for v in get_registry().views_of("apm") if v.id == "apm.ranking")
    for metric in next(a.choices for a in ranking.args if a.name == "metric"):
        out.append((f"apm.ranking|어제|{metric}", "apm.ranking", "어제",
                    {"metric": metric, "n": 5}, ()))
    return out


_WINDOWED = _windowed_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _WINDOWED, ids=[c[0] for c in _WINDOWED])
async def test_resolved_window_arguments_match_the_gateway_schema(case, contract,
                                                                  monkeypatch) -> None:
    from jsonschema import Draft202012Validator

    cid, vid, query, args, hosts = case
    cap = _Capture()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", cap.factory())
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "sub_query": query,
                            "views": [vid], "input_from": []}
    if args:
        task["view_args"] = {vid: args}
    await aq.run_apm_query(task, _iso(query, *hosts), llm=None, app_config=_schema_cfg())
    sent = [(n, a) for n, a in cap.calls if not n.startswith("apm_job_")]
    view = next(v for v in get_registry().views_of("apm") if v.id == vid)
    assert any(n == view.tool for n, _ in sent), (cid, sent)
    for name, call in sent:
        schema = contract["schemas"][name]
        assert not set(call) - set(schema["properties"]), (cid, name, call)
        assert not set(schema.get("required") or []) - set(call), (cid, name, call)
        assert not [e.message for e in Draft202012Validator(schema).iter_errors(call)], (cid,
                                                                                        call)
        if name == view.tool and view.window != "compare":
            ref = call.get("reference_time")
            assert ref is None or datetime.fromisoformat(ref).utcoffset() == timedelta(hours=9), (
                cid, ref)
            assert isinstance(call.get("lookback_minutes"), int) and call["lookback_minutes"] > 0
        if name == "apm_fleet" and call.get("mode") == "ranking" and vid != "apm.ranking":
            # 첫 홉 순위 — 실시간 · 기간 인자 없음
            assert "reference_time" not in call and "lookback_minutes" not in call, call


# ── 2. 시간 경계 — A-1 비교 구간 ────────────────────────────────────────────────

def _compare(query: str, anchor: datetime) -> aq.WindowPlan:
    return aq.plan_compare_window(
        aq.QueryTime.from_state(resolve_query_time(query, anchor).to_state()))


def _spans(plan: aq.WindowPlan) -> tuple[datetime, datetime, datetime, datetime]:
    a = plan.args
    return tuple(datetime.fromisoformat(a[k]) for k in (  # type: ignore[return-value]
        "baseline_start", "baseline_end", "current_start", "current_end"))


_NO_OVERLAP_OK = [
    ("web01 지난주 대비 처리량", datetime(2026, 10, 6, 10, 30, tzinfo=KST)),
    ("web01 어제와 오늘 처리량 비교", datetime(2026, 10, 6, 0, 0, 30, tzinfo=KST)),
    ("web01 지난달 대비 처리량", datetime(2026, 10, 31, 23, 59, 59, tzinfo=KST)),
    ("web01 9월과 8월 처리량 비교", datetime(2026, 10, 1, 0, 5, tzinfo=KST)),
    ("web01 이번 주 처리량 지난주와 비교", datetime(2026, 10, 5, 1, 0, 1, tzinfo=KST)),
    ("web01 오늘 처리량 비교", datetime(2026, 3, 1, 9, 15, tzinfo=KST)),
]


@pytest.mark.parametrize("query,anchor", _NO_OVERLAP_OK, ids=[f"{q}@{a:%m%d-%H%M}" for q, a in
                                                              _NO_OVERLAP_OK])
def test_a1_windows_are_ordered_disjoint_and_end_on_the_hour(query, anchor) -> None:
    plan = _compare(query, anchor)
    if plan.mode == "ask":
        assert plan.note in (aq.COMPARE_ASK_NOTE, aq.COMPARE_ELAPSED_NOTE)
        return
    bs, be, cs, ce = _spans(plan)
    assert bs < be <= cs < ce, (query, plan.args)
    assert ce <= anchor and ce.minute == ce.second == 0
    assert all(x.utcoffset() == timedelta(hours=9) for x in (bs, be, cs, ce))


_OVERLAP = [
    # 이번 달 진행 중(10/31 23:59) — 기준 = 9/1 + 30일 23시간 = 10/1 23:00(비교 구간 10/1과 겹침)
    ("web01 이번 달 처리량 비교", datetime(2026, 10, 31, 23, 59, 59, tzinfo=KST)),
    # 3/29 — 2월(28일) 뒤 달의 같은 경과 시간이 3월로 넘어간다
    ("web01 이번 달 처리량 비교", datetime(2026, 3, 29, 10, 30, tzinfo=KST)),
    # 「최근 7일」이 진행 중으로 판정되면 한 단위(1일) 앞 — 기준 10/24~10/31이 비교와 6일 겹침
    ("web01 최근 7일 처리량 비교", datetime(2026, 11, 1, 0, 0, tzinfo=KST)),
]


@pytest.mark.parametrize("query,anchor", _OVERLAP, ids=[f"{q}@{a:%m%d-%H%M}" for q, a in
                                                        _OVERLAP])
def test_v6_1_compare_baseline_never_overlaps_current(query, anchor) -> None:
    """V6-1 — 앞 단위가 더 짧으면(월말) 기준 끝을 이번 기간 시작에서 자른다 · 롤링은 바로 앞."""
    plan = _compare(query, anchor)
    assert plan.mode == "window", plan
    bs, be, cs, ce = _spans(plan)
    assert be <= cs, (query, plan.args)


def test_v6_1_period_ending_on_the_anchor_is_past_not_in_progress() -> None:
    """V6-1 경계 정각(11/1 00:00) 「지난달 대비」 — 끝 = 기준 시각인 기간은 지난 기간이다(진행 중
    오판 금지 · 종전 기준 9/1~10/2 겹침). 지난 기간이면 비교는 바로 뒤 기간 지금까지인데 그 경과가
    0이라 조회하지 않고 사유(경과 0)로 되묻는다 — 기준·비교가 겹치는 창을 만들지 않는다.

    (교정 2 — 검증 라운드는 이 사례를 `_OVERLAP`의 window 단언으로 두었으나 팀 리드 규칙 1(끝 =
    기준 시각은 지난 기간)을 따르면 비교 구간 길이가 0이라 되묻기가 맞다. 단언을 분리했다.)
    """
    plan = _compare("web01 지난달 대비 처리량", datetime(2026, 11, 1, 0, 0, tzinfo=KST))
    assert plan.mode == "ask" and plan.args == {}
    assert plan.note == aq.COMPARE_ELAPSED_NOTE
    # 1시간 지나면 지난달(10월) 첫 1시간 vs 11/1 00~01시 — 겹치지 않는다
    plan = _compare("web01 지난달 대비 처리량", datetime(2026, 11, 1, 1, 0, tzinfo=KST))
    bs, be, cs, ce = _spans(plan)
    assert (bs, be, cs, ce) == (datetime(2026, 10, 1, tzinfo=KST),
                                datetime(2026, 10, 1, 1, tzinfo=KST),
                                datetime(2026, 11, 1, tzinfo=KST),
                                datetime(2026, 11, 1, 1, tzinfo=KST))


def test_v6_4_rolling_period_compare_compares_the_requested_days() -> None:
    plan = _compare("web01 최근 7일 처리량 비교", ANCHOR)
    assert plan.mode == "window", plan
    bs, be, cs, ce = _spans(plan)
    # 요청한 7일(9/29~10/6)이 한쪽 구간에 통째로 들어가야 한다
    req = (datetime(2026, 9, 29, tzinfo=KST), datetime(2026, 10, 6, tzinfo=KST))
    assert (bs <= req[0] and be >= req[1]) or (cs <= req[0] and ce >= req[1]), plan.args


def test_a1_nothing_elapsed_asks_back_without_a_call() -> None:
    """월요일 00:00:30 「지난주 대비」 — 비교 구간 길이 0 → 되묻기(조회 0) · 사유 전용
    문구(V6-5)."""
    plan = _compare("web01 지난주 대비 처리량", datetime(2026, 10, 5, 0, 0, 30, tzinfo=KST))
    assert plan.mode == "ask" and plan.args == {}
    assert plan.note == aq.COMPARE_ELAPSED_NOTE


# ── 2. 시간 경계 — aware/naive · 해석 None · 망가진 해석 ─────────────────────────

@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))

    def install(replies: dict | None = None) -> _Gateway:
        g = _Gateway(replies or {})
        monkeypatch.setattr(aq, "_SESSION_FACTORY", g.factory())
        return g

    return install


def _legacy(*hosts: str, time_range: dict | None) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": time_range},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}


async def _run(isolated: dict, views: list[str], *, now: datetime | None = None,
               cfg: Any = None, **task: Any) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": views, **task},
                                  isolated, llm=None, app_config=cfg or _cfg(), now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [datetime(2026, 10, 6, 10, 30), None], ids=["naive", "none"])
async def test_legacy_caller_without_resolution_keeps_working(gw, now) -> None:
    g = gw()
    await _run(_legacy("web01", time_range={"start": "2026-10-03", "end": "2026-10-03"}),
               ["apm.runtime"], now=now)
    (call,) = g.named("apm_runtime_health")
    assert (call["reference_time"], call["lookback_minutes"]) == ("2026-10-04T00:00:00", 1440)


@pytest.mark.asyncio
async def test_v6_6_aware_now_without_resolution_is_not_a_type_error(gw) -> None:
    g = gw()
    await _run(_legacy("web01", time_range={"start": "2026-10-03", "end": "2026-10-03"}),
               ["apm.runtime"], now=ANCHOR)
    assert g.named("apm_runtime_health")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"version": 2}, "garbage", {"version": 1, "anchor_at": "2026-10-06T10:30:00"},
    {"version": 1, "anchor_at": "x"},
    {"version": 1, "anchor_at": "2026-10-06T10:30:00+09:00", "metric": {"x": 1}, "event": None},
], ids=["version", "str", "naive-anchor", "bad-anchor", "bad-metric"])
async def test_malformed_resolution_falls_back_to_the_parser_range(gw, bad) -> None:
    g = gw()
    iso = _legacy("web01", time_range={"start": "2026-10-03", "end": "2026-10-03"})
    iso["time_resolution"] = bad
    await _run(iso, ["apm.runtime"], now=datetime(2026, 10, 6, 10, 30))
    (call,) = g.named("apm_runtime_health")
    assert call["lookback_minutes"] == 1440


@pytest.mark.asyncio
async def test_non_kst_anchor_resolution_is_converted_not_mixed(gw) -> None:
    g = gw()
    iso = _legacy("web01", time_range=None)
    iso["time_resolution"] = resolve_query_time(
        "web01 어제 응답시간", datetime(2026, 10, 6, 1, 30, tzinfo=UTC)).to_state()
    await _run(iso, ["apm.runtime"], now=datetime(2026, 10, 6, 10, 30))
    (call,) = g.named("apm_runtime_health")
    assert (call["reference_time"], call["lookback_minutes"]) == ("2026-10-06T00:00:00+09:00",
                                                                  1440)


# ── 3. 감사 — 설정 N · enum · 고지 문구 ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("value,sent", [(-5, 20), (0, 20), (True, 20), (False, 20), ("7", 20),
                                        (3, 3), (10**9, 10**9)])
async def test_untargeted_top_n_setting_adversarial_values(gw, value, sent) -> None:
    g = gw({"apm_fleet": lambda a: _ranked(min(a["n"], 30), total=30)})
    res = await _run(_legacy(time_range=None), ["apm.app_health"], cfg=_cfg(top_n=value))
    (rank,) = g.named("apm_fleet")
    assert rank["n"] == sent and isinstance(rank["n"], int) and not isinstance(rank["n"], bool)
    (health,) = g.named("apm_app_health")
    assert len(health["targets"]) == min(sent, 30)
    assert res["source_status"][0]["status"] == "ok"


def test_top_n_env_setting_is_read(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSITE_APM_UNTARGETED_TOP_N", "7")
    assert CompositeConfig(_env_file=None).apm_untargeted_top_n == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("metric,sent,noticed", [
    ("tps; DROP TABLE x", "response_time_avg_ms", True),
    ("__class__", "response_time_avg_ms", True),
    (5, "response_time_avg_ms", True),
    (True, "response_time_avg_ms", True),
    ("calls\n", "calls", False),  # 앞뒤 공백 정리 — enum 값으로 정규화된다
], ids=["sql", "dunder", "int", "bool", "newline"])
async def test_ranking_metric_outside_the_enum_is_dropped_not_sent(gw, metric, sent,
                                                                   noticed) -> None:
    """enum 밖 지표는 게이트웨이로 가지 않는다 — 기본 지표로 바꾸고 「조건 미반영」을 고지한다."""
    g = gw()
    res = await _run(_iso("어제"), ["apm.ranking"],
                     view_args={"apm.ranking": {"metric": metric, "n": 5}})
    (call,) = g.named("apm_fleet")
    assert call["metric"] == sent
    assert bool(_texts(res, disc.APM_UNRESOLVED_CONDITION)) is noticed


_SECRET_QUERY = "web01 지난주 대비 처리량 password=hunter2 토큰 sk-LIVE-9f8e7d"


@pytest.mark.asyncio
@pytest.mark.parametrize("query,views,view_args", [
    (_SECRET_QUERY, ["apm.period_compare"], None),
    ("web01 처리량 비교 password=hunter2 토큰 sk-LIVE-9f8e7d", ["apm.period_compare"], None),
    ("실패율 높은 WAS password=hunter2 sk-LIVE-9f8e7d", ["apm.ranking"],
     {"apm.ranking": {"metric": "failure_rate"}}),
    ("WAS 응답시간 password=hunter2 sk-LIVE-9f8e7d", ["apm.app_health"], None),
], ids=["compare", "compare-ask", "period-only-ask", "first-hop"])
async def test_notices_do_not_echo_the_user_text_or_secrets(gw, query, views, view_args) -> None:
    gw({"apm_fleet": lambda a: _ranked(min(a["n"], 30), total=30)})
    iso = _iso(query, *(("web01",) if "web01" in query else ()))
    res = await _run(iso, views, **({"view_args": view_args} if view_args else {}))
    texts = [d["text"] for d in res.get("disclosures") or []]
    texts += list((res.get("apm_query") or {}).get("notes") or [])
    assert texts, res
    for t in texts:
        assert "hunter2" not in t and "sk-LIVE" not in t and "password" not in t, t


@pytest.mark.asyncio
async def test_period_ranking_full_flag_and_n_are_forwarded_verbatim(gw) -> None:
    """기간 순위 `full` — 게이트웨이가 범위 안 인스턴스 전부를 부른다(호출 수 = 인스턴스 수 ·
    작업 승격은 게이트웨이 `expect_calls`). 본체는 `full`·`n`을 그대로 싣고 기간 인자를 붙인다."""
    g = gw()
    await _run(_iso("어제"), ["apm.ranking"],
               view_args={"apm.ranking": {"metric": "calls", "full": True}})
    (call,) = g.named("apm_fleet")
    assert call["full"] is True and call["metric"] == "calls"
    assert (call["reference_time"], call["lookback_minutes"]) == ("2026-10-06T00:00:00+09:00",
                                                                  1440)
