DISCORD_MESSAGE_LIMIT = 2000


def split_discord_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
  """Split text into chunks Discord accepts, preferring line and word boundaries."""
  chunks = []
  remaining = text.strip()
  while len(remaining) > limit:
    cut = remaining.rfind("\n", 0, limit + 1)
    if cut < limit // 2:
      cut = remaining.rfind(" ", 0, limit + 1)
    if cut < limit // 2:
      cut = limit
    chunk = remaining[:cut].rstrip()
    if chunk:
      chunks.append(chunk)
    remaining = remaining[cut:].lstrip()
  if remaining:
    chunks.append(remaining)
  return chunks
