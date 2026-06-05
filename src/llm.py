import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from openai import AsyncOpenAI

MessageContent = str | list[dict[str, Any]]


@dataclass
class LLMMessage:
  role: str
  content: Optional[MessageContent] = None
  tool_calls: Optional[list[dict[str, Any]]] = None
  tool_call_id: Optional[str] = None
  name: Optional[str] = None
  response_metadata: Optional[dict[str, Any]] = None

  def to_openai(self) -> dict[str, Any]:
    message = {"role": self.role}
    if self.content is not None:
      message["content"] = self.content
    if self.tool_calls:
      message["tool_calls"] = self.tool_calls
    if self.tool_call_id:
      message["tool_call_id"] = self.tool_call_id
    if self.name and self.role != "tool":
      message["name"] = self.name
    return message

  def __getitem__(self, key: str) -> Any:
    return getattr(self, key)


@dataclass
class LLMResponse:
  content: Optional[MessageContent]
  tool_calls: list[dict[str, Any]]
  finish_reason: Optional[str]
  raw: Any

  def to_message(self) -> LLMMessage:
    return LLMMessage(
      role="assistant",
      content=self.content,
      tool_calls=self.tool_calls or None,
      response_metadata={"finish_reason": self.finish_reason},
    )


@dataclass
class ToolDefinition:
  name: str
  description: str
  parameters: dict[str, Any]
  handler: Callable[..., Any]

  def to_openai_tool(self) -> dict[str, Any]:
    return {
      "type": "function",
      "function": {
        "name": self.name,
        "description": self.description,
        "parameters": self.parameters,
      },
    }

  async def ainvoke(self, args: Any) -> Any:
    if args is None:
      args = {}
    if not isinstance(args, dict):
      args = {"input": args}

    result = self.handler(**args)
    if inspect.isawaitable(result):
      return await result
    return result


class LLMProvider(Protocol):
  async def generate(
    self,
    messages: list[LLMMessage | dict[str, Any]],
    tools: Optional[list[ToolDefinition]] = None,
    max_tokens: Optional[int] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
  ) -> LLMResponse:
    ...


class OpenAICompatibleChatProvider:
  def __init__(
    self,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
  ):
    self.model = model
    self.max_tokens = max_tokens
    self.temperature = temperature
    self.client = AsyncOpenAI(
      api_key=api_key,
      base_url=base_url or None,
    )

  async def generate(
    self,
    messages: list[LLMMessage | dict[str, Any]],
    tools: Optional[list[ToolDefinition]] = None,
    max_tokens: Optional[int] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
  ) -> LLMResponse:
    request = {
      "model": self.model,
      "messages": [to_llm_message(message).to_openai() for message in messages],
    }
    request_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
    if request_max_tokens:
      request["max_completion_tokens"] = request_max_tokens
    if self.temperature is not None:
      request["temperature"] = self.temperature
    if tools:
      request["tools"] = [tool.to_openai_tool() for tool in tools]
    if tool_choice is not None:
      request["tool_choice"] = tool_choice

    completion = await self._create_completion(request)
    choice = completion.choices[0]
    message = choice.message
    tool_calls = []
    for tool_call in message.tool_calls or []:
      tool_calls.append({
        "id": tool_call.id,
        "type": "function",
        "function": {
          "name": tool_call.function.name,
          "arguments": tool_call.function.arguments,
        },
      })
    return LLMResponse(
      content=message.content,
      tool_calls=tool_calls,
      finish_reason=choice.finish_reason,
      raw=completion,
    )

  async def _create_completion(self, request: dict[str, Any]):
    return await self.client.chat.completions.create(**request)


def to_llm_message(message: LLMMessage | dict[str, Any]) -> LLMMessage:
  if isinstance(message, LLMMessage):
    return message
  return LLMMessage(
    role=message.get("role", "user"),
    content=message.get("content"),
    tool_calls=message.get("tool_calls"),
    tool_call_id=message.get("tool_call_id"),
    name=message.get("name"),
    response_metadata=message.get("response_metadata"),
  )


def parse_tool_arguments(arguments: Any) -> dict[str, Any]:
  if arguments is None:
    return {}
  if isinstance(arguments, dict):
    return arguments
  if isinstance(arguments, str):
    try:
      parsed = json.loads(arguments)
      return parsed if isinstance(parsed, dict) else {"input": parsed}
    except json.JSONDecodeError:
      return {"input": arguments}
  return {"input": arguments}
