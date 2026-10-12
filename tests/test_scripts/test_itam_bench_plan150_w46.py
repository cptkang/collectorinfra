"""plans/150 W4·W6 — 선별 보강 정의 세 구절(G-2 (b)) · closed 정책 밖 10칸 등급(G-4).

W4: `tcdmsif41`·`tcdmsif43` `manages`에 「서버 단위 취득·계약 질문은 tcdmsif80」, `tcdmsif52`에
「업무에 할당된 스토리지」 질문 유형 — 정의 검증을 통과하고 선별 후보 줄에 실린다(값 0 · D-308).
W6: 5회차 「분류 필요 컬럼」 10칸 — general 7 · identifier 2 · free_text 1(구분자 `>`는 가짜
값에서 구조로 남는다 · `용도내용`은 보안 감사 L2 권고로 free_text — 2026-10-12).
값은 합성이다(`results/` 원본은 쓰지 않는다). 실 LLM·DB 0.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from scripts.itam_bench import CLOSED_POLICY_PATH, REPO_ROOT
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd
from scripts.itam_bench.substitute import FakeValues
from src.domain.table_definitions import parse_import_document, validate_table_definitions
from src.nodes.table_selection import _candidate_line

_CLOSED = REPO_ROOT / "testdata" / "itam_bench" / "closed"
_SERVER_80 = "서버 단위 취득·계약 질문은 tcdmsif80"
_STORAGE_TASK = "업무에 할당된 스토리지"

#: plans/150 W6 — 정책 밖 10칸(G-4 권고안 확정)
_W6_GRADES: dict[str, dict[str, str]] = {
    "tcdmsif72": {"활성화여부": "general", "용도내용": "free_text", "그룹경로내용": "identifier"},
    "tcdmsif92": {
        "실행상태내용": "general",
        "CPU용량": "general",
        "메모리용량": "general",
        "잠정스토리지용량": "general",
        "가상화서버명": "identifier",
    },
    "tcdmsbr01": {"부점명": "general"},
    "tcdmsgt82": {"어플리케이션명": "general"},
}
#: plans/151 W6 — 92 사용량·사용률 6칸(수치 · 사람 정보 아님)이 위에 더해졌다
_W151_GRADES_92: dict[str, str] = {
    c: "general"
    for c in (
        "CPU사용량", "CPU사용률", "가상화서버메모리사용량", "메모리사용률", "스토리지사용량",
        "스토리지사용률",
    )
}


def _schema_tables() -> dict[str, Any]:
    path = _CLOSED / "itam_schema.json"
    return json.loads(path.read_text(encoding="utf-8"))["schema"]["tables"]


@pytest.fixture(scope="module")
def definitions() -> dict[str, dict[str, Any]]:
    seed = yaml.safe_load((_CLOSED / "table_definitions.yaml").read_text(encoding="utf-8"))
    columns = {t: [c["name"] for c in v["columns"]] for t, v in _schema_tables().items()}
    valid, errors = validate_table_definitions(parse_import_document(seed), columns)
    assert errors == {}
    return valid


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(CLOSED_POLICY_PATH)


# --- W4 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "table, phrase",
    [("tcdmsif41", _SERVER_80), ("tcdmsif43", _SERVER_80), ("tcdmsif52", _STORAGE_TASK)],
)
def test_definition_phrase_reaches_candidate_line(
    definitions: dict[str, dict[str, Any]], table: str, phrase: str
) -> None:
    entry = definitions[table]
    assert phrase in entry["manages"]
    assert phrase in _candidate_line(table, entry, [])


def test_other_definitions_do_not_point_to_80(definitions: dict[str, dict[str, Any]]) -> None:
    """구절은 세 테이블에만 더했다 — 다른 정의의 `manages`에는 없다."""
    hits = {t for t, e in definitions.items() if _SERVER_80 in e["manages"]}
    assert hits == {"tcdmsif41", "tcdmsif43"}
    assert {t for t, e in definitions.items() if _STORAGE_TASK in e["manages"]} == {"tcdmsif52"}


# --- W6 ---------------------------------------------------------------------


def test_w6_grades_exact_and_exist(policy: cat.ColumnPolicy) -> None:
    expected = {**_W6_GRADES, "tcdmsif92": {**_W6_GRADES["tcdmsif92"], **_W151_GRADES_92}}
    assert {t: dict(policy.tables[t]) for t in _W6_GRADES} == expected
    schema = _schema_tables()
    for table, columns in expected.items():
        assert set(columns) <= {c["name"] for c in schema[table]["columns"]}, table
    # 전사본 두 테이블 77칸 + 10칸 + plans/151 W6 6칸 — 다른 테이블은 없다
    assert set(policy.tables) == {"tcdmsif80", "tcdmsif79", *_W6_GRADES}
    assert sum(len(c) for c in policy.tables.values()) == 93


def _summary(policy: cat.ColumnPolicy, *, generator: bool) -> dict[str, Any]:
    sql = (
        "SELECT s.`활성화여부`, s.`그룹경로내용`, s.`용도내용` FROM `tcdmsif72` AS s "
        "WHERE s.`그룹경로내용` LIKE '%통합인증%'"
    )
    names = ["활성화여부", "그룹경로내용", "용도내용"]
    rows = [
        {"활성화여부": "Y", "그룹경로내용": "정보본부>인증팀>통합인증", "용도내용": "웹"},
        {"활성화여부": "N", "그룹경로내용": "정보본부>인증팀>통합인증 DB", "용도내용": "디비"},
    ]
    sources = rd.resolve_result_columns(names, [sql], policy.column_names())
    vault = rd.PiiVault.from_policy(policy, fakes=FakeValues() if generator else None)
    return rd.summarize_result(
        {"status": "ok", "total_rows": 2, "columns": names, "rows": rows},
        sources=sources,
        policy=policy,
        vault=vault,
    )


@pytest.mark.parametrize("generator", [True, False])
def test_general_kept_identifier_substituted(policy: cat.ColumnPolicy, generator: bool) -> None:
    flag, path, usage = _summary(policy, generator=generator)["columns"]
    assert flag["log_policy"] == "general" and flag["sample"] == ["Y", "N"]
    # 용도내용 — free_text: 원값은 남지 않는다(생성기면 가짜 값 · 아니면 표본 없음)
    assert usage["log_policy"] == "free_text"
    assert "웹" not in json.dumps(usage, ensure_ascii=False)
    assert "디비" not in json.dumps(usage, ensure_ascii=False)
    assert path["log_policy"] == "identifier" and path["length"] == [13, 16]
    text = json.dumps(path, ensure_ascii=False)
    assert "인증팀" not in text and "정보본부" not in text
    if generator:
        assert path["substituted"] is True
        # 구분자 `>`·공백은 구조로 남고 글자만 바뀐다
        assert all(s.count(">") == 2 for s in path["sample"])
    else:
        assert "sample" not in path and "substituted" not in path
