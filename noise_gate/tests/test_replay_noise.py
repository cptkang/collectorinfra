"""noise_gate/scripts/replay_noise.py 단위 테스트 (plans/144 W0 — 리플레이 기준선).

서버·Redis·DB·LLM 불필요. 고정 픽스처(`noise_gate/testdata/cross_source/mock_events.jsonl`)를
현행 공급자로 리플레이하면 제니퍼 미해소 알람이 전부 수집 실패 PAGE로 나오는지(기준선)와,
공급 함수·결정 감사 입력 경로가 동작하는지를 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noise_gate.domain.notification_policy import (
    STAGE_COLLECTION_FAILED,
    STAGE_MATRIX,
    STAGE_RESOLVED,
    STAGE_SEVERITY3,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from noise_gate.scripts import mock_polestar_events as mpe
from noise_gate.scripts import replay_noise as rn

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "cross_source" / "mock_events.jsonl"


@pytest.fixture(scope="module")
def baseline_rows() -> list[dict]:
    rows, skipped = rn.replay(rn.load_jsonl(str(FIXTURE)))
    assert skipped == 0
    return rows


def test_fixture_matches_mock_generator():
    """픽스처는 목업 `--dump` 출력과 같다(생성기와 어긋나면 재생성 — 모듈 독스트링 명령)."""
    assert rn.load_jsonl(str(FIXTURE)) == mpe.cross_source_records()


def test_baseline_jennifer_unresolved_is_collection_failed_page(baseline_rows):
    """기준선 — 제니퍼 미해소(severity 1~2)는 전부 수집 실패 단계 PAGE."""
    unresolved = [
        r for r in baseline_rows if r["source"] == rn.SOURCE_JENNIFER and r["severity"] in (1, 2)
    ]
    assert len(unresolved) == 14
    assert {(r["stage"], r["tier"]) for r in unresolved} == {(STAGE_COLLECTION_FAILED, TIER_PAGE)}


def test_baseline_jennifer_sev3_and_recovery(baseline_rows):
    """기준선 — 제니퍼 severity 3은 심각도3 단락 PAGE, 해소(0)는 해소 단계."""
    jennifer = [r for r in baseline_rows if r["source"] == rn.SOURCE_JENNIFER]
    sev3 = [r for r in jennifer if r["severity"] == 3]
    clear = [r for r in jennifer if r["severity"] == 0]
    assert sev3 and {(r["stage"], r["tier"]) for r in sev3} == {(STAGE_SEVERITY3, TIER_PAGE)}
    assert len(clear) == 5
    assert {(r["stage"], r["tier"]) for r in clear} == {(STAGE_RESOLVED, TIER_SUPPRESS)}


def test_baseline_polestar_uses_recorded_signals(baseline_rows):
    """폴스타는 기록된 신호(중요도 보통)로 매트릭스까지 간다 — 수집 실패가 아니다."""
    polestar = [r for r in baseline_rows if r["source"] == rn.SOURCE_POLESTAR]
    firing = [r for r in polestar if r["severity"] > 0]
    assert {(r["stage"], r["tier"]) for r in firing} == {(STAGE_MATRIX, TIER_TICKET)}
    assert all(r["stage"] == STAGE_RESOLVED for r in polestar if r["severity"] == 0)


def test_baseline_summary_counts(baseline_rows):
    summary = rn.summarize(baseline_rows)
    assert summary["total"] == 25
    assert summary["by_source"] == {"polestar": 5, "jennifer": 20}
    assert summary["by_source_stage"]["jennifer"] == {
        STAGE_COLLECTION_FAILED: 14, STAGE_SEVERITY3: 1, STAGE_RESOLVED: 5,
    }
    assert summary["page_ticket"] == {"total": 18, "by_source": {"polestar": 3, "jennifer": 15}}


def test_provider_is_swappable(baseline_rows):
    """공급 함수만 바꾸면 제니퍼도 수집 실패를 벗어난다(이후 Wave 비교 지점)."""

    def provider(event, recorded):
        return {"importance_id": "1", "maintenance": False, "source": "test"}

    rows, _ = rn.replay(rn.load_jsonl(str(FIXTURE)), provider=provider)
    jennifer_unresolved = [
        r for r in rows if r["source"] == rn.SOURCE_JENNIFER and r["severity"] in (1, 2)
    ]
    assert {r["stage"] for r in jennifer_unresolved} == {STAGE_MATRIX}
    assert len(rows) == len(baseline_rows)


def test_polestar_without_recorded_signals_is_collection_failed():
    payload = mpe.make_payload(
        db_id="polestar_cm_gp", server_name="h1", severity=2, alarm_name="가용성",
        alarm_id="MOCK-r-1",
    )
    rows, _ = rn.replay([payload])
    assert rows[0]["source"] == rn.SOURCE_POLESTAR
    assert (rows[0]["stage"], rows[0]["tier"]) == (STAGE_COLLECTION_FAILED, TIER_PAGE)


def _decision(**overrides):
    rec = {
        "ts": "2026-10-07T00:00:00+00:00", "alarm_id": "a1", "tier": "ticket",
        "reason": "매트릭스(...)", "priority": 0, "fingerprint": "fp",
        "signals": {"severity": 2, "importance": "높음", "maintenance": False,
                    "noti_policy": None, "storm": False},
        "stage": STAGE_MATRIX, "db_id": "polestar_cm_gp", "server_name": "h1",
        "alarm_name": "가용성",
    }
    rec.update(overrides)
    return rec


def test_decision_store_records_replayed():
    """결정 감사 레코드도 입력 — 폴스타는 기록 중요도 · 제니퍼는 수집 실패 · 부속은 건너뜀."""
    lines = [
        _decision(),
        _decision(alarm_id="j1", db_id="jennifer_common", stage=STAGE_COLLECTION_FAILED,
                  tier="page"),
        _decision(alarm_id="p2", stage=None, reason="신호 수집 실패 — 보수적 PAGE"),
        {"type": "recurrence", "alarm_id": "a1", "fingerprint": "fp"},
    ]
    rows, skipped = rn.replay(lines)
    assert skipped == 1
    by_id = {r["alarm_id"]: r for r in rows}
    assert (by_id["a1"]["stage"], by_id["a1"]["tier"]) == (STAGE_MATRIX, TIER_PAGE)  # 높음×2
    assert by_id["j1"]["source"] == rn.SOURCE_JENNIFER
    assert by_id["j1"]["stage"] == STAGE_COLLECTION_FAILED
    assert by_id["p2"]["stage"] == STAGE_COLLECTION_FAILED  # stage 없는 구 레코드 — 사유로 역추정


def test_cli_writes_summary(tmp_path, capsys):
    out = tmp_path / "summary.json"
    assert rn.main([str(FIXTURE), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["page_ticket"]["total"] == 18
    assert rn.main([str(FIXTURE)]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 25
