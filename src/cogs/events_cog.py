import asyncio
import copy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands
import re
import random
from logging import getLogger

from config import load_config
from llm import LLMMessage

logger = getLogger(__name__)

MessageContent = str | list[dict[str, Any]]
VOICE_STATE_UPDATE_PATTERN = re.compile(r'^.*が(.*)(からきえてくにゃ・・・|に入ったにゃ！)$')


def remove_mentions(text):
  # 正規表現でメンション部分を削除
  mention_pattern = r'<@!?[0-9]+>'
  return re.sub(mention_pattern, '', text)


def get_user_nickname(member):
  """Return a user's display name, handling clients without ``nick``."""
  nick = getattr(member, "nick", None)
  name = getattr(member, "name", None)
  if nick:
    return nick
  if name:
    return name
  return str(member)


@dataclass
class ConversationMessage:
  message_id: int
  channel_id: int
  author_id: int
  author_name: str
  role: str
  content: MessageContent
  created_at: Any

  def to_llm_message(self) -> dict[str, Any]:
    return {
      "role": self.role,
      "content": self.content,
    }


class ShortTermMemory:
  def __init__(self, max_length: int):
    self.max_length = max_length
    self._messages_by_channel: dict[int, list[ConversationMessage]] = {}

  def add(self, message: ConversationMessage):
    messages = self._messages_by_channel.setdefault(message.channel_id, [])
    messages_by_id = {item.message_id: item for item in messages}
    messages_by_id[message.message_id] = message
    merged = sorted(messages_by_id.values(), key=lambda item: item.created_at)
    self._messages_by_channel[message.channel_id] = merged[-self.max_length:]

  def get(self, channel_id: int) -> list[ConversationMessage]:
    return list(self._messages_by_channel.get(channel_id, []))

  def merge(self, channel_id: int, messages: list[ConversationMessage]) -> list[ConversationMessage]:
    for message in messages:
      self.add(message)
    return self.get(channel_id)


class EventsCog(commands.Cog):
  MAX_HISTORY_LENGTH = 10
  RANDOM_REPLY_CHANCE = 36
  HISTORY_FETCH_MIN_MESSAGES = 3
  HISTORY_FETCH_GAP = timedelta(minutes=5)

  def __init__(self, bot):
    self.bot = bot
    self.short_term_memory = ShortTermMemory(self.MAX_HISTORY_LENGTH)
    self.channel_message_history = {}
    config = load_config()
    self.voice_notification_enabled = config.voice_notification.enabled
    self.leave_message = config.voice_notification.leave_message
    self.join_message = config.voice_notification.join_message
    self.notification_channel_name = config.voice_notification.channel_name
    self.initial_max_tokens = config.openai.max_tokens
    self.current_max_tokens = self.initial_max_tokens


  @commands.Cog.listener()
  async def on_ready(self):
    logger.info('ログイン')
    logger.info(self.bot.user.id)
    logger.info('Servers connected to:')

    for guild in self.bot.guilds:
      logger.info(f'{guild.name} {guild.id}')

  @commands.Cog.listener()
  async def on_message(self, message):
    # メッセージ履歴にメッセージを追加
    if message.author != self.bot.user:
      self.add_message_to_history(message)

    # メッセージがbot自身からのものであれば、何もしない
    if message.author.id == self.bot.user.id:
      return

    if str(self.bot.user.id) in message.content:
      if message.author.bot:  # 相手がbotの場合
        if random.randint(1, self.RANDOM_REPLY_CHANCE) == 1 and self.has_enough_context(message.channel.id):
          await self.reply_to(message)  # ランダムに返信
        return
      else:  # 相手が人間の場合は必ず返信
        await self.reply_to(message)
        return

    if random.randint(1, self.RANDOM_REPLY_CHANCE) == 1 and self.has_enough_context(message.channel.id):
      async with message.channel.typing():
        messages = await self.get_reply(message)
        final_msg = messages[-1]
        if self.is_tool_message(final_msg) or self.get_tool_calls(final_msg):
          logger.error("Random reply failed: final message is a tool call")
          return
        content = self.get_message_content(final_msg)
        if not content or (isinstance(content, str) and not content.strip()) or (isinstance(content, list) and len(content) == 0):
          logger.error("Random reply failed: final message has no textual content")
          return
        # Format and guard against empty content
        random_reply_text = self.safe_text_from_content(content)
        m = await message.channel.send(random_reply_text)
        self.add_message_to_history(m, role="assistant")

      await self.wait_reply(m, messages)
      return

  @commands.Cog.listener()
  async def on_voice_state_update(self, member, before, after):
    # 通知機能が無効の場合は何もしない
    if not self.voice_notification_enabled:
      return

    # 入退室判定
    if before.channel == after.channel:
      return

    # 通知先のテキストチャンネル取得
    server = before.channel.guild if after.channel is None else after.channel.guild
    channel = discord.utils.get(server.channels, name=self.notification_channel_name, type=discord.ChannelType.text)

    if channel is None:
      logger.warning(f"Notification channel '{self.notification_channel_name}' not found in server '{server.name}'.")
      return

    name = get_user_nickname(member)

    # 入退室メッセージ送信
    if after.channel is None:
      message = self.leave_message.format(name=name, channel=before.channel.name)
    else:
      message = self.join_message.format(name=name, channel=after.channel.name)

    async with channel.typing():
      sent = await channel.send(message)

    # bot メッセージも履歴に追加する
    self.add_message_to_history(sent, role="assistant")

  def has_enough_context(self, channel_id: int) -> bool:
    return len(self.short_term_memory.get(channel_id)) > 2

  def add_message_to_history(self, message, role="user"):
    conversation_message = self.to_conversation_message(message, role=role)
    if conversation_message is None:
      return False

    self.short_term_memory.add(conversation_message)
    self.sync_legacy_history(conversation_message.channel_id)
    logger.info(self.channel_message_history)
    return True

  def to_conversation_message(self, message, role="user") -> ConversationMessage | None:
    author_id = message.author.id
    channel_id = message.channel.id
    content = message.content or ""
    name = get_user_nickname(message.author)
    text = content.strip()
    first_image_url = None
    attachments = getattr(message, "attachments", [])
    if attachments:
      for attachment in message.attachments:
        if attachment.content_type and 'image' in attachment.content_type:
          first_image_url = attachment.url
          break

    normalized_role = role
    normalized_content: MessageContent | None = None
    if first_image_url is not None:
      normalized_content = [
        {
          "type": "text",
          "text": f"{name}:{author_id} {text}"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": first_image_url
          }
        },
      ]
    elif text:
      if role == "user":
        normalized_content = f"{name}:{author_id} {text}"
      elif role == "assistant":
        if VOICE_STATE_UPDATE_PATTERN.match(content):
          normalized_role = "system"
          normalized_content = text
        else:
          normalized_content = text
      elif role == "system":
        normalized_content = text

    if normalized_content is None:
      return None

    return ConversationMessage(
      message_id=message.id,
      channel_id=channel_id,
      author_id=author_id,
      author_name=name,
      role=normalized_role,
      content=normalized_content,
      created_at=message.created_at,
    )

  def sync_legacy_history(self, channel_id: int):
    self.channel_message_history[channel_id] = [
      message.to_llm_message()
      for message in self.short_term_memory.get(channel_id)
    ]

  async def build_conversation_context(self, message):
    channel_id = message.channel.id
    memory_messages = self.short_term_memory.get(channel_id)
    if self.should_fetch_discord_history(message, memory_messages):
      try:
        fetched_messages = []
        async for history_message in message.channel.history(limit=self.MAX_HISTORY_LENGTH):
          conversation_message = self.to_conversation_message(
            history_message,
            role="assistant" if history_message.author.id == self.bot.user.id else "user",
          )
          if conversation_message is not None:
            fetched_messages.append(conversation_message)
        memory_messages = self.short_term_memory.merge(channel_id, fetched_messages)
        self.sync_legacy_history(channel_id)
      except Exception:
        logger.exception("Failed to fetch Discord channel history.")

    return [
      conversation_message.to_llm_message()
      for conversation_message in self.short_term_memory.get(channel_id)
    ]

  def should_fetch_discord_history(self, message, memory_messages: list[ConversationMessage]) -> bool:
    if not memory_messages:
      return True
    if len(memory_messages) < self.HISTORY_FETCH_MIN_MESSAGES:
      return True
    reference = getattr(message, "reference", None)
    reference_message_id = getattr(reference, "message_id", None)
    if reference_message_id is not None and not any(item.message_id == reference_message_id for item in memory_messages):
      return True
    content = getattr(message, "content", "") or ""
    if str(self.bot.user.id) in content and len(memory_messages) < self.MAX_HISTORY_LENGTH:
      return True

    previous_messages = [
      item for item in memory_messages
      if item.message_id != message.id
    ]
    if not previous_messages:
      return False

    latest_message = max(previous_messages, key=lambda item: item.created_at)
    return message.created_at - latest_message.created_at >= self.HISTORY_FETCH_GAP



  async def get_reply(self, message, conversation_messages=None):
    if conversation_messages is None:
      conversation_messages = await self.build_conversation_context(message)
    else:
      conversation_messages = copy.deepcopy(conversation_messages)
    max_retries = 3
    retries = 0

    while retries < max_retries:
      # run agent
      final_state = await self.bot.meowgent.app.ainvoke(
        {
          "messages": conversation_messages,
          "current_channel_id": message.channel.id,
        },
        config={"configurable": {"thread_id": message.channel.id, "recursion_limit": 5}}
      )

      # 追加されたメッセージを履歴に格納
      new_messages = final_state['messages'][len(conversation_messages):]
      conversation_messages.extend(new_messages)
      last_message = conversation_messages[-1]

      finish_reason = None
      response_metadata = self.get_response_metadata(last_message)
      if response_metadata:
        finish_reason = response_metadata.get("finish_reason")
      if finish_reason == "length":
        logger.warning("Token limit reached. Increasing max_tokens by 10% and retrying without tools.")
        if conversation_messages:
          conversation_messages.pop()
        new_max = int(self.current_max_tokens * 1.1) if self.current_max_tokens else 0
        cap = int(self.initial_max_tokens * 2)
        if new_max > cap:
          new_max = cap
          logger.info(f"max_tokens increase capped at {cap}")
        if new_max > self.current_max_tokens:
          logger.info(f"Updating max_tokens: {self.current_max_tokens} -> {new_max}")
          self.current_max_tokens = new_max
        else:
          logger.info(f"max_tokens remains at {self.current_max_tokens}")
        provider_messages = [
          LLMMessage(role="system", content=self.bot.meowgent.system_prompt or ""),
          LLMMessage(role="system", content=f"current_channel_id: {message.channel.id}"),
          *conversation_messages,
        ]
        response = await self.bot.meowgent.provider.generate(
          provider_messages,
          tools=[],
          max_tokens=self.current_max_tokens,
        )
        response_message = response.to_message()
        reply_text = self.safe_text_from_content(response_message.content)
        if reply_text == "…":
          logger.error("Retry without tools failed: no textual content")
          break
        response_message.content = reply_text
        conversation_messages.append(response_message)
        break

      if self.is_tool_message(last_message) or self.get_tool_calls(last_message):
        logger.error("Agent returned a tool call without a final text response")
        retries += 1
        continue

      content = self.get_message_content(last_message)
      # content が空の場合は再試行
      if not content or (isinstance(content, str) and not content.strip()) or (isinstance(content, list) and len(content) == 0):
        retries += 1
        logger.warning(f"Empty content received, retrying ({retries}/{max_retries})")
        continue

      # 正常なテキスト応答を得られた場合、履歴に追加してループを抜ける
      reply_text = self.safe_text_from_content(content)
      last_message.content = reply_text
      break

    else:
      # 最大リトライ回数超過
      logger.error("Failed to obtain textual response after retries")

    return conversation_messages

  async def reply_to(self, message, conversation_messages=None):
    async with message.channel.typing():
      messages = await self.get_reply(message, conversation_messages)
    final_msg = messages[-1]
    if self.is_tool_message(final_msg) or self.get_tool_calls(final_msg):
      logger.error("Reply failed: final message is a tool call")
      return
    content = self.get_message_content(final_msg)
    if not content or (isinstance(content, str) and not content.strip()) or (isinstance(content, list) and len(content) == 0):
      logger.error("Reply failed: final message has no textual content")
      return
    # Format and guard against empty content
    reply_text = self.safe_text_from_content(content)
    reply_message = await message.reply(reply_text)
    self.add_message_to_history(reply_message, role="assistant")

    await self.wait_reply(reply_message, messages)

  async def wait_reply(self, message, gpt_messages):
    def check(m):
      return (
        m.reference is not None
        and m.reference.message_id == message.id
      )

    try:
      msg = await self.bot.wait_for('message', timeout=180.0, check=check)

      # メッセージがbotから送信された場合
      if msg.author.bot:
        if random.randint(1, self.RANDOM_REPLY_CHANCE) == 1:  # ランダム返信
          self.add_message_to_history(msg)
          await self.reply_to(msg)
      else:
        # 人間から送信された場合、通常の処理
        self.add_message_to_history(msg)
        await self.reply_to(msg)

    except asyncio.TimeoutError:
      # メッセージが一定時間内に返信されなかった場合
      pass

  def safe_text_from_content(self, content) -> str:
    """Extract a safe, non-empty text from model content.
    Supports string or list-of-parts (e.g., {"type":"text","text":...}).
    Falls back to a placeholder if empty.
    """
    try:
      # Simple string case
      if isinstance(content, str):
        text = content.strip()
        return text if text else "…"

      # OpenAI-style content parts
      if isinstance(content, list):
        parts = []
        for part in content:
          if isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
              parts.append(part["text"]) 
        text = "\n".join([p for p in parts if p]).strip()
        return text if text else "…"

      # Fallback to string representation
      text = str(content).strip()
      return text if text else "…"
    except Exception:
      return "…"

  def get_message_content(self, message):
    if isinstance(message, dict):
      return message.get("content")
    return getattr(message, "content", None)

  def get_tool_calls(self, message):
    if isinstance(message, dict):
      return message.get("tool_calls")
    return getattr(message, "tool_calls", None)

  def get_response_metadata(self, message):
    if isinstance(message, dict):
      return message.get("response_metadata")
    return getattr(message, "response_metadata", None)

  def is_tool_message(self, message):
    if isinstance(message, dict):
      return message.get("role") == "tool"
    return getattr(message, "role", None) == "tool"


async def setup(bot: commands.Bot):
  await bot.add_cog(EventsCog(bot))
