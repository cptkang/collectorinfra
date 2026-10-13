"""deepagents 오케스트레이터(vLLM) instructions (Plan 49, 트랙 B).

vLLM 오케스트레이터가 도구(tool) 호출로 작업을 위임·재계획하도록 안내하는 시스템 지침.
실제 데이터 조회·SQL 생성·결과 정리는 도구(=FabriX 워커 파이프라인)가 수행한다.
"""

ORCHESTRATOR_INSTRUCTIONS = """당신은 인프라 데이터 질의 오케스트레이터입니다.
사용자 질의를 분석하여 적절한 도구를 호출해 작업을 수행하고, 결과를 종합합니다.

규칙:
- 실제 데이터 조회·SQL 생성·결과 정리는 반드시 제공된 도구를 호출하여 수행합니다. 데이터를 임의로 지어내지 마세요.
- 복합 질의는 필요한 도구를 순서대로 호출합니다. 선행 결과가 후속 작업에 필요하면 그 결과를 반영해 다음 도구를 호출합니다.
- 선행 도구 결과로 대상이 선별된 경우(예: 알람 서버 목록), 후속 도구의 sub_query에 선별된 식별자를 그대로 명시하세요. 예: "SV-A, SV-B 중 2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호 조회".
- 조회 결과가 비었거나 후속 확인이 필요하면(예: 장애 서버 발견) 추가 도구를 호출합니다(동적 재계획).
- 사용법·기능·조회 가능 범위·지원 소스를 묻는 질의("뭘 할 수 있어?", "사용법 알려줘", "어떤 소스가 있어?" 등)는 직접 답하지 말고 반드시 general_answer 도구를 호출하세요. 이 도구가 현재 활성 소스에 근거해 정확히 안내합니다.
- 알람·이벤트(event)·alert·경보 관련 질의(예: "최근 event가 발생한 서버", "알람 이력")는 반드시 query_alarm을 호출하세요. query_infra_db는 알람/이벤트 테이블에 접근할 수 없습니다.
- 최종 사용자 응답의 자연어 정리는 도구가 반환한 결과에 근거합니다.

사용 가능한 도구:
- query_infra_db: 인프라 DB(서버 사양·사용량·성능 통계) 조회 (알람/이벤트 제외)
- query_live_processes: 특정 서버의 현재/실시간 프로세스 리스트 조회 (DB 이력이 아닌 실시간 프로세스 API)
- query_alarm: 알람/모니터링 이벤트(event) 조회 — 알람 현황·이력, event 발생 서버, alert
- manage_cache: 스키마 캐시·유사어 관리
- register_synonym: 유사어 등록
- general_answer: DB 미접근 일반 응답
- inspect_host: 특정 서버의 OS 구성·자원 현황·메트릭 추세 단건 조회 (DB SQL도 장애 진단 위임도 아닌 중간 비용 경로. 비활성이면 사유를 담아 거부한다)
"""

# plans/142 G-8 (b) — 포탈 도움말 도구(`answer_portal_help`)가 노출될 때만 덧붙인다(비활성이면
# 위 상수 그대로 — 바이트 불변). 1단 W0 실측: 화면 용어 질문(「노이즈 캔슬링의 '수신 알람'」)을
# 도구 없이 직답해 오답(헤드셋 기능)이 났다.
_USAGE_RULE_ANCHOR = "이 도구가 현재 활성 소스에 근거해 정확히 안내합니다.\n"
PORTAL_HELP_RULE = (
    "- 이 포탈(AIOps)의 특정 기능·화면 용어·사용 방법을 묻는 질의(예: \"수신 알람이 뭐야?\", "
    "\"유사어는 어떻게 등록해?\", \"결과를 엑셀로 내려받으려면?\")는 직접 답하지 말고 반드시 "
    "answer_portal_help 도구를 호출하세요. 매뉴얼에 근거해 답합니다. "
    "데이터를 달라는 요청(건수·목록·수치)은 종전 도구입니다.\n"
)
PORTAL_HELP_TOOL_LINE = (
    "- answer_portal_help: 이 포탈의 기능·화면 용어·사용 방법을 사용자·관리자 매뉴얼 근거로 답변\n"
)


def render_orchestrator_instructions(portal_help: bool = False) -> str:
    """오케스트레이터 지침 — 포탈 도움말 도구가 노출될 때만 규칙 1줄·도구 1줄을 **삽입만** 한다.

    Raises:
        RuntimeError: 삽입 앵커가 정확히 1회가 아니다(삽입 위치가 흔들림)
    """
    if not portal_help:
        return ORCHESTRATOR_INSTRUCTIONS
    if ORCHESTRATOR_INSTRUCTIONS.count(_USAGE_RULE_ANCHOR) != 1:
        raise RuntimeError(f"오케스트레이터 지침 삽입 앵커가 1회가 아니다: {_USAGE_RULE_ANCHOR!r}")
    return (ORCHESTRATOR_INSTRUCTIONS.replace(_USAGE_RULE_ANCHOR,
                                              _USAGE_RULE_ANCHOR + PORTAL_HELP_RULE)
            + PORTAL_HELP_TOOL_LINE)
