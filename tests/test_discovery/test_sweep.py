"""존 순회 실행 + TTL 캐시 (Plan 82 W5-T2 · SPEC-host-discovery).

조회는 **주입 대역**으로 대체한다 — DB 미가동(DBHub localhost:9099 CLOSED)이고,
이 모듈의 계약은 "무엇을 어떤 순서로 몇 번 호출하는가"라 호출 기록으로 전량 검증된다.

★ 이 파일이 지키는 세 규약: 인가된 존만 · 전수 순회 · **0건 미캐시**.
"""

from __future__ import annotations

import pytest

from src.domain.host_availability import judge_availability
from src.orchestration.host_sweep import (
    authorized_zones,
    clear_cache,
    sweep_order,
    sweep_zones,
)

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


class _Lookup:
    """호출을 기록하는 조회 대역. `found`에 있는 db_id에서만 히트를 낸다."""

    def __init__(self, found=(), *, failed=(), raises=()):
        self.found = set(found)
        self.failed = set(failed)
        self.raises = set(raises)
        self.calls: list[str] = []

    async def __call__(self, db_id: str, identifier: str):
        self.calls.append(db_id)
        if db_id in self.raises:
            raise RuntimeError("boom")
        if db_id in self.failed:
            return _Result(None, None, judge_availability(lookup_failed=True))
        if db_id in self.found:
            return _Result(f"{identifier}.{db_id}", identifier.upper(),
                           judge_availability(found=True, avail_status=0))
        return _Result(None, None, judge_availability(found=False))


class _Result:
    def __init__(self, hostname, server_name, availability):
        self.hostname = hostname
        self.server_name = server_name
        self.availability = availability


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


class TestOrder:
    def test_query_order_is_canonical(self):
        """은행존 → 공동존. 입력 배열 순서에 의존하지 않는다."""
        assert sweep_order(ALL_DBS)[0] == "polestar_b0"
        assert sweep_order(list(reversed(ALL_DBS))) == sweep_order(ALL_DBS)

    def test_unregistered_db_is_dropped(self):
        assert "nope" not in sweep_order(["nope", "polestar_b0"])


class TestAuthorization:
    def test_none_means_all_allowed(self):
        assert authorized_zones(ALL_DBS, None) == sweep_order(ALL_DBS)

    def test_empty_list_means_none_allowed(self):
        """★ 빈 리스트는 '전체 허용'이 아니다 — 허용 0건이다."""
        assert authorized_zones(ALL_DBS, []) == []

    def test_only_authorized_zones_are_swept(self):
        assert authorized_zones(ALL_DBS, ["polestar_cm_gp"]) == ["polestar_cm_gp"]

    @pytest.mark.asyncio
    async def test_unauthorized_zone_is_never_queried(self):
        """★ 권한 밖 존은 **조회조차 하지 않는다** — 순회 사실 자체가 정보 누출이다."""
        lookup = _Lookup(found={"polestar_b0"})

        await sweep_zones("abd00", ALL_DBS, lookup=lookup,
                          allowed_db_ids=["polestar_cm_gp"])

        assert lookup.calls == ["polestar_cm_gp"]


class TestSweep:
    @pytest.mark.asyncio
    async def test_full_sweep_visits_every_zone(self):
        """U4 — 첫 히트에서 멈추지 않는다."""
        lookup = _Lookup(found={"polestar_b0", "polestar_cm_yd"})

        outcome = await sweep_zones("abd00", ALL_DBS, lookup=lookup)

        assert lookup.calls == ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
        assert len(outcome.hits) == 2, "동명 호스트 2건이 은폐되지 않는다"

    @pytest.mark.asyncio
    async def test_early_exit_stops_at_first_hit(self):
        lookup = _Lookup(found={"polestar_b0", "polestar_cm_yd"})

        outcome = await sweep_zones("abd00", ALL_DBS, lookup=lookup, early_exit=True)

        assert lookup.calls == ["polestar_b0"]
        assert len(outcome.hits) == 1

    @pytest.mark.asyncio
    async def test_swept_labels_are_recorded(self):
        outcome = await sweep_zones("abd00", ALL_DBS, lookup=_Lookup())

        assert outcome.swept == ("은행존", "공동존 김포", "공동존 여의도")
        assert outcome.hits == ()

    @pytest.mark.asyncio
    async def test_hit_carries_zone_label_and_availability(self):
        outcome = await sweep_zones("abd00", ALL_DBS, lookup=_Lookup(found={"polestar_cm_gp"}))

        (hit,) = outcome.hits
        assert hit.zone_label == "공동존 김포"
        assert hit.hostname == "abd00.polestar_cm_gp"
        assert hit.availability is not None


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_lookup_failure_is_recorded_not_treated_as_absent(self):
        """★ 조회 실패는 '없음'이 아니다."""
        lookup = _Lookup(found={"polestar_cm_yd"}, failed={"polestar_b0"})

        outcome = await sweep_zones("abd00", ALL_DBS, lookup=lookup)

        assert outcome.errors == {"은행존": "조회 실패"}
        assert len(outcome.hits) == 1

    @pytest.mark.asyncio
    async def test_exception_in_one_zone_does_not_stop_the_sweep(self):
        lookup = _Lookup(found={"polestar_cm_yd"}, raises={"polestar_b0"})

        outcome = await sweep_zones("abd00", ALL_DBS, lookup=lookup)

        assert lookup.calls == ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
        assert outcome.errors == {"은행존": "RuntimeError"}
        assert len(outcome.hits) == 1


class TestCache:
    @pytest.mark.asyncio
    async def test_hit_is_cached_within_ttl(self):
        lookup = _Lookup(found={"polestar_cm_gp"})

        first = await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1000.0)
        second = await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1030.0)

        assert len(lookup.calls) == 3, "두 번째 호출은 순회하지 않는다"
        assert second is first

    @pytest.mark.asyncio
    async def test_cache_expires(self):
        lookup = _Lookup(found={"polestar_cm_gp"})

        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1000.0)
        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1061.0)

        assert len(lookup.calls) == 6

    @pytest.mark.asyncio
    async def test_not_found_is_never_cached(self):
        """★ 0건을 캐시하면 방금 등록한 서버가 TTL 동안 '없는 서버'가 된다."""
        lookup = _Lookup()

        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1000.0)
        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1001.0)

        assert len(lookup.calls) == 6

    @pytest.mark.asyncio
    async def test_partial_failure_is_never_cached(self):
        """조회 실패를 캐시하면 일시 장애가 TTL 동안 고정된 사실이 된다."""
        lookup = _Lookup(found={"polestar_cm_gp"}, failed={"polestar_b0"})

        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1000.0)
        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1001.0)

        assert len(lookup.calls) == 6

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_cache(self):
        lookup = _Lookup(found={"polestar_cm_gp"})

        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=0, now=1000.0)
        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=0, now=1000.0)

        assert len(lookup.calls) == 6

    @pytest.mark.asyncio
    async def test_cache_key_includes_zone_set(self):
        """인가 범위가 다르면 다른 캐시 항목이다 — 권한 축소가 캐시로 우회되면 안 된다."""
        lookup = _Lookup(found={"polestar_cm_gp"})

        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1000.0)
        await sweep_zones("abd00", ALL_DBS, lookup=lookup, ttl_seconds=60, now=1001.0,
                          allowed_db_ids=["polestar_cm_gp"])

        assert len(lookup.calls) == 4
