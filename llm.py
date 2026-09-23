import json
import os
from typing import Optional, Tuple

from neonize.utils import log
from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel

from config import (
    AI_ASSISTANT_NAME,
    AZURE_API_VERSION,
    AZURE_DEPLOYMENT_NAME,
    AZURE_ENDPOINT,
    AZURE_SUBSCRIPTION_KEY,
    LLM_PROVIDER,
    LLAMACPP_BASE_URL,
    LLAMACPP_MODEL,
)
from database import get_recent_messages_formatted


def get_client_and_model():
    """
    Returns an LLM client configured for the chosen provider.
    Uses the OpenAI Python SDK interface for all providers.
    Supported: "openai", "azure", "openrouter", "llamacpp"
    """
    if LLM_PROVIDER == "openai":
        return OpenAI(), os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    elif LLM_PROVIDER == "azure":
        return (
            AzureOpenAI(
                azure_endpoint=AZURE_ENDPOINT,
                api_key=AZURE_SUBSCRIPTION_KEY,
                api_version=AZURE_API_VERSION,
            ),
            AZURE_DEPLOYMENT_NAME,
        )
    elif LLM_PROVIDER == "openrouter":
        return (
            OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
            os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        )
    elif LLM_PROVIDER == "llamacpp":
        return OpenAI(base_url=LLAMACPP_BASE_URL, api_key="llamacpp"), LLAMACPP_MODEL
    else:
        raise ValueError(f"Unsupported LLM provider: {LLM_PROVIDER!r}. Expected one of: openai, azure, openrouter, llamacpp")


def call_llm_api(system_prompt: str, user_prompt: str) -> str:
    """
    Unified function to call the LLM API using the OpenAI Python SDK.
    Returns the response text, or "" on error.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        client, model = get_client_and_model()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
        )
        return response.choices[0].message.content
    except Exception as e:
        log.error(f"Error calling LLM API: {e}")
        return ""


def _call_llm_structured(system_prompt: str, user_prompt: str, max_tokens: int = 200) -> dict:
    """
    Calls the LLM and parses the JSON response.
    Uses response_format=json_object when supported, falls back to manual JSON parsing.
    Returns the parsed dict, or {} on error.
    """
    try:
        client, model = get_client_and_model()

        # Try structured output first (OpenAI beta, Azure, OpenRouter)
        try:
            response = client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                response_format=WatchdogResponse,
            )
            parsed = response.choices[0].message.parsed
            return {"relevant": parsed.relevant, "response": parsed.response or ""}
        except Exception:
            # Fallback: plain call with json_object format
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse LLM JSON response: {e}")
        return {}
    except Exception as e:
        log.error(f"Error calling structured LLM: {e}")
        return {}


def call_watchdog_llm(user_message: str, watchdog_prompt: str) -> Tuple[bool, Optional[str]]:
    """
    Calls the watchdog LLM and returns (is_relevant, response_text).
    Returns (False, None) on error.
    """
    log.info(f"Calling watchdog LLM with user message: {user_message}")
    additional_content = _read_converted_files()

    system_prompt = watchdog_prompt.format(
        user_message=user_message,
        additional_content=additional_content or "Ei lisätietoa tiedostoista.",
    )

    result = _call_llm_structured(system_prompt, user_message)

    if not result:
        log.warning("Watchdog LLM returned no valid response; defaulting to relevant.")
        return True, None

    is_relevant = result.get("relevant", True)
    response_text = result.get("response", "")
    return is_relevant, response_text or None


class WatchdogResponse(BaseModel):
    relevant: bool
    response: Optional[str] = None


def _read_converted_files() -> str:
    """Reads all converted files from the converted/ directory."""
    converted_dir = "converted"
    if not os.path.isdir(converted_dir):
        return ""
    additional_content = ""
    for file in os.listdir(converted_dir):
        filepath = os.path.join(converted_dir, file)
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                additional_content += f.read()
    return additional_content


def generate_first_time_greeting(user_name: str, user_message: str, public_prompt: str, private_prompt: str) -> str:
    """
    Generates a first-time greeting by combining public and private prompts.
    """
    log.info(f"Generating first time greeting for user: {user_name}")
    system_prompt = (public_prompt + "\n" + private_prompt).format(ai_assistant_name=AI_ASSISTANT_NAME)
    raw_response = call_llm_api(system_prompt, user_message)
    final_response = raw_response.replace("USER_NAME_HERE", user_name)
    return final_response


def generate_final_response(user_id: str, user_text: str, public_prompt: str, private_prompt: str) -> str:
    """
    Generates the final response combining public and private prompts,
    conversation history, and converted file content.
    """
    conversation_history = get_recent_messages_formatted(user_id)
    additional_content = _read_converted_files()

    system_prompt = (public_prompt + "\n" + private_prompt).format(
        ai_assistant_name=AI_ASSISTANT_NAME,
        previous_messages=conversation_history,
        additional_content=additional_content or "Ei lisätietoa tiedostoista.",
    )
    return call_llm_api(system_prompt, user_text)
