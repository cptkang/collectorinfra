"""멀티턴 체인 통합 검증: 턴1에 명시된 서버명이 이후 지시어 턴까지 전달되는가.

시나리오(사용자 보고):
  턴1 "김포 ### 서버 사양"      — 명시적 서버명 ###
  턴2 "해당 서버 프로세스"        — process_query (결과=프로세스 행)
  턴3 "정상이라고 판단되는가?"    — general_inference (조회 없음, query_results=[])
  턴4 "지난 한 달 해당 서버 알람" — alarm_query

세 수정이 맞물려 ### 가 턴4 알람 조회의 filter_conditions까지 도달해야 한다:
  (1) sticky 승계         — 조회 없는 판단 턴을 건너뛰어도 previous_entities 유지
  (2) 프로세스 행 제외    — 프로세스명(mysql)/pid가 서버로 오수집되지 않음
  (3) hostname 주입       — 지시어 후속의 filter_conditions에 직전 hostname 결정적 주입

주의: 본 테스트는 context_resolver(결정적)와 _inject_demonstrative_hostname(결정적)만
검증한다. query_generator의 실제 SQL 번역(LLM)은 범위 밖.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.context_resolver import context_resolver
from src.orchestration.subagents import _inject_demonstrative_hostname
from src.state import create_initial_state

H = HumanMessage
A = AIMessage


async def _resolve(messages, query_results, filters, prior_ctx):
    """한 턴의 context_resolver를 실행하여 conversation_context를 반환한다."""
    state = create_initial_state(user_query=messages[-1].content)
    state["messages"] = messages
    state["query_results"] = query_results
    state["parsed_requirements"] = {"filter_conditions": filters}
    state["active_db_id"] = "polestar_cm_gp"
    state["target_databases"] = [{"db_id": "polestar_cm_gp"}]
    state["conversation_context"] = prior_ctx
    out = await context_resolver(state)
    return out["conversation_context"]


async def test_named_server_propagates_to_alarm_filter():
    """턴1 명시 서버명(###)이 턴4 알람 조회 filter_conditions까지 도달한다."""
    # 턴2: context_resolver가 턴1 상태(명시 서버 filter + 결과)에서 엔티티 추출
    ctx2 = await _resolve(
        [H("김포 ### 서버 사양"), A("사양..."), H("해당 서버 프로세스 리스트")],
        query_results=[{"hostname": "###", "cpu_cores": 8, "mem_gb": 64}],
        filters=[{"field": "hostname", "op": "=", "value": "###"}],
        prior_ctx=None,
    )
    assert any(str(e["value"]) == "###" for e in ctx2["previous_entities"])

    # 턴3: 직전(턴2)은 프로세스 조회 — 결과=프로세스 행, filter=[](지시어)
    ctx3 = await _resolve(
        [H("김포 ### 서버 사양"), A("사양"), H("해당 서버 프로세스"), A("프로세스"),
         H("정상이라고 판단되는가?")],
        query_results=[{"name": "mysql", "pid": 30176, "cpu_pct": 12.0, "args": "mysqld"}],
        filters=[],
        prior_ctx=ctx2,
    )
    vals3 = {str(e["value"]) for e in ctx3["previous_entities"]}
    assert "###" in vals3, "sticky 승계로 서버명이 유지되어야 함"
    assert "mysql" not in vals3 and "30176" not in vals3, "프로세스명/pid 오염 없어야 함"

    # 턴4: 직전(턴3)은 판단 — 조회 없음(query_results=[]), filter=[]
    ctx4 = await _resolve(
        [H("김포 ### 서버 사양"), A("사양"), H("해당 서버 프로세스"), A("프로세스"),
         H("정상인가?"), A("판단"), H("지난 한 달 동안 해당 서버의 알람을 분석해줘")],
        query_results=[],
        filters=[],
        prior_ctx=ctx3,
    )
    assert any(str(e["value"]) == "###" for e in ctx4["previous_entities"])

    # 알람 subagent: 지시어 후속 질의에 직전 hostname이 filter로 주입된다
    isolated = {
        "parsed_requirements": {
            "original_query": "지난 한 달 동안 해당 서버의 알람을 분석해줘",
            "filter_conditions": [],
        },
        "conversation_context": ctx4,
    }
    parsed = _inject_demonstrative_hostname(isolated)
    assert {"field": "hostname", "op": "=", "value": "###"} in parsed["filter_conditions"], (
        "지시어 '해당 서버'가 턴1의 명시 서버명(###)으로 해소되어 알람 필터에 도달해야 함"
    )


async def test_status_question_also_resolves_server():
    """'해당 서버는 특이사항이 없는가?'(→alarm)도 동일하게 ### 로 필터된다."""
    ctx2 = await _resolve(
        [H("김포 ### 서버 사양"), A("사양"), H("해당 서버 프로세스")],
        query_results=[{"hostname": "###", "cpu_cores": 8}],
        filters=[{"field": "hostname", "op": "=", "value": "###"}],
        prior_ctx=None,
    )
    ctx3 = await _resolve(
        [H("김포 ### 서버 사양"), A("사양"), H("해당 서버 프로세스"), A("프로세스"),
         H("해당 서버는 특이사항이 없는가?")],
        query_results=[{"name": "node_exporter", "pid": 50482, "cpu_pct": 1.0}],
        filters=[],
        prior_ctx=ctx2,
    )
    isolated = {
        "parsed_requirements": {
            "original_query": "해당 서버는 특이사항이 없는가?",
            "filter_conditions": [],
        },
        "conversation_context": ctx3,
    }
    parsed = _inject_demonstrative_hostname(isolated)
    assert {"field": "hostname", "op": "=", "value": "###"} in parsed["filter_conditions"]
