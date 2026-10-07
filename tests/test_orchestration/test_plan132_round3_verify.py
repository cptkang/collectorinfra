"""plans/132 3회차 전 교정(D-319) 독립 검증 — 정합성·보안 관점.

가짜 LLM·목만 쓴다(실 LLM 0). 레지스트리는 실제 `config/db_registry.yaml`, 활성 DB·인가는 테스트
config/상태로 명시한다(.env 누수 방지).

`test_defect_*`는 검증 1라운드에서 찾은 결함을 재현한다 — 기대 동작을 단언하므로
교정 전에는 실패한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.query_time import resolve_query_time
from src.orchestration import subagents
from src.orchestration.replanner import _terminal_source_task_ids
from src.routing.db_scope import names_only_zoneless_sources
from src.utils.prior_dependency import NOTE_OWNERSHIP

ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"]
NOW = datetime(2026, 10, 7, 10, 0)


def _target(db_id: str) -> dict:
    return {"db_id": db_id, "relevance_score": 0.9, "sub_query_context": "테스트",
            "user_specified": False, "reason": "테스트 분류"}


def _config(active: list[str], *, ownership: bool = False) -> MagicMock:
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    config.multi_db.zone_group_exclusive = True
    config.router.capability_ownership_enabled = ownership
    config.polestar_rest.realtime_usage_enabled = False
    return config


@asynccontextmanager
async def _patched(classified: list[str]):
    seen: dict[str, Any] = {"executed": None, "sql_calls": 0}

    async def _record(node_state, *args, **kwargs):
        seen["sql_calls"] += 1
        seen["executed"] = [t["db_id"] for t in node_state["target_databases"]]
        return {}

    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})), \
         patch.object(subagents, "_zone_clarification_before_classify", lambda *a, **k: None), \
         patch.object(subagents, "_zone_clarification_or_none_task", lambda *a, **k: None), \
         patch.object(subagents, "classify_dbs",
                      AsyncMock(return_value=[_target(d) for d in classified])):
        yield seen


async def _run(
    sub_query: str, *, hints: list[str], classified: list[str], active: list[str] = ACTIVE,
    capability: str | None = None, ownership: bool = False,
    allowed_db_ids: list[str] | None = None,
) -> tuple[dict, dict[str, Any]]:
    task: dict[str, Any] = {"task_id": "t1", "agent": "data_query", "sub_query": sub_query}
    if capability:
        task["capability"] = capability
    state: dict[str, Any] = {"user_query": sub_query, "is_composite": False,
                             "parsed_requirements": {"target_db_hints": hints}}
    if allowed_db_ids is not None:
        state["allowed_db_ids"] = allowed_db_ids
        state["user_role"] = "user"
    isolated = subagents._make_isolated_input(task, state, {})
    isolated["user_query"] = sub_query
    async with _patched(classified) as seen:
        result = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_config(active, ownership=ownership)
        )
    return result, seen


def _ownership_notes(result: dict) -> list[dict]:
    return [n for n in result.get("dependency_notes") or [] if n.get("kind") == NOTE_OWNERSHIP]


# ── 결함 재현 ─────────────────────────────────────────────────────────────────


def test_defect_not_canonical_notice_is_terminal_for_replanner():
    """사용률 안내(`source_not_canonical`)도 명시 소스 안내처럼 재계획 종결이어야 한다(N-3 · G-1).

    종결이 아니면 평가 LLM이 다른 소스 대체 task를 붙일 수 있다(replanner 주석의 실측 —
    제니퍼 0건 뒤
    폴스타 대체 4/4).
    """
    tasks = [{"task_id": "t1", "agent": "data_query", "sub_query": "자산관리 CPU 사용률"}]
    results = {"t1": {"final_response": "안내", "query_results": [],
                      "degraded_reason": subagents.REASON_SOURCE_NOT_CANONICAL}}
    assert _terminal_source_task_ids(tasks, results) == {"t1"}


@pytest.mark.asyncio
async def test_defect_named_asset_source_removed_upstream_still_notice_only():
    """소유 플래그 on — 소유 제한이 지목된 ITAM을 먼저 빼면 가드가 지목을 못 보고 폴스타로 답한다.

    수용 기준 (a) 「다른 DB로 대신 답하지 않음」 — 지목 판정이 대상 목록에 비소유 DB가 남아 있을
    때만 돈다(`non_owner`가 비면 바로 반환).
    """
    result, seen = await _run(
        "자산관리에서 지난주 CPU 사용률 추이 보여줘", hints=["자산관리"],
        classified=["itam", "polestar_b0"], capability="server_usage", ownership=True,
    )
    assert seen["sql_calls"] == 0, f"다른 DB로 대신 조회함: {seen['executed']}"
    assert result.get("degraded_reason") == subagents.REASON_SOURCE_NOT_CANONICAL


@pytest.mark.asyncio
async def test_defect_correction_note_does_not_name_unauthorized_db():
    """교정 노트는 인가 필터 전에 만들어진다 — 인가 밖 DB(polestar_b0)를 「조회:」에
    싣지 않아야 한다
    (D-264 ② · TP-1.6 분류 폴백 노트는 같은 이유로 인가 뒤에 싣는다)."""
    result, seen = await _run(
        "서버 CPU 사용률 추이", hints=[], classified=["itam", "polestar_b0", "polestar_cm_gp"],
        allowed_db_ids=["polestar_cm_gp", "itam"],
    )
    assert seen["executed"] == ["polestar_cm_gp"]
    for note in _ownership_notes(result):
        assert "polestar_b0" not in note.get("to_db_ids", [])
        assert "polestar_b0" not in str(note.get("detail"))


@pytest.mark.asyncio
async def test_defect_owner_named_in_text_but_missing_from_hints():
    """「폴스타」는 입력 파서 결정적 보강 대상(`source_alias_terms`·위치어)이 아니다 — LLM이 힌트로
    뽑지 않으면 소유 시스템을 함께 지목했는데도 안내만으로 끝난다(`names_only_zoneless_sources`는
    제품 표면어를 원문에서 직접 스캔해 이 비대칭이 없다)."""
    result, seen = await _run(
        "폴스타와 자산관리에서 서버 CPU 사용률 비교", hints=["자산관리"],
        classified=["itam", "polestar_b0"],
    )
    assert seen["sql_calls"] == 1
    assert "itam" not in (seen["executed"] or [])


def test_defect_past_termination_within_minutes_still_clarifies():
    """「최근 5분 내 종료된 프로세스」는 만료 필터가 아니라 과거 사건 기간이다 — 분 단위는 해석기가
    못 잡으므로 종전처럼 되물어야 한다(D-309 검증 D8). 과거형(종료된·끝난)이 마감 동사로 잡혀
    직전 완결 월 기본값으로 간다."""
    assert resolve_query_time("최근 5분 내 종료된 프로세스", NOW).clarify == "unresolved"


# ── 수용 기준·경계 확인(통과 기대) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_db_without_system_is_outside_guard():
    """`system_of`가 None인 DB(cloud_portal)는 판정 밖 — 빼지 않는다."""
    result, seen = await _run(
        "서버 CPU 사용률 추이", hints=[], classified=["cloud_portal", "polestar_b0"],
        active=["polestar_b0", "cloud_portal"],
    )
    assert seen["executed"] == ["cloud_portal", "polestar_b0"]
    assert not _ownership_notes(result)


@pytest.mark.asyncio
async def test_notice_text_only_user_expression():
    result, _ = await _run(
        "ITAM에서 서버 메모리 사용률", hints=["ITAM"], classified=["itam"],
    )
    text = result["final_response"]
    assert "「ITAM」" in text
    for leaked in ("ITAM DB", "itam", "폴스타", "polestar", "자산관리"):
        assert leaked not in text


@pytest.mark.parametrize("query, expected", [
    ("자산관리 서버들의 CPU 사용률", True),
    ("itam에서 서버들 목록", True),
    ("서버들 담당 부서 알려줘", False),
    ("폴스타 서버들 운영체제", False),
    ("polestar와 ITAM 서버들", False),
])
def test_names_only_zoneless_sources_real_registry(query: str, expected: bool) -> None:
    assert names_only_zoneless_sources(query) is expected


@pytest.mark.parametrize("query", [
    "6개월 안에 eol 도래 장비",       # (?i:EO[SL]) 인라인 플래그 — 소문자
    "6개월 안에 Eos 되는 서버",
    "두 달 안에 만료되는 계약",
    "30일 내로 마감되는 계약",
])
def test_deadline_variants_not_clarified(query: str) -> None:
    qt = resolve_query_time(query, NOW)
    assert qt.clarify is None and qt.metric is not None and qt.metric.source == "default"


@pytest.mark.parametrize("query", [
    "3개월 안에 발생한 알람",
    "최근 5분",
    "6개월 안에 지원종료 예정. 3개월 안에 발생한 알람",   # 문장 경계 — 둘째 흔적은 유지
    "3개월 안에 발생한 장애와 그 이후 조치 완료 건",      # 15자 밖 무관 동사
])
def test_plain_within_still_trace(query: str) -> None:
    assert resolve_query_time(query, NOW).clarify == "unresolved"


def test_inline_flag_does_not_leak_case_insensitivity():
    """`(?i:…)` 스코프 플래그는 그 그룹 안에만 — 3.12에서 전역 플래그로 새지 않는다."""
    from src.domain.time_expr import _TRACE_RE
    assert _TRACE_RE.flags & 2 == 0  # re.IGNORECASE 비설정
