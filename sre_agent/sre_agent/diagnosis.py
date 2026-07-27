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


@dataclass
class DiagnosisResult:
    """진단 결과 요약. answer가 비면 실패 사유를 tool_calls/raw로 확인한다."""

    answer: str
    tool_calls: list[str] = field(default_factory=list)
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
    ):
        """toolsets 미지정 시 VM 진단 프로파일(vm_profile)을 기본 적용한다."""
        self.settings = settings or AgentSettings()
        self._config = Config(
            model=self.settings.model,
            api_key=self.settings.api_key,
            max_steps=self.settings.max_steps,
            toolsets=toolsets if toolsets is not None else vm_profile(),
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
            total_tokens=result.total_tokens,
            total_cost=result.total_cost,
        )
