"""MLX 로컬 서버 워커 클라이언트 (plans/100 — Apple Silicon 로컬 테스트 전용).

`mlx_lm.server`(OpenAI 호환 `/v1`)에 붙는 `ChatOpenAI` 서브클래스다. 요청 형식·스트리밍·
bind_tools는 `ChatOpenAI`를 그대로 쓰고, `_llm_type`만 바꾼다.

**클래스명이 동작에 쓰인다.** 구조화 출력 어댑터는 클래스명이 `ChatOpenAI`면 `TOOLS` 모드를
고른다(`src/clients/instructor_adapter.py` `select_mode`). 이 이름이면 운영 워커 FabriX와 같은
`MD_JSON` 모드가 선택된다. 도입 당시 `TOOLS` 모드는 어댑터 결함으로 늘 실패했고(plans/100 J-3 ·
v4 B-3에서 수정), 지금도 `MD_JSON`을 유지하는 이유는 ①운영 워커와 같은 모드여야 로컬 검증이
충실하고 ②`mlx_lm.server` 0.31.3이 `tool_choice`(도구 호출 강제)를 처리하지 않기 때문이다
(서버 소스 grep 0건). 클래스명을 바꾸거나 `ChatOpenAI`를 직접 쓰지 말 것
(`tests/test_llm_mlx.py` T-5).

`langchain-openai`는 `deepagents` extra 소속이라 이 모듈은 `src/clients/__init__.py`에
등록하지 않는다 — 등록하면 `src.clients` import만으로 그 패키지가 필수가 된다.
`src/llm.py`의 `_create_mlx`가 호출 시점에 import한다.

**`max_tokens` 절단은 경고로 남긴다.** 서버는 `max_tokens`에 닿으면 `finish_reason=length`로
응답을 끊는데, 잘린 응답(예: 끝이 잘린 SQL)이 그대로 다음 단계로 넘어가고 흔적이 없었다
(plans/100 Phase 4 실측). 응답 형태는 바꾸지 않고 로그만 남긴다 — 일반·스트리밍 경로 모두.

**스트리밍 첫 청크 대기 상한은 요청 timeout과 같게 둔다**(`stream_chunk_timeout_kwargs`).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

#: langchain-openai 1.2.0부터 있는 필드다. pyproject 하한 1.1.13에는 없다(wheel 실측 2026-09-17).
HAS_STREAM_CHUNK_TIMEOUT = "stream_chunk_timeout" in ChatOpenAI.model_fields


def stream_chunk_timeout_kwargs(timeout: float) -> dict[str, float]:
    """스트리밍 청크 간격 상한을 요청 timeout과 같게 두는 생성자 인자.

    `mlx_lm.server`는 prompt 처리(prefill)가 끝나야 첫 청크를 보낸다. langchain-openai의
    `stream_chunk_timeout` 기본값 120초가 그보다 짧으면 긴 프롬프트 호출이 첫 청크 전에 끊긴다 —
    Qwen3.8-27B에서 12K 토큰 prefill이 123초 걸려 `schema_analyzer` 테이블 선택과
    `semantic_compiler`가 폴백으로 넘어가고 그 턴이 900초 서버 타임아웃으로 실패했다
    (2026-09-17 plans/94 실 실행). 이 상한은 비동기 스트리밍(`astream`) 경로에만 걸리고 — 앱은
    그래프를 비동기로 돌리며 LLM을 스트리밍으로 부른다 — `timeout`(`LLM_MLX_TIMEOUT`·
    `ORCHESTRATOR_TIMEOUT`)을 올려도 따라 바뀌지 않는다.

    필드가 없는 버전에는 넘기지 않는다 — 모르는 인자는 `model_kwargs`로 옮겨져 요청 본문에 실린다.
    """
    if not HAS_STREAM_CHUNK_TIMEOUT:
        return {}
    return {"stream_chunk_timeout": float(timeout)}


class MLXChatOpenAI(ChatOpenAI):
    """`mlx_lm.server` LLM(워커·오케스트레이터 공용). 응답은 `ChatOpenAI`와 같다."""

    @property
    def _llm_type(self) -> str:
        return "mlx"

    def _create_chat_result(
        self, response: Any, generation_info: dict[str, Any] | None = None
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        for generation in result.generations:
            if (generation.generation_info or {}).get("finish_reason") == "length":
                self._warn_truncated((result.llm_output or {}).get("id"))
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is not None and (generation.generation_info or {}).get(
            "finish_reason"
        ) == "length":
            self._warn_truncated(chunk.get("id"))
        return generation

    def _warn_truncated(self, response_id: Any) -> None:
        logger.warning(
            "MLX 응답이 max_tokens에서 잘렸다(finish_reason=length): model=%s max_tokens=%s id=%s "
            "— 잘린 응답이 그대로 다음 단계로 넘어간다. LLM_MLX_MAX_TOKENS를 확인한다",
            self.model_name, self.max_tokens, response_id,
        )
