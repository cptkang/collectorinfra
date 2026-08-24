"""multi_db_executor 노드 본체의 루프 동작 테스트 (Plan 69 P1-2 / §1.8 공백 보강).

노드 본체(미등록 DB 스킵 · 동일 스키마 SQL 재사용 캐시 · 간이 검증 실패 시 1회 재생성 ·
부분 실패 누적)는 테스트가 0건이었다. P3~P5에서 프롬프트 조립·검증·감사가 공유 빌더로
옮겨갈 때 이 루프 골격이 그대로 유지되는지 판정하는 기준선이다.

LLM·DB 호출은 전부 결정적 목이다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.nodes import multi_db_executor as mdb
from src.state import create_initial_state


def _config():
    """검증 대상 필드만 명시한 설정 대역(.env 누수 차단).

    멀티 검증 강화(P4-3)는 명시 OFF — 이 파일은 간이 검증을 쓰는 현행 루프 골격을
    고정하는 것이 목적이고, 검증기 교체 자체는 별도 테스트의 소관이다.
    """
    return SimpleNamespace(
        query=SimpleNamespace(default_limit=100),
        text2sql=SimpleNamespace(multi_full_validation=False),
    )


class _ClientCtx:
    """execute_sql 결과(또는 예외)를 지정할 수 있는 DB 클라이언트 컨텍스트."""

    def __init__(self, db_id: str, rows_by_db: dict, errors_by_db: dict, calls: list):
        self._db_id = db_id
        self._rows_by_db = rows_by_db
        self._errors_by_db = errors_by_db
        self._calls = calls

    async def __aenter__(self):
        client = AsyncMock()

        async def _execute(sql: str):
            self._calls.append((self._db_id, sql))
            if self._db_id in self._errors_by_db:
                raise self._errors_by_db[self._db_id]
            rows = self._rows_by_db.get(self._db_id, [])
            return SimpleNamespace(rows=rows, row_count=len(rows))

        client.execute_sql = _execute
        return client

    async def __aexit__(self, *args):
        return False


class _Harness:
    """레지스트리·스키마·SQL 생성·검증·감사를 결정적 대역으로 갈아끼운 실행 환경."""

    def __init__(self, monkeypatch, *, registered=None, domains=None,
                 rows_by_db=None, errors_by_db=None, validation_errors=None):
        self.registered = registered            # None이면 전부 등록된 것으로 본다
        self.domains = domains or {}            # db_id -> (engine, schema)
        self.rows_by_db = rows_by_db or {}
        self.errors_by_db = errors_by_db or {}
        self.validation_errors = list(validation_errors or [])  # 호출 순서대로 소비
        self.generate_calls: list[dict] = []
        self.execute_calls: list[tuple[str, str]] = []
        self.client_requests: list[str] = []
        self.audit = AsyncMock()

        harness = self

        class _Registry:
            def __init__(self, cfg):
                pass

            def is_registered(self, db_id):
                return harness.registered is None or db_id in harness.registered

            def get_client(self, db_id):
                harness.client_requests.append(db_id)
                return _ClientCtx(
                    db_id, harness.rows_by_db, harness.errors_by_db, harness.execute_calls
                )

        async def _schema(client, parsed, *, db_id, app_config, **kwargs):
            return {"tables": {"servers": {"columns": [{"name": "hostname", "type": "text"}]}}}

        async def _generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            harness.generate_calls.append({
                "db_id": kwargs.get("db_id"),
                "db_engine": kwargs.get("db_engine"),
                "error_context": kwargs.get("error_context"),
            })
            return f"SELECT hostname FROM servers -- gen{len(harness.generate_calls)}"

        def _validate(sql, schema_info):
            if harness.validation_errors:
                return harness.validation_errors.pop(0)
            return None

        def _domain(db_id):
            spec = harness.domains.get(db_id)
            if spec is None:
                return None
            engine, schema = spec
            return SimpleNamespace(db_engine=engine, db_schema=schema)

        monkeypatch.setattr(mdb, "DBRegistry", _Registry)
        monkeypatch.setattr(mdb, "_analyze_schema", _schema)
        monkeypatch.setattr(mdb, "_generate_sql", _generate)
        monkeypatch.setattr(mdb, "_validate_sql_simple", _validate)
        monkeypatch.setattr(mdb, "get_domain_by_id", _domain)
        monkeypatch.setattr(mdb, "log_query_execution", self.audit)

    async def run(self, db_ids: list[str], **state_overrides) -> dict:
        state = create_initial_state(user_query="서버 목록")
        state["target_databases"] = [{"db_id": db_id} for db_id in db_ids]
        state.update(state_overrides)
        return await mdb.multi_db_executor(state, llm=AsyncMock(), app_config=_config())


class TestUnregisteredDbSkipped:
    async def test_unregistered_db_is_skipped_without_client(self, monkeypatch):
        h = _Harness(monkeypatch, registered={"db_ok"}, rows_by_db={"db_ok": [{"hostname": "a"}]})

        result = await h.run(["db_missing", "db_ok"])

        assert "레지스트리에 등록되지 않았습니다" in result["db_errors"]["db_missing"]
        assert h.client_requests == ["db_ok"], "미등록 DB는 클라이언트를 열지 않는다"
        assert list(result["db_results"]) == ["db_ok"]
        assert "db_missing" not in result["db_schemas"]

    async def test_all_unregistered_reports_total_failure(self, monkeypatch):
        h = _Harness(monkeypatch, registered=set())

        result = await h.run(["db_a", "db_b"])

        assert result["db_results"] == {}
        assert set(result["db_errors"]) == {"db_a", "db_b"}
        assert result["error_message"] == "모든 DB 쿼리가 실패했습니다."
        assert h.generate_calls == []


class TestSqlReuseBySchema:
    """동일 (engine, schema) DB는 SQL을 1회만 생성해 alias 일관성을 지킨다(D-066 후속6)."""

    async def test_same_schema_generates_sql_once(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            domains={"gp": ("postgresql", "polestar"), "yd": ("postgresql", "polestar")},
            rows_by_db={"gp": [{"hostname": "a"}], "yd": [{"hostname": "b"}]},
        )

        result = await h.run(["gp", "yd"])

        assert len(h.generate_calls) == 1, "동일 스키마인데 SQL을 두 번 생성했다"
        assert [db for db, _ in h.execute_calls] == ["gp", "yd"], "두 DB 모두 실행돼야 한다"
        assert h.execute_calls[0][1] == h.execute_calls[1][1], "재사용 SQL이 동일해야 한다"
        assert set(result["db_results"]) == {"gp", "yd"}
        assert len(result["query_attempts"]) == 2

    async def test_different_schema_generates_per_db(self, monkeypatch):
        """엔진 또는 스키마가 다르면 캐시 키가 갈려 DB별로 생성한다."""
        h = _Harness(
            monkeypatch,
            domains={"gp": ("postgresql", "polestar"), "b0": ("db2", "POLESTAR")},
            rows_by_db={"gp": [{"hostname": "a"}], "b0": [{"hostname": "b"}]},
        )

        await h.run(["gp", "b0"])

        assert [c["db_id"] for c in h.generate_calls] == ["gp", "b0"]
        assert h.execute_calls[0][1] != h.execute_calls[1][1]


class TestSimpleValidationRetry:
    """간이 검증 실패 시 에러 컨텍스트를 실어 최대 2회 재생성한다(총 3회 시도).

    단일 경로 재시도 3회와 대칭(D-153 후속1) — 동일 스키마 복구원이 없는 조합은
    재생성 횟수가 유일한 방어선이다(ux_improvement 병합 승계).
    """

    async def test_first_failure_triggers_single_regeneration(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            rows_by_db={"db_a": [{"hostname": "a"}]},
            validation_errors=["금지 키워드 포함: DROP"],  # 1차 실패 → 2차는 통과
        )

        result = await h.run(["db_a"])

        assert len(h.generate_calls) == 2, "검증 실패 후 1회 재생성해야 한다"
        assert h.generate_calls[0]["error_context"] is None
        assert h.generate_calls[1]["error_context"] == "금지 키워드 포함: DROP"
        assert result["db_results"]["db_a"] == [{"hostname": "a"}]
        assert result["db_errors"] == {}

    async def test_second_failure_gives_up_on_that_db(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            validation_errors=["1차 실패", "2차 실패", "3차 실패"],
        )

        result = await h.run(["db_a"])

        assert len(h.generate_calls) == 3, "재시도는 2회로 제한된다(총 3회 시도, D-153 후속1)"
        assert result["db_errors"]["db_a"] == "SQL 검증 실패: 3차 실패"
        assert h.execute_calls == [], "검증을 통과하지 못한 SQL은 실행하지 않는다"
        assert result["error_message"] == "모든 DB 쿼리가 실패했습니다."


class TestPartialFailureAccumulates:
    """DB 하나가 실패해도 성공분은 반환하고 실패는 사유·attempt로 남는다."""

    async def test_one_failure_keeps_other_result(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            domains={"db_ok": ("postgresql", "s"), "db_bad": ("postgresql", "s")},
            rows_by_db={"db_ok": [{"hostname": "web-01"}]},
            errors_by_db={"db_bad": RuntimeError("connection reset")},
        )

        result = await h.run(["db_ok", "db_bad"])

        assert list(result["db_results"]) == ["db_ok"]
        assert list(result["db_errors"]) == ["db_bad"]
        assert "connection reset" in result["db_errors"]["db_bad"]
        assert result["error_message"] is None, "부분 성공은 전체 실패가 아니다"

        attempts = result["query_attempts"]
        assert len(attempts) == 2
        assert [a["success"] for a in attempts] == [True, False]
        assert attempts[1]["sql"] == attempts[0]["sql"], "실패 attempt에도 실행 SQL이 남는다"
        assert h.audit.await_count == 2, "성공·실패 모두 감사 기록"

    async def test_existing_attempts_are_preserved(self, monkeypatch):
        h = _Harness(monkeypatch, rows_by_db={"db_a": [{"hostname": "a"}]})
        prior = [{"sql": "SELECT 1", "success": True, "error": None,
                  "row_count": 1, "execution_time_ms": 1.0}]

        result = await h.run(["db_a"], query_attempts=prior)

        assert len(result["query_attempts"]) == 2
        assert result["query_attempts"][0]["sql"] == "SELECT 1"
        assert len(prior) == 1, "입력 이력 리스트를 제자리 변형하지 않는다"

    async def test_merged_results_tag_source_db(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            domains={"gp": ("postgresql", "s"), "yd": ("postgresql", "s")},
            rows_by_db={"gp": [{"hostname": "a"}], "yd": [{"hostname": "b"}]},
        )

        result = await h.run(["gp", "yd"])

        assert [row["_source_db"] for row in result["query_results"]] == ["gp", "yd"]
        assert result["current_node"] == "multi_db_executor"
