"""판정 계약 교정 — Y-1·Y-3~Y-7·O-a (plans/94 §16~§18 · D-218).

**Y-9(긍정 SQL 단언 가드)는 벤치 세션 소유다** — 여기서 판정하지 않는다.

run `20260915-131903` 의 불합격 46건(401 제거 후) 중 **최소 14건은 제품이 옳은데 하네스가
틀렸다고 판정한 것**이었다. 네 가지가 같은 모양으로 틀렸다:

  - 선택지 **개수**를 계약으로 삼았다 → 미매핑 열이 2개인데 1개를 기대(I군 19턴 무효)
  - 실패 메시지가 **행 수 하나**뿐이었다 → 2338행짜리 정상 산출물이 *빈 파일*로 읽혔다
  - **팬아웃 합계**를 per-DB 기대값과 비교했다 → 각 DB 가 정확했는데 불합격
  - SQL 의 **표기**를 봤다 → 기간이 정확한데 `202607` 이 아니라고 불합격

여기서 그 네 가지와 후속(분류·보류 규칙·원시 로그)을 못박는다. 전부 무과금이다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.analyze import countermeasures, deterministic_failures
from scripts.scenario.assertions import Observation, evaluate_turn, sql_period_bounds
from scripts.scenario.catalog import Group, Scenario, Turn, load_catalog
from scripts.scenario.report import classify_failure


def _eval(expect: dict[str, Any], obs: Observation, *, mock: bool = False):
    scenario = Scenario(id="T-01", group="T", plans=[94], title="t",
                        turns=[Turn(send={"query": "q"}, expect=expect)])
    group = Group(id="T", name="t", latency_target_ms=10000)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, group, mock=mock)


# --- Y-1. 선택지는 개수가 아니라 내용으로 본다 -----------------------------


def _form_fill(names: list[str]) -> Observation:
    return Observation(
        status="clarification",
        form_fill_clarification={
            "question": f"채우지 못한 항목이 {len(names)}건 있습니다.",
            "fields": [{"name": n, "label": n} for n in names],
            "candidates": ["dtype", "id", "acl_id"],
        },
    )


def test_Y1_options_contains_는_열이_늘어도_깨지지_않는다() -> None:
    """**I군 전멸의 단일 원인.** 미매핑 열이 '비고'·'담당자' 2개인데 카탈로그가 1개를 기대했다."""
    expect = {"clarification": {"options_contains": ["담당자"]}}

    assert _eval(expect, _form_fill(["담당자"])).func == "pass"
    assert _eval(expect, _form_fill(["비고", "담당자"])).func == "pass"


def test_Y1_되묻지_않은_항목은_여전히_잡는다() -> None:
    """부분 단언이 판정을 꺼 버리지는 않는다."""
    verdict = _eval({"clarification": {"options_contains": ["담당자"]}}, _form_fill(["비고"]))

    assert verdict.func == "fail"
    assert verdict.failures[0].key == "clarification.options_contains"
    assert verdict.failures[0].actual == ["비고"]        # 무엇을 물었는지 보인다


def test_Y1_options_len_은_그대로_남는다() -> None:
    """개수 자체가 계약인 곳(존 3개)은 종전 단언을 쓴다."""
    obs = Observation(status="clarification", clarification={
        "kind": "zone_select",
        "options": [{"db_id": "polestar_b0"}, {"db_id": "polestar_cm_gp"}],
    })
    assert _eval({"clarification": {"options_len": 3}}, obs).func == "fail"
    assert _eval({"clarification": {"options_len": 2}}, obs).func == "pass"


def test_Y1_존_선택지는_db_id_로도_대조된다() -> None:
    obs = Observation(status="clarification", clarification={
        "kind": "zone_select",
        "options": [{"db_id": "polestar_cm_gp", "label": "김포"}],
    })
    assert _eval({"clarification": {"options_contains": ["polestar_cm_gp"]}}, obs).func == "pass"
    assert _eval({"clarification": {"options_contains": ["김포"]}}, obs).func == "pass"


def test_Y1_카탈로그의_I군은_부분_단언을_쓴다() -> None:
    """픽스처 헤더가 바뀌면 다시 깨지는 단언을 남겨 두지 않는다."""
    catalog = load_catalog()
    specs = [
        turn.expect["clarification"]
        for scenario in catalog.scenarios if scenario.group == "I"
        for turn in scenario.turns if "clarification" in turn.expect
    ]
    assert specs, "I군에 역질문 단언이 사라졌다"
    assert all("options_len" not in spec for spec in specs), specs
    assert all("담당자" in (spec.get("options_contains") or []) for spec in specs), specs


# --- Y-3·V26. 실패 메시지가 열 단위 공란 수를 싣는다 -----------------------


def _xlsx(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _file_verdict(tmp_path: Path, spec: dict[str, Any], header: list[str],
                  rows: list[list[Any]]):
    path = _xlsx(tmp_path / "out.xlsx", header, rows)
    obs = Observation(status="completed", has_file=True, executed_sql="SELECT 1",
                      row_count=len(rows), artifacts=[str(path)])
    return _eval({"file": spec}, obs)


def test_V26_실패_메시지가_열_단위_공란_수를_싣는다(tmp_path: Path) -> None:
    """「filled_rows 기대 1 실제 0」은 *빈 파일*로 읽힌다 - 실제는 2338행짜리 정상 산출물이었다."""
    verdict = _file_verdict(
        tmp_path,
        {"columns": ["호스트명", "IP", "비고"], "filled_rows": {"min": 100}},
        ["호스트명", "IP", "비고"],
        [[f"srv-{i}", f"10.0.0.{i}", None] for i in range(200)],
    )

    actual = verdict.failures[0].actual
    assert actual["filled_rows"] == 0 and actual["data_rows"] == 200
    assert actual["empty_by_column"]["비고"] == "200/200"
    assert actual["empty_by_column"]["호스트명"] == "0/200"


def test_V26_optional_columns_는_filled_rows_를_깎지_않는다(tmp_path: Path) -> None:
    """'비고' 같은 자유 서술 열이 공란 정답일 때의 표현 수단(G-4 확정 전에는 아무도 쓰지 않는다)."""
    verdict = _file_verdict(
        tmp_path,
        {"columns": ["호스트명", "IP", "비고"], "optional_columns": ["비고"],
         "filled_rows": {"min": 100}},
        ["호스트명", "IP", "비고"],
        [[f"srv-{i}", f"10.0.0.{i}", None] for i in range(200)],
    )

    assert verdict.func == "pass"


def test_optional_columns_를_선언하지_않으면_종전과_같다(tmp_path: Path) -> None:
    """기본값으로 동작을 바꾸지 않는다 - 선언한 카탈로그만 달라진다."""
    spec = {"columns": ["호스트명", "비고"], "filled_rows": {"min": 1}}
    rows = [["srv-1", None]]

    assert _file_verdict(tmp_path, spec, ["호스트명", "비고"], rows).func == "fail"


def test_filled_columns_로_검사_대상을_직접_고른다(tmp_path: Path) -> None:
    verdict = _file_verdict(
        tmp_path,
        {"columns": ["호스트명", "IP", "비고"], "filled_columns": ["호스트명", "IP"],
         "filled_rows": {"min": 2}},
        ["호스트명", "IP", "비고"],
        [["srv-1", "10.0.0.1", None], ["srv-2", "10.0.0.2", None]],
    )
    assert verdict.func == "pass"


# --- Y-4. 행 수는 DB별과 합계를 가른다 --------------------------------------


def _fanout(total: int, per_db: dict[str, int]) -> Observation:
    return Observation(status="completed", executed_sql="SELECT 1", row_count=total,
                       row_counts_by_db=dict(per_db), db_ids=sorted(per_db))


def test_Y4_팬아웃_턴에서_row_count_는_보류다() -> None:
    """D-02: 각 DB 가 정확히 100행이었는데 합계 300 이 `max: 100` 에 걸려 불합격이었다."""
    verdict = _eval({"row_count": {"max": 100}},
                    _fanout(300, {"polestar_b0": 100, "polestar_cm_gp": 100,
                                  "polestar_cm_yd": 100}))

    assert verdict.func == "manual"
    assert verdict.failures == []
    assert any("row_count_per_db" in note for note in verdict.manual_notes)


def test_Y4_row_count_per_db_는_DB_하나하나를_본다() -> None:
    per_db = {"polestar_b0": 100, "polestar_cm_gp": 100, "polestar_cm_yd": 100}
    assert _eval({"row_count_per_db": {"max": 100}}, _fanout(300, per_db)).func == "pass"

    verdict = _eval({"row_count_per_db": {"max": 100}},
                    _fanout(310, {**per_db, "polestar_cm_yd": 110}))
    assert verdict.func == "fail"
    assert verdict.failures[0].actual == "polestar_cm_yd=110"   # 어느 DB 가 넘었는지 말한다


def test_Y4_row_count_total_은_합계를_본다() -> None:
    per_db = {"polestar_b0": 1, "polestar_cm_gp": 1, "polestar_cm_yd": 1}
    assert _eval({"row_count_total": {"eq": 3}}, _fanout(3, per_db)).func == "pass"
    assert _eval({"row_count_total": {"eq": 1}}, _fanout(3, per_db)).func == "fail"


def test_Y4_단일_DB_턴의_row_count_는_그대로_판정된다() -> None:
    """교정이 단일 DB 경로를 건드리지 않는다."""
    obs = _fanout(5, {"polestar_cm_gp": 5})
    assert _eval({"row_count": {"eq": 5}}, obs).func == "pass"
    assert _eval({"row_count": {"eq": 1}}, obs).func == "fail"


def test_Y4_DB별_행수를_관측하지_못하면_보류다() -> None:
    """감사 로그 tail 이 없으면 모른다 - 통과로도 불합격으로도 세지 않는다."""
    verdict = _eval({"row_count_per_db": {"max": 100}},
                    Observation(status="completed", executed_sql="SELECT 1", row_count=300))

    assert verdict.func == "manual"


def test_Y4_감사_로그가_DB별_행수를_채운다() -> None:
    """재시도가 섞여도 **마지막 성공분**만 센다 - 합산하면 100행이 200행으로 보인다."""
    obs = Observation()
    runner_mod._apply_sql_audit(obs, [
        {"sql": "SELECT 1", "source": "polestar_b0", "row_count": 0,
         "success": False, "retry_attempt": 0},
        {"sql": "SELECT 2", "source": "polestar_b0", "row_count": 100,
         "success": True, "retry_attempt": 1},
        {"sql": "SELECT 3", "source": "polestar_cm_gp", "row_count": 100,
         "success": True, "retry_attempt": 0},
    ])

    assert obs.row_counts_by_db == {"polestar_b0": 100, "polestar_cm_gp": 100}
    assert obs.retries == 1


# --- Y-5. 기간은 표기가 아니라 의미로 본다 ----------------------------------


_D03_SQL = (
    "SELECT COUNT(*) FROM polestar.cmm_alarm a "
    "WHERE a.ctime >= TIMESTAMP '2026-07-01 00:00:00' "
    "AND a.ctime < TIMESTAMP '2026-08-01 00:00:00'"
)


@pytest.mark.parametrize("sql", [
    _D03_SQL,                                                   # 배타 경계
    "SELECT * FROM t WHERE ctime BETWEEN '2026-07-01' AND '2026-07-31'",   # 포함 경계
    "SELECT * FROM cmm_metric_stat_m WHERE yyyymm = '202607'",             # 월 파티션
    "SELECT * FROM t WHERE d >= 20260701 AND d < 20260801",                # 구분자 없는 표기
])
def test_Y5_같은_기간이면_표기가_달라도_통과한다(sql: str) -> None:
    obs = Observation(status="completed", executed_sql=sql, row_count=1)
    expect = {"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}}

    assert _eval(expect, obs).func == "pass", sql


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t WHERE ctime >= TIMESTAMP '2026-06-01' AND ctime < TIMESTAMP '2026-07-01'",
    "SELECT * FROM t WHERE ctime >= TIMESTAMP '2026-07-15'",     # 상한 없음
    "SELECT * FROM cmm_metric_stat_m WHERE yyyymm = '202606'",
])
def test_Y5_기간이_모자라면_불합격이다(sql: str) -> None:
    obs = Observation(status="completed", executed_sql=sql, row_count=1)
    expect = {"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}}

    assert _eval(expect, obs).func == "fail", sql


def test_Y5_날짜가_없는_SQL_은_불합격이다() -> None:
    obs = Observation(status="completed", executed_sql="SELECT * FROM t", row_count=1)
    verdict = _eval({"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}}, obs)

    assert verdict.func == "fail"
    assert verdict.failures[0].actual == "SQL 에 날짜 리터럴이 없다"


def test_Y5_SQL_을_못_봤으면_보류다() -> None:
    """**못 본 것**을 **틀린 것**으로 세지 않는다 - 긍정 SQL 단언 가드와 같은 규칙이다."""
    obs = Observation(status="completed", row_count=1)
    assert _eval({"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}}, obs).func == "manual"


def test_Y5_월_리터럴은_경계_두_개로_펴진다() -> None:
    low, high = sql_period_bounds(["WHERE yyyymm = '202607'"])
    assert (low.isoformat(), high.isoformat()) == ("2026-07-01", "2026-08-01")


def test_Y5_12월_리터럴의_다음_달은_이듬해_1월이다() -> None:
    low, high = sql_period_bounds(["WHERE yyyymm = '202612'"])
    assert (low.isoformat(), high.isoformat()) == ("2026-12-01", "2027-01-01")


def test_Y5_날짜가_아닌_8자리_숫자는_경계가_아니다() -> None:
    assert sql_period_bounds(["WHERE id = 99999999"]) == (None, None)


# --- Y-7. 실패 분류에 세 유형이 생긴다 --------------------------------------


def _failed(*keys: str, **row: Any) -> dict[str, Any]:
    base = {"failed_assertions": [{"key": k} for k in keys], "executed_sql": "SELECT 1",
            "error": None, "forbidden_mode": None, "row_count": 5}
    base.update(row)
    return base


@pytest.mark.parametrize("keys,expected", [
    (("clarification.options_len",), "clarify"),
    (("clarification.options_contains",), "clarify"),
    (("clarification",), "clarify"),
    (("row_count.max",), "volume"),
    (("row_count_per_db.max",), "volume"),
    (("row_count_total.eq",), "volume"),
    (("response_must_contain",), "contract"),
    (("period_covers",), "semantics"),
    (("sql_must_match",), "semantics"),
])
def test_Y7_신규_분류_3종(keys: tuple[str, ...], expected: str) -> None:
    assert classify_failure(_failed(*keys)) == expected


def test_Y7_0건_진단은_volume_에_먹히지_않는다() -> None:
    """`empty_result` 는 데이터 부재와 SQL 오류를 가르는 축이라 그대로 남아야 한다."""
    assert classify_failure(_failed("row_count.min", row_count=0)) == "empty_result"


def test_Y7_부정_단언은_여전히_guard_다() -> None:
    assert classify_failure(_failed("response_must_not_contain")) == "guard"


# --- Y-6·V27. 시나리오 반복 ≥3 전건 동일 실패는 결정적이다 ------------------


def _row(scenario_id: str, repeat: int, verdict: str, *keys: str) -> dict[str, Any]:
    return {"scenario_id": scenario_id, "repeat": repeat, "turn": 1, "group": "K",
            "func_verdict": verdict, "forbidden_mode": None, "row_count": 1,
            "failed_assertions": [{"key": k} for k in keys]}


def test_V27_반복_3회_전건_동일_실패는_결정적이다() -> None:
    """K-01 5/5 · K-04 3/3 · R1-03 3/3 이 전부 `불안정·보류` 로 내려갔었다."""
    rows = [_row("K-01", r, "fail", "sql_must_match") for r in range(5)]
    found = deterministic_failures(rows)

    assert found["K-01"]["repeats"] == 5
    assert found["K-01"]["keys"] == ["sql_must_match"]


def test_V27_회차마다_다른_곳이_깨지면_흔들림이다() -> None:
    rows = [
        _row("K-02", 0, "fail", "sql_must_match"),
        _row("K-02", 1, "fail", "row_count.min"),
        _row("K-02", 2, "fail", "sql_must_match"),
    ]
    assert "K-02" not in deterministic_failures(rows)


def test_V27_한_번이라도_통과하면_결정적이_아니다() -> None:
    rows = [
        _row("K-03", 0, "fail", "sql_must_match"),
        _row("K-03", 1, "pass"),
        _row("K-03", 2, "fail", "sql_must_match"),
    ]
    assert "K-03" not in deterministic_failures(rows)


def test_V27_반복이_2회면_아직_결정적이_아니다() -> None:
    rows = [_row("R2-09", r, "fail", "sql_must_match") for r in range(2)]
    assert deterministic_failures(rows) == {}


def test_V27_결정적_실패는_보류로_내려가지_않는다() -> None:
    """run 단위 `--repeat` 이 1회여도 시나리오 반복이 봤으면 처방을 낸다."""
    rows = [_row("K-01", r, "fail") | {"forbidden_mode": "silent_wrong"} for r in range(5)]
    summary = {"meta": {"repeat": 1}, "misuse": {}}
    document = countermeasures(summary, rows)

    assert "결정적으로 확정된 실패" in document
    assert "K-01" in document
    assert "전건 `불안정·보류`" not in document


def test_V27_반복이_부족하면_종전대로_보류한다() -> None:
    rows = [_row("A-01", 0, "fail") | {"forbidden_mode": "hang"}]
    document = countermeasures({"meta": {"repeat": 1}, "misuse": {}}, rows)

    assert "전건 `불안정·보류`" in document


# --- O-a. 사후 판정의 재료를 raw.jsonl 에 싣는다 -----------------------------


def _raw_row(obs: Observation) -> dict[str, Any]:
    scenario = Scenario(id="I-01", group="I", plans=[73], title="t",
                        turns=[Turn(send={"query": "q"}, expect={})])
    meta = {"run_id": "r", "env": "closed", "mode": "run"}
    from scripts.scenario.assertions import Verdict

    return runner_mod._row(meta, "baseline", scenario, 1, 0, obs, Verdict())


def test_Oa_응답_본문이_원시_로그에_실린다() -> None:
    """수동 검토 185턴(66%)을 사후에 판정할 방법이 없던 것이 O-2 였다."""
    row = _raw_row(Observation(status="completed", response="김포 서버 5건입니다"))

    assert row["response_text"] == "김포 서버 5건입니다"
    assert row["response_truncated"] is False
    assert json.dumps(row, ensure_ascii=False)          # 직렬화 가능해야 적재된다


def test_Oa_긴_응답은_절단하고_절단_사실을_남긴다() -> None:
    row = _raw_row(Observation(status="completed", response="가" * 10_000))

    assert len(row["response_text"]) == runner_mod.RESPONSE_TEXT_MAX
    assert row["response_truncated"] is True


def test_Oa_역질문_선택지가_원시_로그에_실린다() -> None:
    """이번 I군 원인('비고')도 1.49 GB 체크포인트를 msgpack 수준에서 파싱해서야 확인했다."""
    obs = _form_fill(["비고", "담당자"])
    obs.form_fill_clarification["candidates"] = [f"col{i}" for i in range(86)]
    row = _raw_row(obs)

    snapshot = row["clarification_options"]
    assert snapshot["options"] == ["비고", "담당자"]
    assert snapshot["options_len"] == 2
    # P-14: 후보 86개가 원시 스키마 순서로 나온 사실 자체가 판정 대상이다.
    assert snapshot["candidates_len"] == 86
    assert len(snapshot["candidates_head"]) == 10


def test_Oa_컬럼_매핑이_원시_로그에_실린다() -> None:
    """H-03 의 '리소스유형' 전 행 공란이 매핑 실패인지 조회 누락인지 가르는 재료다(J-4)."""
    row = _raw_row(Observation(status="completed", column_mapping={"호스트명": "hostname"}))

    assert row["column_mapping"] == {"호스트명": "hostname"}


def test_Oa_역질문이_없으면_None_이다() -> None:
    assert _raw_row(Observation(status="completed"))["clarification_options"] is None


def test_Oa_DB별_행수가_원시_로그에_실린다() -> None:
    row = _raw_row(_fanout(300, {"polestar_b0": 100, "polestar_cm_gp": 200}))

    assert row["row_counts_by_db"] == {"polestar_b0": 100, "polestar_cm_gp": 200}
