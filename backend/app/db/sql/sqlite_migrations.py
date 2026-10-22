"""SQLite schema migration helpers."""

SQLITE_PRAGMA_TABLE_INFO_SESSIONS = "PRAGMA table_info(sessions)"
SQLITE_ADD_CONVERSATION_SUMMARY = "ALTER TABLE sessions ADD COLUMN conversation_summary TEXT"
SQLITE_ADD_MEMORY_WINDOW_TOKENS = (
    "ALTER TABLE sessions ADD COLUMN memory_window_tokens INTEGER NOT NULL DEFAULT 0"
)
