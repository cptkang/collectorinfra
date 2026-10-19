"""plans/187 W5a — 웹 표시용 본체 백엔드 (§5.6 「동작」 · D-341 ⑦ · D-325 ②③).

- 진행 중 조사: 지연 poll이 받은 `progress`·`partial_briefing`을 응답 최상위에만 싣는다(저장 0).
- poll 간격 안의 재요청은 직전 poll 값을 메모리(`POLL_LIVE`)에서 재사용 · 종결 시 폐기 · sweep 만료.
- 종결 뒤에는 `partial_briefing` 없음(`progress`만 — 있으면).
- 키가 없는 poll 응답(플레이북 off)이면 응답이 종전과 같다.
- 사용자 경로: `briefing.playbook`·`partial_briefing.playbook`·`deep_dive`의 문자열 전부 마스킹 —
  구조 키·enum·수치는 그대로 · 6요소 원문은 싣지 않는다. 운영자 뷰는 원문.

조사 서비스는 띄우지 않는다 — poll 응답을 고정한 대역을 주입한다.
"""

from __future__ import annotations

import copy
import json
import time

import pytest

from noise_gate.infrastructure.investigation_store import (
    investigation_view,
    playbook_user_view,
)
from src.api import investigation_poll
from tests.test_api.test_noise_dashboard_api import _decision_record, _seed_decisions
from tests.test_api.test_plan153_w2_investigation_api import (
    ADMIN,
    GONGJON_DB,
    GONGJON_USER,
    SECRET,
    _alarm_client,
    _config,
    _inject_client,
    _PollClient,
    _rows,
    _store,
)

MASK = "***"


def _playbook() -> dict:
    """W4 계약 모양의 `playbook` 블록 — 사용자 경로에서 가려야 할 문자열 칸마다 SECRET을 둔다."""
    return {
        "id": "was.latency",
        "version": "1",
        "matched_by": "alarm_kind",
        "scope": {
            "alarm_instance": SECRET,
            "host": SECRET,
            "polestar_server": SECRET,
            "polestar_source": "polestar_cm_gp",
            "targets": [SECRET],
            "incident_window": {"start": "2026-10-19T00:00:00", "end": "2026-10-19T00:40:00",
                                "minutes": 40},
        },
        "windows": {"incident": {"start": "2026-10-19T00:00:00"}},
        "readings": {"dominant": "sql", "db_split": "n/a"},
        "readings_detail": {"dominant": {"basis": SECRET}},
        "sections": [
            {
                "step": "S1", "action": "apm.golden_signals", "title": "골든 시그널",
                "status": "ok", "verdict": "abnormal", "verdict_source": "rule",
                "table": {"columns": ["인스턴스", "p95(ms)"], "rows": [[SECRET, 1830]]},
                "badges": ["현재값"], "tools": ["apm_app_health"], "reason": None,
                "opinion": SECRET, "opinion_dropped_reason": None, "opinion_guard": [],
            },
        ],
        "synthesis": {
            "hypotheses": [{"rank": 1, "title": SECRET, "status": "supported",
                            "status_label": "지지", "evidence_steps": ["S1"],
                            "rule_id": "R-C"}],
            "actions": [{"order": 1, "source": "runbook", "text": SECRET, "key": "triage_first"}],
            "followups": [SECRET],
            "narrative": SECRET,
            "top_inconclusive": False,
        },
        "timings": {"total_ms": 1200},
        "notes": [SECRET],
        "runbook": {"meaning": SECRET, "owner": "WAS팀"},
        "known_errors": [{"id": "KE-2026-001", "status": "validated", "workaround": SECRET}],
    }


def _deep_dive(status: str = "done") -> dict:
    return {"status": status, "mode": "parallel", "reason": SECRET, "label": "심층 조사",
            "summary": SECRET}


def _engine_agreement() -> dict:
    return {"result": "same_layer", "label": "같은 계층", "approximate": True,
            "reason": "두 엔진 모두 db 계층을 가리킴",
            "playbook": {"layer": "db", "basis": ["dominant"]}}


def _final_briefing() -> dict:
    return {
        "severity": {"level": "high", "confidence": "medium"},
        "summary": SECRET,
        "playbook": _playbook(),
        "deep_dive": _deep_dive(),
        "engine_agreement": _engine_agreement(),
    }


def _partial_briefing() -> dict:
    return {"partial": True, "playbook": _playbook(),
            "deep_dive": {"status": "running", "mode": "parallel", "reason": None,
                          "label": "심층 조사"}}


PROGRESS = {"done": 4, "total": 9, "current_step": "S4"}


def _seed_running(tmp_path, inv: str = "inv-1") -> None:
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=["a1"], status="submitted", source="followup"
    )


def _strings(value) -> list[str]:  # noqa: ANN001
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


# ── 진행 중: progress · partial_briefing (응답에만) ────────────────────────────
@pytest.mark.parametrize("path", ["/api/v1/alarm/investigations/inv-1",
                                  "/api/v1/alarm/investigations/by-alarm/a1"])
def test_running_response_carries_progress_and_partial(tmp_path, monkeypatch, path):
    _seed_running(tmp_path)
    _inject_client(monkeypatch, _PollClient(
        {"status": "running", "progress": PROGRESS, "partial_briefing": _partial_briefing()}
    ))
    body = _alarm_client(_config(tmp_path), ADMIN).get(path).json()
    assert body["status"] == "running"
    assert body["progress"] == PROGRESS
    assert body["partial_briefing"] == _partial_briefing()  # 운영자 = 원문
    # 저장하지 않는다 — 행이 늘지 않고 진척 키가 파일에 없다.
    rows = _rows(tmp_path)
    assert len(rows) == 1 and "progress" not in rows[0] and "partial_briefing" not in rows[0]


def test_throttled_request_reuses_last_poll_values(tmp_path, monkeypatch):
    _seed_running(tmp_path)
    fake = _PollClient({"status": "running", "progress": PROGRESS,
                        "partial_briefing": _partial_briefing()})
    _inject_client(monkeypatch, fake)
    client = _alarm_client(_config(tmp_path), ADMIN)
    first = client.get("/api/v1/alarm/investigations/inv-1").json()
    second = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert fake.poll_calls == ["inv-1"]  # 간격 안 — poll 1회
    assert second["progress"] == first["progress"] == PROGRESS
    assert second["partial_briefing"] == _partial_briefing()
    assert len(_rows(tmp_path)) == 1


def test_live_values_expire_with_poll_sweep(tmp_path, monkeypatch):
    _seed_running(tmp_path)
    _inject_client(monkeypatch, _PollClient({"status": "running", "progress": PROGRESS}))
    _alarm_client(_config(tmp_path), ADMIN).get("/api/v1/alarm/investigations/inv-1")
    key = (str(tmp_path / "inv.jsonl"), "inv-1")
    assert investigation_poll.POLL_LIVE[key] == {"progress": PROGRESS}
    investigation_poll.sweep_polls(time.monotonic() + investigation_poll.POLL_MIN_INTERVAL)
    assert key not in investigation_poll.POLL_LIVE and key not in investigation_poll.POLL_AT


def test_terminal_poll_drops_partial_keeps_progress_and_stores_unchanged(tmp_path, monkeypatch):
    _seed_running(tmp_path)
    fake = _PollClient({"status": "running", "progress": PROGRESS,
                        "partial_briefing": _partial_briefing()})
    _inject_client(monkeypatch, fake)
    client = _alarm_client(_config(tmp_path), ADMIN)
    client.get("/api/v1/alarm/investigations/inv-1")
    key = (str(tmp_path / "inv.jsonl"), "inv-1")
    assert key in investigation_poll.POLL_LIVE
    # 종결 — sre_agent는 종결 시 partial_briefing을 비우고 progress만 남긴다.
    done_progress = {"done": 9, "total": 9, "current_step": None}
    fake.res = {"status": "done", "briefing": _final_briefing(), "verdict": {"level": "high"},
                "progress": done_progress, "partial_briefing": _partial_briefing()}
    investigation_poll.POLL_AT[key] -= investigation_poll.POLL_MIN_INTERVAL
    body = client.get("/api/v1/alarm/investigations/inv-1").json()
    assert body["status"] == "done" and body["source"] == "api_poll"
    assert body["progress"] == done_progress
    assert "partial_briefing" not in body
    assert body["briefing"] == _final_briefing()
    assert key not in investigation_poll.POLL_LIVE  # 종결 시 폐기
    last = _rows(tmp_path)[-1]
    assert last["status"] == "done" and "progress" not in last and "partial_briefing" not in last
    assert last["briefing"] == _final_briefing()
    # 종결 저장 뒤의 조회 — poll하지 않으며 진척 키도 없다.
    after = client.get("/api/v1/alarm/investigations/inv-1").json()
    assert "progress" not in after and "partial_briefing" not in after


def test_without_live_keys_response_is_unchanged(tmp_path, monkeypatch):
    _seed_running(tmp_path)
    _inject_client(monkeypatch, _PollClient({"status": "running"}))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert set(body) == {"status", "investigation_id", "kind", "episode_id", "briefing_lines",
                         "cross_source", "verdict_level", "reason", "updated_at", "source"}
    assert (str(tmp_path / "inv.jsonl"), "inv-1") not in investigation_poll.POLL_LIVE


def test_no_playbook_user_view_is_unchanged():
    rec = {"investigation_id": "i", "status": "done", "briefing": {"summary": SECRET},
           "briefing_lines": [{"label": "요약", "text": SECRET}]}
    view = investigation_view(rec, mask_fn=lambda s: MASK if s == SECRET else s)
    assert "briefing" not in view
    assert playbook_user_view({"summary": SECRET}, lambda s: s) is None


# ── 사용자 경로 마스킹 ────────────────────────────────────────────────────────
def test_user_path_masks_every_playbook_string_in_final_briefing(tmp_path, monkeypatch):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="followup",
        briefing=_final_briefing(),
    )
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    briefing = body["briefing"]
    # 6요소 원문(summary·severity)은 싣지 않는다 — 표시용 키만.
    assert set(briefing) == {"playbook", "deep_dive", "engine_agreement"}
    assert SECRET not in json.dumps(briefing, ensure_ascii=False)
    pb = briefing["playbook"]
    sec = pb["sections"][0]
    assert sec["table"]["rows"] == [[MASK, 1830]] and sec["opinion"] == MASK
    assert pb["synthesis"]["narrative"] == MASK
    assert pb["synthesis"]["hypotheses"][0]["title"] == MASK
    assert pb["synthesis"]["actions"][0]["text"] == MASK
    assert pb["scope"]["host"] == MASK and pb["scope"]["polestar_server"] == MASK
    assert pb["scope"]["alarm_instance"] == MASK and pb["scope"]["targets"] == [MASK]
    assert pb["notes"] == [MASK] and pb["runbook"]["meaning"] == MASK
    assert pb["known_errors"][0]["workaround"] == MASK
    assert briefing["deep_dive"]["reason"] == MASK and briefing["deep_dive"]["summary"] == MASK
    # 구조 키·enum·수치는 그대로.
    assert (pb["id"], pb["version"], pb["matched_by"]) == ("was.latency", "1", "alarm_kind")
    assert pb["readings"] == {"dominant": "sql", "db_split": "n/a"}
    assert (sec["step"], sec["action"], sec["status"], sec["verdict"], sec["verdict_source"]) == (
        "S1", "apm.golden_signals", "ok", "abnormal", "rule")
    hyp = pb["synthesis"]["hypotheses"][0]
    assert (hyp["rank"], hyp["status"], hyp["rule_id"]) == (1, "supported", "R-C")
    assert pb["scope"]["incident_window"]["minutes"] == 40
    assert briefing["deep_dive"]["status"] == "done" and briefing["deep_dive"]["mode"] == "parallel"
    assert briefing["engine_agreement"]["result"] == "same_layer"
    # 마스킹 대상이 아닌 문자열은 원문(값 패턴 일치만 가린다).
    assert sec["title"] == "골든 시그널" and pb["runbook"]["owner"] == "WAS팀"


def test_user_path_masks_partial_briefing(tmp_path, monkeypatch):
    _seed_running(tmp_path)
    partial = _partial_briefing()
    partial["summary"] = SECRET  # 플레이북 완료·심층 진행 중이면 6요소가 함께 온다
    _inject_client(monkeypatch, _PollClient(
        {"status": "running", "progress": PROGRESS, "partial_briefing": partial}
    ))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["progress"] == PROGRESS
    pb_view = body["partial_briefing"]
    assert set(pb_view) == {"partial", "playbook", "deep_dive"} and pb_view["partial"] is True
    assert SECRET not in json.dumps(pb_view, ensure_ascii=False)
    assert all(s != SECRET for s in _strings(pb_view))
    assert pb_view["playbook"]["sections"][0]["verdict"] == "abnormal"


def test_operator_gets_raw_playbook(tmp_path):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="followup",
        briefing=_final_briefing(),
    )
    body = _alarm_client(_config(tmp_path), ADMIN).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["briefing"] == _final_briefing()


def test_masking_does_not_mutate_source():
    src = _final_briefing()
    before = copy.deepcopy(src)
    playbook_user_view(src, lambda s: MASK if s == SECRET else s)
    assert src == before


# ── 저장 상한(32KB) — 플레이북은 통째로 빠지지 않고 줄여 담는다 ─────────────────
def _big_briefing() -> dict:
    briefing = _final_briefing()
    pb = briefing["playbook"]
    section = pb["sections"][0]
    pb["sections"] = [
        {**section, "step": f"S{i}", "title": f"단계 {i}",
         "table": {"columns": ["값"], "rows": [[f"한국어 행 {i}-{j} " * 6] for j in range(60)]},
         "opinion": "한국어 소견 " * 80}
        for i in range(9)
    ]
    pb["readings_detail"] = {"basis": "근거 " * 2000}
    briefing["deep_dive"] = {**_deep_dive(), "cause": "심층 원인", "timeline": ["사건 " * 50] * 100}
    return briefing


def test_large_playbook_is_shrunk_not_dropped(tmp_path):
    from noise_gate.infrastructure.investigation_store import (
        MAX_BRIEFING_BYTES,
        PLAYBOOK_CLIPPED_NOTE,
        TRUNCATED_NOTE,
    )

    big = _big_briefing()
    assert len(json.dumps(big, ensure_ascii=False).encode("utf-8")) > 40 * 1024
    _store(tmp_path).record(investigation_id="inv-1", alarm_ids=["a1"], status="done",
                            source="followup", briefing=big)
    stored = _rows(tmp_path)[-1]["briefing"]
    assert len(json.dumps(stored, ensure_ascii=False).encode("utf-8")) <= MAX_BRIEFING_BYTES
    assert TRUNCATED_NOTE in stored["limitations"]
    pb = stored["playbook"]
    assert pb["synthesis"] == big["playbook"]["synthesis"]  # 가설·대응 끝까지 보존
    heads = [(s["step"], s["title"], s["status"], s["verdict"], s["verdict_source"])
             for s in pb["sections"]]
    assert heads == [(f"S{i}", f"단계 {i}", "ok", "abnormal", "rule") for i in range(9)]
    assert any(n.startswith(PLAYBOOK_CLIPPED_NOTE.split("—")[0]) for n in pb["notes"])
    assert "readings_detail" not in pb
    # 심층 결과도 통째로 빠지지 않는다 — 상태·요약·원인 우선 보존.
    dd = stored["deep_dive"]
    assert dd["status"] == "done" and dd["summary"] == SECRET and dd["cause"] == "심층 원인"


def test_small_rows_overflow_keeps_first_rows_with_badge():
    from noise_gate.infrastructure.investigation_store import clip_briefing

    briefing = _final_briefing()
    pb = briefing["playbook"]
    pb["sections"][0]["table"]["rows"] = [[f"행{j}", j] for j in range(5000)]
    clipped = clip_briefing(briefing)
    sec = clipped["playbook"]["sections"][0]
    assert sec["table"]["rows"] == [[f"행{j}", j] for j in range(5)]
    assert "표 일부 생략(5000행 중 5행)" in sec["badges"]
    assert sec["opinion"] == SECRET  # 1단계에서 충분하면 소견은 남는다
