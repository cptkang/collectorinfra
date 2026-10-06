"""plans/139 W3 — 테이블 정의 자산(`AssetGenerationService` · D-308 ①).

초안 경로 3가지(가져오기 · 테이블 주석 · LLM 묶음)와 검토(편집 → `manual`) · 테이블 단위 선택 승인 ·
되돌리기 · 준비도 C11 배선을 확인한다. LLM은 목, Redis는 페이크, MCP는 가짜 세션, 파일은 tmp —
네트워크 0.

확인하는 계약:
- 가져오기: 시드 형식 YAML → 결정적 검증 → 초안(오류 행 표시 · LLM 0 · 파일 쓰기 0)
- 주석: P1 카탈로그 테이블 주석 → 출처 `comment` 정의(검증 실패면 오류 행)
- LLM: 주석·다른 출처 정의·승인된 사람 정의가 있는 테이블은 빼고 군 접두 단위 10개씩 · 예상 호출 수
  = 실제 호출 수 · 동시성 상한 · 입력에 컬럼 설명·코드 라벨·승인 관계·DB 설명(표본 값 없음) ·
  JSON 파싱·호출 실패와 검증 실패는 오류 행 · 실패 묶음만 재실행
- 승인: 검증 실패 행이 선택에 있으면 409(`validation_failed`) · 고른 행만 적용 ·
  base `manual` 보존 ·
  프로필 「버전 이력」 되돌리기는 바이트 동일
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.api.routes.db_structure import asset_sql_checker
from src.schema_cache.asset_generation_service import (
    AssetGenerationService,
    definition_batches,
)
from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService, DraftNotApprovable
from tests.test_schema_cache.test_d294_asset_generation_service import (
    SRC as ASSET_SRC,
)
from tests.test_schema_cache.test_d294_asset_generation_service import (
    FakeLLM,
    _sql_rows,
    _tables,
)
from tests.test_schema_cache.test_plan104_service_fixtures import (
    Env,
    FakeTable,
    failing_client_factory,
    make_env,
    make_registry,
)

SRC = "app_defs"
SEED_DIR = Path(__file__).resolve().parents[2] / "testdata" / "itam_bench" / "closed"
_TABLE_HEADER_RE = re.compile(r"^### (\S+)$", re.MULTILINE)


def _wide_tables() -> dict[str, FakeTable]:
    """군 접두 3종 — `t_alpha01~12`(12) · `t_beta1~3`(3) · `t_gamma`(1)."""
    v = "varchar"
    tables: dict[str, FakeTable] = {}
    for i in range(1, 13):
        tables[f"t_alpha{i:02d}"] = FakeTable(
            [("id", v, False, True), ("nm", v, True, False)], schema="app",
        )
    for i in range(1, 4):
        tables[f"t_beta{i}"] = FakeTable(
            [("id", v, False, True), ("code", "char", True, False)], schema="app",
        )
    tables["t_gamma"] = FakeTable([("id", v, False, True)], schema="app")
    return tables


def tables_in(prompt: str) -> list[str]:
    return _TABLE_HEADER_RE.findall(prompt)


def good_answer(prompt: str) -> str:
    return json.dumps({
        t: {"manages": f"{t} 정보를 관리한다.", "kind": "현행", "key_columns": ["id"]}
        for t in tables_in(prompt)
    }, ensure_ascii=False)


class DefinitionLLM:
    """테이블 정의 프롬프트만 받는 목 LLM — 동시 실행 수와 프롬프트를 기록한다."""

    def __init__(self, respond: Any = good_answer) -> None:
        self.respond = respond
        self.prompts: list[str] = []
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        prompt = "\n".join(str(getattr(m, "content", m)) for m in messages)
        assert "테이블이 관리하는 정보를 정리하는 전문가" in prompt, prompt[:80]
        self.prompts.append(prompt)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            answer = self.respond(prompt)
        finally:
            self.active -= 1
        return SimpleNamespace(content=answer)


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "mariadb", _wide_tables())
    e.registry = make_registry(
        {"db_id": SRC, "engine": "mariadb", "description": "가상 업무 DB(레지스트리 설명)"}
    )
    return e


def _service(env: Env, llm: Any | None = None, **overrides: Any) -> AssetGenerationService:
    kwargs: dict[str, Any] = {**env.service_kwargs(), "sql_checker": asset_sql_checker}
    if llm is not None:
        kwargs["llm_factory"] = lambda: llm
    kwargs.update(overrides)
    return AssetGenerationService(env.config, env.mgr, **kwargs)


async def _snapshot(env: Env, source: str = SRC) -> None:
    await DBRegistrationService(env.config, env.mgr, **env.service_kwargs()).run_register(
        source, steps=["schema"], tables=None, by="admin", ctx=env.ctx,
    )


async def _offline_profile(env: Env, llm: Any | None = None) -> str:
    """DB 연결 없이 P1 초안(주석은 DDL 주석만)을 만든다."""
    service = _service(env, llm, client_factory=failing_client_factory(ConnectionError("x")))
    result = await service.run_asset_profile(SRC, tables=None, by="admin", ctx=env.ctx)
    return str(result["draft_id"])


async def _draft(env: Env, draft_id: str, source: str = SRC) -> dict[str, Any]:
    draft = await env.store.get_asset_draft(source, draft_id)
    assert draft is not None
    return draft


def _rows_and_errors(draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    report = (draft.get("validation") or {}).get("table_definitions") or {}
    return draft["assets"]["table_definitions"], report.get("errors") or {}


# ─── 묶음 ───────────────────────────────────────────────────────


def test_batches_by_family_prefix_then_pack_rests():
    tables = list(_wide_tables())
    batches = definition_batches(tables)
    assert batches == [
        [f"t_alpha{i:02d}" for i in range(1, 11)],
        ["t_beta1", "t_beta2", "t_beta3", "t_alpha11", "t_alpha12", "t_gamma"],
    ]
    assert definition_batches([]) == []


def test_seed_allowed_tables_take_ten_calls():
    """ITAM 조회 대상 99개 ≈ 10~12회(계획 §4.4) — 군 접두 묶음 + 조각 합치기로 10회."""
    seed = yaml.safe_load((SEED_DIR / "table_definitions.yaml").read_text(encoding="utf-8"))
    allowed = [t for t, v in seed["tables"].items() if v.get("allowed") is not False]
    batches = definition_batches(allowed)
    assert len(allowed) == 99
    assert len(batches) == 10 and all(len(b) <= 10 for b in batches)
    assert sorted(t for b in batches for t in b) == sorted(allowed)


# ─── 가져오기 ────────────────────────────────────────────────────


IMPORT_YAML = """
db_id: app_defs
tables:
  t_alpha01:
    group: 알파
    kind: 현행
    manages: 알파 1번을 관리한다.
    key_columns: [ID, nm]
    related:
      t_beta1: id로 연결
    notes: "`nm`은 이름"
    allowed: false
  t_alpha02:
    kind: 현행
    manages: 알파 2번
    key_columns: [nope]
  t_ghost:
    manages: 없는 테이블
  t_beta1:
    manages: "`DELETE FROM t_beta1` 주의"
"""


class TestImport:
    async def test_import_draft_with_error_rows(self, env):
        await _snapshot(env)
        llm = DefinitionLLM()
        result = await _service(env, llm).import_table_definitions(SRC, IMPORT_YAML, by="admin")

        assert result["summary"] == {"total": 4, "valid": 1, "invalid": 3}
        draft = await _draft(env, result["draft_id"])
        rows, errors = _rows_and_errors(draft)
        assert draft["kind"] == "import" and draft["llm_calls"] == 0 and llm.prompts == []
        assert rows["t_alpha01"] == {
            "group": "알파", "kind": "현행", "manages": "알파 1번을 관리한다.",
            "key_columns": ["id", "nm"], "related": {"t_beta1": "id로 연결"},
            "notes": "`nm`은 이름", "origin": "import",
        }
        assert errors["t_alpha02"] == ["테이블에 없는 key_columns: nope"]
        assert rows["t_alpha02"]["key_columns"] == ["nope"]  # 오류 행은 표시용 사본
        assert errors["t_ghost"] == ["스키마에 없는 테이블입니다"]
        assert any("SQL 키워드(DELETE)" in e for e in errors["t_beta1"])
        assert "t_alpha01" not in errors
        assert env.store.read_current_profile(SRC) is None  # 승인 전 파일 쓰기 0

    @pytest.mark.parametrize(("text", "fragment"), [
        ("tables: [", "YAML을 읽지 못했습니다"),
        ("rows: {}", "tables"),
        ("x" * 1_000_001, "1,000,000자 이하"),
    ])
    async def test_import_rejects_bad_documents(self, env, text, fragment):
        await _snapshot(env)
        with pytest.raises(ValueError, match=re.escape(fragment)):
            await _service(env).import_table_definitions(SRC, text, by="a")

    async def test_import_requires_snapshot(self, env):
        with pytest.raises(ValueError, match="스냅샷이 없습니다"):
            await _service(env).import_table_definitions(SRC, "tables: {}", by="a")


# ─── 주석 ────────────────────────────────────────────────────────


@pytest.fixture
def asset_env(tmp_path, monkeypatch) -> Env:
    """D-294 가상 자산 DB — 카탈로그가 `t_cmcode` 테이블 주석 「공통코드」를 돌려준다."""
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(ASSET_SRC, "mariadb", _tables())
    e.session.sql_rows = _sql_rows
    e.registry = make_registry({"db_id": ASSET_SRC, "engine": "mariadb"})
    return e


async def test_profile_turns_table_comments_into_definitions(asset_env):
    await _snapshot(asset_env, ASSET_SRC)
    await asset_env.store.save_ddl_comments(ASSET_SRC, {"t_hw": "하드웨어 {중괄호}"})
    llm = DefinitionLLM()
    result = await _service(asset_env, llm).run_asset_profile(
        ASSET_SRC, tables=None, by="admin", ctx=asset_env.ctx,
    )

    draft = await _draft(asset_env, result["draft_id"], ASSET_SRC)
    rows, errors = _rows_and_errors(draft)
    assert rows["t_cmcode"] == {"manages": "공통코드", "origin": "comment"}
    assert rows["t_hw"]["origin"] == "comment" and "중괄호" in errors["t_hw"][0]
    assert set(rows) == {"t_cmcode", "t_hw"}  # 주석 없는 테이블은 LLM 경로 몫
    assert result["summary"]["table_definitions"] == 2
    assert llm.prompts == [] and draft["llm_calls"] == 0



async def test_assist_llm_keeps_definition_edits_made_while_running(asset_env):
    """LLM 보조(쿼리 예시·규칙 섹션) 잡이 도는 동안 저장한 테이블 정의 편집을 덮지 않는다."""
    await _snapshot(asset_env, ASSET_SRC)
    service = _service(asset_env)
    profiled = await service.run_asset_profile(
        ASSET_SRC, tables=None, by="admin", ctx=asset_env.ctx,
    )
    draft_id = profiled["draft_id"]

    class EditingLLM(FakeLLM):
        async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
            prompt = "\n".join(str(getattr(m, "content", m)) for m in messages)
            if "SQL 예시를 만드는 전문가" in prompt:
                await service.update_table_definitions(
                    ASSET_SRC, draft_id, {"t_cmcode": {"manages": "공통코드 고침"}}, by="a",
                )
            return await super().ainvoke(messages)

    await _service(asset_env, EditingLLM()).run_asset_llm(
        ASSET_SRC, draft_id, by="a", ctx=asset_env.ctx,
    )
    draft = await _draft(asset_env, draft_id, ASSET_SRC)
    rows, _errors = _rows_and_errors(draft)
    assert rows["t_cmcode"] == {"manages": "공통코드 고침", "origin": "manual"}
    assert draft["assets"]["query_examples"] and draft["llm_calls"] == 2


# ─── LLM 묶음 초안 ───────────────────────────────────────────────


async def _prepare_llm_env(env: Env) -> str:
    await _snapshot(env)
    await env.store.save_ddl_comments(SRC, {"t_gamma": "감마 테이블", "t_alpha01.nm": "주석 설명"})
    await env.mgr.save_descriptions(SRC, {"t_alpha02.nm": "알파 이름 설명"})
    env.write_profile(SRC, {
        "source": "manual",
        "code_labels": {"t_beta1.code": {"A": "에이 라벨"}},
        "relationships": [{"from": "t_alpha02.id", "to": "t_beta1.id"}],
        "table_definitions": {"t_alpha12": {"manages": "사람이 쓴 정의", "origin": "manual"}},
    })
    return await _offline_profile(env)


class TestLlmDraft:
    async def test_estimate_matches_calls_and_inputs(self, env):
        draft_id = await _prepare_llm_env(env)
        llm = DefinitionLLM()
        service = _service(env, llm)

        estimate = await service.estimate_table_definition_llm(SRC, draft_id)
        assert estimate["calls"] == 2 and estimate["tables"] == 14
        assert estimate["skipped"] == {"comment": 1, "defined": 0, "manual": 1}
        assert estimate["concurrency"] == 2 and estimate["batch_size"] == 10
        assert llm.prompts == []  # 예상은 LLM 0

        result = await service.run_table_definition_llm(
            SRC, draft_id, only_failed=False, by="admin", ctx=env.ctx,
        )

        assert result["llm_calls"] == len(llm.prompts) == estimate["calls"]
        assert result["failed_batches"] == 0 and llm.max_active <= 2
        assert sorted(tables_in(p) for p in llm.prompts) == [
            [f"t_alpha{i:02d}" for i in range(1, 11)],
            ["t_beta1", "t_beta2", "t_beta3", "t_alpha11"],
        ]
        joined = "\n".join(llm.prompts)
        assert "가상 업무 DB(레지스트리 설명)" in joined
        assert "- `nm` varchar — 알파 이름 설명" in joined
        assert "- `nm` varchar — 주석 설명" in joined
        assert "(코드 라벨: 에이 라벨)" in joined
        assert "- 관계: `t_alpha02.id` = `t_beta1.id`" in joined
        assert "t_gamma" not in joined and "t_alpha12" not in joined

        draft = await _draft(env, draft_id)
        rows, errors = _rows_and_errors(draft)
        assert errors == {}
        assert rows["t_alpha03"] == {
            "kind": "현행", "manages": "t_alpha03 정보를 관리한다.", "key_columns": ["id"],
            "origin": "llm",
        }
        assert "t_alpha12" not in rows  # 승인된 사람 정의는 건너뜀
        assert rows["t_gamma"] == {"manages": "감마 테이블", "origin": "comment"}
        batches = draft["validation"]["table_definitions"]["batches"]
        assert [b["status"] for b in batches] == ["ok", "ok"]
        assert draft["llm_calls"] == 2

    async def test_failures_become_error_rows_and_only_failed_reruns(self, env):
        draft_id = await _prepare_llm_env(env)

        def flaky(prompt: str) -> str:
            tables = tables_in(prompt)
            if "t_beta1" in tables:
                return "이건 JSON이 아니다"
            answer = json.loads(good_answer(prompt))
            del answer["t_alpha03"]  # 응답 누락
            answer["t_alpha04"]["key_columns"] = ["nope"]  # 검증 실패
            answer["t_alpha05"]["kind"] = "현재"  # 허용 밖
            return json.dumps(answer, ensure_ascii=False)

        first = DefinitionLLM(flaky)
        result = await _service(env, first).run_table_definition_llm(
            SRC, draft_id, only_failed=False, by="admin", ctx=env.ctx,
        )
        assert result["failed_batches"] == 2

        draft = await _draft(env, draft_id)
        rows, errors = _rows_and_errors(draft)
        assert errors["t_alpha03"] == ["LLM 응답에 이 테이블이 없습니다"]
        assert errors["t_alpha04"] == ["테이블에 없는 key_columns: nope"]
        assert "kind는" in errors["t_alpha05"][0]
        assert all(errors[t][0].startswith("LLM 초안 실패: ValueError")
                   for t in ("t_beta1", "t_beta2", "t_beta3", "t_alpha11"))
        assert rows["t_beta1"] == {"manages": "", "origin": "llm"}
        assert "t_alpha06" not in errors and rows["t_alpha06"]["origin"] == "llm"
        statuses = {tuple(b["tables"][:1]): b["status"]
                    for b in draft["validation"]["table_definitions"]["batches"]}
        assert sorted(statuses.values()) == ["failed", "partial"]

        service = _service(env, DefinitionLLM())
        estimate = await service.estimate_table_definition_llm(SRC, draft_id, only_failed=True)
        assert estimate["calls"] == 2 and estimate["tables"] == 3 + 4

        second = DefinitionLLM()
        rerun = await _service(env, second).run_table_definition_llm(
            SRC, draft_id, only_failed=True, by="admin", ctx=env.ctx,
        )
        assert rerun["llm_calls"] == 2 and rerun["failed_batches"] == 0
        sent = sorted(t for p in second.prompts for t in tables_in(p))
        assert sent == sorted(
            ["t_alpha03", "t_alpha04", "t_alpha05", "t_beta1", "t_beta2", "t_beta3", "t_alpha11"]
        )
        draft = await _draft(env, draft_id)
        rows, errors = _rows_and_errors(draft)
        assert errors == {} and rows["t_alpha03"]["origin"] == "llm"
        assert [b["status"] for b in draft["validation"]["table_definitions"]["batches"]] == [
            "ok", "ok",
        ]
        assert draft["llm_calls"] == 4

        nothing = await service.estimate_table_definition_llm(SRC, draft_id, only_failed=True)
        assert nothing["calls"] == 0

    async def test_llm_exception_is_failed_batch(self, env):
        draft_id = await _prepare_llm_env(env)

        def boom(prompt: str) -> str:
            raise RuntimeError("provider down")

        result = await _service(env, DefinitionLLM(boom)).run_table_definition_llm(
            SRC, draft_id, only_failed=False, by="a", ctx=env.ctx,
        )
        assert result["failed_batches"] == 2
        _rows, errors = _rows_and_errors(await _draft(env, draft_id))
        assert errors["t_alpha01"] == ["LLM 초안 실패: RuntimeError: provider down"]

    async def test_rows_from_other_origins_are_never_overwritten(self, env):
        await _snapshot(env)
        imported = await _service(env).import_table_definitions(
            SRC, "tables:\n  t_alpha01:\n    manages: 가져온 정의\n", by="a",
        )
        llm = DefinitionLLM()
        service = _service(env, llm)
        estimate = await service.estimate_table_definition_llm(SRC, imported["draft_id"])
        assert estimate["skipped"]["defined"] == 1 and estimate["tables"] == 15

        await service.run_table_definition_llm(
            SRC, imported["draft_id"], only_failed=False, by="a", ctx=env.ctx,
        )
        rows, _errors = _rows_and_errors(await _draft(env, imported["draft_id"]))
        assert rows["t_alpha01"] == {"manages": "가져온 정의", "origin": "import"}
        assert len(rows) == 16


# ─── 편집 · 승인 · 되돌리기 ──────────────────────────────────────


class TestReviewAndApprove:
    async def test_edit_marks_manual_and_rejects_invalid(self, env):
        await _snapshot(env)
        service = _service(env)
        imported = await service.import_table_definitions(SRC, IMPORT_YAML, by="a")
        draft_id = imported["draft_id"]

        with pytest.raises(ValueError, match="편집 검증 실패"):
            await service.update_table_definitions(
                SRC, draft_id, {"t_alpha02": {"manages": "고침"}}, by="a",
            )
        result = await service.update_table_definitions(
            SRC, draft_id,
            {"t_alpha02": {"manages": "  알파 2번\n관리 ", "key_columns": ["id"], "kind": None}},
            by="a",
        )
        assert result["updated"] == ["t_alpha02"]
        rows, errors = _rows_and_errors(await _draft(env, draft_id))
        assert rows["t_alpha02"] == {"manages": "알파 2번 관리", "key_columns": ["id"],
                                     "origin": "manual"}
        assert "t_alpha02" not in errors and "t_ghost" in errors
        with pytest.raises(ValueError, match="초안에 없는"):
            await service.update_table_definitions(SRC, draft_id, {"t_x": {"manages": "x"}},
                                                   by="a")

    async def test_approve_selection_409_manual_kept_and_rollback_bytes(self, env):
        await _snapshot(env)
        original = (
            "# 사람이 쓴 프로필\nsource: manual\nallowed_tables: [t_alpha01, t_alpha02]\n"
            "table_definitions:\n  t_alpha01:\n    manages: 사람 정의\n    origin: manual\n"
        )
        path = env.profiles_dir / f"{SRC}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original, encoding="utf-8")
        service = _service(env)
        imported = await service.import_table_definitions(SRC, IMPORT_YAML, by="a")
        draft_id = imported["draft_id"]

        for selection in (["t_alpha01", "t_alpha02"], None):
            with pytest.raises(DraftNotApprovable) as err:
                await service.approve_asset_draft(
                    SRC, draft_id, include=["table_definitions"], allowed_tables=None,
                    by="a", reason="", table_definition_tables=selection,
                )
            assert err.value.code == "validation_failed"
        with pytest.raises(ValueError, match="초안에 없는"):
            await service.approve_asset_draft(
                SRC, draft_id, include=["table_definitions"], allowed_tables=None, by="a",
                reason="", table_definition_tables=["t_nope"],
            )
        assert path.read_text(encoding="utf-8") == original  # 거절은 쓰기 0

        await service.update_table_definitions(
            SRC, draft_id, {"t_alpha02": {"manages": "알파 2번 고침", "key_columns": ["id"]}},
            by="a",
        )
        approved = await service.approve_asset_draft(
            SRC, draft_id, include=["table_definitions"], allowed_tables=None, by="a",
            reason="검토 완료", table_definition_tables=["t_alpha01", "t_alpha02"],
        )

        profile = env.store.read_current_profile(SRC)["profile"]
        assert profile["table_definitions"] == {
            "t_alpha01": {"manages": "사람 정의", "origin": "manual"},  # base manual 보존
            "t_alpha02": {"kind": "현행", "manages": "알파 2번 고침", "key_columns": ["id"],
                          "origin": "manual"},
        }
        assert profile["allowed_tables"] == ["t_alpha01", "t_alpha02"]
        assert approved["applied"]["profile"]["table_definitions_kept_manual"] == ["t_alpha01"]
        assert approved["draft"]["approved_table_definitions"] == ["t_alpha01", "t_alpha02"]

        versions = await env.store.list_versions(SRC)
        before = next(v for v in versions if v["content"] == original)
        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())
        await structure.rollback(SRC, before["ver"], by="a", reason="되돌리기")
        assert path.read_bytes() == original.encode("utf-8")

    async def test_overview_and_readiness_warning(self, env):
        await _snapshot(env)
        allowed = [f"t_alpha{i:02d}" for i in range(1, 13)]
        env.write_profile(SRC, {"source": "manual", "allowed_tables": allowed})
        service = _service(env)
        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())

        def c11(report: Any) -> Any:
            return next(item for item in report.items if item.code == "C11")

        assert c11(await structure._readiness_for(SRC, {})).ok is False
        overview = await service.overview(SRC)
        assert overview["table_definitions"]["approved"] == 0
        assert overview["table_definitions"]["allowed"] == 12
        assert "신청·처리" in overview["table_definitions"]["kinds"]

        env.write_profile(SRC, {"source": "manual", "allowed_tables": allowed,
                                "table_definitions": {
                                    t: {"manages": "정의", "origin": "manual"}
                                    for t in allowed[:10]}})
        assert c11(await structure._readiness_for(SRC, {})).ok is True
        assert (await service.overview(SRC))["table_definitions"]["covered"] == 10
