"""src.alarm: 알람 분석·발송 서브패키지.

Redis Stream('alarm:raw')에서 알람 이벤트를 소비하여
LLM 분석 후 worKB 등 채널로 발송한다.

서브패키지 구조:
    domain/       - AlarmEvent, AlarmAnalysisResult 데이터 모델
    infrastructure/ - Redis Stream 소비 헬퍼
    application/  - AlarmWorker, LangGraph 노드
    orchestration/ - 2-노드 알람 분석 그래프
    prompts/      - LLM 프롬프트 상수
"""
