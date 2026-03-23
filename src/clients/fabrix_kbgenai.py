"""FabriX KBGenAI 채팅 클라이언트.

삼성 SDS FabriX의 KBGenAI REST API를 호출하는 LangChain 호환 클라이언트.
NWAgent의 llm_chat_connector.py를 기반으로 collectorinfra에 맞게 적용.
"""

import json

import httpx
import requests
import urllib3
from typing import Any, AsyncIterator, Iterator, List, Optional

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

LLAMA_JUNK_TOKENS = [
    "<|eot_id|>",
    "<|end_header_id|>",
    "<|eom_id|>",
    "<|start_header_id|>assistant",
]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class KBGenAIChat(BaseChatModel):
    """FabriX KBGenAI REST API 클라이언트."""

    endpoint_url: str
    x_openapi_token: str
    x_generative_ai_client: str
    asset_id: str
    kb_id: str = "User"
    tool_registry: dict = {}
    system_prompt: str = ""
    timeout: int = 300

    @property
    def _llm_type(self) -> str:
        return "kb-gen-ai-chat"

    def _convert_messages_to_prompts(self, messages: List[BaseMessage]) -> List[str]:
        prompts = []
        for m in messages:
            if isinstance(m, (HumanMessage, AIMessage, SystemMessage)):
                prompts.append(m.content)
            elif isinstance(m, str):
                prompts.append(m)
            else:
                prompts.append(str(m.content))
        return prompts

    def remove_llm_junk(self, text: str, strip: bool = True) -> str:
        for t in LLAMA_JUNK_TOKENS:
            text = text.replace(t, "")
        return text.strip() if strip else text

    def _get_headers(self) -> dict:
        return {
            "x-openapi-token": f"Bearer {self.x_openapi_token}",
            "x-generative-ai-client": self.x_generative_ai_client,
            "Content-Type": "application/json",
        }

    def _get_payload(
        self, messages: List[BaseMessage], is_stream: bool = False
    ) -> dict:
        return {
            "modelId": self.asset_id,
            "contents": self._convert_messages_to_prompts(messages),
            "isStream": is_stream,
            "isRagOn": False,
            "executeRagFinalAnswer": False,
            "executeRagStandaloneQuery": False,
            "systemPrompt": self.system_prompt,
        }

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = requests.post(
            self.endpoint_url,
            json=self._get_payload(messages),
            headers=self._get_headers(),
            timeout=self.timeout,
            verify=False,
        )
        response.raise_for_status()
        result = response.json()

        if result.get("status") != "SUCCESS":
            raise ValueError(f"API returned error status: {result.get('status')}")

        result_text = self.remove_llm_junk(result.get("content", ""))
        message = AIMessage(content=result_text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                self.endpoint_url,
                json=self._get_payload(messages),
                headers=self._get_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("status") != "SUCCESS":
                raise ValueError(
                    f"API returned error status: {result.get('status')}"
                )

            result_text = self.remove_llm_junk(result.get("content", ""))

        message = AIMessage(content=result_text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        response = requests.post(
            self.endpoint_url,
            json=self._get_payload(messages, is_stream=True),
            headers=self._get_headers(),
            timeout=self.timeout,
            verify=False,
            stream=True,
        )
        response.raise_for_status()

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                line = line[len("data: ") :].strip()
            if not line or line == "[DONE]":
                continue

            try:
                line_json = json.loads(line)
                content = line_json.get("content", "")
                event_status = line_json.get("event_status", "")

                if event_status in ["STATUS", "SYNC", "FINISH"]:
                    continue

                clean_content = self.remove_llm_junk(content, strip=False)
                if clean_content:
                    chunk = ChatGenerationChunk(
                        message=AIMessageChunk(content=clean_content)
                    )
                    if run_manager:
                        run_manager.on_llm_new_token(clean_content, chunk=chunk)
                    yield chunk
            except json.JSONDecodeError:
                continue

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        async with httpx.AsyncClient(verify=False) as client:
            async with client.stream(
                "POST",
                self.endpoint_url,
                json=self._get_payload(messages, is_stream=True),
                headers=self._get_headers(),
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[len("data: ") :].strip()
                    if not line or line == "[DONE]":
                        continue

                    try:
                        line_json = json.loads(line)
                        content = line_json.get("content", "")
                        event_status = line_json.get("event_status", "")

                        if event_status in ["STATUS", "SYNC", "FINISH"]:
                            continue

                        clean_content = self.remove_llm_junk(content, strip=False)
                        if clean_content:
                            chunk = ChatGenerationChunk(
                                message=AIMessageChunk(content=clean_content)
                            )
                            if run_manager:
                                await run_manager.on_llm_new_token(
                                    clean_content, chunk=chunk
                                )
                            yield chunk
                    except json.JSONDecodeError:
                        continue

    def bind_tools(self, tools, tool_choice="auto") -> "KBGenAIChat":
        for t in tools:
            self.tool_registry[t.name] = t
        return self.model_copy(
            update={"tool_registry": self.tool_registry}
        )
