"""plans/145 독립 검증(W4) — 형식 보존 가짜 값 치환의 수용 기준 보강 테스트.

- 생성기 없는 `PiiVault` 경로가 기준선 `89773af`와 같은 출력인가(기준선 worktree 에서 뽑은 값 고정).
- 실 run 경로(`start_substitution` → `stage_gated`)에서 한 생성기가 SQL·코드값 파일·목록에 같은
  가짜 값을 내는가(A2).
- 모든 등급 열이 섞인 결과 요약이 관문 `substitution` ②에 오검출되지 않는가.
- A2 결함 재현: IP 가 든 값 · 망 컬럼과 미분류 컬럼의 같은 IP · 등가류 올림이 「분류 필요 컬럼」을
  지우는 회귀(실패하면 제품 결함이다 — xfail 로 덮지 않는다).

실 LLM·DB 0. 가짜 값은 난수라 값이 아니라 성질로 단언한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench.substitute import FakeValues


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _vault(policy: cat.ColumnPolicy) -> rd.PiiVault:
    vault = rd.PiiVault.from_policy(policy)
    vault.add("박서준")
    vault.add("김민수")
    return vault


def _catalog(*names: str, relations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "tables": {
            "TCDMSIF80": {
                "columns": [{"name": n} for n in names],
                "relations": relations or [],
                "key": [],
            }
        },
        "same_key_groups": [],
    }


def _start(
    policy: cat.ColumnPolicy, catalog_doc: dict[str, Any], p1_draft: dict[str, Any] | None = None
) -> tuple[rd.PiiVault, FakeValues, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=policy, catalog_doc=catalog_doc, user_values={"login_id": "5488923"},
        p1_draft=p1_draft,
    )


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, row, strict=True)) for row in rows],
        "total_rows": len(rows),
        "truncated": False,
    }


# --- 기준선 비트 동일(생성기 없는 경로) ----------------------------------------------------

#: 기준선 `89773af` worktree 에서 같은 입력으로 뽑은 출력.
_BASE_SQL = {
    "SELECT rspblPsnEmnm AS 담당자 FROM TCDMSIF80 WHERE rspblPsnEmnm = '박서준'":
        "SELECT rspblPsnEmnm AS <가림> FROM TCDMSIF80 WHERE rspblPsnEmnm = '<가림>'",
    "SELECT * FROM TCDMSIF80 WHERE zzzMemo = 'secret value' /* 주석 내용 abc */ -- 끝 주석":
        "SELECT * FROM TCDMSIF80 WHERE zzzMemo = '<가림>' /*<가림>*/ -- <가림>",
    "SELECT sevrHostName FROM TCDMSIF80 WHERE sevrHostName LIKE '%web01%' AND ip = '10.1.2.3'":
        "SELECT sevrHostName FROM TCDMSIF80 WHERE sevrHostName LIKE '%web01%' AND ip = '<가림>'",
    "SELECT 담당자명 FROM 자산 WHERE 상태 = '운영중' AND 코드 = 'A01' # hash":
        "SELECT 담당자명 FROM 자산 WHERE 상태 = '운영중' AND 코드 = '<가림>' # <가림>",
    "SELECT a FROM t WHERE b = '홍길동' OR c = 'T000003' OR d='2024-01-05' OR e = '12'":
        "SELECT a FROM t WHERE b = '<가림>' OR c = '<가림>' OR d='2024-01-05' OR e = '12'",
    "SELECT x FROM TCDMSIF80 WHERE rspblPsnEmnm IN ('김민수','이영희') AND zzzMemo = 'O''Brien'":
        "SELECT x FROM TCDMSIF80 WHERE rspblPsnEmnm IN ('<가림>','<가림>') AND zzzMemo = '<가림>'",
}
_BASE_TEXT = {
    "You have an error in your SQL syntax near 'secret value' at line 1":
        "You have an error in your SQL syntax near '<가림>' at line 1",
    "Duplicate entry '박서준' for key 'PRIMARY'": "Duplicate entry '<가림>' for key '<가림>'",
    "Unknown column 'zzzMemo' in 'where clause' near 'abc":
        "Unknown column 'zzzMemo' in 'where clause' near '<가림>'",
    "connect to 10.20.30.40 failed for 'T000003' and '홍길동'":
        "connect to 10.20.30.*** failed for '<가림>' and '<가림>'",
}
_TEXT_SQL = "SELECT * FROM TCDMSIF80 WHERE zzzMemo = 'secret value' /* 주석 내용 abc */ -- 끝 주석"


@pytest.mark.parametrize("sql", list(_BASE_SQL))
def test_baseline_redact_sql_unchanged_without_generator(
    policy: cat.ColumnPolicy, sql: str
) -> None:
    out = rd.redact_sql(
        sql, policy=policy, vault=_vault(policy), prompt="운영중 서버",
        catalog_columns=["zzzMemo", "ip"],
    )
    assert out == _BASE_SQL[sql]


@pytest.mark.parametrize("text", list(_BASE_TEXT))
def test_baseline_redact_text_unchanged_without_generator(
    policy: cat.ColumnPolicy, text: str
) -> None:
    out = rd.redact_text(text, vault=_vault(policy), sql=_TEXT_SQL, prompt="운영중")
    assert out == _BASE_TEXT[text]


def test_baseline_summary_scrub_detail_unchanged_without_generator(
    policy: cat.ColumnPolicy,
) -> None:
    rows = [
        ["박서준", "web01", "free", "1500", "10.0.0.5"],
        ["이영희", "web02", "x y", "200", "10.0.0.6"],
        ["이영희", "web01", "", None, "10.0.0.5"],
    ]
    names = ["담당자", "host", "memo", "amt", "ip"]
    sources = {"담당자": ["rspblPsnEmnm"], "host": ["sevrHostName"], "memo": ["zzzMemo"],
               "amt": [], "ip": []}
    summary = rd.summarize_result(
        _result(names, rows), sources=sources, policy=policy, vault=_vault(policy)
    )
    assert summary["columns"] == [
        {"name": "열#1", "source": ["rspblPsnEmnm"], "log_policy": "pii", "count": 3,
         "nulls": 0, "distinct": 2},
        {"name": "host", "source": ["sevrHostName"], "log_policy": "general", "count": 3,
         "nulls": 0, "top_values": {"web01": 2, "web02": 1}},
        {"name": "memo", "source": ["zzzMemo"], "log_policy": "unclassified", "count": 3,
         "nulls": 1, "length": [3, 4]},
        {"name": "amt", "source": [], "log_policy": "unclassified", "count": 3, "nulls": 1,
         "length": [3, 4]},
        {"name": "ip", "source": [], "log_policy": "unclassified", "count": 3, "nulls": 0,
         "length": [8, 8]},
    ]
    assert "substituted" not in json.dumps(summary)
    scrubbed = rd.scrub_tree(
        {"a": ["박서준 10.1.1.1", {"k": "홍길동 T000003 010-1234-5678"}]}, _vault(policy)
    )
    assert scrubbed == {
        "a": ["<가림:pii> 10.1.1.***", {"k": "<가림:pii> <가림:pii> 0***********8 (숫자11자리)"}]
    }
    detail = {
        "missing": [["K1", "박서준"]],
        "oracle_top": [["K1", 100], ["K2", 200]],
        "value_diffs": [{"key": "K1", "oracle": 1, "system": 2}],
        "header": ["a", "한글"],
    }
    labels = {"한글": "라벨"}
    assert jd.sanitize_detail(detail, value_grade="amount", keys_allowed=False, labels=labels) == {
        "missing": 1, "oracle_top": 2, "value_diffs": 1, "header": ["a", "라벨"]
    }
    assert jd.sanitize_detail(detail, value_grade="amount", keys_allowed=True, labels=labels) == {
        "missing": [["K1", "박서준"]],
        "oracle_top": [["K1"], ["K2"]],
        "value_diffs": [{"key": "K1", "differs": True}],
        "header": ["a", "라벨"],
    }


def test_baseline_gate_only_adds_substitution_rule(policy: cat.ColumnPolicy) -> None:
    """기존 6규칙 순서·위반 판정 불변 — 규칙 목록에 `substitution`이 끼어들었을 뿐이다."""
    assert [r for r in rd.GATE_RULES if r != "substitution"] == [
        "canary", "pii_value", "pii_regex", "schema_form", "user_info", "code_original"
    ]
    gate = rd.LeakGate(policy=policy, vault=_vault(policy), user_values={"login_id": "5488923"})
    files = {
        "trace.jsonl": json.dumps(
            {"result": {"columns": [{"log_policy": "pii", "distinct": 2}]}, "x": "박서준"},
            ensure_ascii=False,
        ) + "\n",
        "report.md": "hello 5488923 T000003\n",
    }
    rules = sorted({(v["file"], v["rule"]) for v in gate.check(files)})
    assert rules == [
        ("report.md", "canary"), ("report.md", "user_info"), ("trace.jsonl", "pii_value")
    ]


# --- A2 실 run 경로: 한 생성기 ------------------------------------------------------------


def test_a2_run_path_code_samples_share_sql_fake(policy: cat.ColumnPolicy, tmp_path) -> None:
    """`start_substitution`의 vault 를 그대로 `stage_gated`에 넘기면 코드값 파일·목록이 SQL 리터럴과
    같은 가짜 값이다(`_run_with_server` 배선 순서와 같다)."""
    draft = {"assets": {"code_values": {"TCDMSIF80.asstStusDstcd": ["ACTIVE", "RETIRED"]}}}
    vault, fakes, run_policy = _start(policy, _catalog("zzzMemo", "asstStusDstcd"), draft)
    sql = "SELECT zzzMemo FROM TCDMSIF80 WHERE zzzMemo = 'ACTIVE' /* RETIRED */"
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault, catalog_columns=["zzzMemo"])
    error = rd.redact_text("Duplicate entry 'ACTIVE' for key", vault=vault, sql=sql)
    record = {"run_id": "20261008-000000", "id": "s1", "turn": 1, "repeat": 0,
              "status": "ok", "taxonomy": [], "executed_sqls": [{"sql": redacted}],
              "error": error}
    staged, gate = cli.stage_gated(
        run_meta={"run_id": "20261008-000000", "substitution_note": rd.SUBSTITUTION_NOTE},
        catalog_doc={"tables": {}}, records=[record], policy=policy, vault=vault,
        user_values={"login_id": "5488923"}, p1_draft=draft,
    )
    active, retired = fakes.fake("ACTIVE"), fakes.fake("RETIRED")
    assert f"zzzMemo = '{active}'" in redacted and f"/*{retired}*/" in redacted
    assert error == f"Duplicate entry '{active}' for key"
    samples = yaml.safe_load(staged[rd.CODE_SAMPLES_FILE])
    assert set(samples["columns"]["TCDMSIF80.asstStusDstcd"]["values"]) == {active, retired}
    listed = yaml.safe_load(staged[rd.SUBSTITUTIONS_FILE])["values"]
    assert {active, retired} <= set(listed)
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok, violations
    written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").iterdir())
    assert "ACTIVE" not in written and "RETIRED" not in written


def test_a2_comment_fake_matches_error_text(policy: cat.ColumnPolicy) -> None:
    vault, _fakes, run_policy = _start(policy, _catalog("zzzMemo"))
    sql = "SELECT zzzMemo FROM TCDMSIF80 /* 비밀메모 abc */"
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault, catalog_columns=["zzzMemo"])
    fake = redacted.split("/*", 1)[1].split("*/", 1)[0]
    error = rd.redact_text("bad comment 비밀메모 abc here", vault=vault, sql=sql)
    assert "비밀메모" not in redacted + error and error == f"bad comment {fake} here"


# --- 관문 ② 오검출 없음(모든 등급 혼합) ------------------------------------------------------


def test_gate_trace_positive_check_accepts_all_grades(policy: cat.ColumnPolicy) -> None:
    names = ["담당자", "host", "ip", "amt", "desc", "memo", "demoted"]
    sources = {"담당자": ["rspblPsnEmnm"], "host": ["sevrHostName"], "ip": ["iPCtnt"],
               "amt": ["acqsiAmt"], "desc": ["cnfgItemDescCtnt"], "memo": ["zzzMemo"],
               "demoted": ["asstHoldBrnName"]}
    rows = [
        ["박서준", "web01", "10.0.0.5", "1500", "설명 문장 하나", "KQ4821ZX", "박서준"],
        ["이영희", "web02", "10.0.0.6", "200", "설명 둘", "MEMO-77", "본점"],
        ["이영희", "web01", "10.0.0.5", "200", "설명 둘", "KQ4821ZX", "본점"],
    ]
    vault, _fakes, run_policy = _start(policy, _catalog(*[s[0] for s in sources.values()]))
    summary = rd.summarize_result(
        _result(names, rows), sources=sources, policy=run_policy, vault=vault
    )
    by_name = {c["source"][0]: c for c in summary["columns"]}
    assert by_name["asstHoldBrnName"].get("demoted") == "pii_value_match"
    for column in ("rspblPsnEmnm", "acqsiAmt", "cnfgItemDescCtnt", "zzzMemo", "asstHoldBrnName"):
        assert by_name[column]["substituted"] is True, column
    assert by_name["sevrHostName"]["top_values"] == {"web01": 2, "web02": 1}  # 일반 = 원값
    text = json.dumps(summary, ensure_ascii=False)
    # 금액 합·최소·최대는 현행 통계 칸이라 원값이 남는다(§2.3 L7) — 표본만 본다
    assert set(by_name["acqsiAmt"]["top_values"]) & {"1500", "200"} == set()
    for original in ("박서준", "이영희", "설명 둘", "KQ4821ZX", "MEMO-77", "10.0.0.6"):
        assert original not in text, original
    gate = rd.LeakGate(policy=policy, vault=vault, user_values={"login_id": "5488923"})
    trace = json.dumps({"result": summary}, ensure_ascii=False) + "\n"
    assert gate.check({"trace.jsonl": trace}) == []


# --- A2 결함 재현(IP) ---------------------------------------------------------------------


def test_a2_value_containing_ip_same_fake_in_sql_and_result(policy: cat.ColumnPolicy) -> None:
    """IP 가 든 값 — 가린 리터럴은 끝의 `vault.mask_ip` 패스가 가짜 값 안 IP 꼴을 다시 바꿔 결과
    표본의 가짜 값과 달라진다.

    재현: 'host 10.1.2.3' → SQL 'kurn 30.8.6.12' · 표본 'kurn 30.8.6.3'.
    """
    vault, fakes, run_policy = _start(policy, _catalog("zzzMemo"))
    value = "host 10.1.2.3"
    sql = f"SELECT zzzMemo FROM TCDMSIF80 WHERE zzzMemo = '{value}'"
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault, catalog_columns=["zzzMemo"])
    summary = rd.summarize_result(
        _result(["zzzMemo"], [[value]]), sources={"zzzMemo": ["zzzMemo"]},
        policy=run_policy, vault=vault,
    )
    literal = redacted.split("zzzMemo = '", 1)[1].rstrip("'")
    assert summary["columns"][0]["sample"] == [literal]
    assert fakes.is_fake(literal)


def test_a2_same_ip_same_fake_in_network_and_unclassified(policy: cat.ColumnPolicy) -> None:
    """같은 IP 원값 — 망 컬럼은 `fake_ip`(앞 세 자리 유지), 미분류 컬럼 리터럴은 일반 문자 군
    치환(옥텟이 268·424 처럼 IP 가 아니게 된다)으로 서로 다른 가짜 값이 된다(§2.1 「전역 일관」)."""
    vault, _fakes, run_policy = _start(policy, _catalog("iPCtnt", "zzzMemo"))
    ip = "192.168.10.200"
    sql = f"SELECT iPCtnt FROM TCDMSIF80 WHERE iPCtnt = '{ip}' OR zzzMemo = '{ip}'"
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault, catalog_columns=["zzzMemo"])
    network = redacted.split("iPCtnt = '", 1)[1].split("'", 1)[0]
    other = redacted.split("zzzMemo = '", 1)[1].split("'", 1)[0]
    assert network == other


# --- A3 등가류가 「분류 필요 컬럼」을 지우는 회귀 ---------------------------------------------


def test_effective_policy_keeps_unclassified_report(policy: cat.ColumnPolicy) -> None:
    """정책 밖 컬럼이 관계로 pii 와 이어져 유효 정책에서 올라가도, 정책 파일에는 여전히 없으므로
    리포트 「분류 필요 컬럼」(`unknown_identifiers`)에 남아야 한다 — 유효 정책이 `column_names()`를
    늘려 결과 열 해석에서 미분류 표지(`?:`)가 사라진다."""
    relation = {"from": "TCDMSIF80", "to": "TCDMSIF80",
                "columns": [["zzzOwner", "rspblPsnEmpid"]], "kind": "declared"}
    _vault_, _fakes, run_policy = _start(
        policy, _catalog("zzzOwner", "rspblPsnEmpid", relations=[relation])
    )
    assert run_policy.grade("zzzOwner", "TCDMSIF80") == "pii"  # 등가류 올림 자체는 동작
    sql = "SELECT zzzOwner AS 담당 FROM TCDMSIF80"
    sources = rd.resolve_result_columns(["담당"], [sql], run_policy.column_names())
    summary = rd.summarize_result(
        _result(["담당"], [["E12345"]]), sources=sources, policy=run_policy,
        vault=rd.PiiVault.from_policy(run_policy),
    )
    assert rd.unclassified_columns(summary) == ["zzzOwner"]
