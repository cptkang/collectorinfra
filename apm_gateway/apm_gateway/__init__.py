"""APM 게이트웨이 — 제니퍼 Open API 읽기 전용 MCP 서버 · 이벤트 폴러 (plans/87 · D-274 · D-195).

독립 최상위 패키지(D-139)다. `src`·`noise_gate`·`sre_agent`·`mcp_server`와 양방향 import 0이고 MCP ·
Redis Stream(`alarm:raw`) 계약으로만 소비자와 통신한다(D-274 ③).
"""
