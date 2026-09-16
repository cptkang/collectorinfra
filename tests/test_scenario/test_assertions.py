"""단언 평가기 수용 기준 V15·V17 + 대응 등급 (plans/94 §3.8 · §10)."""

from __future__ import annotations

import pytest

from scripts.scenario.assertions import (
    INVALID_VERDICT,
    Observation,
    classify_mode,
    evaluate_turn,
)
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


def test_예산_단언은_측정치가_없으면_manual_이다() -> None:
    """llm_calls 는 응답에 실리지 않는다 - 없는 측정치를 0 으로 보면 전건 통과가 된다.

    그렇다고 `pass` 로 세도 안 된다. 시나리오는 예산을 선언했는데 리포트에 `pass` 가
    찍히면 "예산을 지켰다"로 읽힌다 — 실제로는 한 번도 확인하지 않았다. 모듈 원칙
    그대로 **판정할 수 없는 것은 판정하지 않고 `manual` 로 남긴다**(2026-09-14 정정).
    """
    scenario = _scenario(turns=[Turn({"query": "q"}, {"llm_calls": {"max": 0}})])
    verdict = _eval(scenario, Observation(status="completed", executed_sql="SELECT 1"))
    assert verdict.func == "manual"
    assert any("확인하지 못했다" in note for note in verdict.manual_notes)


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


# --- 모의 실행은 canned 응답을 판정하지 않는다 (2026-09-14) ----------------

def test_모의_실행은_배관_외의_단언을_적용하지_않는다() -> None:
    """canned 응답은 SQL 뿐 아니라 상태·역질문·산출물까지 전부 정한다.

    2026-09-14 모의 실행에서 F-03·F-04 가 `status: clarification` 에서, H-01 이
    `has_file: true` 에서 불합격이었다 — 전부 모의 서버를 판정한 결과였다.
    """
    scenario = _scenario(turns=[Turn({"query": "q"}, {
        "status": "clarification",
        "has_file": True,
        "sql_must_match": ["(?i)cmm_metric_stat_m"],
        "row_count": {"eq": 5},
    })])
    obs = Observation(status="completed", executed_sql="SELECT 1", row_count=999)

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, _group(), mock=True)

    assert verdict.func == "manual"
    assert not verdict.failures, "모의가 정하는 값으로 불합격을 만들면 안 된다"
    assert any("모의 실행" in note for note in verdict.manual_notes)


def test_모의_실행도_배관은_판정한다() -> None:
    """모의가 실제로 증명하는 것 — SSE 가 흘렀고 노드를 밟았고 200 이 왔다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {
        "sse_events": ["node_start", "done"],
        "http_status": 200,
    })])
    broken = Observation(status="completed", http_status=500)
    broken.sse_events = []

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], broken, _group(), mock=True)

    assert verdict.func == "fail"
    assert {f.key for f in verdict.failures} >= {"http_status"}


def test_모의_블록이_있으면_내용_단언을_적용한다() -> None:
    """작성자가 응답을 직접 정했다면 그 단언은 배관(SQL·행수 전달)을 실제로 검증한다."""
    scenario = _scenario(
        turns=[Turn({"query": "q"}, {"sql_must_match": ["(?i)cmm_resource"]})],
        mock={"turns": [{"executed_sql": "SELECT 1 FROM cmm_resource"}]},
    )
    ok = Observation(status="completed", executed_sql="SELECT 1 FROM cmm_resource")
    bad = Observation(status="completed", executed_sql="SELECT 1")

    assert evaluate_turn(scenario, 1, scenario.turns[0], ok, _group(), mock=True).func == "pass"
    assert evaluate_turn(scenario, 1, scenario.turns[0], bad, _group(), mock=True).func == "fail"


def test_실_모드는_모의와_무관하게_내용을_판정한다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {
        "sql_must_match": ["(?i)cmm_metric_stat_m"],
    })])
    obs = Observation(status="completed", executed_sql="SELECT 1")

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, _group(), mock=False)

    assert verdict.func == "fail"


def test_관측되지_않는_intent_로_불합격을_만들지_않는다() -> None:
    """done 페이로드에 intent 가 없다 - 그대로 대조하면 전건 거짓 불합격이다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"intent": "data_query"})])
    obs = Observation(status="completed", executed_sql="SELECT 1")

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, _group())

    assert verdict.func == "manual"
    assert not verdict.failures
    assert any("db_ids" in note for note in verdict.manual_notes)


def test_intent_가_관측되면_정상_대조한다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"intent": "data_query"})])
    obs = Observation(status="completed", intent="alarm_query", executed_sql="SELECT 1")

    assert evaluate_turn(scenario, 1, scenario.turns[0], obs, _group()).func == "fail"


# --- 기대한 오류는 오류 판정이 아니다 (2026-09-15 · R2-08 회귀) ----------------

def test_기대한_4xx_는_error_로_떨어지지_않는다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"http_status": 400})])
    obs = Observation(status="error", http_status=400, error="http 400: 거부")

    assert _eval(scenario, obs).func == "pass"


def test_기대와_다른_4xx_는_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"http_status": 200})])
    obs = Observation(status="error", http_status=400, error="http 400: 거부")

    assert _eval(scenario, obs).func == "fail"


def test_기대값이_없는_오류는_여전히_error_다() -> None:
    """401 이 manual 로 새던 회귀를 다시 열지 않는다.

    **T-c(D-218) 로 판정값이 `error` → `invalid` 로 바뀌었다.** 바뀐 것은 이름이 아니라
    분모다 - 러너 자신의 인증 실패는 기능 판정에 넣지 않는다. 지켜야 하는 원래 계약
    (`manual` 로 새지 않는다)은 그대로다.
    """
    scenario = _scenario(turns=[Turn({"query": "q"}, {})])
    obs = Observation(status="error", http_status=401, error="http 401 - 토큰 없음")
    verdict = _eval(scenario, obs)

    assert verdict.func == INVALID_VERDICT
    assert verdict.func != "manual"
    assert "401" in (verdict.invalid_reason or "")


def test_인증과_무관한_오류는_여전히_error_다() -> None:
    """T-c 가 **모든** 오류를 무효로 바꾸지는 않는다 - 500 은 제품 실패다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {})])
    obs = Observation(status="error", http_status=500, error="http 500: 내부 오류")

    assert _eval(scenario, obs).func == "error"


def test_재시도를_셀_수_없으면_예산_단언은_manual_이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"retries": {"max": 1}})])
    obs = Observation(status="completed", executed_sql="SELECT 1")   # retries=None

    verdict = _eval(scenario, obs)

    assert verdict.func == "manual"
    assert any("retries.max" in note for note in verdict.manual_notes)


# --- SQL 은 하나씩 본다 · 못 본 것과 없는 것을 구별한다 (2026-09-15) ---------

def _sql_obs(*sqls: str, status: str = "completed") -> Observation:
    obs = Observation(status=status)
    obs.executed_sqls = list(sqls)
    return obs


def test_must_match_는_SQL_하나라도_맞으면_통과다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"sql_must_match": ["(?i)cmm_alarm"]})])
    obs = _sql_obs("SELECT 1 FROM cmm_resource", "SELECT 1 FROM cmm_alarm_active")

    assert _eval(scenario, obs).func == "pass"


def test_SQL_경계를_넘는_거짓_일치를_만들지_않는다() -> None:
    """앞 SQL 의 ORDER BY 와 뒤 SQL 의 DESC 가 이어붙으면 맞는 것처럼 보인다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {
        "sql_must_match": ["(?i)order\\s+by[\\s\\S]*\\bdesc\\b"]})])
    obs = _sql_obs("SELECT a FROM x ORDER BY a", "SELECT b FROM y WHERE c = 'desc'")
    obs.executed_sqls[1] = "SELECT b FROM y ORDER BY b ASC"
    obs.executed_sqls.append("SELECT 'DESC' AS label FROM z")

    assert _eval(scenario, obs).func == "fail"


def test_must_not_match_는_SQL_하나라도_맞으면_위반이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"sql_must_not_match": ["(?i)여의도"]})])
    obs = _sql_obs("SELECT 1 FROM cmm_resource", "SELECT 1 FROM cmm_resource WHERE loc = '여의도'")

    verdict = _eval(scenario, obs)

    assert verdict.func == "fail"
    assert verdict.failures[0].actual.endswith("'여의도'")




def test_완료됐는데_SQL_이_없다고_관측되면_불합격이다() -> None:
    scenario = _scenario(turns=[Turn({"query": "q"}, {"sql_must_match": ["(?i)select"]})])

    assert _eval(scenario, _sql_obs()).func == "fail"


# --- Y-9 · 못 본 SQL 과 안 만든 SQL 을 가른다 (plans/94 §16 · 2026-09-16) -----

def test_역질문으로_끝나_SQL_이_없으면_보류다() -> None:
    """존 역질문은 아직 조회 단계가 아니다 - 불합격으로 세면 'SQL 미생성'으로 읽힌다."""
    scenario = _scenario(turns=[Turn({"query": "q"}, {"sql_must_match": ["(?i)select"]})])

    verdict = _eval(scenario, _sql_obs(status="clarification"))

    assert verdict.func == "manual"
    assert any("역질문" in note for note in verdict.manual_notes)


def test_모의_실행은_SQL_수집기가_없어_보류다() -> None:
    """감사 로그 tail 은 실 모드에만 붙는다 - 수집기 부재를 미생성으로 세지 않는다."""
    scenario = _scenario(
        turns=[Turn({"query": "q"}, {"sql_must_match": ["(?i)select"]})],
        mock={"turns": [{"response": "ok"}]},
    )

    verdict = evaluate_turn(scenario, 1, scenario.turns[0],
                            Observation(status="completed"), _group(), mock=True)

    assert verdict.func == "manual"
    assert any("수집기" in note for note in verdict.manual_notes)
