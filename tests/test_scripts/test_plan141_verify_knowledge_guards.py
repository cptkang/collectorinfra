"""plans/141 W1·W2·W5 독립 검증 — 지식 자산 검증기·근거 묶음·검증 모드 가드의 경계.

구현 테스트와 겹치지 않는 것만 본다. 결함은 `xfail(strict=True)`로 남긴다(고쳐지면 XPASS로
드러난다).

- 사용률 규칙(D-308 G-6): K1 문장 가드가 같은 문장의 무관한 부정어로 풀리는지 · K3 설명·유사어에는
  가드가 없는지
- 근거 묶음: 테이블 주석의 코드 열거가 그대로 실리는지(컬럼 주석은 첫 마디만)
- `--verify-assets` 가드: MariaDB 실행 주석(`/*! … */`)

LLM·DB 0.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import asset_verify as av
from src.domain import knowledge_assets as ka
from src.domain import knowledge_evidence as ke

_COLUMNS = {
    "tcdmsif72": ["서버호스트명", "서버CPU사용률", "활성화여부"],
    "tcdmsif80": ["담당부점명"],
}
_CATALOG = ka.make_catalog(_COLUMNS, list(_COLUMNS))


# ──────────────────────────────────────────────
# 사용률 규칙 (D-308 G-6)
# ──────────────────────────────────────────────


def test_utilization_rule_rejects_plain_instruction() -> None:
    text = "CPU 사용률 질문은 `tcdmsif72`의 `서버CPU사용률` 내림차순으로 답한다."
    assert [i["code"] for i in ka.guide_item_issues(text, _CATALOG)] == [ka.UTILIZATION_RULE]


def test_utilization_rule_not_bypassed_by_unrelated_negation() -> None:
    text = "CPU 사용률 질문은 `tcdmsif72`의 `서버CPU사용률`로 답하고 NULL 행은 세지 않는다."
    assert ka.UTILIZATION_RULE in [i["code"] for i in ka.guide_item_issues(text, _CATALOG)]


def test_synonym_for_utilization_is_rejected() -> None:
    item = {"table": "tcdmsif72", "column": "서버CPU사용률", "words": ["CPU 사용률", "서버 사용률"]}
    assert ka.synonym_item_issues(item, _CATALOG)


def test_description_answering_utilization_is_rejected() -> None:
    item = {
        "table": "tcdmsif72", "column": "서버CPU사용률",
        "text": "서버 CPU 사용률. 사용률 질문은 이 칸으로 답한다.",
    }
    assert ka.description_item_issues(item, _CATALOG)


def test_synonym_conflict_and_ambiguity_guards() -> None:
    """D-142 쓰기 가드 — 다른 컬럼 이름과 같은 낱말 · 다의어는 거절."""
    conflict = {"table": "tcdmsif80", "column": "담당부점명", "words": ["서버호스트명"]}
    assert [i["code"] for i in ka.synonym_item_issues(conflict, _CATALOG)] == [ka.NAME_CONFLICT]
    items = [
        {"table": "tcdmsif72", "column": "서버호스트명", "words": ["호스트"]},
        {"table": "tcdmsif80", "column": "담당부점명", "words": ["호스트"]},
    ]
    assert set(ka.ambiguous_synonyms(items)) == {0, 1}


def test_hallucinated_identifier_in_guide_rejected() -> None:
    text = "서버 원장은 `tcdmsif99`의 `없는칸`이다."
    codes = [i["code"] for i in ka.guide_item_issues(text, _CATALOG)]
    assert ka.UNKNOWN_IDENTIFIER in codes


# ──────────────────────────────────────────────
# 근거 묶음 — 주석의 코드 열거
# ──────────────────────────────────────────────


def _catalog_with_comments(table_comment: str, column_comment: str) -> dict[str, Any]:
    return {"tables": {"tcdmsif72": {
        "meaning": table_comment, "meaning_source": "db_comment",
        "columns": [{
            "name": "활성화여부", "type": "char(1)",
            "meaning": column_comment, "meaning_source": "db_comment",
        }],
    }}}


def test_column_comment_enum_reduced_to_label_and_count() -> None:
    docs = ke.table_documents(
        _catalog_with_comments("서버 원장", "활성 여부 Y:사용, N:미사용"), {}, ["tcdmsif72"],
    )
    column = docs["tcdmsif"]["tables"]["tcdmsif72"]["columns"][0]
    assert column.get("comment_enum") == 2
    assert "Y:사용" not in str(column)


def test_table_comment_enum_not_copied() -> None:
    docs = ke.table_documents(
        _catalog_with_comments("서버 원장 구분 1:운영, 2:개발", "활성 여부"), {}, ["tcdmsif72"],
    )
    entry = docs["tcdmsif"]["tables"]["tcdmsif72"]
    assert "1:운영" not in str(entry)


# ──────────────────────────────────────────────
# --verify-assets 가드
# ──────────────────────────────────────────────


class _Db:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_sql(self, sql: str) -> Any:
        self.calls.append(sql)
        return SimpleNamespace(rows=[], truncated=False)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t INTO OUTFILE '/tmp/x'",
        "SELECT a FROM t FOR UPDATE",
        "WITH x AS (SELECT 1) DELETE FROM t",
        "SELECT GET_LOCK('a', 10)",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT a /*!, SLEEP(5) */ FROM t",
    ],
)
def test_verify_rejects_mariadb_side_effects_before_execution(sql: str) -> None:
    db = _Db()
    result = asyncio.run(av.averify([av.VerifyItem("e1", av.KIND_EXAMPLE, sql)], db.execute_sql))
    assert result[0]["error"] == av.ERR_GUARD and db.calls == []


def test_verify_rejects_executable_comment_into_outfile() -> None:
    db = _Db()
    sql = "SELECT a FROM t /*!50000 INTO OUTFILE '/tmp/x' */"
    result = asyncio.run(av.averify([av.VerifyItem("e1", av.KIND_EXAMPLE, sql)], db.execute_sql))
    assert result[0]["error"] == av.ERR_GUARD and db.calls == []
