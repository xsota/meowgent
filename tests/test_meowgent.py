import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm import LLMResponse, ToolDefinition
from meowgent import Meowgent


class FakeProvider:
  def __init__(self, responses):
    self.responses = list(responses)
    self.calls = []

  async def generate(
    self,
    messages,
    tools=None,
    max_tokens=None,
    tool_choice=None,
    previous_response_id=None,
    input_items=None,
  ):
    self.calls.append({
      "messages": messages,
      "tools": tools,
      "max_tokens": max_tokens,
      "tool_choice": tool_choice,
      "previous_response_id": previous_response_id,
      "input_items": input_items,
    })
    return self.responses.pop(0)


class MeowgentToolLoopTest(unittest.TestCase):
  def test_preserves_response_id_and_passes_function_output_to_next_turn(self):
    async def run_test():
      tool_calls = [{
        "id": "call_1",
        "call_id": "call_1",
        "type": "function",
        "function": {
          "name": "echo",
          "arguments": '{"value":"hello"}',
        },
      }]
      provider = FakeProvider([
        LLMResponse(
          content=None,
          tool_calls=tool_calls,
          finish_reason="tool_calls",
          raw=None,
          response_id="resp_1",
          status="completed",
        ),
        LLMResponse(
          content="done",
          tool_calls=[],
          finish_reason="stop",
          raw=None,
          response_id="resp_2",
          status="completed",
        ),
      ])
      tool = ToolDefinition(
        name="echo",
        description="Echo a value.",
        parameters={
          "type": "object",
          "properties": {"value": {"type": "string"}},
          "required": ["value"],
        },
        handler=lambda value: {"echo": value},
      )
      meowgent = Meowgent(provider=provider, tools=[tool], system_prompt="system")

      state = await meowgent.ainvoke({
        "messages": [{"role": "user", "content": "Run echo."}],
        "current_channel_id": 10,
      })

      self.assertEqual(len(provider.calls), 2)
      self.assertIsNone(provider.calls[0]["previous_response_id"])
      self.assertIsNone(provider.calls[0]["input_items"])
      self.assertEqual(provider.calls[1]["previous_response_id"], "resp_1")
      self.assertEqual(provider.calls[1]["input_items"], [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"echo": "hello"}',
      }])
      self.assertEqual(state["messages"][-1].content, "done")

    asyncio.run(run_test())

  def test_does_not_log_message_tool_or_provider_contents(self):
    async def run_test():
      private_memory = "private memory content that must not be logged"
      private_provider_output = "private provider output that must not be logged"
      tool_calls = [{
        "id": "call_1",
        "call_id": "call_1",
        "type": "function",
        "function": {
          "name": "search_memory",
          "arguments": '{"query":"private"}',
        },
      }]
      provider = FakeProvider([
        LLMResponse(
          content=None,
          tool_calls=tool_calls,
          finish_reason="tool_calls",
          raw={"private": private_provider_output},
          response_id="resp_1",
          status="completed",
        ),
        LLMResponse(
          content="done",
          tool_calls=[],
          finish_reason="stop",
          raw={"private": private_provider_output},
          response_id="resp_2",
          status="completed",
        ),
      ])
      tool = ToolDefinition(
        name="search_memory",
        description="Search memories.",
        parameters={
          "type": "object",
          "properties": {"query": {"type": "string"}},
          "required": ["query"],
        },
        handler=lambda query: {
          "status": "ok",
          "memories": [{"content": private_memory}],
        },
      )
      meowgent = Meowgent(provider=provider, tools=[tool], system_prompt="system")

      with self.assertLogs("meowgent", level="INFO") as logs:
        await meowgent.ainvoke({
          "messages": [{"role": "user", "content": private_memory}],
          "current_channel_id": 10,
        })

      logged = "\n".join(logs.output)
      self.assertNotIn(private_memory, logged)
      self.assertNotIn(private_provider_output, logged)

    asyncio.run(run_test())


if __name__ == "__main__":
  unittest.main()
