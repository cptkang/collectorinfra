"""LLM(워커)·오케스트레이터(제어 평면) 설정.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class LLMConfig(BaseSettings):
    """LLM 관련 설정."""

    provider: Literal["ollama", "fabrix", "gemini", "mlx"] = "ollama"
    model: str = "llama3.1:8b"

    # Ollama 설정
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_timeout: int = 180

    # MLX 설정 (plans/100 — Apple Silicon 로컬 테스트 전용)
    # 앱 밖 `mlx_lm.server`에 OpenAI 호환으로 붙는다.
    # provider가 mlx가 아니면 읽히지 않는다. ORCHESTRATOR_PROVIDER=mlx도 mlx_max_tokens를 공유한다.
    mlx_base_url: str = "http://127.0.0.1:8080/v1"
    # 빈 값이면 "default_model"을 보내 서버 --model로 매핑된다(이름 불일치 재적재 회피)
    mlx_model: str = ""
    # 서버 기본값 512토큰에서 finish_reason=length로 절단된다(plans/100 J-1 ①) — 반드시 보낸다
    mlx_max_tokens: int = 4096
    # 콜드 prefill 320~370 tok/s 실측 기준 최악(프롬프트 약 10만 토큰 + 생성 4096)
    # 약 400초(plans/100 §3.3)
    mlx_timeout: int = 600
    # 미전송이면 Qwen3.5가 생성 예산 전부를 reasoning에 쓰고 content가 빈다(plans/100 J-1 ②)
    mlx_enable_thinking: bool = False

    # Gemini 설정
    gemini_api_key: str = ""
    gemini_model: str = ""

    # FabriX 설정
    fabrix_base_url: str = ""
    fabrix_api_key: str = ""
    fabrix_client_key: str = ""
    fabrix_chat_model: str = ""
    # FabriX 호출 1건의 벽시계 총상한(초, D-198). 클라이언트 timeout(300)은 httpx
    # 의미상 read 1회당 간격이라 하트비트(STATUS/SYNC)가 계속 오면 무기한 대기가
    # 가능하다(2026-09-07 무한대기 실측) — 총상한이 벽시계 기준으로 끊는다.
    fabrix_total_timeout: int = 300

    # FabriX 하이퍼파라미터 프로파일(D-194) — KBGenAI 요청 body의 `llmConfig` 규약
    # ({"temperature": <float>, "top_k": <int>, "top_p": <float>})으로 전송된다.
    # JSON 객체 **문자열**로 선언한다: dict 타입이면 `.env`의 빈 값(`KEY=`)이 config 로드
    # 자체를 깨뜨린다(json.loads("") 실패). 빈 값이면 llmConfig 필드를 보내지 않아
    # FabriX 서버 기본값이 적용된다(D-194 이전 동작 보존).
    fabrix_llm_config: str = ""
    # 최종 사용자 응답 합성(create_llm purpose="answer") 전용 프로파일.
    # 빈 값이면 fabrix_llm_config로 폴백한다.
    fabrix_answer_llm_config: str = ""

    model_config = {"env_prefix": "LLM_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    @field_validator("fabrix_llm_config", "fabrix_answer_llm_config")
    @classmethod
    def _validate_fabrix_llm_config(cls, v: str) -> str:
        """비어 있지 않으면 JSON 객체여야 한다 — 오탈자를 기동 시점에 명확히 실패시킨다."""
        if v and v.strip():
            import json

            parsed = json.loads(v)  # 실패 시 ValidationError로 승격 (침묵 폴백 금지)
            if not isinstance(parsed, dict):
                raise ValueError(
                    'JSON 객체여야 합니다 (예: {"temperature": 0.5, "top_k": 40})'
                )
        return v


class OrchestratorConfig(BaseSettings):
    """deepagents 오케스트레이터(vLLM, OpenAI 호환) 설정 (Plan 49 / D-037, 트랙 B).

    vLLM이 tool-calling(제어 평면)을 담당하고, FabriX(LLMConfig)는 워커(실질 응답처리)를 담당한다.
    base_url 미설정 또는 vLLM 미서빙 시 트랙 B를 사용하지 않고 semantic_router로 회귀한다
    (가용성 분기 — deep_agent.select_orchestration_backend).
    """

    # provider: vllm(운영, OpenAI 호환) | gemini(테스트/PoC 전용 — egress 필요, Plan 49 §4.7)
    #         | mlx(Apple Silicon 로컬 테스트 전용 — vLLM 경로 재사용 + max_tokens 명시, plans/100)
    provider: Literal["vllm", "gemini", "mlx"] = "vllm"
    base_url: str = ""            # vLLM /v1 엔드포인트 (예: http://vllm-host:8000/v1)
    model: str = "Qwen3.5-9B"     # vLLM 서빙 모델 (gemini 사용 시 gemini 모델명으로 설정)
    api_key: str = ""             # vLLM은 보통 불필요 / gemini는 미설정 시 LLM_GEMINI_API_KEY 폴백
    timeout: int = 120
    health_timeout: int = 3       # 가용성 health check 타임아웃(초, vLLM)
    # SSL 인증서 검증 여부(D-060). 목적지 vLLM이 443을 listen하되 유효 인증서를 쓰지 않는
    # 폐쇄망 환경에서 False로 두면 health check·tool-calling 요청의 SSL 검증을 건너뛴다.
    # 미설정 시 True(안전 기본값) — `.env`에 ORCHESTRATOR_VERIFY_SSL=false로 비활성화.
    verify_ssl: bool = True
    # 제어 평면 그래프 순회 상한(LangGraph recursion_limit). 미지정 시 LangGraph 기본값 25에
    # 암묵 의존하므로 명시 노브로 노출한다(Plan 67 Phase 0 ③ — 기본값은 동작 불변).
    # 도구 호출 단계가 많은 복합 질의에서 GraphRecursionError가 나면 .env로 상향한다.
    recursion_limit: int = 25

    # ── Plan 50 / D-040 (B6): 제어 평면 컨텍스트 예산 노브 ──
    # 모델 교체(예: 9B → 대형) 시 코드 수정 없이 .env(ORCHESTRATOR_*)만 조정하면 예산이 확장된다.
    # 단순 int/float이므로 .env JSON 파싱 이슈 없음(Known Mistakes 2026-03-23).
    # ※ 미소비(미배선) 주의 — 이 블록에서 실제로 읽히는 값은 max_tool_result_tokens 하나뿐이다
    #   (deepagents_tools._max_chars). 나머지 3개는 소비 지점이 없다(Plan 67 Phase 0 ⑦ 실측).
    #   삭제하지 않고 유지한다 — 설정 웹UI 카탈로그가 "미소비" 필드로 노출하는 대상이기 때문
    #   (settings_catalog.UNCONSUMED_KEYS / D-129, 2026-07-29 확정). 단 max_history_turns는
    #   아직 그 목록에 없어 UI가 "소비 중"으로 표시한다(카탈로그 누락 — 별건).
    #   배선하거나 삭제할 때는 카탈로그 등재도 함께 갱신한다.
    # 제어 평면 입력 토큰 안전 상한. 서버 max_model_len(=16384 상향 진행) − 출력 여유(~4000) = 12000.
    # 초과 예상 시 트리밍/요약/강등(B2) 트리거. 모델 교체 시 서버 max_model_len 상향과 함께 올린다.
    max_input_tokens: int = 12000        # 미소비
    # 상한 대비 트리밍 시작 임계 비율(80% 도달 시 오래된 도구 결과 쌍을 요약). 보통 유지.
    context_budget_ratio: float = 0.8    # 미소비
    # 제어 평면으로 반환하는 도구 결과 1건 요약 상한(B1). 원본은 collector에만 보관한다.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_tool_result_tokens: int = 2000
    # 제어 평면에 유지할 멀티턴 압축 맥락 턴 수(B3). 데이터 평면 MAX_HISTORY_TURNS=10과 별도.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_history_turns: int = 6           # 미소비

    # ── Plan 50 / D-040 (B7): Qwen 계열 no-think(추론 비활성) 모드 ──
    # 제어 평면 추론 모드. Qwen 계열은 false(no-think) 권장 — 추론 토큰으로 인한 한계 압박과
    # tool_call JSON 파싱 불안정을 회피한다. 추론이 유리한 큰 모델로 교체 시 .env로 true 전환.
    enable_thinking: bool = False

    model_config = {
        "env_prefix": "ORCHESTRATOR_",
        "env_file": [".env", ".encenv"],
        "extra": "ignore",
    }
