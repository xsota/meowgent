import asyncio
import contextvars
import re
from contextlib import contextmanager
from typing import Any, Iterator, cast

from memory.client import MemoryClientError, MemoryClientProtocol
from memory.models import MemoryContext, MemoryKind


MAX_REMEMBER_CALLS = 3
MAX_SEARCH_CALLS = 5
MAX_SEARCH_RESULTS = 5
MAX_DUPLICATE_CANDIDATES = 50
MAX_CONTENT_LENGTH = 10_000
MAX_IDENTIFIER_LENGTH = 128


class MemoryServiceError(RuntimeError):
  pass


class MemoryService:
  """Policy layer between LLM tools and the remote Memory API."""

  def __init__(self, client: MemoryClientProtocol | None):
    self.client = client
    self._context_var: contextvars.ContextVar[MemoryContext | None] = contextvars.ContextVar(
      "memory_request_context",
      default=None,
    )
    self._remember_lock: asyncio.Lock | None = None

  @property
  def enabled(self) -> bool:
    return self.client is not None

  @contextmanager
  def bind_context(self, context: MemoryContext) -> Iterator[None]:
    token = self._context_var.set(context)
    try:
      yield
    finally:
      self._context_var.reset(token)

  async def remember(
    self,
    *,
    content: str,
    kind: str = "episode",
    importance: float = 0.5,
    confidence: float = 0.5,
    subject_user_id: str | int | None = None,
  ) -> dict[str, Any]:
    context = self._require_context()
    client = self._require_client()
    self._consume_remember_call(context)

    normalized_content = self._parse_content(content)
    normalized_kind = self._parse_kind(kind)
    normalized_importance = self._parse_score(importance, "importance", 0.5)
    normalized_confidence = self._parse_score(confidence, "confidence", 0.5)
    normalized_subject = self._parse_identifier(
      subject_user_id,
      "subject_user_id",
      default=context.requester_user_id,
    )

    if not context.source_message_ids:
      raise MemoryServiceError("Memory cannot be saved without source Discord messages.")

    # The Worker search is ACL-filtered. The exact normalized comparison below
    # prevents a lexical near-match from suppressing a genuinely new memory.
    duplicate_query = re.sub(r"\s+", " ", normalized_content).strip()
    async with self._get_remember_lock():
      try:
        candidates = await client.search_memories(
          query=duplicate_query[:1_000],
          context=context,
          subject_user_id=normalized_subject,
          limit=MAX_DUPLICATE_CANDIDATES,
        )
      except MemoryClientError as error:
        raise MemoryServiceError("Could not check existing memories before saving.") from error

      duplicate = next(
        (
          memory for memory in candidates
          if self._normalize_content(memory.get("content")) == self._normalize_content(normalized_content)
        ),
        None,
      )
      if duplicate is not None:
        return {
          "status": "duplicate",
          "memory": self._project_memory(duplicate),
        }

      payload = {
        "kind": normalized_kind,
        "content": normalized_content,
        "subject_user_id": normalized_subject,
        "guild_id": context.guild_id,
        "channel_id": context.channel_id,
        "happened_at": context.happened_at,
        "importance": normalized_importance,
        "confidence": normalized_confidence,
        # These policy fields are intentionally derived from the request context,
        # not accepted from the model's tool arguments.
        "access_scope": context.access_scope,
        "owner_user_id": context.owner_user_id,
        "source_message_ids": list(context.source_message_ids),
      }
      try:
        memory = await client.create_memory(payload)
      except MemoryClientError as error:
        raise MemoryServiceError("Could not save the memory.") from error

    return {
      "status": "created",
      "memory": self._project_memory(memory),
    }

  async def search(
    self,
    *,
    query: str,
    subject_user_id: str | int | None = None,
    limit: int = MAX_SEARCH_RESULTS,
  ) -> dict[str, Any]:
    context = self._require_context()
    client = self._require_client()
    self._consume_search_call(context)

    normalized_query = self._parse_query(query)
    normalized_subject = self._parse_identifier(subject_user_id, "subject_user_id")
    normalized_limit = self._parse_limit(limit)

    try:
      memories = await client.search_memories(
        query=normalized_query,
        context=context,
        subject_user_id=normalized_subject,
        limit=normalized_limit,
      )
    except MemoryClientError as error:
      raise MemoryServiceError("Could not search memories.") from error

    # Keep the check here as defense in depth if an alternate API client is used.
    if normalized_subject is not None:
      memories = [
        memory for memory in memories
        if memory.get("subject_user_id") == normalized_subject
      ]

    return {
      "status": "ok",
      "memories": [self._project_memory(memory) for memory in memories[:normalized_limit]],
    }

  def _require_context(self) -> MemoryContext:
    context = self._context_var.get()
    if context is None:
      raise MemoryServiceError("Memory tools require an active Discord conversation context.")
    return context

  def _require_client(self) -> MemoryClientProtocol:
    if self.client is None:
      raise MemoryServiceError("Memory API is not configured.")
    return self.client

  def _consume_remember_call(self, context: MemoryContext):
    if context.remember_calls >= MAX_REMEMBER_CALLS:
      raise MemoryServiceError("The memory save limit for this conversation turn has been reached.")
    context.remember_calls += 1

  def _consume_search_call(self, context: MemoryContext):
    if context.search_calls >= MAX_SEARCH_CALLS:
      raise MemoryServiceError("The memory search limit for this conversation turn has been reached.")
    context.search_calls += 1

  @staticmethod
  def _parse_content(value: Any) -> str:
    if not isinstance(value, str):
      raise MemoryServiceError("content must be a string.")
    normalized = value.strip()
    if not normalized:
      raise MemoryServiceError("content must not be empty.")
    if len(normalized) > MAX_CONTENT_LENGTH:
      raise MemoryServiceError(f"content must be at most {MAX_CONTENT_LENGTH} characters.")
    return normalized

  @staticmethod
  def _parse_query(value: Any) -> str:
    if not isinstance(value, str):
      raise MemoryServiceError("query must be a string.")
    normalized = value.strip()
    if not normalized:
      raise MemoryServiceError("query must not be empty.")
    if len(normalized) > 1_000:
      raise MemoryServiceError("query must be at most 1000 characters.")
    return normalized

  @staticmethod
  def _parse_kind(value: Any) -> MemoryKind:
    if not isinstance(value, str) or value.strip().lower() not in {"episode", "note"}:
      raise MemoryServiceError("kind must be episode or note.")
    return cast(MemoryKind, value.strip().lower())

  @staticmethod
  def _parse_score(value: Any, field: str, default: float) -> float:
    if value is None:
      return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
      raise MemoryServiceError(f"{field} must be a number between 0 and 1.")
    return float(value)

  @staticmethod
  def _parse_identifier(
    value: Any,
    field: str,
    default: str | None = None,
  ) -> str | None:
    if value is None:
      return default
    if isinstance(value, bool) or not isinstance(value, (str, int)):
      raise MemoryServiceError(f"{field} must be a string or safe integer.")
    normalized = str(value).strip()
    if not normalized or len(normalized) > MAX_IDENTIFIER_LENGTH:
      raise MemoryServiceError(f"{field} must contain between 1 and {MAX_IDENTIFIER_LENGTH} characters.")
    return normalized

  @staticmethod
  def _parse_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SEARCH_RESULTS:
      raise MemoryServiceError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}.")
    return value

  @staticmethod
  def _normalize_content(value: Any) -> str:
    if not isinstance(value, str):
      return ""
    return re.sub(r"\s+", " ", value).strip().casefold()

  @staticmethod
  def _project_memory(memory: dict[str, Any]) -> dict[str, Any]:
    sources = memory.get("sources")
    normalized_sources = []
    source_message_ids = []
    if isinstance(sources, list):
      for source in sources:
        if not isinstance(source, dict):
          continue
        source_type = source.get("source_type")
        source_id = source.get("source_id")
        if not isinstance(source_type, str) or not isinstance(source_id, str):
          continue
        normalized_sources.append({
          "source_type": source_type,
          "source_id": source_id,
        })
        if source_type == "discord_message":
          source_message_ids.append(source_id)

    return {
      "id": memory.get("id"),
      "kind": memory.get("kind"),
      "content": memory.get("content"),
      "subject_user_id": memory.get("subject_user_id"),
      "happened_at": memory.get("happened_at"),
      "importance": memory.get("importance"),
      "confidence": memory.get("confidence"),
      "access_scope": memory.get("access_scope"),
      "source_message_ids": source_message_ids,
      "sources": normalized_sources,
    }

  def _get_remember_lock(self) -> asyncio.Lock:
    if self._remember_lock is None:
      self._remember_lock = asyncio.Lock()
    return self._remember_lock
