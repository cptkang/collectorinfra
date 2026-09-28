"""plans/119 N-1 · T-4 — 응답 표는 코드가 렌더하고 LLM은 2~3줄 요약만 쓴다 (D-251 ④ · CU-5).

검증 항목(계획 §5.1 N-1 · §5.4 T-4)
- 표: 20행 초과 절단 줄 · 59열 전부(D-100) · 복합 필드명 `|`·셀 `|`·개행 탈출 · None 빈 칸 ·
  멀티 DB 균형 미리보기(출처 표시명)
- 요약: LLM 입력은 미리보기 5행 + 수치 요약(코드 계산) — 표 컬럼 규칙 없음
- 응답 순서: 표 → 요약 → 덧붙임
- 스트리밍: 표 선행 발행(stream on) · 미발행(stream off · D-062 중간 산출) · 실제 custom event 순서
- anytime: 요약 시간 초과·예외·마감 경과 시 표 + 사유 · 파일 경로 시간 초과에도 파일 보존 ·
  폼필 역질문 턴은 요약 LLM 0회
- 빈 결과는 LLM·선행 발행 모두 0(결정적 경로 그대로)

LLM·DB·네트워크 0 — 모의 LLM만 쓴다.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from src.domain.partial_result import PartialAnswer, render_partial_text
from src.llm import USER_RESPONSE_TAG
from src.utils.deadline import bind_request_deadline, unbind_request_deadline
from src.utils.progress_events import ANSWER_PREFIX_EVENT

# `src.nodes` 패키지가 같은 이름의 함수를 재노출하므로 모듈은 import_module로 잡는다.
og = importlib.import_module("src.nodes.output_generator")

_SUMMARY ="총 25건이며 CPU 최대는 99.5입니다. 임계치 90을 넘는 서버가 3대입니다."


class _RecordingLLM:
    """astream 호출을 기록하고 정해진 요약을 흘리는 모의 LLM(지연·예외 주입 가능)."""

    def __init__(self, text: str = _SUMMARY, *, delay: float = 0.0, error: Exception | None = None,
                 events: list | None = None) -> None:
        self.text = text
        self.delay = delay
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.events = events if events is not None else []

    def astream(self, messages, config=None, **kwargs):  # noqa: ANN001 - langchain 메시지 목록
        self.calls.append({"messages": messages, "config": config, "kwargs": kwargs})
        self.events.append("llm")

        async def _gen():
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            yield AIMessageChunk(content=self.text)

        return _gen()


def _config() -> MagicMock:
    return MagicMock()


def _state(rows: list[dict], *, output_format: str = "text", **extra: Any) -> dict:
    state: dict[str, Any] = {
        "user_query": "서버 CPU 조회",
        "organized_data": {"summary": f"{len(rows)}건 조회", "rows": rows,
                           "column_mapping": None, "is_sufficient": True},
        "parsed_requirements": {"original_query": "서버 CPU 조회", "output_format": output_format,
                                "query_targets": ["서버"]},
        "query_results": rows,
    }
    state.update(extra)
    return state


def _table_lines(response: str) -> list[str]:
    return [line for line in response.split("\n") if line.startswith("|")]


def _split_cells(line: str) -> list[str]:
    import re

    body = line.strip()[1:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


@pytest.fixture
def prefixes(monkeypatch):
    """`emit_answer_prefix` 호출을 기록한다(부모 run 없는 단위 테스트에서 발행 여부 판독)."""
    sent: list[str] = []

    async def _emit(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(og, "emit_answer_prefix", _emit)
    return sent


@pytest.fixture
def deadline():
    """요청 마감을 묶는 헬퍼 — 테스트 끝에 반드시 푼다."""
    tokens: list = []

    def _bind(seconds_from_now: float, reserve: float = 0.0) -> None:
        tokens.append(bind_request_deadline(time.monotonic() + seconds_from_now, reserve))

    yield _bind
    for tok in reversed(tokens):
        try:
            unbind_request_deadline(tok)
        except ValueError:
            # 비동기 테스트는 복사된 컨텍스트(자기 태스크)에서 묶었다 — 그 컨텍스트는 테스트와
            # 함께 버려지므로 다른 테스트로 새지 않는다. 동기 테스트는 여기서 되돌린다.
            pass


# ── 표 렌더 ─────────────────────────────────────────────────────────────────


class TestResultTable:
    def test_over_twenty_rows_shows_twenty_and_total_line(self):
        rows = [{"host": f"h{i}", "cpu": float(i)} for i in range(25)]
        table = og._render_result_table(rows, ranked=False)
        lines = _table_lines(table)
        assert len(lines) == 2 + 20  # 헤더 · 구분선 · 본문 20
        assert "| h19 | 19.0 |" in table and "h20" not in table
        assert table.endswith("전체 25건 중 20건 표시(전체는 CSV 다운로드)")

    def test_twenty_or_less_has_no_total_line(self):
        table = og._render_result_table([{"host": "a"}, {"host": "b"}], ranked=False)
        assert "전체" not in table and len(_table_lines(table)) == 4

    def test_all_59_columns_rendered(self):
        rows = [{f"col{j}": j for j in range(59)} for _ in range(3)]
        table = og._render_result_table(rows, ranked=False)
        header = _table_lines(table)[0]
        assert len(_split_cells(header)) == 59
        assert _split_cells(_table_lines(table)[1]) == ["---"] * 59

    def test_composite_field_pipe_newline_and_none(self):
        rows = [{"그룹|서브": "a|b", "비고": "첫줄\n둘째줄", "값": None, "호스트": "w1"}]
        table = og._render_result_table(rows, ranked=False)
        header, sep, body = _table_lines(table)
        assert _split_cells(header) == ["그룹 > 서브", "비고", "값", "호스트"]
        cells = _split_cells(body)
        assert len(cells) == 4
        assert cells == [r"a\|b", "첫줄 둘째줄", "", "w1"]

    def test_multi_db_balanced_preview_and_source_label(self):
        from src.nodes.multi_db_executor import _merge_results
        from src.routing.domain_config import get_domain_by_id

        gp, yd = "polestar_cm_gp", "polestar_cm_yd"
        rows = _merge_results({
            gp: [{"host": f"gp-{i}"} for i in range(25)],
            yd: [{"host": f"yd-{i}"} for i in range(5)],
        })
        table = og._render_result_table(rows, ranked=False)
        assert "출처" in _split_cells(_table_lines(table)[0])
        assert "yd-4" in table  # 뒤 DB 행이 사라지지 않는다
        assert get_domain_by_id(yd).display_name in table
        assert "_source_db" not in table
        assert table.endswith("전체 30건 중 DB별로 고르게 20건 표시(전체는 CSV 다운로드)")

    def test_partial_result_table_is_same_renderer(self):
        """D-265 부분 결과와 정상 응답이 같은 렌더를 쓴다 — 종전 부분 결과 바이트 유지."""
        rows = [{"hostname": "kpo-web-01", "cpu": 91.5}]
        text = render_partial_text(PartialAnswer(rows=rows, source="query_results"))
        assert "| hostname | cpu |\n|---|---|\n| kpo-web-01 | 91.5 |" in text
        assert og._render_result_table(rows, ranked=False) == (
            "| hostname | cpu |\n|---|---|\n| kpo-web-01 | 91.5 |"
        )


# ── 요약 프롬프트 ─────────────────────────────────────────────────────────────


class TestSummaryPrompt:
    def test_preview_is_five_rows_and_no_column_rule(self):
        rows = [{"host": f"h{i}", "cpu": float(i)} for i in range(50)]
        prompt = og._build_response_prompt("q", "50건", rows)
        body = prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
        assert len(json.loads(body)) == 5
        assert "## 표시 규칙" not in prompt and "모두** 표에 포함" not in prompt
        # 수치 요약은 전체 50건 기준 코드 계산값 그대로
        assert "## 수치 요약 (전체 50건 전수 기준" in prompt and "최대 49.0" in prompt

    def test_system_prompt_is_summary_only(self):
        from src.prompts.output_generator import OUTPUT_SUMMARY_SYSTEM_PROMPT

        prompt = OUTPUT_SUMMARY_SYSTEM_PROMPT
        assert "2~3줄" in prompt and "표를 다시 쓰지 않습니다" in prompt
        assert "모든 컬럼을 표에 포함" not in prompt


# ── 노드 응답 ─────────────────────────────────────────────────────────────────


class TestTextResponse:
    @pytest.mark.asyncio
    async def test_table_then_summary_then_notes(self, prefixes):
        rows = [{"host": f"h{i}", "cpu": float(i)} for i in range(25)]
        llm = _RecordingLLM()
        state = _state(rows, spike_notes=["급증 한계 각주"])
        out = await og.output_generator(state, llm=llm, app_config=_config())
        text = out["final_response"]
        table = og._render_result_table(rows, ranked=False)
        assert text.startswith(table + "\n\n" + _SUMMARY)
        assert text.index(_SUMMARY) < text.index("- 급증 한계 각주")
        # 요약 LLM 입력: 요약 프롬프트 · 미리보기 5행 · 수치 요약 동반
        (call,) = llm.calls
        system, human = call["messages"][0].content, call["messages"][-1].content
        assert "짧게 요약" in system
        assert "## 수치 요약" in human and "상위 5건" in human
        assert call["config"] == {"tags": [USER_RESPONSE_TAG]}

    @pytest.mark.asyncio
    async def test_prefix_emitted_before_llm_when_streaming(self, prefixes, monkeypatch):
        events: list[str] = []

        async def _emit(text: str) -> None:
            events.append("prefix")
            prefixes.append(text)

        monkeypatch.setattr(og, "emit_answer_prefix", _emit)
        rows = [{"host": "a", "cpu": 1.0}]
        llm = _RecordingLLM(events=events)
        out = await og.output_generator(_state(rows), llm=llm, app_config=_config())
        assert events == ["prefix", "llm"]
        table = og._render_result_table(rows, ranked=False)
        assert prefixes == [table + "\n\n"]
        # 스트림 누적(선행 본문 + 요약 토큰)이 최종 본문의 앞부분과 같다 — 화면이 튀지 않는다
        assert out["final_response"].startswith(prefixes[0] + _SUMMARY)

    @pytest.mark.asyncio
    async def test_no_prefix_when_stream_off(self, prefixes):
        """D-062 중간 산출(stream_user_response=False)은 표를 선행 발행하지 않는다."""
        llm = _RecordingLLM()
        out = await og.output_generator(
            _state([{"host": "a"}]), llm=llm, app_config=_config(), stream_user_response=False,
        )
        assert prefixes == []
        assert llm.calls[0]["config"] is None  # 태그 없음(토큰 비노출)
        assert out["final_response"].startswith("| host |")

    @pytest.mark.asyncio
    async def test_empty_result_is_deterministic(self, prefixes):
        llm = _RecordingLLM()
        out = await og.output_generator(_state([]), llm=llm, app_config=_config())
        assert "데이터가 없습니다" in out["final_response"]
        assert llm.calls == [] and prefixes == []

    @pytest.mark.asyncio
    async def test_year_guard_applies_to_summary(self, prefixes):
        rows = [{"host": "a", "cpu": 1.0}]
        llm = _RecordingLLM("2023년 1월 기준 요약입니다.")
        state = _state(rows, form_month_anchor={"start": "202601", "end": "202606"})
        out = await og.output_generator(state, llm=llm, app_config=_config())
        text = out["final_response"]
        assert "**[확인 필요]**" in text
        assert text.index("| host |") < text.index("2023년 1월") < text.index("[확인 필요]")

    @pytest.mark.asyncio
    async def test_real_custom_event_precedes_summary_tokens(self):
        """실제 LangChain 이벤트 스트림에서 `answer_prefix`가 요약 토큰보다 먼저 나온다."""
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.runnables import RunnableLambda

        llm = GenericFakeChatModel(messages=iter([AIMessage(content="요약 한 줄입니다.")]))
        rows = [{"host": "a", "cpu": 1.0}]

        async def _node(state):
            return await og.output_generator(state, llm=llm, app_config=_config())

        order: list[str] = []
        async for ev in RunnableLambda(_node).astream_events(_state(rows), version="v2"):
            if ev["event"] == "on_custom_event" and ev["name"] == ANSWER_PREFIX_EVENT:
                order.append("prefix")
                assert ev["data"]["text"].startswith("| host | cpu |")
            elif ev["event"] == "on_chat_model_stream" and USER_RESPONSE_TAG in ev.get("tags", []):
                order.append("token")
        assert order[0] == "prefix" and "token" in order


# ── T-4 anytime — 시간 상한·예외 ─────────────────────────────────────────────


class TestSummaryBudget:
    def test_max_tokens_from_remaining_budget(self):
        assert og._summary_max_tokens(None) == og.SUMMARY_MAX_TOKENS
        assert og._summary_max_tokens(30.0) == og.SUMMARY_MAX_TOKENS
        assert og._summary_max_tokens(5.0) == 200  # 5초 ÷ 25ms/자
        assert og._summary_max_tokens(0.5) == og.SUMMARY_MIN_TOKENS

    def test_no_deadline_means_no_limit(self):
        assert og.narration_limit_sec() is None

    def test_limit_is_min_of_cap_and_processing_deadline(self, deadline):
        deadline(100.0, reserve=15.0)
        assert og.narration_limit_sec() == pytest.approx(og.SUMMARY_TIMEOUT_SEC, abs=0.01)
        deadline(10.0, reserve=15.0)  # 서술 단계는 예약을 빼지 않는다 — 처리 마감까지
        assert og.narration_limit_sec() == pytest.approx(10.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_no_deadline_passes_static_max_tokens_only(self, prefixes):
        llm = _RecordingLLM()
        with patch.object(og, "astream_text", wraps=og.astream_text) as spy:
            await og.output_generator(_state([{"a": 1}]), llm=llm, app_config=_config())
        assert spy.call_args.kwargs["max_tokens"] == og.SUMMARY_MAX_TOKENS
        # 모의 LLM은 출력 상한을 받는 provider가 아니다 — astream 인자는 종전과 같다
        assert llm.calls[0]["kwargs"] == {}

    @pytest.mark.asyncio
    async def test_summary_timeout_keeps_table_with_reason(self, prefixes, deadline, monkeypatch):
        monkeypatch.setattr(og, "SUMMARY_TIMEOUT_SEC", 1.0)
        deadline(100.0)
        rows = [{"host": "a", "cpu": 1.0}]
        llm = _RecordingLLM(delay=10.0)
        started = time.monotonic()
        out = await og.output_generator(_state(rows), llm=llm, app_config=_config())
        assert time.monotonic() - started < 5.0
        text = out["final_response"]
        assert text.startswith(og._render_result_table(rows, ranked=False))
        assert "요약은 처리 시간 상한으로 생략했습니다." in text
        assert prefixes  # 표는 이미 나갔다

    @pytest.mark.asyncio
    async def test_processing_deadline_bounds_summary(self, prefixes, deadline):
        deadline(1.5)  # 서술 상한(30초)보다 처리 마감이 먼저다
        llm = _RecordingLLM(delay=10.0)
        started = time.monotonic()
        out = await og.output_generator(_state([{"host": "a"}]), llm=llm, app_config=_config())
        assert time.monotonic() - started < 5.0
        assert "요약은 처리 시간 상한으로 생략했습니다." in out["final_response"]

    @pytest.mark.asyncio
    async def test_expired_deadline_skips_llm(self, prefixes, deadline):
        deadline(-1.0)
        llm = _RecordingLLM()
        out = await og.output_generator(_state([{"host": "a"}]), llm=llm, app_config=_config())
        assert llm.calls == []
        assert out["final_response"].startswith("| host |")
        assert "요약은 처리 시간 상한으로 생략했습니다." in out["final_response"]

    @pytest.mark.asyncio
    async def test_llm_error_keeps_table_with_reason(self, prefixes, caplog):
        llm = _RecordingLLM(error=RuntimeError("gateway 502"))
        out = await og.output_generator(_state([{"host": "a"}]), llm=llm, app_config=_config())
        assert out["final_response"].startswith("| host |")
        assert "요약은 생성하지 못해 생략했습니다." in out["final_response"]
        assert any("요약 LLM 실패" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_blank_summary_keeps_table_with_reason(self, prefixes):
        out = await og.output_generator(
            _state([{"host": "a"}]), llm=_RecordingLLM("   "), app_config=_config(),
        )
        assert "요약은 생성하지 못해 생략했습니다." in out["final_response"]


# ── 파일·폼필 응답 ───────────────────────────────────────────────────────────


def _file_state(fill_stats: dict[str, int]) -> tuple[dict, dict]:
    rows = [{"서버명": "w1", "CPU|M": 10.0}, {"서버명": "w2", "CPU|M": 20.0}]
    state = _state(
        rows, output_format="xlsx",
        template_structure={"file_type": "xlsx", "sheets": []},
        uploaded_file=b"TEMPLATE",
        file_type="xlsx",
    )
    file_result = {"file_bytes": b"FILLED-XLSX", "file_name": "result.xlsx",
                   "total_filled": 2, "fill_stats": fill_stats}
    return state, file_result


class TestFileResponse:
    @pytest.mark.asyncio
    async def test_file_kept_when_summary_times_out(self, prefixes, deadline, monkeypatch):
        state, file_result = _file_state({"서버명": 2, "CPU|M": 2})
        monkeypatch.setattr(og, "_generate_document_file", lambda s, f: file_result)
        deadline(1.5)
        out = await og.output_generator(
            state, llm=_RecordingLLM(delay=10.0), app_config=SimpleNamespace(query=None),
        )
        assert out["output_file"] == b"FILLED-XLSX"
        assert out["output_file_name"] == "result.xlsx"
        text = out["final_response"]
        assert text.startswith("| 서버명 | CPU > M |")
        assert "요약은 처리 시간 상한으로 생략했습니다." in text
        assert out["pending_form_fill"] is None and "form_fill_clarification" not in out

    @pytest.mark.asyncio
    async def test_form_fill_clarification_skips_summary_llm(self, prefixes, monkeypatch):
        state, file_result = _file_state({"서버명": 2, "CPU|M": 2, "비고": 0})
        monkeypatch.setattr(og, "_generate_document_file", lambda s, f: file_result)
        llm = _RecordingLLM()
        out = await og.output_generator(state, llm=llm, app_config=SimpleNamespace(query=None))
        assert llm.calls == []  # 역질문 턴은 요약 LLM 0회
        assert out["output_file"] == b"FILLED-XLSX"
        assert [f["name"] for f in out["form_fill_clarification"]["fields"]] == ["비고"]
        assert out["pending_form_fill"]["unresolved"] == ["비고"]
        text = out["final_response"]
        assert text.startswith("| 서버명 | CPU > M |")
        assert "채우지 못한 항목 1건의 처리 방법을 먼저 여쭙니다." in text
        assert "**[미작성 항목]**" in text  # 폼필 덧붙임은 그대로
        assert prefixes and prefixes[0].startswith("| 서버명 |")

    @pytest.mark.asyncio
    async def test_answer_turn_summarizes_once(self, prefixes, monkeypatch):
        state, file_result = _file_state({"서버명": 2, "CPU|M": 2})
        monkeypatch.setattr(og, "_generate_document_file", lambda s, f: file_result)
        llm = _RecordingLLM()
        out = await og.output_generator(state, llm=llm, app_config=SimpleNamespace(query=None))
        assert len(llm.calls) == 1
        assert _SUMMARY in out["final_response"]


# ── 2단 집계기 경로 (result_aggregator) ──────────────────────────────────────


def _data_task(tid: str, order: int, rows: list[dict]) -> tuple[dict, dict]:
    task = {"task_id": tid, "agent": "data_query", "sub_query": f"q{order}", "order": order,
            "status": "completed"}
    res = {"organized_data": {"summary": f"{len(rows)}건", "rows": rows, "is_sufficient": True},
           "query_results": rows}
    return task, res


def _agg_state(*task_rows: list[dict]) -> dict:
    from src.state import create_initial_state

    state = create_initial_state(user_query="복합 질의")
    state["parsed_requirements"] = {"original_query": "복합 질의", "output_format": "text"}
    plan, results = [], {}
    for i, rows in enumerate(task_rows, 1):
        task, res = _data_task(f"t{i}", i, rows)
        plan.append(task)
        results[task["task_id"]] = res
    state["task_plan"] = plan
    state["task_results"] = results
    return state


class TestAggregatorPath:
    @pytest.mark.asyncio
    async def test_single_task_streams_table_first(self, prefixes, mock_config):
        from src.orchestration.result_aggregator import result_aggregator

        rows = [{"host": "a", "cpu": 1.0}]
        out = await result_aggregator(
            _agg_state(rows), llm=_RecordingLLM(), app_config=mock_config, synthesize=True,
        )
        table = og._render_result_table(rows, ranked=False)
        assert prefixes == [table + "\n\n"]
        assert out["final_response"].startswith(table + "\n\n" + _SUMMARY)

    @pytest.mark.asyncio
    async def test_synthesis_mode_suppresses_per_task_prefix(self, prefixes, mock_config):
        agg = importlib.import_module("src.orchestration.result_aggregator")

        async def _synth(*a, **k):
            return "합성 답변"

        state = _agg_state([{"host": "a", "cpu": 1.0}], [{"alarm": "x"}])
        with patch.object(agg, "astream_text", _synth):
            out = await agg.result_aggregator(
                state, llm=_RecordingLLM(), app_config=mock_config, synthesize=True,
            )
        assert prefixes == []  # 중간 per-task 산출은 선행 발행하지 않는다(D-062)
        assert out["final_response"] == "합성 답변"

    @pytest.mark.asyncio
    async def test_synthesis_timeout_falls_back_to_merge_with_notice(
        self, prefixes, mock_config, deadline,
    ):
        agg = importlib.import_module("src.orchestration.result_aggregator")

        async def _slow_synth(*a, **k):
            await asyncio.sleep(10)
            return "늦은 합성"

        state = _agg_state([{"host": "a", "cpu": 1.0}], [{"alarm": "x"}])
        deadline(1.5)
        started = time.monotonic()
        with patch.object(agg, "astream_text", _slow_synth):
            out = await agg.result_aggregator(
                state, llm=_RecordingLLM(), app_config=mock_config, synthesize=True,
            )
        assert time.monotonic() - started < 5.0
        text = out["final_response"]
        assert "늦은 합성" not in text
        assert text.startswith("| host | cpu |")  # 조회별 결과(표 + 요약)를 이어 붙인다
        assert "| alarm |" in text
        assert agg._SYNTHESIS_SKIPPED_NOTE in text

    @pytest.mark.asyncio
    async def test_expired_deadline_skips_synthesis(self, prefixes, mock_config, deadline):
        agg = importlib.import_module("src.orchestration.result_aggregator")

        called: list[int] = []

        async def _synth(*a, **k):
            called.append(1)
            return "합성"

        state = _agg_state([{"host": "a"}], [{"alarm": "x"}])
        deadline(-1.0)
        with patch.object(agg, "astream_text", _synth):
            out = await agg.result_aggregator(
                state, llm=_RecordingLLM(), app_config=mock_config, synthesize=True,
            )
        assert called == []
        assert agg._SYNTHESIS_SKIPPED_NOTE in out["final_response"]
