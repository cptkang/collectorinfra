"""DB별 선행 스코프 분할 — `prior-scope-by-db` (D-203 · plans/88 §4.9 · SPEC-prior-scope-by-db).

현행: `_extract_identity_rows`가 `_source_db`를 버려 후속 멀티 DB 조회가 모든 DB에 같은 IN 목록을
적용한다. 이 파일은 플래그 on에서 DB별로 분리되고, off·태그 없는 행에서 현행과 같음을 고정한다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.nodes.multi_db_executor as mod
from src.config import load_config
from src.nodes.prompt_blocks import prior_server_scope_by_db
from src.orchestration.subagents import _extract_identity_rows
from src.utils.query_gen_common import (
    build_prior_rows_block,
    collect_prior_identity_values,
    collect_prior_identity_values_by_db,
    filter_prior_rows_for_db,
)

B0, GP, YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
TAGGED = {"t1": [
    {"hostname": "b1", "_source_db": B0}, {"hostname": "b2", "_source_db": B0}, {"hostname": "b3", "_source_db": B0},
    {"hostname": "g1", "_source_db": GP}, {"hostname": "g2", "_source_db": GP},
    {"hostname": "g3", "_source_db": GP}, {"hostname": "g4", "_source_db": GP},
]}
UNTAGGED = {"t1": [{"hostname": "s1"}, {"hostname": "s2"}]}


@pytest.fixture
def by_db_on(monkeypatch):
    monkeypatch.setenv("COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED", "true")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


@pytest.fixture
def by_db_off(monkeypatch):
    """off를 **명시**한다 — 운영 .env가 2026-09-10부터 on이라 "기본=off"를 .env에 기대면 누수된다(CLAUDE.md Known Mistakes)."""
    monkeypatch.setenv("COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED", "false")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


# ──────────────────────────────────────────────
# 분할 함수
# ──────────────────────────────────────────────

def test_collect_by_db_splits_buckets():
    out = collect_prior_identity_values_by_db(TAGGED)
    assert out == {B0: ("hostname", ["b1", "b2", "b3"]), GP: ("hostname", ["g1", "g2", "g3", "g4"])}


def test_collect_by_db_untagged_matches_current():
    out = collect_prior_identity_values_by_db(UNTAGGED)
    assert out == {"": collect_prior_identity_values(UNTAGGED)}
    assert prior_server_scope_by_db(UNTAGGED) is None  # 분할 없음 → 현행


def test_source_db_never_leaks_into_values():
    _, values = collect_prior_identity_values(TAGGED)
    assert not any(v.startswith("polestar_") for v in values)
    block = build_prior_rows_block(TAGGED, db_id=GP)
    assert "polestar_" not in block and "'g1'" in block and "'b1'" not in block


def test_filter_prior_rows_for_db_keeps_untagged_rows():
    mixed = {"t1": [{"hostname": "b1", "_source_db": B0}, {"hostname": "s1"}]}
    assert filter_prior_rows_for_db(mixed, GP) == {"t1": [{"hostname": "s1"}]}
    assert filter_prior_rows_for_db(UNTAGGED, GP) is UNTAGGED  # 태그 0건 → 그대로
    assert filter_prior_rows_for_db(TAGGED, YD) == {}


def test_build_block_db_id_none_is_current_behavior():
    assert build_prior_rows_block(TAGGED) == build_prior_rows_block(TAGGED, db_id=None)
    assert build_prior_rows_block(TAGGED, db_id=YD) == ""


# ──────────────────────────────────────────────
# _extract_identity_rows 패스스루
# ──────────────────────────────────────────────

ROWS = [{"hostname": "b1", "cpu": 91.0, "_source_db": B0}]


def test_extract_identity_rows_flag_off_drops_tag(by_db_off):
    assert _extract_identity_rows(ROWS) == [{"hostname": "b1"}]


def test_extract_identity_rows_flag_on_keeps_tag_only(by_db_on):
    assert _extract_identity_rows(ROWS) == [{"hostname": "b1", "_source_db": B0}]


# ──────────────────────────────────────────────
# multi_db_executor — DB별 선택 · 미조회
# ──────────────────────────────────────────────

def _run(prior_rows, by_db):
    return SimpleNamespace(
        state={"prior_rows": prior_rows, "user_query": "q"}, prior_block="ALL", prior_scope=("hostname", ["x"]),
        prior_scope_by_db=by_db, skipped_dbs=[], dependency_notes=[], db_errors={},
        registry=MagicMock(is_registered=lambda d: True),
        llm=MagicMock(), parsed_requirements={}, effective_limit=10, unmapped_fields=[], app_config=MagicMock(),
        mc_candidates=[], mc_derivations=[], value_index=None, form_context="", form_fill_out={},
        form_intent=False, mapping_sources={}, form_fill_answers=None,
    )


def test_prior_for_db_without_partition_is_run_level():
    run = _run(TAGGED, None)
    assert mod._prior_for_db(run, GP) == ("ALL", ("hostname", ["x"]))


def test_prior_for_db_with_partition_picks_bucket():
    run = _run(TAGGED, collect_prior_identity_values_by_db(TAGGED))
    with patch.object(mod, "is_scrub_samples_enabled", return_value=False):
        block, scope = mod._prior_for_db(run, B0)
    assert scope == ("hostname", ["b1", "b2", "b3"]) and "'b1'" in block and "'g1'" not in block


@pytest.mark.asyncio
async def test_generate_validated_sql_passes_db_specific_scope():
    run = _run(TAGGED, collect_prior_identity_values_by_db(TAGGED))
    gen = AsyncMock(return_value="SELECT 1")
    with patch.object(mod, "_generate_sql", gen), patch.object(mod, "_validate_sql", return_value=(None, None)), \
         patch.object(mod, "is_scrub_samples_enabled", return_value=False):
        await mod._generate_validated_sql(run, MagicMock(), {}, "q", {}, db_engine="postgresql", db_id=GP)
    kwargs = gen.call_args.kwargs
    assert kwargs["prior_scope"] == ("hostname", ["g1", "g2", "g3", "g4"])
    assert "'g1'" in kwargs["prior_block"] and "'b1'" not in kwargs["prior_block"]


@pytest.mark.asyncio
async def test_run_single_target_skips_db_without_selected_servers():
    run = _run(TAGGED, collect_prior_identity_values_by_db(TAGGED))
    await mod._run_single_target({"db_id": YD}, run)
    assert run.skipped_dbs == [YD] and run.dependency_notes[0]["kind"] == "scope_db"
    assert run.db_errors == {}  # 에러가 아니라 경과
    run.registry.get_client.assert_not_called()


def _executor_out(run_attrs):
    async def _fake_run_single(target, run):
        run.db_results[target["db_id"]] = [{"a": 1}]

    def _fake_prepare(st, llm, cfg):
        run = MagicMock()
        run.state = st
        run.db_results, run.db_errors, run.db_schemas = {}, {}, {}
        run.all_attempts, run.validation_failed, run.sql_by_schema = [], {}, {}
        run.mc_candidates, run.mc_derivations, run.form_fill_out = [], [], {}
        for k, v in run_attrs.items():
            setattr(run, k, v)
        return run

    state = {"user_query": "q", "target_databases": [{"db_id": B0, "sub_query_context": "q"}]}
    with patch.object(mod, "_run_single_target", AsyncMock(side_effect=_fake_run_single)), \
         patch.object(mod, "_prepare_multi_run", AsyncMock(side_effect=_fake_prepare)):
        return asyncio.run(mod.multi_db_executor(state, llm=MagicMock(), app_config=MagicMock()))


def test_executor_returns_notes_only_when_present():
    note = {"kind": "scope_db", "detail": "x"}
    out = _executor_out({"dependency_notes": [note], "skipped_dbs": [YD]})
    assert out["dependency_notes"] == [note] and out["skipped_dbs"] == [YD]
    out2 = _executor_out({})  # MagicMock 속성 — list가 아니므로 키가 생기지 않는다(반환 shape 현행)
    assert "dependency_notes" not in out2 and "skipped_dbs" not in out2


def test_prepare_multi_run_flag_off_has_no_partition(by_db_off):
    """플래그 off(명시) → `prior_scope_by_db is None` — `_prior_for_db`가 run 단위 값을 쓴다."""
    cfg = load_config()
    assert cfg.composite.prior_scope_by_db_enabled is False
