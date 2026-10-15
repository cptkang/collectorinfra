"""plans/166 W1 (noise_gate 분) — 동작 불변 구조 리팩터의 고정 테스트.

검증 항목:
    X1. 설정 폴백 → 직접 접근: 부모 설정이 없는(None) 경로는 현행대로 조용히 None
    G4. `is_apm_source`·`is_apm_db_id` domain 이동 재노출 · `_unavailable` 공개명 별칭(D-337 ①③)
    X5. 서버 식별 캐시 조회 침묵 핸들러에 경고 로그 — 반환값(None)은 그대로
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from noise_gate.application import server_identity
from noise_gate.application.nodes.investigation_trigger import _resolve_target_state
from noise_gate.domain import alarm as domain_alarm
from noise_gate.infrastructure import polestar_noise_context
from noise_gate.infrastructure.sre_agent_client import build_sre_agent_client


# ── X1 ──────────────────────────────────────────────────────────────────────
class TestConfigDirectAccess:
    def test_sre_client_none_gate_cfg_is_silent_none(self, caplog):
        caplog.set_level(logging.DEBUG, logger="noise_gate.infrastructure.sre_agent_client")
        assert build_sre_agent_client(None) is None
        assert caplog.records == []

    def test_sre_client_blank_url_is_none(self):
        assert build_sre_agent_client(SimpleNamespace(investigation_service_url="")) is None

    async def test_target_state_without_composite_is_none(self):
        assert await _resolve_target_state(SimpleNamespace(), SimpleNamespace(), None) is None

    async def test_target_state_flag_off_is_none(self):
        cfg = SimpleNamespace(composite=SimpleNamespace(availability_precheck_enabled=False))
        assert await _resolve_target_state(cfg, SimpleNamespace(), None) is None


# ── G4 ──────────────────────────────────────────────────────────────────────
class TestMovedNames:
    def test_server_identity_reexports_domain_functions(self):
        assert server_identity.is_apm_source is domain_alarm.is_apm_source
        assert server_identity.is_apm_db_id is domain_alarm.is_apm_db_id

    def test_is_apm_db_id_rule_unchanged(self):
        assert domain_alarm.is_apm_db_id("jennifer") is True
        assert domain_alarm.is_apm_db_id("jennifer_bank") is True
        assert domain_alarm.is_apm_db_id("jenniferbank") is False
        assert domain_alarm.is_apm_db_id("") is False

    def test_unavailable_alias_is_public_name(self):
        assert polestar_noise_context._unavailable is polestar_noise_context.unavailable_noise_ctx
        ctx = polestar_noise_context.unavailable_noise_ctx()
        assert ctx["source"] == "unavailable"
        assert set(ctx) == set(polestar_noise_context._NOISE_CTX_KEYS) | {"source"}


# ── X5 ──────────────────────────────────────────────────────────────────────
class _RaisingRedis:
    async def get(self, key: str):  # noqa: ANN201
        raise ConnectionError("redis down")


class _BrokenRedis:
    async def get(self, key: str) -> bytes:
        return b"{not json"


class TestCacheGetWarns:
    async def test_get_failure_warns_and_returns_none(self, caplog):
        caplog.set_level(logging.WARNING, logger=server_identity.__name__)
        assert await server_identity._cache_get(_RaisingRedis(), "k1") is None
        recs = [r for r in caplog.records if "캐시 조회 실패" in r.getMessage()]
        assert len(recs) == 1 and recs[0].exc_info is not None
        assert "key=k1" in recs[0].getMessage()

    async def test_corrupt_cache_warns_and_returns_none(self, caplog):
        caplog.set_level(logging.WARNING, logger=server_identity.__name__)
        assert await server_identity._cache_get(_BrokenRedis(), "k2") is None
        recs = [r for r in caplog.records if "캐시 손상" in r.getMessage()]
        assert len(recs) == 1 and recs[0].exc_info is not None
