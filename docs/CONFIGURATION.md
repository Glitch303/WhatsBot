# Configuration

Complete reference for all environment variables and runtime settings.

---

## `.env` Variables

Copy `example.env` to `.env` and edit. All variables are read once at startup via `python-dotenv`.

### Core

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_NUMBERS` | ✅ | — | Comma-separated phone numbers (international format, no `+`) allowed to send admin commands. Example: `6285712345678` |
| `OWNER_NAME` | ❌ | `Owner` | The owner's name. Injected into the system prompt so the persona knows who gets the private face. |
| `OWNER_NUMBER` | ❌ | — (pushname match) | Owner's phone number (international format, no `+`). When set, the owner is matched by exact phone; when unset, by pushname. |
| `REPLY_WHITELIST` | ❌ | `*` | Comma-separated phone numbers that get AI responses. `*` = everyone. Example: `6285712345678,6281234567890` |
| `LLM_PROVIDER` | ✅ | — | One of: `openai`, `azure`, `openrouter`, `llamacpp` |

> **Persona:** the bot's character is not configured via env — it lives in `SOUL.md` (see [File Paths](#file-paths)). Copy `SOUL.example.md` to `SOUL.md` and edit. The file is re-read on every message, so edits apply live.

### OpenAI

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | If openai | — | OpenAI API key |
| `OPENAI_MODEL` | ❌ | `gpt-4o-mini` | Model name |

### Azure OpenAI

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_ENDPOINT` | If azure | — | Azure OpenAI endpoint URL. Example: `https://my-resource.openai.azure.com/` |
| `AZURE_DEPLOYMENT_NAME` | If azure | — | Deployment name. Example: `gpt-4.1-nano` |
| `AZURE_SUBSCRIPTION_KEY` | If azure | — | Azure subscription key |
| `AZURE_API_VERSION` | If azure | — | API version. Example: `2024-12-01-preview` |

### OpenRouter

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | If openrouter | — | OpenRouter API key |
| `OPENROUTER_MODEL` | ❌ | `openai/gpt-4o-mini` | Model name (OpenRouter format) |

### llama.cpp / llama-swap

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLAMACPP_BASE_URL` | If llamacpp | `http://localhost:8080/v1` | Base URL of the llama.cpp server (OpenAI-compatible) |
| `LLAMACPP_MODEL` | ❌ | `Qwen3.8-Orca-27B-Instruct` | Model name/ID |

> For llama-swap, the base URL should point to the llama-swap instance (its configured port). For llama-server, use the server's port (default 8080).

### Session / Memory

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SESSION_GAP_SECONDS` | ❌ | `3600` | Silence gap (seconds). When the last activity in a session is older than this, the LLM is told "new session, last conversation ended X hours ago". |
| `VERBATIM_TURNS` | ❌ | `10` | How many recent *user turns* (and the replies between them) are kept verbatim in the context. Older messages are folded into the rolling summary. |
| `MEMORY_CUTOFF_DAYS` | ❌ | `30` | Messages older than this many days are pruned from the DB. |
| `FACT_CUTOFF_DAYS` | ❌ | `60` | Facts whose `last_confirmed` is older than this many days are pruned. |
| `MAX_FACTS_IN_CONTEXT` | ❌ | `8` | Max number of facts injected into the system prompt. |
| `MAX_REPLY_TOKENS` | ❌ | `500` | Hard token cap per LLM reply (passed to the API). |

---

## Runtime Settings (`bot_settings.py`)

These are **not** in `.env` — they're toggled at runtime via commands or the menu. They persist for the lifetime of the process (reset on restart to defaults).

| Setting | Default | Toggle Command | Description |
|---------|---------|----------------|-------------|
| `group_mention` | `False` | `!replies group_mention on/off` | Shared group session: ON = every group message is recorded as context in the `ch-{group}` session and the bot speaks when @mentioned; OFF = groups are ignored entirely |
| `dm` | `True` | `!replies dm on/off` | Reply to direct messages |
| `self_chat` | `True` | `!replies self_chat on/off` | Reply in the bot's own self-chat |

---

## Runtime LLM Overrides (menu)

The model picker in the terminal menu sets runtime overrides that take precedence over `.env` for the current process:

| Override | Source | Description |
|----------|--------|-------------|
| `provider` | Menu → Select model | `openai`, `azure`, `openrouter`, `llamacpp` |
| `model` | Menu → Select model | Model name/deployment |
| `base_url` | Menu → Select model | For `llamacpp`: server URL; for `azure`: endpoint |

For `azure`, the menu also sets `AZURE_DEPLOYMENT_NAME`, `AZURE_ENDPOINT`, and `AZURE_SUBSCRIPTION_KEY` in `os.environ` so the config module picks them up.

> Runtime overrides are lost on restart. To make them permanent, edit `.env`.

---

## File Paths

| Path | Description |
|------|-------------|
| `SOUL.md` | The bot's persona (gitignored — copy from `SOUL.example.md`). Re-read on every message. |
| `SOUL.example.md` | Public persona template (committed). |
| `db/conversations.sqlite3` | Bot conversation memory: sessions, messages, summaries, facts |
| `db/neonize.sqlite3` | Neonize/Whatsmeow session state (device keys, LID mapping) |
| `messages/` | Raw received media from history sync |
| `downloads/` | Admin-uploaded original files (PDF, DOCX, TXT) |
| `converted/` | Extracted text from uploads (`.txt` files) |
| `app.log` | Log file (all DEBUG level) |

---

## Example `.env`

```env
ADMIN_NUMBERS=6285712345678
OWNER_NAME=YourName
OWNER_NUMBER=6285712345678
REPLY_WHITELIST=6285712345678,6281234567890

# LLM Provider
LLM_PROVIDER=llamacpp

# --- llama.cpp / llama-swap ---
LLAMACPP_BASE_URL=http://localhost:8080/v1
LLAMACPP_MODEL=Qwen3.8-Orca-27B-Instruct

# --- OpenAI (uncomment if using) ---
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini

# --- Azure (uncomment if using) ---
# AZURE_ENDPOINT=https://xxx.openai.azure.com/
# AZURE_DEPLOYMENT_NAME=gpt-4.1-nano
# AZURE_SUBSCRIPTION_KEY=xxx
# AZURE_API_VERSION=2024-12-01-preview

# --- OpenRouter (uncomment if using) ---
# OPENROUTER_API_KEY=sk-or-...
# OPENROUTER_MODEL=openai/gpt-4o-mini

# --- Session / Memory (optional) ---
# SESSION_GAP_SECONDS=3600
# VERBATIM_TURNS=10
# MEMORY_CUTOFF_DAYS=30
# FACT_CUTOFF_DAYS=60
# MAX_FACTS_IN_CONTEXT=8
# MAX_REPLY_TOKENS=500
```
