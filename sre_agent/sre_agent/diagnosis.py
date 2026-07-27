"""HolmesGPT SDK 래퍼 — 장애 진단 에이전트의 핵심 진입점.

holmesgpt 0.36.0 실측 API 기준:
- Config(model, api_key, max_steps, ...) → create_toolcalling_llm() → ToolCallingLLM
- build_initial_ask_messages()로 시스템/유저 프롬프트 조립 후 llm.call(messages)
"""

from dataclasses import dataclass, field

from holmes.config import Config
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import ToolsetTag

from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import vm_profile


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
        else:
            status, output, error, return_code = "unknown", "", None, None
        records.append(
            ToolCallRecord(
                tool_name=getattr(tc, "tool_name", ""),
                description=getattr(tc, "description", ""),
                status=str(status) if status is not None else "unknown",
                output=output or "",
                error=error,
                return_code=return_code,
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
    """

    tool_name: str
    description: str
    status: str  # StructuredToolResultStatus.value ("success"|"error"|"no_data"|...)
    output: str  # StructuredToolResult.get_stringified_data() — 원시 출력 문자열
    error: str | None = None
    return_code: int | None = None


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
        self._config = Config(
            model=self.settings.model,
            api_key=self.settings.api_key,
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
            )
        return self._llm

    def ask(self, question: str, system_prompt_additions: str | None = None) -> DiagnosisResult:
        """자연어 질문으로 장애 진단을 수행한다."""
        messages = build_initial_ask_messages(
            initial_user_prompt=question,
            file_paths=None,
            tool_executor=self.llm.tool_executor,
            system_prompt_additions=system_prompt_additions,
        )
        result = self.llm.call(messages)
        return DiagnosisResult(
            answer=result.result or "",
            tool_calls=[tc.description for tc in (result.tool_calls or [])],
            tool_outputs=to_tool_records(result.tool_calls),
            total_tokens=result.total_tokens,
            total_cost=result.total_cost,
        )
