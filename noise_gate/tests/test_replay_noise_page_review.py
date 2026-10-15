"""replay_noise `--page-review-mode` 테스트 (plans/162 §8 — PAGE 필요성 재판정 리플레이).

서버·Redis·DB·LLM 불필요. 테스트 안에서 만든 작은 판정 감사 시퀀스(폴스타 · sev3 위주)를
리플레이와 W0 what-if(`page_audit`)에 함께 넣어 R1~R3 강등 수가 같은지 대조한다.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    STAGE_PAGE_REVIEW,
    STAGE_RESOLVED,
    STAGE_SEVERITY3,
    TIER_PAGE,
    TIER_TICKET,
    compute_fingerprint,
)
from noise_gate.domain.page_review import KEEP_ALWAYS_PAGE, KEEP_SEVERITY_RISE
from noise_gate.scripts import page_audit as pa
from noise_gate.scripts import replay_noise as rn

KST = timezone(timedelta(hours=9))
_BASE = datetime(2026, 10, 13, 1, 0, 0, tzinfo=KST)
_DB = "polestar_cm_gp"


def _rec(sec: int, server: str, name: str, *, sev: int = 3, importance: str = "보통",
         tier: str = TIER_PAGE, stage: str = STAGE_SEVERITY3) -> dict[str, Any]:
    """판정 감사 레코드 1건 — 지문은 워커와 같은 규칙(compute_fingerprint)으로 채운다."""
    resource = f"res-{server}"
    fp = compute_fingerprint(SimpleNamespace(
        db_id=_DB, server_name=server, hostname="", alarm_name=name, resource_name=resource,
    ))
    return {
        "ts": (_BASE + timedelta(seconds=sec)).astimezone(UTC).isoformat(),
        "alarm_id": f"SEQ-{server}-{sec}", "tier": tier, "reason": "x", "priority": 1,
        "fingerprint": fp,
        "signals": {"severity": sev, "effective_severity": sev, "importance": importance},
        "stage": stage, "alarm_name": name, "server_name": server, "db_id": _DB,
        "resource_name": resource,
    }


def _sequence() -> list[dict[str, Any]]:
    """같은 지문 반복(R1) · 같은 호스트 다른 알람(R2) · 여러 호스트 동시(R3) · 심각도 상승 보존."""
    return [
        # A 같은 지문 10분 간격 3회 → R1 2건 · 해소 뒤 1시간 재발(새 사건) · 1분 뒤 반복 → R1 1건
        _rec(0, "seq-a", "디스크 가득"),
        _rec(600, "seq-a", "디스크 가득"),
        _rec(1200, "seq-a", "디스크 가득"),
        _rec(1500, "seq-a", "디스크 가득", sev=0, tier="suppress", stage=STAGE_RESOLVED),
        _rec(5100, "seq-a", "디스크 가득"),
        _rec(5160, "seq-a", "디스크 가득"),
        # R sev2(높음 → PAGE) 뒤 같은 지문 sev3 → R1 적중이지만 심각도 상승 보존
        _rec(300, "seq-r", "메모리 부족", sev=2, importance="높음", stage=STAGE_MATRIX),
        _rec(400, "seq-r", "메모리 부족"),
        # B 같은 호스트 2분 간격 서로 다른 알람 3건 → R2 2건
        *(_rec(7000 + m * 120, "seq-b", f"서비스 지연 유형{m}") for m in range(3)),
        # C 1분 간격 4호스트 같은 알람 → R3 3건
        *(_rec(10000 + i * 60, f"seq-fan-{i}", "링크 다운") for i in range(4)),
    ]


def _replay_lines() -> list[dict[str, Any]]:
    """리플레이는 입력 순서를 시계로 쓴다 — 발생 시각 순으로 정렬한다."""
    return sorted(_sequence(), key=lambda r: r["ts"])


def _whatif(tmp_path: Path) -> tuple[list[pa.PageEvent], pa.RunResult]:
    """W0 what-if 누적 — 같은 시퀀스를 판정 감사 파일로 써서 page_audit 경로로 읽는다."""
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in _sequence()), "utf-8"
    )
    start, end = _BASE - timedelta(hours=1), _BASE + timedelta(days=1)
    audit = pa.read_audit(path, start, end)
    clears = pa.build_resolutions(audit.decisions, audit.resolutions)
    _, events = pa.derive_features(
        audit.decisions, clears, window_start=start, coverage_start=start,
        repeat_interval_s=14400, sev3_repeat_interval_s=14400,
    )
    runs, _ = pa.run_whatif(events, clears)
    return events, runs["cumulative"]


def test_off_is_bit_identical() -> None:
    """기본 off — 행·요약이 인자 없이 돌린 종전 출력과 같다(`page_review` 키 없음)."""
    lines = _replay_lines()
    base_rows, base_skipped = rn.replay(lines)
    rows, skipped = rn.replay(lines, page_review_mode="off")
    assert (rows, skipped) == (base_rows, base_skipped)
    assert all("page_review" not in r for r in rows)
    summary = rn.summarize(rows, skipped, page_review_mode="off")
    assert summary == rn.summarize(base_rows, base_skipped)
    assert "page_review" not in summary


def test_enforce_matches_whatif_cumulative(tmp_path: Path) -> None:
    """같은 시퀀스에서 replay enforce의 규칙별 강등 수 = W0 what-if R1~R3 누적."""
    events, cumulative = _whatif(tmp_path)
    lines = _replay_lines()
    base_rows, _ = rn.replay(lines)
    rows, _ = rn.replay(lines, page_review_mode="enforce")

    # PAGE 모집단이 같다(page_audit은 원 판정 · replay는 정책 재실행).
    assert sorted(r["alarm_id"] for r in base_rows if r["tier"] == TIER_PAGE) == sorted(
        ev.alarm_id for ev in events
    )
    # what-if 누적에는 생성 경로(R4·R5)·R6 강등이 없다 — R1~R3만 비교 대상이다.
    expected = Counter(cumulative.demoted_by.values())
    assert set(expected) <= set(pa.DOMAIN_RULES)
    assert expected == Counter({"R1": 3, "R2": 2, "R3": 3})

    summary = rn.summarize(rows, page_review_mode="enforce")["page_review"]
    assert summary["by_rule"] == dict(expected)
    assert summary["demoted"] == summary["would_demote"] == sum(expected.values())
    # 강등된 알람도 같다.
    by_idx = {ev.idx: ev.alarm_id for ev in events}
    assert sorted(r["alarm_id"] for r in rows if r["stage"] == STAGE_PAGE_REVIEW) == sorted(
        by_idx[i] for i in cumulative.demoted_by
    )
    assert summary["pages_after"] == len(events) - len(cumulative.demoted_by)
    assert summary["by_keep"] == {KEEP_SEVERITY_RISE: 1}


def test_enforce_rows_carry_demotion() -> None:
    """enforce 행 — 강등은 TICKET/`page_review` 단계 · 비PAGE(해소 등)는 블록 None."""
    rows, _ = rn.replay(_replay_lines(), page_review_mode="enforce")
    demoted = [r for r in rows if r["stage"] == STAGE_PAGE_REVIEW]
    assert demoted and {r["tier"] for r in demoted} == {TIER_TICKET}
    assert all(r["page_review"]["would_demote"] for r in demoted)
    clear = next(r for r in rows if r["stage"] == STAGE_RESOLVED)
    assert clear["page_review"] is None
    # 해소 뒤 1시간 재발은 새 사건 → 보존(PAGE) · 그 1분 뒤 반복은 R1 강등.
    reopen = {r["alarm_id"]: r for r in rows}
    assert reopen["SEQ-seq-a-5100"]["tier"] == TIER_PAGE
    assert reopen["SEQ-seq-a-5100"]["page_review"]["rule"] is None
    assert reopen["SEQ-seq-a-5160"]["page_review"]["rule"] == "R1"


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_review_block_present_for_pages(mode: str) -> None:
    rows, _ = rn.replay(_replay_lines(), page_review_mode=mode)
    blocks = [r["page_review"] for r in rows if r["page_review"]]
    assert blocks and {b["mode"] for b in blocks} == {mode}


def test_shadow_would_demote_equals_enforce() -> None:
    """shadow 「했을 강등」 = enforce 강등(레지스트리는 「했을 결과」 기준) · shadow 판정 불변."""
    lines = _replay_lines()
    base_rows, _ = rn.replay(lines)
    shadow_rows, _ = rn.replay(lines, page_review_mode="shadow")
    enforce_rows, _ = rn.replay(lines, page_review_mode="enforce")
    shadow = rn.summarize(shadow_rows, page_review_mode="shadow")["page_review"]
    enforce = rn.summarize(enforce_rows, page_review_mode="enforce")["page_review"]

    assert shadow["would_demote"] == enforce["would_demote"] == enforce["demoted"] == 8
    assert shadow["by_rule"] == enforce["by_rule"]
    assert shadow["by_keep"] == enforce["by_keep"]
    assert shadow["pages_after"] == enforce["pages_after"]
    assert shadow["demoted"] == 0
    assert [(r["tier"], r["stage"]) for r in shadow_rows] == [
        (r["tier"], r["stage"]) for r in base_rows
    ]
    assert [r["page_review"] for r in shadow_rows if r["page_review"]] == [
        {**r["page_review"], "mode": "shadow"} for r in enforce_rows if r["page_review"]
    ]


def test_config_page_review_fields_apply() -> None:
    """정책 설정의 `page_review_*`를 쓴다 — 항상 PAGE 글롭은 R3 보존 · 호스트 창 1분이면 R2 0."""
    config = SimpleNamespace(
        importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
        page_review_always_page_globs=["링크*"],
        page_review_host_window_minutes=1,
    )
    rows, _ = rn.replay(_replay_lines(), config=config, page_review_mode="enforce")
    summary = rn.summarize(rows, page_review_mode="enforce")["page_review"]
    assert summary["by_rule"] == {"R1": 3}
    assert summary["by_keep"] == {KEEP_SEVERITY_RISE: 1, KEEP_ALWAYS_PAGE: 3}


def test_cli_page_review_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "seq.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in _replay_lines()),
                   "utf-8")
    assert rn.main([str(src)]) == 0
    off = json.loads(capsys.readouterr().out)
    assert "page_review" not in off
    out = tmp_path / "summary.json"
    assert rn.main([str(src), "--page-review-mode", "enforce", "--out", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["page_review"]["mode"] == "enforce"
    assert summary["page_review"]["demoted"] == 8
    assert summary["by_tier"][TIER_PAGE] == off["by_tier"][TIER_PAGE] - 8


def _apm_rec(sec: int, db_id: str = "jennifer_cm_prd") -> dict[str, Any]:
    """APM(제니퍼) sev3 판정 레코드 — 해소 이벤트가 오지 않는 출처."""
    return {
        "ts": (_BASE + timedelta(seconds=sec)).astimezone(UTC).isoformat(),
        "alarm_id": f"APM-{db_id}-{sec}", "tier": TIER_PAGE, "reason": "x", "priority": 1,
        "signals": {"severity": 3, "effective_severity": 3, "importance": "보통"},
        "stage": STAGE_SEVERITY3, "alarm_name": "SERVICE_QUEUING", "server_name": "fake-was01",
        "db_id": db_id, "resource_name": "res-was01",
    }


def test_idle_close_reopens_silent_apm_incident() -> None:
    """무소식 닫힘 — idle 30분이면 APM 50분 공백 뒤 재발은 새 사건 PAGE · 기본 0은 종전(R1)."""
    lines = [_apm_rec(0), _apm_rec(600), _apm_rec(3600)]
    idle = SimpleNamespace(
        importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
        page_review_idle_close_minutes=30,
    )
    default_rows, _ = rn.replay(lines, page_review_mode="enforce")
    idle_rows, _ = rn.replay(lines, config=idle, page_review_mode="enforce")
    assert [(r["page_review"] or {}).get("rule") for r in default_rows] == [None, "R1", "R1"]
    assert [(r["page_review"] or {}).get("rule") for r in idle_rows] == [None, "R1", None]
    assert idle_rows[2]["tier"] == TIER_PAGE
    # 폴스타(해소가 오는 출처)는 idle 설정이 있어도 닫히지 않는다.
    polestar = [_apm_rec(s, db_id=_DB) for s in (0, 600, 3600)]
    rows, _ = rn.replay(polestar, config=idle, page_review_mode="enforce")
    assert [(r["page_review"] or {}).get("rule") for r in rows] == [None, "R1", "R1"]
