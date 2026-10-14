"""plans/153 W1 — 판정 레코드 `analysis` 가산(G-7 (a)) · 처리 안내 정본 `STAGE_HANDLING`.

    1. 게이트 노드 — `decision_analysis_text_enabled` off(기본)면 레코드에 키가 없고(비트 동일),
       on이면 이미 만든 AI 분석 3필드를 각 500자로 잘라 싣는다. 분석 실패면 폴백 문구를 저장하지
       않고 `failed: true`만 남긴다. SUPPRESS로 끝난 알람에도 저장한다. 판정(티어·사유·우선순위·
       단계)은 플래그와 무관하다.
    2. 도메인 표 — 판정 단계 상수 전부(+ unknown)를 덮고, 근거 치환 키는 선택 구간 안에서 그 단계가
       실제로 남기는 근거 키만 쓴다. domain 계층이라 표준 라이브러리 외 import가 없다.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from noise_gate.application.nodes.notification_gate import (
    ANALYSIS_TEXT_MAX,
    analysis_text,
    notification_gate_node,
)
from noise_gate.domain import notification_policy as policy
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.stage_handling import HANDLING_DEFAULT, STAGE_HANDLING
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import IMP_MAP, make_ctx, make_event

# ─── 1. 게이트 노드 ──────────────────────────────────────────────────────


def _result(event, *, error: str | None = None, summary: str = "CPU 98%로 임계 초과",
            cause: str = "배치 작업으로 CPU 포화 추정",
            action: str = "상위 CPU 프로세스 확인") -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary=summary, probable_cause=cause,
        recommended_action=action, notification_channels=[], error=error,
    )


def _config(store: DecisionStore, **gate) -> dict:
    base = dict(enable_noise_gate=True, suppress_max_severity=2, importance_value_map=IMP_MAP,
                resolved_to_dashboard=False)
    base.update(gate)
    return {"configurable": {
        "app_config": SimpleNamespace(noise_gate=SimpleNamespace(**base)), "decision_store": store,
    }}


async def _record(tmp_path: Path, *, result_kw: dict | None = None, ctx: dict | None = None,
                  **gate) -> dict:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    event = make_event(severity=2)
    state = {
        "alarm_event": event,
        "analysis_result": _result(event, **(result_kw or {})),
        "history_stats": None,
        "noise_context": ctx or make_ctx(),
        "error": None,
    }
    await notification_gate_node(state, _config(store, **gate))
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_flag_default_is_off():
    from src.config import NoiseGateConfig

    assert NoiseGateConfig.model_fields["decision_analysis_text_enabled"].default is False


async def test_flag_off_records_no_analysis(tmp_path):
    rec = await _record(tmp_path, decision_analysis_text_enabled=False)
    assert "analysis" not in rec


async def test_flag_missing_on_config_records_no_analysis(tmp_path):
    # 경량 설정(필드 없음)도 off로 본다 — getattr 기본값.
    rec = await _record(tmp_path)
    assert "analysis" not in rec


async def test_flag_on_records_three_fields(tmp_path):
    rec = await _record(tmp_path, decision_analysis_text_enabled=True)
    assert rec["analysis"] == {
        "summary": "CPU 98%로 임계 초과",
        "probable_cause": "배치 작업으로 CPU 포화 추정",
        "recommended_action": "상위 CPU 프로세스 확인",
        "failed": False,
    }


async def test_flag_on_cuts_each_field_at_500(tmp_path):
    long = "가" * (ANALYSIS_TEXT_MAX + 50)
    rec = await _record(
        tmp_path, decision_analysis_text_enabled=True,
        result_kw={"summary": long, "cause": long, "action": long},
    )
    for key in ("summary", "probable_cause", "recommended_action"):
        assert rec["analysis"][key] == "가" * ANALYSIS_TEXT_MAX


async def test_failed_analysis_stores_failed_only(tmp_path):
    rec = await _record(
        tmp_path, decision_analysis_text_enabled=True,
        result_kw={"error": "analysis_failed: TimeoutError",
                   "summary": "AI 분석에 실패해 원문 알람으로 판단했습니다: X"},
    )
    assert rec["analysis"] == {
        "summary": "", "probable_cause": "", "recommended_action": "", "failed": True,
    }
    assert rec["stage_evidence"]["analysis"] == "AI 분석 실패 — 원문 알람으로 판단"


async def test_suppressed_alarm_also_stores_analysis(tmp_path):
    rec = await _record(
        tmp_path, decision_analysis_text_enabled=True, ctx=make_ctx(maintenance=True)
    )
    assert rec["tier"] == policy.TIER_SUPPRESS and rec["stage"] == policy.STAGE_MAINTENANCE
    assert rec["analysis"]["probable_cause"] == "배치 작업으로 CPU 포화 추정"


@pytest.mark.parametrize(
    "ctx", [make_ctx(), make_ctx(maintenance=True), make_ctx(importance_id="LOW")]
)
async def test_flag_does_not_change_decision(tmp_path, ctx):
    off = await _record(tmp_path / "off", ctx=ctx)
    on = await _record(tmp_path / "on", ctx=ctx, decision_analysis_text_enabled=True)
    on.pop("analysis")
    for rec in (off, on):
        rec.pop("ts")
    assert on == off


def test_analysis_text_handles_missing_attributes():
    assert analysis_text(SimpleNamespace(error=None)) == {
        "summary": "", "probable_cause": "", "recommended_action": "", "failed": False,
    }


# ─── 2. 도메인 표 ────────────────────────────────────────────────────────

_TIERS = {policy.TIER_PAGE, policy.TIER_TICKET, policy.TIER_DASHBOARD, policy.TIER_SUPPRESS}

# 단계별로 판정이 실제로 남기는 근거 키(`notification_policy` 결정 지점 + 워커 detection_evidence).
# 운영자 전용 근거(`created_by`)는 일부러 뺐다 — 사용자 화면 문장에 넣지 않는다.
_STAGE_EVIDENCE_KEYS: dict[str, set[str]] = {
    policy.STAGE_NON_ALARM: {"markers"},
    policy.STAGE_SELF_HEAL: {"heal_seconds", "fired_severity"},
    policy.STAGE_SILENCE: {"rule_id", "matcher_summary", "expires_at"},
    policy.STAGE_DEPENDENCY: {"mode", "root_notified", "root_resource_name"},
    policy.STAGE_INHIBITION: {"inhibitor", "inhibitor_severity", "age_seconds", "window_seconds"},
    policy.STAGE_FLAPPING: {"flap_percent", "high", "low", "samples"},
    policy.STAGE_STORM: {"window_count", "threshold", "window_seconds"},
    policy.STAGE_CROSS_SOURCE: {
        "capped_from", "episode_id", "cross_source.rule_id", "cross_source.cause_alarm_id",
        "cross_source.host_key", "cross_source.lag_seconds", "cross_source.missing",
    },
    policy.STAGE_ANNOTATION: {"corroborated_by"},
    policy.STAGE_MATRIX: {"base_tier", "promote", "demote"},
    # (plans/162) 재판정 근거 블록 — 규칙별 대표 알람 id.
    policy.STAGE_PAGE_REVIEW: {
        "page_review.page_alarm_id", "page_review.host_page_alarm_id",
        "page_review.representative_alarm_id", "original_stage", "original_reason",
    },
}


def _stage_constants() -> set[str]:
    return {
        value for name, value in vars(policy).items()
        if name.startswith("STAGE_") and isinstance(value, str)
    }


def test_handling_covers_every_stage_constant():
    assert _stage_constants() == set(policy.STAGE_ORDER) | {policy.STAGE_UNKNOWN}
    assert set(STAGE_HANDLING) == _stage_constants()


def test_every_stage_has_default_and_only_known_tier_keys():
    for stage, table in STAGE_HANDLING.items():
        assert table.get(HANDLING_DEFAULT, "").strip(), stage
        assert set(table) <= _TIERS | {HANDLING_DEFAULT}, stage
        assert all(text.strip() for text in table.values()), stage


def test_placeholders_sit_in_optional_segments_and_use_recorded_evidence_keys():
    for stage, table in STAGE_HANDLING.items():
        for text in table.values():
            outside = re.sub(r"\[[^\[\]]*\]", "", text)
            assert "{" not in outside and "[" not in outside and "]" not in outside, (stage, text)
            keys = set(re.findall(r"\{([\w.]+)\}", text))
            assert keys <= _STAGE_EVIDENCE_KEYS.get(stage, set()), (stage, keys)
            assert "created_by" not in keys


def test_stage_handling_imports_only_stdlib():
    source = Path(policy.__file__).with_name("stage_handling.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= {"__future__"}
