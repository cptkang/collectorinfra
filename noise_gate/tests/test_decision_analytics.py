"""결정 저장소 관제 집계·조회 테스트 (Plan 54 모듈 2 `decision-analytics` · 모듈 3 `decision-lookup`).

퍼널 항등식(`Σ terminated == raw`), 창 경계, 구 레코드 폴백, 시계열 고정 격자,
상위 억제 집계, 그리고 조회 계열의 필터·페이지·마스킹을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from noise_gate.domain.notification_policy import (
    NotificationDecision,
    STAGE_FLAPPING,
    STAGE_MATRIX,
    STAGE_SEVERITY3,
    STAGE_UNKNOWN,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from noise_gate.infrastructure.decision_store import DecisionStore


def _decision(tier: str, stage: str, reason: str = "사유") -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason=reason, priority=1, signals={"severity": 2}, stage=stage
    )


@pytest.fixture()
def store(tmp_path):
    return DecisionStore(str(tmp_path / "decisions.jsonl"))


def _write_raw(store: DecisionStore, records: list[dict]) -> None:
    """stage 필드가 없는 **구 레코드**를 직접 적재한다(폴백 경로 검증용)."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with store.path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class TestFunnel:
    def test_empty_store_returns_zero_not_error(self, store):
        f = store.funnel()
        assert f["raw"] == 0
        assert f["suppress_ratio"] == 0.0
        assert all(s["terminated"] == 0 for s in f["stages"])

    def test_terminated_sum_equals_raw(self, store):
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3), alarm_id="a1")
        store.record(_decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a2")
        store.record(_decision(TIER_TICKET, STAGE_MATRIX), alarm_id="a3")

        f = store.funnel()
        assert f["raw"] == 3
        assert sum(s["terminated"] for s in f["stages"]) == f["raw"]

    def test_residual_decreases_along_pipeline(self, store):
        # severity3에서 1건이 종결되면 그 뒤 단계의 잔여는 1 줄어야 한다.
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3), alarm_id="a1")
        store.record(_decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a2")

        stages = {s["stage"]: s for s in store.funnel()["stages"]}
        assert stages[STAGE_SEVERITY3]["residual"] == 2
        assert stages[STAGE_FLAPPING]["residual"] == 1

    def test_cut_counts_only_uncommunicated_tiers(self, store):
        # PAGE·TICKET은 운영자에게 도달하므로 캔슬로 세지 않는다.
        store.record(_decision(TIER_PAGE, STAGE_MATRIX), alarm_id="a1")
        store.record(_decision(TIER_TICKET, STAGE_MATRIX), alarm_id="a2")
        store.record(_decision(TIER_DASHBOARD, STAGE_MATRIX), alarm_id="a3")
        store.record(_decision(TIER_SUPPRESS, STAGE_MATRIX), alarm_id="a4")

        stages = {s["stage"]: s for s in store.funnel()["stages"]}
        assert stages[STAGE_MATRIX]["terminated"] == 4
        assert stages[STAGE_MATRIX]["cut"] == 2  # dashboard + suppress

    def test_legacy_records_without_stage_are_mapped_by_reason(self, store):
        _write_raw(store, [
            {"ts": datetime.now(timezone.utc).isoformat(), "alarm_id": "old1",
             "tier": TIER_PAGE, "reason": "심각도3 — 항상 통보(억제 단계 미경유)",
             "priority": 1, "signals": {}},
            {"ts": datetime.now(timezone.utc).isoformat(), "alarm_id": "old2",
             "tier": TIER_SUPPRESS, "reason": "플래핑 — 상태 진동(Nagios), 안정화까지 통보 보류",
             "priority": 1, "signals": {}},
        ])
        stages = {s["stage"]: s for s in store.funnel()["stages"]}
        assert stages[STAGE_SEVERITY3]["terminated"] == 1
        assert stages[STAGE_FLAPPING]["terminated"] == 1

    def test_unmappable_reason_goes_to_unknown_not_dropped(self, store):
        # 합계 항등식이 깨지면 퍼널이 거짓말을 하므로, 매핑 실패도 반드시 센다.
        _write_raw(store, [
            {"ts": datetime.now(timezone.utc).isoformat(), "alarm_id": "x",
             "tier": TIER_SUPPRESS, "reason": "정체불명 사유", "priority": 1, "signals": {}},
        ])
        f = store.funnel()
        assert f["raw"] == 1
        assert sum(s["terminated"] for s in f["stages"]) == 1
        unknown = [s for s in f["stages"] if s["stage"] == STAGE_UNKNOWN]
        assert unknown and unknown[0]["terminated"] == 1

    def test_non_decision_records_are_excluded(self, store):
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3), alarm_id="a1")
        _write_raw(store, [{"type": "resolution", "duration_seconds": 5}])
        assert store.funnel()["raw"] == 1

    def test_window_excludes_old_records(self, store):
        old_ts = datetime.now(timezone.utc) - timedelta(hours=48)
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3), alarm_id="old", ts=old_ts)
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3), alarm_id="new")
        assert store.funnel(window_seconds=3600)["raw"] == 1
        assert store.funnel()["raw"] == 2

    def test_ratios(self, store):
        store.record(_decision(TIER_PAGE, STAGE_MATRIX), alarm_id="a1")
        store.record(_decision(TIER_SUPPRESS, STAGE_MATRIX), alarm_id="a2")
        f = store.funnel()
        assert f["suppress_ratio"] == 0.5
        assert f["actionable_ratio"] == 0.5


class TestTimeseries:
    def test_buckets_are_aligned_to_utc_grid(self, store):
        series = store.timeseries(window_seconds=7200, bucket_seconds=3600)
        for row in series:
            ts = datetime.fromisoformat(row["bucket_ts"])
            assert ts.timestamp() % 3600 == 0

    def test_empty_buckets_are_filled_with_zero(self, store):
        series = store.timeseries(window_seconds=7200, bucket_seconds=3600)
        assert len(series) >= 2
        assert all(row[TIER_PAGE] == 0 for row in series)

    def test_counts_land_in_the_right_bucket(self, store):
        store.record(_decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a1")
        series = store.timeseries(window_seconds=7200, bucket_seconds=3600)
        assert sum(row[TIER_SUPPRESS] for row in series) == 1

    def test_series_is_chronological(self, store):
        series = store.timeseries(window_seconds=21600, bucket_seconds=3600)
        stamps = [datetime.fromisoformat(r["bucket_ts"]) for r in series]
        assert stamps == sorted(stamps)


class TestTopSuppressed:
    def test_groups_by_alarm_name_and_stage(self, store):
        for _ in range(3):
            store.record(
                _decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a", alarm_name="ntpd 감시"
            )
        store.record(
            _decision(TIER_SUPPRESS, STAGE_MATRIX), alarm_id="b", alarm_name="디스크 사용률"
        )
        top = store.top_suppressed()
        assert top[0]["alarm_name"] == "ntpd 감시"
        assert top[0]["count"] == 3
        assert top[0]["label"] == "플래핑"

    def test_notified_tiers_are_not_counted(self, store):
        store.record(_decision(TIER_PAGE, STAGE_MATRIX), alarm_id="a", alarm_name="통보됨")
        assert store.top_suppressed() == []

    def test_missing_alarm_name_is_kept_as_placeholder(self, store):
        store.record(_decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a")
        top = store.top_suppressed()
        assert top[0]["alarm_name"] == "(미기록)"

    def test_limit_is_respected(self, store):
        for i in range(5):
            store.record(
                _decision(TIER_SUPPRESS, STAGE_MATRIX), alarm_id=str(i), alarm_name=f"알람{i}"
            )
        assert len(store.top_suppressed(limit=2)) == 2


class TestListDecisions:
    @pytest.fixture()
    def seeded(self, store):
        store.record(
            _decision(TIER_PAGE, STAGE_SEVERITY3, "심각도3 — 항상 통보(억제 단계 미경유)"),
            alarm_id="a1", alarm_name="CPU 사용률 임계 초과", server_name="WEB-01",
        )
        store.record(
            _decision(TIER_SUPPRESS, STAGE_FLAPPING, "플래핑 — 상태 진동"),
            alarm_id="a2", alarm_name="ntpd 프로세스 감시", server_name="DB-02",
        )
        store.record(
            _decision(TIER_TICKET, STAGE_MATRIX, "매트릭스 — 최종 ticket"),
            alarm_id="a3", alarm_name="메모리 사용률", server_name="WEB-01",
        )
        return store

    def test_newest_first(self, seeded):
        items = seeded.list_decisions()["items"]
        assert [i["alarm_id"] for i in items] == ["a3", "a2", "a1"]

    def test_tier_filter(self, seeded):
        r = seeded.list_decisions(tier=TIER_SUPPRESS)
        assert r["total"] == 1 and r["items"][0]["alarm_id"] == "a2"

    def test_stage_filter(self, seeded):
        assert seeded.list_decisions(stage=STAGE_MATRIX)["total"] == 1

    def test_filters_combine_with_and(self, seeded):
        assert seeded.list_decisions(tier=TIER_PAGE, stage=STAGE_FLAPPING)["total"] == 0

    def test_query_matches_server_and_alarm_name(self, seeded):
        assert seeded.list_decisions(q="WEB-01")["total"] == 2
        assert seeded.list_decisions(q="ntpd")["total"] == 1

    def test_query_is_case_insensitive(self, seeded):
        assert seeded.list_decisions(q="web-01")["total"] == 2

    def test_pagination_boundaries(self, seeded):
        p1 = seeded.list_decisions(page=1, size=2)
        p2 = seeded.list_decisions(page=2, size=2)
        assert len(p1["items"]) == 2 and len(p2["items"]) == 1
        assert p1["total"] == p2["total"] == 3
        assert {i["alarm_id"] for i in p1["items"]} & {i["alarm_id"] for i in p2["items"]} == set()

    def test_stage_label_is_filled_for_legacy_records(self, store):
        _write_raw(store, [
            {"ts": datetime.now(timezone.utc).isoformat(), "alarm_id": "old",
             "tier": TIER_SUPPRESS, "reason": "유지보수 모드 — 신규 발송 억제(감사 기록)",
             "priority": 1, "signals": {}},
        ])
        item = store.list_decisions()["items"][0]
        assert item["stage"] == "maintenance"
        assert item["stage_label"] == "유지보수"

    def test_empty_store(self, store):
        r = store.list_decisions()
        assert r["total"] == 0 and r["items"] == []


class TestGetDecisionAndMasking:
    def test_returns_latest_for_duplicate_alarm_id(self, store):
        store.record(_decision(TIER_TICKET, STAGE_MATRIX, "첫 결정"), alarm_id="dup")
        store.record(_decision(TIER_PAGE, STAGE_SEVERITY3, "재통보 결정"), alarm_id="dup")
        d = store.get_decision("dup")
        assert d["reason"] == "재통보 결정"

    def test_missing_alarm_id_returns_none(self, store):
        assert store.get_decision("nope") is None
        assert store.get_decision("") is None

    def test_mask_fn_applies_to_strings_only(self, store):
        store.record(
            _decision(TIER_SUPPRESS, STAGE_FLAPPING, "사유"),
            alarm_id="a", alarm_name="비밀", server_name="서버",
        )
        d = store.get_decision("a", mask_fn=lambda s: "***")
        assert d["alarm_name"] == "***" and d["reason"] == "***"
        assert d["signals"]["severity"] == 2  # 숫자 신호는 판정 근거 — 가려지면 안 된다

    def test_without_mask_fn_content_is_untouched(self, store):
        store.record(_decision(TIER_SUPPRESS, STAGE_FLAPPING), alarm_id="a", alarm_name="원문")
        assert store.get_decision("a")["alarm_name"] == "원문"


class TestScanLimit:
    def test_max_lines_caps_the_scan_window(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"), True, max_lines=2)
        for i in range(5):
            store.record(_decision(TIER_PAGE, STAGE_MATRIX), alarm_id=f"a{i}")
        # 파일 끝 2줄만 되짚으므로 최근 2건만 보인다(감사 파일은 그대로 남는다).
        assert store.funnel()["raw"] == 2
        assert store.list_decisions()["total"] == 2
        assert sum(1 for _ in store.path.open(encoding="utf-8")) == 5


class TestDisabledStore:
    def test_all_read_paths_are_inert(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"), enabled=False)
        store.record(_decision(TIER_PAGE, STAGE_MATRIX), alarm_id="a")
        assert store.funnel()["raw"] == 0
        assert store.top_suppressed() == []
        assert store.list_decisions()["total"] == 0
        assert store.get_decision("a") is None
        assert all(
            row[TIER_PAGE] == 0
            for row in store.timeseries(window_seconds=3600, bucket_seconds=3600)
        )
