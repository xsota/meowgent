import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm import (
  LLMMessage,
  OpenAICompatibleResponsesProvider,
  ToolDefinition,
  response_to_llm_response,
)


def fake_response(
  *,
  response_id="resp_1",
  output=None,
  output_text="",
  status="completed",
  incomplete_reason=None,
):
  incomplete_details = None
  if incomplete_reason is not None:
    incomplete_details = SimpleNamespace(reason=incomplete_reason)
  return SimpleNamespace(
    id=response_id,
    output=output or [],
    output_text=output_text,
    status=status,
    incomplete_details=incomplete_details,
  )


class FakeResponses:
  def __init__(self, responses):
    self.responses = list(responses)
    self.requests = []

  async def create(self, **request):
    self.requests.append(request)
    return self.responses.pop(0)


def fake_tool():
  return ToolDefinition(
    name="get_current_time",
    description="Get the current time.",
    parameters={
      "type": "object",
      "properties": {"timezone_name": {"type": "string"}},
      "required": ["timezone_name"],
    },
    handler=lambda timezone_name: timezone_name,
  )


class ResponsesProviderTest(unittest.TestCase):
  def test_converts_messages_tools_and_reasoning_options(self):
    async def run_test():
      function_call = SimpleNamespace(
        type="function_call",
        id="fc_item_1",
        call_id="call_1",
        name="get_current_time",
        arguments='{"timezone_name":"Asia/Tokyo"}',
      )
      responses = FakeResponses([
        fake_response(output=[function_call]),
      ])
      provider = OpenAICompatibleResponsesProvider(
        model="gpt-5.6-luna",
        api_key="test-key",
        max_tokens=321,
        temperature=1,
        reasoning_effort="medium",
      )
      provider.client = SimpleNamespace(responses=responses)

      result = await provider.generate(
        [
          LLMMessage(role="system", content="You are helpful."),
          LLMMessage(role="system", content="current_channel_id: 10"),
          LLMMessage(
            role="user",
            content=[
              {"type": "text", "text": "What time is it?"},
              {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
            ],
          ),
        ],
        tools=[fake_tool()],
      )

      request = responses.requests[0]
      self.assertEqual(request["model"], "gpt-5.6-luna")
      self.assertEqual(request["instructions"], "You are helpful.\n\ncurrent_channel_id: 10")
      self.assertEqual(request["max_output_tokens"], 321)
      self.assertEqual(request["reasoning"], {"effort": "medium"})
      self.assertEqual(
        request["input"],
        [{
          "role": "user",
          "content": [
            {"type": "input_text", "text": "What time is it?"},
            {"type": "input_image", "image_url": "https://example.com/image.png", "detail": "auto"},
          ],
        }],
      )
      self.assertEqual(request["tools"], [{
        "type": "function",
        "name": "get_current_time",
        "description": "Get the current time.",
        "parameters": {
          "type": "object",
          "properties": {"timezone_name": {"type": "string"}},
          "required": ["timezone_name"],
        },
      }])
      self.assertEqual(result.response_id, "resp_1")
      self.assertEqual(result.tool_calls[0]["call_id"], "call_1")
      self.assertEqual(result.tool_calls[0]["function"]["name"], "get_current_time")
      self.assertEqual(result.finish_reason, "tool_calls")

    asyncio.run(run_test())

  def test_continuation_sends_only_function_outputs_with_previous_response_id(self):
    async def run_test():
      responses = FakeResponses([
        fake_response(response_id="resp_1"),
        fake_response(response_id="resp_2", output_text="The answer is ready."),
      ])
      provider = OpenAICompatibleResponsesProvider(
        model="gpt-5.6-luna",
        api_key="test-key",
        reasoning_effort="medium",
      )
      provider.client = SimpleNamespace(responses=responses)
      tool = fake_tool()
      messages = [
        LLMMessage(role="system", content="You are helpful."),
        LLMMessage(role="user", content="Use the tool."),
      ]

      await provider.generate(messages, [tool])
      result = await provider.generate(
        messages,
        [tool],
        previous_response_id="resp_1",
        input_items=[{
          "type": "function_call_output",
          "call_id": "call_1",
          "output": "12:00",
        }],
      )

      request = responses.requests[1]
      self.assertEqual(request["previous_response_id"], "resp_1")
      self.assertEqual(request["input"], [{
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "12:00",
      }])
      self.assertEqual(request["instructions"], "You are helpful.")
      self.assertEqual(result.content, "The answer is ready.")
      self.assertEqual(result.finish_reason, "stop")

    asyncio.run(run_test())

  def test_maps_max_output_tokens_to_length_finish_reason(self):
    response = fake_response(
      status="incomplete",
      incomplete_reason="max_output_tokens",
    )

    result = response_to_llm_response(response)

    self.assertEqual(result.finish_reason, "length")
    self.assertEqual(result.incomplete_reason, "max_output_tokens")


if __name__ == "__main__":
  unittest.main()
