"""plans/134 W6 본체 — M-7 기간 정본 · A-1 두 기간 비교 · A-4 기간 순위 · ④ 대상 미지정 상위 N.

고정하는 계약(가짜 LLM 0 · 모의 게이트웨이 세션 — 실 제니퍼·실 게이트웨이 0):
  M-7  창은 요청 시간 해석(`time_resolution` · D-309)에서 — 하루 넘게 지난 기간도 조회한다(거부
       폐지) · 반개구간 `[start, end)` 길이가 `lookback_minutes` · 끝이 기준 시각과 1분 안이면
       `reference_time` null · 「지금」·기간 없음 = 현재값 · 해석 없으면 파서 `time_range`(종전) ·
       보기 단위 창이라 배치 1호출 유지 · 추세 간격은 검증된 허용값이 없으면 고르지 않고 알린다
       (W10) · 사용자가 말한 간격은 바꾸지 않는다 · 빈 결과를 「보존 기간 만료」라 부르지 않는다.
  A-1  `apm.period_compare` — 기간 2개 = 이른 쪽 기준 · 1개 = 규칙(진행 중이면 앞 단위 같은 경과
       시간 — 이번 기간 시작을 넘지 않음 · 롤링 「최근 N일」이면 바로 앞 같은 길이 · 지난 기간이면
       바로 뒤 기간 지금까지) · 0개 = 되묻기(조회 0) · 경과 0 = 사유 전용 되묻기 · 계산은
       게이트웨이 값 그대로 결정적 줄.
  A-4  `apm.ranking` 기간이면 `reference_time`·`lookback_minutes` · `window_mode` period/current
       줄 · 잠정 줄 · 표본 상위 N을 「전체 순위」라 부르지 않는다.
  ④   대상 필수 보기에 대상이 없으면 첫 홉 = 부하(TPS) 순위 상위 N(`apm_untargeted_top_n` 기본 20 ·
       실시간 — 기간 인자 없음) · M > N 의무 고지 · M ≤ N 「전체 M개」 · 대상 지정은 종전 ·
       `full`이면 전부 · 순위 실패·0건이면 목록 앞 N(「부하 순이 아님」) · 잠정 고지.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import CompositeConfig, DBHubConfig
from src.domain import disclosure as disc
from src.domain.query_time import QueryTime, resolve_query_time
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.routing.registry import _parse_views
from src.utils.prior_dependency import NOTE_TRACE
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 10, 6, 10, 30, 0)  # 화요일 10:30 KST


def _cfg(top_n: int | None = None) -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    composite: dict[str, Any] = {"max_targets": 10, "fanout_concurrency": 2,
                                 "audit_enabled": False, "task_frame_enabled": False,
                                 "plan_dag_validation_enabled": False}
    if top_n is not None:
        composite["apm_untargeted_top_n"] = top_n
    return SimpleNamespace(
        dbhub=dbhub, composite=SimpleNamespace(**composite),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:30:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 게이트웨이 — 도구별 응답(함수 가능 · 없으면 1행) · `targets` 배치는 계약 A-2로 흉내."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        reply = self.replies.get(name)
        if callable(reply):
            reply = reply(args)
        return reply if reply is not None else _env(name, [{"tool_row": name}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))

    def install(replies: dict | None = None) -> _Gateway:
        gw = _Gateway(replies or {})
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(*hosts: str, query: str | None = None, time_range: dict | None = None) -> dict:
    out: dict[str, Any] = {
        "parsed_requirements": {
            "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
            "time_range": time_range},
        "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}
    if query is not None:
        out["time_resolution"] = resolve_query_time(query, NOW).to_state()
    return out


async def _run(views: list[str], isolated: dict, *, cfg: SimpleNamespace | None = None,
               **task: Any) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": views, **task},
                                  isolated, llm=None, app_config=cfg or _cfg(), now=NOW)


def _texts(res: dict, kind: str) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or [] if d["kind"] == kind]


def _views() -> dict[str, Any]:
    return {v.id: v for v in aq.apm_views()}


def _qt(query: str) -> QueryTime:
    qt = QueryTime.from_state(resolve_query_time(query, NOW).to_state())
    assert qt is not None
    return qt


# ── M-7 기간 정본 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_m7_period_more_than_a_day_ago_is_queried_with_aware_bounds(gateway) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    await _run(["apm.app_health"], _isolated("web01", query="지난주 응답시간"))
    (args,) = gw.named("apm_app_health")
    # 지난주 = [9/28 00:00, 10/5 00:00) KST — 반개구간 길이 7일 · 끝은 기준 시각과 멀어 reference
    assert (args["reference_time"], args["lookback_minutes"]) == (
        "2026-10-05T00:00:00+09:00", 7 * 1440)


def test_m7_half_open_yesterday_and_in_progress_today() -> None:
    view = _views()["apm.app_health"]
    yesterday = aq.plan_window(view, None, NOW, query_time=_qt("어제 응답시간"))
    assert yesterday.args == {"reference_time": "2026-10-06T00:00:00+09:00",
                              "lookback_minutes": 1440}
    today = aq.plan_window(view, None, NOW, query_time=_qt("오늘 응답시간"))
    assert today.args == {"reference_time": None, "lookback_minutes": 630}, \
        "끝이 기준 시각과 1분 안이면 reference_time null(진행 중 기간 = 지금까지)"


@pytest.mark.parametrize("query", ["지금 응답시간", "응답시간 보여줘"])
def test_m7_present_or_no_period_is_the_current_value(query: str) -> None:
    plan = aq.plan_window(_views()["apm.app_health"], None, NOW, query_time=_qt(query))
    assert plan.mode == "current" and plan.args == {}


def test_m7_without_resolution_uses_the_parser_range_half_open() -> None:
    """옛 체크포인트(해석 없음) — 파서 `time_range`. 날짜만 준 끝은 다음 날 0시(반개구간)."""
    view = _views()["apm.app_health"]
    plan = aq.plan_window(view, {"start": "2026-10-01", "end": "2026-10-02"}, NOW)
    assert plan.args == {"reference_time": "2026-10-03T00:00:00", "lookback_minutes": 2 * 1440}
    assert aq.plan_window(view, None, NOW).mode == "current"


def test_m7_current_view_with_a_period_discloses_current_only() -> None:
    plan = aq.plan_window(_views()["apm.pool"], None, NOW, query_time=_qt("어제 커넥션 풀"))
    assert plan.mode == "current" and plan.kind == disc.APM_CURRENT_ONLY


@pytest.mark.asyncio
async def test_m7_window_is_per_view_so_a_batch_stays_one_call(gateway) -> None:
    gw = gateway()
    await _run(["apm.app_health", "apm.events"],
               _isolated("web01", "web02", "web03", query="어제 응답시간"))
    for tool in ("apm_app_health", "apm_events"):
        (call,) = gw.named(tool)
        assert len(call["targets"]) == 3 and call["lookback_minutes"] == 1440, tool


@pytest.mark.asyncio
async def test_m7_long_window_without_verified_intervals_discloses_w10(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.runtime"], _isolated("web01", query="지난주 힙 추세"))
    (args,) = gw.named("apm_runtime_health")
    assert "interval_minute" not in args, "검증된 허용값이 없으면 고르지 않는다"
    (note,) = [t for t in _texts(res, NOTE_TRACE) if "추세 간격 미지정" in t]
    assert "허용 간격 값이 확인되지 않아(W10)" in note


@pytest.mark.asyncio
async def test_m7_user_interval_is_never_changed(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.runtime"], _isolated("web01", query="지난주 힙 추세"),
                     view_args={"apm.runtime": {"interval_minute": 5}})
    (args,) = gw.named("apm_runtime_health")
    assert args["interval_minute"] == 5
    assert not [t for t in _texts(res, NOTE_TRACE) if "추세 간격" in t]


def test_m7_verified_intervals_pick_the_smallest_that_fits() -> None:
    runtime = _views()["apm.runtime"]
    arg = next(a for a in runtime.args if a.name == "interval_minute")
    assert arg.verified_values == (), "허용값은 아직 검증 전(W10) — 레지스트리에 선언 없음"
    verified = dataclasses.replace(runtime, args=tuple(
        dataclasses.replace(a, verified_values=(5, 60, 1440)) if a is arg else a
        for a in runtime.args))
    week = aq.WindowPlan("window", {"reference_time": None, "lookback_minutes": 7 * 1440})
    plan, note = aq.auto_interval(verified, week, None)
    assert plan.args["interval_minute"] == 60 and "60분 간격" in note
    day = aq.WindowPlan("window", {"reference_time": None, "lookback_minutes": 1440})
    assert aq.auto_interval(verified, day, None) == (day, ""), "하루 이하는 종전 기본 간격"
    assert aq.auto_interval(verified, week, {"interval_minute": 5}) == (week, "")


def _view_with_verified(values: Any) -> list[dict]:
    return [{"id": "apm.x", "label": "x", "capability": "was_performance", "tool": "apm_x",
             "window": "range", "required_input": "hostname",
             "args": [{"name": "interval_minute", "type": "int", "min": 1, "label": "간격",
                       "verified_values": values}]}]


def test_registry_accepts_verified_values() -> None:
    (view,) = _parse_views(_view_with_verified([5, 60, 1440]))
    assert view.args[0].verified_values == (5, 60, 1440)


@pytest.mark.parametrize("values", [[0, 5], [60, 5], [5, 5], ["5"], [True]])
def test_registry_rejects_bad_verified_values(values: list) -> None:
    raw = _view_with_verified(values)
    with pytest.raises(ValueError, match="verified_values"):  # 로더가 RegistryError로 감싼다
        _parse_views(raw)


@pytest.mark.asyncio
async def test_m7_empty_old_period_is_not_called_expired(gateway) -> None:
    gateway({"apm_slow_transactions": _env("apm_slow_transactions", [])})
    res = await _run(["apm.slow_tx"], _isolated("web01", query="지난달 느린 거래"))
    assert aq.EMPTY_PERIOD_NOTE in _texts(res, NOTE_TRACE)
    assert "보존 기간 만료" not in json.dumps(res, ensure_ascii=False)


# ── A-1 두 기간 비교 ────────────────────────────────────────────────────────────

def test_a1_two_periods_earlier_is_the_baseline() -> None:
    plan = aq.plan_compare_window(_qt("어제와 오늘 응답시간 비교"))
    assert plan.args == {"current_start": "2026-10-06T00:00:00+09:00",
                         "current_end": "2026-10-06T10:00:00+09:00",
                         "baseline_start": "2026-10-05T00:00:00+09:00",
                         "baseline_end": "2026-10-05T10:00:00+09:00"}
    assert "기준 구간을 같은 경과 시간으로 맞춤" in plan.note
    assert "진행 중인 시간대는 시 단위 통계라 뺐습니다" in plan.note
    assert plan.kind == NOTE_TRACE


def test_a1_one_past_period_is_compared_with_the_next_period_so_far() -> None:
    plan = aq.plan_compare_window(_qt("지난주 대비 처리량"))
    assert plan.args == {"current_start": "2026-10-05T00:00:00+09:00",
                         "current_end": "2026-10-06T10:00:00+09:00",
                         "baseline_start": "2026-09-28T00:00:00+09:00",
                         "baseline_end": "2026-09-29T10:00:00+09:00"}
    assert "바로 뒤 기간(지금까지)과 비교" in plan.note


def test_a1_one_in_progress_period_is_compared_with_the_previous_unit() -> None:
    plan = aq.plan_compare_window(_qt("이번 주 처리량 비교"))
    assert (plan.args["baseline_start"], plan.args["baseline_end"]) == (
        "2026-09-28T00:00:00+09:00", "2026-09-29T10:00:00+09:00")
    assert "앞 기간의 같은 경과 시간과 비교" in plan.note


def test_a1_two_complete_periods_keep_their_lengths() -> None:
    plan = aq.plan_compare_window(_qt("지지난주와 지난주 비교"))
    assert plan.args == {"current_start": "2026-09-28T00:00:00+09:00",
                         "current_end": "2026-10-05T00:00:00+09:00",
                         "baseline_start": "2026-09-21T00:00:00+09:00",
                         "baseline_end": "2026-09-28T00:00:00+09:00"}
    assert "맞춤" not in plan.note and "진행 중" not in plan.note


@pytest.mark.parametrize("query", ["처리량 비교해줘", None])
def test_a1_no_period_asks_back(query: str | None) -> None:
    plan = aq.plan_compare_window(_qt(query) if query else None)
    assert plan.mode == "ask" and plan.note == aq.COMPARE_ASK_NOTE


@pytest.mark.asyncio
async def test_a1_no_period_is_not_queried_and_asks_back(gateway) -> None:
    gw = gateway()
    res = await _run(["apm.period_compare"], _isolated("web01", query="web01 처리량 비교"))
    assert gw.calls == [], "되묻기 — 게이트웨이를 부르지 않는다"
    assert aq.COMPARE_ASK_NOTE in " ".join(_texts(res, disc.APM_UNRESOLVED_CONDITION))


_COMPARE_SUMMARY = {
    "baseline": {"hour_start": "2026-09-28T00:00:00+09:00",
                 "hour_end": "2026-09-29T10:00:00+09:00", "calls": 1000, "failures": 10,
                 "failure_rate": 0.01, "avg_response_ms": 200.0, "max_response_ms": 900.0},
    "current": {"hour_start": "2026-10-05T00:00:00+09:00",
                "hour_end": "2026-10-06T10:00:00+09:00", "calls": 1200, "failures": 36,
                "failure_rate": 0.03, "avg_response_ms": 250.0, "max_response_ms": None},
    "delta": {"calls": {"abs": 200, "pct": 20.0}, "failure_rate": {"abs": 2.0, "pct": 200.0},
              "avg_response_ms": {"abs": 50.0, "pct": 25.0},
              "max_response_ms": {"abs": None, "pct": None}},
}


@pytest.mark.asyncio
async def test_a1_compare_call_and_deterministic_line(gateway) -> None:
    gw = gateway({"apm_period_compare": _env("apm_period_compare", [],
                                             summary=_COMPARE_SUMMARY)})
    res = await _run(["apm.period_compare"], _isolated("web01", query="web01 지난주 대비 처리량"))
    (args,) = gw.named("apm_period_compare")
    assert args["hostname"] == "web01"
    assert (args["baseline_start"], args["current_end"]) == (
        "2026-09-28T00:00:00+09:00", "2026-10-06T10:00:00+09:00")
    (line,) = [x for x in res["answer_lines"] if "기준" in x]
    assert ("기준 2026-09-28T00:00:00+09:00 ~ 2026-09-29T10:00:00+09:00 → 비교"
            " 2026-10-05T00:00:00+09:00 ~ 2026-10-06T10:00:00+09:00") in line
    assert "호출 1,000 → 1,200(+20.0%)" in line
    assert "실패율 1.0% → 3.0%(+2.0%p)" in line
    assert "평균 응답(호출 수 가중) 200ms → 250ms(+25.0%)" in line
    assert "최대 응답 900ms → N/A(N/A)" in line, "값이 없으면 N/A — 0으로 채우지 않는다"


@pytest.mark.asyncio
async def test_a1_two_hosts_are_one_batch_call(gateway) -> None:
    gw = gateway({"apm_period_compare": lambda a: _env("apm_period_compare", [],
                                                       summary=_COMPARE_SUMMARY)})
    res = await _run(["apm.period_compare"],
                     _isolated("web01", "web02", query="어제와 오늘 응답시간 비교"))
    (call,) = gw.named("apm_period_compare")
    assert [t["hostname"] for t in call["targets"]] == ["web01", "web02"]
    assert call["baseline_start"] == "2026-10-05T00:00:00+09:00"
    assert len([x for x in res["answer_lines"] if "기준" in x]) == 2, "대상별 한 줄"


# ── A-4 기간 순위 ───────────────────────────────────────────────────────────────

def _ranking(summary: dict, **extra: Any) -> dict:
    rows = [{"rank": 1, "source_id": "s1", "instance_id": 1, "instance_name": "w1",
             "hostname": "web01", "value": 300.0}]
    return _env("apm_fleet", rows, summary={"metric": "response_time_avg_ms", "order": "desc",
                                            "instances_total": 40, "instances_ranked": 38,
                                            "instances_unranked": 2, "domains_failed": 0,
                                            **summary}, **extra)


@pytest.mark.asyncio
async def test_a4_period_ranking_sends_the_window_and_says_period(gateway) -> None:
    gw = gateway({"apm_fleet": _ranking({"window_mode": "period",
                                         "hour_start": "2026-10-05T00:00:00+09:00",
                                         "hour_end": "2026-10-06T00:00:00+09:00"})})
    res = await _run(["apm.ranking"], _isolated(query="어제 응답시간 가장 느렸던 WAS 5개"),
                     view_args={"apm.ranking": {"n": 5}})
    (args,) = gw.named("apm_fleet")
    assert (args["reference_time"], args["lookback_minutes"], args["n"]) == (
        "2026-10-06T00:00:00+09:00", 1440, 5)
    head = res["answer_lines"][0]
    assert ("기간 순위(시 단위 통계 · 조회 구간 2026-10-05T00:00:00+09:00 ~"
            " 2026-10-06T00:00:00+09:00) — 전체 인스턴스 40개 중 상위 1") in head
    assert "전체 순위" not in json.dumps(res, ensure_ascii=False), "표본 상위 N ≠ 전체 순위"


@pytest.mark.asyncio
async def test_a4_metric_without_period_stats_falls_back_to_current_with_a_line(gateway) -> None:
    gateway({"apm_fleet": _ranking({"window_mode": "current", "metric": "heap_used_mb"})})
    res = await _run(["apm.ranking"], _isolated(query="어제 힙 많이 쓴 WAS"),
                     view_args={"apm.ranking": {"metric": "heap_used_mb"}})
    assert "heap_used_mb 내림차순 순위 — 전체 인스턴스 40개" in res["answer_lines"][0]
    assert "기간 순위(시 단위 통계" not in res["answer_lines"][0]
    assert any("요청 기간의 순위가 아니라 현재값 순위입니다" in x for x in res["answer_lines"])


@pytest.mark.asyncio
async def test_a4_present_ranking_has_no_window_and_provisional_is_said(gateway) -> None:
    gw = gateway({"apm_fleet": _ranking({"domains_failed": 2}, provisional=True)})
    res = await _run(["apm.ranking"], _isolated(query="지금 응답시간 느린 WAS"))
    (args,) = gw.named("apm_fleet")
    assert "lookback_minutes" not in args and "reference_time" not in args
    assert any("잠정 순위 — 조회 실패 도메인 2곳 제외" in x for x in res["answer_lines"])
    assert not any("현재값 순위" in x for x in res["answer_lines"]), "기간을 말하지 않았다"


@pytest.mark.asyncio
@pytest.mark.parametrize("metric", aq.PERIOD_ONLY_RANKING_METRICS)
@pytest.mark.parametrize("query", ["실패율 높은 WAS 5개", "지금 실패율 높은 WAS 5개"])
async def test_a4_period_only_metric_without_period_asks_back(gateway, metric, query) -> None:
    """교정 1 — 기간 전용 지표를 기간 없이 보내면 게이트웨이가 거부한다 → 조회하지 않고 되묻는다
    (기본 지표로 바꿔 조회하지 않는다)."""
    gw = gateway()
    res = await _run(["apm.ranking"], _isolated(query=query),
                     view_args={"apm.ranking": {"metric": metric, "n": 5}})
    assert gw.calls == [], "되묻기 — 침묵 대체 조회 없음"
    assert aq.PERIOD_ONLY_ASK_NOTE in " ".join(_texts(res, disc.APM_UNRESOLVED_CONDITION))
    assert res["apm_query"]["unresolved"] == [
        {"view": "apm.ranking", "conditions": ["period"], "queried": False}]


@pytest.mark.asyncio
async def test_a4_period_only_metric_with_period_is_queried_and_rate_is_explained(gateway) -> None:
    gw = gateway({"apm_fleet": _ranking({
        "metric": "failure_rate", "window_mode": "period",
        "requested": {"start": "2026-10-05T00:00:00+09:00", "end": "2026-10-06T00:00:00+09:00"},
        "queried": {"start": "2026-10-05T00:00:00+09:00", "end": "2026-10-06T01:00:00+09:00",
                    "seconds": 90000}})})
    res = await _run(["apm.ranking"], _isolated(query="어제 실패율 높은 WAS 5개"),
                     view_args={"apm.ranking": {"metric": "failure_rate", "n": 5}})
    (args,) = gw.named("apm_fleet")
    assert (args["metric"], args["lookback_minutes"], args["n"]) == ("failure_rate", 1440, 5)
    head = res["answer_lines"][0]
    assert "failure_rate(실패율 · 표의 값은 0~1 비율 — 0.05 = 5%)" in head
    assert "조회 구간 2026-10-05T00:00:00+09:00 ~ 2026-10-06T01:00:00+09:00" in head, \
        "게이트웨이가 시 경계로 넓혀 실제 조회한 구간(queried)"


# ── ④ 대상 미지정 = 부하 순위 상위 N ─────────────────────────────────────────────

def _ranked(count: int, *, total: int | None = None, provisional: int | None = None,
            hostless: tuple[int, ...] = ()) -> dict:
    rows = [{"rank": i, "source_id": "s1", "domain_id": 1, "instance_id": i,
             "instance_name": f"w{i}", "hostname": "" if i in hostless else f"h{i}",
             "value": 100.0 - i} for i in range(1, count + 1)]
    extra: dict[str, Any] = {"provisional": True} if provisional is not None else {}
    return _env("apm_fleet", rows, summary={
        "metric": "tps", "order": "desc", "instances_total": total or count,
        "instances_ranked": total or count, "instances_unranked": 0,
        "domains_failed": provisional or 0}, **extra)


def test_top_n_setting_default_and_env() -> None:
    assert CompositeConfig(_env_file=None).apm_untargeted_top_n == 20


@pytest.mark.asyncio
async def test_untargeted_takes_only_the_top_n_by_load_with_a_mandatory_notice(gateway) -> None:
    gw = gateway({"apm_fleet": lambda a: _ranked(a["n"], total=50)})
    res = await _run(["apm.app_health"], _isolated(query="어제 응답시간"), cfg=_cfg(top_n=3))
    assert [n for n, _ in gw.calls] == ["apm_fleet", "apm_app_health"]
    rank = gw.named("apm_fleet")[0]
    assert (rank["mode"], rank["metric"], rank["order"], rank["n"]) == ("ranking", "tps",
                                                                        "desc", 3)
    assert "lookback_minutes" not in rank and "reference_time" not in rank, \
        "첫 홉 순위는 실시간(기간 질문이어도)"
    (health,) = gw.named("apm_app_health")
    assert [t["hostname"] for t in health["targets"]] == ["h1", "h2", "h3"]
    assert health["lookback_minutes"] == 1440, "본 조회는 요청 기간 그대로"
    # 교정 1 — 건강한 순위로 상위 N만 고른 것은 실패가 아니라 의도된 범위 제한(의무 · 중립)
    (notice,) = [t for t in _texts(res, disc.APM_UNTARGETED_SCOPE) if "대상 서버 미지정" in t]
    assert notice == ("대상 서버 미지정 — 전체 인스턴스 50개 중 현재 부하(TPS) 상위 3개(호스트"
                      " 3대)만 조회했습니다 · 특정 서버(또는 업무)를 지정하면 그 대상만 정확히"
                      " 조회합니다")
    spec = disc.KIND_TABLE[disc.APM_UNTARGETED_SCOPE]
    assert (spec.grade, spec.mandatory, spec.scope) == ("neutral", True, "task")
    assert not [t for t in _texts(res, disc.APM_PARTIAL_SOURCES) if "대상 서버 미지정" in t]
    assert res["source_status"][0]["status"] == "ok", "범위 제한은 부분 실패가 아니다"


@pytest.mark.asyncio
async def test_untargeted_default_top_n_is_twenty(gateway) -> None:
    gw = gateway({"apm_fleet": lambda a: _ranked(a["n"], total=99)})
    await _run(["apm.app_health"], _isolated(), cfg=_cfg())
    assert gw.named("apm_fleet")[0]["n"] == 20
    assert len(gw.named("apm_app_health")[0]["targets"]) == 20


@pytest.mark.asyncio
async def test_untargeted_fleet_not_larger_than_n_is_everything(gateway) -> None:
    gw = gateway({"apm_fleet": _ranked(4, hostless=(4,))})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=20))
    (health,) = gw.named("apm_app_health")
    assert health["targets"][-1] == {"instance_name": "w4", "source_id": "s1", "instance_id": 4}
    (notice,) = [t for t in _texts(res, NOTE_TRACE) if "대상 서버 미지정" in t]
    assert notice == "대상 서버 미지정 — 전체 인스턴스 4개(호스트 3대) 조회"
    assert not [t for t in _texts(res, disc.APM_PARTIAL_SOURCES) if "대상 서버 미지정" in t]


@pytest.mark.asyncio
async def test_targeted_question_keeps_every_target_and_no_ranking(gateway) -> None:
    gw = gateway()
    await _run(["apm.app_health"], _isolated(*[f"web{i:02d}" for i in range(1, 26)]),
               cfg=_cfg(top_n=3))
    assert gw.named("apm_fleet") == []
    assert len(gw.named("apm_app_health")[0]["targets"]) == 25, "말한 대상은 전부"


@pytest.mark.asyncio
async def test_full_from_decomposition_queries_every_instance(gateway) -> None:
    inventory = [{"source_id": "s1", "instance_id": i, "instance_name": f"w{i}",
                  "domain_id": 1, "hostname": f"h{i}", "match_confidence": "high"}
                 for i in range(1, 8)]
    gw = gateway({"apm_instance_map": _env("apm_instance_map", inventory)})
    res = await _run(["apm.active"], _isolated(), cfg=_cfg(top_n=3),
                     view_args={"apm.active": {"full": True}})
    assert gw.named("apm_fleet") == [], "`full`이면 순위 없이 전부(종전 W3 경로)"
    assert len(gw.named("apm_active_services")[0]["targets"]) == 7
    (notice,) = [t for t in _texts(res, NOTE_TRACE) if "대상 서버 미지정" in t]
    assert notice == "대상 서버 미지정 — 전체 인스턴스 7개(호스트 7대) 조회(전부 조회 요청)"


@pytest.mark.asyncio
@pytest.mark.parametrize("rank_reply", [
    {"error": "api_error", "reason": "순위 실패", "tool": "apm_fleet"},
    _env("apm_fleet", []),
], ids=["error", "zero"])
async def test_ranking_failure_falls_back_to_the_list_head_not_everything(
        gateway, rank_reply) -> None:
    inventory = [{"source_id": "s1", "instance_id": i, "instance_name": f"w{i}",
                  "domain_id": 1, "hostname": f"h{i}", "match_confidence": "high"}
                 for i in range(1, 11)]
    gw = gateway({"apm_fleet": rank_reply,
                  "apm_instance_map": _env("apm_instance_map", inventory)})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=3))
    assert [n for n, _ in gw.calls] == ["apm_fleet", "apm_instance_map", "apm_app_health"]
    assert [t["hostname"] for t in gw.named("apm_app_health")[0]["targets"]] == [
        "h1", "h2", "h3"], "조용히 전체로 넓히지 않는다"
    (notice,) = [t for t in _texts(res, disc.APM_PARTIAL_SOURCES) if "대상 서버 미지정" in t]
    assert "부하 순위를 받지 못해" in notice and "인스턴스 목록 순서 앞 3개" in notice
    assert "부하 순이 아닙니다" in notice and "목록 인스턴스 10개" in notice


@pytest.mark.asyncio
async def test_provisional_ranking_is_a_mandatory_notice(gateway) -> None:
    gateway({"apm_fleet": _ranked(2, provisional=1)})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=20))
    (notice,) = [t for t in _texts(res, disc.APM_PARTIAL_SOURCES) if "대상 서버 미지정" in t]
    assert notice == ("대상 서버 미지정 — 전체 인스턴스 2개(호스트 2대) 조회 · 잠정 — 조회 실패"
                      " 도메인 1곳의 인스턴스는 부하 순위에 없습니다")


# ── 교정 2(verify-134-w6 V6-1~V6-8) ──────────────────────────────────────────────

def test_a1_rolling_period_is_the_current_span_and_the_baseline_is_just_before() -> None:
    """V6-4 — 「최근 N일·N시간」은 그 기간이 비교 · 기준은 바로 앞 같은 길이(되묻지 않는다)."""
    days = aq.plan_compare_window(_qt("최근 7일 처리량 비교"))
    assert days.args == {"current_start": "2026-09-29T00:00:00+09:00",
                         "current_end": "2026-10-06T00:00:00+09:00",
                         "baseline_start": "2026-09-22T00:00:00+09:00",
                         "baseline_end": "2026-09-29T00:00:00+09:00"}
    assert "요청 기간과 바로 앞 같은 길이 기간을 비교" in days.note
    hours = aq.plan_compare_window(_qt("최근 3시간 처리량 비교"))
    assert hours.mode == "window"
    # 시 단위 통계라 비교 끝은 정시(10:00) — 기준 04:00~07:00 · 비교 07:00~10:00(겹침 없음)
    assert hours.args == {"current_start": "2026-10-06T07:00:00+09:00",
                          "current_end": "2026-10-06T10:00:00+09:00",
                          "baseline_start": "2026-10-06T04:00:00+09:00",
                          "baseline_end": "2026-10-06T07:00:00+09:00"}
    assert "진행 중인 시간대는 시 단위 통계라 뺐습니다" in hours.note


def test_a1_shorter_previous_month_is_taken_whole_without_overlap() -> None:
    """V6-1 — 3/29 「이번 달」: 2월(28일)의 같은 경과 시간은 3월로 넘어가므로 2월 전체가 기준."""
    qt = QueryTime.from_state(resolve_query_time(
        "이번 달 처리량 비교", datetime(2026, 3, 29, 10, 30)).to_state())
    plan = aq.plan_compare_window(qt)
    assert (plan.args["baseline_start"], plan.args["baseline_end"],
            plan.args["current_start"]) == ("2026-02-01T00:00:00+09:00",
                                            "2026-03-01T00:00:00+09:00",
                                            "2026-03-01T00:00:00+09:00")
    assert "앞 기간이 더 짧아 앞 기간 전체와 비교" in plan.note


@pytest.mark.parametrize("query", ["지난주 대비 처리량", "이번 주 처리량 지난주와 비교",
                                   "이번 주 처리량 비교"])
def test_a1_nothing_elapsed_has_its_own_ask_note_and_both_wordings_agree(query: str) -> None:
    """V6-5 — 월요일 00:00 정각: 지난주 대비 · 이번 주 vs 지난주 · 이번 주 비교가 같은 되묻기."""
    qt = QueryTime.from_state(resolve_query_time(query, datetime(2026, 10, 5)).to_state())
    plan = aq.plan_compare_window(qt)
    assert (plan.mode, plan.note) == ("ask", aq.COMPARE_ELAPSED_NOTE)


def test_v6_2_range_view_rolling_hours_reach_the_anchor_but_hourly_stays() -> None:
    qt = _qt("최근 3시간 응답시간")
    ranged = aq.plan_window(_views()["apm.runtime"], None, NOW, query_time=qt)
    assert ranged.args == {"reference_time": None, "lookback_minutes": 180}
    hourly = aq.plan_window(_views()["apm.app_stats"], None, NOW, query_time=qt)
    assert hourly.args == {"reference_time": "2026-10-06T10:00:00+09:00",
                           "lookback_minutes": 180}, "시 단위 보기는 해석 그대로(정시)"


def test_v6_6_aware_now_without_resolution_uses_kst_bounds() -> None:
    from src.domain.time_spec import KST

    plan = aq.plan_window(_views()["apm.runtime"], {"start": "2026-10-03", "end": "2026-10-03"},
                          NOW.replace(tzinfo=KST))
    assert plan.args == {"reference_time": "2026-10-04T00:00:00+09:00", "lookback_minutes": 1440}


def _two_source_ranked(args: dict) -> dict:
    """h1은 s1·s2 둘 다에 인스턴스가 있지만 순위에는 s1만 · h2는 s2만."""
    rows = [{"rank": 1, "source_id": "s1", "domain_id": 1, "instance_id": 1,
             "instance_name": "w1", "hostname": "h1", "value": 90.0},
            {"rank": 2, "source_id": "s2", "domain_id": 1, "instance_id": 2,
             "instance_name": "w2", "hostname": "h2", "value": 80.0}]
    return _env("apm_fleet", rows, summary={"metric": "tps", "order": "desc",
                                            "instances_total": 9, "instances_ranked": 9,
                                            "instances_unranked": 0, "domains_failed": 0})


def _health_per_source(extra: dict[str, list[int]] | None = None):
    """호스트 · 소스별 인스턴스 행 — `extra`(호스트 → 같은 소스의 순위 밖 인스턴스 id)를
    덧붙인다."""
    layout = {"h1": {"s1": [1], "s2": [11]}, "h2": {"s2": [2]}}

    def reply(args: dict) -> dict:
        host = args.get("hostname")
        sources = args.get("source_ids") or list(layout.get(host, {}))
        rows = [{"hostname": host, "source_id": sid, "instance_id": iid, "tps": 1.0}
                for sid in sources for iid in [*layout.get(host, {}).get(sid, []),
                                               *(extra or {}).get(host, [])]]
        return _env("apm_app_health", rows)

    return reply


@pytest.mark.asyncio
async def test_v6_3_first_hop_hostname_targets_carry_the_ranking_source(gateway) -> None:
    gw = gateway({"apm_fleet": _two_source_ranked, "apm_app_health": _health_per_source()})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=2))
    (health,) = gw.named("apm_app_health")
    assert health["targets"] == [{"hostname": "h1", "source_id": "s1"},
                                 {"hostname": "h2", "source_id": "s2"}]
    got = {(r["source_id"], r["instance_id"]) for r in res["query_results"]}
    assert got == {("s1", 1), ("s2", 2)}, "h1의 순위 밖 s2 인스턴스(11)는 부르지 않는다"
    (notice,) = _texts(res, disc.APM_UNTARGETED_SCOPE)
    assert "실제 조회 인스턴스" not in notice


@pytest.mark.asyncio
async def test_v6_3_more_instances_than_ranked_are_counted_in_the_notice(gateway) -> None:
    """같은 소스의 순위 밖 인스턴스(같은 호스트)는 hostname 대상이 함께 부른다 — 고지에 실제 수."""
    gw = gateway({"apm_fleet": _two_source_ranked,
                  "apm_app_health": _health_per_source({"h1": [21]})})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=2))
    assert len(gw.named("apm_app_health")) == 1
    (notice,) = _texts(res, disc.APM_UNTARGETED_SCOPE)
    assert "상위 2개(호스트 2대 · 실제 조회 인스턴스 3개)만 조회했습니다" in notice, notice
    assert "실제 조회 인스턴스 3개" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_v6_8_first_hop_calls_are_in_the_audit_commands(gateway) -> None:
    from src.orchestration.investigation_audit import _apm_query_fields

    gateway({"apm_fleet": _two_source_ranked, "apm_app_health": _health_per_source()})
    res = await _run(["apm.app_health"], _isolated(), cfg=_cfg(top_n=2))
    commands = _apm_query_fields(res)["commands"]
    assert commands[0] == "apm_fleet(hostname=*, mode=ranking, metric=tps, order=desc, n=2)"
    assert commands[1:] == ['apm_app_health(hostname=h1, source_ids=["s1"])',
                            'apm_app_health(hostname=h2, source_ids=["s2"])']
    step = res["apm_query"]["inserted_steps"][0]
    assert "_host_sources" not in step, "호스트별 소스는 메타에 남기지 않는다"


@pytest.mark.parametrize("value", [-5, 0, "7", 2.5, True])
def test_v6_8_invalid_top_n_setting_warns_one_line(value: Any, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger=aq.__name__):
        got = aq._int_setting(_cfg(top_n=value), "apm_untargeted_top_n", 20)
    assert got == 20
    (record,) = [r for r in caplog.records if "apm_untargeted_top_n" in r.getMessage()]
    assert repr(value) in record.getMessage()


def test_v6_8_valid_or_absent_top_n_does_not_warn(caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger=aq.__name__):
        assert aq._int_setting(_cfg(top_n=7), "apm_untargeted_top_n", 20) == 7
        assert aq._int_setting(_cfg(), "apm_untargeted_top_n", 20) == 20
    assert not [r for r in caplog.records if "apm_untargeted_top_n" in r.getMessage()]
