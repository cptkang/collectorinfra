"""ITAM 질의 벤치 — 폐쇄망 1회차 키트 (plans/135 v1.4 · W8 · 사용자 인터뷰 2026-10-06).

G-7 답: 내부망 「DB 구조」 탭으로 만든 스키마로 1회차는 관찰만 하고, 반출 로그로 서비스↔서버 연결
위치를 찾아 정답 SQL 을 쓴 뒤 2회차부터 대조한다. 반출 카탈로그는 운영 108테이블 전부(구조·의미만).
DB·LLM 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import (
    CLOSED_POLICY_PATH,
    CLOSED_SCENARIOS_PATH,
    POLICY_PATH,
    REPO_ROOT,
    TRANSCRIPT_PATH,
)
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp


#: 폐쇄망 「DB 구조」 탭 스키마 사본(운영 108테이블 · 한글 컬럼 이름)
CLOSED_SCHEMA_PATH = REPO_ROOT / "testdata" / "itam_bench" / "closed" / "itam_schema.json"
#: G-1 (b) — 검토 등급 general 중 식별자류(호스트명·가상화·군집 ID · 번호류 7칸)만 `identifier`로
#: 내린다(그룹회사코드는 코드라 general)
_DEMOTED_IDENTIFIERS = frozenset(
    {
        "sevrHostName",
        "vrtlMgtSevrID",
        "clstRefID",
        "vrtlSevrRefID",
        "srialNoCtnt",
        "cmdtsUniqno",
        "cmdtsClsfiNo",
        "byCtrcNo",
        "sevrManmenCtrcNo",
        "byCmdtsDtalsSerno",
        "manmenCmdtsDtalsSerno",
    }
)


def _closed_schema_tables() -> dict[str, Any]:
    return json.loads(CLOSED_SCHEMA_PATH.read_text(encoding="utf-8"))["schema"]["tables"]


@pytest.fixture(scope="module")
def closed_policy() -> cat.ColumnPolicy:
    return cat.load_policy(CLOSED_POLICY_PATH)


class TestClosedScenarios:
    def test_draft_oracle_only_converted_rest_observe_user_prompts(
        self, closed_policy: cat.ColumnPolicy
    ) -> None:
        scenarios = cat.load_scenarios(CLOSED_SCENARIOS_PATH, closed_policy)
        assert len(scenarios) == 18  # 1회차 14 + 2회차 관찰 4(서비스↔서버 단서 · plans/139 W7)
        assert all(s.env == ("closed",) and "초안" in s.title for s in scenarios)
        # plans/146 W5 (1): 3회차 관찰로 정답 모양이 정해진 7건만 oracle — 나머지는 관찰 유지
        converted = {f"ITAM-{n}" for n in (107, 108, 109, 110, 111, 112, 116)}
        for s in scenarios:
            for turn in s.turns:
                if s.id in converted:
                    assert turn.oracle and turn.observe is None, s.id
                else:
                    assert turn.oracle is None and turn.observe, s.id
        assert all(set(s.turns[0].send) == {"query"} for s in scenarios)
        first = next(s for s in scenarios if s.id == "ITAM-101").turns[0]
        assert first.query == "자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘"
        assert sum(s.category == "service" for s in scenarios) >= 5

    def test_closed_policy_is_sandbox_review_rekeyed_by_sheet_order(
        self, closed_policy: cat.ColumnPolicy
    ) -> None:
        """plans/149 W5 — 샌드박스 검토 등급(영문 변수명) → 시트 순서 대응(한글 컬럼) → 식별자류만
        `identifier`로 내림 = 폐쇄망 정책(등급 동기 유지 · 결정적 대응 — 자리별 타입·NULL 일치)."""
        sandbox = cat.load_policy(POLICY_PATH)
        sheet = yaml.safe_load(TRANSCRIPT_PATH.read_text(encoding="utf-8"))["tables"]
        closed = _closed_schema_tables()
        expected: dict[str, dict[str, str]] = {}
        for table, columns in sandbox.tables.items():
            sheet_columns = sheet[table]["columns"]
            real = closed[table.lower()]["columns"]
            assert len(sheet_columns) == len(real), table
            for spec, column in zip(sheet_columns, real, strict=True):
                assert spec["type"].split("(")[0].lower() == column["type"], spec["var"]
                assert spec["nullable"] == column["nullable"], spec["var"]
            seq = {spec["var"]: index for index, spec in enumerate(sheet_columns)}
            expected[table.lower()] = {
                real[seq[var]]["name"]: (
                    "identifier" if var in _DEMOTED_IDENTIFIERS and grade == "general" else grade
                )
                for var, grade in columns.items()
            }
        assert {t: dict(c) for t, c in closed_policy.tables.items()} == expected
        assert sum(len(c) for c in closed_policy.tables.values()) == 77
        assert closed_policy.scope == "closed"
        assert closed_policy.canary_literals == () and closed_policy.canary_patterns == ()
        # 샌드박스 정책은 새 등급을 쓰지 않는다
        assert "identifier" not in {g for c in sandbox.tables.values() for g in c.values()}

    def test_closed_policy_names_exist_in_closed_schema(
        self, closed_policy: cat.ColumnPolicy
    ) -> None:
        """가드 — 정책의 테이블·컬럼 이름이 폐쇄망 스키마 사본에 실존한다.

        4회차처럼 정책이 한 칸도 맞지 않는 상태의 재발을 막는다.
        """
        closed = _closed_schema_tables()
        for table, columns in closed_policy.tables.items():
            assert table in closed, table
            real = {column["name"] for column in closed[table]["columns"]}
            assert set(columns) <= real, sorted(set(columns) - real)

    def test_cli_defaults_follow_env(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["--dry-run", "--env", "closed"]) == 0
        out = capsys.readouterr().out
        assert "시나리오   : 18건 (env=closed)" in out
        assert "오라클 7턴 · 관측(observe) 12턴" in out  # plans/146 W5 (1) 전환 7건
        assert cli.main(["--dry-run"]) == 0
        assert "시나리오   : 22건 (env=sandbox)" in capsys.readouterr().out


class TestCatalogAwareRedaction:
    def test_literal_next_to_unreviewed_column_is_masked(
        self, closed_policy: cat.ColumnPolicy
    ) -> None:
        vault = rd.PiiVault.from_policy(closed_policy)
        sql = "SELECT svcNm FROM TSVC01 WHERE ownerEmpNo = '1234' AND svcNm LIKE '%통합인증%'"
        plain = rd.redact_sql(sql, policy=closed_policy, vault=vault, prompt="통합인증 서비스")
        assert "'1234'" in plain  # 카탈로그를 모르면 짧은 수로 남는다
        aware = rd.redact_sql(
            sql,
            policy=closed_policy,
            vault=vault,
            prompt="통합인증 서비스",
            catalog_columns={"ownerEmpNo", "svcNm"},
        )
        assert "'1234'" not in aware and "'%통합인증%'" in aware  # 프롬프트 말은 남는다


def _write_run(directory: Path) -> None:
    directory.mkdir(parents=True)
    catalog = {
        "db_id": "itam",
        "source": "schema_cache",
        "tables": {
            "TCDMSIF80": {
                "columns": [
                    {"name": "sevrHostName"},
                    {"name": "svcCd"},
                    {"name": "newOwnerNm", "policy_suggestion": "pii"},
                ]
            },
            "TSVC01": {
                "columns": [
                    {"name": "svcCd"},
                    {"name": "svcNm", "meaning": "서비스 이름"},
                    {"name": "bizDesc", "meaning": "업무 설명"},
                ]
            },
        },
    }
    (directory / "schema_catalog.yaml").write_text(
        yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8"
    )
    record: dict[str, Any] = {
        "id": "ITAM-101",
        "turn": 1,
        "observe": {"what": "x"},
        "sql_analysis": {"tables": ["TCDMSIF80", "TSVC01"], "columns": ["svcCd", "svcNm"]},
        "executed_sqls": [{"success": True, "row_count": 7}],
        "result": {"total_rows": 7},
    }
    (directory / "trace.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )


class TestSync:
    def test_sync_lists_differences_and_service_candidates(
        self, tmp_path: Path, closed_policy: cat.ColumnPolicy
    ) -> None:
        run = tmp_path / "20261010-090000"
        _write_run(run)
        before = sorted(p.name for p in run.iterdir())
        text = rp.sync_report(run, transcript_path=TRANSCRIPT_PATH, policy=closed_policy)
        assert sorted(p.name for p in run.iterdir()) == before  # 파일을 쓰지 않는다
        assert "- `TSVC01` — 컬럼 3" in text  # 전사본에 없는 테이블
        assert "| TCDMSIF80 | newOwnerNm, svcCd |" in text  # 공통 테이블 컬럼 차이
        assert "`TCDMSIF80.newOwnerNm`" in text  # 사람 정보 휴리스틱 제안
        assert "| TSVC01 | svcNm | 서비스 이름 |" in text  # 서비스 연결 후보
        assert "| TSVC01 | bizDesc | 업무 설명 |" in text
        assert "| ITAM-101 | 1 | TCDMSIF80, TSVC01 | svcCd, svcNm | SQL 7 · 결과 7 |" in text

    def test_cli_sync_accepts_run_id_and_reports_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path)
        _write_run(tmp_path / "r1")
        assert cli.main(["--sync", "r1", "--env", "closed"]) == 0
        assert "싱크 대조 — r1" in capsys.readouterr().out
        assert cli.main(["--sync", "nope"]) == 1


# --- plans/149 W5 — 한글 이름 프롬프트 린트 · 식별자 등급(`identifier`) -----------------------


class TestKoreanIdentifierLint:
    """비ASCII 정책 이름은 코드 꼴(백틱·한정)일 때만 식별자로 잡는다 · ASCII 이름은 종전대로."""

    def test_korean_name_in_prose_passes(self, closed_policy: cat.ColumnPolicy) -> None:
        idents = {"취득금액", "서버호스트명", "tcdmsif80"}
        prose = "자산관리에서 취득금액 제일 큰 서버 3대랑 서버호스트명 보여줘"
        assert cat.lint_prompt(prose, identifiers=idents, policy=closed_policy) == []

    @pytest.mark.parametrize(
        "text",
        ["`취득금액` 큰 서버", "a.취득금액 큰 서버", "취득금액.합계 보여줘", "tcdmsif80 보여줘"],
    )
    def test_code_form_still_flagged(self, text: str, closed_policy: cat.ColumnPolicy) -> None:
        problems = cat.lint_prompt(
            text, identifiers={"취득금액", "tcdmsif80"}, policy=closed_policy
        )
        assert any("테이블·컬럼 식별자" in p for p in problems), text

    def test_ascii_rule_unchanged(self, closed_policy: cat.ColumnPolicy) -> None:
        idents = {"sevrHostName"}
        flagged = cat.lint_prompt("sevrHostName 알려줘", identifiers=idents, policy=closed_policy)
        assert flagged and "sevrHostName" in flagged[0]
        assert cat.lint_prompt("xsevrHostName", identifiers=idents, policy=closed_policy) == []


def test_oracle_key_accepts_identifier_rejects_pii(closed_policy: cat.ColumnPolicy) -> None:
    spec = {"id": "ITAM-108", "compare": "keyset", "db_ids": ["itam"]}
    assert cat._check_oracle(
        {**spec, "key": [["서버호스트명", "hostname"]]}, where="t", db_ids=["itam"],
        policy=closed_policy,
    ) == []  # fmt: skip
    errors = cat._check_oracle(
        {**spec, "key": [["담당자직원명"]]}, where="t", db_ids=["itam"], policy=closed_policy
    )
    assert any("키는 general·identifier만" in e for e in errors)


#: 서버 원장 두 테이블 — 정책 안(`tcdmsif80`)과 정책 밖(`tcdmsif72`)이 같은 호스트명 칸으로 잇는다
_HOST_CATALOG: dict[str, Any] = {
    "tables": {
        "tcdmsif80": {
            "columns": [{"name": n} for n in ("서버호스트명", "운영체제타입내용", "담당자직원명")],
            "relations": [
                {
                    "from": "tcdmsif80",
                    "to": "tcdmsif72",
                    "columns": [["서버호스트명", "서버호스트명"]],
                }
            ],
            "key": [],
        },
        "tcdmsif72": {"columns": [{"name": "서버호스트명"}], "relations": [], "key": []},
    },
    "same_key_groups": [],
}


def test_identifier_propagates_along_relation(closed_policy: cat.ColumnPolicy) -> None:
    """관계 등가류 — 정책 밖 같은 키 칸(unclassified)도 identifier 로 올라 조인 양쪽이 같은 등급."""
    from scripts.itam_bench.substitute import effective_policy

    run_policy = effective_policy(closed_policy, _HOST_CATALOG)
    assert run_policy.grade("서버호스트명", "tcdmsif72") == "identifier"
    assert run_policy.grade("서버호스트명", "tcdmsif80") == "identifier"
    assert closed_policy.grade("서버호스트명", "tcdmsif72") == "identifier"  # 테이블 모름 = 최엄격
    assert cat.strictest(["identifier", "pii"]) == "pii"
    assert cat.strictest(["identifier", "unclassified", "free_text"]) == "identifier"


#: 108 모양 — 별칭 붙인 호스트명 · 호스트명 리터럴 조건(값은 합성 · `results/` 원본 아님)
_HOST_SQL = (
    "SELECT s.`서버호스트명` AS 서버명, s.`운영체제타입내용` FROM `tcdmsif80` s "
    "WHERE s.`서버호스트명` LIKE 'realhost0%' ORDER BY 서버명"
)
_HOST_ROWS = [
    {"서버명": "realhost01", "운영체제타입내용": "AIX"},
    {"서버명": "realhost02", "운영체제타입내용": "AIX"},
]
#: 오라클 정답 — realhost02 대신 realhost03(missing·extra 가 하나씩 생긴다)
_HOST_ORACLE = [{"서버호스트명": "realhost01"}, {"서버호스트명": "realhost03"}]


class _HostClient:
    def send(self, endpoint: str, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            status="completed", query_id="q", response="답변", db_ids=["itam"], disclosures=[],
            clarification=None, http_status=200, retries=0, wall_ms=1.0,
            error="Unknown column 'realhost02' in 'where clause'",
        )  # fmt: skip

    def download_csv(self, query_id: str) -> dict[str, Any]:
        return {
            "status": "ok", "columns": ["서버명", "운영체제타입내용"], "rows": _HOST_ROWS,
            "total_rows": 2, "truncated": False, "reason": None,
        }  # fmt: skip


class _HostTail:
    def __init__(self, sql: str = "") -> None:
        self.sql = sql

    def mark(self) -> int:
        return 0

    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
        if not self.sql:
            return []
        return [
            {"sql": self.sql, "source": "itam", "success": True, "row_count": 2,
             "retry_attempt": 0, "error": None}
        ]  # fmt: skip


def _host_run(
    policy: cat.ColumnPolicy, generating: bool
) -> tuple[list[dict[str, Any]], rd.PiiVault]:
    from scripts.itam_bench import judge as jd

    if generating:
        vault, _fakes, run_policy = cli.start_substitution(
            policy=policy, catalog_doc=_HOST_CATALOG, user_values={}, p1_draft=None
        )
    else:
        vault, run_policy = rd.PiiVault.from_policy(policy), policy
    spec = {
        "id": "ITAM-108",
        "compare": "keyset",
        "key": [["서버호스트명", "서버명", "hostname"]],
        "db_ids": ["itam"],
    }
    turn = cat.Turn(
        index=1,
        send={"query": "이번 분기에 유지보수 계약이 끝나는 서버 있어?"},
        expect={"db_ids": ["itam"], "oracle": spec},
    )
    scenario = cat.Scenario(
        id="ITAM-108", title="t", category="maintenance", env=("closed",), traps=(),
        key_columns=(), gold_tables=(), turns=(turn,),
    )  # fmt: skip
    ctx = cli.RunContext(
        run_id="r",
        tier=None,
        policy=run_policy,
        catalog=jd.CatalogFacts.from_catalog(_HOST_CATALOG),
        vault=vault,
        oracle=lambda *a: {"status": "ok", "rows_by_db": {"itam": _HOST_ORACLE}},
    )
    records = cli.run_scenarios(
        [scenario],
        client=_HostClient(),
        audit=_HostTail(_HOST_SQL),
        capture=_HostTail(),
        ctx=ctx,
        progress=lambda _t: None,
    )
    return records, vault


@pytest.mark.parametrize("generating", [False, True])
def test_identifier_values_substituted_structure_kept(
    generating: bool, closed_policy: cat.ColumnPolicy
) -> None:
    """식별자 칸 — 값(결과 표본·SQL 리터럴·오류 문구·판정 키)은 원값 0, 별칭·SQL 구조는 남는다."""
    records, vault = _host_run(closed_policy, generating)
    (record,) = records
    text = json.dumps(record, ensure_ascii=False)
    assert "realhost" not in text
    # 원값은 vault 에 모였다 — 관문이 다른 칸에서 훑는다
    assert vault.harvested_hits("realhost01 realhost02") == 2
    sql = record["executed_sqls"][0]["sql"]
    assert sql.startswith(
        "SELECT s.`서버호스트명` AS 서버명, s.`운영체제타입내용` FROM `tcdmsif80` s"
    )
    assert sql.endswith("ORDER BY 서버명")
    host, os_column = record["result"]["columns"]
    assert host["name"] == "서버명" and host["log_policy"] == "identifier"  # 별칭은 남긴다
    assert os_column["log_policy"] == "general" and os_column["top_values"] == {"AIX": 2}
    oracle = record["oracle"]
    assert oracle["verdict"] == "fail" and oracle["keys_recorded"] is False
    detail = oracle["detail"]
    if generating:
        assert host["substituted"] is True and len(host["sample"]) == 2
        assert "LIKE 'realhost0%'" not in sql and f"'{rd.MASK}'" not in sql  # 가짜 값
        # 판정 상세 키는 구조 그대로 가짜 값 — 결과 표본과 run 일관(같은 원값 → 같은 가짜 값)
        assert {"missing", "extra"} <= set(oracle["substituted_fields"])
        (extra,) = detail["extra"]
        assert extra[0] in host["sample"]
        (missing,) = detail["missing"]
        assert missing[0] not in host["sample"]
    else:
        assert "sample" not in host and "top_values" not in host
        assert f"LIKE '{rd.MASK}'" in sql
        assert detail["missing"] == 1 and detail["extra"] == 1  # 생성기 없으면 건수만
    gate = rd.LeakGate(policy=closed_policy, vault=vault, user_values={})
    trace = json.dumps(record, ensure_ascii=False) + "\n"
    assert gate.check({"trace.jsonl": trace}) == []


#: 번호류 식별자(물품고유번호)가 정책 밖 자산 원장(`tcdmsif41`)과 같은 이름 칸으로 잇는다
_ITEM_CATALOG: dict[str, Any] = {
    "tables": {
        "tcdmsif80": {
            "columns": [{"name": "물품고유번호"}, {"name": "운영체제타입내용"}],
            "relations": [
                {
                    "from": "tcdmsif80",
                    "to": "tcdmsif41",
                    "columns": [["물품고유번호", "물품고유번호"]],
                }
            ],
            "key": [],
        },
        "tcdmsif41": {"columns": [{"name": "물품고유번호"}], "relations": [], "key": []},
    },
    "same_key_groups": [],
}
#: 105·111 모양 — 물품 키로 자산 원장에 잇는다(값은 합성)
_ITEM_SQL = (
    "SELECT s.`물품고유번호` AS 물품번호 FROM `tcdmsif80` s "
    "JOIN `tcdmsif41` a ON s.`물품고유번호` = a.`물품고유번호` "
    "WHERE a.`물품고유번호` = 'Q123456' OR s.`물품고유번호` = 'Q123456'"
)


def test_number_identifier_keeps_join_and_oracle_key(closed_policy: cat.ColumnPolicy) -> None:
    """번호류 identifier — 관계 전파로 정책 밖 같은 이름 칸도 identifier · 조인 조건·별칭은 그대로 ·
    같은 원값은 양쪽에서 같은 가짜 값(키 비교 유지) · `oracle.key`로 받는다."""
    vault, _fakes, run_policy = cli.start_substitution(
        policy=closed_policy, catalog_doc=_ITEM_CATALOG, user_values={}, p1_draft=None
    )
    assert run_policy.grade("물품고유번호", "tcdmsif41") == "identifier"
    out = rd.redact_sql(
        _ITEM_SQL,
        policy=run_policy,
        vault=vault,
        catalog_columns=("물품고유번호", "운영체제타입내용"),
    )
    assert "Q123456" not in out and rd.MASK not in out
    head, _, tail = out.partition(" WHERE ")
    assert head == _ITEM_SQL.partition(" WHERE ")[0]  # 별칭·조인 조건 그대로
    left, right = (part.rsplit("= ", 1)[1] for part in tail.split(" OR "))
    assert left == right  # run 일관 가짜 값
    spec = {"id": "ITAM-111", "compare": "keyset", "key": [["물품고유번호"]], "db_ids": ["itam"]}
    assert cat._check_oracle(spec, where="t", db_ids=["itam"], policy=closed_policy) == []
    gate = rd.LeakGate(policy=closed_policy, vault=vault, user_values={})
    record = {"executed_sqls": [{"sql": out}]}
    assert gate.check({"trace.jsonl": json.dumps(record, ensure_ascii=False) + "\n"}) == []


def test_network_without_generator_records_length_only(closed_policy: cat.ColumnPolicy) -> None:
    """생성기 없는 경로의 IP 칸(network)은 길이만 — 기준선(재키잉 전 미분류)과 같은 강도.

    기준선 38adcc6: `IP주소내용` = unclassified → `length` 만 · 재키잉 직후(2차): network →
    `top_values {"10.20.30.***": 3}`(앞 세 옥텟 원문) — 3차에서 길이만으로 되돌렸다.
    """
    result = {
        "status": "ok",
        "columns": ["IP주소내용"],
        "rows": [{"IP주소내용": f"10.20.30.{n}"} for n in (41, 43, 45)],
        "total_rows": 3,
        "truncated": False,
    }
    sources = rd.resolve_result_columns(
        ["IP주소내용"], ["SELECT s.`IP주소내용` FROM `tcdmsif80` s"], closed_policy.column_names()
    )
    plain = rd.summarize_result(
        result, sources=sources, policy=closed_policy, vault=rd.PiiVault.from_policy(closed_policy)
    )
    (column,) = plain["columns"]
    assert column["log_policy"] == "network" and column["length"] == [11, 11]
    assert "10.20" not in json.dumps(plain) and "sample" not in column
    vault, _fakes, run_policy = cli.start_substitution(
        policy=closed_policy, catalog_doc={"tables": {}}, user_values={}, p1_draft=None
    )
    (faked,) = rd.summarize_result(
        result, sources=sources, policy=run_policy, vault=vault
    )["columns"]
    assert len(faked.get("sample") or faked.get("top_values") or []) >= 1  # 생성기 경로는 가짜 IP
    assert "10.20.30" not in json.dumps(faked)
