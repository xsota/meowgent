from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from llm import ToolDefinition
from meowgent import current_run_context
from tools.task_manager import TaskManager

MAX_PENDING_TASKS_PER_CHANNEL = 10
MAX_DELAY_MINUTES = 60 * 24 * 30
MAX_PROMPT_LENGTH = 2_000


def create_task_tools(
  task_manager: TaskManager,
  run_task: Callable[[int, str], Awaitable[None]],
) -> list[ToolDefinition]:
  """Tools for scheduling follow-up work. Every tool is scoped to the current channel."""

  def _current_channel_id() -> int | None:
    context = current_run_context()
    return context.channel_id if context else None

  # async so scheduler access stays on the event loop thread
  async def create_task(prompt: str, minutes_later: int, channel_id: int | None = None) -> dict[str, Any]:
    current_channel_id = _current_channel_id()
    if current_channel_id is None:
      return {"error": "Tasks can only be scheduled from a Discord conversation."}
    # channel_id stays in the schema for older prompts, but the task always
    # runs where it was requested so it cannot post into unrelated channels.
    if channel_id is not None and int(channel_id) != int(current_channel_id):
      return {"error": "Tasks can only be scheduled for the current channel."}
    if not isinstance(prompt, str) or not prompt.strip():
      return {"error": "prompt must not be empty."}
    if len(prompt) > MAX_PROMPT_LENGTH:
      return {"error": f"prompt must be at most {MAX_PROMPT_LENGTH} characters."}
    if isinstance(minutes_later, bool) or not isinstance(minutes_later, (int, float)):
      return {"error": "minutes_later must be a number."}
    if not 1 <= minutes_later <= MAX_DELAY_MINUTES:
      return {"error": f"minutes_later must be between 1 and {MAX_DELAY_MINUTES}."}
    if len(task_manager.list_tasks(current_channel_id)) >= MAX_PENDING_TASKS_PER_CHANNEL:
      return {"error": "Too many pending tasks in this channel. Cancel one first."}

    run_at = datetime.now(task_manager.timezone) + timedelta(minutes=minutes_later)
    task = task_manager.add_task(run_task, run_at, current_channel_id, prompt.strip())
    return {"status": "scheduled", "task": task.to_dict()}

  async def list_tasks() -> dict[str, Any]:
    current_channel_id = _current_channel_id()
    if current_channel_id is None:
      return {"error": "Tasks can only be listed from a Discord conversation."}
    return {"tasks": [task.to_dict() for task in task_manager.list_tasks(current_channel_id)]}

  async def cancel_task(task_id: str) -> dict[str, Any]:
    current_channel_id = _current_channel_id()
    if current_channel_id is None:
      return {"error": "Tasks can only be cancelled from a Discord conversation."}
    if task_manager.cancel_task(str(task_id), current_channel_id):
      return {"status": "cancelled", "task_id": task_id}
    return {"error": f"No pending task {task_id} in this channel."}

  return [
    ToolDefinition(
      name="create_task",
      description=(
        "Schedule yourself to act in the current channel after a delay, e.g. a reminder or a follow-up check. "
        "When the time comes you receive the prompt, can use tools, and your answer is posted to the channel. "
        "Pending tasks are lost if the bot restarts."
      ),
      parameters={
        "type": "object",
        "properties": {
          "prompt": {
            "type": "string",
            "description": (
              "Self-contained instruction for your future self, including who asked and what to tell them."
            ),
          },
          "minutes_later": {
            "type": "integer",
            "description": "Minutes from now when the task will execute.",
          },
        },
        "required": ["prompt", "minutes_later"],
      },
      handler=create_task,
    ),
    ToolDefinition(
      name="list_tasks",
      description="List the pending scheduled tasks for the current channel.",
      parameters={"type": "object", "properties": {}},
      handler=list_tasks,
    ),
    ToolDefinition(
      name="cancel_task",
      description="Cancel a pending scheduled task in the current channel by its task_id.",
      parameters={
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string",
            "description": "task_id returned by create_task or list_tasks.",
          },
        },
        "required": ["task_id"],
      },
      handler=cancel_task,
    ),
  ]
