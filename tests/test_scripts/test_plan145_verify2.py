"""plans/145 독립 검증 재확인(교정 1차 뒤) — 실제 `__main__.finish_turns` 2단계 경로.

- 짧은 영숫자 우연 일치(지난 High): `[A-Z][0-9]`·`[A-Z]{2}`·3자 값을 여러 턴에 흩어 `finish_turns` →
  `stage_gated` → `write_gated`까지 돌려 run 실패 0 · 치환 칸 원값 0.
- 2단계 정합: 1차(수집) 실행이 가짜 값·치환 불가 수·vault 수·카운터를 두 번 남기지 않는다.
- 뒤 턴 수집 값으로 앞 턴 일반 열이 강등되는 run(단일 단계면 관문 실패) — 2단계는 통과하고 두 턴이
  같은 가짜 값이다(A2).
- A2 전 지점(SQL 리터럴 대소문자·끝 공백 · 결과 표본 · 오류 문구 · 판정 상세 키 · 코드값 파일).
- F9: FROM·JOIN·쉼표 조인·하위 질의 FROM 뒤 테이블 이름 유지 + 값 자리는 계속 바뀐다(생성기 경로).
- Low-2(동명 열 승격 전파) 판단 근거: 승격은 이름 단위로 일관돼 리터럴·표본이 같은 처분이다.
- 2차에서 처음 보는 원값 경로(결함 재현): 앞 공백 셀은 1차 등록 키(` ab1x`)와 2차 발급 키(`ab1x`)가
  달라 앞서 낸 가짜 값과 겹치면 관문 ①이 run 을 막는다.
- A9 생성기 경로 20KB 성능.

실 LLM·DB 0. 가짜 값은 난수라 성질로 단언한다(결정적 재현만 `_draw`를 바꿔 끼운다).
"""

from __future__ import annotations

import json
import random
import re
import string
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import substitute as sb
from scripts.itam_bench.substitute import FakeValues

_USER = {"login_id": "5488923"}


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _catalog(*names: str, relations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "tables": {
            "TCDMSIF80": {
                "columns": [{"name": n} for n in names],
                "relations": relations or [],
                "key": [],
            }
        },
        "same_key_groups": [],
    }


def _ctx(
    policy: cat.ColumnPolicy, catalog_doc: dict[str, Any], p1_draft: dict[str, Any] | None = None
) -> tuple[cli.RunContext, FakeValues]:
    vault, fakes, run_policy = cli.start_substitution(
        policy=policy, catalog_doc=catalog_doc, user_values=_USER, p1_draft=p1_draft
    )
    ctx = cli.RunContext(
        run_id="20261008-000000", tier=None, policy=run_policy,
        catalog=jd.CatalogFacts.from_catalog(catalog_doc), vault=vault, oracle=lambda *a: {},
    )  # fmt: skip
    return ctx, fakes


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, row, strict=True)) for row in rows],
        "total_rows": len(rows),
        "truncated": False,
    }


def _turn(
    ctx: cli.RunContext,
    index: int,
    result: dict[str, Any],
    sources: dict[str, list[str]],
    sql: str = "",
    error: str | None = None,
    oracle: tuple[Any, ...] | None = None,
    log: list[int] | None = None,
) -> cli.PendingTurn:
    """`run_turn`과 같은 재료로 지연 레코드를 만든다(마감은 `_turn_record`와 같은 함수들)."""
    executed = (
        [{"sql": sql, "source": "itam", "success": error is None, "row_count": 1,
          "retry_attempt": 0, "error": error}]
        if sql
        else []
    )  # fmt: skip

    def finish() -> dict[str, Any]:
        if log is not None:
            log.append(index)
        summary = rd.summarize_result(result, sources=sources, policy=ctx.policy, vault=ctx.vault)
        record: dict[str, Any] = {
            "run_id": ctx.run_id, "id": f"S{index}", "turn": 1, "repeat": 0, "status": "ok",
            "taxonomy": [], "result": summary,
            "executed_sqls": cli._redacted_sqls(executed, ctx, ""),
            "oracle": None,
        }  # fmt: skip
        if oracle is not None:
            names = rd.result_column_names(result)
            record["oracle"] = cli._oracle_record(
                oracle, names, summary, [sql], SimpleNamespace(query=""), ctx
            )
        return record

    def originals() -> Iterable[object]:
        yield from cli._row_cells(result.get("rows"))
        yield from rd.sql_literal_contents(sql)
        if oracle is not None:
            for rows in ((oracle[1] or {}).get("rows_by_db") or {}).values():
                yield from cli._row_cells(rows)

    return cli.PendingTurn("ok", None, [], finish, originals)


def _write(
    tmp_path: Path,
    ctx: cli.RunContext,
    policy: cat.ColumnPolicy,
    records: list[dict[str, Any]],
    p1_draft: dict[str, Any] | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, str]]:
    staged, gate = cli.stage_gated(
        run_meta={"run_id": ctx.run_id, "substitution_note": rd.SUBSTITUTION_NOTE},
        catalog_doc={"tables": {}}, records=records, policy=policy, vault=ctx.vault,
        user_values=_USER, p1_draft=p1_draft,
    )  # fmt: skip
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    return ok, violations, staged


# --- 지난 High — 짧은 값 우연 일치(몬테카를로 축소판 · 2단계 경로) ------------------------------


def _pool(shape: str, n: int, rng: random.Random) -> list[str]:
    out: set[str] = set()
    while len(out) < n:
        if shape == "A9":
            out.add(rng.choice(string.ascii_uppercase) + rng.choice(string.digits))
        elif shape == "AA":
            out.add("".join(rng.choice(string.ascii_uppercase) for _ in range(2)))
        else:
            out.add("".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(3)))
    values = sorted(out)
    rng.shuffle(values)
    return values


@pytest.mark.parametrize(("shape", "n"), [("A9", 40), ("AA", 40), ("X3", 40)])
def test_short_values_two_phase_runs_never_fail(
    policy: cat.ColumnPolicy, tmp_path: Path, shape: str, n: int
) -> None:
    """단일 단계였으면 [A-Z][0-9] 40 은 run 의 97%가 관문 ①로 실패했다
    (지난 라운드 · 대조 재측정)."""
    rng = random.Random(f"{shape}{n}")
    catalog_doc = _catalog("zzzMemo")
    for run in range(15):
        values = _pool(shape, n, rng)
        ctx, fakes = _ctx(policy, catalog_doc)
        pending = []
        for i, value in enumerate(values):
            other = values[(i + 1) % n]
            pending.append(
                _turn(
                    ctx, i, _result(["zzzMemo"], [[value], [other]]), {"zzzMemo": ["zzzMemo"]},
                    sql=f"SELECT zzzMemo FROM TCDMSIF80 WHERE zzzMemo = '{value}'",
                    error=f"near '{value}' at line 1" if i % 3 == 0 else None,
                )  # fmt: skip
            )
        records = cli.finish_turns(pending, ctx)
        ok, violations, _staged = _write(tmp_path / str(run), ctx, policy, records)
        assert ok, violations
        assert fakes.collisions() == 0
        folded = {v.casefold() for v in values}
        for record in records:
            literal = record["executed_sqls"][0]["sql"].split("zzzMemo = '", 1)[1].rstrip("'")
            column = record["result"]["columns"][0]
            shown = [literal, *(column.get("sample") or []), *(column.get("top_values") or {})]
            assert not {s.casefold() for s in shown} & folded
            assert rd.MASK not in shown


# --- 2단계 정합 — 1차 실행의 부수 효과 ---------------------------------------------------------


def test_collecting_pass_leaves_no_double_side_effects(policy: cat.ColumnPolicy) -> None:
    """1차(버림) 실행은 가짜 값·치환 불가 수를 남기지 않고, vault 수·카운터는 한 번 마감과 같다."""
    catalog_doc = _catalog("zzzMemo", "rspblPsnEmnm")
    ctx, fakes = _ctx(policy, catalog_doc)
    log: list[int] = []
    snapshots: list[tuple[int, dict[str, int]]] = []
    sources = {"담당": ["rspblPsnEmnm"], "zzzMemo": ["zzzMemo"]}
    pending = [
        _turn(ctx, 0, _result(["담당", "zzzMemo"], [["박서준", "%%"], ["김민수", "ZQ-77"]]),
              sources, sql="SELECT rspblPsnEmnm AS 담당, zzzMemo FROM TCDMSIF80 "
              "WHERE zzzMemo = '%%' OR zzzMemo = 'ZQ-77'", log=log),
        _turn(ctx, 1, _result(["담당", "zzzMemo"], [["이영희", "%%"]]), sources,
              sql="SELECT rspblPsnEmnm AS 담당, zzzMemo FROM TCDMSIF80", log=log),
    ]  # fmt: skip
    original_finish = [item.finish for item in pending]

    def watched(finish: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
        def run() -> dict[str, Any]:
            out = finish()
            snapshots.append((len(fakes.fakes()), fakes.fallback_counts()))
            return out

        return run

    for item, finish in zip(pending, original_finish, strict=True):
        item.finish = watched(finish)
    ctx.counters["turns"] = 2  # run_turn 이 센 값 — 마감은 건드리지 않아야 한다
    records = cli.finish_turns(pending, ctx)
    assert log == [0, 1, 0, 1]  # 1차 2턴 + 2차 2턴
    # 1차가 끝난 시점(앞 두 스냅숏)에는 낸 가짜 값·치환 불가 수 0
    assert snapshots[0][0] == snapshots[1][0] == 0
    assert snapshots[1][1] == {"no_change": 0, "space": 0, "draws": 0}
    # `%%`(바꿀 글자 없음)는 서로 다른 원값 1건 — 두 번 세지 않는다
    assert fakes.fallback_counts()["no_change"] == 1
    assert len(ctx.vault) == 3 and ctx.counters["turns"] == 2
    listed = set(fakes.fakes())
    text = json.dumps(records, ensure_ascii=False)
    assert all(value in text for value in listed)  # 목록에 1차의 유령 값이 없다
    for original in ("박서준", "김민수", "이영희", "ZQ-77"):
        assert original not in text


def test_later_harvest_demotes_earlier_general_column(
    policy: cat.ColumnPolicy, tmp_path: Path
) -> None:
    """뒤 턴 사람 열에서 처음 보는 값이 앞 턴 일반 열에 있었다 — 2단계면 앞 턴도 강등·치환되고
    두 턴이 같은 가짜 값이다(단일 단계면 앞 턴 일반 열에 원값이 실려 관문 `pii_value`로 실패)."""
    catalog_doc = _catalog("sevrHostName", "rspblPsnEmnm")
    ctx, fakes = _ctx(policy, catalog_doc)
    pending = [
        _turn(ctx, 0, _result(["sevrHostName"], [["박서준"], ["web01"]]),
              {"sevrHostName": ["sevrHostName"]}, sql="SELECT sevrHostName FROM TCDMSIF80"),
        _turn(ctx, 1, _result(["담당"], [["박서준"], ["이영희"]]), {"담당": ["rspblPsnEmnm"]},
              sql="SELECT rspblPsnEmnm AS 담당 FROM TCDMSIF80"),
    ]  # fmt: skip
    records = cli.finish_turns(pending, ctx)
    first, second = records[0]["result"]["columns"][0], records[1]["result"]["columns"][0]
    assert first.get("demoted") == "pii_value_match" and first["substituted"] is True
    fake = fakes.fake("박서준")
    assert fake in first["sample"] and fake in second["sample"]
    ok, violations, _staged = _write(tmp_path, ctx, policy, records)
    assert ok, violations
    assert "박서준" not in json.dumps(records, ensure_ascii=False)


# --- A2 전 지점 ---------------------------------------------------------------------------


def test_a2_same_fake_across_all_points(policy: cat.ColumnPolicy, tmp_path: Path) -> None:
    draft = {"assets": {"code_values": {"TCDMSIF80.asstStusDstcd": ["KQ7Z", "RT55"]}}}
    catalog_doc = _catalog("zzzMemo", "asstStusDstcd")
    ctx, fakes = _ctx(policy, catalog_doc, draft)
    spec = {"id": "o1", "compare": "keyset", "key": ["zzzMemo"]}
    detail = {"missing": [["kq7z"]], "extra": [["RT55"]]}
    outcome = {"status": "ok", "rows_by_db": {"itam": [{"zzzMemo": "KQ7Z"}]}}
    pending = [
        _turn(ctx, 0, _result(["zzzMemo"], [["RT55"]]), {"zzzMemo": ["zzzMemo"]},
              sql="SELECT zzzMemo FROM TCDMSIF80 WHERE zzzMemo = 'Kq7Z  ' /* rt55 */",
              error="Duplicate entry 'KQ7Z' for key 'PRIMARY'"),
        _turn(ctx, 1, _result(["zzzMemo"], [["kq7z"], ["RT55"]]), {"zzzMemo": ["zzzMemo"]},
              sql="SELECT zzzMemo FROM TCDMSIF80",
              oracle=(spec, outcome, detail, "as_is", "fail")),
    ]  # fmt: skip
    records = cli.finish_turns(pending, ctx)
    base = fakes.fake("KQ7Z").casefold()
    rt = fakes.fake("RT55").casefold()
    sql = records[0]["executed_sqls"][0]["sql"]
    literal = sql.split("zzzMemo = '", 1)[1].split("'", 1)[0]
    assert literal.casefold().rstrip() == base and literal.endswith("  ")  # 끝 공백 다시 입힘
    assert literal[0].isupper() and literal[1].islower()  # 대소문자 다시 입힘(Kq7Z)
    assert f"/*{fakes.fake('rt55')}*/" in sql
    error = records[0]["executed_sqls"][0]["error"]
    assert f"'{fakes.fake('KQ7Z')}'" in error and "'PRIMARY'" in error
    samples = records[1]["result"]["columns"][0]
    shown = [*(samples.get("sample") or []), *(samples.get("top_values") or {})]
    assert {s.casefold() for s in shown} == {base, rt}
    oracle = records[1]["oracle"]
    assert oracle["detail"]["missing"][0][0].casefold() == base
    assert oracle["detail"]["extra"][0][0].casefold() == rt
    ok, violations, staged = _write(tmp_path, ctx, policy, records, draft)
    assert ok, violations
    values = yaml.safe_load(staged[rd.CODE_SAMPLES_FILE])["columns"]["TCDMSIF80.asstStusDstcd"]
    assert {v.casefold() for v in values["values"]} == {base, rt}
    written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").iterdir())
    assert not re.search(r"(?i)(?<![a-z0-9])(kq7z|rt55)(?![a-z0-9])", written)


# --- F9 생성기 경로 ------------------------------------------------------------------------

_CC = ["운영체제타입내용"]


@pytest.mark.parametrize(
    ("sql", "tables", "originals"),
    [
        ("SELECT `운영체제타입내용` FROM `tcdmsif72` WHERE `운영체제타입내용` = '비밀메모값'",
         ["FROM `tcdmsif72`"], ["비밀메모값"]),
        ("SELECT a.x FROM `tcdmsif80` a JOIN `tcdmsif72` b ON a.x = b.x "
         "WHERE `운영체제타입내용` = '박서준'", ["FROM `tcdmsif80` a", "JOIN `tcdmsif72` b"],
         ["박서준"]),
        ("SELECT COUNT(*) FROM `tcdmsif80` a, `tcdmsif72` b WHERE `운영체제타입내용` = `홍길동`",
         ["FROM `tcdmsif80` a, `tcdmsif72` b"], ["홍길동"]),
        ("SELECT * FROM t WHERE x IN (SELECT y FROM `tcdmsif72` WHERE `운영체제타입내용` = "
         "'값비밀') GROUP BY `운영체제타입내용`", ["FROM `tcdmsif72` WHERE"], ["값비밀"]),
        ("SELECT COUNT(*) FROM 자산테이블 GROUP BY 운영체제타입내용 "
         "HAVING 운영체제타입내용 = 김민수",
         ["FROM 자산테이블 GROUP"], ["김민수"]),
    ],
)  # fmt: skip
def test_f9_tables_kept_values_faked_with_generator(
    sql: str, tables: list[str], originals: list[str]
) -> None:
    fakes = FakeValues()
    out = rd.redact_sql(sql, policy=None, vault=rd.PiiVault(fakes=fakes), catalog_columns=_CC)
    for table in tables:
        assert table in out, out
    for original in originals:
        assert original not in out and fakes.fake(original) in out, out


# --- Low-2 판단 근거 — 동명 열 승격은 이름 단위로 일관 ----------------------------------------


def test_low2_same_name_promotion_is_consistent(tmp_path: Path) -> None:
    """T2.X 만 사람 열과 관계지만 결과 열·리터럴은 테이블을 모르므로 T1.X 도 같이 올라간다 —
    누출이 아니라 과치환(유용성 손실)이고, 리터럴과 표본이 같은 가짜 값이라 원값↔가짜 짝이 없다."""
    base = cat.ColumnPolicy(
        db_id="itam", scope="t",
        tables={"T1": {"X": "general"}, "T2": {"X": "general"}, "T3": {"Y": "pii"}},
    )  # fmt: skip
    relation = {"from": "T2", "to": "T3", "columns": [["X", "Y"]], "kind": "declared"}
    catalog_doc = {
        "tables": {
            "T1": {"columns": [{"name": "X"}], "relations": [], "key": []},
            "T2": {"columns": [{"name": "X"}], "relations": [relation], "key": []},
            "T3": {"columns": [{"name": "Y"}], "relations": [], "key": []},
        },
        "same_key_groups": [],
    }
    ctx, fakes = _ctx(base, catalog_doc)
    assert ctx.policy.grade("X", "T1") == "general" and ctx.policy.grade("X") == "pii"
    pending = [
        _turn(ctx, 0, _result(["X"], [["ABCD7"]]), {"X": ["X"]},
              sql="SELECT X FROM T1 WHERE X = 'ABCD7'"),
    ]  # fmt: skip
    record = cli.finish_turns(pending, ctx)[0]
    fake = fakes.fake("ABCD7")
    assert f"X = '{fake}'" in record["executed_sqls"][0]["sql"]
    assert record["result"]["columns"][0]["sample"] == [fake]
    ok, violations, _staged = _write(tmp_path, ctx, base, [record])
    assert ok, violations


# --- 2차에서 처음 보는 원값(결함 재현) ---------------------------------------------------------


def test_leading_space_cell_registered_under_issue_key(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1차 등록은 셀 원문 키(앞 공백 유지 ` ab1x`)인데, 강등 열 표본은 `strip()`한 `ab1x`를 낸다.
    앞 턴 일반 열은 1차에서는 강등되지 않아(뒤 턴 수집 전) `ab1x`가 2차에서 처음 보는 원값이 되고,
    그보다 먼저 낸 가짜 값이 `ab1x`와 같으면 관문 ①이 run 을 막는다(닫힌 쪽 · 가용성 결함).

    재현은 첫 발급 후보를 `ab1x`로 고정한다. 권고: `register`가 `fake` 계열 입력과 같은 정규화
    (`strip()`)로도 등록하거나, 1차 수집을 vault 가 다 찬 뒤 한 번 더 돈다.
    """
    catalog_doc = _catalog("zzzMemo", "sevrHostName", "rspblPsnEmnm")
    ctx, fakes = _ctx(policy, catalog_doc)
    draws = iter(["ab1x"])
    real_draw = sb._draw
    monkeypatch.setattr(sb, "_draw", lambda segment: next(draws, None) or real_draw(segment))
    pending = [
        _turn(ctx, 0, _result(["zzzMemo", "sevrHostName"], [["QQ9Z", " ab1x"], ["QQ9Z", "박서준"]]),
              {"zzzMemo": ["zzzMemo"], "sevrHostName": ["sevrHostName"]},
              sql="SELECT zzzMemo, sevrHostName FROM TCDMSIF80"),
        _turn(ctx, 1, _result(["담당"], [["박서준"]]), {"담당": ["rspblPsnEmnm"]},
              sql="SELECT rspblPsnEmnm AS 담당 FROM TCDMSIF80"),
    ]  # fmt: skip
    records = cli.finish_turns(pending, ctx)
    ok, violations, _staged = _write(tmp_path, ctx, policy, records)
    assert fakes.collisions() == 0 and ok, violations


# --- A9 생성기 경로 20KB ---------------------------------------------------------------------


def test_generator_path_20kb_bounded(policy: cat.ColumnPolicy) -> None:
    ctx, _fakes = _ctx(policy, _catalog("zzzMemo"))
    ctx.vault.add("박서준")
    big_sql = "SELECT zzzMemo FROM TCDMSIF80 WHERE " + " OR ".join(
        f"zzzMemo = 'svr-{i:05d}'" for i in range(700)
    )
    big_text = ("'" + "a" * 19_000 + " near '" + "'" * 500)[:20_000]
    rd.redact_sql("SELECT 1 FROM t WHERE a = '1'", policy=ctx.policy, vault=ctx.vault)
    for call in (
        lambda: rd.redact_sql(
            big_sql[:20_000], policy=ctx.policy, vault=ctx.vault, catalog_columns=["zzzMemo"]
        ),
        lambda: rd.redact_sql(("a.b" * 7000)[:20_000], policy=ctx.policy, vault=ctx.vault),
        lambda: rd.redact_text(big_text, vault=ctx.vault, sql=big_sql),
    ):
        started = time.perf_counter()
        call()
        assert (time.perf_counter() - started) < 0.2  # 기존 단언과 같은 상한(실측 중앙값 76ms)
