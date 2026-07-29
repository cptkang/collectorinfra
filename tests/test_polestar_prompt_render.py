"""폴스타 프롬프트 정본 렌더 검증 (Plan 67 R1-2).

- 정본(config/knowledge 카탈로그)에서 렌더한 프롬프트가 현행 상수와 **바이트 동일**해야 한다
  (무해 전환 — 플래그 없이 적용 가능한 근거).
- 폴백 상수가 정본과 어긋나면(드리프트) 즉시 실패한다 — R1이 없애려는 사본 드리프트의 재발 감시.
- 카탈로그를 못 읽는 상황에서도 프롬프트가 깨지지 않고 폴백으로 동작해야 한다.
"""

from __future__ import annotations

from src.db_adapters.polestar import prompts as polestar_prompts
from src.db_adapters.polestar.prompts import (
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    render_metric_catalog_block,
    render_system_template,
)
from src.schema_cache.catalog_builder import build_catalog, load_knowledge_overrides


def test_rendered_template_is_byte_identical_to_shipped_constant():
    assert render_system_template() == POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE


def test_metric_block_matches_fallback_literal():
    """폴백 상수와 정본 렌더가 어긋나면 실패한다(사본 드리프트 감시)."""
    catalog = build_catalog(None, overrides=load_knowledge_overrides("_base"))
    assert render_metric_catalog_block(catalog) == polestar_prompts._METRIC_CATALOG_FALLBACK


def test_new_measure_flows_into_prompt_without_editing_the_constant():
    """정본에 measure를 1건 추가하면 프롬프트에 자동 반영된다(수정 지점 1곳)."""
    catalog = {"pattern_b": {"measures": [
        {"resource_type": "server.Net", "definition_name": "Throughput", "aliases": ["네트워크 처리량"]},
    ]}}
    rendered = render_system_template(catalog)
    assert "- 'server.Net' + definition_name = 'Throughput' → 네트워크 처리량" in rendered
    assert "server.Cpus' + definition_name" not in rendered


def test_render_falls_back_when_catalog_missing(monkeypatch):
    """카탈로그 미가용 시 폴백 상수로 프롬프트를 유지한다(마커 누출 없음)."""
    monkeypatch.setattr(polestar_prompts, "_rendered_cache", None)
    monkeypatch.setattr(
        "src.schema_cache.catalog_builder.load_knowledge_overrides", lambda *a, **k: {})
    rendered = render_system_template()
    assert polestar_prompts._METRIC_CATALOG_MARKER not in rendered
    assert rendered == POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE


def test_sql_examples_read_hostname_and_ip_from_direct_columns():
    """예제 SQL이 빈 EAV 속성 대신 직접 컬럼으로 호스트명·IP를 읽어야 한다(D-058/D-061).

    EAV 'Hostname'/'IPaddress'는 실측상 비어 있어(카탈로그도 두 속성을 제외) EAV로 조회하면
    결과가 NULL이 된다. 2026-07-29 정본 대조로 발견해 교정한 사본 드리프트의 재발 감시.
    """
    sql_lines = [
        line for line in render_system_template().splitlines()
        if not line.lstrip().startswith("--")
    ]
    body = "\n".join(sql_lines)
    assert "'Hostname'" not in body
    assert "'IPaddress'" not in body
    assert "THEN c.hostname END) AS hostname" in body
    assert "THEN c.ipaddress END) AS ipaddress" in body


def test_rendered_template_keeps_format_placeholders():
    """query_generator가 .format()으로 채우는 자리표시자가 렌더 후에도 남아 있어야 한다."""
    rendered = render_system_template()
    for token in ("{schema}", "{structure_guide}", "{db_engine_hint}", "{default_limit}"):
        assert token in rendered
