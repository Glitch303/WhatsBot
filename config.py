import os
from dotenv import load_dotenv
load_dotenv()

# Database Configuration
CONV_DB_PATH = "db/conversations.sqlite3"
NEO_DB_PATH = "db/neonize.sqlite3"

# "openai" or "azure" or "openrouter" or "llamacpp"
LLM_PROVIDER = os.getenv("LLM_PROVIDER")

# Azure configuration
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
AZURE_SUBSCRIPTION_KEY = os.getenv("AZURE_SUBSCRIPTION_KEY")
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION")

# llama.cpp / llama-swap configuration (local OpenAI-compatible server)
LLAMACPP_BASE_URL = os.getenv("LLAMACPP_BASE_URL", "http://localhost:27906/v1")
LLAMACPP_MODEL = os.getenv("LLAMACPP_MODEL", "Qwen3.8-Orca-27B-Instruct")

# Prompt Configuration
# (MAX_MESSAGES is deprecated -- replaced by VERBATIM_TURNS for the context window)
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "5"))

# Session / memory configuration
# A new session starts when the user is silent longer than this (seconds).
SESSION_GAP_SECONDS = int(os.getenv("SESSION_GAP_SECONDS", "3600"))  # default 1 hour
# How many recent messages are kept verbatim in the context window.
VERBATIM_TURNS = int(os.getenv("VERBATIM_TURNS", "10"))
# Max characters for the assembled context (facts + summary + tail + current msg).
# ~2000 chars ~ 500 tokens, safe for a 27B local model.
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", "2000"))
# Messages older than this many days are pruned from the DB.
MEMORY_CUTOFF_DAYS = int(os.getenv("MEMORY_CUTOFF_DAYS", "30"))
# Facts older than this many days (unconfirmed) are pruned.
FACT_CUTOFF_DAYS = int(os.getenv("FACT_CUTOFF_DAYS", "60"))
# Max number of facts injected into the context window.
MAX_FACTS_IN_CONTEXT = int(os.getenv("MAX_FACTS_IN_CONTEXT", "8"))

# LLM reply limits
MAX_REPLY_TOKENS = int(os.getenv("MAX_REPLY_TOKENS", "500"))  # hard token cap per reply
MAX_REPLY_WORDS = int(os.getenv("MAX_REPLY_WORDS", "120"))   # soft word cap (prompt instruction)

# AI Assistant Configuration
AI_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME")
ADMIN_NUMBERS = [num.strip() for num in os.getenv("ADMIN_NUMBERS", "").split(",") if num.strip()]

# Reply whitelist: only these numbers will get AI responses.
# Empty or "*" = everyone gets replies. Comma-separated phone numbers (international format).
REPLY_WHITELIST = [num.strip() for num in os.getenv("REPLY_WHITELIST", "*").split(",") if num.strip()]

# Group replies are ALWAYS disabled (hardcoded). The only group reply path is
# @mention, controlled at runtime via bot_settings (default off).