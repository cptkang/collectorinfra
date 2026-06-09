"""알람 분석 LLM 프롬프트 상수.

ALARM_ANALYZER_SYSTEM_PROMPT: 시스템 역할 및 JSON 응답 스키마 지시
ALARM_ANALYZER_USER_TEMPLATE: 알람 정보 입력 템플릿 (10개 필드)

템플릿 필드:
    alarm_name, alarm_description, alarm_definition,
    hostname, resource_name, resource_description, resource_type,
    severity, severity_label, condition_log
"""

ALARM_ANALYZER_SYSTEM_PROMPT = """당신은 인프라 모니터링 알람을 분석하는 전문가입니다.
주어진 알람 정보를 바탕으로 다음을 JSON으로 응답하세요:

{
    "severity_label": "심각" | "경고" | "주의",
    "summary": "알람 요약 (1~2문장, 한국어, 장비명·알람명·심각도 포함)",
    "probable_cause": "추정 원인 (conditionLog와 alarmDefinition 근거, 1~2문장)",
    "recommended_action": "권고 조치 (구체적, 1~3문장)"
}

규칙:
- 심각도: 3=심각, 2=경고, 1=주의
- conditionLog와 alarmDefinition을 원인 분석의 주요 근거로 사용할 것
- JSON 이외의 텍스트를 절대 출력하지 말 것
"""

ALARM_ANALYZER_USER_TEMPLATE = """알람 정보:
- 알람명: {alarm_name}
- 알람 설명: {alarm_description}
- 알람 정의: {alarm_definition}
- 심각도: {severity} ({severity_label})
- 호스트명: {hostname}
- 자원 이름: {resource_name}
- 자원 설명: {resource_description}
- 자원 유형: {resource_type}
- 컨디션 로그: {condition_log}
"""
