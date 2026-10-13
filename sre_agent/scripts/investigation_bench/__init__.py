"""장애 조사 품질 벤치마크 (plans/154 · D-324).

정답이 있는 합성 장애 세계(세계 YAML)를 가짜 MCP 서버(`world_server`)로 내주고 HolmesGPT 조사를
반복 측정한다. 경계(D-139): `mcp_server`·`apm_gateway`·`sre_agent` 패키지를 import하지 않는다 —
도구 표면은 MCP 프로토콜로 받은 스냅샷(`schema`)만 쓴다.
"""
