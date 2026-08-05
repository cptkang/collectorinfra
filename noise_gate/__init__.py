"""noise_gate: 알람 노이즈 캔슬링·분석·발송 패키지.

Redis Stream('alarm:raw')에서 알람 이벤트를 소비해 노이즈 게이트(4-티어 라우팅·dedup·
상관·억제)로 판정하고, LLM 분석을 거쳐 worKB 등 채널로 발송한다.

종전 `src/alarm/`에서 최상위 패키지로 분리했다(Plan 66 · D-139). 상세는 README.md 참조 —
특히 **본체와 같은 프로세스·같은 venv에서 돈다**는 점이 sre_agent/mcp_server와 다르다.

서브패키지 구조:
    domain/         - 순수 판정 로직(정책·상관·이상탐지·지문·시그니처)·데이터 모델
    infrastructure/ - Redis·폴스타 API·decision_store·SSE·MCP 클라이언트
    application/    - AlarmWorker, LangGraph 노드, 분류기
    orchestration/  - 알람 분석 그래프 조립
    prompts/        - LLM 프롬프트 상수
"""
