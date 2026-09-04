CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('episode', 'note')),
  content TEXT NOT NULL CHECK (length(trim(content)) > 0),
  subject_user_id TEXT,
  guild_id TEXT,
  channel_id TEXT,
  happened_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
  confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
  access_scope TEXT NOT NULL CHECK (
    access_scope IN ('PUBLIC', 'GUILD', 'CHANNEL', 'DM', 'USER_PRIVATE')
  ),
  owner_user_id TEXT,
  last_recalled_at TEXT,
  recall_count INTEGER NOT NULL DEFAULT 0 CHECK (recall_count >= 0),
  archived_at TEXT,
  CHECK (
    access_scope = 'PUBLIC'
    OR (access_scope = 'GUILD' AND guild_id IS NOT NULL)
    OR (access_scope = 'CHANNEL' AND guild_id IS NOT NULL AND channel_id IS NOT NULL)
    OR (access_scope = 'DM' AND guild_id IS NULL AND channel_id IS NOT NULL)
    OR (access_scope = 'USER_PRIVATE' AND guild_id IS NULL AND owner_user_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS memories_visibility_idx
  ON memories (access_scope, guild_id, channel_id, owner_user_id);

CREATE INDEX IF NOT EXISTS memories_recall_idx
  ON memories (archived_at, importance, happened_at);
