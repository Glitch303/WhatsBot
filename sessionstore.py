"""Session store: per-chat conversation sessions with titles and lineage.

Ported from DiscordBot's bot/store.py (Hermes-style session management),
adapted for WhatsApp: one database, text user_ids (phone numbers),
plain-text message content, and a one-time migration of the legacy
per-user ``messages``/``summaries`` tables into per-chat sessions.

Session keys:
  dm-{phone}      one session lineage per DM
  self            the bot's self-chat
  ch-{group jid}  shared group session (all speakers, [Name] labels)
"""

import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from config import CONV_DB_PATH

log = logging.getLogger(__name__)

PREVIEW_CHARS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    user_id TEXT,
    title TEXT,
    title_source TEXT DEFAULT '',
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    last_activity_at REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_key ON sessions(session_key, last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_title ON sessions(title);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL NOT NULL,
    user_id TEXT,
    sender TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT PRIMARY KEY,
    summary_text TEXT NOT NULL,
    last_summarized_timestamp REAL NOT NULL
);
"""


def _new_id() -> str:
    return "s-" + secrets.token_hex(6)


def _now() -> float:
    return time.time()


def _preview_text(content: str) -> str:
    text = (content or "").strip().replace("\n", " ")
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[: PREVIEW_CHARS - 1] + "…"


def _label_user_message(m: dict, sender, user_id) -> dict:
    """Prefix a user message with [Sender] — view-time only, stored content
    stays clean (the model needs to know who said what in a shared group)."""
    name = sender or (f"id:{user_id}" if user_id else None)
    if not name:
        return m
    content = m.get("content")
    if isinstance(content, str):
        m["content"] = f"[{name}] " + content
    return m


def _dedupe_repeated_replies(out: list) -> list:
    """Collapse consecutive identical assistant replies into one copy
    (keeps the most recent), then drop the leading assistant run so the
    history always starts with a user message."""
    deduped = []
    for m in out:
        if (
            m["role"] == "assistant"
            and deduped
            and deduped[-1]["role"] == "assistant"
            and deduped[-1]["content"] == m["content"]
        ):
            continue
        deduped.append(m)
    return deduped


class SessionStore:
    """One SQLite ledger for all conversation sessions.

    One connection, guarded by a lock (neonize callbacks and background
    threads share it). WAL mode for concurrent readers.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._rename_legacy_tables()
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate_legacy()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- migrations -----------------------------------------------------------

    def _rename_legacy_tables(self):
        """Move old-shape tables out of the way so SCHEMA can create the new
        ones (a same-named legacy table would shadow CREATE TABLE IF NOT EXISTS)."""
        with self._lock:
            msg_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(messages)")}
            if msg_cols and "session_id" not in msg_cols:
                self._conn.execute("ALTER TABLE messages RENAME TO messages_legacy")
            sum_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(summaries)")}
            if sum_cols and "user_id" in sum_cols:
                self._conn.execute("ALTER TABLE summaries RENAME TO summaries_legacy")
            self._conn.commit()

    def _migrate_legacy(self):
        """One-time data migration from the renamed legacy tables."""
        with self._lock:
            if self._table_exists("messages_legacy"):
                self._migrate_messages()
            if self._table_exists("summaries_legacy"):
                self._migrate_summaries()

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _migrate_messages(self):
        """Legacy messages (per user, from_me flag) -> one session per user."""
        users = [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT user_id FROM messages_legacy"
            )
        ]
        for user_id in users:
            first, last, cnt = self._conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) "
                "FROM messages_legacy WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            session_id = _new_id()
            self._conn.execute(
                "INSERT INTO sessions (id, session_key, user_id, started_at, last_activity_at, message_count) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, f"dm-{user_id}", user_id, first or _now(), last or _now(), cnt),
            )
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, user_id) "
                "SELECT ?, CASE WHEN from_me THEN 'assistant' ELSE 'user' END, "
                "message_content, timestamp, user_id "
                "FROM messages_legacy WHERE user_id = ?",
                (session_id, user_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) VALUES (?,?)",
                (f"dm-{user_id}", session_id),
            )
        self._conn.execute("DROP TABLE messages_legacy")
        self._conn.commit()
        log.info("Migrated %d legacy per-user conversations into sessions", len(users))

    def _migrate_summaries(self):
        """Legacy per-user summaries -> per-session summaries."""
        self._conn.execute(
            "INSERT INTO summaries (session_id, summary_text, last_summarized_timestamp) "
            "SELECT s.id, l.summary_text, l.last_summarized_timestamp "
            "FROM summaries_legacy l JOIN sessions s ON s.session_key = 'dm-' || l.user_id "
            "WHERE l.user_id IS NOT NULL"
        )
        self._conn.execute("DROP TABLE summaries_legacy")
        self._conn.commit()
        log.info("Migrated per-user summaries to per-session summaries")

    # ---- sessions ---------------------------------------------------------------

    def create_session(self, session_key, user_id=None, parent_session_id=None,
                       started_at=None) -> str:
        session_id = _new_id()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, session_key, user_id, parent_session_id, "
                "started_at, last_activity_at) VALUES (?,?,?,?,?,?)",
                (session_id, session_key, user_id, parent_session_id, started_at or now, now),
            )
            self._conn.commit()
        return session_id

    def ensure_session(self, session_key, user_id=None) -> str:
        """Return the chat's active session, creating one if none is active."""
        with self._lock:
            row = self._conn.execute(
                "SELECT c.session_id FROM chat_sessions c "
                "JOIN sessions s ON s.id = c.session_id "
                "WHERE c.session_key = ? AND s.ended_at IS NULL",
                (session_key,),
            ).fetchone()
            if row:
                return row["session_id"]
            session_id = self.create_session(session_key, user_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) VALUES (?,?)",
                (session_key, session_id),
            )
            self._conn.commit()
            return session_id

    def end_current(self, session_key, reason) -> str:
        """End the chat's active session and bind a fresh one (lineage kept)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            old_id = row["session_id"] if row else None
            if old_id:
                self._conn.execute(
                    "UPDATE sessions SET ended_at = ?, end_reason = ? "
                    "WHERE id = ? AND ended_at IS NULL",
                    (_now(), reason, old_id),
                )
            session_id = self.create_session(session_key)
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) VALUES (?,?)",
                (session_key, session_id),
            )
            if old_id:
                self._conn.execute(
                    "UPDATE sessions SET parent_session_id = ? "
                    "WHERE id = ? AND parent_session_id IS NULL",
                    (session_id, old_id),
                )
            self._conn.commit()
            return session_id

    def resume(self, session_key, session_id) -> bool:
        """Rebind the chat to an existing (ended or active) session."""
        with self._lock:
            target = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not target:
                return False
            row = self._conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            current = row["session_id"] if row else None
            if current and current != session_id:
                self._conn.execute(
                    "UPDATE sessions SET ended_at = ?, end_reason = ? "
                    "WHERE id = ? AND ended_at IS NULL",
                    (_now(), "superseded_by_resume", current),
                )
            self._conn.execute(
                "UPDATE sessions SET ended_at = NULL, end_reason = NULL, "
                "last_activity_at = ? WHERE id = ?",
                (_now(), session_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) VALUES (?,?)",
                (session_key, session_id),
            )
            self._conn.commit()
            return True

    def current_session(self, session_key):
        """Row for the chat's currently bound session, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT s.id, s.title, s.started_at, s.ended_at, s.end_reason, "
                "s.last_activity_at, s.message_count "
                "FROM chat_sessions c JOIN sessions s ON s.id = c.session_id "
                "WHERE c.session_key = ?",
                (session_key,),
            ).fetchone()
        return dict(row) if row else None

    # ---- messages ----------------------------------------------------------------

    def append(self, session_id, *messages, user_id=None, sender=None,
               timestamp=None) -> None:
        """Append rows to a session. ``user_id``/``sender`` stamp USER rows so
        shared group history can say who said what; ``timestamp`` is used for
        backfill (defaults to now)."""
        ts = float(timestamp) if timestamp is not None else _now()
        with self._lock:
            for role, content in messages:
                uid = user_id if role == "user" else None
                name = sender if role == "user" else None
                self._conn.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, "
                    "user_id, sender) VALUES (?,?,?,?,?,?)",
                    (session_id, role, content, ts, uid, name),
                )
            self._conn.execute(
                "UPDATE sessions SET message_count = message_count + ?, "
                "last_activity_at = MAX(last_activity_at, ?), "
                "started_at = MIN(started_at, ?) WHERE id = ?",
                (len(messages), ts, ts, session_id),
            )
            self._conn.commit()

    def history(self, session_id, max_messages, label_senders=False) -> list:
        """Most recent messages for the model context (oldest first).

        The window is measured in user turns. ``label_senders`` (shared group
        sessions) prefixes each user message with [Sender] at view time.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, user_id, sender FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, max_messages * 4),
            ).fetchall()
        kept, turns = [], 0
        for r in rows:  # newest first
            kept.append(r)
            if r["role"] == "user":
                turns += 1
                if turns >= max_messages:
                    break
        out = []
        for r in reversed(kept):
            m = {"role": r["role"], "content": r["content"]}
            if label_senders and m["role"] == "user":
                m = _label_user_message(m, r["sender"], r["user_id"])
            out.append(m)
        out = _dedupe_repeated_replies(out)
        while out and out[0]["role"] != "user":
            out = out[1:]
        return out

    def messages_since(self, session_id, since_timestamp) -> list:
        """Messages with timestamp > since_timestamp, oldest first.
        Returns [(content, timestamp, role), ...]."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT content, timestamp, role FROM messages "
                "WHERE session_id = ? AND timestamp > ? ORDER BY timestamp ASC",
                (session_id, since_timestamp),
            ).fetchall()
        return [(r["content"], r["timestamp"], r["role"]) for r in rows]

    def oldest_tail_timestamp(self, session_id, max_messages):
        """Timestamp of the oldest message inside the verbatim-tail window
        (the last N user turns and the replies between them)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY id DESC LIMIT ?",
                (session_id, max_messages),
            ).fetchall()
            if not rows:
                return None
            row = self._conn.execute(
                "SELECT MIN(timestamp) FROM messages "
                "WHERE session_id = ? AND id >= ?",
                (session_id, rows[-1]["id"]),
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def user_message_count(self, session_id) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()
        return row[0]

    def prune_old_messages(self, session_id=None, cutoff_days=None) -> int:
        """Delete messages older than cutoff_days (default MEMORY_CUTOFF_DAYS)."""
        from config import MEMORY_CUTOFF_DAYS
        if cutoff_days is None:
            cutoff_days = MEMORY_CUTOFF_DAYS
        cutoff = time.time() - cutoff_days * 86400
        with self._lock:
            if session_id:
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE session_id = ? AND timestamp < ?",
                    (session_id, cutoff),
                )
            else:
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
                )
            self._conn.execute(
                "UPDATE sessions SET message_count = "
                "(SELECT COUNT(*) FROM messages m WHERE m.session_id = sessions.id)"
            )
            self._conn.commit()
        return cur.rowcount

    # ---- summaries (per session) ----------------------------------------------------

    def save_summary(self, session_id, summary_text, last_summarized_timestamp):
        with self._lock:
            self._conn.execute(
                "INSERT INTO summaries (session_id, summary_text, last_summarized_timestamp) "
                "VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "summary_text = excluded.summary_text, "
                "last_summarized_timestamp = excluded.last_summarized_timestamp",
                (session_id, summary_text, last_summarized_timestamp),
            )
            self._conn.commit()

    def get_summary(self, session_id):
        """(summary_text, last_summarized_timestamp) or (None, None)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT summary_text, last_summarized_timestamp "
                "FROM summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return (row["summary_text"], row["last_summarized_timestamp"]) if row else (None, None)

    def clear_summary(self, session_id):
        with self._lock:
            self._conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
            self._conn.commit()

    # ---- titles -----------------------------------------------------------------------

    def set_title(self, session_id, title, source="user"):
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ?, title_source = ? WHERE id = ?",
                (title, source, session_id),
            )
            self._conn.commit()

    def session_title(self, session_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row["title"] if row else None

    # ---- listing / search / resolve -----------------------------------------------------

    def _list_rows(self, session_key, search=None) -> list:
        q = (
            "SELECT s.id, s.title, s.started_at, s.ended_at, s.message_count, "
            "s.last_activity_at, s.parent_session_id, "
            "(SELECT content FROM messages m WHERE m.session_id = s.id "
            "AND m.role = 'user' ORDER BY m.id LIMIT 1) AS preview "
            "FROM sessions s WHERE s.session_key = ?"
        )
        args = [session_key]
        if search:
            like = f"%{search}%"
            q += (
                " AND (s.title LIKE ? OR EXISTS (SELECT 1 FROM messages m "
                "WHERE m.session_id = s.id AND m.content LIKE ?))"
            )
            args += [like, like]
        q += " ORDER BY s.last_activity_at DESC LIMIT 15"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        current = self.current_session(session_key)
        current_id = current["id"] if current else None
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "title": r["title"] or "",
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "message_count": r["message_count"],
                "last_activity_at": r["last_activity_at"],
                "parent_session_id": r["parent_session_id"],
                "preview": _preview_text(r["preview"] or ""),
                "is_current": r["id"] == current_id,
            })
        return out

    def list_sessions(self, session_key) -> list:
        """Latest 15 sessions for this chat, most recent first."""
        return self._list_rows(session_key)

    def search_sessions(self, session_key, query) -> list:
        """Sessions matching a query (title or message content)."""
        return self._list_rows(session_key, search=query)

    def resolve(self, session_key, ref):
        """Resolve a reference (id, list number, or title) to a session id."""
        ref = (ref or "").strip()
        if not ref:
            return None
        # 1. id (exact, then unique prefix)
        if ref.startswith("s-"):
            with self._lock:
                row = self._conn.execute(
                    "SELECT id FROM sessions WHERE session_key = ? AND id = ?",
                    (session_key, ref),
                ).fetchone()
                if row:
                    return row["id"]
                rows = self._conn.execute(
                    "SELECT id FROM sessions WHERE session_key = ? AND id LIKE ? "
                    "ORDER BY last_activity_at DESC LIMIT 2",
                    (session_key, ref + "%"),
                ).fetchall()
                if len(rows) == 1:
                    return rows[0]["id"]
        # 2. number in the current listing
        if ref.isdigit():
            n = int(ref)
            rows = self._list_rows(session_key)
            if 1 <= n <= len(rows):
                return rows[n - 1]["id"]
        # 3. title (exact, then partial)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE session_key = ? AND title = ? "
                "ORDER BY last_activity_at DESC LIMIT 1",
                (session_key, ref),
            ).fetchone()
            if row:
                return row["id"]
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE session_key = ? AND title LIKE ? "
                "ORDER BY last_activity_at DESC LIMIT 1",
                (session_key, f"%{ref}%"),
            ).fetchone()
            if row:
                return row["id"]
        return None

    # ---- delete / reset -------------------------------------------------------------------

    def delete_session(self, session_key, session_id) -> dict:
        """Delete a session (and its messages/summary). If it was the chat's
        bound session, a fresh replacement is bound.
        Returns {id, title, new_session}."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title FROM sessions WHERE id = ? AND session_key = ?",
                (session_id, session_key),
            ).fetchone()
            if not row:
                return {"id": session_id, "title": None, "new_session": None}
            title = row["title"]
            bound = self._conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            was_bound = bool(bound and bound["session_id"] == session_id)
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
            self._conn.execute(
                "UPDATE sessions SET parent_session_id = NULL WHERE parent_session_id = ?",
                (session_id,),
            )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            new_session = None
            if was_bound:
                new_session = self.create_session(session_key)
                self._conn.execute(
                    "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) "
                    "VALUES (?,?)",
                    (session_key, new_session),
                )
            self._conn.commit()
            return {"id": session_id, "title": title, "new_session": new_session}

    def reset_key(self, session_key) -> int:
        """Delete every session for this chat and bind a fresh empty one.
        Returns the number of sessions deleted."""
        with self._lock:
            ids = [
                r[0]
                for r in self._conn.execute(
                    "SELECT id FROM sessions WHERE session_key = ?", (session_key,)
                )
            ]
            if ids:
                ph = ",".join("?" * len(ids))
                self._conn.execute(f"DELETE FROM messages WHERE session_id IN ({ph})", ids)
                self._conn.execute(f"DELETE FROM summaries WHERE session_id IN ({ph})", ids)
                self._conn.execute(
                    f"UPDATE sessions SET parent_session_id = NULL WHERE parent_session_id IN ({ph})",
                    ids,
                )
                self._conn.execute(f"DELETE FROM sessions WHERE id IN ({ph})", ids)
            session_id = self.create_session(session_key)
            self._conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (session_key, session_id) VALUES (?,?)",
                (session_key, session_id),
            )
            self._conn.commit()
        return len(ids)

    # ---- stats (menu) ---------------------------------------------------------------------

    def stats(self) -> dict:
        """Aggregate session stats for the standby menu."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
            named = self._conn.execute(
                "SELECT COUNT(*) c FROM sessions WHERE title IS NOT NULL AND title != ''"
            ).fetchone()["c"]
            msgs = self._conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
            bindings = self._conn.execute(
                "SELECT c.session_key, s.id, s.title, s.message_count, s.ended_at, "
                "s.last_activity_at "
                "FROM chat_sessions c JOIN sessions s ON s.id = c.session_id "
                "ORDER BY s.last_activity_at DESC LIMIT 10"
            ).fetchall()
        return {
            "total_sessions": total,
            "named_sessions": named,
            "total_messages": msgs,
            "bindings": [dict(r) for r in bindings],
        }


# Shared instance (created at import; db dir is created by SessionStore).
store = SessionStore(CONV_DB_PATH)
