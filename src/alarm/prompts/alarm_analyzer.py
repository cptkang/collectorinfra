"""알람 분석 LLM 프롬프트 상수.

ALARM_ANALYZER_SYSTEM_PROMPT: 시스템 역할 및 JSON 응답 스키마 지시
ALARM_ANALYZER_USER_TEMPLATE: 알람 정보 입력 템플릿

템플릿 필드 (폴스타 JSON 키 기반):
    db_id, server_name, hostname, ip_address, resource_ancestry,
    resource_type, resource_name, alarm_name, alarm_id,
    severity, severity_label, alarm_status, alarm_time,
    conditions, condition_log
"""

ALARM_ANALYZER_SYSTEM_PROMPT = """당신은 인프라 모니터링 알람을 분석하는 전문가입니다.
주어진 알람 정보를 바탕으로 다음을 JSON으로 응답하세요:

{
    "severity_label": "심각" | "경고" | "주의" | "해소",
    "summary": "알람 요약 (1~2문장, 한국어, 장비명·알람명·심각도 포함)",
    "probable_cause": "추정 원인 (conditions와 conditionLog 근거, 1~2문장)",
    "recommended_action": "권고 조치 (구체적, 1~3문장)"
}

규칙:
- 심각도: 3=심각, 2=경고, 1=주의, 0=해소
- conditions(발생/해소 임계 정의)와 conditionLog(실제 측정값)를 원인 분석의 핵심 근거로 사용
- 해소 알람(alarmStatus=해소)인 경우 severity_label은 "해소"로 출력
- JSON 이외의 텍스트를 절대 출력하지 말 것
"""

ALARM_ANALYZER_USER_TEMPLATE = """알람 정보:
- DB: {db_id} / 서버: {server_name} ({hostname}, {ip_address})
- 자원 경로: {resource_ancestry}
- 자원 종류: {resource_type}
- 자원 이름: {resource_name}
- 알람명: {alarm_name}
- 알람 ID: {alarm_id}
- 심각도: {severity} ({severity_label})
- 알람 상태: {alarm_status}
- 알람 일시: {alarm_time}
- 임계 조건: {conditions}
- 조건 로그: {condition_log}
"""
