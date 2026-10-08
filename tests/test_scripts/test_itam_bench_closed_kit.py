"""ITAM 질의 벤치 — 폐쇄망 1회차 키트 (plans/135 v1.4 · W8 · 사용자 인터뷰 2026-10-06).

G-7 답: 내부망 「DB 구조」 탭으로 만든 스키마로 1회차는 관찰만 하고, 반출 로그로 서비스↔서버 연결
위치를 찾아 정답 SQL 을 쓴 뒤 2회차부터 대조한다. 반출 카탈로그는 운영 108테이블 전부(구조·의미만).
DB·LLM 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import (
    CLOSED_POLICY_PATH,
    CLOSED_SCENARIOS_PATH,
    POLICY_PATH,
    TRANSCRIPT_PATH,
)
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp


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

    def test_closed_policy_matches_sandbox_review_without_canaries(
        self, closed_policy: cat.ColumnPolicy
    ) -> None:
        sandbox = cat.load_policy(POLICY_PATH)
        assert {t: dict(c) for t, c in closed_policy.tables.items()} == {
            t: dict(c) for t, c in sandbox.tables.items()
        }
        assert closed_policy.scope == "closed"
        assert closed_policy.canary_literals == () and closed_policy.canary_patterns == ()

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
