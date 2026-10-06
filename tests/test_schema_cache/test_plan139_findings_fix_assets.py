"""plans/139 검증 교정 — 테이블 정의 자산 경로(F-2 · F-3 · F-8).

- F-2: 가져오기 YAML 앵커·별칭 거절(펼치기 전) · 깊은 중첩은 사용자 오류(422 — 500 금지) ·
  오류 문구·표시용 사본은 비문자열 값을 짧은 발췌로(별칭으로 부푼 값을 펼치지 않는다)
- F-3: NFKC 정규화 뒤 금지 검사(전각 우회 차단) · 서식 문자(Cf) 거절 · `~~~` 펜스 ·
  group 상한·금지 검사
- F-8: LLM 초안의 key_columns 상한 초과는 앞 10개로 결정적 절단(가져오기·편집은 종전대로 거절)

LLM·DB·네트워크 0.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from src.domain.table_definitions import (
    GROUP_MAX_CHARS,
    IMPORT_MAX_CHARS,
    KEY_COLUMNS_MAX,
    VALUE_EXCERPT_MAX_CHARS,
    describe_value,
    parse_import_document,
    validate_table_definitions,
)
from src.schema_cache import asset_generation_service as sas
from tests.test_api.test_plan139_table_definitions_api import BASE, _Audit, _client
from tests.test_schema_cache.test_plan104_service_fixtures import make_env, make_registry
from tests.test_schema_cache.test_plan139_table_definitions_asset import (
    SRC,
    DefinitionLLM,
    _wide_tables,
)

_COLS = {"t1": ["a", "b"]}


def _bomb(levels: int) -> str:
    lines = ['l0: &l0 ["xxxx","xxxx","xxxx","xxxx","xxxx","xxxx","xxxx","xxxx","xxxx"]']
    for i in range(1, levels):
        lines.append(f"l{i}: &l{i} [" + ",".join([f"*l{i - 1}"] * 9) + "]")
    lines += ["tables:", "  t1:", f"    kind: *l{levels - 1}", "    manages: ok"]
    return "\n".join(lines) + "\n"


def _validate(**fields: Any) -> tuple[dict, dict]:
    entry = {"manages": "서버 목록", "origin": "import", **fields}
    return validate_table_definitions({"t1": entry}, _COLS)


# ──────────────────────────────────────────────
# F-2 별칭 폭탄 · 깊은 중첩
# ──────────────────────────────────────────────


class TestImportYamlBounds:
    def test_alias_bomb_rows_bounded_even_if_loaded(self):
        """로더를 거치지 않고 펼쳐진 값이 들어와도 오류 문구·표시용 사본은 펼치지 않는다."""
        text = _bomb(6)
        assert len(text) < 400
        raw = parse_import_document(yaml.safe_load(text))
        rows, errors = sas._definition_rows(raw, {"t1": ["a"]}, "import")
        stored = json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False)
        assert len(stored) <= IMPORT_MAX_CHARS
        assert len(stored) < 2_000, "행 1개 — 짧은 발췌만 남는다"
        assert rows["t1"]["kind"].startswith("list ")
        assert any("kind는" in m and "list " in m for m in errors["t1"])

    @pytest.mark.parametrize("text", [
        _bomb(3),
        "tables:\n  t1: &a\n    manages: x\n",
        "base: &b {manages: x}\ntables:\n  t1: *b\n",
        "base: &b {manages: x}\ntables:\n  t1:\n    <<: *b\n",
    ])
    def test_loader_rejects_anchor_and_alias(self, text):
        with pytest.raises(ValueError, match="앵커"):
            sas.load_import_yaml(text)

    def test_deep_nesting_is_user_error(self):
        with pytest.raises(ValueError, match="중첩"):
            sas.load_import_yaml("tables: " + "[" * 2000 + "]" * 2000 + "\n")

    def test_plain_yaml_still_loads(self):
        doc = sas.load_import_yaml("tables:\n  t1:\n    manages: 서버 목록\n    kind: 현행\n")
        assert doc == {"tables": {"t1": {"manages": "서버 목록", "kind": "현행"}}}

    def test_seed_document_loads_without_alias(self):
        from pathlib import Path

        seed = Path(__file__).resolve().parents[2] / "testdata/itam_bench/closed"
        text = (seed / "table_definitions.yaml").read_text(encoding="utf-8")
        assert sas.load_import_yaml(text) == yaml.safe_load(text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "mariadb", _wide_tables())
    e.registry = make_registry({"db_id": SRC, "engine": "mariadb", "description": "가상 업무 DB"})
    return e


@pytest.mark.parametrize("text", [
    _bomb(4),
    "tables: " + "[" * 2000 + "]" * 2000 + "\n",
])
def test_import_api_returns_422_not_500(env, text):
    with _client(env, _Audit(), DefinitionLLM()) as client:
        response = client.post(f"{BASE}/{SRC}/table-definitions/import", json={"text": text})
    assert response.status_code == 422, response.text
    assert "YAML을 읽지 못했습니다" in response.json()["detail"]


class TestDescribeValue:
    def test_short_string_is_repr(self):
        assert describe_value("현재") == "'현재'"

    def test_long_string_is_cut(self):
        text = describe_value("가" * 500)
        assert text.endswith("…") and len(text) <= VALUE_EXCERPT_MAX_CHARS + 4

    @pytest.mark.parametrize("value", [
        [["x"] * 9] * 9, {"a": {"b": {"c": ["x"] * 1000}}}, 12345, None, 1.5,
    ])
    def test_non_string_is_type_and_excerpt(self, value):
        text = describe_value(value)
        assert text.startswith(type(value).__name__ + " ")
        assert len(text) <= VALUE_EXCERPT_MAX_CHARS

    def test_display_row_non_string_values(self):
        row = sas._display_row(
            {"manages": ["a"] * 50, "key_columns": [["x"] * 50, "id"], "related": {"t2": [1] * 50}},
            "import",
        )
        assert row["manages"].startswith("list ") and len(row["manages"]) <= VALUE_EXCERPT_MAX_CHARS
        assert row["key_columns"][1] == "id" and row["key_columns"][0].startswith("list ")
        assert row["related"]["t2"].startswith("list ")


# ──────────────────────────────────────────────
# F-3 텍스트 검증 틈
# ──────────────────────────────────────────────


class TestTextChecks:
    def test_zero_width_hides_sql_keyword_in_backticks(self):
        valid, errors = _validate(manages="조회 예: `SEL​ECT * FROM t1`")
        assert valid == {} and "서식 문자" in errors["t1"][0]

    @pytest.mark.parametrize("text", ["﻿서버", "서버‍목록", "서버­목록"])
    def test_format_chars_rejected(self, text):
        valid, errors = _validate(manages=text)
        assert valid == {} and errors["t1"]

    @pytest.mark.parametrize(("text", "needle"), [
        ("조회 예: `ＳＥＬＥＣＴ * FROM t1`", "SQL 키워드(SELECT)"),
        ("값 ｛user_query｝ 주입", "중괄호"),
        ("~~~sql 블록", "코드 펜스(~~~)"),
        ("전각 백틱 ｀하나", "닫히지 않은 백틱"),
    ])
    def test_nfkc_and_tilde_fence(self, text, needle):
        valid, errors = _validate(manages=text)
        assert valid == {} and any(needle in m for m in errors["t1"]), errors

    def test_group_has_length_and_forbidden_check(self):
        valid, errors = _validate(group="```" + "가" * 5000)
        assert valid == {}
        joined = " ".join(errors["t1"])
        assert f"{GROUP_MAX_CHARS}자 이하" in joined and "코드 펜스" in joined

    def test_group_within_limit_kept(self):
        valid, errors = _validate(group=" 가" * 15)
        assert errors == {} and valid["t1"]["group"] == ("가 " * 15).strip()

    def test_fullwidth_text_without_forbidden_is_kept_as_is(self):
        valid, errors = _validate(manages="ＣＰＵ 사용률 집계")
        assert errors == {} and valid["t1"]["manages"] == "ＣＰＵ 사용률 집계"


# ──────────────────────────────────────────────
# F-8 LLM 초안 key_columns 절단
# ──────────────────────────────────────────────


class TestLlmKeyColumnsCap:
    columns = [f"c{i}" for i in range(KEY_COLUMNS_MAX + 3)]

    def test_llm_row_is_cut_to_first_ten(self, caplog):
        rows: dict[str, Any] = {}
        errors: dict[str, list[str]] = {}
        raw = {"t1": {"manages": "서버 목록", "kind": "현행", "key_columns": list(self.columns),
                      "origin": "llm"}}
        with caplog.at_level(logging.INFO, logger=sas.__name__):
            batch = sas._merge_definition_batch(
                rows, errors, ["t1"], raw, None, {"t1": self.columns},
            )
        assert batch["status"] == "ok" and errors == {}
        assert rows["t1"]["key_columns"] == self.columns[:KEY_COLUMNS_MAX]
        assert raw["t1"]["key_columns"] == self.columns, "원본은 바꾸지 않는다"
        assert "key_columns 절단" in caplog.text

    def test_import_and_edit_paths_still_reject(self):
        raw = {"t1": {"manages": "서버 목록", "key_columns": list(self.columns),
                      "origin": "import"}}
        rows, errors = sas._definition_rows(raw, {"t1": self.columns}, "import")
        assert f"{KEY_COLUMNS_MAX}개 이하" in errors["t1"][0]
