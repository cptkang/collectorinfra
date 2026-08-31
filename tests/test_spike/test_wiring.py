"""급증 배선 — `_try_deterministic` 진입 조건 · 한계 표기 (Plan 82 W9-T9 · SPEC-spike-condition).

**플래그 OFF면 `_try_deterministic` 반환이 불변**이어야 한다(회귀 0). 폼필과 배타이고,
재시도 턴에는 진입하지 않으며, `build_stat_month_block`은 비교 모드에 **주입되지 않는다**
(단일 기간 강제와 정면 충돌 — §6.11).

DB·LLM 0(D-127).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config import AppConfig, QueryConfig, Text2SQLConfig
from src.nodes.query_generator import _GenContext, _try_deterministic, _try_spike
from src.nodes.output_generator import _append_spike_notes

SPIKE_QUERY = "파일시스템 사용률이 갑자기 80% 이상으로 상승한 서버 목록"


def _config(*, enabled: bool = True) -> AppConfig:
    config = AppConfig()
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(spike_condition_enabled=enabled)
    return config


def _ctx(config: AppConfig, *, query: str = SPIKE_QUERY, is_retry: bool = False,
         stat_month=None) -> _GenContext:
    return _GenContext(
        llm=MagicMock(),
        app_config=config,
        user_query=query,
        retry_count=0,
        is_retry=is_retry,
        limit_value=100,
        stat_month=stat_month,
        stat_block_db=True,
        conversation_context=None,
        prior_scope=None,
        adapter_db_ids={"polestar", "b0", "cm_gp"},
    )


def _state(**overrides) -> dict:
    base = {
        "active_db_id": "polestar",
        "active_db_engine": "postgresql",
        "user_query": SPIKE_QUERY,
        "parsed_requirements": {"query_targets": ["서버"]},
        "template_structure": None,
        "column_mapping": None,
    }
    base.update(overrides)
    return base


class TestFlagGate:
    def test_disabled_returns_none(self):
        assert _try_spike(_state(), _ctx(_config(enabled=False))) is None

    def test_disabled_keeps_deterministic_result_unchanged(self):
        """플래그 OFF면 `_try_deterministic`의 반환이 폼필 경로 그대로다."""
        off = _try_deterministic(_state(), _ctx(_config(enabled=False)))

        assert off is None or "sql" not in off


class TestEntryConditions:
    def test_retry_turn_never_enters(self):
        """재시도 턴에는 결정적 SQL이 이미 실패했을 수 있다 — LLM이 에러를 반영하게 둔다."""
        assert _try_deterministic(_state(), _ctx(_config(), is_retry=True)) is None

    def test_non_adapter_db_is_skipped(self):
        state = _state(active_db_id="other_db")

        assert _try_spike(state, _ctx(_config())) is None

    def test_no_spike_term_is_skipped(self):
        ctx = _ctx(_config(), query="파일시스템 사용률 80% 이상인 서버")

        assert _try_spike(_state(), ctx) is None

    def test_missing_absolute_threshold_is_skipped(self):
        """차분만으로 판정하면 5→10%(2배)가 75→85%를 이겨 저사용이 상위를 점령한다."""
        ctx = _ctx(_config(), query="파일시스템 사용률이 갑자기 급증한 서버")

        assert _try_spike(_state(), ctx) is None

    def test_non_filesystem_axis_is_skipped(self):
        ctx = _ctx(_config(), query="CPU 사용률이 갑자기 80% 이상으로 상승한 서버")

        assert _try_spike(_state(), ctx) is None


class TestAssembly:
    def test_sql_is_assembled(self):
        result = _try_spike(_state(), _ctx(_config()))

        assert result["sql"].lstrip().startswith("SELECT")
        assert "GROUP BY svr.name, r.name" in result["sql"]
        assert ">= 80" in result["sql"]
        assert ">= 20" in result["sql"], "임계 미명시 → 선언 파일 기본값 +20%p"

    def test_engine_dialect_follows_state(self):
        pg = _try_spike(_state(), _ctx(_config()))["sql"]
        db2 = _try_spike(
            _state(active_db_engine="db2"), _ctx(_config())
        )["sql"]

        assert "LIMIT 100" in pg and "::numeric" in pg
        assert "FETCH FIRST 100 ROWS ONLY" in db2 and "::numeric" not in db2

    def test_resolved_period_drives_the_comparison(self):
        ctx = _ctx(_config(), stat_month=("202607", "202607"))

        sql = _try_spike(_state(), ctx)["sql"]

        assert "s.stat_date IN ('202606', '202607')" in sql

    def test_explicit_comparison_expression_wins(self):
        ctx = _ctx(
            _config(),
            query="전월 대비 파일시스템 사용률이 갑자기 80% 이상으로 상승한 서버",
            stat_month=("202501", "202501"),
        )

        sql = _try_spike(_state(), ctx)["sql"]

        assert "'202501'" not in sql, "명시된 비교 표현이 해석된 단일 기간을 이긴다"

    def test_deterministic_path_returns_spike_when_no_form_fill(self):
        result = _try_deterministic(_state(), _ctx(_config()))

        assert result is not None
        assert "GROUP BY svr.name, r.name" in result["sql"]


class TestLimitationDisclosure:
    def test_default_threshold_is_disclosed(self):
        notes = _try_spike(_state(), _ctx(_config()))["spike_notes"]

        assert any("기본값" in n and "20%p" in n for n in notes)

    def test_capacity_change_note_is_always_present(self):
        notes = _try_spike(_state(), _ctx(_config()))["spike_notes"]

        assert any("용량 변경" in n for n in notes)

    def test_explicit_threshold_is_not_marked_default(self):
        ctx = _ctx(
            _config(),
            query="파일시스템 사용률이 전월 대비 30%p 이상 급증해 80% 이상으로 상승한 서버",
        )

        notes = _try_spike(_state(), ctx)["spike_notes"]

        assert any("30%p" in n for n in notes)
        assert not any("기본값" in n for n in notes)

    def test_week_request_is_blocked_with_reason_and_no_sql(self):
        ctx = _ctx(
            _config(),
            query="파일시스템 사용률이 지난주 대비 갑자기 80% 이상으로 상승한 서버",
        )

        result = _try_spike(_state(), ctx)

        assert "sql" not in result, "보존기간 미확인 — 약속하지 않는다"
        assert any("보존기간" in n for n in result["spike_notes"])
        assert any("월 단위" in n for n in result["spike_notes"])

    def test_blocked_result_carries_no_sql_key_for_the_caller(self):
        """★ 호출부는 `form_fill.get("sql")`로 읽는다 — notes만 담긴 dict가 와도 죽지 않는다.

        `form_fill["sql"]`이면 주 단위 차단 턴마다 KeyError로 노드가 죽는다(실측 2026-08-28).
        """
        ctx = _ctx(
            _config(),
            query="파일시스템 사용률이 지난주 대비 갑자기 80% 이상으로 상승한 서버",
        )

        result = _try_deterministic(_state(), ctx)

        assert result is not None
        assert result.get("sql") is None
        assert result["spike_notes"]

    def test_other_metric_axis_is_reported_as_unevaluated(self):
        ctx = _ctx(
            _config(),
            query="CPU 80% 이상인 서버 중 파일시스템이 갑자기 80% 이상으로 상승한 목록",
        )

        notes = _try_spike(_state(), ctx)["spike_notes"]

        assert any("파일시스템 사용률 급증만" in n for n in notes)
        assert any("CPU" in n for n in notes)

    def test_notes_reach_the_response_deterministically(self):
        notes = _try_spike(_state(), _ctx(_config()))["spike_notes"]

        text = _append_spike_notes("조회 결과입니다.", {"spike_notes": notes})

        assert "조회 결과입니다." in text
        assert "용량 변경" in text
        assert _append_spike_notes("x", {}) == "x"


class TestPeriodBlockExclusivity:
    def test_stat_month_block_is_not_injected_in_comparison_mode(self):
        """비교 모드는 프롬프트 경로를 지나지 않는다 — 단일 기간 강제와 구조적으로 배타다.

        `build_stat_month_block`이 강제하는 `s.stat_date = 'YYYYMM'` 단일 등호가
        두 기간 `IN` 필터와 충돌하면 SQL이 조용히 한 달만 보게 된다(§6.11).
        """
        from src.utils.query_gen_common import build_stat_month_block

        sql = _try_spike(_state(), _ctx(_config(), stat_month=("202607", "202607")))["sql"]
        block = build_stat_month_block(("202607", "202607"))
        where = sql.split(" WHERE ", 1)[1].split(" GROUP BY ", 1)[0]

        assert "s.stat_date = '202607'" in block, "블록은 단일 등호를 강제한다"
        # 기간 **필터**는 두 기간 IN 하나뿐이다. SELECT/HAVING의 `stat_date = '…'`는
        # 조건부 집계의 피벗 분기라 필터가 아니다 — 그래서 WHERE 절만 본다.
        assert "s.stat_date =" not in where
        assert "s.stat_date IN ('202606', '202607')" in where
