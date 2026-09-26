# Architecture

System design, message flow, threading model, and state management for WhatsBot.

---

## Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                         main.py (entry point)                       │
│                                                                     │
│  configure_logging()                                                │
│  init_db() + start_cleanup_thread()                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  run_menu()  ←  menu.py                                    │   │
│  │                                                            │   │
│  │  connect_fn()     ← creates Neonize client + event handlers│   │
│  │  start_bot_fn()   ← set_bot_running(True)                   │   │
│  │  stop_bot_fn()    ← set_bot_running(False)                  │   │
│  │  disconnect_fn()  ← set_bot_running(False) + client.disc() │   │
│  │                                                            │   │
│  │  run_live_view()  ← live log view (stdin reader)           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Neonize event handlers (in main.py, called by neonize event loop): │
│    on_connected()   → set presence, set_bot_running(False)          │
│    on_history_sync()→ whatsapp.on_history_sync()                    │
│    on_message()     → whatsapp.on_message()                         │
│    on_qr()          → print QR code                                 │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       whatsapp.py (message handler)                  │
│                                                                     │
│  on_message()          — main pipeline (see Message Flow below)     │
│  handle_commands()     — !commands, !files, !reset, etc.            │
│  handle_reply_commands() — !replies, !status                        │
│  handle_file()         — PDF/DOCX/TXT ingestion                     │
│  handle_greeting()     — first-time greeting                        │
│  handle_final_response() — watchdog + LLM call + send               │
│  resolve_sender_phone() — LID → phone (cached)                      │
│  is_self_chat()        — self-chat detection                        │
│  is_bot_mentioned()    — group @mention check                       │
└─────────────────────────────────────────────────────────────────────┘
         │
         ├──► llm.py
         │       get_client_and_model()   — provider-specific client
         │       call_llm_api()           — unified chat completion
         │       call_watchdog_llm()      — structured JSON filter
         │       generate_final_response() — context + prompt + call
         │       generate_first_time_greeting()
         │       summarize_session()      — rolling summary
         │       extract_facts()          — fact extraction
         │       build_context()          — budget-aware context
         │
         ├──► database.py
         │       messages table     — all conversation messages
         │       summaries table    — rolling conversation summaries
         │       facts table        — durable extracted facts
         │
         ├──► bot_settings.py
         │       _bot_settings dict — {group_mention, dm, self_chat}
         │       get_settings()     — thread-safe read
         │       toggle()           — thread-safe write
         │
         └──► config.py
                 All env vars loaded once at import time
```

---

## Message Flow (detailed)

```
whatsapp.on_message(client, message)
│
├── 1. PARSE
│     chat = message.Info.MessageSource.Chat
│     sender_id = chat.User
│     text = conversation / extendedTextMessage.text / caption
│     from_me, is_group, is_edit, is_viewonce, timestamp, sender_name
│
├── 2. IDENTITY
│     bot_jid = get_bot_jid(client)
│     if _self_chat_jid is None: set_self_chat_jid(bot_jid)
│
│     sender_phone = resolve_sender_phone(client, chat)
│     if sender_phone != sender_id:
│         log "Resolved LID ..."
│         sender_id = sender_phone
│
│     is_self = is_self_chat(chat, sender_id)
│     if not is_self and phone matches: is_self = True  (fallback)
│
├── 3. FILTER
│     if is_edit or is_viewonce: return
│
├── 4. GATE (reply behavior)
│     settings = get_settings()
│
│     if is_group:
│         if group_mention ON AND is_bot_mentioned:
│             sender_id = message.Info.MessageSource.Sender
│         else: return  (skip)
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
├── 6. MARK READ + SAVE
│     client.mark_read(...)
│     save_message(sender_id, text, timestamp, from_me)
│
├── 7. PRESENCE
│     client.send_chat_presence(COMPOSING)
│
├── 8. COMMANDS
│     if handle_commands(...): return
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
│         sender_phone = resolve_sender_phone(Sender or Chat)
│         norm = strip "+", "@", ":N", whitespace
│         if norm(sender_phone) not in {norm(w) for w in whitelist}: return
│
├── 12. RATE LIMIT
│     if not can_respond_to_user(sender_id): return
│
├── 13. SESSION
│     is_new, is_first = is_new_session(sender_id)
│     stats = get_session_stats(sender_id)
│
│     if not is_new and message_count > 100:
│         prune_old_messages(sender_id)
│
│     if not is_first and message_count > VERBATIM_TURNS:
│         summarize_session(sender_id)
│
├── 14. FACT EXTRACTION (background thread)
│     threading.Thread(target=_extract_facts_async, ...).start()
│
├── 15. RESPOND
│     if is_first:
│         handle_greeting()
│     else:
│         handle_final_response()
│           ├── call_watchdog_llm()
│           │     if not relevant: return (silent)
│           │
│           └── generate_final_response()
│                 ├── build_context(user_id, user_text)
│                 │     ├── facts (25% budget)
│                 │     ├── summary (50% remaining)
│                 │     └── verbatim tail (rest)
│                 ├── _read_converted_files()
│                 ├── format prompt (public + private)
│                 └── call_llm_api()
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
        └── every 6h: prune_old_messages() + prune_stale_facts()

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
| **Summarization is synchronous** | It only runs when `message_count > VERBATIM_TURNS` and needs the summary to be ready before the next response. Acceptable trade-off. |
| **SQLite connections are short-lived** | Each `database.py` function opens and closes its own connection. No persistent connection pool. This avoids SQLite "database is locked" errors across threads. |
| **`pause_flag` is a mutable list** | `pause_flag = [False]` is shared between `run_menu()` and `run_live_view()`. Lists are mutable, so `pause_flag[0] = True` is visible across both without a lock (GIL makes single-element list mutation atomic). |
| **`bot_settings` uses `threading.Lock`** | The `_bot_settings` dict is read from the Neonize thread (in `on_message`) and written from the main thread (in menu). The lock prevents torn reads. |

---

## State Management

### Global state (in `whatsapp.py`)

| Variable | Type | Purpose |
|----------|------|---------|
| `is_bot_running` | `bool` | Master on/off switch. When False, non-self-chat messages are skipped. |
| `_self_chat_jid` | `str` | The bot's own JID (e.g. `6285732705939@s.whatsapp.net`). Set on first connection. |
| `_lid_cache` | `dict` | LID → phone number cache. Avoids re-querying the store for repeat messages. |
| `public_prompts` | `dict` | Admin-editable prompts (greeting, final_response). Reset on restart. |
| `private_prompts` | `dict` | Non-editable prompts (greeting, final_response, watchdog). |
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
| `messages` | All conversation messages (user + bot) | `(user_id, message_content, timestamp, from_me)` |
| `summaries` | Rolling conversation summary per user | `user_id` |
| `facts` | Durable extracted facts per user | `(user_id, entity, fact)` |

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

**Device suffix:** The resolved phone may carry a `:N` device suffix (e.g. `6287711076107:52`). The whitelist normalization strips this:

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
if sender_phone == self_phone:
    is_self = True
```

**Why `IsFromMe` can't be used:** In Whatsmeow's event stream, `IsFromMe` is `True` for ALL inbound messages (both others' DMs and your own self-chat), because the phone is receiving them. It cannot distinguish self-chat from regular DMs.

---

## Connect / Start / Stop / Disconnect

The menu separates **session** (WhatsApp login) from **bot** (message processing):

```
┌──────────────┐     [3] Connect      ┌──────────────┐
│  MENU (idle) │ ──────────────────►  │  STANDBY     │
│  session: off│                      │  session: ON │
└──────────────┘                      │  bot: paused │
                                      │  self-chat: ON│
                                      └──────┬───────┘
                                             │
                              [4] Start      │  [3] Disconnect
                                             │
                                      ┌──────▼───────┐
                                      │  RUNNING     │
                                      │  session: ON │
                                      │  bot: active │
                                      │  self-chat: ON│
                                      │  DMs: (if ON)│
                                      └──────┬───────┘
                                             │
                                      [stop] in live view
                                             │
                                      ┌──────▼───────┐
                                      │  STANDBY     │
                                      │  (as above)  │
                                      └──────────────┘
```

| Action | `set_bot_running` | `client.disconnect()` | `state["connected"]` |
|--------|-------------------|----------------------|---------------------|
| Connect | `False` (standby) | — | `True` |
| Start | `True` | — | `True` |
| Stop (live view) | `False` | — | `True` |
| Disconnect | `False` | ✅ | `False` |

---

## Error Handling

| Layer | Strategy |
|-------|----------|
| `on_message` | One big `try/except` around the entire pipeline. Any exception is logged with `log.exception()` (full traceback) and the handler returns. The bot keeps running. |
| LLM calls | `call_llm_api()` catches all exceptions and returns `""`. The caller checks for empty response and skips sending. |
| Watchdog | `_call_llm_structured()` catches JSON parse errors and returns `{}`. `call_watchdog_llm()` defaults to `(True, None)` on error — the message passes through. |
| File conversion | `convert_pdf_to_markdown()` / `convert_docx_to_markdown()` catch exceptions and return an error string. |
| Database | Each function opens/closes its own connection. `IntegrityError` on duplicate messages is silently ignored. |
| Connection | `connect_fn()` waits up to 120s for `ConnectedEv`. If it times out or the user presses Ctrl+C, it calls `disconnect_fn()` and returns `False`. |
| Process exit | `os._exit(0)` is used for hard exits (menu quit, `!permanentstop`) because the Neonize event loop thread may not stop cleanly. |
