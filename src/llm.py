import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from openai import AsyncOpenAI

MessageContent = str | list[dict[str, Any]]
ResponsesInputItem = dict[str, Any]


@dataclass
class LLMMessage:
  role: str
  content: Optional[MessageContent] = None
  tool_calls: Optional[list[dict[str, Any]]] = None
  tool_call_id: Optional[str] = None
  name: Optional[str] = None
  response_metadata: Optional[dict[str, Any]] = None

  def to_responses_items(self) -> list[ResponsesInputItem]:
    """Convert this internal message to Responses API input items."""
    if self.role == "tool":
      if not self.tool_call_id:
        return []
      return [{
        "type": "function_call_output",
        "call_id": self.tool_call_id,
        "output": content_to_text(self.content),
      }]

    items = []
    for tool_call in self.tool_calls or []:
      function = tool_call.get("function", {})
      call_id = tool_call.get("call_id") or tool_call.get("id")
      if not call_id or not function.get("name"):
        continue
      items.append({
        "type": "function_call",
        "call_id": call_id,
        "name": function["name"],
        "arguments": function.get("arguments") or "{}",
      })

    if self.content is not None:
      role = self.role if self.role in {"user", "assistant", "system", "developer"} else "user"
      items.append({
        "role": role,
        "content": responses_content(self.content),
      })
    return items

  def __getitem__(self, key: str) -> Any:
    return getattr(self, key)


@dataclass
class LLMResponse:
  content: Optional[MessageContent]
  tool_calls: list[dict[str, Any]]
  finish_reason: Optional[str]
  raw: Any
  response_id: Optional[str] = None
  status: Optional[str] = None
  incomplete_reason: Optional[str] = None

  def to_message(self) -> LLMMessage:
    response_metadata = {
      "finish_reason": self.finish_reason,
      "status": self.status,
      "incomplete_reason": self.incomplete_reason,
    }
    if self.response_id:
      response_metadata["response_id"] = self.response_id
    return LLMMessage(
      role="assistant",
      content=self.content,
      tool_calls=self.tool_calls or None,
      response_metadata=response_metadata,
    )


@dataclass
class ToolDefinition:
  name: str
  description: str
  parameters: dict[str, Any]
  handler: Callable[..., Any]

  def to_responses_tool(self) -> dict[str, Any]:
    """Convert this tool to the Responses API function schema."""
    return {
      "type": "function",
      "name": self.name,
      "description": self.description,
      "parameters": self.parameters,
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
    previous_response_id: Optional[str] = None,
    input_items: Optional[list[ResponsesInputItem]] = None,
  ) -> LLMResponse:
    ...


class OpenAICompatibleResponsesProvider:
  def __init__(
    self,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
  ):
    self.model = model
    self.max_tokens = max_tokens
    self.temperature = temperature
    self.reasoning_effort = reasoning_effort
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
    previous_response_id: Optional[str] = None,
    input_items: Optional[list[ResponsesInputItem]] = None,
  ) -> LLMResponse:
    instructions, message_items = responses_input_from_messages(messages)
    request: dict[str, Any] = {
      "model": self.model,
    }

    if instructions:
      request["instructions"] = instructions
    if previous_response_id:
      request["previous_response_id"] = previous_response_id
      request["input"] = input_items or []
    elif message_items:
      request["input"] = message_items

    request_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
    if request_max_tokens:
      request["max_output_tokens"] = request_max_tokens
    if self.temperature is not None:
      request["temperature"] = self.temperature
    if self.reasoning_effort:
      request["reasoning"] = {"effort": self.reasoning_effort}
    if tools:
      request["tools"] = [tool.to_responses_tool() for tool in tools]
    if tool_choice is not None:
      request["tool_choice"] = tool_choice

    response = await self.client.responses.create(**request)
    return response_to_llm_response(response)


def responses_content(content: MessageContent) -> str | list[dict[str, Any]]:
  if isinstance(content, str):
    return content

  converted = []
  for part in content:
    if not isinstance(part, dict):
      continue
    if part.get("type") == "text":
      converted.append({
        "type": "input_text",
        "text": str(part.get("text", "")),
      })
    elif part.get("type") == "image_url":
      image_url = part.get("image_url", {}).get("url")
      if image_url:
        converted.append({
          "type": "input_image",
          "image_url": image_url,
          "detail": "auto",
        })
  return converted


def content_to_text(content: Optional[MessageContent]) -> str:
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    text_parts = []
    for part in content:
      if isinstance(part, dict) and part.get("type") == "text":
        text_parts.append(str(part.get("text", "")))
    return "\n".join(part for part in text_parts if part)
  return "" if content is None else str(content)


def responses_input_from_messages(
  messages: list[LLMMessage | dict[str, Any]],
) -> tuple[Optional[str], list[ResponsesInputItem]]:
  instruction_parts = []
  input_items = []
  for message in messages:
    llm_message = to_llm_message(message)
    if llm_message.role in {"system", "developer"}:
      text = content_to_text(llm_message.content)
      if text:
        instruction_parts.append(text)
      continue
    input_items.extend(llm_message.to_responses_items())
  instructions = "\n\n".join(instruction_parts) or None
  return instructions, input_items


def response_to_llm_response(response: Any) -> LLMResponse:
  tool_calls = []
  for item in getattr(response, "output", []) or []:
    if getattr(item, "type", None) != "function_call":
      continue
    call_id = getattr(item, "call_id", None)
    if not call_id:
      continue
    tool_calls.append({
      "id": call_id,
      "call_id": call_id,
      "type": "function",
      "function": {
        "name": getattr(item, "name", ""),
        "arguments": getattr(item, "arguments", "{}"),
      },
    })

  content = getattr(response, "output_text", None)
  if not isinstance(content, str) or not content:
    content = response_output_text(response)
  if not content:
    content = None

  status = getattr(response, "status", None)
  incomplete_details = getattr(response, "incomplete_details", None)
  incomplete_reason = getattr(incomplete_details, "reason", None)
  if incomplete_reason == "max_output_tokens":
    finish_reason = "length"
  elif status == "incomplete":
    finish_reason = incomplete_reason or "incomplete"
  elif status == "completed" and tool_calls:
    finish_reason = "tool_calls"
  elif status == "completed":
    finish_reason = "stop"
  else:
    finish_reason = status

  return LLMResponse(
    content=content,
    tool_calls=tool_calls,
    finish_reason=finish_reason,
    raw=response,
    response_id=getattr(response, "id", None),
    status=status,
    incomplete_reason=incomplete_reason,
  )


def response_output_text(response: Any) -> Optional[str]:
  text_parts = []
  for item in getattr(response, "output", []) or []:
    if getattr(item, "type", None) != "message":
      continue
    for content in getattr(item, "content", []) or []:
      if getattr(content, "type", None) == "output_text":
        text = getattr(content, "text", None)
        if text:
          text_parts.append(text)
  return "\n".join(text_parts) or None


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
