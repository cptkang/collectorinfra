"""plans/107 W1·W2·W3·W5 — 의도 프레임·렌더·재작성 감사·정규 질의 채널 단위 테스트.

골든셋(`test_plan107_rewrite_gold.py`)이 사례 단위로 보는 것을 규칙 단위로 쪼개 고정한다.
핵심 불변식: **꺼져 있으면 현행과 비트 동일**(상태·프롬프트·done 페이로드). DB·LLM 0(D-127).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.config import AppConfig, IntentFrameConfig
from src.domain import intent_frame as dom
from src.domain.intent_frame import (
    SLOT_SOURCES,
    SOURCE_UTTERANCE,
    SlotValue,
    merge_frame,
    rewrite_needed,
    verify_rewrite,
)
from src.nodes import intent_frame_builder as ifb
from src.prompts.canonical_query import (
    SOURCE_NOTES,
    render_canonical_block,
    render_canonical_query,
)
from src.state import create_followup_input, create_initial_state


def _on(**overrides) -> AppConfig:
    config = AppConfig()
    config.intent_frame.enabled = True
    for key, value in overrides.items():
        setattr(config.intent_frame, key, value)
    return config


@pytest.fixture(autouse=True)
def _clean_trace_store():
    ifb._TRACES.clear()
    yield
    ifb._TRACES.clear()


# ──────────────────────────────────────────────
# 도메인 — 병합 우선순위(§4.3)
# ──────────────────────────────────────────────

class TestMergePriority:
    def test_answer_beats_utterance(self):
        frame = merge_frame(
            original_query="q",
            utterance={"targets.db_ids": ["a"]}, answers={"targets.db_ids": ["b"]},
        )
        assert frame.targets["db_ids"] == SlotValue(["b"], dom.SOURCE_ANSWER)

    def test_utterance_replaces_inherited_location(self):
        """발화 명시 위치는 승계 위치를 대체한다 — 병합하지 않는다(2026-07-16)."""
        frame = merge_frame(
            original_query="q",
            utterance={"targets.db_ids": ["yd"]}, inherited={"targets.db_ids": ["gp"]},
        )
        assert frame.targets["db_ids"].value == ["yd"]
        assert frame.targets["db_ids"].source == SOURCE_UTTERANCE

    def test_inherited_location_fills_empty_slot(self):
        frame = merge_frame(original_query="q", inherited={"targets.db_ids": ["gp"]})
        assert frame.targets["db_ids"].source == dom.SOURCE_INHERITED

    def test_inherited_entity_needs_demonstrative(self):
        """엔티티 승계는 지시 참조일 때만(규칙 3′) — 상한 샘플을 스코프로 쓰지 않는다."""
        without = merge_frame(original_query="q", inherited={"targets.hosts": ["h1"]})
        with_ = merge_frame(
            original_query="q", inherited={"targets.hosts": ["h1"]}, demonstrative=True,
        )
        assert "hosts" not in without.targets
        assert with_.targets["hosts"].evidence == "demonstrative"

    def test_default_is_marked_spec_uncertain(self):
        frame = merge_frame(original_query="q", defaults={"time_range": "1h"})
        assert frame.time_range == SlotValue("1h", dom.SOURCE_DEFAULT, spec_uncertain=True)

    def test_registry_is_lowest(self):
        frame = merge_frame(
            original_query="q", defaults={"targets.zone": "d"}, registry={"targets.zone": "r"},
        )
        assert frame.targets["zone"].source == dom.SOURCE_DEFAULT

    def test_empty_values_are_not_present(self):
        frame = merge_frame(original_query="q", utterance={"limit": None, "metrics": []})
        assert frame.all_slots() == []

    def test_unknown_source_is_rejected(self):
        with pytest.raises(ValueError):
            SlotValue(1, "guess")


class TestGateAndVerify:
    def test_unresolved_wins(self):
        frame = merge_frame(original_query="q", unresolved=[{"slot": "time_range", "reason": "x"}])
        assert rewrite_needed(frame) == (True, dom.GATE_UNRESOLVED)

    def test_empty_frame_passes_through(self):
        assert rewrite_needed(merge_frame(original_query="q")) == (False, dom.GATE_PASS_THROUGH)

    def test_slot_missing(self):
        frame = merge_frame(original_query="q", utterance={"targets.hosts": ["web01"]})
        assert verify_rewrite(frame, "서버 CPU") == "fail:slot_missing"

    def test_no_rewrite_is_pass(self):
        assert verify_rewrite(merge_frame(original_query="q"), None) == dom.VERIFY_PASS

    def test_frame_hash_ignores_original_text(self):
        """해시는 해석(슬롯)만 본다 — 원문 전문은 감사 레코드에 싣지 않는다(D-183)."""
        a = merge_frame(original_query="원문 A", utterance={"limit": 10})
        b = merge_frame(original_query="원문 B", utterance={"limit": 10})
        assert a.frame_hash() == b.frame_hash()


# ──────────────────────────────────────────────
# 렌더(prompts) — 도메인과의 키 계약
# ──────────────────────────────────────────────

class TestRenderer:
    def test_source_note_keys_match_domain_sources(self):
        """prompts는 도메인을 import할 수 없다(arch_check) — 출처 문자열 일치를 여기서 고정한다."""
        assert set(SOURCE_NOTES) == set(SLOT_SOURCES) - {SOURCE_UTTERANCE}

    def test_block_keeps_raw_query_below(self):
        slots = {"metrics": {"value": ["CPU"], "source": "utterance"}}
        block = render_canonical_block(slots, raw_query="원문")
        assert block.endswith("[사용자 원문]\n원문")

    def test_no_slots_returns_raw_only(self):
        assert render_canonical_block({}, raw_query="원문") == "[사용자 원문]\n원문"

    def test_target_rule_only_with_location_slot(self):
        limit_only = {"limit": {"value": 5, "source": "utterance"}}
        with_db = {"targets.db_ids": {"value": ["a"], "source": "utterance"}}
        no_loc = render_canonical_block(limit_only, raw_query="q")
        loc = render_canonical_block(with_db, raw_query="q")
        assert "WHERE" not in no_loc and "WHERE" in loc

    def test_canonical_query_drops_location_when_target_fixed(self):
        slots = {
            "targets.db_ids": {"value": ["a"], "source": "utterance"},
            "metrics": {"value": ["CPU"], "source": "utterance"},
        }
        assert "대상 DB" in render_canonical_query(slots, original_query="q")
        assert "대상 DB" not in render_canonical_query(slots, original_query="q", target_db="a")

    def test_canonical_query_falls_back_to_original(self):
        assert render_canonical_query({}, original_query="원문") == "원문"


# ──────────────────────────────────────────────
# 설정 — 기본 off · 받지 않는 값은 경고 후 off
# ──────────────────────────────────────────────

class TestConfig:
    def test_defaults_are_all_off(self):
        ifc = IntentFrameConfig()
        assert (ifc.enabled, ifc.query_mode(), ifc.gate_mode(), ifc.verify_mode()) == (
            False, "off", "off", "off",
        )
        assert ifc.consumers() == frozenset()

    def test_replace_and_verify_enforce_are_not_accepted(self, caplog):
        ifc = IntentFrameConfig(canonical_query_mode="replace", rewrite_verify_mode="enforce")
        with caplog.at_level(logging.WARNING):
            assert ifc.query_mode() == "off"
            assert ifc.verify_mode() == "off"
        assert "지원하지 않는 값" in caplog.text

    def test_consumers_are_csv(self):
        ifc = IntentFrameConfig(canonical_query_consumers=" output_generator, query_generator ,")
        assert ifc.consumers() == {"output_generator", "query_generator"}


# ──────────────────────────────────────────────
# 빌더·감사(W1·W2·W5)
# ──────────────────────────────────────────────

_STATE = {
    "user_query": "서버 CPU",                 # 1·2단 재작성문
    "original_user_query": "김포 서버 CPU 상위 5",
    "thread_id": "t-1",
    "active_db_id": "polestar_cm_gp",
    "target_databases": [{"db_id": "polestar_cm_gp"}],
    "db_scope_source": "hint",
    "parsed_requirements": {"query_targets": ["CPU"], "limit": 5, "original_query": "서버 CPU"},
}


class TestRewriteTrace:
    def test_schema_matches_plan_49(self):
        trace = ifb.build_rewrite_trace(_STATE, _on(), consumer="query_generator")
        for key in ("frame_hash", "frame_version", "slots", "renderer", "mode", "consumers",
                    "gate", "verify"):
            assert key in trace
        assert set(trace["gate"]) >= {"needed", "reason"}

    def test_trace_carries_no_raw_text(self):
        """원문 전문은 싣지 않는다 — 원문은 user_request 감사에만 있다(D-183)."""
        trace = ifb.build_rewrite_trace(_STATE, _on(), consumer="query_generator")
        assert "김포 서버 CPU 상위 5" not in repr(trace)

    def test_verify_off_leaves_verify_empty(self):
        assert ifb.build_rewrite_trace(_STATE, _on(), consumer="q")["verify"] == {}

    def test_verify_shadow_checks_only_rewritten_channels(self):
        trace = ifb.build_rewrite_trace(_STATE, _on(rewrite_verify_mode="shadow"), consumer="q")
        assert set(trace["verify"]) == {"user_query", "original_query"}

    @pytest.mark.asyncio
    async def test_observe_off_returns_empty_and_records_nothing(self):
        assert await ifb.observe_rewrite(_STATE, AppConfig(), consumer="q") == {}
        assert ifb.pop_rewrite_traces("t-1") == []

    @pytest.mark.asyncio
    async def test_observe_ignores_mock_config(self):
        """설정 대역(MagicMock)의 truthy 속성으로 켜지지 않는다 — 실제 True일 때만."""
        from unittest.mock import MagicMock

        assert await ifb.observe_rewrite(_STATE, MagicMock(), consumer="q") == {}

    @pytest.mark.asyncio
    async def test_observe_on_records_store_and_audit(self):
        fake = AsyncMock()
        with patch("src.security.audit_logger.log_rewrite_trace", fake):
            delta = await ifb.observe_rewrite(_STATE, _on(), consumer="query_generator")
        assert set(delta) == {"intent_frame", "rewrite_trace"}
        fake.assert_awaited_once()
        assert fake.await_args.kwargs["thread_id"] == "t-1"
        traces = ifb.pop_rewrite_traces("t-1")
        assert len(traces) == 1 and traces[0]["consumer"] == "query_generator"
        assert ifb.pop_rewrite_traces("t-1") == []          # 1회 소비

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_block(self):
        with patch("src.security.audit_logger.log_rewrite_trace", AsyncMock(side_effect=OSError)):
            delta = await ifb.observe_rewrite(_STATE, _on(), consumer="q")
        assert "rewrite_trace" in delta


class TestTraceStoreBounds:
    """in-memory 보관소는 키 수·키당 레코드 수·키 수명 세 방향으로 bound된다."""

    def test_per_thread_cap(self):
        for i in range(ifb._MAX_TRACES_PER_THREAD + 5):
            ifb.record_rewrite_trace("t", {"i": i})
        traces = ifb.pop_rewrite_traces("t")
        cap = ifb._MAX_TRACES_PER_THREAD
        assert len(traces) == cap and traces[-1]["i"] == cap + 4

    def test_thread_cap(self):
        for i in range(ifb._MAX_THREADS + 10):
            ifb.record_rewrite_trace(f"t{i}", {})
        assert len(ifb._TRACES) == ifb._MAX_THREADS
        assert "t0" not in ifb._TRACES                    # 오래된 키부터 밀린다

    def test_ttl_sweep(self, monkeypatch):
        ifb.record_rewrite_trace("old", {})
        now = ifb.time.monotonic()
        monkeypatch.setattr(ifb.time, "monotonic", lambda: now + ifb._TRACE_TTL_SEC + 1)
        ifb.record_rewrite_trace("new", {})
        assert "old" not in ifb._TRACES

    def test_no_thread_id_is_not_stored(self):
        ifb.record_rewrite_trace(None, {})
        assert not ifb._TRACES


# ──────────────────────────────────────────────
# 정규 질의 채널(W3) — 기본 off면 받은 텍스트 그대로
# ──────────────────────────────────────────────

class TestPromptQuery:
    def test_off_returns_current(self):
        text = ifb.get_prompt_query(_STATE, AppConfig(), consumer="query_generator", current="C")
        assert text == "C"

    def test_consumer_not_listed_returns_current(self):
        config = _on(canonical_query_mode="augment", canonical_query_consumers="output_generator")
        assert ifb.get_prompt_query(_STATE, config, consumer="query_generator", current="C") == "C"

    def test_shadow_mode_returns_current(self):
        config = _on(canonical_query_mode="shadow", canonical_query_consumers="query_generator")
        assert ifb.get_prompt_query(_STATE, config, consumer="query_generator", current="C") == "C"

    def test_augment_renders_block_with_original(self):
        config = _on(canonical_query_mode="augment", canonical_query_consumers="query_generator")
        text = ifb.get_prompt_query(_STATE, config, consumer="query_generator", current="C")
        assert text.startswith("[확정된 해석]")
        assert text.endswith("[사용자 원문]\n김포 서버 CPU 상위 5")

    def test_composite_is_never_augmented(self):
        config = _on(canonical_query_mode="augment", canonical_query_consumers="query_generator")
        state = {**_STATE, "is_composite": True}
        assert ifb.get_prompt_query(state, config, consumer="query_generator", current="C") == "C"

    def test_gate_enforce_skips_pass_through(self):
        config = _on(canonical_query_mode="augment", canonical_query_consumers="query_generator",
                     rewrite_gate_mode="enforce")
        assert ifb.get_prompt_query(_STATE, config, consumer="query_generator", current="C") == "C"


class TestOffIsByteIdentical:
    """꺼져 있으면 SQL 생성 사용자 프롬프트가 종전과 바이트 동일하다."""

    def test_user_prompt_prompt_query_none_equals_legacy(self):
        from src.nodes.query_generator import _build_user_prompt

        parsed = {"original_query": "김포 서버 CPU", "query_targets": ["CPU"]}
        legacy = _build_user_prompt(parsed, None, None, None)
        via_channel = _build_user_prompt(
            parsed, None, None, None,
            prompt_query=ifb.get_prompt_query({}, AppConfig(), consumer="query_generator",
                                              current=parsed["original_query"]),
        )
        assert legacy == via_channel


# ──────────────────────────────────────────────
# 상태·라우트 — 요청 스코프 초기화 · 꺼지면 원문 미기록 · done 키 미첨부
# ──────────────────────────────────────────────

class TestStateAndRoute:
    def test_followup_resets_request_scoped_fields(self):
        delta = create_followup_input("q")
        assert {k: delta[k] for k in ("raw_user_query", "display_query", "intent_frame",
                                      "rewrite_trace")} == dict.fromkeys(
            ("raw_user_query", "display_query", "intent_frame", "rewrite_trace"))

    def test_raw_query_given_records_display(self):
        state = create_initial_state("치환본", raw_user_query="원문")
        assert (state["raw_user_query"], state["display_query"], state["user_query"]) == (
            "원문", "치환본", "치환본",
        )

    def test_route_seed_off_is_none(self):
        from src.api.routes.query import _raw_query_seed

        assert _raw_query_seed("원문", AppConfig()) is None
        assert _raw_query_seed("원문", None) is None
        assert _raw_query_seed("원문", _on()) == "원문"

    def test_done_fields_omit_key_when_empty(self):
        """레코드가 없으면 done 페이로드에 키 자체를 싣지 않는다(바이트 불변)."""
        from src.api.routes.query import _rewrite_trace_fields

        assert _rewrite_trace_fields("t-none") == {}
        ifb.record_rewrite_trace("t-2", {"x": 1})
        assert _rewrite_trace_fields("t-2") == {"rewrite_trace": [{"x": 1}]}


@pytest.mark.asyncio
async def test_audit_event_shape(monkeypatch):
    from src.security import audit_logger

    written: list = []

    async def _capture(entry):
        written.append(entry.to_dict())

    monkeypatch.setattr(audit_logger, "_write_audit_file", _capture)
    await audit_logger.log_rewrite_trace({"gate": {"reason": "pass_through"}}, thread_id="t-9")
    assert written[0]["event"] == "rewrite_trace"
    assert written[0]["thread_id"] == "t-9"
    assert written[0]["rewrite_trace"]["gate"]["reason"] == "pass_through"
