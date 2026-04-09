# 아키텍처 문서 인덱스

> 최종 갱신: 2026-04-02

아키텍처 문서는 3개 파일로 분리되어 있다.

| # | 문서 | 내용 |
|---|------|------|
| 05 | [시스템 아키텍처](05_system_architecture.md) | 전체 시스템 구성도, 인프라 구성 요소, LLM/DB 접근 경로, 멀티 DB 도메인, 스키마 캐시, 설정 계층, 기술 스택 |
| 06 | [소프트웨어 아키텍처](06_software_architecture.md) | Clean Architecture 계층, 디렉토리 구조, AgentState 데이터 모델, API 엔드포인트, 보안 아키텍처, LangGraph 그래프 구조 |
| 07 | [처리 프로세스](07_processing_flow.md) | LangGraph 파이프라인 흐름도, 노드별 상세 처리, 재시도/에러 복구, 멀티 DB 흐름, SSE 스트리밍, End-to-End 시나리오 (일반 질의, Excel 업로드, 멀티턴+HITL, 캐시 관리), 문서 처리 파이프라인 |
