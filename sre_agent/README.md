# sre_agent

HolmesGPT 기반 장애 진단 에이전트 — collectorinfra 최상위 **독립 패키지**(D-118).
본체(`src/`, LangGraph 스택)와 **별도 프로세스·별도 venv**(Python >=3.13, holmesgpt 스택)로
런타임 격리한다. 분리 절차 = 폴더 복사 + URL 설정 변경.

## 경계 불변식 (D-118)

- `sre_agent/`는 collectorinfra `src/`를 **절대 import하지 않는다**.
- collectorinfra `src/`도 `sre_agent`를 **절대 import하지 않는다**.
- 통신은 MCP 계약(`sre_investigate_alarm`/`sre_get_investigation`, `contract_version`)뿐.
- 이 불변식은 `tests/test_boundary.py`가 양방향으로 고정한다.

## 현재 범위 (Plan 66 Wave 2-B 골격 + 2-0 Gemini 테스트 경로)

- 패키지 골격: `settings` / `diagnosis` / `toolset_profiles` (SREAgent 이관).
- R16 Gemini 테스트 경로(D-120): `AgentSettings.investigation_llm_model`·`gemini_api_key`,
  스모크 하네스 `scripts/smoke_llm.py`.
- **범위 밖(후속 Wave)**: 엔트리 `run_service.py`(FastMCP 조사 서비스)·`interface/mcp_service.py`·
  `application/investigation_jobs.py`·dispatcher·후처리·폴스타 toolset·원격 프로파일은
  2-C/2-D/W-A 소관.

## 개발 명령 (반드시 `.venv/bin/python`으로 실행)

```bash
# 테스트 (실 LLM 없이)
.venv/bin/python -m pytest tests -q

# 계층 게이트
.venv/bin/python scripts/arch_check.py --ci

# Gemini 스모크 (GEMINI_API_KEY 미설정 시 보류·graceful)
.venv/bin/python scripts/smoke_llm.py
```

## 데이터 통제 (D-120 · 절대 제약)

Gemini API는 외부 SaaS다. 개발·테스트 전용이며 운영 투입 금지. 외부 송신 입력은
목업·로컬 픽스처 데이터만 허용하고, 실 폴스타 연결/데이터 송신 코드 경로를 두지 않는다.
