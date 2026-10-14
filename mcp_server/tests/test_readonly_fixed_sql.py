"""고정 SQL 읽기 전용 통과 고정 테스트 (plans/163 W0 · S1).

MCP 서버가 코드로 조립하는 모든 고정 SQL(고수준 도구 빌더 · exporter 빌더 · 스키마 조회
헬퍼 · 헬스체크 상수)이 `validate_readonly`를 통과함을 단언한다. W1에서 읽기 전용 검사를
공용 실행 지점으로 옮겨도 고정 SQL 경로가 막히지 않음을 보장하는 회귀 기준선이다.
대표 인자는 엔진(PostgreSQL·DB2·MariaDB)과 선택 인자 on/off 분기를 모두 덮는다.
스키마 조회 헬퍼는 고정 SQL이 아니다 — 도구 입력(table_name)이 들어가는 SQL을 무해한 대표 인자로
만든 기준선이다(PG·DB2 헬퍼의 미이스케이프 보간 주입 거부는 W1 S1 몫).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp_server.db import DBPoolManager  # noqa: E402
from mcp_server.security import validate_readonly  # noqa: E402

from mcp_server import polestar_exporter as ex  # noqa: E402
from mcp_server import polestar_tools as pt  # noqa: E402
from mcp_server import tools as tl  # noqa: E402

_REF = "2026-10-01T14:00:00"
_ENGINES = (("pg", False), ("db2", True))


def _builder_cases() -> list[tuple[str, Callable[[], str]]]:
    """(케이스 ID, SQL 생성 호출) 목록 — 빌더별 엔진·선택 인자 분기를 펼친다."""
    cases: list[tuple[str, Callable[[], str]]] = []

    def add(case_id: str, fn: Callable[[], str]) -> None:
        cases.append((case_id, fn))

    for eng, d in _ENGINES:
        for exc in (None, "12345", "abc-1"):
            for ref, lb in ((None, None), (_REF, None), (_REF, 90)):
                add(
                    f"alarm_history-{eng}-exc{exc}-ref{bool(ref)}-lb{lb}",
                    lambda d=d, exc=exc, ref=ref, lb=lb: pt.build_alarm_history_sql(
                        d, "web01", "CPU High", 168, exc, 1000,
                        reference_time=ref, lookback_minutes=lb,
                    ),
                )
        # 값 리터럴 안의 금지 키워드는 리터럴 마스킹으로 오탐하지 않는다.
        add(
            f"alarm_history-{eng}-keyword-in-literal",
            lambda d=d: pt.build_alarm_history_sql(
                d, "o'drop", "Process Kill", 168, None, 1000
            ),
        )
        for an in (None, "CPU High"):
            add(
                f"incident_alarms-{eng}-name{bool(an)}",
                lambda d=d, an=an: pt.build_incident_alarms_sql(
                    d, "web01", _REF, 60, an, 1000
                ),
            )
        for kind in ("cpu", "memory", "filesystem", "disk_io"):
            for gran in ("h", "d", "m"):
                for ref, lb, bp in ((None, None, None), (_REF, None, None), (_REF, 120, 3)):
                    add(
                        f"metric_trend-{eng}-{kind}-{gran}-ref{bool(ref)}-lb{lb}-bp{bp}",
                        lambda d=d, kind=kind, gran=gran, ref=ref, lb=lb, bp=bp: (
                            pt.build_metric_trend_sql(
                                d, "web01", kind, gran, 24, reference_time=ref,
                                lookback_minutes=lb, baseline_periods=bp,
                            )
                        ),
                    )
        add(f"resource_status-{eng}", lambda d=d: pt.build_resource_status_sql(d, "web01", 1000))
        add(f"topology-{eng}", lambda d=d: pt.build_topology_sql(d, "web01", 3, 1000))
        for aid in ("12345", "A-1"):
            add(
                f"condition_log-{eng}-{aid}",
                lambda d=d, aid=aid: pt.build_condition_log_sql(d, aid),
            )
        add(f"os_config-{eng}", lambda d=d: pt.build_os_config_sql(d, "host01", 1000))
        add(f"exporter_server_info-{eng}", lambda d=d: ex.build_server_info_sql(d, 5000))
        add(
            f"exporter_latest_utilization-{eng}",
            lambda d=d: ex.build_latest_utilization_sql(d, "2026100114", 5000),
        )
        add(
            f"exporter_active_alarm_count-{eng}",
            lambda d=d: ex.build_active_alarm_count_sql(d, 5000),
        )
    # 변경 이력은 PostgreSQL 전용 빌더다.
    for until in (None, 1760000000):
        add(
            f"change_history-pg-until{bool(until)}",
            lambda until=until: pt.build_change_history_sql(
                "web01", 1759900000, 1000, until_epoch=until
            ),
        )
    for fn in (
        tl._pg_search_objects_sql,
        tl._db2_search_objects_sql,
        tl._mariadb_search_objects_sql,
    ):
        for pat in ("*", "srv_*"):
            for typ in ("table", "view"):
                add(f"{fn.__name__}-{pat}-{typ}", lambda fn=fn, pat=pat, typ=typ: fn(pat, typ))
    return cases


_BUILDER_CASES = _builder_cases()


@pytest.mark.parametrize(
    "build", [c[1] for c in _BUILDER_CASES], ids=[c[0] for c in _BUILDER_CASES]
)
def test_builder_sql_passes_readonly(build: Callable[[], str]) -> None:
    """고정 SQL 빌더 출력은 읽기 전용 검사를 통과한다."""
    validate_readonly(build())


def test_builder_cases_cover_every_public_builder() -> None:
    """두 모듈의 공개 `build_*_sql` 빌더가 전부 위 케이스에 들어 있다(새 빌더 누락 방지)."""
    covered = {
        "alarm_history", "incident_alarms", "metric_trend", "resource_status",
        "topology", "condition_log", "os_config", "change_history",
        "exporter_server_info", "exporter_latest_utilization", "exporter_active_alarm_count",
    }
    found: set[str] = set()
    for prefix, mod in (("", pt), ("exporter_", ex)):
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            is_builder = name.startswith("build_") and name.endswith("_sql")
            if obj.__module__ == mod.__name__ and is_builder:
                found.add(prefix + name[len("build_"):-len("_sql")])
    assert found == covered
    ids = {c[0].split("-")[0] for c in _BUILDER_CASES}
    assert covered <= ids


class _CapturePool:
    """`execute`로 넘어온 SQL만 모으는 가짜 풀."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, source: str, sql: str) -> list[dict[str, Any]]:
        self.sql.append(sql)
        return []


_SCHEMA_HELPERS = [
    "_pg_get_columns", "_pg_get_primary_keys", "_pg_get_foreign_keys",
    "_db2_get_columns", "_db2_get_primary_keys", "_db2_get_foreign_keys",
    "_mariadb_get_columns", "_mariadb_get_primary_keys", "_mariadb_get_foreign_keys",
]


@pytest.mark.parametrize("table_name", ["servers", "myschema.servers"])
@pytest.mark.parametrize("helper", _SCHEMA_HELPERS)
async def test_schema_helper_sql_passes_readonly(helper: str, table_name: str) -> None:
    """스키마 조회 헬퍼가 실행하는 SQL은 읽기 전용 검사를 통과한다."""
    pool = _CapturePool()
    await getattr(tl, helper)(pool, "src", table_name)
    assert len(pool.sql) == 1
    validate_readonly(pool.sql[0])


@pytest.mark.parametrize(
    ("registry", "runner"),
    [
        ("_pg_pools", "_execute_pg"),
        ("_db2_configs", "_execute_db2"),
        ("_mariadb_pools", "_execute_mariadb"),
    ],
)
async def test_health_check_sql_passes_readonly(
    registry: str, runner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """헬스체크가 엔진 러너로 직접 보내는 상수 SQL도 읽기 전용 검사를 통과한다."""
    pm = DBPoolManager([])
    getattr(pm, registry)["src"] = object()
    seen: list[str] = []

    async def _capture(source: str, sql: str) -> list[dict[str, Any]]:
        seen.append(sql)
        return [{"ok": 1}]

    monkeypatch.setattr(pm, runner, _capture)
    assert await pm.health_check("src") is True
    assert len(seen) == 1
    validate_readonly(seen[0])
