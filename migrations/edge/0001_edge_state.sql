CREATE TABLE IF NOT EXISTS edge_state (
  key TEXT PRIMARY KEY NOT NULL,
  payload TEXT NOT NULL,
  checksum TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edge_state_updated_at ON edge_state(updated_at);
