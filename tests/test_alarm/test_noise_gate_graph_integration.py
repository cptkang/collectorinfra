"""폴스타 합성 이벤트 → 알람 그래프 노이즈 게이트(Plan 52) end-to-end 하니스.

옵션 C(그래프 하니스, CI). 임의로 생성한 폴스타 이벤트(payload)를 **실제 `build_alarm_graph`**
에 흘려 `alarm_context_enricher → alarm_analyzer → notification_gate → alarm_notifier`
전 경로를 구동하고, 게이트의 4-티어(PAGE/TICKET/DASHBOARD/SUPPRESS) 라우팅을 검증한다.

기존 `test_polestar_noise_context_integration.py`가 fetch()+decide_notification()을 직접
호출한다면, 이 하니스는 **워커가 실제로 쓰는 그래프 배선**(enricher가 noise_context를 실
polestar_pg에서 수집 → 게이트가 소비)을 그대로 통과시켜 통합 결함을 잡는다.

결정성 확보:
  - LLM 경계만 페이크로 대체(`create_llm` monkeypatch) — analyzer가 결정적 analysis_result를
    내도록. 게이트 매트릭스는 LLM is_routine=None·pattern="" 라 LLM 출력과 무관하게 결정적.
  - noise_context는 실 polestar_pg(06_plan52_noise_fixtures.sql)에서 수집 — asyncpg 어댑터 주입.

사전 조건: polestar_pg 컨테이너 + 전체 스키마/픽스처(04~06 init). 미가용 시 skip(CI 안전).
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
import pytest_asyncio

asyncpg = pytest.importorskip("asyncpg")

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from src.alarm.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph
from src.config import AlarmConfig, NoiseGateConfig

POLESTAR_DSN = os.getenv(
    "POLESTAR_PG_CONNECTION",
    "postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb",
)
DB_ID = "polestar_pg"
FIXTURE_SQL = (
    Path(__file__).resolve().parents[2]
    / "testdata" / "pg" / "init" / "06_plan52_noise_fixtures.sql"
)

# 06_plan52_noise_fixtures.sql 의 서버/알람 (중요도 코드 1=낮음,2=보통,3=높음)
SRV_HIGH = "noise-test-high"
SRV_MED = "noise-test-med"
SRV_LOW = "noise-test-low"
SRV_MAINT = "noise-test-maint"
ALARM_NOTI = "noise-test-alarm-noti"
ALARM_NONOTI = "noise-test-alarm-nonoti"


# ── 합성 폴스타 이벤트 생성기 ────────────────────────────────────────────
def make_polestar_payload(**overrides: Any) -> dict[str, Any]:
    """폴스타가 보내는 단일행 JSON 페이로드를 임의로 생성한다.

    TcpReceiver/AlarmWorker가 받는 키 이름(camelCase)을 그대로 사용한다.
    overrides로 severity/serverName/alarmName 등을 바꿔 시나리오를 구성한다.
    """
    base = {
        "dbId": DB_ID,
        "serverName": SRV_MED,
        "hostname": "noise-test-host",
        "ipAddress": "10.0.0.1",
        "resourceAncestry": "/Servers/noise-test",
        "alarmId": "SYN-0001",
        "severity": 2,
        "alarmStatus": "NOT_ACK",
        "resourceType": "server.Server",
        "resourceName": "noise-test",
        "alarmName": ALARM_NONOTI,
        "alarmTime": "20260630120000",
        "conditions": "",
        "conditionLog": "",
    }
    base.update(overrides)
    return base


def payload_to_event(payload: dict[str, Any]) -> AlarmEvent:
    """페이로드 → AlarmEvent (AlarmWorker._process와 동일한 변환 규칙)."""
    try:
        alarm_time = datetime.strptime(str(payload.get("alarmTime", "")), "%Y%m%d%H%M%S")
    except ValueError:
        alarm_time = datetime(2026, 6, 30, 12, 0, 0)
    severity = int(payload.get("severity", 0))
    return AlarmEvent(
        db_id=payload.get("dbId", ""),
        server_name=payload.get("serverName", ""),
        hostname=payload.get("hostname", ""),
        ip_address=payload.get("ipAddress", ""),
        resource_ancestry=payload.get("resourceAncestry", ""),
        alarm_id=str(payload.get("alarmId", "SYN")),
        severity=severity,
        alarm_status=payload.get("alarmStatus", ""),
        resource_type=payload.get("resourceType", ""),
        resource_name=payload.get("resourceName", ""),
        alarm_name=payload.get("alarmName", ""),
        alarm_time=alarm_time,
        conditions=payload.get("conditions", ""),
        condition_log=payload.get("conditionLog", ""),
        is_clear=(severity == 0),
        raw_payload=payload,
    )


# ── 실 DB 어댑터 (repo 계약: execute_sql → SimpleNamespace(rows=[dict])) ──
class _AsyncpgClient:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def execute_sql(self, sql):
        async with self._pool.acquire() as conn:
            records = await conn.fetch(sql)
        return SimpleNamespace(rows=[dict(r) for r in records], row_count=len(records))


class _LiveRegistry:
    def __init__(self, pool, db_id: str = DB_ID) -> None:
        self._pool = pool
        self._db_id = db_id

    def is_registered(self, db_id: str) -> bool:
        return db_id == self._db_id

    @asynccontextmanager
    async def get_client(self, db_id: str):
        yield _AsyncpgClient(self._pool)


class _RecordingStore:
    """decision_store 대역 — 게이트→스토어 배선을 검증하기 위해 결정을 수집한다."""

    def __init__(self) -> None:
        self.records: list[tuple[Any, Optional[str]]] = []

    def record(  # noqa: ANN001
        self, decision, alarm_id=None, recurrence=None, correlation_meta=None
    ) -> None:
        self.records.append((decision, alarm_id))


class _FakeLLM:
    """analyzer LLM 경계 대역 — 결정적 analysis_result용 JSON을 반환한다.

    is_routine 미포함(→None)·pattern_type 빈값 → 게이트 매트릭스는 LLM과 무관하게 결정적.
    """

    async def ainvoke(self, messages, *args, **kwargs):
        return SimpleNamespace(content=json.dumps({
            "severity_label": "경고",
            "summary": "합성 이벤트 테스트 요약",
            "probable_cause": "테스트 원인",
            "recommended_action": "테스트 조치",
            "pattern_type": "",
        }))


def _make_app_config(*, enable_gate: bool) -> SimpleNamespace:
    """노드가 덕 타이핑으로 소비하는 app_config (실 AlarmConfig/NoiseGateConfig 사용).

    - history_enabled=False: 이력 조회 건너뜀(noise_context만 수집).
    - notification_channels_csv="": notifier PAGE 경로가 외부 발송 없이 무동작.
    - cache_ttl=0: noise_context 캐시 비활성(redis 불필요).
    - importance: 1=낮음/2=보통/3=높음 (06 픽스처 정합).
    """
    alarm = AlarmConfig(
        history_enabled=False,
        notification_channels_csv="",
        enrich_timeout_seconds=10,
        min_severity=1,
    )
    gate = NoiseGateConfig(
        enable_noise_gate=enable_gate,
        suppress_max_severity=2,
        importance_value_map_csv="1=낮음,2=보통,3=높음",
        noise_context_cache_ttl_seconds=0,
        resolved_to_dashboard=False,
        dependency_suppression=False,
    )
    return SimpleNamespace(alarm=alarm, noise_gate=gate)


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """그래프의 alarm_analyzer가 호출하는 create_llm을 페이크로 대체(결정성)."""
    monkeypatch.setattr(
        "src.alarm.application.nodes.alarm_analyzer.create_llm",
        lambda cfg, **kw: _FakeLLM(),
    )


@pytest_asyncio.fixture
async def pool():
    try:
        p = await asyncpg.create_pool(POLESTAR_DSN, min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001 — 미가용은 skip
        pytest.skip(f"polestar_pg 미가용 — 통합 테스트 skip: {exc}")
    try:
        async with p.acquire() as conn:
            await conn.execute(FIXTURE_SQL.read_text())
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def harness(pool):
    """그래프 실행 클로저 + decision_store 대역."""
    noise_repo = PolestarNoiseContextRepository(_LiveRegistry(pool), SimpleNamespace())
    store = _RecordingStore()
    app_cfg = _make_app_config(enable_gate=True)

    async def run(payload: dict[str, Any]) -> dict[str, Any]:
        graph = build_alarm_graph(app_cfg)
        return await graph.ainvoke(
            {
                "alarm_event": payload_to_event(payload),
                "history_stats": None,
                "process_snapshot": None,
                "analysis_result": None,
                "error": None,
                "noise_context": None,
                "notification_decision": None,
                "self_heal": False,
                "inhibited": False,
                "flapping": False,
                "storm": False,
            },
            config={"configurable": {
                "app_config": app_cfg,
                "noise_repo": noise_repo,
                "decision_store": store,
            }},
        )

    return SimpleNamespace(run=run, store=store)


# ── 1. 그래프 전 경로: enricher 실 DB 수집 + analyzer + 게이트 ──────────
class TestGraphPipeline:
    async def test_enricher_collects_noise_context_from_real_db(self, harness):
        # 그래프의 enricher가 실 polestar_pg에서 noise_context를 수집했는지
        final = await harness.run(make_polestar_payload(serverName=SRV_HIGH))
        ctx = final["noise_context"]
        assert ctx is not None and ctx["source"] == "polestar_db"
        assert int(ctx["importance_id"]) == 3
        assert ctx["maintenance"] is False

    async def test_analyzer_ran_and_decision_produced(self, harness):
        final = await harness.run(make_polestar_payload(serverName=SRV_HIGH))
        assert final["analysis_result"] is not None       # analyzer(페이크 LLM) 동작
        assert final["notification_decision"] is not None  # 게이트 결정 산출


# ── 2. 합성 이벤트별 4-티어 라우팅 (그래프 통과) ──────────────────────
class TestTierRoutingThroughGraph:
    async def test_high_sev2_page(self, harness):
        final = await harness.run(make_polestar_payload(serverName=SRV_HIGH, severity=2))
        assert final["notification_decision"].tier == TIER_PAGE

    async def test_med_sev2_ticket(self, harness):
        final = await harness.run(make_polestar_payload(serverName=SRV_MED, severity=2))
        assert final["notification_decision"].tier == TIER_TICKET

    async def test_low_sev2_dashboard(self, harness):
        final = await harness.run(make_polestar_payload(serverName=SRV_LOW, severity=2))
        assert final["notification_decision"].tier == TIER_DASHBOARD

    async def test_low_sev1_dashboard(self, harness):
        # E3 결정: sev1×낮음 SUPPRESS→DASHBOARD (저중요도 주의알람은 묵살 아닌 대시보드 강등)
        final = await harness.run(make_polestar_payload(serverName=SRV_LOW, severity=1))
        assert final["notification_decision"].tier == TIER_DASHBOARD

    async def test_noti_policy_promotes_to_page(self, harness):
        # 보통×sev2=TICKET 이지만 폴스타 통보정책(실 JOIN) 승격 → PAGE
        final = await harness.run(
            make_polestar_payload(serverName=SRV_MED, severity=2, alarmName=ALARM_NOTI)
        )
        d = final["notification_decision"]
        assert d.tier == TIER_PAGE
        assert d.signals["noti_policy"] == "notify"

    async def test_maintenance_suppresses_sev2(self, harness):
        final = await harness.run(make_polestar_payload(serverName=SRV_MAINT, severity=2))
        d = final["notification_decision"]
        assert d.tier == TIER_SUPPRESS
        assert "유지보수" in d.reason

    async def test_severity3_always_page_even_maintenance(self, harness):
        # ★ 핵심 안전: 심각도3은 유지보수 서버여도 항상 PAGE(억제 단계 미경유)
        final = await harness.run(make_polestar_payload(serverName=SRV_MAINT, severity=3))
        d = final["notification_decision"]
        assert d.tier == TIER_PAGE
        assert d.signals["maintenance"] is True
        assert d.signals["effective_severity"] == 3


# ── 3. 게이트→decision_store 배선 + 옵트인 회귀 ──────────────────────
class TestStoreAndOptIn:
    async def test_decision_recorded_to_store(self, harness):
        await harness.run(make_polestar_payload(serverName=SRV_HIGH, severity=2))
        assert len(harness.store.records) == 1
        decision, alarm_id = harness.store.records[0]
        assert decision.tier == TIER_PAGE
        assert alarm_id == "SYN-0001"

    async def test_suppress_tier_also_recorded(self, harness):
        # 억제 ≠ 삭제: SUPPRESS도 감사 기록 대상
        await harness.run(make_polestar_payload(serverName=SRV_MAINT, severity=2))
        assert len(harness.store.records) == 1
        assert harness.store.records[0][0].tier == TIER_SUPPRESS

    async def test_gate_off_no_decision_node(self, pool):
        # enable_noise_gate=False → 게이트 노드 미포함 → notification_decision 없음(회귀 0)
        noise_repo = PolestarNoiseContextRepository(_LiveRegistry(pool), SimpleNamespace())
        app_cfg = _make_app_config(enable_gate=False)
        graph = build_alarm_graph(app_cfg)
        final = await graph.ainvoke(
            {
                "alarm_event": payload_to_event(make_polestar_payload(serverName=SRV_HIGH)),
                "history_stats": None, "process_snapshot": None,
                "analysis_result": None, "error": None,
                "noise_context": None, "notification_decision": None,
                "self_heal": False, "inhibited": False, "flapping": False, "storm": False,
            },
            config={"configurable": {"app_config": app_cfg, "noise_repo": noise_repo}},
        )
        assert final.get("notification_decision") is None  # 게이트 미동작
        assert final.get("noise_context") is None          # enricher도 미포함(회귀 0)
        assert final["analysis_result"] is not None         # 기존 발송 경로는 동작
