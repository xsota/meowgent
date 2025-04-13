import datetime
import os
from datetime import datetime, timedelta
from logging import basicConfig, getLogger, INFO

import discord
from discord.ext import commands
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from meowgent import Meowgent
from tools.get_current_time import get_current_time
from tools.task_manager import TaskManager
from tools.web_search import web_search

basicConfig(level=INFO)
logger = getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!?!!?', intents=intents)

# Initialize LLM model early
try:
  model = ChatOpenAI(
    model=os.environ.get('OPEN_AI_MODEL'),
    openai_api_key=os.environ.get('OPEN_AI_API_KEY'),
    openai_api_base=os.environ.get('OPEN_AI_API_URL'),
    max_tokens=int(os.environ.get('OPEN_AI_MAX_TOKEN')),
    temperature=float(os.environ.get('TEMPERATURE', 1))
  )
except Exception as e:
  logger.error(f"モデル初期化失敗: {e}")
  model = None

character_prompt = os.environ.get('CHARACTER_PROMPT')

# Initialize Meowgent (empty tools for now)
bot.meowgent = Meowgent(
  model=model,
  tools=[],
  system_prompt=character_prompt
)

@bot.event
async def on_ready():
  logger.info(f"Bot is ready. Logged in as {bot.user}")

  task_manager = TaskManager()

  async def task(channel_id: int, prompt: str):
    try:
      final_state = await bot.meowgent.app.ainvoke({
        "messages": [SystemMessage(content=prompt)],
        "current_channel_id": channel_id
      }, config={"configurable": {"thread_id": channel_id, "recursion_limit": 5}})
      message = final_state['messages'][-1]
      await bot.get_channel(channel_id).send(f"{message.content}")
    except Exception as e:
      logger.error(f"error: {e}")

  from langchain_core.tools import tool

  @tool
  def create_task(channel_id: int, prompt: str, minutes_later: int):
    try:
      scheduled_time = datetime.now() + timedelta(minutes=minutes_later)
      task_manager.add_task(task, scheduled_time, [channel_id, prompt])
      return f"Successfully scheduled.: {scheduled_time.isoformat()}."
    except Exception as e:
      return f"Error: {str(e)}"

  # Assign tools after initialization
  bot.meowgent.tools = [web_search, create_task, get_current_time]

  async def on_stamina_change(stamina: int, max_stamina: int):
    activity = discord.Game(name=render_stamina_bar(stamina, max_stamina, 10))
    await bot.change_presence(activity=activity)

  def render_stamina_bar(current: int, max_stamina: int, bar_length: int = 20) -> str:
    filled_length = int(bar_length * current / max_stamina)
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    return f"[{bar}]"

  bot.meowgent.add_stamina_listener(on_stamina_change)
  bot.meowgent.start_stamina_recovery(interval=360, recovery_amount=1)

  logger.info("Meowgent fully initialized.")
  task_manager.start_scheduler()

@bot.event
async def setup_hook():
  await bot.load_extension("cogs.chart_cog")
  await bot.load_extension("cogs.proposal_cog")
  await bot.load_extension("cogs.price_cog")
  await bot.load_extension("cogs.events_cog")
  await bot.tree.sync()

bot.run(DISCORD_TOKEN)
