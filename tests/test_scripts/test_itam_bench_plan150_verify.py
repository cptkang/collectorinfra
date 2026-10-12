"""plans/150 W2·W9 독립 검증(verifier) — 조인 키 판정 경계 · 로더 · 순번 · 형식 리터럴 · r6 파일.

구현자 테스트(`test_itam_bench_plan150_w2.py`·`…_r6_overlap.py`)가 고정한 수용 기준 밖의
경계를 본다.
결함으로 확인한 것은 `xfail(strict=True)`로 고정한다 — 고쳐지면 XPASS로 실패해 표지를 걷게 한다.
입력 SQL은 합성 모양이다(값·`results/` 원본 0). 실 LLM·DB 0.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd

REPO = Path(__file__).resolve().parents[2]
R6_PATH = REPO / "testdata" / "itam_bench" / "scenarios.closed.r6.yaml"
ORACLES = REPO / "testdata" / "scenarios" / "oracles"

P_72_79 = [
    (("tcdmsif72", "서버호스트명"), ("tcdmsif79", "서버호스트명")),
    (("tcdmsif72", "IP주소내용"), ("tcdmsif79", "IP주소내용")),
]
P_221 = [(("tcdmsif52", "호스트명"), ("tcdmsif72", "서버호스트명"))]
P_222 = [
    (("tcdmsif52", "시리얼번호"), ("tcdmsif51", "시리얼번호")),
    (("tcdmsif51", "시리얼번호"), ("tcdmsif58", "시리얼번호")),
]


def _miss(sql: str, pairs: list[Any]) -> list[list[str]]:
    return jd.join_key_judgement(sql, pairs)["missed"]


# ── W2 (6) 조인 키 판정 ───────────────────────────────────────────────────────


class TestJoinJudgementPasses:
    def test_left_join_is_null_anti_join(self) -> None:
        sql = (
            "SELECT COUNT(*) FROM `tcdmsif72` s LEFT JOIN `tcdmsif79` h "
            "ON h.`서버호스트명` = s.`서버호스트명` AND h.`IP주소내용` = s.`IP주소내용` "
            "WHERE h.`서버호스트명` IS NULL"
        )
        assert _miss(sql, P_72_79) == []

    def test_composite_key_half_joined_is_reported(self) -> None:
        """219 함정 — 호스트만 잇고 IP를 빠뜨리면 IP 쌍만 빠짐."""
        sql = (
            "SELECT s.`운영체제버전내용`, COUNT(*) FROM tcdmsif79 h JOIN tcdmsif72 s "
            "ON s.서버호스트명 = h.서버호스트명 GROUP BY 1"
        )
        assert _miss(sql, P_72_79) == [["tcdmsif72.IP주소내용", "tcdmsif79.IP주소내용"]]

    def test_backtick_schema_and_backtick_alias(self) -> None:
        sql = (
            "SELECT 1 FROM `itam`.`tcdmsif52` AS `a` INNER JOIN `itam`.`tcdmsif72` AS `b` "
            "ON `a`.`호스트명` = `b`.`서버호스트명`"
        )
        assert _miss(sql, P_221) == []

    def test_comparison_operator_is_not_join(self) -> None:
        sql = "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b ON a.호스트명 >= b.서버호스트명"
        assert _miss(sql, P_221) == [["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]]

    def test_derived_table_alias_is_unresolved_not_miss(self) -> None:
        sql = (
            "SELECT 1 FROM (SELECT 호스트명 FROM tcdmsif52 WHERE 업무명 LIKE '%x%') d "
            "JOIN tcdmsif72 b ON d.호스트명 = b.서버호스트명"
        )
        out = jd.join_key_judgement(sql, P_221)
        assert out["missed"] == [] and out["alias_unresolved"] is True

    def test_extract_from_does_not_break_alias_map(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif72 s JOIN tcdmsif79 h ON s.서버호스트명 = h.서버호스트명 "
            "AND s.IP주소내용 = h.IP주소내용 "
            "WHERE EXTRACT(YEAR FROM h.하드웨어지원종료년월일) = 2026"
        )
        assert _miss(sql, P_72_79) == []

    def test_alias_reused_for_two_tables_is_unresolved(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b ON a.호스트명 = b.서버호스트명 "
            "WHERE EXISTS (SELECT 1 FROM tcdmsif51 b WHERE b.시리얼번호 = a.시리얼번호)"
        )
        out = jd.join_key_judgement(sql, P_221)
        assert out["missed"] == [] and out["alias_unresolved"] is True

    def test_self_join_same_table_keeps_pair_table_scoped(self) -> None:
        """같은 테이블 두 번(self-join)만으로는 72↔79 쌍을 이은 것이 아니다."""
        sql = (
            "SELECT 1 FROM tcdmsif72 a JOIN tcdmsif72 b ON a.서버호스트명 = b.서버호스트명 "
            "JOIN tcdmsif79 h ON h.IP주소내용 = b.IP주소내용"
        )
        assert _miss(sql, P_72_79) == [["tcdmsif72.서버호스트명", "tcdmsif79.서버호스트명"]]


class TestJoinJudgementDefects:
    def test_function_wrapped_equality_is_join(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b "
            "ON TRIM(a.호스트명) = TRIM(b.서버호스트명)"
        )
        assert _miss(sql, P_221) == []

    def test_not_in_subquery_is_join(self) -> None:
        sql = (
            "SELECT COUNT(*) FROM tcdmsif72 s WHERE s.서버호스트명 NOT IN "
            "(SELECT h.서버호스트명 FROM tcdmsif79 h)"
        )
        assert _miss(sql, P_72_79[:1]) == []

    def test_tuple_in_subquery_is_join(self) -> None:
        sql = (
            "SELECT COUNT(*) FROM tcdmsif72 s WHERE (s.서버호스트명, s.IP주소내용) IN "
            "(SELECT h.서버호스트명, h.IP주소내용 FROM tcdmsif79 h)"
        )
        assert _miss(sql, P_72_79) == []

    def test_using_does_not_cover_other_pair(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif52 JOIN tcdmsif51 USING (시리얼번호) "
            "JOIN tcdmsif58 c ON c.모델명 = tcdmsif51.모델명"
        )
        assert _miss(sql, P_222) == [["tcdmsif51.시리얼번호", "tcdmsif58.시리얼번호"]]

    def test_composite_key_split_across_instances(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif79 h JOIN tcdmsif72 a ON a.서버호스트명 = h.서버호스트명 "
            "JOIN tcdmsif72 b ON b.IP주소내용 = h.IP주소내용"
        )
        assert _miss(sql, P_72_79) != []


# ── W2 (5) 승인 코드값 없음 — 공통 칸 소음 ─────────────────────────────────────


def _catalog() -> jd.CatalogFacts:
    return jd.CatalogFacts.from_catalog({
        "tables": {"tcdmsif72": {"key": [], "columns": [
            {"name": "그룹회사코드"}, {"name": "서버호스트명"}, {"name": "활성화여부"},
        ]}},
        "same_key_groups": [],
    })


def _facts(sql: str) -> jd.TurnFacts:
    return jd.TurnFacts(status="ok", executed=[{"sql": sql, "source": "itam", "success": True}])


class TestNoApprovedCodes:
    def test_group_company_literal_is_marked(self) -> None:
        """「코드」로 끝나는 공통 칸(그룹회사코드)도 표시 대상 — 관찰 소음(판정 불변)."""
        facts = _facts("SELECT 1 FROM tcdmsif72 WHERE 그룹회사코드 = 'A1'")
        analysis = jd.analyze_sql(facts, _catalog(), db_id="itam")
        assert analysis["unknown_code_value"] == jd.NO_APPROVED_CODES
        assert "code_value" not in jd.classify(facts, analysis, db_id="itam")

    def test_not_equal_is_not_marked(self) -> None:
        facts = _facts("SELECT 1 FROM tcdmsif72 WHERE 활성화여부 <> 'N'")
        assert jd.analyze_sql(facts, _catalog(), db_id="itam")["unknown_code_value"] is None


# ── W2 (4) retry_attempt 순번 ────────────────────────────────────────────────


def _ctx() -> Any:
    policy = cat.ColumnPolicy(
        db_id="itam", scope="t", tables={"tcdmsif72": {"서버호스트명": "pii"}}
    )
    return SimpleNamespace(
        policy=policy, vault=rd.PiiVault.from_policy(policy),
        catalog=SimpleNamespace(columns=frozenset({"서버호스트명"})),
    )


def test_two_tasks_without_retry_read_as_order() -> None:
    """재생성 없는 task 2개 — 순번 [0, 1] · 서버 값 0은 둘째에만 `server_retry_attempt`로 남는다.

    `retry_attempt`가 이제 「재생성 횟수」가 아니라 「턴 안 실행 순번」이다(옛 run과 의미가 다르다).
    """
    executed = [
        {"sql": "SELECT 1 FROM tcdmsif72", "source": "itam", "retry_attempt": 0},
        {"sql": "SELECT 2 FROM tcdmsif72", "source": "itam", "retry_attempt": 0},
    ]
    out = cli._redacted_sqls(executed, _ctx(), "")
    assert [e["retry_attempt"] for e in out] == [0, 1]
    assert [e.get("server_retry_attempt") for e in out] == [None, 0]


# ── W2 (2) 날짜 형식 리터럴 ──────────────────────────────────────────────────


class TestDateFormatBoundary:
    def test_ip_like_digits_after_two_directives_not_format(self) -> None:
        """`%Y%m10.1.2.3`은 형식이 아니다 — 보안 감사 L1 교정(숫자 총 4자 상한)으로 가린다."""
        assert rd._date_format("%Y%m10.1.2.3") is False

    def test_gate_still_blocks_ip_inside_format_literal(self) -> None:
        """가림을 통과해도 누출 관문(IPv4 모양)이 막는다 — 닫힌 쪽 유지."""
        policy = cat.ColumnPolicy(db_id="itam", scope="t", tables={})
        gate = rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})
        text = "SELECT DATE_FORMAT(x, '%Y%m10.1.2.3') FROM t"
        assert gate.leaf_rules(text, schema_section=False) == ["pii_regex"]

    def test_year_end_literal_still_masked(self) -> None:
        """205 모양 `'%Y1231'`(지시자 1 · 네 자리)은 종전대로 가린다."""
        assert rd._date_format("%Y1231") is False


# ── W2 (7) 로더 ──────────────────────────────────────────────────────────────


def _write(path: Path, doc: Any) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _scn(**extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "ITAM-299", "title": "t", "category": "c", "env": ["closed"],
        "gold_tables": ["tcdmsif72"], "key_columns": ["서버호스트명"],
        "turns": [{"send": {"query": "서버 목록 보여줘"},
                   "expect": {"observe": {"what": "w", "no_data": False}}}],
    }
    raw.update(extra)
    return raw


_POLICY = cat.ColumnPolicy(db_id="itam", scope="t", tables={"tcdmsif72": {"서버호스트명": "pii"}})


class TestLoaderBoundary:
    def test_turn_override_to_empty_gold_rejected(self, tmp_path: Path) -> None:
        scn = _scn()
        scn["turns"][0]["expect"]["gold_tables"] = []
        body = {"require_judgement": True, "scenarios": [scn]}
        with pytest.raises(cat.CatalogError, match="gold_tables 가 비었다"):
            cat.load_scenarios(_write(tmp_path / "s.yaml", body), _POLICY)

    def test_without_flag_empty_judgement_accepted(self, tmp_path: Path) -> None:
        body = {"scenarios": [_scn(gold_tables=[], key_columns=[])]}
        assert len(cat.load_scenarios(_write(tmp_path / "s.yaml", body), _POLICY)) == 1

    def test_schema_names_do_not_widen_prompt_lint(self, tmp_path: Path) -> None:
        """스냅숏 칸 이름은 판정 칸 검사에만 — 프롬프트 린트 식별자는 정책 이름 그대로."""
        scn = _scn()
        scn["turns"][0]["send"]["query"] = "업무명 칸으로 서버 목록 보여줘"
        body = {"scenarios": [scn]}
        loaded = cat.load_scenarios(
            _write(tmp_path / "s.yaml", body), _POLICY,
            schema_names={"tcdmsif52": ["업무명"]},
        )
        assert loaded[0].turns[0].query.startswith("업무명")

    def test_r6_file_loads_with_real_snapshot(self) -> None:
        policy = cat.load_policy(REPO / "testdata" / "itam_bench" / "column_policy.closed.yaml")
        scenarios = cat.load_scenarios(
            R6_PATH, policy, schema_names=cat.load_schema_names(cat.CLOSED_SCHEMA_PATH)
        )
        assert len(scenarios) == 32
        # 스냅숏 없이(정책만)는 정책 밖 테이블(조인 군)을 거절한다 — 출처 확장이 실제로 쓰인다
        with pytest.raises(cat.CatalogError):
            cat.load_scenarios(R6_PATH, policy)


# ── W9 r6 파일 ───────────────────────────────────────────────────────────────

_SERVICES = ("무인공과금", "법원공탁금", "그린뱅크")


def _r6() -> dict[str, Any]:
    return yaml.safe_load(R6_PATH.read_text(encoding="utf-8"))


class TestR6File:
    def test_oracle_files_exist_for_oracle_turns_only(self) -> None:
        oracle_ids = {
            t["expect"]["oracle"]["id"]
            for s in _r6()["scenarios"] for t in s["turns"] if "oracle" in t["expect"]
        }
        expected = (201, 202, 203, 204, 205, 206, 207, 212, 218, 227)
        assert oracle_ids == {f"ITAM-{n}" for n in expected}
        on_disk = {p.name.split(".")[0] for p in ORACLES.glob("ITAM-2[0-9][0-9].mariadb.sql")}
        assert on_disk == oracle_ids

    def test_oracles_have_no_value_literals(self) -> None:
        """오라클 SQL의 작은따옴표 리터럴은 날짜 서식·빈 문자열뿐(값 리터럴 0)."""
        import re

        for path in ORACLES.glob("ITAM-2[0-9][0-9].mariadb.sql"):
            body = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("--")
            )
            for lit in re.findall(r"'((?:[^']|'')*)'", body):
                assert lit == "" or lit.startswith("%Y"), (path.name, lit)

    def test_only_three_service_names(self) -> None:
        text = R6_PATH.read_text(encoding="utf-8")
        for banned in ("통합인증", "인터넷뱅킹"):
            assert banned not in text
        prompts = [t["send"]["query"] for s in _r6()["scenarios"] for t in s["turns"]]
        used = {name for name in _SERVICES for p in prompts if name in p}
        assert used == set(_SERVICES)

    def test_join_turns_are_observe(self) -> None:
        for s in _r6()["scenarios"]:
            for t in s["turns"]:
                if t["expect"].get("join_keys") or (s.get("join_keys") and len(s["turns"]) == 1):
                    assert "oracle" not in t["expect"], s["id"]
