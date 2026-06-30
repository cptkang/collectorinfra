"""폴스타 노이즈 캔슬링(Plan 52, Phase E1) — 실 폴스타 DB 연동 통합 테스트.

기존 `test_polestar_noise_context.py`(모킹 단위 테스트)와 달리, **구동 중인 polestar_pg
컨테이너의 실제 스키마 + Plan 52 픽스처(testdata/pg/init/06_plan52_noise_fixtures.sql)**
를 대상으로 다음을 end-to-end로 검증한다:

  1. `PolestarNoiseContextRepository.fetch()` 의 고정 SQL이 실 스키마에서 동작하여
     중요도(importance_id)/유지보수(maintenance)/알림정책(noti_policy)을 정확히 추출.
  2. 추출 신호 → `decide_notification()` 결정 파이프라인 → 4-티어(PAGE/TICKET/DASHBOARD/
     SUPPRESS) 라우팅 + **심각도3 절대 PAGE**(유지보수여도) + 유지보수 SUPPRESS +
     폴스타 통보정책 승격.

사전 조건:
  - polestar_pg 컨테이너 실행: cd testdata/pg && docker compose up -d
  - 전체 스키마/픽스처 적용(04~06 init). 미가용 시 테스트는 skip(CI 안전).

repo 계약은 덕 타이핑(execute_sql→.rows dict)이므로, asyncpg 연결을 얇게 감싼 어댑터를
주입한다 — PostgresClient의 정수→float 강제변환을 거치지 않아 DB 네이티브 타입을 보존한다
(프로덕션 DBHub 반환 형태에 더 충실).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

asyncpg = pytest.importorskip("asyncpg")

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)
from src.alarm.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
)

POLESTAR_DSN = os.getenv(
    "POLESTAR_PG_CONNECTION",
    "postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb",
)
DB_ID = "polestar_pg"
FIXTURE_SQL = (
    Path(__file__).resolve().parents[2]
    / "testdata" / "pg" / "init" / "06_plan52_noise_fixtures.sql"
)

# 테스트 정책: importance_id 코드 → 중요도 라벨 (Plan 52 §13.1 인스턴스별 확정 항목)
IMPORTANCE_MAP = {"1": "낮음", "2": "보통", "3": "높음"}

# decide_notification config (덕 타이핑 — 필요한 속성만)
DECIDE_CFG = SimpleNamespace(
    suppress_max_severity=2,
    importance_value_map=IMPORTANCE_MAP,
    resolved_to_dashboard=False,
)

# 픽스처에 정의된 서버명/알람명 (06_plan52_noise_fixtures.sql)
SRV_HIGH = "noise-test-high"     # importance_id=3(높음), maint=0
SRV_MED = "noise-test-med"       # importance_id=2(보통), maint=0
SRV_LOW = "noise-test-low"       # importance_id=1(낮음), maint=0
SRV_MAINT = "noise-test-maint"   # importance_id=3(높음), maint=1
ALARM_NOTI = "noise-test-alarm-noti"      # 통보정책 有 → noti_policy="notify"
ALARM_NONOTI = "noise-test-alarm-nonoti"  # 통보정책 無 → noti_policy=None


# ── 실 DB 연동 어댑터 (repo 계약: execute_sql → SimpleNamespace(rows=[dict])) ──
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


@pytest_asyncio.fixture
async def repo():
    """polestar_pg 풀 생성 + Plan 52 픽스처 idempotent 적용 → 실 DB repo 반환.

    컨테이너 미가용 시 skip(CI 안전 — Plan 52 §11 통합 분리 정책).
    """
    try:
        pool = await asyncpg.create_pool(POLESTAR_DSN, min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001 — 미가용은 실패가 아니라 skip
        pytest.skip(f"polestar_pg 미가용 — 통합 테스트 skip: {exc}")
    try:
        async with pool.acquire() as conn:
            await conn.execute(FIXTURE_SQL.read_text())
        yield PolestarNoiseContextRepository(_LiveRegistry(pool), SimpleNamespace())
    finally:
        await pool.close()


def _event(server_name: str, alarm_name: str, severity: int, *, is_clear: bool = False) -> AlarmEvent:
    return AlarmEvent(
        db_id=DB_ID,
        server_name=server_name,
        hostname="noise-test-host",
        ip_address="10.0.0.1",
        resource_ancestry="/Servers/noise-test",
        alarm_id="NOISE-IT-001",
        severity=severity,
        alarm_status="NOT_ACK",
        resource_type="server.Server",
        resource_name=server_name,
        alarm_name=alarm_name,
        alarm_time=datetime(2026, 6, 29, 12, 0, 0),
        conditions="",
        condition_log="",
        is_clear=is_clear,
    )


async def _decide(repo, server_name, alarm_name, severity, *, is_clear=False):
    event = _event(server_name, alarm_name, severity, is_clear=is_clear)
    ctx = await repo.fetch(event)
    decision = decide_notification(event, None, None, ctx, DECIDE_CFG)
    return ctx, decision


# ── 1. fetch(): 실 스키마에서 신호 추출 검증 ──────────────────────────
class TestFetchRealDB:
    async def test_high_server_signals(self, repo):
        ctx = await repo.fetch(_event(SRV_HIGH, ALARM_NONOTI, 2))
        assert ctx["source"] == "polestar_db"
        assert int(ctx["importance_id"]) == 3
        assert ctx["maintenance"] is False
        assert ctx["parent_avail_status"] is None  # E2 자리예약

    async def test_maintenance_server_signals(self, repo):
        ctx = await repo.fetch(_event(SRV_MAINT, ALARM_NONOTI, 2))
        assert ctx["source"] == "polestar_db"
        assert int(ctx["importance_id"]) == 3
        assert ctx["maintenance"] is True  # is_maintenance=1 → True

    async def test_noti_policy_present(self, repo):
        # 통보정의(cmm_alarm_def_noti) 존재 → "notify" (실 JOIN: DN.DEFINITION_ID=D.MASTERDEFINITION_ID)
        ctx = await repo.fetch(_event(SRV_MED, ALARM_NOTI, 2))
        assert ctx["noti_policy"] == "notify"

    async def test_noti_policy_absent_conservative_none(self, repo):
        # 통보정의 부재 → "suppress" 단정 금지, None (보수적)
        ctx = await repo.fetch(_event(SRV_MED, ALARM_NONOTI, 2))
        assert ctx["noti_policy"] is None

    async def test_unknown_server_partial_none(self, repo):
        # 미등록 서버명 → 중요도/유지보수 None, 단 연결은 됐으므로 source=polestar_db
        ctx = await repo.fetch(_event("no-such-server-xyz", ALARM_NONOTI, 2))
        assert ctx["source"] == "polestar_db"
        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None


# ── 2. decide_notification(): 신호 → 4-티어 라우팅 검증 ────────────────
class TestDecisionRouting:
    async def test_high_sev2_page(self, repo):
        _, d = await _decide(repo, SRV_HIGH, ALARM_NONOTI, 2)
        assert d.tier == TIER_PAGE  # 매트릭스 sev2×높음

    async def test_med_sev2_ticket(self, repo):
        _, d = await _decide(repo, SRV_MED, ALARM_NONOTI, 2)
        assert d.tier == TIER_TICKET  # 매트릭스 sev2×보통

    async def test_low_sev2_dashboard(self, repo):
        _, d = await _decide(repo, SRV_LOW, ALARM_NONOTI, 2)
        assert d.tier == TIER_DASHBOARD  # 매트릭스 sev2×낮음

    async def test_low_sev1_suppress(self, repo):
        _, d = await _decide(repo, SRV_LOW, ALARM_NONOTI, 1)
        assert d.tier == TIER_SUPPRESS  # 매트릭스 sev1×낮음

    async def test_noti_policy_promotes_ticket_to_page(self, repo):
        # 보통×sev2=TICKET 이지만 폴스타 통보정책(notify) 승격 → PAGE
        ctx, d = await _decide(repo, SRV_MED, ALARM_NOTI, 2)
        assert ctx["noti_policy"] == "notify"
        assert d.tier == TIER_PAGE
        assert "승격" in d.reason

    async def test_maintenance_suppresses_sev2(self, repo):
        # 유지보수 모드 → 높은 중요도여도 SUPPRESS(신규 발송 억제)
        _, d = await _decide(repo, SRV_MAINT, ALARM_NONOTI, 2)
        assert d.tier == TIER_SUPPRESS
        assert "유지보수" in d.reason

    async def test_severity3_always_page_even_maintenance(self, repo):
        # ★ 핵심 안전: 심각도3은 유지보수 서버여도 항상 PAGE(억제 단계 미경유, D-035)
        _, d = await _decide(repo, SRV_MAINT, ALARM_NONOTI, 3)
        assert d.tier == TIER_PAGE
        assert d.signals["maintenance"] is True  # 유지보수 신호는 수집됐으나
        assert d.signals["effective_severity"] == 3  # 심각도3이 단락

    async def test_signals_snapshot_schema(self, repo):
        # §8.2 동결 키 스키마 — Plan 54 결정추적 드로어 인터페이스 정합
        _, d = await _decide(repo, SRV_HIGH, ALARM_NOTI, 2)
        expected = {
            "severity", "ai_severity", "effective_severity", "importance",
            "maintenance", "parent_avail_status", "pattern", "is_routine",
            "noti_policy", "flapping", "self_heal", "storm",
        }
        assert set(d.signals.keys()) == expected
        assert d.signals["importance"] == "높음"
        assert d.signals["noti_policy"] == "notify"
        assert d.fingerprint  # 핑거프린트 산출됨
