"""범위 선택 상쇄 장치·시간 문구 배선 (plans/82 v7 R-6·R-7 · D-249).

- **R-6** 역질문 발동(발동률의 분자)과 범위 축소를 감사에 남긴다 — 4개 진입점 대칭
- **R-7** 그룹 계측 분포가 범위 질문의 예상 시간 문구로 이어진다 — 종전에는 라우트가
  표본·분포를 넘기지 않아 표본이 쌓여도 문구가 영영 나오지 않았다

DB·LLM 0(D-127).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes import query as route
from src.config import CompositeConfig, MultiDBConfig
from src.observability import group_metrics as gm
from src.routing.execution_groups import partition_execution_groups

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
FULL_SCAN = "모든 서버의 OS 버전을 조회해줘"


@pytest.fixture(autouse=True)
def _clean_metrics():
    gm.reset()
    yield
    gm.reset()


def _config():
    return SimpleNamespace(
        composite=CompositeConfig(scope_select_enabled=True),
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(ALL_DBS)),
    )


def _ask():
    return route._scope_select_or_none(
        SimpleNamespace(query=FULL_SCAN, selected_db_ids=None), None, _config(), None
    )


def _record(n: int, ms: float = 4000.0) -> None:
    for group in partition_execution_groups(ALL_DBS):
        for _ in range(n):
            gm.record_group(group, ms)


class TestR7EstimateWiring:
    def test_estimate_appears_once_every_group_has_enough_samples(self):
        _record(gm.MIN_SAMPLES_FOR_ESTIMATE)
        assert re.search(r"\(예상 8~8초\)", _ask()["question"])

    def test_no_estimate_below_threshold(self):
        """표본 미달이면 숫자를 만들지 않는다(§5.5 S-C — 환각 금지)."""
        _record(gm.MIN_SAMPLES_FOR_ESTIMATE - 1)
        assert "예상" not in _ask()["question"]

    def test_weakest_group_decides(self):
        """한 그룹이라도 표본이 모자라면 합계 추정의 근거가 없다."""
        bank, common = partition_execution_groups(ALL_DBS)
        for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE):
            gm.record_group(bank, 4000.0)
        gm.record_group(common, 4000.0)
        assert "예상" not in _ask()["question"]

    def test_distribution_fields_do_not_leak_into_options(self):
        _record(gm.MIN_SAMPLES_FOR_ESTIMATE)
        for option in _ask()["options"]:
            assert "p50_ms" not in option and "p90_ms" not in option


class TestR6ClarificationAudit:
    def test_records_kind_axis_and_option_count_without_query_text(self):
        clarification = {"kind": "scope_select", "axis": "zone_group", "question": FULL_SCAN,
                         "options": [{}, {}, {}], "original_query": FULL_SCAN}
        with patch("src.security.audit_logger.log_clarification", AsyncMock()) as log:
            asyncio.run(route._audit_clarification(clarification, {"sub": "u1"}, "t1"))
        log.assert_awaited_once_with(
            kind="scope_select", axis="zone_group", option_count=3, user_id="u1", thread_id="t1",
        )

    def test_audit_failure_is_swallowed(self):
        with patch("src.security.audit_logger.log_clarification", AsyncMock(side_effect=OSError("disk"))):
            asyncio.run(route._audit_clarification({"kind": "zone_select"}, None, None))

    def test_every_pre_gate_entry_point_records_the_fire(self):
        """경로 대칭 — 텍스트 2 · 파일 2 진입점이 모두 발동을 기록한다(한쪽만 기록하면 발동률이 틀린다)."""
        src = Path(route.__file__).read_text(encoding="utf-8")
        assert src.count("await _audit_clarification(clarification, current_user, thread_id)") == 4
        assert src.count("await _audit_scope_narrowed(input_state, current_user, thread_id)") == 2


class TestR6ScopeNarrowedAudit:
    def test_narrowed_turn_is_recorded(self):
        state = {"scope_narrowed": {"selected": ["공동존"], "skipped": ["은행존"],
                                    "skipped_db_ids": ["polestar_b0"], "all_db_ids": ALL_DBS}}
        with patch("src.security.audit_logger.log_scope_narrowed", AsyncMock()) as log:
            asyncio.run(route._audit_scope_narrowed(state, {"sub": "u1"}, "t1"))
        log.assert_awaited_once_with(
            selected=["공동존"], skipped=["은행존"], skipped_db_ids=["polestar_b0"],
            user_id="u1", thread_id="t1",
        )

    def test_turn_without_narrowing_is_not_recorded(self):
        with patch("src.security.audit_logger.log_scope_narrowed", AsyncMock()) as log:
            asyncio.run(route._audit_scope_narrowed({"scope_narrowed": None}, None, None))
        log.assert_not_awaited()


class TestAuditRecords:
    """감사 파일에 남는 레코드 모양 — 이벤트 이름과 필드가 사후 집계의 키다."""

    def _capture(self, coro_factory):
        from src.security import audit_logger

        written = []

        async def _fake_write(entry):
            written.append(entry.to_dict())

        with patch.object(audit_logger, "_write_audit_file", _fake_write):
            asyncio.run(coro_factory(audit_logger))
        return written

    def test_clarification_issued(self):
        rec = self._capture(lambda a: a.log_clarification(kind="scope_select", axis="zone_group",
                                                          option_count=3, thread_id="t1"))[0]
        assert rec["event"] == "clarification_issued" and rec["kind"] == "scope_select"
        assert "user_query" not in rec and "question" not in rec

    def test_scope_narrowed(self):
        rec = self._capture(lambda a: a.log_scope_narrowed(selected=["공동존"], skipped=["은행존"],
                                                           skipped_db_ids=["polestar_b0"]))[0]
        assert rec["event"] == "scope_narrowed" and rec["skipped_db_ids"] == ["polestar_b0"]

    def test_group_execution(self):
        rec = self._capture(lambda a: a.log_group_execution(
            group_key="polestar:bank", label="은행존", kind="peer", db_ids=["polestar_b0"],
            row_count=12, elapsed_ms=3210.456))[0]
        assert rec["event"] == "group_execution" and rec["elapsed_ms"] == 3210.46
        assert "error_db_ids" not in rec, "오류가 없으면 필드를 남기지 않는다(None 제거)"
