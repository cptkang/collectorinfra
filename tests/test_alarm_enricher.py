"""알람 컨텍스트 보강 노드 + 그래프 통합(Plan 47) 단위 테스트.

graceful degradation(타임아웃/DB 실패/미등록 db_id/캐시 장애),
Redis 단기 캐시, 3-노드 그래프 전환, is_clear 판정(severity=0 단독 기준),
analyzer 이력 섹션 렌더링·패턴 필드 파싱, notifier 패턴 출력을 검증한다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.alarm.application.nodes.alarm_context_enricher import (
    alarm_context_enricher_node,
    enrich_history,
)
from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent
from src.alarm.domain.alarm_pattern import (
    compute_history_stats,
    history_entries_to_dicts,
)
from src.config import AlarmConfig
from tests.test_alarm_pattern import REF, make_entry, make_event


def make_alarm_cfg(**kwargs) -> AlarmConfig:
    defaults = dict(
        history_enabled=True,
        history_lookback_days=90,
        history_max_rows=2000,
        history_cache_ttl_seconds=300,
        enrich_timeout_seconds=1,
        burst_threshold_24h=5,
    )
    defaults.update(kwargs)
    return AlarmConfig(**defaults)


def make_app_cfg(**alarm_kwargs) -> SimpleNamespace:
    return SimpleNamespace(alarm=make_alarm_cfg(**alarm_kwargs))


class FakeRepo:
    def __init__(self, entries=None, registered=True, error=None, delay=0.0):
        self.entries = entries or []
        self.registered = registered
        self.error = error
        self.delay = delay
        self.fetch_calls = 0

    def is_db_registered(self, db_id):
        return self.registered

    async def fetch(self, event):
        self.fetch_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.entries


class FakeRedis:
    def __init__(self, store=None, fail=False):
        self.store = store if store is not None else {}
        self.fail = fail
        self.setex_calls = 0

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        if self.fail:
            raise ConnectionError("redis down")
        self.setex_calls += 1
        self.store[key] = value


def lc_config(cfg, repo=None, redis=None) -> dict:
    return {
        "configurable": {
            "app_config": cfg,
            "history_repo": repo,
            "history_redis": redis,
        }
    }


class TestEnricherGracefulDegradation:
    async def test_history_disabled_returns_none(self):
        cfg = make_app_cfg(history_enabled=False)
        repo = FakeRepo()
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(cfg, repo)
        )
        assert out == {"history_stats": None}
        assert repo.fetch_calls == 0

    async def test_no_repo_returns_none(self):
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(make_app_cfg(), repo=None)
        )
        assert out == {"history_stats": None}

    async def test_clear_alarm_skips_history(self):
        repo = FakeRepo()
        event = make_event(severity=0, is_clear=True)
        out = await alarm_context_enricher_node(
            {"alarm_event": event}, lc_config(make_app_cfg(), repo)
        )
        assert out == {"history_stats": None}
        assert repo.fetch_calls == 0

    async def test_unregistered_db_id_returns_none(self):
        # 미등록 db_id는 빈 이력 통계("첫 발생" 오판)가 아니라 None이어야 함
        repo = FakeRepo(registered=False)
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event(db_id="unknown")},
            lc_config(make_app_cfg(), repo),
        )
        assert out == {"history_stats": None}
        assert repo.fetch_calls == 0

    async def test_db_failure_returns_none(self):
        repo = FakeRepo(error=RuntimeError("DB down"))
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(make_app_cfg(), repo)
        )
        assert out == {"history_stats": None}

    async def test_timeout_returns_none(self):
        repo = FakeRepo(delay=3.0)
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()},
            lc_config(make_app_cfg(enrich_timeout_seconds=1), repo),
        )
        assert out == {"history_stats": None}

    async def test_redis_failure_falls_back_to_db(self):
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)]
        repo = FakeRepo(entries=entries)
        redis = FakeRedis(fail=True)
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(make_app_cfg(), repo, redis)
        )
        stats = out["history_stats"]
        assert stats is not None
        assert stats.source == "polestar_db"
        assert stats.total_count == 3


class TestEnricherSuccess:
    async def test_success_computes_stats(self):
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 11)]
        repo = FakeRepo(entries=entries)
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(make_app_cfg(), repo)
        )
        stats = out["history_stats"]
        assert stats.pre_classification == "주기적"
        assert stats.period_label == "일 주기"
        assert stats.source == "polestar_db"

    async def test_cache_hit_skips_db(self):
        event = make_event()
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)]
        key = f"alarm:histcache:{event.db_id}:{event.server_name}:{event.alarm_name}"
        redis = FakeRedis(
            store={key: json.dumps(history_entries_to_dicts(entries), ensure_ascii=False)}
        )
        repo = FakeRepo(entries=[])
        out = await alarm_context_enricher_node(
            {"alarm_event": event}, lc_config(make_app_cfg(), repo, redis)
        )
        stats = out["history_stats"]
        assert stats.source == "cache"
        assert stats.total_count == 3
        assert repo.fetch_calls == 0  # 캐시 적중 — DB 미접근

    async def test_cache_miss_populates_cache(self):
        entries = [make_entry(REF - timedelta(hours=1))]
        repo = FakeRepo(entries=entries)
        redis = FakeRedis()
        out = await alarm_context_enricher_node(
            {"alarm_event": make_event()}, lc_config(make_app_cfg(), repo, redis)
        )
        assert out["history_stats"].source == "polestar_db"
        assert repo.fetch_calls == 1
        assert redis.setex_calls == 1

    async def test_cache_disabled_by_zero_ttl(self):
        repo = FakeRepo(entries=[])
        redis = FakeRedis()
        await enrich_history(
            make_event(), make_alarm_cfg(history_cache_ttl_seconds=0), repo, redis
        )
        assert redis.setex_calls == 0
        assert repo.fetch_calls == 1

    async def test_truncated_when_max_rows_reached(self):
        entries = [make_entry(REF - timedelta(hours=i)) for i in range(1, 6)]
        repo = FakeRepo(entries=entries)
        stats = await enrich_history(
            make_event(), make_alarm_cfg(history_max_rows=5), repo, None
        )
        assert stats.truncated is True


class TestGraphStructure:
    def test_three_node_graph_when_history_enabled(self):
        from src.alarm.orchestration.alarm_graph import build_alarm_graph

        graph = build_alarm_graph(make_app_cfg(history_enabled=True))
        nodes = set(graph.get_graph().nodes)
        assert "alarm_context_enricher" in nodes
        assert "alarm_analyzer" in nodes
        assert "alarm_notifier" in nodes

    def test_two_node_graph_when_history_disabled(self):
        # ALARM_HISTORY_ENABLED=false → 기존 2-노드 동작과 완전 동일
        from src.alarm.orchestration.alarm_graph import build_alarm_graph

        graph = build_alarm_graph(make_app_cfg(history_enabled=False))
        nodes = set(graph.get_graph().nodes)
        assert "alarm_context_enricher" not in nodes
        assert "alarm_analyzer" in nodes
        assert "alarm_notifier" in nodes


class TestIsClearJudgment:
    """is_clear는 severity == 0 단독 기준 — alarmStatus(ACK 상태)와 무관."""

    def test_severity_zero_is_clear_regardless_of_status(self):
        from src.api.routes.alarm import _build_alarm_event_from_payload

        event = _build_alarm_event_from_payload(
            {"alarmId": "1", "severity": 0, "alarmStatus": "NOT_ACK"}
        )
        assert event.is_clear is True

    def test_status_text_does_not_imply_clear(self):
        from src.api.routes.alarm import _build_alarm_event_from_payload

        event = _build_alarm_event_from_payload(
            {"alarmId": "1", "severity": 2, "alarmStatus": "해소"}
        )
        assert event.is_clear is False


def make_result(**kwargs) -> AlarmAnalysisResult:
    defaults = dict(
        alarm_event=make_event(),
        severity_label="경고",
        summary="요약",
        probable_cause="원인",
        recommended_action="조치",
        notification_channels=["workb"],
    )
    defaults.update(kwargs)
    return AlarmAnalysisResult(**defaults)


class TestNotifierPatternOutput:
    def test_workb_body_includes_pattern_section(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        result = make_result(
            pattern_type="주기적", is_routine=True, pattern_analysis="매일 새벽 반복."
        )
        body = build_workb_body(result)
        assert "<b>패턴 분석</b>" in body
        assert "[주기적 · 일상 알람]" in body
        assert "매일 새벽 반복." in body

    def test_workb_body_badge_for_non_routine(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        result = make_result(
            pattern_type="급증", is_routine=False, pattern_analysis="24시간 내 급증."
        )
        assert "[급증 · 확인 필요]" in build_workb_body(result)

    def test_workb_body_omits_section_without_pattern(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        body = build_workb_body(make_result())
        assert "패턴 분석" not in body

    def test_webhook_preview_includes_pattern_fields(self):
        from src.api.routes.alarm import _build_webhook_preview

        alarm_cfg = make_alarm_cfg()
        result = make_result(
            pattern_type="첫 발생", is_routine=False, pattern_analysis="이력 없음."
        )
        preview = _build_webhook_preview(alarm_cfg, result)
        assert preview.payload["pattern_type"] == "첫 발생"
        assert preview.payload["is_routine"] is False
        assert preview.payload["pattern_analysis"] == "이력 없음."


class FakeLLM:
    def __init__(self, content: str):
        self._content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self._content)


class TestAnalyzerHistoryIntegration:
    def _make_cfg(self):
        return SimpleNamespace(alarm=make_alarm_cfg())

    async def test_history_section_injected(self, monkeypatch):
        import src.alarm.application.nodes.alarm_analyzer as analyzer_mod

        llm = FakeLLM(json.dumps({
            "severity_label": "경고",
            "summary": "s",
            "probable_cause": "c",
            "recommended_action": "a",
            "pattern_type": "주기적",
            "is_routine": True,
            "pattern_analysis": "p",
        }, ensure_ascii=False))
        monkeypatch.setattr(analyzer_mod, "create_llm", lambda cfg: llm)

        event = make_event()
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 11)]
        stats = compute_history_stats(event, entries, 5, 90)

        out = await analyzer_mod.alarm_analyzer_node(
            {"alarm_event": event, "history_stats": stats},
            {"configurable": {"app_config": self._make_cfg()}},
        )
        user_msg = llm.messages[1]["content"]
        assert "[알람 이력 통계 — 최근 90일" in user_msg
        assert "사전 분류: 주기적 (일 주기)" in user_msg
        result = out["analysis_result"]
        assert result.pattern_type == "주기적"
        assert result.is_routine is True
        assert result.pattern_analysis == "p"

    async def test_no_history_section_when_stats_none(self, monkeypatch):
        import src.alarm.application.nodes.alarm_analyzer as analyzer_mod

        llm = FakeLLM(json.dumps({
            "severity_label": "경고",
            "summary": "s",
            "probable_cause": "c",
            "recommended_action": "a",
        }, ensure_ascii=False))
        monkeypatch.setattr(analyzer_mod, "create_llm", lambda cfg: llm)

        out = await analyzer_mod.alarm_analyzer_node(
            {"alarm_event": make_event(), "history_stats": None},
            {"configurable": {"app_config": self._make_cfg()}},
        )
        assert "[알람 이력 통계" not in llm.messages[1]["content"]
        # LLM이 패턴 필드를 누락 응답해도 기존 분석 결과는 정상 생성
        result = out["analysis_result"]
        assert result.summary == "s"
        assert result.pattern_type == ""
        assert result.is_routine is None
        assert result.pattern_analysis == ""

    async def test_truncated_note_rendered(self):
        from src.alarm.application.nodes.alarm_analyzer import _render_history_section

        event = make_event()
        entries = [make_entry(REF - timedelta(hours=i)) for i in range(1, 4)]
        stats = compute_history_stats(event, entries, 5, 90, truncated=True)
        text = _render_history_section(stats, event, make_alarm_cfg())
        assert "(이력 일부만 반영 — 최근 2,000건 한정)" in text


class TestSimulatedHistoryHelper:
    def test_simulated_entries_conversion(self):
        from src.api.routes.alarm import _simulated_entries

        entries = _simulated_entries([
            {"alarm_time": "20260610021100", "severity": 3,
             "alarm_status": "NOT_ACK", "resource_name": "svr-001-CPU"},
        ])
        assert entries[0].alarm_time == datetime(2026, 6, 10, 2, 11, 0)
        assert entries[0].severity == 3
        assert entries[0].alarm_id == "SIM-0"

    def test_stats_to_dict(self):
        from src.api.routes.alarm import _stats_to_dict

        event = make_event()
        entries = [make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)]
        stats = compute_history_stats(event, entries, 5, 90, source="simulated")
        d = _stats_to_dict(stats)
        assert d["pre_classification"] == "주기적"
        assert d["period_label"] == "일 주기"
        assert d["source"] == "simulated"
        assert d["total_count"] == 3
