from logging import getLogger
from typing import Any

from llm import ToolDefinition
from memory.service import MemoryService, MemoryServiceError


logger = getLogger(__name__)

MEMORY_SYSTEM_PROMPT = """
Long-term memory is available as a constrained tool.

Memories are your recollections from past experiences. They may be incomplete,
outdated, ambiguous, or incorrect, so do not treat them as unquestionable facts.

Use remember_memory when a conversation contains a durable preference, dislike,
long-term goal, project, purchase, important decision, relationship detail,
personal rule, value, meaningful change, or important event involving you and the
user. Do not save greetings, acknowledgements, laughter, one-off small talk, or
general knowledge that can be looked up easily. Prefer one concise episode over
several copies of the same event. A user explicitly asking you to remember
something is a strong reason to use the tool.

The runtime automatically supplies the source Discord messages and privacy scope.
Never try to reproduce or override those fields. Do not invent facts or sources.
Use search_memory only when you need to recall more detail than the current
conversation provides. Private recollections must never be exposed in a public
conversation.
""".strip()


def create_memory_tools(service: MemoryService) -> list[ToolDefinition]:
  async def remember_memory(
    content: str,
    kind: str = "episode",
    importance: float = 0.5,
    confidence: float = 0.5,
    subject_user_id: str | None = None,
  ) -> dict[str, Any]:
    try:
      return await service.remember(
        content=content,
        kind=kind,
        importance=importance,
        confidence=confidence,
        subject_user_id=subject_user_id,
      )
    except MemoryServiceError as error:
      return {"status": "error", "error": str(error)}
    except Exception:
      logger.exception("remember_memory failed")
      return {"status": "error", "error": "Memory save failed."}

  async def search_memory(
    query: str,
    subject_user_id: str | None = None,
    limit: int = 5,
  ) -> dict[str, Any]:
    try:
      return await service.search(
        query=query,
        subject_user_id=subject_user_id,
        limit=limit,
      )
    except MemoryServiceError as error:
      return {"status": "error", "error": str(error)}
    except Exception:
      logger.exception("search_memory failed")
      return {"status": "error", "error": "Memory search failed."}

  return [
    ToolDefinition(
      name="remember_memory",
      description=(
        "Save a durable recollection from the current Discord conversation. "
        "Use only for information likely to matter in future conversations."
      ),
      parameters={
        "type": "object",
        "additionalProperties": False,
        "properties": {
          "content": {
            "type": "string",
            "description": "A concise natural-language recollection of an event or note.",
            "maxLength": 10_000,
          },
          "kind": {
            "type": "string",
            "enum": ["episode", "note"],
            "description": "Use episode for an event and note for something worth remembering.",
          },
          "importance": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "How useful this memory is likely to be later, from 0 to 1.",
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "How directly and reliably the conversation supports this memory, from 0 to 1.",
          },
          "subject_user_id": {
            "type": "string",
            "description": "The Discord user the memory is about; omit to use the current user.",
            "maxLength": 128,
          },
        },
        "required": ["content", "kind", "importance", "confidence"],
      },
      handler=remember_memory,
    ),
    ToolDefinition(
      name="search_memory",
      description="Search memories visible in the current Discord context for additional recollection.",
      parameters={
        "type": "object",
        "additionalProperties": False,
        "properties": {
          "query": {
            "type": "string",
            "description": "A concise search query about the recollection you need.",
            "maxLength": 1_000,
          },
          "subject_user_id": {
            "type": "string",
            "description": "Optionally restrict results to memories about this Discord user.",
            "maxLength": 128,
          },
          "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "default": 5,
          },
        },
        "required": ["query"],
      },
      handler=search_memory,
    ),
  ]
