"""plans/145 교정 2차 — 감사·검증 재확인(2026-10-08) 지적의 회귀 고정.

- M-A: 끝·앞 점이 붙은 IPv4(`172.31.45.67.`)도 가린다 + 관문이 가짜 값 아닌 점 4묶음 IPv4 를 잡는다.
- M-B: 생성기 경로에서 비교 자리의 따옴표 없는 수(사번·금액)를 가짜 값으로 바꾸고 구조용 수는
  남긴다.
- keyset fail-closed: 키 열을 못 찾으면 키 값은 건수로만(생성기 있으면 가짜 값) 남는다.
- Minor-1: 1차 등록이 `strip()` 꼴·`_key_cell` 꼴도 등록한다.

실 LLM·DB 0. 가짜 값은 난수라 성질로 단언한다.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH, SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import substitute as sb
from scripts.itam_bench.substitute import FakeValues
from tests.test_scripts.test_itam_bench_driver import FakeClient, FakeTail, _result
from tests.test_scripts.test_itam_bench_driver import _ctx as _driver_ctx

_IP = "172.31.45.67"
_DOTTED = re.compile(r"\d+\.\d+\.\d+\.\d+")


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def scenarios(policy: cat.ColumnPolicy) -> dict[str, cat.Scenario]:
    return {s.id: s for s in cat.load_scenarios(SCENARIOS_PATH, policy)}


_CATALOG: dict[str, Any] = {
    "tables": {"TCDMSIF80": {"columns": [{"name": "sevrHostName"}], "relations": [], "key": []}},
    "same_key_groups": [],
}


def _grade_column(policy: cat.ColumnPolicy, grade: str) -> str:
    return next(
        name
        for columns in policy.tables.values()
        for name, found in columns.items()
        if found == grade
    )


def _generating(policy: cat.ColumnPolicy) -> tuple[rd.PiiVault, FakeValues, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=policy,
        catalog_doc={"tables": {}, "same_key_groups": []},
        user_values={"login_id": "5488923"},
        p1_draft=None,
    )


# --- M-A: 끝 점 IPv4 ---------------------------------------------------------------------------


class TestTrailingDotIp:
    @pytest.mark.parametrize(
        "text", [f"Lost connection to {_IP}.", f"a:{_IP}.b"]
    )
    def test_text_without_generator_masks_last_octet(self, text: str) -> None:
        out = rd.redact_text(text, vault=rd.PiiVault())
        assert _IP not in out and "172.31.45.***" in out

    @pytest.mark.parametrize("text", [f"Lost connection to {_IP}.", f"a:{_IP}.b"])
    def test_text_with_generator_issues_fake_ip(
        self, policy: cat.ColumnPolicy, text: str
    ) -> None:
        vault, fakes, _policy = _generating(policy)
        out = rd.redact_text(text, vault=vault)
        found = _DOTTED.findall(out)
        assert _IP not in out and found and all(fakes.is_fake(ip) for ip in found)

    def test_in_list_literal_is_masked_on_both_paths(self, policy: cat.ColumnPolicy) -> None:
        sql = f"SELECT a FROM t WHERE ipAddr IN ('{_IP}.', '10.0.0.1')"
        plain = rd.redact_sql(sql, policy=policy, vault=rd.PiiVault())
        assert _IP not in plain
        vault, _fakes, run_policy = _generating(policy)
        assert _IP not in rd.redact_sql(sql, policy=run_policy, vault=vault)

    def test_five_chunk_chain_masks_head(self) -> None:
        """교정 3차 M-A2 — 다섯 덩이 이상 점 연쇄는 앞 네 덩이를 가린다(2차 「IP 아님」 폐기)."""
        assert rd.redact_text("v1.2.3.4.5", vault=rd.PiiVault()) == "v1.2.3.***.5"


class TestGateFlagsLeftoverIp:
    @staticmethod
    def _trace(**fields: Any) -> dict[str, str]:
        return {"trace.jsonl": json.dumps({"id": "S1", "turn": 1, **fields}) + "\n"}

    def test_without_generator_raw_ip_is_a_violation(self, policy: cat.ColumnPolicy) -> None:
        gate = rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})
        hits = gate.check(self._trace(note="http://10.37.26.72:9099/sse"))
        assert [v["rule"] for v in hits] == ["pii_regex"] and hits[0]["field"] == "note"
        assert gate.check(self._trace(note="http://10.37.26.***:9099/sse")) == []

    def test_with_generator_only_fake_ips_pass(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        fake = fakes.fake_ip(_IP)
        assert gate.check(self._trace(note=f"to {fake}.")) == []
        assert [v["rule"] for v in gate.check(self._trace(note=f"to {_IP}."))] == ["pii_regex"]

    def test_reject_callback_does_not_refuse_ip_shapes(self, policy: cat.ColumnPolicy) -> None:
        """생성기 거부 콜백(`rules`)은 IP 검사를 품지 않는다 — 새 가짜 IP 를 거부하면 안 된다."""
        gate = rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})
        assert gate.rules("10.20.30.40", schema_section=False) == []


# --- M-B: 비교 자리 수 ------------------------------------------------------------------------


class TestCompareNumbers:
    @pytest.mark.parametrize(
        ("sql", "originals"),
        [
            ("SELECT * FROM t WHERE 담당자 = 20230001", ["20230001"]),
            ("SELECT * FROM t WHERE 담당자 <> -20230001", ["20230001"]),
            ("SELECT * FROM t WHERE 담당자 != 20230001", ["20230001"]),
            ("SELECT * FROM t WHERE 담당자 IN (20230001, 20230002)", ["20230001", "20230002"]),
            (
                "SELECT * FROM t WHERE 담당자 BETWEEN 20230001 AND 20230009",
                ["20230001", "20230009"],
            ),
            ("SELECT * FROM t WHERE 담당자 LIKE 2023000", ["2023000"]),
            ("SELECT CASE 담당자 WHEN 20230001 THEN 1 ELSE 0 END FROM t", ["20230001"]),
        ],
    )
    def test_value_numbers_become_fakes(
        self, policy: cat.ColumnPolicy, sql: str, originals: list[str]
    ) -> None:
        vault, fakes, run_policy = _generating(policy)
        out = rd.redact_sql(sql, policy=run_policy, vault=vault)
        for original in originals:
            assert original not in out
        assert all(fakes.is_fake(fakes.fake(original)) for original in originals)

    def test_amount_column(self, policy: cat.ColumnPolicy) -> None:
        amount = _grade_column(policy, "amount")
        vault, _fakes, run_policy = _generating(policy)
        out = rd.redact_sql(
            f"SELECT * FROM t WHERE {amount} > 98765432", policy=run_policy, vault=vault
        )
        assert "98765432" not in out and f"{amount} > " in out

    def test_same_value_quoted_and_bare_share_a_fake(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        out = rd.redact_sql(
            "SELECT * FROM t WHERE 담당자 = '20230001' OR 담당자 = 20230001",
            policy=run_policy,
            vault=vault,
        )
        fake = fakes.fake("20230001")
        assert "20230001" not in out and out.count(fake) == 2

    def test_structural_numbers_stay(self, policy: cat.ColumnPolicy) -> None:
        vault, _fakes, run_policy = _generating(policy)
        sql = (
            "SELECT TOP 5 ROUND(a, 2), SUBSTRING(b, 1, 3) FROM t "
            "WHERE c > NOW() - INTERVAL 30 DAY ORDER BY 1 LIMIT 10, 20 OFFSET 40 "
            "FETCH FIRST 10 ROWS ONLY"
        )
        assert rd.redact_sql(sql, policy=run_policy, vault=vault) == sql

    def test_general_column_comparisons_stay(self, policy: cat.ColumnPolicy) -> None:
        general = _grade_column(policy, "general")
        vault, _fakes, run_policy = _generating(policy)
        sql = f"SELECT * FROM t WHERE {general} = 1 AND {general} BETWEEN 3 AND 20230009"
        assert rd.redact_sql(sql, policy=run_policy, vault=vault) == sql

    def test_without_generator_numbers_are_unchanged(self, policy: cat.ColumnPolicy) -> None:
        sql = "SELECT * FROM t WHERE 담당자 = 20230001 AND x IN (20230002)"
        assert rd.redact_sql(sql, policy=policy, vault=rd.PiiVault()) == sql

    def test_numbers_inside_tokens_and_identifiers_untouched(
        self, policy: cat.ColumnPolicy
    ) -> None:
        vault, _fakes, run_policy = _generating(policy)
        out = rd.redact_sql(
            "SELECT t1.col2 FROM t1 WHERE t1.col2 = 1e5 -- = 20230001",
            policy=run_policy,
            vault=vault,
        )
        assert out.startswith("SELECT t1.col2 FROM t1 WHERE t1.col2 = 1e5 ")
        assert "20230001" not in out  # 주석 내용은 원래대로 가짜 값


# --- keyset fail-closed --------------------------------------------------------------------------


def _record(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, result: dict[str, Any]
) -> dict[str, Any]:
    spec = {
        "id": "X-1",
        "compare": "keyset",
        "key": [["sevrHostName", "호스트명", "서버명"]],
        "db_ids": ["itam"],
    }
    rows = [{"sevrHostName": f"realhost{i:02d}"} for i in range(4)]
    outcome = {"status": "ok", "rows_by_db": {"itam": rows}}
    verdict, detail, mode = jd.evaluate(spec, outcome, result)
    names = rd.result_column_names(result)
    summary = rd.summarize_result(
        result, sources={name: [name] for name in names}, policy=policy, vault=vault
    )
    ctx = SimpleNamespace(policy=policy, vault=vault)
    return cli._oracle_record(
        (spec, outcome, detail, mode, verdict), names, summary, [],
        SimpleNamespace(query=""), ctx,  # type: ignore[arg-type]
    )  # fmt: skip


class TestKeysetFailClosed:
    @pytest.mark.parametrize(
        "result",
        [
            {"status": "empty", "columns": [], "rows": []},
            {"status": "empty", "columns": ["server_hostname"], "rows": []},
        ],
    )
    def test_unresolved_key_column_records_counts_only(
        self, policy: cat.ColumnPolicy, result: dict[str, Any]
    ) -> None:
        rec = _record(policy, rd.PiiVault.from_policy(policy), result)
        assert rec["verdict"] == "fail" and rec["keys_recorded"] is False
        assert rec["detail"]["missing"] == 4
        assert "realhost" not in json.dumps(rec)

    def test_unresolved_key_column_with_generator_has_no_originals(
        self, policy: cat.ColumnPolicy
    ) -> None:
        vault, _fakes, run_policy = _generating(policy)
        rec = _record(run_policy, vault, {"status": "empty", "columns": [], "rows": []})
        assert rec["keys_recorded"] is False and "realhost" not in json.dumps(rec)

    def test_resolved_general_key_still_records_keys(self, policy: cat.ColumnPolicy) -> None:
        result = {"status": "ok", "columns": ["sevrHostName"], "rows": [{"sevrHostName": "a"}]}
        rec = _record(policy, rd.PiiVault.from_policy(policy), result)
        assert rec["keys_recorded"] is True and isinstance(rec["detail"]["missing"], list)


# --- Minor-1: 등록 정규화 ----------------------------------------------------------------------


class TestRegisterNormalizedForms:
    def test_stripped_form_is_an_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fakes = FakeValues()
        fakes.register([" ab1x\t"])
        draws = iter(["ab1x", "qq7z"])
        monkeypatch.setattr(sb, "_draw", lambda segment: next(draws))
        assert fakes.fake("zz9q") == "qq7z"
        assert fakes.collisions() == 0

    def test_turn_originals_include_key_cell_forms(
        self, policy: cat.ColumnPolicy, scenarios: dict[str, cat.Scenario]
    ) -> None:
        """`run_turn`의 1차 등록 재료에 결과·오라클 셀의 `_key_cell` 꼴(앞 0·반올림)도 든다."""
        scenario = scenarios["ITAM-03"]
        client = FakeClient(
            {("ITAM-03", 1): {"result": _result(["sevrHostName"], [[" 00123 "], [True]])}}
        )
        oracle_rows: dict[str, Any] = {"ITAM-03": [{"sevrHostName": 1.23456789}]}
        ctx = _driver_ctx(policy, _CATALOG, oracle_rows)
        pending = cli.run_turn(
            scenario, scenario.turns[0], 0, "itam-bench-ITAM-03-r0",
            client=client, audit=FakeTail({}), capture=FakeTail({}), ctx=ctx,
        )  # fmt: skip
        originals = [v for v in pending.originals() if not isinstance(v, bool)]
        assert {" 00123 ", "123", "1.234568"} <= {str(v) for v in originals}
        assert "True" not in originals  # 불리언 셀의 `_key_cell` 꼴은 넣지 않는다
