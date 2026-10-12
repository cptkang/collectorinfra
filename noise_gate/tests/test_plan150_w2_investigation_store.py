"""plans/150 W2 — 조사 저장소(`investigation_store`) 단위 + 워커 배선 계약.

- 레코드 모양(§4.1) · 상태 정규화 · 32KB 절단(「잘림」 표지) · 최신 행 우선 · `alarm_ids` 멤버십
- 연결 행(`episode_existing`)은 기존 조사의 상태를 덮지 않는다
- 화면 요약(`investigation_view`) — 사용자 마스킹·원 dict 제외 / 운영자 원문·원 dict
- 워커: 조사 트리거 off면 저장소 미생성·파일 생성 0(비트 동일)
- (교정) 멤버 `alarm_db_ids` 합집합 · poll 사유 보존(`terminal_poll_reason`)
  · 스텁 `message` → 「안내」
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure.investigation_store import (
    MAX_BRIEFING_BYTES,
    TRUNCATED_NOTE,
    InvestigationStore,
    clip_briefing,
    investigation_view,
    normalize_status,
    terminal_poll_reason,
)


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _briefing(**over) -> dict:
    base = {
        "severity": {"level": "high", "confidence": "medium"},
        "summary": "web-01 CPU 포화",
        "cause": "배치 작업 과다",
        "limitations": ["로그 도구 미연결"],
    }
    base.update(over)
    return base


# ── 기록 모양 ─────────────────────────────────────────────────────────────
class TestRecordShape:
    def test_done_row_carries_briefing_and_lines(self, tmp_path):
        path = tmp_path / "inv.jsonl"
        store = InvestigationStore(str(path))
        store.record(
            investigation_id="inv-1", alarm_ids=["A-1"], status="done", source="inline",
            verdict={"level": "critical", "escalate": True}, briefing=_briefing(),
        )
        (row,) = _rows(path)
        assert row["investigation_id"] == "inv-1"
        assert row["alarm_ids"] == ["A-1"]
        assert row["kind"] == "alarm" and row["status"] == "done"
        assert row["source"] == "inline" and row["ts"]
        assert row["verdict"] == {"level": "critical", "escalate": True}
        assert row["briefing"] == _briefing()
        labels = [line["label"] for line in row["briefing_lines"]]
        assert labels[:2] == ["중요도", "요약"] and "한계" in labels
        # 값 없는 선택 키는 싣지 않는다.
        assert "episode_id" not in row and "reason" not in row and "cross_source" not in row

    def test_cross_source_block_is_lifted(self, tmp_path):
        path = tmp_path / "inv.jsonl"
        InvestigationStore(str(path)).record(
            investigation_id="inv-1", alarm_ids=["A-1"], status="done", source="followup",
            briefing=_briefing(cross_source={"timeline": ["t1"]}), episode_id="ep-1",
        )
        (row,) = _rows(path)
        assert row["cross_source"] == {"timeline": ["t1"]}
        assert row["episode_id"] == "ep-1"

    def test_status_normalization_and_default_reason(self):
        assert normalize_status("stub") == ("done", None)
        assert normalize_status("accepted") == ("running", None)
        assert normalize_status("not_found")[0] == "failed"
        assert normalize_status("followup_timeout")[0] == "timeout"
        assert normalize_status("authz_denied")[0] == "rejected"
        assert normalize_status("done") == ("done", None)
        assert normalize_status("brand_new") == ("brand_new", None)  # 모르는 값은 그대로

    def test_caller_reason_wins_over_default(self, tmp_path):
        path = tmp_path / "inv.jsonl"
        store = InvestigationStore(str(path))
        store.record(investigation_id=None, alarm_ids=["A-1"], status="down", source="inline")
        store.record(
            investigation_id=None, alarm_ids=["A-2"], status="down", source="inline",
            reason="조사 서비스 미가용: 연결 거부",
        )
        first, second = _rows(path)
        assert first["status"] == "failed" and first["reason"] == "조사 서비스 연결 실패"
        assert second["reason"] == "조사 서비스 미가용: 연결 거부"

    def test_write_failure_is_swallowed(self, tmp_path):
        # 디렉터리를 파일 경로로 주면 열기가 실패한다 — 예외가 새지 않아야 한다(발송 차단 금지).
        InvestigationStore(str(tmp_path)).record(
            investigation_id="inv-1", alarm_ids=["A-1"], status="done", source="inline"
        )

    def test_constructor_creates_no_file(self, tmp_path):
        path = tmp_path / "sub" / "inv.jsonl"
        store = InvestigationStore(str(path))
        assert store.get_by_alarm("A-1") is None
        assert not path.exists() and not path.parent.exists()


# ── 32KB 절단 ─────────────────────────────────────────────────────────────
class TestClip:
    def test_small_briefing_untouched(self):
        b = _briefing()
        assert clip_briefing(b) is b

    def test_large_briefing_is_clipped_with_note(self, tmp_path):
        big = _briefing(timeline=[f"이벤트 {i} " + "x" * 200 for i in range(400)],
                        summary="s" * 40000)
        clipped = clip_briefing(big)
        size = len(json.dumps(clipped, ensure_ascii=False).encode("utf-8"))
        assert size <= MAX_BRIEFING_BYTES
        assert clipped["limitations"][-1] == TRUNCATED_NOTE
        assert clipped["limitations"][0] == "로그 도구 미연결"  # 원래 한계는 보존
        assert list(clipped)[: len(big)] == [k for k in big if k in clipped]  # 원 키 순서

        path = tmp_path / "inv.jsonl"
        InvestigationStore(str(path)).record(
            investigation_id="inv-1", alarm_ids=["A-1"], status="done", source="inline",
            briefing=big,
        )
        (row,) = _rows(path)
        assert row["briefing"]["limitations"][-1] == TRUNCATED_NOTE
        assert any(TRUNCATED_NOTE in line["text"] for line in row["briefing_lines"])

    def test_string_limitations_become_list(self):
        clipped = clip_briefing({"limitations": "도구 없음", "summary": "y" * 40000})
        assert clipped["limitations"] == ["도구 없음", TRUNCATED_NOTE]
        assert clipped["summary"].endswith("…")


# ── 조회 ─────────────────────────────────────────────────────────────────
class TestLookup:
    def test_latest_row_wins(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(investigation_id="inv-1", alarm_ids=["A-1"], status="submitted",
                     source="followup")
        store.record(investigation_id="inv-1", alarm_ids=["A-1"], status="done",
                     source="followup", briefing=_briefing())
        assert store.get_by_investigation("inv-1")["status"] == "done"
        assert store.get_by_alarm("A-1")["status"] == "done"

    def test_alarm_ids_membership_and_union(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(investigation_id="inv-9", alarm_ids=["A-1", "A-2"], status="submitted",
                     source="followup", kind="episode", episode_id="ep-1")
        store.record(investigation_id="inv-9", alarm_ids=["A-3"], status="done",
                     source="followup", kind="episode", episode_id="ep-1")
        for alarm_id in ("A-1", "A-2", "A-3"):
            rec = store.get_by_alarm(alarm_id)
            assert rec["investigation_id"] == "inv-9" and rec["status"] == "done"
            assert rec["alarm_ids"] == ["A-1", "A-2", "A-3"]
        assert store.get_by_alarm("A-4") is None
        assert store.get_by_investigation("nope") is None

    def test_link_row_does_not_override_status(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(investigation_id="inv-1", alarm_ids=["J-1"], status="done",
                     source="inline", briefing=_briefing())
        store.record(investigation_id="inv-1", alarm_ids=["J-2"], status="episode_existing",
                     source="inline", episode_id="ep-1")
        rec = store.get_by_alarm("J-2")
        assert rec["status"] == "done" and rec["alarm_ids"] == ["J-1", "J-2"]
        assert store.get_by_investigation("inv-1")["status"] == "done"

    def test_row_without_investigation_id(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(investigation_id=None, alarm_ids=["A-1"], status="authz_denied",
                     source="inline")
        rec = store.get_by_alarm("A-1")
        assert rec["status"] == "rejected" and rec["investigation_id"] is None

    def test_tail_limit_and_corrupt_lines(self, tmp_path):
        path = tmp_path / "inv.jsonl"
        store = InvestigationStore(str(path), max_lines=2)
        store.record(investigation_id="inv-old", alarm_ids=["A-0"], status="done",
                     source="inline")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        store.record(investigation_id="inv-new", alarm_ids=["A-1"], status="done",
                     source="inline")
        assert store.get_by_alarm("A-0") is None  # 스캔 상한 밖
        assert store.get_by_alarm("A-1")["investigation_id"] == "inv-new"


# ── 화면 요약 ────────────────────────────────────────────────────────────
def _mask(text: str) -> str:
    return "***" if text.startswith("sk-") else text


class TestView:
    def _rec(self) -> dict:
        return {
            "investigation_id": "inv-1", "alarm_ids": ["A-1"], "kind": "alarm",
            "status": "done", "reason": "sk-secretreason", "source": "inline",
            "ts": "2026-10-12T00:00:00+00:00",
            "briefing": _briefing(),
            "briefing_lines": [{"label": "요약", "text": "첫 줄\nsk-abcdefghijklmnopqrstuv"}],
            "cross_source": {"note": "sk-crosssource"},
            "verdict": {"level": "critical"},
        }

    def test_user_view_masks_and_drops_raw_briefing(self):
        view = investigation_view(self._rec(), mask_fn=_mask)
        assert set(view) == {
            "status", "investigation_id", "kind", "episode_id", "briefing_lines",
            "cross_source", "verdict_level", "reason", "updated_at", "source",
        }
        assert view["briefing_lines"] == [{"label": "요약", "text": "첫 줄\n***"}]
        assert view["reason"] == "***" and view["cross_source"] == {"note": "***"}
        assert view["verdict_level"] == "critical"
        assert view["updated_at"] == "2026-10-12T00:00:00+00:00"

    def test_operator_view_is_raw_with_briefing_dict(self):
        view = investigation_view(self._rec(), mask_fn=_mask, operator_view=True)
        assert view["briefing"] == _briefing()
        assert view["briefing_lines"][0]["text"].endswith("sk-abcdefghijklmnopqrstuv")
        assert view["reason"] == "sk-secretreason"

    def test_verdict_level_falls_back_to_briefing_severity(self):
        rec = {**self._rec(), "verdict": "문자열 판정"}
        assert investigation_view(rec)["verdict_level"] == "high"


# ── 워커 배선 ────────────────────────────────────────────────────────────
def _worker(tmp_path, **ng) -> AlarmWorker:
    base = dict(
        enable_noise_gate=True,
        investigation_trigger_enabled=False,
        investigation_store_path=str(tmp_path / "inv.jsonl"),
        investigation_store_max_lines=2000,
    )
    base.update(ng)
    worker = AlarmWorker.__new__(AlarmWorker)
    worker._config = SimpleNamespace(noise_gate=SimpleNamespace(**base))
    return worker


class TestWorkerWiring:
    def test_trigger_off_builds_no_store_and_no_file(self, tmp_path):
        assert _worker(tmp_path)._build_investigation_store() is None
        assert _worker(
            tmp_path, enable_noise_gate=False, investigation_trigger_enabled=True
        )._build_investigation_store() is None
        assert not (tmp_path / "inv.jsonl").exists()

    def test_trigger_on_builds_store_lazily(self, tmp_path):
        store = _worker(tmp_path, investigation_trigger_enabled=True)._build_investigation_store()
        assert isinstance(store, InvestigationStore)
        assert store.path == tmp_path / "inv.jsonl" and store.max_lines == 2000
        assert not store.path.exists()  # 첫 기록 전에는 파일을 만들지 않는다


# ── W2 교정: 멤버 db_id · poll 사유 ───────────────────────────────────────
class TestDbIdsAndPollReason:
    def test_alarm_db_ids_are_stored_and_merged(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(
            investigation_id="inv-ep", alarm_ids=["a1"], status="submitted", source="followup",
            kind="episode", alarm_db_ids={"a1": "db_a", "x": None},
        )
        store.record(
            investigation_id="inv-ep", alarm_ids=["a2"], status="episode_existing",
            source="inline", kind="episode", alarm_db_ids={"a2": "db_b"},
        )
        rows = _rows(tmp_path / "inv.jsonl")
        assert rows[0]["alarm_db_ids"] == {"a1": "db_a"}  # 값 없는 항목은 싣지 않는다
        rec = store.get_by_investigation("inv-ep")
        assert rec["alarm_ids"] == ["a1", "a2"]
        assert rec["alarm_db_ids"] == {"a1": "db_a", "a2": "db_b"}

    def test_no_db_ids_keeps_record_shape(self, tmp_path):
        store = InvestigationStore(str(tmp_path / "inv.jsonl"))
        store.record(investigation_id="i", alarm_ids=["a1"], status="done", source="inline")
        assert "alarm_db_ids" not in _rows(tmp_path / "inv.jsonl")[0]
        assert "alarm_db_ids" not in store.get_by_investigation("i")

    def test_terminal_poll_reason(self):
        assert terminal_poll_reason({"status": "failed", "error": "boom"}) == "boom"
        assert terminal_poll_reason({"status": "rejected", "reason": "r", "error": "e"}) == "r"
        assert terminal_poll_reason({"status": "timeout", "reason": "investigation_timeout"}) == (
            "investigation_timeout"
        )
        assert terminal_poll_reason({"status": "stub", "reason": "키 부재"}) == "키 부재"
        assert terminal_poll_reason({"status": "done", "error": "x"}) is None
        assert terminal_poll_reason({"status": "running", "reason": "x"}) is None
        assert terminal_poll_reason({"status": "failed"}) is None

    def test_stub_message_label_is_korean_and_order_unchanged(self):
        from noise_gate.domain.investigation_briefing import render_briefing_lines

        assert render_briefing_lines({"stub": True, "message": "조사 미실행(스텁)"}) == [
            ("안내", "조사 미실행(스텁)")
        ]
        # 라벨만 바뀐다 — 위치(원 키 정렬)와 다른 모르는 키는 그대로.
        assert render_briefing_lines({"zeta": "z", "message": "m", "alpha": "a"}) == [
            ("alpha", "a"), ("안내", "m"), ("zeta", "z")
        ]
