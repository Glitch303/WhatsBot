"""Facts store + DB init.

Session/message/summary storage lives in sessionstore.py (SessionStore).
This module keeps the per-user durable facts table and DB initialization.
"""

import sqlite3
import time

from config import CONV_DB_PATH, FACT_CUTOFF_DAYS


def init_db():
    """Create the facts table (SessionStore creates the session tables)."""
    conn = sqlite3.connect(CONV_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact TEXT,
            entity TEXT,
            confidence REAL DEFAULT 0.5,
            created_at INTEGER,
            last_confirmed INTEGER
        )
    """)
    conn.commit()
    conn.close()


def save_fact(user_id, fact, entity, confidence, now=None):
    """
    Insert a fact, or update the existing one if (user_id, entity, fact) matches.
    Returns the fact ID.
    """
    if now is None:
        now = int(time.time())
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    # Check for existing fact with same user + entity + exact text
    cursor.execute("""
        SELECT id, confidence FROM facts
        WHERE user_id = ? AND entity = ? AND fact = ?
    """, (user_id, entity, fact))
    row = cursor.fetchone()
    if row:
        # Update confidence (keep the higher) and last_confirmed
        cursor.execute("""
            UPDATE facts SET confidence = ?, last_confirmed = ? WHERE id = ?
        """, (max(confidence, row[1]), now, row[0]))
        conn.commit()
        conn.close()
        return row[0]
    else:
        cursor.execute("""
            INSERT INTO facts (user_id, fact, entity, confidence, created_at, last_confirmed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, fact, entity, confidence, now, now))
        conn.commit()
        fact_id = cursor.lastrowid
        conn.close()
        return fact_id


def get_facts(user_id, limit=10):
    """
    Return the most recent/relevant facts for a user, ordered by last_confirmed DESC.
    Returns list of (id, fact, entity, confidence, created_at, last_confirmed).
    """
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, fact, entity, confidence, created_at, last_confirmed
        FROM facts
        WHERE user_id = ?
        ORDER BY last_confirmed DESC
        LIMIT ?
    """, (user_id, limit))
    results = cursor.fetchall()
    conn.close()
    return results


def delete_fact(fact_id):
    """Delete a fact by ID. Returns True if a row was deleted."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_fact_count(user_id=None):
    """Return total fact count (all users if user_id is None)."""
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT COUNT(*) FROM facts WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("SELECT COUNT(*) FROM facts")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def prune_stale_facts(cutoff_days=None):
    """
    Delete facts where last_confirmed is older than cutoff_days.
    Returns the number of rows deleted.
    """
    if cutoff_days is None:
        cutoff_days = FACT_CUTOFF_DAYS
    cutoff = int(time.time()) - (cutoff_days * 86400)
    conn = sqlite3.connect(CONV_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM facts WHERE last_confirmed < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted
