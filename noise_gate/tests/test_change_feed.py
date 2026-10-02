"""변경 피드 어댑터 인프라 단위 테스트 (Plan 60 E5 · D-081 초안).

- `build_recent_changes_sql`: 읽기전용 SELECT 단일문·스키마 한정·창 경계·정렬·LIMIT·
  resource_id 스코프·리터럴 이스케이프 문자열 단언.
- `ChangeFeed.fetch_recent_changes`: 행 파싱→ChangeEvent, cutoff 계산, event_time 미상 행
  스킵, 조회 실패/빈 결과 → 빈 리스트(graceful, D-003).
- `PolestarNoiseContextRepository._compute_change_correlation`: 변경 피드+overlay 배선으로
  (change_nearby, change_candidates dict) 산출.

DBHub 클라이언트는 topology_loader/polestar_noise_context 테스트 패턴
(SimpleNamespace(rows=...))을 재사용한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.infrastructure.change_feed import (
    ChangeEvent,
    ChangeFeed,
    build_recent_changes_sql,
)
from noise_gate.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
)


class TestBuildRecentChangesSql:
    def test_readonly_structure(self):
        sql = build_recent_changes_sql("polestar_cm_gp", 6400, 10000)
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql  # 단일문
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "LIFECYCLE_TYPE" in upper
        assert "EVENT_TIME" in upper
        assert "ORDER BY event_time DESC" in sql
        assert "LIMIT" in upper

    def test_window_bounds(self):
        sql = build_recent_changes_sql("polestar_cm_gp", 6400, 10000)
        assert "event_time >= 6400" in sql
        assert "event_time <= 10000" in sql

    def test_schema_qualified(self):
        sql = build_recent_changes_sql("polestar_cm_yd", 0, 1)
        assert "polestar.cmm_resource_lifecycle_history" in sql
        assert "FROM cmm_resource_lifecycle_history" not in sql

    def test_resource_scope_in_clause(self):
        sql = build_recent_changes_sql("polestar_cm_gp", 0, 100, ["10", "20"])
        assert "resource_id IN ('10', '20')" in sql

    def test_no_scope_omits_in_clause(self):
        sql = build_recent_changes_sql("polestar_cm_gp", 0, 100, None)
        assert "resource_id IN" not in sql

    def test_literal_escaping(self):
        sql = build_recent_changes_sql("polestar", 0, 100, ["x'; DROP TABLE t--"])
        assert "x''; DROP TABLE t--" in sql


class FakeClient:
    """SQL을 기록하고 지정된 변경 행을 반환하는 가짜 DB 클라이언트(예외 모사)."""

    def __init__(self, rows=None, raise_exc=False):
        self._rows = rows if rows is not None else []
        self._raise = raise_exc
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        if self._raise:
            raise RuntimeError("feed boom")
        return SimpleNamespace(rows=self._rows, row_count=len(self._rows))


def _row(resource_id, event_time, *, lifecycle_type="deploy", description="배포"):
    return {
        "id": 1, "resource_id": resource_id, "resource_type": "server.Server",
        "lifecycle_type": lifecycle_type, "description": description, "event_time": event_time,
    }


class TestFetchRecentChanges:
    async def test_parses_rows_to_change_events(self):
        client = FakeClient(rows=[_row("10", 9500), _row("20", 9800)])
        feed = ChangeFeed()
        out = await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000
        )
        assert all(isinstance(c, ChangeEvent) for c in out)
        assert {(c.resource_id, c.change_type, c.event_time) for c in out} == {
            ("10", "deploy", 9500), ("20", "deploy", 9800),
        }

    async def test_cutoff_computed_from_window(self):
        client = FakeClient(rows=[])
        feed = ChangeFeed()
        await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000
        )
        # cutoff = reference(10000) − window(3600) = 6400.
        assert "event_time >= 6400" in client.executed_sqls[0]
        assert "event_time <= 10000" in client.executed_sqls[0]

    async def test_scope_passed_to_sql(self):
        client = FakeClient(rows=[])
        feed = ChangeFeed()
        await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000, resource_ids=["77"]
        )
        assert "resource_id IN ('77')" in client.executed_sqls[0]

    async def test_bad_event_time_row_skipped(self):
        client = FakeClient(rows=[_row("10", None), _row("20", "nope"), _row("30", 9500)])
        feed = ChangeFeed()
        out = await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000
        )
        assert [c.resource_id for c in out] == ["30"]

    async def test_query_failure_returns_empty(self):
        client = FakeClient(raise_exc=True)
        feed = ChangeFeed()
        out = await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000
        )
        assert out == []  # graceful

    async def test_empty_rows_returns_empty(self):
        client = FakeClient(rows=[])
        feed = ChangeFeed()
        out = await feed.fetch_recent_changes(
            client, "polestar_cm_gp", 3600, reference_epoch=10000
        )
        assert out == []


def _event() -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/svr", alarm_id="A1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Server", resource_name="r1", alarm_name="CPU",
        alarm_time=datetime(2026, 7, 22, 10, 0, 0), conditions="", condition_log="",
    )


class TestComputeChangeCorrelation:
    async def test_nearby_change_matched_to_resource(self):
        event = _event()
        ref = int(event.alarm_time.timestamp())
        repo = PolestarNoiseContextRepository(registry=None, alarm_cfg=None)
        repo._change_feed = ChangeFeed()
        client = FakeClient(rows=[_row("R1", ref - 100)])  # 창 내(3600s) + 리소스 매칭
        nearby, candidates = await repo._compute_change_correlation(
            client, event, "R1", change_window_seconds=3600
        )
        assert nearby is True
        assert isinstance(candidates, list) and len(candidates) == 1
        # noise_ctx Redis 왕복 대비 plain dict(직렬화 가능)로 저장.
        assert candidates[0]["resource_id"] == "R1"
        assert candidates[0]["proximity_seconds"] == 100
        assert set(candidates[0]) == {
            "resource_id", "change_type", "description", "event_time", "proximity_seconds",
        }

    async def test_empty_feed_returns_false_empty(self):
        event = _event()
        repo = PolestarNoiseContextRepository(registry=None, alarm_cfg=None)
        repo._change_feed = ChangeFeed()
        nearby, candidates = await repo._compute_change_correlation(
            FakeClient(rows=[]), event, "R1", change_window_seconds=3600
        )
        assert nearby is False
        assert candidates == []

    async def test_change_outside_window_not_nearby(self):
        event = _event()
        ref = int(event.alarm_time.timestamp())
        repo = PolestarNoiseContextRepository(registry=None, alarm_cfg=None)
        repo._change_feed = ChangeFeed()
        # 창(3600s) 밖(오래 전) 변경 — feed는 SQL 필터로 걸러지지만, overlay도 창 배제.
        client = FakeClient(rows=[_row("R1", ref - 99999)])
        nearby, candidates = await repo._compute_change_correlation(
            client, event, "R1", change_window_seconds=3600
        )
        assert nearby is False
        assert candidates == []
