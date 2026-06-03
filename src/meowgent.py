import asyncio
import json
from logging import getLogger
from typing import Callable, List

from llm import (
  LLMMessage,
  LLMProvider,
  ToolDefinition,
  parse_tool_arguments,
  to_llm_message,
)

logger = getLogger(__name__)


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


class Meowgent:
  def __init__(self, provider: LLMProvider, tools, system_prompt, checkpointer=None):
    self.system_prompt = system_prompt
    self.provider = provider
    self.model = provider
    self.checkpointer = checkpointer
    self.max_stamina = 100
    self.stamina = self.max_stamina
    self._stamina_updated_listeners: List[Callable[[int, int], None]] = []  # スタミナ変更リスナー
    self._stamina_recovery_task = None  # スタミナ回復用のタスク
    self.tools: dict[str, ToolDefinition] = {tool.name: tool for tool in tools}
    self.app = MeowgentApp(self)
    logger.info("Meowgent runtime has been initialized.")

  async def ainvoke(self, state, config=None):
    configurable = (config or {}).get("configurable", {})
    recursion_limit = configurable.get("recursion_limit", 5)
    channel_id = state["current_channel_id"]
    conversation_messages = [to_llm_message(message) for message in state["messages"]]
    messages = [
      LLMMessage(role="system", content=self.system_prompt or ""),
      LLMMessage(role="system", content=f"current_channel_id: {channel_id}"),
      *conversation_messages,
    ]
    output_messages = list(conversation_messages)

    for _ in range(recursion_limit):
      logger.info(f"[ainvoke] Messages passed to the provider: {[message.content for message in messages]}")
      response = await self.provider.generate(messages, list(self.tools.values()))
      assistant_message = response.to_message()
      messages.append(assistant_message)
      output_messages.append(assistant_message)
      logger.info(f"[ainvoke] Response from the provider: {response.raw}")
      await self.reduce_stamina(5) # スタミナ使う

      if not response.tool_calls:
        return {"messages": output_messages}

      logger.info("[ainvoke] Tool calls have been detected.")
      await self.reduce_stamina(5) # スタミナ使う
      for tool_call in response.tool_calls:
        function = tool_call.get("function", {})
        tool_name = function.get("name")
        tool_args = parse_tool_arguments(function.get("arguments"))
        tool_id = tool_call.get("id")
        tool = self.tools.get(tool_name)
        try:
          if tool is None:
            result = f"Tool {tool_name} not found"
          else:
            result = await tool.ainvoke(tool_args)
        except Exception as e:
          logger.exception(f"Tool {tool_name} execution failed: {e}")
          result = str(e)

        tool_message = LLMMessage(
          role="tool",
          content=json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
          tool_call_id=tool_id,
          name=tool_name,
        )
        messages.append(tool_message)
        output_messages.append(tool_message)

    logger.error("Meowgent recursion limit reached before final response.")
    return {"messages": output_messages}

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
