# Memory System

How the bot's conversation memory, summarization, fact extraction, and session detection work.

---

## Overview

The bot maintains three layers of context for each user, all stored in SQLite (`db/conversations.sqlite3`):

```
┌─────────────────────────────────────────────────────────────┐
│                    Context Window                           │
│                   (CONTEXT_CHAR_BUDGET)                     │
│                                                             │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │   FACTS      │   │  SUMMARY         │   │  VERBATIM   │ │
│  │  (25% budget)│   │ (50% remaining)  │   │  TAIL       │ │
│  │              │   │                  │   │ (rest)      │ │
│  │ - user's     │   │ Rolling LLM-     │   │ Last N      │ │
│  │   name       │   │ generated        │   │ messages    │ │
│  │ - lives in   │   │ summary of older │   │ (exact     │ │
│  │   Jakarta    │   │ messages         │   │  text)     │ │
│  │ - prefers    │   │                  │   │             │ │
│  │   dark anime │   │ "User and Yuki  │   │ USER: hi    │ │
│  │              │   │  discussed anime│   │ ASSISTANT:  │ │
│  │              │   │  last week..."   │   │   onii-chan!│ │
│  └──────────────┘   └──────────────────┘   └─────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### `messages` table

Stores all conversation messages (both user and bot).

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    message_content TEXT,
    timestamp INTEGER,      -- unix seconds
    from_me BOOLEAN,        -- True = bot, False = user
    UNIQUE(user_id, message_content, timestamp, from_me)
);
```

- `from_me=True` → bot's response (speaker: ASSISTANT)
- `from_me=False` → user's message (speaker: USER)
- The `UNIQUE` constraint prevents duplicate inserts (same user + content + timestamp + direction).

### `summaries` table

Stores the rolling conversation summary per user.

```sql
CREATE TABLE summaries (
    user_id TEXT PRIMARY KEY,
    summary_text TEXT,
    last_summarized_timestamp INTEGER  -- unix seconds
);
```

- One row per user (upserted on each summarization).
- `last_summarized_timestamp` tracks how far the summary has progressed. New messages after this timestamp are candidates for the next summarization.

### `facts` table

Stores durable extracted facts per user.

```sql
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    fact TEXT,
    entity TEXT,            -- normalized (lowercase, no punctuation)
    confidence REAL DEFAULT 0.5,  -- 0.0 to 1.0
    created_at INTEGER,     -- unix seconds
    last_confirmed INTEGER  -- unix seconds (updated on re-extraction)
);
```

- Facts are upserted: if the same `(user_id, entity, fact)` is found, `confidence` is updated to `max(old, new)` and `last_confirmed` is refreshed.
- This means repeated facts get higher confidence over time.

---

## Session Detection

A "session" is a continuous conversation. A new session starts when the user is silent for more than `SESSION_GAP_SECONDS` (default 3600 = 1 hour).

### `is_new_session(user_id)`

Returns `(is_new_session, is_first_ever)`:

| Condition | `is_new_session` | `is_first_ever` |
|-----------|-----------------|-----------------|
| No messages in DB | `True` | `True` |
| Last message > `SESSION_GAP_SECONDS` ago | `True` | `False` |
| Last message within gap | `False` | `False` |

### How it's used

When a message arrives:

```python
is_new, is_first = is_new_session(sender_id)
stats = get_session_stats(sender_id)
```

- **`is_first=True`**: First-ever message → `handle_greeting()` (special greeting prompt).
- **`is_new=True` (not first)**: Returning after a gap → the LLM is told "New session. Last conversation ended X hours ago."
- **`is_new=False`**: Ongoing session → the LLM is told "Ongoing session (X min active, Y total messages)."

### `get_session_stats(user_id)`

Returns a dict of session statistics:

```python
{
    "user_id": "6287711076107",
    "message_count": 42,
    "session_start": 1727268000,
    "last_message_time": 1727271600,
    "session_age_seconds": 3600,
    "session_gap_seconds": 120,
    "is_new_session": False,
    "is_first_ever": False,
    "estimated_bytes": 3072,
}
```

---

## Context Building (`build_context`)

When generating a response, `build_context()` assembles the context from three layers within a character budget:

```python
def build_context(user_id, current_message, char_budget=None):
    if char_budget is None:
        char_budget = CONTEXT_CHAR_BUDGET  # default 2000

    # 1. Get verbatim tail (last VERBATIM_TURNS messages)
    tail = get_verbatim_tail(user_id, VERBATIM_TURNS)  # default 10

    # 2. Get facts (most recent, max MAX_FACTS_IN_CONTEXT)
    facts = get_facts(user_id, MAX_FACTS_IN_CONTEXT)   # default 8

    # 3. Get summary
    summary_text, _ = get_summary(user_id)

    # 4. Budget allocation:
    #    reserved = len(current_message) + 60 (separators)
    #    available = char_budget - reserved
    #
    #    facts:    25% of available (highest priority)
    #    summary:  50% of remaining
    #    tail:     rest of budget

    # 5. Walk tail from newest to oldest, accumulating until budget hit
    # 6. Return: "[FACTS]\n..." + "[CONVERSATION SUMMARY]\n..." + tail lines
```

### Budget Allocation Example

With `CONTEXT_CHAR_BUDGET=2000` and a 200-char current message:

```
reserved   = 200 + 60 = 260
available  = 2000 - 260 = 1740

facts:     1740 // 4 = 435 chars
summary:  (1740 - 435) // 2 = 652 chars
tail:     1740 - 435 - 652 = 653 chars
```

### Truncation Rules

- **Facts**: If the facts text exceeds 25% of available, it's truncated to fit.
- **Summary**: Capped at 50% of remaining (after facts). Also hard-capped at 1200 chars during generation.
- **Tail**: Lines are added from newest to oldest until the budget is hit. Oldest messages in the tail may be dropped.

---

## Summarization (`summarize_session`)

When `message_count > VERBATIM_TURNS`, older messages are summarized:

```
messages: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
                                              ^^^^^^^^^^^^
                                              verbatim tail (10)
                ^^^^^^^^^^^^
                summarized into rolling summary
```

### Process

1. Get the verbatim tail (last `VERBATIM_TURNS` messages).
2. Get the existing summary (if any).
3. Find messages older than the tail that haven't been summarized yet (`timestamp > last_summarized_timestamp`).
4. Exclude messages already in the verbatim tail.
5. If there are messages to summarize, call the LLM with `SUMMARY_SYSTEM_PROMPT`.
6. Merge the result with the existing summary (the LLM is instructed to merge, not replace).
7. Cap at 1200 chars.
8. Save with `last_summarized_timestamp = max(timestamp of summarized messages)`.

### Summary Prompt

```
You are a conversation summarizer. Your task is to update a rolling summary.
Rules:
- Write in the same language as the conversation.
- Keep the summary under 200 words.
- Focus on: key facts, decisions, preferences, ongoing topics, unresolved threads.
- Drop trivial pleasantries.
- If there is an existing summary, MERGE it with the new messages.
- Be concise and factual.
```

### When it runs

```python
if not is_first and stats["message_count"] > VERBATIM_TURNS:
    summarize_session(sender_id)
```

- Skipped for first-ever messages (nothing to summarize).
- Skipped if there are no new messages to summarize (already summarized up to the latest).
- Runs synchronously (blocks the response). This is acceptable because it only triggers when the conversation exceeds the verbatim window, and the LLM call is typically 1-3s.

---

## Fact Extraction (`extract_facts`)

Runs in a **background thread** (non-blocking) for every non-command, non-file message:

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

### Pruning

Facts whose `last_confirmed` is older than `FACT_CUTOFF_DAYS` (default 60) are pruned by the cleanup thread. This means facts that are never re-confirmed eventually disappear.

---

## Pruning

### Message pruning

```python
def prune_old_messages(user_id=None, cutoff_days=None):
    cutoff = int(time.time()) - (cutoff_days * 86400)
    DELETE FROM messages WHERE timestamp < ?
```

- Triggered per-user when `message_count > 100` (in the message pipeline).
- Also run globally every 6 hours by the cleanup thread.
- Default cutoff: `MEMORY_CUTOFF_DAYS` = 30 days.

### Fact pruning

```python
def prune_stale_facts(cutoff_days=None):
    cutoff = int(time.time()) - (cutoff_days * 86400)
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
        prune_old_messages()
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
- `record_user_response()` is called after each successful response (greeting or final).

---

## Data Flow Summary

```
User message
    │
    ├──► save_message()          → messages table
    │
    ├──► is_new_session()        → session detection
    │
    ├──► get_session_stats()     → stats for prompt
    │
    ├──► prune_old_messages()    → if > 100 messages
    │
    ├──► summarize_session()     → if > VERBATIM_TURNS
    │       └──► save_summary()  → summaries table
    │
    ├──► extract_facts()         → background thread
    │       └──► save_fact()     → facts table
    │
    └──► build_context()         → assemble context
            ├── get_facts()      ← facts table
            ├── get_summary()    ← summaries table
            └── get_verbatim_tail() ← messages table
```
