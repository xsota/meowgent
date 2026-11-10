import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from discord_utils import split_discord_message
from llm import LLMResponse, ToolDefinition
from meowgent import Meowgent, current_run_context
from tools.task_manager import TaskManager
from tools.tasks import create_task_tools
from tools.web_fetch import UnsafeURLError, fetch_url, html_to_text, validate_public_url


def tool_call(call_id, name, arguments="{}"):
  return {
    "id": call_id,
    "call_id": call_id,
    "type": "function",
    "function": {"name": name, "arguments": arguments},
  }


def tool_response(*calls, response_id="resp"):
  return LLMResponse(
    content=None,
    tool_calls=list(calls),
    finish_reason="tool_calls",
    raw=None,
    response_id=response_id,
    status="completed",
  )


def text_response(text="done"):
  return LLMResponse(content=text, tool_calls=[], finish_reason="stop", raw=None, status="completed")


class FakeProvider:
  def __init__(self, responses):
    self.responses = list(responses)
    self.calls = []

  async def generate(self, messages, tools=None, max_tokens=None, tool_choice=None,
                     previous_response_id=None, input_items=None):
    self.calls.append({"tool_choice": tool_choice, "input_items": input_items})
    return self.responses.pop(0)


def tool(name, handler):
  return ToolDefinition(name=name, description=name, parameters={"type": "object", "properties": {}}, handler=handler)


def run_agent(meowgent, **config):
  return meowgent.ainvoke(
    {"messages": [{"role": "user", "content": "go"}], "current_channel_id": 10},
    config={"configurable": config},
  )


class AgentLoopTest(unittest.TestCase):
  def test_forces_a_text_answer_when_the_step_budget_is_exhausted(self):
    async def run_test():
      runs = []
      provider = FakeProvider([
        tool_response(tool_call("c1", "work")),
        tool_response(tool_call("c2", "work")),
        # A provider that ignores tool_choice=none must still not trigger tools.
        LLMResponse(content="partial answer", tool_calls=[tool_call("c3", "work")],
                    finish_reason="tool_calls", raw=None, status="completed"),
      ])
      meowgent = Meowgent(provider, [tool("work", lambda: runs.append(1) or "ok")], "system", max_steps=2)

      state = await run_agent(meowgent)

      self.assertEqual([call["tool_choice"] for call in provider.calls], [None, None, "none"])
      self.assertEqual(len(runs), 2)
      self.assertEqual(state["messages"][-1].role, "assistant")
      self.assertEqual(state["messages"][-1].content, "partial answer")
      self.assertIsNone(state["messages"][-1].tool_calls)

    asyncio.run(run_test())

  def test_runs_tool_calls_concurrently_and_keeps_call_order(self):
    async def run_test():
      async def slow(value):
        await asyncio.sleep(0.2)
        return value

      provider = FakeProvider([
        tool_response(tool_call("c1", "slow", '{"value":"a"}'), tool_call("c2", "slow", '{"value":"b"}')),
        text_response(),
      ])
      meowgent = Meowgent(provider, [tool("slow", slow)], "system")

      started = time.monotonic()
      await run_agent(meowgent)

      self.assertLess(time.monotonic() - started, 0.35)
      self.assertEqual(
        [(item["call_id"], item["output"]) for item in provider.calls[1]["input_items"]],
        [("c1", "a"), ("c2", "b")],
      )

    asyncio.run(run_test())

  def test_sync_tools_do_not_block_the_event_loop(self):
    async def run_test():
      ticks = []

      async def ticker():
        while True:
          ticks.append(1)
          await asyncio.sleep(0.02)

      provider = FakeProvider([tool_response(tool_call("c1", "block")), text_response()])
      meowgent = Meowgent(provider, [tool("block", lambda: time.sleep(0.2) or "ok")], "system")

      ticker_task = asyncio.create_task(ticker())
      await run_agent(meowgent)
      ticker_task.cancel()

      self.assertGreater(len(ticks), 3)

    asyncio.run(run_test())

  def test_tool_timeout_error_and_unknown_tool_are_reported_to_the_model(self):
    async def run_test():
      async def hang():
        await asyncio.sleep(5)

      def boom():
        raise RuntimeError("kaboom")

      provider = FakeProvider([
        tool_response(tool_call("c1", "hang"), tool_call("c2", "boom"), tool_call("c3", "missing")),
        text_response(),
      ])
      meowgent = Meowgent(provider, [tool("hang", hang), tool("boom", boom)], "system", tool_timeout_seconds=0.05)

      with self.assertLogs("meowgent", level="WARNING"):
        state = await run_agent(meowgent)

      outputs = [item["output"] for item in provider.calls[1]["input_items"]]
      self.assertIn("timed out", outputs[0])
      self.assertIn("kaboom", outputs[1])
      self.assertIn("not found", outputs[2])
      self.assertEqual(state["messages"][-1].content, "done")

    asyncio.run(run_test())

  def test_truncates_oversized_tool_output(self):
    async def run_test():
      provider = FakeProvider([tool_response(tool_call("c1", "big")), text_response()])
      meowgent = Meowgent(provider, [tool("big", lambda: "x" * 500)], "system", max_tool_output_chars=100)

      await run_agent(meowgent)

      output = provider.calls[1]["input_items"][0]["output"]
      self.assertTrue(output.startswith("x" * 100 + "\n...[truncated 400 characters]"))

    asyncio.run(run_test())

  def test_run_context_is_visible_to_tools_and_reset_afterwards(self):
    async def run_test():
      seen = []
      provider = FakeProvider([tool_response(tool_call("c1", "peek")), text_response()])
      meowgent = Meowgent(provider, [tool("peek", lambda: seen.append(current_run_context().channel_id) or "ok")], "system")

      await run_agent(meowgent)

      self.assertEqual(seen, [10])
      self.assertIsNone(current_run_context())

    asyncio.run(run_test())


class TaskToolsTest(unittest.TestCase):
  def test_schedule_list_cancel_scoped_to_the_current_channel(self):
    async def run_test():
      manager = TaskManager()
      manager.start_scheduler()
      ran = []

      async def run_task(channel_id, prompt):
        ran.append((channel_id, prompt))

      tools = {item.name: item for item in create_task_tools(manager, run_task)}

      async def call(current_channel_id, name, **args):
        provider = FakeProvider([
          tool_response(tool_call("c1", name, json.dumps(args))),
          text_response(),
        ])
        meowgent = Meowgent(provider, list(tools.values()), "system")
        await meowgent.ainvoke({"messages": [{"role": "user", "content": "go"}], "current_channel_id": current_channel_id})
        return json.loads(provider.calls[1]["input_items"][0]["output"])

      created = await call(10, "create_task", prompt="remind sota", minutes_later=5)
      self.assertEqual(created["status"], "scheduled")
      task_id = created["task"]["task_id"]

      self.assertIn("error", await call(10, "create_task", prompt="x", minutes_later=5, channel_id=99))
      self.assertIn("error", await call(10, "create_task", prompt="x", minutes_later=0))
      self.assertEqual([task["task_id"] for task in (await call(10, "list_tasks"))["tasks"]], [task_id])
      self.assertEqual((await call(20, "list_tasks"))["tasks"], [])
      self.assertIn("error", await call(20, "cancel_task", task_id=task_id))
      self.assertEqual((await call(10, "cancel_task", task_id=task_id))["status"], "cancelled")
      self.assertEqual((await call(10, "list_tasks"))["tasks"], [])
      self.assertEqual(manager.scheduler.get_jobs(), [])
      self.assertEqual(ran, [])
      manager.scheduler.shutdown(wait=False)

    asyncio.run(run_test())

  def test_due_task_runs_and_leaves_the_pending_list(self):
    async def run_test():
      from datetime import datetime, timedelta

      manager = TaskManager()
      manager.start_scheduler()
      ran = []

      async def run_task(channel_id, prompt):
        ran.append((channel_id, prompt))

      manager.add_task(run_task, datetime.now(manager.timezone) + timedelta(seconds=0.1), 10, "ping")
      await asyncio.sleep(0.4)

      self.assertEqual(ran, [(10, "ping")])
      self.assertEqual(manager.list_tasks(), [])
      manager.scheduler.shutdown(wait=False)

    asyncio.run(run_test())


class WebFetchTest(unittest.TestCase):
  def test_rejects_non_public_or_unusual_urls(self):
    for url in [
      "file:///etc/passwd",
      "ftp://example.com/",
      "http://127.0.0.1/",
      "http://169.254.169.254/latest/meta-data/",
      "http://[::1]/",
      "http://[fdaa::3]/",
      "http://10.0.0.5/",
      "http://user:pass@example.com/",
      "http://example.com:8080/",
      "http:///nohost",
    ]:
      with self.subTest(url=url):
        with self.assertRaises(UnsafeURLError):
          validate_public_url(url)
    self.assertEqual(validate_public_url("https://example.com/a?b=c"), "https://example.com/a?b=c")

  def test_refuses_host_names_that_resolve_to_private_addresses(self):
    result = asyncio.run(fetch_url("http://localhost/"))
    self.assertEqual(result, {"error": "The URL does not point to a public address."})

  def test_decimal_ip_host_is_never_fetched(self):
    # urllib does not see this as an IP literal, so it must fail further down.
    with self.assertLogs("tools.web_fetch", level="WARNING"):
      result = asyncio.run(fetch_url("http://2130706433/"))
    self.assertEqual(list(result), ["error"])

  def test_extracts_readable_text_from_html(self):
    title, text = html_to_text(
      "<html><head><title> Hello  World </title><style>p{color:red}</style></head>"
      "<body><script>alert(1)</script><h1>Heading</h1><p>First   line</p><p>Second &amp; last</p></body></html>"
    )
    self.assertEqual(title, "Hello World")
    self.assertEqual(text, "Heading\nFirst line\nSecond & last")


class SplitDiscordMessageTest(unittest.TestCase):
  def test_short_text_is_one_chunk_and_empty_text_is_none(self):
    self.assertEqual(split_discord_message("hello"), ["hello"])
    self.assertEqual(split_discord_message("   "), [])

  def test_long_text_is_split_within_the_limit_without_losing_content(self):
    text = "\n".join(f"line {index} " + "あ" * 80 for index in range(60))
    chunks = split_discord_message(text)
    self.assertGreater(len(chunks), 1)
    self.assertTrue(all(0 < len(chunk) <= 2000 for chunk in chunks))
    self.assertEqual("\n".join(chunks), text)

    unbroken = "x" * 4500
    self.assertEqual([len(chunk) for chunk in split_discord_message(unbroken)], [2000, 2000, 500])


if __name__ == "__main__":
  unittest.main()
