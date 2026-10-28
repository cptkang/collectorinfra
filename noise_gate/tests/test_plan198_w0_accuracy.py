"""plans/198 W0 I-1 — 조사 정확도 집계기(결정적 · LLM 0)와 피드백 저장소 라벨 (D-350 ②).

- 라벨 4종 × 엔진 3종 고정 픽스처 → 지표 값 단언(후보 재현율과 RC@1·RC@3 분리)
- 라벨 없음 · 「판단 불가」는 정확도 분모에서 제외(D-238 L5) · 분모 0이면 None
- 미완주율 · 조사 불가율 · 라벨률 · 엔진·kind 판별 · 주 단위 묶음 · 후보 내보내기(메타만)
- 저장소: 최신 라벨이 이김 · 철회 반영 · few-shot·유효/노이즈·사건 집계에 섞이지 않음
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from noise_gate.domain import investigation_accuracy as acc
from noise_gate.infrastructure.feedback_store import FeedbackStore

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)  # 수요일 — 그 주 월요일 2026-10-26
TS = NOW.isoformat()


def _hyps(n: int) -> list[dict]:
    return [{"rank": i, "cause": f"가설 {i}", "confidence": "high", "status": "supported"}
            for i in range(1, n + 1)]


def _briefing(engine: str, n_hyps: int = 4) -> dict:
    base = {"summary": "요약", "cause": "가설 1", "root_cause_hypotheses": _hyps(n_hyps)}
    if engine == acc.ENGINE_HOLMESGPT:
        return base
    pb = {**base, "playbook": {"id": "was.latency", "synthesis": {"hypotheses": []}}}
    if engine == acc.ENGINE_PARALLEL:
        pb["deep_dive"] = {"status": "done", "mode": "parallel", "cause": "GC 정지"}
    else:
        pb["deep_dive"] = {"status": "skipped", "mode": "conditional"}
    return pb


def _rec(inv: str, engine: str, *, status: str = "done", kind: str = "alarm",
         ts: str = TS, briefing: dict | None = None) -> dict:
    return {"investigation_id": inv, "kind": kind, "status": status, "ts": ts,
            "briefing": briefing if briefing is not None else _briefing(engine)}


def _fixture() -> tuple[list[dict], dict[str, dict]]:
    """엔진 3종 × (라벨 4종 + 라벨 없음 1) + 미완주 1 · 조사 불가 2(거부·스텁) · 진행 중 1."""
    records: list[dict] = []
    labels: dict[str, dict] = {}
    # HolmesGPT만 k순위 = 4(3순위 밖) — RC@3과 후보 재현율이 갈라지는 경우
    ranked_k = {acc.ENGINE_PLAYBOOK: 2, acc.ENGINE_HOLMESGPT: 4, acc.ENGINE_PARALLEL: 3}
    for engine in (acc.ENGINE_PLAYBOOK, acc.ENGINE_HOLMESGPT, acc.ENGINE_PARALLEL):
        for verdict in (*acc.CAUSE_VERDICTS, None):
            inv = f"{engine}-{verdict}"
            records.append(_rec(inv, engine))
            if verdict is None:
                continue
            label: dict = {"verdict": verdict}
            if verdict == acc.VERDICT_RANKED:
                label["rank"] = ranked_k[engine]
            if verdict == acc.VERDICT_NOT_LISTED:
                label["note"] = "디스크 장애"
            labels[inv] = label
    records.append(_rec("t-1", acc.ENGINE_PLAYBOOK, status="timeout", kind="episode"))
    records.append(_rec("r-1", acc.ENGINE_NONE, status="rejected", briefing={}))
    records.append(_rec("s-1", acc.ENGINE_NONE, briefing={"stub": True, "message": "스텁"}))
    records.append(_rec("run-1", acc.ENGINE_PLAYBOOK, status="running"))
    # 라벨이 붙었어도 완료가 아닌 조사는 정확도에 들지 않는다
    labels["t-1"] = {"verdict": acc.VERDICT_TOP1}
    return records, labels


def test_engine_and_outcome_classification() -> None:
    assert acc.engine_of(_rec("a", acc.ENGINE_PLAYBOOK)) == acc.ENGINE_PLAYBOOK
    assert acc.engine_of(_rec("a", acc.ENGINE_HOLMESGPT)) == acc.ENGINE_HOLMESGPT
    assert acc.engine_of(_rec("a", acc.ENGINE_PARALLEL)) == acc.ENGINE_PARALLEL
    assert acc.engine_of(_rec("a", "", briefing={"stub": True})) == acc.ENGINE_NONE
    assert acc.engine_of({"status": "failed"}) == acc.ENGINE_NONE
    assert acc.outcome_of(_rec("a", acc.ENGINE_PLAYBOOK)) == acc.OUTCOME_DONE
    assert acc.outcome_of({"status": "timeout"}) == acc.OUTCOME_INCOMPLETE
    assert acc.outcome_of({"status": "failed"}) == acc.OUTCOME_INCOMPLETE
    assert acc.outcome_of({"status": "rejected"}) == acc.OUTCOME_UNAVAILABLE
    assert acc.outcome_of({"status": "submitted"}) == acc.OUTCOME_ACTIVE
    unavailable = _rec("a", "", briefing={"summary": "[미결] 조사 불가 — 관측 도구 연결 실패"})
    assert acc.outcome_of(unavailable) == acc.OUTCOME_UNAVAILABLE


def test_metrics_per_engine_fixture() -> None:
    records, labels = _fixture()
    report = acc.weekly_report(records, labels, now=NOW, weeks=1)
    by_engine = report["by_engine"]
    for engine in (acc.ENGINE_PLAYBOOK, acc.ENGINE_PARALLEL):
        m = by_engine[engine]
        assert (m["done"], m["labeled"], m["undeterminable"], m["judged"]) == (5, 4, 1, 3)
        assert (m["in_list"], m["top1"], m["top3"]) == (2, 1, 2)
        assert m["label_rate"] == 0.8
        assert m["candidate_recall"] == 0.6667
        assert m["rc_at_1"] == 0.3333
        assert m["rc_at_3"] == 0.6667
    holmes = by_engine[acc.ENGINE_HOLMESGPT]
    assert (holmes["in_list"], holmes["top1"], holmes["top3"]) == (2, 1, 1)
    assert holmes["candidate_recall"] == 0.6667  # 4순위도 목록 안 — 재현율에는 든다
    assert holmes["rc_at_3"] == 0.3333           # 3순위 밖 — 순위 지표에서는 빠진다
    none = by_engine[acc.ENGINE_NONE]
    assert (none["investigations"], none["unavailable"], none["done"]) == (2, 2, 0)
    assert none["rc_at_1"] is None and none["label_rate"] is None  # 분모 0 = None(0%가 아니다)


def test_total_rates_exclude_unlabeled_and_undeterminable() -> None:
    records, labels = _fixture()
    total = acc.weekly_report(records, labels, now=NOW, weeks=1)["total"]
    # 진행 중 1건은 세지 않는다 — 끝난 조사 15(완료) + 미완주 1 + 조사 불가 2
    assert total["investigations"] == 18
    assert (total["done"], total["incomplete"], total["unavailable"]) == (15, 1, 2)
    assert total["incomplete_rate"] == round(1 / 18, 4)
    assert total["unavailable_rate"] == round(2 / 18, 4)
    # 판정 분모 = 9(엔진 3 × 1순위·k순위·목록에 없음)
    # 라벨 없음 3 · 판단 불가 3 · 미완주 라벨 1 제외
    assert total["judged"] == 9
    assert total["labeled"] == 12 and total["label_rate"] == 0.8
    assert total["rc_at_1"] == round(3 / 9, 4)
    assert total["rc_at_3"] == round(5 / 9, 4)
    assert total["candidate_recall"] == round(6 / 9, 4)


def test_unlabeled_only_gives_none_rates() -> None:
    records = [_rec("x", acc.ENGINE_PLAYBOOK)]
    m = acc.accuracy_metrics(records, {})
    assert m["judged"] == 0 and m["label_rate"] == 0.0
    assert m["candidate_recall"] is None and m["rc_at_1"] is None and m["rc_at_3"] is None
    only_undeterminable = acc.accuracy_metrics(records, {"x": {"verdict": "undeterminable"}})
    assert only_undeterminable["judged"] == 0 and only_undeterminable["rc_at_1"] is None
    assert only_undeterminable["label_rate"] == 1.0


def test_kind_breakdown_and_weekly_buckets() -> None:
    last_week = (NOW - timedelta(days=7)).isoformat()
    too_old = (NOW - timedelta(days=60)).isoformat()
    records = [
        _rec("a", acc.ENGINE_PLAYBOOK, kind="alarm"),
        _rec("m", acc.ENGINE_PLAYBOOK, kind="manual", ts=last_week),
        _rec("old", acc.ENGINE_PLAYBOOK, ts=too_old),
        {"investigation_id": "no-ts", "status": "done", "briefing": _briefing("playbook")},
    ]
    labels = {"a": {"verdict": "top1"}, "m": {"verdict": "not_listed"}, "old": {"verdict": "top1"}}
    report = acc.weekly_report(records, labels, now=NOW, weeks=2)
    assert [w["week"] for w in report["weeks"]] == ["2026-W44", "2026-W43"]
    assert report["weeks"][0]["start"] == "2026-10-26" and report["weeks"][0]["end"] == "2026-11-01"
    assert report["weeks"][0]["rc_at_1"] == 1.0
    assert report["weeks"][1]["rc_at_1"] == 0.0 and report["weeks"][1]["candidate_recall"] == 0.0
    assert set(report["by_kind"]) == {"alarm", "manual"}
    assert report["total"]["investigations"] == 2  # 창 밖·시각 없음 제외
    assert report["window"] == {"weeks": 2, "start": "2026-10-19", "end": "2026-11-01"}


def test_validate_label() -> None:
    ranks = [1, 2, 3]
    assert acc.validate_label("top1", None, ranks) == (1, None)
    assert acc.validate_label("ranked", 3, ranks) == (3, None)
    assert acc.validate_label("ranked", 1, ranks)[1]
    assert acc.validate_label("ranked", 4, ranks)[1]
    assert acc.validate_label("ranked", None, ranks)[1]
    assert acc.validate_label("top1", None, [])[1]
    assert acc.validate_label("not_listed", 2, []) == (None, None)
    assert acc.validate_label("undeterminable", None, []) == (None, None)
    assert acc.validate_label("valid", None, ranks)[1]


def test_hypotheses_meta_has_no_text() -> None:
    rec = _rec("a", acc.ENGINE_HOLMESGPT)
    rec["briefing"]["root_cause_hypotheses"] = [{"cause": "무순위"}, {"rank": 1, "cause": "x"}]
    hyps = acc.hypotheses_of(rec)
    assert [h["rank"] for h in hyps] == [1, 1]
    assert all(set(h) == {"rank", "status", "confidence"} for h in hyps)
    assert acc.hypothesis_ranks(rec) == [1]


def test_export_candidates_meta_only() -> None:
    records, labels = _fixture()
    items = acc.export_candidates(records, labels)
    assert len(items) == 9  # 판정 라벨 붙은 완료 조사만(판단 불가·라벨 없음·미완주 제외)
    text = repr(items)
    assert "디스크 장애" not in text and "가설 1" not in text and "요약" not in text
    first = items[0]
    assert set(first) == {
        "investigation_id", "kind", "engine", "playbook_id", "label", "hypotheses",
    }
    assert first["label"] == {"verdict": "top1", "rank": 1, "has_note": False}
    noted = next(i for i in items if i["label"]["verdict"] == "not_listed")
    assert noted["label"]["has_note"] is True and noted["label"]["rank"] is None


def test_feedback_store_labels_latest_wins_and_isolated(tmp_path) -> None:
    store = FeedbackStore(str(tmp_path / "fb.jsonl"))
    store.record_feedback(label="valid", alarm_name="CPU", resource_name="r1")
    t0 = datetime(2026, 10, 28, 1, tzinfo=UTC)
    assert store.record_investigation_label(investigation_id="inv-1", verdict="top1", ts=t0)
    store.record_investigation_label(investigation_id="inv-1", verdict="ranked", rank=2,
                                     ts=t0 + timedelta(minutes=1))
    store.record_investigation_label(investigation_id="inv-2", verdict="not_listed",
                                     note="원인 X", ts=t0 + timedelta(minutes=2))
    labels = store.investigation_labels()
    assert labels["inv-1"]["verdict"] == "ranked" and labels["inv-1"]["rank"] == 2
    assert labels["inv-2"]["note"] == "원인 X"
    # 철회 tombstone 규약 그대로
    store.record_retract(target_ts=(t0 + timedelta(minutes=2)).isoformat())
    assert "inv-2" not in store.investigation_labels()
    # few-shot·유효/노이즈·사건 집계에 섞이지 않는다
    assert [s["label"] for s in store.find_similar(alarm_name="CPU")] == ["valid"]
    assert [(s["valid"], s["noise"]) for s in store.summarize()] == [(1, 0)]
    assert store.summarize_episode_feedback() == {"episode_split": 0, "demotion_needed": 0}
    # 빈 값·비활성은 기록하지 않는다
    assert not store.record_investigation_label(investigation_id="", verdict="top1")
    off = FeedbackStore(str(tmp_path / "off.jsonl"), enabled=False)
    assert not off.record_investigation_label(investigation_id="x", verdict="top1")
    assert off.investigation_labels() == {}
