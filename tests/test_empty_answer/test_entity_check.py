"""식별자 존재 확인(plans/123 S-4a · 섀도) · 0건 부류별 문구(S-4b) — LLM 0 · DB 0.

조건이 서버 식별자 등호뿐인 0건 조회가 「필터 조건을 완화해보세요 (예: 임계값 낮추기)」로 끝나던
결함(B-05 · A-11 · R3-04 모양)을 합성 입력으로 재현한다. DB는 전부 대역이다(D-127).
S-4a는 D-280 ⑧(123·G-8 (c))대로 섀도다 — 판정은 로그에만 남고 응답·재생성은 바뀌지 않는다.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.config import AppConfig, QueryConfig, Text2SQLConfig
from src.db_adapters import get_adapter
from src.db_adapters.polestar.entity_probe import build_entity_probe_sql
from src.dbhub.models import QueryResult
from src.domain import disclosure as disc
from src.domain.empty_answer import (
    SINGLE_GROUP,
    EntityCheck,
    FunnelStage,
    as_payload,
    build_diagnosis,
    entity_lines,
    from_payload,
    identifier_only_values,
    render_diagnosis,
)
from src.security.sql_guard import SQLGuard
from src.utils.query_gen_common import HOST_IDENTIFIER_FIELDS, is_demonstrative_identifier

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
ro = importlib.import_module("src.nodes.result_organizer")
og = importlib.import_module("src.nodes.output_generator")
ra = importlib.import_module("src.orchestration.result_aggregator")

_THRESHOLD_HINTS = ("임계값 낮추기", "임계값을 낮추거나")

_DOMAINS = {
    "db_a": SimpleNamespace(display_name="김포", db_engine="postgresql", db_schema="polestar"),
    "db_b": SimpleNamespace(display_name="여의도", db_engine="postgresql", db_schema="polestar"),
    "db_c": SimpleNamespace(display_name="은행존", db_engine="db2", db_schema="POLESTAR"),
}


def _host(value: str, field: str = "hostname") -> dict:
    return {"field": field, "op": "=", "value": value}


def _config(*, enabled: bool = True, adapter_ids: str = "db_a,db_b,db_c") -> AppConfig:
    """검증 대상 필드를 **명시**해 `.env` 누수를 막는다(Known Mistakes)."""
    config = AppConfig(polestar_db_ids=adapter_ids)
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(
        empty_diagnosis_enabled=enabled, empty_diagnosis_max_probes=5,
    )
    return config


class _Db:
    """DB별 존재 여부를 흉내 내는 클라이언트 대역 — 실행 SQL을 (db_id, sql)로 기록한다."""

    def __init__(self, present: dict[str, set[str]] | None = None, fail: set[str] | None = None):
        self.present = present or {}
        self.fail = fail or set()
        self.calls: list[tuple[str | None, str]] = []

    def client(self, db_id: str | None):
        outer = self

        class _Client:
            async def execute_sql(self, sql: str) -> QueryResult:
                outer.calls.append((db_id, sql))
                if db_id in outer.fail:
                    raise RuntimeError("connection refused")
                if sql.startswith("SELECT COUNT(*)"):
                    return QueryResult(columns=["count"], rows=[{"count": 7}], row_count=1)
                hit = any(f"'{v.lower()}'" in sql for v in outer.present.get(db_id, set()))
                rows = [{"hit": 1}] if hit else []
                return QueryResult(columns=["hit"], rows=rows, row_count=len(rows))

        return _Client()


@pytest.fixture
def db(monkeypatch):
    fake = _Db()

    @asynccontextmanager
    async def _ctx(_config, *, db_id=None):
        yield fake.client(db_id)

    monkeypatch.setattr(ro, "get_db_client", _ctx)
    monkeypatch.setattr(ro, "get_domain_by_id", _DOMAINS.get)
    return fake


def _state(filters: list, **overrides) -> dict:
    base = {
        "query_results": [],
        "parsed_requirements": {"query_targets": ["서버", "CPU"], "filter_conditions": filters},
        "template_structure": None,
        "user_query": "호스트명이 sbhdbo53인 서버의 CPU 리소스를 조회해줘",
        "generated_sql": "SELECT r.name FROM res r WHERE r.hostname = 'sbhdbo53' LIMIT 1000",
        "active_db_id": "db_a",
        "is_multi_db": False,
        "retry_count": 0,
    }
    base.update(overrides)
    return base


async def _diagnose(state: dict, config: AppConfig | None = None):
    return await ro._diagnose_empty_result(
        state, state["parsed_requirements"], config or _config()
    )


# ── 어댑터: 존재 확인 SQL 방언 · 스키마 한정 ─────────────────────────────────


def test_probe_sql_postgresql_uses_limit_and_schema() -> None:
    sql = build_entity_probe_sql("SBHDBO53", db_engine="postgresql", db_schema="polestar")

    assert "FROM polestar.cmm_resource r" in sql
    assert sql.rstrip().endswith("LIMIT 1") and "FETCH FIRST" not in sql
    assert "LOWER(r.name) IN ('sbhdbo53')" in sql
    assert "LOWER(r.hostname) LIKE 'sbhdbo53.%'" in sql, "단일 레이블은 FQDN 저장값까지 찾는다"
    assert "r.dtime IS NULL" in sql
    assert SQLGuard().is_safe_select(sql)[0]


def test_probe_sql_db2_uses_fetch_first_and_upper_schema() -> None:
    sql = build_entity_probe_sql("sbhdbo53", db_engine="db2", db_schema="POLESTAR")

    assert "FROM POLESTAR.cmm_resource r" in sql
    assert sql.rstrip().endswith("FETCH FIRST 1 ROWS ONLY") and "LIMIT" not in sql
    assert SQLGuard().is_safe_select(sql)[0]


def test_probe_sql_ip_fqdn_and_quote_escaping() -> None:
    ip = build_entity_probe_sql("10.1.2.3", db_engine="postgresql", db_schema=None)
    assert "r.ipaddress = '10.1.2.3'" in ip
    assert "'10'" not in ip and "LIKE" not in ip, "IP의 점은 레이블 구분자가 아니다"
    assert "FROM cmm_resource r" in ip, "스키마가 비면 무한정"

    fqdn = build_entity_probe_sql("Web01.Bank.Local", db_engine="postgresql", db_schema="s")
    assert "IN ('web01.bank.local', 'web01')" in fqdn, "FQDN 입력은 단축명 저장값도 본다"

    quoted = build_entity_probe_sql("a'b", db_engine="postgresql", db_schema="s")
    assert "'a''b'" in quoted


def test_adapter_exposes_probe_hook() -> None:
    adapter = get_adapter("db_a", {"db_a"})

    assert adapter is not None
    assert adapter.entity_probe_sql(
        "x1", db_engine="db2", db_schema="POLESTAR"
    ) == build_entity_probe_sql("x1", db_engine="db2", db_schema="POLESTAR")


# ── 조건 부류 판정 ───────────────────────────────────────────────────────────


def _ids(filters) -> tuple[str, ...]:
    return identifier_only_values(
        filters, identity_fields=HOST_IDENTIFIER_FIELDS, is_placeholder=is_demonstrative_identifier,
    )


def test_identifier_only_classification() -> None:
    assert _ids([_host("sbhdbo53")]) == ("sbhdbo53",)
    assert _ids([_host("a"), {"field": "name", "op": "IN", "value": ["b", "a"]}]) == ("a", "b")
    assert _ids([{"field": "ip", "op": "=", "value": "10.0.0.1"}]) == ("10.0.0.1",), "IP는 값으로"
    assert _ids([_host("a"), {"field": "cpu_usage", "op": ">=", "value": 80}]) == ()
    assert _ids([_host("해당 서버")]) == (), "지시어는 식별자가 아니다"
    assert _ids([{"field": "hostname", "op": "LIKE", "value": "web%"}]) == ()
    assert _ids(["CPU 사용률 80% 이상"]) == (), "자연어 조건은 판정하지 않는다"
    assert _ids([{"field": "OSType", "op": "=", "value": "LINUX"}]) == ()
    assert _ids([]) == ()


# ── S-4a 발동 · 결과 — 섀도(D-280 ⑧ · 123·G-8 (c)): 판정은 로그만, 응답·재생성 불변 ──


def _verdict(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "S-4a 섀도" in r.getMessage()]


@pytest.mark.asyncio
async def test_missing_identifier_is_logged_in_shadow(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)

    diagnosis = await _diagnose(_state([_host("sbhdbo53")]))

    assert len(db.calls) == 1, "0건 턴에 존재 확인 1회 — 수치 조건이 없어 퍼널 프로브는 없다"
    assert db.calls[0][0] == "db_a" and "LIMIT 1" in db.calls[0][1]
    assert diagnosis is None, "섀도 — 진단(응답)에 싣지 않는다"
    [line] = _verdict(caplog)
    assert "verdict=all_missing" in line and "'sbhdbo53'" in line and "응답 불변" in line


@pytest.mark.asyncio
async def test_shadow_keeps_regeneration_and_uses_s4b_phrase(db) -> None:
    state = _state([_host("sbhdbo53")], parsed_requirements={
        "query_targets": ["서버", "CPU"], "filter_conditions": [_host("sbhdbo53")],
        "aggregation": "summary",
    })

    result = await ro.result_organizer(state, llm=None, app_config=_config())
    text = og._generate_empty_result_response(
        state["parsed_requirements"], result.get("empty_diagnosis")
    )

    assert result["error_message"] == "data_insufficient", "섀도 — 재생성 판정은 종전대로"
    assert "등록된 서버가 아닙니다" not in text, "섀도 — 응답 불변(run R5′에 on)"
    assert "대상 이름(서버명·호스트명·IP)이 정확한지 확인해보세요" in text, "S-4b 문구는 on"
    assert not any(h in text for h in _THRESHOLD_HINTS)


@pytest.mark.asyncio
async def test_existing_identifier_logs_all_present(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)
    db.present = {"db_a": {"sbhdbo53"}}
    state = _state([_host("sbhdbo53")])

    diagnosis = await _diagnose(state)
    text = og._generate_empty_result_response(
        state["parsed_requirements"], as_payload(diagnosis) if diagnosis else None
    )

    assert len(db.calls) == 1
    assert diagnosis is None, "대상이 있으면 말할 것이 없다 — 종전 0건 경로"
    assert "verdict=all_present" in _verdict(caplog)[0]
    assert "등록된 서버가 아닙니다" not in text
    assert "대상 이름(서버명·호스트명·IP)이 정확한지 확인해보세요" in text
    assert not any(h in text for h in _THRESHOLD_HINTS)


@pytest.mark.asyncio
async def test_numeric_condition_keeps_funnel_without_probe(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)
    state = _state(
        [_host("sbhdbo53"), {"field": "cpu_usage", "op": ">=", "value": 80}],
        generated_sql=(
            "SELECT r.name FROM res r JOIN m s ON r.id = s.rid "
            "WHERE r.hostname = 'sbhdbo53' AND s.val >= 80 LIMIT 1000"
        ),
    )

    diagnosis = await _diagnose(state)

    assert all(sql.startswith("SELECT COUNT(*)") for _, sql in db.calls), "존재 확인 0회"
    assert _verdict(caplog) == []
    assert diagnosis.entity is None and diagnosis.identifier_only is False
    assert [s.counts[SINGLE_GROUP] for s in diagnosis.stages] == [7, 0]
    assert "임계값을 낮추거나" in render_diagnosis(diagnosis), "수치 조건 완화 제안은 종전대로"


@pytest.mark.asyncio
async def test_no_adapter_or_probe_failure_falls_back_with_warning(db, caplog) -> None:
    no_adapter = await _diagnose(_state([_host("x1")]), _config(adapter_ids="other"))
    assert no_adapter is None and db.calls == []
    assert "존재 확인 어댑터가 없습니다" in caplog.text

    db.fail = {"db_a"}
    failed = await _diagnose(_state([_host("x1")]))
    assert failed is None, "「확인하지 못함」은 「없음」이 아니다"
    assert "식별자 존재 확인 실패" in caplog.text
    assert _verdict(caplog) == [], "확인하지 못하면 판정 줄도 없다"


@pytest.mark.asyncio
async def test_flag_off_does_not_probe(db) -> None:
    assert await _diagnose(_state([_host("x1")]), _config(enabled=False)) is None
    assert db.calls == []


# ── 멀티 DB 대칭 ─────────────────────────────────────────────────────────────


def _multi(filters: list, dbs: list[str]) -> dict:
    return _state(
        filters, is_multi_db=True, generated_sql=None,
        target_databases=[{"db_id": d} for d in dbs],
    )


@pytest.mark.asyncio
async def test_multi_db_missing_everywhere(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)

    diagnosis = await _diagnose(_multi([_host("nonexistent-01")], ["db_a", "db_c"]))

    assert [d for d, _ in db.calls] == ["db_a", "db_c"], "DB별 1회"
    assert "FETCH FIRST 1 ROWS ONLY" in db.calls[1][1], "DB2 대상은 DB2 방언"
    assert diagnosis is None
    [line] = _verdict(caplog)
    assert "verdict=all_missing" in line and "['김포', '은행존']" in line


@pytest.mark.asyncio
async def test_multi_db_present_in_some(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)
    db.present = {"db_a": {"web01"}}

    diagnosis = await _diagnose(_multi([_host("web01")], ["db_a", "db_b"]))

    assert diagnosis is None
    [line] = _verdict(caplog)
    assert "verdict=partial" in line and "'web01': ['김포']" in line


@pytest.mark.asyncio
async def test_multi_db_present_everywhere_is_silent(db, caplog) -> None:
    caplog.set_level("INFO", logger=ro.logger.name)
    db.present = {"db_a": {"web01"}, "db_b": {"web01"}}

    assert await _diagnose(_multi([_host("web01")], ["db_a", "db_b"])) is None
    assert "verdict=all_present" in _verdict(caplog)[0]


# ── 판정 → 문장(run R5′ on 전환 때 쓰는 렌더 — 도메인 순수 함수) ─────────────


def test_entity_sentences_for_missing_and_partial() -> None:
    missing = EntityCheck(values=("n1",), groups=("김포", "은행존"), found={"n1": ()})
    partial = EntityCheck(values=("w1",), groups=("김포", "여의도"), found={"w1": ("김포",)})
    present = EntityCheck(values=("w1",), groups=("김포",), found={"w1": ("김포",)})

    assert entity_lines(missing) == [
        "'n1'은(는) 등록된 서버가 아닙니다(확인한 곳: 김포 · 은행존)"
        " — 서버 이름(호스트명·IP)을 확인해 주세요."
    ]
    assert entity_lines(partial) == ["'w1'은(는) 김포에만 등록된 서버입니다 — 여의도에는 없습니다."]
    assert entity_lines(present) == [] and missing.all_missing and not partial.all_missing


# ── (run R5′ on 전환 준비) 판정이 진단에 실리면 — 2단 task 결과 → 출력 · 고지 ──


def _missing_payload(value: str = "sbhdbo53") -> dict:
    check = EntityCheck(values=(value,), groups=("김포",), found={value: ()})
    return as_payload(build_diagnosis(
        parsed={}, stage_counts=[], unexpressed=[], notes=[], entity=check, identifier_only=True,
    ))


@pytest.mark.asyncio
async def test_tier2_task_result_reaches_output_with_disclosure() -> None:
    res = {
        "organized_data": {"summary": "조건에 해당하는 데이터가 없습니다.", "rows": []},
        "query_results": [],
        "empty_diagnosis": _missing_payload(),
    }
    parsed = {"query_targets": ["서버"], "filter_conditions": [_host("sbhdbo53")]}
    state = ra._build_output_state(
        {"user_query": "sbhdbo53 서버 사양", "parsed_requirements": parsed},
        {"task_id": "t1", "sub_query": "sbhdbo53 서버 사양"}, res,
    )

    out = await og.output_generator(state, app_config=_config())

    assert "'sbhdbo53'은(는) 등록된 서버가 아닙니다" in out["final_response"]
    assert not any(h in out["final_response"] for h in _THRESHOLD_HINTS)
    kinds = [(d["kind"], d["source"]) for d in out["disclosures"]]
    assert (disc.ENTITY_NOT_FOUND, "task:t1") in kinds


def test_disclosure_only_when_sentence_is_in_body() -> None:
    state = {"empty_diagnosis": _missing_payload()}

    assert og.collect_disclosures(state, "표가 있는 정상 응답") == []
    body = og._generate_empty_result_response({"query_targets": ["서버"]}, _missing_payload())
    assert [d["kind"] for d in og.collect_disclosures(state, body)] == [disc.ENTITY_NOT_FOUND]


def test_entity_kind_is_registered() -> None:
    spec = disc.KIND_TABLE[disc.ENTITY_NOT_FOUND]
    assert (spec.grade, spec.mandatory, spec.scope, spec.priority) == ("guide", False, "task", 15)


# ── S-4b 폴백 문구 (진단 없음) ───────────────────────────────────────────────


@pytest.mark.parametrize("value", ["sbhdbo53", "○○", "nonexistent-01"])  # B-05 · A-11 · R3-04
def test_identifier_only_fallback_has_no_threshold_hint(value: str) -> None:
    parsed = {
        "query_targets": ["서버"],
        "filter_conditions": [_host(value)],
        "time_range": {"start": "2026-08-01", "end": "2026-08-31"},
    }

    text = og._generate_empty_result_response(parsed)

    assert not any(h in text for h in _THRESHOLD_HINTS)
    assert "대상 이름(서버명·호스트명·IP)이 정확한지 확인해보세요" in text
    assert "시간 범위를 넓혀보세요" in text


def test_no_condition_fallback_is_byte_identical() -> None:
    assert og._generate_empty_result_response({"query_targets": ["서버"]}) == (
        "조건에 해당하는 서버 데이터가 없습니다."
    )


def test_relaxation_hint_by_condition_class() -> None:
    stages = [
        FunnelStage("조건 없음(대상 전체)", {SINGLE_GROUP: 12}, "probe"),
        FunnelStage("s.val > 0", {SINGLE_GROUP: 0}, "probe"),
    ]
    common = {"parsed": {}, "stage_counts": stages, "unexpressed": [], "notes": []}

    numeric = render_diagnosis(build_diagnosis(**common))
    ident = render_diagnosis(build_diagnosis(**common, identifier_only=True))

    assert "임계값을 낮추거나" in numeric, "수치 조건은 완화 제안(자동 적용 없음) — 종전 문구"
    assert not any(h in ident for h in _THRESHOLD_HINTS)
    assert "그 단계의 조건부터 확인하세요" in ident


# ── 페이로드 호환 ────────────────────────────────────────────────────────────


def test_payload_shape_unchanged_without_entity() -> None:
    diag = build_diagnosis(
        parsed={}, stage_counts=[FunnelStage("대상", {SINGLE_GROUP: 3}, "probe")],
        unexpressed=[], notes=[],
    )

    assert set(as_payload(diag)) == {"stages", "unexpressed", "notes", "regenerable"}
    assert from_payload({"stages": [], "notes": ["x"]}) is None, "단계도 존재 확인도 없으면 None"


def test_payload_roundtrip_with_entity() -> None:
    restored = from_payload(_missing_payload("x1"))

    assert restored is not None and restored.stages == ()
    assert restored.entity == EntityCheck(values=("x1",), groups=("김포",), found={"x1": ()})
    assert restored.identifier_only is True and restored.regenerable is False
