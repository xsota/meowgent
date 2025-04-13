import asyncio
import os

import discord
from discord.ext import commands
import re
import random
from logging import getLogger

logger = getLogger(__name__)


def remove_mentions(text):
  # 正規表現でメンション部分を削除
  mention_pattern = r'<@!?[0-9]+>'
  return re.sub(mention_pattern, '', text)


def get_user_nickname(member):
  return member.name if member.nick is None else member.nick


class EventsCog(commands.Cog):
  MAX_HISTORY_LENGTH = 10
  RANDOM_REPLY_CHANCE = 36
  channel_message_history = {}

  def __init__(self, bot):
    self.bot = bot
    self.voice_notification_enabled = os.getenv('VOICE_NOTIFICATION_ENABLED', 'false').lower() == 'true'
    self.leave_message = os.getenv('VOICE_LEAVE_MESSAGE', '{name}が{channel}からきえてくにゃ・・・')
    self.join_message = os.getenv('VOICE_JOIN_MESSAGE', '{name}が{channel}に入ったにゃ！')
    self.notification_channel_name = os.getenv('VOICE_NOTIFICATION_CHANNEL', 'general')  # 通知先チャンネル名


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
    if message.author == self.bot.user:
      self.add_message_to_history(message,role="assistant")
    else:
      self.add_message_to_history(message)

    # メッセージがbot自身からのものであれば、何もしない
    if message.author.id == self.bot.user.id:
      return

    if str(self.bot.user.id) in message.content:
      if message.author.bot:  # 相手がbotの場合
        if random.randint(1, self.RANDOM_REPLY_CHANCE) == 1 and len(self.channel_message_history[message.channel.id]) > 2:
          await self.reply_to(message)  # ランダムに返信
        return
      else:  # 相手が人間の場合は必ず返信
        await self.reply_to(message)
        return

    if random.randint(1, self.RANDOM_REPLY_CHANCE) == 1 and len(self.channel_message_history[message.channel.id]) > 2:
      async with message.channel.typing():
        messages = await self.get_reply(message)
        m = await message.channel.send(messages[-1].content)

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
      await channel.send(message)

  def add_message_to_history(self, message, role="user"):
    import re
    author_id = message.author.id
    channel_id = message.channel.id
    content = message.content
    name = get_user_nickname(message.author)

    text = content.strip()

    if channel_id not in self.channel_message_history:
      self.channel_message_history[channel_id] = []

    # 添付画像がある場合は最初の1枚だけ処理
    first_image_url = None
    if message.attachments:
      for attachment in message.attachments:
        if attachment.content_type and 'image' in attachment.content_type:
          first_image_url = attachment.url
          break  # 最初の1枚だけ扱うにゃ

    # テキスト + 画像がある場合
    if first_image_url:
      content_list = []

      # テキスト（空でも入れるにゃ）
      content_list.append({
        "type": "text",
        "text": f"{name}:{author_id} {text}"
      })

      # 画像
      content_list.append({
        "type": "image_url",
        "image_url": {
          "url": first_image_url
        }
      })

      self.channel_message_history[channel_id].append({
        "role": role,
        "content": content_list
      })

    # テキストだけある場合（画像がない or 既に追加済みでない）
    elif text:
      formatted_text = f"{name}:{author_id} {text}"
      if role == "user":
        self.channel_message_history[channel_id].append({"role": "user", "content": formatted_text})
      elif role == "assistant":
        voice_state_update_pattern = re.compile(r'^.*が(.*)(からきえてくにゃ・・・|に入ったにゃ！)$')
        if voice_state_update_pattern.match(content):
          self.channel_message_history[channel_id].append({"role": "system", "content": text})
        else:
          self.channel_message_history[channel_id].append({"role": "assistant", "content": text})
      elif role == "system":
        self.channel_message_history[channel_id].append({"role": "system", "content": text})

    # MAX_HISTORY_LENGTH件を超えた場合、最も古いメッセージを削除
    if len(self.channel_message_history[channel_id]) > self.MAX_HISTORY_LENGTH:
      self.channel_message_history[channel_id].pop(0)

    logger.info(self.channel_message_history)
    return True



  async def get_reply(self, message, gpt_messages=None):
    if gpt_messages is None:
      gpt_messages = self.channel_message_history[message.channel.id]

    # run agent
    final_state = await self.bot.meowgent.app.ainvoke(
      {
        "messages": gpt_messages,
        "current_channel_id": message.channel.id,
      },

      config={"configurable": {"thread_id": message.channel.id, "recursion_limit": 5}}
    )
    message = final_state['messages'][-1]
    gpt_messages.append(message)

    return gpt_messages

  async def reply_to(self, message, gpt_messages=None):
    if gpt_messages is None:
      gpt_messages = self.channel_message_history[message.channel.id]

    async with message.channel.typing():
      messages = await self.get_reply(message, gpt_messages)

    reply_message = await message.reply(messages[-1].content)

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
          gpt_messages.append({"role": "user", "content": f"{get_user_nickname(msg.author)}「{msg.content}」"})
          await self.reply_to(msg, gpt_messages)
      else:
        # 人間から送信された場合、通常の処理
        gpt_messages.append({"role": "user", "content": f"{get_user_nickname(msg.author)}「{msg.content}」"})
        await self.reply_to(msg, gpt_messages)

    except asyncio.TimeoutError:
      # メッセージが一定時間内に返信されなかった場合
      pass


async def setup(bot: commands.Bot):
  await bot.add_cog(EventsCog(bot))
