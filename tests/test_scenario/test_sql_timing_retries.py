"""SQL 목록 판정 · 대응 등급 오분류 · 노드 누적 지연 · 재시도 실측 (D-217).

run 20260914-154940 분석에서 나온 측정 결함을 고정한다. 네트워크 없이 합성 이벤트로 검증한다.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import pytest

from scripts.scenario.assertions import Observation, classify_mode, evaluate_turn, observed_sqls
from scripts.scenario.catalog import Group, Scenario, Turn
from scripts.scenario.client import ClientConfig, ScenarioClient, _apply_done

GROUP = Group("T", "t", 60000)


def _judge(expect: dict, obs: Observation):
    scenario = Scenario(id="T-01", group="T", plans=[94], title="t", turns=[Turn({"query": "q"}, expect)])
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, GROUP)


class FakeResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def iter_lines(self) -> Iterator[str]:
        for event in self._events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}"


@pytest.fixture()
def client() -> Iterator[ScenarioClient]:
    instance = ScenarioClient(ClientConfig(port=1))
    yield instance
    instance.close()


def _consume(client: ScenarioClient, events: list[dict[str, Any]]) -> Observation:
    obs = Observation()
    client._consume_sse(FakeResponse(events + [{"type": "done", "response": "끝"}]), obs,
                        started=time.perf_counter())
    return obs


# --- SQL 목록 판정 --------------------------------------------------------

def test_감사_로그_수집분이_있으면_그것을_SQL로_본다() -> None:
    assert observed_sqls(Observation(executed_sql="SELECT done")) == ["SELECT done"]
    assert observed_sqls(Observation(executed_sql="SELECT done", executed_sqls=["A", "B"])) == ["A", "B"]
    assert observed_sqls(Observation()) == []


def test_must_match_는_하나라도_must_not_match_는_전부를_본다() -> None:
    obs = Observation(status="completed", row_count=3,
                      executed_sqls=["DELETE FROM x", "SELECT * FROM t LIMIT 10"])
    verdict = _judge({"sql_must_match": ["(?i)limit 10"], "sql_must_not_match": ["(?i)\\bdelete\\b"]}, obs)
    assert [(f.key, f.actual) for f in verdict.failures] == [("sql_must_not_match", "DELETE FROM x")]


def test_SQL을_이어붙여_검색하지_않는다() -> None:
    """두 SQL 경계를 넘는 거짓 일치 - 'FROM a' 끝과 'SELECT' 시작이 붙어 보이면 안 된다."""
    obs = Observation(status="completed", row_count=1, executed_sqls=["SELECT 1 FROM a", "SELECT 2 FROM b"])
    verdict = _judge({"sql_must_match": ["FROM a\\s*SELECT"]}, obs)
    assert [f.key for f in verdict.failures] == ["sql_must_match"]


def test_행은_있는데_SQL을_못_보면_부정_SQL_단언을_통과로_세지_않는다() -> None:
    verdict = _judge({"sql_must_not_match": ["(?i)delete"]}, Observation(status="completed", row_count=5))
    assert verdict.func == "manual"
    assert any("실행 SQL 을 관측하지 못했다" in note for note in verdict.manual_notes)


def test_금지_칼럼은_모든_SQL에서_찾는다() -> None:
    obs = Observation(status="completed", row_count=1, executed_sqls=["SELECT a FROM t", "SELECT cpu_usage FROM m"])
    verdict = _judge({"column_must_not_map": ["cpu_usage"]}, obs)
    assert [f.key for f in verdict.failures] == ["column_must_not_map"]


# --- 대응 등급 오분류 ----------------------------------------------------

DIAGNOSTIC = "경고 알람 45건입니다.\n\n**[일부 존 조회 실패]**\n- polestar_b0: 알람 조회에 허용되지 않은 테이블"


def test_데이터를_돌려준_응답의_진단_절은_거부로_보지_않는다() -> None:
    """SYN-F-03 - 45행 표에 붙은 '허용되지 않은 테이블' 진단이 refuse 로 분류됐다."""
    assert classify_mode(Observation(status="completed", response=DIAGNOSTIC, row_count=45))[0] == "answer"
    assert classify_mode(Observation(status="completed", response=DIAGNOSTIC,
                                     executed_sqls=["SELECT 1"]))[0] == "answer"


def test_데이터가_없는_거부_문구는_여전히_거부다() -> None:
    obs = Observation(status="completed", response="요청하신 삭제는 수행할 수 없습니다.", row_count=0)
    assert classify_mode(obs)[0] == "refuse"


# --- 재시도 예산 ----------------------------------------------------------

def test_재시도가_하한이면_예산_안이어도_확정하지_않는다() -> None:
    verdict = _judge({"retries": {"max": 3}},
                     Observation(status="completed", retries=1, retries_partial=True))
    assert verdict.func == "manual" and any("하한" in note for note in verdict.manual_notes)


def test_하한이어도_예산을_넘으면_불합격이다() -> None:
    verdict = _judge({"retries": {"max": 3}},
                     Observation(status="completed", retries=4, retries_partial=True))
    assert [f.key for f in verdict.failures] == ["retries.max"]


# --- 노드 누적 지연 -------------------------------------------------------

def test_재계획_루프의_노드_지연은_회차를_합치고_전체_소요를_넘지_않는다(client: ScenarioClient) -> None:
    """서버는 node_start 를 노드마다 한 번만 보낸다 - 재진입 회차는 직전 완료에서 시작한다."""
    obs = _consume(client, [
        {"type": "node_start", "node": "agent_orchestrator", "timestamp_ms": 0.0},
        {"type": "node_complete", "node": "agent_orchestrator", "timestamp_ms": 100.0},
        {"type": "node_start", "node": "replanner", "timestamp_ms": 100.0},
        {"type": "node_complete", "node": "replanner", "timestamp_ms": 150.0},
        {"type": "node_complete", "node": "agent_orchestrator", "timestamp_ms": 400.0},
        {"type": "node_complete", "node": "replanner", "timestamp_ms": 420.0},
        {"type": "node_start", "node": "result_aggregator", "timestamp_ms": 420.0},
        {"type": "node_complete", "node": "result_aggregator", "timestamp_ms": 430.0},
    ])
    assert obs.node_elapsed_ms == {"agent_orchestrator": 350.0, "replanner": 70.0, "result_aggregator": 10.0}
    assert obs.node_calls == {"agent_orchestrator": 2, "replanner": 2, "result_aggregator": 1}
    assert sum(obs.node_elapsed_ms.values()) <= 430.0


# --- 재시도 실측 ---------------------------------------------------------

def _step(name: str, phase: str = "start", label: str = "") -> dict:
    return {"type": "progress", "kind": "step", "name": name, "phase": phase, "label": label}


def test_서브에이전트_경로는_재생성_진행_이벤트로_재시도를_센다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        _step("pipeline.schema"), _step("pipeline.generate", label="SQL 생성"),
        _step("pipeline.generate", label="SQL 재생성 1회차"), _step("pipeline.generate", label="SQL 재생성 2회차"),
    ])
    assert (obs.retries, obs.retries_partial) == (2, False)


def test_멀티_DB_경로는_재시도를_하한으로_표시한다(client: ScenarioClient) -> None:
    obs = _consume(client, [_step("pipeline.multi_db", label="멀티 DB 조회 3곳")])
    assert (obs.retries, obs.retries_partial) == (0, True)


def test_단일_그래프_경로는_회귀_노드_완료_횟수로_센다(client: ScenarioClient) -> None:
    """node_start 는 한 번만 오므로 시작을 세면 늘 0 이었다."""
    obs = _consume(client, [
        {"type": "node_start", "node": "query_generator", "timestamp_ms": 0.0},
        {"type": "node_complete", "node": "query_generator", "timestamp_ms": 10.0},
        {"type": "node_complete", "node": "query_generator", "timestamp_ms": 20.0},
        {"type": "node_complete", "node": "query_generator", "timestamp_ms": 30.0},
    ])
    assert obs.retries == 2


def test_볼_수_있는_신호가_없으면_재시도는_측정_불가다(client: ScenarioClient) -> None:
    assert _consume(client, []).retries is None


def test_저장_값_패널이_관측치로_옮겨진다() -> None:
    obs = Observation()
    _apply_done(obs, {"response": "저장 값", "form_memory_panel": {"signature": "s", "entries": []}})
    assert obs.form_memory_panel == {"signature": "s", "entries": []}
