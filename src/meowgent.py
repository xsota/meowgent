import asyncio
import contextvars
import json
from dataclasses import dataclass
from logging import getLogger
from typing import Any, Callable, List

from llm import (
  LLMMessage,
  LLMProvider,
  ToolDefinition,
  content_to_text,
  parse_tool_arguments,
  to_llm_message,
)

logger = getLogger(__name__)


def _message_log_summary(message: LLMMessage) -> dict[str, Any]:
  """Return useful message metadata without logging message contents."""
  tool_names = []
  for tool_call in message.tool_calls or []:
    function = tool_call.get("function", {})
    name = function.get("name")
    if isinstance(name, str) and name:
      tool_names.append(name)

  return {
    "role": message.role,
    "name": message.name,
    "content_length": len(content_to_text(message.content)),
    "tool_names": tool_names,
  }


class MeowgentApp:
  def __init__(self, meowgent: "Meowgent"):
    self.meowgent = meowgent

  async def ainvoke(self, state, config=None):
    return await self.meowgent.ainvoke(state, config=config)

  def invoke(self, state, config=None):
    try:
      asyncio.get_running_loop()
    except RuntimeError:
      return asyncio.run(self.ainvoke(state, config=config))
    raise RuntimeError("MeowgentApp.invoke cannot run inside an active event loop")


@dataclass(frozen=True)
class AgentRunContext:
  """Request-scoped facts that tools may rely on instead of model-supplied arguments."""

  channel_id: int | None


_run_context: contextvars.ContextVar[AgentRunContext | None] = contextvars.ContextVar(
  "meowgent_run_context",
  default=None,
)


def current_run_context() -> AgentRunContext | None:
  return _run_context.get()


class Meowgent:
  DEFAULT_MAX_STEPS = 8
  DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
  DEFAULT_MAX_TOOL_OUTPUT_CHARS = 8_000

  def __init__(
    self,
    provider: LLMProvider,
    tools,
    system_prompt,
    checkpointer=None,
    max_steps: int = DEFAULT_MAX_STEPS,
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    max_tool_output_chars: int = DEFAULT_MAX_TOOL_OUTPUT_CHARS,
  ):
    self.system_prompt = system_prompt
    self.provider = provider
    self.model = provider
    self.checkpointer = checkpointer
    self.max_steps = max(1, max_steps)
    self.tool_timeout_seconds = tool_timeout_seconds
    self.max_tool_output_chars = max_tool_output_chars
    self.max_stamina = 100
    self.stamina = self.max_stamina
    self._stamina_updated_listeners: List[Callable[[int, int], None]] = []  # スタミナ変更リスナー
    self._stamina_recovery_task = None  # スタミナ回復用のタスク
    self.tools: dict[str, ToolDefinition] = {tool.name: tool for tool in tools}
    self.app = MeowgentApp(self)
    logger.info("Meowgent runtime has been initialized.")

  async def ainvoke(self, state, config=None):
    configurable = (config or {}).get("configurable", {})
    max_steps = max(1, configurable.get("recursion_limit") or self.max_steps)
    channel_id = state["current_channel_id"]
    token = _run_context.set(AgentRunContext(channel_id=channel_id))
    try:
      return await self._run(state, channel_id, max_steps)
    finally:
      _run_context.reset(token)

  async def _run(self, state, channel_id, max_steps: int):
    conversation_messages = [to_llm_message(message) for message in state["messages"]]
    messages = [
      LLMMessage(role="system", content=self.system_prompt or ""),
      LLMMessage(role="system", content=f"current_channel_id: {channel_id}"),
      *conversation_messages,
    ]
    output_messages = list(conversation_messages)
    previous_response_id = None
    input_items = None

    # The last iteration is reserved for a tool-free answer, so a run that uses
    # its whole step budget still ends with text instead of a dangling tool call.
    for step in range(max_steps + 1):
      out_of_steps = step == max_steps
      logger.info(
        "[ainvoke] Messages passed to the provider: %s",
        [_message_log_summary(message) for message in messages],
      )
      response = await self.provider.generate(
        messages,
        list(self.tools.values()),
        tool_choice="none" if out_of_steps else None,
        previous_response_id=previous_response_id,
        input_items=input_items,
      )
      if out_of_steps:
        logger.warning("Meowgent step budget (%s) exhausted; forced a final answer.", max_steps)
        response.tool_calls = []
      assistant_message = response.to_message()
      messages.append(assistant_message)
      output_messages.append(assistant_message)
      logger.info(
        "[ainvoke] Provider response: finish_reason=%s status=%s tool_names=%s response_id=%s",
        response.finish_reason,
        response.status,
        [
          call.get("function", {}).get("name")
          for call in response.tool_calls
          if isinstance(call.get("function", {}).get("name"), str)
        ],
        response.response_id,
      )
      await self.reduce_stamina(5) # スタミナ使う

      if not response.tool_calls:
        return {"messages": output_messages}

      logger.info("[ainvoke] Tool calls have been detected.")
      await self.reduce_stamina(5) # スタミナ使う
      tool_messages = await asyncio.gather(*[
        self._run_tool_call(tool_call)
        for tool_call in response.tool_calls
      ])
      messages.extend(tool_messages)
      output_messages.extend(tool_messages)

      previous_response_id = response.response_id
      input_items = [
        {
          "type": "function_call_output",
          "call_id": tool_message.tool_call_id,
          "output": tool_message.content,
        }
        for tool_message in tool_messages
      ]

    return {"messages": output_messages}

  async def _run_tool_call(self, tool_call: dict[str, Any]) -> LLMMessage:
    function = tool_call.get("function", {})
    tool_name = function.get("name")
    tool_args = parse_tool_arguments(function.get("arguments"))
    tool = self.tools.get(tool_name)
    try:
      if tool is None:
        result = {"error": f"Tool {tool_name} not found"}
      else:
        result = await asyncio.wait_for(tool.ainvoke(tool_args), timeout=self.tool_timeout_seconds)
    except asyncio.TimeoutError:
      logger.warning("Tool %s timed out after %ss", tool_name, self.tool_timeout_seconds)
      result = {"error": f"Tool {tool_name} timed out after {self.tool_timeout_seconds:g} seconds."}
    except Exception as e:
      logger.exception("Tool %s execution failed", tool_name)
      result = {"error": str(e) or type(e).__name__}

    content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(content) > self.max_tool_output_chars:
      omitted = len(content) - self.max_tool_output_chars
      content = f"{content[:self.max_tool_output_chars]}\n...[truncated {omitted} characters]"

    return LLMMessage(
      role="tool",
      content=content,
      tool_call_id=tool_call.get("call_id") or tool_call.get("id"),
      name=tool_name,
    )

  def add_stamina_listener(self, listener: Callable[[int, int], None]):
    """スタミナ変更時に呼び出されるリスナーを追加"""
    self._stamina_updated_listeners.append(listener)

  async def _notify_stamina_change(self):
    """スタミナ変更時にリスナーを通知"""
    for listener in self._stamina_updated_listeners:
      if asyncio.iscoroutinefunction(listener):
        await listener(self.stamina, self.max_stamina)
      else:
        listener(self.stamina, self.max_stamina)

  async def reduce_stamina(self, amount):
    self.stamina = max(0, self.stamina - amount)
    logger.info(f"Stamina reduced by {amount}. Current stamina: {self.stamina}")
    await self._notify_stamina_change()

  async def recover_stamina(self, amount):
    self.stamina = min(self.max_stamina, self.stamina + amount)
    await self._notify_stamina_change()

  def start_stamina_recovery(self, interval: int = 10, recovery_amount: int = 1):
    """スタミナを時間経過で回復させるタスクを開始"""
    if self._stamina_recovery_task is None:
      self._stamina_recovery_task = asyncio.create_task(
        self._recover_stamina_periodically(interval, recovery_amount)
      )
      logger.info("Stamina recovery task started.")

  def stop_stamina_recovery(self):
    """スタミナ回復タスクを停止"""
    if self._stamina_recovery_task:
      self._stamina_recovery_task.cancel()
      self._stamina_recovery_task = None
      logger.info("Stamina recovery task stopped.")

  async def _recover_stamina_periodically(self, interval: int, recovery_amount: int):
    """スタミナを一定間隔で回復"""
    while True:
      await asyncio.sleep(interval)
      await self.recover_stamina(recovery_amount)
