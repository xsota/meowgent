CREATE TABLE IF NOT EXISTS memory_sources (
  id TEXT PRIMARY KEY NOT NULL,
  memory_id TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (length(trim(source_type)) > 0),
  source_id TEXT NOT NULL CHECK (length(trim(source_id)) > 0),
  created_at TEXT NOT NULL,
  FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS memory_sources_memory_id_idx
  ON memory_sources (memory_id, created_at);

CREATE INDEX IF NOT EXISTS memory_sources_lookup_idx
  ON memory_sources (source_type, source_id);
