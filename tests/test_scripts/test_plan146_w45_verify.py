"""plans/146 W4·W5 독립 검증 (verifier · 2026-10-08).

LLM 0 · DB 0. 오라클·탐침·지식 원천의 정적 계약과, 폐쇄망 keyset 오라클 판정 기록의 키 값
누출 경계(`_oracle_record`의 키 등급 판정)를 확인한다. 남은 결함은 `xfail(strict=True)`로 남긴다 —
고쳐지면 XPASS로 실패해 표지를 걷게 한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import CLOSED_POLICY_PATH, CLOSED_SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.scenario import oracle as oracle_mod

REPO = Path(__file__).resolve().parents[2]
ORACLES = REPO / "testdata" / "scenarios" / "oracles"
PROBE_PATH = REPO / "testdata" / "itam_bench" / "scenarios.closed.probe.yaml"
KNOWLEDGE = REPO / "testdata" / "itam_bench" / "closed" / "knowledge"

CONVERTED = ("107", "108", "109", "110", "111", "112", "116")
PROBES = ("P01", "P02", "P03", "P04", "P05", "P06", "P07A", "P07B", "P08", "P09")


def _body(path: Path) -> str:
    """머리 주석을 뺀 SQL 본문."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


@pytest.fixture(scope="module")
def closed_policy() -> cat.ColumnPolicy:
    return cat.load_policy(CLOSED_POLICY_PATH)


@pytest.fixture(scope="module")
def closed_specs(closed_policy: cat.ColumnPolicy) -> dict[str, dict[str, Any]]:
    scenarios = cat.load_scenarios(CLOSED_SCENARIOS_PATH, closed_policy)
    return {s.id: t.oracle for s in scenarios for t in s.turns if t.oracle}


# --- W5 (1) 오라클 정본 --------------------------------------------------------------


class TestClosedOracles:
    @pytest.mark.parametrize("num", CONVERTED)
    def test_no_value_literals_no_now_functions(self, num: str) -> None:
        body = _body(ORACLES / f"ITAM-{num}.mariadb.sql")
        literals = re.findall(r"'[^']*'", body)
        assert set(literals) <= {"'%Y%m%d'", "''"}, literals
        assert not re.search(r"(?i)\b(curdate|now|sysdate|current_date)\b", body)
        assert "`" in body  # 한글 식별자 백틱(MariaDB)

    @pytest.mark.parametrize("num", ("108", "109", "110"))
    def test_date_queries_use_anchor_placeholders(self, num: str) -> None:
        body = _body(ORACLES / f"ITAM-{num}.mariadb.sql")
        assert ":today" in body

    def test_plan_bases(self) -> None:
        """G-4: 110은 `취득년월일`(경계 포함 `<=`) · 111은 `tcdmsif80` · 108은 분기 식."""
        b110 = _body(ORACLES / "ITAM-110.mariadb.sql")
        assert "`tcdmsif80`" in b110 and "`취득년월일` <=" in b110 and "5 YEAR" in b110
        assert "`경과년수`" not in b110
        b111 = _body(ORACLES / "ITAM-111.mariadb.sql")
        assert "`tcdmsif80`" in b111 and "`tcdmsif41`" not in b111
        assert "ORDER BY `취득금액` DESC" in b111
        b108 = _body(ORACLES / "ITAM-108.mariadb.sql")
        assert "QUARTER(:today)" in b108 and "BETWEEN" in b108
        b109 = _body(ORACLES / "ITAM-109.mariadb.sql")
        assert b109.count("BETWEEN :today_ymd AND") == 2 and "\n   OR " in b109

    def test_rendered_placeholders_are_quoted(self) -> None:
        sql = oracle_mod.render_sql(
            (ORACLES / "ITAM-108.mariadb.sql").read_text(encoding="utf-8"),
            anchor_at="2026-12-31T10:00:00+09:00", run_id="v", scenario_id="ITAM-108-t1",
        )
        assert ":today" not in sql and "QUARTER('2026-12-31')" in sql

    def test_specs_converted_exactly(self, closed_specs: dict[str, dict[str, Any]]) -> None:
        assert sorted(closed_specs) == [f"ITAM-{n}" for n in CONVERTED]
        assert closed_specs["ITAM-107"]["compare"] == "value"
        assert {closed_specs[f"ITAM-{n}"]["compare"] for n in ("108", "109", "110")} == {"keyset"}
        assert closed_specs["ITAM-111"]["compare"] == "argmax"
        assert closed_specs["ITAM-111"]["top"] == 3
        assert {closed_specs[f"ITAM-{n}"]["compare"] for n in ("112", "116")} == {"count"}

    def test_value_count_rows_ok_reads_n_sum(self, closed_specs: dict[str, dict[str, Any]]) -> None:
        """107 이탈 판단 근거 — 목록 답은 오라클 `n` 합(전체 수)과 행 수로 대조된다."""
        spec = closed_specs["ITAM-107"]
        outcome = {"status": "ok", "rows_by_db": {"itam": [{"n": 3}]}}
        listed = {"status": "ok", "columns": ["h"], "rows": [{"h": "a"}, {"h": "b"}, {"h": "c"}]}
        verdict, _detail, mode = jd.evaluate(spec, outcome, listed, count_rows_ok=True)
        assert (verdict, mode) == ("pass", "count_fallback")
        scalar = {"status": "ok", "columns": ["server_count"], "rows": [{"server_count": "3"}]}
        assert jd.evaluate(spec, outcome, scalar, count_rows_ok=True)[0] == "pass"

    def test_host_key_covers_run3_aliases(self, closed_specs: dict[str, dict[str, Any]]) -> None:
        names = {str(n).casefold() for n in closed_specs["ITAM-108"]["key"][0]}
        assert {"server_hostname", "server_host"} <= names


# --- W5 (2) 탐침 ------------------------------------------------------------------------


class TestProbes:
    @pytest.mark.parametrize("pid", PROBES)
    def test_probe_returns_rows_and_only_scenario_keyword(self, pid: str) -> None:
        body = _body(ORACLES / f"ITAM-146-{pid}.mariadb.sql")
        assert not re.search(r"(?i)\bcount\s*\(\s*\*", body)  # 한 행 COUNT(*) 모양 아님
        assert re.search(r"(?i)\blimit\s+\d+\s*$", body.strip())
        literals = set(re.findall(r"'[^']*'", body))
        assert literals <= {"'%통합인증%'", "'%'"}, literals

    def test_probe_file_is_check_oracle_only(
        self, closed_policy: cat.ColumnPolicy, closed_specs: dict[str, dict[str, Any]]
    ) -> None:
        scenarios = cat.load_scenarios(PROBE_PATH, closed_policy)
        assert [s.id for s in scenarios] == [f"ITAM-146-{p}" for p in PROBES]
        assert all(s.category == "probe" for s in scenarios)
        assert all(t.oracle and t.oracle["compare"] == "count" for s in scenarios for t in s.turns)
        assert not any(spec["id"].startswith("ITAM-146-P") for spec in closed_specs.values())


# --- W4 지식 원천 -----------------------------------------------------------------------


class TestKnowledgeSources:
    def _items(self, name: str) -> list[dict[str, Any]]:
        return yaml.safe_load((KNOWLEDGE / name).read_text(encoding="utf-8"))["items"]

    def test_k2_three_only_and_no_k8(self) -> None:
        ids = [i["id"] for i in self._items("examples.yaml") if i.get("status") == "active"]
        assert len(ids) == 3
        tables = [tuple(i["tables"]) for i in self._items("examples.yaml")]
        assert tables == [("tcdmsif79",), ("tcdmsif72",), ("tcdmsif72",)]
        assert not (KNOWLEDGE / "query_templates.yaml").exists()

    @pytest.mark.parametrize(
        "name", ("guide.yaml", "prompt_section.yaml", "descriptions.yaml", "examples.yaml")
    )
    def test_text_has_no_cycle_history(self, name: str) -> None:
        for item in self._items(name):
            for field in ("text", "description", "question"):
                text = str(item.get(field) or "")
                assert not re.search(r"\d+회차|2026\d{4}-\d{6}", text), (item["id"], field)

    def test_w4_items_present(self) -> None:
        guide = {i["id"]: i["text"] for i in self._items("guide.yaml")}
        assert "`활성화여부`·`자산상태구분명` 칸이 없다" in guide["g03-server"]
        assert "`tcdmsif72`를 조인하지 않고" in guide["g03-server"]
        assert "`tcdmsif78`" in guide["g05-asset-maintenance"]
        assert "(추정)" in guide["g05-asset-maintenance"]
        assert "`용도내용`은 서비스 단서로 쓰지 않는다" in guide["g06-service"]
        rules = {i["id"]: i["text"] for i in self._items("prompt_section.yaml")}
        assert "일반어" in rules["s10-name-match"]
        assert "항진 조건도 쓰지 않는다" in rules["s04-code-columns"]
        assert "관측 DB" in rules["s09-utilization"]
        desc = {i["id"]: i for i in self._items("descriptions.yaml")}
        for key in ("tcdmsif80.자산상태구분", "tcdmsif52.업무명", "tcdmsgt82.어플리케이션명"):
            assert desc[key]["evidence"] == "20261007-174622"


# --- 값 누출 경계 — 폐쇄망 keyset 판정 기록 ----------------------------------------------


def _record(
    spec: dict[str, Any], result: dict[str, Any], oracle_rows: list[dict[str, Any]],
    policy: cat.ColumnPolicy,
) -> dict[str, Any]:
    outcome = {"status": "ok", "rows_by_db": {"itam": oracle_rows}}
    verdict, detail, mode = jd.evaluate(spec, outcome, result)
    names = rd.result_column_names(result)
    vault = rd.PiiVault.from_policy(policy)
    summary = rd.summarize_result(
        result, sources={name: [] for name in names}, policy=policy, vault=vault,
    )
    ctx = SimpleNamespace(policy=policy, vault=vault)
    turn = SimpleNamespace(query="이번 분기에 유지보수 계약이 끝나는 서버 있어?")
    return cli._oracle_record(  # type: ignore[arg-type]
        (spec, outcome, detail, mode, verdict), names, summary, [], turn, ctx,
    )


_ORACLE_HOSTS = [{"서버호스트명": f"realhost{i:02d}"} for i in range(12)]


class TestOracleKeyLeakBoundary:
    def test_unclassified_system_key_records_counts_only(
        self, closed_specs: dict[str, dict[str, Any]], closed_policy: cat.ColumnPolicy
    ) -> None:
        """시스템 결과에 `서버호스트명`(폐쇄망 정책 밖)이 있으면 키는 건수로만 남는다."""
        result = {"status": "empty", "columns": ["서버호스트명"], "rows": []}
        rec = _record(closed_specs["ITAM-108"], result, _ORACLE_HOSTS, closed_policy)
        assert rec["verdict"] == "fail" and rec["keys_recorded"] is False
        assert "realhost" not in repr(rec)

    def test_unresolved_system_key_must_not_record_oracle_hosts(
        self, closed_specs: dict[str, dict[str, Any]], closed_policy: cat.ColumnPolicy
    ) -> None:
        """키 열을 못 찾으면 닫힌 쪽 — 검증 시작 시점(교정 전 작업 트리)에는 `keys_allowed`가 True로
        남아 `missing`에 오라클 호스트명 10개가 실렸다(재현). 145 교정 2차(`entry is None` → 건수만)
        뒤 회귀 가드."""
        # 3회차 ITAM-101이 실제로 쓴 별칭(`server_hostname`) · 0행
        result = {"status": "empty", "columns": ["server_hostname", "ip_address"], "rows": []}
        rec = _record(closed_specs["ITAM-108"], result, _ORACLE_HOSTS, closed_policy)
        assert rec["verdict"] == "fail"
        assert "realhost" not in repr(rec), rec["detail"].get("missing")

    def test_argmax_empty_system_must_not_record_oracle_top_keys(
        self, closed_specs: dict[str, dict[str, Any]], closed_policy: cat.ColumnPolicy
    ) -> None:
        """argmax 0행 — `oracle_top` 키(호스트명 3개)도 같은 경계(회귀 가드)."""
        rows = [{"서버호스트명": f"realhost{i:02d}", "취득금액": str(1000 - i)} for i in range(5)]
        result = {"status": "empty", "columns": [], "rows": []}
        rec = _record(closed_specs["ITAM-111"], result, rows, closed_policy)
        assert "realhost" not in repr(rec), rec["detail"].get("oracle_top")
