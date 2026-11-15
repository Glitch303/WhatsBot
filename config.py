import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# Project root (absolute, so paths work from any CWD)
_PROJECT_ROOT = Path(__file__).resolve().parent

# Database Configuration
CONV_DB_PATH = str(_PROJECT_ROOT / "db" / "conversations.sqlite3")
NEO_DB_PATH = str(_PROJECT_ROOT / "db" / "neonize.sqlite3")

# "openai" or "azure" or "openrouter" or "llamacpp"
LLM_PROVIDER = os.getenv("LLM_PROVIDER")

# Azure configuration
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
AZURE_SUBSCRIPTION_KEY = os.getenv("AZURE_SUBSCRIPTION_KEY")
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION")

# llama.cpp / llama-swap configuration (local OpenAI-compatible server)
LLAMACPP_BASE_URL = os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080/v1")
LLAMACPP_MODEL = os.getenv("LLAMACPP_MODEL", "Qwen3.8-Orca-27B-Instruct")
# API key for the llama.cpp / llama-swap server (empty = no auth).
LLAMACPP_API_KEY = os.getenv("LLAMACPP_API_KEY", "")

# Persona (soul) configuration
# The bot's persona lives in SOUL.md (public template: SOUL.example.md).
# It is re-read on every message, so edits apply live without a restart.
# Anchored to the project root so it resolves regardless of the CWD.
SOUL_PATH = str(Path(__file__).resolve().parent / "SOUL.md")

DEFAULT_PERSONA = """
You are a helpful AI assistant on WhatsApp.
Reply in the same language the user writes in.
Keep replies short and direct.
"""


def load_soul() -> str:
    """Read the persona from SOUL.md (fresh on every call)."""
    try:
        with open(SOUL_PATH, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
    except OSError:
        pass
    return DEFAULT_PERSONA


# Owner identity: injected into the system prompt so the persona knows
# who gets the private face. OWNER_NUMBER = exact phone match; when unset,
# the owner is matched by pushname.
OWNER_NAME = os.getenv("OWNER_NAME", "Owner")
OWNER_NUMBER = os.getenv("OWNER_NUMBER", "")

# Session / memory configuration
# A new session starts when the user is silent longer than this (seconds).
SESSION_GAP_SECONDS = int(os.getenv("SESSION_GAP_SECONDS", "3600"))  # default 1 hour
# How many recent messages are kept verbatim in the context window.
VERBATIM_TURNS = int(os.getenv("VERBATIM_TURNS", "10"))
# Messages older than this many days are pruned from the DB.
MEMORY_CUTOFF_DAYS = int(os.getenv("MEMORY_CUTOFF_DAYS", "30"))
# Facts older than this many days (unconfirmed) are pruned.
FACT_CUTOFF_DAYS = int(os.getenv("FACT_CUTOFF_DAYS", "60"))
# Max number of facts injected into the context window.
MAX_FACTS_IN_CONTEXT = int(os.getenv("MAX_FACTS_IN_CONTEXT", "8"))

# LLM reply limits
MAX_REPLY_TOKENS = int(os.getenv("MAX_REPLY_TOKENS", "500"))  # hard token cap per reply

# Away mode: the bot steps in as the owner's proxy when the owner is inactive.
# The owner is considered "away" once they have not been active for this many
# minutes (env-adjustable). Override via the menu or !away (auto|present|away).
AWAY_AFTER_MINUTES = int(os.getenv("AWAY_AFTER_MINUTES", "30"))

# Signature: the persona name appended to the end of the bot's replies
# (e.g. "(Yuki)"). Applied to DM / self-chat replies (not groups).
SIGNATURE_NAME = os.getenv("SIGNATURE_NAME", "Yuki")
SIGNATURE_ENABLED = os.getenv("SIGNATURE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

def norm_phone(num: str) -> str:
    """Normalize a phone number for comparison: strip +, @, :N device suffix, whitespace."""
    return (num or "").strip().lstrip("+").replace("@", "").split(":", 1)[0]

ADMIN_NUMBERS = [norm_phone(num) for num in os.getenv("ADMIN_NUMBERS", "").split(",") if num.strip()]

# Reply whitelist: only these numbers will get AI responses.
# Empty or "*" = everyone gets replies. Comma-separated phone numbers (international format).
REPLY_WHITELIST = [num.strip() for num in os.getenv("REPLY_WHITELIST", "*").split(",") if num.strip()]

# Group sessions are shared: every group message is recorded in the ch-{group}
# session as context; the bot only *speaks* on @mention (bot_settings toggle).