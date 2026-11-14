"""LLM layer: client, system-prompt assembly, conversation building,
summarization, fact extraction.

Single pipeline (DiscordBot model): one system prompt (SOUL.md + owner/group
note + memory context) + session history + the current user turn.
"""

import json
import os
import re

from neonize.utils import log
from openai import AzureOpenAI, OpenAI

from config import (
    AZURE_API_VERSION,
    AZURE_DEPLOYMENT_NAME,
    AZURE_ENDPOINT,
    AZURE_SUBSCRIPTION_KEY,
    LLM_PROVIDER,
    LLAMACPP_API_KEY,
    LLAMACPP_BASE_URL,
    LLAMACPP_MODEL,
    MAX_FACTS_IN_CONTEXT,
    MAX_REPLY_TOKENS,
    OWNER_NAME,
    OWNER_NUMBER,
    VERBATIM_TURNS,
    load_soul,
    norm_phone,
)
from database import save_fact, get_facts
from prompts import GROUP_CHAT_NOTE, SUMMARY_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT


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
        api_key = LLAMACPP_API_KEY or "llamacpp"  # "llamacpp" = no-auth servers
        return OpenAI(base_url=base_url, api_key=api_key), model
    else:
        raise ValueError(f"Unsupported LLM provider: {provider!r}. Expected one of: openai, azure, openrouter, llamacpp")


def call_llm_api(messages: list) -> str:
    """
    Unified function to call the LLM API with a full message list
    (system prompt + history + current user turn).
    Returns the response text, or "" on error.
    """
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


# ---- owner identity -----------------------------------------------------------


def is_owner(sender_phone: str, sender_name: str) -> bool:
    """True if the sender is the owner.

    Exact phone match when OWNER_NUMBER is set; otherwise pushname match.
    """
    if OWNER_NUMBER and sender_phone:
        return norm_phone(sender_phone) == norm_phone(OWNER_NUMBER)
    if sender_name and sender_name.strip().lower() == OWNER_NAME.strip().lower():
        return True
    return False


def _owner_tag() -> str:
    if OWNER_NUMBER:
        return f"{OWNER_NAME} (phone {OWNER_NUMBER})"
    return OWNER_NAME


# ---- system prompt / conversation assembly -------------------------------------


def build_system_prompt(is_group: bool, is_owner: bool, session_note: str = "",
                        facts_text: str = "", summary_text: str = "",
                        files_text: str = "") -> str:
    """Assemble the system prompt: SOUL.md + owner/group note + memory context.

    Mirrors DiscordBot: the persona (SOUL.md) is the base; the code states who
    the owner is and whether the current sender is them (DMs), or adds the
    stable group-chat section (shared group sessions).
    """
    soul = load_soul()
    if is_group:
        system = soul + "\n" + GROUP_CHAT_NOTE.strip()
        system += (
            f"\n\n## Owner\nYour owner is {_owner_tag()}. In a group you only speak "
            f"when @mentioned; the note attached to each turn says whether the "
            f"person who mentioned you is your owner. How you treat each person "
            f"is defined in your persona."
        )
    elif is_owner:
        system = (
            soul
            + f"\n\nThe person messaging you right now is your owner: {_owner_tag()}. "
            + "Use your private face."
        )
    else:
        system = (
            soul
            + f"\n\nYour owner is {_owner_tag()}. "
            + "The person messaging you right now is not them. Use your public face."
        )

    extras = []
    if session_note:
        extras.append(session_note)
    if facts_text:
        extras.append(facts_text)
    if summary_text:
        extras.append(f"[CONVERSATION SUMMARY]\n{summary_text}")
    if files_text:
        extras.append(f"[ADDITIONAL CONTENT]\n{files_text}")
    if extras:
        system += "\n\n" + "\n\n".join(extras)
    return system


def _facts_text(user_id: str) -> str:
    """Durable facts about this user, formatted for the system prompt."""
    facts = get_facts(user_id, MAX_FACTS_IN_CONTEXT)
    if not facts:
        return ""
    lines = [f"- {fact}" for (_, fact, _, _, _, _) in facts]
    return "[FACTS about this user]\n" + "\n".join(lines)


def build_convo(store, session_id: str, user_text: str, user_name: str,
                is_group: bool, is_owner: bool, user_id: str,
                session_note: str = "") -> list:
    """Assemble the full conversation: system prompt + session history + the
    current user turn (DiscordBot's single-pipeline model)."""
    summary_text, _ = store.get_summary(session_id)
    system = build_system_prompt(
        is_group=is_group,
        is_owner=is_owner,
        session_note=session_note,
        facts_text=_facts_text(user_id),
        summary_text=summary_text or "",
        files_text=_read_converted_files(),
    )
    history = store.history(session_id, VERBATIM_TURNS, label_senders=is_group)

    user_content = user_text
    if is_group:
        user_content = f"[{user_name}] {user_text}"
        if is_owner:
            user_content += "\n(You are replying to your owner. Private face.)"
        else:
            user_content += "\n(You are replying to someone who is not your owner. Public face.)"

    convo = [{"role": "system", "content": system}]
    convo.extend(history)
    convo.append({"role": "user", "content": user_content})
    return convo


def generate_final_response(store, session_id: str, user_id: str, user_text: str,
                            user_name: str, is_group: bool, is_owner: bool,
                            session_note: str = "") -> str:
    """Generate the reply for the current turn."""
    convo = build_convo(store, session_id, user_text, user_name,
                        is_group, is_owner, user_id, session_note)
    return call_llm_api(convo)


# ---- files ----------------------------------------------------------------------


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


# ---- rolling summary (per session) -------------------------------------------------


def summarize_session(store, session_id: str, user_id: str) -> bool:
    """
    Summarize messages that fall outside the verbatim tail window of this
    session. Merges with the existing summary and stores the result.
    """
    tail_ts = store.oldest_tail_timestamp(session_id, VERBATIM_TURNS)
    if tail_ts is None:
        return False

    existing_summary, last_summarized_ts = store.get_summary(session_id)
    effective_since = last_summarized_ts if last_summarized_ts else 0
    messages_to_summarize = [
        m for m in store.messages_since(session_id, effective_since)
        if m[1] < tail_ts
    ]
    if not messages_to_summarize:
        return False

    new_lines = [
        f"{'ASSISTANT' if role == 'assistant' else 'USER'}: {content}"
        for content, _ts, role in messages_to_summarize
    ]
    system_prompt = SUMMARY_SYSTEM_PROMPT.format(
        existing_summary=existing_summary or "(none)",
        new_messages="\n".join(new_lines),
    )
    result = call_llm_api([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Update the summary."},
    ])
    if not result or not result.strip():
        return False

    result = result.strip()[:1200]
    last_summarized = max(m[1] for m in messages_to_summarize)
    store.save_summary(session_id, result, last_summarized)
    log.info(f"Updated summary for session {session_id} ({len(new_lines)} messages).")
    return True


# ---- fact extraction -----------------------------------------------------------------


def normalize_entity(entity: str) -> str:
    """Normalize an entity string: lowercase, no punctuation, single spaces."""
    if not entity:
        return "unknown"
    entity = entity.strip().lower()
    entity = re.sub(r"[^\w\s]", "", entity)
    entity = re.sub(r"\s+", " ", entity)
    return entity or "unknown"


def extract_facts(user_id: str, user_message: str) -> int:
    """Extract durable facts from a user message. Returns number saved."""
    system_prompt = FACT_EXTRACTION_PROMPT.format(user_message=user_message)
    result = call_llm_api([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Extract facts from the message above."},
    ])
    if not result:
        return 0

    # Strip markdown code fences if present
    result = result.strip()
    if result.startswith("```"):
        result = re.sub(r"^```(?:json)?\s*", "", result)
        result = re.sub(r"\s*```$", "", result)
    try:
        facts = json.loads(result)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse fact extraction JSON: {e}")
        return 0
    if not isinstance(facts, list):
        return 0

    saved = 0
    for item in facts[:5]:
        if not isinstance(item, dict):
            continue
        fact = (item.get("fact") or "").strip()
        if not fact:
            continue
        entity = normalize_entity(item.get("entity"))
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        save_fact(user_id, fact, entity, confidence)
        saved += 1
    return saved
