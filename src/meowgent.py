import asyncio
from typing import Literal, Callable, List

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from logging import getLogger

from discord_messages_state import DiscordMessagesState

logger = getLogger(__name__)

class Meowgent:
  def __init__(self, model, tools, system_prompt, checkpointer=None):
    self.system_prompt = system_prompt
    self.model = model
    self.tools = tools
    self.checkpointer = checkpointer
    self.max_stamina = 100
    self.stamina = self.max_stamina
    self._stamina_updated_listeners: List[Callable[[int, int], None]] = []  # スタミナ変更リスナー
    self._stamina_recovery_task = None  # スタミナ回復用のタスク

    # 新しいグラフを定義
    workflow = StateGraph(DiscordMessagesState)
    logger.info("The graph has been initialized.")
    workflow.add_node("agent", self.call_model)

    # ツールの設定
    self.tools = {t.name: t for t in tools}
    self.model = model.bind_tools(tools)

    tool_node = ToolNode(tools)
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
      "agent",
      self.should_continue,
    )
    logger.info("Conditional edges have been added to 'agent'.")

    # toolのあとはagent
    workflow.add_edge("tools", 'agent')

    self.app = workflow.compile(checkpointer=checkpointer)
    logger.info("The graph has been compiled.")


  # 処理を継続するかどうかを決定する
  async def should_continue(self, state: DiscordMessagesState) -> Literal["tools", END]:
    messages = state['messages']
    last_message = messages[-1]
    logger.info(f"[should_continue] The last message: {last_message.content}")
    if last_message.tool_calls:
      logger.info("[should_continue] A tool call has been detected. Next node: tools")
      await self.reduce_stamina(5) # スタミナ使う
      return "tools"
    logger.info("[should_continue] No tool call detected. Next node: END")
    return END

  # モデルを呼び出す関数
  async def call_model(self, state: DiscordMessagesState):
    channel_id = state['current_channel_id']
    system_prompt = SystemMessage(content=self.system_prompt)
    channel_id_prompt = SystemMessage(content=f"current_channel_id: {channel_id}")

    messages = [system_prompt, channel_id_prompt] + state['messages']
    logger.info(f"[call_model] Messages passed to the model: {[msg.content for msg in messages]}")
    response = self.model.invoke(messages)
    logger.info(f"[call_model] Response from the model: {response}")

    await self.reduce_stamina(5) # スタミナ使う
    return {"messages": [response]}

  def add_stamina_listener(self, listener: Callable[[int, int], None]):
    """スタミナ変更時に呼び出されるリスナーを追加"""
    self._stamina_updated_listeners.append(listener)

  async def _notify_stamina_change(self):
    """スタミナ変更時にリスナーを通知"""
    for listener in self._stamina_updated_listeners:
      if asyncio.iscoroutinefunction(listener):
        # リスナーが非同期関数の場合
        #loop = asyncio.get_running_loop()
        # asyncio.run_coroutine_threadsafe(listener(self.stamina, self.max_stamina), loop)
        await listener(self.stamina, self.max_stamina)
      else:
        # リスナーが同期関数の場合
        listener(self.stamina, self.max_stamina)

  async def reduce_stamina(self, amount):
    self.stamina = max(0, self.stamina - amount)
    logger.info(f"Stamina reduced by {amount}. Current stamina: {self.stamina}")
    await self._notify_stamina_change()

  async def recover_stamina(self, amount):
    self.stamina = min(self.max_stamina, self.stamina + amount)
    # logger.info(f"Stamina recovered by {amount}. Current stamina: {self.stamina}")
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
