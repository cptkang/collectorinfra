"""조회 템플릿 결정적 조립 — 계약·슬롯 검증·조립기·단일/멀티 배선 (plans/141 W6 · D-314 ③).

여기서 고정하는 것:
- 계약 검사(id 유일 · 자리표 = 슬롯 · SELECT 단일문 · tables 일치 · 중괄호·세미콜론·주석 금지)
- 슬롯 형식 검증과 주입 거절(따옴표·세미콜론·주석·백슬래시·유니코드 따옴표) · 리터럴 안 자리표 불변
- 조립 SQL 바이트 고정 · 폴백 사유 열거 · 표지·로그에 슬롯 값 없음
- 단일(`query_generator`)·멀티(`_generate_sql`·`multi_db_executor`)가 같은 SQL · 같은 표지
- 템플릿 파일 없는 DB / 스위치 off → LLM 호출 0 · 반환 비트 동일 · 재시도 턴 미진입

템플릿 파일은 tmp_path에 만든다(ITAM 실제 템플릿은 다른 단계). LLM·DB는 전부 가짜다(D-127).
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from src.config import AppConfig, Text2SQLConfig
from src.db_adapters import template_assembler as ta
from src.domain import query_templates as qt
from src.state import create_followup_input, create_initial_state

mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")

DB_ID = "tpl_db"

_TEMPLATE_SQL = (
    "SELECT hostname, center FROM servers WHERE center = :center"
    " AND (:status IS NULL OR status = :status)"
    " AND (:period_start IS NULL OR reg_date BETWEEN :period_start AND :period_end)"
    " ORDER BY hostname"
)

_DOC: dict[str, Any] = {
    "version": 1,
    "templates": [
        {
            "id": "servers_by_center",
            "intent": "센터별 서버 목록",
            "triggers": ["센터", "서버 목록"],
            "slots": [
                {"name": "center", "type": "center", "required": True},
                {"name": "status", "type": "code", "required": False, "column": "servers.status"},
                {"name": "period", "type": "date_range", "required": False, "format": "yyyymmdd"},
            ],
            "sql": _TEMPLATE_SQL,
            "tables": ["servers"],
            "status": "active",
        },
        {
            "id": "old_one",
            "intent": "철회된 템플릿",
            "triggers": [],
            "slots": [],
            "sql": "SELECT hostname FROM servers",
            "tables": ["servers"],
            "status": "withdrawn",
        },
    ],
}

_SCHEMA: dict[str, Any] = {
    "tables": {
        "servers": {
            "columns": [
                {"name": n, "type": "varchar"} for n in ("hostname", "center", "status", "reg_date")
            ],
        },
    },
    "_structure_meta": {"code_values": {"servers.status": ["RUN", "STOP"]}},
}

#: 센터=김포 · 상태=RUN · 기간 없음 → 조립 SQL(행 제한 자동 추가 포함) — 바이트 고정
_EXPECTED_SQL = (
    "SELECT hostname, center FROM servers WHERE center = '김포'"
    " AND ('RUN' IS NULL OR status = 'RUN')"
    " AND (NULL IS NULL OR reg_date BETWEEN NULL AND NULL)"
    " ORDER BY hostname\nLIMIT 100;"
)

_QUESTION = "김포 센터 가동 중인 서버 목록"


class _FakeLLM:
    """프롬프트를 기록하고 정해 둔 응답(또는 예외)을 차례로 돌려준다(마지막 응답은 반복)."""

    def __init__(self, *responses: Any) -> None:
        self.prompts: list[str] = []
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        self.prompts.append(messages[-1].content)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=response)


def _pick(tid: str, **slots: Any) -> str:
    return json.dumps({"template_id": tid, "slots": slots}, ensure_ascii=False)


def _write(root: Path, doc: Any, db_id: str = DB_ID) -> Path:
    path = root / ta.TEMPLATE_PATH.format(db_id=db_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = doc if isinstance(doc, str) else yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ta, "DEFAULT_ROOT", tmp_path)
    ta._cache.clear()
    yield tmp_path
    ta._cache.clear()


@pytest.fixture
def with_file(_root: Path) -> Path:
    return _write(_root, _DOC)


def _cfg(*, on: bool = True) -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(
        template_assembly=on, semantic_compose=False, multi_candidate=False,
        spike_condition_enabled=False,
    )
    cfg.query.default_limit = 100
    return cfg


async def _assemble(llm: Any, *, cfg: Any = None, db_id: str = DB_ID,
                    schema: Any = None) -> ta.TemplateOutcome | None:
    return await ta.assemble_from_template(
        llm=llm, question=_QUESTION, db_id=db_id,
        schema_info=copy.deepcopy(_SCHEMA if schema is None else schema),
        app_config=cfg or _cfg(), db_engine="mariadb", user_query=_QUESTION, default_limit=100,
    )


# ──────────────────────────────────────────────
# 계약 검사
# ──────────────────────────────────────────────


def _doc_with(**overrides: Any) -> dict:
    doc = copy.deepcopy(_DOC)
    doc["templates"][0].update(overrides)
    return doc


class TestContract:
    def test_valid_file_passes(self):
        assert qt.check_templates(_DOC) == []
        templates, issues = qt.parse_templates(_DOC)
        assert issues == [] and [t.id for t in templates] == ["servers_by_center", "old_one"]

    def test_catalog_checks_tables_and_code_column(self):
        assert qt.check_templates(_DOC, {"servers": ["hostname", "center", "status"]}) == []
        issues = qt.check_templates(_DOC, {"other": ["x"]})
        assert any("카탈로그에 없는 테이블 servers" in i for i in issues)
        assert any("code 컬럼 servers.status" in i for i in issues)

    @pytest.mark.parametrize("override, needle", [
        ({"sql": _TEMPLATE_SQL + ";"}, "세미콜론"),
        ({"sql": _TEMPLATE_SQL + " -- x"}, "주석"),
        ({"sql": _TEMPLATE_SQL + " # x"}, "주석"),
        ({"sql": _TEMPLATE_SQL.replace("hostname,", "{hostname},")}, "중괄호"),
        ({"sql": "DELETE FROM servers WHERE center = :center AND :status AND :period_start"
                 " AND :period_end"}, "SELECT"),
        ({"sql": _TEMPLATE_SQL.replace("= :center", "= ':center'")}, "자리표 불일치"),
        ({"sql": _TEMPLATE_SQL + " AND x = :extra"}, "자리표 불일치"),
        ({"tables": ["servers", "racks"]}, "tables 불일치"),
        ({"slots": [{"name": "center", "type": "free_text"}]}, "허용 목록 밖"),
        ({"slots": [{"name": "status", "type": "code"}]}, "column"),
    ])
    def test_violations(self, override, needle):
        issues = qt.check_templates(_doc_with(**override))
        assert any(needle in i for i in issues), issues

    def test_table_refs_ignore_function_from_and_cte(self):
        sql = (
            "WITH recent AS (SELECT hostname FROM servers) SELECT EXTRACT(YEAR FROM r.reg_date)"
            " FROM recent r JOIN `racks` k ON k.id = r.id"
        )
        assert qt.referenced_tables(sql) == {"servers", "racks"}

    @pytest.mark.parametrize("slot_type", ["hostname", "center", "dept_code", "date"])
    @pytest.mark.parametrize("where", [
        "hostname LIKE CONCAT(:x, '%')",
        "hostname NOT LIKE :x",
        "(hostname LIKE CONCAT('%', :x, '%'))",
    ])
    def test_like_operand_only_keyword_slot(self, slot_type, where):
        """LIKE 피연산자 자리표는 keyword만 — 다른 슬롯 값의 `_`는 와일드카드로 남는다."""
        def doc(stype: str) -> dict:
            return {"version": 1, "templates": [{
                "id": "t1", "intent": "i", "triggers": [], "tables": ["servers"],
                "slots": [{"name": "x", "type": stype}],
                "sql": f"SELECT hostname FROM servers WHERE {where}",
            }]}

        assert any("LIKE 피연산자" in i for i in qt.check_templates(doc(slot_type)))
        assert qt.check_templates(doc("keyword")) == []

    def test_like_rule_ignores_equality_and_literal_patterns(self):
        """LIKE 밖 자리표 · 리터럴만 쓴 LIKE는 계약 위반이 아니다."""
        sql = _TEMPLATE_SQL.replace("ORDER BY", "AND hostname LIKE 'web%' ORDER BY")
        assert qt.check_templates(_doc_with(sql=sql)) == []
        assert qt.like_placeholders(sql) == set()

    @pytest.mark.parametrize("sql", [
        "SELECT a FROM t /*!50000 INTO OUTFILE '/tmp/x' */",
        "SELECT a /*M!100000 , SLEEP(1) */ FROM t",
    ])
    def test_executable_comment(self, sql):
        """MariaDB 실행 주석 — 공용 판정 참 · 템플릿 계약 거절."""
        assert qt.has_executable_comment(sql)
        assert any("실행 주석" in i for i in qt.check_templates(_doc_with(sql=sql)))
        assert not qt.has_executable_comment("SELECT a /* 설명 */ FROM t")

    def test_duplicate_id_and_version(self):
        doc = copy.deepcopy(_DOC)
        doc["templates"].append(copy.deepcopy(doc["templates"][0]))
        assert any("id 중복" in i for i in qt.check_templates(doc))
        assert qt.check_templates({"version": 2, "templates": []})


# ──────────────────────────────────────────────
# 슬롯 형식 · 바인딩
# ──────────────────────────────────────────────


def _tpl() -> qt.QueryTemplate:
    return qt.parse_templates(_DOC)[0][0]


_CODES = _SCHEMA["_structure_meta"]["code_values"]


class TestBinding:
    def test_bind_bytes(self):
        out = qt.bind_template(
            _tpl(), {"center": "김포", "period": {"start": "2026-03-01", "end": "2026.03.31"}},
            _CODES,
        )
        assert out.sql == (
            "SELECT hostname, center FROM servers WHERE center = '김포'"
            " AND (NULL IS NULL OR status = NULL)"
            " AND ('20260301' IS NULL OR reg_date BETWEEN '20260301' AND '20260331')"
            " ORDER BY hostname"
        )
        assert out.slot_names == ("center", "period")

    @pytest.mark.parametrize("value", [
        "'; DROP TABLE x; --",
        "김포' OR '1'='1",
        'a"b',
        "a`b",
        "a;b",
        "a--b",
        "a/*b",
        "a#b",
        "a\\b",
        "a’b",   # ’
        "a“b",   # “
        "a＇b",   # ＇
        "a\nb",
        "",
        True,
        ["김포"],
    ])
    def test_injection_and_shape_rejected(self, value):
        out = qt.bind_template(_tpl(), {"center": value}, _CODES)
        assert out.sql is None
        assert out.reason in (qt.REASON_SLOT_REJECTED, qt.REASON_SLOT_MISSING)
        if isinstance(value, str) and value:
            assert value not in out.detail

    @pytest.mark.parametrize("slots, reason", [
        ({}, qt.REASON_SLOT_MISSING),
        ({"center": "김포", "status": "DROP"}, qt.REASON_SLOT_REJECTED),
        ({"center": "김포", "period": {"start": "2026-02-30", "end": "2026-03-01"}},
         qt.REASON_SLOT_REJECTED),
        ({"center": "김포", "period": {"start": "2026-04-01", "end": "2026-03-01"}},
         qt.REASON_SLOT_REJECTED),
        ({"center": "김포", "period": "2026-03"}, qt.REASON_SLOT_REJECTED),
    ])
    def test_slot_rules(self, slots, reason):
        assert qt.bind_template(_tpl(), slots, _CODES).reason == reason

    def test_code_without_allowed_list_rejected(self):
        out = qt.bind_template(_tpl(), {"center": "김포", "status": "RUN"}, {})
        assert out.reason == qt.REASON_SLOT_REJECTED and "허용 코드 목록 없음" in out.detail

    def test_slot_types(self):
        spec = qt.SlotSpec
        assert qt.normalize_slot(spec("h", "hostname"), "web-01.dc") == {"h": "web-01.dc"}
        assert qt.normalize_slot(spec("d", "dept_code"), "IT_01") == {"d": "IT_01"}
        assert qt.normalize_slot(spec("k", "keyword"), "오라클 DB") == {"k": "오라클 DB"}
        assert qt.normalize_slot(spec("d", "date", format="iso"), "20260105") == {"d": "2026-01-05"}
        for stype, bad in (("hostname", "web 01"), ("dept_code", "부서"), ("keyword", "a%b")):
            with pytest.raises(qt.SlotRejectedError):
                qt.normalize_slot(spec("x", stype), bad)

    def test_placeholder_inside_literal_and_backtick_untouched(self):
        doc = _doc_with(
            sql="SELECT ':center' AS a, `:center` AS b, hostname FROM servers"
                " WHERE center = :center AND :status IS NULL AND :period_start IS NULL"
                " AND :period_end IS NULL",
        )
        tpl = qt.parse_templates(doc)[0][0]
        out = qt.bind_template(tpl, {"center": "김포"}, _CODES)
        assert out.sql is not None
        assert out.sql.startswith("SELECT ':center' AS a, `:center` AS b, hostname FROM servers")
        assert "center = '김포'" in out.sql

    def test_quote_literal_refuses_quotes(self):
        with pytest.raises(qt.SlotRejectedError):
            qt.quote_literal("a'b")

    def test_sample_bindings_cover_all_combinations(self):
        combos = qt.sample_bindings(_tpl(), _CODES)
        assert len(combos) == 4  # 필수 1 × 선택 2개(있음/없음)
        assert all(qt.bind_template(_tpl(), c, _CODES).sql for c in combos)
        assert qt.sample_bindings(_tpl().__class__(
            id="x", intent="x", triggers=(), sql="SELECT 1 FROM t WHERE c = :s", tables=("t",),
            slots=(qt.SlotSpec("s", "code", column="t.c"),),
        )) == []


# ──────────────────────────────────────────────
# 조립기 — 가짜 LLM
# ──────────────────────────────────────────────


class TestAssembler:
    async def test_assembled_bytes_and_marker(self, with_file, caplog):
        caplog.set_level(logging.INFO)
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        out = await _assemble(llm)
        assert out is not None and out.sql == _EXPECTED_SQL
        assert out.as_state() == {
            "outcome": "assembled", "template_id": "servers_by_center",
            "slot_names": ["center", "status"], "reason": None,
            "final_sql_from_template": True,
        }
        assert len(llm.prompts) == 1
        prompt = llm.prompts[0]
        assert "servers_by_center" in prompt and "old_one" not in prompt, "withdrawn은 목록 밖"
        assert "허용 값: RUN, STOP" in prompt and prompt.rstrip().endswith('"값"}}')
        assert "김포" not in caplog.text, "슬롯 값은 로그에 남기지 않는다"

    @pytest.mark.parametrize("response, reason", [
        (_pick("none"), "no_match"),
        ("템플릿이 없습니다", "invalid_json"),
        ('{"slots": {}}', "invalid_json"),
        (_pick("no_such_template", center="김포"), "unknown_template"),
        (_pick("servers_by_center"), "slot_missing"),
        (_pick("servers_by_center", center="'; DROP TABLE x; --"), "slot_rejected"),
        (_pick("servers_by_center", center="김포", status="'; DROP TABLE x; --"), "slot_rejected"),
        (RuntimeError("boom"), "llm_error"),
    ])
    async def test_fallback_reasons(self, with_file, response, reason, caplog):
        caplog.set_level(logging.INFO)
        out = await _assemble(_FakeLLM(response))
        assert out is not None and out.sql is None
        assert out.outcome == "fallback" and out.reason == reason
        assert "DROP" not in caplog.text
        assert any(reason in r.getMessage() for r in caplog.records), "침묵 폴백 금지"

    async def test_validation_failed(self, with_file):
        schema = copy.deepcopy(_SCHEMA)
        schema["tables"] = {"racks": {"columns": [{"name": "rack_id", "type": "varchar"}]}}
        out = await _assemble(_FakeLLM(_pick("servers_by_center", center="김포")), schema=schema)
        assert out is not None and out.reason == "validation_failed"
        assert out.template_id == "servers_by_center" and out.slot_names == ("center",)

    async def test_guard_rejected(self, with_file, monkeypatch):
        monkeypatch.setattr(ta.SQLGuard, "is_safe_select", lambda self, sql: (False, "막힘"))
        out = await _assemble(_FakeLLM(_pick("servers_by_center", center="김포")))
        assert out is not None and out.reason == "guard_rejected"

    async def test_no_file_no_call(self):
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        assert await _assemble(llm) is None
        assert llm.prompts == []

    async def test_switch_off_no_call(self, with_file):
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        assert await _assemble(llm, cfg=_cfg(on=False)) is None
        assert await _assemble(llm, cfg=SimpleNamespace(text2sql=SimpleNamespace())) is None
        assert llm.prompts == []

    async def test_invalid_file_no_call(self, _root):
        _write(_root, "version: [unclosed")
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        out = await _assemble(llm)
        assert out is not None and out.reason == "invalid_file" and llm.prompts == []

    async def test_cache_follows_file_change(self, _root):
        _write(_root, _DOC)
        assert ta.load_templates(DB_ID).templates[0].id == "servers_by_center"
        doc = copy.deepcopy(_DOC)
        doc["templates"][0]["status"] = "withdrawn"
        doc["templates"][0]["intent"] = "철회됨 — 크기를 바꿔 캐시 무효화"
        _write(_root, doc)
        assert ta.load_templates(DB_ID).templates == ()

    def test_config_default_on(self):
        assert Text2SQLConfig.model_fields["template_assembly"].default is True


# ──────────────────────────────────────────────
# 단일 경로 배선 — query_generator
# ──────────────────────────────────────────────


def _single_state(**extra: Any) -> dict:
    state = create_initial_state(user_query=_QUESTION)
    state.update({
        "active_db_id": DB_ID, "active_db_engine": "mariadb",
        "schema_info": copy.deepcopy(_SCHEMA),
        "parsed_requirements": {"original_query": _QUESTION},
    })
    state.update(extra)
    return state


@pytest.fixture
def fake_llm_fallback(monkeypatch):
    calls: list[int] = []

    async def _fallback(state, ctx, coverage_outside):
        calls.append(1)
        return "SELECT hostname FROM servers", None, None, {}

    monkeypatch.setattr(qg, "_llm_fallback", _fallback)
    monkeypatch.setattr(qg, "observe_rewrite", AsyncMock(return_value={}))
    return calls


class TestSinglePath:
    async def test_assembled(self, with_file, fake_llm_fallback):
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        out = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg())
        assert out["generated_sql"] == _EXPECTED_SQL
        assert out["template_assembly"] == {DB_ID: {
            "outcome": "assembled", "template_id": "servers_by_center",
            "slot_names": ["center", "status"], "reason": None,
            "final_sql_from_template": True,
        }}
        assert fake_llm_fallback == [] and len(llm.prompts) == 1

    async def test_fallback_goes_to_llm_with_marker(self, with_file, fake_llm_fallback):
        llm = _FakeLLM(_pick("none"))
        out = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg())
        assert out["generated_sql"] == "SELECT hostname FROM servers"
        assert out["template_assembly"][DB_ID]["reason"] == "no_match"
        assert fake_llm_fallback == [1]

    async def test_no_file_and_switch_off_are_bit_identical(self, fake_llm_fallback):
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        no_file = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg())
        _write(ta.DEFAULT_ROOT, _DOC)
        off = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg(on=False))
        assert "template_assembly" not in no_file
        assert no_file == off
        assert llm.prompts == []

    async def test_retry_turn_not_entered(self, with_file, fake_llm_fallback):
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        state = _single_state(error_message="Unknown column", retry_count=0)
        out = await qg.query_generator(state, llm=llm, app_config=_cfg())
        assert llm.prompts == [] and "template_assembly" not in out
        assert fake_llm_fallback == [1]

    async def test_existing_entries_are_kept(self, with_file, fake_llm_fallback):
        llm = _FakeLLM(_pick("none"))
        state = _single_state(template_assembly={"other": {"outcome": "assembled"}})
        out = await qg.query_generator(state, llm=llm, app_config=_cfg())
        assert set(out["template_assembly"]) == {"other", DB_ID}


# ──────────────────────────────────────────────
# 멀티 경로 배선 — _generate_sql · multi_db_executor
# ──────────────────────────────────────────────


async def _multi_generate(llm: Any, sink: dict, **kw: Any) -> str:
    return await mdb._generate_sql(
        llm, {"original_query": _QUESTION}, copy.deepcopy(_SCHEMA), _QUESTION, 100,
        db_engine="mariadb", db_id=DB_ID, app_config=_cfg(), template_sink=sink, **kw,
    )


class TestMultiPath:
    async def test_same_sql_and_marker_as_single(self, with_file, fake_llm_fallback):
        single_llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        single = await qg.query_generator(_single_state(), llm=single_llm, app_config=_cfg())
        multi_llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        sink: dict = {}
        sql = await _multi_generate(multi_llm, sink)
        assert sql == single["generated_sql"] == _EXPECTED_SQL
        assert sink == single["template_assembly"]
        assert multi_llm.prompts == single_llm.prompts, "같은 함수 · 같은 프롬프트"

    async def test_error_context_and_form_not_entered(self, with_file, monkeypatch):
        invoke = AsyncMock(return_value="SELECT hostname FROM servers")
        monkeypatch.setattr(mdb, "_invoke_llm_for_sql", invoke)
        monkeypatch.setattr(mdb, "_build_multi_system_prompt", AsyncMock(return_value="sys"))
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        sink: dict = {}
        await _multi_generate(llm, sink, error_context="Unknown column")
        await _multi_generate(llm, sink, form_intent=True)
        assert llm.prompts == [] and sink == {} and invoke.await_count == 2

    async def test_no_file_no_call(self, monkeypatch):
        invoke = AsyncMock(return_value="SELECT hostname FROM servers")
        monkeypatch.setattr(mdb, "_invoke_llm_for_sql", invoke)
        monkeypatch.setattr(mdb, "_build_multi_system_prompt", AsyncMock(return_value="sys"))
        llm = _FakeLLM(_pick("servers_by_center", center="김포"))
        sink: dict = {}
        assert await _multi_generate(llm, sink) == "SELECT hostname FROM servers"
        assert llm.prompts == [] and sink == {}

    async def test_node_lifts_marker_to_state(self, with_file, monkeypatch):
        class _Registry:
            def __init__(self, cfg: Any) -> None:
                pass

            def is_registered(self, db_id: str) -> bool:
                return True

            def get_client(self, db_id: str) -> Any:
                @asynccontextmanager
                async def _ctx():
                    client = AsyncMock()
                    client.execute_sql.return_value = SimpleNamespace(
                        rows=[{"hostname": "a"}], row_count=1,
                    )
                    yield client

                return _ctx()

        async def _schema(*_a: Any, **_kw: Any) -> dict:
            return copy.deepcopy(_SCHEMA)

        monkeypatch.setattr(mdb, "DBRegistry", _Registry)
        monkeypatch.setattr(mdb, "_analyze_schema", _schema)
        monkeypatch.setattr(mdb, "get_domain_by_id", lambda _d: SimpleNamespace(
            db_engine="mariadb", db_schema="", label="tpl",
        ))
        monkeypatch.setattr(mdb, "log_query_execution", AsyncMock())
        monkeypatch.setattr(mdb, "observe_rewrite", AsyncMock(return_value={}))
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        state = create_initial_state(user_query=_QUESTION)
        state["parsed_requirements"] = {"original_query": _QUESTION}
        state["target_databases"] = [{"db_id": DB_ID, "sub_query_context": _QUESTION}]
        out = await mdb.multi_db_executor(state, llm=llm, app_config=_cfg())
        assert out["template_assembly"] == {DB_ID: {
            "outcome": "assembled", "template_id": "servers_by_center",
            "slot_names": ["center", "status"], "reason": None,
            "final_sql_from_template": True,
        }}
        assert out["db_executed_sqls"][DB_ID] == _EXPECTED_SQL
        assert len(llm.prompts) == 1


class TestRequestScope:
    def test_initialized_in_both_state_builders(self):
        assert create_initial_state(user_query="q")["template_assembly"] is None
        assert create_followup_input("q")["template_assembly"] is None


# ──────────────────────────────────────────────
# fix3 작업 3 — 「템플릿 적중 뒤 최종 SQL은 LLM이 고침」 표지(단일·멀티 대칭 · D-066)
# ──────────────────────────────────────────────

_ASSEMBLED = {
    "outcome": "assembled", "template_id": "servers_by_center",
    "slot_names": ["center", "status"], "reason": None, "final_sql_from_template": True,
}


class TestFinalSqlFromTemplate:
    def test_as_state_and_mark_regenerated(self):
        ok = ta.TemplateOutcome(sql="SELECT 1", template_id="t", outcome=qt.OUTCOME_ASSEMBLED)
        fb = ta.TemplateOutcome(sql=None, template_id=None, reason=qt.REASON_NO_MATCH)
        assert ok.as_state()["final_sql_from_template"] is True
        assert fb.as_state()["final_sql_from_template"] is False
        assert ta.mark_regenerated(ok.as_state()) == {
            **ok.as_state(), "final_sql_from_template": False,
        }
        assert ta.mark_regenerated(fb.as_state()) is None          # 폴백은 그대로
        assert ta.mark_regenerated(None) is None
        assert ta.mark_regenerated(ta.mark_regenerated(ok.as_state())) is None  # 이미 False

    async def test_single_retry_after_assembly_marks_llm_revised(
        self, with_file, fake_llm_fallback,
    ):
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        first = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg())
        assert first["template_assembly"][DB_ID] == _ASSEMBLED
        # 같은 요청의 재시도 턴(검증·실행 실패 회귀) — 템플릿 미진입 · LLM이 다시 만든다
        retry = _single_state(
            error_message="Unknown column", retry_count=1,
            template_assembly={"other": {"outcome": "fallback"}, **first["template_assembly"]},
        )
        out = await qg.query_generator(retry, llm=llm, app_config=_cfg())
        assert out["generated_sql"] == "SELECT hostname FROM servers"
        assert out["template_assembly"] == {
            "other": {"outcome": "fallback"},
            DB_ID: {**_ASSEMBLED, "final_sql_from_template": False},
        }
        assert fake_llm_fallback == [1] and len(llm.prompts) == 1

    async def test_single_retry_after_fallback_leaves_marker(self, with_file, fake_llm_fallback):
        llm = _FakeLLM(_pick("none"))
        prev = {DB_ID: {"outcome": "fallback", "template_id": None, "slot_names": [],
                        "reason": "no_match", "final_sql_from_template": False}}
        out = await qg.query_generator(
            _single_state(error_message="Unknown column", retry_count=1, template_assembly=prev),
            llm=llm, app_config=_cfg(),
        )
        assert "template_assembly" not in out

    async def test_multi_regeneration_marks_llm_revised(self, with_file, monkeypatch):
        invoke = AsyncMock(return_value="SELECT hostname FROM servers")
        monkeypatch.setattr(mdb, "_invoke_llm_for_sql", invoke)
        monkeypatch.setattr(mdb, "_build_multi_system_prompt", AsyncMock(return_value="sys"))
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        sink: dict = {}
        assert await _multi_generate(llm, sink) == _EXPECTED_SQL
        assert sink == {DB_ID: _ASSEMBLED}
        # 같은 run의 재생성(error_context) — 템플릿 미진입 · LLM 생성 → 표지 갱신
        assert await _multi_generate(llm, sink, error_context="Unknown column") == (
            "SELECT hostname FROM servers"
        )
        assert sink == {DB_ID: {**_ASSEMBLED, "final_sql_from_template": False}}
        assert invoke.await_count == 1

    async def test_single_and_multi_markers_are_symmetric(
        self, with_file, fake_llm_fallback, monkeypatch,
    ):
        monkeypatch.setattr(
            mdb, "_invoke_llm_for_sql", AsyncMock(return_value="SELECT hostname FROM servers")
        )
        monkeypatch.setattr(mdb, "_build_multi_system_prompt", AsyncMock(return_value="sys"))
        llm = _FakeLLM(_pick("servers_by_center", center="김포", status="RUN"))
        first = await qg.query_generator(_single_state(), llm=llm, app_config=_cfg())
        single = await qg.query_generator(
            _single_state(error_message="Unknown column", retry_count=1,
                          template_assembly=first["template_assembly"]),
            llm=llm, app_config=_cfg(),
        )
        sink: dict = {}
        await _multi_generate(llm, sink)
        await _multi_generate(llm, sink, error_context="Unknown column")
        assert sink == single["template_assembly"]

    def test_tier2_task_state_carries_flag_to_bench(self):
        """2단 — `_pack_pipeline_result`가 내보내는 task 상태(`task_pipeline_state`)의 표지를
        벤치 수신기가 그대로 읽는다(재시도로 바뀐 False 포함)."""
        from scripts.itam_bench._serve import schema_context_record
        from src.observability import run_capture
        from src.orchestration import subagents

        s = _single_state(
            thread_id="t-tier2",
            template_assembly={DB_ID: {**_ASSEMBLED, "final_sql_from_template": False}},
        )
        captured: list[tuple[str, Any]] = []
        run_capture.install(lambda kind, payload: captured.append((kind, payload)))
        try:
            subagents._pack_pipeline_result(
                s, [], None, ownership_notes=[], db_origin="planner",
                db_succeeded=True, db_pinned=False,
            )
        finally:
            run_capture.uninstall()
        kinds = [k for k, _ in captured]
        assert kinds == ["task_pipeline_state"]
        record = schema_context_record(*captured[0])
        assert record is not None
        assert record["dbs"][DB_ID]["template"]["final_sql_from_template"] is False
