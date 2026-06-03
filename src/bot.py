from datetime import datetime, timedelta
from logging import basicConfig, getLogger, INFO

import discord
from discord.ext import commands

from config import load_config
from llm import OpenAICompatibleChatProvider, ToolDefinition
from tools.get_current_time import get_current_time
from tools.task_manager import TaskManager
from tools.web_search import web_search

basicConfig(level=INFO)
logger = getLogger(__name__)

config = load_config()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!?!!?', intents=intents)
bot.meowgent = None

appId = None

@bot.event
async def on_ready():
  logger.info(f"Bot is ready. Logged in as {bot.user}")

  from meowgent import Meowgent

  # load llm
  provider = OpenAICompatibleChatProvider(
    model=config.openai.model,
    api_key=config.openai.api_key,
    base_url=config.openai.api_url,
    max_tokens=config.openai.max_tokens,
    temperature=config.openai.temperature
  )

  # Task Manager
  task_manager = TaskManager()
  async def task(channel_id: int, prompt: str):
    try:
      final_state = await bot.meowgent.app.ainvoke(
        {
          "messages": [{"role": "user", "content": prompt}],
          "current_channel_id": channel_id
        },

        config={"configurable": {"thread_id": channel_id, "recursion_limit": 5}}
      )
      message = final_state['messages'][-1]
      await bot.get_channel(channel_id).send(f"{message.content}")
    except Exception as e:
      logger.error(f"error: {e}")

  def create_task(channel_id: int, prompt: str, minutes_later: int):
    """
    Schedule a new task to run after a specified time.

    Args:
        channel_id (int): Discord channel ID where the task will run.
        prompt (str): Content to execute as the task after the specified delay.
        minutes_later (int): Minutes from now when the task will execute.

    Example:
        create_task(1234567890, "Check server status", 10)  # Executes 10 minutes later
    """

    try:
      # 現在時刻から指定された分だけ後の時刻を計算
      scheduled_time = datetime.now() + timedelta(minutes=minutes_later)

      # タスクをスケジュール
      task_manager.add_task(task, scheduled_time, [channel_id, prompt])
      return f"Successfully scheduled.: {scheduled_time.isoformat()}."
    except Exception as e:
      return f"Error: {str(e)}"

  # tools settings
  tools = [
    ToolDefinition(
      name="web_search",
      description="Search the web for the given query.",
      parameters={
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Search query.",
          },
        },
        "required": ["query"],
      },
      handler=web_search,
    ),
    ToolDefinition(
      name="create_task",
      description="Schedule a task to run after the specified number of minutes.",
      parameters={
        "type": "object",
        "properties": {
          "channel_id": {
            "type": "integer",
            "description": "Discord channel ID where the task will run.",
          },
          "prompt": {
            "type": "string",
            "description": "Prompt to run as the scheduled task.",
          },
          "minutes_later": {
            "type": "integer",
            "description": "Minutes from now when the task will execute.",
          },
        },
        "required": ["channel_id", "prompt", "minutes_later"],
      },
      handler=create_task,
    ),
    ToolDefinition(
      name="get_current_time",
      description="Get current time in the specified timezone.",
      parameters={
        "type": "object",
        "properties": {
          "timezone_name": {
            "type": "string",
            "description": "Timezone name, e.g. Asia/Tokyo.",
          },
        },
        "required": ["timezone_name"],
      },
      handler=get_current_time,
    ),
  ]

  # character settings
  runtime_prompt = f"- Your Discord user ID is {bot.user.id}"
  system_prompt = f"{runtime_prompt}\n\n{config.character_prompt}"

  # Meowgent initialize
  bot.meowgent = Meowgent(
    provider=provider,
    tools=tools,
    system_prompt=system_prompt
  )

  async def on_stamina_change(stamina: int, max_stamina: int):
    """スタミナ変更時に呼び出される処理"""
    logger.info(f"[EventsCog] Meowgent's stamina updated: {stamina}")
    # botのステータスをスタミナに変更
    # Botのステータスを更新
    activity = discord.Game(name=render_stamina_bar(stamina, max_stamina, 10))
    await bot.change_presence(activity=activity)

  def render_stamina_bar(current: int, max_stamina: int, bar_length: int = 20) -> str:
    filled_length = int(bar_length * current / max_stamina)
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    return f"[{bar}]"

  bot.meowgent.add_stamina_listener(on_stamina_change)
  bot.meowgent.start_stamina_recovery(interval=360, recovery_amount=1) # 8時間で80くらい回復してほしい

  logger.info("Meowgent instance has been initialized.")

  task_manager.start_scheduler()


@bot.event
async def setup_hook():
  # Cogロード
  await bot.load_extension("cogs.proposal_cog")
  await bot.load_extension("cogs.events_cog")

  # コマンド反映
  await bot.tree.sync()

bot.run(config.discord_token)
