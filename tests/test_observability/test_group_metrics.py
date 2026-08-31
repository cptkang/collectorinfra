"""그룹 계측 테스트 (D-176 · plans/82 §5.5 · SPEC-group-runner T14).

핵심 계약: **표본이 부족하면 시간 추정치를 만들지 않는다**(§5.5 S-C). 근거 없는 숫자를
사용자에게 보여주는 것은 환각이고, 근거 없는 임계가 무기한 실동작한 전례(D-174 ②)를
반복하지 않기 위한 장치다.
"""

from __future__ import annotations

import pytest

from src.observability import group_metrics as gm

_PEER = {"solution": "polestar", "zone_group": "bank", "kind": "peer", "backend": "sql"}
_PEER2 = {"solution": "polestar", "zone_group": "common", "kind": "peer", "backend": "sql"}
_DISCOVERY = {"solution": "polestar", "zone_group": "", "kind": "discovery", "backend": "sql"}


@pytest.fixture(autouse=True)
def _clean():
    gm.reset()
    yield
    gm.reset()


class TestRecording:
    def test_records_and_counts(self):
        for _ in range(5):
            gm.record_group(_PEER, 1000.0)
        assert gm.group_stats(_PEER)["sample_size"] == 5

    def test_types_are_separated(self):
        gm.record_group(_PEER, 1000.0)
        gm.record_group(_PEER2, 2000.0)
        assert gm.group_stats(_PEER)["sample_size"] == 1
        assert gm.group_stats(_PEER2)["sample_size"] == 1

    @pytest.mark.parametrize("bad", [None, "x", float("nan") and "x", -1])
    def test_invalid_values_ignored(self, bad):
        gm.record_group(_PEER, bad)
        assert gm.group_stats(_PEER)["sample_size"] == 0

    def test_bounded_memory(self):
        for i in range(gm._MAX_SAMPLES + 50):
            gm.record_group(_PEER, float(i))
        assert gm.group_stats(_PEER)["sample_size"] == gm._MAX_SAMPLES

    def test_percentiles(self):
        for v in range(1, 101):
            gm.record_group(_PEER, float(v) * 100)
        st = gm.group_stats(_PEER)
        assert 4000 <= st["p50_ms"] <= 6000
        assert 8000 <= st["p90_ms"] <= 10000


class TestEstimateReadiness:
    """표본 부족 시 숫자를 만들지 않는다 — §5.5 S-C."""

    def test_not_ready_below_threshold(self):
        for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE - 1):
            gm.record_group(_PEER, 1000.0)
        assert gm.group_stats(_PEER)["estimate_ready"] is False

    def test_ready_at_threshold(self):
        for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE):
            gm.record_group(_PEER, 1000.0)
        assert gm.group_stats(_PEER)["estimate_ready"] is True

    def test_estimate_returns_none_when_not_ready(self):
        est = gm.estimate_seconds([_PEER, _PEER2])
        assert est["ready"] is False
        assert est["seconds_lo"] is None and est["seconds_hi"] is None
        assert est["groups"] == 2       # 그룹 수는 표본과 무관하게 말할 수 있다

    def test_estimate_sums_when_ready(self):
        for g in (_PEER, _PEER2):
            for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE):
                gm.record_group(g, 5000.0)
        est = gm.estimate_seconds([_PEER, _PEER2])
        assert est["ready"] is True
        assert est["seconds_lo"] == pytest.approx(10.0)

    def test_one_unready_group_blocks_the_estimate(self):
        """한 그룹이라도 근거가 없으면 전체 추정을 내지 않는다."""
        for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE):
            gm.record_group(_PEER, 5000.0)
        assert gm.estimate_seconds([_PEER, _PEER2])["ready"] is False


class TestDiscoveryExcluded:
    """탐색 그룹은 비용 산정에서 제외한다 — 존당 ~50ms 실측(§5.1)."""

    def test_discovery_not_counted(self):
        for _ in range(gm.MIN_SAMPLES_FOR_ESTIMATE):
            gm.record_group(_PEER, 5000.0)
        est = gm.estimate_seconds([_DISCOVERY, _PEER])
        assert est["groups"] == 1
        assert est["ready"] is True

    def test_discovery_only_is_not_billable(self):
        est = gm.estimate_seconds([_DISCOVERY])
        assert est["groups"] == 0 and est["ready"] is False

    def test_empty(self):
        assert gm.estimate_seconds([])["groups"] == 0
        assert gm.estimate_seconds(None)["groups"] == 0


class TestSnapshot:
    def test_snapshot_lists_types(self):
        gm.record_group(_PEER, 1000.0)
        gm.record_group(_PEER2, 2000.0)
        snap = gm.snapshot()
        assert len(snap) == 2
        assert all("sample_size" in v for v in snap.values())
