# WhatsBot — WhatsApp AI Chatbot

A personal WhatsApp AI assistant that runs on a local LLM (or any OpenAI-compatible API). It answers DMs, self-chat messages, and group @mentions — keeping a per-chat session ledger (resumable), a rolling conversation summary, and durable facts about you. Its personality lives in a single `SOUL.md` file you edit directly.

Built on top of [Neonize](https://github.com/krypton-byte/neonize) (a Python wrapper for [Whatsmeow](https://github.com/tulir/whatsmeow)) and the OpenAI Python SDK.

> **Provenance** — This project is based on the original [GPT-Laboratory / whatsapp-ai-chatbot](https://github.com/GPT-Laboratory/whatsapp-ai-chatbot) by the GPT Lab Seinäjoki / Tampere University team. See [Acknowledgments](#acknowledgments) for full credit.

---

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Admin Commands](#admin-commands)
- [Terminal / Menu](#terminal--menu)
- [File Structure](#file-structure)
- [Documentation](#documentation)
- [Troubleshooting](#troubleshooting)
- [Acknowledgments](#acknowledgments)
- [License](#license)

---

## Features

| Feature | Description |
|---------|-------------|
| **WhatsApp Integration** | Uses Neonize/Whatsmeow — no WhatsApp Business API needed. Log in with a QR code. |
| **File-Based Persona** | The entire personality lives in `SOUL.md` (gitignored). Edit it, save, and the change applies on the very next message — no restart, no prompt juggling. |
| **Session Management** | One session per chat (DM, self-chat, group). `!new` starts a fresh one, `!sessions` lists the ledger, `!resume` reopens any previous session, `!delete` removes one. |
| **Shared Group Sessions** | With group mode ON, every group message is recorded as context in a shared session (speakers labeled `[Name]`), and the bot speaks only when @mentioned. |
| **Multi-Provider LLM** | Works with OpenAI, Azure OpenAI, OpenRouter, or any local llama.cpp / llama-swap server. Switch providers at runtime from the menu. |
| **Self-Chat Support** | The bot's own "Message yourself" chat works as a control channel — send commands and test responses without involving another person. |
| **DM / Group / Self-Chat Toggles** | Reply behavior for each channel type can be turned on/off at runtime (self-chat defaults ON, DM defaults ON, group mode defaults OFF). |
| **Reply Whitelist** | Restrict who gets AI responses to a comma-separated list of phone numbers. `*` = everyone. |
| **Rolling Memory** | Conversation history beyond the verbatim window is summarized into a compact rolling summary (per session), so long conversations stay coherent. |
| **Fact Extraction** | Durable facts (names, preferences, plans, etc.) are extracted from messages and injected into future context. |
| **File Ingestion** | Send PDF, DOCX, or TXT files from an admin number — content is extracted and used as context for all subsequent responses. |
| **Session Awareness** | Detects when a user returns after a long gap and tells the LLM about it ("new session, last conversation was 3 hours ago"). |
| **Away Mode** | When the owner goes quiet for `AWAY_AFTER_MINUTES`, the bot steps in as their proxy — the LLM naturally lets people know the owner is away and keeps chatting. Override via `!away [on\|off\|auto]` or the menu. Replies are signed with the persona name (e.g. `- Yuki`). |
| **Rate Limiting** | Max 5 responses per user per 30-second window. |
| **Background Cleanup** | A daemon thread prunes old messages and stale facts every 6 hours. |

---

## How It Works

### Message Pipeline

```
Incoming WhatsApp message
        │
        ▼
  ┌─────────────┐
  │  on_message │  (whatsapp.py)
  └──────┬──────┘
         │
         ├─ Parse: chat JID, sender, text, is_group, is_edit, is_viewonce
         │
         ├─ Resolve LID → phone number (cached)
         │
         ├─ Classify: self-chat? group? DM?
         │     │
         │     ├─ group (mode ON):
         │     │     @mentioned → process
         │     │     otherwise  → record as group context, no reply
         │     ├─ group (mode OFF): skip
         │     ├─ self:   reply if self_chat toggle ON (default)
         │     └─ DM:     reply if dm toggle ON (default)
         │
         ├─ If the sender is the owner (or self-chat): mark owner active
         │     (resets the away-mode timer)
         │
         ├─ Skip if older than 60s, or if bot is paused (except self-chat)
         │
         ├─ Mark as read
         │
         ├─ Is it a command? ──► handle_commands() → return
         ├─ Is it a file?    ──► handle_file()     → return
         │
         ├─ Whitelist check (phone number match)
         ├─ Rate limit check
         │
         ├─ Session: ensure active session for this chat, store the message
         ├─ Prune old messages if > 100 stored
         ├─ Summarize older messages if > VERBATIM_TURNS user turns
         ├─ Extract facts (background thread, non-blocking)
         │
         └─ handle_final_response()
               │
               ├─ If the owner is away (DM/self-chat only): add an
               │     "owner is away" note so the LLM crafts the first message
               │
               ├─ Build conversation:
               │     SOUL.md + owner/group note + session note
               │     + [FACTS] + [CONVERSATION SUMMARY] + [ADDITIONAL CONTENT]
               │     + verbatim history tail + current turn
               │
               └─ LLM call → append "({SIGNATURE_NAME})" (non-group)
                          → send reply → store assistant message
```

### Memory Model

Every LLM call is assembled from the persona plus three memory layers:

| Layer | What | Scope | Lifetime |
|-------|------|-------|----------|
| **SOUL.md** | The persona (public face, private face, how you talk) | global | re-read on every message |
| **Facts** | Durable, verifiable statements (names, preferences, plans) | per user | 60 days (pruned if unconfirmed) |
| **Summary** | Rolling conversation summary (LLM-generated) | per session | until the session ends |
| **Verbatim Tail** | Last `VERBATIM_TURNS` user turns + replies (exact text) | per session | 30 days |

There is no character budget — the context is the persona + memory blocks + a fixed-size verbatim tail. Older messages are folded into the rolling summary instead of being dropped.

---

## Quick Start

### Prerequisites

- Python 3.10+
- A WhatsApp account (the bot logs in as a linked device)
- An LLM: local (llama.cpp / llama-swap) or API (OpenAI / Azure / OpenRouter)

### Setup

```bash
# 1. Clone / enter the project directory
cd WhatsBot

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp example.env .env
# Edit .env — set ADMIN_NUMBERS, OWNER_NAME, and your LLM provider

# 5. Create your persona file (SOUL.md is gitignored — create your own)
cp SOUL.example.md SOUL.md
# Edit SOUL.md — customize the character, owner, speech style, etc.

# 6. Start
python main.py
```

> **Note:** `SOUL.md` is intentionally **gitignored** so your personal persona stays
> private. The public template is `SOUL.example.md` — copy it to `SOUL.md` on first
> run (step 5 above) and edit from there. Edits apply on the next message.

The first time you run it, the menu will show a QR code. Scan it with WhatsApp → **Settings → Linked Devices → Link a Device**.

### Using llama-swap (recommended for local models)

```bash
# Install llama-swap from https://github.com/mostlygeek/llama-swap
# Download a GGUF model (e.g. from HuggingFace) into the models directory

# Start the server (auto-loads models on demand):
llama-swap -port 8080 -m ./models

# In .env:
LLM_PROVIDER=llama.cpp
LLAMACPP_BASE_URL=http://localhost:8080/v1
LLAMACPP_MODEL=Gemma4
```

---

## Configuration

All configuration lives in `.env`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_NUMBERS` | — | Comma-separated phone numbers allowed to send commands |
| `OWNER_NAME` | `Owner` | The owner's name (used for the public/private face) |
| `OWNER_NUMBER` | — (name match) | Owner's phone number (exact match; falls back to pushname) |
| `REPLY_WHITELIST` | `*` | Phone numbers that get AI responses (`*` = everyone) |
| `LLM_PROVIDER` | — | `openai` \| `azure` \| `openrouter` \| `llama.cpp` |
| `SESSION_GAP_SECONDS` | `3600` | Silence gap (seconds) before a session is reported as "new" |
| `VERBATIM_TURNS` | `10` | How many recent user turns kept verbatim in context |
| `MEMORY_CUTOFF_DAYS` | `30` | Prune messages older than this |
| `FACT_CUTOFF_DAYS` | `60` | Prune facts unconfirmed for longer than this |
| `MAX_FACTS_IN_CONTEXT` | `8` | Max facts injected into the context window |
| `MAX_REPLY_TOKENS` | `500` | Hard token cap per LLM reply |

### LLM Provider Options

| Provider | Env vars | Notes |
|----------|----------|-------|
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | Standard OpenAI API |
| `azure` | `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT_NAME`, `AZURE_SUBSCRIPTION_KEY`, `AZURE_API_VERSION` | Azure OpenAI Service |
| `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter gateway |
| `llama.cpp` | `LLAMACPP_BASE_URL`, `LLAMACPP_MODEL` | Local llama.cpp / llama-swap (OpenAI-compatible) |

---

## Admin Commands

Send these from your admin number (configured in `ADMIN_NUMBERS`):

| Command | Description |
|---------|-------------|
| `!commands` | List all available commands |
| `!files` | List uploaded files |
| `!removefile <ID or name>` | Remove a file |
| `!renamebot <name>` | Change the bot's name |
| `!away [on\|off\|auto]` | Show/set away mode (`auto` = derive from owner activity) |
| `!new` | Start a new session (previous one stays saved, resumable) |
| `!title [name]` | Set/show the title of the current session |
| `!sessions [search]` | List sessions for this chat (optionally search) |
| `!resume <n\|name\|id>` | Resume a previous session (bare `!resume` resumes the bot) |
| `!delete <n\|name\|id>` | Delete a session |
| `!memory` | Show your session/memory stats |
| `!facts` | List stored facts about you |
| `!forget <ID>` | Delete a specific fact by ID |
| `!stats` | Show full memory/stats overview |
| `!reset` | Delete all sessions for this chat (fresh start) |
| `!pause` | Pause the bot (self-chat still works) |
| `!resume` | Resume the bot (no argument) |
| `!permanentstop` | Stop the bot permanently |
| `!replies <key> <on\|off>` | Toggle reply behavior: `!replies dm on`, `!replies self_chat off`, etc. |
| `!status` | Show current reply settings |

> **Self-chat** is the easiest way to test: send `!status`, `!replies dm on`, etc. from your own "Message yourself" chat.

---

## Terminal / Menu

Running `python main.py` opens a terminal menu:

```
  ┌─────────── WHATSBOT ───────────┐
  │                                │
  │  Model:     llama.cpp / Qwen...│
  │  DM:        ON                 │
  │  Group @:   off                │
  │  Self-chat: ON                 │
  │  Session:   CONNECTED          │
  │                                │
  └────────────────────────────────┘

    [1] Select model
    [2] Settings
    [3] Disconnect session (stop bot)
    [4] Start bot (connected)
    [5] Sessions
    [6] Quit
```

### Menu Items

| # | Action | Description |
|---|--------|-------------|
| 1 | **Select model** | Switch LLM provider and model at runtime. For `llama.cpp`, fetches the model list from the server. |
| 2 | **Settings** | Toggle DM / group mode / self-chat replies. View session/memory settings. |
| 3 | **Connect / Disconnect** | Connect = QR-scan to log in (session shows "CONNECTED"). Disconnect = stop bot + drop session. |
| 4 | **Start bot** | Activate the bot and enter the live log view. Session stays connected. |
| 5 | **Sessions** | Read-only ledger overview: totals + the current session per chat. |
| 6 | **Quit** | Exit the program. |

### Live View Commands

When the bot is running (option 4), the terminal shows live logs. Type:

| Command | Description |
|---------|-------------|
| `pause` | Pause the bot (self-chat still works) |
| `resume` | Resume the bot |
| `status` | Show reply settings |
| `replies <key> <on\|off>` | Toggle reply behavior |
| `stop` | Return to the menu (bot stops, session stays connected) |
| `quit` | Exit the program |

### Connect vs Start

| State | Session | Bot | Self-chat | DMs | Groups |
|-------|---------|-----|-----------|-----|--------|
| **Standby** (after Connect) | ✅ Connected | ⏸ Paused | ✅ Works | ❌ Skipped | ❌ Skipped |
| **Running** (after Start) | ✅ Connected | ▶️ Active | ✅ Works | ✅ (if toggle ON) | ✅ (if mode ON; context always recorded) |
| **Disconnected** | ❌ | ⏸ | ❌ | ❌ | ❌ |

---

## File Structure

```
WhatsBot/
├── main.py              # Entry point: logging, Neonize client, menu loop
├── menu.py              # Terminal UI: standby menu, live log view, model picker
├── config.py            # All config from .env + SOUL.md loader
├── whatsapp.py          # Message handler: on_message, commands, files, LID resolution
├── bot_settings.py      # Thread-safe runtime settings (dm, group_mention, self_chat)
├── sessionstore.py      # Session store: sessions, messages, summaries (persistent SQLite)
├── away.py              # Away mode: owner-activity tracking + override
├── database.py          # SQLite: facts table (per-user durable facts)
├── llm.py               # LLM client, conversation assembly, summary, fact extraction
├── prompts.py           # Prompt scaffolding (group note, summary, fact prompts)
├── SOUL.md              # Your persona (gitignored — create from SOUL.example.md)
├── SOUL.example.md      # Public template for SOUL.md
├── requirements.txt     # Dependencies
├── .env                 # Environment config (gitignored)
├── example.env          # Template for .env
├── app.log              # Log file (all DEBUG level)
├── db/
│   ├── conversations.sqlite3   # Sessions, messages, summaries, facts
│   └── neonize.sqlite3         # Neonize/Whatsmeow session state
├── messages/            # Raw received media (from history sync)
├── downloads/           # Admin-uploaded original files
├── converted/           # Extracted text (PDF/DOCX → .txt)
└── assets/              # Images for README
```

---

## Documentation

For deeper technical detail, see:

| Document | Contents |
|----------|----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, message flow, threading model, state management |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Every command with examples and expected responses |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Complete env var reference with defaults and examples |
| [docs/MEMORY.md](docs/MEMORY.md) | Session/memory/fact system: how context is built, summarized, pruned |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | venv setup, systemd service, llama-swap configuration, production tips |

---

## Troubleshooting

### QR code not showing
- Make sure your terminal supports ANSI (most do). Try `NO_COLOR=1 python main.py`.
- The QR appears in the log too: check `app.log`.

### "not in reply whitelist" for a user who should be whitelisted
- Phone numbers must be in international format without `+` or `@`. Example: `6281234567890`.
- The `:N` device suffix is stripped automatically, so `6281234567890:52` matches `6281234567890`.
- Check the log for `Resolved LID ... -> ...` to see what phone number was resolved.

### Bot not responding to DMs
- Check `!status` — is the `dm` toggle ON? (Defaults ON.)
- Check `REPLY_WHITELIST` in `.env` — is the number included?
- Check the log for "Skipping DM from ..." or "not in reply whitelist".

### Bot responding in the wrong language
- The language rule lives in `SOUL.md` ("respond in the same language as the user's message"). If it's mixing languages, the model may be too small — try a larger model, or strengthen the rule in `SOUL.md`.

### LLM errors
- Check `app.log` for "Error calling LLM API". Common causes: model not loaded, server unreachable, API key invalid.
- For `llama.cpp`, verify the server is running: `curl http://localhost:8080/v1/models`.

### Messages from history sync
- On first connect, Whatsmeow syncs conversation history. These are backfilled into sessions but **not** processed for replies (only real-time messages are).

### Away mode not triggering (or triggering too early)
- The owner is "away" when they have not sent any message for `AWAY_AFTER_MINUTES` (default 30). Owner activity = their own messages (matched by `OWNER_NUMBER` or pushname) + self-chat.
- `!away` shows the current state and lets you force it: `!away on` (force away), `!away off` (force present), `!away auto` (derive from timing). The menu (Settings → Set away mode) does the same.
- The away message is LLM-generated (not hardcoded). It is only pushed on the **first reply of each session** after the owner goes away; later replies keep chatting without repeating "the owner is away". It re-announces when the owner comes back and goes away again, or in a new session (`!new`). Adjust the wording in `away.py:away_context()`.
- The `({SIGNATURE_NAME})` signature is only added to DM / self-chat replies (never groups) and only when `SIGNATURE_ENABLED=true`.

---

## Acknowledgments

This project is a fork of [whatsapp-ai-chatbot](https://github.com/GPT-Laboratory/whatsapp-ai-chatbot) by the GPT Lab Seinäjoki / Tampere University team.

It utilizes the [Neonize](https://github.com/krypton-byte/neonize) Python library, which wraps [Whatsmeow](https://github.com/tulir/whatsmeow) for WhatsApp automation.

## License

MIT — see [LICENSE](LICENSE).
