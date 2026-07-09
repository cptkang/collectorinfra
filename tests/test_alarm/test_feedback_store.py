"""운영자 피드백 저장소 단위 테스트 (Plan 52 E4).

FeedbackStore가 JSONL로 라벨을 적재하고, alarm_name 일치 후보를 랭킹·최신 우선으로 few-shot
조회함을 고정한다. 잘못된 라벨은 무시하고, 비활성·파일 없음·손상 줄·OSError는 graceful하게
빈 리스트/no-op임을 검증한다(decision_store 패턴 미러).
"""

from __future__ import annotations

import json

from src.alarm.infrastructure.feedback_store import FeedbackStore


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
