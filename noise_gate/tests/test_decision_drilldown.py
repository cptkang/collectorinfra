"""결정 저장소 드릴다운 조회 테스트 (plans/112 S2·S3·S5·S6·S7 — 저장소 계층).

관제 화면의 세부 리스트 패널이 기대는 저장소 계약을 고정한다:
    - `list_decisions`: 티어 집합 · facets 항등식 · 알람명 정확 일치 · since/until · related
      (대표/원 발생 판단) — 전부 **파일 1회 읽기**.
    - `_view`: 마스킹 확장(resource_name·condition_log·stage_evidence·related)과 운영자 전용 키.
    - `record`: 신규 식별·근거 필드(빈 값이면 키 없음 · condition_log 200자).
    - `timeseries(tz_offset_minutes=…)`: 로컬 격자 정렬 · 미지정은 종전 UTC 격자와 비트 동일.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

import noise_gate.infrastructure.decision_store as decision_store_module
from noise_gate.domain.notification_policy import (
    STAGE_CORRELATION,
    STAGE_FLAPPING,
    STAGE_MATRIX,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
)
from noise_gate.infrastructure.decision_store import DecisionStore

BASE = datetime.now(UTC) - timedelta(minutes=30)


def _at(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _rec(alarm_id: str, tier: str, stage: str, minutes: float, **over) -> dict:
    rec = {
        "ts": _at(minutes),
        "alarm_id": alarm_id,
        "tier": tier,
        "stage": stage,
        "reason": f"{stage} 사유",
        "priority": 1,
        "fingerprint": f"fp-{alarm_id}",
        "signals": {"severity": 2},
        "alarm_name": f"알람-{alarm_id}",
        "server_name": "WEB-01",
    }
    rec.update(over)
    return rec


def _write(store: DecisionStore, records: list[dict]) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with store.path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


@pytest.fixture()
def store(tmp_path) -> DecisionStore:
    return DecisionStore(str(tmp_path / "decisions.jsonl"))


@pytest.fixture()
def seeded(store) -> DecisionStore:
    _write(store, [
        _rec("p1", TIER_PAGE, STAGE_SEVERITY3, 1),
        _rec("p2", TIER_PAGE, STAGE_MATRIX, 2),
        _rec("t1", TIER_TICKET, STAGE_MATRIX, 3),
        _rec("d1", TIER_DASHBOARD, STAGE_MATRIX, 4),
        _rec("s1", TIER_SUPPRESS, STAGE_FLAPPING, 5),
        _rec("s2", TIER_SUPPRESS, STAGE_FLAPPING, 6, server_name="DB-02"),
        _rec("s3", TIER_SUPPRESS, STAGE_MATRIX, 7),
    ])
    return store


# ─── 티어 집합 · facets ──────────────────────────────────────────────────


class TestTierSetAndFacets:
    def test_single_string_tier_is_backward_compatible(self, seeded):
        assert seeded.list_decisions(tier=TIER_SUPPRESS)["total"] == 3

    def test_tier_set_is_or(self, seeded):
        r = seeded.list_decisions(tier={TIER_PAGE, TIER_TICKET})
        assert r["total"] == 3
        assert {i["tier"] for i in r["items"]} == {TIER_PAGE, TIER_TICKET}

    @pytest.mark.parametrize("empty", [None, "", set(), frozenset(), []])
    def test_empty_tier_means_no_filter(self, seeded, empty):
        assert seeded.list_decisions(tier=empty)["total"] == 7

    def test_facets_have_four_tier_keys_even_when_empty(self, store):
        facets = store.list_decisions()["facets"]
        assert facets == {
            "tiers": {TIER_PAGE: 0, TIER_TICKET: 0, TIER_DASHBOARD: 0, TIER_SUPPRESS: 0},
            "stages": {},
        }

    def test_identity_without_filters(self, seeded):
        r = seeded.list_decisions()
        assert sum(r["facets"]["tiers"].values()) == r["total"] == 7
        assert sum(r["facets"]["stages"].values()) == r["total"]
        assert r["facets"]["stages"] == {STAGE_SEVERITY3: 1, STAGE_MATRIX: 4, STAGE_FLAPPING: 2}

    def test_identity_with_tier_filter(self, seeded):
        tiers = {TIER_PAGE, TIER_TICKET}
        r = seeded.list_decisions(tier=tiers)
        # tiers facet은 tier 필터만 뺀 집합 — 칩이 "다른 티어로 바꾸면 몇 건"을 보여준다.
        assert r["facets"]["tiers"] == {
            TIER_PAGE: 2, TIER_TICKET: 1, TIER_DASHBOARD: 1, TIER_SUPPRESS: 3,
        }
        assert sum(r["facets"]["tiers"][t] for t in tiers) == r["total"]
        assert sum(r["facets"]["stages"].values()) == r["total"]  # stage 필터 없음

    def test_identity_with_stage_filter(self, seeded):
        r = seeded.list_decisions(stage=STAGE_MATRIX)
        assert r["facets"]["stages"][STAGE_MATRIX] == r["total"] == 4
        assert sum(r["facets"]["tiers"].values()) == r["total"]  # tier 필터 없음
        # stages facet은 stage 필터만 뺀 집합이라 다른 단계도 센다.
        assert r["facets"]["stages"][STAGE_FLAPPING] == 2

    def test_identity_with_both_filters(self, seeded):
        r = seeded.list_decisions(tier=TIER_SUPPRESS, stage=STAGE_FLAPPING)
        assert r["total"] == 2
        assert r["facets"]["tiers"][TIER_SUPPRESS] == r["total"]
        assert r["facets"]["stages"][STAGE_FLAPPING] == r["total"]
        assert r["facets"]["stages"] == {STAGE_FLAPPING: 2, STAGE_MATRIX: 1}

    def test_facets_respect_other_filters(self, seeded):
        # q 필터는 facets에도 걸린다 — 칩 합계가 목록과 다른 모집단이면 안 된다.
        r = seeded.list_decisions(q="DB-02")
        assert r["total"] == 1
        assert r["facets"]["tiers"][TIER_SUPPRESS] == 1
        assert sum(r["facets"]["tiers"].values()) == 1

    def test_facets_ignore_pagination(self, seeded):
        r = seeded.list_decisions(size=2, page=2)
        assert len(r["items"]) == 2
        assert sum(r["facets"]["tiers"].values()) == 7

    def test_list_reads_the_file_once(self, seeded, monkeypatch):
        calls = []
        original = DecisionStore._tail_records

        def _counting(self, window_seconds):  # noqa: ANN001, ANN202
            calls.append(window_seconds)
            return original(self, window_seconds)

        monkeypatch.setattr(DecisionStore, "_tail_records", _counting)
        seeded.list_decisions(window_seconds=3600, related=True, tier={TIER_SUPPRESS})
        assert len(calls) == 1


# ─── 알람명 정확 일치 · 구간 ─────────────────────────────────────────────


class TestExactNameAndInterval:
    def test_alarm_name_is_exact(self, seeded):
        assert seeded.list_decisions(alarm_name="알람-s1")["total"] == 1
        assert seeded.list_decisions(alarm_name="알람-")["total"] == 0  # 부분일치 아님
        assert seeded.list_decisions(alarm_name="")["total"] == 7       # 빈 값 = 필터 없음

    def test_since_inclusive_until_exclusive(self, seeded):
        since = BASE + timedelta(minutes=3)
        until = BASE + timedelta(minutes=6)
        r = seeded.list_decisions(since=since, until=until)
        assert [i["alarm_id"] for i in r["items"]] == ["s1", "d1", "t1"]

    def test_interval_is_and_with_window(self, store):
        old = datetime.now(UTC) - timedelta(hours=3)
        _write(store, [
            _rec("old", TIER_PAGE, STAGE_MATRIX, 0, ts=old.isoformat()),
            _rec("new", TIER_PAGE, STAGE_MATRIX, 1),
        ])
        r = store.list_decisions(
            window_seconds=3600, since=old - timedelta(minutes=1), until=datetime.now(UTC)
        )
        assert [i["alarm_id"] for i in r["items"]] == ["new"]  # 창 밖은 여전히 제외

    def test_unreadable_ts_is_excluded_only_under_interval(self, store):
        _write(store, [_rec("bad", TIER_PAGE, STAGE_MATRIX, 0, ts="not-a-time")])
        assert store.list_decisions()["total"] == 1
        assert store.list_decisions(since=BASE)["total"] == 0


# ─── related ─────────────────────────────────────────────────────────────


class TestRelated:
    def _corr(self, alarm_id: str, minutes: float, rep_fp: str) -> dict:
        return _rec(
            alarm_id, TIER_SUPPRESS, STAGE_CORRELATION, minutes,
            correlation_meta={"representative_fp": rep_fp, "member_seq": 3, "similarity": 0.8},
        )

    def test_representative_is_latest_at_or_before(self, store):
        _write(store, [
            _rec("rep-old", TIER_PAGE, STAGE_MATRIX, 1, fingerprint="fp-rep"),
            _rec("rep", TIER_PAGE, STAGE_MATRIX, 2, fingerprint="fp-rep", server_name="AP-01"),
            self._corr("m1", 3, "fp-rep"),
            _rec("rep-later", TIER_PAGE, STAGE_MATRIX, 4, fingerprint="fp-rep"),
        ])
        item = next(
            i for i in store.list_decisions(related=True)["items"] if i["alarm_id"] == "m1"
        )
        assert item["related"] == {
            "kind": "representative", "found": True, "alarm_id": "rep",
            "alarm_name": "알람-rep", "server_name": "AP-01", "ts": _at(2),
            "tier": TIER_PAGE, "stage": STAGE_MATRIX,
        }

    def test_representative_outside_window_but_in_tail_is_found(self, store):
        old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        _write(store, [
            _rec("rep", TIER_PAGE, STAGE_MATRIX, 0, fingerprint="fp-rep", ts=old),
            self._corr("m1", 3, "fp-rep"),
        ])
        r = store.list_decisions(window_seconds=3600, related=True)
        assert r["total"] == 1
        assert r["items"][0]["related"]["found"] is True
        assert r["items"][0]["related"]["alarm_id"] == "rep"

    def test_representative_outside_tail_is_explicitly_not_found(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"), True, max_lines=1)
        _write(store, [
            _rec("rep", TIER_PAGE, STAGE_MATRIX, 0, fingerprint="fp-rep"),
            self._corr("m1", 3, "fp-rep"),
        ])
        item = store.list_decisions(related=True)["items"][0]
        assert item["related"] == {"kind": "representative", "found": False}

    def test_missing_correlation_meta_is_not_found(self, store):
        _write(store, [_rec("m1", TIER_SUPPRESS, STAGE_CORRELATION, 3)])
        item = store.list_decisions(related=True)["items"][0]
        assert item["related"] == {"kind": "representative", "found": False}

    def test_origin_is_latest_prior_firing(self, store):
        _write(store, [
            _rec("f-old", TIER_TICKET, STAGE_MATRIX, 1, fingerprint="fp-x",
                 signals={"severity": 1}),
            _rec("f", TIER_DASHBOARD, STAGE_MATRIX, 2, fingerprint="fp-x",
                 signals={"severity": 2}),
            _rec("clear-prev", TIER_SUPPRESS, STAGE_MATRIX, 3, fingerprint="fp-x",
                 signals={"severity": 0}),
            _rec("heal", TIER_SUPPRESS, STAGE_SELF_HEAL, 4, fingerprint="fp-x",
                 signals={"severity": 0}),
            _rec("f-after", TIER_TICKET, STAGE_MATRIX, 5, fingerprint="fp-x",
                 signals={"severity": 1}),
        ])
        item = next(
            i for i in store.list_decisions(related=True)["items"] if i["alarm_id"] == "heal"
        )
        assert item["related"]["kind"] == "origin"
        assert item["related"]["found"] is True
        assert item["related"]["alarm_id"] == "f"  # 해소(0)·이후 발생은 원 발생이 아니다

    def test_origin_not_found(self, store):
        _write(store, [_rec("heal", TIER_SUPPRESS, STAGE_SELF_HEAL, 4, signals={"severity": 0})])
        item = store.list_decisions(related=True)["items"][0]
        assert item["related"] == {"kind": "origin", "found": False}

    def test_related_only_on_correlation_and_self_heal_rows(self, seeded):
        items = seeded.list_decisions(related=True)["items"]
        assert all("related" not in i for i in items)

    def test_related_off_by_default(self, store):
        _write(store, [self._corr("m1", 3, "fp-rep")])
        assert "related" not in store.list_decisions()["items"][0]

    def test_related_strings_are_masked(self, store):
        _write(store, [
            _rec("rep", TIER_PAGE, STAGE_MATRIX, 2, fingerprint="fp-rep"),
            self._corr("m1", 3, "fp-rep"),
        ])
        item = store.list_decisions(related=True, mask_fn=lambda s: "***")["items"][0]
        assert item["related"]["alarm_name"] == "***"
        assert item["related"]["server_name"] == "***"
        assert item["related"]["alarm_id"] == "rep"  # 식별자는 조회 키라 가리지 않는다
        assert item["related"]["found"] is True


# ─── 뷰 · 운영자 전용 키 · 마스킹 ─────────────────────────────────────────


class TestViewMaskingAndOperatorFields:
    def _seed_s6(self, store) -> None:
        _write(store, [_rec(
            "a1", TIER_SUPPRESS, STAGE_FLAPPING, 1,
            db_id="db1", resource_name="SECRET-res", condition_log="SECRET-log 97%",
            stage_evidence={"flap_percent": 62.5, "note": "SECRET-n",
                            "markers": ["SECRET-a", "ok"]},
        )])

    @staticmethod
    def _mask(text: str) -> str:
        return "***" if text.startswith("SECRET") else text

    def test_condition_log_is_operator_only_by_default(self, store):
        self._seed_s6(store)
        assert "condition_log" not in store.list_decisions()["items"][0]  # fail-closed 기본
        assert "condition_log" not in store.get_decision("a1")
        assert store.list_decisions(operator_view=True)["items"][0]["condition_log"]
        assert store.get_decision("a1", operator_view=True)["condition_log"]

    def test_new_fields_are_masked(self, store):
        self._seed_s6(store)
        item = store.list_decisions(mask_fn=self._mask, operator_view=True)["items"][0]
        assert item["resource_name"] == "***"
        assert item["condition_log"] == "***"
        assert item["stage_evidence"] == {
            "flap_percent": 62.5, "note": "***", "markers": ["***", "ok"],
        }
        assert item["db_id"] == "db1"

    def test_masking_does_not_mutate_the_record_file(self, store):
        self._seed_s6(store)
        store.list_decisions(mask_fn=self._mask, operator_view=True)
        raw = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
        assert raw["stage_evidence"]["markers"] == ["SECRET-a", "ok"]


# ─── record 신규 필드 ────────────────────────────────────────────────────


def _decision(**over) -> NotificationDecision:
    base = dict(tier=TIER_SUPPRESS, reason="r", priority=1, signals={"severity": 2},
                fingerprint="fp", stage=STAGE_FLAPPING)
    base.update(over)
    return NotificationDecision(**base)


class TestRecordNewFields:
    def _last(self, store) -> dict:
        return json.loads(store.path.read_text(encoding="utf-8").splitlines()[-1])

    def test_legacy_call_writes_the_same_keys(self, store):
        store.record(_decision(), alarm_id="a1")
        assert set(self._last(store)) == {
            "ts", "alarm_id", "tier", "reason", "priority", "fingerprint", "signals", "stage",
        }

    def test_blank_values_add_no_keys(self, store):
        store.record(_decision(), alarm_id="a1", stage_evidence={}, db_id="",
                     resource_name="", condition_log="")
        assert not {"stage_evidence", "db_id", "resource_name", "condition_log"} & set(
            self._last(store)
        )

    def test_values_are_recorded_and_condition_log_is_capped(self, store):
        store.record(_decision(), alarm_id="a1", stage_evidence={"samples": 12},
                     db_id="db1", resource_name="r1", condition_log="가" * 250)
        rec = self._last(store)
        assert rec["stage_evidence"] == {"samples": 12}
        assert rec["db_id"] == "db1" and rec["resource_name"] == "r1"
        assert rec["condition_log"] == "가" * 200


# ─── timeseries 로컬 격자 ────────────────────────────────────────────────


FROZEN = datetime(2026, 9, 22, 4, 37, 12, 345678, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return FROZEN if tz is not None else FROZEN.replace(tzinfo=None)


@pytest.fixture()
def frozen(monkeypatch):
    monkeypatch.setattr(decision_store_module, "datetime", _FrozenDatetime)
    return FROZEN


def _legacy_grid(window: int, bucket: int) -> list[str]:
    """변경 전 UTC 격자 산식 — 미지정 응답이 비트 동일함을 대조하는 기준."""
    now = FROZEN.timestamp()
    start = int((now - window) // bucket * bucket)
    end = int(now // bucket * bucket)
    return [
        datetime.fromtimestamp(ts, UTC).isoformat()
        for ts in range(start, end + bucket, bucket)
    ]


class TestTimeseriesLocalGrid:
    @pytest.mark.parametrize("window,bucket", [
        (3600, 300), (86400, 7200), (604800, 21600), (2592000, 86400),
    ])
    def test_unspecified_offset_is_the_legacy_utc_grid(self, store, frozen, window, bucket):
        store.record(_decision(), alarm_id="a1", ts=FROZEN - timedelta(minutes=5))
        default = store.timeseries(window_seconds=window, bucket_seconds=bucket)
        explicit_none = store.timeseries(
            window_seconds=window, bucket_seconds=bucket, tz_offset_minutes=None
        )
        assert default == explicit_none
        assert [p["bucket_ts"] for p in default] == _legacy_grid(window, bucket)
        assert sum(p[TIER_SUPPRESS] for p in default) == 1

    def test_kst_two_hour_buckets_start_on_even_local_hours(self, store, frozen):
        series = store.timeseries(window_seconds=86400, bucket_seconds=7200,
                                  tz_offset_minutes=540)
        kst = timezone(timedelta(hours=9))
        for point in series:
            boundary = datetime.fromisoformat(point["bucket_ts"])
            assert boundary.utcoffset() == timedelta(0)  # 여전히 UTC 표기
            local = boundary.astimezone(kst)
            assert local.hour % 2 == 0 and local.minute == 0 and local.second == 0
            assert (boundary.timestamp() + 540 * 60) % 7200 == 0

    def test_kst_daily_buckets_start_at_local_midnight(self, store, frozen):
        series = store.timeseries(window_seconds=604800, bucket_seconds=86400,
                                  tz_offset_minutes=540)
        kst = timezone(timedelta(hours=9))
        assert all(
            datetime.fromisoformat(p["bucket_ts"]).astimezone(kst).hour == 0 for p in series
        )

    def test_counts_land_in_the_local_bucket(self, store, frozen):
        # KST 12:10(= UTC 03:10) 판단은 KST 12:00–14:00 구간(UTC 03:00 경계)에 든다.
        store.record(_decision(), alarm_id="a1",
                     ts=datetime(2026, 9, 22, 3, 10, tzinfo=UTC))
        series = store.timeseries(window_seconds=86400, bucket_seconds=7200,
                                  tz_offset_minutes=540)
        hit = [p for p in series if p[TIER_SUPPRESS]]
        assert [p["bucket_ts"] for p in hit] == [
            datetime(2026, 9, 22, 3, 0, tzinfo=UTC).isoformat()
        ]

    def test_zero_offset_equals_utc_grid(self, store, frozen):
        assert store.timeseries(window_seconds=86400, bucket_seconds=7200,
                                tz_offset_minutes=0) == store.timeseries(
            window_seconds=86400, bucket_seconds=7200)
