"""plans/141 W6 독립 검증 — K8 템플릿 조립의 주입·와일드카드·로그 누출 경계.

구현 테스트(`test_plan141_template_assembly.py`)와 겹치지 않는 경계만 본다.
- 허용 문자 클래스 밖 유니코드 따옴표·백틱 변형 전수 거절(슬롯 형식별)
- 허용 코드 목록에 따옴표가 섞인 값이 있어도 인용 전에 거절
- 키워드 슬롯의 LIKE 와일드카드(`%`·`_`) 거절
- 호스트명 슬롯을 LIKE에 쓰면 `_`가 그대로 남는다(결함 기록)
- 검증 실패·가드 거절 폴백 로그에 슬롯 값이 남지 않는다
- 바인딩 값 안의 자리표 모양(`:x`)이 재치환되지 않는다

LLM·DB 0 — 가짜 LLM과 tmp 템플릿 파일만 쓴다(D-127).
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.config import AppConfig, Text2SQLConfig
from src.db_adapters import template_assembler as ta
from src.domain import query_templates as qt

DB_ID = "verify_db"
_VALUE = "김포센터"


def _tpl(sql: str, slots: list[dict[str, Any]], tables: list[str] | None = None) -> dict:
    return {
        "id": "t1", "intent": "검증용", "triggers": [], "slots": slots,
        "sql": sql, "tables": tables or ["servers"], "status": "active",
    }


def _parse(doc_tpl: dict) -> qt.QueryTemplate:
    templates, issues = qt.parse_templates({"version": 1, "templates": [doc_tpl]})
    assert not issues, issues
    return templates[0]


_CENTER_TPL = _tpl(
    "SELECT hostname FROM servers WHERE center = :center",
    [{"name": "center", "type": "center"}],
)


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        return SimpleNamespace(content=self.response)


@pytest.fixture(autouse=True)
def _root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ta, "DEFAULT_ROOT", tmp_path)
    ta._cache.clear()
    yield tmp_path
    ta._cache.clear()


def _write(root: Path, templates: list[dict]) -> None:
    path = root / ta.TEMPLATE_PATH.format(db_id=DB_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"version": 1, "templates": templates}, allow_unicode=True),
        encoding="utf-8",
    )


def _cfg() -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(template_assembly=True)
    return cfg


# ──────────────────────────────────────────────
# 슬롯 형식 — 유니코드 따옴표·백틱 변형
# ──────────────────────────────────────────────

_QUOTE_LIKES = [
    "❛", "❜", "❝", "❞",  # 장식 따옴표
    "｀",  # 전각 백틱
    "ʹ", "ʺ", "ʼ",  # 수식 문자 프라임·아포스트로피
    "‹", "›", "«", "»",  # 홑·겹 꺾쇠 따옴표
    "〝", "〞", "＂", "＇",
    "`", "´", "‘", "“",
]


@pytest.mark.parametrize("slot_type", ["center", "hostname", "keyword", "dept_code"])
@pytest.mark.parametrize("quote", _QUOTE_LIKES)
def test_quote_like_characters_rejected_in_every_free_text_slot(slot_type: str, quote: str) -> None:
    spec = qt.SlotSpec(name="x", type=slot_type)
    with pytest.raises(qt.SlotRejectedError):
        qt.normalize_slot(spec, f"abc{quote}def")


@pytest.mark.parametrize(
    "value",
    ["A'B", "A\\B", "A;B", "A--B", "A/*B", "A#B", "A’B", "A\nB"],
)
def test_code_slot_rejects_unsafe_value_even_if_in_allowed_list(value: str) -> None:
    """허용 목록(DB 값)에 따옴표 등이 섞여 있어도 인용 전에 거절한다."""
    spec = qt.SlotSpec(name="st", type="code", column="servers.status")
    with pytest.raises(qt.SlotRejectedError):
        qt.normalize_slot(spec, value, {"servers.status": [value]})


@pytest.mark.parametrize("value", ["50%", "a_b", "%", "_"])
def test_keyword_slot_rejects_like_wildcards(value: str) -> None:
    spec = qt.SlotSpec(name="kw", type="keyword")
    with pytest.raises(qt.SlotRejectedError):
        qt.normalize_slot(spec, value)


def test_hostname_slot_in_like_is_rejected_or_escaped() -> None:
    raw = {
        "version": 1,
        "templates": [_tpl(
            "SELECT hostname FROM servers WHERE hostname LIKE CONCAT(:host, '%')",
            [{"name": "host", "type": "hostname"}],
        )],
    }
    templates, issues = qt.parse_templates(raw)
    if issues:  # 계약이 거절하면 통과
        return
    bound = qt.bind_template(templates[0], {"host": "web_01"})
    assert bound.sql is None or "web\\_01" in bound.sql


def test_bound_value_with_placeholder_shape_is_not_resubstituted() -> None:
    """값 안의 `:center` 모양은 형식 검사에서 막히고, 통과 값은 한 번만 치환된다."""
    tpl = _parse(_tpl(
        "SELECT hostname FROM servers WHERE center = :center AND hostname = :host",
        [{"name": "center", "type": "center"}, {"name": "host", "type": "hostname"}],
    ))
    rejected = qt.bind_template(tpl, {"center": "A", "host": "x:center"})
    assert rejected.sql is None and rejected.reason == qt.REASON_SLOT_REJECTED
    ok = qt.bind_template(tpl, {"center": "A", "host": "web-01"})
    assert ok.sql == (
        "SELECT hostname FROM servers WHERE center = 'A' AND hostname = 'web-01'"
    )


def test_placeholder_inside_double_quoted_string_is_not_a_slot() -> None:
    """MariaDB 기본 모드의 큰따옴표 문자열 안 `:x`는 자리표가 아니다(계약 불일치로 잡힌다)."""
    raw = {"version": 1, "templates": [_tpl(
        'SELECT hostname FROM servers WHERE note = ":center"',
        [{"name": "center", "type": "center"}],
    )]}
    _templates, issues = qt.parse_templates(raw)
    assert any("자리표 불일치" in i for i in issues)


# ──────────────────────────────────────────────
# 폴백 로그 — 슬롯 값 누출 없음
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validation_failure_log_has_no_slot_value(
    _root: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """조립 SQL이 검증에서 떨어질 때 폴백 로그(검증 오류 문구 포함)에 슬롯 값이 없다."""
    _write(_root, [_CENTER_TPL])
    schema = {"tables": {"other": {"columns": [{"name": "x", "type": "varchar"}]}}}
    llm = _FakeLLM(json.dumps({"template_id": "t1", "slots": {"center": _VALUE}}))
    with caplog.at_level(logging.DEBUG):
        out = await ta.assemble_from_template(
            llm=llm, question="q", db_id=DB_ID, schema_info=copy.deepcopy(schema),
            app_config=_cfg(), db_engine="mariadb", user_query="q", default_limit=10,
        )
    assert out is not None and out.sql is None
    assert out.reason == qt.REASON_VALIDATION_FAILED
    assert _VALUE not in caplog.text
    assert _VALUE not in json.dumps(out.as_state(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_guard_rejection_log_has_no_slot_value(
    _root: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """실제 `SQLGuard`가 거절한 조립 SQL — 폴백 로그(가드 사유 포함)에 슬롯 값이 없다."""
    _write(_root, [_tpl(
        "SELECT hostname, SLEEP(1) FROM servers WHERE center = :center",
        [{"name": "center", "type": "center"}],
    )])
    llm = _FakeLLM(json.dumps({"template_id": "t1", "slots": {"center": _VALUE}}))
    with caplog.at_level(logging.DEBUG):
        out = await ta.assemble_from_template(
            llm=llm, question="q", db_id=DB_ID,
            schema_info={"tables": {"servers": {"columns": [
                {"name": "hostname", "type": "varchar"}, {"name": "center", "type": "varchar"},
            ]}}},
            app_config=_cfg(), db_engine="mariadb", user_query="q", default_limit=10,
        )
    assert out is not None and out.reason == qt.REASON_GUARD_REJECTED
    assert _VALUE not in caplog.text


@pytest.mark.asyncio
async def test_unknown_template_id_not_logged(
    _root: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _write(_root, [_CENTER_TPL])
    llm = _FakeLLM(json.dumps({"template_id": "evil'; DROP", "slots": {}}))
    with caplog.at_level(logging.DEBUG):
        out = await ta.assemble_from_template(
            llm=llm, question="q", db_id=DB_ID, schema_info={}, app_config=_cfg(),
            db_engine="mariadb",
        )
    assert out is not None and out.reason == qt.REASON_UNKNOWN_TEMPLATE
    assert out.template_id is None
    assert "DROP" not in caplog.text


@pytest.mark.asyncio
async def test_slot_keyword_value_cannot_smuggle_sql_keywords_outside_literal(
    _root: Path,
) -> None:
    """키워드 값에 SQL 낱말이 있어도 리터럴 안에 갇힌다(조립 SQL의 따옴표 수 불변)."""
    tpl = _parse(_tpl(
        "SELECT hostname FROM servers WHERE center = :center",
        [{"name": "center", "type": "center"}],
    ))
    bound = qt.bind_template(tpl, {"center": "A OR 1 = 1 UNION SELECT x"})
    # `=` 은 center 형식 밖이라 거절
    assert bound.sql is None
    bound = qt.bind_template(tpl, {"center": "A OR 1 UNION SELECT x"})
    assert bound.sql == "SELECT hostname FROM servers WHERE center = 'A OR 1 UNION SELECT x'"
    assert bound.sql.count("'") == 2
