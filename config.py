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
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "5"))

# AI Assistant Configuration
AI_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME")
ADMIN_NUMBERS = [num.strip() for num in os.getenv("ADMIN_NUMBERS", "").split(",") if num.strip()]