"""plans/149 W4 — 벤치 반출·판정 교정(4회차 결과 판독 결함 F7·F8·F10·F11·F12).

(1) LIKE 와일드카드만인 리터럴은 구조로 남긴다 · (2) 프롬프트 낱말 허용 = 같은 시나리오의 현재
턴까지 프롬프트 · (3) SQL 분석기가 한글·백틱 식별자를 읽는다 · (4) `COUNT(…)` 결과 열은 general ·
(5) `compare: value` 상세 스칼라 등급 = 시스템 결과 값 열 등급(명세 등급을 모를 때) · (6) keyset
「행 수 ≠ 키 수」 표지. 항목마다 누출 관문(`LeakGate`) 통과를 함께 본다.

「전에는 가렸는데 이제 남는 입력」(docs/18 2026-10-07 — 기준선 `38adcc6` 대비)은
`test_relaxed_inputs_*`·`test_still_masked_*`가 고정한다. 입력은 4회차 SQL 의 **모양**을 값 없이
재구성했다(`results/` 원본은 쓰지 않는다 · D-308). 정책은 이 파일 안에서 만든다(폐쇄망 정책
파일은 W5 가 고친다). 실 LLM·DB 0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp

M = rd.MASK
_PERSON = "김민수"  # 합성 성명
_LOGIN = "5488923"

_POLICY = cat.ColumnPolicy(
    db_id="itam",
    scope="test",
    tables={
        "tcdmsif72": {
            "그룹경로내용": "general",
            "서버호스트명": "pii",
            "담당자명": "pii",
            "비고": "free_text",
            "취득금액": "amount",
            "IP주소내용": "network",
        },
        "tcdmsgt82": {"어플리케이션명": "general"},
    },
)
_CATALOG_COLUMNS = ("그룹경로내용", "서버호스트명", "담당자명", "비고", "취득금액", "미분류칼럼")
_CATALOG_DOC: dict[str, Any] = {
    "tables": {
        "tcdmsif72": {
            "columns": [{"name": n} for n in _CATALOG_COLUMNS],
            "relations": [],
            "key": [],
        },
        "tcdmsgt82": {"columns": [{"name": "어플리케이션명"}], "relations": [], "key": []},
    },
    "same_key_groups": [],
}

#: 101·104·106 모양 — 서비스 앱 이름으로 서버 그룹 경로를 부분 일치로 잇는다
_SERVICE_SQL = (
    "SELECT a.`어플리케이션명` FROM `tcdmsgt82` a JOIN `tcdmsif72` s "
    "ON s.`그룹경로내용` LIKE CONCAT('%', a.`어플리케이션명`, '%') "
    "WHERE a.`어플리케이션명` LIKE '%통합인증%'"
)


def _vault() -> rd.PiiVault:
    return rd.PiiVault.from_policy(_POLICY)


def _generating() -> tuple[rd.PiiVault, Any, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=_POLICY, catalog_doc=_CATALOG_DOC, user_values={"login_id": _LOGIN}, p1_draft=None
    )


def _gate(vault: rd.PiiVault) -> rd.LeakGate:
    return rd.LeakGate(policy=_POLICY, vault=vault, user_values={"login_id": _LOGIN})


def _trace(*records: Any) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def _redact(sql: str, vault: rd.PiiVault, prompt: str = "") -> str:
    return rd.redact_sql(
        sql, policy=_POLICY, vault=vault, prompt=prompt, catalog_columns=_CATALOG_COLUMNS
    )


# --- (1) LIKE 와일드카드만인 리터럴 (F7) -------------------------------------------------


def test_service_shape_keeps_wildcards_and_masks_value() -> None:
    """101 모양 — `CONCAT('%', …, '%')`는 그대로, 값이 섞인 `'%통합인증%'`은 종전대로 가림."""
    out = _redact(_SERVICE_SQL, _vault(), prompt="서버 목록 보여줘")
    assert out == _SERVICE_SQL.replace("'%통합인증%'", f"'{M}'")


def test_mixed_wildcard_literal_kept_only_with_prompt_word() -> None:
    """값이 섞인 리터럴 판정은 종전대로 — 프롬프트에 있는 말이면 남는다."""
    out = _redact(_SERVICE_SQL, _vault(), prompt="통합인증 서비스 서버 목록")
    assert out == _SERVICE_SQL


def test_wildcard_with_trailing_newline_still_masked() -> None:
    """교정 1 — `$`가 끝 개행을 허용하던 구멍(`'%\n'`)은 기준선처럼 가린다."""
    assert not rd._trivially_safe("%\n", "")
    sql = "SELECT 1 FROM `tcdmsif72` s WHERE s.`담당자명` LIKE '%\n'"
    assert _redact(sql, _vault()) == sql.replace("'%\n'", f"'{M}'")


def test_trivially_safe_wildcards() -> None:
    for content in ("%", "_", "%%", "%_%", "__", "_%"):
        assert rd._trivially_safe(content, "")
    for content in ("%a%", "% ", "\\_", "%_1", "%통합인증%", ""):
        assert not rd._trivially_safe(content, ""), content


#: 「전에는 가렸는데 이제 남는 입력」 — 기준선 `38adcc6`에서 이 리터럴은 전부 `'<가림>'`이었다.
#: 와일드카드만인 리터럴(7종) × 술어 컬럼 등급(6종) × 구문 위치(LIKE · CONCAT 인자 · `=`)만
#: 바뀌었고, 다른 리터럴 처분은 그대로다(기준선 worktree 대조 · 126건).
_WILDCARDS = ("%", "_", "%%", "%_%", "__", "%%%", "_%")
_GRADED = (
    "s.`그룹경로내용`",  # general
    "s.`담당자명`",  # pii
    "s.`비고`",  # free_text
    "s.`미분류칼럼`",  # unclassified(카탈로그 · 정책 밖)
    "s.`취득금액`",  # amount
    "s.`IP주소내용`",  # network
)
_FORMS = ("LIKE '{w}'", "LIKE CONCAT('{w}', a.`어플리케이션명`, '{w}')", "= '{w}'")


@pytest.mark.parametrize("wildcard", _WILDCARDS)
@pytest.mark.parametrize("column", _GRADED)
@pytest.mark.parametrize("form", _FORMS)
def test_relaxed_inputs_wildcard_only(wildcard: str, column: str, form: str) -> None:
    sql = f"SELECT 1 FROM `tcdmsif72` s, `tcdmsgt82` a WHERE {column} " + form.format(w=wildcard)
    assert _redact(sql, _vault(), prompt="서버 목록") == sql


@pytest.mark.parametrize("literal", ["%a%", "% ", "%_1", "a1", "%통합인증%", f"%{_PERSON}%"])
@pytest.mark.parametrize("column", _GRADED[1:4])
def test_still_masked_value_literals(literal: str, column: str) -> None:
    """값이 한 글자라도 섞이면 사람·서술형·미분류 술어에서는 종전대로 가린다."""
    sql = f"SELECT 1 FROM `tcdmsif72` s WHERE {column} LIKE '{literal}'"
    assert _redact(sql, _vault(), prompt="서버 목록") == sql.replace(f"'{literal}'", f"'{M}'")


def test_wildcards_pass_gate_with_generator() -> None:
    """생성기 경로 — `'%'`는 가짜 값으로 바뀌지 않고, 가린 값은 가짜 값 · 관문 통과."""
    vault, fakes, _policy = _generating()
    fakes.register(rd.sql_literal_contents(_SERVICE_SQL))
    out = _redact(_SERVICE_SQL, vault, prompt="서버 목록")
    assert "CONCAT('%', a.`어플리케이션명`, '%')" in out
    assert "통합인증" not in out
    assert _gate(vault).check({"trace.jsonl": _trace({"executed_sqls": [{"sql": out}]})}) == []


def test_wildcard_concat_alias_no_longer_unknown() -> None:
    """골격도 와일드카드를 무해한 리터럴로 본다 — `CONCAT('%', 일반, '%')` 열은 그 컬럼 등급."""
    sql = "SELECT CONCAT('%', s.`그룹경로내용`, '%') AS p FROM `tcdmsif72` s"
    sources = rd.resolve_result_columns(["p"], [sql], _POLICY.column_names())
    assert sources == {"p": ["그룹경로내용"]}


# --- (2) 프롬프트 범위 = 같은 시나리오의 현재 턴까지 (F8) ----------------------------------------


def _turn(index: int, query: str) -> cat.Turn:
    return cat.Turn(index=index, send={"query": query}, expect={"db_ids": ["itam"]})


def _scenario(sid: str, *queries: str) -> cat.Scenario:
    return cat.Scenario(
        id=sid,
        title="t",
        category="service",
        env=("closed",),
        traps=(),
        key_columns=(),
        gold_tables=(),
        turns=tuple(_turn(i, q) for i, q in enumerate(queries, start=1)),
    )


_T1 = "자산관리에서 통합인증 서비스 서버 목록 보여줘"
_T2 = "그 서버들 지원 종료일도 알려줘"
#: 106 t2 모양 — 앞 턴 서버를 승계해 지원 종료일을 붙인다(검색어는 1턴 프롬프트의 말)
_SUCCESSION_SQL = (
    "SELECT s.`서버호스트명`, s.`비고` FROM `tcdmsif72` s JOIN `tcdmsgt82` a "
    "ON s.`그룹경로내용` LIKE CONCAT('%', a.`어플리케이션명`, '%') "
    "WHERE a.`어플리케이션명` LIKE '%통합인증%'"
)


def test_prompt_scope_covers_earlier_turns_only() -> None:
    scenario = _scenario("ITAM-106", _T1, _T2, "셋째 턴")
    assert cli.prompt_scope(scenario, scenario.turns[0]) == _T1
    assert cli.prompt_scope(scenario, scenario.turns[1]) == f"{_T1}\n{_T2}"
    assert "셋째" not in cli.prompt_scope(scenario, scenario.turns[1])


class _Client:
    """턴마다 같은 결과(서버호스트명·비고 1행)를 돌려주는 가짜 서버."""

    def send(self, endpoint: str, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            status="completed",
            query_id="q",
            response="답변",
            db_ids=["itam"],
            disclosures=[],
            clarification=None,
            http_status=200,
            retries=0,
            wall_ms=1.0,
            error=f"Unknown column '%통합인증%' in 'where clause' · '{_PERSON}'",
        )

    def download_csv(self, query_id: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "columns": ["서버호스트명", "비고"],
            "rows": [{"서버호스트명": "realhost01", "비고": "메모"}],
            "total_rows": 1,
            "truncated": False,
            "reason": None,
        }


class _Tail:
    def __init__(self, sql: str = "") -> None:
        self.sql = sql

    def mark(self) -> int:
        return 0

    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
        if not self.sql:
            return []
        return [
            {"sql": self.sql, "source": "itam", "success": True, "row_count": 1,
             "retry_attempt": 0, "error": None}
        ]  # fmt: skip


def _run(scenarios: list[cat.Scenario], vault: rd.PiiVault) -> list[dict[str, Any]]:
    ctx = cli.RunContext(
        run_id="r",
        tier=None,
        policy=_POLICY,
        catalog=jd.CatalogFacts.from_catalog(_CATALOG_DOC),
        vault=vault,
        oracle=lambda *a: {},
    )
    return cli.run_scenarios(
        scenarios,
        client=_Client(),
        audit=_Tail(_SUCCESSION_SQL),
        capture=_Tail(),
        ctx=ctx,
        progress=lambda _t: None,
    )


@pytest.mark.parametrize("generating", [False, True])
def test_second_turn_keeps_first_turn_word(generating: bool) -> None:
    """106 t2 모양 — 2턴 SQL `'%통합인증%'`은 1턴 프롬프트의 말이라 원문 · 다른 시나리오는 치환."""
    vault = _generating()[0] if generating else _vault()
    other = _scenario("ITAM-104", "자산관리 시스템에서 서비스별 서버 수 알려줘")
    records = _run([_scenario("ITAM-106", _T1, _T2), other], vault)
    by_key = {(r["id"], r["turn"]): r for r in records}
    t2_sql = by_key[("ITAM-106", 2)]["executed_sqls"][0]["sql"]
    assert "LIKE '%통합인증%'" in t2_sql
    assert "CONCAT('%', a.`어플리케이션명`, '%')" in t2_sql
    # 오류 문구도 같은 범위 — 프롬프트 말은 남고 사람 값은 남지 않는다
    assert "%통합인증%" in by_key[("ITAM-106", 2)]["error"]
    other_record = by_key[("ITAM-104", 1)]
    assert "통합인증" not in other_record["executed_sqls"][0]["sql"]
    assert "통합인증" not in other_record["error"]
    text = json.dumps(records, ensure_ascii=False)
    assert _PERSON not in text and "realhost01" not in text
    assert _gate(vault).check({"trace.jsonl": _trace(*records)}) == []


# --- (3) 한글·백틱 식별자 분석 (F11) -----------------------------------------------------


_COLUMNS = frozenset(
    {"서버호스트명", "그룹경로내용", "어플리케이션명", "유지보수종료일자", "my col"}
)


def test_sql_columns_reads_korean_and_backticks() -> None:
    assert jd.sql_columns(_SERVICE_SQL, _COLUMNS) == ["그룹경로내용", "어플리케이션명"]
    plain = "SELECT s.서버호스트명, `my col` FROM tcdmsif72 s"
    assert jd.sql_columns(plain, _COLUMNS) == ["my col", "서버호스트명"]
    # 리터럴 안 낱말은 컬럼이 아니다
    assert jd.sql_columns("SELECT 1 FROM t WHERE x = '서버호스트명'", _COLUMNS) == []


@pytest.mark.parametrize(
    "where, expected",
    [
        ("s.`유지보수종료일자` < CURDATE()", True),
        ("s.유지보수종료일자 BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 3 MONTH)", True),
        ("s.`유지보수종료일자` < DATE_FORMAT(CURDATE(), '%Y%m%d')", False),
        ("s.`서버호스트명` < CURDATE()", False),
    ],
)
def test_date_text_misuse_korean(where: str, expected: bool) -> None:
    sql = f"SELECT 1 FROM `tcdmsif80` s WHERE {where}"
    assert jd.date_text_misuse(sql, {"유지보수종료일자"}) is expected


_GROUPS = ((frozenset({"tcdmsif72", "tcdmsif75"}), ("그룹회사코드", "서버호스트명")),)


@pytest.mark.parametrize(
    "on, partial",
    [
        ("s.`그룹회사코드` = w.`그룹회사코드` AND s.`서버호스트명` = w.`서버호스트명`", False),
        ("`s`.`그룹회사코드` = `w`.`그룹회사코드` AND s.서버호스트명 = w.서버호스트명", False),
        ("s.`서버호스트명` = w.`서버호스트명`", True),
        ("s.`그룹회사코드` = w.`다른코드` AND s.`서버호스트명` = w.`서버호스트명`", True),
    ],
)
def test_join_key_partial_backticks(on: str, partial: bool) -> None:
    sql = f"SELECT 1 FROM `tcdmsif72` s JOIN `tcdmsif75` w ON {on}"
    assert jd.join_key_partial(sql, _GROUPS) is partial


def test_analysis_fills_columns_and_passes_gate() -> None:
    """4회차 모양 SQL 로 `columns`가 채워진다 · 분석 결과(이름·불린)는 관문 통과."""
    catalog = jd.CatalogFacts.from_catalog(_CATALOG_DOC | {"same_key_groups": []})
    facts = jd.TurnFacts(
        status="completed", executed=[{"sql": _SERVICE_SQL, "source": "itam", "success": True}]
    )
    analysis = jd.analyze_sql(facts, catalog, db_id="itam")
    assert analysis["columns"] == ["그룹경로내용", "어플리케이션명"]
    assert _gate(_vault()).check({"trace.jsonl": _trace({"sql_analysis": analysis})}) == []


# --- (4) COUNT 결과 열 = general (F10) ---------------------------------------------------


@pytest.mark.parametrize(
    "expression, sources",
    [
        ("COUNT(DISTINCT s.`서버호스트명`)", [rd.COMPUTED]),
        ("COUNT(*)", [rd.COMPUTED]),
        (f"COUNT(CASE WHEN s.`담당자명` = '{_PERSON}' THEN 1 END)", [rd.COMPUTED]),
        ("COUNT(s.`담당자명`) + SUM(s.`취득금액`)", ["취득금액"]),
        ("SUM(s.`취득금액`)", ["취득금액"]),
        ("MAX(s.`서버호스트명`)", ["서버호스트명"]),
        ("COUNT(*) OVER (PARTITION BY s.`담당자명`)", ["담당자명"]),
        ("ACCOUNT(s.`담당자명`)", ["담당자명"]),
        # COUNT 위장(교정 1 · 감사 권고) — 문자열·주석 속 COUNT · GROUP_CONCAT · 백틱 함수 호출
        ("CONCAT('COUNT(', s.`담당자명`, ')')", ["담당자명", rd.UNKNOWN]),
        ("/* COUNT( */ s.`담당자명`", ["담당자명"]),
        ("-- COUNT(\n s.`담당자명`", ["담당자명"]),
        ("GROUP_CONCAT(s.`담당자명`)", ["담당자명"]),
        ("`COUNT`(s.`담당자명`)", [f"{rd.UNKNOWN}:COUNT", "담당자명"]),
    ],
)
def test_count_expression_sources(expression: str, sources: list[str]) -> None:
    sql = f"SELECT {expression} AS c FROM `tcdmsif72` s"
    assert rd.resolve_result_columns(["c"], [sql], _POLICY.column_names()) == {"c": sources}


def test_count_alias_redefined_elsewhere_stays_strict() -> None:
    """같은 별칭이 다른 SQL 에서 사람 컬럼이면 가장 엄격한 쪽(COUNT 예외 없음)."""
    sqls = [
        "SELECT COUNT(s.`담당자명`) AS c FROM `tcdmsif72` s",
        "SELECT s.`담당자명` AS c FROM `tcdmsif72` s",
    ]
    assert rd.resolve_result_columns(["c"], sqls, _POLICY.column_names()) == {
        "c": [rd.COMPUTED, "담당자명"]
    }
    assert rd.is_count_column("c", sqls) is False


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("COUNT(*)", True),
        ("(COUNT(DISTINCT s.`서버호스트명`))", True),
        ("COUNT(*) + 0", False),
        ("COUNT(s.`담당자명`) + SUM(s.`취득금액`)", False),
        ("`COUNT`(s.`담당자명`)", False),
        ("GROUP_CONCAT(s.`담당자명`)", False),
        ("DATEDIFF(NOW(), '2020-01-01')", False),
        ("1234", False),
        ("s.`그룹경로내용`", False),
    ],
)
def test_is_count_column(expression: str, expected: bool) -> None:
    assert rd.is_count_column("c", [f"SELECT {expression} AS c FROM `tcdmsif72` s"]) is expected


@pytest.mark.parametrize(
    "call",
    [
        "`count`/*c*/(s.`담당자명`)",
        "`count`-- c\n(s.`담당자명`)",
        "`count`#c\n(s.`담당자명`)",
        "COUNT/*c*/(s.`담당자명`)",  # 이름과 `(` 사이 공백·주석 = 저장 함수 해석(IGNORE_SPACE 꺼짐)
        "COUNT (s.`담당자명`)",
    ],
)
def test_count_lookalike_with_comment_is_not_count(call: str) -> None:
    """교정 2(재감사 Low-1) — 주석·공백을 사이에 둔 `count(` 호출은 내장 COUNT 로 보지 않는다."""
    sql = f"SELECT {call} AS n FROM `tcdmsif72` s"
    assert rd.is_count_column("n", [sql]) is False
    assert "담당자명" in rd.resolve_result_columns(["n"], [sql], _POLICY.column_names())["n"]


def test_union_is_not_count() -> None:
    """교정 2(재감사 Low-2) — 집합 연산이 있으면 다른 SELECT 의 열이 같은 결과 열로 들어온다."""
    sql = "SELECT COUNT(*) AS n FROM a UNION SELECT salary FROM b"
    assert rd.is_count_column("n", [sql]) is False
    assert rd.is_count_column("n", ["SELECT COUNT(*) AS n FROM a"]) is True


def test_is_count_column_needs_alias_definition() -> None:
    assert rd.is_count_column("COUNT(*)", ["SELECT COUNT(*) FROM t"]) is False
    assert rd.is_count_column("count", ["SELECT COUNT(*) AS `count` FROM t"]) is True


def test_count_column_value_kept_and_gate_passes() -> None:
    """104 모양 — `COUNT(DISTINCT 서버호스트명) AS server_count`는 원값 · MAX 열은 가짜 값."""
    vault, fakes, _policy = _generating()
    sql = (
        "SELECT a.`어플리케이션명`, COUNT(DISTINCT s.`서버호스트명`) AS server_count, "
        "MAX(s.`서버호스트명`) AS last_host FROM `tcdmsif72` s JOIN `tcdmsgt82` a "
        "ON s.`그룹경로내용` LIKE CONCAT('%', a.`어플리케이션명`, '%') GROUP BY a.`어플리케이션명`"
    )
    result = {
        "status": "ok",
        "columns": ["어플리케이션명", "server_count", "last_host"],
        "rows": [{"어플리케이션명": "앱", "server_count": "85", "last_host": "realhost01"}],
        "total_rows": 1,
        "truncated": False,
    }
    fakes.register(["앱", "85", "realhost01"])
    names = rd.result_column_names(result)
    sources = rd.resolve_result_columns(names, [sql], _POLICY.column_names())
    summary = rd.summarize_result(result, sources=sources, policy=_POLICY, vault=vault)
    entries = {c["name"]: c for c in summary["columns"]}
    assert entries["server_count"]["log_policy"] == "general"
    assert entries["server_count"]["sample"] == ["85"]
    assert entries["last_host"]["log_policy"] == "pii"
    assert "realhost01" not in json.dumps(summary, ensure_ascii=False)
    assert _gate(vault).check({"trace.jsonl": _trace({"result": summary})}) == []


# --- (5) 상세 스칼라 등급 = 시스템 결과 값 열 등급 (F10) ----------------------------------

_VALUE_SPEC = {
    "id": "ITAM-107",
    "compare": "value",
    "value": ["n", "서버수", "count", "server_count"],
    "db_ids": ["itam"],
}


def _value_record(
    vault: rd.PiiVault, column: str, select: str, spec: dict[str, Any] | None = None
) -> dict[str, Any]:
    spec = spec or _VALUE_SPEC
    sql = f"SELECT {select} AS {column} FROM `tcdmsif72` s"
    result = {
        "status": "ok",
        "columns": [column],
        "rows": [{column: "1234"}],
        "total_rows": 1,
        "truncated": False,
    }
    outcome = {"status": "ok", "reason": None, "rows_by_db": {"itam": [{"n": 1234}]}}
    verdict, detail, mode = jd.evaluate(spec, outcome, result)
    names = rd.result_column_names(result)
    sources = rd.resolve_result_columns(names, [sql], _POLICY.column_names())
    summary = rd.summarize_result(result, sources=sources, policy=_POLICY, vault=vault)
    ctx = SimpleNamespace(policy=_POLICY, vault=vault)
    return cli._oracle_record(  # type: ignore[arg-type]
        (spec, outcome, detail, mode, verdict), names, summary, [sql],
        SimpleNamespace(query="자산관리 시스템에 등록된 서버 몇 대야?"), ctx,
    )  # fmt: skip


@pytest.mark.parametrize("column", ["server_count", "총대수"])
def test_count_scalar_matches_result_value(column: str) -> None:
    """107 모양 — `COUNT(*)` 결과(general)와 상세 값이 같은 원값(`총대수`는 1×1 이름 바꿈)."""
    vault, fakes, _policy = _generating()
    fakes.register(["1234"])
    record = _value_record(vault, column, "COUNT(*)")
    assert record["verdict"] == "pass"
    assert "value.scalar" not in record["substituted_fields"]
    assert record["detail"]["oracle"] == 1234 and record["detail"]["system"] == 1234
    assert _gate(vault).check({"trace.jsonl": _trace({"oracle": record})}) == []


def test_non_general_system_column_scalar_still_faked() -> None:
    """시스템 값 열이 general 이 아니면(사람 컬럼 MAX) 종전대로 가짜 값."""
    vault, fakes, _policy = _generating()
    fakes.register(["1234"])
    record = _value_record(vault, "server_count", "MAX(s.`담당자명`)")
    assert "value.scalar" in record["substituted_fields"]
    assert record["detail"]["oracle"] != 1234
    assert _gate(vault).check({"trace.jsonl": _trace({"oracle": record})}) == []


@pytest.mark.parametrize(
    "select",
    [
        "DATEDIFF(NOW(), '2020-01-01')",  # 컬럼 없는 계산(computed) — COUNT 아님
        "1234",  # 상수 항목
        "s.`그룹경로내용`",  # 일반 컬럼 별칭
        "COUNT(*) + 0",  # COUNT 밖 항이 섞인 식
    ],
)
def test_non_count_general_system_column_scalar_faked(select: str) -> None:
    """교정 1 — 명세 등급을 모르면(None → unclassified) 시스템 열이 general 이어도 가짜 값.

    기준선 `38adcc6`도 가짜 값이었다 · W4 첫 구현은 원값이었다(감사 Medium).
    """
    vault, fakes, _policy = _generating()
    fakes.register(["1234"])
    record = _value_record(vault, "server_count", select)
    assert "value.scalar" in record["substituted_fields"]
    assert record["detail"]["oracle"] != 1234
    assert _gate(vault).check({"trace.jsonl": _trace({"oracle": record})}) == []


def test_known_spec_grade_wins_over_general_system() -> None:
    """명세 값 컬럼 등급을 알면(금액) 시스템 쪽이 general(COUNT)이어도 내리지 않는다."""
    vault, fakes, _policy = _generating()
    fakes.register(["1234"])
    spec = {**_VALUE_SPEC, "value": ["취득금액", "server_count"]}
    record = _value_record(vault, "server_count", "COUNT(*)", spec)
    assert "value.scalar" in record["substituted_fields"]
    assert _gate(vault).check({"trace.jsonl": _trace({"oracle": record})}) == []


def test_scalar_grade_rule() -> None:
    # 기본 — 두 등급 중 엄격한 쪽(None 은 unclassified)
    assert jd.scalar_grade(None, "general") == "unclassified"
    assert jd.scalar_grade("general", None) == "unclassified"
    assert jd.scalar_grade("general", "general") == "general"
    assert jd.scalar_grade("amount", "general") == "amount"
    assert jd.scalar_grade(None, None) == "unclassified"
    # 예외 — 순수 COUNT 결과: 명세 등급을 모르면 general · 알면 그 등급
    assert jd.scalar_grade(None, "general", count_result=True) == "general"
    assert jd.scalar_grade("amount", "general", count_result=True) == "amount"
    detail = {"compare": "value", "oracle": 1.0, "system": 1.0}
    fields = jd.substituted_fields
    assert fields(detail, value_grade=None, keys_allowed=True) == ["value.scalar"]
    assert fields(detail, value_grade=None, keys_allowed=True, system_grade="general") == [
        "value.scalar"
    ]
    assert (
        fields(
            detail, value_grade=None, keys_allowed=True, system_grade="general", count_result=True
        )
        == []
    )


# --- (6) keyset 「행 수 ≠ 키 수」 표지 (F12) -------------------------------------------------

_KEYSET_SPEC = {
    "id": "ITAM-110",
    "compare": "keyset",
    "key": [["서버호스트명", "server_hostname"]],
    "db_ids": ["itam"],
}


def _keyset(rows: list[str]) -> tuple[str, Any, str]:
    outcome = {
        "status": "ok",
        "reason": None,
        "rows_by_db": {"itam": [{"서버호스트명": "h1"}, {"서버호스트명": "h2"}]},
    }
    result = {
        "status": "ok",
        "columns": ["server_hostname"],
        "rows": [{"server_hostname": h} for h in rows],
        "total_rows": len(rows),
        "truncated": False,
    }
    return jd.evaluate(_KEYSET_SPEC, outcome, result)


def test_keyset_duplicate_rows_marked() -> None:
    verdict, detail, _mode = _keyset(["h1", "h1", "h2"])
    assert verdict == "pass" and detail["system_rows"] == 3 and detail["system_keys"] == 2
    _verdict, clean, _mode = _keyset(["h1", "h2"])
    assert "system_rows" not in clean


def test_report_cell_shows_marker() -> None:
    _verdict, detail, mode = _keyset(["h1", "h1", "h2"])
    cell = rp._verdict_cell({"verdict": "pass", "mode": mode, "detail": detail})
    assert cell == "pass · 행≠키(3/2)"
    assert rp._verdict_cell({"verdict": "fail", "mode": "as_is", "detail": {}}) == "fail"
    rowset = {"compare": "rowset", "system_rows": 3}
    assert rp._verdict_cell({"verdict": "fail", "mode": "as_is", "detail": rowset}) == "fail"


def test_keyset_marker_record_passes_gate() -> None:
    vault, fakes, _policy = _generating()
    fakes.register(["h1", "h2"])
    verdict, detail, mode = _keyset(["h1", "h1", "h2"])
    result = {
        "status": "ok",
        "columns": ["server_hostname"],
        "rows": [{"server_hostname": "h1"}],
        "total_rows": 1,
        "truncated": False,
    }
    names = rd.result_column_names(result)
    summary = rd.summarize_result(
        result, sources={n: [] for n in names}, policy=_POLICY, vault=vault
    )
    record = cli._oracle_record(  # type: ignore[arg-type]
        (_KEYSET_SPEC, {"rows_by_db": {"itam": []}}, detail, mode, verdict), names, summary, [],
        SimpleNamespace(query=""), SimpleNamespace(policy=_POLICY, vault=vault),
    )  # fmt: skip
    assert record["detail"]["system_rows"] == 3
    assert _gate(vault).check({"trace.jsonl": _trace({"oracle": record})}) == []
