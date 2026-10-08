"""ITAM 질의 벤치 — 로그 위생 계층 (plans/135 W2 · §3.5 · D-301 ②④⑤). TDD — 구현보다 먼저 썼다.

합성 성명·직원번호·전화·이메일을 ①결과 행 ②별칭 열 ③SQL 리터럴 ④DB 오류 문구 ⑤미분류 열로 각각 흘려
산출 텍스트에 0건인지 본다. 사용자 정보(로그인 계정·JWT·DSN·홈 경로·OS 사용자)는 가린 형태만 남는다.
누출 관문은 걸린 **위치만** 적고 값은 적지 않으며, 실패하면 산출물을 옮기지 않는다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd

_NAME = "홍길동"  # 카나리아(정책 파일) — 합성 성명
_NAME2 = "박서준"  # 카나리아 밖 합성 성명 — 결과에서 수집한 값으로만 막힌다
_EMPID = "T000003"
_PHONE = "010-1234-5678"
_EMAIL = "synth.user@example.com"


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


@pytest.fixture()
def vault(policy: cat.ColumnPolicy) -> rd.PiiVault:
    return rd.PiiVault.from_policy(policy)


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, row)) for row in rows],
        "total_rows": len(rows),
        "truncated": False,
        "reason": None,
    }


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _no_person_values(text: str) -> None:
    for value in (_NAME, _NAME2, _EMPID, _PHONE, _EMAIL, "T000004"):
        assert value not in text, value


# --- 사용자 정보 가림 (§3.5.4) ---------------------------------------------------


class TestUserInfoMasking:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("5488923", "5***"),
            ("ab", "***"),
            ("", ""),
            (None, ""),
            ("cptkang", "c***"),
        ],
    )
    def test_mask_identifier_follows_d300(self, value: Any, expected: str) -> None:
        assert rd.mask_identifier(value) == expected

    def test_mask_dsn_hides_account_and_password(self) -> None:
        masked = rd.mask_dsn("mariadb://itam_ro:itam_ro_pass_2024@localhost:3307/INST1")
        assert masked == "mariadb://***:***@localhost:3307/INST1"
        assert rd.mask_dsn("http://127.0.0.1:9099/sse") == "http://127.0.0.1:9099/sse"
        assert "admin" not in rd.mask_dsn("http://admin@127.0.0.1:9099/sse")

    def test_dsn_scheme_keeps_only_scheme(self) -> None:
        dsn = "mariadb://itam_ro:pw@itam-db01:3307/INST1"
        assert rd.dsn_scheme(dsn) == "mariadb"
        assert rd.dsn_scheme("http://127.0.0.1:9099/sse") == "http"
        assert rd.dsn_scheme("") is None and rd.dsn_scheme(None) is None

    def test_run_meta_endpoint_survives_harvested_host_values(
        self, policy: cat.ColumnPolicy
    ) -> None:
        # 20261007-141833 재현 — ITAM 결과에서 수집한 호스트·DB 이름이 마스킹 DSN 토큰과 겹쳤다
        vault = rd.PiiVault.from_policy(policy)
        for value in ("itam-db01", "INST1", "sse"):
            vault.add(value)
        gate = rd.LeakGate(policy=policy, vault=vault, user_values={})
        dsn = "mariadb://itam_ro:pw@itam-db01:3307/INST1"
        before = {"itam_dsn": rd.mask_dsn(dsn), "mcp_endpoint": rd.mask_dsn("http://h:9099/sse")}
        assert {v["rule"] for v in gate.check({"run.json": _dump(before)})} == {"pii_value"}
        after = {"itam_dsn": rd.dsn_scheme(dsn), "mcp_endpoint": rd.dsn_scheme("http://h:9099/sse")}
        assert gate.check({"run.json": _dump(after)}) == []

    def test_display_path_relative_and_home(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        home = tmp_path
        assert rd.display_path(repo / "results" / "x", repo_root=repo, home=home) == "results/x"
        assert rd.display_path(tmp_path / "other" / "y", repo_root=repo, home=home) == "~/other/y"

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("10.0.1.4", "10.0.1.***"),
            ("10.0.1.4,10.0.1.104", "10.0.1.***,10.0.1.***"),
            ("svr-web-01", "svr-web-01"),
        ],
    )
    def test_mask_ip_tail(self, value: str, expected: str) -> None:
        assert rd.mask_ip(value) == expected


# --- 결과 요약 (§3.5.1 · §3.5.2) --------------------------------------------------


class TestSummarizeResult:
    SQL = (
        "SELECT sevrHostName AS 호스트명, rspblBrnName AS 담당부점, rspblPsnEmnm AS 담당자, "
        "rspblPsnEmpid, acqsiAmt AS 금액, iPCtnt, byCtrcName, CONCAT(rspblPsnEmnm, '') AS 표시명 "
        "FROM TCDMSIF80 WHERE sevrHostName = 'svr-db-03'"
    )

    def _summary(
        self,
        policy: cat.ColumnPolicy,
        vault: rd.PiiVault,
        extra_cols: list[str] | None = None,
        extra_vals: list[Any] | None = None,
    ) -> dict:
        columns = [
            "호스트명",
            "담당부점",
            "담당자",
            "rspblPsnEmpid",
            "금액",
            "iPCtnt",
            "byCtrcName",
            "표시명",
        ] + (extra_cols or [])
        rows = [
            [
                "svr-db-03",
                "합성부점",
                _NAME2,
                _EMPID,
                10300000,
                "10.0.3.3",
                f"합성 구매계약 {_NAME2}",
                _NAME2,
            ]
            + (extra_vals or []),
            [
                "svr-db-04",
                "합성부점",
                _NAME,
                "T000004",
                10400000,
                "10.0.3.4",
                "합성 구매계약 04",
                _NAME,
            ]
            + (extra_vals or []),
        ]
        sources = rd.resolve_result_columns(columns, [self.SQL], policy.column_names())
        # 열 이름은 근거가 있을 때만 남는다 — 이 턴 프롬프트에 있는 말이면 그대로 남는다
        return rd.summarize_result(
            _result(columns, rows),
            sources=sources,
            policy=policy,
            vault=vault,
            prompt="svr-db-03 담당자·표시명·메모 알려줘",
        )

    def test_alias_and_expression_columns_resolve(self, policy: cat.ColumnPolicy) -> None:
        sources = rd.resolve_result_columns(
            ["호스트명", "담당자", "표시명", "rspblPsnEmpid", "모름"],
            [self.SQL],
            policy.column_names(),
        )
        assert sources["호스트명"] == ["sevrHostName"]
        assert sources["담당자"] == ["rspblPsnEmnm"]
        assert sources["표시명"] == ["rspblPsnEmnm"]  # 식 안의 컬럼 → 가장 엄격한 등급
        assert sources["rspblPsnEmpid"] == ["rspblPsnEmpid"]
        assert sources["모름"] == []

    def test_grades_shape_the_summary(self, policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
        summary = self._summary(policy, vault)
        cols = {c["name"]: c for c in summary["columns"]}
        assert cols["호스트명"]["log_policy"] == "general" and "svr-db-03" in _dump(
            cols["호스트명"]
        )
        assert cols["담당부점"]["log_policy"] == "general" and cols["담당부점"]["top_values"] == {
            "합성부점": 2
        }
        assert cols["담당자"] == {
            "name": "담당자",
            "source": ["rspblPsnEmnm"],
            "log_policy": "pii",
            "count": 2,
            "nulls": 0,
            "distinct": 2,
        }
        assert cols["금액"]["log_policy"] == "amount"
        assert cols["금액"]["sum"] == 20700000 and cols["금액"]["min"] == 10300000
        assert "values" not in cols["금액"] and "sample" not in cols["금액"]
        # 생성기 없는 경로의 IP 칸은 길이만 — 앞 세 옥텟도 싣지 않는다(plans/149 W5 3차)
        assert cols["iPCtnt"]["log_policy"] == "network" and "10.0.3" not in _dump(cols["iPCtnt"])
        assert "sample" not in cols["iPCtnt"] and "length" in cols["iPCtnt"]
        assert (
            cols["byCtrcName"]["log_policy"] == "free_text" and "sample" not in cols["byCtrcName"]
        )
        assert cols["표시명"]["log_policy"] == "pii"
        _no_person_values(_dump(summary))
        assert "10.0.3.3" not in _dump(summary)

    def test_unclassified_column_records_no_values(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        summary = self._summary(policy, vault, ["메모"], [f"{_NAME2} {_PHONE} {_EMAIL}"])
        cols = {c["name"]: c for c in summary["columns"]}
        assert cols["메모"]["log_policy"] == "unclassified"
        assert set(cols["메모"]) <= {"name", "source", "log_policy", "count", "nulls", "length"}
        _no_person_values(_dump(summary))
        # 「분류 필요 컬럼」은 별칭이 아니라 식에서 찾은 정책 밖 컬럼 이름이다(별칭은 값일 수 있다)
        assert rd.unclassified_columns(summary) == []
        sources = rd.resolve_result_columns(
            ["x"], ["SELECT ownerUserNm AS x FROM T"], policy.column_names()
        )
        unknown = rd.summarize_result(
            _result(["x"], [["v"]]), sources=sources, policy=policy, vault=vault
        )
        assert rd.unclassified_columns(unknown) == ["ownerUserNm"]

    def test_pii_values_are_harvested_to_memory(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        self._summary(policy, vault)
        assert vault.hits(f"담당 {_NAME2}") == 1  # 카나리아 밖 이름도 수집돼 막힌다
        assert vault.hits(_EMPID) >= 1

    def test_general_column_carrying_pii_value_is_demoted(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        # 별칭이 일반 컬럼(부점명)으로 해석됐는데 값이 사람 이름이다 → 값 미기록으로 강등
        columns = ["담당자", "담당부점"]
        sql = "SELECT rspblPsnEmnm AS 담당자, rspblBrnName AS 담당부점 FROM TCDMSIF80"
        rows = [[_NAME2, _NAME2], [_NAME, "합성부점"]]
        sources = rd.resolve_result_columns(columns, [sql], policy.column_names())
        summary = rd.summarize_result(
            _result(columns, rows), sources=sources, policy=policy, vault=vault
        )
        cols = {c["name"]: c for c in summary["columns"]}
        assert cols["담당부점"].get("demoted") == "pii_value_match"
        _no_person_values(_dump(summary))

    def test_general_value_matching_pii_regex_is_demoted(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        columns = ["호스트명"]
        sql = "SELECT sevrHostName AS 호스트명 FROM TCDMSIF80"
        summary = rd.summarize_result(
            _result(columns, [[_EMAIL]]),
            sources=rd.resolve_result_columns(columns, [sql], policy.column_names()),
            policy=policy,
            vault=vault,
        )
        assert summary["columns"][0].get("demoted") == "pii_value_match"
        _no_person_values(_dump(summary))

    def test_unavailable_result_passes_through_reason_safely(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        out = rd.summarize_result(
            {
                "status": "unavailable",
                "columns": [],
                "rows": [],
                "total_rows": 0,
                "truncated": False,
                "reason": f"http 500: {_NAME} 오류",
            },
            sources={},
            policy=policy,
            vault=vault,
        )
        assert out["status"] == "unavailable" and _NAME not in _dump(out)


# --- SQL·문구 가림 (§3.5.3) -------------------------------------------------------


class TestRedactSql:
    def test_structure_kept_and_person_literals_masked(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        sql = (
            f"SELECT sevrHostName FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' "
            f"AND rspblPsnEmpid IN ('{_EMPID}', 'T000004') AND byCtrcName LIKE '%{_PHONE}%' "
            f"AND sevrHostName = 'svr-db-03' AND manmenCtrcEndYmd >= '20261001' "
            f"AND acqsiYmd = DATE_FORMAT(CURDATE(), '%Y%m%d') AND iPCtnt = '10.0.3.3' LIMIT 1000"
        )
        out = rd.redact_sql(sql, policy=policy, vault=vault, prompt="svr-db-03 담당자가 누구야?")
        _no_person_values(out)
        assert "rspblPsnEmnm = '<가림>'" in out  # 구조는 남는다
        assert "'svr-db-03'" in out and "'20261001'" in out and "'%Y%m%d'" in out
        assert "'10.0.3.***'" in out and "10.0.3.3" not in out
        assert out.count("<가림>") >= 3

    @pytest.mark.parametrize(
        "sql",
        [
            f"SELECT * FROM TCDMSIF80 WHERE '{_NAME2}' = rspblPsnEmnm",  # 뒤집힌 비교
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm <> '{_NAME2}'",  # 부정 비교
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm NOT LIKE '{_NAME2}%'",
            f"SELECT * FROM TCDMSIF80 WHERE UPPER(rspblPsnEmnm) = UPPER('{_NAME2}')",
            f'SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = "{_NAME2}"',  # MariaDB 큰따옴표 = 문자열
        ],
    )
    def test_default_deny_catches_forms_the_extractor_misses(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str
    ) -> None:
        _no_person_values(rd.redact_sql(sql, policy=policy, vault=vault, prompt=""))

    def test_prompt_words_and_identifier_quotes_survive(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        sql = (
            "SELECT \"sevrHostName\" FROM TCDMSIF80 WHERE cnfgItemDescCtnt LIKE '%통합인증%' "
            "AND sevrHostName || '-x' = '0'"
        )
        out = rd.redact_sql(
            sql,
            policy=policy,
            vault=vault,
            prompt="자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘",
        )
        assert '"sevrHostName"' in out  # 방언 함정의 증거(큰따옴표 식별자)
        assert "'%통합인증%'" in out  # 프롬프트에 그대로 있는 말
        assert "'-x'" not in out  # 근거 없는 리터럴은 가린다

    def test_vault_values_scrubbed_even_in_kept_literals(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        vault.add(_NAME2)
        out = rd.redact_sql(
            f"SELECT 1 FROM TCDMSIF80 WHERE sevrHostName = '{_NAME2}' /* {_NAME2} */",
            policy=policy,
            vault=vault,
            prompt="",
        )
        _no_person_values(out)

    def test_error_text_masks_literals_keeps_identifiers(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        sql = f"SELECT sevrHostNm FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}'"
        error = (
            f"(1054, \"Unknown column 'sevrHostNm' in 'field list'\") near '{_NAME2}' LIMIT "
            f"1000' {_EMAIL} {_PHONE} " + "x" * 2000
        )
        out = rd.redact_text(error, vault=vault, sql=sql, prompt="")
        _no_person_values(out)
        assert "'sevrHostNm'" in out and "'field list'" in out and "1054" in out
        assert len(out) <= rd.ERROR_TEXT_MAX + 1


# --- 누출 관문 (§3.5.5) -----------------------------------------------------------


class TestLeakGate:
    def _gate(self, policy: cat.ColumnPolicy, vault: rd.PiiVault) -> rd.LeakGate:
        return rd.LeakGate(
            policy=policy,
            vault=vault,
            user_values={
                "login_id": "5488923",
                "os_user": "cptkang",
                "host": "cpt-macbook",
                "home": "/Users/cptkang",
                "dsn_user": "itam_ro",
                "dsn_password": "itam_ro_pass_2024",
            },
        )

    def test_clean_files_pass(self, policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
        files = {
            "run.json": _dump({"login_user": "5***", "operator": "c***", "path": "~/x"}),
            "trace.jsonl": _dump({"id": "ITAM-06", "result": {"columns": [{"count": 1}]}}) + "\n",
            "schema_catalog.yaml": "tables:\n  TCDMSIF80:\n    columns: []\n",
            "report.md": "# 리포트\n통과 1건\n",
        }
        assert self._gate(policy, vault).check(files) == []

    @pytest.mark.parametrize(
        "file, text, rule",
        [
            ("trace.jsonl", _dump({"id": "x", "result": {"sample": [_NAME]}}) + "\n", "canary"),
            ("trace.jsonl", _dump({"id": "x", "sql": "WHERE a = 'T000017'"}) + "\n", "canary"),
            ("report.md", f"| x | {_PHONE} |\n", "pii_regex"),
            ("report.md", f"메일 {_EMAIL}\n", "pii_regex"),
            ("schema_catalog.yaml", "note: CREATE TABLE TCDMSIF80 (a int)\n", "schema_form"),
            (
                "trace.jsonl",
                _dump({"schema_context": {"note": "information_schema.columns"}}) + "\n",
                "schema_form",
            ),
            ("run.json", _dump({"login_user": "5488923"}), "user_info"),
            ("run.json", _dump({"path": "/Users/cptkang/AIOps"}), "user_info"),
            ("run.json", _dump({"dsn": "mariadb://itam_ro:x@h/db"}), "user_info"),
            (
                "trace.jsonl",
                _dump({"e": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI1NDg4OTIzIn0.sig"}) + "\n",
                "user_info",
            ),
        ],
    )
    def test_violations_report_location_not_value(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault, file: str, text: str, rule: str
    ) -> None:
        violations = self._gate(policy, vault).check({file: text})
        assert [v["rule"] for v in violations] == [rule]
        assert violations[0]["file"] == file
        dumped = _dump(violations)
        for secret in (
            _NAME,
            "T000017",
            _PHONE,
            _EMAIL,
            "5488923",
            "/Users/cptkang",
            "itam_ro",
            "eyJhbGci",
            "CREATE TABLE",
            "information_schema",
        ):
            assert secret not in dumped

    def test_executed_sql_may_mention_information_schema(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        line = _dump({"executed_sqls": [{"sql": "SELECT * FROM information_schema.columns"}]})
        assert self._gate(policy, vault).check({"trace.jsonl": line + "\n"}) == []

    def test_harvested_values_are_checked(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        vault.add(_NAME2)
        violations = self._gate(policy, vault).check({"trace.jsonl": _dump({"a": [_NAME2]}) + "\n"})
        assert [(v["rule"], v["record"], v["field"]) for v in violations] == [
            ("pii_value", 1, "a[0]")
        ]

    def test_write_gated_moves_nothing_on_failure(
        self, tmp_path: Path, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        out = tmp_path / "run"
        ok, violations = rd.write_gated(
            out, {"run.json": _dump({"x": _NAME}), "report.md": "ok\n"}, self._gate(policy, vault)
        )
        assert not ok and violations
        assert sorted(p.name for p in out.iterdir()) == ["leak_check.json"]
        assert _NAME not in (out / "leak_check.json").read_text(encoding="utf-8")

    def test_write_gated_writes_all_plus_leak_check(
        self, tmp_path: Path, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        out = tmp_path / "run"
        ok, violations = rd.write_gated(
            out, {"run.json": "{}", "report.md": "ok\n"}, self._gate(policy, vault)
        )
        assert ok and violations == []
        assert sorted(p.name for p in out.iterdir()) == ["leak_check.json", "report.md", "run.json"]
        assert json.loads((out / "leak_check.json").read_text(encoding="utf-8"))["passed"] is True


# --- 정규식 상한 (docs/18 2026-08-19 ReDoS) ---------------------------------------


def test_redaction_is_bounded_on_20kb_input(policy: cat.ColumnPolicy) -> None:
    vault = rd.PiiVault.from_policy(policy)
    vault.add(_NAME2)
    big_sql = "SELECT sevrHostName FROM TCDMSIF80 WHERE " + " OR ".join(
        f"sevrHostName = 'svr-{i:05d}'" for i in range(700)
    )
    big_text = ("'" + "a" * 19_000 + " near '" + "'" * 500)[:20_000]
    gate = rd.LeakGate(policy=policy, vault=vault, user_values={"login_id": "5488923"})
    rd.redact_sql(
        "SELECT 1 FROM t WHERE a = '1'", policy=policy, vault=vault
    )  # 모듈 적재 비용 제외
    # 긴 영숫자 연속열 — `scan_pii` 이메일 규칙이 제곱 시간이 되는 입력(20KB 5.7초 실측)
    long_run = ("a.b" * 7000)[:20_000]
    for call in (
        lambda: rd.redact_sql(big_sql[:20_000], policy=policy, vault=vault, prompt=""),
        lambda: rd.redact_sql(long_run, policy=policy, vault=vault, prompt=""),
        lambda: rd.redact_text(big_text, vault=vault, sql=big_sql, prompt=""),
        lambda: gate.check({"report.md": big_text}),
        lambda: gate.check({"report.md": "1" * 20_000}),
    ):
        started = time.perf_counter()
        call()
        # 20KB 입력 200ms 이내(plans/135 W2 50ms → plans/145 완화) — 중앙값 31ms인데 병렬 부하
        # 지터로 51.7ms 실패가 났다. 막으려는 제곱 시간(20KB 5.7초 실측)과는 여전히 25배 이상 차이
        assert (time.perf_counter() - started) < 0.2


# --- 보안 리뷰 재현(2026-10-06 · 리뷰 12건 중 누출 경로) -----------------------------


class TestReviewFindings:
    """독립 보안 리뷰가 재현한 누출 경로 — 고치기 전에 실패를 먼저 확인했다(W2 TDD 연장)."""

    def test_alias_expression_wins_over_catalog_name_and_unions_all_sqls(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        known = policy.column_names()
        # (a) 실패한 첫 시도와 성공한 재시도가 같은 별칭에 다른 컬럼 → 가장 엄격한 쪽
        sqls = [
            "SELECT sevrHostName AS 서버 FROM TCDMSIF8O",
            "SELECT rspblPsnEmnm AS 서버 FROM TCDMSIF80",
        ]
        assert "rspblPsnEmnm" in rd.resolve_result_columns(["서버"], sqls, known)["서버"]
        # (b) 별칭이 카탈로그 이름과 같아도 식이 먼저다
        for sql in (
            "SELECT rspblPsnEmnm AS sevrHostName FROM TCDMSIF80",
            "SELECT CONCAT(sevrHostName, ' ', rspblPsnEmnm) AS sevrHostName FROM TCDMSIF80",
            "SELECT rspblPsnEmnm sevrHostName FROM TCDMSIF80",
        ):
            sources = rd.resolve_result_columns(["sevrHostName"], [sql], known)
            summary = rd.summarize_result(
                _result(["sevrHostName"], [[_NAME2]]), sources=sources, policy=policy, vault=vault
            )
            assert summary["columns"][0]["log_policy"] == "pii", sql
            _no_person_values(_dump(summary))
        # (c) 정책에 없는 컬럼이 섞인 식은 미분류로 올린다
        sources = rd.resolve_result_columns(
            ["서버"], ["SELECT CONCAT(sevrHostName, '/', ownerUserNm) AS 서버 FROM T"], known
        )
        summary = rd.summarize_result(
            _result(["서버"], [[f"svr-db-03/{_NAME2}"]]),
            sources=sources,
            policy=policy,
            vault=vault,
        )
        assert summary["columns"][0]["log_policy"] == "unclassified"
        _no_person_values(_dump(summary))

    def test_gate_checks_whole_leaf_and_decoded_values(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        gate = rd.LeakGate(policy=policy, vault=vault, user_values={})
        long_leaf = {
            "executed_sqls": [{"sql": "SELECT 1 " + "x " * 10100 + f" -- {_NAME} {_PHONE}"}]
        }
        assert {v["rule"] for v in gate.check({"trace.jsonl": _dump(long_leaf) + "\n"})} >= {
            "canary",
            "pii_regex",
        }
        escaped = {"sql": "... IN (\nT000003)"}
        assert gate.check({"trace.jsonl": _dump(escaped) + "\n"})
        import yaml as _yaml

        folded = _yaml.safe_dump(
            {"meaning": "설명 " * 30 + "010 1234 5678"}, allow_unicode=True, width=40
        )
        assert gate.check({"schema_catalog.yaml": folded})

    def test_long_sql_tail_is_scrubbed(self, policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
        sql = (
            "SELECT 1 FROM TCDMSIF80 WHERE byCtrcName IN ("
            + ",".join("'a'" for _ in range(6000))
            + f") -- {_PHONE} {_EMAIL}"
        )
        _no_person_values(rd.redact_sql(sql, policy=policy, vault=vault))

    @pytest.mark.parametrize(
        "sql",
        [
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' -- {_NAME2} 담당 서버",
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' /* {_NAME2} */",
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' # {_NAME2}",
            f"-- don't touch\nSELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}'",
            f"SELECT * FROM TCDMSIF80 /* owner's name */ WHERE rspblPsnEmnm = '{_NAME2}'",
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = `{_NAME2}`",
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = {_NAME2}",
            "SELECT * FROM TCDMSIF80 WHERE rspblPsnEmpid IN ('5488923', '1234567')",
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = '%Y{_NAME2}'",
        ],
    )
    def test_sql_comment_backtick_unquoted_and_numeric_holes(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str
    ) -> None:
        out = rd.redact_sql(sql, policy=policy, vault=vault, prompt="")
        _no_person_values(out)
        assert "5488923" not in out and "1234567" not in out

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM TCDMSIF80 WHERE iPCtnt <> '10.0.3.3'",
            "SELECT * FROM TCDMSIF80 WHERE '10.0.3.3' = iPCtnt",
            "SELECT * FROM TCDMSIF80 WHERE INSTR(iPCtnt, '10.0.3.3') > 0",
        ],
    )
    def test_ip_tail_always_masked(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str
    ) -> None:
        assert "10.0.3.3" not in rd.redact_sql(sql, policy=policy, vault=vault)

    def test_gate_paths_never_carry_value_keys(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault, tmp_path: Path
    ) -> None:
        vault.add("parkseojun")
        gate = rd.LeakGate(policy=policy, vault=vault, user_values={})
        record = {"result": {"columns": [{"top_values": {"T000003": 2, "parkseojun": 3}}]}}
        violations = gate.check({"trace.jsonl": _dump(record) + "\n"})
        assert violations
        text = _dump(violations)
        assert "T000003" not in text and "parkseojun" not in text
        ok, _v = rd.write_gated(tmp_path / "r", {"trace.jsonl": _dump(record) + "\n"}, gate)
        assert not ok and "T000003" not in (tmp_path / "r" / "leak_check.json").read_text("utf-8")

    def test_general_column_with_late_pii_value_is_demoted(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        columns = ["호스트명"]
        rows = [[f"svr-{i:020d}"] for i in range(900)] + [["T000005"]]
        sources = rd.resolve_result_columns(
            columns, ["SELECT sevrHostName AS 호스트명 FROM TCDMSIF80"], policy.column_names()
        )
        summary = rd.summarize_result(
            _result(columns, rows), sources=sources, policy=policy, vault=vault
        )
        assert summary["columns"][0].get("demoted") == "pii_value_match"
        assert "T000005" not in _dump(summary)

    def test_oracle_detail_keys_from_non_general_system_column_are_counted(self) -> None:
        from scripts.itam_bench import judge as jd

        detail = {
            "compare": "keyset",
            "missing": [["svr-db-01"]],
            "extra": [[f"합성 구매계약 {_NAME2}"]],
            "header": ["host", _NAME2],
        }
        clean = jd.sanitize_detail(detail, value_grade=None, keys_allowed=False)
        assert clean["missing"] == 1 and clean["extra"] == 1
        assert _NAME2 not in _dump(clean)

    @pytest.mark.parametrize(
        "text",
        [
            f"(1064, \"... near 'WHERE rspblPsnEmnm = '{_NAME2}' LIMIT 1000' at line 3\")",
            f"http 500: near 'x = '{_NAME2}' and' at line 1",
            "(1292, \"Truncated incorrect DOUBLE value: '5488923'\")",
        ],
    )
    def test_error_text_without_sql(self, vault: rd.PiiVault, text: str) -> None:
        out = rd.redact_text(text, vault=vault)
        _no_person_values(out)
        assert "5488923" not in out

    def test_value_aliases_are_not_recorded(
        self, policy: cat.ColumnPolicy, vault: rd.PiiVault
    ) -> None:
        sql = f"SELECT SUM(rspblPsnEmnm = '{_NAME2}') AS `{_NAME2}` FROM TCDMSIF80"
        sources = rd.resolve_result_columns([_NAME2], [sql], policy.column_names())
        summary = rd.summarize_result(
            _result([_NAME2], [["3"]]),
            sources=sources,
            policy=policy,
            vault=vault,
            prompt="담당자별 서버 수",
        )
        _no_person_values(_dump(summary))
        assert summary["columns"][0]["name"].startswith("열#")
        kept = rd.summarize_result(
            _result(["담당자"], [[_NAME]]),
            sources={"담당자": ["rspblPsnEmnm"]},
            policy=policy,
            vault=vault,
            prompt="svr-db-03 담당자가 누구야?",
        )
        assert kept["columns"][0]["name"] == "담당자"  # 프롬프트에 있는 말은 이름으로 남는다

    def test_format_regex_is_linear_and_strict(self) -> None:
        started = time.perf_counter()
        rd.redact_text("'" + "%Y" * 9000 + "a'", vault=rd.PiiVault())
        assert time.perf_counter() - started < 0.05

    def test_mask_dsn_with_unencoded_password(self) -> None:
        for dsn in (
            "mariadb://itam_ro:p/ss@localhost:3307/INST1",
            "mariadb://itam_ro:p#ss@localhost:3307/INST1",
            "mariadb://itam_ro:p?ss@localhost:3307/INST1",
        ):
            masked = rd.mask_dsn(dsn)
            assert "itam_ro" not in masked and "p/ss" not in masked and "p#ss" not in masked
            assert "p?ss" not in masked and masked.endswith("@localhost:3307/INST1")
        values = rd.dsn_credentials("mariadb://itam_ro:p/ss@localhost:3307/INST1")
        assert values == ("itam_ro", "p/ss")

    def test_short_or_ascii_vault_values_match_whole_words_only(self) -> None:
        vault = rd.PiiVault()
        vault.add("1006")
        vault.add("kim")
        assert vault.harvested_hits("run 20261006-120000 · LIMIT 1000 · kimchi") == 0
        assert vault.harvested_hits("담당 kim") == 1

    def test_pii_inside_long_runs_is_still_found(self, vault: rd.PiiVault) -> None:
        text = "x-" * 100 + _PHONE
        assert _PHONE not in rd.redact_text(text, vault=vault, limit=10_000)
        gate = rd.LeakGate(policy=cat.load_policy(POLICY_PATH), vault=vault, user_values={})
        assert gate.check({"report.md": text})
