from dataclasses import dataclass, field
from typing import Literal


MemoryKind = Literal["episode", "note"]
MemoryContextType = Literal["guild", "dm"]
MemoryAccessScope = Literal["GUILD", "USER_PRIVATE"]


@dataclass
class MemoryContext:
  """Request-scoped Discord context used to enforce memory policy."""

  requester_user_id: str
  guild_id: str | None
  channel_id: str
  context_type: MemoryContextType
  happened_at: str
  source_message_ids: tuple[str, ...] = ()
  remember_calls: int = field(default=0, init=False, repr=False)
  search_calls: int = field(default=0, init=False, repr=False)

  @property
  def access_scope(self) -> MemoryAccessScope:
    return "USER_PRIVATE" if self.context_type == "dm" else "GUILD"

  @property
  def owner_user_id(self) -> str | None:
    return self.requester_user_id if self.context_type == "dm" else None
