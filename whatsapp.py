import os
import random
import re
import time
from collections import defaultdict, deque

import pdfplumber
from docx import Document
from neonize.client import NewClient
from neonize.events import MessageEv, HistorySyncEv
from neonize.utils import log
from neonize.utils.enum import ChatPresence, ChatPresenceMedia, ReceiptType
from neonize.utils.jid import Jid2String

from config import ADMIN_NUMBERS, REPLY_WHITELIST, SESSION_GAP_SECONDS
from bot_settings import get_settings, toggle as toggle_setting, status_text
from database import (
    delete_messages,
    get_messages,
    get_session_stats,
    is_new_session,
    prune_old_messages,
    save_message,
)
from llm import generate_final_response, generate_first_time_greeting
from prompts import (
    PRIVATE_FINAL_RESPONSE_PROMPT,
    PRIVATE_GREETING_PROMPT,
    PRIVATE_WATCHDOG_PROMPT,
    PUBLIC_FINAL_RESPONSE_PROMPT,
    PUBLIC_GREETING_PROMPT,
)


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal attacks."""
    # Replace any path separators and special chars, keep only basename
    filename = os.path.basename(filename)
    # Remove any characters that aren't alphanumeric, dots, hyphens, or underscores
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    # Prevent hidden files (dotfiles)
    if filename.startswith("."):
        filename = "_" + filename
    return filename or "file"

is_bot_running = True

def set_bot_running(value: bool):
    """Toggle the bot's running state (used by terminal commands)."""
    global is_bot_running
    is_bot_running = bool(value)
    log.info(f"Bot running state set to: {is_bot_running}")

# Public prompts (admin can edit, reset on startup)
public_prompts = {
    "greeting": PUBLIC_GREETING_PROMPT,
    "final_response": PUBLIC_FINAL_RESPONSE_PROMPT
}
# Yksityiset promptit (ei muokattavissa)
private_prompts = {
    "greeting": PRIVATE_GREETING_PROMPT,
    "final_response": PRIVATE_FINAL_RESPONSE_PROMPT,
    "watchdog": PRIVATE_WATCHDOG_PROMPT
}

# --- Self-chat / identity helpers ---
_self_chat_jid = None  # set on first connection (normalized via Jid2String)

def set_self_chat_jid(jid):
    """Record the bot's own chat JID (e.g. '6285732705939@s.whatsapp.net')."""
    global _self_chat_jid
    _self_chat_jid = Jid2String(jid)
    log.info(f"Self-chat JID set: {_self_chat_jid}")

def get_self_chat_jid():
    return _self_chat_jid

def is_self_chat(chat_jid, sender_id):
    """True if this is a message in the bot's own self-chat."""
    chat_str = Jid2String(chat_jid) if hasattr(chat_jid, "User") else str(chat_jid)
    if _self_chat_jid and chat_str == _self_chat_jid:
        return True
    # Fallback: a DM to yourself
    return bool(sender_id) and sender_id == chat_str

def get_bot_jid(client):
    """Return the bot's own JID (as a JID object) if available."""
    try:
        me = client.get_me()
        if me and getattr(me, "JID", None):
            return me.JID
    except Exception as e:
        log.debug(f"get_me() failed: {e}")
    return None

# --- LID -> phone-number resolution (cached) ---
# In groups, whatsmeow reports the sender as a LID (Server == "lid") instead of a
# phone number, which breaks phone-based whitelists. We resolve LID -> phone once
# and cache it so repeat messages don't re-query the store.
_lid_cache = {}  # lid_str -> phone_str

def resolve_sender_phone(client, sender_jid) -> str:
    """
    Return the phone number (user part) for a sender JID.
    - If the JID is already a phone (Server == 's.whatsapp.net'), returns it as-is.
    - If it's a LID (Server == 'lid'), resolves it to a phone via get_pn_from_lid,
      with caching. Falls back to the raw user string if resolution fails.
    """
    if sender_jid is None:
        return ""
    try:
        s = Jid2String(sender_jid)
    except Exception:
        return str(sender_jid)
    # Not a LID -> already a phone number JID
    if getattr(sender_jid, "Server", "") != "lid":
        return s.split("@", 1)[0]
    # LID -> resolve
    if s in _lid_cache:
        return _lid_cache[s]
    phone = s.split("@", 1)[0]  # fallback = raw LID user
    try:
        pn = client.get_pn_from_lid(sender_jid)
        if pn is not None:
            phone = Jid2String(pn).split("@", 1)[0]
    except Exception as e:
        log.debug(f"get_pn_from_lid failed for {s}: {e}")
    _lid_cache[s] = phone
    return phone

def is_bot_mentioned(message, bot_jid):
    """Check if the bot is @mentioned in this message (group contextInfo)."""
    if not bot_jid:
        return False
    try:
        bot_str = Jid2String(bot_jid)
        ext = getattr(message.Message, "extendedTextMessage", None)
        if not ext:
            return False
        ctx = getattr(ext, "contextInfo", None)
        if not ctx:
            return False
        for jid in ctx.mentionedJID:
            if Jid2String(jid) == bot_str:
                return True
    except Exception as e:
        log.debug(f"mention check failed: {e}")
    return False

# --- Command: reply toggles + status (self-chat / admin) ---
def handle_reply_commands(client, chat, sender_id, text):
    """
    Handle !replies and !status commands.
    Returns True if the message was a handled command.
    """
    text = text.strip()
    parts = text.split()
    cmd = parts[0].lower()

    if cmd in ("!replies", "!toggle"):
        # Format: !replies <group_mention|dm|self_chat> <on|off>
        # or:     !replies  (show status)
        if len(parts) < 2:
            client.send_message(chat, status_text())
            return True

        key = parts[1].lower()
        # normalize "self_chat" / "self-chat" / "selfchat" -> "self_chat"
        key = key.replace("-", "_").replace(" ", "_")
        if key in ("selfchat",):
            key = "self_chat"

        if len(parts) < 3:
            client.send_message(chat, f"Usage: !replies {key} <on|off>")
            return True

        val_raw = parts[2].lower()
        if val_raw not in ("on", "off", "true", "false"):
            client.send_message(chat, f"Invalid value: {val_raw}. Use 'on' or 'off'.")
            return True

        value = val_raw in ("on", "true")
        ok, msg = toggle_setting(key, value)
        if ok:
            client.send_message(chat, f"✓ {msg}\n\n" + status_text())
        else:
            client.send_message(chat, f"✗ {msg}")
        return True

    if cmd in ("!status",):
        client.send_message(chat, status_text())
        return True

    return False

# --- Helper functions ---
def convert_pdf_to_markdown(pdf_path):
    """Converts a PDF file to markdown text."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            markdown_lines = []
            for page_num, page in enumerate(pdf.pages, start=1):
                markdown_lines.append(f"## Page {page_num}\n")
                text = page.extract_text()
                if text:
                    markdown_lines.append(text.strip() + "\n")
                else:
                    markdown_lines.append("*(No text could be extracted from this page)*\n")
            return "\n".join(markdown_lines)
    except Exception as e:
        return f"Error reading PDF: {e}"

def convert_docx_to_markdown(docx_path):
    """Converts a DOCX file to markdown text."""
    try:
        document = Document(docx_path)
        markdown_text = ""
        for paragraph in document.paragraphs:
            markdown_text += paragraph.text + "\n"
        return markdown_text
    except Exception as e:
        return f"Error reading DOCX: {e}"

def handle_greeting(client: NewClient, chat, sender_id, sender_name, text):
    """Handles first-time greeting for a new conversation."""
    # Start timer for minimum response delay
    min_total_delay = random.uniform(2, 5)
    start_time = time.time()

    greeting = generate_first_time_greeting(
        sender_name, text,
        public_prompts["greeting"],
        private_prompts["greeting"]
    )

    # Calculate remaining delay time
    elapsed = time.time() - start_time
    remaining = min_total_delay - elapsed
    if remaining > 0:
        time.sleep(remaining)

    client.send_message(chat, greeting)
    client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_PAUSED, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
    log.info(f"Sent greeting to {sender_name} ({sender_id}).")
    save_message(sender_id, greeting, int(time.time()), True)

def handle_file(client: NewClient, sender_id, message):
    """Handles file attachments (downloads the file)."""
    if sender_id not in ADMIN_NUMBERS:
        log.info(f"Files from {sender_id} are not allowed.")
        return False

    raw_name = (message.Message.documentMessage.fileName or
                message.Message.imageMessage.fileName or "file")
    file_name = sanitize_filename(raw_name)
    chat = message.Info.MessageSource.Chat

    client.send_message(chat, f"[SYSTEM] Lataan nyt tiedoston {file_name}...")
    client.download_any(message=message.Message, path=f"./downloads/{file_name}")
    log.info(f"Downloaded file: {file_name}")

    file_extension = os.path.splitext(file_name)[1].lower()

    if file_extension == ".pdf":
        submission_markdown = convert_pdf_to_markdown(f"./downloads/{file_name}")
    elif file_extension == ".docx":
        submission_markdown = convert_docx_to_markdown(f"./downloads/{file_name}")
    elif file_extension == ".txt":
        with open(f"./downloads/{file_name}", "r", encoding="utf-8") as f:
            submission_markdown = f.read()
    else:
        client.send_message(chat, f"[SYSTEM] Tiedoston '{file_name}' tiedostotyyppi '{file_extension}' ei ole tuettu.")
        return

    base_filename = os.path.splitext(file_name)[0]
    txt_filename = f"{base_filename}.txt"
    txt_filepath = os.path.join("converted", txt_filename)
    try:
        with open(txt_filepath, "w", encoding="utf-8") as f:
            f.write(submission_markdown)
        log.info(f"Converted file saved to: {txt_filepath}")
        client.send_message(chat, "[SYSTEM] File saved successfully and will be used in future responses.")
    except Exception as e:
        log.error(f"Error saving converted file: {e}")
        client.send_message(chat, f"[SYSTEM] Error while saving the file: {e}")

    return True

def handle_commands(client: NewClient, chat, sender_id, text: str) -> bool:
    """
    Checks for special commands. If one of the commands is detected and the sender's number
    matches a specific number, it sends back pre-formatted info and returns True.
    """
    if not text.startswith("!") or not text.strip():
        return

    if sender_id not in ADMIN_NUMBERS:
        log.info(f"Command {text} from {sender_id} not allowed.")
        return False

    log.info(f"Command {text} from {sender_id} is allowed.")
    global is_bot_running

    # Reply toggles + status (no delay — these are control commands)
    if handle_reply_commands(client, chat, sender_id, text):
        log.info(f"Processed reply-toggle command for {sender_id}.")
        return True

    # Add a variable delay before responding
    delay = random.uniform(2, 5)
    time.sleep(delay)
    if text.startswith("!files"):
        # Get list of files in the downloads folder
        downloads_folder = "./downloads"
        files = sorted(os.listdir(downloads_folder))
        if not files:
            client.send_message(chat, "[COMMAND] No files.")
        else:
            files_list = "\n".join([f"{i+1}. {file}" for i, file in enumerate(files)])
            client.send_message(chat, f"[COMMAND] Files in the folder:\n{files_list}")
        log.info(f"Processed !files command for {sender_id}.")
        return True
    elif text.startswith("!removefile"):
        # Remove a file from the downloads folder by ID or filename
        parts = text.split(" ", 1)
        if len(parts) < 2:
            client.send_message(chat, "[COMMAND] Provide the ID number or name of the file to remove.")
            return True
        file_identifier = parts[1].strip()
        downloads_folder = "./downloads"
        files = sorted(os.listdir(downloads_folder))

        # Check if it's a numeric ID
        filename = None
        if file_identifier.isdigit():
            file_index = int(file_identifier) - 1
            if 0 <= file_index < len(files):
                filename = files[file_index]
            else:
                client.send_message(chat, f"[COMMAND] Invalid ID number: {file_identifier}")
                return True
        else:
            # Treat as filename
            if file_identifier in files:
                filename = file_identifier
            else:
                client.send_message(chat, f"[COMMAND] File not found: {file_identifier}")
                return True

        dl_path = os.path.join(downloads_folder, filename)
        base_filename = os.path.splitext(os.path.basename(filename))[0]
        txt_filename = f"{base_filename}.txt"
        conv_path = os.path.join("./converted", txt_filename)

        os.remove(dl_path)
        if os.path.exists(conv_path):
            os.remove(conv_path)
        client.send_message(chat, f"[COMMAND] File removed: {filename}")
        log.info(f"Processed !removefile command for {sender_id} and file {filename}.")
        return True
    elif text.startswith("!commands") or text.startswith("!komennot"):
        commands = ["!commands - Show available commands",
                    "!files - List files in the downloads folder",
                    "!removefile <ID or filename> - Remove a file from the downloads folder",
                    "!prompts - Show available prompts",
                    "!editprompt <prompt_name> <new_content> - Edit a prompt",
                    "!renamebot <new_name> - Change the bot's name",
                    "!memory - Show your session/memory stats",
                    "!reset - Clear your conversation history",
                    "!pause - Pause the bot",
                    "!resume - Resume the bot",
                    "!permanentstop - Stop the bot permanently"]
        commands_joined = "\n".join(commands)
        client.send_message(chat, f"[COMMAND] Available commands:\n{commands_joined}")
        log.info(f"Processed !commands command for {sender_id}.")
        return True
    elif text.startswith("!prompts"):
        prompts = [
            f"greeting: {public_prompts['greeting']}",
            f"final_response: {public_prompts['final_response']}"
        ]
        prompts_joined = "\n".join(prompts)
        client.send_message(chat, f"[COMMAND] Public prompts (editable):\n\n{prompts_joined}")
        log.info(f"Processed !prompts command for {sender_id}.")
        return True
    elif text.startswith("!editprompt"):
        parts = text.split(" ", 2)
        if len(parts) < 3:
            client.send_message(chat, "[COMMAND] Provide the prompt name and new content.")
            return True
        prompt_name = parts[1]
        new_prompt_content = parts[2]
        if prompt_name not in public_prompts:
            client.send_message(chat, f"[COMMAND] Unknown prompt name: {prompt_name}")
            return True
        public_prompts[prompt_name] = new_prompt_content
        client.send_message(chat, f"[COMMAND] Public prompt {prompt_name} updated.")
        log.info(f"Processed !editprompt command for {sender_id} and prompt {prompt_name}.")
        return True
    elif text.startswith("!renamebot"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            client.send_message(chat, "[COMMAND] Provide the new bot name.")
            return True
        new_bot_name = parts[1].strip()
        if not new_bot_name:
            client.send_message(chat, "[COMMAND] Provide the new bot name.")
            return True
        client.send_message(chat, f"[COMMAND] Bot name updated: {new_bot_name}.")
        log.info(f"Processed !renamebot command for {sender_id}. New name: {new_bot_name}")
        return True
    elif text.startswith("!memory"):
        stats = get_session_stats(sender_id)
        gap_str = f"{stats['session_gap_seconds'] // 60} min ago" if stats['session_gap_seconds'] is not None else "never"
        age_str = f"{stats['session_age_seconds'] // 60} min" if stats['session_age_seconds'] else "new"
        first_str = "yes (first ever)" if stats['is_first_ever'] else ("yes (after gap)" if stats['is_new_session'] else "no")
        msg = (
            f"[COMMAND] Memory stats for {sender_id}:\n"
            f"- Total messages: {stats['message_count']}\n"
            f"- Current session: {age_str}\n"
            f"- Last message: {gap_str}\n"
            f"- New session: {first_str}\n"
            f"- Est. storage: {stats['estimated_bytes'] // 1024} KB"
        )
        client.send_message(chat, msg)
        log.info(f"Processed !memory command for {sender_id}.")
        return True
    elif text.startswith("!reset"):
        client.send_message(chat, "[COMMAND] Conversation history cleared!")
        delete_messages(sender_id)
        log.info(f"Cleared conversation history for {sender_id} due to '!reset' command.")
        return True
    elif text.startswith("!pause"):
        client.send_message(chat, "[COMMAND] Bot is now paused!")
        is_bot_running = False
        log.info(f"Processed !pause command for {sender_id}.")
        return True
    elif text.startswith("!resume"):
        client.send_message(chat, "[COMMAND] Bot has resumed!")
        is_bot_running = True
        log.info(f"Processed !resume command for {sender_id}.")
        return True
    elif text.startswith("!permanentstop"):
        client.send_message(chat, "[COMMAND] Bot is shutting down permanently!")
        log.info(f"Processed !permanentstop command for {sender_id}.")
        os._exit(0)
        return True
    elif text.startswith("!"):
        client.send_message(chat, "[COMMAND] Unknown command. Use !commands to see available commands.")
        log.info(f"Processed unknown command for {sender_id}.")
        return True

    return False

def handle_final_response(client: NewClient, chat, sender_id, text, user_name: str = "User"):
    """Generates and sends the final response using the LLM, with watchdog check."""
    from llm import call_watchdog_llm

    # Start timer for minimum response delay
    min_total_delay = random.uniform(2, 5)
    start_time = time.time()

    is_relevant, response = call_watchdog_llm(text, private_prompts["watchdog"])
    if not is_relevant:
        # Watchdog blocked the message — stay silent, no reply sent.
        # (Old behavior: sent the watchdog's canned response to the user.)
        # # Calculate remaining delay time
        # elapsed = time.time() - start_time
        # remaining = min_total_delay - elapsed
        # if remaining > 0:
        #     time.sleep(remaining)
        #
        # client.send_message(chat, response)
        log.info(f"Watchdog blocked response for {sender_id}. Message not relevant.")
        return

    final_answer = generate_final_response(
        user_id=sender_id,
        user_text=text,
        public_prompt=public_prompts["final_response"],
        private_prompt=private_prompts["final_response"],
        user_name=user_name,
    )
    log.debug(f"Final answer generated: {final_answer}")
    if not final_answer.strip():
        log.info(f"No final response generated for {sender_id}.")
        return

    # Calculate remaining delay time
    elapsed = time.time() - start_time
    remaining = min_total_delay - elapsed
    if remaining > 0:
        time.sleep(remaining)

    client.send_message(chat, final_answer)
    client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_PAUSED, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
    save_message(sender_id, final_answer, int(time.time()), True)
    log.info(f"Sent final response to {sender_id}.")

def on_history_sync(client: NewClient, history: HistorySyncEv):
    """
    Processes historical messages from the sync data, storing them in the DB.
    The data structure is at `history.Data.conversations[...]`.
    """
    sync_type = getattr(history.Data, "syncType", None)
    log.info(f"Received history sync event with syncType: {sync_type}")
    log.debug(f"Full history sync data: {history}")

    if not hasattr(history.Data, "conversations"):
        log.info("No conversations found in HistorySyncEv.")
        return

    for conversation in history.Data.conversations:
        user_id = conversation.ID.split('@')[0]  # e.g. phone number
        log.debug(f"Processing conversation for user {user_id}")
        for message_obj in conversation.messages:
            message_data = message_obj.message
            msg = message_data.message
            from_me = getattr(message_data.key, "fromMe", False)
            log.debug(f"Processing message (from_me={from_me}): {msg}")

            # Extract text from different possible fields
            if hasattr(msg, "conversation") and msg.conversation:
                message_content = msg.conversation
            elif (hasattr(msg, "extendedTextMessage") and msg.extendedTextMessage.text):
                message_content = msg.extendedTextMessage.text
            else:
                log.debug("Message is not a text message; skipping.")
                continue

            timestamp = message_obj.message.messageTimestamp
            log.debug(f"Saving message with timestamp {timestamp} for user {user_id}")
            save_message(user_id, message_content, timestamp, from_me)

# Rate limiting: user_id -> deque of timestamps (seconds)
user_message_timestamps = defaultdict(lambda: deque(maxlen=5))

def can_respond_to_user(user_id):
    now = time.time()
    timestamps = user_message_timestamps[user_id]
    # Remove timestamps older than 30 seconds
    while timestamps and now - timestamps[0] > 30:
        timestamps.popleft()
    return len(timestamps) < 5

def record_user_response(user_id):
    user_message_timestamps[user_id].append(time.time())

def on_message(client: NewClient, message: MessageEv):
    """
    Real-time incoming messages.
    Uses one big try/except to capture any errors and separates out key functionality
    into helper functions.
    """
    try:
        chat = message.Info.MessageSource.Chat
        sender_id = message.Info.MessageSource.Chat.User
        text = (message.Message.conversation or
                message.Message.extendedTextMessage.text or
                message.Message.imageMessage.caption or
                message.Message.documentMessage.caption or
                "")
        from_me = message.Info.MessageSource.IsFromMe
        is_group = message.Info.MessageSource.IsGroup
        is_edit = message.IsEdit
        is_viewonce = message.IsViewOnce or message.IsViewOnceV2 or message.IsViewOnceV2Extension
        timestamp = message.Info.Timestamp // 1000  # Convert ms to s if needed
        sender_name = message.Info.Pushname or "User"

        log.info(f"Message from {sender_name} ({sender_id}): {text}")

        # Determine the bot's own JID and whether this is the self-chat
        bot_jid = get_bot_jid(client)
        if _self_chat_jid is None and bot_jid:
            set_self_chat_jid(bot_jid)
        is_self = is_self_chat(chat, sender_id)

        # Note: in whatsmeow's event stream IsFromMe is true for ALL inbound
        # messages (both others' DMs and your own self-chat), so it cannot be
        # used to detect echoes. Self-chat is detected by JID match (is_self)
        # and handled in the gating below. No echo-skip needed here.

        # Skip edit and view once messages
        if is_edit or is_viewonce:
            log.info(f"Skipping edit/view once message from {sender_id}.")
            return

        # --- Reply gating ---
        settings = get_settings()
        if is_group:
            # Rule 1: group replies are ALWAYS disabled unless @mentioned
            # (and only when the group_mention toggle is on).
            if settings["group_mention"] and is_bot_mentioned(message, bot_jid):
                sender_id = message.Info.MessageSource.Sender or sender_id
                log.info(f"Group @mention (enabled): processing from {sender_name} ({sender_id}).")
            else:
                log.info(f"Skipping group message from {sender_id} (group replies always off; mention={settings['group_mention']}).")
                return
        elif is_self:
            # Rule 4: self-chat replies (default on, toggleable)
            if not settings["self_chat"]:
                log.info(f"Skipping self-chat message (self_chat toggle off).")
                return
            log.info(f"Self-chat message: processing.")
        else:
            # Rule 3: DM replies (default off, toggleable)
            if not settings["dm"]:
                log.info(f"Skipping DM from {sender_id} (dm toggle off).")
                return
            log.info(f"DM message (enabled): processing from {sender_id}.")

        # Check if the message is older than one minute
        if time.time() - timestamp > 60:
            log.info(f"Message from {sender_name} ({sender_id}) is older than one minute; skipping.")
            return

        # Mark as read
        client.mark_read(
            message.Info.ID,
            chat=chat,
            sender=message.Info.MessageSource.Sender,
            receipt=ReceiptType.READ
        )
        log.info(f"Marked message {message.Info.ID} as read.")

        # Save the incoming message to the DB
        save_message(sender_id, text, timestamp, from_me)
        log.info(f"Saved incoming message for user {sender_id} at timestamp {timestamp}.")

        client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_COMPOSING, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)

        # Check for a command and process it if present.
        if handle_commands(client, chat, sender_id, text):
            # If a command was processed, do not process further.
            client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_PAUSED, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
            return

        # Process file attachments if present
        if message.Info.Type == "media" and not message.Info.MediaType == "url":
            if handle_file(client, sender_id, message):
                return

        if not is_bot_running:
            log.info("Bot is paused; skipping message processing.")
            return

        # Whitelist: only reply to allowed phone numbers.
        # sender_id may be a LID (in groups) -> resolve to phone first.
        if REPLY_WHITELIST and "*" not in REPLY_WHITELIST:
            sender_phone = resolve_sender_phone(client, message.Info.MessageSource.Sender or message.Info.MessageSource.Chat)
            if not sender_phone:
                sender_phone = sender_id
            # Normalize: strip '+', '@', and whitespace so format differences don't matter
            norm = lambda s: s.strip().lstrip("+").replace("@", "")
            sender_normalized = norm(sender_phone)
            whitelist_normalized = {norm(num) for num in REPLY_WHITELIST}
            if sender_normalized not in whitelist_normalized:
                log.info(f"User {sender_id} (phone={sender_phone}) not in reply whitelist; skipping response.")
                return

        # Rate limiting: only respond if under the limit
        if not can_respond_to_user(sender_id):
            log.info(f"Rate limit reached for {sender_id}; skipping response.")
            return

        # Session detection
        is_new, is_first = is_new_session(sender_id)
        stats = get_session_stats(sender_id)
        log.info(f"User {sender_id}: new_session={is_new}, first_ever={is_first}, "
                 f"messages={stats['message_count']}, gap={stats['session_gap_seconds']}s")

        # Prune old messages (fire-and-forget, non-blocking)
        if not is_new and stats["message_count"] > 100:
            pruned = prune_old_messages(sender_id)
            if pruned:
                log.info(f"Pruned {pruned} old messages for {sender_id}.")

        # Process greeting for a first-time message
        if is_first:
            handle_greeting(client, chat, sender_id, sender_name, text)
            record_user_response(sender_id)
            return

        log.info(f"Generating final response for {sender_id}...")
        handle_final_response(client, chat, sender_id, text, sender_name)
        record_user_response(sender_id)

    except Exception as e:
        log.error(f"Error in on_message handler: {e}")
        log.exception(e)
