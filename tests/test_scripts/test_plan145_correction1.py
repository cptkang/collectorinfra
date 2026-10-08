"""plans/145 교정 1차 — 2단계 run · 가짜 값 통과 예외 제거 · IP 전체 계층 가짜 · 판정 상세 값 치환 ·
문자 군 · 긴 값 · 낮은 항목 · F9(테이블 자리) · F12(분류 필요 컬럼 낱말 단위).

실 LLM·DB 0. 가짜 값은 난수라 값이 아니라 성질(모양·원값 부재·등록부·같은 원값 같은 가짜 값)로
단언한다. 결정적이어야 하는 곳(2단계 충돌 회피)은 난수 함수를 바꿔 끼운다.
"""

from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH, SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import substitute as sb
from scripts.itam_bench.substitute import FakeValues
from tests.test_scripts.test_itam_bench_driver import _OWNER_SQL, FakeClient, FakeTail, _result

_NAME2 = "박서준"
_IP4 = re.compile(r"\d+\.\d+\.\d+\.\d+")


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _catalog() -> dict[str, Any]:
    columns = [{"name": "sevrHostName"}, {"name": "rspblPsnEmnm"}, {"name": "iPCtnt"}]
    return {
        "tables": {"TCDMSIF80": {"columns": columns, "relations": [], "key": []}},
        "same_key_groups": [],
    }


def _start(policy: cat.ColumnPolicy) -> tuple[rd.PiiVault, FakeValues, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=policy,
        catalog_doc=_catalog(),
        user_values={"login_id": "5488923", "os_user": "zqtester", "host": "zq-buildbox"},
        p1_draft=None,
    )


def _gate(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> rd.LeakGate:
    return rd.LeakGate(policy=policy, vault=vault, user_values={"login_id": "5488923"})


def _hits(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [v for v in violations if v["rule"] == "substitution"]


# --- 1. run 단위 2단계 ---------------------------------------------------------------


class TestTwoPhase:
    def test_later_original_is_never_issued(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """뒤 턴의 원값(김민수)을 앞 턴 가짜 값으로 먼저 뽑으려 해도 1차 등록이 막는다."""
        fakes = FakeValues()
        vault = rd.PiiVault(fakes=fakes)
        draws = iter(["김민수", "최영희"])
        monkeypatch.setattr(sb, "_draw", lambda segment: next(draws))
        ctx = cli.RunContext(
            run_id="r", tier=None, policy=cat.ColumnPolicy(db_id="itam", scope="t", tables={}),
            catalog=jd.CatalogFacts.from_catalog({"tables": {}}), vault=vault,
            oracle=lambda *a: {},
        )  # fmt: skip
        pending = [
            cli.PendingTurn("completed", None, [], lambda: {"x": vault.substitute(_NAME2)},
                            lambda: [_NAME2]),
            cli.PendingTurn("completed", None, [], lambda: {"cell": "김민수"}, lambda: ["김민수"]),
        ]  # fmt: skip
        records = cli.finish_turns(pending, ctx)
        assert records[0] == {"x": "최영희"} and fakes.collisions() == 0

    def test_single_phase_would_collide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """대조 — 등록 없이 차례로 내면 같은 상황이 관문 ① 충돌이 된다(2단계가 필요한 근거)."""
        fakes = FakeValues()
        draws = iter(["김민수", "최영희"])
        monkeypatch.setattr(sb, "_draw", lambda segment: next(draws))
        assert fakes.fake(_NAME2) == "김민수"
        fakes.fake("김민수")
        assert fakes.collisions() == 1

    def test_run_scenarios_two_phase_records(self, policy: cat.ColumnPolicy) -> None:
        """실 턴 루프 — 생성기가 있어도 산출 레코드에 원값이 없고 충돌 0 · 관문 통과."""
        scenarios = {s.id: s for s in cat.load_scenarios(SCENARIOS_PATH, policy)}
        vault, fakes, run_policy = _start(policy)
        client = FakeClient(
            {("ITAM-06", 1): {"result": _result(
                ["호스트명", "담당부점", "담당자"], [["svr-db-03", "합성부점", _NAME2]])}}
        )  # fmt: skip
        audit = FakeTail({"ITAM-06": [{
            "sql": _OWNER_SQL, "source": "itam", "success": False, "row_count": 0,
            "retry_attempt": 0, "error": f"near '{_NAME2} 10.0.3.3' at line 1",
        }]})  # fmt: skip
        ctx = cli.RunContext(
            run_id="r", tier=None, policy=run_policy,
            catalog=jd.CatalogFacts.from_catalog(_catalog()), vault=vault,
            oracle=lambda *a: {"status": "ok", "reason": None, "rows_by_db": {"itam": [{"n": 1}]},
                               "elapsed_ms": 1.0, "phase": "post", "limit_by_db": {}},
        )  # fmt: skip
        records = cli.run_scenarios(
            [scenarios["ITAM-06"]], client=client, audit=audit, capture=FakeTail({}), ctx=ctx,
            progress=lambda _t: None,
        )  # fmt: skip
        text = json.dumps(records, ensure_ascii=False)
        assert _NAME2 not in text and "10.0.3.3" not in text
        assert fakes.fake(_NAME2) in text  # SQL·오류·결과 표본이 같은 가짜 값
        assert fakes.collisions() == 0
        assert ctx.counters["turns"] == 1  # 카운터는 턴 중에 한 번만
        gate = _gate(policy, vault)
        assert gate.check({"trace.jsonl": text.strip("[]") + "\n"}) == []


# --- 2. HIGH-1 가짜 값 통과 예외 제거 (t1 · t4 이식) ----------------------------------------


class TestNoFakePassThrough:
    def test_t1_ip_original_equal_to_earlier_fake(self) -> None:
        for _ in range(50):
            fakes = FakeValues()
            vault = rd.PiiVault(fakes=fakes)
            fake = vault.mask_ip("host 10.1.1.5").split()[-1]
            out = vault.mask_ip("peer " + fake)
            assert out != "peer " + fake  # 원값으로 다시 치환된다
            assert fakes.collisions() >= 1  # 관문 ①이 닫힌 쪽으로 잡는다

    def test_t1_quoted_fake_in_error_text(self) -> None:
        for _ in range(50):
            fakes = FakeValues()
            vault = rd.PiiVault(fakes=fakes)
            earlier = vault.substitute("ab12")
            text = f"Duplicate entry '{earlier}' for key"
            out = rd.redact_text(text, vault=vault, sql="select 1")
            assert f"'{earlier}'" not in out

    @pytest.mark.parametrize("count", [10, 20, 40])
    def test_t4_no_raw_ip_in_subnet(self, count: int) -> None:
        for _ in range(30):
            fakes = FakeValues()
            vault = rd.PiiVault(fakes=fakes)
            for octet in random.sample(range(1, 255), count):
                ip = f"10.20.30.{octet}"
                assert vault.mask_ip(ip) != ip
            assert fakes.collisions() == 0


# --- 3. IP 전체 계층 가짜 ----------------------------------------------------------------


class TestIpHierarchy:
    def test_same_ip_same_fake_across_sql_error_sample(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        sql = "SELECT 1 FROM TCDMSIF80 WHERE iPCtnt = '10.0.3.3'"
        out_sql = rd.redact_sql(sql, policy=run_policy, vault=vault)
        out_err = rd.redact_text("host 10.0.3.3 unreachable", vault=vault, sql=sql)
        summary = rd.summarize_result(
            _result(["iPCtnt"], [["10.0.3.3"]]),
            sources={"iPCtnt": ["iPCtnt"]}, policy=run_policy, vault=vault,
        )  # fmt: skip
        fake = fakes.fake_ip("10.0.3.3")
        assert f"'{fake}'" in out_sql and f"host {fake} unreachable" == out_err
        assert summary["columns"][0]["sample"] == [fake]
        first = int(fake.split(".")[0])
        assert 1 <= first <= 223 and first != 127 and not fake.startswith("10.")

    def test_prefix_literal_uses_same_memo(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            "SELECT 1 FROM TCDMSIF80 WHERE iPCtnt LIKE '10.0.1.%' OR iPCtnt = '10.0.1.4'",
            policy=run_policy, vault=vault,
        )  # fmt: skip
        fake = fakes.fake_ip("10.0.1.4")
        assert f"'{fake.rsplit('.', 1)[0]}.%'" in out and f"'{fake}'" in out
        assert "10.0.1" not in out

    def test_prompt_word_prefix_kept(self, policy: cat.ColumnPolicy) -> None:
        vault, _fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            "SELECT 1 FROM TCDMSIF80 WHERE iPCtnt LIKE '10.0.%'",
            policy=run_policy, vault=vault, prompt="10.0. 대역 서버",
        )  # fmt: skip
        assert "'10.0.%'" in out

    def test_without_generator_mask_ip_unchanged(self) -> None:
        assert rd.PiiVault().mask_ip("a 10.0.1.4 b") == rd.mask_ip("a 10.0.1.4 b")


# --- 4. HIGH-2 판정 상세 값 · 관문 양성 검사 확장 -------------------------------------------


_ARGMAX = {
    "compare": "argmax",
    "oracle_top": [["svr-db-03", 98.5], ["svr-db-04", 91.25]],
    "system_top": [["svr-db-03"]],
    "missing_must": [["svr-db-04"]],
    "value_diffs": [{"key": ["svr-db-03"], "oracle": 98.5, "system": 77.0}],
}


class TestOracleDetail:
    def test_non_general_values_faked_with_generator(self) -> None:
        fakes = FakeValues()
        out = jd.sanitize_detail(_ARGMAX, value_grade="pii", keys_allowed=True, fakes=fakes)
        values = [item[-1] for item in out["oracle_top"]]
        assert 98.5 not in values and 91.25 not in values
        assert all(fakes.is_fake(str(v)) for v in values)
        assert out["oracle_top"][0][0] == "svr-db-03"  # 키는 허용(일반)이면 그대로
        diff = out["value_diffs"][0]
        assert diff["oracle"] == values[0] and fakes.is_fake(str(diff["system"]))
        fields = jd.substituted_fields(_ARGMAX, value_grade="pii", keys_allowed=True)
        assert fields == ["oracle_top.value", "value_diffs.value"]

    def test_none_grade_is_strictest(self) -> None:
        fields = jd.substituted_fields(_ARGMAX, value_grade=None, keys_allowed=False)
        assert {"oracle_top.key", "oracle_top.value", "value_diffs.key", "missing_must"} <= set(
            fields
        )
        assert jd.substituted_fields(_ARGMAX, value_grade="general", keys_allowed=True) == []

    def test_value_compare_scalars(self) -> None:
        fakes = FakeValues()
        detail = {"compare": "value", "tol": 0.0, "oracle": 1500.0, "system": 1500}
        out = jd.sanitize_detail(detail, value_grade="amount", keys_allowed=True, fakes=fakes)
        assert out["tol"] == 0.0 and fakes.is_fake(str(out["oracle"]))
        assert str(out["oracle"]).split(".")[0] == str(out["system"])  # Low-3 같은 수 같은 가짜

    def test_without_generator_unchanged(self) -> None:
        amount = jd.sanitize_detail(_ARGMAX, value_grade="amount", keys_allowed=True)
        assert amount["oracle_top"] == [["svr-db-03"], ["svr-db-04"]]
        # 89773af 동작 — 금액 밖 등급은 생성기 없이 값이 남는다(보고만 · 바꾸지 않음)
        pii = jd.sanitize_detail(_ARGMAX, value_grade="pii", keys_allowed=True)
        assert pii["oracle_top"] == _ARGMAX["oracle_top"]

    def _record(self, oracle: dict[str, Any]) -> str:
        return json.dumps({"oracle": oracle}, ensure_ascii=False) + "\n"

    def test_gate_checks_substituted_fields(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        detail = jd.sanitize_detail(_ARGMAX, value_grade="pii", keys_allowed=False, fakes=fakes)
        fields = jd.substituted_fields(_ARGMAX, value_grade="pii", keys_allowed=False)
        gate = _gate(policy, vault)
        ok = {"keys_recorded": False, "substituted_fields": fields, "detail": detail}
        assert _hits(gate.check({"trace.jsonl": self._record(ok)})) == []
        leaked = {**ok, "detail": {**detail, "oracle_top": [["svr-db-03", 98.5]]}}
        assert _hits(gate.check({"trace.jsonl": self._record(leaked)})) == [
            {"file": "trace.jsonl", "record": 1, "field": "oracle.detail.oracle_top",
             "rule": "substitution"}
        ]  # fmt: skip
        # 키를 남기지 않는 기록이 키 목록을 싣는데 치환 칸 표지가 없으면 위반
        unmarked = {**ok, "substituted_fields": []}
        assert _hits(gate.check({"trace.jsonl": self._record(unmarked)}))
        # 생성기 없이 치환 칸 표지가 있으면 닫힌 쪽 실패
        bare = rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})
        assert _hits(bare.check({"trace.jsonl": self._record(ok)}))

    def test_run_json_user_fields(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        gate = _gate(policy, vault)
        run = {"login_user": fakes.fake("5488923"), "operator": fakes.fake("zqtester"),
               "host": None}  # fmt: skip
        assert _hits(gate.check({"run.json": json.dumps(run)})) == []
        raw = {**run, "operator": "someone"}
        assert _hits(gate.check({"run.json": json.dumps(raw)})) == [
            {"file": "run.json", "record": None, "field": "operator", "rule": "substitution"}
        ]

    def test_report_uses_run_json_fakes(self, policy: cat.ColumnPolicy) -> None:
        """`report.md` 머리의 사용자 칸은 `run.json`과 같은 가짜 값이다(같은 run_meta 를 그린다)."""
        vault, fakes, _ = _start(policy)
        meta = {"run_id": "r", "env": "sandbox", "login_user": fakes.fake("5488923"),
                "operator": fakes.fake("zqtester"), "host": fakes.fake("zq-buildbox"),
                "git": {"sha": "abc", "dirty": False}, "scenarios": 0, "repeat": 1}  # fmt: skip
        staged = cli.build_artifacts(run_meta=meta, catalog_doc=_catalog(), records=[])
        assert f"`{meta['login_user']}`" in staged["report.md"]
        assert f"`{meta['operator']}`" in staged["report.md"]
        assert "5488923" not in staged["report.md"] and "zqtester" not in staged["report.md"]
        assert _hits(_gate(policy, vault).check({"run.json": staged["run.json"]})) == []


# --- 5. MEDIUM-2 문자 군 (t9) ------------------------------------------------------------


def _family(ch: str) -> str:
    name = unicodedata.name(ch, "")
    for word in ("HANGUL", "CJK", "HIRAGANA", "KATAKANA", "CYRILLIC", "GREEK"):
        if word in name:
            return word
    return unicodedata.category(ch)[0]


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("金鐵洙1", ["CJK", "CJK", "CJK", "digit"]),
        ("ＡＢＣ１２", ["ascii", "ascii", "ascii", "digit", "digit"]),
        ("ЖЕНЯ Иванов", ["CYRILLIC"] * 4 + ["Z"] + ["CYRILLIC"] * 6),
        ("José", ["ascii"] * 4),
        ("ひらがなカタ", ["HIRAGANA"] * 4 + ["KATAKANA"] * 2),
        ("ㄱㄴ-ΑΒ", ["HANGUL", "HANGUL", "P", "GREEK", "GREEK"]),
    ],
)
def test_t9_no_original_character_class_survives(original: str, expected: list[str]) -> None:
    out = FakeValues().fake(original)
    assert len(out) == len(original) and out != original
    for o, f, want in zip(original, out, expected, strict=True):
        if want == "digit":
            assert f in "0123456789"
        elif want == "ascii":  # ASCII 대문자 원글만 대문자로 다시 입힌다
            assert f.isascii() and f.isalpha() and f.isupper() == ("A" <= o <= "Z")
        elif want in ("P", "Z"):
            assert f == o  # 구조 글자(P*·Z*·S*)는 그대로
        else:
            assert _family(f) == want and f.isupper() == o.isupper()


# --- 6. MEDIUM-3 긴 값 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "contact a@b.co " * 1400,  # 20KB 이메일 모양
        "contact a@b.co " * 14000,  # 200KB 이메일 모양
        "010-1234-5678 " * 1500,  # 20KB 전화 모양
        "010-1234-5678 " * 14500,  # 200KB 전화 모양
    ],
)
def test_long_pii_shaped_value_is_fast(policy: cat.ColumnPolicy, value: str) -> None:
    vault, fakes, _ = _start(policy)
    rd._scrub_free_text("a@b.co")  # 설정 1회 적재를 재지 않는다
    started = time.perf_counter()
    out = fakes.fake(value)
    assert time.perf_counter() - started <= 0.5
    assert out != value and not rd._pii_regex_hit(out)


# --- 7. 낮은 항목 ------------------------------------------------------------------------


class TestLowItems:
    def test_effective_policy_keeps_original_unclassified(self, policy: cat.ColumnPolicy) -> None:
        """Low-1 — 「분류 필요 컬럼」 판단은 원 정책, 등급만 유효 정책."""
        catalog_doc = {
            "tables": {
                "TCDMSIF80": {"columns": [{"name": "rspblPsnEmnm"}], "relations": [], "key": []},
                "ZZNEW": {"columns": [{"name": "zzRef"}], "relations": [
                    {"from": "ZZNEW", "to": "TCDMSIF80", "columns": [["zzRef", "rspblPsnEmnm"]]}
                ], "key": []},
            },
            "same_key_groups": [],
        }  # fmt: skip
        effective = sb.effective_policy(policy, catalog_doc)
        assert effective.grade("zzRef", "ZZNEW") == "pii"  # 등급은 류 등급으로 오른다
        assert "zzRef" not in effective.column_names()  # 아는 컬럼 목록은 원 정책
        sources = rd.resolve_result_columns(
            ["zzRef"], ["SELECT zzRef FROM ZZNEW"], effective.column_names()
        )
        assert sources == {"zzRef": []}  # 정책 밖 그대로 → 미분류 열
        assert rd.resolve_result_columns(
            ["c"], ["SELECT zzRef AS c FROM ZZNEW"], effective.column_names()
        ) == {"c": ["?:zzRef"]}

    def test_zero_fraction_same_fake(self) -> None:
        """Low-3 — 1500 과 1500.0 은 같은 가짜 정수부(소수 모양은 다시 입힌다)."""
        fakes = FakeValues()
        whole, decimal = fakes.fake("1500"), fakes.fake("1500.0")
        assert decimal == whole + ".0" and fakes.is_fake(decimal) and fakes.is_fake(whole)
        number = jd._fake_number(1500, fakes)
        assert str(number) == whole
        assert str(jd._fake_number(1500.0, fakes)) == decimal

    def test_primary_kept_with_generator(self) -> None:
        vault = rd.PiiVault(fakes=FakeValues())
        out = rd.redact_text("Duplicate entry 'zz9q' for key 'PRIMARY'", vault=vault)
        assert out.endswith("'PRIMARY'") and "zz9q" not in out
        # 생성기 없는 경로는 기준선 그대로(verifier 고정)
        bare = rd.redact_text("Duplicate entry 'zz9q' for key 'PRIMARY'", vault=rd.PiiVault())
        assert "'PRIMARY'" not in bare


# --- F9 테이블 자리 ---------------------------------------------------------------------


_CATALOG_COLUMNS = ["운영체제타입내용"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT `운영체제타입내용` AS os_type, COUNT(*) AS c FROM `tcdmsif72` "
        "GROUP BY `운영체제타입내용`",
        "SELECT `운영체제타입내용` AS os_type, COUNT(*) AS c FROM `tcdmsif72` AS t "
        "GROUP BY `운영체제타입내용`",
        "SELECT COUNT(*) FROM `tcdmsif72` GROUP BY `운영체제타입내용`",
        "SELECT `운영체제타입내용` FROM `tcdmsif72`",
        "SELECT a.`운영체제타입내용` FROM `tcdmsif80` a JOIN `tcdmsif72` b ON a.x = b.x "
        "GROUP BY `운영체제타입내용`",
        "SELECT s.c FROM (SELECT `운영체제타입내용` AS c FROM `tcdmsif72` "
        "GROUP BY `운영체제타입내용`) AS s",
        "SELECT COUNT(*) FROM `tcdmsif80` a, `tcdmsif72` b GROUP BY `운영체제타입내용`",
    ],
)
@pytest.mark.parametrize("generating", [False, True])
def test_f9_table_names_kept(sql: str, generating: bool) -> None:
    vault = rd.PiiVault(fakes=FakeValues() if generating else None)
    out = rd.redact_sql(sql, policy=None, vault=vault, catalog_columns=_CATALOG_COLUMNS)
    assert out == sql  # 이름은 가리지도 바꾸지도 않는다


def test_f9_value_slot_still_masked() -> None:
    sql = "SELECT COUNT(*) FROM t WHERE `운영체제타입내용` = `홍길동`"
    out = rd.redact_sql(sql, policy=None, vault=rd.PiiVault(), catalog_columns=_CATALOG_COLUMNS)
    assert "홍길동" not in out and "`운영체제타입내용`" in out


# --- F12 분류 필요 컬럼 --------------------------------------------------------------------


def test_f12_mixed_names_not_cut_to_ascii(policy: cat.ColumnPolicy) -> None:
    sql = (
        "SELECT a.`IP주소내용` AS IP주소, v.`CPU용량` AS cpu_capacity, `서버호스트명`, "
        "`홍길동` AS x FROM `tcdmsif79` AS a"
    )
    names = ["IP주소", "cpu_capacity", "서버호스트명", "x"]
    sources = rd.resolve_result_columns(
        names, [sql], policy.column_names(),
        catalog_columns=["IP주소내용", "CPU용량", "서버호스트명"],
    )  # fmt: skip
    assert sources == {
        "IP주소": ["?:IP주소내용"],
        "cpu_capacity": ["?:CPU용량"],
        "서버호스트명": ["?:서버호스트명"],
        "x": ["?"],  # 카탈로그 밖 비ASCII 낱말은 이름 없이
    }
    summary = rd.summarize_result(
        _result(names, [["10.1.2.3", "8", "h1", "v"]]),
        sources=sources, policy=policy, vault=rd.PiiVault.from_policy(policy),
    )  # fmt: skip
    assert rd.unclassified_columns(summary) == ["CPU용량", "IP주소내용", "서버호스트명"]
    assert [c["name"] for c in summary["columns"]][2] == "서버호스트명"  # 카탈로그 이름은 라벨로
