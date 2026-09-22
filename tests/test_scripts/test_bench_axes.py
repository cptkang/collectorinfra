"""`scripts/bench/axes.py` 테스트 (plans/93 · T-08)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes, catalog  # noqa: E402


def _knob(env_key="TEXT2SQL_CANDIDATE_COUNT", **over) -> catalog.KnobSpec:
    base = dict(
        env_key=env_key, group_key="text2sql", field_name=env_key.lower().replace("text2sql_", ""),
        type="int", enum_choices=None, default="3", consumed=True, is_secret=False,
        is_sensitive=False, apply_mode="restart", description="d",
    )
    base.update(over)
    return catalog.KnobSpec(**base)


# ── 영향 경로 ──────────────────────────────────────────────

@pytest.mark.parametrize("key,field,expected", [
    ("TEXT2SQL_CANDIDATE_COUNT", "candidate_count", "llm_calls"),
    ("SCHEMA_CACHE_ENABLED", "enabled", "db_roundtrips"),
    ("TEXT2SQL_PROMPT_TOKEN_BUDGET", "prompt_token_budget", "prompt_size"),
    ("QUERY_MAX_RETRY_COUNT", "max_retry_count", "retry_budget"),
])
def test_impact_paths_detected(key, field, expected):
    assert expected in axes._impact_paths(_knob(key, field_name=field))


def test_no_impact_path_means_not_an_axis():
    knob = _knob("SOME_LABEL", field_name="some_label", type="bool", default="false")
    assert axes._impact_paths(knob) == ()
    selected, decisions = axes.select_axes([knob])
    assert selected == []
    assert decisions[0].stage == "F2" and "영향 경로 없음" in decisions[0].reason


# ── 상한 판정 (1차/2차 분리) ──────────────────────────────

@pytest.mark.parametrize("key,field", [
    ("API_QUERY_TIMEOUT", "query_timeout"),
    ("COMPOSITE_MAX_TARGETS", "max_targets"),
    ("ORCHESTRATOR_RECURSION_LIMIT", "recursion_limit"),
    ("TEXT2SQL_PROMPT_TOKEN_BUDGET", "prompt_token_budget"),
])
def test_cap_settings_are_secondary(key, field):
    """★ 타임아웃을 줄이면 빨라지는 게 아니라 실패가 는다 — 1차 축이 아니다."""
    selected, _ = axes.select_axes([_knob(key, field_name=field)])
    assert selected and selected[0].tier == "secondary"


def test_behavior_flag_is_primary():
    knob = _knob("SCHEMA_CACHE_ENABLED", group_key="schema_cache", field_name="enabled",
                 type="bool", default="true")
    selected, _ = axes.select_axes([knob])
    assert selected[0].tier == "primary"


def test_primary_axes_sort_first():
    knobs = [
        _knob("API_QUERY_TIMEOUT", field_name="query_timeout"),
        _knob("SCHEMA_CACHE_ENABLED", group_key="schema_cache", field_name="enabled",
              type="bool", default="true"),
    ]
    selected, _ = axes.select_axes(knobs)
    assert selected[0].tier == "primary"


# ── 레벨 생성 ──────────────────────────────────────────────

def test_levels_bool():
    assert axes._levels_of(_knob(type="bool", default="false")) == ("true", "false")


def test_levels_enum_uses_all_choices():
    knob = _knob(type="str", enum_choices=["consistency", "llm", "hybrid"])
    assert axes._levels_of(knob) == ("consistency", "llm", "hybrid")


def test_levels_int_brackets_the_default():
    """기본값을 가운데 두고 절반·두 배를 본다."""
    assert axes._levels_of(_knob(type="int", default="3")) == ("1", "3", "6")


def test_levels_int_without_default():
    assert axes._levels_of(_knob(type="int", default=None)) == ("1", "3")


def test_levels_unsupported_type_is_empty():
    assert axes._levels_of(_knob(type="list", default=None)) == ()


def test_axis_rejected_when_levels_insufficient():
    knob = _knob("TEXT2SQL_CANDIDATE_LIST", field_name="candidate_list", type="list", default=None)
    selected, decisions = axes.select_axes([knob])
    assert selected == []
    assert any("비교할 값이 부족" in d.reason for d in decisions)


# ── OFAT 전개 ──────────────────────────────────────────────

def test_expand_ofat_has_baseline_and_one_arm_per_level():
    """전수(full factorial)를 쓰지 않는다 — 축 하나씩만 움직인다."""
    axis = axes.AxisCandidate("K", "g", "bool", ("true", "false"), ("llm_calls",), "r")
    arms = axes.expand_ofat([axis])
    assert arms[0]["arm_id"] == "baseline" and arms[0]["env"] == {}
    assert len(arms) == 3
    assert {a["env"].get("K") for a in arms[1:]} == {"true", "false"}


def test_expand_ofat_is_linear_not_combinatorial():
    made = [axes.AxisCandidate(f"K{i}", "g", "bool", ("true", "false"), ("llm_calls",), "r")
            for i in range(5)]
    arms = axes.expand_ofat(made)
    assert len(arms) == 1 + 5 * 2  # 조합이면 2**5


def test_expand_ofat_arm_changes_exactly_one_key():
    made = [axes.AxisCandidate(f"K{i}", "g", "bool", ("true",), ("llm_calls",), "r")
            for i in range(3)]
    for arm in axes.expand_ofat(made)[1:]:
        assert len(arm["env"]) == 1


# ── 설명·직렬화 ────────────────────────────────────────────

def test_explain_covers_every_decision():
    knobs = [_knob(), _knob("NOISE_X", group_key="noise_gate", field_name="x", type="bool")]
    _, decisions = axes.select_axes(knobs)
    text = axes.render_explain(decisions)
    for knob in knobs:
        assert knob.env_key in text


def test_explain_every_row_has_reason():
    _, decisions = axes.select_axes(catalog.load_knobs()[:40])
    assert all(d.reason.strip() for d in decisions)


def test_to_yaml_marks_generated_and_includes_tier():
    axis = axes.AxisCandidate("K", "g", "bool", ("true", "false"), ("llm_calls",), "r", "primary")
    text = axes.to_yaml([axis])
    assert "자동 생성물" in text and "tier: primary" in text


# ── 실제 카탈로그 ─────────────────────────────────────────

def test_real_selection_produces_axes_and_full_decisions():
    selected, decisions = axes.select_axes()
    assert selected, "축이 하나도 안 뽑혔다"
    # **노브 전건을 덮는지**를 본다(건수 비교가 아니라 집합 비교) — 구조 축(F0 · plans/114 M-0)은
    # 노브가 아니라 3키 묶음이라 판정 행이 하나 더 있다.
    knob_keys = {k.env_key for k in catalog.load_knobs()}
    assert knob_keys <= {d.env_key for d in decisions}, "판정이 전건을 덮지 않았다"
    assert all(a.levels and len(a.levels) >= 2 for a in selected)


def test_real_selection_includes_known_performance_flags():
    """계획서 §3.2가 손으로 고른 축과 수렴하는지 — 자동 선별의 회귀 감시."""
    selected, _ = axes.select_axes()
    keys = {a.env_key for a in selected if a.tier == "primary"}
    for expected in ("TEXT2SQL_CANDIDATE_COUNT", "SCHEMA_CACHE_ENABLED", "ROUTER_TWO_STAGE_ENABLED"):
        assert expected in keys, f"{expected}이 1차 축에서 빠졌다"
