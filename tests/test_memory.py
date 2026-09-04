import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory.client import MemoryClient, MemoryClientError
from memory.models import MemoryContext
from memory.service import MAX_REMEMBER_CALLS, MemoryService, MemoryServiceError
from tools.memory import create_memory_tools


class FakeMemoryClient:
  def __init__(self):
    self.memories = []
    self.create_requests = []
    self.search_requests = []
    self.next_id = 1

  async def create_memory(self, payload):
    self.create_requests.append(payload)
    memory = {
      "id": f"memory-{self.next_id}",
      "kind": payload["kind"],
      "content": payload["content"],
      "subject_user_id": payload["subject_user_id"],
      "happened_at": payload["happened_at"],
      "importance": payload["importance"],
      "confidence": payload["confidence"],
      "access_scope": payload["access_scope"],
      "sources": [
        {
          "source_type": "discord_message",
          "source_id": source_id,
        }
        for source_id in payload["source_message_ids"]
      ],
    }
    self.next_id += 1
    self.memories.append(memory)
    return memory

  async def search_memories(self, *, query, context, subject_user_id=None, limit=5):
    self.search_requests.append({
      "query": query,
      "context": context,
      "subject_user_id": subject_user_id,
      "limit": limit,
    })
    memories = [memory for memory in self.memories if query.casefold() in memory["content"].casefold()]
    if subject_user_id is not None:
      memories = [
        memory for memory in memories
        if memory["subject_user_id"] == subject_user_id
      ]
    return memories[:limit]


def guild_context():
  return MemoryContext(
    requester_user_id="user-1",
    guild_id="guild-1",
    channel_id="channel-1",
    context_type="guild",
    happened_at="2026-09-04T12:00:00.000Z",
    source_message_ids=("message-1", "message-2"),
  )


class MemoryServiceTest(unittest.TestCase):
  def test_remember_derives_scope_and_sources_and_avoids_duplicates(self):
    async def run_test():
      client = FakeMemoryClient()
      service = MemoryService(client)

      with service.bind_context(guild_context()):
        created = await service.remember(
          content="sota bought a PCD1000.",
          kind="episode",
          importance=0.8,
          confidence=0.98,
        )
        duplicate = await service.remember(
          content="  SOTA   bought a PCD1000. ",
          kind="episode",
          importance=0.8,
          confidence=0.98,
        )

      self.assertEqual(created["status"], "created")
      self.assertEqual(duplicate["status"], "duplicate")
      self.assertEqual(len(client.create_requests), 1)
      self.assertEqual(client.create_requests[0]["access_scope"], "GUILD")
      self.assertEqual(client.create_requests[0]["guild_id"], "guild-1")
      self.assertIsNone(client.create_requests[0]["owner_user_id"])
      self.assertEqual(client.create_requests[0]["subject_user_id"], "user-1")
      self.assertEqual(client.create_requests[0]["source_message_ids"], ["message-1", "message-2"])

    asyncio.run(run_test())

  def test_dm_memory_is_private_to_the_requesting_user(self):
    async def run_test():
      client = FakeMemoryClient()
      service = MemoryService(client)
      context = MemoryContext(
        requester_user_id="user-1",
        guild_id=None,
        channel_id="dm-1",
        context_type="dm",
        happened_at="2026-09-04T12:00:00.000Z",
        source_message_ids=("message-1",),
      )

      with service.bind_context(context):
        await service.remember(
          content="sota values meowgent's personality.",
          kind="note",
          importance=0.9,
          confidence=0.8,
        )

      payload = client.create_requests[0]
      self.assertEqual(payload["access_scope"], "USER_PRIVATE")
      self.assertIsNone(payload["guild_id"])
      self.assertEqual(payload["channel_id"], "dm-1")
      self.assertEqual(payload["owner_user_id"], "user-1")

    asyncio.run(run_test())

  def test_remember_limit_is_enforced_per_context(self):
    async def run_test():
      client = FakeMemoryClient()
      service = MemoryService(client)
      context = guild_context()

      with service.bind_context(context):
        for index in range(MAX_REMEMBER_CALLS):
          await service.remember(
            content=f"durable event {index}",
            kind="episode",
          )
        with self.assertRaises(MemoryServiceError):
          await service.remember(content="one more durable event", kind="episode")

    asyncio.run(run_test())

  def test_memory_tools_do_not_accept_policy_or_source_fields(self):
    service = MemoryService(FakeMemoryClient())
    tools = create_memory_tools(service)

    self.assertEqual([tool.name for tool in tools], ["remember_memory", "search_memory"])
    remember_properties = tools[0].parameters["properties"]
    self.assertNotIn("access_scope", remember_properties)
    self.assertNotIn("source_message_ids", remember_properties)


class MemoryClientTest(unittest.TestCase):
  def test_search_sends_the_bound_discord_context_to_the_worker(self):
    async def run_test():
      client = MemoryClient("https://memory.example", "secret-token")
      requests = []

      async def fake_request(method, path, payload):
        requests.append((method, path, payload))
        return {"memories": []}

      client._request = fake_request
      context = guild_context()
      result = await client.search_memories(
        query="PCD1000",
        context=context,
        subject_user_id="user-1",
        limit=5,
      )

      self.assertEqual(result, [])
      self.assertEqual(requests[0][0:2], ("POST", "/memories/search"))
      self.assertEqual(requests[0][2], {
        "query": "PCD1000",
        "requester_user_id": "user-1",
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "context_type": "guild",
        "limit": 5,
        "subject_user_id": "user-1",
      })

    asyncio.run(run_test())

  def test_remote_memory_api_must_use_https(self):
    async def run_test():
      client = MemoryClient("http://memory.example", "secret-token")
      with self.assertRaises(MemoryClientError):
        await client.search_memories(query="PCD1000", context=guild_context())

    asyncio.run(run_test())


if __name__ == "__main__":
  unittest.main()
