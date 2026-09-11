"""단언 평가기 수용 기준 V15·V17 + 대응 등급 (plans/94 §3.8 · §10)."""

from __future__ import annotations

import pytest

from scripts.scenario.assertions import Observation, classify_mode, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn


def _scenario(**kwargs) -> Scenario:
    base = dict(
        id="T-01", group="T", plans=[94], title="t",
        turns=[Turn(send={"query": "q"}, expect={})],
    )
    base.update(kwargs)
    return Scenario(**base)


def _group(policy_confirmed: bool = False) -> Group:
    return Group(id="T", name="t", latency_target_ms=10000, policy_confirmed=policy_confirmed)


def _eval(scenario: Scenario, obs: Observation, group: Group | None = None):
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, group or _group())


# --- 기본 L1 -----------------------------------------------------------

def test_기계_단언이_통과하면_합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"status": "completed", "row_count": {"min": 1}})])
    verdict = _eval(scenario, Observation(status="completed", row_count=5, executed_sql="SELECT 1"))
    assert verdict.func == "pass"
    assert verdict.failures == []


def test_단언_미작성은_합격이_아니라_수동검토다() -> None:
    """옮기지 않은 기대값이 합격으로 세어지면 커버리지가 부풀려진다(§3.4)."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"manual_review": "눈으로 볼 것"})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.func == "manual"
    assert verdict.manual_notes == ["눈으로 볼 것"]


def test_기계_단언이_통과해도_수동_항목이_남으면_합격이_아니다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"status": "completed", "manual_review": "확인"})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.func == "manual"


def test_row_count_min_위반은_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"row_count": {"min": 1}})])
    verdict = _eval(scenario, Observation(status="completed", row_count=0, executed_sql="SELECT 1"))
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "row_count.min"


# --- 대응 등급 (§3.8) ---------------------------------------------------

@pytest.mark.parametrize(
    "obs,expected",
    [
        (Observation(http_status=500), "crash"),
        (Observation(response="Traceback (most recent call last):"), "crash"),
        (Observation(hang=True), "hang"),
        (Observation(status="clarification", clarification={"kind": "zone_select"}), "clarify"),
        (Observation(status="error", http_status=400), "error"),
        (Observation(response="읽기 전용이라 수행할 수 없습니다"), "refuse"),
        (Observation(response="폴스타가 수집하지 않는 지표입니다"), "guide"),
        (Observation(response="cocm-hdkapp01 의 오타로 이해했습니다", executed_sql="SELECT 1"), "correct"),
        (Observation(response="상위 100건만 표시했습니다. 일부만 보여드립니다", executed_sql="SELECT 1"), "partial"),
        (Observation(response="5건입니다", executed_sql="SELECT 1"), "answer"),
    ],
)
def test_대응_등급_분류(obs: Observation, expected: str) -> None:
    mode, _evidence = classify_mode(obs)
    assert mode == expected


def test_금지_등급_crash_는_무조건_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {})])
    verdict = _eval(scenario, Observation(http_status=500, response="Internal Server Error"))
    assert verdict.forbidden_mode == "crash"
    assert verdict.func == "fail"


def test_V15_조용한_오답은_합격으로_새지_않는다() -> None:
    """착각을 그대로 받아 그럴듯한 답을 낸 경우. 부정 단언 위반 + answer = silent_wrong."""
    scenario = _scenario(turns=[Turn(
        {"query": "서버 가동률 보여줘"},
        {"column_must_not_map": ["cpu_usage"]},
    )])
    obs = Observation(
        status="completed",
        response="가동률 상위 5대입니다",
        executed_sql="SELECT hostname, cpu_usage FROM cmm_metric_stat_d",
        row_count=5,
    )
    verdict = _eval(scenario, obs)
    assert verdict.forbidden_mode == "silent_wrong"
    assert verdict.func == "fail"


def test_V15_되묻기는_silent_wrong_이_아니다() -> None:
    """거부와 되묻기는 사용자가 알아차린다 - 조용한 오답과 같은 취급을 하지 않는다."""
    scenario = _scenario(turns=[Turn(
        {"query": "서버 가동률 보여줘"}, {"column_must_not_map": ["cpu_usage"]})])
    obs = Observation(status="clarification", clarification={"kind": "metric_select"})
    verdict = _eval(scenario, obs)
    assert verdict.forbidden_mode is None
    assert verdict.response_mode == "clarify"


def test_V17_정책_확정_시_선언_밖_등급은_불합격이다() -> None:
    scenario = _scenario(response_modes=["refuse"], turns=[Turn({"query": "삭제해줘"}, {})])
    obs = Observation(status="completed", response="5건입니다", executed_sql="SELECT 1")
    verdict = _eval(scenario, obs, _group(policy_confirmed=True))
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "response_modes"


def test_G10b_정책_미확정_시_선언_밖_등급은_관측으로만_남는다() -> None:
    """'이때는 되물어야 한다'를 우리가 단정하지 않는다(R11). 1차는 manual 이다."""
    scenario = _scenario(response_modes=["refuse"], turns=[Turn({"query": "삭제해줘"}, {})])
    obs = Observation(status="completed", response="5건입니다", executed_sql="SELECT 1")
    verdict = _eval(scenario, obs, _group(policy_confirmed=False))
    assert verdict.func == "manual"
    assert any("등급 정책 미확정" in note for note in verdict.manual_notes)


# --- L2 -----------------------------------------------------------------

def test_sql_must_not_match_위반을_잡는다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"sql_must_not_match": ["(?i)\\bdelete\\b"]})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="DELETE FROM t"))
    assert verdict.func == "fail"


def test_column_must_not_map_은_매핑_산출물도_본다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"column_must_not_map": ["cpu_usage"]})])
    obs = Observation(status="completed", executed_sql="SELECT 1",
                      column_mapping={"가동률": "cpu_usage"})
    verdict = _eval(scenario, obs)
    assert verdict.failures[0].actual == "column_mapping 에 존재"


# --- 단언 키 전수 (§3.3 L1·L2) -------------------------------------------
#
# 키 하나가 조용히 안 걸리면 그 축의 판정이 통째로 사라진다. 전 키를 통과/불합격 쌍으로 덮는다.

_PASS_OBS = dict(status="completed", executed_sql="SELECT hostname FROM cmm_resource LIMIT 100",
                 row_count=5, response="김포 서버 5건입니다", intent="data_query",
                 db_ids=["polestar_cm_gp"], has_file=False, llm_calls=4, retries=1,
                 node_path=["field_mapper", "deep_agent"], sse_events=["node_start", "done"])


@pytest.mark.parametrize(
    "expect,override,should_fail,key",
    [
        ({"intent": "data_query"}, {}, False, "intent"),
        ({"intent": "alarm_query"}, {}, True, "intent"),
        ({"db_ids": ["polestar_cm_gp"]}, {}, False, "db_ids"),
        ({"db_ids": ["polestar_b0"]}, {}, True, "db_ids"),
        ({"has_file": False}, {}, False, "has_file"),
        ({"has_file": True}, {}, True, "has_file"),
        ({"http_status": 200}, {"http_status": 200}, False, "http_status"),
        ({"http_status": 200}, {"http_status": 201}, True, "http_status"),
        ({"response_must_contain": ["김포"]}, {}, False, "response_must_contain"),
        ({"response_must_contain": ["여의도"]}, {}, True, "response_must_contain"),
        ({"response_must_not_contain": ["여의도"]}, {}, False, "response_must_not_contain"),
        ({"response_must_not_contain": ["김포"]}, {}, True, "response_must_not_contain"),
        ({"sql_must_match": ["(?i)cmm_resource"]}, {}, False, "sql_must_match"),
        ({"sql_must_match": ["(?i)cmm_alarm"]}, {}, True, "sql_must_match"),
        ({"node_path": ["deep_agent"]}, {}, False, "node_path"),
        ({"node_path": ["semantic_router"]}, {}, True, "node_path"),
        ({"sse_events": ["done"]}, {}, False, "sse_events"),
        ({"sse_events": ["heartbeat"]}, {}, True, "sse_events"),
        ({"llm_calls": {"max": 10}}, {}, False, "llm_calls.max"),
        ({"llm_calls": {"max": 2}}, {}, True, "llm_calls.max"),
        ({"retries": {"max": 3}}, {}, False, "retries.max"),
        ({"retries": {"max": 0}}, {}, True, "retries.max"),
        ({"row_count": {"eq": 5}}, {}, False, "row_count.eq"),
        ({"row_count": {"eq": 4}}, {}, True, "row_count.eq"),
        ({"row_count": {"max": 10}}, {}, False, "row_count.max"),
        ({"row_count": {"max": 3}}, {}, True, "row_count.max"),
    ],
)
def test_단언_키가_통과와_불합격을_모두_구별한다(
    expect: dict, override: dict, should_fail: bool, key: str
) -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, expect)])
    verdict = _eval(scenario, Observation(**{**_PASS_OBS, **override}))
    if should_fail:
        assert verdict.func == "fail", f"{key}: 불합격이어야 한다"
        assert verdict.failures[0].key == key
    else:
        assert verdict.func == "pass", f"{key}: 합격이어야 한다 ({verdict.failures})"


def test_응답_부정_단언_위반도_silent_wrong_후보다() -> None:
    """프롬프트 원문 노출·미지원 기능을 있는 것처럼 말하는 것이 이 축에서 잡힌다."""
    scenario = _scenario(turns=[Turn(
        {"query": "CPU 90% 넘으면 알림 보내줘"},
        {"response_must_not_contain": ["등록했습니다"]},
    )])
    obs = Observation(status="completed", response="알림 규칙을 등록했습니다",
                      executed_sql="SELECT 1")
    verdict = _eval(scenario, obs)
    assert verdict.forbidden_mode == "silent_wrong"
    assert verdict.func == "fail"


def test_예산_단언은_측정치가_없으면_판정하지_않는다() -> None:
    """llm_calls 는 응답에 실리지 않는다 - None 을 0 으로 보면 전건 통과가 된다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"llm_calls": {"max": 0}})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.func == "pass"      # 판정 재료가 없으면 단언을 적용하지 않는다


def test_역질문_옵션_개수를_본다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {
        "clarification": {"kind": "zone_select", "options_len": 3}})])
    obs = Observation(status="clarification",
                      clarification={"kind": "zone_select", "options": [1, 2, 3]})
    assert _eval(scenario, obs).func == "pass"

    obs.clarification = {"kind": "zone_select", "options": [1, 2]}
    verdict = _eval(scenario, obs)
    assert verdict.failures[0].key == "clarification.options_len"


def test_역질문_종류가_다르면_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"clarification": {"kind": "zone_select"}})])
    obs = Observation(status="clarification", clarification={"kind": "scope_reexpand"})
    assert _eval(scenario, obs).failures[0].key == "clarification.kind"


def test_역질문이_기대됐는데_오지_않으면_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"clarification": {"kind": "zone_select"}})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.failures[0].key == "clarification"


def test_gold_sql_은_수동_판정으로_넘어간다() -> None:
    """EX 동등성은 eval_text2sql.execution_match 가 정본이다 - 여기서 흉내내지 않는다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"gold_sql": "SELECT 1"})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.func == "manual"
    assert any("eval_text2sql" in note for note in verdict.manual_notes)


def test_폼필_역질문도_clarify_로_잡힌다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {})])
    obs = Observation(status="completed", form_fill_clarification={"question": "어느 컬럼?"})
    assert _eval(scenario, obs).response_mode == "clarify"


# --- 성능 축 (§2-2) ------------------------------------------------------

def test_기능_합격_성능_불합격이_따로_나온다() -> None:
    """200초에 정답을 낸 시나리오는 기능 합격 + 성능 불합격이다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"status": "completed"})],
                         perf={"target_ms": 10000})
    obs = Observation(status="completed", executed_sql="SELECT 1", processing_time_ms=200000)
    verdict = _eval(scenario, obs)
    assert verdict.func == "pass"
    assert verdict.perf == "fail"


def test_성능_측정치가_없으면_판정하지_않는다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"status": "completed"})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.perf == "n/a"


def test_적용_턴이_아니면_성능을_판정하지_않는다() -> None:
    scenario = _scenario(
        turns=[Turn({"query": "q"}, {}), Turn({"query": "q2"}, {})],
        perf={"target_ms": 10, "applies_to_turn": 2},
    )
    obs = Observation(status="completed", executed_sql="SELECT 1", processing_time_ms=99999)
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, _group())
    assert verdict.perf == "n/a"
