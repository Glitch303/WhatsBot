# Memory System

How the bot's conversation memory, sessions, summarization, and fact extraction work.

---

## Overview

The bot keeps **one session per chat** (DM, self-chat, or group) in SQLite (`db/conversations.sqlite3`). Every LLM call sends:

```
┌─────────────────────────────────────────────────────────────┐
│  system prompt                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ SOUL.md persona (re-read on every message)            │  │
│  │ + owner note (DM) or group-chat note (groups)         │  │
│  │ + session note (first-ever / "new session, ended Xh") │  │
│  │ + [FACTS about this user] (per-user, durable)         │  │
│  │ + [CONVERSATION SUMMARY] (per-session, rolling)       │  │
│  │ + [ADDITIONAL CONTENT] (converted/ file uploads)      │  │
│  └───────────────────────────────────────────────────────┘  │
│  history: last VERBATIM_TURNS user turns + replies          │
│            (in groups, user messages carry [Name] labels)   │
│  current user turn                                          │
└─────────────────────────────────────────────────────────────┘
```

There is no character budget: the context is the persona + memory blocks + a fixed-size verbatim tail. Older messages are folded into the rolling summary instead of being dropped.

---

## Sessions

### Session keys

| Key | Chat type |
|-----|-----------|
| `dm-{phone}` | One session lineage per DM |
| `self` | The bot's own self-chat |
| `ch-{group jid}` | Shared group session (all speakers, `[Name]` labels) |

Each chat has exactly one **active** session (the `chat_sessions` binding). Ending a session (`!new`, `!reset`) creates a new one; the old one stays in the ledger, resumable with `!resume`.

### Lifecycle

```
ensure_session(key)          → return the active session, or create one
append(session_id, ...)      → store a message, bump last_activity_at
end_current(key, reason)     → end the active session + create a fresh one
resume(key, session_id)      → rebind the chat to a previous session
delete_session(key, id)      → delete a session (bound → create replacement)
reset_key(key)               → delete all sessions for the chat + fresh bind
```

Session rows keep lineage: `parent_session_id` points to the previous session in the chat's chain, and `end_reason` records why it ended (`new`, `reset`, `superseded_by_resume`, …).

### Titles

Sessions are untitled by default. `!title` sets one (`title_source='user'`); the ledger and `!sessions` show them. `!sessions`/`!resume`/`!delete` accept a list number, a title (exact or partial), or a session ID (full or `s-` prefix).

---

## Database Schema

### `sessions` table

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,            -- "s-" + 12 hex chars
    session_key TEXT NOT NULL,      -- dm-{phone} | self | ch-{group jid}
    user_id TEXT,                   -- owner phone (DMs/self) or last actor
    title TEXT,
    title_source TEXT DEFAULT '',   -- 'user' when set via !title
    parent_session_id TEXT,         -- previous session in the chat's chain
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    last_activity_at REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_sessions_key ON sessions(session_key, last_activity_at DESC);
CREATE INDEX idx_sessions_title ON sessions(title);
```

### `messages` table

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,             -- 'user' | 'assistant'
    content TEXT NOT NULL,          -- plain text (group sender is in `sender`)
    timestamp REAL NOT NULL,
    user_id TEXT,
    sender TEXT                     -- display name (groups; view-time [Name] label source)
);
CREATE INDEX idx_messages_session ON messages(session_id, id);
```

### `chat_sessions` table

```sql
CREATE TABLE chat_sessions (
    session_key TEXT PRIMARY KEY,   -- one active session per chat
    session_id TEXT NOT NULL
);
```

### `summaries` table

```sql
CREATE TABLE summaries (
    session_id TEXT PRIMARY KEY,    -- per session (not per user)
    summary_text TEXT NOT NULL,
    last_summarized_timestamp REAL NOT NULL
);
```

### `facts` table

```sql
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    fact TEXT,
    entity TEXT,
    confidence REAL DEFAULT 0.5,
    created_at INTEGER,
    last_confirmed INTEGER
);
```

---

## Context Assembly (`llm.build_convo`)

For each reply, the conversation is assembled as:

1. **System prompt** (`build_system_prompt`):
   - `SOUL.md` content (re-read from disk on every message — edits apply live; falls back to a generic persona if the file is missing/empty).
   - **DMs:** an owner note — "The person messaging you right now is your owner: {OWNER_NAME}. Use your private face." or "Your owner is {OWNER_NAME}. The person messaging you right now is not them. Use your public face."
   - **Groups:** the group-chat note (`[Name]` labels explained, only speak when @mentioned, pronouns point at the speaker) + an `## Owner` section.
   - **Session note** (when applicable): "This is the user's first message ever." or "New session. Last conversation ended X hours ago." (when the session's last activity is older than `SESSION_GAP_SECONDS`).
   - **Facts** (`[FACTS about this user]`): the `MAX_FACTS_IN_CONTEXT` most recently confirmed facts for the sender.
   - **Summary** (`[CONVERSATION SUMMARY]`): the session's rolling summary, if any.
   - **Files** (`[ADDITIONAL CONTENT]`): concatenated text of everything in `converted/` (admin uploads).
2. **History** (`store.history`): the last `VERBATIM_TURNS` *user turns* and the assistant replies between them, oldest first. In group sessions, user messages are prefixed with `[Sender]` at view time (stored content stays clean). Consecutive identical assistant replies are collapsed, and a leading assistant run is dropped so the history always starts with a user message.
3. **Current turn**: the incoming message. In groups it is labeled `[Name] text` plus a face note: "(You are replying to your owner. Private face.)" or "(…not your owner. Public face.)".

Owner detection (`llm.is_owner`): exact phone match against `OWNER_NUMBER` when set, otherwise pushname match against `OWNER_NAME`. Self-chat is always the owner.

---

## Rolling Summary

When a session's user-turn count exceeds `VERBATIM_TURNS`, the bot summarizes the messages that just fell out of the verbatim window:

1. `store.oldest_tail_timestamp(session_id, VERBATIM_TURNS)` — timestamp of the oldest message still in the window.
2. `store.messages_since(session_id, since_ts)` — everything older than that (excluding the tail), formatted as `USER:` / `ASSISTANT:` lines.
3. The LLM merges them into the existing summary (`SUMMARY_SYSTEM_PROMPT`, same language as the conversation, < 200 words) — capped at 1200 chars.
4. `store.save_summary(session_id, text, max_ts)`.

Summaries are **per session**: `!new`/`!reset` start with a blank summary; `!resume` restores the resumed session's summary.

---

## Fact Extraction

Facts are extracted in a **background thread** (non-blocking) for every non-command, non-file message:

```python
threading.Thread(
    target=_extract_facts_async,
    args=(sender_id, text),
    daemon=True
).start()
```

### Process

1. Call the LLM with `FACT_EXTRACTION_PROMPT`.
2. Parse the JSON array response.
3. For each fact (max 5 per message):
   - Normalize the entity name (lowercase, strip punctuation).
   - Upsert into the `facts` table (update confidence + `last_confirmed` if exists).
4. Log extracted facts.

### Fact Prompt

```
You are a fact extractor. Identify durable, verifiable facts.
Rules:
- Extract 0 to 5 facts.
- A "fact" is: names, preferences, relationships, events, plans, jobs,
  locations, habits.
- Do NOT extract: opinions, questions, general knowledge, pleasantries.
- Write each fact as a short sentence (max 15 words).
- Entity: person/thing the fact is about (lowercase, no punctuation).
- Confidence: 0.0 to 1.0 (0.9+ for explicit, 0.5-0.8 for implied).

Format: [{"fact": "...", "entity": "...", "confidence": 0.8}]
```

### Confidence

- **0.9+**: Explicit statement ("my name is ninn", "I live in Jakarta")
- **0.5-0.8**: Implied fact ("I'll be in Jakarta next week" → "user plans to visit Jakarta")
- Facts are upserted: if the same fact is extracted again, confidence becomes `max(old, new)` and `last_confirmed` is refreshed.

### Scope

Facts are **per user** (phone number), not per session — they follow the person across sessions and chats, and are injected into the system prompt for that user.

### Pruning

Facts whose `last_confirmed` is older than `FACT_CUTOFF_DAYS` (default 60) are pruned by the cleanup thread. This means facts that are never re-confirmed eventually disappear.

---

## Pruning

### Message pruning

```python
def prune_old_messages(session_id=None, cutoff_days=None):
    cutoff = time.time() - (cutoff_days * 86400)
    DELETE FROM messages WHERE timestamp < ?   -- (+ session_id filter)
```

- Triggered per-session when `message_count > 100` (in the message pipeline).
- Also run globally every 6 hours by the cleanup thread.
- Default cutoff: `MEMORY_CUTOFF_DAYS` = 30 days.

### Fact pruning

```python
def prune_stale_facts(cutoff_days=None):
    cutoff = time.time() - (cutoff_days * 86400)
    DELETE FROM facts WHERE last_confirmed < ?
```

- Run globally every 6 hours by the cleanup thread.
- Default cutoff: `FACT_CUTOFF_DAYS` = 60 days.

### Cleanup thread

```python
def _cleanup_thread():
    CLEANUP_INTERVAL = 6 * 3600  # 6 hours
    while True:
        time.sleep(CLEANUP_INTERVAL)
        store.prune_old_messages()
        prune_stale_facts()
        log.info(f"[CLEANUP] Pruned ...")
```

- Daemon thread, started in `main.py` via `start_cleanup_thread()`.
- Runs the first cleanup 6 hours after startup.

---

## Rate Limiting

Prevents spam by limiting responses per user:

```python
user_message_timestamps = defaultdict(lambda: deque(maxlen=5))

def can_respond_to_user(user_id):
    now = time.time()
    timestamps = user_message_timestamps[user_id]
    while timestamps and now - timestamps[0] > 30:
        timestamps.popleft()
    return len(timestamps) < 5
```

- Max **5 responses per user per 30-second window**.
- Timestamps are in-memory (lost on restart).
- `record_user_response()` is called after each successful response.

---

## Storage

The store keeps **one persistent SQLite connection** (WAL mode) guarded by a re-entrant lock — neonize event callbacks and background threads share it. No per-call connect/close.

---

## Data Flow Summary

```
User message
    │
    ├──► store.ensure_session(key)   → active session for this chat
    ├──► store.append(user msg)      → messages table
    │
    ├──► _session_note()             → first-ever / new-session note
    ├──► store.prune_old_messages()  → if message_count > 100
    ├──► summarize_session()         → if user turns > VERBATIM_TURNS
    │       └──► store.save_summary()
    │
    ├──► extract_facts()             → background thread
    │       └──► save_fact()         → facts table
    │
    └──► build_convo()               → assemble the LLM conversation
            ├── load_soul()          ← SOUL.md (fresh read)
            ├── get_facts()          ← facts table (per user)
            ├── store.get_summary()  ← summaries table (per session)
            ├── store.history()      ← messages table (verbatim tail)
            └── converted/ files     ← admin uploads
```
