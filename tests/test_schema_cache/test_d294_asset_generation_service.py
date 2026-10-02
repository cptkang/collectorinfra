"""D-294 W4 — `AssetGenerationService` (프로파일링 · LLM 보조 · 승인 · 반려 · 되돌리기).

가짜 MCP 세션이 작은 가상 자산 DB(MariaDB 방언)의 데이터로 카탈로그 · `DISTINCT` · 값 겹침 · 코드쌍
조회에 답한다(실제 `DBHubClient` 파싱 경로 통과). LLM은 목, Redis는 페이크, 파일은 tmp. 네트워크 0.

확인하는 계약:
- P1은 LLM 0 · 파일·정본 쓰기 0(초안만) · 관계는 선언·추론·같은 기본키 군 중 값 겹침 ≥ 0.9만 채택 ·
  공통 컬럼 단독 기본키는 부모 아님 · 코드값·라벨(주석 열거 · 공통코드 테이블) · 날짜 규칙 · 식별 키
  ·
  행 수 0 테이블은 조회 대상 후보에서 빠짐 · 주석 → 설명 초안
- DB에 닿지 못하면 스키마만으로 만든다(예외 없음)
- P2는 실행 성공한 예시만 · 섹션은 식별자 실존·중괄호·SQL 실행으로 거른다
- 승인은 사람 값 보존 · 시드는 기존 O-7 로더로 적재(값 유사어는 DB 공용 사전 · 132 G-13) · 섹션 파일
  · 재승인 409
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.api.routes.db_structure import asset_sql_checker
from src.schema_cache.asset_generation_service import AssetGenerationService
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DraftNotApprovable
from tests.test_schema_cache.test_plan104_service_fixtures import (
    Env,
    FakeTable,
    failing_client_factory,
    make_env,
    make_registry,
)

SRC = "app_asset"
PK3 = ["grp", "hostNm", "ipAddr"]

DATA: dict[str, list[dict[str, Any]]] = {
    "t_srv": [
        {"grp": "G1", "hostNm": "web01", "ipAddr": "10.0.0.1", "statCd": "1", "useYn": "Y",
         "regYmd": "20260101", "partTypCd": "A"},
        {"grp": "G1", "hostNm": "WEB02", "ipAddr": "10.0.0.2,10.0.0.3", "statCd": "2",
         "useYn": "N", "regYmd": "20260215", "partTypCd": "B"},
        {"grp": "G1", "hostNm": "db01.example.com", "ipAddr": "10.0.0.4", "statCd": "1",
         "useYn": "Y", "regYmd": "20260301", "partTypCd": "A"},
    ],
    "t_hw": [
        {"grp": "G1", "hostNm": "web01", "ipAddr": "10.0.0.1", "endYmd": "20290101"},
        {"grp": "G1", "hostNm": "WEB02", "ipAddr": "10.0.0.2,10.0.0.3", "endYmd": "20290101"},
    ],
    "t_part": [
        {"grp": "G1", "hostNm": "web01", "ipAddr": "10.0.0.1", "seq": "1", "partNm": "disk"},
        {"grp": "G1", "hostNm": "ghost", "ipAddr": "10.9.9.9", "seq": "1", "partNm": "nic"},
    ],
    "t_grp": [{"grp": "G1", "coNm": "본사"}],
    "t_cmcode": [{"codeCd": "A", "codeNm": "서버"}, {"codeCd": "B", "codeNm": "네트워크"}],
    "t_empty": [],
}
COMMENTS = {
    ("t_srv", "statCd"): "상태(1:정상, 2:장애)",
    ("t_srv", "hostNm"): "서버 호스트명",
    ("t_srv", "regYmd"): "등록 일자",
}
ROWS = {"t_srv": 3, "t_hw": 2, "t_part": 2, "t_grp": 1, "t_cmcode": 2, "t_empty": 0}


def _tables() -> dict[str, FakeTable]:
    v = "varchar"
    return {
        "t_srv": FakeTable([("grp", v, False, True), ("hostNm", v, False, True),
                            ("ipAddr", v, False, True), ("statCd", "char", True, False),
                            ("useYn", "char", True, False), ("regYmd", v, True, False),
                            ("partTypCd", v, True, False)], schema="app"),
        "t_hw": FakeTable([("grp", v, False, True), ("hostNm", v, False, True),
                           ("ipAddr", v, False, True), ("endYmd", v, True, False)], schema="app"),
        "t_part": FakeTable([("grp", v, False, True), ("hostNm", v, False, True),
                             ("ipAddr", v, False, True), ("seq", v, False, True),
                             ("partNm", v, True, False)], schema="app"),
        "t_grp": FakeTable([("grp", v, False, True), ("coNm", v, True, False)], schema="app"),
        "t_cmcode": FakeTable([("codeCd", v, False, True), ("codeNm", v, True, False)],
                              schema="app"),
        "t_empty": FakeTable([("id", "int", False, True)], schema="app"),
    }


_DISTINCT_RE = re.compile(r"^SELECT DISTINCT (\w+) FROM (\w+) WHERE")
_PAIRS_RE = re.compile(r"^SELECT DISTINCT (\w+) AS code_value, (\w+) AS code_label FROM (\w+)")
_OVERLAP_RE = re.compile(
    r"FROM \(SELECT DISTINCT (.*?) FROM (\w+) c WHERE .*? LEFT JOIN (\w+) p ON (.*)$"
)


def _sql_rows(_source: str, sql: str) -> list[dict[str, Any]]:
    if "information_schema.tables" in sql:
        return [{"table_name": t, "table_comment": "공통코드" if t == "t_cmcode" else "",
                 "row_estimate": n} for t, n in ROWS.items()]
    if "information_schema.columns" in sql:
        return [{"table_name": t, "column_name": c, "column_comment": text}
                for (t, c), text in COMMENTS.items()]
    if (m := _PAIRS_RE.match(sql)):
        code, name, table = m.groups()
        return [{"code_value": r[code], "code_label": r[name]} for r in DATA[table]]
    if (m := _DISTINCT_RE.match(sql)):
        column, table = m.groups()
        return [{column: v} for v in dict.fromkeys(r[column] for r in DATA[table])]
    if (m := _OVERLAP_RE.search(sql)):
        select, child, parent, on = m.groups()
        child_cols = re.findall(r"c\.(\w+) AS k\d", select)
        parent_cols = re.findall(r"p\.(\w+) = s\.k\d", on)
        child_keys = {tuple(r[c] for c in child_cols) for r in DATA[child]}
        parent_keys = {tuple(r[c] for c in parent_cols) for r in DATA[parent]}
        return [{"sampled": len(child_keys), "matched": len(child_keys & parent_keys)}]
    if sql.upper().startswith("SELECT"):  # LLM 예시·섹션 SQL 실행
        if "nope" in sql:
            raise RuntimeError("Unknown column 'nope'")
        return [{"n": 1}]
    raise AssertionError(f"예상하지 못한 SQL: {sql}")


GOOD_SECTION = (
    "### 업무 개요\n`t_srv`는 서버 목록이다. `t_hw`와 `grp`·`hostNm`·`ipAddr`로 조인한다.\n\n"
    "### 자주 쓰는 SQL\n```sql\nSELECT s.hostNm FROM t_srv s WHERE s.useYn = 'Y' LIMIT 50\n```\n"
)


class FakeLLM:
    """쿼리 예시·섹션 프롬프트를 가려 응답하는 목 LLM."""

    def __init__(self, section: str = GOOD_SECTION) -> None:
        self.section = section
        self.calls: list[str] = []

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        prompt = "\n".join(str(getattr(m, "content", m)) for m in messages)
        if "SQL 예시를 만드는 전문가" in prompt:
            self.calls.append("examples")
            return SimpleNamespace(content=json.dumps([
                {"question": "사용 중 서버 목록",
                 "sql": "SELECT hostNm FROM t_srv WHERE useYn = 'Y' LIMIT 50"},
                {"question": "행 제한 없음", "sql": "SELECT hostNm FROM t_srv"},
                {"question": "없는 컬럼", "sql": "SELECT nope FROM t_srv LIMIT 5"},
            ], ensure_ascii=False))
        assert "시스템 프롬프트를 쓰는 전문가" in prompt, prompt[:80]
        self.calls.append("section")
        return SimpleNamespace(content=self.section)


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "mariadb", _tables())
    e.session.sql_rows = _sql_rows
    e.registry = make_registry({"db_id": SRC, "engine": "mariadb", "description": "가상 자산 DB"})
    return e


def _service(env: Env, llm: Any | None = None, **overrides: Any) -> AssetGenerationService:
    kwargs: dict[str, Any] = {**env.service_kwargs(), "sql_checker": asset_sql_checker}
    if llm is not None:
        kwargs["llm_factory"] = lambda: llm
    kwargs.update(overrides)
    return AssetGenerationService(env.config, env.mgr, **kwargs)


async def _snapshot(env: Env) -> None:
    await DBRegistrationService(env.config, env.mgr, **env.service_kwargs()).run_register(
        SRC, steps=["schema"], tables=None, by="admin", ctx=env.ctx,
    )


async def _profile(env: Env, **kw: Any) -> dict[str, Any]:
    await _snapshot(env)
    result = await _service(env, **kw).run_asset_profile(SRC, tables=None, by="admin", ctx=env.ctx)
    draft = await env.store.get_asset_draft(SRC, result["draft_id"])
    assert draft is not None
    return draft


class TestProfile:
    async def test_assets_and_evidence(self, env):
        llm = FakeLLM()
        draft = await _profile(env, llm=llm)
        assets = draft["assets"]

        rels = {(r["from"], r["to"], r["origin"]) for r in assets["relationships"]}
        assert ("t_hw.hostNm", "t_srv.hostNm", "same_key") in rels  # 같은 기본키 군 · 겹침 1.0
        assert not any(r[0].startswith("t_part.") for r in rels)  # 겹침 0.5 → 채택 안 함
        assert not any(r[1].startswith("t_grp.") for r in rels)  # 공통 컬럼 단독 기본키
        part = next(e for e in draft["evidence"]["relationships"] if e["child"] == "t_part")
        assert part["overlap"] == 0.5 and part["accepted"] is False

        assert assets["code_values"]["t_srv.statCd"] == ["1", "2"]
        assert assets["code_labels"]["t_srv.statCd"] == {"1": "정상", "2": "장애"}
        assert assets["code_labels"]["t_srv.partTypCd"] == {"A": "서버", "B": "네트워크"}
        assert any("'YYYYMMDD'" in r and "t_srv.regYmd" in r for r in assets["query_rules"])
        assert any("플래그" in r and "t_srv.useYn" in r for r in assets["query_rules"])
        assert assets["entity_keys"] == {
            "entity": "server", "table": "t_srv",
            "keys": [{"type": "hostname", "column": "hostNm", "priority": 1, "compare": "casefold"},
                     {"type": "ip", "column": "ipAddr", "priority": 2, "multi_value": True}],
        }
        assert "t_empty" not in assets["allowed_tables"] and "t_srv" in assets["allowed_tables"]
        assert assets["seeds"]["column_synonyms"]["t_srv.hostNm"] == ["서버 호스트명"]
        assert assets["seeds"]["column_values"]["t_srv.statCd"] == {
            "정상": {"op": "=", "value": "1"}, "장애": {"op": "=", "value": "2"},
        }

        assert llm.calls == [] and draft["llm_calls"] == 0  # P1은 LLM 0
        assert draft["status"] == "pending" and draft["evidence"]["budget"]["used"] > 0
        assert not (env.tmp / "config" / "synonym_seeds").exists()  # 승인 전 파일 쓰기 0
        assert env.store.read_current_profile(SRC) is None
        desc = await env.store.get_description_draft(SRC, draft["description_draft_id"])
        assert desc["origin"] == "comment"
        assert desc["descriptions"]["t_srv"]["t_srv.statCd"] == "상태(1:정상, 2:장애)"

    async def test_offline_builds_schema_only_assets(self, env):
        await _snapshot(env)
        await env.store.save_ddl_comments(SRC, {"t_srv.hostNm": "서버 호스트명"})
        service = _service(env, client_factory=failing_client_factory(ConnectionError("down")))

        result = await service.run_asset_profile(SRC, tables=None, by="a", ctx=env.ctx)

        draft = await env.store.get_asset_draft(SRC, result["draft_id"])
        assert result["offline"] and "down" in result["offline"]
        assert draft["assets"]["relationships"] == [] and draft["assets"]["code_values"] == {}
        assert draft["assets"]["seeds"]["column_synonyms"] == {"t_srv.hostNm": ["서버 호스트명"]}
        assert all(e["error"] for e in draft["evidence"]["relationships"])

    async def test_requires_snapshot(self, env):
        with pytest.raises(ValueError, match="스냅샷이 없습니다"):
            await _service(env).run_asset_profile(SRC, tables=None, by="a", ctx=env.ctx)


class TestLlmAndApprove:
    async def test_llm_validation_then_approve_everything(self, env):
        llm = FakeLLM()
        draft = await _profile(env, llm=llm)
        service = _service(env, llm=llm)
        env.write_profile(SRC, {"source": "manual", "allowed_tables": ["t_srv"],
                                "query_guide": "사람 안내"})

        llm_result = await service.run_asset_llm(SRC, draft["draft_id"], by="a", ctx=env.ctx)

        assert llm_result["query_examples"] == 1 and llm_result["prompt_template_passed"] is True
        updated = await env.store.get_asset_draft(SRC, draft["draft_id"])
        checks = updated["validation"]["query_examples"]["checks"]
        assert [c["error"] is None for c in checks] == [True, False, False]

        approved = await service.approve_asset_draft(
            SRC, draft["draft_id"], include=list(updated["assets"].keys() & {
                "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
                "query_examples", "seeds", "prompt_template"}),
            allowed_tables=None, by="admin", reason="검토 완료",
        )

        profile = env.store.read_current_profile(SRC)["profile"]
        assert profile["allowed_tables"] == ["t_srv"] and profile["query_guide"] == "사람 안내"
        assert profile["code_labels"]["t_srv.statCd"]["1"] == "정상"
        assert profile["entity_keys"]["table"] == "t_srv"
        assert profile["query_examples"][0]["question"] == "사용 중 서버 목록"
        seeds_path = env.tmp / "config" / "synonym_seeds" / f"{SRC}.yaml"
        seeds = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))
        assert seeds["db_id"] == SRC
        assert seeds["column_values"]["t_srv.statCd"]["장애"]["value"] == "2"
        seeds_applied = approved["applied"]["seeds"]
        expected_values = len(updated["assets"]["seeds"]["column_values"])
        assert seeds_applied["column_values_loaded"] == expected_values
        redis_synonyms = await env.mgr._redis_cache.load_synonyms(SRC)
        assert "서버 호스트명" in (redis_synonyms.get("t_srv.hostNm") or [])
        # DB 공용 사전(폴스타·ITAM 공유 — plans/132 G-13)에 테이블 한정 키로 들어간다
        value_synonyms = await env.mgr.get_column_value_synonyms()
        assert value_synonyms["T_SRV.STATCD"]["장애"] == {"op": "=", "value": "2"}
        section = yaml.safe_load(
            (env.tmp / "config" / "knowledge" / SRC / "prompt_template.yaml").read_text("utf-8")
        )
        assert section["section"].startswith("### 업무 개요")

        with pytest.raises(DraftNotApprovable) as err:
            await service.approve_asset_draft(SRC, draft["draft_id"], include=["seeds"],
                                              allowed_tables=None, by="a", reason="")
        assert err.value.code == "not_pending"

    async def test_bad_section_is_not_approvable(self, env):
        llm = FakeLLM(section="### 업무 개요\n`t_unknown` 를 쓴다 { }\n")
        draft = await _profile(env, llm=llm)
        service = _service(env, llm=llm)
        await service.run_asset_llm(SRC, draft["draft_id"], by="a", ctx=env.ctx)

        updated = await env.store.get_asset_draft(SRC, draft["draft_id"])
        check = updated["validation"]["prompt_template"]
        assert check["passed"] is False and check["unknown_identifiers"] == ["t_unknown"]
        assert any("중괄호" in e for e in check["errors"])
        with pytest.raises(DraftNotApprovable) as err:
            await service.approve_asset_draft(SRC, draft["draft_id"], include=["prompt_template"],
                                              allowed_tables=None, by="a", reason="")
        assert err.value.code == "not_available"

    async def test_unvalidated_examples_and_unknown_kind_rejected(self, env):
        draft = await _profile(env)
        service = _service(env)
        with pytest.raises(DraftNotApprovable) as err:
            await service.approve_asset_draft(SRC, draft["draft_id"], include=["query_examples"],
                                              allowed_tables=None, by="a", reason="")
        assert err.value.code == "not_available"  # P2 전이라 예시가 없다
        with pytest.raises(ValueError, match="모르는 자산"):
            await service.approve_asset_draft(SRC, draft["draft_id"], include=["profile"],
                                              allowed_tables=None, by="a", reason="")

    async def test_allowed_tables_override_reject_and_rollback(self, env):
        draft = await _profile(env)
        service = _service(env)
        await service.approve_asset_draft(
            SRC, draft["draft_id"], include=["allowed_tables", "seeds"],
            allowed_tables=["t_srv", "t_hw", "t_nope"], by="a", reason="",
        )
        assert env.store.read_current_profile(SRC)["profile"]["allowed_tables"] == ["t_srv", "t_hw"]

        second = await _service(env).run_asset_profile(SRC, tables=["t_srv"], by="a", ctx=env.ctx)
        rejected = await service.reject_asset_draft(
            SRC, second["draft_id"], by="a", reason="불필요"
        )
        assert rejected["draft"]["status"] == "rejected"

        seeds_path = env.tmp / "config" / "synonym_seeds" / f"{SRC}.yaml"
        seeds_path.write_text("db_id: app_asset\ncolumn_synonyms: {}\n", encoding="utf-8")
        await service.rollback_asset(SRC, "seeds", 1, by="a", reason="복구")
        assert "서버 호스트명" in seeds_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="되돌리기는"):
            await service.rollback_asset(SRC, "profile", 1, by="a", reason="")


def _executed(env: Env, needle: str) -> list[str]:
    return [sql for sql in env.session.executed_sql() if needle in sql]


class TestReviewHardening:
    """코드 리뷰 지적(2026-10-02) 회귀 — LLM SQL 실행 방어 · 명시 선택 · 부분 적용."""

    async def _llm_run(self, env: Env, section: str, examples: list[dict[str, str]],
                       **service_kw: Any) -> dict[str, Any]:
        llm = FakeLLM(section=section)

        async def ainvoke(messages: list[Any]) -> SimpleNamespace:
            prompt = "\n".join(str(getattr(m, "content", m)) for m in messages)
            if "SQL 예시를 만드는 전문가" in prompt:
                return SimpleNamespace(content=json.dumps(examples, ensure_ascii=False))
            return SimpleNamespace(content=section)

        llm.ainvoke = ainvoke  # type: ignore[method-assign]
        draft = await _profile(env, llm=llm)
        service = _service(env, llm=llm, **service_kw)
        await service.run_asset_llm(SRC, draft["draft_id"], by="a", ctx=env.ctx)
        updated = await env.store.get_asset_draft(SRC, draft["draft_id"])
        assert updated is not None
        return updated

    async def test_dangerous_sql_is_rejected_before_execution(self, env):
        updated = await self._llm_run(env, GOOD_SECTION, [
            {"question": "세션 종료", "sql": "SELECT pg_terminate_backend(1) FROM t_srv LIMIT 5"},
            {"question": "계정 표", "sql": "SELECT user, password FROM mysql.user LIMIT 5"},
            {"question": "정상", "sql": "SELECT hostNm FROM t_srv LIMIT 5"},
        ])

        checks = updated["validation"]["query_examples"]["checks"]
        assert "부수효과" in checks[0]["error"]
        assert "mysql.user" in checks[1]["error"]
        assert checks[2]["error"] is None
        assert _executed(env, "pg_terminate_backend") == [] and _executed(env, "mysql.user") == []
        # 실행은 바깥 행 제한으로 감싼다
        assert _executed(env, "SELECT * FROM (SELECT hostNm FROM t_srv LIMIT 5) q LIMIT 50")

    async def test_without_checker_llm_sql_is_never_executed(self, env):
        updated = await self._llm_run(
            env, GOOD_SECTION, [{"question": "정상", "sql": "SELECT hostNm FROM t_srv LIMIT 5"}],
            sql_checker=None,
        )

        assert updated["validation"]["query_examples"]["passed"] is False
        assert updated["validation"]["prompt_template"]["passed"] is False
        assert _executed(env, ") q ") == []

    async def test_unlabeled_fence_is_checked_and_bad_structure_skips_execution(self, env):
        fenced = "### 업무 개요\n`t_srv` 목록\n```\nSELECT hostNm FROM t_srv\n```\n"
        updated = await self._llm_run(env, fenced, [])
        check = updated["validation"]["prompt_template"]
        assert check["passed"] is False  # 표기 없는 펜스도 SQL로 검사(행 제한 없음 → 실패)
        assert "행 제한" in check["sql_checks"][0]["error"]

        bad = "### 업무 개요\n`t_nope` {x}\n```sql\nSELECT hostNm FROM t_srv LIMIT 5\n```\n"
        env.session.calls.clear()
        updated = await self._llm_run(env, bad, [])
        check = updated["validation"]["prompt_template"]
        assert check["sql_checks"][0]["error"] == "구조 검사 실패로 실행 안 함"
        assert _executed(env, "SELECT hostNm FROM t_srv LIMIT 5) q") == []

    async def test_explicit_table_selection_overrides_and_unchanged_is_reported(self, env):
        draft = await _profile(env)
        env.write_profile(SRC, {
            "source": "manual", "allowed_tables": ["t_grp"],
            "entity_keys": {"entity": "server", "table": "t_hw", "keys": []},
        })

        result = await _service(env).approve_asset_draft(
            SRC, draft["draft_id"], include=["allowed_tables", "entity_keys"],
            allowed_tables=["t_srv", "t_hw"], by="a", reason="",
        )

        profile = env.store.read_current_profile(SRC)["profile"]
        assert profile["allowed_tables"] == ["t_srv", "t_hw"]  # 관리자 선택 그대로
        assert profile["entity_keys"]["table"] == "t_hw"  # 사람 값 보존
        assert result["applied"]["profile"]["unchanged"] == ["entity_keys"]

    async def test_partial_application_is_recorded(self, env, monkeypatch):
        draft = await _profile(env)
        service = _service(env)

        async def broken_apply(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise OSError("디스크 가득 참")

        monkeypatch.setattr(service._assets, "apply", broken_apply)

        result = await service.approve_asset_draft(
            SRC, draft["draft_id"], include=["relationships", "seeds"],
            allowed_tables=None, by="a", reason="",
        )

        assert result["partial"] is True
        assert result["applied"]["profile"]["ver"] == 1  # 프로필은 이미 적용됐다
        assert "디스크 가득 참" in result["applied"]["seeds"]["error"]
        assert result["draft"]["status"] == "partially_applied"
        assert result["draft"]["apply_errors"] == ["seeds"]

    async def test_first_step_failure_writes_nothing(self, env, monkeypatch):
        draft = await _profile(env)
        service = _service(env)

        async def broken_profile(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise OSError("쓰기 실패")

        monkeypatch.setattr(env.store, "apply_profile", broken_profile)

        with pytest.raises(OSError):
            await service.approve_asset_draft(
                SRC, draft["draft_id"], include=["relationships", "seeds"],
                allowed_tables=None, by="a", reason="",
            )
        assert (await env.store.get_asset_draft(SRC, draft["draft_id"]))["status"] == "pending"
        assert not (env.tmp / "config" / "synonym_seeds" / f"{SRC}.yaml").exists()
