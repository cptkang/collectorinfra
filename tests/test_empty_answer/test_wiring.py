"""0건 진단 배선 — 플래그·프로브 발동·G-5 (Plan 82 W8-T5 · SPEC-empty-answer-diagnosis).

**플래그 OFF면 0건 응답이 바이트 동일**해야 한다(회귀 0). 결과가 있으면 프로브 호출은
0회여야 하고(정상 경로 비용 0), 프로브 실패는 사유와 함께 강등돼야 한다(침묵 금지).

DB는 전부 mock — 실 DBHub 미사용(D-127 · localhost:9099 연결 거부).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import MagicMock

import pytest

from src.config import AppConfig, QueryConfig, Text2SQLConfig
from src.dbhub.models import QueryResult
from src.nodes.output_generator import _generate_empty_result_response
from src.nodes.result_organizer import _diagnose_empty_result, result_organizer

SQL = (
    "SELECT svr.name, r.name FROM res r JOIN metric s ON r.id = s.res_id "
    "WHERE r.res_type = 'FileSystems' AND r.dtime IS NULL "
    "GROUP BY svr.name, r.name "
    "HAVING MAX(s.cpu_val) >= 80 AND MAX(s.fs_val) >= 80 "
    "ORDER BY 1 LIMIT 100"
)


def _config(*, enabled: bool = True, max_probes: int = 5) -> AppConfig:
    """검증 대상 필드를 **명시**해 `.env` 누수를 막는다(Known Mistakes)."""
    config = AppConfig()
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(
        empty_diagnosis_enabled=enabled,
        empty_diagnosis_max_probes=max_probes,
    )
    return config


class _CountingClient:
    """프로브 호출 수와 실행 SQL을 기록하는 DB 클라이언트 대역."""

    def __init__(self, counts: list[int] | Exception):
        self.counts = counts
        self.calls: list[str] = []

    async def execute_sql(self, sql: str) -> QueryResult:
        self.calls.append(sql)
        if isinstance(self.counts, Exception):
            raise self.counts
        value = self.counts[len(self.calls) - 1]
        return QueryResult(columns=["count"], rows=[{"count": value}], row_count=1)


def _patch_db(monkeypatch, client):
    @asynccontextmanager
    async def _ctx(_config, *, db_id=None):
        yield client

    # `src/nodes/__init__.py`가 동명 함수를 재수출해 문자열 경로가 **모듈이 아니라 함수**로
    # 해석된다 — 모듈 객체를 직접 집어 패치한다.
    monkeypatch.setattr(import_module("src.nodes.result_organizer"), "get_db_client", _ctx)
    return client


def _state(**overrides) -> dict:
    base = {
        "query_results": [],
        "parsed_requirements": {
            "query_targets": ["서버"],
            "filter_conditions": ["CPU 사용률 80% 이상", "파일시스템 사용률 80% 이상"],
        },
        "template_structure": None,
        "user_query": "CPU 80% 이상인 서버 중 파일시스템이 갑자기 80% 이상으로 상승한 목록",
        "generated_sql": SQL,
        "active_db_id": "polestar",
        "retry_count": 0,
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────
# 플래그 OFF — 회귀 0
# ──────────────────────────────────────────────

class TestFlagOff:
    @pytest.mark.asyncio
    async def test_no_probe_when_disabled(self, monkeypatch):
        client = _patch_db(monkeypatch, _CountingClient([1204, 12]))

        result = await _diagnose_empty_result(
            _state(), _state()["parsed_requirements"], _config(enabled=False)
        )

        assert result is None
        assert client.calls == []

    def test_empty_response_is_byte_identical_without_diagnosis(self):
        parsed = {
            "query_targets": ["서버"],
            "filter_conditions": ["CPU 80% 이상"],
            "time_range": {"start": "2026-07-01", "end": "2026-07-31"},
        }

        assert _generate_empty_result_response(parsed) == (
            "조건에 해당하는 서버 데이터가 없습니다."
            "\n\n다음과 같은 방법을 시도해보세요:"
            "\n- 필터 조건을 완화해보세요 (예: 임계값 낮추기)"
            "\n- 시간 범위를 넓혀보세요"
        )
        assert _generate_empty_result_response(parsed, None) == _generate_empty_result_response(parsed)
        assert _generate_empty_result_response(parsed, {}) == _generate_empty_result_response(parsed)


# ──────────────────────────────────────────────
# 발동 조건 — 0건일 때만
# ──────────────────────────────────────────────

class TestProbeActivation:
    @pytest.mark.asyncio
    async def test_probe_runs_only_on_empty_result(self, monkeypatch):
        client = _patch_db(monkeypatch, _CountingClient([1204, 12]))
        state = _state(query_results=[{"server_name": "a"}])

        await result_organizer(state, llm=MagicMock(), app_config=_config())

        assert client.calls == [], "결과가 있으면 프로브 호출은 0회다"

    @pytest.mark.asyncio
    async def test_probe_count_matches_user_conditions(self, monkeypatch):
        client = _patch_db(monkeypatch, _CountingClient([1204, 12]))

        diagnosis = await _diagnose_empty_result(
            _state(), _state()["parsed_requirements"], _config()
        )

        assert len(client.calls) == 2, "사용자 조건 2개 → 0·1단계 프로브 2회"
        assert all(c.startswith("SELECT COUNT(*) FROM (") for c in client.calls)
        assert [s.counts[""] for s in diagnosis.stages] == [1204, 12, 0]

    @pytest.mark.asyncio
    async def test_probe_respects_max_probes(self, monkeypatch):
        client = _patch_db(monkeypatch, _CountingClient([1204]))

        diagnosis = await _diagnose_empty_result(
            _state(), _state()["parsed_requirements"], _config(max_probes=1)
        )

        assert len(client.calls) == 1
        assert any("프로브 상한" in n for n in diagnosis.notes)
        assert diagnosis.stages[1].counts[""] is None, "미측정은 0이 아니라 미측정이다"


# ──────────────────────────────────────────────
# 강등 — 사유를 남긴다
# ──────────────────────────────────────────────

class TestDegradation:
    @pytest.mark.asyncio
    async def test_probe_failure_is_reported_not_silent(self, monkeypatch):
        _patch_db(monkeypatch, _CountingClient(RuntimeError("connection refused")))

        diagnosis = await _diagnose_empty_result(
            _state(), _state()["parsed_requirements"], _config()
        )

        assert any("프로브가 실패" in n for n in diagnosis.notes)
        assert any("RuntimeError" in n for n in diagnosis.notes)

    @pytest.mark.asyncio
    async def test_no_user_condition_still_reports_unexpressed(self, monkeypatch):
        """프로브를 못 돌려도 **미반영 경고와 사유는 낸다** — 말하지 않는 것이 최악이다."""
        client = _patch_db(monkeypatch, _CountingClient([]))
        state = _state(generated_sql="SELECT a FROM t WHERE t.name = 'x'")

        diagnosis = await _diagnose_empty_result(
            state, state["parsed_requirements"], _config()
        )

        assert client.calls == []
        assert diagnosis.stages == ()
        assert diagnosis.unexpressed, "'갑자기'가 조건에 없으므로 미반영 경고가 나와야 한다"
        assert any("수치 비교가 없어" in n for n in diagnosis.notes)

    @pytest.mark.asyncio
    async def test_nothing_to_say_returns_none(self, monkeypatch):
        _patch_db(monkeypatch, _CountingClient([]))
        state = _state(
            generated_sql="SELECT a FROM t WHERE t.name = 'x'",
            user_query="서버 목록 보여줘",
            parsed_requirements={"query_targets": ["서버"], "filter_conditions": []},
        )

        assert await _diagnose_empty_result(
            state, state["parsed_requirements"], _config()
        ) is None


# ──────────────────────────────────────────────
# G-5 — 0건 재생성 판정
# ──────────────────────────────────────────────

class TestRegenerationGate:
    @pytest.mark.asyncio
    async def test_p0_positive_stops_regeneration(self, monkeypatch):
        _patch_db(monkeypatch, _CountingClient([1204, 12]))
        state = _state(parsed_requirements={
            **_state()["parsed_requirements"], "aggregation": "count",
        })

        result = await result_organizer(state, llm=MagicMock(), app_config=_config())

        assert result["error_message"] != "data_insufficient"
        assert result["empty_diagnosis"]["regenerable"] is False

    @pytest.mark.asyncio
    async def test_p0_zero_allows_regeneration(self, monkeypatch):
        _patch_db(monkeypatch, _CountingClient([0, 0]))
        state = _state(parsed_requirements={
            **_state()["parsed_requirements"], "aggregation": "count",
        })

        result = await result_organizer(state, llm=MagicMock(), app_config=_config())

        assert result["error_message"] == "data_insufficient"
        assert result["empty_diagnosis"]["regenerable"] is True

    @pytest.mark.asyncio
    async def test_diagnosis_reaches_the_response(self, monkeypatch):
        _patch_db(monkeypatch, _CountingClient([1204, 12]))
        state = _state()

        result = await result_organizer(state, llm=MagicMock(), app_config=_config())
        text = _generate_empty_result_response(
            state["parsed_requirements"], result["empty_diagnosis"]
        )

        assert "조건에 해당하는 서버 데이터가 없습니다." in text
        assert "1,204" in text
        assert "여기서 끊겼습니다" in text
        assert "반영되지 않았습니다" in text


# ──────────────────────────────────────────────
# 그룹 축 — group_results 재사용
# ──────────────────────────────────────────────

class TestGroupFunnel:
    @pytest.mark.asyncio
    async def test_two_groups_split_into_columns(self, monkeypatch):
        client = _patch_db(monkeypatch, _CountingClient([412, 12, 792, 0]))
        state = _state(group_results={
            "bank": {"label": "은행존", "db_ids": ["b0"], "row_count": 0, "sqls": [SQL]},
            "common": {"label": "공동존", "db_ids": ["cm_gp"], "row_count": 0, "sqls": [SQL]},
        })

        diagnosis = await _diagnose_empty_result(
            state, state["parsed_requirements"], _config()
        )

        assert len(client.calls) == 4, "그룹 2개 × 프로브 2회"
        assert diagnosis.stages[0].counts == {"은행존": 412, "공동존": 792}
        by_group = {bp.group: bp for bp in diagnosis.breakpoints}
        assert by_group["공동존"].mfs_index == 1
        assert by_group["은행존"].mfs_index == 2
