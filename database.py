import sqlite3
import time
from config import CONV_DB_PATH, MAX_MESSAGES, VERBATIM_TURNS, MEMORY_CUTOFF_DAYS, SESSION_GAP_SECONDS

def init_db():
    """Initialize the SQLite database with a unique constraint."""
    # create folder if it doesn't exist

    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            message_content TEXT,
            timestamp INTEGER,
            from_me BOOLEAN,
            UNIQUE(user_id, message_content, timestamp, from_me)
        )
    """)
    conn.commit()
    conn.close()

def save_message(user_id, message_content, timestamp, from_me):
    """Insert a message into the DB if it doesn't already exist."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO messages (user_id, message_content, timestamp, from_me)
            VALUES (?, ?, ?, ?)
        """, (user_id, message_content, timestamp, from_me))
        conn.commit()
    except sqlite3.IntegrityError:
        # A message with the same (user_id, message_content, timestamp, from_me) already exists
        pass
    finally:
        conn.close()

def get_messages(user_id):
    """Retrieve all messages for a particular user_id."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_content, timestamp, from_me
        FROM messages
        WHERE user_id = ?
        ORDER BY timestamp
    """, (user_id,))
    results = cursor.fetchall()
    conn.close()
    return results

def get_recent_messages(user_id):
    """
    Retrieve the most recent `max_messages` for a particular user_id, ordered oldest to newest.
    """
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_content, timestamp, from_me
        FROM messages
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (user_id, MAX_MESSAGES))
    results = cursor.fetchall()
    conn.close()

    # Reverse the results to return them in chronological order
    return results[::-1]

def delete_messages(user_id):
    """Delete all messages for the specified user_id from the database."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_recent_messages_formatted(user_id):
    # Build a minimal text representation of the conversation
    conversation_history = get_recent_messages(user_id)
    lines = []
    for msg_content, msg_timestamp, from_me in conversation_history:
        if from_me:
            speaker = "ASSISTANT"
        else:
            speaker = "USER"
        lines.append(f"{speaker}: {msg_content}")

    conversation_text = "\n".join(lines)

    return conversation_text


# --- Session-aware helpers (Phase 1) ---

def get_last_message_time(user_id):
    """Return the timestamp (unix seconds) of the user's most recent message, or None."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(timestamp) FROM messages WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else None


def get_session_start(user_id, now=None):
    """
    Return the timestamp of the start of the current session.
    A new session begins if the gap since the last message exceeds SESSION_GAP_SECONDS.
    Returns None if the user has no messages yet.
    """
    if now is None:
        now = int(time.time())
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    # Find the most recent message within the session gap.
    # If none exists, the current message starts a new session.
    cutoff = now - SESSION_GAP_SECONDS
    cursor.execute("""
        SELECT MIN(timestamp) FROM messages WHERE user_id = ? AND timestamp >= ?
    """, (user_id, cutoff))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0]
    # No recent messages — check if there are any at all (for first-time detection)
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(timestamp) FROM messages WHERE user_id = ?
    """, (user_id,))
    last = cursor.fetchone()
    conn.close()
    return None  # caller decides: new session or first-ever


def is_new_session(user_id, now=None):
    """
    Returns (is_new_session, is_first_ever).
    is_new_session: True if the user has no messages OR the last message was > SESSION_GAP ago.
    is_first_ever: True if the user has no messages at all.
    """
    if now is None:
        now = int(time.time())
    last_time = get_last_message_time(user_id)
    if last_time is None:
        return True, True
    gap = now - last_time
    if gap > SESSION_GAP_SECONDS:
        return True, False
    return False, False


def get_verbatim_tail(user_id, limit=None):
    """
    Return the most recent `limit` messages as (content, timestamp, from_me) tuples,
    oldest to newest. Uses VERBATIM_TURNS as default limit.
    """
    if limit is None:
        limit = VERBATIM_TURNS
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message_content, timestamp, from_me
        FROM messages
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (user_id, limit))
    results = cursor.fetchall()
    conn.close()
    return list(reversed(results))


def get_session_stats(user_id, now=None):
    """
    Return a dict of session statistics for a user:
    - message_count: total messages stored
    - session_start: timestamp of current session start (or None)
    - last_message_time: timestamp of most recent message (or None)
    - session_age_seconds: how long the current session has been active
    - session_gap_seconds: time since last message
    - is_first_ever: whether this user has no prior messages
    - total_storage_bytes: approximate size of this user's data
    """
    if now is None:
        now = int(time.time())
    last_time = get_last_message_time(user_id)
    is_new, is_first = is_new_session(user_id, now)
    session_start = get_session_start(user_id, now) if not is_new else None

    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,))
    msg_count = cursor.fetchone()[0]
    # Estimate storage: avg message size * count (rough)
    cursor.execute("SELECT AVG(LENGTH(message_content)) FROM messages WHERE user_id = ?", (user_id,))
    avg_len = cursor.fetchone()[0] or 0
    conn.close()

    return {
        "user_id": user_id,
        "message_count": msg_count,
        "session_start": session_start,
        "last_message_time": last_time,
        "session_age_seconds": (now - session_start) if session_start else 0,
        "session_gap_seconds": (now - last_time) if last_time else None,
        "is_new_session": is_new,
        "is_first_ever": is_first,
        "estimated_bytes": int(msg_count * avg_len),
    }


def prune_old_messages(user_id=None, cutoff_days=None):
    """
    Delete messages older than cutoff_days (default MEMORY_CUTOFF_DAYS).
    If user_id is None, prunes for all users.
    Returns the number of rows deleted.
    """
    if cutoff_days is None:
        cutoff_days = MEMORY_CUTOFF_DAYS
    cutoff = int(time.time()) - (cutoff_days * 86400)
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    if user_id:
        cursor.execute("DELETE FROM messages WHERE user_id = ? AND timestamp < ?", (user_id, cutoff))
    else:
        cursor.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted