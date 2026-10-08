"""plans/145 교정 3차 — 감사 재확인 2차(2026-10-08) 지적의 회귀 고정.

- M-A2: 다섯·여섯 덩이 점 연쇄(`IP.port`)는 앞 네 덩이를 바꾸거나 가린다 + 관문 전용 느슨한
  패턴(`_IP_LEFT` — 점 연쇄 중간 · 전각 점)이 원값 잔존을 잡는다.
- M-B 괄호류: `= (N)` · `= CAST(N AS …)` · `= COALESCE(x, N)`의 수도 비교 자리다.
- 3차b: 전각 점 IP 가림·치환 · per_db DB id 태그 비치환 · 1~2자리 비교값 유지.

실 LLM·DB 0. 가짜 값은 난수라 성질로 단언한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench.substitute import FakeValues

_IP = "172.31.45.67"
_QUAD = re.compile(r"\d+\.\d+\.\d+\.\d+")


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _generating(policy: cat.ColumnPolicy) -> tuple[rd.PiiVault, FakeValues, cat.ColumnPolicy]:
    return cli.start_substitution(
        policy=policy,
        catalog_doc={"tables": {}, "same_key_groups": []},
        user_values={"login_id": "5488923"},
        p1_draft=None,
    )


def _plain_gate(policy: cat.ColumnPolicy) -> rd.LeakGate:
    return rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})


def _trace(note: str) -> dict[str, str]:
    return {"trace.jsonl": json.dumps({"id": "S1", "turn": 1, "note": note}) + "\n"}


# --- M-A2: 점 연쇄 · 전각 점 -------------------------------------------------------------------


_CHAINS = [f"x {_IP}.8080 y", f"x {_IP}.8.9 y", f"tcp {_IP}.443 > 10.0.0.1.51234"]


class TestDottedChains:
    @pytest.mark.parametrize("text", _CHAINS)
    def test_without_generator_masks_head_and_passes_gate(
        self, policy: cat.ColumnPolicy, text: str
    ) -> None:
        out = rd.redact_text(text, vault=rd.PiiVault())
        assert _IP not in out and "172.31.45.***." in out
        assert _plain_gate(policy).check(_trace(out)) == []

    @pytest.mark.parametrize("text", _CHAINS)
    def test_with_generator_fakes_head_and_passes_gate(
        self, policy: cat.ColumnPolicy, text: str
    ) -> None:
        vault, fakes, run_policy = _generating(policy)
        out = rd.redact_text(text, vault=vault)
        assert _IP not in out
        heads = _QUAD.findall(out)
        assert heads and all(fakes.is_fake(head) for head in heads)
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert gate.check(_trace(out)) == []

    @pytest.mark.parametrize("text", _CHAINS)
    def test_gate_catches_raw_chain_on_both_paths(
        self, policy: cat.ColumnPolicy, text: str
    ) -> None:
        assert [v["rule"] for v in _plain_gate(policy).check(_trace(text))] == ["pii_regex"]
        vault, _fakes, run_policy = _generating(policy)
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert [v["rule"] for v in gate.check(_trace(text))] == ["pii_regex"]

    def test_trailing_dot_still_handled(self, policy: cat.ColumnPolicy) -> None:
        out = rd.redact_text(f"Lost connection to {_IP}.", vault=rd.PiiVault())
        assert out == "Lost connection to 172.31.45.***."
        assert _plain_gate(policy).check(_trace(out)) == []


class TestChainDisposition:
    """버전 문자열·긴 점 연쇄의 처분(`LeakGate._ip_left` docstring 근거)."""

    def test_six_group_version_is_masked_at_head_and_passes(
        self, policy: cat.ColumnPolicy
    ) -> None:
        out = rd.redact_text("v1.2.3.4.5.6", vault=rd.PiiVault())
        assert out == "v1.2.3.***.5.6"
        assert _plain_gate(policy).check(_trace(out)) == []

    def test_wide_group_version_is_not_an_ip(self, policy: cat.ColumnPolicy) -> None:
        assert rd.redact_text("10.0.19041.1", vault=rd.PiiVault()) == "10.0.19041.1"
        assert _plain_gate(policy).check(_trace("build 10.0.19041.1")) == []

    def test_quad_after_masked_head_is_blocked(self, policy: cat.ColumnPolicy) -> None:
        """앞 네 덩이만 가려지고 뒤에 네 덩이가 남는 긴 연쇄는 닫힌 쪽으로 막는다."""
        for text in ("9.9.9.9.172.31.45.67", "1.3.6.1.4.1.9.9.1"):
            out = rd.redact_text(text, vault=rd.PiiVault())
            assert [v["rule"] for v in _plain_gate(policy).check(_trace(out))] == ["pii_regex"]


_FULL_WIDTH = ["172．31．45．67", "172。31。45。67", "172｡31｡45｡67"]


class TestFullWidthDots:
    """교정 3차b — 가림·치환도 전각 점을 구분자로 본다(셀 하나가 run 을 세우지 않는다)."""

    @pytest.mark.parametrize("text", _FULL_WIDTH)
    def test_without_generator_masks_and_passes(self, policy: cat.ColumnPolicy, text: str) -> None:
        out = rd.redact_text(f"to {text} y", vault=rd.PiiVault())
        assert out == f"to {text[:-3]}.*** y"
        assert _plain_gate(policy).check(_trace(out)) == []

    @pytest.mark.parametrize("text", _FULL_WIDTH)
    def test_with_generator_shares_ascii_fake(self, policy: cat.ColumnPolicy, text: str) -> None:
        vault, fakes, run_policy = _generating(policy)
        out = rd.redact_text(f"{text} and {_IP}", vault=vault)
        fake = fakes.fake_ip(_IP)
        assert out == f"{fake} and {fake}" and fakes.is_fake(fake)
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert gate.check(_trace(out)) == []

    @pytest.mark.parametrize("text", _FULL_WIDTH)
    def test_general_cell_is_not_written_raw_and_run_passes(
        self, policy: cat.ColumnPolicy, text: str
    ) -> None:
        general = next(
            name
            for columns in policy.tables.values()
            for name, grade in columns.items()
            if grade == "general"
        )
        generating_vault, _fakes, generating_policy = _generating(policy)
        for vault, run_policy in (
            (rd.PiiVault.from_policy(policy), policy),
            (generating_vault, generating_policy),
        ):
            result = {"status": "ok", "columns": [general], "rows": [{general: text}]}
            summary = rd.summarize_result(
                result, sources={general: [general]}, policy=run_policy, vault=vault
            )
            line = json.dumps({"id": "S1", "turn": 1, "result": summary}, ensure_ascii=False)
            assert text not in line
            gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
            assert gate.check({"trace.jsonl": line + "\n"}) == []

    @pytest.mark.parametrize("text", _FULL_WIDTH)
    def test_gate_still_blocks_raw_leftover(self, policy: cat.ColumnPolicy, text: str) -> None:
        assert [v["rule"] for v in _plain_gate(policy).check(_trace(text))] == ["pii_regex"]

    def test_full_width_check_stays_out_of_reject_callback(self, policy: cat.ColumnPolicy) -> None:
        assert _plain_gate(policy).rules("172．31．45．67", schema_section=False) == []


# --- Minor-A: per_db DB id 태그 ------------------------------------------------------------------


def _per_db_record(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, *, tagged: bool = True
) -> dict[str, Any]:
    spec = {"id": "X-1", "compare": "keyset", "key": [["sevrHostName"]], "db_ids": ["itam"]}
    rows = [{"sevrHostName": f"realhost{i:02d}"} for i in range(3)]
    outcome = {"status": "ok", "rows_by_db": {"itam": rows}}
    columns = ["_source_db"] if tagged else []
    result = {"status": "empty", "columns": columns, "rows": []}
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


class TestPerDbTag:
    def test_tag_is_kept_and_key_is_faked(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        rec = _per_db_record(run_policy, vault)
        assert rec["key_tagged"] is True and rec["keys_recorded"] is False
        missing = rec["detail"]["missing"]
        assert [key[0] for key in missing] == ["itam"] * 3
        assert all(fakes.is_fake(key[1]) for key in missing)
        assert "realhost" not in json.dumps(rec) and "itam" not in fakes.fakes()
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert gate.check(_trace_record(rec)) == []

    def test_untagged_record_has_no_flag(self, policy: cat.ColumnPolicy) -> None:
        vault, _fakes, run_policy = _generating(policy)
        rec = _per_db_record(run_policy, vault, tagged=False)
        assert "key_tagged" not in rec

    @pytest.mark.parametrize(
        ("key", "tagged"),
        [
            (["itam", "realhost00"], True),  # 태그 뒤 원값은 여전히 위반
            (["박서준", "{fake}"], True),  # ASCII 이름 모양이 아닌 태그는 치환 칸
            (["itam", "{fake}"], False),  # 표지 없는 기록은 태그도 가짜 값이어야 한다
        ],
    )
    def test_gate_checks_everything_but_the_identifier_tag(
        self, policy: cat.ColumnPolicy, key: list[str], tagged: bool
    ) -> None:
        vault, fakes, run_policy = _generating(policy)
        fake = fakes.fake("realhost00")
        rec = {
            "keys_recorded": False,
            **({"key_tagged": True} if tagged else {}),
            "substituted_fields": ["missing"],
            "detail": {"compare": "keyset", "missing": [[k.format(fake=fake) for k in key]]},
        }
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        rules = {v["rule"] for v in gate.check(_trace_record(rec))}
        assert "substitution" in rules


def _trace_record(rec: dict[str, Any]) -> dict[str, str]:
    line = json.dumps({"id": "S1", "turn": 1, "oracle": rec}, ensure_ascii=False)
    return {"trace.jsonl": line + "\n"}


# --- M-B 괄호류 ------------------------------------------------------------------------------


class TestWrappedCompareNumbers:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t WHERE 담당자 = (20230001)",
            "SELECT * FROM t WHERE 담당자 = CAST(20230001 AS CHAR)",
            "SELECT * FROM t WHERE 담당자 = COALESCE(x, 20230001)",
            "SELECT * FROM t WHERE 담당자 <> COALESCE(20230001, x)",
        ],
    )
    def test_wrapped_value_becomes_fake(self, policy: cat.ColumnPolicy, sql: str) -> None:
        vault, fakes, run_policy = _generating(policy)
        out = rd.redact_sql(sql, policy=run_policy, vault=vault)
        assert "20230001" not in out and fakes.fake("20230001") in out

    def test_structural_numbers_in_calls_stay(self, policy: cat.ColumnPolicy) -> None:
        vault, _fakes, run_policy = _generating(policy)
        sql = (
            "SELECT ROUND(a, 2), COALESCE(b, 0), CAST(c AS DECIMAL(10, 2)) FROM t "
            "WHERE 담당자 > ROUND(x, 2) AND y = CAST(z AS DECIMAL(12, 3)) LIMIT (10)"
        )
        assert rd.redact_sql(sql, policy=run_policy, vault=vault) == sql

    def test_without_generator_unchanged(self, policy: cat.ColumnPolicy) -> None:
        sql = "SELECT * FROM t WHERE 담당자 = (20230001) OR 담당자 = CAST(20230002 AS CHAR)"
        assert rd.redact_sql(sql, policy=policy, vault=rd.PiiVault()) == sql


class TestShortCompareNumbers:
    """교정 3차e — 1~2자리 유지(3차b Minor-C)를 철회했다: 자릿수와 관계없이 등급 규칙으로 바꾼다."""

    def test_short_values_are_faked_or_marked(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        sql = "SELECT * FROM t WHERE zzzUnknown = 0 AND 담당자 IN (1, 12) AND 금액 > -7"
        out = rd.redact_sql(sql, policy=run_policy, vault=vault)
        values = re.findall(r"= (\S+) AND 담당자 IN \((\S+), (\S+)\) AND 금액 > -(\S+)$", out)
        assert values, out
        for original, shown in zip(("0", "1", "12", "7"), values[0], strict=True):
            assert shown != original and (shown == rd.MASK or fakes.is_fake(shown))

    def test_general_comparison_still_kept(self, policy: cat.ColumnPolicy) -> None:
        general = next(
            name
            for columns in policy.tables.values()
            for name, grade in columns.items()
            if grade == "general"
        )
        vault, _fakes, run_policy = _generating(policy)
        sql = f"SELECT * FROM t WHERE {general} = 1"
        assert rd.redact_sql(sql, policy=run_policy, vault=vault) == sql

    @pytest.mark.parametrize("number", ["123", "9800", "20230001", "1.5"])
    def test_longer_values_are_faked(self, policy: cat.ColumnPolicy, number: str) -> None:
        vault, fakes, run_policy = _generating(policy)
        sql = f"SELECT * FROM t WHERE 금액 > {number}"
        out = rd.redact_sql(sql, policy=run_policy, vault=vault)
        assert out == f"SELECT * FROM t WHERE 금액 > {fakes.fake(number)}"
        assert fakes.fake(number) != number


# --- 3차c: 태그 예외는 등록 DB id 만 ------------------------------------------------------------


def _pii_key_record(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, system_tag: str
) -> dict[str, Any]:
    """키 열이 사람 열이라 키가 치환되는 per_db keyset 기록(시스템 태그 = `_source_db` 셀)."""
    spec = {"id": "X-2", "compare": "keyset", "key": [["rspblPsnEmnm"]], "db_ids": ["itam"]}
    outcome = {"status": "ok", "rows_by_db": {"itam": [{"rspblPsnEmnm": "emp0001"}]}}
    result = {
        "status": "ok",
        "columns": ["_source_db", "rspblPsnEmnm"],
        "rows": [{"_source_db": system_tag, "rspblPsnEmnm": "emp0002"}],
    }
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


class TestRegisteredTagsOnly:
    def test_registered_set_comes_from_db_registry(self) -> None:
        ids = jd.registered_db_ids()
        assert {"itam", "polestar", "polestar_cm_gp"} <= ids and "webprd01" not in ids

    def test_unregistered_system_tag_is_faked(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        rec = _pii_key_record(run_policy, vault, "webprd01")
        assert rec["key_tagged"] is True and rec["keys_recorded"] is False
        (extra,) = rec["detail"]["extra"]
        assert extra[0] != "webprd01" and fakes.is_fake(extra[0]) and fakes.is_fake(extra[1])
        assert rec["detail"]["missing"][0][0] == "itam"
        text = json.dumps(rec, ensure_ascii=False)
        assert "webprd01" not in text and "emp000" not in text
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert gate.check(_trace_record(rec)) == []

    def test_registered_tags_are_kept(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        rec = _pii_key_record(run_policy, vault, "polestar")
        assert rec["detail"]["extra"][0][0] == "polestar"
        assert rec["detail"]["missing"][0][0] == "itam"
        assert "polestar" not in fakes.fakes() and "itam" not in fakes.fakes()
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        assert gate.check(_trace_record(rec)) == []

    def test_gate_rejects_unregistered_tag_despite_flag(self, policy: cat.ColumnPolicy) -> None:
        vault, fakes, run_policy = _generating(policy)
        fake = fakes.fake("emp0002")
        rec = {
            "keys_recorded": False,
            "key_tagged": True,
            "substituted_fields": ["extra"],
            "detail": {"compare": "keyset", "extra": [["webprd01", fake]]},
        }
        gate = rd.LeakGate(policy=run_policy, vault=vault, user_values={})
        hits = gate.check(_trace_record(rec))
        assert {"rule": "substitution", "field": "oracle.detail.extra"}.items() <= hits[0].items()


# --- 3차d: 레지스트리 로드 실패 폴백 ------------------------------------------------------------


@pytest.fixture
def registry_cache() -> Iterator[None]:
    """등록 DB id 캐시를 전·후로 비운다(레지스트리를 바꿔 끼우는 테스트끼리 오염 방지)."""
    jd.registered_db_ids_cache_clear()
    yield
    jd.registered_db_ids_cache_clear()


def _broken_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routing import registry

    def boom() -> Any:
        raise registry.RegistryError("db_registry.yaml 없음(주입)")

    monkeypatch.setattr(registry, "get_registry", boom)


class TestRegistryFallback:
    def test_fallback_is_db_id_only_and_not_cached(
        self, registry_cache: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with monkeypatch.context() as patch:
            _broken_registry(patch)
            assert jd.registered_db_ids() == frozenset({"itam"})
            assert jd.registered_db_ids() == frozenset({"itam"})
            assert jd.registry_fallbacks() == 2
        assert "polestar" in jd.registered_db_ids()  # 실패는 캐시하지 않았다

    def test_run_completes_with_tags_faked_and_marker(
        self,
        registry_cache: None,
        monkeypatch: pytest.MonkeyPatch,
        policy: cat.ColumnPolicy,
        tmp_path: Path,
    ) -> None:
        _broken_registry(monkeypatch)
        vault, fakes, run_policy = _generating(policy)
        ctx = cli.RunContext(
            run_id="20261008-000000", tier=None, policy=run_policy,
            catalog=jd.CatalogFacts.from_catalog({"tables": {}}), vault=vault,
            oracle=lambda *_a: {},
        )  # fmt: skip
        spec = {"id": "X-3", "compare": "keyset", "key": [["rspblPsnEmnm"]], "db_ids": ["itam"]}
        oracle_rows = [{"rspblPsnEmnm": "emp0001"}]
        outcome: dict[str, Any] = {"status": "ok", "rows_by_db": {"itam": oracle_rows}}
        result = {
            "status": "ok",
            "columns": ["_source_db", "rspblPsnEmnm"],
            "rows": [{"_source_db": "polestar", "rspblPsnEmnm": "emp0002"}],
        }
        verdict, detail, mode = jd.evaluate(spec, outcome, result)
        names = rd.result_column_names(result)

        def finish() -> dict[str, Any]:
            summary = rd.summarize_result(
                result, sources={n: [n] for n in names}, policy=run_policy, vault=vault
            )
            oracle = cli._oracle_record(
                (spec, outcome, detail, mode, verdict), names, summary, [],
                SimpleNamespace(query=""), ctx,  # type: ignore[arg-type]
            )  # fmt: skip
            return {"run_id": ctx.run_id, "id": "S1", "turn": 1, "repeat": 0, "oracle": oracle}

        def originals() -> Iterable[object]:
            yield from cli._row_cells(result["rows"])
            yield from cli._row_cells(oracle_rows)

        pending = cli.PendingTurn(
            status="ok", verdict=verdict, taxonomy=[], finish=finish, originals=originals
        )
        (record,) = cli.finish_turns([pending], ctx)
        extra = record["oracle"]["detail"]["extra"]
        assert extra[0][0] != "polestar" and fakes.is_fake(extra[0][0])
        assert record["oracle"]["detail"]["missing"][0][0] == "itam"  # 내린 집합 = {DB_ID}
        assert ctx.counters["registry_fallback"] == 1
        run_meta = {
            "run_id": ctx.run_id,
            "substitution_note": rd.SUBSTITUTION_NOTE,
            **({"registry_fallback": True} if ctx.counters["registry_fallback"] else {}),
        }
        staged, gate = cli.stage_gated(
            run_meta=run_meta, catalog_doc={"tables": {}}, records=[record], policy=policy,
            vault=vault, user_values={"login_id": "5488923"}, p1_draft=None,
        )  # fmt: skip
        ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
        assert ok, violations
        written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").rglob("*"))
        assert "polestar" not in written.replace(rd.SUBSTITUTION_NOTE, "")
        assert "emp000" not in written and '"registry_fallback": true' in written
