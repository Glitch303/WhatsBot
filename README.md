# WhatsBot — WhatsApp AI Chatbot

A personal WhatsApp AI assistant that runs on a local LLM (or any OpenAI-compatible API). It answers DMs, self-chat messages, and optional group @mentions — all while keeping a rolling conversation memory, durable facts, and a watchdog filter that keeps it from answering the wrong things.

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
| **Multi-Provider LLM** | Works with OpenAI, Azure OpenAI, OpenRouter, or any local llama.cpp / llama-swap server. Switch providers at runtime from the menu. |
| **Self-Chat Support** | The bot's own "Message yourself" chat works as a control channel — send commands and test responses without involving another person. |
| **DM / Group / Self-Chat Toggles** | Reply behavior for each channel type can be turned on/off at runtime (self-chat defaults ON, DM defaults OFF, group @mention defaults OFF). |
| **Reply Whitelist** | Restrict who gets AI responses to a comma-separated list of phone numbers. `*` = everyone. |
| **Watchdog Filter** | A secondary LLM call screens each message for inappropriate, violent, or malicious content before the main response is generated. |
| **Rolling Memory** | Conversation history is summarized into a compact rolling summary when it exceeds the verbatim window, so long conversations don't blow the context budget. |
| **Fact Extraction** | Durable facts (names, preferences, plans, etc.) are extracted from messages and injected into future context. |
| **File Ingestion** | Send PDF, DOCX, or TXT files from an admin number — content is extracted and used as context for all subsequent responses. |
| **Session Awareness** | Detects when a user returns after a long gap and tells the LLM about it ("new session, last conversation was 3 hours ago"). |
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
         ├─ Parse: chat JID, sender, text, from_me, is_group, is_edit, is_viewonce
         │
         ├─ Resolve LID → phone number (cached)
         │
         ├─ Classify: self-chat? group? DM?
         │     │
         │     ├─ group:  reply only if @mentioned AND group_mention toggle ON
         │     ├─ self:   reply if self_chat toggle ON (default)
         │     └─ DM:     reply if dm toggle ON
         │
         ├─ Skip if older than 60s, or if bot is paused (except self-chat)
         │
         ├─ Mark as read
         ├─ Save to SQLite (messages table)
         │
         ├─ Is it a command? ──► handle_commands() → return
         ├─ Is it a file?    ──► handle_file()     → return
         │
         ├─ Whitelist check (phone number match)
         ├─ Rate limit check
         │
         ├─ Session detection (new? first-ever? ongoing?)
         ├─ Prune old messages if > 100 stored
         ├─ Summarize older messages if > VERBATIM_TURNS
         ├─ Extract facts (background thread, non-blocking)
         │
         ├─ First-ever message? ──► handle_greeting()
         └─ Otherwise           ──► handle_final_response()
                                       │
                                       ├─ Watchdog LLM call (relevant?)
                                       │     └─ not relevant → silent
                                       │
                                       └─ Generate final response
                                             (budget-aware context:
                                              facts + summary + tail)
```

### Memory Model

The bot maintains three layers of context for each user:

| Layer | What | Lifetime | Budget |
|-------|------|----------|--------|
| **Facts** | Durable, verifiable statements (names, preferences, plans) | 60 days (pruned if unconfirmed) | 25% of context |
| **Summary** | Rolling conversation summary (LLM-generated) | 30 days | 50% of remaining |
| **Verbatim Tail** | Last N messages (exact text) | 30 days | Rest of budget |

The total context is capped at `CONTEXT_CHAR_BUDGET` (default 2000 chars ≈ 500 tokens).

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
# Edit .env — set ASSISTANT_NAME, ADMIN_NUMBERS, and your LLM provider

# 5. Start
python main.py
```

The first time you run it, the menu will show a QR code. Scan it with WhatsApp → **Settings → Linked Devices → Link a Device**.

### Using llama-swap (recommended for local models)

```bash
# Install llama-swap from https://github.com/mostlygeek/llama-swap
# Download a GGUF model (e.g. from HuggingFace) into the models directory

# Start the server (auto-loads models on demand):
llama-swap -port 27906 -m ./models

# In .env:
LLM_PROVIDER=llamacpp
LLAMACPP_BASE_URL=http://localhost:27906/v1
LLAMACPP_MODEL=Qwen3.8-Orca-27B-Instruct
```

---

## Configuration

All configuration lives in `.env`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSISTANT_NAME` | — | The bot's persona name (injected into prompts) |
| `ADMIN_NUMBERS` | — | Comma-separated phone numbers allowed to send commands |
| `REPLY_WHITELIST` | `*` | Phone numbers that get AI responses (`*` = everyone) |
| `LLM_PROVIDER` | — | `openai` \| `azure` \| `openrouter` \| `llamacpp` |
| `SESSION_GAP_SECONDS` | `3600` | Silence gap (seconds) before a new session starts |
| `VERBATIM_TURNS` | `10` | How many recent messages kept verbatim in context |
| `CONTEXT_CHAR_BUDGET` | `2000` | Max chars for assembled context (~500 tokens) |
| `MEMORY_CUTOFF_DAYS` | `30` | Prune messages older than this |
| `FACT_CUTOFF_DAYS` | `60` | Prune facts unconfirmed for longer than this |
| `MAX_FACTS_IN_CONTEXT` | `8` | Max facts injected into the context window |
| `MAX_REPLY_TOKENS` | `500` | Hard token cap per LLM reply |
| `MAX_REPLY_WORDS` | `120` | Soft word cap (prompt instruction) |

### LLM Provider Options

| Provider | Env vars | Notes |
|----------|----------|-------|
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | Standard OpenAI API |
| `azure` | `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT_NAME`, `AZURE_SUBSCRIPTION_KEY`, `AZURE_API_VERSION` | Azure OpenAI Service |
| `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter gateway |
| `llamacpp` | `LLAMACPP_BASE_URL`, `LLAMACPP_MODEL` | Local llama.cpp / llama-swap (OpenAI-compatible) |

---

## Admin Commands

Send these from your admin number (configured in `ADMIN_NUMBERS`):

| Command | Description |
|---------|-------------|
| `!commands` | List all available commands |
| `!files` | List uploaded files |
| `!removefile <ID or name>` | Remove a file |
| `!prompts` | Show current editable prompts |
| `!editprompt <name> <content>` | Edit a public prompt |
| `!renamebot <name>` | Change the bot's name |
| `!memory` | Show your session/memory stats |
| `!facts` | List stored facts about you |
| `!forget <ID>` | Delete a specific fact by ID |
| `!stats` | Show full memory/stats overview |
| `!reset` | Clear conversation history for your number |
| `!pause` | Pause the bot (self-chat still works) |
| `!resume` | Resume the bot |
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
  │  Model:     llamacpp / Qwen... │
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
    [5] Quit
```

### Menu Items

| # | Action | Description |
|---|--------|-------------|
| 1 | **Select model** | Switch LLM provider and model at runtime. For `llamacpp`, fetches the model list from the server. |
| 2 | **Settings** | Toggle DM / group @mention / self-chat replies. View context/memory settings. |
| 3 | **Connect / Disconnect** | Connect = QR-scan to log in (session shows "CONNECTED"). Disconnect = stop bot + drop session. |
| 4 | **Start bot** | Activate the bot and enter the live log view. Session stays connected. |
| 5 | **Quit** | Exit the program. |

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
| **Running** (after Start) | ✅ Connected | ▶️ Active | ✅ Works | ✅ (if toggle ON) | ✅ (if @mentioned + toggle ON) |
| **Disconnected** | ❌ | ⏸ | ❌ | ❌ | ❌ |

---

## File Structure

```
WhatsBot/
├── main.py              # Entry point: logging, Neonize client, menu loop
├── menu.py              # Terminal UI: standby menu, live log view, model picker
├── config.py            # All config from .env (loaded once at startup)
├── whatsapp.py          # Message handler: on_message, commands, files, LID resolution
├── bot_settings.py      # Thread-safe runtime settings (dm, group_mention, self_chat)
├── database.py          # SQLite: messages, summaries, facts tables + helpers
├── llm.py              # LLM provider client, watchdog, summary, fact extraction, context builder
├── prompts.py           # All system prompts (public editable + private)
├── requirements.txt     # Dependencies
├── .env                 # Environment config (gitignored)
├── example.env          # Template for .env
├── app.log              # Log file (all DEBUG level)
├── db/
│   ├── conversations.sqlite3   # Bot conversation memory
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
- Phone numbers must be in international format without `+` or `@`. Example: `6287711076107`.
- The `:N` device suffix is stripped automatically, so `6287711076107:52` matches `6287711076107`.
- Check the log for `Resolved LID ... -> ...` to see what phone number was resolved.

### Bot not responding to DMs
- Check `!status` — is the `dm` toggle ON? (Defaults OFF.)
- Check `REPLY_WHITELIST` in `.env` — is the number included?
- Check the log for "Skipping DM from ..." or "not in reply whitelist".

### Bot responding in the wrong language
- The private prompts enforce "respond in the same language as the user's message." If it's mixing languages, the model may be too small. Try a larger model.

### LLM errors
- Check `app.log` for "Error calling LLM API". Common causes: model not loaded, server unreachable, API key invalid.
- For `llamacpp`, verify the server is running: `curl http://localhost:27906/v1/models`.

### Messages from history sync
- On first connect, Whatsmeow syncs conversation history. These are saved to the DB but **not** processed for replies (only real-time messages are).

---

## Acknowledgments

This project is a fork of [whatsapp-ai-chatbot](https://github.com/GPT-Laboratory/whatsapp-ai-chatbot) by the GPT Lab Seinäjoki / Tampere University team.

It utilizes the [Neonize](https://github.com/krypton-byte/neonize) Python library, which wraps [Whatsmeow](https://github.com/tulir/whatsmeow) for WhatsApp automation.

## License

MIT — see [LICENSE](LICENSE).
