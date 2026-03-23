"""Ollama HTTP 클라이언트.

폐쇄망 환경에서 Ollama 서버와 통신하는 LangChain 호환 클라이언트.
NWAgent의 ollama_client.py를 기반으로 collectorinfra에 맞게 적용.
"""

import json
import os
import requests
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.callbacks.manager import AsyncCallbackManagerForChainRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class LLMAPIClient(BaseChatModel):
    """HTTP 방식으로 Ollama 서버를 호출하는 LangChain 호환 클라이언트."""

    api_endpoint: str = "/api/chat"
    base_url: str = "http://localhost:11434"
    chat_model: str = "llama3.1:8b"
    embedding_model: str = "mxbai-embed-large"
    api_key: Optional[str] = None
    timeout: int = 180
    temperature: float = 0.0

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        api_endpoint: str = "/api/chat",
        base_url: str = "http://localhost:11434",
        chat_model: str = "llama3.1:8b",
        embedding_model: str = "mxbai-embed-large",
        temperature: float = 0.0,
        api_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 180,
        **kwargs,
    ):
        final_api_key = api_key or os.getenv("LLM_API_KEY")
        final_base_url = os.getenv("LLM_OLLAMA_BASE_URL", base_url)

        super().__init__(
            api_endpoint=api_endpoint,
            base_url=final_base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            api_key=final_api_key,
            timeout=timeout,
            temperature=temperature,
            **kwargs,
        )

        self._headers = {"Content-Type": "application/json", **(headers or {})}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"

    @property
    def _llm_type(self) -> str:
        return "ollama_api_client"

    def _convert_messages_to_ollama_format(
        self, messages: List[BaseMessage]
    ) -> List[Dict[str, Any]]:
        ollama_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                ollama_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                ollama_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                ai_dict: Dict[str, Any] = {"role": "assistant", "content": msg.content}
                if getattr(msg, "tool_calls", None):
                    ai_dict["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": tc.get("args", {}),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                ollama_messages.append(ai_dict)
            elif isinstance(msg, ToolMessage):
                tool_message: Dict[str, Any] = {
                    "role": "tool",
                    "content": str(msg.content),
                }
                tool_call_id = getattr(msg, "tool_call_id", None)
                if tool_call_id is not None:
                    tool_message["tool_call_id"] = tool_call_id
                ollama_messages.append(tool_message)
            else:
                ollama_messages.append({"role": "user", "content": str(msg.content)})
        return ollama_messages

    def _get_chat_endpoint(self) -> str:
        if self.api_endpoint.startswith("http"):
            return self.api_endpoint
        return f"{self.base_url}{self.api_endpoint}"

    def _format_tools_for_payload(self) -> Optional[List[Dict[str, Any]]]:
        if not (hasattr(self, "_bound_tools") and self._bound_tools):
            return None

        formatted_tools = []
        for tool_obj in self._bound_tools:
            if hasattr(tool_obj, "name") and hasattr(tool_obj, "description"):
                name = tool_obj.name
                desc = tool_obj.description
            elif isinstance(tool_obj, dict):
                if "function" in tool_obj:
                    name = tool_obj["function"].get("name", "Tool")
                    desc = tool_obj["function"].get("description", "")
                else:
                    name = tool_obj.get("name", "Tool")
                    desc = tool_obj.get("description", "")
            else:
                name = getattr(tool_obj, "name", "Tool")
                desc = getattr(tool_obj, "description", "")

            tool_schema: Dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": self._extract_tool_parameters(tool_obj),
                },
            }
            formatted_tools.append(tool_schema)
        return formatted_tools

    def _extract_tool_parameters(self, tool_obj: Any) -> Dict[str, Any]:
        if isinstance(tool_obj, type) and hasattr(tool_obj, "model_json_schema"):
            return tool_obj.model_json_schema()
        if isinstance(tool_obj, dict):
            if "function" in tool_obj and "parameters" in tool_obj["function"]:
                return tool_obj["function"]["parameters"]
            if "parameters" in tool_obj:
                return tool_obj["parameters"]
        if hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
            if hasattr(tool_obj.args_schema, "model_json_schema"):
                return tool_obj.args_schema.model_json_schema()
        if hasattr(tool_obj, "input_schema"):
            try:
                input_schema = tool_obj.input_schema
                if hasattr(input_schema, "model_json_schema"):
                    return input_schema.model_json_schema()
            except Exception:
                pass
        return {"type": "object", "properties": {}}

    def _build_payload(
        self, ollama_messages: List[Dict[str, Any]], **kwargs: Any
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.chat_model,
            "messages": ollama_messages,
            "temperature": self.temperature,
            "stream": False,
            **kwargs,
        }

        formatted_tools = self._format_tools_for_payload()
        if formatted_tools:
            payload["tools"] = formatted_tools
            payload["tool_choice"] = "auto"

        try:
            json.dumps(payload)
        except (TypeError, ValueError):
            payload = {
                "model": self.chat_model,
                "messages": ollama_messages,
                "temperature": self.temperature,
                "stream": False,
            }
        return payload

    def _get_request_headers(self) -> Dict[str, str]:
        headers = self._headers.copy() if hasattr(self, "_headers") else {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _parse_response(self, result: Any) -> AIMessage:
        content, tool_calls = self._extract_content_and_tool_calls(result)
        lc_tool_calls = self._convert_tool_calls_to_langchain_format(tool_calls)
        if lc_tool_calls:
            return AIMessage(content=content or "", tool_calls=lc_tool_calls)
        return AIMessage(content=content or "")

    def _extract_content_and_tool_calls(
        self, result: Any
    ) -> Tuple[Optional[str], List[Any]]:
        content = None
        tool_calls: List[Any] = []
        if isinstance(result, dict):
            if "message" in result:
                message = result["message"]
                if isinstance(message, dict):
                    content = message.get("content") or message.get("text")
                    tool_calls = message.get("tool_calls", [])
                else:
                    content = str(message)
            else:
                content = result.get("response") or result.get("content")
                if content is None:
                    for value in result.values():
                        if isinstance(value, str):
                            content = value
                            break
                    if content is None:
                        content = str(result)
                tool_calls = result.get("tool_calls", [])
        elif isinstance(result, str):
            content = result
        return content, tool_calls

    def _convert_tool_calls_to_langchain_format(
        self, tool_calls: List[Any]
    ) -> List[Dict[str, Any]]:
        lc_tool_calls = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function", {})
            if not function:
                continue
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    args_dict = json.loads(arguments)
                except json.JSONDecodeError:
                    args_dict = {}
            else:
                args_dict = arguments
            lc_tool_calls.append(
                {
                    "name": function.get("name", ""),
                    "args": args_dict,
                    "id": tc.get("id", f"call_{len(lc_tool_calls)}"),
                }
            )
        return lc_tool_calls

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForChainRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        ollama_messages = self._convert_messages_to_ollama_format(messages)
        chat_endpoint = self._get_chat_endpoint()
        payload = self._build_payload(ollama_messages, **kwargs)
        headers = self._get_request_headers()

        try:
            response = requests.post(
                chat_endpoint, json=payload, headers=headers, timeout=self.timeout
            )
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                detail = response.text if hasattr(response, "text") else ""
                raise requests.exceptions.HTTPError(
                    f"{e} | body: {detail}"
                ) from e

            result = response.json()
            response_message = self._parse_response(result)
            return ChatResult(generations=[ChatGeneration(message=response_message)])

        except requests.exceptions.RequestException as e:
            raise ValueError(f"Ollama API 호출 실패: {str(e)}")

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "LLMAPIClient":
        new_instance = LLMAPIClient(
            api_endpoint=self.api_endpoint,
            base_url=self.base_url,
            chat_model=self.chat_model,
            embedding_model=self.embedding_model,
            temperature=self.temperature,
            api_key=self.api_key,
            headers=self._headers.copy() if hasattr(self, "_headers") else None,
            timeout=self.timeout,
        )
        new_instance._bound_tools = tools
        new_instance._tool_kwargs = kwargs
        return new_instance
