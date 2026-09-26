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
    CONTEXT_CHAR_BUDGET,
    LLM_PROVIDER,
    LLAMACPP_BASE_URL,
    LLAMACPP_MODEL,
    MAX_FACTS_IN_CONTEXT,
    MAX_REPLY_TOKENS,
    MAX_REPLY_WORDS,
    SESSION_GAP_SECONDS,
    VERBATIM_TURNS,
)
from database import (
    get_recent_messages_formatted,
    get_verbatim_tail,
    get_messages_since,
    get_summary,
    save_summary,
    save_fact,
    get_facts,
    is_new_session,
    get_session_stats,
)
from prompts import SUMMARY_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT


def get_client_and_model():
    """
    Returns an LLM client configured for the chosen provider.
    Uses the OpenAI Python SDK interface for all providers.
    Supported: "openai", "azure", "openrouter", "llamacpp"

    Runtime overrides from the menu (menu._runtime) take precedence over .env.
    """
    # Check for runtime overrides set via the menu
    try:
        from menu import get_runtime
        rt = get_runtime()
    except ImportError:
        rt = {}

    provider = rt.get("provider") or LLM_PROVIDER
    override_model = rt.get("model")
    override_base_url = rt.get("base_url")

    if provider == "openai":
        model = override_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAI(), model
    elif provider == "azure":
        model = override_model or AZURE_DEPLOYMENT_NAME
        endpoint = override_base_url or AZURE_ENDPOINT
        return (
            AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=AZURE_SUBSCRIPTION_KEY,
                api_version=AZURE_API_VERSION,
            ),
            model,
        )
    elif provider == "openrouter":
        model = override_model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        return (
            OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
            model,
        )
    elif provider == "llamacpp":
        base_url = override_base_url or LLAMACPP_BASE_URL
        model = override_model or LLAMACPP_MODEL
        return OpenAI(base_url=base_url, api_key="llamacpp"), model
    else:
        raise ValueError(f"Unsupported LLM provider: {provider!r}. Expected one of: openai, azure, openrouter, llamacpp")


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
            max_tokens=MAX_REPLY_TOKENS,
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
        additional_content=additional_content or "No additional info from files.",
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


def summarize_session(user_id: str) -> bool:
    """
    Summarize messages that fall outside the verbatim tail window.
    Merges with the existing summary (if any) and stores the result.
    Returns True if a summary was generated and saved, False otherwise.
    """
    import time
    tail = get_verbatim_tail(user_id, VERBATIM_TURNS)
    if not tail:
        return False

    oldest_tail_ts = tail[0][1]  # timestamp of the oldest message in the verbatim tail
    existing_summary, last_summarized_ts = get_summary(user_id)

    # Only summarize if there are messages older than the tail window
    # and they haven't been summarized yet.
    effective_since = last_summarized_ts if last_summarized_ts else 0
    messages_to_summarize = get_messages_since(user_id, effective_since)

    # Exclude messages that are already in the verbatim tail
    tail_timestamps = {ts for _, ts, _ in tail}
    messages_to_summarize = [m for m in messages_to_summarize if m[1] not in tail_timestamps]

    if not messages_to_summarize:
        return False

    # Build the new messages text
    new_lines = []
    for content, ts, from_me in messages_to_summarize:
        speaker = "ASSISTANT" if from_me else "USER"
        new_lines.append(f"{speaker}: {content}")
    new_messages_text = "\n".join(new_lines)

    existing_summary_text = existing_summary or "(none)"

    system_prompt = SUMMARY_SYSTEM_PROMPT.format(
        existing_summary=existing_summary_text,
        new_messages=new_messages_text,
    )

    log.info(f"Summarizing {len(messages_to_summarize)} older messages for user {user_id}")
    result = call_llm_api(system_prompt, "Update the summary.")

    if not result or not result.strip():
        log.warning(f"Summary generation failed for user {user_id}.")
        return False

    result = result.strip()
    # Cap the summary length to prevent unbounded growth
    max_summary_chars = 1200  # ~300 words
    if len(result) > max_summary_chars:
        result = result[:max_summary_chars]

    # Use the timestamp of the last message we just summarized
    last_summarized = max(m[1] for m in messages_to_summarize)
    save_summary(user_id, result, last_summarized)
    log.info(f"Saved summary for user {user_id} ({len(result)} chars, up to ts={last_summarized}).")
    return True


def normalize_entity(entity: str) -> str:
    """
    Normalize an entity name for consistent matching.
    Lowercase, strip punctuation, collapse whitespace.
    'Syahid' -> 'syahid', 'Dr. John Smith' -> 'john smith'
    """
    import re
    entity = entity.strip().lower()
    entity = re.sub(r"[^\w\s]", "", entity)  # remove punctuation
    entity = re.sub(r"\s+", " ", entity).strip()
    return entity or "unknown"


def extract_facts(user_id: str, user_message: str) -> int:
    """
    Extract durable facts from a user message and save them to the DB.
    Returns the number of facts saved (0 if none or on error).
    """
    if not user_message or not user_message.strip():
        return 0

    system_prompt = FACT_EXTRACTION_PROMPT.format(user_message=user_message)
    result = call_llm_api(system_prompt, "Extract facts from the message above.")

    if not result or not result.strip():
        return 0

    # Parse the JSON array
    try:
        # Handle cases where the model wraps in code fences
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        facts = json.loads(result)
        if not isinstance(facts, list):
            return 0
    except (json.JSONDecodeError, IndexError) as e:
        log.warning(f"Failed to parse fact extraction response: {e}")
        return 0

    saved = 0
    for item in facts[:5]:  # cap at 5 facts per message
        if not isinstance(item, dict):
            continue
        fact_text = str(item.get("fact", "")).strip()
        entity_raw = str(item.get("entity", "unknown")).strip()
        confidence = float(item.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]

        if not fact_text:
            continue

        entity = normalize_entity(entity_raw)
        save_fact(user_id, fact_text, entity, confidence)
        saved += 1
        log.info(f"Extracted fact for {user_id}: [{entity}] {fact_text} (conf={confidence:.1f})")

    return saved


def generate_first_time_greeting(user_name: str, user_message: str, public_prompt: str, private_prompt: str) -> str:
    """
    Generates a first-time greeting by combining public and private prompts.
    """
    log.info(f"Generating first time greeting for user: {user_name}")
    system_prompt = (public_prompt + "\n" + private_prompt).format(ai_assistant_name=AI_ASSISTANT_NAME)
    raw_response = call_llm_api(system_prompt, user_message)
    final_response = raw_response.replace("USER_NAME_HERE", user_name)
    return final_response


def build_context(user_id: str, current_message: str, char_budget: int = None) -> str:
    """
    Build a budget-aware context string from:
      1. Stored facts (most recent first)
      2. Rolling conversation summary
      3. Verbatim message tail
    Returns a formatted conversation block that fits within char_budget.
    If there's no context at all, returns "".
    """
    if char_budget is None:
        char_budget = CONTEXT_CHAR_BUDGET

    tail = get_verbatim_tail(user_id, VERBATIM_TURNS)
    if not tail:
        return ""

    # --- Facts section ---
    facts = get_facts(user_id, MAX_FACTS_IN_CONTEXT)

    # --- Summary section ---
    summary_text, _ = get_summary(user_id)

    # Format tail as conversation lines (newest last)
    lines = []
    for content, ts, from_me in tail:
        speaker = "ASSISTANT" if from_me else "USER"
        lines.append(f"{speaker}: {content}")

    # Reserve space for the current message + separators.
    reserved = len(current_message) + 60  # separators, labels
    available = char_budget - reserved

    # Facts get 25% of the budget (highest priority -- durable knowledge)
    facts_text = ""
    facts_reserved = 0
    if facts:
        facts_lines = []
        for fid, fact, entity, conf, created, confirmed in facts:
            facts_lines.append(f"- {fact}")
        facts_text = "[FACTS]\n" + "\n".join(facts_lines)
        max_facts = available // 4  # 25% cap
        if len(facts_text) > max_facts:
            facts_text = facts_text[:max_facts]
        facts_reserved = len(facts_text) + 2

    # Summary gets 50% of remaining space
    summary_reserved = 0
    max_summary = 0
    if summary_text:
        summary_label = "[CONVERSATION SUMMARY]\n"
        max_summary = min(len(summary_text), (available - facts_reserved) // 2)
        summary_reserved = len(summary_label) + max_summary + 2

    tail_available = available - facts_reserved - summary_reserved

    # Walk from newest to oldest, accumulating until we hit the budget
    kept = []
    used = 0
    for line in reversed(lines):
        line_cost = len(line) + 2  # + newline
        if used + line_cost > tail_available and kept:
            break
        kept.insert(0, line)
        used += line_cost

    parts = []
    if facts_text:
        parts.append(facts_text)
    if summary_text:
        truncated = summary_text[:max_summary]
        parts.append(f"[CONVERSATION SUMMARY]\n{truncated}")
    if kept:
        parts.append("\n".join(kept))

    return "\n".join(parts)


def generate_final_response(user_id: str, user_text: str, public_prompt: str, private_prompt: str, user_name: str = "User") -> str:
    """
    Generates the final response combining public and private prompts,
    session context, and converted file content.
    """
    # Session detection
    is_new, is_first = is_new_session(user_id)
    stats = get_session_stats(user_id)

    # Build budget-aware context
    context = build_context(user_id, user_text)
    additional_content = _read_converted_files()

    # Session metadata for the prompt
    if is_first:
        session_info = "This is the user's first message ever."
    elif is_new:
        gap_hours = stats["session_gap_seconds"] / 3600 if stats["session_gap_seconds"] else 0
        session_info = f"New session. Last conversation ended {gap_hours:.1f} hours ago."
    else:
        session_info = f"Ongoing session ({stats['session_age_seconds'] // 60} min active, {stats['message_count']} total messages)."

    system_prompt = (public_prompt + "\n" + private_prompt).format(
        ai_assistant_name=AI_ASSISTANT_NAME,
        previous_messages=context or "(no previous messages)",
        additional_content=additional_content or "No additional info from files.",
        max_reply_words=MAX_REPLY_WORDS,
        session_info=session_info,
    ).replace("USER_NAME_HERE", user_name)
    return call_llm_api(system_prompt, user_text)
