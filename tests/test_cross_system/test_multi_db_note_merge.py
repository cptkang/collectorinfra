"""멀티 DB 경로의 경과 노트 병합 — 단일 DB 경로와 대칭 (plans/102 §4.1.4-1 · 성공 기준 5 "침묵 0").

`dependency_notes`에는 리듀서가 없어 노드 반환이 곧 덮어쓰기다. 단일 DB 경로
(`schema_analyzer.py:757,765`)는 `state.get("dependency_notes")`를 이어 붙이는데
`multi_db_executor`는 `_MultiRun.dependency_notes`가 빈 목록에서 시작해 state 노트를 덮어썼다 —
프로브(NOTE_PROBE)·소유 교정(NOTE_OWNERSHIP)·분류 폴백(NOTE_ROUTING_FALLBACK)이 유실된다.

이 파일이 고정하는 것:
  ① 병합 — state 노트가 앞, 이번 노드 노트가 뒤
  ② 중복 제거 — 같은 (kind, db_id)는 한 번만(단일 경로 `add_db_note`와 같은 규칙)
  ③ 반환 shape — 이번 노드 노트가 없으면 키를 만들지 않는다(현행 유지)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import src.nodes.multi_db_executor as mod
from src.utils.prior_dependency import (
    NOTE_OWNERSHIP,
    NOTE_PROBE,
    NOTE_ROUTING_FALLBACK,
    descriptions_missing_note,
    scope_db_note,
    structure_missing_note,
)

B0, ITAM = "polestar_b0", "itam"

PROBE_NOTE = {"kind": NOTE_PROBE, "task_id": None, "reason": "single_system",
              "detail": "소재 확인: 폴스타"}
OWNERSHIP_NOTE = {"kind": NOTE_OWNERSHIP, "task_id": None, "db_id": ITAM,
                  "detail": "자산 영역은 자산관리로 교정"}
FALLBACK_NOTE = {"kind": NOTE_ROUTING_FALLBACK, "task_id": None,
                 "detail": "분류 결과 없음 — 첫 활성 DB 사용"}


def _executor_out(state_notes: list[dict] | None, run_notes: list[dict]) -> dict:
    """`multi_db_executor`를 실제로 돌려 반환 델타를 얻는다(I/O는 전부 대역)."""

    async def _fake_run_single(target, run):
        run.db_results[target["db_id"]] = [{"a": 1}]

    def _fake_prepare(st, llm, cfg):
        run = MagicMock()
        run.state = st
        run.db_results, run.db_errors, run.db_schemas = {}, {}, {}
        run.all_attempts, run.validation_failed, run.sql_by_schema = [], {}, {}
        run.mc_candidates, run.mc_derivations, run.form_fill_out = [], [], {}
        run.dependency_notes = list(run_notes)
        run.skipped_dbs = []
        return run

    state: dict = {"user_query": "q", "target_databases": [{"db_id": B0, "sub_query_context": "q"}]}
    if state_notes is not None:
        state["dependency_notes"] = state_notes
    with patch.object(mod, "_run_single_target", AsyncMock(side_effect=_fake_run_single)), \
         patch.object(mod, "_prepare_multi_run", AsyncMock(side_effect=_fake_prepare)):
        return asyncio.run(mod.multi_db_executor(state, llm=MagicMock(), app_config=MagicMock()))


# ──────────────────────────────────────────────
# ① 병합 — 덮어쓰기 금지
# ──────────────────────────────────────────────

def test_state_notes_survive_multi_db_execution():
    """프로브·소유 교정·분류 폴백 노트가 이번 노드 노트에 밀려 사라지지 않는다."""
    state_notes = [PROBE_NOTE, OWNERSHIP_NOTE, FALLBACK_NOTE]
    run_note = scope_db_note(B0)
    out = _executor_out(state_notes, [run_note])
    assert out["dependency_notes"] == [PROBE_NOTE, OWNERSHIP_NOTE, FALLBACK_NOTE, run_note]


def test_admin_asset_notes_from_both_paths_coexist():
    """구조 정보 없음·컬럼 설명 미등록이 state·run 양쪽에서 나와도 DB별로 남는다."""
    state_notes = [structure_missing_note(ITAM)]
    run_notes = [structure_missing_note(B0), descriptions_missing_note(B0)]
    out = _executor_out(state_notes, run_notes)
    assert out["dependency_notes"] == [structure_missing_note(ITAM), *run_notes]


# ──────────────────────────────────────────────
# ② 중복 제거 — 단일 경로 `add_db_note`와 같은 규칙
# ──────────────────────────────────────────────

def test_same_kind_and_db_not_duplicated():
    existing = structure_missing_note(B0)
    out = _executor_out([existing], [structure_missing_note(B0)])
    assert out["dependency_notes"] == [existing]


# ──────────────────────────────────────────────
# ③ 반환 shape — 현행 유지
# ──────────────────────────────────────────────

def test_no_key_when_this_node_has_no_notes():
    """이번 노드 노트가 없으면 키를 만들지 않는다 — 키 부재가 곧 state 보존이다."""
    out = _executor_out([PROBE_NOTE], [])
    assert "dependency_notes" not in out


def test_no_state_notes_matches_current_behaviour():
    run_note = scope_db_note(B0)
    out = _executor_out(None, [run_note])
    assert out["dependency_notes"] == [run_note]
