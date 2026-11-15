# Architecture

How WhatsBot works under the hood: components, message flow, threading, state, and error handling.

---

## Overview

WhatsBot is a personal WhatsApp assistant built on [neonize](https://github.com/evkloev/neonize) (a Python client for WhatsApp Web via whatsmeow). It runs as a **single-process CLI app** with an interactive terminal menu.

```
┌─────────────────────────────────────────────────────────────┐
│                      WHATSBOT                               │
│                                                             │
│  ┌─────────────┐    ┌───────────────────────────────────┐   │
│  │   Menu      │    │      Message Pipeline             │   │
│  │  (terminal) │    │                                   │   │
│  │             │    │  on_message()                     │   │
│  │  model      │    │    ├── identity + LID resolve     │   │
│  │  settings   │    │    ├── gate (dm/group/self)       │   │
│  │  sessions   │    │    ├── commands / files           │   │
│  │  live view  │    │    ├── session store (SQLite)     │   │
│  │             │    │    ├── summarize + facts (bg)     │   │
│  │             │    │    └── LLM → reply                │   │
│  └──────┬──────┘    └───────────────┬───────────────────┘   │
│         │                           │                       │
│         ▼                           ▼                       │
│  ┌─────────────┐    ┌───────────────────────────────────┐   │
│  │   config    │    │      neonize client               │   │
│  │   (env)     │    │      (WhatsApp Web)               │   │
│  └─────────────┘    └───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

```
WhatsBot/
├── main.py               — entry point
├── menu.py               — terminal menu + live view
├── whatsapp.py           — message pipeline
├── llm.py                — LLM calls + conversation assembly
├── sessionstore.py       — session store (SQLite, persistent conn) + bot_state
├── database.py           — facts table (per-user durable facts)
├── prompts.py            — prompt scaffolding (group note, summary, facts)
├── away.py               — away mode (owner-activity tracking + override)
├── config.py             — env vars + SOUL.md loader
├── bot_settings.py       — runtime reply toggles (dm, group, self_chat)
├── SOUL.md               — the persona (gitignored, re-read every message)
├── SOUL.example.md       — public template for SOUL.md
├── .env                  — secrets (gitignored)
├── db/
│   └── conversations.sqlite3  — sessions, messages, summaries, facts
└── docs/                 — this documentation
```

### `main.py`

- Loads `.env`, sets up logging to `app.log`.
- Creates runtime directories (`db/`, `downloads/`, `converted/`).
- `init_db()` — creates the `facts` table (sessionstore handles its own tables).
- `start_cleanup_thread()` — background pruning thread.
- Runs `run_menu()` with connect/start/disconnect/stop callbacks.
- On exit: disconnects the client and `os._exit(0)`.

### `menu.py`

- `run_menu()` — main loop: standby menu → connect → live view → back to menu.
- `run_live_view()` — displays log output in real time; reads stdin via `select()` with a 0.1s timeout.
- `install_log_capture()` — mirrors all logging records to a queue for the live view.
- `_runtime` dict — holds LLM overrides (`provider`, `model`, `base_url`) set by the model picker; read by `llm.get_client_and_model()`.
- `print_sessions_menu()` — read-only ledger overview (`store.stats()`).

### `whatsapp.py`

```
on_message()            — main pipeline (see Message Flow below)
on_history_sync()       — backfills synced messages into sessions
handle_commands()       — !new, !title, !sessions, !resume, !delete, !away, !reset, ...
handle_reply_commands() — !replies, !status (no delay)
handle_file()           — PDF/DOCX/TXT ingestion → converted/*.txt
handle_final_response() — LLM call + send + store assistant reply (away-aware)
resolve_sender_phone()  — LID → phone (cached)
is_self_chat()          — self-chat detection
is_bot_mentioned()      — group @mention check
session_key_for()       — dm-{phone} | self | ch-{group jid}
_session_note()         — first-ever / new-session context note
```

### `llm.py`

```
get_client_and_model()    — provider-specific client (openai/azure/openrouter/llama.cpp)
call_llm_api()            — unified chat completion (takes a messages list)
is_owner()                — owner detection (OWNER_NUMBER exact, else pushname)
build_system_prompt()     — SOUL.md + owner/group note + session note + memory blocks
build_convo()             — [system] + history + current user turn
generate_final_response() — assemble + call + return reply text
summarize_session()       — rolling summary (per session)
extract_facts()           — fact extraction (per user)
```

### `sessionstore.py`

`SessionStore` class (ported from the DiscordBot session store) with a **single persistent SQLite connection** (WAL mode) guarded by a re-entrant lock.

```
ensure_session(key)       — active session for a chat, or create one
append(session_id, ...)   — store a message (user/assistant), bump activity
end_current(key, reason)  — end active session + create a fresh one
resume(key, id)           — rebind a chat to a previous session
resolve(key, ref)         — number | title | session id → session id
delete_session(key, id)   — delete (bound → create replacement)
reset_key(key)            — delete all sessions for a chat + fresh bind
history(session_id, ...)  — last N user turns + replies (oldest first, [Name] labels)
user_message_count()      — user turns in a session
prune_old_messages()      — prune by age
save_summary()/get_summary() — per-session rolling summary
list_sessions()/search_sessions() — ledger (up to 15 rows)
stats()                   — totals + current binding per chat (menu view)
```

Module-level `store = SessionStore(CONV_DB_PATH)` — created at import (imported by `whatsapp.py`/`menu.py`), so migration runs at startup before any message.

### `away.py`

Away mode: the bot steps in as the owner's proxy when the owner is inactive. Owner activity is tracked in the store's `bot_state` table (survives restarts).

```
mark_owner_active(ts)   — record owner activity (their messages / self-chat); clears the announce flag
is_away()               — override wins (present/away), else idle > AWAY_AFTER_MINUTES
set_override(state)     — auto | present | away (menu / !away); leaving "away" clears the flag
status()                — {override, away, idle_seconds, threshold_minutes}
away_context(sid, ann)  — system-prompt note: announce (first reply) vs keep-chatting (later)
away_announced(sid)     — was the away notice already sent in this session?
mark_away_announced(sid)— set after a successful away reply (per session)
clear_away_announced()  — forget all per-session flags (owner active / away ended)
```

On first run the owner is assumed present; on later restarts the persisted
timestamp is used. The owner becomes "present" the moment they send any message.

The away notice is sent **once per session**: the first reply after the owner
goes away announces it (LLM-crafted, not hardcoded); later replies in the same
session use a softer note so the bot keeps chatting without repeating "the
owner is away". The flag resets when the owner comes back, when the override
leaves "away", or in a new session (`!new`).

### `database.py`

Facts table only (per-user durable facts): `save_fact`, `get_facts`, `delete_fact`, `get_fact_count`, `prune_stale_facts`, `init_db`.

### `prompts.py`

Prompt scaffolding: `GROUP_CHAT_NOTE` (group rules: `[Name]` labels, speak only on @mention, pronouns point at the speaker), `SUMMARY_SYSTEM_PROMPT` (rolling summary merge), `FACT_EXTRACTION_PROMPT` (JSON fact extraction). No persona lives here — the persona is `SOUL.md`.

### `config.py`

All env vars loaded once at import. `SOUL_PATH` is anchored to the project root; `load_soul()` re-reads `SOUL.md` on every call (falls back to `DEFAULT_PERSONA` if missing/empty). Owner identity: `OWNER_NAME` (default `Owner`) + `OWNER_NUMBER` (optional exact phone).

### `bot_settings.py`

Runtime reply toggles: `{group_mention, dm, self_chat}` with `get_settings()` / `toggle()` (thread-safe). Defaults: `group_mention=False`, `dm=True`, `self_chat=True`.

---

## Message Flow (detailed)

```
whatsapp.on_message(client, message)
│
├── 1. PARSE
│     chat = message.Info.MessageSource.Chat
│     sender_id = chat.User
│     text = conversation / extendedTextMessage.text / caption
│     is_group, is_edit, is_viewonce, timestamp, sender_name
│
├── 2. IDENTITY
│     bot_jid = get_bot_jid(client)
│     if _self_chat_jid is None: set_self_chat_jid(bot_jid)
│
│     identity_jid = Sender (groups) else Chat
│     sender_phone = resolve_sender_phone(client, identity_jid)
│     if not is_group and sender_phone != sender_id:
│         log "Resolved LID ..."
│         sender_id = sender_phone
│
│     is_self = is_self_chat(chat, sender_id)
│     if not is_self and phone matches: is_self = True  (fallback)
│
│     if is_self or is_owner(sender_phone, sender_name):
│         away.mark_owner_active(timestamp)   # resets the "away" timer
│
├── 3. FILTER
│     if is_edit or is_viewonce: return
│
├── 4. GATE (reply behavior)
│     settings = get_settings()
│
│     if is_group:
│         if not group_mention: return
│         sender_id = resolve_sender_phone(Sender) or sender_id
│         if is_bot_mentioned: → process (below)
│         else:
│             if whitelisted:
│                 session_id = store.ensure_session("ch-{group}")
│                 store.append(session_id, ("user", text), sender=sender_name)
│             return   (context only, no reply)
│
│     elif is_self:
│         if not self_chat: return
│
│     else:  # DM
│         if not dm: return
│
├── 5. FRESHNESS
│     if time.time() - timestamp > 60: return
│
├── 6. MARK READ
│     client.mark_read(...)
│
├── 7. PRESENCE
│     client.send_chat_presence(COMPOSING)
│
├── 8. COMMANDS
│     if handle_commands(...):
│         presence PAUSED
│         return
│
├── 9. FILES
│     if media and not url:
│         if handle_file(...): return
│
├── 10. BOT STATE
│     if not is_bot_running and not is_self: return
│
├── 11. WHITELIST
│     if REPLY_WHITELIST and "*" not in it:
│         norm = strip "+", "@", ":N", whitespace
│         if norm(sender_phone) not in {norm(w) for w in whitelist}: return
│
├── 12. RATE LIMIT
│     if not can_respond_to_user(sender_id): return
│
├── 13. SESSION
│     key = session_key_for(is_group, is_self, chat, sender_id)
│     session_id = store.ensure_session(key, sender_id)
│     session = store.current_session(key)
│     session_note = _session_note(session)
│     store.append(session_id, ("user", text), sender=sender_name, timestamp=...)
│
│     if session["message_count"] > 100:
│         store.prune_old_messages(session_id)
│
│     if store.user_message_count(session_id) > VERBATIM_TURNS:
│         summarize_session(store, session_id, sender_id)
│
├── 14. FACT EXTRACTION (background thread)
│     threading.Thread(target=_extract_facts_async, ...).start()
│
├── 15. RESPOND
│     handle_final_response(...)
│           ├── 2-5s random delay
│           ├── owner = is_self or is_owner(sender_id, sender_name)
│           ├── is_away = (not is_group) and (not is_self) and away.is_away()
│           ├── generate_final_response(store, session_id, ..., is_away=is_away)
│           │     └── build_convo()
│           │           ├── load_soul()          ← SOUL.md (fresh read)
│           │           ├── owner/group note
│           │           ├── [Owner is away]      ← only when is_away; announce once
│           │           │                           per session, then keep-chatting
│           │           ├── session_note
│           │           ├── [FACTS about this user]
│           │           ├── [CONVERSATION SUMMARY]
│           │           ├── [ADDITIONAL CONTENT] (converted/ files)
│           │           ├── store.history()      ← verbatim tail
│           │           └── current user turn
│           │     └── call_llm_api()
│           │     └── append "({SIGNATURE_NAME})" (non-group, if enabled)
│           ├── send_message(...)
│           ├── presence PAUSED
│           └── store.append(session_id, ("assistant", reply))
│
│     record_user_response(sender_id)
│
└── 16. ERROR HANDLING
      except Exception as e:
          log.error + log.exception
```

---

## Threading Model

```
MAIN THREAD
  │
  ├── run_menu() loop
  │     └── prompt_choice()  ← blocking input()
  │
  └── run_live_view()
        └── _read_stdin_line(timeout=0.1)  ← select() on stdin
              └── main thread is the SOLE stdin reader

NEONIZE EVENT LOOP THREAD (daemon)
  │
  ├── on_connected()
  ├── on_history_sync()
  ├── on_message()  ← the main pipeline (runs on this thread)
  └── on_qr()

CLEANUP THREAD (daemon)
  └── _cleanup_thread()
        └── every 6h: store.prune_old_messages() + prune_stale_facts()

FACT EXTRACTION THREADS (daemon, per-message, short-lived)
  └── _extract_facts_async()
        └── extract_facts() → LLM call → save_fact()

LOG CONSUMER THREAD (daemon, only during live view)
  └── _log_consumer()
        └── drain _log_queue → print to stdout
```

### Key threading decisions

| Decision | Rationale |
|----------|-----------|
| **Neonize event loop in a daemon thread** | `client.connect()` blocks. Running it in a daemon thread lets the main thread run the menu. |
| **Main thread is sole stdin reader** | Avoids race conditions between `input()` and `select()`. The live view uses `select()` with a 0.1s timeout so it can check for Ctrl+C and pending log messages. |
| **Fact extraction in background thread** | LLM call for fact extraction can take 1-5s. Running it in a thread means the user's response isn't delayed. |
| **Summarization is synchronous** | It only runs when user turns exceed `VERBATIM_TURNS` and needs the summary ready before the next response. Acceptable trade-off. |
| **Store uses ONE persistent connection + `threading.RLock`** | The session store keeps a single WAL-mode SQLite connection guarded by a re-entrant lock. Neonize event callbacks and background threads share it; short-lived per-call connections caused "database is locked" races. |
| **`pause_flag` is a mutable list** | `pause_flag = [False]` is shared between `run_menu()` and `run_live_view()`. Lists are mutable, so `pause_flag[0] = True` is visible across both without a lock (GIL makes single-element list mutation atomic). |
| **`bot_settings` uses `threading.Lock`** | The `_bot_settings` dict is read from the Neonize thread (in `on_message`) and written from the main thread (in menu). The lock prevents torn reads. |

---

## State Management

### Global state (in `whatsapp.py`)

| Variable | Type | Purpose |
|----------|------|---------|
| `is_bot_running` | `bool` | Master on/off switch. When False, non-self-chat messages are skipped. |
| `_self_chat_jid` | `str` | The bot's own JID (e.g. `6285712345678@s.whatsapp.net`). Set on first connection. |
| `_lid_cache` | `dict` | LID → phone number cache. Avoids re-querying the store for repeat messages. |
| `user_message_timestamps` | `defaultdict(deque)` | Rate limiter: last 5 response timestamps per user. |

### Global state (in `menu.py`)

| Variable | Type | Purpose |
|----------|------|---------|
| `_runtime` | `dict` | Runtime LLM overrides: `{provider, model, base_url}`. Set by the model picker, read by `llm.get_client_and_model()`. |
| `_ctrl_c` | `list[bool]` | Ctrl+C flag. Set by signal handler, checked by menu loop. |
| `_log_queue` | `Queue` | Log capture: mirrors all logging records to the live view. |
| `_pending_input` | `list` | Buffer for input that arrives while the menu is redrawing. |
| `_pending_ready` | `Event` | Signals that `_pending_input` has new data. |
| `_live_running` | `bool` | Whether the live view is active. |

### Database state

| Table | Purpose | Key |
|-------|---------|-----|
| `sessions` | One session per chat (ledger, resumable) | `id` (TEXT, `s-` + hex) |
| `messages` | All session messages (user + assistant) | `id` (INTEGER) |
| `chat_sessions` | Active session binding per chat | `session_key` |
| `summaries` | Rolling summary per session | `session_id` |
| `facts` | Durable extracted facts per user | `id` (INTEGER) |
| `bot_state` | Away-mode bookkeeping (`owner_last_active`, `away_override`) | `key` |

---

## LID Resolution

Whatsmeow uses **Linked IDs (LIDs)** for group messages. Instead of a phone number, the sender is reported as a LID (e.g. `69642961854565@lid`). This breaks phone-based whitelists and self-chat detection.

**Solution:** `resolve_sender_phone()` resolves LID → phone on first sight and caches the result:

```python
def resolve_sender_phone(client, sender_jid) -> str:
    s = Jid2String(sender_jid)
    if sender_jid.Server != "lid":
        return s.split("@", 1)[0]  # already a phone
    if s in _lid_cache:
        return _lid_cache[s]       # cached
    phone = client.get_pn_from_lid(sender_jid)
    _lid_cache[s] = phone
    return phone
```

The cache is in-memory (lost on restart). This is fine — LID resolution is cheap after the first message.

**Device suffix:** The resolved phone may carry a `:N` device suffix (e.g. `6281234567890:52`). The whitelist normalization strips this:

```python
norm = lambda s: s.strip().lstrip("+").replace("@", "").split(":", 1)[0]
```

---

## Self-Chat Detection

The bot's self-chat ("Message yourself") is the only chat where `chat == bot_jid`. In a regular DM, `chat` is the other person's number, so it never matches the bot's own JID.

```python
def is_self_chat(chat_jid, sender_id):
    chat_str = Jid2String(chat_jid)
    return chat_str == _self_chat_jid  # set on first connection
```

**Fallback:** If the self-chat JID has a `:N` device suffix but the resolved phone doesn't (or vice versa), a phone-part comparison is used:

```python
self_phone = _self_chat_jid.split("@", 1)[0].split(":", 1)[0]
sender_phone = sender_id.split(":", 1)[0]
if self_phone == sender_phone:
    is_self = True
```

**Note:** `message.Info.IsFromMe` is **not** reliable for self-chat detection — it's `True` for all inbound messages in some neonize versions. The JID comparison is the reliable method.

---

## Connect / Start / Stop / Disconnect

```
┌─────────────────────────────────────────────────────────────┐
│                      STATE MACHINE                          │
│                                                             │
│   ┌──────────┐   connect()   ┌──────────┐                  │
│   │  STANDBY │ ─────────────► │CONNECTED │                  │
│   └──────────┘               └────┬─────┘                  │
│        ▲                          │ start_bot()            │
│        │                          ▼                        │
│        │                   ┌──────────┐                    │
│   stop()│                  │ RUNNING  │                    │
│        │                   └──────────┘                    │
│        │                          │ stop_bot()             │
│        └──────────────────────────┘                        │
│                                                             │
│   disconnect() → drop WhatsApp session → STANDBY            │
└─────────────────────────────────────────────────────────────┘
```

| Action | What happens |
|--------|--------------|
| **Connect** | `client.connect()` → QR code → WhatsApp session established. Bot is connected but not responding. |
| **Start bot** | `is_bot_running = True`. Bot begins responding to messages. |
| **Stop bot** | `is_bot_running = False`. Bot stops responding (self-chat still works). |
| **Disconnect** | `client.disconnect()` → WhatsApp session dropped. Back to standby. |

---

## Error Handling

| Error | Handling |
|-------|----------|
| LLM API error | `call_llm_api()` catches all exceptions, logs, returns `""`. Bot stays silent. |
| LID resolution failure | Falls back to the raw JID user part. Logged as a warning. |
| File conversion failure | Logged; bot replies with an error message. |
| Database error | Logged; the persistent store connection is retried on the next operation. |
| Neonize disconnect | Client emits `on_disconnected`; menu shows the state. Reconnect via menu. |
| Unknown command | Bot replies with "Unknown command. Use !commands to see available commands." |
