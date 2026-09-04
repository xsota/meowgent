import asyncio
import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from memory.models import MemoryContext


class MemoryClientError(RuntimeError):
  def __init__(self, message: str, status: int | None = None, code: str | None = None):
    super().__init__(message)
    self.status = status
    self.code = code


class MemoryClientProtocol(Protocol):
  async def create_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
    ...

  async def search_memories(
    self,
    *,
    query: str,
    context: MemoryContext,
    subject_user_id: str | None = None,
    limit: int = 5,
  ) -> list[dict[str, Any]]:
    ...


class MemoryClient:
  """HTTPS client for the Memory Worker API.

  The bot only talks to this client. It never accesses D1 directly.
  """

  def __init__(self, api_url: str, api_token: str, timeout_seconds: float = 10.0):
    self.api_url = api_url.rstrip("/")
    self.api_token = api_token
    self.timeout_seconds = timeout_seconds

  async def create_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
    response = await self._request("POST", "/memories", payload)
    if not isinstance(response, dict):
      raise MemoryClientError("Memory API returned an invalid memory.")
    return response

  async def search_memories(
    self,
    *,
    query: str,
    context: MemoryContext,
    subject_user_id: str | None = None,
    limit: int = 5,
  ) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
      "query": query,
      "requester_user_id": context.requester_user_id,
      "guild_id": context.guild_id,
      "channel_id": context.channel_id,
      "context_type": context.context_type,
      "limit": limit,
    }
    if subject_user_id is not None:
      payload["subject_user_id"] = subject_user_id

    response = await self._request("POST", "/memories/search", payload)
    memories = response.get("memories") if isinstance(response, dict) else None
    if not isinstance(memories, list):
      raise MemoryClientError("Memory API returned an invalid search response.")
    return [memory for memory in memories if isinstance(memory, dict)]

  async def _request(
    self,
    method: str,
    path: str,
    payload: dict[str, Any],
  ) -> dict[str, Any]:
    if not self.api_url or not self.api_token:
      raise MemoryClientError("Memory API is not configured.")
    if not self._is_safe_url():
      raise MemoryClientError("Memory API URL must use HTTPS outside localhost.")

    return await asyncio.to_thread(self._request_sync, method, path, payload)

  def _is_safe_url(self) -> bool:
    parsed = urlparse(self.api_url)
    if parsed.scheme == "https" and parsed.netloc:
      return True
    return parsed.scheme == "http" and parsed.hostname in {
      "localhost",
      "127.0.0.1",
      "::1",
    }

  def _request_sync(
    self,
    method: str,
    path: str,
    payload: dict[str, Any],
  ) -> dict[str, Any]:
    request = Request(
      f"{self.api_url}/{path.lstrip('/')}",
      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
      headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {self.api_token}",
        "Content-Type": "application/json",
      },
      method=method,
    )

    try:
      with urlopen(request, timeout=self.timeout_seconds) as response:
        status = response.status
        body = response.read()
    except HTTPError as error:
      raise self._response_error(error.code, error.read()) from error
    except (URLError, TimeoutError, OSError) as error:
      raise MemoryClientError("Memory API is unavailable.") from error

    return self._decode_response(status, body)

  def _decode_response(self, status: int, body: bytes) -> dict[str, Any]:
    try:
      response = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
      raise MemoryClientError("Memory API returned invalid JSON.", status=status) from error

    if not isinstance(response, dict):
      raise MemoryClientError("Memory API returned an invalid response.", status=status)
    if status >= 400:
      raise self._response_error(status, body, response)
    return response

  def _response_error(
    self,
    status: int,
    body: bytes,
    response: dict[str, Any] | None = None,
  ) -> MemoryClientError:
    if response is None:
      try:
        decoded = json.loads(body.decode("utf-8"))
      except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = None
      response = decoded if isinstance(decoded, dict) else None

    error = response.get("error") if response else None
    if isinstance(error, dict):
      message = error.get("message")
      code = error.get("code")
      if isinstance(message, str) and message:
        return MemoryClientError(message, status=status, code=code if isinstance(code, str) else None)

    return MemoryClientError("Memory API request failed.", status=status)
