"""OpenAI 호환 HTTP 표면 — `/v1/chat/completions` · `/v1/models` · `/health` (plans/148 §3.2~§3.7).

프록시는 상태가 없다 — 요청 1건을 KBGenAI 페이로드로 바꿔 부르고, 응답 텍스트를 결정적으로 파싱해
OpenAI `tool_calls`로 돌려준다. 도구 루프는 호출자가 돈다. 업스트림 재시도 0(D-268) · 교정 재질의는
프로토콜 단계다. 교정 재질의까지 포함한 호출 1건 전체에 벽시계 총상한
(`FABRIX_TOTAL_TIMEOUT`)을 건다.

로그에는 프롬프트·응답 본문을 어떤 레벨에도 싣지 않는다(필드 이름·수치만).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from fabrix_proxy import tool_protocol as tp
from fabrix_proxy.config import ConfigError, ProxySettings, require_token, resolve_path
from fabrix_proxy.convert import (
    ChatRequest,
    EmulationOptions,
    InvalidRequestError,
    build_llm_config,
    build_payload,
    native_body,
    parse_chat_request,
    prepare_request,
    repair_turns,
    resolve_options,
)
from fabrix_proxy.fabrix_client import (
    ContentFilterError,
    KBGenAIUpstream,
    Upstream,
    UpstreamError,
    quiet_http_loggers,
)
from fabrix_proxy.sse import completion_chunks

logger = logging.getLogger(__name__)

BACKEND = "fabrix"
HEADER_BACKEND = "X-Proxy-Backend"
HEADER_EMULATION = "X-Proxy-Tool-Emulation"
DIAG_FIELD = "fabrix_proxy_diag"
RAW_HEAD_CHARS = 500  # POC 진단 raw_heads 시도별 상한
MAX_BODY_BYTES = 4 * 1024 * 1024  # 요청 바디 상한(4 MiB) — 인증 뒤·JSON 파싱 전에 건다
IGNORED_LOG_FIELDS = 20  # 무시 필드 DEBUG 로그에 싣는 이름 수 상한
IGNORED_LOG_NAME_CHARS = 40  # 무시 필드 이름 1개당 로그 길이 상한

_ERROR_TYPES = {
    "invalid_api_key": "authentication_error",
    "invalid_request": "invalid_request_error",
    "content_filter": "invalid_request_error",
}


class ProxyError(Exception):
    """OpenAI 오류 봉투로 나갈 실패. message에 프롬프트·응답 본문을 싣지 않는다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Attempt:
    """업스트림 응답 1건의 판정값(본문 없음)."""

    kind: str
    reasons: list[str]
    called_names: list[str]
    example_tool_called: bool
    call_like_text: bool = False


@dataclass
class Diag:
    """POC_MODE 진단 — 판정값·수치와 시도별 원출력 앞부분(`raw_heads`)을 싣는다."""

    emulation: str = "none"
    passthrough: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    payload_chars: int = 0
    payload_est_tokens: int = 0
    protocol_chars: int = 0
    fewshot_chars: int = 0
    contents_len: int = 0
    upstream_ms: int = 0
    # 시도별 원출력 앞 500자(junk 제거 후) — 러너가 실패 건만 내부망 `failures/`에 남긴다.
    # 반출물(results·summary)에는 쓰지 않는다. POC_MODE일 때만 채운다(off면 diag 자체가 없다).
    raw_heads: list[str] = field(default_factory=list)


@dataclass
class _Ctx:
    """요청 1건의 감사 기록 재료."""

    request_id: str
    started: float
    alias: str = "-"
    stream: bool = False
    tools: int = 0
    in_chars: int = 0
    out_chars: int = 0
    diag: Diag | None = None


def _error_response(
    status: int, code: str, message: str, emulation: str, diag: Diag | None
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "message": message,
            "type": _ERROR_TYPES.get(code, "api_error"),
            "code": code,
        }
    }
    if diag is not None:
        body[DIAG_FIELD] = asdict(diag)
    return JSONResponse(
        body, status_code=status, headers={HEADER_BACKEND: BACKEND, HEADER_EMULATION: emulation}
    )


def _load_templates(settings: ProxySettings) -> tuple[dict[str, str], str]:
    """패키지 기본 규약 템플릿 2종과 설정 기본 템플릿을 기동 시 1회 읽는다."""
    templates = {lang: tp.load_protocol_template(lang) for lang in ("en", "ko")}
    lang = settings.fabrix_proxy_protocol_lang
    if settings.fabrix_proxy_protocol_file:
        return templates, tp.load_protocol_template(
            lang, resolve_path(settings.fabrix_proxy_protocol_file)
        )
    return templates, templates[lang]


def _default_options(
    settings: ProxySettings,
) -> tuple[EmulationOptions, dict[str, str], dict[str, Any]]:
    """설정 기본 선택지 · 패키지 기본 규약 템플릿 2종 · 패키지 기본 few-shot을 기동 시 만든다."""
    try:
        templates, default_template = _load_templates(settings)
        package_fewshot = tp.load_fewshot()
        fewshot_file = settings.fabrix_proxy_fewshot_file
        fewshot = (
            tp.load_fewshot(resolve_path(fewshot_file)) if fewshot_file else package_fewshot
        )
    except (OSError, ValueError) as exc:
        raise ConfigError(f"규약·few-shot 파일 로드 실패: {type(exc).__name__}: {exc}") from exc
    options = EmulationOptions(
        contents_mode=settings.fabrix_proxy_contents_mode,
        protocol_lang=settings.fabrix_proxy_protocol_lang,
        protocol_template=default_template,
        fewshot=settings.fabrix_proxy_fewshot,
        fewshot_placement=settings.fabrix_proxy_fewshot_placement,
        fewshot_data=fewshot,
        repair_max=settings.fabrix_proxy_repair_max,
        passthrough=settings.fabrix_proxy_passthrough,
    )
    return options, templates, package_fewshot


async def _read_body(request: Request) -> bytes:
    """바디를 상한까지만 읽는다 — Content-Length 헤더와 실제 읽은 길이 모두 본다."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > MAX_BODY_BYTES
        except ValueError as exc:
            raise ProxyError(400, "invalid_request", "Content-Length 형식 오류") from exc
        if too_big:
            raise ProxyError(400, "invalid_request", "요청 바디가 상한(4 MiB)을 넘는다")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ProxyError(400, "invalid_request", "요청 바디가 상한(4 MiB)을 넘는다")
        chunks.append(chunk)
    return b"".join(chunks)


def _assistant_message(parsed: tp.ParseResult) -> tuple[dict[str, Any], str]:
    message: dict[str, Any] = {"role": "assistant", "content": parsed.content}
    if parsed.kind == "tool_calls":
        message["tool_calls"] = [
            {
                "id": tp.new_tool_call_id(),
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in parsed.tool_calls
        ]
        return message, "tool_calls"
    return message, "stop"


def _message_chars(message: dict[str, Any]) -> int:
    total = len(message.get("content") or "")
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        total += len(str(fn.get("name") or "")) + len(str(fn.get("arguments") or ""))
    return total


def _native_choice(result: Any) -> dict[str, Any] | None:
    """네이티브 응답에서 choices[0]을 꺼낸다. OpenAI 형식이 아니면 None."""
    try:
        choice = result["choices"][0]
        message = choice["message"]
        for call in message.get("tool_calls") or []:
            if not (
                call["id"]
                and call["function"]["name"] is not None
                and "arguments" in call["function"]
            ):
                return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return choice if isinstance(choice, dict) else None


def create_app(settings: ProxySettings, upstream: Upstream | None = None) -> FastAPI:
    """프록시 앱을 조립한다. 토큰이 비거나 규약·few-shot 파일이 깨지면 `ConfigError`(기동 거부).

    Args:
        settings: 기동 시 1회 읽은 설정.
        upstream: 업스트림 구현. None이면 실 KBGenAI 호출자 — 테스트는 가짜를 주입한다.
    """
    require_token(settings)
    quiet_http_loggers()
    defaults, templates, package_fewshot = _default_options(settings)
    backend: Upstream = upstream if upstream is not None else KBGenAIUpstream(settings)
    token = settings.fabrix_proxy_token.encode("utf-8")
    aliases = list(settings.fabrix_proxy_model_aliases)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        return hmac.compare_digest(value.strip().encode("utf-8"), token)

    async def guarded(work: Any) -> Any:
        """총상한 + 업스트림 예외 → 오류 봉투 매핑."""
        try:
            return await asyncio.wait_for(work, timeout=settings.fabrix_total_timeout)
        except TimeoutError as exc:
            raise ProxyError(504, "upstream_timeout", "업스트림 총 소요 상한 초과") from exc
        except ContentFilterError as exc:
            raise ProxyError(400, "content_filter", "FabriX PII 필터가 요청을 차단했다") from exc
        except UpstreamError as exc:
            raise ProxyError(502, "upstream_error", str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ProxyError(504, "upstream_timeout", "업스트림 응답 대기 시간 초과") from exc
        except httpx.HTTPError as exc:
            raise ProxyError(
                502, "upstream_error", f"업스트림 연결 오류({type(exc).__name__})"
            ) from exc

    async def emulate(
        req: ChatRequest, opts: EmulationOptions, diag: Diag
    ) -> tuple[tp.ParseResult, dict[str, Any] | None]:
        prepared = prepare_request(req, opts)
        llm_config = build_llm_config(settings.fabrix_llm_config, req.temperature, req.top_p)
        diag.protocol_chars = prepared.protocol_chars
        diag.fewshot_chars = prepared.fewshot_chars
        extra: list[tp.Turn] | None = None
        for attempt in range(opts.repair_max + 1):
            payload = build_payload(
                prepared,
                model_id=settings.fabrix_model,
                contents_mode=opts.contents_mode,
                llm_config=llm_config,
                extra_turns=extra,
            )
            if attempt == 0:
                joined = payload["systemPrompt"] + "".join(payload["contents"])
                diag.payload_chars = len(joined)
                diag.payload_est_tokens = tp.estimate_tokens(joined)
                diag.contents_len = len(payload["contents"])
            t0 = time.monotonic()
            try:
                result = await backend.complete(payload)
            finally:
                diag.upstream_ms += int((time.monotonic() - t0) * 1000)
            if settings.fabrix_proxy_poc_mode:
                diag.raw_heads.append(result.text[:RAW_HEAD_CHARS])
            parsed = tp.parse_response(
                result.text,
                req.tools,
                req.tool_choice,
                req.parallel_tool_calls,
                prepared.example_tool_names,
            )
            diag.attempts.append(
                Attempt(
                    parsed.kind,
                    list(parsed.reasons),
                    list(parsed.called_names),
                    parsed.example_tool_called,
                    parsed.call_like_text,
                )
            )
            if parsed.kind != "invalid":
                if attempt > 0:
                    diag.emulation = "repaired"
                else:
                    diag.emulation = "parsed" if parsed.kind == "tool_calls" else "none"
                return parsed, result.usage
            message = tp.build_repair_message(
                parsed, req.tools, req.tool_choice, req.parallel_tool_calls, opts.protocol_lang
            )
            extra = repair_turns(tp.remove_llm_junk(result.text), message)
        diag.emulation = "invalid"
        reasons = ", ".join(diag.attempts[-1].reasons)
        raise ProxyError(
            502,
            "tool_call_invalid",
            f"도구 호출 에뮬레이션 실패({len(diag.attempts)}회 시도) — 사유: {reasons}",
        )

    def respond(
        ctx: _Ctx,
        req: ChatRequest,
        message: dict[str, Any],
        finish: str,
        usage: dict[str, Any] | None,
        emulation: str,
        poc: bool,
    ) -> Response:
        created = int(time.time())
        headers = {HEADER_BACKEND: BACKEND, HEADER_EMULATION: emulation}
        ctx.out_chars = _message_chars(message)
        if req.stream:
            chunks = completion_chunks(
                completion_id=ctx.request_id,
                created=created,
                model=req.alias,
                message=message,
                finish_reason=finish,
            )
            return StreamingResponse(chunks, media_type="text/event-stream", headers=headers)
        body: dict[str, Any] = {
            "id": ctx.request_id,
            "object": "chat.completion",
            "created": created,
            "model": req.alias,
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish, "logprobs": None}
            ],
        }
        if usage:
            body["usage"] = usage
        if poc and ctx.diag is not None:
            body[DIAG_FIELD] = asdict(ctx.diag)
        return JSONResponse(body, headers=headers)

    async def passthrough(ctx: _Ctx, req: ChatRequest, diag: Diag) -> Response:
        if not settings.fabrix_native_url:
            raise ProxyError(
                400, "invalid_request", "passthrough 대상(FABRIX_NATIVE_URL)이 설정되지 않았다"
            )
        diag.passthrough = True
        body = native_body(req.raw, settings.fabrix_native_model or settings.fabrix_model)
        t0 = time.monotonic()
        try:
            result = await guarded(backend.native(body))
        finally:
            diag.upstream_ms = int((time.monotonic() - t0) * 1000)
        choice = _native_choice(result)
        if choice is None:
            raise ProxyError(502, "upstream_error", "네이티브 응답 형식 오류")
        message = dict(choice["message"])
        finish = choice.get("finish_reason") or (
            "tool_calls" if message.get("tool_calls") else "stop"
        )
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
        return respond(ctx, req, message, finish, usage, "none", settings.fabrix_proxy_poc_mode)

    async def handle(request: Request, ctx: _Ctx) -> Response:
        if not authorized(request):
            raise ProxyError(401, "invalid_api_key", "프록시 토큰이 없거나 맞지 않다")
        body = await _read_body(request)
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise ProxyError(400, "invalid_request", "요청 바디가 JSON이 아니다") from exc
        try:
            req = parse_chat_request(raw, aliases, settings.fabrix_proxy_poc_mode)
            opts = resolve_options(defaults, req.options, templates, package_fewshot)
        except InvalidRequestError as exc:
            raise ProxyError(400, "invalid_request", str(exc)) from exc
        ctx.alias, ctx.stream, ctx.tools = req.alias, req.stream, len(req.tools)
        ctx.in_chars = sum(len(m.get("content") or "") for m in req.messages)
        if req.ignored_fields:
            # 필드 이름은 호출자 입력이다 — repr·길이 상한으로 개행 주입을 막는다.
            logger.debug(
                "무시한 요청 필드 id=%s n=%d fields=%r",
                ctx.request_id,
                len(req.ignored_fields),
                [name[:IGNORED_LOG_NAME_CHARS] for name in req.ignored_fields[:IGNORED_LOG_FIELDS]],
            )
        ctx.diag = Diag()
        if opts.passthrough:
            return await passthrough(ctx, req, ctx.diag)
        parsed, usage = await guarded(emulate(req, opts, ctx.diag))
        message, finish = _assistant_message(parsed)
        return respond(
            ctx, req, message, finish, usage, ctx.diag.emulation, settings.fabrix_proxy_poc_mode
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        ctx = _Ctx(request_id="chatcmpl-" + uuid.uuid4().hex[:24], started=time.monotonic())
        try:
            response = await handle(request, ctx)
        except ProxyError as exc:
            if exc.status >= 500 or exc.code == "content_filter":
                logger.warning("요청 실패 id=%s code=%s", ctx.request_id, exc.code)
            diag = ctx.diag if settings.fabrix_proxy_poc_mode else None
            emulation = ctx.diag.emulation if ctx.diag is not None else "none"
            response = _error_response(exc.status, exc.code, exc.message, emulation, diag)
        except Exception as exc:  # noqa: BLE001 — 예상 못 한 예외도 OpenAI 오류 봉투로 낸다
            # 예외 메시지·트레이스백에는 요청 본문 조각이 섞일 수 있어 클래스 이름만 남긴다.
            logger.error(
                "요청 처리 중 예상 못 한 예외 id=%s type=%s", ctx.request_id, type(exc).__name__
            )
            response = _error_response(
                500, "internal_error", f"프록시 내부 오류({type(exc).__name__})", "none", None
            )
        logger.info(
            "chat id=%s alias=%s stream=%s tools=%d emulation=%s status=%d ms=%d "
            "in_chars=%d out_chars=%d",
            ctx.request_id,
            ctx.alias,
            ctx.stream,
            ctx.tools,
            response.headers.get(HEADER_EMULATION, "none"),
            response.status_code,
            int((time.monotonic() - ctx.started) * 1000),
            ctx.in_chars,
            ctx.out_chars,
        )
        return response

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """별칭 목록 — 인증 없음(본체 가용성 판정이 헤더 없이 부른다).

        업스트림 URL·자격증명은 싣지 않는다.
        """
        return {
            "object": "list",
            "data": [
                {
                    "id": alias,
                    "object": "model",
                    "created": 0,
                    "owned_by": "fabrix_proxy",
                    "backend": BACKEND,
                    "capabilities": {"tools": "emulated", "stream": True},
                }
                for alias in aliases
            ],
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        """프로세스 생존만 — 업스트림을 부르지 않는다."""
        return {"status": "ok"}

    return app
