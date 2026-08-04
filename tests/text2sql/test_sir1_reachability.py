"""S-IR1~5 기본(1방) 경로 도달성 회귀 테스트 (Plan 69 P0-⑨).

S3가 IR을 확장(count/sum·전역 집계·월별 분해·랭킹·filterable 필터)했지만 1방 SMQ
프롬프트와 render_catalog가 구판 안내(avg/max/min만, COUNT·랭킹·서버 지목은 "none")를
유지해 **LLM이 확장 IR을 산출할 수 없어 결정적 컴파일이 발동 불가**이던 결함의 재발 방지.
"""

from src.nodes.semantic_compiler import (
    _AGG_FN,
    SMQ,
    check_coverage,
    load_semantic_model,
    render_catalog,
)
from src.prompts.semantic_compiler import SEMANTIC_SMQ_SYSTEM_TEMPLATE

_DB_ID = "polestar_cm_gp"


class TestPromptAdvertisesExpandedIR:
    def test_schema_block_includes_extended_fields(self):
        """1방 프롬프트가 S-IR 확장 필드를 전부 안내한다."""
        for token in ("count", "sum", "global_aggregate", "entity_count",
                      "time_breakdown", "order_by", "limit", "time_range"):
            assert token in SEMANTIC_SMQ_SYSTEM_TEMPLATE, f"안내 누락: {token}"

    def test_stale_none_directives_removed(self):
        """커버리지 안으로 편입된 형태를 'none'으로 돌리는 구판 지시가 남지 않는다."""
        assert "개수 집계(COUNT)" not in SEMANTIC_SMQ_SYSTEM_TEMPLATE
        assert "상위 N개 랭킹" not in SEMANTIC_SMQ_SYSTEM_TEMPLATE
        assert "특정 서버 지목(장비명/호스트명이 특정 값)·가용성 등 개별 행 필터가 필요" \
            not in SEMANTIC_SMQ_SYSTEM_TEMPLATE

    def test_render_catalog_agg_derived_from_agg_fn(self):
        """카탈로그 집계 안내는 _AGG_FN에서 파생된다(하드코딩 드리프트 차단)."""
        cat = render_catalog(load_semantic_model(_DB_ID))
        assert "집계(agg): " + ", ".join(_AGG_FN) in cat

    def test_render_catalog_exposes_filterable(self):
        """S-IR4 filterable 선언이 카탈로그에 노출된다(서버명 필터 도달 경로)."""
        cat = render_catalog(load_semantic_model(_DB_ID))
        assert "필터 가능 필드(filterable)" in cat
        assert "hostname" in cat


class TestExpandedIRCoverageInside:
    """프롬프트가 안내하는 대표 형태가 실제로 coverage inside다(안내-판정 정합)."""

    def test_entity_count_server_count_inside(self):
        """'서버 수' — global_aggregate + entity_count (gp-003 유형)."""
        model = load_semantic_model(_DB_ID)
        smq = SMQ.from_dict({
            "pattern": "A",
            "resource_types": ["server.Server"],
            "global_aggregate": True,
            "entity_count": True,
        })
        cov = check_coverage(smq, model)
        assert cov.covered, cov.reason

    def test_ranking_order_by_limit_inside(self):
        """'CPU 사용률 상위 10대' — order_by + limit (S-IR3)."""
        model = load_semantic_model(_DB_ID)
        smq = SMQ.from_dict({
            "pattern": "B",
            "dimensions": ["Hostname"],
            "measures": [{"agg": "avg", "definition_name": "Utilization",
                          "resource_type": "server.Cpus"}],
            "order_by": {"field": "server.Cpus", "direction": "desc"},
            "limit": 10,
        })
        cov = check_coverage(smq, model)
        assert cov.covered, cov.reason

    def test_hostname_filter_inside(self):
        """특정 서버 지목 — filterable 선언 필드 필터 (S-IR4)."""
        model = load_semantic_model(_DB_ID)
        smq = SMQ.from_dict({
            "pattern": "A",
            "resource_types": ["server.Server"],
            "dimensions": ["OSType"],
            "filters": [{"field": "hostname", "op": "eq", "value": "web-01"}],
        })
        cov = check_coverage(smq, model)
        assert cov.covered, cov.reason
