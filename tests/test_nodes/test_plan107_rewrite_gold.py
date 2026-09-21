"""plans/107 W0 — 재작성 골든셋 채점(`testdata/routing_gold/rewrite.yaml`).

파서·라우터 출력 스냅샷만으로 병합·게이트·렌더·검증을 결정적으로 채점한다. 서버·LLM 0(D-127).
E2E 회귀는 plans/94 §19가 소유한다 — 여기는 슬롯 단위만 본다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import AppConfig
from src.domain.intent_frame import rewrite_needed, verify_rewrite
from src.nodes.intent_frame_builder import (
    CONSUMER_QUERY_GENERATOR,
    build_intent_frame,
    foreign_location_terms,
    get_prompt_query,
    raw_query_of,
)
from src.prompts.canonical_query import render_canonical_block

GOLD = Path(__file__).resolve().parents[2] / "testdata" / "routing_gold" / "rewrite.yaml"
CATEGORIES = {
    "contamination", "demonstrative", "location_switch", "clarification_resume",
    "composite", "multi_db", "pass_through",
}


def _items() -> list[dict]:
    return yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]


def _augment_enforce_config() -> AppConfig:
    config = AppConfig()
    config.intent_frame.enabled = True
    config.intent_frame.canonical_query_mode = "augment"
    config.intent_frame.canonical_query_consumers = CONSUMER_QUERY_GENERATOR
    config.intent_frame.rewrite_gate_mode = "enforce"
    return config


def test_gold_covers_every_category():
    """W0 범주 ①~⑦이 전부 한 건 이상 있다(범주가 빠지면 회귀가 조용히 사라진다)."""
    assert {item["category"] for item in _items()} == CATEGORIES


def test_gold_ids_are_unique():
    ids = [item["id"] for item in _items()]
    assert len(ids) == len(set(ids))


def test_contamination_cases_are_all_four():
    """§2.4 오염 실측 4건이 전부 회귀 케이스로 고정돼 있다."""
    cases = [item["case"][:10] for item in _items() if item["category"] == "contamination"]
    assert sorted(cases) == ["2026-07-16", "2026-07-24", "2026-08-04", "2026-08-05"]


@pytest.mark.parametrize("item", _items(), ids=lambda item: item["id"])
def test_gold_item(item: dict):
    state = item["state"]
    expect = item["expect"]
    frame = build_intent_frame(state)
    slots = frame.slot_dict()

    if "gate" in expect:
        assert rewrite_needed(frame)[1] == expect["gate"]

    for name, want in (expect.get("slots") or {}).items():
        assert name in slots, f"{name} 슬롯이 없다: {sorted(slots)}"
        assert slots[name]["source"] == want["source"]
        if "value" in want:
            assert slots[name]["value"] == want["value"]

    for name in expect.get("absent_slots") or []:
        assert name not in slots, f"{name} 슬롯이 있으면 안 된다: {slots[name]}"

    for term in expect.get("original_contains") or []:
        assert term in frame.original_query

    block = render_canonical_block(slots, intent=frame.intent, raw_query=raw_query_of(state))
    for term in expect.get("render_contains") or []:
        assert term in block, block
    for term in expect.get("render_not_contains") or []:
        assert term not in block, block

    if "verify" in expect:
        db_slot = frame.targets.get("db_ids")
        foreign = foreign_location_terms(list(db_slot.value) if db_slot else [])
        context = state.get("conversation_context") or {}
        prior = [e["value"] for e in context.get("previous_entities") or []]
        result = verify_rewrite(
            frame, expect["verify"]["rewritten"],
            foreign_location_terms=foreign, prior_entities=prior,
        )
        assert result == expect["verify"]["result"]

    if expect.get("augment_skipped"):
        current = "현재 소비자 텍스트"
        assert get_prompt_query(
            state, _augment_enforce_config(), consumer=CONSUMER_QUERY_GENERATOR, current=current,
        ) == current


def test_frame_hash_is_deterministic_across_items():
    """같은 스냅샷이면 같은 해시 — 턴별 감사 대조의 전제."""
    for item in _items():
        first = build_intent_frame(item["state"]).frame_hash()
        assert first == build_intent_frame(item["state"]).frame_hash()
