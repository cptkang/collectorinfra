"""plans/197 W1(F1) — 제니퍼 알람의 폴스타 전용 수집 경로 명시 생략.

판정·기록 모양은 그대로 두고, 알람마다 쌓이던 「미등록 db_id」 WARNING만 없앤다.
    - `_collect_history`: 제니퍼 이벤트는 `is_db_registered`·`enrich_history` 이전에 None.
    - `collect_importance_maintenance`·`collect_dependency_status`: 제니퍼 이벤트는 `fetch`를
      부르지 않고 종전 미등록 분기와 같은 `unavailable_noise_ctx()`를 돌려준다(트랙 A·B 대칭).
    - 폴스타 이벤트는 현행 그대로(fetch 호출 · 미등록 WARNING 유지).
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import noise_gate.application.nodes.alarm_context_enricher as ace_mod
from noise_gate.application.nodes.alarm_context_enricher import _collect_history
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.infrastructure.noise_signal_tools import (
    build_noise_signal_tools,
    collect_signal,
    extract_escalation_candidate,
)
from noise_gate.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
    unavailable_noise_ctx,
)

REF = datetime(2026, 10, 27, 10, 0, 0)


def make_event(db_id: str = "jennifer_cm_prd", **kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent — 기본은 제니퍼 소스 발생 알람(메시지형)."""
    defaults = dict(
        db_id=db_id,
        server_name="srv-was01",
        hostname="hwas01",
        ip_address="10.1.2.9",
        resource_ancestry="/Servers/srv/WAS",
        alarm_id="APM-001",
        severity=2,
        alarm_status="NOT_ACK",
        resource_type="apm.Instance",
        resource_name="was01",
        alarm_name="응답 지연",
        alarm_time=REF,
        conditions="",
        condition_log="too many open files",
    )
    defaults.update(kwargs)
    return AlarmEvent(**defaults)


def _history_cfg() -> SimpleNamespace:
    """검증 대상 필드만 명시한 config(.env 누수 차단)."""
    return SimpleNamespace(alarm=SimpleNamespace(history_enabled=True))


def _unregistered_repo() -> PolestarNoiseContextRepository:
    """미등록 db_id만 있는 실 레포 — fetch 미등록 분기 실측용(DB 미접근)."""
    repo = PolestarNoiseContextRepository.__new__(PolestarNoiseContextRepository)
    repo.is_db_registered = lambda db_id: False  # type: ignore[method-assign]
    return repo


# ── _collect_history ─────────────────────────────────────────────────────
class TestCollectHistory:
    async def test_apm_event_skips_without_warning(self, monkeypatch, caplog):
        repo = MagicMock()
        enrich = AsyncMock()
        monkeypatch.setattr(ace_mod, "enrich_history", enrich)
        with caplog.at_level(logging.DEBUG):
            got = await _collect_history(make_event(), _history_cfg(), repo, None)
        assert got is None
        repo.is_db_registered.assert_not_called()
        enrich.assert_not_called()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    async def test_bare_jennifer_db_id_also_skipped(self, caplog):
        repo = MagicMock()
        with caplog.at_level(logging.WARNING):
            got = await _collect_history(make_event("jennifer"), _history_cfg(), repo, None)
        assert got is None
        repo.is_db_registered.assert_not_called()
        assert not caplog.records

    async def test_polestar_unregistered_keeps_warning(self, caplog):
        repo = MagicMock()
        repo.is_db_registered.return_value = False
        with caplog.at_level(logging.WARNING):
            got = await _collect_history(
                make_event("polestar_cm_gp"), _history_cfg(), repo, None
            )
        assert got is None
        repo.is_db_registered.assert_called_once_with("polestar_cm_gp")
        assert any("미등록 db_id" in r.getMessage() for r in caplog.records)


# ── 트랙 A — collect_signal ─────────────────────────────────────────────
class TestCollectSignalTrackA:
    async def test_unavailable_equals_legacy_fetch_result(self):
        """종전 결과(fetch 미등록 분기)와 새 반환값이 같다 — 전제 실측."""
        legacy = await _unregistered_repo().fetch(make_event())
        assert legacy == unavailable_noise_ctx()

    async def test_importance_skips_fetch(self, caplog):
        repo = MagicMock()
        repo.fetch = AsyncMock()
        with caplog.at_level(logging.WARNING):
            r = await collect_signal("importance_maintenance", noise_repo=repo, event=make_event())
        repo.fetch.assert_not_called()
        assert r == {"signal": "importance_maintenance", "context": unavailable_noise_ctx()}
        assert not caplog.records

    async def test_dependency_skips_fetch(self):
        repo = MagicMock()
        repo.fetch = AsyncMock()
        r = await collect_signal("dependency", noise_repo=repo, event=make_event())
        repo.fetch.assert_not_called()
        assert r == {"signal": "dependency", "context": unavailable_noise_ctx()}

    async def test_message_signature_still_scanned(self):
        r = await collect_signal("message_signature", noise_repo=MagicMock(), event=make_event())
        assert r["signal"] == "message_signature" and r["severity"] == 2

    async def test_non_alarm_event_double_without_db_id(self):
        """db_id 없는 테스트 더블은 현행대로 fetch로 간다(안전 접근)."""
        repo = MagicMock()
        repo.fetch = AsyncMock(return_value={"source": "polestar_db"})
        r = await collect_signal(
            "importance_maintenance", noise_repo=repo, event=SimpleNamespace(alarm_id="x")
        )
        repo.fetch.assert_awaited_once()
        assert r["context"] == {"source": "polestar_db"}

    async def test_polestar_unregistered_keeps_fetch_and_warning(self, caplog):
        repo = _unregistered_repo()
        with caplog.at_level(logging.WARNING):
            r = await collect_signal(
                "importance_maintenance", noise_repo=repo, event=make_event("polestar_cm_gp")
            )
        assert r == {"signal": "importance_maintenance", "context": unavailable_noise_ctx()}
        assert any("미등록 db_id" in rec.getMessage() for rec in caplog.records)


# ── 트랙 B — 도구 + 승격 후보 ──────────────────────────────────────────
class TestTrackBTools:
    async def test_tools_skip_fetch_and_same_shape(self):
        repo = MagicMock()
        repo.fetch = AsyncMock()
        collector: list = []
        tools = build_noise_signal_tools(
            repo, make_event(), collect_dependency=True, collector=collector
        )
        for tool in tools:
            await tool.ainvoke({})
        repo.fetch.assert_not_called()
        by_signal = {item["signal"]: item for item in collector}
        assert by_signal["importance_maintenance"] == {
            "signal": "importance_maintenance",
            "context": unavailable_noise_ctx(),
        }
        assert by_signal["dependency"] == {
            "signal": "dependency",
            "context": unavailable_noise_ctx(),
        }

    async def test_escalation_candidate_unchanged(self):
        """새 collector와 종전(fetch 미등록 분기) collector의 승격 후보가 같다."""
        event = make_event()
        new_collector: list = []
        for sig in ("message_signature", "importance_maintenance", "dependency"):
            new_collector.append(await collect_signal(sig, noise_repo=MagicMock(), event=event))
        legacy_ctx = await _unregistered_repo().fetch(event)
        legacy_collector = [
            new_collector[0],
            {"signal": "importance_maintenance", "context": legacy_ctx},
            {"signal": "dependency", "context": legacy_ctx},
        ]
        assert new_collector == legacy_collector
        assert extract_escalation_candidate(new_collector) == extract_escalation_candidate(
            legacy_collector
        )
