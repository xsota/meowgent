import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cogs.events_cog import ConversationMessage, EventsCog, ShortTermMemory


def fake_message(
  *,
  message_id=1,
  channel_id=10,
  author_id=100,
  author_name="sota",
  content="hello",
  created_at=None,
  attachments=None,
  reference=None,
):
  return SimpleNamespace(
    id=message_id,
    channel=SimpleNamespace(id=channel_id),
    author=SimpleNamespace(id=author_id, name=author_name, nick=None, bot=False),
    content=content,
    created_at=created_at or datetime(2026, 6, 5, tzinfo=timezone.utc),
    attachments=attachments or [],
    reference=reference,
  )


def fake_cog(bot_user_id=999):
  cog = EventsCog.__new__(EventsCog)
  cog.bot = SimpleNamespace(user=SimpleNamespace(id=bot_user_id))
  cog.short_term_memory = ShortTermMemory(cog.MAX_HISTORY_LENGTH)
  cog.channel_message_history = {}
  return cog


class ShortTermMemoryTest(unittest.TestCase):
  def test_keeps_channels_separate_and_trims_old_messages(self):
    memory = ShortTermMemory(max_length=2)
    base_time = datetime(2026, 6, 5, tzinfo=timezone.utc)

    for message_id in range(3):
      memory.add(ConversationMessage(
        message_id=message_id,
        channel_id=10,
        author_id=100,
        author_name="sota",
        role="user",
        content=f"message {message_id}",
        created_at=base_time + timedelta(minutes=message_id),
      ))
    memory.add(ConversationMessage(
      message_id=100,
      channel_id=20,
      author_id=100,
      author_name="sota",
      role="user",
      content="other channel",
      created_at=base_time,
    ))

    self.assertEqual([message.message_id for message in memory.get(10)], [1, 2])
    self.assertEqual([message.message_id for message in memory.get(20)], [100])

  def test_deduplicates_by_message_id(self):
    memory = ShortTermMemory(max_length=10)
    created_at = datetime(2026, 6, 5, tzinfo=timezone.utc)
    memory.add(ConversationMessage(1, 10, 100, "sota", "user", "old", created_at))
    memory.add(ConversationMessage(1, 10, 100, "sota", "user", "new", created_at))

    messages = memory.get(10)
    self.assertEqual(len(messages), 1)
    self.assertEqual(messages[0].content, "new")


class ConversationMessageTest(unittest.TestCase):
  def test_user_text_message_is_normalized_with_name_and_id(self):
    cog = fake_cog()
    message = fake_message(author_id=123, author_name="sota", content="hi")

    conversation_message = cog.to_conversation_message(message)

    self.assertEqual(conversation_message.role, "user")
    self.assertEqual(conversation_message.content, "sota:123 hi")

  def test_image_message_uses_openai_content_parts(self):
    cog = fake_cog()
    attachment = SimpleNamespace(content_type="image/png", url="https://example.com/image.png")
    message = fake_message(author_id=123, author_name="sota", content="look", attachments=[attachment])

    conversation_message = cog.to_conversation_message(message)

    self.assertEqual(conversation_message.role, "user")
    self.assertEqual(conversation_message.content[0], {"type": "text", "text": "sota:123 look"})
    self.assertEqual(conversation_message.content[1], {
      "type": "image_url",
      "image_url": {"url": "https://example.com/image.png"},
    })

  def test_voice_notification_assistant_message_becomes_system(self):
    cog = fake_cog()
    message = fake_message(content="sotaがgeneralに入ったにゃ！")

    conversation_message = cog.to_conversation_message(message, role="assistant")

    self.assertEqual(conversation_message.role, "system")
    self.assertEqual(conversation_message.content, "sotaがgeneralに入ったにゃ！")


class HistoryFetchDecisionTest(unittest.TestCase):
  def test_fetches_when_memory_is_empty_or_too_small(self):
    cog = fake_cog()
    message = fake_message()
    self.assertTrue(cog.should_fetch_discord_history(message, []))

    memory_messages = [
      ConversationMessage(1, 10, 100, "sota", "user", "one", message.created_at),
      ConversationMessage(2, 10, 100, "sota", "user", "two", message.created_at),
    ]
    self.assertTrue(cog.should_fetch_discord_history(message, memory_messages))

  def test_fetches_when_reply_reference_is_missing(self):
    cog = fake_cog()
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    message = fake_message(message_id=4, created_at=now, reference=SimpleNamespace(message_id=99))
    memory_messages = [
      ConversationMessage(1, 10, 100, "sota", "user", "one", now),
      ConversationMessage(2, 10, 100, "sota", "user", "two", now),
      ConversationMessage(4, 10, 100, "sota", "user", "current", now),
    ]

    self.assertTrue(cog.should_fetch_discord_history(message, memory_messages))

  def test_fetches_when_bot_is_mentioned_and_memory_is_not_full(self):
    cog = fake_cog(bot_user_id=999)
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    message = fake_message(message_id=4, content="<@999> hi", created_at=now)
    memory_messages = [
      ConversationMessage(1, 10, 100, "sota", "user", "one", now),
      ConversationMessage(2, 10, 100, "sota", "user", "two", now),
      ConversationMessage(4, 10, 100, "sota", "user", "current", now),
    ]

    self.assertTrue(cog.should_fetch_discord_history(message, memory_messages))

  def test_fetches_when_previous_memory_is_older_than_gap(self):
    cog = fake_cog()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    message = fake_message(message_id=4, created_at=now)
    memory_messages = [
      ConversationMessage(1, 10, 100, "sota", "user", "one", now - timedelta(minutes=10)),
      ConversationMessage(2, 10, 100, "sota", "user", "two", now - timedelta(minutes=6)),
      ConversationMessage(4, 10, 100, "sota", "user", "current", now),
    ]

    self.assertTrue(cog.should_fetch_discord_history(message, memory_messages))

  def test_does_not_fetch_for_recent_full_enough_memory(self):
    cog = fake_cog()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    message = fake_message(message_id=4, created_at=now)
    memory_messages = [
      ConversationMessage(1, 10, 100, "sota", "user", "one", now - timedelta(minutes=2)),
      ConversationMessage(2, 10, 100, "sota", "user", "two", now - timedelta(minutes=1)),
      ConversationMessage(4, 10, 100, "sota", "user", "current", now),
    ]

    self.assertFalse(cog.should_fetch_discord_history(message, memory_messages))


class FakeAsyncHistory:
  def __init__(self, messages):
    self.messages = list(messages)

  def __aiter__(self):
    return self

  async def __anext__(self):
    if not self.messages:
      raise StopAsyncIteration
    return self.messages.pop(0)


class ConversationContextTest(unittest.TestCase):
  def test_build_context_fetches_discord_history_and_returns_oldest_first(self):
    async def run_test():
      cog = fake_cog()
      now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
      channel = SimpleNamespace(id=10)
      older = fake_message(message_id=1, channel_id=10, content="older", created_at=now - timedelta(minutes=2))
      newer = fake_message(message_id=2, channel_id=10, content="newer", created_at=now - timedelta(minutes=1))
      current = fake_message(message_id=3, channel_id=10, content="current", created_at=now)
      older.channel = channel
      newer.channel = channel
      current.channel = channel
      channel.history = lambda limit: FakeAsyncHistory([current, newer, older])

      context = await cog.build_conversation_context(current)

      self.assertEqual(
        [message["content"] for message in context],
        ["sota:100 older", "sota:100 newer", "sota:100 current"],
      )

    asyncio.run(run_test())


if __name__ == "__main__":
  unittest.main()
