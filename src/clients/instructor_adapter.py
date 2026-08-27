"""LangChain LLM ↔ instructor 구조화 출력 어댑터 (Plan 79 트랙 E-3 · D-169).

**왜 어댑터인가.** instructor는 `Instructor(client=None, create=<임의 콜러블>)`을 허용하므로,
FabriX 프로토콜을 재구현하지 않고 **기존 `BaseChatModel`에 위임**할 수 있다. 페이로드·인증·
PII 훅(`log_filter_block_if_any`)·`remove_llm_junk`·SSE는 전부 기존 클라이언트가 계속 담당한다.
(대조: `pydantic-ai`는 `Model` ABC 서브클래스를 요구해 **세 번째 FabriX 구현**이 생긴다 —
`plans/78` §4.7.5에서 그 이유로 미채택.)

**왜 한국어 핸들러인가.** instructor 기본 `MD_JSON` 핸들러는 한국어 system 프롬프트 뒤에
영어 스키마 지시문("As a genius expert…")을 붙이고 추가 user 메시지까지 덧붙인다. 트랙 A가
고정한 few-shot 구조와 경쟁할 수 있어(Known Mistakes: "프롬프트 강제가 few-shot과 경쟁"),
`@register_mode_handler`로 **핸들러를 교체해 주입 문구를 통제**한다.

계층: infrastructure (`scripts/arch_check.py` `src.clients` 매핑).
"""

from __future__ import annotations

import json
import logging
from textwrap import dedent
from typing import Any, Optional, Sequence, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from src.utils.json_extract import coerce_content_text
from src.utils.llm_compat import is_kbgenai

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 모드 식별자 — instructor 미설치 환경에서도 임포트가 가능해야 하므로 문자열 상수로 둔다.
MODE_MD_JSON = "markdown_json_mode"
MODE_TOOLS = "tool_call"

# 네이티브 tool-calling을 지원하는 LLM 클래스명(전수 grep 기준).
# FabriX 두 모드는 양쪽 다 미지원이다 — KBGenAI는 페이로드에 tools 자리가 없고,
# OpenAI 호환 모드도 `_build_payload`가 tools를 싣지 않고 few-shot 문자열로 모사한다.
_NATIVE_TOOL_CALLING_CLASSES = frozenset({"ChatOpenAI", "AzureChatOpenAI"})

_HANDLER_REGISTERED = False


class StructuredOutputError(RuntimeError):
    """재시도를 소진하고도 스키마를 만족하지 못했다.

    **삼키지 않는다** — 호출자는 이 사유를 사용자 응답에 구조화해 노출해야 한다
    (침묵 폴백 금지 · Known Mistakes).
    """

    def __init__(self, message: str, *, attempts: int, last_error: Any = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def _load_instructor():
    """instructor를 lazy import한다.

    optional extra(`structured`)이므로 미설치가 정상 상태일 수 있다. 임포트 시점을 늦춰
    **미설치 환경에서도 앱이 기동**되게 한다(`semantic`·`stl`·`deepagents` 전례).

    Raises:
        ImportError: 미설치
    """
    import instructor  # noqa: PLC0415 — lazy import가 의도다

    return instructor


def structured_backend_available() -> bool:
    """instructor 사용 가능 여부. 판정은 예외를 던지지 않는다."""
    try:
        _load_instructor()
    except Exception:
        return False
    return True


def select_mode(llm: Any) -> str:
    """LLM 종류로 출력 모드를 고른다.

    라우터 평면 이동이 결정돼도 **이 분기만 바뀌고 소비처는 그대로**다 —
    그래서 E-3은 평면 이동을 선행조건으로 갖지 않는다(`plans/80` J-9).

    Args:
        llm: LangChain LLM 인스턴스

    Returns:
        MODE_TOOLS(네이티브 tool-calling) 또는 MODE_MD_JSON(평문)
    """
    if type(llm).__name__ in _NATIVE_TOOL_CALLING_CLASSES:
        return MODE_TOOLS
    return MODE_MD_JSON


# ─────────────────────── 한국어 MD_JSON 핸들러 ───────────────────────

def _korean_schema_block(response_model: type[BaseModel]) -> str:
    schema = json.dumps(
        response_model.model_json_schema(), indent=2, ensure_ascii=False
    )
    return dedent(
        f"""
        ## 출력 스키마 (반드시 준수)
        아래 JSON 스키마를 **정확히 만족하는 객체**를 ```json 블록으로 출력하세요.
        스키마 자체가 아니라 스키마를 만족하는 **값**을 출력해야 합니다.

        {schema}
        """
    ).rstrip()


def _korean_reask_block(exception: Exception) -> str:
    if isinstance(exception, ValidationError):
        detail = "\n".join(
            f"- `{'.'.join(str(x) for x in e['loc'])}`: {e['msg']} "
            f"(받은 값: {e.get('input')!r})"
            for e in exception.errors()
        )
    else:
        detail = str(exception)
    return (
        "직전 응답이 스키마 검증에 실패했습니다. 아래 오류를 고쳐 "
        "```json 블록으로 다시 출력하세요.\n\n### 검증 오류\n" + detail
    )


def _register_korean_handler() -> None:
    """MD_JSON 핸들러를 한국어판으로 교체 등록한다(프로세스당 1회).

    영어 주입문을 제거하는 것이 목적이며, 파싱·재시도 배선은 기본 핸들러를 **상속**한다.
    """
    global _HANDLER_REGISTERED
    if _HANDLER_REGISTERED:
        return

    from instructor import Mode
    from instructor.v2.core.decorators import register_mode_handler
    from instructor.v2.core.providers import Provider
    from instructor.v2.providers.openai.handlers import OpenAIMDJSONHandler

    @register_mode_handler(Provider.OPENAI, Mode.MD_JSON)
    class _KoreanMDJSONHandler(OpenAIMDJSONHandler):  # noqa: D401
        """스키마 주입·재질의 문구만 한국어로 교체한다."""

        def prepare_request(self, response_model, kwargs):
            if response_model is None:
                return None, kwargs
            new_kwargs = dict(kwargs)
            block = _korean_schema_block(response_model)
            messages = list(new_kwargs.get("messages", []))
            if (
                messages
                and messages[0].get("role") == "system"
                and isinstance(messages[0].get("content"), str)
            ):
                # 기존 프롬프트를 덮지 않고 **말미에만** 붙인다.
                head = dict(messages[0])
                head["content"] = f"{head['content']}\n{block}"
                messages[0] = head
            else:
                messages.insert(0, {"role": "system", "content": block})
            new_kwargs["messages"] = messages
            return response_model, new_kwargs

        def handle_reask(self, kwargs, response, exception):
            new_kwargs = dict(kwargs)
            messages = list(new_kwargs.get("messages", []))
            prev = response.choices[0].message
            messages.append({"role": "assistant", "content": prev.content})
            messages.append({"role": "user", "content": _korean_reask_block(exception)})
            new_kwargs["messages"] = messages
            return new_kwargs

    _HANDLER_REGISTERED = True


# ─────────────────────── 메시지 변환 (어댑터 본체) ───────────────────────

class _Msg:
    """OpenAI 응답 message 대역."""

    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None
        self.refusal = None

    def model_dump(self) -> dict:
        return {"role": "assistant", "content": self.content}


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)
        self.finish_reason = "stop"


class _Resp:
    def __init__(self, content: str, model: str):
        self.choices = [_Choice(content)]
        self.usage = None
        self.model = model
        self.id = "lc-adapter"


_ROLE_TO_LC = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def _to_lc_messages(messages: Sequence[dict], *, kbgenai: bool) -> list[BaseMessage]:
    """instructor의 dict 메시지를 LangChain 메시지로 되돌린다.

    KBGenAI는 System 다음에 빈 AIMessage를 요구한다 — 현재 이 규약이 8곳에 흩어져 있는데,
    구조화 출력 경로는 **여기 한 곳만** 알면 된다(Plan 69 P2 단일 출처 원칙 정합).
    """
    out: list[BaseMessage] = []
    for i, m in enumerate(messages):
        cls = _ROLE_TO_LC.get(m.get("role", "user"), HumanMessage)
        out.append(cls(content=m.get("content", "")))
        if kbgenai and i == 0 and m.get("role") == "system":
            out.append(AIMessage(content=""))
    return out


def _build_create(llm: Any):
    """instructor에 넘길 `create` 콜러블을 만든다(패치 전 원본)."""
    kbgenai = is_kbgenai(llm)

    async def _lc_create(*_args: Any, **kwargs: Any):
        lc_messages = _to_lc_messages(kwargs.get("messages", []), kbgenai=kbgenai)
        response = await llm.ainvoke(lc_messages)
        # 실 모델은 content를 콘텐츠 블록 리스트로 주기도 한다(2026-08-04 E1 실측) —
        # str 가정 시 여기서 깨지므로 반드시 정규화 유틸을 경유한다.
        text = coerce_content_text(getattr(response, "content", ""))
        return _Resp(text, kwargs.get("model", "lc"))

    return _lc_create


async def try_structured_call(
    llm: Any,
    messages: Sequence[BaseMessage],
    response_model: type[T],
    *,
    backend: str = "none",
    max_retries: int = 1,
) -> Optional[T]:
    """구조화 출력을 시도한다.

    Args:
        llm: LangChain LLM 인스턴스
        messages: 기존 경로와 동일한 LangChain 메시지 목록
        response_model: 기대 스키마(pydantic 모델)
        backend: "none"(기본 · 비활성) | "instructor"
        max_retries: 재시도 횟수. 총 호출은 max_retries + 1회.

    Returns:
        검증된 모델 인스턴스. **비활성이거나 백엔드 미설치면 None** — 호출자는 기존 경로로
        강등한다.

    Raises:
        StructuredOutputError: 재시도를 소진했다. 삼키지 말고 사유를 노출할 것.
    """
    if backend != "instructor":
        return None

    try:
        instructor = _load_instructor()
    except Exception as e:  # noqa: BLE001 — 미설치는 정상 상태일 수 있다
        logger.warning(
            "구조화 출력 백엔드(instructor) 사용 불가 — 기존 파싱 경로로 강등합니다: %s", e
        )
        return None

    mode_value = select_mode(llm)
    from instructor import Mode

    mode = Mode(mode_value)
    if mode is Mode.MD_JSON:
        _register_korean_handler()

    client = instructor.AsyncInstructor(
        client=None,
        create=instructor.patch(create=_build_create(llm), mode=mode),
        mode=mode,
    )

    payload = [
        {"role": _lc_role(m), "content": coerce_content_text(m.content)}
        for m in messages
    ]

    try:
        return await client.chat.completions.create(
            model=type(llm).__name__,
            messages=payload,
            response_model=response_model,
            max_retries=max_retries,
        )
    except Exception as e:  # noqa: BLE001 — 소진·파싱 실패를 구조화해 올린다
        attempts = getattr(e, "n_attempts", None) or (max_retries + 1)
        raise StructuredOutputError(
            f"구조화 출력 검증 실패({attempts}회 시도): {e}",
            attempts=attempts,
            last_error=e,
        ) from e


def _lc_role(m: BaseMessage) -> str:
    if isinstance(m, SystemMessage):
        return "system"
    if isinstance(m, AIMessage):
        return "assistant"
    return "user"
