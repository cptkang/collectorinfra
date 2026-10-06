"""plans/134 W3·W4 독립 검증(verify-134-w34) — 본체 처리기 경계(모의 게이트웨이 세션).

- 관측 소스 인가(`allowed_sources`)가 배치·이름 보기·전 대상 보기 경로의 실행 경계에서 유지된다
  (게이트웨이 세션을 열지 않는다).
- 결함 재현(`xfail(strict=True)` — 고치면 XPASS로 실패하니 표지를 걷는다):
  - V34-1 추세 보기(서비스·업무)에 지표를 말하지 않으면 지표 없이 `apm_metrics` series를 부른다.
    → 팀 리드 결정(body-fix-34 B-1): **기본 지표는 게이트웨이가 정한다**(벤더 카탈로그 지식은
    게이트웨이 소유 · D-274 ③). 본체는 지표를 말하지 않은 task를 `metrics` 없이 그대로 보낸다 —
    단언을 그 방향으로 바꾸고 표지를 걷었다(실프로세스 재현은 게이트웨이 패치 뒤 팀 리드가
    뒤집는다).
  - V34-4 `target: none` 보기(전 대상 순위·이벤트)가 분해 `targets`의 서비스 이름·이번 턴 hostname을
    조용히 버리고 전 대상으로 조회한다(고지 0). → 교정됨(표지 걷음).
  - V34-5 전 대상 이벤트 결정적 줄의 건수가 `n`으로 자른 행 수다(게이트웨이 `summary.events_total`
    미사용 — 「이벤트 1건」인데 실제 5건). → 교정됨(표지 걷음).

실 게이트웨이·실 LLM 0.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from tests.test_orchestration import apm_batch_mock

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 6, 10, 0, 0)


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    def __init__(self, replies: dict | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        reply = self.replies.get(name)
        if callable(reply):
            reply = reply(args)
        return reply if reply is not None else _env(name, [{"tool_row": name,
                                                           "hostname": args.get("hostname")}])

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
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _isolated(*hosts: str, **extra: Any) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts], "time_range": None, "limit": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": "q", "user_query": "q", **extra}


async def _run(views: list[str], isolated: dict | None = None, **task: Any) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                                   "views": views, "input_from": [], **task},
                                  isolated or _isolated(), llm=None, app_config=_cfg(), now=NOW)


def _texts(res: dict) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or []]


# ── 관측 소스 인가 — 실행 경계 ──────────────────────────────────────────────────

_PATHS = [
    ("batch", ["apm.app_health", "apm.events"], ("web01", "web02", "web03"), {}),
    ("named", ["apm.service", "apm.business"], (),
     {"targets": [{"text": "주문", "kind": "auto"}]}),
    ("fleet", ["apm.ranking", "apm.fleet_events"], (), {}),
    ("first-hop", ["apm.pool"], (), {}),
    ("sources", ["apm.app_health"], ("web01", "web02"), {"sources": ["bank"]}),
]


@pytest.mark.parametrize("case", _PATHS, ids=[c[0] for c in _PATHS])
async def test_source_authorization_holds_on_every_new_path(case, gateway) -> None:
    _, views, hosts, task = case
    gw = gateway()
    res = await _run(views, _isolated(*hosts, user_role="user", allowed_sources=["polestar"]),
                     **task)
    assert gw.opened == 0 and gw.calls == [], "게이트웨이 세션을 열지 않는다"
    assert res.get("routing_intent") == "access_denied" and "권한" in res["final_response"], res
    # 관리자 · 허용 목록에 apm이 있으면 조회한다(대조군)
    gw2 = gateway()
    ok = await _run(views, _isolated(*hosts, user_role="user", allowed_sources=["apm"]), **task)
    assert gw2.opened == 1, ok


# ── V34-1 — 추세 보기 지표 없음 ────────────────────────────────────────────────

@pytest.mark.parametrize("view,target", [("apm.service_trend", "주문"),
                                         ("apm.business_trend", "결제")])
async def test_v34_1_trend_view_sends_metrics_or_asks(view, target, gateway) -> None:
    """V34-1 본체 쪽(팀 리드 결정 — 기본 지표는 게이트웨이가 정한다 · D-274 ③).

    지표를 말하지 않은 추세 task는 되묻지 않고 `metrics` 없이 1회 보낸다(본체가 벤더 기본 지표를
    지어내지 않는다).
    """
    gw = gateway()
    res = await _run([view], targets=[{"text": target, "kind": "auto"}],
                     view_args={view: {"interval_minute": 10}})
    (sent,) = gw.named("apm_metrics")
    assert "metrics" not in sent, sent
    assert sent["mode"] == "series" and sent["interval_minute"] == 10, sent
    assert not res.get("error"), res.get("final_response")


def test_v34_1_registry_examples_teach_metric_less_trends() -> None:
    """레지스트리 예시 사실 고정(결함 근거) — 두 추세 보기 예시에 metrics가 없다 · 기본값도 없다."""
    views = {v.id: v for v in aq.apm_views()}
    for vid in ("apm.service_trend", "apm.business_trend"):
        view = views[vid]
        metrics = next(a for a in view.args if a.name == "metrics")
        assert metrics.default is None and not metrics.required
        assert all("metrics=" not in e for e in view.examples), view.examples


# ── V34-4 — target: none 보기가 말한 대상을 조용히 버림 ─────────────────────────────

@pytest.mark.parametrize("view", ["apm.ranking", "apm.fleet_events"])
async def test_v34_4_service_name_in_targets_is_applied_or_disclosed(view, gateway) -> None:
    gw = gateway()
    res = await _run([view], targets=[{"text": "주문", "kind": "auto"}])
    (args,) = gw.named("apm_fleet")
    applied = args.get("service") in ("주문", ["주문"])
    disclosed = any("주문" in t for t in _texts(res))
    assert applied or disclosed, (args, _texts(res))


@pytest.mark.parametrize("view", ["apm.ranking", "apm.fleet_events"])
async def test_v34_4_named_hosts_beside_a_fleet_view_are_disclosed(view, gateway) -> None:
    gateway()
    res = await _run([view], _isolated("web01", "web02"))
    assert any("web01" in t for t in _texts(res)), _texts(res)


# ── V34-5 — 전 대상 이벤트 결정적 줄 건수 ──────────────────────────────────────────

async def test_v34_5_fleet_event_line_reports_the_total_not_the_cut(gateway) -> None:
    rows = [{"time_ms": 1, "level": "fatal", "source_id": "bank", "domain_id": 1,
             "instance_id": 1, "event_type": "X"}]
    env = _env("apm_fleet", rows, mode="events",
               summary={"domains_total": 3, "domains_ok": 3, "domains_failed": 0,
                        "events_total": 5},
               coverage={"domains_total": 3, "from_buffer": 0, "from_api": 3, "mixed": 0,
                         "failed": 0})
    gateway({"apm_fleet": env})
    res = await _run(["apm.fleet_events"], view_args={"apm.fleet_events": {"n": 1}})
    lines = res.get("answer_lines") or []
    assert any("5건" in ln for ln in lines), lines


async def test_fleet_event_line_counts_failed_domains_as_unknown(gateway) -> None:
    """대조 — 실패 도메인이 있으면 「0건이 아니라 확인하지 못함」(계약 B-3)."""
    env = _env("apm_fleet", [], mode="events",
               summary={"domains_total": 2, "domains_ok": 1, "domains_failed": 1,
                        "events_total": 0},
               coverage={"domains_total": 2, "from_buffer": 0, "from_api": 1, "mixed": 0,
                         "failed": 1}, partial=True,
               limits=["[한계] 이벤트 조회 실패 도메인 1곳(bank 도메인 2) — 0건이 아니라 "
                       "확인하지 못했다"])
    gateway({"apm_fleet": env})
    res = await _run(["apm.fleet_events"])
    lines = res.get("answer_lines") or []
    assert any("실패 1" in ln and "0건이 아니라 확인하지 못함" in ln for ln in lines), lines
    assert disc.APM_PARTIAL_SOURCES in {d["kind"] for d in res.get("disclosures") or []}
