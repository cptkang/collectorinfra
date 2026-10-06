"""plans/125 E-1~E-4 · M-2 — 교차 계층 엔터티 연결(패싯 · 간선 표 · 변환 단계 · 연결 장부).

고정하는 계약:
  1. E-1 `TargetRef` 교차 계층 패싯 — 값이 없으면 직렬화에서 빠진다(폴스타 → 폴스타 바이트 불변).
  2. E-2 간선 표는 레지스트리 데이터 · 최단 경로 · 표 밖 경로 없음(DM-5).
  3. E-3 변환은 교차 시스템 간선에서만 · E2 는 폴스타 어댑터 고정 조회(LLM 0 · 방언 · 읽기 전용).
  4. E-4 연결 장부 — 1:1 연결 · 0건 미연결 · 다건 모호(자동 결합 금지) · 조회 안 함(사유).
  5. M-2 오프라인 픽스처 e2e — 서버명만 아는 선행 결과 → E2 → APM 보기 → 행·장부·출처
     (정밀도·재현율 1.0).
DB·게이트웨이는 전부 대역이다(LLM·네트워크 0).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.config import DBHubConfig
from src.db_adapters.polestar.entity_probe import build_hostname_lookup_sql
from src.dbhub.models import QueryResult
from src.orchestration import apm_query as aq
from src.orchestration import entity_link as el
from src.routing.entity_edges import cross_system_only, facet_path
from src.routing.registry import get_registry
from src.security.sql_guard import SQLGuard
from src.utils.prior_targets import TargetRef
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 9, 30, 10, 0, 0)
DOMAINS = {
    "polestar_cm_gp": SimpleNamespace(display_name="김포", db_engine="postgresql",
                                      db_schema="polestar"),
    "polestar_b0": SimpleNamespace(display_name="은행존", db_engine="db2", db_schema="POLESTAR"),
}


def _cfg(active_dbs=("polestar_cm_gp",)) -> SimpleNamespace:
    return SimpleNamespace(
        dbhub=DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(active_dbs)),
        get_polestar_db_ids=lambda: {"polestar_cm_gp", "polestar_b0"},
    )


class _Polestar:
    """폴스타 DB 대역 — 등록명 → hostname 표(이름 하나가 여러 행이면 모호)."""

    def __init__(self, table: dict[str, list[tuple[str, str]]], fail: set[str] | None = None):
        self.table = table
        self.fail = fail or set()
        self.sqls: list[tuple[str, str]] = []

    def client(self, db_id):
        outer = self

        class _Client:
            async def execute_sql(self, sql: str) -> QueryResult:
                outer.sqls.append((db_id, sql))
                if db_id in outer.fail:
                    raise RuntimeError("down")
                rows = [{"server_name": n, "hostname": h} for n, h in outer.table.get(db_id, [])
                        if f"'{n.lower()}'" in sql]
                return QueryResult(columns=["server_name", "hostname"], rows=rows,
                                   row_count=len(rows))

        return _Client()


@pytest.fixture
def polestar(monkeypatch):
    def install(table, fail=None) -> _Polestar:
        db = _Polestar(table, fail)

        @asynccontextmanager
        async def ctx(_config, *, db_id=None):
            yield db.client(db_id)

        monkeypatch.setattr(el, "get_db_client", ctx)
        monkeypatch.setattr(el, "get_domain_by_id", DOMAINS.get)
        return db

    return install


# ── 1. E-1 패싯 ───────────────────────────────────────────────────────────────

def test_target_facets_are_dropped_when_empty() -> None:
    plain = TargetRef(server_name="svr-1", hostname="h1")
    assert plain.model_dump() == {"server_name": "svr-1", "hostname": "h1", "ip": None,
                                  "db_id": None}, "종전 4필드 직렬화 그대로"
    rich = TargetRef(hostname="h1", apm_instance_id="11", asset_key="A-9")
    dumped = rich.model_dump()
    assert dumped["apm_instance_id"] == "11" and dumped["asset_key"] == "A-9"
    assert "apm_domain_id" not in dumped
    assert TargetRef(**dumped) == rich, "로컬 ID 를 모두 보존한다"


# ── 2. E-2 간선 표 ────────────────────────────────────────────────────────────

def test_edge_table_paths() -> None:
    edges = {e.id: e for e in get_registry().entity_edges()}
    assert set(edges) == {"E1", "E1r", "E2", "E2r", "E3", "E4", "E5", "E6", "E7"}
    assert edges["E2"].owner == "polestar" and edges["E1"].owner == "apm"
    assert [e.id for e in facet_path({"server_name"}, "hostname")] == ["E2"]
    assert [e.id for e in facet_path({"apm_instance"}, "asset_key")] == ["E1", "E5"]
    assert facet_path({"hostname", "server_name"}, "hostname") == []
    assert facet_path({"ip"}, "hostname") is None, "표 밖 경로는 없다(DM-5)"
    assert cross_system_only([edges["E2"]], "apm") is True
    assert cross_system_only([edges["E2"]], "polestar") is False, "같은 시스템 간선은 삽입 안 함"


# ── 3. E2 고정 조회 ───────────────────────────────────────────────────────────

def test_hostname_lookup_sql_dialects() -> None:
    pg = build_hostname_lookup_sql(["Svr-1", "svr-1", "x'y"], db_engine="postgresql",
                                   db_schema="polestar")
    assert "FROM polestar.cmm_resource r" in pg and pg.rstrip().endswith("LIMIT 10")
    assert "LOWER(r.name) IN ('svr-1', 'x''y')" in pg and "r.dtime IS NULL" in pg
    db2 = build_hostname_lookup_sql(["a"], db_engine="db2", db_schema="POLESTAR")
    assert db2.rstrip().endswith("FETCH FIRST 5 ROWS ONLY") and "POLESTAR.cmm_resource" in db2
    assert SQLGuard().is_safe_select(pg)[0] and SQLGuard().is_safe_select(db2)[0]


# ── 4. E-3 · E-4 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_hostnames_grades_and_reasons(polestar) -> None:
    db = polestar({"polestar_cm_gp": [("svr-1", "h1"), ("svr-dup", "hA"), ("svr-dup", "hB")]})
    targets = [TargetRef(hostname="given-1"), TargetRef(server_name="svr-1"),
               TargetRef(server_name="svr-dup"), TargetRef(server_name="svr-none"),
               TargetRef(ip="10.0.0.9")]
    hosts, ledger, steps = await el.link_hostnames(targets, consumer="apm", app_config=_cfg())
    assert hosts == ["given-1", "h1"], "모호(다건)는 자동 결합하지 않는다"
    by_key = {e.key: e for e in ledger}
    assert by_key["svr-1"].status == "linked" and by_key["svr-1"].value == "h1"
    assert by_key["svr-dup"].status == "ambiguous"
    assert by_key["svr-none"].status == "unlinked"
    assert by_key["10.0.0.9"].status == "not_queried"
    assert steps == [{"edge": "E2", "from": "server_name", "to": "hostname", "owner": "polestar",
                      "keys": 3, "db_ids": ["polestar_cm_gp"], "linked": 1}]
    assert len(db.sqls) == 1, "DB 하나에 조회 1회(LLM 0)"
    line = el.ledger_line(ledger)
    assert line == "호스트 연결(서버명 → hostname) 1/3 · 미연결 1 · 모호 1"


@pytest.mark.asyncio
async def test_same_system_consumer_does_not_insert(polestar) -> None:
    db = polestar({"polestar_cm_gp": [("svr-1", "h1")]})
    hosts, ledger, steps = await el.link_hostnames([TargetRef(server_name="svr-1")],
                                                   consumer="polestar", app_config=_cfg())
    assert hosts == [] and steps == [] and db.sqls == []
    assert ledger[0].status == "not_queried"


@pytest.mark.asyncio
async def test_no_active_owner_db_or_query_failure(polestar) -> None:
    polestar({}, fail={"polestar_cm_gp"})
    _hosts, ledger, steps = await el.link_hostnames(
        [TargetRef(server_name="svr-1")], consumer="apm", app_config=_cfg())
    assert ledger[0].status == "not_queried" and "조회 실패" in ledger[0].reason
    assert steps[0]["errors"]
    _hosts, ledger, steps = await el.link_hostnames(
        [TargetRef(server_name="svr-1")], consumer="apm", app_config=_cfg(active_dbs=()))
    assert steps[0]["error"] == "polestar 활성 DB 가 없다"


# ── 5. M-2 오프라인 픽스처 e2e ────────────────────────────────────────────────

def _env(tool, rows, **extra):
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-09-30T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


@pytest.mark.asyncio
async def test_prior_server_names_flow_through_e2_to_apm(polestar, monkeypatch) -> None:
    """P-C 모양 — 선행 폴스타 결과(등록명만) → E2 → APM 응답시간 → 장부·출처."""
    polestar({"polestar_cm_gp": [("svr-web-1", "web01.local"), ("svr-web-2", "web02.local")]})
    calls: list[tuple[str, dict]] = []

    def single(arguments):
        if arguments["hostname"] == "web02.local":
            return _env("apm_app_health", [], instance_resolution={
                "matched": False, "confidence": None, "reason": "no match", "instances": []})
        return _env("apm_app_health", [{"instance_id": 7, "response_time_avg_ms": 180}],
                    instance_resolution={"matched": True, "confidence": "medium",
                                         "reason": "prefix", "instances": [7]})

    class _Gw:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            # plans/134 M-5 — 다건 대상은 `targets` 배치 1호출(계약 A-2 봉투로 흉내)
            env = apm_batch_mock.reply_for(name, arguments, single)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)

    @asynccontextmanager
    async def factory(url, headers):
        yield _Gw()

    monkeypatch.setattr(aq, "_SESSION_FACTORY", factory)
    isolated = {"parsed_requirements": {"filter_conditions": []}, "conversation_context": {},
                "thread_id": "th-9",
                "prior_rows": {"t1": [{"server_name": "svr-web-1", "cpu": 91},
                                      {"server_name": "svr-web-2", "cpu": 93}]}}
    cfg = _cfg()
    # 신규 처리기는 조사 대상 승계 플래그와 무관하게 input_from 을 따른다
    cfg.composite.prior_targets_enabled = False
    res = await aq.run_apm_query({"task_id": "t2", "agent": "apm_query",
                                  "views": ["apm.app_health"]},
                                 isolated, llm=None, app_config=cfg, now=NOW)
    # plans/134 M-5 — 대상 2대 = `targets` 배치 1호출(종전 대상별 2호출)
    assert [t["hostname"] for t in calls[0][1]["targets"]] == ["web01.local", "web02.local"]
    assert len(calls) == 1
    meta = res["apm_query"]
    assert meta["link_summary"] == {"server_name": {"linked:one": 2},
                                    "apm_instance": {"linked:medium": 1, "unlinked": 1}}
    # 연결 정답표 대비 정밀도·재현율 = 1.0(결정적 경로)
    expected = {("svr-web-1", "linked"), ("svr-web-2", "linked"), ("web01.local", "linked"),
                ("web02.local", "unlinked")}
    got = {(e["key"], e["status"]) for e in meta["link_ledger"]}
    assert got == expected
    summary = res["organized_data"]["summary"]
    assert "호스트 연결(서버명 → hostname) 2/2" in summary
    assert "WAS 연결 1/2(medium 1) · 미연결 1" in summary
    assert meta["provenance"][0]["tool"] == "apm_app_health" and meta["provenance"][0]["queried_at"]
    assert res["organized_data"]["rows"] == [{"instance_id": 7, "response_time_avg_ms": 180,
                                              "hostname": "web01.local"}]
