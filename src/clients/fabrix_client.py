"""FabriX OpenAI 호환 REST API 클라이언트.

삼성 SDS FabriX의 /v1/chat/completions 엔드포인트를 호출하는 LangChain 호환 클라이언트.
NWAgent의 fabrix_client.py를 기반으로 collectorinfra에 맞게 적용.
"""

import json
import os
import requests
from typing import Any, Dict, List, Optional

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


class FabriXAPIClient(BaseChatModel):
    """FabriX OpenAI 호환 REST API 클라이언트.

    /v1/chat/completions 형식으로 FabriX 서버를 호출한다.
    Tool calling은 Few-shot 프롬프트 기반으로 처리한다.
    """

    base_url: str = "http://localhost:8002/v1"
    chat_model: str = "fabrix-chat-model"
    api_key: Optional[str] = None
    timeout: int = 120
    temperature: float = 0.0

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        base_url: str = "http://localhost:8002/v1",
        chat_model: str = "fabrix-chat-model",
        temperature: float = 0.0,
        api_key: Optional[str] = None,
        timeout: int = 120,
        **kwargs,
    ):
        final_base_url = base_url or os.getenv("FABRIX_BASE_URL", "http://localhost:8002/v1")
        final_api_key = api_key or os.getenv("FABRIX_API_KEY")
        final_model = chat_model or os.getenv("FABRIX_CHAT_MODEL", "fabrix-chat-model")

        super().__init__(
            base_url=final_base_url,
            chat_model=final_model,
            api_key=final_api_key,
            timeout=timeout,
            temperature=temperature,
            **kwargs,
        )

        self._headers = {"Content-Type": "application/json"}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"

    @property
    def _llm_type(self) -> str:
        return "fabrix_api_client"

    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                content = msg.content or ""
                if getattr(msg, "tool_calls", None):
                    tc = msg.tool_calls[0]
                    tc_dict = {
                        "tool_name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                    }
                    content = json.dumps(tc_dict, ensure_ascii=False, indent=2)
                result.append({"role": "assistant", "content": content})
            elif isinstance(msg, ToolMessage):
                result.append(
                    {
                        "role": "user",
                        "content": f"Tool '{getattr(msg, 'name', 'unknown')}' result:\n{msg.content}",
                    }
                )
            else:
                result.append({"role": "user", "content": str(msg.content)})
        return result

    @staticmethod
    def _extract_parameters(tool_obj: Any) -> Dict[str, Any]:
        if hasattr(tool_obj, "args_schema"):
            schema_cls = tool_obj.args_schema
            if hasattr(schema_cls, "model_json_schema"):
                return schema_cls.model_json_schema()
            if hasattr(schema_cls, "schema"):
                return schema_cls.schema()
        if hasattr(tool_obj, "input_schema"):
            try:
                inp = tool_obj.input_schema
                if hasattr(inp, "model_json_schema"):
                    return inp.model_json_schema()
            except Exception:
                pass
        return {"type": "object", "properties": {}}

    def _build_payload(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        try:
            json.dumps(payload)
        except (TypeError, ValueError):
            payload = {
                "model": self.chat_model,
                "messages": messages,
                "temperature": self.temperature,
                "stream": False,
            }
        return payload

    def _get_endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/chat/completions"

    def _parse_response(self, data: Dict[str, Any]) -> AIMessage:
        choices = data.get("choices", [])
        if not choices:
            return AIMessage(content=str(data))

        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        raw_tool_calls = msg.get("tool_calls", [])

        lc_tool_calls = []
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            args_raw = func.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            lc_tool_calls.append(
                {
                    "name": func.get("name", ""),
                    "args": args,
                    "id": tc.get("id", f"call_{len(lc_tool_calls)}"),
                }
            )

        # Few-shot JSON 응답에서 tool call 파싱
        if not lc_tool_calls and content:
            stripped = content.strip()
            if stripped.startswith("```json"):
                stripped = stripped[7:]
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                stripped = stripped.strip()
            elif stripped.startswith("```"):
                stripped = stripped[3:]
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                stripped = stripped.strip()

            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict) and "tool_name" in parsed:
                        lc_tool_calls.append(
                            {
                                "name": parsed["tool_name"],
                                "args": parsed.get("arguments", {}),
                                "id": f"call_fabrix_{len(lc_tool_calls)}",
                            }
                        )
                        content = ""
                except json.JSONDecodeError:
                    pass

        if lc_tool_calls:
            return AIMessage(content=content, tool_calls=lc_tool_calls)
        return AIMessage(content=content)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForChainRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        openai_messages = self._convert_messages(messages)

        # Tools 바인딩된 경우 Few-shot 가이드 주입
        if hasattr(self, "_bound_tools") and self._bound_tools:
            formatted_tools = []
            for tool_obj in self._bound_tools:
                name = getattr(tool_obj, "name", getattr(tool_obj, "__name__", "tool"))
                desc = getattr(
                    tool_obj, "description", getattr(tool_obj, "__doc__", "")
                ) or ""
                parameters = self._extract_parameters(tool_obj)
                formatted_tools.append(
                    {"name": name, "description": desc, "parameters": parameters}
                )
            tools_json = json.dumps(formatted_tools, ensure_ascii=False, indent=2)

            last_user_idx = -1
            for i in range(len(openai_messages) - 1, -1, -1):
                if openai_messages[i]["role"] == "user":
                    last_user_idx = i
                    break

            if last_user_idx != -1:
                original_content = openai_messages[last_user_idx]["content"]
                tool_prompt = (
                    f"### Tool Definitions\n{tools_json}\n\n"
                    f"### Response Format\n"
                    f'{{"tool_name": "도구 이름", "arguments": {{"파라미터명": "값"}}}}\n\n'
                    f"### Request\nUser: {original_content}\nAssistant:"
                )
                openai_messages[last_user_idx]["content"] = tool_prompt

        endpoint = self._get_endpoint()
        payload = self._build_payload(openai_messages, **kwargs)
        headers = self._headers.copy()

        try:
            resp = requests.post(
                endpoint, json=payload, headers=headers, timeout=self.timeout
            )
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                detail = resp.text if hasattr(resp, "text") else ""
                raise requests.exceptions.HTTPError(f"{e} | body: {detail}") from e

            result = resp.json()
            ai_msg = self._parse_response(result)
            return ChatResult(generations=[ChatGeneration(message=ai_msg)])

        except requests.exceptions.RequestException as e:
            raise ValueError(f"FabriX API 호출 실패: {str(e)}")

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "FabriXAPIClient":
        new_instance = FabriXAPIClient(
            base_url=self.base_url,
            chat_model=self.chat_model,
            temperature=self.temperature,
            api_key=self.api_key,
            timeout=self.timeout,
        )
        new_instance._headers = self._headers.copy()
        new_instance._bound_tools = tools
        new_instance._tool_kwargs = kwargs
        return new_instance
