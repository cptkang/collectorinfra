"""판정기·카탈로그 (plans/123 트랙 V · CT) — LLM·서버·DB 0.

고정하는 계약:
  1. V-1 kind → 등급 표는 제품 정본(`src/domain/disclosure.KIND_TABLE`)의 사본이다(drift) · kind 가
     표지어보다 먼저 등급을 정한다 · kind 로 등급이 정해진 고지 문구는 표지어 대조에서 걷는다 ·
     상한 고지만의 partial 은 answer 를 허용한 시나리오에서 answer 동치다 · `disclosures_contains`.
  2. V-3 안내 표지 보강 · 0행 응답은 SQL 이 있어도 안내 표지를 본다 · 8번째 등급 `empty_template`.
  3. V-4 불변식 — 모든 턴에 계산(트리아지) · 군 헤더가 활성으로 선언하고 run 이 고지를 수집했을
     때만 판정.
  4. V-5 활성 불변식 위반 + answer·empty_template = silent_wrong.
  5. V-2·V-6 러너·재판정 — 역질문 기록 · 존 선택 · `pre_answer_mode` · 입력 변경 보류.
  6. CT — R군 등급 정책 확정(동결 가드) · 불변식 선언 · 카탈로그 키.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.scenario import clarify, invariants
from scripts.scenario.assertions import Observation, classify_mode, evaluate_turn
from scripts.scenario.catalog import (
    DISCLOSURE_KIND_GRADES,
    INVARIANTS,
    RESPONSE_MODES,
    CatalogError,
    Group,
    Scenario,
    Turn,
    judgement_digest,
    load_catalog,
)
from scripts.scenario.rejudge import (
    input_gap,
    restore_observation,
    restore_pre_answer_mode,
    restore_zone_selection,
)
from scripts.scenario.runner import _turn_zone_selection
from src.domain import disclosure as disc
from tests.test_scenario.conftest import write

ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _scenario(query: str = "서버 목록", *, modes: list[str] | None = None, expect=None,
              kind: str = "normal", turns: list[Turn] | None = None, **kwargs) -> Scenario:
    return Scenario(
        id="T-01", group="T", plans=[123], title="t", kind=kind,
        response_modes=modes or [],
        turns=turns or [Turn(send={"query": query}, expect=expect or {})], **kwargs,
    )


def _group(*, invariants_decl: dict | None = None, policy: bool = True) -> Group:
    return Group(id="T", name="t", latency_target_ms=10000, policy_confirmed=policy,
                 invariants=invariants_decl or {})


def _obs(response: str = "", *, rows: int | None = None, sql: str | None = "SELECT 1",
         disclosures: list[dict] | None = None, **kwargs) -> Observation:
    return Observation(status="completed", http_status=200, response=response, row_count=rows,
                       executed_sql=sql, disclosures=disclosures, **kwargs)


def _d(kind: str, text: str = "") -> dict:
    return {"kind": kind, "text": text, "source": "turn"}


# --- 1. V-1 kind → 등급 ------------------------------------------------------------

def test_kind_grade_table_is_a_copy_of_the_product_table() -> None:
    """하네스는 제품 모듈을 import 하지 않는 사본을 쓴다 - 낡으면 등급이 조용히 갈린다."""
    assert {k: spec.grade for k, spec in disc.KIND_TABLE.items()} == DISCLOSURE_KIND_GRADES


def test_kind_decides_the_grade_before_markers() -> None:
    """읽기 전용 차단(kind sql_blocked)은 본문이 「데이터가 없습니다」여도 refuse 다."""
    obs = _obs("서버 데이터가 없습니다.", rows=0, sql=None,
               disclosures=[_d("sql_blocked",
                               "삭제·수정 같은 데이터 변경 요청은 수행할 수 없습니다")])
    assert classify_mode(obs) == ("refuse", "kind:sql_blocked")


def test_disclosed_text_is_not_read_as_markers() -> None:
    """생성기 메모 인용문의 「대신」은 correct 표지가 아니다(D-279 주의 ①).

    kind 로 이미 등급이 났다.
    """
    note = "생성기 메모: 「cpu_usage 대신 cpu_util 을 썼다」"
    obs = _obs(f"결과 5건입니다.\n\n{note}", rows=5, disclosures=[_d("generator_note", note)])
    assert classify_mode(obs) == ("answer", None)


def test_scope_narrowed_does_not_set_the_grade() -> None:
    """러너 자동 응답(D-216 ②)이 한 존을 고르면 답 턴마다 좁힌 범위 고지가 붙는다 - 등급 중립."""
    note = "전체가 아니라 김포만 조회했습니다. 은행존, 여의도은(는) 조회하지 않았습니다"
    obs = _obs(f"서버 1,690대입니다.\n\n- {note}", rows=1690,
               disclosures=[_d("scope_narrowed", note)])
    assert classify_mode(obs) == ("answer", None), "「전체가 아니」 표지어로 partial 이 되지 않는다"
    partial = _obs("서버 1,690대입니다.", rows=1690,
                   disclosures=[_d("scope_partial", "전체가 아니라 …")])
    assert classify_mode(partial) == ("partial", "kind:scope_partial")


def test_guide_marker_wins_over_context_kind() -> None:
    """「수집하지 않는 지표」 안내 응답에 상한 고지가 있어도 guide 다(R4-05 형)."""
    obs = _obs("해당 지표는 수집하지 않습니다.", rows=0,
               disclosures=[_d("row_limit_reached", "상한(LIMIT 10000)에 도달해 …")])
    assert classify_mode(obs)[0] == "guide"


def test_limit_only_partial_is_answer_equivalent_where_answer_is_allowed() -> None:
    """상한 도달 대조군(R2-07C · R3-08C).

    상한을 밝힌 정상 조회를 「정상 응답 아님」으로 떨어뜨리지 않는다.
    """
    obs = _obs("알람 임계치 목록입니다.", rows=10000,
               disclosures=[_d("row_limit_reached", "상한(LIMIT 10000)에 도달해 …")])
    control = _scenario(modes=["answer"], expect={"status": "completed"}, kind="control")
    verdict = evaluate_turn(control, 1, control.turns[0], obs, _group())
    assert verdict.response_mode == "partial" and verdict.func == "pass", verdict.failures
    guide_only = _scenario(modes=["guide"], expect={"status": "completed"})
    verdict = evaluate_turn(guide_only, 1, guide_only.turns[0], obs, _group())
    assert verdict.func == "fail", "answer 를 허용하지 않은 시나리오에는 동치가 없다"


def test_disclosures_contains_pass_fail_and_holds() -> None:
    scenario = _scenario(expect={"disclosures_contains": ["unregistered_zone"]})
    turn = scenario.turns[0]
    ok = _obs("…", rows=3, disclosures=[_d("unregistered_zone", "'판교존'은 …")])
    assert evaluate_turn(scenario, 1, turn, ok, _group(policy=False)).func == "pass"
    missing = _obs("…", rows=3, disclosures=[])
    verdict = evaluate_turn(scenario, 1, turn, missing, _group(policy=False))
    assert verdict.func == "fail" and verdict.failures[0].key == "disclosures_contains"
    uncollected = evaluate_turn(scenario, 1, turn, _obs("…", rows=3), _group(policy=False))
    assert uncollected.func == "manual" and "unobservable" in uncollected.manual_sources
    question = Observation(status="clarification", clarification={"kind": "zone_select"},
                           disclosures=[])
    held = evaluate_turn(scenario, 1, turn, question, _group(policy=False))
    assert held.func == "manual", "역질문으로 끝난 턴에는 조회 고지가 없다"


# --- 2. V-3 안내 표지 · empty_template ----------------------------------------------

def test_empty_template_is_the_eighth_mode() -> None:
    assert "empty_template" in RESPONSE_MODES and len(RESPONSE_MODES) == 8


@pytest.mark.parametrize("text", ["지원 범위에 포함되지 않습니다", "현재 제공하고 있지 않습니다",
                                  "해당 정보는 제공되지 않습니다"])
def test_new_guide_markers(text: str) -> None:
    assert classify_mode(_obs(text, rows=None, sql=None))[0] == "guide"


def test_zero_rows_read_guide_markers_even_with_sql() -> None:
    """「nonexistent-01 은 존재하지 않습니다」를 조회 뒤에 말한 응답이 answer 로 분류됐다.

    run 20260923-103638 실측.
    """
    obs = _obs("nonexistent-01 서버는 존재하지 않습니다.", rows=0)
    assert classify_mode(obs)[0] == "guide"
    with_rows = _obs("존재하지 않는 값은 제외했습니다.", rows=5)
    # 행이 있는 응답은 종전대로 안내 표지를 보지 않는다
    assert classify_mode(with_rows)[0] == "answer"


def test_empty_template_needs_zero_rows() -> None:
    template = ("조건에 해당하는 서버 데이터가 없습니다.\n"
                "- 필터 조건을 완화해보세요 (예: 임계값 낮추기)")
    assert classify_mode(_obs(template, rows=0)) == ("empty_template", "데이터가 없습니다")
    assert classify_mode(_obs(template, rows=None, sql="SELECT 1"))[0] == "answer", \
        "SQL 은 있는데 행 수를 관측하지 못했으면 0행으로 보지 않는다"


# --- 3·4. V-4 불변식 · V-5 silent_wrong ---------------------------------------------

def _limit_obs(**kwargs) -> Observation:
    return _obs("서버 목록입니다.", rows=10000, sql="SELECT * FROM t LIMIT 10000",
                sql_entries=[{"sql": "SELECT * FROM t LIMIT 10000", "row_count": 10000,
                              "success": True}], **kwargs)


def test_invariants_are_triage_until_declared_active() -> None:
    scenario = _scenario("전체 서버 목록", modes=["answer"], expect={"status": "completed"})
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], _limit_obs(disclosures=[]), _group())
    names = [v["name"] for v in verdict.invariant_violations]
    assert "limit_disclosed" in names and not any(v["active"] for v in verdict.invariant_violations)
    assert verdict.func == "pass", "트리아지 칸은 판정을 바꾸지 않는다"


def test_active_invariant_fails_and_marks_silent_wrong() -> None:
    scenario = _scenario("전체 서버 목록", modes=["answer"], expect={"status": "completed"})
    group = _group(invariants_decl={"limit_disclosed": "R5"})
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], _limit_obs(disclosures=[]), group)
    assert verdict.func == "fail" and verdict.forbidden_mode == "silent_wrong"
    assert [f.key for f in verdict.failures] == ["invariant.limit_disclosed"]


def test_active_invariant_needs_collected_disclosures() -> None:
    """과거 run(고지 미수집)은 선언이 활성이어도 트리아지다.

    그 run 의 제품에는 소유 수정이 없다.
    """
    scenario = _scenario("전체 서버 목록", modes=["answer"], expect={"status": "completed"})
    group = _group(invariants_decl={"limit_disclosed": "R5"})
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], _limit_obs(disclosures=None), group)
    assert verdict.func == "pass" and verdict.invariant_violations[0]["active"] is False


def test_limit_disclosed_ignores_requested_counts_and_disclosed_limits() -> None:
    top = _obs("상위 10대입니다.", rows=10, sql="SELECT * FROM t LIMIT 10",
               sql_entries=[{"sql": "SELECT * FROM t LIMIT 10", "row_count": 10, "success": True}],
               disclosures=[])
    scenario = _scenario("CPU 상위 10대")
    assert invariants._limit_disclosed(scenario, 1, "CPU 상위 10대", top, "answer") is None
    disclosed = _limit_obs(disclosures=[_d("row_limit_reached", "상한 …")])
    assert invariants._limit_disclosed(scenario, 1, "전체 서버", disclosed, "partial") is None
    # 「N건 이상」은 건수 지정이 아니다(W-0)
    assert invariants.requested_counts("3건 이상 난 서버") == set()


def test_zone_coverage_named() -> None:
    selection = {"selected": ["polestar_cm_gp"], "offered": ZONES, "source": "auto"}
    bare = _obs("전체 서버는 1,690대입니다.", rows=1, disclosures=[], zone_selection=selection)
    scenario = _scenario("전체 서버 몇 대야?")
    def check(query: str, obs: Observation) -> object:
        return invariants._zone_coverage_named(scenario, 1, query, obs, "answer")

    assert check("전체 서버 몇 대야?", bare)
    named = _obs("…", rows=1, disclosures=[_d("scope_narrowed", "김포만 조회했습니다")],
                 zone_selection=selection)
    assert check("전체 서버 몇 대야?", named) is None
    assert check("김포 서버 몇 대야?", bare) is None


def test_empty_template_misuse_and_silent_wrong() -> None:
    scenario = _scenario("nonexistent-01 서버 사양", modes=["guide", "empty_template"],
                         expect={"status": "completed"})
    template = _obs("조건에 해당하는 서버 데이터가 없습니다.", rows=0, disclosures=[])
    group = _group(invariants_decl={"empty_template_misuse": "R5′"})
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], template, group)
    assert verdict.response_mode == "empty_template"
    assert verdict.forbidden_mode == "silent_wrong", "빈 결과 템플릿도 V-5 대상이다"
    conflict = invariants._empty_template_misuse(
        scenario, 1, "CPU 90% 넘으면서 10% 미만인 서버", template, "empty_template")
    assert conflict == {"conditions": ["conflict"]}


def test_other_invariants() -> None:
    scenario = _scenario("그 장비 정보 보여줘")
    rows = _obs("서버 목록입니다.", rows=1690, disclosures=[])
    assert invariants._demonstrative_bulk(scenario, 1, "그 장비 정보 보여줘", rows, "answer") == \
        {"rows": 1690}
    general = _obs("모든 서버가 VMware 입니다.", rows=409, disclosures=[])
    assert invariants._overgeneralization(scenario, 1, "q", general, "answer")["marker"] == "모든"
    uploaded = _obs("업로드하신 양식에 채웠습니다.", rows=3, disclosures=[])
    assert invariants._upload_misattributed(scenario, 1, "q", uploaded, "answer")


def test_invariants_skip_questions_mock_probe_and_truncated_rows() -> None:
    scenario = _scenario("전체 서버 목록", modes=["answer"], expect={"status": "completed"})
    group = _group(invariants_decl={"limit_disclosed": "R5"})
    truncated = _limit_obs(disclosures=[], response_truncated=True)
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], truncated, group)
    assert verdict.invariant_violations == [] and "invariant" in verdict.manual_sources
    assert verdict.func == "manual"
    mock = evaluate_turn(scenario, 1, scenario.turns[0], _limit_obs(disclosures=[]), group,
                         mock=True)
    assert mock.invariant_violations == []


def test_invariant_names_match_the_catalog_vocabulary() -> None:
    assert set(invariants.CHECKS) == set(INVARIANTS)


# --- CT-6 결과 행 합 -------------------------------------------------------------

def test_result_matches_db_row_sum() -> None:
    scenario = _scenario(expect={"result": {"matches_db_row_sum": True}})
    turn = scenario.turns[0]

    def run(total: int, per_db: dict) -> object:
        obs = _obs("…", rows=total, row_counts_by_db=per_db, disclosures=[])
        obs.result = {"status": "ok", "total_rows": total, "columns": ["h"],
                      "rows": [{"h": "a"}] * min(total, 3)}
        return evaluate_turn(scenario, 1, turn, obs, _group(policy=False))

    assert run(2449, {"polestar_cm_gp": 1690, "polestar_cm_yd": 759}).func == "pass"
    lost = run(2442, {"polestar_cm_gp": 1690, "polestar_cm_yd": 759})
    assert lost.func == "fail" and lost.failures[0].key == "result.matches_db_row_sum"
    assert run(3, {}).func == "manual", "DB별 행 수를 못 봤으면 보류"


# --- 5. V-2 러너 기록 · V-6 재판정 ----------------------------------------------------

def test_question_record_keeps_reason_text_and_offered_zones() -> None:
    question = clarify.Question(kind="zone", payload={
        "kind": "zone_select", "reason": "unregistered_zone",
        "question": "'판교존'은(는) 등록되지 않은 존입니다." + "…" * 300,
        "options": [{"db_id": d, "label": d} for d in ZONES]})
    record = clarify.question_record(question)
    assert record["reason"] == "unregistered_zone" and record["offered_db_ids"] == ZONES
    assert len(record["question"]) == clarify.QUESTION_TEXT_MAX


def test_turn_zone_selection_uses_previous_question_options() -> None:
    last = Observation(status="clarification", clarification={
        "kind": "zone_select", "options": [{"db_id": d} for d in ZONES]})
    assert _turn_zone_selection({"selected_db_ids": ["polestar_cm_gp"]}, last) == {
        "selected": ["polestar_cm_gp"], "offered": ZONES, "source": "turn"}
    assert _turn_zone_selection({"query": "q"}, last) is None


def test_rejudge_restores_123_fields(tmp_path: Path) -> None:
    old = {"response_text": "…", "auto_answers": [
        {"kind": "zone_select", "selected_db_ids": ["polestar_cm_gp"]}],
        "executed_sqls": [{"sql": "SELECT 1 LIMIT 10000", "row_count": 10000, "success": True}]}
    obs, _estimated = restore_observation(old, tmp_path)
    assert obs.disclosures is None, "칸이 없으면 수집하지 않은 run 이다"
    assert obs.sql_entries[0]["row_count"] == 10000
    assert obs.zone_selection == {"selected": ["polestar_cm_gp"], "offered": None, "source": "auto"}
    # 자동 응답이 있었으면 첫 응답은 역질문이었다
    assert restore_pre_answer_mode(old, "answer") == "clarify"
    new = {**old, "disclosures": [], "pre_answer_mode": "answer",
           "zone_selection": {"selected": ["x"], "offered": ["x", "y"], "source": "turn"}}
    obs, _estimated = restore_observation(new, tmp_path)
    assert obs.disclosures == [] and restore_zone_selection(new)["offered"] == ["x", "y"]
    assert restore_pre_answer_mode(new, "clarify") == "answer"


def test_input_gap_excludes_changed_inputs() -> None:
    from scripts.scenario.catalog import Catalog

    def catalog_with(turns: list[Turn]) -> Catalog:
        scenario = Scenario(id="R3-03", group="T", plans=[123], title="t", turns=turns)
        return Catalog(profiles={"baseline": {}}, groups={"T": _group()}, scenarios=[scenario])

    before = catalog_with([Turn({"query": "판교존 서버 목록"}, {})])
    after = catalog_with([Turn({"query": "판교존 서버 목록"}, {"status": "clarification"}),
                          Turn({"selected_db_ids": ["polestar_cm_gp"]}, {})])
    row = {"scenario_id": "R3-03", "turn": 1}
    scenario = after.scenarios[0]
    assert input_gap(row, before.scenarios[0], 1, before) is None, "같은 입력은 비교한다"
    gap = input_gap(row, scenario, 1, before)
    assert gap and "턴 수" in gap, "턴 수가 바뀌면 자동 응답 여부가 바뀐다"
    assert input_gap(row, scenario, 1, None) is None, "입력 카탈로그가 없으면 대조하지 않는다"


# --- 6. CT 카탈로그 ------------------------------------------------------------------

def test_policy_confirmed_groups_are_frozen() -> None:
    """등급 정책 확정(`policy_confirmed: true`)은 R군 4개뿐이다(123·G-2 (b) · D-280 ②).

    A~K·L·M 군을 확정하는 것은 그 군 소유 계획(122 등)의 사용자 결정이다 - 조용히 늘지 않게 막는다.
    """
    confirmed = sorted(g for g, group in load_catalog().groups.items() if group.policy_confirmed)
    assert confirmed == ["R1", "R2", "R3", "R4"]


def test_r_groups_declare_invariants_with_owner_run() -> None:
    groups = load_catalog().groups
    for group_id in ("R1", "R2", "R3", "R4"):
        declared = groups[group_id].invariants
        assert set(declared) == set(INVARIANTS), group_id
        assert {name for name, since in declared.items() if since} == {
            "limit_disclosed", "zone_coverage_named", "upload_misattributed"}


def test_r_catalog_expectations_after_ct() -> None:
    catalog = load_catalog()
    by_id = {s.id: s for s in catalog.scenarios}
    # CT-2 - F-06형 2턴(역질문 선언 → 자동 응답 억제)
    for sid, kind in (("R3-03", "unregistered_zone"), ("R4-12", "scope_narrowed")):
        first, second = by_id[sid].turns
        assert first.expect["status"] == "clarification" and clarify.expects_question(first.expect)
        assert second.send == {"selected_db_ids": ["polestar_cm_gp"]}
        assert kind in second.expect["disclosures_contains"]
    # CT-3 - A-12형(파일 없음 + 안내) · 121 `plan` 단언 제거
    for sid in ("R1-03", "R4-07C"):
        expect = by_id[sid].turns[0].expect
        assert expect["has_file"] is False and "plan" not in expect
        assert expect["response_must_contain"] == ["양식 파일이 첨부되지 않아"]
    assert by_id["R4-07U"].upload and by_id["R4-07U"].turns[0].expect["has_file"] is True
    # CT-1 - 정상 0건 대조군 · 말없는 단정 제외 · 첫 턴 지시어
    assert "empty_template" in by_id["R1-09"].response_modes
    assert "answer" not in by_id["R3-09"].response_modes
    assert by_id["R4-04C"].response_modes == ["clarify"]
    # CT-5 - 부정 단언
    assert "(?i)select\\s+\\*" in by_id["R3-08"].turns[0].expect["sql_must_not_match"]
    assert by_id["R3-08"].turns[0].expect["sql_executed"] is False
    assert by_id["R3-12"].turns[0].expect["http_status"] == 422
    # G-9 (c) - J-02 와 R2-02 는 같은 기대
    j02 = by_id["J-02"].turns[0].expect["sql_must_not_match"]
    assert by_id["R2-02"].turns[0].expect["sql_must_not_match"] == j02


def test_r_manual_review_is_down_to_the_ct7_keep_list() -> None:
    """CT-7 이관표의 「수동 유지」 10턴만 남는다 - 새 수동 검토는 사유와 함께 이관표에 적는다."""
    kept = sorted(
        (s.id, index) for s in load_catalog().scenarios if s.group.startswith("R")
        for index, turn in enumerate(s.turns, start=1) if turn.expect.get("manual_review")
    )
    assert kept == [("R1-02", 1), ("R1-04", 1), ("R1-07", 1), ("R1-10", 1), ("R2-09", 2),
                    ("R2-09", 3), ("R3-02", 1), ("R4-03", 1), ("R4-10", 1), ("R4-11", 1)]


def _group_file(header_extra: str) -> str:
    return ("version: 1\ngroup:\n  id: T\n  name: \"t\"\n  latency_target_ms: 10000\n"
            f"{header_extra}scenarios:\n  - id: T-01\n    plans: [123]\n    title: \"t\"\n"
            "    turns:\n      - send: {query: \"q\"}\n        expect: {status: completed}\n")


def test_group_invariants_are_validated_and_in_the_digest(
    scenario_dir: Path, profiles_path: Path
) -> None:
    write(scenario_dir / "t.yaml", _group_file("  invariants: {nope: R5}\n"))
    with pytest.raises(CatalogError) as exc:
        load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)
    assert any("알 수 없는 불변식 'nope'" in e for e in exc.value.errors)

    write(scenario_dir / "t.yaml", _group_file("  invariants: {limit_disclosed: null}\n"))
    triage = load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)
    write(scenario_dir / "t.yaml", _group_file("  invariants: {limit_disclosed: R5}\n"))
    active = load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)
    assert active.groups["T"].invariants == {"limit_disclosed": "R5"}
    assert judgement_digest(active) != judgement_digest(triage), "활성 전환은 계약 변경이다"


def test_disclosures_contains_vocabulary_is_checked(
    scenario_dir: Path, profiles_path: Path
) -> None:
    text = _group_file("").replace("expect: {status: completed}",
                                   "expect: {disclosures_contains: [not_a_kind]}")
    write(scenario_dir / "t.yaml", text)
    with pytest.raises(CatalogError):
        load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)
