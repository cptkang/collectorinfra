"""조사 LLM의 Harmony 파싱 오류를 같은 요청 그대로 재시도한다 (plans/213 W3 · D-366 ①).

**왜 필요한가** (폐쇄망 실측 2026-11-04):
gpt-oss 계열(Harmony 응답 형식)을 서빙하는 vLLM이 모델 출력을 Harmony로 해석하지 못하면
`Unexpected token … expecting start token 200006`(`<|start|>`) 400을 돌려준다. 같은 요청을 다시
보내면 정상 반환되는 일시 오류인데, holmes 0.36.0 `DefaultLLM.completion`에는 재시도가 없어
400 한 번으로 심층 조사가 `failed`로 끝난다.

**무엇을 하는가**: 문자열화한 예외 메시지에 표지 `HARMONY_PARSE_ERROR_MARKER`가 있는 litellm
`BadRequestError`·`InternalServerError`만 **같은 인자 그대로** 재시도한다(최대 `max_retries`회).
temperature 등 요청은 바꾸지 않는다(사용자 결정 — 같은 서버 재요청 정상 반환 실측). 재시도도
실패하면 마지막 예외를 그대로 올린다(종전 실패 경로). 첫 시도 시작부터 경과가 `skip_after_s`를
넘으면 재시도하지 않는다 — 단계 소견·종합은 바깥 타이머(60·90초)가 끝나면 결과가 버려지고,
조사 루프도 조사 전체 상한(600초)을 아낀다.

**안전성**: 표지가 없는 예외(컨텍스트 초과·인증·403 등)는 즉시 그대로 올리고, 정상 응답은 입력
객체 그대로 돌려준다 — 그 밖의 경로는 비트 동일.

**어디에 거는가**: `DefaultLLM.completion` — D-213 `llm_message_guard`·D-343
`tool_call_markup_guard`와 같은 꼴(멱등 표식). **가장 안쪽**(실제 호출 바로 바깥)에 둔다 —
`DiagnosisAgent`가 이 가드를 먼저 설치하므로 사슬은 C8(D-213(이 가드(원본)))이 되고, C8은 재시도
뒤의 최종 응답을 본다(조사 계측 훅 `SRE_INVESTIGATION_METRICS_ENABLED`이 서비스 조립 때
먼저 설치되면 그 훅은 이 가드 안쪽에 남는다 — 재시도 1회가 LLM 호출 1건으로 따로 계측된다).
조사 루프(`tools` + `tool_choice="auto"`)와 단계 소견·종합·압축 호출이 모두
대상이며, 호출 종류는 로그 구분에만 쓴다.

**로그**: 오류 원문은 앞 200자만 남긴다. 로그 레코드 팩토리 가림(D-339 · `run_service`가 설치하는
`log_masking.install_log_record_factory`)이 렌더 메시지를 가린 뒤 기록된다.

계층: infrastructure(외부 패키지 어댑터). 상위는 `install_harmony_retry_guard()`만 부른다.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from sre_agent.infrastructure.tool_call_markup_guard import _sends_tools

logger = logging.getLogger(__name__)

# 몽키패치 멱등성 표식 — 재설치·중복 호출을 무해하게 만든다.
_GUARD_FLAG = "_sre_harmony_retry_guarded"

#: Harmony 파싱 실패 표지 — 단일 출처. vLLM 버전에 따라 문구가 바뀌면 재시도가 조용히 멈춘다
#: (실패해도 종전 동작).
HARMONY_PARSE_ERROR_MARKER = "expecting start token"

#: 재시도 대상 litellm 예외 클래스 이름 — litellm import 실패 시 폴백 판정에도 쓴다.
_RETRYABLE_NAMES = frozenset({"BadRequestError", "InternalServerError"})

#: 로그에 남기는 오류 원문 길이 상한.
_EXCERPT_LIMIT = 200


def _retryable_classes() -> tuple[type[BaseException], ...] | None:
    """litellm 재시도 대상 클래스. import 실패면 None(이름 판정 폴백)."""
    try:
        import litellm

        return (litellm.BadRequestError, litellm.InternalServerError)
    except Exception:  # noqa: BLE001 — litellm 부재·구조 변경은 이름 판정으로 넘긴다
        return None


def _is_litellm_retryable(exc: BaseException) -> bool:
    """litellm `BadRequestError`·`InternalServerError`(하위 클래스 포함)인지.

    litellm을 import할 수 없으면 MRO에서 **litellm 모듈에 정의된** 같은 이름 클래스를 찾는다
    (openai의 같은 이름 클래스 등 다른 패키지 예외는 오탐하지 않는다).
    """
    classes = _retryable_classes()
    if classes is not None:
        return isinstance(exc, classes)
    return any(
        cls.__name__ in _RETRYABLE_NAMES and cls.__module__.split(".")[0] == "litellm"
        for cls in type(exc).__mro__
    )


def is_harmony_parse_error(exc: BaseException) -> bool:
    """재시도 대상인 Harmony 파싱 오류인지(litellm 400·500 + 표지)."""
    if not _is_litellm_retryable(exc):
        return False
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 — 문자열화 실패는 대상 아님
        return False
    return HARMONY_PARSE_ERROR_MARKER in text


def _excerpt(exc: BaseException) -> str:
    try:
        return str(exc)[:_EXCERPT_LIMIT]
    except Exception:  # noqa: BLE001
        return type(exc).__name__


def install_harmony_retry_guard(
    max_retries: int = 1,
    skip_after_s: float = 45.0,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """holmes LLM 경계(`DefaultLLM.completion`)에 Harmony 파싱 오류 재시도를 설치한다.

    설치는 holmes 클래스 경계 교체라 **프로세스 전역·1회**다 — 인자는 처음 설치할 때 값으로 고정된다.

    Args:
        max_retries: 재시도 상한(같은 인자 그대로). 0 이하면 설치하지 않는다(off).
        skip_after_s: 첫 시도 시작부터 경과가 이 값을 넘으면 재시도하지 않는다.
        clock: 경과 측정 시계(초). 테스트 주입용.

    Returns:
        True면 이번 호출에서 설치했고, False면 설치하지 않았다(off · 이미 설치됨 · holmes 부재).
        **어느 경우에도 예외를 올리지 않는다**(사유는 로그로 가시화).
    """
    if max_retries <= 0:
        return False
    try:
        from holmes.core.llm import DefaultLLM
    except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
        logger.warning("Harmony 재시도 가드 미설치 — holmes.core.llm 임포트 실패: %s", exc)
        return False

    original = getattr(DefaultLLM, "completion", None)
    if original is None:
        logger.warning("Harmony 재시도 가드 미설치 — DefaultLLM.completion 부재(상위 버전 변경?)")
        return False
    if getattr(original, _GUARD_FLAG, False):
        return False  # 이미 설치됨(멱등)

    @functools.wraps(original)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        start = clock()
        attempt = 0
        while True:
            try:
                response = original(self, *args, **kwargs)
            except Exception as exc:
                if attempt > 0:
                    logger.info(
                        "Harmony 파싱 오류 재시도 결과 — 재실패 %d/%d (%s)",
                        attempt,
                        max_retries,
                        type(exc).__name__,
                    )
                if not is_harmony_parse_error(exc):
                    raise
                if attempt >= max_retries:
                    raise
                elapsed = clock() - start
                if elapsed > skip_after_s:
                    logger.info(
                        "Harmony 파싱 오류 재시도 생략 — 첫 시도 경과 %.1fs > %.0fs",
                        elapsed,
                        skip_after_s,
                    )
                    raise
                attempt += 1
                logger.warning(
                    "Harmony 파싱 오류 재시도 %d/%d — 첫 시도 경과 %.1fs · 호출 종류=%s · 오류=%s",
                    attempt,
                    max_retries,
                    elapsed,
                    "조사" if _sends_tools(args, kwargs) else "요약",
                    _excerpt(exc),
                )
                continue
            if attempt > 0:
                logger.info("Harmony 파싱 오류 재시도 결과 — 통과 %d/%d", attempt, max_retries)
            return response

    setattr(guarded, _GUARD_FLAG, True)
    DefaultLLM.completion = guarded  # type: ignore[method-assign]
    logger.info(
        "Harmony 재시도 가드 설치 완료 — 표지 오류 같은 요청 재시도 최대 %d회 · %.0fs 넘으면 생략 "
        "(D-366 ①)",
        max_retries,
        skip_after_s,
    )
    return True
