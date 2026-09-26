# Configuration

Complete reference for all environment variables and runtime settings.

---

## `.env` Variables

Copy `example.env` to `.env` and edit. All variables are read once at startup via `python-dotenv`.

### Core

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASSISTANT_NAME` | ✅ | — | The bot's persona name. Injected into prompts as `{ai_assistant_name}`. Example: `Yuki` |
| `ADMIN_NUMBERS` | ✅ | — | Comma-separated phone numbers (international format, no `+`) allowed to send admin commands. Example: `6285732705939` |
| `REPLY_WHITELIST` | ❌ | `*` | Comma-separated phone numbers that get AI responses. `*` = everyone. Example: `6285732705939,6287711076107` |
| `LLM_PROVIDER` | ✅ | — | One of: `openai`, `azure`, `openrouter`, `llamacpp` |

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
| `LLAMACPP_BASE_URL` | If llamacpp | `http://localhost:27906/v1` | Base URL of the llama.cpp server (OpenAI-compatible) |
| `LLAMACPP_MODEL` | ❌ | `Qwen3.8-Orca-27B-Instruct` | Model name/ID |

> For llama-swap, the base URL should point to the llama-swap instance (default port 27906). For llama-server, use the server's port (default 8080).

### Session / Memory

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SESSION_GAP_SECONDS` | ❌ | `3600` | Silence gap (seconds) before a new session starts. 3600 = 1 hour. |
| `VERBATIM_TURNS` | ❌ | `10` | How many recent messages are kept verbatim in the context window. Older messages are summarized. |
| `CONTEXT_CHAR_BUDGET` | ❌ | `2000` | Max characters for the assembled context (facts + summary + tail + current message). ~2000 chars ≈ 500 tokens. |
| `MEMORY_CUTOFF_DAYS` | ❌ | `30` | Messages older than this many days are pruned from the DB. |
| `FACT_CUTOFF_DAYS` | ❌ | `60` | Facts whose `last_confirmed` is older than this many days are pruned. |
| `MAX_FACTS_IN_CONTEXT` | ❌ | `8` | Max number of facts injected into the context window. |
| `MAX_REPLY_TOKENS` | ❌ | `500` | Hard token cap per LLM reply (passed to the API). |
| `MAX_REPLY_WORDS` | ❌ | `120` | Soft word cap (prompt instruction to the LLM). |

> `MAX_MESSAGES` is deprecated (replaced by `VERBATIM_TURNS`). It's still read from `.env` but only used in `database.get_recent_messages()` which is no longer called in the main pipeline.

---

## Runtime Settings (`bot_settings.py`)

These are **not** in `.env` — they're toggled at runtime via commands or the menu. They persist for the lifetime of the process (reset on restart to defaults).

| Setting | Default | Toggle Command | Description |
|---------|---------|----------------|-------------|
| `group_mention` | `False` | `!replies group_mention on/off` | Reply to @mentions in groups |
| `dm` | `True` | `!replies dm on/off` | Reply to direct messages |
| `self_chat` | `True` | `!replies self_chat on/off` | Reply in the bot's own self-chat |

> **Group replies are always disabled** (hardcoded). The only group reply path is @mention, and only when `group_mention` is ON.

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
| `db/conversations.sqlite3` | Bot conversation memory (messages, summaries, facts) |
| `db/neonize.sqlite3` | Neonize/Whatsmeow session state (device keys, LID mapping) |
| `messages/` | Raw received media from history sync |
| `downloads/` | Admin-uploaded original files (PDF, DOCX, TXT) |
| `converted/` | Extracted text from uploads (`.txt` files) |
| `app.log` | Log file (all DEBUG level) |

---

## Example `.env`

```env
ASSISTANT_NAME=Yuki
ADMIN_NUMBERS=6285732705939
REPLY_WHITELIST=6285732705939,6287711076107

# LLM Provider
LLM_PROVIDER=llamacpp

# --- llama.cpp / llama-swap ---
LLAMACPP_BASE_URL=http://localhost:27906/v1
LLAMACPP_MODEL=Qwen3.8-Orca-27B-Instruct

# --- OpenAI (uncomment if using) ---
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini

# --- Azure OpenAI (uncomment if using) ---
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
# CONTEXT_CHAR_BUDGET=2000
# MEMORY_CUTOFF_DAYS=30
# FACT_CUTOFF_DAYS=60
# MAX_FACTS_IN_CONTEXT=8
# MAX_REPLY_TOKENS=500
# MAX_REPLY_WORDS=120
```
