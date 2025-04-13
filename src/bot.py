import datetime
import os
from datetime import datetime, timedelta
from logging import basicConfig, getLogger, INFO

import discord
from discord.ext import commands
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from tools.get_current_time import get_current_time
from tools.task_manager import TaskManager
from tools.web_search import web_search

basicConfig(level=INFO)
logger = getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')

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
  model = ChatOpenAI(
    model=os.environ.get('OPEN_AI_MODEL'),
    openai_api_key=os.environ.get('OPEN_AI_API_KEY'),
    openai_api_base=os.environ.get('OPEN_AI_API_URL'),
    max_tokens=int(os.environ.get('OPEN_AI_MAX_TOKEN')),
    temperature=float(os.environ.get('TEMPERATURE', 1))
  )

  # Task Manager
  task_manager = TaskManager()
  async def task(channel_id: int, prompt: str):
    try:
      final_state = await bot.meowgent.app.ainvoke(
        {
          "messages": [SystemMessage(content=prompt)],
          "current_channel_id": channel_id
        },

        config={"configurable": {"thread_id": channel_id, "recursion_limit": 5}}
      )
      message = final_state['messages'][-1]
      await bot.get_channel(channel_id).send(f"{message.content}")
    except Exception as e:
      logger.error(f"error: {e}")

  @tool
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
    web_search,
    create_task,
    get_current_time,
  ]

  # character settings
  character_prompt = os.environ.get('CHARACTER_PROMPT')

  # Meowgent initialize
  bot.meowgent = Meowgent(
    model=model,
    tools=tools,
    system_prompt=character_prompt
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
  await bot.load_extension("cogs.chart_cog")
  await bot.load_extension("cogs.proposal_cog")
  await bot.load_extension("cogs.price_cog")
  await bot.load_extension("cogs.events_cog")

  # コマンド反映
  await bot.tree.sync()

bot.run(DISCORD_TOKEN)
