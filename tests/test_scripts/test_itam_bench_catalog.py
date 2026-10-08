"""ITAM 질의 벤치 — 시나리오·정책 로더 · 프롬프트 린트 · 오라클 정본 (plans/135 W0 · D-301 ①).

DB·LLM 에 붙지 않는다. `--check-oracle` 은 하네스 오라클 실행기의 `_open_client` 를 가짜로 바꿔
판정·출력 계약만 고정한다(실 샌드박스 실행은 스위트 밖 — 네트워크 가드).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.eval_text2sql import is_select_only
from scripts.itam_bench import POLICY_PATH, SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.scenario import oracle as oracle_mod
from src.config import SecurityConfig

_ROOT = Path(__file__).resolve().parents[2]
_TRANSCRIPT = _ROOT / "testdata" / "itam" / "schema.yaml"


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def scenarios(policy: cat.ColumnPolicy) -> list[cat.Scenario]:
    return cat.load_scenarios(SCENARIOS_PATH, policy)


def _write_scenarios(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text("version: 1\nscenarios:\n" + body, encoding="utf-8")
    return path


def _errors(tmp_path: Path, policy: cat.ColumnPolicy, body: str) -> list[str]:
    with pytest.raises(cat.CatalogError) as exc:
        cat.load_scenarios(_write_scenarios(tmp_path, body), policy)
    return exc.value.errors


_OK_TURN = """
  - id: ITAM-90
    title: t
    category: c
    env: [sandbox]
    key_columns: [sevrHostName]
    gold_tables: [TCDMSIF80]
    turns:
      - send: {query: "자산관리에 등록된 서버 목록 보여줘"}
        expect:
          db_ids: [itam]
          oracle: {id: ITAM-02, compare: count, db_ids: [itam]}
"""


# --- 컬럼 정책 ------------------------------------------------------------------


class TestPolicy:
    def test_policy_covers_transcript_columns_exactly(self, policy: cat.ColumnPolicy) -> None:
        transcript = yaml.safe_load(_TRANSCRIPT.read_text(encoding="utf-8"))
        for table, spec in transcript["tables"].items():
            assert set(policy.tables[table]) == {c["var"] for c in spec["columns"]}, table
        assert sum(len(c) for c in policy.tables.values()) == 77

    def test_transcript_sensitive_columns_are_not_general(self, policy: cat.ColumnPolicy) -> None:
        sensitive = yaml.safe_load(_TRANSCRIPT.read_text(encoding="utf-8"))["sensitive"]
        for entry in sensitive["pii"]:
            assert policy.grade(entry["column"]) == "pii"
        for name in sensitive["employee_identifiable"]:
            assert policy.grade(name) == "pii"
        for name in sensitive["amount"]:
            assert policy.grade(name) == "amount"
        for name in sensitive["contract_identifiable"]:
            assert policy.grade(name) == "free_text"

    def test_grade_is_case_insensitive_and_default_deny(self, policy: cat.ColumnPolicy) -> None:
        assert policy.grade("RSPBLPSNEMNM") == "pii"
        assert policy.grade("sevrhostname", "INST1.TCDMSIF80") == "general"
        assert policy.grade("no_such_column") == "unclassified"

    def test_canaries_count_without_returning_values(self, policy: cat.ColumnPolicy) -> None:
        assert policy.canary_hits("담당자 홍길동 · 사번 T000003") == 2
        assert policy.canary_hits("svr-db-03 · 2026-10-06T15:02:10") == 0

    def test_load_policy_rejects_unknown_grade_and_duplicate(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text("tables:\n  T1:\n    general: [a, A]\n    secret: [b]\n", encoding="utf-8")
        with pytest.raises(cat.CatalogError) as exc:
            cat.load_policy(path)
        text = " | ".join(exc.value.errors)
        assert "두 번" in text and "모르는 등급" in text

    @pytest.mark.parametrize(
        "name, comment, expected",
        [
            ("sysRegiUno", "", True),
            ("rspblPsnEmnm", "", True),
            ("RSPBL_PSN_EMPID", "", True),
            ("cmdtsUniqno", "", False),  # uniqno ≠ uno — 토큰 단위
            ("sevrHostName", "", False),  # 「Name」 부분 일치 오탐 금지(docs/18 2026-09-11)
            ("x1", "담당자 직원번호", True),
            ("x2", "담당 부점명", False),
        ],
    )
    def test_pii_suggestion_is_token_based(self, name: str, comment: str, expected: bool) -> None:
        assert cat.pii_suggestion(name, comment) is expected


# --- 프롬프트 린트 ---------------------------------------------------------------


class TestLint:
    @pytest.mark.parametrize(
        "prompt, needle",
        [
            ("TCDMSIF80 에서 서버 목록 보여줘", "식별자 `TCDMSIF80`"),
            ("manmenctrcendymd 가 이번 분기인 서버", "식별자 `manmenCtrcEndYmd`"),
            ("select 서버 목록", "SQL 용어 `select`"),
            ("서버 테이블에서 목록 보여줘", "SQL 용어 `테이블`"),
            ("두 개 조인해서 보여줘", "SQL 용어 `조인`"),
            ("홍길동 담당 서버 알려줘", "카나리아"),
            ("사번 T000003 담당 서버", "사번"),
            ("010-1234-5678 로 연락한 서버", "개인정보 규칙"),
        ],
    )
    def test_lint_rejects(self, policy: cat.ColumnPolicy, prompt: str, needle: str) -> None:
        idents = policy.column_names() | policy.table_names()
        problems = cat.lint_prompt(prompt, identifiers=idents, policy=policy)
        assert any(needle in p for p in problems), problems

    def test_lint_problem_text_does_not_echo_person_values(self, policy: cat.ColumnPolicy) -> None:
        problems = cat.lint_prompt("홍길동 T000003", identifiers=set(), policy=policy)
        assert problems and not any("홍길동" in p or "T000003" in p for p in problems)

    def test_lint_passes_user_wording(self, policy: cat.ColumnPolicy) -> None:
        idents = policy.column_names() | policy.table_names()
        for prompt in (
            "자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘",
            "6개월 안에 지원 종료(EOS)되는 서버 알려줘",
            "자산관리 시스템에서 호스트명이랑 IP를 '호스트(IP)' 형태로 붙여서 보여줘",
        ):
            assert cat.lint_prompt(prompt, identifiers=idents, policy=policy) == []


# --- 시나리오 로더 ---------------------------------------------------------------


class TestScenarioLoader:
    def test_real_catalog_loads(self, scenarios: list[cat.Scenario]) -> None:
        assert len(scenarios) == 22
        assert len({s.id for s in scenarios}) == 22
        by_id = {s.id: s for s in scenarios}
        # 사용자 예시 원문 · G-7 미응답 → 관측(정답 SQL 을 추정으로 만들지 않는다)
        first = by_id["ITAM-01"].turns[0]
        assert first.query == "자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘"
        assert first.oracle is None and first.observe and first.observe["no_data"] is True
        # 코드값 미확보(G-5) → 관측
        assert by_id["ITAM-21"].turns[0].oracle is None
        # 첫 턴 요청 본문은 query 하나(대상 DB 고정 주입 0)
        assert all(set(s.turns[0].send) == {"query"} for s in scenarios)

    def test_every_oracle_has_mariadb_canon(self, scenarios: list[cat.Scenario]) -> None:
        for scenario in scenarios:
            for turn in scenario.turns:
                if turn.oracle:
                    path = oracle_mod.ORACLE_DIR / f"{turn.oracle['id']}.mariadb.sql"
                    assert path.is_file(), (scenario.id, path)

    def test_turn_level_key_columns_override(self, scenarios: list[cat.Scenario]) -> None:
        multi = next(s for s in scenarios if s.id == "ITAM-22")
        assert multi.key_columns_for(multi.turns[0]) == [["manmenCtrcEndYmd"]]
        assert multi.key_columns_for(multi.turns[1]) == [["hWSportEndYmd", "sWSportEndYmd"]]
        assert multi.gold_tables_for(multi.turns[1]) == ["TCDMSIF79"]

    def test_minimal_valid_scenario(self, tmp_path: Path, policy: cat.ColumnPolicy) -> None:
        loaded = cat.load_scenarios(_write_scenarios(tmp_path, _OK_TURN), policy)
        assert [s.id for s in loaded] == ["ITAM-90"]

    @pytest.mark.parametrize(
        "old, new, needle",
        [
            (
                'send: {query: "자산관리에 등록된 서버 목록 보여줘"}',
                'send: {query: "자산관리에 등록된 서버 목록 보여줘", selected_db_ids: [itam]}',
                "첫 턴은 query 만",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "oracle: {id: ITAM-02, compare: keyset, key: [[rspblPsnEmnm, 담당자]], "
                "db_ids: [itam]}",
                "키는 general·identifier만",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "oracle: {id: ITAM-02, compare: keyset, key: [[호스트명, 서버]], db_ids: [itam]}",
                "카탈로그 컬럼 이름이 하나도 없다",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "oracle: {id: ITAM-14, compare: value, value: [byCtrcName], db_ids: [itam]}",
                "값은 general·amount만",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "oracle: {id: ITAM-99, compare: count, db_ids: [itam]}",
                "ITAM-99.mariadb.sql",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}\n"
                "          observe: {what: x}",
                "함께 쓰지 않는다",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "observe: {what: '', no_data: true}",
                "observe 는",
            ),
            (
                "oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "count_rows_ok: true\n"
                "          oracle: {id: ITAM-02, compare: count, db_ids: [itam]}",
                "count_rows_ok",
            ),
            ("key_columns: [sevrHostName]", "key_columns: [sevrHostNmae]", "카탈로그에 없다"),
            ("gold_tables: [TCDMSIF80]", "gold_tables: [TCDMSIF81]", "카탈로그에 없다"),
            ("env: [sandbox]", "env: [prod]", "env 는"),
            (
                'send: {query: "자산관리에 등록된 서버 목록 보여줘"}',
                'send: {query: "TCDMSIF80 목록 보여줘"}',
                "식별자",
            ),
        ],
    )
    def test_loader_rejects(
        self, tmp_path: Path, policy: cat.ColumnPolicy, old: str, new: str, needle: str
    ) -> None:
        assert old in _OK_TURN
        errors = _errors(tmp_path, policy, _OK_TURN.replace(old, new))
        assert any(needle in e for e in errors), errors

    def test_duplicate_ids_and_reply_turn_rules(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        errors = _errors(tmp_path, policy, _OK_TURN + _OK_TURN)
        assert any("id 중복" in e for e in errors)
        reply = _OK_TURN + "      - send: {selected_db_ids: [itam]}\n        expect: {}\n"
        assert any("reply_to: clarification" in e for e in _errors(tmp_path, policy, reply))
        ok = _OK_TURN + (
            "      - send: {selected_db_ids: [itam]}\n        reply_to: clarification\n"
            "        expect: {}\n"
        )
        assert cat.load_scenarios(_write_scenarios(tmp_path, ok), policy)[0].turns[1].reply_to

    def test_select_rejects_unknown_ids(self, scenarios: list[cat.Scenario]) -> None:
        assert [s.id for s in cat.select(scenarios, env="sandbox", only=["ITAM-02"])] == ["ITAM-02"]
        # 폐쇄망 시나리오는 `scenarios.closed.yaml`에 따로 둔다(plans/135 W8)
        assert cat.select(scenarios, env="closed") == []
        with pytest.raises(ValueError, match="ITAM-77"):
            cat.select(scenarios, env="sandbox", only=["ITAM-77"])


# --- 오라클 정본(MariaDB) ---------------------------------------------------------


class TestMariadbCanon:
    _FILES = sorted(oracle_mod.ORACLE_DIR.glob("ITAM-*.mariadb.sql"))

    def test_canon_files_exist(self) -> None:
        """샌드박스 정본 16 · 폐쇄망 정답 7(plans/146 W5 (1)) · 연결 위치 탐침 10(W5 (2))."""
        stems = [p.name.split(".")[0] for p in self._FILES]
        sandbox = [s for s in stems if re.fullmatch(r"ITAM-\d{2}", s)]
        closed = [s for s in stems if re.fullmatch(r"ITAM-1\d{2}", s)]
        probes = [s for s in stems if s.startswith("ITAM-146-P")]
        assert len(sandbox) == 16
        assert closed == [f"ITAM-{n}" for n in (107, 108, 109, 110, 111, 112, 116)]
        assert len(probes) == 10
        assert len(stems) == len(sandbox) + len(closed) + len(probes)

    @pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
    def test_canon_header_and_dialect_rules(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        head = [line for line in text.splitlines() if line.startswith("--")]
        body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
        for word in ("대상", "검수:", "근거:"):
            assert any(word in line for line in head), (path.name, word)
        # 상대 기간은 앵커 자리표로만 — DB 현재시각 함수 금지(plans/122 §9.2 와 같은 규칙).
        assert not re.search(
            r"(?i)\bcurdate\s*\(|current[_ ](date|timestamp)|\bnow\s*\(|sysdate", body
        ), path.name
        assert "::" not in body and "||" not in body and '"' not in body, path.name
        rendered = oracle_mod.render_sql(
            text, anchor_at="2026-10-06T10:00:00+09:00", run_id="t", scenario_id=path.stem
        )
        assert is_select_only(rendered), path.name
        assert (
            oracle_mod.validate_oracle_spec(
                {"id": path.name.split(".")[0], "compare": "count"},
                scenario_id=path.stem,
                db_ids=["itam"],
            )
            == []
        )

    def test_canon_selects_no_person_columns(self, policy: cat.ColumnPolicy) -> None:
        """정답 SQL 은 사람 열을 읽지 않는다.

        오라클 행이 판정 상세로 새는 통로를 처음부터 막는다.
        """
        person = {
            c for t in policy.tables.values() for c, g in t.items() if g in ("pii", "free_text")
        }
        for path in self._FILES:
            body = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("--")
            )
            assert not {c for c in person if re.search(rf"\b{c}\b", body)}, path.name


# --- CLI ----------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.truncated = False


def _fake_open_client(rows_for: dict[str, list[dict[str, Any]]]) -> Any:
    def factory(_cfg: Any, _db_id: str) -> Any:
        class _Client:
            async def execute_sql(self, sql: str) -> _FakeResult:
                oracle_id = re.search(r"scn=(ITAM-\d+)", sql)
                return _FakeResult(
                    rows_for.get(oracle_id.group(1) if oracle_id else "", [{"n": 1}])
                )

        @asynccontextmanager
        async def _ctx() -> AsyncIterator[_Client]:
            yield _Client()

        return _ctx()

    return factory


def _fake_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        db_backend="dbhub",
        security=SecurityConfig(
            sensitive_columns=["password"], mask_ip=False, mask_email=False, mask_pattern="***"
        ),
    )


class TestCli:
    def test_dry_run_is_default_and_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main([]) == 0
        out = capsys.readouterr().out
        assert "시나리오   : 22건" in out and "프롬프트 린트: 통과" in out

    def test_check_oracle_counts_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            oracle_mod,
            "_open_client",
            _fake_open_client({"ITAM-03": [{"sevrHostName": "svr-web-01"}]}),
        )
        monkeypatch.setattr("src.config.load_config", _fake_cfg)
        assert cli.main(["--check-oracle"]) == 0
        out = capsys.readouterr().out
        assert "실패·0행 0건" in out and "svr-web-01" not in out  # 행 원문은 출력하지 않는다

    def test_check_oracle_fails_on_zero_rows(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(oracle_mod, "_open_client", _fake_open_client({"ITAM-09": []}))
        monkeypatch.setattr("src.config.load_config", _fake_cfg)
        assert cli.main(["--check-oracle", "--only", "ITAM-09"]) == 1
        assert "0행" in capsys.readouterr().out

    def test_check_oracle_refuses_direct_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.config.load_config", lambda: SimpleNamespace(db_backend="direct"))
        assert cli.main(["--check-oracle"]) == 1
