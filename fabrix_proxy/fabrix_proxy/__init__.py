"""FabriX KBGenAI → OpenAI Chat Completions(+tools) 프록시 (plans/148 1단계 PoC).

독립 프로세스다 — `src`·`noise_gate`·`sre_agent`·`mcp_server`·`apm_gateway`와 양방향 import 0
(`tests/test_boundary.py`). 소비자와는 OpenAI HTTP 계약만 공유한다.
"""
