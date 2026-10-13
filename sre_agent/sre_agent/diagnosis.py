"""HolmesGPT SDK 래퍼 — 장애 진단 에이전트의 핵심 진입점.

holmesgpt 0.36.0 실측 API 기준:
- Config(model, api_key, max_steps, ...) → create_toolcalling_llm() → ToolCallingLLM
- build_initial_ask_messages()로 시스템/유저 프롬프트 조립 후 `call_stream`을 직접 소비한다
  (`_StreamDrain` — 결과 조립은 holmes `ToolCallingLLM.call()`과 같다 · plans/154 W1)
"""

import logging
import re
from dataclasses import dataclass, field

from holmes.config import Config
from holmes.core.llm_usage import RequestStats
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.utils.colors import AI_COLOR
from holmes.utils.stream import StreamEvents

from sre_agent.infrastructure.bash_write_guard import install_bash_write_guard
from sre_agent.infrastructure.llm_message_guard import install_system_message_guard
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import LOAD_GUARD_NOTE, vm_profile

# holmes `call()`이 쓰는 표시 로거(같은 이름) — 직접 소비로 바꿔도 콘솔 진행 표시를 유지한다.
display_logger = logging.getLogger("holmes.display.tool_calling_llm")
logger = logging.getLogger(__name__)

# holmes step 상한 예외 문구 `Too many LLM calls - exceeded max_steps: i/N`의 i.
_STEP_LIMIT_RE = re.compile(r"max_steps:\s*(\d+)\s*/")


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


class _StreamDrain:
    """holmes `ToolCallingLLM.call_stream`을 직접 소비하는 `call()` 대체 (plans/154 W1 · M-2).

    holmes 0.36.0 `call()`은 `call_stream`을 끝까지 돌리는 래퍼라 step 상한 예외가 오르면 그때까지의
    도구 기록·누적 토큰(지역 변수)이 버려진다. 여기서는 `call()`의 조립(ANSWER_END의 `tool_call_id`
    기준 dedup · `num_llm_calls` 대입 · 라운드별 `costs` 누적 · APPROVAL_REQUIRED 처리)을 그대로
    따르되, 진행 중 기록을 인스턴스에 남겨 예외 뒤에도 `partial()`로 회수한다.
    정상 완주 결과는 `call()`과 필드 단위로 같다
    (`tests/test_plan154_w1_instrumentation.py` 동등성 테스트).
    sre_agent는 approval_callback을 쓰지 않으므로 `call(messages)` 기본 인자 경로만 옮긴다.
    """

    def __init__(self, llm: ToolCallingLLM) -> None:
        self._llm = llm
        self.tool_calls: list[dict] = []
        self.num_llm_calls = 0
        # 끝난 스트림 라운드의 누적(`call()`의 accumulated_stats)
        self._stats = RequestStats()
        # 진행 중 라운드의 마지막 TOKEN_COUNT `costs`(그 라운드의 누적값)
        self._round_costs: dict = {}

    def run(self, messages: list[dict]) -> LLMResult:
        """`call(messages)`와 같은 결과를 돌려준다. 예외는 그대로 전파한다."""
        tool_decisions = None
        tool_number_offset = 0
        while True:
            stream = self._llm.call_stream(
                msgs=messages,
                enable_tool_approval=False,
                tool_decisions=tool_decisions,
                tool_number_offset=tool_number_offset,
                iteration_offset=self.num_llm_calls,
            )
            tool_decisions = None
            terminal_data = None
            terminal_event = None
            start_tool_count = 0
            saw_tool_results = False
            for event in stream:
                # 표시 로그는 `call()`과 같다(도구 묶음이 끝나면 빈 줄).
                if saw_tool_results and event.event != StreamEvents.TOOL_RESULT:
                    display_logger.info("")
                    saw_tool_results = False
                if event.event == StreamEvents.START_TOOL:
                    start_tool_count += 1
                elif event.event == StreamEvents.TOOL_RESULT:
                    tool_number_offset += 1
                    saw_tool_results = True
                    self.tool_calls.append(event.data)
                    if start_tool_count > 0:
                        display_logger.info(
                            f"The AI requested [bold]{start_tool_count}[/bold] tool call(s)."
                        )
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

    def ask(self, question: str, system_prompt_additions: str | None = None) -> DiagnosisResult:
        """자연어 질문으로 장애 진단을 수행한다.

        holmes는 max_steps 상한 도달 시 전용 클래스 없이 plain `Exception("Too many LLM
        calls - exceeded max_steps: i/N")`을 던진다(실측). 하드 실패로 전파하지 않고
        **구조화 미완주 결과**(`incomplete=True`)로 graceful 반환한다 — Plan 02 §12-④
        (침묵 실패 금지·부분/사유 전달). 결정적 가드: 상한 자체는 escalate 신호가 아니다.
        그 외 예외는 그대로 전파(진짜 오류는 dispatcher가 failed로 확정).

        plans/154 W1: holmes를 `call_stream` 직접 소비(`_StreamDrain`)로 부른다 — 상한 예외에서도
        그때까지의 도구 기록·토큰·step 수를 미완주 결과에 싣는다(답변 문구는 종전 그대로).
        상한 턴에서 예외 없이 결론을 낸 경우(`steps == max_steps`)는 미완주로 바꾸지 않는다.
        """
        messages = build_initial_ask_messages(
            initial_user_prompt=question,
            file_paths=None,
            tool_executor=self.llm.tool_executor,
            system_prompt_additions=_with_load_guard_note(system_prompt_additions),
        )
        drain = _StreamDrain(self.llm)
        try:
            result = drain.run(messages)
        except Exception as exc:  # noqa: BLE001 — holmes는 전용 예외 클래스 없이 plain Exception
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
                )
            raise
        return DiagnosisResult(
            answer=result.result or "",
            tool_calls=[tc.description for tc in (result.tool_calls or [])],
            tool_outputs=to_tool_records(result.tool_calls),
            total_tokens=result.total_tokens,
            total_cost=result.total_cost,
            steps=result.num_llm_calls,
        )
