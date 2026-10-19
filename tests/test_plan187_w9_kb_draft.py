"""plans/187 W9 — KB 쓰기·참조 측 순수 도메인·저장소 (D-341 ⑩⑮⑳ · G-14~G-16 · G-27).

- 초안: state 항상 wip · match 후보 = 성립한 판독 enum만 · 입력에 섞인 state/match 무시 ·
  원인 기본값 = 엔진 선택(§5.0.1 7번) · cause_test/workaround 빈 칸 · YAML 스키마 순서·주석
- 포스트모템: 기준(PAGE · 판정 등급) 전부 충족만 · 미충족 사유 · 사용자 마스킹
- 엔진 통계: (조사, 사용자) 최신 1 · 전환 권고 경계 29/30 · 79/80%
- KE 피드백 「아님」 연속 · 재발 30일 · 일치 0건 180일(창 짧으면 판정 보류)
- 참고 문서: 런북 references 먼저 · 최대 3건
- 저장소: sre_agent 아래 경로 거부
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from noise_gate.infrastructure.incident_kb_store import (
    FORBIDDEN_ROOT,
    ForbiddenKbPathError,
    IncidentKbStore,
)
from src.domain import kb_draft as kb

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _briefing(**pb_over) -> dict:
    pb = {
        "id": "was.latency",
        "version": 1,
        "scope": {"host": "wasdemo01", "alarm_instance": "A1", "polestar_server": "wasdemo01",
                  "incident_window": {"end": "2026-10-19T10:10:00", "minutes": 40}},
        "readings": {
            "values": {"dominant": "sql", "db_split": "n/a", "scope": None,
                       "heap_shape": "periodic", "load_mode": "bad value", "flag": True},
        },
        "sections": [
            {"step": "S3", "title": "트랜잭션", "status": "ok", "verdict": "degraded"},
            {"step": "S5", "title": "연관 알람", "status": "ok",
             "table": {"columns": ["t_offset_min", "kind", "detail"],
                       "rows": [[-12, "polestar_alarm", "CPU 경고"], [0, "apm_event", "지연"]]}},
        ],
        "synthesis": {
            "hypotheses": [
                {"rank": 2, "title": "외부 호출 지연", "status": "inconclusive"},
                {"rank": 1, "title": "SQL 실행 지연", "status": "supported",
                 "status_label": "지지"},
            ],
            "actions": ["DBA 확인 요청", {"action": "배치 담당 통보"}],
        },
        "runbook": {"owner": "WAS운영팀", "references": ["manual://incident/was-latency"]},
        "known_errors": [{"id": "KE-1", "state": "validated", "result": "match"}],
    }
    pb.update(pb_over)
    return {"severity": {"level": "심각"}, "cause": "플레이북 종합", "playbook": pb}


def _draft(briefing: dict | None = None, choice: str | None = None) -> dict:
    return kb.build_draft(
        draft_id="abc123", investigation_id="inv-1", briefing=briefing or _briefing(),
        alarm={"alarm_id": "a1", "alarm_name": "응답 지연", "condition_log": "p95>3s",
               "tier": "page"},
        engine_choice=choice, created_by="admin1", created_at=NOW.isoformat(),
    )


# ── 초안 ─────────────────────────────────────────────────────────────────────
def test_match_candidates_only_established_enums():
    out = kb.match_candidates(_briefing()["playbook"]["readings"])
    assert out == ["dominant == 'sql'", "heap_shape == 'periodic'"]


def test_draft_fields_and_state_always_wip():
    d = _draft()
    ke = d["ke"]
    assert ke["state"] == "wip"
    assert ke["issue"] == "응답 지연 — p95>3s"
    assert ke["environment"]["playbook"] == "was.latency"
    assert ke["environment"]["targets"] == {"solution": [], "instance_glob": ["A1"]}
    assert ke["match"] == {"all": ["dominant == 'sql'", "heap_shape == 'periodic'"]}
    assert ke["cause"] == f"SQL 실행 지연 {kb.UNVERIFIED_MARK}"
    assert ke["cause_test"] == {"all": []} and ke["workaround"] == []
    assert ke["owner"] == "WAS운영팀" and ke["review_every_days"] == 90
    assert d["cause_source"] == kb.CAUSE_FROM_PLAYBOOK
    assert d["timeline"] == [
        {"t_offset_min": -12, "kind": "polestar_alarm", "detail": "CPU 경고"},
        {"t_offset_min": 0, "kind": "apm_event", "detail": "지연"},
    ]
    assert list(ke) == ["id", "title", "state", "governance", "issue", "environment", "match",
                        "cause", "cause_test", "workaround", "permanent_fix", "owner",
                        "last_reviewed", "review_every_days"]


def test_suggestion_like_inputs_cannot_change_state_or_match():
    # 브리핑(또는 LLM 제안 텍스트)에 state·match·cause_test가 섞여 있어도 읽지 않는다.
    b = _briefing(state="validated", match={"all": ["evil == 'x'"]},
                  cause_test={"all": ["x"]},
                  kb_suggestion={"state": "validated", "match": {"all": ["evil == 'x'"]},
                                 "title": "다듬은 제목"})
    b["state"] = "validated"
    b["match"] = {"all": ["evil == 'y'"]}
    ke = _draft(b)["ke"]
    assert ke["state"] == "wip"
    assert ke["match"] == {"all": ["dominant == 'sql'", "heap_shape == 'periodic'"]}
    assert ke["cause_test"] == {"all": []}


@pytest.mark.parametrize("choice,cause,source", [
    (None, f"SQL 실행 지연 {kb.UNVERIFIED_MARK}", kb.CAUSE_FROM_PLAYBOOK),
    ("playbook", f"SQL 실행 지연 {kb.UNVERIFIED_MARK}", kb.CAUSE_FROM_PLAYBOOK),
    ("both", f"SQL 실행 지연 {kb.UNVERIFIED_MARK}", kb.CAUSE_FROM_PLAYBOOK),
    ("holmesgpt", f"GC 정지 {kb.UNVERIFIED_MARK}", kb.CAUSE_FROM_HOLMESGPT),
    ("neither", "", kb.CAUSE_EMPTY),
])
def test_cause_default_follows_engine_choice(choice, cause, source):
    b = _briefing()
    b["deep_dive"] = {"cause": "GC 정지"}
    d = _draft(b, choice)
    assert d["ke"]["cause"] == cause and d["cause_source"] == source


def test_holmesgpt_choice_without_deep_dive_leaves_cause_empty():
    d = _draft(_briefing(), "holmesgpt")
    assert d["ke"]["cause"] == "" and d["cause_source"] == kb.CAUSE_EMPTY


def test_draft_requires_playbook():
    with pytest.raises(ValueError):
        kb.build_draft(draft_id="x", investigation_id="i", briefing={"cause": "c"}, alarm=None,
                       engine_choice=None, created_by="", created_at="")


def test_yaml_is_schema_ordered_wip_with_review_comment():
    d = _draft()
    d["ke"]["state"] = "validated"  # 저장 레코드가 바뀌어도 내보내기는 wip
    text = kb.draft_to_yaml(d)
    assert kb.REVIEW_COMMENT in text and "# 타임라인(S5" in text
    loaded = yaml.safe_load(text)
    assert loaded["state"] == "wip"
    assert list(loaded) == list(d["ke"])
    assert loaded["match"] == {"all": ["dominant == 'sql'", "heap_shape == 'periodic'"]}
    assert loaded["cause_test"] == {"all": []} and loaded["workaround"] == []
    assert loaded["last_reviewed"] is None


# ── 포스트모템 ───────────────────────────────────────────────────────────────
def test_postmortem_eligible_only_when_all_criteria_met():
    ok = kb.evaluate_postmortem(tier="page", verdict_level="심각")
    assert ok["eligible"] and ok["reason"] == ""
    assert [c["id"] for c in ok["criteria"]] == ["page", "impact"]
    low = kb.evaluate_postmortem(tier="page", verdict_level="경고")
    assert not low["eligible"] and "사용자 영향" in low["reason"]
    no_page = kb.evaluate_postmortem(tier="ticket", verdict_level="심각")
    assert not no_page["eligible"] and "당직 호출" in no_page["reason"]
    unknown = kb.evaluate_postmortem(tier=None, verdict_level=None)
    assert not unknown["eligible"] and "미상" in unknown["reason"]


def test_postmortem_markdown_sections_and_mask():
    ev = kb.evaluate_postmortem(tier="page", verdict_level="심각")
    md = kb.render_postmortem(investigation_id="inv-1", briefing=_briefing(),
                              alarm={"alarm_name": "SECRET-NAME"}, evaluation=ev,
                              verdict_level="심각", mask_fn=lambda s: "***" if "SECRET" in s else s)
    for name in kb.PAGERDUTY_SECTIONS:
        assert f"## {name}" in md
    assert "SECRET-NAME" not in md and "***" in md
    assert "SQL 실행 지연 (지지) — 미검증 가설" in md
    assert "(제안) 배치 담당 통보" in md and "t_offset_min=-12" in md


# ── 엔진 통계 (G-27) ────────────────────────────────────────────────────────
def _choices(n_side: int, n_other: int, playbook_id: str = "was.latency") -> list[dict]:
    out = [{"investigation_id": f"i{k}", "chosen_by": "u", "choice": "playbook",
            "playbook_id": playbook_id} for k in range(n_side)]
    out += [{"investigation_id": f"j{k}", "chosen_by": "u", "choice": "holmesgpt",
             "playbook_id": playbook_id} for k in range(n_other)]
    return out


@pytest.mark.parametrize("side,other,expected", [
    (29, 0, False),   # 표본 29 — 100%여도 권고 없음
    (24, 6, True),    # 30 · 80% 경계
    (23, 7, False),   # 30 · 76.7%
    (64, 16, True),   # 80 · 80%
    (63, 17, False),  # 80 · 78.75%(79% 미만)
])
def test_engine_switch_recommendation_boundaries(side, other, expected):
    [stat] = kb.engine_stats(_choices(side, other), min_samples=30, switch_ratio=0.8)
    assert stat["samples"] == side + other and stat["recommend_switch"] is expected


def test_engine_stats_latest_per_user_and_both_counts_as_playbook():
    rows = [
        {"investigation_id": "i1", "chosen_by": "u1", "choice": "neither", "playbook_id": "p"},
        {"investigation_id": "i1", "chosen_by": "u1", "choice": "both", "playbook_id": "p"},
        {"investigation_id": "i1", "chosen_by": "u2", "choice": "holmesgpt", "playbook_id": "p"},
        {"investigation_id": "i2", "chosen_by": "u1", "choice": "bogus", "playbook_id": "p"},
    ]
    [stat] = kb.engine_stats(rows, min_samples=1, switch_ratio=0.5)
    assert stat["samples"] == 2
    assert stat["counts"] == {"playbook": 0, "holmesgpt": 1, "both": 1, "neither": 0}
    assert stat["playbook_ratio"] == 0.5 and stat["recommend_switch"] is True


# ── KE 피드백 · 재발 · 검토 대상 ─────────────────────────────────────────────
def test_incorrect_streak_needs_review():
    fb = [{"ke_id": "KE-1", "verdict": "incorrect", "ts": str(i)} for i in range(3)]
    [item] = kb.ke_feedback_review(fb, streak=3)
    assert item["needs_review"] and item["incorrect_streak"] == 3
    broken = [{"ke_id": "KE-1", "verdict": v} for v in
              ("incorrect", "incorrect", "correct", "incorrect")]
    [item] = kb.ke_feedback_review(broken, streak=3)
    assert not item["needs_review"] and item["incorrect_streak"] == 1
    assert item["counts"] == {"correct": 1, "incorrect": 3, "workaround_ineffective": 0}


def _rec(days_ago: float, result: str = "match", **ke_over) -> dict:
    ke = {"id": "KE-1", "result": result, **ke_over}
    return {"ts": (NOW - timedelta(days=days_ago)).isoformat(),
            "briefing": _briefing(known_errors=[ke])}


def test_recurrence_counts_matches_in_30_days():
    act = kb.ke_activity([_rec(40), _rec(10), _rec(5, "candidate"), _rec(1)], now=NOW)
    rec = kb.recurrence_of(act)["KE-1"]
    assert rec["count_30d"] == 2 and rec["last"] == (NOW - timedelta(days=1)).isoformat()


def test_unmatched_180_days_requires_long_enough_window():
    act = kb.ke_activity([_rec(200, "candidate"), _rec(10, "candidate")], now=NOW)
    cov = kb.runbook_coverage([])
    short = kb.review_targets(coverage=cov, activity=act, feedback_review=[], now=NOW,
                              window_oldest=NOW - timedelta(days=100))
    check = short["unmatched_check"]
    assert not check["judgeable"] and "짧아" in check["reason"]
    assert all(i["kind"] != "ke_unmatched" for i in short["items"])
    long = kb.review_targets(coverage=cov, activity=act, feedback_review=[], now=NOW,
                             window_oldest=NOW - timedelta(days=200))
    assert {"kind": "ke_unmatched", "id": "KE-1"}.items() <= long["items"][0].items()
    matched = kb.ke_activity([_rec(200, "candidate"), _rec(10)], now=NOW)
    again = kb.review_targets(coverage=cov, activity=matched, feedback_review=[], now=NOW,
                              window_oldest=NOW - timedelta(days=200))
    assert again["items"] == []


def test_review_overdue_and_runbook_stale_and_streak():
    records = [_rec(1, last_reviewed="2026-01-01", review_every_days=90)]
    records[0]["briefing"]["playbook"]["runbook"]["stale"] = True
    act = kb.ke_activity(records, now=NOW)
    cov = kb.runbook_coverage(records)
    assert cov["by_playbook"][0]["runbook_stale"] is True
    fb = kb.ke_feedback_review([{"ke_id": "KE-9", "verdict": "incorrect"}] * 3, streak=3)
    out = kb.review_targets(coverage=cov, activity=act, feedback_review=fb, now=NOW,
                            window_oldest=None)
    kinds = {(i["kind"], i["id"]) for i in out["items"]}
    assert kinds == {("runbook_stale", "was.latency"), ("ke_review_overdue", "KE-1"),
                     ("ke_incorrect_streak", "KE-9")}


# ── 참고 문서 ─────────────────────────────────────────────────────────────────
def test_references_pinned_first_and_limited():
    hits = [{"title": f"문서{i}", "section": "절", "excerpt": "가" * 300, "href": f"h{i}",
             "doc_id": f"d{i}"} for i in range(4)]
    hits.append({"title": "런북 문서", "section": "1장", "excerpt": "발췌",
                 "href": "", "doc_id": "manual://incident/was-latency"})
    out = kb.merge_references(["manual://incident/was-latency", "manual://none"], hits)
    assert len(out) == 3
    assert out[0]["title"] == "런북 문서" and out[0]["pinned"] and out[0]["excerpt"] == "발췌"
    assert out[1] == {"title": "manual://none", "section": "", "excerpt": "",
                      "href": "manual://none", "pinned": True}
    assert out[2]["title"] == "문서0" and not out[2]["pinned"]
    assert len(out[2]["excerpt"]) == kb.EXCERPT_MAX_CHARS + 1
    assert kb.reference_query(_briefing()) == "was.latency manual://incident/was-latency"


def test_with_kb_extras_does_not_mutate_original():
    b = _briefing()
    out = kb.with_kb_extras(b, references=[{"title": "t"}], references_status={"status": "ok"},
                            recurrence={"KE-1": {"count_30d": 2, "last": "x"}})
    assert out["playbook"]["references"] == [{"title": "t"}]
    assert out["playbook"]["references_status"]["notice"] == kb.REFERENCES_NOTICE
    assert out["playbook"]["known_errors"][0]["recurrence"] == {"count_30d": 2, "last": "x"}
    assert "references" not in b["playbook"]
    assert "recurrence" not in b["playbook"]["known_errors"][0]


# ── 저장소 경로 차단 ─────────────────────────────────────────────────────────
def test_store_rejects_paths_under_sre_agent(tmp_path):
    for bad in (FORBIDDEN_ROOT / "config" / "known_errors" / "x.jsonl",
                FORBIDDEN_ROOT / "config" / "playbooks" / "kb.jsonl"):
        with pytest.raises(ForbiddenKbPathError):
            IncidentKbStore(str(bad))
    link = tmp_path / "link"
    link.symlink_to(FORBIDDEN_ROOT / "config")
    with pytest.raises(ForbiddenKbPathError):
        IncidentKbStore(str(link / "kb.jsonl"))


def test_store_roundtrip_and_latest_engine_choice(tmp_path):
    store = IncidentKbStore(str(tmp_path / "kb.jsonl"), max_lines=4)
    assert store.record_engine_choice(investigation_id="i1", choice="playbook",
                                      playbook_id="p", chosen_by="u1")
    assert store.record_engine_choice(investigation_id="i1", choice="neither",
                                      playbook_id="p", chosen_by="u2")
    assert store.engine_choice_of("i1", "u1") == "playbook"
    assert store.engine_choice_of("i1", "u2") == "neither"
    # (교정 M6) 다른 사용자의 선택으로 폴백하지 않는다.
    assert store.engine_choice_of("i1", "u3") is None
    assert store.engine_choice_of("i1", "") is None
    assert store.record_draft(_draft())
    assert store.get_draft("abc123")["ke"]["state"] == "wip"
    for _ in range(3):
        store.record_ke_feedback(ke_id="KE-1", investigation_id="i1", verdict="incorrect",
                                 labeled_by="u1")
    assert (tmp_path / "kb.jsonl.1").exists()  # 회전


def test_store_rotation_overwrites_single_generation(tmp_path):
    """(교정 M6) `.1`은 직전 회전분만 — append로 무한히 자라지 않는다."""
    store = IncidentKbStore(str(tmp_path / "kb.jsonl"), max_lines=4)
    for i in range(40):
        store.record_engine_choice(investigation_id=f"i{i}", choice="playbook",
                                   playbook_id="p", chosen_by="u1")
    rotated = (tmp_path / "kb.jsonl.1").read_text(encoding="utf-8").splitlines()
    assert 0 < len(rotated) <= 4
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kb.jsonl", "kb.jsonl.1"]


def test_store_ke_feedback_dedupes_same_user_investigation_ke(tmp_path):
    """(교정 M6) 같은 (KE, 조사, 사용자)의 반복 기록은 최신 1건만 집계 재료로 돌려준다."""
    store = IncidentKbStore(str(tmp_path / "kb.jsonl"))
    for _ in range(5):
        store.record_ke_feedback(ke_id="KE-1", investigation_id="i1", verdict="incorrect",
                                 labeled_by="u1")
    store.record_ke_feedback(ke_id="KE-1", investigation_id="i1", verdict="correct",
                             labeled_by="u1")
    store.record_ke_feedback(ke_id="KE-1", investigation_id="i2", verdict="incorrect",
                             labeled_by="u1")
    rows = store.ke_feedback()
    assert [(r["investigation_id"], r["verdict"]) for r in rows] == [
        ("i1", "correct"), ("i2", "incorrect"),
    ]
