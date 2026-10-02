"""복수 지시어 후속 턴의 서버 스코프 승계 (plans/116 §10.3 Q-MULTI-TOP 2턴).

1턴 「…상위 3대 서버」 → 2턴 「그 서버들의 제조사와 OS 종류」가 운영 설정(시맨틱 컴파일 on)에서
서버 필터 없는 전체 피벗(54행)을 냈다. 턴 간 승계에는 prior_rows가 없어 결정적 컴파일의
server_scope가 비었기 때문이다. 직전 턴 엔티티를 같은 (식별컬럼, 값목록) 스코프로 넘기는지,
단일(query_generator)·멀티(multi_db_executor) 경로가 같은 판정을 쓰는지 본다.
"""

from __future__ import annotations

import inspect

from src.nodes.prompt_blocks import demonstrative_entity_scope
from src.nodes.query_generator import _prior_server_scope
from src.nodes.semantic_compiler import SMQ, compile_smq, load_semantic_model

# Q-MULTI-TOP 2턴 녹화의 context_resolver 출력 그대로.
_CTX = {
    "previous_result_count": 3,
    "previous_entities": [
        {"field": "name", "value": "DB-ORA-023"},
        {"field": "name", "value": "cocm-hdkapp01"},
        {"field": "name", "value": "SV-WEB-001"},
    ],
}


def _state(query: str, ctx: dict | None = None, filters: list | None = None) -> dict:
    return {
        "user_query": query,
        "parsed_requirements": {"original_query": query, "filter_conditions": filters or []},
        "conversation_context": ctx if ctx is not None else dict(_CTX),
        "prior_rows": None,
    }


def test_plural_demonstrative_inherits_previous_entities():
    scope = demonstrative_entity_scope(_state("그 서버들의 제조사와 OS 종류"))
    assert scope == ("name", ["DB-ORA-023", "cocm-hdkapp01", "SV-WEB-001"])


def test_single_path_uses_entity_scope_when_no_prior_rows():
    assert _prior_server_scope(_state("그 서버들의 제조사와 OS 종류")) == (
        "name", ["DB-ORA-023", "cocm-hdkapp01", "SV-WEB-001"])


def test_orchestration_rewritten_query_judged_on_original():
    """2단 격리 입력은 user_query·original_query가 planner 재작성문이다 — 원문으로 판정한다."""
    rewritten = "직전 조회 결과인 서버 목록 (DB-ORA-023, cocm-hdkapp01, SV-WEB-001) 의 제조사 조회"
    state = _state(rewritten)
    state["original_user_query"] = "그 서버들의 제조사와 OS 종류"
    assert demonstrative_entity_scope(state) == (
        "name", ["DB-ORA-023", "cocm-hdkapp01", "SV-WEB-001"])


def test_prior_rows_take_precedence():
    state = _state("그 서버들의 제조사")
    state["prior_rows"] = {"t1": [{"hostname": "h1"}]}
    assert _prior_server_scope(state) == ("hostname", ["h1"])


def test_not_applied_without_demonstrative_or_with_concrete_filter():
    assert demonstrative_entity_scope(_state("전체 서버의 제조사")) is None
    assert demonstrative_entity_scope(_state("서버별 제조사와 OS 종류")) is None
    concrete = _state("그 서버의 제조사", filters=[
        {"field": "hostname", "op": "=", "value": "svweb001"}])
    assert demonstrative_entity_scope(concrete) is None  # 2단 단수 주입과 중복 금지


def test_not_applied_when_entities_are_a_truncated_sample():
    """직전 결과가 엔티티 상한보다 많으면 표본으로 좁히지 않는다."""
    ctx = dict(_CTX, previous_result_count=54)
    assert demonstrative_entity_scope(_state("그 서버들의 제조사", ctx)) is None


def test_not_applied_without_previous_entities():
    assert demonstrative_entity_scope(_state("그 서버들의 제조사", {})) is None


def test_scope_compiles_to_having_filter():
    """녹화 SMQ(Vendor·OSType)에 스코프가 걸리면 서버 3대로 좁히는 HAVING과 서버명이 붙는다."""
    model = load_semantic_model("polestar")
    sql = compile_smq(
        SMQ.from_dict({"pattern": "A", "dimensions": ["Vendor", "OSType"]}),
        "polestar", model,
        server_scope=demonstrative_entity_scope(_state("그 서버들의 제조사와 OS 종류")),
    )
    assert "HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.name END) IN " \
        "('DB-ORA-023', 'cocm-hdkapp01', 'SV-WEB-001')" in sql
    assert 'AS "name"' in sql


def test_multi_path_uses_same_scope_rule():
    from src.nodes import multi_db_executor

    src = inspect.getsource(multi_db_executor)
    rule = 'prior_server_scope(state.get("prior_rows")) or demonstrative_entity_scope(state)'
    assert rule in src
