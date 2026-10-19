"""plans/187 독립 검증 — 플래그 off(플레이북 키 없음) 경로의 비트 동일(수용 기준 5).

계획 시작 SHA(`99ec1ea4`)의 모듈 원문을 `git show`로 읽어 임시 모듈로 올리고, 같은 입력에서
현행 모듈과 출력이 같은지 대조한다. 대상은 플레이북이 없을 때 종전과 같아야 한다고 선언된 함수다:
`clip_briefing` · `investigation_view` · `InvestigationStore.record`(연결 필드 없음) ·
`DecisionStore.record`(W11 새 인자 없음) · `build_followup_body`(플레이북 없는 브리핑).
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASE_SHA = "99ec1ea4"


def _load_base(rel_path: str, name: str) -> types.ModuleType:
    try:
        src = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_SHA}:{rel_path}"],
            check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"기준 SHA {BASE_SHA}의 {rel_path}를 읽지 못함")
    mod = types.ModuleType(name)
    mod.__file__ = str(REPO / rel_path)
    sys.modules[name] = mod
    try:
        exec(compile(src, str(REPO / rel_path), "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


def _big_briefing() -> dict:
    return {
        "summary": "요약 " * 400,
        "cause": "원인 " * 3000,
        "evidence": ["근거 " * 200 for _ in range(60)],
        "root_cause_hypotheses": [{"rank": i, "cause": "c" * 500} for i in range(30)],
        "timeline": ["t" * 300 for _ in range(50)],
        "severity": {"level": "high", "confidence": "medium"},
        "limitations": ["한계 1"],
        "extra": {"nested": "x" * 5000},
    }


def _small_briefing() -> dict:
    return {"summary": "s", "cause": "OOM", "evidence": "dmesg", "limitations": []}


@pytest.mark.parametrize("briefing", [_small_briefing(), _big_briefing()])
def test_clip_briefing_without_playbook_is_bit_identical(briefing: dict) -> None:
    from noise_gate.infrastructure import investigation_store as cur

    base = _load_base("noise_gate/infrastructure/investigation_store.py", "_base_inv_store")
    a = json.dumps(base.clip_briefing(dict(briefing)), ensure_ascii=False)
    b = json.dumps(cur.clip_briefing(dict(briefing)), ensure_ascii=False)
    assert a == b


def _mask(s: str) -> str:
    return s.replace("srv", "***")


@pytest.mark.parametrize("operator", [False, True])
def test_investigation_view_alarm_record_bit_identical(operator: bool) -> None:
    from noise_gate.infrastructure import investigation_store as cur

    base = _load_base("noise_gate/infrastructure/investigation_store.py", "_base_inv_store2")
    rec = {
        "investigation_id": "inv-1", "alarm_ids": ["A-1"], "kind": "alarm", "status": "done",
        "reason": "srv-1 원인", "verdict": "high(신뢰도 medium) escalate=False",
        "briefing": _small_briefing(),
        "briefing_lines": [{"label": "원인", "text": "srv-1 OOM"}],
        "ts": "2026-10-19T10:00:00+00:00", "source": "followup",
    }
    kw = {"mask_fn": None if operator else _mask, "operator_view": operator}
    assert base.investigation_view(dict(rec), **kw) == cur.investigation_view(dict(rec), **kw)


def test_store_record_without_link_fields_bit_identical(tmp_path: Path) -> None:
    from noise_gate.infrastructure import investigation_store as cur

    base = _load_base("noise_gate/infrastructure/investigation_store.py", "_base_inv_store3")
    ts = datetime(2026, 10, 19, 10, 0, 0)
    kwargs = dict(
        investigation_id="inv-1", alarm_ids=["A-1"], status="done", source="followup",
        briefing=_big_briefing(), verdict="v", ts=ts, alarm_db_ids={"A-1": "db1"},
    )
    p_base, p_cur = tmp_path / "base.jsonl", tmp_path / "cur.jsonl"
    base.InvestigationStore(str(p_base)).record(**kwargs)
    cur.InvestigationStore(str(p_cur)).record(**kwargs)
    assert p_base.read_text(encoding="utf-8") == p_cur.read_text(encoding="utf-8")


def test_decision_store_record_without_w11_fields_bit_identical(tmp_path: Path) -> None:
    from noise_gate.domain.notification_policy import NotificationDecision
    from noise_gate.infrastructure import decision_store as cur

    base = _load_base("noise_gate/infrastructure/decision_store.py", "_base_decision_store")
    decision = NotificationDecision(
        tier="PAGE", reason="심각도2", priority=3, signals={"root_resource": None},
        fingerprint="fp-1",
    )
    ts = datetime(2026, 10, 19, 10, 0, 0)
    kwargs = dict(alarm_id="A-1", alarm_name="CPU", server_name="srv-1", ts=ts, db_id="db1",
                  resource_name="r1", condition_log="cpu 95%")
    p_base, p_cur = tmp_path / "base.jsonl", tmp_path / "cur.jsonl"
    base.DecisionStore(str(p_base), True).record(decision, **kwargs)
    cur.DecisionStore(str(p_cur), True).record(decision, **kwargs)
    assert p_base.read_text(encoding="utf-8") == p_cur.read_text(encoding="utf-8")


def test_followup_body_without_playbook_bit_identical() -> None:
    from noise_gate.application.nodes import alarm_notifier as cur
    from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent

    base = _load_base("noise_gate/application/nodes/alarm_notifier.py", "_base_notifier")
    ev = AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=datetime(2026, 10, 19, 10, 0),
        conditions=">90%", condition_log="cpu 95%",
    )
    result = AlarmAnalysisResult(
        alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
    )
    for briefing in (None, _small_briefing(), _big_briefing()):
        assert base.build_followup_body(result, briefing) == cur.build_followup_body(
            result, briefing
        )
