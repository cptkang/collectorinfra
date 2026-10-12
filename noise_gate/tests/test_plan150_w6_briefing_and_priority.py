"""plans/150 W6 — 「크로스소스」 브리핑 줄 · 조사 조회 우선순위 · 종합 조사 후속 표지.

    A. `render_briefing_lines` — `cross_source`가 있으면 원 키 나열 대신 「크로스소스」 결정적
       요약 줄 · 없으면 출력 비트 동일(채팅·workb·웹 공용)
    B. `InvestigationStore.get_by_alarm` 우선순위 — ① 종결(done) 종합 조사 ② 자기 단독 조사
       ③ 나머지 · 같은 등급은 최신
    C. 종합 조사 workb 후속 머리 표지(`kind="episode"`만 · 단독 조사 후속 호출은 종전과 같다)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    build_followup_body,
    episode_followup_note,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.investigation_briefing import render_briefing_lines
from noise_gate.infrastructure.investigation_store import InvestigationStore

BRIEFING_MOCK = Path(__file__).resolve().parents[1] / "testdata" / "plan150" / "briefing_mock.json"


def _mock() -> dict[str, Any]:
    return json.loads(BRIEFING_MOCK.read_text(encoding="utf-8"))


def _cross_source(**over: Any) -> dict[str, Any]:
    cs: dict[str, Any] = {
        "episode_id": "ep-1", "host_key": "xsapp01",
        "sources": {"jennifer": 1, "polestar": 2},  # 생산자 순서와 무관하게 폴스타 먼저
        "apm_tools_connected": True, "apm_tools_called": 3,
        "links": [
            {"rule_id": "r1", "cause": "CPU 사용률", "effect": "SERVICE_QUEUING",
             "lag_seconds": 270.0, "supported_by_tools": True},
            {"rule_id": "r2", "cause": "메모리 사용률", "effect": "SERVICE_EXCEPTION",
             "lag_seconds": 12.5, "supported_by_tools": False},
            {"rule_id": "r3", "cause": "디스크", "effect": "SLOW", "lag_seconds": 30,
             "supported_by_tools": None},
        ],
    }
    cs.update(over)
    return cs


# ═════════════════════════════════════════════════════════════════════════════
# A. 「크로스소스」 줄
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossSourceLine:
    def test_line_replaces_raw_keys(self):
        lines = render_briefing_lines({**_mock(), "cross_source": _cross_source()})
        assert ("cross_source" not in [label for label, _ in lines])
        assert lines[-1] == ("크로스소스", "\n".join([
            "사건 호스트 xsapp01 · 폴스타 2건 · 제니퍼 1건",
            "APM 도구 연결 · 호출 3회",
            "규칙 r1: CPU 사용률 → SERVICE_QUEUING (270초) · 도구 뒷받침: 양쪽",
            "규칙 r2: 메모리 사용률 → SERVICE_EXCEPTION (12.5초) · 도구 뒷받침: 한쪽",
            "규칙 r3: 디스크 → SLOW (30초) · 도구 뒷받침: 인용 없음",
        ]))

    def test_without_cross_source_is_bit_identical(self):
        base = render_briefing_lines(_mock())
        assert all(label != "크로스소스" for label, _ in base)
        with_cs = render_briefing_lines({**_mock(), "cross_source": _cross_source()})
        assert with_cs[:-1] == base  # 블록이 있으면 줄 1개만 덧붙는다

    def test_apm_disconnected_and_missing_fields(self):
        cs = {"sources": {"polestar": 1, "other": 4}, "apm_tools_connected": False,
              "links": [{"cause": "CPU", "effect": "Q"}, "깨진 항목"]}
        (text,) = [t for label, t in render_briefing_lines({"cross_source": cs})
                   if label == "크로스소스"]
        assert text.split("\n") == [
            "사건 호스트 미상 · 폴스타 1건 · other 4건",
            "APM 도구 미연결",
            "규칙 미상: CPU → Q (None초) · 도구 뒷받침: 인용 없음",
        ]

    def test_empty_block_is_omitted(self):
        assert render_briefing_lines({"summary": "s", "cross_source": {}}) == [("요약", "s")]


# ═════════════════════════════════════════════════════════════════════════════
# B. 조회 우선순위
# ═════════════════════════════════════════════════════════════════════════════


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def _alarm(store: InvestigationStore, inv: str | None, alarm_id: str, status: str) -> None:
    store.record(investigation_id=inv, alarm_ids=[alarm_id], status=status, source="followup")


def _episode(store: InvestigationStore, inv: str | None, alarm_ids: list[str],
             status: str) -> None:
    store.record(investigation_id=inv, alarm_ids=alarm_ids, status=status, source="followup",
                 kind="episode", episode_id="ep-1")


class TestLookupPriority:
    def test_done_episode_beats_newer_own_alarm(self, tmp_path):
        s = _store(tmp_path)
        _episode(s, "inv-ep", ["P", "J"], "done")
        _alarm(s, "inv-j", "J", "done")
        assert s.get_by_alarm("J")["investigation_id"] == "inv-ep"
        assert s.get_by_alarm("P")["investigation_id"] == "inv-ep"

    def test_failed_episode_does_not_hide_own_alarm(self, tmp_path):
        s = _store(tmp_path)
        _alarm(s, "inv-p", "P", "done")
        _episode(s, None, ["P", "J"], "down")  # 제출 실패 — 조사 id 없음
        rec = s.get_by_alarm("P")
        assert rec["investigation_id"] == "inv-p" and rec["status"] == "done"
        # 자기 단독 조사가 없는 멤버는 실패 레코드라도 보인다(침묵 금지)
        assert s.get_by_alarm("J")["status"] == "failed"

    def test_running_episode_ranks_below_own_alarm(self, tmp_path):
        s = _store(tmp_path)
        _alarm(s, "inv-p", "P", "accepted")
        _episode(s, "inv-ep", ["P", "J"], "submitted")
        assert s.get_by_alarm("P")["investigation_id"] == "inv-p"
        _episode(s, "inv-ep", ["P", "J"], "done")  # 종결되면 종합 조사가 앞선다
        assert s.get_by_alarm("P")["investigation_id"] == "inv-ep"

    def test_latest_wins_within_grade(self, tmp_path):
        s = _store(tmp_path)
        _alarm(s, "inv-1", "A", "done")
        _alarm(s, "inv-2", "A", "failed")
        assert s.get_by_alarm("A")["investigation_id"] == "inv-2"
        _alarm(s, "inv-1", "A", "done")  # inv-1에 새 행 — 그 조사가 최신이 된다
        assert s.get_by_alarm("A")["investigation_id"] == "inv-1"

    def test_linked_alarm_investigation_is_not_own(self, tmp_path):
        # J-2는 J-1의 단독 조사에 연결 행으로 붙었다 — 자기 조사(등급 ②)가 아니다.
        s = _store(tmp_path)
        _alarm(s, None, "J-2", "authz_denied")  # 자기 단독(조사 id 없음) — 등급 ②, 가장 오래됨
        _alarm(s, "inv-j1", "J-1", "done")
        s.record(investigation_id="inv-j1", alarm_ids=["J-2"], status="episode_existing",
                 source="inline", episode_id="ep-1")
        _episode(s, None, ["P", "J-2"], "down")
        assert s.get_by_alarm("J-2")["status"] == "rejected"
        assert s.get_by_alarm("J-1")["investigation_id"] == "inv-j1"


# ═════════════════════════════════════════════════════════════════════════════
# C. 종합 조사 후속 표지
# ═════════════════════════════════════════════════════════════════════════════


def _result() -> AlarmAnalysisResult:
    ev = AlarmEvent(
        db_id="db", server_name="xsapp01", hostname="xsapp01", ip_address="",
        resource_ancestry="", alarm_id="P", severity=2, alarm_status="NOT_ACK",
        resource_type="", resource_name="CPU", alarm_name="CPU 사용률",
        alarm_time="20261007090000", conditions="", condition_log="",
    )
    return AlarmAnalysisResult(
        alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
        notifications_sent={"workb": True},
    )


class TestEpisodeFollowupNote:
    def test_note_text(self):
        assert episode_followup_note({"episode_id": "ep-1", "alarm_ids": ["P", "J"]}) == (
            "같은 사건(폴스타×제니퍼) 종합 조사 — 사건 ep-1 · 멤버 알람 2건"
        )
        assert episode_followup_note({}) == "같은 사건(폴스타×제니퍼) 종합 조사"

    def test_body_head_line_only_with_note(self):
        r = _result()
        plain = build_followup_body(r, {"summary": "s"})
        noted = build_followup_body(r, {"summary": "s"}, episode_note="같은 사건 <x>")
        head = "<b>자동 조사 결과 (후속)</b><br>"
        assert noted == plain.replace(head, head + "<b>같은 사건 &lt;x&gt;</b><br>", 1)

    async def test_deliver_passes_note_only_for_episode(self, monkeypatch):
        sent: list[tuple] = []

        async def _poll(investigation_id, gate_cfg):  # noqa: ANN001
            return {"summary": "b"}, "done", None, None

        async def _send(*args: Any, **kw: Any) -> None:
            sent.append((len(args), kw))

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", _poll)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", _send)
        cfg = SimpleNamespace(workb=SimpleNamespace())
        gate = SimpleNamespace(investigation_followup_timeout_seconds=5.0)
        single = {"investigation_id": "inv-1", "alarm_id": "P", "fingerprint": "fp"}
        episode = {**single, "kind": "episode", "episode_id": "ep-1", "alarm_ids": ["P", "J"]}
        for pending in (single, episode):
            await notifier_mod._deliver_investigation_followup(
                pending, _result(), cfg, gate, None
            )
        assert sent == [
            (4, {}),  # 단독 조사 — 종전 호출 그대로
            (4, {"episode_note": "같은 사건(폴스타×제니퍼) 종합 조사 — 사건 ep-1 · 멤버 알람 2건"}),
        ]
