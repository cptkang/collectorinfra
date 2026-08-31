"""탐색 배선 — `_resolve_db_id` ⑤ (Plan 82 W5-T3 · SPEC-host-discovery).

★ 이 파일이 지키는 계약: **①~④가 성립하면 탐색은 호출조차 되지 않는다.** 탐색이 앞
순위를 덮으면 존 선택 UI 확정(D-143)·선행 결과(D-176)가 무력화된다.

DB·LLM 0(D-127) — 조회는 대역 주입.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config import AppConfig, CompositeConfig, MultiDBConfig
from src.domain.host_availability import judge_availability
from src.orchestration.host_sweep import clear_cache
from src.orchestration.process_query import _discover_db_id

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


class _Result:
    def __init__(self, hostname=None, server_name=None, availability=None):
        self.hostname = hostname
        self.server_name = server_name
        self.availability = availability or judge_availability(found=False)


def _config(*, enabled: bool = True, early_exit: bool = False, ttl: float = 60.0,
            active=None) -> AppConfig:
    """검증 대상 필드를 **명시**해 `.env` 누수를 막는다(Known Mistakes)."""
    config = AppConfig()
    config.composite = CompositeConfig(
        host_discovery_enabled=enabled,
        discovery_early_exit=early_exit,
        discovery_cache_ttl_seconds=ttl,
    )
    config.multi_db = MultiDBConfig(
        active_db_ids_csv=",".join(ALL_DBS if active is None else active)
    )
    return config


@pytest.fixture(autouse=True)
def _clean():
    clear_cache()
    yield
    clear_cache()


def _patch_lookup(found=(), failed=()):
    calls: list[str] = []

    async def _fake(db_id, value, app_config):
        calls.append(db_id)
        if db_id in failed:
            return _Result(availability=judge_availability(lookup_failed=True))
        if db_id in found:
            return _Result(hostname=f"{value}.{db_id}", server_name=value.upper(),
                           availability=judge_availability(found=True, avail_status=0))
        return _Result()

    return patch(
        "src.orchestration.process_query._resolve_target_lookup", side_effect=_fake,
    ), calls


class TestFlagGate:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_and_never_looks_up(self):
        ctx, calls = _patch_lookup(found={"polestar_cm_gp"})
        with ctx:
            db_id, payload = await _discover_db_id("abd00", {}, _config(enabled=False))

        assert (db_id, payload) == (None, None)
        assert calls == [], "플래그 off면 조회 0회 — 현행 안내 문구가 그대로 나간다"

    @pytest.mark.asyncio
    async def test_no_sweepable_zone_falls_back_to_current_message(self):
        """단일 DB 배포(`ACTIVE_DB_IDS=polestar`)는 존이 없어 탐색이 무의미하다."""
        ctx, calls = _patch_lookup()
        with ctx:
            db_id, payload = await _discover_db_id(
                "abd00", {}, _config(active=["polestar"]),
            )

        assert (db_id, payload) == (None, None)
        assert calls == []


class TestOutcomes:
    @pytest.mark.asyncio
    async def test_single_hit_resolves_db_id(self):
        ctx, calls = _patch_lookup(found={"polestar_cm_gp"})
        with ctx:
            db_id, payload = await _discover_db_id("abd00", {}, _config())

        assert db_id == "polestar_cm_gp"
        assert payload["state"] == "resolved"
        assert calls == ALL_DBS, "전수 순회(U4)"

    @pytest.mark.asyncio
    async def test_multiple_hits_ask_back_without_choosing(self):
        ctx, _ = _patch_lookup(found={"polestar_b0", "polestar_cm_yd"})
        with ctx:
            db_id, payload = await _discover_db_id("abd00", {}, _config())

        assert db_id is None, "임의 선택 금지(U5)"
        assert payload["state"] == "ambiguous"
        assert "은행존" in payload["message"] and "공동존 여의도" in payload["message"]

    @pytest.mark.asyncio
    async def test_not_found_names_every_swept_zone(self):
        ctx, _ = _patch_lookup()
        with ctx:
            db_id, payload = await _discover_db_id("abd00", {}, _config())

        assert db_id is None
        assert payload["state"] == "not_found"
        for label in ("은행존", "공동존 김포", "공동존 여의도"):
            assert label in payload["message"]

    @pytest.mark.asyncio
    async def test_lookup_failure_is_distinguished_from_absence(self):
        ctx, _ = _patch_lookup(failed={"polestar_b0"})
        with ctx:
            _db_id, payload = await _discover_db_id("abd00", {}, _config())

        assert "조회 자체가 실패" in payload["message"]
        assert payload["trace"]["errors"] == {"은행존": "조회 실패"}


class TestAuthorization:
    @pytest.mark.asyncio
    async def test_only_authorized_zones_are_queried(self):
        ctx, calls = _patch_lookup(found={"polestar_b0"})
        with ctx:
            db_id, _payload = await _discover_db_id(
                "abd00", {"allowed_db_ids": ["polestar_cm_gp"]}, _config(),
            )

        assert calls == ["polestar_cm_gp"], "권한 밖 존은 조회조차 하지 않는다"
        assert db_id is None


class TestTrace:
    @pytest.mark.asyncio
    async def test_trace_is_json_serializable(self):
        import json

        ctx, _ = _patch_lookup(found={"polestar_cm_gp"})
        with ctx:
            _db_id, payload = await _discover_db_id("abd00", {}, _config())

        json.dumps(payload["trace"])  # 체크포인터 직렬화 대상
        assert payload["trace"]["swept"] == ["은행존", "공동존 김포", "공동존 여의도"]
