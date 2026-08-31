"""운영자 피드백 저장소 단위 테스트 (Plan 52 E4).

FeedbackStore가 JSONL로 라벨을 적재하고, alarm_name 일치 후보를 랭킹·최신 우선으로 few-shot
조회함을 고정한다. 잘못된 라벨은 무시하고, 비활성·파일 없음·손상 줄·OSError는 graceful하게
빈 리스트/no-op임을 검증한다(decision_store 패턴 미러).
"""

from __future__ import annotations

import json

from noise_gate.infrastructure.feedback_store import FeedbackStore


def _read_rows(path):
    """JSONL 파일을 dict 목록으로 읽는다."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestRecord:
    def test_record_appends_jsonl(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        store.record_feedback(
            label="noise",
            alarm_name="CPU 임계",
            resource_name="svr-CPU",
            pattern="주기적",
            note="야간 배치",
        )
        rows = _read_rows(p)
        assert len(rows) == 1
        assert rows[0]["label"] == "noise"
        assert rows[0]["alarm_name"] == "CPU 임계"
        assert rows[0]["resource_name"] == "svr-CPU"
        assert rows[0]["pattern"] == "주기적"
        assert rows[0]["note"] == "야간 배치"
        assert "ts" in rows[0]

    def test_valid_label_recorded(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p)).record_feedback(label="valid", alarm_name="OOM")
        assert _read_rows(p)[0]["label"] == "valid"

    def test_invalid_label_ignored(self, tmp_path):
        # noise|valid 외 라벨은 기록하지 않는다(파일 생성 안 됨).
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p)).record_feedback(label="bogus", alarm_name="X")
        assert not p.exists()

    def test_disabled_is_noop(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p), enabled=False).record_feedback(label="noise", alarm_name="X")
        assert not p.exists()

    def test_record_oserror_graceful(self, tmp_path):
        # 경로가 디렉토리면 open("a")가 IsADirectoryError(OSError)지만 전파하지 않는다(graceful).
        d = tmp_path / "adir"
        d.mkdir()
        FeedbackStore(str(d)).record_feedback(label="noise", alarm_name="X")  # 예외 없이 반환


class TestFindSimilar:
    def _seed(self, path):
        """CPU 임계 3건 + OOM 1건을 파일 순서대로 적재하고 store를 반환한다."""
        store = FeedbackStore(str(path))
        store.record_feedback(
            label="noise", alarm_name="CPU 임계", resource_name="svr-a-CPU",
            pattern="주기적", note="첫 기록",
        )
        store.record_feedback(
            label="valid", alarm_name="OOM", resource_name="svr-b", pattern="급증",
        )
        store.record_feedback(
            label="noise", alarm_name="CPU 임계", resource_name="svr-c-CPU",
            pattern="산발적", note="다른 자원",
        )
        store.record_feedback(
            label="valid", alarm_name="CPU 임계", resource_name="svr-a-CPU",
            pattern="주기적", note="정확 일치",
        )
        return store

    def test_alarm_name_match_only(self, tmp_path):
        res = self._seed(tmp_path / "fb.jsonl").find_similar(alarm_name="CPU 임계")
        assert len(res) == 3  # OOM 제외
        assert all(r["alarm_name"] == "CPU 임계" for r in res)

    def test_resource_pattern_ranked_first(self, tmp_path):
        # resource+pattern 정확 일치(가점 3)가 최상위. 동점(가점 3)이면 최신 우선.
        res = self._seed(tmp_path / "fb.jsonl").find_similar(
            alarm_name="CPU 임계", resource_name="svr-a-CPU", pattern="주기적"
        )
        assert res[0]["note"] == "정확 일치"

    def test_recency_within_same_score(self, tmp_path):
        # resource/pattern 미지정(전부 가점 0)이면 최신(파일 뒤쪽) 우선.
        res = self._seed(tmp_path / "fb.jsonl").find_similar(alarm_name="CPU 임계")
        assert res[0]["note"] == "정확 일치"

    def test_limit_applied(self, tmp_path):
        res = self._seed(tmp_path / "fb.jsonl").find_similar(alarm_name="CPU 임계", limit=1)
        assert len(res) == 1

    def test_returned_keys(self, tmp_path):
        res = self._seed(tmp_path / "fb.jsonl").find_similar(alarm_name="CPU 임계", limit=1)
        assert set(res[0].keys()) == {"label", "alarm_name", "resource_name", "pattern", "note"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert FeedbackStore(str(tmp_path / "nope.jsonl")).find_similar(alarm_name="X") == []

    def test_disabled_returns_empty(self, tmp_path):
        self._seed(tmp_path / "fb.jsonl")
        off = FeedbackStore(str(tmp_path / "fb.jsonl"), enabled=False)
        assert off.find_similar(alarm_name="CPU 임계") == []

    def test_empty_alarm_name_returns_empty(self, tmp_path):
        assert self._seed(tmp_path / "fb.jsonl").find_similar(alarm_name="") == []

    def test_corrupted_line_skipped(self, tmp_path):
        # 손상된 JSONL 줄은 건너뛰고 정상 항목만 반환한다(graceful).
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        store.record_feedback(label="noise", alarm_name="CPU 임계")
        with p.open("a", encoding="utf-8") as fh:
            fh.write("{ 손상된 줄 not json\n")
        assert len(store.find_similar(alarm_name="CPU 임계")) == 1


# ─── Plan 83 T4: 작성자 기록 · 철회 · 회전 ────────────────────────────────────


class TestLabeledBy:
    """A-4: 라벨 작성자를 감사용으로 남기되, few-shot 프롬프트에는 싣지 않는다."""

    def test_labeled_by_recorded(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p)).record_feedback(
            label="noise", alarm_name="CPU 임계", labeled_by="op1"
        )
        assert _read_rows(p)[0]["labeled_by"] == "op1"

    def test_labeled_by_absent_from_fewshot_render(self, tmp_path):
        """작성자 이름이 LLM 판단에 개입할 이유가 없다 — 렌더 문자열에 나타나면 안 된다."""
        from noise_gate.application.nodes.alarm_analyzer import _render_feedback_section

        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        store.record_feedback(label="noise", alarm_name="CPU 임계", labeled_by="op-secret")
        rendered = _render_feedback_section(store.find_similar(alarm_name="CPU 임계"))
        assert rendered  # 예시가 실제로 렌더됐고
        assert "op-secret" not in rendered  # 작성자는 빠져 있다

    def test_labeled_by_defaults_empty(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p)).record_feedback(label="valid", alarm_name="MEM 임계")
        assert _read_rows(p)[0]["labeled_by"] == ""


class TestRetract:
    """A-5: 철회는 tombstone append — 파일을 재작성하지 않는다(append-only 감사)."""

    def _record_and_get_ts(self, store, path, **kw):
        store.record_feedback(**kw)
        return _read_rows(path)[-1]["ts"]

    def test_retract_excludes_target_from_candidates(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        ts = self._record_and_get_ts(store, p, label="noise", alarm_name="CPU 임계")
        assert len(store.find_similar(alarm_name="CPU 임계")) == 1

        store.record_retract(target_ts=ts, alarm_name="CPU 임계", labeled_by="op1")
        assert store.find_similar(alarm_name="CPU 임계") == []

    def test_retract_appends_and_keeps_original(self, tmp_path):
        """원본 레코드는 파일에 남는다(감사 추적) — 조회에서만 빠진다."""
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        ts = self._record_and_get_ts(store, p, label="noise", alarm_name="CPU 임계")
        store.record_retract(target_ts=ts, alarm_name="CPU 임계")

        rows = _read_rows(p)
        assert len(rows) == 2
        assert rows[0]["label"] == "noise"
        assert rows[1]["label"] == "retract"
        assert rows[1]["target_ts"] == ts

    def test_retract_does_not_affect_other_records(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        ts1 = self._record_and_get_ts(store, p, label="noise", alarm_name="CPU 임계", note="첫번째")
        store.record_feedback(label="noise", alarm_name="CPU 임계", note="두번째")
        store.record_retract(target_ts=ts1, alarm_name="CPU 임계")

        remaining = store.find_similar(alarm_name="CPU 임계")
        assert len(remaining) == 1
        assert remaining[0]["note"] == "두번째"

    def test_retract_disabled_is_noop(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        FeedbackStore(str(p), enabled=False).record_retract(target_ts="x", alarm_name="a")
        assert not p.exists()


class TestRotation:
    """A-6: 파일 무한 증가를 막는다 — 상한 초과 시 2세대 회전(.1)."""

    def test_rotation_moves_old_half(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p), max_lines=10)
        for i in range(12):
            store.record_feedback(label="noise", alarm_name=f"알람{i}")

        assert (tmp_path / "fb.jsonl.1").exists()
        # 회전 후 본 파일은 상한 미만으로 줄어든다
        assert len(_read_rows(p)) < 10

    def test_no_rotation_below_limit(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p), max_lines=100)
        for i in range(5):
            store.record_feedback(label="noise", alarm_name=f"알람{i}")
        assert not (tmp_path / "fb.jsonl.1").exists()

    def test_tail_window_equals_full_scan_within_limit(self, tmp_path):
        """창(=상한) 안에서는 tail 읽기 결과가 전체 스캔과 동일하다(등가성 고정)."""
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p), max_lines=20000)
        for i in range(500):
            store.record_feedback(
                label="noise" if i % 2 else "valid", alarm_name="CPU 임계", note=f"n{i}"
            )
        results = store.find_similar(alarm_name="CPU 임계", limit=3)
        # 최신 우선 — 마지막 3건이 역순으로
        assert [r["note"] for r in results] == ["n499", "n498", "n497"]

    def test_default_max_lines_is_generous(self, tmp_path):
        """max_lines 미지정 시 회전이 즉시 일어나지 않는다(기존 동작 보존)."""
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        for i in range(50):
            store.record_feedback(label="noise", alarm_name="A")
        assert not (tmp_path / "fb.jsonl.1").exists()


class TestSummarize:
    """A-9: 상반된 라벨을 사람이 볼 수 있게 집계한다(판정 로직은 불변)."""

    def test_counts_by_alarm_and_resource(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.jsonl"))
        store.record_feedback(label="noise", alarm_name="CPU", resource_name="cpu_usage")
        store.record_feedback(label="noise", alarm_name="CPU", resource_name="cpu_usage")
        store.record_feedback(label="valid", alarm_name="CPU", resource_name="cpu_usage")
        store.record_feedback(label="valid", alarm_name="MEM", resource_name="mem_usage")

        items = {(i["alarm_name"], i["resource_name"]): i for i in store.summarize()}
        assert items[("CPU", "cpu_usage")]["noise"] == 2
        assert items[("CPU", "cpu_usage")]["valid"] == 1
        assert items[("MEM", "mem_usage")]["valid"] == 1

    def test_last_label_and_author(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.jsonl"))
        store.record_feedback(label="noise", alarm_name="CPU", labeled_by="op1")
        store.record_feedback(label="valid", alarm_name="CPU", labeled_by="op2")
        item = store.summarize()[0]
        assert item["last_label"] == "valid"
        assert item["last_labeled_by"] == "op2"

    def test_retracted_excluded_from_summary(self, tmp_path):
        p = tmp_path / "fb.jsonl"
        store = FeedbackStore(str(p))
        store.record_feedback(label="noise", alarm_name="CPU")
        ts = _read_rows(p)[-1]["ts"]
        store.record_retract(target_ts=ts, alarm_name="CPU")
        assert store.summarize() == []

    def test_empty_when_no_file(self, tmp_path):
        assert FeedbackStore(str(tmp_path / "none.jsonl")).summarize() == []
