"""HolmesGPT SDK 래퍼 — 장애 진단 에이전트의 핵심 진입점.

holmesgpt 0.36.0 실측 API 기준:
- Config(model, api_key, max_steps, ...) → create_toolcalling_llm() → ToolCallingLLM
- build_initial_ask_messages()로 시스템/유저 프롬프트 조립 후 `call_stream`을 직접 소비한다
  (`_StreamDrain` — 결과 조립은 holmes `ToolCallingLLM.call()`과 같다 · plans/154 W1)
"""

import copy
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from holmes.config import Config
from holmes.core.llm_usage import RequestStats
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tool_calling_llm import LLMInterruptedError, LLMResult, ToolCallingLLM
from holmes.core.tools import (
    PrerequisiteCacheMode,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolsetStatusEnum,
    ToolsetTag,
    ToolsetType,
)
from holmes.utils.colors import AI_COLOR
from holmes.utils.stream import StreamEvents

from sre_agent.domain.activity_log import ActivityLog, safe_append
from sre_agent.domain.failure_guards import StallTracker
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.bash_write_guard import install_bash_write_guard
from sre_agent.infrastructure.llm_message_guard import install_system_message_guard
from sre_agent.infrastructure.tool_call_markup_guard import install_tool_call_markup_guard
from sre_agent.infrastructure.tool_result_cache import CacheClosedError, ToolResultCache
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import LOAD_GUARD_NOTE, vm_profile

# holmes `call()`이 쓰는 표시 로거(같은 이름) — 직접 소비로 바꿔도 콘솔 진행 표시를 유지한다.
display_logger = logging.getLogger("holmes.display.tool_calling_llm")
logger = logging.getLogger(__name__)

# holmes step 상한 예외 문구 `Too many LLM calls - exceeded max_steps: i/N`의 i.
_STEP_LIMIT_RE = re.compile(r"max_steps:\s*(\d+)\s*/")

#: 관측 도구(MCP 툴셋) 연결 실패로 LLM 조사를 생략한 결과의 서술 머리(plans/188 C2 · Q1 A안).
TOOLS_UNAVAILABLE_PREFIX = "조사 불가 — 관측 도구 연결 실패"

# holmes 툴셋 사유 정리(plans/188 교정): 서버 모드 전용 재시도 안내 꼬리 · URL userinfo.
_HOLMES_RETRY_TAIL_RE = re.compile(r"\.?\s*If the server is still starting up\b.*$")
_URL_USERINFO_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@'\"]+@")


def clean_toolset_error(error: str | None) -> str:
    """holmes 툴셋 사유를 브리핑·헬스·로그용 한 줄로 정리한다(plans/188 교정).

    첫 줄만 남기고(httpx 401의 「For more information check: …」 줄 제거), holmes 서버 모드 전용
    「If the server is still starting up, … retry automatically」 꼬리를 떼고, URL userinfo를
    `***@`로 가린다.
    """
    lines = (error or "").strip().splitlines()
    first = _HOLMES_RETRY_TAIL_RE.sub("", lines[0]) if lines else ""
    return _URL_USERINFO_RE.sub(r"\1***@", first).strip()


def to_tool_records(tool_calls: list | None) -> list["ToolCallRecord"]:
    """holmes `LLMResult.tool_calls`(list[ToolCallResult])를 원시 출력 보존 레코드로 변환한다.

    실측(holmesgpt 0.36.0): 각 원소는 `tool_name/description/result` 속성을 가지며,
    `result`(StructuredToolResult)의 `get_stringified_data()`가 원시 출력 문자열이다.
    result가 None이거나 속성이 없어도 침묵 실패하지 않고 방어적으로 빈 값을 채운다.
    """
    records: list[ToolCallRecord] = []
    for tc in tool_calls or []:
        result = getattr(tc, "result", None)
        if result is not None:
            status_obj = getattr(result, "status", None)
            status = getattr(status_obj, "value", status_obj)
            output = result.get_stringified_data()
            error = getattr(result, "error", None)
            return_code = getattr(result, "return_code", None)
            params = getattr(result, "params", None)
        else:
            status, output, error, return_code, params = "unknown", "", None, None, None
        records.append(
            ToolCallRecord(
                tool_name=getattr(tc, "tool_name", ""),
                description=getattr(tc, "description", ""),
                status=str(status) if status is not None else "unknown",
                output=output or "",
                error=error,
                return_code=return_code,
                params=params if isinstance(params, dict) else None,
            )
        )
    return records


@dataclass
class ToolCallRecord:
    """단일 도구 호출의 **원시 출력** 보존 레코드 (Plan 02 §3·§6).

    severity_judge는 LLM 서술이 아니라 도구 **원시 출력**에 시그니처 매칭을 수행하므로
    `output`(raw 문자열)이 필수다. holmesgpt 0.36.0 실측:
    `LLMResult.tool_calls: list[ToolCallResult]`이며, 각 `ToolCallResult`는
    `tool_name/description/result: StructuredToolResult`를 가지고, 원시 출력은
    `StructuredToolResult.data(Any)`이고 `get_stringified_data() -> str`로 문자열화된다
    (`status: StructuredToolResultStatus`(.value=str)·`error`·`return_code` 동봉).

    `params`는 `StructuredToolResult.params`(도구 호출 인자 — holmes 0.36.0 MCP `_invoke_async`가 채운다)다.
    정체 가드(plans/87 P15 — 같은 도구·같은 인자 반복)의 입력이며, 없으면 None이다.
    """

    tool_name: str
    description: str
    status: str  # StructuredToolResultStatus.value ("success"|"error"|"no_data"|...)
    output: str  # StructuredToolResult.get_stringified_data() — 원시 출력 문자열
    error: str | None = None
    return_code: int | None = None
    params: dict | None = None


@dataclass
class DiagnosisResult:
    """진단 결과 요약. answer가 비면 실패 사유를 tool_calls/raw로 확인한다.

    tool_calls는 사람이 읽는 요약(description)이고, tool_outputs는 severity_judge가
    소비할 **원시 출력**을 보존한다(Plan 02 §3 — description만으로는 시그니처 매칭 불가).
    """

    answer: str
    tool_calls: list[str] = field(default_factory=list)
    tool_outputs: list[ToolCallRecord] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    # step 상한 도달 등으로 조사가 결론에 이르지 못한 미완주 여부(Plan 02 §12-④).
    # True면 후처리(briefing/severity_judge)가 "가설/한계"로 표기하고, escalate 신호로
    # 취급하지 않는다(재현율 우선 — 미완주가 상향 근거가 되면 오탐 폭주).
    incomplete: bool = False
    # 조사 LLM 턴 수(holmes `LLMResult.num_llm_calls` · plans/154 W1). 상한 예외면 예외 문구의 i.
    steps: int | None = None
    # 관측 도구 연결 실패로 LLM 조사를 생략했을 때의 사유 1줄(plans/188 C2 · Q1 A안).
    # 값이 있으면 dispatcher가 요약을 `[미결] `로 표기하고 한계에 이 줄을 싣는다.
    # None이면 종전과 같다.
    tools_unavailable: str | None = None
    # 정체 가드(plans/198 I-10 · `stall_stop_enabled`)로 조사를 멈춘 사유 1줄. 값이 있으면
    # `incomplete=True`다. None이면 종전과 같다(플래그 off면 늘 None).
    stalled: str | None = None
    # 턴 계측(plans/200 I-0 · 가산) — `_StreamDrain`이 센 값. `tool_batch_sizes`는 도구를 요청한
    # 턴마다 그 턴의 도구 수(요청 순 · 도구 0인 턴은 넣지 않는다), `turns_n`은 드레인이 시작한
    # LLM 턴 수(도구 0인 결론 턴 포함). 드레인을 거치지 않은 결과는 None.
    tool_batch_sizes: list[int] | None = None
    turns_n: int | None = None


class InvestigationStalledError(Exception):
    """정체 가드가 조사를 멈췄다(plans/198 I-10) — `_StreamDrain.run`이 도구 step 경계에서 낸다."""

    def __init__(self, reason: str, steps: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.steps = steps


def tools_unavailable_result(errors: dict[str, str]) -> DiagnosisResult:
    """MCP 툴셋 연결 실패로 LLM 조사를 생략한 결과(plans/188 C2 · Q1 A안 — LLM 호출 0).

    서술은 「조사 불가 — 관측 도구 연결 실패」뿐이고(오류 원문 없음 — 요약에만 쓰인다), 사유는
    `tools_unavailable` 한 줄 「…(이름: 정리된 사유; …)」에만 싣는다(브리핑 한계·감사).
    """
    reason = "; ".join(
        f"{name}: {clean_toolset_error(err) or '사유 미상'}" for name, err in errors.items()
    )
    line = f"{TOOLS_UNAVAILABLE_PREFIX}({reason})"
    return DiagnosisResult(answer=TOOLS_UNAVAILABLE_PREFIX, tools_unavailable=line)


def _result_text(result: StructuredToolResult) -> str:
    """holmes 도구 결과 → 잡 캐시 값(플레이북 배치 콜러블과 같은 「도구 반환 문자열」).

    MCP 도구는 `data`가 도구 텍스트(isError여도 `data`에 실린다)이고, 호출 예외는 `error`만 있다
    — 후자는 배치 콜러블의 `{"error": …}`와 같은 모양으로 남긴다.
    """
    data = result.data
    if isinstance(data, str):
        return data
    if data is not None:
        return json.dumps(data, ensure_ascii=False, default=str)
    if result.error:
        return json.dumps({"error": result.error}, ensure_ascii=False)
    return ""


def _result_from_text(text: str, params: dict) -> StructuredToolResult:
    """잡 캐시 값 → holmes 도구 결과. `{"error": …}` 단독이면 ERROR, 그 밖은 SUCCESS(텍스트)."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and set(parsed) == {"error"}:
        return StructuredToolResult(status=StructuredToolResultStatus.ERROR,
                                    error=str(parsed["error"]), params=params)
    return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=text,
                                params=params)


class _CachedToolInvoke:
    """잡 단위 도구 결과 캐시를 거치는 holmes `_directly_invoke_tool_call` 대체 (G-26 · D-341 ⑲).

    holmes 0.36.0 실측: 병렬 도구 호출은 `call_stream`의 스레드풀(16)이
    `self._invoke_llm_tool_call` → `self._directly_invoke_tool_call(tool_name, tool_params,
    user_approved, tool_call_id, …)`로 부른다. `ask` 1회마다 `ToolCallingLLM` 얕은 사본에 이 객체를
    인스턴스 속성으로 얹으므로(holmes `with_executor`와 같은 요청별 사본) 스레드풀 문맥 전파 없이도
    그 잡의 캐시만 쓰인다 — 공유 `ToolCallingLLM`·전역 상태는 건드리지 않는다(잡 간 누수 0).

    캐시 대상은 MCP 도구(`mcp_tool_name`)뿐이다 — 플레이북이 부르는 폴스타·APM 도구와 같은
    서버 쪽 이름으로 키를 만든다(충돌 접두어가 붙은 노출 이름이 아니다). 그 밖의 도구·사용자 승인
    재호출은 종전 그대로 부른다. 잡이 끝나 캐시가 닫혔으면 실호출 없이 오류 결과를 돌려준다.
    """

    def __init__(self, llm: ToolCallingLLM, cache: ToolResultCache) -> None:
        self._llm = llm
        self._inner = llm._directly_invoke_tool_call
        self._cache = cache
        self._lock = threading.Lock()
        self._hits: set[str] = set()

    def __call__(self, tool_name: str, tool_params: dict, user_approved: bool,
                 tool_call_id: str, **kwargs: Any) -> StructuredToolResult:
        user_id = (kwargs.get("request_context") or {}).get("user_id")
        tool = self._llm.tool_executor.get_tool_by_name(tool_name, user_id=user_id)
        server_name = getattr(tool, "mcp_tool_name", None) or None
        if server_name is None or user_approved:
            return self._inner(tool_name, tool_params, user_approved, tool_call_id, **kwargs)
        owned: list[StructuredToolResult] = []

        def _batch(calls: object) -> list[str]:
            res = self._inner(tool_name, tool_params, user_approved, tool_call_id, **kwargs)
            owned.append(res)
            return [_result_text(res)]

        try:
            text = self._cache.fetch(_batch, [(str(server_name), tool_params)])[0]
        except CacheClosedError as exc:
            return StructuredToolResult(status=StructuredToolResultStatus.ERROR,
                                        error=str(exc), params=tool_params)
        if owned:
            return owned[0]
        with self._lock:
            self._hits.add(tool_call_id)
        return _result_from_text(text, tool_params)

    def is_hit(self, tool_call_id: str) -> bool:
        """이 호출이 잡 캐시(완료·진행 중)에서 나왔는지 — 실행 기록 `status=cached`용."""
        with self._lock:
            return tool_call_id in self._hits


def _with_tool_cache(llm: ToolCallingLLM, cache: ToolResultCache
                     ) -> tuple[ToolCallingLLM, _CachedToolInvoke]:
    """`ask` 1회용 사본 — 도구 실행만 잡 캐시를 거친다(원본·다른 잡은 그대로)."""
    invoke = _CachedToolInvoke(llm, cache)
    clone = copy.copy(llm)
    clone._directly_invoke_tool_call = invoke
    return clone, invoke


def _with_tool_allowlist(llm: ToolCallingLLM, names: Collection[str]) -> ToolCallingLLM:
    """`ask` 1회용 사본 — 도구 목록만 `names`로 좁힌다(plans/198 I-9 · 원본·다른 잡은 그대로).

    holmes `ToolExecutor._clone_base()`(요청별 프런트 도구 주입과 같은 얕은 사본 — 툴셋 공유 ·
    도구 등록부만 독립)에서 빠질 이름을 지운다. LLM에 보이는 도구(`_get_tools`)와 실행 조회
    (`get_tool_by_name` → 없으면 holmes 「Failed to find tool」 오류 결과)가 같은 등록부를 읽으므로
    좁힌 밖 도구는 노출도 실행도 되지 않는다.

    시스템 프롬프트의 툴셋 지침(holmes `build_initial_ask_messages` → `tool_executor.toolsets`)도
    좁힌다 — 도구가 하나도 남지 않은 툴셋을 사본의 `toolsets`·`enabled_toolsets`에서 뺀다(새
    목록 · 공유 목록은 그대로). 원래 등록부에 도구가 없던 툴셋(비활성 등)은 건드리지 않는다.
    """
    executor = llm.tool_executor._clone_base()
    emptied: list[object] = []
    for name in [n for n in executor.tools_by_name if n not in names]:
        del executor.tools_by_name[name]
        ts = executor._tool_to_toolset.pop(name, None)
        if ts is not None:
            emptied.append(ts)
    remaining = {id(ts) for ts in executor._tool_to_toolset.values()}
    dropped = {id(ts) for ts in emptied} - remaining
    if dropped:
        executor.toolsets = [ts for ts in executor.toolsets if id(ts) not in dropped]
        executor.enabled_toolsets = [
            ts for ts in executor.enabled_toolsets if id(ts) not in dropped]
    clone = copy.copy(llm)
    clone.tool_executor = executor
    return clone


class _StreamDrain:
    """holmes `ToolCallingLLM.call_stream`을 직접 소비하는 `call()` 대체 (plans/154 W1 · M-2).

    holmes 0.36.0 `call()`은 `call_stream`을 끝까지 돌리는 래퍼라 step 상한 예외가 오르면 그때까지의
    도구 기록·누적 토큰(지역 변수)이 버려진다. 여기서는 `call()`의 조립(ANSWER_END의 `tool_call_id`
    기준 dedup · `num_llm_calls` 대입 · 라운드별 `costs` 누적 · APPROVAL_REQUIRED 처리)을 그대로
    따르되, 진행 중 기록을 인스턴스에 남겨 예외 뒤에도 `partial()`로 회수한다.
    정상 완주 결과는 `call()`과 필드 단위로 같다
    (`tests/test_plan154_w1_instrumentation.py` 동등성 테스트).
    sre_agent는 approval_callback을 쓰지 않으므로 `call(messages)` 기본 인자 경로만 옮긴다.

    `activity`(plans/187 W13)가 있으면 스트림 소비자 쪽에서만 실행 기록을 남긴다
    (holmes 내부 무수정):
    판단 회차 `llm_start`(라운드 시작·도구 묶음 종료 뒤) → `llm_end`(AI 메시지·도구 시작·종결 중 첫
    이벤트) · `START_TOOL` → `tool_start`(도구명만 옴) · `TOOL_RESULT` → `tool_end`(`description`
    한 줄 · `result.status` · 발췌) · `AI_MESSAGE` 본문 → `ai_note`. 없으면 기록 0(결과 조립 불변).
    `cache_hit`(G-26)이 있으면 잡 캐시에서 나온 도구 결과의 `tool_end`를 `status=cached`로 남긴다
    (플레이북 스텝의 캐시 적중 표시와 같다).

    `cancel_event`(plans/194 W3 · D-347)가 있으면 협조적 중단이다 — holmes `call_stream`에 그대로
    넘기고(holmes 0.36.0 실측: 턴 시작 전·LLM 응답 직후·도구 결과 사이에 확인해
    `LLMInterruptedError`), 스트림 이벤트마다 여기서도 확인해 같은 예외로 멈춘다. 진행 중인 LLM 요청
    1건(`completion` — holmes가 `LLM_REQUEST_TIMEOUT` 고정)과 이미 시작한 도구 호출은 끊지 못한다.
    없으면 종전 호출 그대로(인자 자체를 넘기지 않는다).

    `stall`(plans/198 I-10 · `stall_stop_enabled`)이 있으면 도구 결과마다 넣고, 도구 묶음이 끝난
    경계(다음 판단 회차 시작 직전)에서 정체면 `InvestigationStalledError`로 멈춘다.
    holmes 0.36.0 실측: `call_stream`은 시작할 때 `msgs`를 복사하고(1091) 진행 중 이벤트
    (`TOOL_RESULT`·`TOKEN_COUNT`)에 대화가 실리지 않아 실행 중 대화에 결론 지시를 넣을 공개
    통로가 없다 — 그래서 주입 대신 협조적으로 멈추고, 결론은
    dispatcher의 부분 브리핑(원장 기반 · 결정적)이 낸다. 없으면 판정 0(종전 그대로).

    턴 계측(plans/200 I-0)은 인스턴스 속성 `tool_batch_sizes`·`turns_n`에만 남긴다 — 결과
    조립(`LLMResult`)은 바뀌지 않는다(위 동등성 테스트 그대로).
    """

    def __init__(self, llm: ToolCallingLLM, activity: ActivityLog | None = None,
                 cache_hit: Callable[[str], bool] | None = None,
                 cancel_event: threading.Event | None = None,
                 stall: StallTracker | None = None) -> None:
        self._llm = llm
        self._cache_hit = cache_hit
        self._cancel = cancel_event
        self._stall = stall
        self.tool_calls: list[dict] = []
        self.num_llm_calls = 0
        # 끝난 스트림 라운드의 누적(`call()`의 accumulated_stats)
        self._stats = RequestStats()
        # 진행 중 라운드의 마지막 TOKEN_COUNT `costs`(그 라운드의 누적값)
        self._round_costs: dict = {}
        # 실행 기록(plans/187 W13) — 판단 회차 번호·열린 회차 시작 시각·도구 시작 시각(id별)
        self._activity = activity
        self._judge_round = 0
        self._judge_t0: float | None = None
        self._tool_t0: dict[str, float] = {}
        # 턴 계측(plans/200 I-0) — 도구를 요청한 턴의 도구 수(요청 순) · 시작한 LLM 턴 수.
        # 결과 조립(`LLMResult`)에는 넣지 않는다 — `ask()`가 `DiagnosisResult`에 옮긴다.
        self.tool_batch_sizes: list[int] = []
        self.turns_n = 0

    # ── 실행 기록 훅(plans/187 W13 — activity 없으면 무동작) ──

    def _judge_start(self) -> None:
        if self._activity is None:
            return
        self._judge_round += 1
        self._judge_t0 = time.monotonic()
        safe_append(self._activity, "llm_start", engine="holmes",
                    label=f"HolmesGPT 판단 {self._judge_round}회차")

    def _judge_end(self, status: str = "success") -> None:
        if self._activity is None or self._judge_t0 is None:
            return
        elapsed = int((time.monotonic() - self._judge_t0) * 1000)
        self._judge_t0 = None
        safe_append(self._activity, "llm_end", engine="holmes", status=status,
                    elapsed_ms=elapsed, label=f"HolmesGPT 판단 {self._judge_round}회차")

    def _record(self, event) -> None:  # noqa: ANN001 — holmes StreamMessage
        """스트림 이벤트 1건을 실행 기록으로 옮긴다 — best-effort(예외는 경고만 · 스트림 무영향)."""
        if self._activity is None:
            return
        try:
            self._record_event(event, self._activity)
        except Exception as exc:  # noqa: BLE001 — 기록 실패가 조사를 실패시키지 않는다
            logger.warning("실행 기록 실패(무시): %s: %s", type(exc).__name__, exc)

    def _record_event(self, event, log: ActivityLog) -> None:  # noqa: ANN001
        kind = event.event
        if kind in (StreamEvents.AI_MESSAGE, StreamEvents.START_TOOL, StreamEvents.ANSWER_END,
                    StreamEvents.APPROVAL_REQUIRED):
            self._judge_end()
        data = event.data if isinstance(event.data, dict) else {}
        if kind == StreamEvents.START_TOOL:
            name = str(data.get("tool_name") or "")
            if data.get("id"):
                self._tool_t0[str(data["id"])] = time.monotonic()
            log.append("tool_start", engine="holmes", tool=name or None, label=name or "도구")
        elif kind == StreamEvents.TOOL_RESULT:
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            status = result.get("status")
            status = str(getattr(status, "value", status) or "unknown")
            call_id = str(data.get("tool_call_id") or "")
            if self._cache_hit is not None and call_id and self._cache_hit(call_id):
                status = "cached"
            t0 = self._tool_t0.pop(call_id, None)
            name = str(data.get("tool_name") or data.get("name") or "")
            text = result.get("data") or result.get("error")
            log.append(
                "tool_end", engine="holmes", tool=name or None,
                label=str(data.get("description") or name or "도구"), status=status,
                elapsed_ms=int((time.monotonic() - t0) * 1000) if t0 is not None else None,
                excerpt=str(text) if text else None,
            )
        elif kind == StreamEvents.AI_MESSAGE:
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                log.append("ai_note", engine="holmes",
                           label=f"HolmesGPT {self._judge_round}회차 메모", note=content)

    def run(self, messages: list[dict]) -> LLMResult:
        """`call(messages)`와 같은 결과를 돌려준다. 예외는 그대로 전파한다."""
        tool_decisions = None
        tool_number_offset = 0
        cancel: dict[str, Any] = {"cancel_event": self._cancel} if self._cancel is not None else {}
        while True:
            self._check_cancelled()
            stream = self._llm.call_stream(
                msgs=messages,
                enable_tool_approval=False,
                tool_decisions=tool_decisions,
                tool_number_offset=tool_number_offset,
                iteration_offset=self.num_llm_calls,
                **cancel,
            )
            tool_decisions = None
            terminal_data = None
            terminal_event = None
            start_tool_count = 0
            saw_tool_results = False
            stream_steps = 0  # 이 스트림에서 끝난 도구 묶음 수(정체 중단 시 step 수)
            self.turns_n += 1
            self._judge_start()
            for event in stream:
                self._check_cancelled()
                # 표시 로그는 `call()`과 같다(도구 묶음이 끝나면 빈 줄).
                if saw_tool_results and event.event != StreamEvents.TOOL_RESULT:
                    display_logger.info("")
                    saw_tool_results = False
                    stream_steps += 1
                    self._check_stalled(self.num_llm_calls + stream_steps)
                    self.turns_n += 1
                    self._judge_start()  # 도구 묶음이 끝났다 — 다음 판단 회차
                self._record(event)
                if event.event == StreamEvents.START_TOOL:
                    start_tool_count += 1
                elif event.event == StreamEvents.TOOL_RESULT:
                    tool_number_offset += 1
                    saw_tool_results = True
                    self.tool_calls.append(event.data)
                    self._observe_stall(event.data)
                    if start_tool_count > 0:
                        display_logger.info(
                            f"The AI requested [bold]{start_tool_count}[/bold] tool call(s)."
                        )
                        # holmes는 한 턴의 START_TOOL을 모두 낸 뒤 결과를 낸다 — 첫 결과 시점의
                        # 개수가 그 턴의 도구 수다(plans/200 I-0).
                        self.tool_batch_sizes.append(start_tool_count)
                        start_tool_count = 0
                elif event.event == StreamEvents.TOKEN_COUNT:
                    # `call()`에 없는 유일한 분기 — 라운드 누적 비용을 붙든다(상한 예외 회수용).
                    costs = (event.data.get("metadata") or {}).get("costs")
                    if isinstance(costs, dict):
                        self._round_costs = dict(costs)
                elif event.event == StreamEvents.AI_MESSAGE:
                    reasoning = event.data.get("reasoning")
                    content = event.data.get("content")
                    if reasoning:
                        display_logger.info(
                            f"[italic dim]AI reasoning:\n\n{reasoning}[/italic dim]\n"
                        )
                    if content and content.strip():
                        display_logger.info(f"[bold {AI_COLOR}]AI:[/bold {AI_COLOR}] {content}")
                elif event.event in (StreamEvents.ANSWER_END, StreamEvents.APPROVAL_REQUIRED):
                    terminal_data = event.data
                    terminal_event = event.event
                    break

            if not terminal_data:
                raise Exception("Stream ended without ANSWER_END or APPROVAL_REQUIRED")

            # call_stream은 오프셋을 포함한 절대 턴 수를 준다 — 누적이 아니라 대입(`call()`과 같다).
            self.num_llm_calls = terminal_data.get("num_llm_calls", 0)
            self._stats += RequestStats(**terminal_data.get("costs", {}))
            self._round_costs = {}

            if terminal_event == StreamEvents.APPROVAL_REQUIRED:
                pending_frontend = terminal_data.get("pending_frontend_tool_calls", [])
                if pending_frontend:
                    logger.warning(
                        "Frontend tool calls requested but no frontend available in sync mode. "
                        f"Pending: {[fc['tool_name'] for fc in pending_frontend]}"
                    )
                    return LLMResult(
                        result=(
                            "Investigation paused: the AI requested frontend-defined tools "
                            "that cannot be executed in sync mode."
                        ),
                        tool_calls=self.tool_calls,  # type: ignore[arg-type]  # holmes call()과 같이 dict를 넘긴다
                        num_llm_calls=self.num_llm_calls,
                        messages=terminal_data.get("messages"),
                        metadata=terminal_data.get("metadata"),
                        finish_reason=(terminal_data.get("metadata") or {}).get("finish_reason"),
                        **self._stats.model_dump(),
                    )
                messages = terminal_data["messages"]
                tool_decisions = self._llm._prompt_for_approval_decisions(
                    terminal_data["pending_approvals"], None
                )
                continue

            return LLMResult(
                result=terminal_data["content"],
                tool_calls=self._deduped(),  # type: ignore[arg-type]
                num_llm_calls=self.num_llm_calls,
                messages=terminal_data["messages"],
                metadata=terminal_data.get("metadata"),
                finish_reason=(terminal_data.get("metadata") or {}).get("finish_reason"),
                **self._stats.model_dump(),
            )

    def _observe_stall(self, data: object) -> None:
        if self._stall is None or not isinstance(data, dict):
            return
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        status = result.get("status")
        self._stall.observe(str(data.get("tool_name") or data.get("name") or ""),
                            result.get("params"), str(data.get("description") or ""),
                            str(getattr(status, "value", status) or "unknown"))

    def _check_stalled(self, steps: int) -> None:
        """도구 묶음 경계의 정체 판정 — 정체면 `InvestigationStalledError`(가드 없으면 무동작)."""
        if self._stall is None:
            return
        reason = self._stall.end_step()
        if reason is not None:
            raise InvestigationStalledError(reason, steps)

    def _check_cancelled(self) -> None:
        """협조적 중단 확인 — 신호가 섰으면 holmes와 같은 `LLMInterruptedError`로 멈춘다."""
        if self._cancel is not None and self._cancel.is_set():
            raise LLMInterruptedError()

    def partial(self, steps: int | None) -> LLMResult:
        """예외 직전까지 회수한 도구 기록·누적 토큰으로 부분 결과를 만든다(답변 없음)."""
        stats = self._stats.model_copy()
        stats += RequestStats(**self._round_costs)
        return LLMResult(
            tool_calls=self._deduped(),  # type: ignore[arg-type]
            num_llm_calls=steps,
            **stats.model_dump(),
        )

    def _deduped(self) -> list[dict]:
        """같은 `tool_call_id`는 마지막 기록만 남긴다(holmes `call()`의 ANSWER_END dedup)."""
        deduped: dict = {}
        for tc in self.tool_calls:
            deduped[tc.get("tool_call_id", id(tc))] = tc
        return list(deduped.values())


def _turn_metrics(drain: _StreamDrain) -> dict[str, Any]:
    """드레인의 턴 계측 → `DiagnosisResult` 가산 필드(plans/200 I-0 — 사본).

    계측 속성이 없는 드레인 대역(테스트 가짜 등)이면 None — 종전 결과 그대로다.
    """
    sizes = getattr(drain, "tool_batch_sizes", None)
    return {"tool_batch_sizes": list(sizes) if sizes is not None else None,
            "turns_n": getattr(drain, "turns_n", None)}


def _steps_from_error(msg: str, fallback: int) -> int:
    """step 상한 예외 문구에서 도달 턴 수 i를 읽는다(형식이 다르면 상한값)."""
    m = _STEP_LIMIT_RE.search(msg)
    return int(m.group(1)) if m else fallback


def _apply_token_budget(llm, settings: AgentSettings) -> None:
    """설정된 토큰 예산을 holmes `DefaultLLM` 인스턴스에 적용한다 (미설정이면 아무것도 안 함).

    holmesgpt 0.36.0 실측 경로:
    - 컨텍스트 창: `get_context_window_size()`가 인스턴스 속성 `max_context_size`를 가장 먼저 본다
      (모델 목록 `custom_args.max_context_size`가 채우는 바로 그 속성).
    - 출력 상한: `get_maximum_output_token()`은 프로세스 env 또는 litellm 모델표만 보므로 인스턴스에서
      대체한다. 이 한 값이 요청 `max_tokens`(completion의 setdefault)와 압축·초과 판정의 출력 예약분에
      함께 쓰여, 둘이 어긋나지 않는다. litellm 전역 모델표 등록(`litellm.register_model`)은 프로세스
      전역 변경이고 holmes env에 밀려 필드가 조용히 무시될 수 있어 쓰지 않는다.
    """
    if settings.override_max_content_size is not None:
        llm.max_context_size = settings.override_max_content_size
    if settings.override_max_output_token is not None:
        limit = settings.override_max_output_token
        llm.get_maximum_output_token = lambda: limit


def _with_load_guard_note(additions: str | None) -> str:
    """조사 지침에 **부하 가드 안내를 항상** 덧붙인다 (Plan 78 W2-6 · `docs/25`).

    **왜 기본 주입인가**: 부하 가드의 실효 강제는 bash allowlist다 — 무거운 명령이
    `timeout N nice -n P …` 형태로만 등록돼 있어 가드 없는 형태는 거부된다. 그런데
    `system_prompt_additions`를 넘기는 **프로덕션 호출부가 0건**이었으므로(2026-08-27 실측),
    안내 없이 강제만 걸리면 무거운 명령이 전부 거부되어 **조사가 무력화된다**.
    강제와 안내는 한 세트여야 한다.

    호출자가 준 지침은 **보존**하고 뒤에 덧붙인다(덮어쓰지 않는다).

    Args:
        additions: 호출자가 준 system_prompt_additions (없으면 None)

    Returns:
        부하 가드 안내가 포함된 지침 문자열
    """
    if additions and LOAD_GUARD_NOTE in additions:
        return additions          # 이미 포함(예: MIDDLEWARE_FOCUS_NOTE) — 중복 주입하지 않는다
    return f"{additions}\n\n{LOAD_GUARD_NOTE}" if additions else LOAD_GUARD_NOTE


class DiagnosisAgent:
    """HolmesGPT ToolCallingLLM을 감싼 장애 진단 에이전트.

    LLM 생성은 지연 초기화한다 — create_toolcalling_llm()이 toolset
    prerequisite 검사(kubectl 등)를 수행하므로 생성 비용이 크다.
    """

    def __init__(
        self,
        settings: AgentSettings | None = None,
        toolsets: dict[str, dict] | None = None,
        mcp_servers: dict[str, dict] | None = None,
    ):
        """toolsets 미지정 시 VM 진단 프로파일(vm_profile)을 기본 적용한다.

        mcp_servers는 holmes Config.mcp_servers 인자로 그대로 전달한다(실측:
        holmes 0.36.0 Config는 mcp_servers: Optional[dict[str, dict[str, Any]]]를
        받아 각 항목을 type=mcp로 스탬프해 RemoteMCPToolset으로 등록한다 —
        Plan 06 §6, toolsets dict가 아닌 별도 Config 인자). 원격 VM 진단 시
        remote_vm_profile()을 toolsets로, 폴스타/PromQL MCP를 mcp_servers로 명시
        선택한다. 미지정 시 기존 로컬 VM 경로(vm_profile · mcp_servers 없음)를
        그대로 유지한다.
        """
        self.settings = settings or AgentSettings()
        # D-213 후속: holmes 압축 산출물이 Qwen 템플릿(system은 맨 앞만)과 충돌해 조사가
        # 전건 실패하는 것을 막는다. 교정할 것이 없으면 no-op이라 정상 경로는 비트 동일.
        if self.settings.system_message_position_fix:
            install_system_message_guard()
        # plans/188 C8: vLLM 파서 불일치로 content에 실린 Gemma 도구 호출 표기를
        # tool_calls로 바꾼다.
        # 마크업이 없으면 no-op · 해석 실패면 원본 그대로(W4 방어가 미결로 종결).
        if self.settings.tool_call_markup_parse_enabled:
            install_tool_call_markup_guard()
        # bash를 켜는 프로파일(로컬 vm_profile·middleware_profile)이 파일을 만들지 못하게 한다(D-235).
        # 프로파일별 분기 없이 여기 한 곳에서 건다 — 단일 출처. 쓰기 형태가 없으면 no-op이다.
        if self.settings.bash_write_guard_enabled:
            install_bash_write_guard()
        self._config = Config(
            model=self.settings.model,
            api_key=self.settings.api_key,
            # 사내 OpenAI 호환 엔드포인트(vLLM 등). None이면 프로바이더 기본 경로.
            api_base=self.settings.api_base,
            max_steps=self.settings.max_steps,
            toolsets=toolsets if toolsets is not None else vm_profile(),
            mcp_servers=mcp_servers,
        )
        self._llm: ToolCallingLLM | None = None

    @property
    def llm(self) -> ToolCallingLLM:
        if self._llm is None:
            self._llm = self._config.create_toolcalling_llm(
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
                # 활성 정본 = 넘긴 프로파일(toolsets dict) + holmes 내장 기본값 (2026-09-17 실측).
                # holmes 기본값(캐시 ENABLED · enable_all=True)은 ① `~/.holmes/toolsets_status.json`이
                # 있으면 그 enabled로 프로파일을 덮고(`enabled: False`가 무시됨) ② 캐시가 없으면 호스트에
                # 깔린 CLI(kubectl·docker·helm…)의 toolset까지 켠다. 조사 도구가 호스트 상태에 따라
                # 달라지므로 둘 다 끈다. 비용: 기본 toolset 선행검사는 config 파싱뿐이라 생성 시간 차이
                # 없음(0.17s 대 0.17s 실측) · MCP 서버는 캐시 모드에서도 매번 검사한다.
                enable_all_toolsets_possible=False,
                prerequisite_cache=PrerequisiteCacheMode.DISABLED,
            )
            _apply_token_budget(self._llm.llm, self.settings)
        return self._llm

    def mcp_unavailable(self) -> dict[str, str]:
        """FAILED 상태인 MCP 툴셋의 이름→사유(plans/188 C1). 전부 정상이면 빈 dict.

        holmes 0.36.0 실측: MCP 툴셋은 `create_toolcalling_llm` 안에서 **즉시**(지연 아님)
        선행검사돼, 서버에 연결이 안 되면 생성 직후 `status=FAILED`·`error`가 찍히고 도구가 0개다.
        한 번 FAILED면 같은 `ToolCallingLLM` 안에서는 다시 검사하지 않는다
        — 회복하려면 에이전트를 새로 만든다.
        첫 호출이면 LLM(툴셋 검사 포함)을 생성한다. 사유는 `clean_toolset_error`로 정리한다.
        """
        return {
            ts.name: clean_toolset_error(ts.error)
            for ts in self.llm.tool_executor.toolsets
            if ts.type == ToolsetType.MCP and ts.status == ToolsetStatusEnum.FAILED
        }

    def tool_names(self) -> frozenset[str]:
        """조사 LLM에 실제로 노출되는 도구 이름(plans/188 C2 — 지침 도구 이름 필터 입력)."""
        return frozenset(self.llm.tool_executor.tools_by_name)

    def ask(self, question: str, system_prompt_additions: str | None = None,
            activity: ActivityLog | None = None,
            tool_cache: ToolResultCache | None = None,
            cancel_event: threading.Event | None = None,
            tool_allowlist: Collection[str] | None = None) -> DiagnosisResult:
        """자연어 질문으로 장애 진단을 수행한다.

        holmes는 max_steps 상한 도달 시 전용 클래스 없이 plain `Exception("Too many LLM
        calls - exceeded max_steps: i/N")`을 던진다(실측). 하드 실패로 전파하지 않고
        **구조화 미완주 결과**(`incomplete=True`)로 graceful 반환한다 — Plan 02 §12-④
        (침묵 실패 금지·부분/사유 전달). 결정적 가드: 상한 자체는 escalate 신호가 아니다.
        그 외 예외는 그대로 전파(진짜 오류는 dispatcher가 failed로 확정).

        plans/154 W1: holmes를 `call_stream` 직접 소비(`_StreamDrain`)로 부른다 — 상한 예외에서도
        그때까지의 도구 기록·토큰·step 수를 미완주 결과에 싣는다(답변 문구는 종전 그대로).
        상한 턴에서 예외 없이 결론을 낸 경우(`steps == max_steps`)는 미완주로 바꾸지 않는다.

        `tool_cache`(G-26 · 선택)는 플레이북 모드 잡의 잡 단위 캐시다 — 주면 이 조사의 MCP 도구
        호출이 플레이북과 같은 캐시를 거친다(`_CachedToolInvoke`). 없으면 종전 호출 그대로.

        `cancel_event`(plans/194 W3 · 선택)는 dispatcher의 협조적 중단 신호다 — 서면 다음 확인
        지점에서 `LLMInterruptedError`로 멈춘다(`_StreamDrain`). 결과는 버려진 잡이라 쓰이지 않는다.

        `stall_stop_enabled`(plans/198 I-10)면 정체(같은 도구·정규화 인자 반복 · 새 근거 없는 연속
        step)에서 멈추고 그때까지의 기록으로 미완주 결과(`stalled` = 사유)를 돌려준다.

        `tool_allowlist`(plans/198 I-9 · 선택)를 주면 이 조사에만 도구를 그 이름들로 좁힌다
        (`_with_tool_allowlist`). None이면 종전 호출 그대로.
        """
        base = (self.llm if tool_allowlist is None
                else _with_tool_allowlist(self.llm, tool_allowlist))
        messages = build_initial_ask_messages(
            initial_user_prompt=question,
            file_paths=None,
            tool_executor=base.tool_executor,
            system_prompt_additions=_with_load_guard_note(system_prompt_additions),
        )
        # 협조적 중단 신호가 없으면 인자를 넘기지 않는다(종전 호출 그대로).
        drain_kw: dict[str, Any] = (
            {"cancel_event": cancel_event} if cancel_event is not None else {})
        if getattr(self.settings, "stall_stop_enabled", False):
            # plans/198 I-10 — off면 인자를 넘기지 않는다(종전 호출 그대로). getattr: 설정 대역
            # (필드 일부만 가진 객체)을 쓰는 호출자도 종전처럼 돈다.
            # 사유의 인자 표기는 실행 기록과 같은 가림을 거친다(사유가 답변·브리핑·감사로 나간다).
            drain_kw["stall"] = StallTracker(self.settings.stall_repeat_threshold,
                                             self.settings.stall_idle_steps,
                                             masker=mask_activity_text)
        if tool_cache is None:
            drain = _StreamDrain(base, activity=activity, **drain_kw)
        else:
            llm, invoke = _with_tool_cache(base, tool_cache)
            drain = _StreamDrain(llm, activity=activity, cache_hit=invoke.is_hit, **drain_kw)
        try:
            result = drain.run(messages)
        except InvestigationStalledError as stalled:
            partial = drain.partial(stalled.steps)
            return DiagnosisResult(
                answer=(
                    f"[미완주] 조사가 정체돼 중단했습니다({stalled.reason}). 수집된 근거가 "
                    f"불충분하므로 가설/한계로 취급합니다."
                ),
                tool_calls=[tc.description for tc in (partial.tool_calls or [])],
                tool_outputs=to_tool_records(partial.tool_calls),
                total_tokens=partial.total_tokens,
                total_cost=partial.total_cost,
                incomplete=True,
                steps=partial.num_llm_calls,
                stalled=stalled.reason,
                **_turn_metrics(drain),
            )
        except Exception as exc:  # noqa: BLE001 — holmes는 전용 예외 클래스 없이 plain Exception
            drain._judge_end("error")  # 열린 판단 회차가 있으면 실패로 닫는다(기록 없으면 무동작)
            msg = str(exc)
            if "max_steps" in msg or "Too many LLM calls" in msg:
                partial = drain.partial(_steps_from_error(msg, self.settings.max_steps))
                return DiagnosisResult(
                    answer=(
                        f"[미완주] 조사가 step 상한({self.settings.max_steps})에 도달해 "
                        f"결론을 도출하지 못했습니다({msg}). 수집된 근거가 불충분하므로 "
                        f"가설/한계로 취급합니다."
                    ),
                    tool_calls=[tc.description for tc in (partial.tool_calls or [])],
                    tool_outputs=to_tool_records(partial.tool_calls),
                    total_tokens=partial.total_tokens,
                    total_cost=partial.total_cost,
                    incomplete=True,
                    steps=partial.num_llm_calls,
                    **_turn_metrics(drain),
                )
            raise
        return DiagnosisResult(
            answer=result.result or "",
            tool_calls=[tc.description for tc in (result.tool_calls or [])],
            tool_outputs=to_tool_records(result.tool_calls),
            total_tokens=result.total_tokens,
            total_cost=result.total_cost,
            steps=result.num_llm_calls,
            **_turn_metrics(drain),
        )
