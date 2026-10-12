"""plans/150 W2 — 벤치 반출·근거·판정 교정(5회차 결과 판독 결함).

(1) 근거 묶음 관문이 `substitutions.yaml` 가짜 값 목록을 「이 run 가짜 값」 등록부로 쓴다 ·
(2) 날짜 형식 리터럴(`%Y%m01`)은 구조로 남긴다 · (3) 자산 지문 줄바꿈 정규화(v2) 표지 ·
(4) `retry_attempt` = 턴 안 실행 순번(서버 값은 다를 때만 `server_retry_attempt`) ·
(5) 승인 코드값 없는 코드·여부 칸의 리터럴 등호 = 「승인 코드값 없음」(관찰) ·
(6) 조인 키 판정(`join_keys` · 별칭 해석 · `USING` · `EXISTS`)과 「조인 판정」 절 ·
(7) 판정 칸 이름 = 정책 ∪ closed 스키마 이름 · `require_judgement`.

입력은 5회차 SQL 의 **모양**을 값 없이 재구성했다(`results/` 원본은 쓰지 않는다 · D-308). IP·값은
합성이다. 실 LLM·DB 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import knowledge as kn
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.itam_bench import substitute as sub

_FAKE_IP = "10.77.31.204"  # 합성 — 이 run 가짜 IP 역할
_OTHER_IP = "10.77.31.205"  # 합성 — 목록에 없는 IP
_LOGIN = "5488923"

_POLICY = cat.ColumnPolicy(
    db_id="itam",
    scope="test",
    tables={
        "tcdmsif72": {
            "서버호스트명": "pii",
            "취득일자": "general",
            "IP주소내용": "network",
            "활성화여부": "general",
        },
    },
)


def _gate(fakes: sub.FakeValues | None) -> rd.LeakGate:
    return rd.LeakGate(
        policy=_POLICY,
        vault=rd.PiiVault.from_policy(_POLICY, fakes=fakes),
        user_values={"login_id": _LOGIN},
    )


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ──────────────────────────────────────────────
# (1) 가짜 값 등록부
# ──────────────────────────────────────────────


class TestRegistry:
    def test_registry_answers_is_fake_only(self) -> None:
        fakes = sub.FakeValues.registry([_FAKE_IP, "QX7MV", "", "  "])
        assert fakes.is_fake(_FAKE_IP)
        assert fakes.is_fake("QX7MV")
        assert not fakes.is_fake(_OTHER_IP)
        assert not fakes.is_fake("")

    @pytest.mark.parametrize(
        "call",
        [
            lambda f: f.register(["원값"]),
            lambda f: f.fake_or_none("원값"),
            lambda f: f.fake("원값"),
            lambda f: f.fake_ip("10.0.0.1"),
        ],
    )
    def test_registry_refuses_issue_and_register(self, call: Any) -> None:
        with pytest.raises(RuntimeError):
            call(sub.FakeValues.registry([_FAKE_IP]))

    def test_gate_passes_listed_fake_ip_blocks_others(self) -> None:
        text = f"SELECT * FROM tcdmsif72 WHERE `IP주소내용` = '{_FAKE_IP}'"
        listed = _gate(sub.FakeValues.registry([_FAKE_IP]))
        assert listed.leaf_rules(text, schema_section=False) == []
        other = text.replace(_FAKE_IP, _OTHER_IP)
        assert _gate(sub.FakeValues.registry([_FAKE_IP])).leaf_rules(
            other, schema_section=False
        ) == ["pii_regex"]

    def test_gate_without_registry_blocks_by_shape(self) -> None:
        """등록부가 없으면(1·2회차) 종전대로 IPv4 모양만으로 막는다."""
        text = f"WHERE `IP주소내용` = '{_FAKE_IP}'"
        assert _gate(None).leaf_rules(text, schema_section=False) == ["pii_regex"]


def _evidence_run(tmp_path: Path, *, fakes: list[str] | None) -> Path:
    run = tmp_path / "results" / "20261101-000000"
    _write(
        run / ba.CATALOG_FILE,
        {
            "db_id": "itam",
            "source": "structure_store",
            "approved_profile": {"source": "manual", "environment": None,
                                 "allowed_tables": ["t_srv"]},
            "tables": {"t_srv": {"key": [], "rows_estimate": None, "relations": [], "columns": [
                {"name": "ip_addr", "type": "varchar", "nullable": True, "meaning": None,
                 "meaning_source": "none"}]}},
        },
    )
    if fakes is not None:
        _write(run / rd.SUBSTITUTIONS_FILE, {
            "db_id": "itam", "run_id": run.name, "note": "형식 보존 치환값 — 원값 아님",
            "summary": {"values": len(fakes)}, "values": fakes,
        })
    record = {
        "id": "ITAM-150", "turn": 1, "repeat": 1, "category": "c", "prompt": "서버 목록",
        "status": "ok", "db_ids": ["itam"],
        "executed_sqls": [{
            "sql": f"SELECT ip_addr FROM t_srv WHERE ip_addr = '{_FAKE_IP}'",
            "source": "itam", "success": True, "row_count": 1, "retry_attempt": 0,
        }],
    }
    (run / kn.TRACE_FILE).write_text(json.dumps(record, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    return run


def _run_evidence(tmp_path: Path, run: Path) -> int:
    policy = tmp_path / "policy.yaml"
    _write(policy, {"db_id": "itam", "tables": {}})
    repo = tmp_path / "repo"
    _write(repo / ba.PROFILE_REL, {"allowed_tables": ["t_srv"], "table_definitions": {}})
    return kn.run_evidence(run, policy_path=policy, scenarios_path=tmp_path / "없는.yaml",
                           repo_root=repo, knowledge_dir=tmp_path / "knowledge")


class TestEvidenceRegistry:
    def test_listed_fake_ip_passes_evidence_gate(self, tmp_path: Path) -> None:
        run = _evidence_run(tmp_path, fakes=[_FAKE_IP, "QX7MV"])
        assert _run_evidence(tmp_path, run) == kn.EXIT_OK
        turns = (run / kn.EVIDENCE_DIR / "turns.yaml").read_text(encoding="utf-8")
        assert _FAKE_IP in turns

    def test_without_substitutions_file_still_blocks(self, tmp_path: Path, capsys: Any) -> None:
        run = _evidence_run(tmp_path, fakes=None)
        assert _run_evidence(tmp_path, run) == kn.EXIT_FAILED
        out = capsys.readouterr().out
        assert "turns.yaml · turns[0].executed_sqls[0].sql · pii_regex" in out
        assert _FAKE_IP not in out

    def test_ip_not_in_list_still_blocks(self, tmp_path: Path, capsys: Any) -> None:
        run = _evidence_run(tmp_path, fakes=[_OTHER_IP])
        assert _run_evidence(tmp_path, run) == kn.EXIT_FAILED
        assert "pii_regex" in capsys.readouterr().out
        assert not list((run / kn.EVIDENCE_DIR).glob("*.yaml"))


# ──────────────────────────────────────────────
# (2) 날짜 형식 리터럴
# ──────────────────────────────────────────────


class TestDateFormat:
    @pytest.mark.parametrize(
        "content, kept",
        [
            ("%Y%m01", True),  # 108 모양 — 지시자 2 + 두 자리 숫자
            ("%Y-%m-01", True),
            ("%Y", True),  # 종전 그대로(지시자만)
            ("%Y%m%d", True),
            ("%Y-%m", True),
            ("20261001", False),  # 숫자만 — 지시자 0
            ("%통합인증%", False),
            ("%a%", False),
            ("%Y01", False),  # 지시자 1
            ("%Y%m123", False),  # 세 자리 숫자
            ("%d1234", False),
            ("%Y%m01\n", False),  # 끝 개행
            ("%H:%i:00", True),
            ("12%Y%m34", True),  # 숫자 총 4자 — 상한 안
            ("%a%b 0 1 0 1 2 3", False),  # 보안 감사 L1 — 띄운 숫자 덩이 연쇄
            ("%p%p9 8 7 6 5 4 3", False),
            ("%y%m 1 2 3 4 5 6 7 8", False),
            ("%Y%m 1 2", False),  # 숫자 2자라도 띄운 연쇄는 가린다
            ("%Y%m01 00:00", False),  # 숫자 총 6자
            ("%Y%m10.1.2.3", False),  # 숫자 총 5자
        ],
    )
    def test_date_format_rule(self, content: str, kept: bool) -> None:
        assert rd._date_format(content) is kept

    def test_redact_sql_keeps_format_literal(self) -> None:
        vault = rd.PiiVault.from_policy(_POLICY)
        cols = ["서버호스트명", "취득일자"]
        sql = "SELECT DATE_FORMAT(`취득일자`, '%Y%m01') FROM tcdmsif72"
        assert rd.redact_sql(sql, policy=_POLICY, vault=vault, catalog_columns=cols) == sql
        wide = "SELECT DATE_FORMAT(`취득일자`, '%Y%m123') FROM tcdmsif72"
        assert "'%Y%m123'" not in rd.redact_sql(
            wide, policy=_POLICY, vault=vault, catalog_columns=cols
        )
        like = "SELECT * FROM tcdmsif72 WHERE `서버호스트명` LIKE '%통합인증%'"
        assert rd.redact_sql(like, policy=_POLICY, vault=vault, catalog_columns=cols) == (
            f"SELECT * FROM tcdmsif72 WHERE `서버호스트명` LIKE '{rd.MASK}'"
        )
        assert _gate(None).leaf_rules(sql, schema_section=False) == []


# ──────────────────────────────────────────────
# (3) 지문 줄바꿈 정규화
# ──────────────────────────────────────────────


class TestFingerprint:
    def test_crlf_and_lf_same_fingerprint(self, tmp_path: Path) -> None:
        lf, crlf = tmp_path / "lf.yaml", tmp_path / "crlf.yaml"
        lf.write_bytes(b"a: 1\nb: 2\n")
        crlf.write_bytes(b"a: 1\r\nb: 2\r\n")
        assert cat._fingerprint_file(lf) == cat._fingerprint_file(crlf)
        assert cat._fingerprint_file(tmp_path / "없음.yaml") is None

    def test_assets_carry_method(self, tmp_path: Path) -> None:
        assert cat.asset_fingerprints(repo_root=tmp_path)["fingerprint_method"] == "v2"

    def test_report_head_marker(self) -> None:
        head = rp.render_report({"run_id": "x"}, {"assets": {"fingerprint_method": "v2"}}, [])
        (line,) = [x for x in head.splitlines() if x.startswith("- 스키마 입력")]
        assert line.endswith("· 지문 정규화 v2")
        old = rp.render_report({"run_id": "x"}, {"assets": {"profile": None}}, [])
        assert "지문 정규화" not in old

    def test_retry_attempt_legend_only_when_noted(self) -> None:
        """교정 L17 — `run.json`에 뜻 표지가 있는 run 만 머리에 한 줄(옛 run 리포트는 그대로)."""
        run = {"run_id": "x", "retry_attempt_note": rp.RETRY_ATTEMPT_NOTE}
        noted = rp.render_report(run, {}, [])
        assert f"\n- {rp.RETRY_ATTEMPT_NOTE}\n\n" in noted
        assert "retry_attempt" not in rp.render_report({"run_id": "x"}, {}, [])

    @staticmethod
    def _run(directory: Path, assets: dict[str, Any]) -> Path:
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(
            json.dumps({"run_id": directory.name, "assets": assets}), encoding="utf-8"
        )
        (directory / "trace.jsonl").write_text("", encoding="utf-8")
        return directory

    def test_compare_flags_method_change(self, tmp_path: Path) -> None:
        old = self._run(tmp_path / "a", {"profile": {"fingerprint": "76692c2b4715"}})
        new = self._run(
            tmp_path / "b",
            {"profile": {"fingerprint": "891c9e10fe73"}, "fingerprint_method": "v2"},
        )
        assert "**지문 방식 변경** — `v1` → `v2`" in rp.compare_runs(old, new)
        same = self._run(tmp_path / "c", {"profile": {"fingerprint": "891c9e10fe73"},
                                         "fingerprint_method": "v2"})
        assert "지문 방식 변경" not in rp.compare_runs(new, same)


# ──────────────────────────────────────────────
# (4) retry_attempt = 실행 순번
# ──────────────────────────────────────────────


def _ctx() -> Any:
    return SimpleNamespace(
        policy=_POLICY,
        vault=rd.PiiVault.from_policy(_POLICY),
        catalog=SimpleNamespace(columns=frozenset({"서버호스트명", "활성화여부"})),
    )


class TestRetryAttempt:
    def test_two_tasks_shape_105(self) -> None:
        """105 모양 — task 2개: 서버 값 1(재생성)을 먼저, 서버 값 0(다른 task 첫 SQL)을 나중에."""
        executed = [
            {"sql": "SELECT 1 FROM tcdmsif72 WHERE `활성화여부` = '0'", "source": "itam",
             "success": True, "row_count": 0, "retry_attempt": 1},
            {"sql": "SELECT 1 FROM tcdmsif72", "source": "itam", "success": True,
             "row_count": 85, "retry_attempt": 0},
        ]
        out = cli._redacted_sqls(executed, _ctx(), "")
        assert [e["retry_attempt"] for e in out] == [0, 1]
        assert [e.get("server_retry_attempt") for e in out] == [1, 0]

    def test_single_task_unchanged(self) -> None:
        executed = [
            {"sql": "SELECT 1 FROM tcdmsif72", "source": "itam", "retry_attempt": n}
            for n in (0, 1, 2)
        ]
        out = cli._redacted_sqls(executed, _ctx(), "")
        assert [e["retry_attempt"] for e in out] == [0, 1, 2]
        assert all("server_retry_attempt" not in e for e in out)

    def test_missing_server_value(self) -> None:
        out = cli._redacted_sqls([{"sql": "SELECT 1 FROM tcdmsif72"}], _ctx(), "")
        assert out[0]["retry_attempt"] == 0 and "server_retry_attempt" not in out[0]


# ──────────────────────────────────────────────
# (5) 승인 코드값 없음
# ──────────────────────────────────────────────


def _catalog_facts(code_values: dict[str, Any] | None = None) -> jd.CatalogFacts:
    doc = {
        "tables": {
            "tcdmsif72": {
                "key": [],
                "columns": [
                    {"name": "서버호스트명"},
                    {"name": "활성화여부"},
                    {"name": "상태값", "value_kind": "code_text"},
                    {"name": "구분", "meaning": "자산구분코드"},
                ],
            },
        },
        "same_key_groups": [],
    }
    return jd.CatalogFacts.from_catalog(doc, code_values=code_values)


def _facts(sql: str, **extra: Any) -> jd.TurnFacts:
    return jd.TurnFacts(
        status="ok",
        executed=[{"sql": sql, "source": "itam", "success": True}],
        **extra,
    )


class TestNoApprovedCodes:
    def test_marked_columns(self) -> None:
        assert _catalog_facts().code_marked == frozenset({"활성화여부", "상태값", "구분"})

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 FROM tcdmsif72 WHERE `활성화여부` = '0'",  # 105 모양
            "SELECT 1 FROM tcdmsif72 t WHERE t.활성화여부 = 1",
            "SELECT 1 FROM tcdmsif72 WHERE 상태값 IN ('A', 'B')",
            "SELECT 1 FROM tcdmsif72 WHERE `구분` = 'X'",
        ],
    )
    def test_literal_equal_marks_observation(self, sql: str) -> None:
        analysis = jd.analyze_sql(_facts(sql), _catalog_facts(), db_id="itam")
        assert analysis["unknown_code_value"] == jd.NO_APPROVED_CODES

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT `활성화여부`, COUNT(*) FROM tcdmsif72 GROUP BY `활성화여부`",
            "SELECT 1 FROM tcdmsif72 WHERE `서버호스트명` = 'h'",
            "SELECT 1 FROM tcdmsif72 WHERE 활성화여부값 = '0'",
        ],
    )
    def test_no_literal_on_marked_column_is_none(self, sql: str) -> None:
        analysis = jd.analyze_sql(_facts(sql), _catalog_facts(), db_id="itam")
        assert analysis["unknown_code_value"] is None

    def test_approved_codes_keep_previous_judgement(self) -> None:
        facts = _catalog_facts({"tcdmsif72.활성화여부": ["1"]})
        good = jd.analyze_sql(
            _facts("SELECT 1 FROM tcdmsif72 WHERE 활성화여부 = '1'"), facts, db_id="itam"
        )
        bad = jd.analyze_sql(
            _facts("SELECT 1 FROM tcdmsif72 WHERE 활성화여부 = '9'"), facts, db_id="itam"
        )
        assert good["unknown_code_value"] is False
        assert bad["unknown_code_value"] is True

    def test_classify_unchanged(self) -> None:
        """관찰 표지라 분류는 `None`과 같다(`code_value`는 승인 코드값이 있을 때만)."""
        facts = _facts("SELECT 1 FROM tcdmsif72 WHERE `활성화여부` = '0'")
        analysis = jd.analyze_sql(facts, _catalog_facts(), db_id="itam")
        assert jd.classify(facts, analysis, db_id="itam") == jd.classify(
            facts, {**analysis, "unknown_code_value": None}, db_id="itam"
        )


# ──────────────────────────────────────────────
# (6) 조인 키 판정
# ──────────────────────────────────────────────

_PAIR_221 = (("tcdmsif52", "호스트명"), ("tcdmsif72", "서버호스트명"))
_PAIRS_222 = [
    (("tcdmsif51", "시리얼번호"), ("tcdmsif52", "시리얼번호")),
    (("tcdmsif51", "시리얼번호"), ("tcdmsif58", "시리얼번호")),
]


class TestJoinKeys:
    def test_221_hit(self) -> None:
        sql = (
            "SELECT a.`호스트명` FROM `tcdmsif52` a JOIN `tcdmsif72` b "
            "ON a.`호스트명` = b.`서버호스트명`"
        )
        assert jd.join_key_judgement(sql, [_PAIR_221]) == {
            "missed": [], "alias_unresolved": False, "group_company": False,
        }

    def test_221_miss(self) -> None:
        sql = "SELECT a.호스트명 FROM tcdmsif52 AS a JOIN tcdmsif72 AS b ON a.자산번호 = b.자산번호"
        assert jd.join_key_judgement(sql, [_PAIR_221])["missed"] == [
            ["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]
        ]

    def test_221_reversed_and_schema_qualified(self) -> None:
        sql = (
            "SELECT 1 FROM itam.tcdmsif72 s JOIN itam.tcdmsif52 h "
            "ON s.서버호스트명 = h.호스트명 AND s.그룹회사코드 = h.그룹회사코드"
        )
        out = jd.join_key_judgement(sql, [_PAIR_221])
        assert out["missed"] == [] and out["group_company"] is True

    def test_222_aliases_distinguish_same_name(self) -> None:
        """같은 이름 칸 `시리얼번호` — 51·52만 잇고 58은 다른 칸으로 이었으면 51–58이 빠진 것."""
        sql = (
            "SELECT 1 FROM tcdmsif51 a JOIN tcdmsif52 b ON a.시리얼번호 = b.시리얼번호 "
            "JOIN tcdmsif58 c ON c.자산번호 = b.자산번호"
        )
        assert jd.join_key_judgement(sql, _PAIRS_222)["missed"] == [
            ["tcdmsif51.시리얼번호", "tcdmsif58.시리얼번호"]
        ]

    def test_222_transitive_closure(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif51 a JOIN tcdmsif52 b ON a.시리얼번호 = b.시리얼번호 "
            "JOIN tcdmsif58 c ON c.시리얼번호 = b.시리얼번호"
        )
        assert jd.join_key_judgement(sql, _PAIRS_222)["missed"] == []

    def test_229_not_exists_subquery(self) -> None:
        hit = (
            "SELECT h.호스트명 FROM tcdmsif52 h WHERE NOT EXISTS "
            "(SELECT 1 FROM tcdmsif72 s WHERE s.서버호스트명 = h.호스트명)"
        )
        assert jd.join_key_judgement(hit, [_PAIR_221])["missed"] == []
        miss = hit.replace("s.서버호스트명 = h.호스트명", "s.자산번호 = h.자산번호")
        assert jd.join_key_judgement(miss, [_PAIR_221])["missed"] == [
            ["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]
        ]

    def test_using_same_name(self) -> None:
        sql = "SELECT 1 FROM tcdmsif51 JOIN tcdmsif52 USING (시리얼번호, 그룹회사코드)"
        out = jd.join_key_judgement(sql, _PAIRS_222[:1])
        assert out["missed"] == [] and out["group_company"] is True

    def test_unqualified_equal_is_alias_unresolved(self) -> None:
        sql = "SELECT 1 FROM tcdmsif52, tcdmsif72 WHERE 호스트명 = 서버호스트명"
        assert jd.join_key_judgement(sql, [_PAIR_221]) == {
            "missed": [], "alias_unresolved": True, "group_company": False,
        }

    def test_pair_skipped_when_table_unused(self) -> None:
        sql = "SELECT 1 FROM tcdmsif52 a WHERE a.호스트명 = 'h'"
        assert jd.join_key_judgement(sql, [_PAIR_221])["missed"] == []

    def test_literal_text_is_not_join(self) -> None:
        sql = (
            "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b ON a.자산번호 = b.자산번호 "
            "WHERE a.비고 = 'a.호스트명 = b.서버호스트명'"
        )
        assert jd.join_key_judgement(sql, [_PAIR_221])["missed"] == [
            ["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]
        ]

    @pytest.mark.parametrize(
        "on",
        [
            "UPPER(TRIM(a.호스트명)) = CAST(b.서버호스트명 AS CHAR(64))",  # 교정 M4 — 겹친 감쌈
            "CONVERT(a.호스트명, CHAR) = LOWER(b.`서버호스트명`)",
        ],
    )
    def test_wrapped_equality_is_join(self, on: str) -> None:
        sql = f"SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b ON {on}"
        assert jd.join_key_judgement(sql, [_PAIR_221])["missed"] == []

    def test_multi_argument_function_is_not_unwrapped(self) -> None:
        """`SUBSTR(a.칸, 1, 3) = b.칸`은 키 등호가 아니다(칸 하나 감싼 함수만 벗긴다)."""
        sql = (
            "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b "
            "ON SUBSTR(a.호스트명, 1, 3) = b.서버호스트명"
        )
        assert jd.join_key_judgement(sql, [_PAIR_221])["missed"] == [
            ["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]
        ]

    @pytest.mark.parametrize(
        "where, unresolved",
        [
            ("a.호스트명 IN (SELECT 서버호스트명 FROM tcdmsif72 WHERE 활성화여부 = '1')", False),
            ("a.호스트명 NOT IN (SELECT DISTINCT TRIM(s.서버호스트명) FROM tcdmsif72 AS s)", False),
            ("호스트명 IN (SELECT 서버호스트명 FROM tcdmsif72)", True),  # 바깥 칸 맨 이름
        ],
    )
    def test_in_subquery_is_join(self, where: str, unresolved: bool) -> None:
        """교정 M5 — 하위 SELECT 의 맨 칸 이름은 그 FROM 테이블 칸이다."""
        out = jd.join_key_judgement(f"SELECT 1 FROM tcdmsif52 a WHERE {where}", [_PAIR_221])
        assert out["missed"] == [] and out["alias_unresolved"] is unresolved

    def test_using_chain_links_adjacent_tables(self) -> None:
        """교정 L15 — `USING`은 그 JOIN 테이블과 바로 앞 테이블 사이(연쇄는 추이로 이어진다)."""
        sql = (
            "SELECT 1 FROM tcdmsif51 JOIN tcdmsif52 USING (시리얼번호) "
            "JOIN tcdmsif58 USING (시리얼번호)"
        )
        assert jd.join_key_judgement(sql, _PAIRS_222)["missed"] == []

    def test_analyze_adds_keys_only_when_declared(self) -> None:
        sql = "SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 b ON a.자산번호 = b.자산번호"
        facts = _catalog_facts()
        plain = jd.analyze_sql(_facts(sql), facts, db_id="itam")
        assert not {"join_key_miss", "join_alias_unresolved", "join_group_company"} & set(plain)
        declared = jd.analyze_sql(_facts(sql, join_keys=[_PAIR_221]), facts, db_id="itam")
        assert declared["join_key_miss"] == [["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]]
        assert declared["join_alias_unresolved"] is False
        assert declared["join_group_company"] is False
        assert {k: v for k, v in declared.items() if not k.startswith("join_key_m")
                and not k.startswith("join_alias") and not k.startswith("join_group")} == plain


def _record(**expected: Any) -> dict[str, Any]:
    return {
        "id": "ITAM-221", "turn": 1, "repeat": 1,
        "expected": expected,
        "sql_analysis": {"tables": ["tcdmsif52", "tcdmsif72"], "join_key_miss": [],
                         "join_alias_unresolved": False, "join_group_company": True},
        "schema_context": {"presented_tables": ["tcdmsif72"]},
    }


class TestJoinReport:
    def test_section_absent_without_declarations(self) -> None:
        record = _record(gold_tables=["tcdmsif72"])
        assert rp.join_section([record]) == []

    def test_section_rows(self) -> None:
        record = _record(
            gold_tables=["tcdmsif72", "tcdmsif52"],
            join_keys=[["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]],
        )
        miss = _record(gold_tables=["tcdmsif72", "tcdmsif52"],
                       join_keys=[["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]])
        miss["turn"] = 2
        miss["sql_analysis"]["join_key_miss"] = [["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]]
        lines = rp.join_section([record, miss])
        text = "\n".join(lines)
        assert "## 10. 조인 판정" in lines
        assert "조인 키 선언 턴 2 · `join_key_miss` 턴 1." in text
        assert (
            "| ITAM-221 | 1 | 1 | tcdmsif72, tcdmsif52 | tcdmsif52, tcdmsif72 | 1.00 | 1.00 | 없음 "
            "| — | 예 | tcdmsif52 |"
        ) in text
        assert "tcdmsif52.호스트명 = tcdmsif72.서버호스트명" in text

    def test_gold_paths_best_path(self) -> None:
        record = _record(gold_tables=["tcdmsif80", "tcdmsif52"],
                         gold_paths=[["tcdmsif80", "tcdmsif52"], ["tcdmsif72", "tcdmsif52"]])
        assert rp.table_scores(record) == (1.0, 1.0, ["tcdmsif72", "tcdmsif52"])
        single = _record(gold_tables=["tcdmsif80", "tcdmsif52"])
        assert rp.table_scores(single) == (0.5, 0.5, ["tcdmsif80", "tcdmsif52"])

    def test_render_report_appends_section(self) -> None:
        record = _record(gold_tables=["tcdmsif72", "tcdmsif52"],
                         join_keys=[["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]])
        record.update(category="c", status="ok", taxonomy=[])
        body = rp.render_report({"run_id": "x"}, {}, [record])
        assert "\n## 10. 조인 판정\n" in body


# ──────────────────────────────────────────────
# (7) 판정 칸 이름 검사 · require_judgement
# ──────────────────────────────────────────────

_SCHEMA_NAMES = {
    "tcdmsif52": ["호스트명", "시리얼번호", "그룹회사코드"],
    "tcdmsif51": ["시리얼번호"],
    "tcdmsif72": ["서버호스트명"],
}


def _scenarios(tmp_path: Path, body: dict[str, Any]) -> Path:
    path = tmp_path / "scenarios.yaml"
    _write(path, body)
    return path


def _scenario(sid: str = "ITAM-221", **extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": sid, "title": "t", "category": "c", "env": ["closed"],
        "turns": [{"send": {"query": "서버 목록 보여줘"},
                   "expect": {"observe": {"what": "목록", "no_data": False}}}],
    }
    raw.update(extra)
    return raw


class TestLoader:
    def test_schema_names_extend_judgement_names(self, tmp_path: Path) -> None:
        body = {"scenarios": [_scenario(
            gold_tables=["tcdmsif72", "tcdmsif52"],
            key_columns=["호스트명"],
            join_keys=[["tcdmsif52.호스트명", "tcdmsif72.서버호스트명"]],
        )]}
        path = _scenarios(tmp_path, body)
        with pytest.raises(cat.CatalogError):
            cat.load_scenarios(path, _POLICY)
        (scenario,) = cat.load_scenarios(path, _POLICY, schema_names=_SCHEMA_NAMES)
        turn = scenario.turns[0]
        assert scenario.join_keys_for(turn) == [_PAIR_221]
        assert scenario.gold_tables_for(turn) == ["tcdmsif72", "tcdmsif52"]

    def test_unknown_names_rejected(self, tmp_path: Path) -> None:
        body = {"scenarios": [_scenario(
            gold_tables=["tcdmsif99"],
            key_columns=["없는칸"],
            join_keys=[["tcdmsif52.없는칸", "tcdmsif72.서버호스트명"],
                       ["tcdmsif52.호스트명", "tcdmsif52.호스트명"],
                       ["tcdmsif52.호스트명"]],
        )]}
        with pytest.raises(cat.CatalogError) as caught:
            cat.load_scenarios(_scenarios(tmp_path, body), _POLICY, schema_names=_SCHEMA_NAMES)
        text = str(caught.value)
        assert "gold_tables 의 `tcdmsif99` 는 카탈로그에 없다" in text
        assert "key_columns 의 `없는칸` 은 카탈로그에 없다" in text
        assert "join_keys 의 `tcdmsif52.없는칸` 은 카탈로그에 없다" in text
        assert "양쪽이 같은 칸이다" in text
        assert "[\"표.칸\", \"표.칸\"] 두 개" in text

    def test_gold_paths_list_of_lists(self, tmp_path: Path) -> None:
        body = {"scenarios": [_scenario(
            gold_tables=[["tcdmsif72", "tcdmsif52"], ["tcdmsif51", "tcdmsif52"]],
        )]}
        (scenario,) = cat.load_scenarios(
            _scenarios(tmp_path, body), _POLICY, schema_names=_SCHEMA_NAMES
        )
        turn = scenario.turns[0]
        assert scenario.gold_paths_for(turn) == [
            ["tcdmsif72", "tcdmsif52"], ["tcdmsif51", "tcdmsif52"]
        ]
        assert scenario.gold_tables_for(turn) == ["tcdmsif72", "tcdmsif52"]
        assert cli._expected(turn, [], ["tcdmsif72", "tcdmsif52"],
                             scenario.gold_paths_for(turn), [])["gold_paths"] == [
            ["tcdmsif72", "tcdmsif52"], ["tcdmsif51", "tcdmsif52"]
        ]

    def test_mixed_gold_rejected(self, tmp_path: Path) -> None:
        body = {"scenarios": [_scenario(gold_tables=["tcdmsif72", ["tcdmsif52"]])]}
        with pytest.raises(cat.CatalogError, match="허용 경로 묶음"):
            cat.load_scenarios(_scenarios(tmp_path, body), _POLICY, schema_names=_SCHEMA_NAMES)

    def test_require_judgement(self, tmp_path: Path) -> None:
        no_data = _scenario("ITAM-229")
        no_data["turns"][0]["expect"]["observe"]["no_data"] = True
        body = {"require_judgement": True, "scenarios": [_scenario(), no_data]}
        with pytest.raises(cat.CatalogError) as caught:
            cat.load_scenarios(_scenarios(tmp_path, body), _POLICY, schema_names=_SCHEMA_NAMES)
        assert caught.value.errors == [
            "ITAM-221 턴1: require_judgement — gold_tables 가 비었다",
            "ITAM-221 턴1: require_judgement — key_columns 가 비었다",
        ]
        filled = {"require_judgement": True, "scenarios": [
            _scenario(gold_tables=["tcdmsif72"], key_columns=["서버호스트명"]), no_data,
        ]}
        assert len(cat.load_scenarios(
            _scenarios(tmp_path, filled), _POLICY, schema_names=_SCHEMA_NAMES
        )) == 2

    def test_require_judgement_must_be_bool(self, tmp_path: Path) -> None:
        body = {"require_judgement": "yes", "scenarios": [_scenario()]}
        with pytest.raises(cat.CatalogError, match="require_judgement 는 불리언"):
            cat.load_scenarios(_scenarios(tmp_path, body), _POLICY)

    def test_schema_snapshot_reader(self, tmp_path: Path) -> None:
        path = tmp_path / "itam_schema.json"
        path.write_text(json.dumps({"schema": {"tables": {
            "tcdmsif52": {"columns": [{"name": "호스트명"}, {"name": "시리얼번호"}]},
        }}}, ensure_ascii=False), encoding="utf-8")
        assert cat.load_schema_names(path) == {"tcdmsif52": ["호스트명", "시리얼번호"]}
        assert cat.load_schema_names(tmp_path / "없음.json") == {}
