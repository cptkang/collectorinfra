"""plans/141 W1·W2 — 지식 자산 순수 함수(원천 파일 형식·정적 검사·K6 파생 · 근거 묶음 조립).

합성 이름(`zzab01` 군)으로 계약을 고정한다: 공통 칸 · 언급 식별자(백틱 · `table.column` · 조사 붙은
표기 · 테이블 이름형 토큰) · 조회 대상 밖 · 중괄호·펜스 · 사용률 규칙(D-308 G-6) · 설명 값 금지 ·
유사어 쓰기 가드(D-142) · K6 규칙 · 근거 묶음 값 칸 0 · 결정성.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.domain import knowledge_assets as ka
from src.domain import knowledge_evidence as ke

_COLUMNS = {
    "zzab01": ["그룹코드", "서버호스트명", "활성화여부", "상태구분"],
    "zzab02": ["그룹코드", "서버호스트명"],
    "zzab03": ["그룹코드", "기준년월일", "서버호스트명"],
    "zzab09": ["비밀번호"],
}
_ALLOWED = ["zzab01", "zzab02", "zzab03"]


@pytest.fixture
def catalog() -> ka.Catalog:
    return ka.make_catalog(_COLUMNS, _ALLOWED)


def _codes(issues: list[dict[str, str]]) -> list[str]:
    return [i["code"] for i in issues]


def _item(**fields: Any) -> dict[str, Any]:
    base = {"origin": ka.ORIGIN, "evidence": "20261101-000000", "status": ka.STATUS_ACTIVE}
    return {**base, **fields}


class TestParseFile:
    def test_common_fields(self) -> None:
        doc = {
            "version": 1,
            "items": [
                _item(id="a", text="정상"),
                _item(id="a", text="겹침"),
                {"id": "b", "text": "출처 없음", "status": "active"},
                _item(id="c", text="x", status="draft"),
                _item(id="d", status=ka.STATUS_WITHDRAWN),
                _item(id="e", status=ka.STATUS_WITHDRAWN, reason="내부망 실패"),
                _item(id="f"),
            ],
        }
        parsed = ka.parse_file(ka.GUIDE_FILE, doc)
        codes = {e.label: _codes(e.issues) for e in parsed.entries}
        assert codes["a"] == [ka.DUPLICATE_ID]
        assert set(codes["b"]) == {ka.BAD_ORIGIN, ka.MISSING_FIELD}
        assert ka.BAD_STATUS in codes["c"]
        assert codes["d"] == [ka.MISSING_REASON]
        assert codes["e"] == []
        assert codes["f"] == [ka.MISSING_FIELD]
        assert [e.label for e in parsed.active] == ["a", "a", "b", "c", "f"]

    @pytest.mark.parametrize("doc", [[], {"items": []}, {"version": 1, "items": {}}])
    def test_file_invalid(self, doc: Any) -> None:
        parsed = ka.parse_file(ka.GUIDE_FILE, doc)
        assert _codes(parsed.file_issues) == [ka.FILE_INVALID] and not parsed.entries

    def test_list_field_shape(self) -> None:
        doc = {
            "version": 1,
            "items": [_item(id="s", table="zzab01", column="상태구분", words="말")],
        }
        assert _codes(ka.parse_file(ka.SYNONYMS_FILE, doc).entries[0].issues) == [ka.BAD_FIELD]


class TestGuide:
    def test_normal_passes(self, catalog: ka.Catalog) -> None:
        text = (
            "서버 원장은 `zzab01`이다 — 호스트는 zzab01.서버호스트명으로 찾고 "
            "`WHERE 활성화여부 = 'x'` 조건은 코드값 안내를 따른다. "
            "CPU 사용률은 관측 DB가 정본이라 이 DB에서 답하지 않는다."
        )
        assert ka.guide_item_issues(text, catalog) == []

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("`zzab01.없는칸`으로 찾는다", ka.UNKNOWN_IDENTIFIER),
            ("zzab77을 쓴다", ka.UNKNOWN_IDENTIFIER),
            ("zzab01.없는칸으로 찾는다", ka.UNKNOWN_IDENTIFIER),
            ("`zzab09`를 본다", ka.TABLE_NOT_ALLOWED),
            ("값은 {x}이다", ka.BRACES),
            ("예시\n```sql\nSELECT 1\n```", ka.CODE_FENCE),
            ("서버 CPU 사용률은 `zzab01`에서 조회한다.", ka.UTILIZATION_RULE),
            ("메모리 사용량을 보여 준다", ka.UTILIZATION_RULE),
        ],
    )
    def test_rejects(self, catalog: ka.Catalog, text: str, code: str) -> None:
        assert code in _codes(ka.guide_item_issues(text, catalog))

    @pytest.mark.parametrize(
        "text",
        [
            "사용률 칸이 있지만 정본이 아니다 — "
            "사용률 질문은 이 DB에서 답하지 않고 관측 DB로 넘긴다.",
            "CPU 사용률, 메모리 사용률은 이 DB에서 답하지 말고 관측 DB로 넘긴다.",
            "사용률 — 사용률 칸으로 사용률을 답하는 SQL은 만들지 않는다(정본은 관측 DB).",
        ],
    )
    def test_utilization_redirect_passes(self, text: str) -> None:
        """부정·관측 DB 안내가 사용률을 말한 절에 있으면 통과."""
        assert ka.utilization_issues(text) == []

    @pytest.mark.parametrize(
        "text",
        [
            "사용률 질문은 `zzab01`로 답하며 NULL 행은 세지 않는다.",
            "사용률은 `zzab01`에서 조회하는데 결과가 없다면 비운다.",
        ],
    )
    def test_utilization_unrelated_negation_rejected(self, text: str) -> None:
        """같은 문장의 다른 절 부정어로는 풀리지 않는다."""
        assert _codes(ka.utilization_issues(text)) == [ka.UTILIZATION_RULE]

    def test_total_length(self) -> None:
        assert ka.guide_total_issues(["가" * 4000, "나" * 4000]) != []
        assert ka.guide_total_issues(["가" * 3999, "나" * 4000]) == []


class TestExamples:
    def test_tables_and_utilization(self, catalog: ka.Catalog) -> None:
        ok = {"question": "서버 몇 대", "description": "원장 행 수", "tables": ["zzab01"]}
        assert ka.example_item_issues(ok, catalog) == []
        bad = {"question": "CPU 사용률 보여줘", "description": "d", "tables": ["zzab77", "zzab09"]}
        assert _codes(ka.example_item_issues(bad, catalog)) == [
            ka.UTILIZATION_RULE,
            ka.UNKNOWN_IDENTIFIER,
            ka.TABLE_NOT_ALLOWED,
        ]


class TestDescriptions:
    def test_normal(self, catalog: ka.Catalog) -> None:
        item = {
            "table": "zzab01",
            "column": "상태구분",
            "text": "서버 운영 상태 구분(코드값은 코드 안내)",
        }
        assert ka.description_item_issues(item, catalog) == []

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("상태 'RUN' 이면 운영", ka.VALUE_LITERAL),
            ("1:정상, 2:장애", ka.VALUE_LITERAL),
            ("Y=사용", ka.VALUE_LITERAL),
            ("관리번호 123456 고정", ka.VALUE_LITERAL),
            ("가" * 201, ka.TOO_LONG),
            ("{x}", ka.BRACES),
        ],
    )
    def test_rejects(self, catalog: ka.Catalog, text: str, code: str) -> None:
        item = {"table": "zzab01", "column": "상태구분", "text": text}
        assert code in _codes(ka.description_item_issues(item, catalog))

    def test_unknown_column(self, catalog: ka.Catalog) -> None:
        item = {"table": "zzab01", "column": "없는칸", "text": "설명"}
        assert _codes(ka.description_item_issues(item, catalog)) == [ka.UNKNOWN_COLUMN]

    def test_utilization_rule(self, catalog: ka.Catalog) -> None:
        """K3 설명도 K1·K4와 같은 사용률 규칙(D-308 G-6)."""
        bad = {
            "table": "zzab01", "column": "상태구분",
            "text": "CPU 사용률. 사용률 질문은 이 칸으로 답한다.",
        }
        assert _codes(ka.description_item_issues(bad, catalog)) == [ka.UTILIZATION_RULE]
        ok = {
            "table": "zzab01", "column": "상태구분",
            "text": "수집 시점 사용률 기록일 뿐 사용률 질문의 정본이 아니다 — "
            "사용률은 관측 DB로 답한다.",
        }
        assert ka.description_item_issues(ok, catalog) == []


class TestSynonyms:
    def test_item_guard(self, catalog: ka.Catalog) -> None:
        item = {
            "table": "zzab01",
            "column": "상태구분",
            "words": ["상태", "x", "서버호스트명", "상태구분"],
        }
        codes = _codes(ka.synonym_item_issues(item, catalog))
        assert codes == [ka.TOO_SHORT, ka.NAME_CONFLICT]
        assert "서버호스트명" in ka.synonym_item_issues(item, catalog)[1]["message"]
        assert "상태구분" not in ka.synonym_item_issues(item, catalog)[1]["message"]

    def test_utilization_word_and_column(self, catalog: ka.Catalog) -> None:
        """사용률 낱말 · 사용률 컬럼 대상 유사어는 거절(D-308 G-6)."""
        word = {"table": "zzab01", "column": "상태구분", "words": ["상태값", "CPU 사용률"]}
        found = ka.synonym_item_issues(word, catalog)
        assert _codes(found) == [ka.UTILIZATION_RULE] and "상태값" not in found[0]["message"]
        util = ka.make_catalog({"zzab01": ["서버CPU사용률"]}, ["zzab01"])
        column = {"table": "zzab01", "column": "서버CPU사용률", "words": ["CPU 부하"]}
        assert _codes(ka.synonym_item_issues(column, util)) == [ka.UTILIZATION_RULE]

    def test_ambiguous_rejects_all(self) -> None:
        items = [
            {"table": "zzab01", "column": "상태구분", "words": ["상태값"]},
            {"table": "zzab01", "column": "활성화여부", "words": ["상태값", "활성"]},
            {"table": "zzab02", "column": "서버호스트명", "words": ["호스트"]},
            {"table": "zzab03", "column": "서버호스트명", "words": ["호스트"]},
        ]
        found = ka.ambiguous_synonyms(items)
        assert sorted(found) == [0, 1]  # 같은 이름 컬럼(쌍둥이 테이블)은 한 뜻
        assert "상태값" in found[1][0]["message"] and "활성" not in found[1][0]["message"]

    def test_duplicate_target(self) -> None:
        items = [
            {"table": "zzab01", "column": "상태구분"},
            {"table": "ZZAB01", "column": "상태구분"},
            {"table": "zzab02", "column": "서버호스트명"},
        ]
        assert sorted(ka.duplicate_targets(items)) == [0, 1]


class TestSection:
    def test_prose_only(self, catalog: ka.Catalog) -> None:
        text = "- 호스트는 `zzab01.서버호스트명`.\n```sql\nSELECT 없는칸 FROM zzab01 LIMIT 5\n```"
        assert ka.section_item_issues(text, catalog) == []  # 펜스 SQL은 SQL 검사가 본다
        assert _codes(ka.section_item_issues("- `zzab01.없는칸`", catalog)) == [
            ka.UNKNOWN_IDENTIFIER
        ]


class TestDeriveKindRules:
    _DEFS = {
        "zzab01": {"kind": "현행"},
        "zzab02": {"kind": "수집적재"},
        "zzab03": {"kind": "수집이력"},
        "zzab09": {"kind": "수집적재"},
    }

    def test_rules(self, catalog: ka.Catalog) -> None:
        rules = ka.derive_kind_rules(self._DEFS, _COLUMNS, allowed=_ALLOWED)
        assert len(rules) == 3
        assert "`zzab01.활성화여부`" in rules[0]
        assert "`zzab03.기준년월일`(YYYYMMDD 추정)" in rules[1]
        assert "`zzab02`" in rules[2] and "zzab09" not in rules[2]
        for rule in rules:
            assert ka.text_form_issues(rule) == [] and ka.mention_issues(rule, catalog) == []

    def test_non_string_date_skipped_and_deterministic(self) -> None:
        types = {"zzab03": {"기준년월일": "int"}}
        rules = ka.derive_kind_rules(self._DEFS, _COLUMNS, types=types, allowed=_ALLOWED)
        assert not any("기준년월일" in r for r in rules)
        shuffled = dict(reversed(list(self._DEFS.items())))
        assert ka.derive_kind_rules(shuffled, _COLUMNS) == ka.derive_kind_rules(
            self._DEFS, _COLUMNS
        )

    def test_no_kinds_no_rules(self) -> None:
        assert ka.derive_kind_rules({"zzab01": {"kind": "로그"}}, _COLUMNS) == []


# ──────────────────────────────────────────────
# 근거 묶음
# ──────────────────────────────────────────────

_SECRET = "Q7Z-SECRET"


def _catalog_doc() -> dict[str, Any]:
    profile = {
        "candidate": "code",
        "code": True,
        "distinct": 3,
        "truncated": False,
        "total": 10,
        "formats": {"date8": 0.0, "ipv4": 0.97},
        "mixed_case": False,
        "flag": [_SECRET],
        "entity_key": None,
        "error": None,
    }
    return {
        "tables": {
            "zzab01": {
                "meaning": "서버 원장",
                "meaning_source": "db_comment",
                "rows_estimate": 5,
                "key": [],
                "relations": [
                    {
                        "kind": "p1",
                        "from": "zzab01",
                        "to": "zzab02",
                        "columns": [["그룹코드", "그룹코드"]],
                        "overlap": 0.95,
                        "accepted": True,
                    },
                    {
                        "kind": "declared",
                        "from": "zzab01",
                        "to": "zzab03",
                        "columns": [["그룹코드", "그룹코드"]],
                    },
                ],
                "columns": [
                    {
                        "name": "상태구분",
                        "type": "char",
                        "nullable": True,
                        "value_kind": "code_text",
                        "log_policy": "general",
                        "meaning": f"상태({_SECRET}:운영, B:중지)",
                        "meaning_source": "db_comment",
                        "profile": profile,
                    },
                    {
                        "name": "서버호스트명",
                        "type": "varchar",
                        "nullable": False,
                        "meaning": None,
                        "meaning_source": "none",
                    },
                ],
            },
            "yyq01": {"columns": [{"name": "c1", "type": "int"}]},
        }
    }


def _records() -> list[dict[str, Any]]:
    return [
        {
            "id": "ITAM-2",
            "turn": 1,
            "repeat": 0,
            "status": "completed",
            "prompt": "질문 둘",
            "taxonomy": [],
            "result": {
                "status": "ok",
                "total_rows": 3,
                "columns": [{"name": "n", "top_values": [_SECRET]}],
            },
            "executed_sqls": [
                {
                    "sql": "SELECT 1 FROM zzab01 LIMIT 1",
                    "source": "itam",
                    "success": True,
                    "row_count": 1,
                    "error": None,
                }
            ],
            "sql_analysis": {"sql_count": 1},
            "oracle": {"verdict": "fail", "mode": "as_is", "detail": {"missing": [_SECRET]}},
        },
        {
            "id": "ITAM-1",
            "turn": 1,
            "repeat": 0,
            "status": "clarification",
            "prompt": "질문 하나",
            "taxonomy": ["routing_miss"],
            "result": {"status": "unavailable"},
            "executed_sqls": [],
            "schema_context": {"tasks": 1, "presented_tables": ["zzab02", "zzab01"]},
        },
    ]


def _evidence(**overrides: Any) -> dict[str, dict[str, Any]]:
    kwargs: dict[str, Any] = dict(
        run_id="20261101-000000",
        catalog=_catalog_doc(),
        records=_records(),
        definitions={"zzab01": {"kind": "현행", "manages": "서버 원장", "origin": "import"}},
        allowed=["zzab01"],
        groups={"서버": "서버 원장"},
        scenarios={
            "scenarios": [
                {
                    "id": "ITAM-1",
                    "turns": [
                        {
                            "send": {"query": "질문 하나"},
                            "expect": {
                                "oracle": {
                                    "id": "O-1",
                                    "compare": "count",
                                    "anchors": {"x": _SECRET},
                                }
                            },
                        }
                    ],
                }
            ]
        },
        oracle_sqls={"O-1": "SELECT COUNT(*) FROM zzab01 LIMIT 1"},
        knowledge={
            ka.GUIDE_FILE: {
                "items": [
                    _item(id="g1", status=ka.STATUS_WITHDRAWN, reason="내부망 실패"),
                    _item(id="g2"),
                ]
            }
        },
        validation={
            "results": [
                {
                    "file": ka.EXAMPLES_FILE,
                    "id": "e1",
                    "ok": False,
                    "issues": [{"code": "db_failed", "message": "m"}],
                }
            ]
        },
        fix_hints={"routing_miss": "소스 선별"},
    )
    kwargs.update(overrides)
    return ke.build_evidence(**kwargs)


class TestEvidence:
    def test_no_values(self) -> None:
        assert _SECRET not in repr(_evidence())

    def test_files_and_content(self) -> None:
        files = _evidence()
        assert list(files) == sorted(files)
        assert set(files) == {
            "index.yaml",
            "p1.yaml",
            "previous_cycle.yaml",
            "scenarios.yaml",
            "turns.yaml",
            "tables.zzab.yaml",
            "tables.yyq.yaml",
        }
        table = files["tables.zzab.yaml"]["tables"]["zzab01"]
        assert table["allowed"] is True and table["kind"] == "현행"
        assert table["relations"] == [
            {"kind": "declared", "to": "zzab03", "columns": [["그룹코드", "그룹코드"]]}
        ]
        status = table["columns"][0]
        assert status["meaning"] == "상태" and status["comment_enum"] == 2
        assert "meaning" not in table["columns"][1]
        assert files["tables.yyq.yaml"]["tables"]["yyq01"]["allowed"] is False
        p1 = files["p1.yaml"]
        assert p1["available"] and p1["accepted_relations"] == 1
        assert p1["columns"]["zzab01.상태구분"]["has_flag"] is True
        assert p1["columns"]["zzab01.상태구분"]["formats"] == {"ipv4": 0.97}
        turns = files["turns.yaml"]
        assert [t["id"] for t in turns["turns"]] == ["ITAM-1", "ITAM-2"]
        assert turns["turns"][0]["failure_reasons"] == [
            "taxonomy:routing_miss",
            "status:clarification",
            "result:unavailable",
            "no_itam_sql",
        ]
        assert turns["turns"][1]["failure_reasons"] == ["oracle:fail"]
        assert turns["turns"][0]["schema_context"]["presented_tables"] == ["zzab01", "zzab02"]
        assert turns["turns"][1]["result"] == {"status": "ok", "total_rows": 3}
        assert turns["fix_hints"] == {"routing_miss": "소스 선별"}
        scenario_turn = files["scenarios.yaml"]["scenarios"][0]["turns"][0]
        assert scenario_turn["oracle"] == {"id": "O-1", "compare": "count"}
        assert scenario_turn["oracle_sql"].startswith("SELECT COUNT")
        previous = files["previous_cycle.yaml"]
        assert previous["withdrawn"] == [
            {
                "file": ka.GUIDE_FILE,
                "id": "g1",
                "reason": "내부망 실패",
                "evidence": "20261101-000000",
            }
        ]
        assert previous["validation_failed"][0]["issues"][0]["code"] == "db_failed"

    def test_deterministic(self) -> None:
        first = _evidence()
        shuffled = _catalog_doc()
        shuffled["tables"] = dict(reversed(list(shuffled["tables"].items())))
        assert _evidence(catalog=shuffled, records=list(reversed(_records()))) == first
        assert _evidence() == copy.deepcopy(first)

    def test_p1_absent(self) -> None:
        catalog = {"tables": {"zzab01": {"columns": [{"name": "a", "type": "int"}]}}}
        p1 = _evidence(catalog=catalog)["p1.yaml"]
        assert p1["available"] is False and p1["reason"]

    def test_comment_enum_label_only_table_and_column(self) -> None:
        """테이블·컬럼 주석 모두 코드 열거는 첫 마디·쌍 수만 — 첫 쌍의 코드도 라벨에 남지 않는다."""
        catalog = {"tables": {"zzab01": {
            "meaning": "서버 원장 구분 7:운영, 8:개발", "meaning_source": "db_comment",
            "columns": [{
                "name": "상태구분", "type": "char(1)",
                "meaning": "활성 여부 Y:사용, N:미사용", "meaning_source": "db_comment",
            }],
        }}}
        entry = ke.table_documents(catalog, {}, ["zzab01"])["zzab"]["tables"]["zzab01"]
        assert entry["comment"] == "서버 원장 구분" and entry["comment_enum"] == 2
        column = entry["columns"][0]
        assert column["meaning"] == "활성 여부" and column["comment_enum"] == 2
        assert not any(v in repr(entry) for v in ("7", "8", "운영", "Y", "미사용"))
