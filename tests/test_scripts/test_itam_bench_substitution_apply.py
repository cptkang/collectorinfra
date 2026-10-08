"""plans/145 W2 — 가리던 자리의 형식 보존 가짜 값 적용 · run 배선 · 관문 `substitution` · 반출 표기.

수용 기준 A1(L1~L9·L11 지점별 원값 부재 + 같은 모양 가짜 값) · A2(같은 원값 → SQL·결과·오류 문구·
판정 키·코드값 파일에서 같은 가짜 값) · A3(관계 등가류 — 일반 컬럼도 치환 · 조인 유지) · A5(관문
`substitution` ①②③ 주입 위반 검출) · A7(`substitutions.yaml`·`run.json`·`report.md` 표기 ·
`write_gated` 종단). 실 LLM·DB 0. 가짜 값은 난수라 값이 아니라 성질(모양·원값 부재·등록부)로
단언한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import code_samples as cs
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench.substitute import FakeValues

_NAME = "홍길동"  # 정책 카나리아
_NAME2 = "박서준"  # 결과에서 수집하는 합성 성명
_EMPID = "T000003"  # 카나리아 패턴(T + 6자리)
_HANGUL = re.compile(r"^[가-힣]+$")


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _catalog(relations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    columns = [{"name": "sevrHostName"}, {"name": "rspblPsnEmnm"}, {"name": "zzzMemo"}]
    return {
        "tables": {
            "TCDMSIF80": {"columns": columns, "relations": relations or [], "key": []},
            "TCDMSIF79": {"columns": [{"name": "sevrHostName"}], "relations": [], "key": []},
        },
        "same_key_groups": [],
    }


def _start(
    policy: cat.ColumnPolicy,
    catalog_doc: dict[str, Any] | None = None,
    user_values: dict[str, str | None] | None = None,
    p1_draft: dict[str, Any] | None = None,
) -> tuple[rd.PiiVault, FakeValues, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=policy,
        catalog_doc=catalog_doc or _catalog(),
        user_values=user_values or {"login_id": "5488923"},
        p1_draft=p1_draft,
    )


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, row)) for row in rows],
        "total_rows": len(rows),
        "truncated": False,
        "reason": None,
    }


def _shape(original: str, fake: str) -> None:
    """같은 모양의 가짜 값 — 길이·숫자/라틴/한글 위치·그 밖 문자 그대로 · 원값과 다름."""
    assert fake != original and len(fake) == len(original), (len(original), len(fake))
    for o, f in zip(original, fake, strict=True):
        if o.isdigit():
            assert f.isdigit()
        elif o.isascii() and o.isalpha():
            assert f.isascii() and f.isalpha() and f.isupper() == o.isupper()
        elif _HANGUL.match(o):
            assert _HANGUL.match(f)
        else:
            assert f == o


def _only(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    assert match, text
    return match.group(1)


# --- A1 지점별 --------------------------------------------------------------------


class TestPoints:
    def test_l1_masked_literal_becomes_fake(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            f"SELECT sevrHostName FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}'",
            policy=run_policy,
            vault=vault,
        )
        fake = _only(out, r"rspblPsnEmnm = '([^']*)'")
        assert _NAME2 not in out and rd.MASK not in out
        _shape(_NAME2, fake)
        assert fakes.is_fake(fake)

    def test_l2_comment_content_becomes_fake(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            "SELECT 1 FROM TCDMSIF80 /* 담당 김민수 확인 */ -- svr memo 7", policy=run_policy,
            vault=vault,
        )
        block = _only(out, r"/\*(.*?)\*/")
        line = _only(out, r"-- (.*)$")
        assert "김민수" not in out and "memo" not in out and rd.MASK not in out
        _shape("담당 김민수 확인", block)
        _shape("svr memo 7", line)
        assert fakes.is_fake(block) and fakes.is_fake(line)

    def test_l3_identifier_slot_value_becomes_fake(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            "SELECT * FROM TCDMSIF80 WHERE sevrHostName = 김민수", policy=run_policy, vault=vault
        )
        word = _only(out, r"sevrHostName = (\S+)")
        assert "김민수" not in out
        _shape("김민수", word)
        assert fakes.is_fake(word)

    def test_l4_vault_values_and_canaries_become_fakes(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        vault.add(_NAME2)
        out = rd.scrub_tree({"a": f"담당 {_NAME2} · {_NAME} · {_EMPID}"}, vault)["a"]
        for value in (_NAME2, _NAME, _EMPID):
            assert value not in out
        assert rd.MASK_PII not in out
        parts = out.split(" · ")
        _shape(_NAME2, parts[0].removeprefix("담당 "))
        _shape(_NAME, parts[1])
        _shape(_EMPID, parts[2])
        assert parts[0].removeprefix("담당 ") == fakes.fake(_NAME2)  # 같은 원값 → 같은 가짜 값

    def test_l5_kept_literal_person_value_becomes_fake(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        vault.add(_NAME2)
        out = rd.redact_sql(
            f"SELECT 1 FROM TCDMSIF80 WHERE sevrHostName LIKE '%{_NAME2}%'",
            policy=run_policy,
            vault=vault,
        )
        assert _NAME2 not in out and rd.MASK_PII not in out
        assert f"'%{fakes.fake(_NAME2)}%'" in out  # 값 자리만 가짜 — 결과 표본과 같은 값

    def test_l6_error_text_fragments_become_fakes(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        sql = f"SELECT sevrHostNm FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}'"
        error = (
            "(1054, \"Unknown column 'sevrHostNm' in 'field list'\") "
            f"near 'zq {_NAME2} LIMIT 5' at line 1 · '{_NAME2}' · 'abc-xyz'"
        )
        out = rd.redact_text(error, vault=vault, sql=sql, prompt="")
        assert _NAME2 not in out and "abc-xyz" not in out and rd.MASK not in out
        assert "'sevrHostNm'" in out and "'field list'" in out  # 식별자·고정 어구는 남는다
        near = _only(out, r"near '(.*?)' at line 1")
        assert fakes.is_fake(near)
        assert f"'{fakes.fake(_NAME2)}'" in out  # 가린 리터럴 내용 — SQL 과 같은 가짜 값
        sql_out = rd.redact_sql(sql, policy=run_policy, vault=vault)
        assert f"= '{fakes.fake(_NAME2)}'" in sql_out
        quoted = re.findall(r"'([^']*)'", out)[-1]
        _shape("abc-xyz", quoted)  # 근거 없는 따옴표 조각
        # 가짜 값 통과 예외는 없다(교정 1차 HIGH-1) — 산출 경로는 같은 글을 두 번 가리지 않는다
        assert fakes.collisions() == 0

    def test_l7_non_general_columns_carry_fake_samples(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        sql = (
            "SELECT rspblPsnEmnm AS 담당자, acqsiAmt AS 금액, byCtrcName, zzzMemo, iPCtnt, "
            "rspblBrnName AS 담당부점 FROM TCDMSIF80"
        )
        columns = ["담당자", "금액", "byCtrcName", "zzzMemo", "iPCtnt", "담당부점"]
        rows = [
            [_NAME2, 10300000, "합성 구매계약 A", "메모 하나", "10.0.3.3", _NAME2],
            [_NAME, 10400000, "합성 구매계약 B", "메모 둘", "10.0.3.4", "합성부점"],
        ]
        summary = rd.summarize_result(
            _result(columns, rows),
            sources=rd.resolve_result_columns(columns, [sql], run_policy.column_names()),
            policy=run_policy,
            vault=vault,
            prompt="담당자 금액",
        )
        cols = {c["name"]: c for c in summary["columns"]}
        dumped = json.dumps(summary, ensure_ascii=False)
        for value in (_NAME, _NAME2, "구매계약 A", "메모 하나", '"10.0.3.3"', '"10.0.3.4"'):
            assert value not in dumped, value
        person = cols["담당자"]
        assert person["log_policy"] == "pii" and person["distinct"] == 2  # 기존 통계 유지
        assert person["substituted"] is True and len(person["sample"]) == 2
        assert all(fakes.is_fake(v) for v in person["sample"])
        assert sorted(len(v) for v in person["sample"]) == [3, 3]
        amount = cols["금액"]
        assert amount["sum"] == 20700000 and amount["substituted"] is True  # 합계는 그대로
        assert all(v.isdigit() and len(v) == 8 for v in amount["sample"])
        assert not {"10300000", "10400000"} & set(amount["sample"])
        for name in ("byCtrcName", "zzzMemo"):
            assert cols[name]["substituted"] is True and "length" in cols[name]
            assert all(fakes.is_fake(v) for v in cols[name]["sample"])
        assert cols["zzzMemo"]["log_policy"] == "unclassified"
        demoted = cols["담당부점"]
        assert demoted["demoted"] == "pii_value_match" and demoted["substituted"] is True
        assert all(fakes.is_fake(v) for v in demoted["sample"])
        # L11 — 일반 IP 열은 IP 전체가 계층 일관 가짜(치환 표지 없음 · 교정 1차 3)
        network = cols["iPCtnt"]
        assert "substituted" not in network
        assert sorted(network["sample"]) == sorted(
            fakes.fake_ip(ip) for ip in ("10.0.3.3", "10.0.3.4")
        )
        assert len({v.rsplit(".", 1)[0] for v in network["sample"]}) == 1  # 같은 가짜 /24
        assert not any(v.startswith("10.0.3.") for v in network["sample"])

    def test_l7_categorical_columns_keep_counts(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        summary = rd.summarize_result(
            _result(["zzzMemo"], [["상태값가"], ["상태값가"], ["상태값나"]]),
            sources={"zzzMemo": ["?:zzzMemo"]},
            policy=run_policy,
            vault=vault,
        )
        top = summary["columns"][0]["top_values"]
        assert sorted(top.values()) == [1, 2] and "상태값가" not in top
        assert top[fakes.fake("상태값가")] == 2

    def test_l8_oracle_detail_keys_and_amounts_become_fakes(self) -> None:
        fakes = FakeValues()
        detail = {
            "compare": "argmax",
            "oracle_top": [["svr-a01", 1500000.0], ["svr-b02", 900.5]],
            "system_top": [["svr-a01"]],
            "missing": [["svr-b02"]],
            "value_diffs": [{"key": ["svr-a01"], "oracle": 1500000.0, "system": 1400000.0}],
        }
        out = jd.sanitize_detail(detail, value_grade="amount", keys_allowed=False, fakes=fakes)
        dumped = json.dumps(out)
        for value in ("svr-a01", "svr-b02", "1500000", "1400000", "900.5"):
            assert value not in dumped, value
        assert out["missing"] == [[fakes.fake("svr-b02")]]  # 구조 유지 · 같은 원값 같은 가짜 값
        assert out["system_top"] == [[fakes.fake("svr-a01")]]
        assert out["oracle_top"][0][0] == fakes.fake("svr-a01")
        assert isinstance(out["oracle_top"][0][1], float)
        assert out["value_diffs"][0]["key"] == [fakes.fake("svr-a01")]
        assert isinstance(out["value_diffs"][0]["system"], float)
        # 생성기 없으면 현행(건수·삭제) 그대로
        legacy = jd.sanitize_detail(detail, value_grade="amount", keys_allowed=False)
        assert legacy["missing"] == 1 and legacy["oracle_top"] == 2 and legacy["value_diffs"] == 1

    def test_l9_run_json_user_info_is_fake(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        import scripts.scenario.catalog as scenario_catalog
        import scripts.scenario.client as scenario_client
        import scripts.scenario.runner as scenario_runner
        import scripts.scenario.server as scenario_server

        seen: dict[str, Any] = {}

        class FakeHandle:
            def __init__(self, **kwargs: Any) -> None:
                self.log_path = kwargs["log_path"]

            def start(self) -> None: ...
            def wait_healthy(self) -> tuple[bool, str]:
                return True, ""
            def stop(self) -> None: ...
            def port_released(self) -> None: ...

        class FakeClient:
            def __init__(self, *_a: Any, **_k: Any) -> None: ...
            def close(self) -> None: ...

        status = SimpleNamespace(
            valid=True, reasons=[], tier="intent_orchestration", degraded_reason=None,
            auth_enabled=False, server_timeouts=None,
        )
        monkeypatch.setattr(scenario_catalog, "load_profiles", lambda: {"tier2_intent": {}})
        monkeypatch.setattr(scenario_server, "ServerHandle", FakeHandle)
        monkeypatch.setattr(scenario_server, "pick_port", lambda _p: 1)
        monkeypatch.setattr(scenario_server, "verify_profile", lambda *a, **k: status)
        monkeypatch.setattr(scenario_client, "ScenarioClient", FakeClient)
        monkeypatch.setattr(scenario_runner, "resolve_admin_credentials", lambda *_a: (None, None))
        monkeypatch.setattr(scenario_runner, "jwt_lifetime_sec", lambda *a, **k: 3600.0)
        monkeypatch.setattr(cli, "_git_provenance", lambda: {"sha": "abc", "dirty": False})
        monkeypatch.setattr(cli, "_itam_dsn", lambda: None)
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "zqtester")
        monkeypatch.setattr(cli.socket, "gethostname", lambda: "zq-buildbox")

        def fake_scenarios(*_a: Any, **kwargs: Any) -> list[dict[str, Any]]:
            seen["ctx"] = kwargs["ctx"]
            return []

        def fake_stage(**kwargs: Any) -> tuple[dict[str, str], Any]:
            seen.update(kwargs)
            return {}, None

        monkeypatch.setattr(cli, "run_scenarios", fake_scenarios)
        monkeypatch.setattr(cli, "stage_gated", fake_stage)
        monkeypatch.setattr(cli.rd, "write_gated", lambda *a: (True, []))
        args = cli.build_parser().parse_args(["--run"])
        args.scenarios = "s.yaml"
        cfg = SimpleNamespace(
            llm=SimpleNamespace(provider="mlx"), orchestrator=SimpleNamespace(provider="mlx"),
            db_backend="dbhub", dbhub=SimpleNamespace(server_url=""),
        )
        relation = {"from": "TCDMSIF80", "to": "TCDMSIF80",
                    "columns": [["byCtrcNo", "rspblPsnEmpid"]]}
        cli._run_with_server(
            args, cfg, policy, [], _catalog([relation]), None, tmp_path  # type: ignore[arg-type]
        )
        meta, fakes = seen["run_meta"], seen["vault"].fakes
        assert fakes is not None and seen["ctx"].vault is seen["vault"]
        assert meta["substitution_note"] == rd.SUBSTITUTION_NOTE
        assert meta["login_user"] is None  # 인증 꺼짐
        for key, original in (("operator", "zqtester"), ("host", "zq-buildbox")):
            assert original not in meta[key] and "***" not in meta[key]
            _shape(original, meta[key])
            assert fakes.is_fake(meta[key])
        assert seen["ctx"].policy.grade("byCtrcNo", "TCDMSIF80") == "pii"  # 유효 정책(§2.2)

    def test_l9_verify_run_json_user_info_is_fake(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        from contextlib import asynccontextmanager

        from scripts.itam_bench import asset_verify as av

        repo = tmp_path / "repo"
        (repo / "config" / "db_profiles").mkdir(parents=True)
        (repo / "config" / "db_profiles" / "itam.yaml").write_text(
            yaml.safe_dump(
                {"query_examples": [{"id": "ex1", "question": "q", "sql": "SELECT 1 FROM t"}]}
            ),
            encoding="utf-8",
        )

        class Db:
            async def execute_sql(self, _sql: str) -> Any:
                return SimpleNamespace(rows=[], truncated=False)

        @asynccontextmanager
        async def opener() -> Any:
            yield Db()

        said: list[str] = []
        monkeypatch.setattr(av.getpass, "getuser", lambda: "zqtester")
        monkeypatch.setattr(av.socket, "gethostname", lambda: "zq-buildbox")
        code = av.run_verify(
            policy=policy, env="sandbox", provenance={"sha": "abc", "dirty": False},
            user_values={"os_user": "zqtester", "host": "zq-buildbox"}, dsn=None,
            cfg=SimpleNamespace(db_backend="dbhub", dbhub=SimpleNamespace(server_url="")),
            repo_root=repo, results_root=tmp_path / "results", open_client=opener,
            say=said.append,
        )
        assert code == 0, said
        (run_dir,) = (tmp_path / "results").iterdir()
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert run["substitution_note"] == rd.SUBSTITUTION_NOTE
        for key, original in (("operator", "zqtester"), ("host", "zq-buildbox")):
            assert original not in run[key] and "***" not in run[key]
            _shape(original, run[key])

    def test_l11_ip_tail_fake_everywhere(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _start(policy)
        out = rd.redact_sql(
            "SELECT 1 FROM TCDMSIF80 WHERE iPCtnt = '10.0.3.3'", policy=run_policy, vault=vault
        )
        ip = _only(out, r"'(\d+\.\d+\.\d+\.\d+)'")
        assert not ip.startswith("10.0.3.") and fakes.is_fake(ip)
        tree = rd.scrub_tree({"a": "host 10.0.3.3 down"}, vault)
        assert tree == {"a": f"host {ip} down"}  # 같은 IP → 같은 가짜 IP
        assert fakes.collisions() == 0

    def test_without_generator_markers_unchanged(self, policy: cat.ColumnPolicy) -> None:
        vault = rd.PiiVault.from_policy(policy)
        out = rd.redact_sql(
            f"SELECT 1 FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' /* x */ AND iPCtnt = "
            "'10.0.3.3'",
            policy=policy,
            vault=vault,
        )
        assert f"'{rd.MASK}'" in out and f"/*{rd.MASK}*/" in out and "'10.0.3.***'" in out


# --- A2 전역 일관 · A3 등가류 -------------------------------------------------------


def test_a2_same_original_same_fake_across_outputs(policy: cat.ColumnPolicy) -> None:
    draft = {"assets": {"code_values": {"TCDMSIF80.asstStusDstcd": ["ACTIVE", "RETIRED"]}}}
    vault, fakes, run_policy = _start(policy, p1_draft=draft)
    sql = (
        f"SELECT rspblPsnEmnm AS 담당자 FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}' "
        "AND zzzMemo = 'active '"
    )
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault, catalog_columns=["zzzMemo"])
    summary = rd.summarize_result(
        _result(["담당자"], [[_NAME2]]),
        sources={"담당자": ["rspblPsnEmnm"]},
        policy=run_policy,
        vault=vault,
    )
    error = rd.redact_text(f"Duplicate entry '{_NAME2}'", vault=vault, sql=sql)
    detail = rd.scrub_tree(
        jd.sanitize_detail({"missing": [[_NAME2]]}, value_grade=None, keys_allowed=False,
                           fakes=vault.fakes),
        vault,
    )
    samples = cs.build_code_samples(
        draft, policy, db_id="itam", run_id="r1", comments={},
        originals=rd.CodeOriginals(cs.original_values(draft)), reject=lambda _t: False,
        fakes=fakes,
    )
    fake = fakes.fake(_NAME2)
    assert f"rspblPsnEmnm = '{fake}'" in redacted
    assert summary["columns"][0]["sample"] == [fake]
    assert error == f"Duplicate entry '{fake}'"
    assert detail["missing"] == [[fake]]
    code_fake = _only(redacted, r"zzzMemo = '([^']*)'")
    assert code_fake.rstrip() == fakes.fake("ACTIVE").lower()  # 대소문자·끝 공백만 원값을 따른다
    assert fakes.fake("ACTIVE") in samples["columns"]["TCDMSIF80.asstStusDstcd"]["values"]


def test_a3_relation_class_substitutes_general_column_and_keeps_join(
    policy: cat.ColumnPolicy,
) -> None:
    relation = {"from": "TCDMSIF80", "to": "TCDMSIF80", "columns": [["byCtrcNo", "rspblPsnEmpid"]]}
    vault, fakes, run_policy = _start(policy, _catalog([relation]))
    value = "KQ4821ZX"
    sql = f"SELECT byCtrcNo FROM TCDMSIF80 WHERE rspblPsnEmpid = '{value}'"
    redacted = rd.redact_sql(sql, policy=run_policy, vault=vault)
    columns = ["byCtrcNo"]
    sources = rd.resolve_result_columns(columns, [sql], run_policy.column_names())
    summary = rd.summarize_result(
        _result(columns, [[value]]), sources=sources, policy=run_policy, vault=vault
    )
    entry = summary["columns"][0]
    assert entry["substituted"] is True and value not in json.dumps(summary)
    assert f"rspblPsnEmpid = '{entry['sample'][0]}'" in redacted  # 한쪽 리터럴 = 다른 쪽 표본
    # 원 정책(등가류 없음)이었다면 일반 컬럼은 원값을 그대로 냈다 — 짝 노출의 근거
    plain = rd.summarize_result(
        _result(columns, [[value]]), sources=sources, policy=policy,
        vault=rd.PiiVault.from_policy(policy),
    )
    assert plain["columns"][0]["sample"] == [value]


# --- A5 관문 `substitution` --------------------------------------------------------


def _gate(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> rd.LeakGate:
    return rd.LeakGate(policy=policy, vault=vault, user_values={"login_id": "5488923"})


def _trace(entry: dict[str, Any]) -> str:
    return json.dumps({"result": {"columns": [entry]}}, ensure_ascii=False) + "\n"


def _substitution_hits(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [v for v in violations if v["rule"] == "substitution"]


class TestGateSubstitution:
    def test_rule_registered_before_code_original(self) -> None:
        assert rd.GATE_RULES[5:] == ("substitution", "code_original")

    def test_collision_fails_closed_without_values(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        fake = fakes.fake("abcdefgh")
        gate = _gate(policy, vault)
        assert _substitution_hits(gate.check({"report.md": "ok\n"})) == []
        fakes.fake(fake)  # 나중에 본 원값이 앞서 낸 가짜 값과 같다
        hits = _substitution_hits(gate.check({"report.md": "ok\n"}))
        assert hits == [{"file": None, "record": None, "field": None, "rule": "substitution"}]
        assert fake not in json.dumps(hits)

    def test_trace_positive_check(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        fake = fakes.fake("원값하나")
        ok = {"name": "x", "log_policy": "pii", "substituted": True,
              "sample": [fake, rd.MASK, fakes.fake_ip("10.0.3.3")]}
        gate = _gate(policy, vault)
        assert _substitution_hits(gate.check({"trace.jsonl": _trace(ok)})) == []
        # 생성기 경로는 IP 끝자리 가림(`a.b.c.***`)을 내지 않는다 — 허용 형태에서 뺐다(교정 1차 3)
        masked_ip = {**ok, "sample": [fake, "10.0.3.***"]}
        assert _substitution_hits(gate.check({"trace.jsonl": _trace(masked_ip)}))
        leaked = {**ok, "sample": [fake, "원값둘둘"]}
        hits = _substitution_hits(gate.check({"trace.jsonl": _trace(leaked)}))
        assert hits == [{"file": "trace.jsonl", "record": 1,
                         "field": "result.columns[0].sample", "rule": "substitution"}]
        top = {"name": "x", "log_policy": "amount", "substituted": True, "top_values": {"77": 2}}
        assert _substitution_hits(gate.check({"trace.jsonl": _trace(top)}))
        # 비일반 열이 값을 싣는데 치환 표시가 없으면 위반
        unmarked = {"name": "x", "log_policy": "pii", "sample": [fake]}
        assert _substitution_hits(gate.check({"trace.jsonl": _trace(unmarked)}))
        general = {"name": "x", "log_policy": "general", "sample": ["svr-01"]}
        assert _substitution_hits(gate.check({"trace.jsonl": _trace(general)})) == []
        # 생성기 없는 관문 — 치환 열이 있으면 닫힌 쪽 실패
        bare = rd.PiiVault.from_policy(policy)
        assert _substitution_hits(_gate(policy, bare).check({"trace.jsonl": _trace(ok)}))

    def test_substitutions_file_checked(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, _ = _start(policy)
        fakes.fake("원값하나")
        doc = rd.substitutions_document(fakes, db_id="itam", run_id="20261008-000000")
        gate = _gate(policy, vault)

        def fields(body: dict[str, Any]) -> list[Any]:
            text = yaml.safe_dump(body, allow_unicode=True, sort_keys=False)
            found = gate.check({rd.SUBSTITUTIONS_FILE: text})
            return [v["field"] for v in _substitution_hits(found)]

        assert fields(doc) == []
        assert fields({**doc, "values": [*doc["values"], "원값둘둘"]}) == [
            "summary", f"values[{len(doc['values'])}]",
        ]
        assert fields({**doc, "note": "다른 문구"}) == ["note"]
        assert fields({**doc, "extra": 1}) == ["keys"]
        assert fields({**doc, "summary": {"values": 1, "fallback": {"nope": 1}}}) == ["summary"]
        bare = rd.PiiVault.from_policy(policy)
        text = yaml.safe_dump(doc, allow_unicode=True)
        assert _substitution_hits(_gate(policy, bare).check({rd.SUBSTITUTIONS_FILE: text}))


# --- A7 반출 표기 · 종단 ------------------------------------------------------------


def test_a7_stage_and_write_gated_end_to_end(policy: cat.ColumnPolicy, tmp_path: Path) -> None:
    draft = {"assets": {"code_values": {"TCDMSIF80.asstStusDstcd": ["ACTIVE", "RETIRED"]}}}
    values = {"login_id": "5488923", "os_user": "zqtester"}
    vault, fakes, run_policy = _start(policy, user_values=values, p1_draft=draft)
    sql = f"SELECT rspblPsnEmnm AS 담당자 FROM TCDMSIF80 WHERE rspblPsnEmnm = '{_NAME2}'"
    summary = rd.summarize_result(
        _result(["담당자"], [[_NAME2], [_NAME]]),
        sources={"담당자": ["rspblPsnEmnm"]},
        policy=run_policy,
        vault=vault,
    )
    record = {
        "run_id": "20261008-000000",
        "id": "s1",
        "turn": 1,
        "repeat": 0,
        "status": "ok",
        "taxonomy": [],
        "executed_sqls": [{"sql": rd.redact_sql(sql, policy=run_policy, vault=vault)}],
        "result": summary,
    }
    run_meta = {
        "run_id": "20261008-000000",
        "operator": fakes.fake("zqtester"),
        "substitution_note": rd.SUBSTITUTION_NOTE,
    }
    staged, gate = cli.stage_gated(
        run_meta=run_meta, catalog_doc={"tables": {}}, records=[record], policy=policy,
        vault=vault, user_values=values, p1_draft=draft,
    )
    assert rd.SUBSTITUTIONS_FILE in staged and rd.CODE_SAMPLES_FILE in staged
    out = tmp_path / "run"
    ok, violations = rd.write_gated(out, staged, gate)
    assert ok, violations
    written = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    for text in written.values():
        for original in (_NAME, _NAME2, "zqtester", "ACTIVE", "RETIRED"):
            assert original not in text, original
    doc = yaml.safe_load(written[rd.SUBSTITUTIONS_FILE])
    assert list(doc) == list(rd.SUBSTITUTIONS_KEYS)
    assert doc["note"] == rd.SUBSTITUTION_NOTE and doc["db_id"] == "itam"
    assert doc["values"] == sorted(doc["values"]) and doc["summary"]["values"] == len(doc["values"])
    assert set(doc["summary"]["fallback"]) == {"no_change", "space", "draws"}
    assert fakes.fake(_NAME2) in doc["values"] and fakes.fake("ACTIVE") in doc["values"]
    assert json.loads(written["run.json"])["substitution_note"] == rd.SUBSTITUTION_NOTE
    report = written["report.md"].splitlines()
    assert report[2] == f"> {rd.SUBSTITUTION_NOTE}"  # 제목 바로 아래
    assert json.loads(written["leak_check.json"])["rules"] == list(rd.GATE_RULES)


def test_a7_no_generator_no_substitutions_file(policy: cat.ColumnPolicy) -> None:
    staged, _gate_ = cli.stage_gated(
        run_meta={"run_id": "r1"}, catalog_doc={"tables": {}}, records=[], policy=policy,
        vault=rd.PiiVault.from_policy(policy), user_values={}, p1_draft=None,
    )
    assert rd.SUBSTITUTIONS_FILE not in staged
    assert rd.SUBSTITUTION_NOTE not in staged["report.md"]


def test_numeric_fakes_list_passes_joined_leaf_check(policy: cat.ColumnPolicy) -> None:
    """숫자 가짜 값 200개를 줄바꿈으로 이어 붙인 글이 관문 「이어 붙인 잎」 검사에 걸리지 않는다."""
    import secrets

    vault, fakes, _ = _start(policy)
    for _ in range(200):
        fakes.fake(str(10**9 + secrets.randbelow(9 * 10**9)))
    doc = rd.substitutions_document(fakes, db_id="itam", run_id="r1")
    assert not rd._pii_regex_hit("\n".join(doc["values"]))
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    assert _gate(policy, vault).check({rd.SUBSTITUTIONS_FILE: text}) == []


def test_substitution_is_bounded_on_20kb_input(policy: cat.ColumnPolicy) -> None:
    """생성기 경로 20KB 병적 입력 — 가린 리터럴 700개·긴 주석·이어 붙인 오류 문구(A9)."""
    import time

    vault, _fakes, run_policy = _start(policy)
    vault.add(_NAME2)
    masked = "SELECT x FROM TCDMSIF80 WHERE " + " OR ".join(
        f"zzzMemo = 'v{i:05d}'" for i in range(700)
    )
    rd.redact_sql("SELECT 1 FROM t WHERE a = '1'", policy=run_policy, vault=vault)  # 적재 비용
    for call in (
        lambda: rd.redact_sql(
            masked[: rd.INPUT_MAX], policy=run_policy, vault=vault, catalog_columns=["zzzMemo"]
        ),
        lambda: rd.redact_sql("SELECT 1 /*" + "가" * 19_000 + "*/", policy=run_policy, vault=vault),
        lambda: rd.redact_text("'" + "a" * 19_000 + " near '" + "'" * 500, vault=vault, sql=masked),
    ):
        started = time.perf_counter()
        call()
        assert time.perf_counter() - started < 0.5
