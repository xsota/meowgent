from typing import Any

from llm import ToolDefinition
from meowgent import current_run_context

MAX_HISTORY_MESSAGES = 50
MAX_MESSAGE_CHARS = 500


def create_discord_tools(bot) -> list[ToolDefinition]:
  async def read_channel_history(limit: int = 30) -> dict[str, Any]:
    context = current_run_context()
    channel = bot.get_channel(context.channel_id) if context and context.channel_id else None
    if channel is None or not hasattr(channel, "history"):
      return {"error": "The current channel history is not available."}
    if isinstance(limit, bool) or not isinstance(limit, int):
      limit = 30
    limit = min(max(limit, 1), MAX_HISTORY_MESSAGES)

    messages = []
    async for message in channel.history(limit=limit):
      content = message.content or ""
      attachments = [attachment.filename for attachment in getattr(message, "attachments", [])]
      if not content and not attachments:
        continue
      entry = {
        "message_id": str(message.id),
        "author": getattr(message.author, "display_name", None) or str(message.author),
        "author_id": str(message.author.id),
        "created_at": message.created_at.isoformat(timespec="seconds"),
        "content": content[:MAX_MESSAGE_CHARS],
      }
      if attachments:
        entry["attachments"] = attachments
      messages.append(entry)

    messages.reverse()
    return {"messages": messages}

  return [
    ToolDefinition(
      name="read_channel_history",
      description=(
        "Read older messages of the current Discord channel, oldest first. "
        "Use it when the visible conversation is not enough, e.g. to summarize or find what was said earlier."
      ),
      parameters={
        "type": "object",
        "properties": {
          "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_HISTORY_MESSAGES,
            "description": "Number of most recent messages to read.",
          },
        },
      },
      handler=read_channel_history,
    ),
  ]
