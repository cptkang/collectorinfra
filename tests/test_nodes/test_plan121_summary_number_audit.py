"""plans/121 TP-4.4 ① — 요약 숫자 대조 **측정 전용**(G-25 · D-272 ⑥).

검증 항목
- 분류기(순수 함수): 오탐 원천 사례 — 질의 숫자 · 76.5→76% · 1,024MB→1GB · 합계·평균 ·
  「Linux 2건」 · 「2026년 5월」 · 임계 초과 건수 · 식별자·IP·목록 번호 제외 · 불일치
- 응답 바이트 불변: 요약 직후 측정 호출이 있든 없든 `output_generator` 반환·선행 발행이 같다
- 로그: 한 줄 · 개수만 — 요약의 숫자·문장 원문이 없다(D-219)
- 측정 예외는 삼키고 클래스명만 경고 · 요약 실패 문구(LLM 문장 아님)는 재지 않는다

LLM·DB·네트워크 0 — 모의 LLM만 쓴다.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessageChunk

# `src.nodes` 패키지가 같은 이름의 함수를 재노출하므로 모듈은 import_module로 잡는다.
og = importlib.import_module("src.nodes.output_generator")

_LOGGER = "src.nodes.output_generator"


def _classes(summary: str, rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, int]:
    """0이 아닌 부류만 남긴 분류 결과."""
    counts = og.classify_summary_numbers(summary, rows=rows, **kwargs)
    return {k: v for k, v in counts.items() if v}


# ── 추출 ────────────────────────────────────────────────────────────────


class TestExtract:
    def test_identifiers_ip_versions_are_not_numbers(self) -> None:
        assert og._extract_summary_numbers("web01 · srv-02 · 10.0.0.1 · v7.2.1 · x86_64") == []

    def test_list_markers_are_not_numbers(self) -> None:
        tokens = og._extract_summary_numbers("1. 첫째 항목\n2) 둘째 항목")
        assert tokens == []

    def test_thousands_comma_is_one_number_other_comma_is_a_list(self) -> None:
        assert [t.value for t in og._extract_summary_numbers("1,024건")] == [1024.0]
        assert [t.value for t in og._extract_summary_numbers("2,3번 서버")] == [2.0, 3.0]

    def test_date_forms_are_tagged_date(self) -> None:
        tokens = og._extract_summary_numbers("2026년 5월 · 2026-05-01. · 14:30 · 3일")
        assert [t.tag for t in tokens] == ["date"] * 5

    def test_units_and_korean_scale(self) -> None:
        pct, byte, scaled = og._extract_summary_numbers("76.5 % · 1.5GiB · 1.2만 건")
        assert (pct.value, pct.decimals, pct.tag) == (76.5, 1, "%")
        assert (byte.value, byte.tag) == (1.5, "byte")
        assert (scaled.value, scaled.scale) == (1.2, 1e4)

    def test_ordinals(self) -> None:
        assert [t.tag for t in og._extract_summary_numbers("1위 · 2번째")] == ["ordinal"] * 2


# ── 분류(오탐 원천 사례) ───────────────────────────────────────────────


_SERVERS = [
    {"hostname": "alpha", "os": "Linux", "mem_mb": 1024, "cpu": 76.5},
    {"hostname": "bravo", "os": "Linux", "mem_mb": 2048, "cpu": 50.1},
    {"hostname": "charlie", "os": "AIX", "mem_mb": 512, "cpu": 30.0},
]


class TestClassify:
    def test_every_class_key_present(self) -> None:
        counts = og.classify_summary_numbers("", rows=_SERVERS)
        assert tuple(counts) == og.SUMMARY_NUMBER_CLASSES
        assert set(counts.values()) == {0}

    def test_table_value(self) -> None:
        assert _classes("최고 CPU는 76.5%입니다", _SERVERS) == {"table": 1}

    def test_query_number(self) -> None:
        assert _classes(
            "상위 10개 서버를 조회했습니다", _SERVERS, query="CPU 상위 10개 서버"
        ) == {"query": 1}

    def test_rounding_76_5_to_76(self) -> None:
        assert _classes("최고 CPU는 약 76%입니다", _SERVERS) == {"rounded": 1}

    def test_unit_conversion_1024mb_to_1gb_not_a_category_count(self) -> None:
        # 「1」은 범주 건수(AIX 1건)이기도 하지만 단위 붙은 값은 건수와 맞추지 않는다.
        assert _classes("메모리 최소 서버는 0.5GB, 최대 2GB, alpha는 1GB", _SERVERS) == {
            "rounded": 3,
        }

    def test_percent_from_ratio(self) -> None:
        rows = [{"hostname": "alpha", "ratio": 0.765}]
        assert _classes("사용률 76.5%", rows) == {"rounded": 1}

    def test_sum_and_mean_are_derived(self) -> None:
        # mem 합계 3584 · CPU 평균 52.2 — 표 값이 아니라 코드 파생값
        assert _classes("메모리 합계 3584MB, CPU 평균 52.2%", _SERVERS) == {"derived": 2}

    def test_row_count_and_threshold_count_are_derived(self) -> None:
        # 「40 이상 2대」: 질의 숫자 기준 이상 건수 · 「총 3대」: 전체 행 수
        assert _classes(
            "CPU 40 이상 서버는 총 3대 중 2대입니다", _SERVERS, query="CPU 40 이상 서버"
        ) == {"query": 1, "derived": 2}

    def test_category_counts(self) -> None:
        assert _classes("Linux 2건, AIX 1건", _SERVERS) == {"category": 2}

    def test_year_month(self) -> None:
        assert _classes("2026년 5월 기준 조회 결과입니다", _SERVERS) == {"date": 2}

    def test_mismatch(self) -> None:
        assert _classes("최고 CPU는 99.9%입니다", _SERVERS) == {"mismatch": 1}

    def test_string_and_decimal_cells_are_table_values(self) -> None:
        rows = [{"hostname": "alpha", "cores": "4.0", "mem": Decimal("63.25")}]
        assert _classes("코어 4개, 메모리 63.25", rows) == {"table": 2}

    def test_data_summary_and_aggregates_are_derived(self) -> None:
        aggregates = {
            "applied": True,
            "columns": [
                {"column": "cnt", "kind": "COUNT", "per_db": {"a": 7, "b": 5}, "total": 12},
            ],
        }
        rows = [{"_source_db": "a", "cnt": 7}, {"_source_db": "b", "cnt": 5}]
        assert _classes(
            "전체 12건 · 조회 대상 38곳", rows, data_summary="조회 대상 38곳", aggregates=aggregates
        ) == {"derived": 2}

    def test_ordinal(self) -> None:
        assert _classes("1위는 alpha입니다", _SERVERS) == {"ordinal": 1}


# ── output_generator 배선 ─────────────────────────────────────────────


_SECRET_SUMMARY = "alpha의 값은 987.65이고 합은 4321입니다."


class _FakeLLM:
    """astream으로 정해진 요약을 흘리는 모의 LLM(예외 주입 가능)."""

    def __init__(self, text: str = _SECRET_SUMMARY, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def astream(
        self, messages: Any, config: Any = None, **kwargs: Any
    ) -> AsyncIterator[AIMessageChunk]:
        async def _gen() -> AsyncIterator[AIMessageChunk]:
            if self.error is not None:
                raise self.error
            yield AIMessageChunk(content=self.text)

        return _gen()


def _state() -> dict[str, Any]:
    rows = [{"hostname": "alpha", "value": 12.5}, {"hostname": "bravo", "value": 3.25}]
    return {
        "user_query": "서버 값 조회",
        "organized_data": {"summary": f"{len(rows)}건 조회", "rows": rows,
                           "column_mapping": None, "is_sufficient": True},
        "parsed_requirements": {"original_query": "서버 값 조회", "output_format": "text",
                                "query_targets": ["서버"]},
        "query_results": rows,
    }


@pytest.fixture
def prefixes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`emit_answer_prefix` 호출 기록(부모 run 없는 단위 테스트에서 발행 여부 판독)."""
    sent: list[str] = []

    async def _emit(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(og, "emit_answer_prefix", _emit)
    return sent


async def _run(stream: bool) -> dict[str, Any]:
    out: dict[str, Any] = await og.output_generator(
        _state(), llm=_FakeLLM(), app_config=MagicMock(), stream_user_response=stream,
    )
    return out


class TestOutputGeneratorWiring:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("stream", [True, False])
    async def test_response_bytes_identical_with_and_without_measurement(
        self, monkeypatch: pytest.MonkeyPatch, prefixes: list[str], stream: bool
    ) -> None:
        calls: list[str] = []
        real = og._audit_summary_numbers

        def _spy(summary: str, state: Any, *, stream: bool) -> None:
            calls.append(summary)
            real(summary, state, stream=stream)

        monkeypatch.setattr(og, "_audit_summary_numbers", _spy)
        measured = await _run(stream)
        measured_prefixes = list(prefixes)
        assert calls == [_SECRET_SUMMARY]  # 요약 직후 1회

        prefixes.clear()
        monkeypatch.setattr(og, "_audit_summary_numbers", lambda *a, **k: None)
        baseline = await _run(stream)

        assert measured["final_response"] == baseline["final_response"]
        assert measured.keys() == baseline.keys()
        for key in measured:
            if key != "messages":
                assert measured[key] == baseline[key], key
        assert [m.content for m in measured["messages"]] == [
            m.content for m in baseline["messages"]
        ]
        assert measured_prefixes == prefixes

    @pytest.mark.asyncio
    async def test_log_line_has_counts_only(
        self, caplog: pytest.LogCaptureFixture, prefixes: list[str]
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await _run(True)
        lines = [r.getMessage() for r in caplog.records if "TP-4.4" in r.getMessage()]
        assert len(lines) == 1
        line = lines[0]
        assert "stream=1" in line and "total=2" in line and "mismatch=2" in line
        for fragment in ("987.65", "4321", "alpha", "합은"):
            assert fragment not in line

    @pytest.mark.asyncio
    async def test_measurement_error_is_swallowed_with_class_name_only(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        prefixes: list[str],
    ) -> None:
        baseline = await _run(True)

        def _boom(*args: Any, **kwargs: Any) -> dict[str, int]:
            raise ValueError("민감 987.65")

        monkeypatch.setattr(og, "classify_summary_numbers", _boom)
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            out = await _run(True)
        assert out["final_response"] == baseline["final_response"]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ValueError" in w for w in warnings)
        assert not any("987.65" in w or "민감" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_failed_summary_note_is_not_measured(
        self, caplog: pytest.LogCaptureFixture, prefixes: list[str]
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            out = await og.output_generator(
                _state(), llm=_FakeLLM(error=RuntimeError("down")), app_config=MagicMock(),
            )
        assert og._SUMMARY_FAILED_NOTE in out["final_response"]
        assert not [r for r in caplog.records if "TP-4.4" in r.getMessage()]
