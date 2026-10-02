"""`scripts/bench/catalog.py` 테스트 (plans/93 · T-01·T-02).

원칙: **저장소의 `.env`를 읽지 않는다.** 전 케이스가 `tmp_path`와 명시 주입으로 돈다
(환경 의존 테스트는 다른 개발자 기계에서 조용히 다른 결과를 낸다).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import catalog  # noqa: E402


def _knob(env_key: str, **over) -> catalog.KnobSpec:
    """테스트용 노브. 기본은 '축 후보로 살아남는' 형태다."""
    base = dict(
        env_key=env_key, group_key="text2sql", field_name="flag", type="bool",
        enum_choices=None, default="false", consumed=True, is_secret=False,
        is_sensitive=False, apply_mode="restart", description="설명",
    )
    base.update(over)
    return catalog.KnobSpec(**base)


# ── F1 필터 ────────────────────────────────────────────────

def test_f1_keeps_plain_behavior_flag():
    assert catalog.f1_exclusion_reason(_knob("TEXT2SQL_SEMANTIC_COMPOSE")) is None


@pytest.mark.parametrize("over,expected_head", [
    ({"consumed": False}, "미소비"),
    ({"is_secret": True}, "크레덴셜"),
    ({"is_sensitive": True}, "크레덴셜"),
    ({"type": "secret"}, "크레덴셜"),
    ({"group_key": "noise_gate"}, "질의 경로 밖"),
    ({"group_key": "alarm"}, "질의 경로 밖"),
    ({"group_key": "workb"}, "질의 경로 밖"),
])
def test_f1_excludes_with_reason(over, expected_head):
    reason = catalog.f1_exclusion_reason(_knob("SOME_KEY", **over))
    assert reason is not None and reason.startswith(expected_head)


@pytest.mark.parametrize("key", [
    "DBHUB_BASE_URL", "REDIS_HOST", "API_PORT", "SCHEMA_CACHE_CACHE_DIR",
    "OBS_TRACE_RETENTION_DAYS", "ALARM_DEAD_LETTER_MAXLEN",
])
def test_f1_excludes_infra_suffixes(key):
    """접속·저장 정책은 동작 모드가 아니다."""
    assert catalog.f1_exclusion_reason(_knob(key)) is not None


def test_f1_reason_is_single_not_concatenated():
    """여러 규칙에 걸려도 사유는 하나다 — 장부 가독성 때문에 먼저 맞는 것만 남긴다."""
    reason = catalog.f1_exclusion_reason(
        _knob("NOISE_SOME_URL", consumed=False, group_key="noise_gate")
    )
    assert reason.startswith("미소비")          # 규칙 순서상 미소비가 먼저
    assert "질의 경로 밖" not in reason


def test_f1_filter_partitions_without_loss():
    knobs = [_knob("A_FLAG"), _knob("B_FLAG", consumed=False), _knob("C_FLAG", group_key="alarm")]
    kept, dropped = catalog.f1_filter(knobs)
    assert len(kept) + len(dropped) == len(knobs)
    assert {k.env_key for k in kept} == {"A_FLAG"}
    assert all(d.reason for d in dropped), "제외에는 반드시 사유가 붙는다"


# ── 주석 키 파싱 ───────────────────────────────────────────

def test_parse_commented_keys(tmp_path):
    f = tmp_path / ".env.example"
    f.write_text(
        "ACTIVE=1\n"
        "# COMMENTED_KEY=value\n"
        "#   SPACED_KEY=value\n"
        "# 산문 주석입니다 — SOME_KEY가 있으면 자동 활성화\n"
        "# lower_case=x\n",
        encoding="utf-8",
    )
    keys = catalog.parse_commented_keys(f)
    assert keys == {"COMMENTED_KEY", "SPACED_KEY"}


def test_parse_commented_keys_missing_file(tmp_path):
    assert catalog.parse_commented_keys(tmp_path / "nope") == set()


# ── L1 정합 ────────────────────────────────────────────────

def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


def test_integrity_detects_orphan(tmp_path):
    """파일에만 있고 카탈로그에 없는 키 = 고아."""
    env = _write(tmp_path / ".env", "KNOWN_FLAG=true\nGHOST_KEY=1\n")
    ex = _write(tmp_path / ".env.example", "KNOWN_FLAG=false\n")
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[_knob("KNOWN_FLAG")])
    orphans = [f for f in found if f.kind == "orphan"]
    assert [f.env_key for f in orphans] == ["GHOST_KEY"]
    assert orphans[0].severity == "high"


def test_integrity_detects_missing_example(tmp_path):
    env = _write(tmp_path / ".env", "KNOWN_FLAG=true\n")
    ex = _write(tmp_path / ".env.example", "")
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[_knob("KNOWN_FLAG")])
    assert [f.env_key for f in found if f.kind == "missing_example"] == ["KNOWN_FLAG"]


def test_integrity_commented_key_is_not_missing(tmp_path):
    """주석으로 안내된 키는 '선택 키'이지 누락이 아니다 (2026-09-11 실측 교정)."""
    env = _write(tmp_path / ".env", "")
    ex = _write(tmp_path / ".env.example", "# KNOWN_FLAG=true\n")
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[_knob("KNOWN_FLAG")])
    assert not [f for f in found if f.kind == "missing_example"]


def test_integrity_missing_key_is_not_double_reported_as_undocumented(tmp_path):
    """누락 키는 설명이 없는 게 당연하다 — 상위 사유(누락)만 남긴다."""
    env = _write(tmp_path / ".env", "")
    ex = _write(tmp_path / ".env.example", "")
    knob = _knob("KNOWN_FLAG", description=None)
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[knob])
    kinds = {f.kind for f in found}
    assert "missing_example" in kinds
    assert "undocumented" not in kinds


def test_integrity_reports_undocumented_when_present_in_example(tmp_path):
    env = _write(tmp_path / ".env", "")
    ex = _write(tmp_path / ".env.example", "KNOWN_FLAG=false\n")
    knob = _knob("KNOWN_FLAG", description="   ")
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[knob])
    assert [f.env_key for f in found if f.kind == "undocumented"] == ["KNOWN_FLAG"]


def test_integrity_clean_case_returns_empty(tmp_path):
    env = _write(tmp_path / ".env", "KNOWN_FLAG=true\n")
    ex = _write(tmp_path / ".env.example", "KNOWN_FLAG=false\n")
    found = catalog.check_integrity(env_path=env, example_path=ex, knobs=[_knob("KNOWN_FLAG")])
    assert found == []


# ── 카탈로그 어댑터 ────────────────────────────────────────

def test_load_knobs_structure():
    """필드 수는 단언하지 않는다(설정 추가마다 낡는다). 구조만 본다."""
    knobs = catalog.load_knobs()
    assert knobs, "카탈로그가 비었다"
    assert all(isinstance(k, catalog.KnobSpec) for k in knobs)
    assert all(k.env_key and k.group_key for k in knobs)
    assert [k.env_key for k in knobs] == sorted(k.env_key for k in knobs), "정렬 계약"


def test_load_knobs_has_no_duplicate_keys():
    keys = [k.env_key for k in catalog.load_knobs()]
    assert len(keys) == len(set(keys))
