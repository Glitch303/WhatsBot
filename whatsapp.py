import os
import random
import re
import threading
import time
from collections import defaultdict, deque

import pdfplumber
from docx import Document
from neonize.client import NewClient
from neonize.events import MessageEv, HistorySyncEv
from neonize.utils import log
from neonize.utils.enum import ChatPresence, ChatPresenceMedia, ReceiptType
from neonize.utils.jid import Jid2String

import away
from config import ADMIN_NUMBERS, REPLY_WHITELIST, SESSION_GAP_SECONDS, VERBATIM_TURNS, norm_phone
from bot_settings import get_settings, toggle as toggle_setting, status_text
from database import get_facts, delete_fact, get_fact_count, prune_stale_facts
from llm import (
    extract_facts,
    generate_final_response,
    is_owner as is_owner_sender,
    summarize_session,
)
from sessionstore import store


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


# --- Self-chat / identity helpers ---
_self_chat_jid = None  # set on first connection (normalized via Jid2String)


def set_self_chat_jid(jid):
    """Record the bot's own chat JID (e.g. '6285712345678@s.whatsapp.net')."""
    global _self_chat_jid
    _self_chat_jid = Jid2String(jid)
    log.info(f"Self-chat JID set: {_self_chat_jid}")


def get_self_chat_jid():
    return _self_chat_jid


def is_self_chat(chat_jid, sender_id):
    """True if this is a message in the bot's own self-chat.

    A self-chat is the special "Message yourself" chat where the chat JID
    equals the bot's own JID. In a regular DM, the chat JID is the OTHER
    person's number, so it must never match -- even though sender == chat
    in any 1:1 DM (that is the normal case, not a self-chat).
    """
    chat_str = Jid2String(chat_jid) if hasattr(chat_jid, "User") else str(chat_jid)
    if not _self_chat_jid:
        return False
    return chat_str == _self_chat_jid


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


# --- Session keys ------------------------------------------------------------------


def session_key_for(is_group: bool, is_self: bool, chat_jid, sender_id: str) -> str:
    """Stable key per conversation: one shared session per group, one per DM,
    one for the self-chat."""
    if is_self:
        return "self"
    if is_group:
        return f"ch-{Jid2String(chat_jid) if hasattr(chat_jid, 'User') else chat_jid}"
    return f"dm-{sender_id}"


def _session_note(session: dict) -> str:
    """Small session context line for the system prompt (first-ever / gap)."""
    if not session or session["message_count"] == 0:
        return "This is the user's first message ever."
    last = session["last_activity_at"]
    if last and (time.time() - last) > SESSION_GAP_SECONDS:
        hours = (time.time() - last) / 3600
        return f"New session. Last conversation ended {hours:.1f} hours ago."
    return ""


def _whitelisted(phone: str) -> bool:
    """True if the phone number is in the reply whitelist (or the whitelist is open)."""
    if not REPLY_WHITELIST or "*" in REPLY_WHITELIST:
        return True
    return norm_phone(phone) in {norm_phone(num) for num in REPLY_WHITELIST}


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
            client.send_message(chat, f"[OK] {msg}\n\n" + status_text())
        else:
            client.send_message(chat, f"[X] {msg}")
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


def handle_file(client: NewClient, sender_id, message):
    """Handles file attachments (downloads the file)."""
    if norm_phone(sender_id) not in ADMIN_NUMBERS:
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


def _format_sessions(rows: list) -> str:
    """Format a session listing for chat (numbered refs usable with !resume/!delete)."""
    lines = []
    for i, r in enumerate(rows, start=1):
        title = r["title"] or r["id"]
        line = f"{i}. {r['id']} {title}"
        if r["message_count"]:
            line += f" ({r['message_count']} msgs)"
        if r["last_activity_at"]:
            line += f", {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_activity_at']))}"
        if r["preview"]:
            line += f" — {r['preview']}"
        if r["is_current"]:
            line += " [current]"
        lines.append(line)
    return "\n".join(lines)


def handle_commands(client: NewClient, chat, sender_id, sender_name, text: str,
                    is_group: bool, is_self: bool) -> bool:
    """
    Checks for special commands. If one of the commands is detected and the sender's number
    matches a specific number, it sends back pre-formatted info and returns True.
    Commands are control messages: they are not stored in the session history.
    """
    if not text.startswith("!") or not text.strip():
        return

    if norm_phone(sender_id) not in ADMIN_NUMBERS:
        log.info(f"Command {text} from {sender_id} not allowed.")
        return False

    log.info(f"Command {text} from {sender_id} is allowed.")
    global is_bot_running

    # Reply toggles + status (no delay -- these are control commands)
    if handle_reply_commands(client, chat, sender_id, text):
        log.info(f"Processed reply-toggle command for {sender_id}.")
        return True

    # Add a variable delay before responding
    delay = random.uniform(2, 5)
    time.sleep(delay)
    key = session_key_for(is_group, is_self, chat, sender_id)

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
                    "!renamebot <new_name> - Change the bot's name",
                    "!away [on|off|auto] - Show/set away mode (bot replies as your proxy when you're away)",
                    "!new - Start a new session (the current one stays saved)",
                    "!title [name] - Show or set the current session title",
                    "!sessions [search] - List sessions for this chat (or search them)",
                    "!resume <number|id|title> - Resume a saved session (!resume alone resumes the bot)",
                    "!delete <number|id|title> - Delete a session",
                    "!memory - Show your session/memory stats",
                    "!facts - List stored facts about you",
                    "!forget <ID> - Delete a specific fact by ID",
                    "!stats - Show full memory/stats overview",
                    "!reset - Clear all sessions for this chat",
                    "!pause - Pause the bot",
                    "!resume - Resume the bot",
                    "!permanentstop - Stop the bot permanently"]
        commands_joined = "\n".join(commands)
        client.send_message(chat, f"[COMMAND] Available commands:\n{commands_joined}")
        log.info(f"Processed !commands command for {sender_id}.")
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
    elif text.startswith("!away"):
        parts = text.split(" ", 1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg in ("on", "away"):
            away.set_override(away.OVERRIDE_AWAY)
            client.send_message(chat, "[COMMAND] Away mode forced ON (the bot replies as your proxy until you send a message or set it back to auto).")
        elif arg in ("off", "present"):
            away.set_override(away.OVERRIDE_PRESENT)
            client.send_message(chat, "[COMMAND] Away mode forced OFF (the bot will not step in as your proxy).")
        elif arg in ("auto", ""):
            away.set_override(away.OVERRIDE_AUTO)
            st = away.status()
            idle = st["idle_seconds"]
            idle_txt = f"{idle // 60}m" if idle >= 0 else "never"
            state_txt = "AWAY" if st["away"] else "present"
            client.send_message(chat, f"[COMMAND] Away mode: auto (owner {state_txt}, last active {idle_txt} ago, threshold {st['threshold_minutes']}m).")
        else:
            client.send_message(chat, "[COMMAND] Usage: !away [on|off|auto]")
        log.info(f"Processed !away command for {sender_id}.")
        return True
    elif text.startswith("!new"):
        had = store.current_session(key) is not None
        store.end_current(key, "new")
        if had:
            client.send_message(chat, "[COMMAND] New session started. The previous one is saved — use !sessions to find it and !resume <number> to go back.")
        else:
            client.send_message(chat, "[COMMAND] New session started.")
        log.info(f"Processed !new command for {sender_id}.")
        return True
    elif text.startswith("!title"):
        current = store.current_session(key)
        if not current:
            client.send_message(chat, "[COMMAND] No active session for this chat.")
            return True
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            client.send_message(chat, f"[COMMAND] Current title: {current['title'] or '(none)'}")
            return True
        store.set_title(current["id"], parts[1].strip())
        client.send_message(chat, "[COMMAND] Session title set.")
        log.info(f"Processed !title command for {sender_id}.")
        return True
    elif text.startswith("!sessions"):
        parts = text.split(" ", 1)
        query = parts[1].strip() if len(parts) > 1 else ""
        rows = store.search_sessions(key, query) if query else store.list_sessions(key)
        if not rows:
            client.send_message(chat, f"[COMMAND] No sessions found." + (f" (search: {query})" if query else ""))
        else:
            client.send_message(chat, f"[COMMAND] Sessions for this chat:\n" + _format_sessions(rows))
        log.info(f"Processed !sessions command for {sender_id}.")
        return True
    elif text.startswith("!resume"):
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            # Bare !resume = bot resume (legacy behavior)
            is_bot_running = True
            client.send_message(chat, "[COMMAND] Bot has resumed!")
            log.info(f"Processed !resume (bot) command for {sender_id}.")
            return True
        ref = parts[1].strip()
        sid = store.resolve(key, ref)
        if not sid:
            client.send_message(chat, f"[COMMAND] No session matches: {ref}")
            return True
        store.resume(key, sid)
        title = store.session_title(sid)
        client.send_message(chat, f"[COMMAND] Resumed session {sid} ({title or 'untitled'}).")
        log.info(f"Processed !resume command for {sender_id}: {sid}.")
        return True
    elif text.startswith("!delete"):
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            client.send_message(chat, "[COMMAND] Usage: !delete <number|id|title>. Use !sessions to list.")
            return True
        ref = parts[1].strip()
        sid = store.resolve(key, ref)
        if not sid:
            client.send_message(chat, f"[COMMAND] No session matches: {ref}")
            return True
        result = store.delete_session(key, sid)
        extra = " A fresh session was started for this chat." if result.get("new_session") else ""
        client.send_message(chat, f"[COMMAND] Deleted session {result['id']} ({result['title'] or 'untitled'}).{extra}")
        log.info(f"Processed !delete command for {sender_id}: {sid}.")
        return True
    elif text.startswith("!memory"):
        session = store.current_session(key)
        fact_count = get_fact_count(sender_id)
        if not session:
            client.send_message(chat, f"[COMMAND] No session for {sender_id}. Facts stored: {fact_count}")
            return True
        summary_text, _ = store.get_summary(session["id"])
        started = time.strftime("%Y-%m-%d %H:%M", time.localtime(session["started_at"])) if session["started_at"] else "-"
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(session["last_activity_at"])) if session["last_activity_at"] else "-"
        msg = (
            f"[COMMAND] Memory for {sender_id}:\n"
            f"- Session: {session['id']}\n"
            f"- Messages: {session['message_count']}\n"
            f"- Started: {started}\n"
            f"- Last active: {last}\n"
            f"- Summary: {len(summary_text) if summary_text else 0} chars\n"
            f"- Facts stored: {fact_count}"
        )
        client.send_message(chat, msg)
        log.info(f"Processed !memory command for {sender_id}.")
        return True
    elif text.startswith("!facts"):
        facts = get_facts(sender_id, limit=20)
        if not facts:
            client.send_message(chat, "[COMMAND] No facts stored yet.")
            log.info(f"Processed !facts command for {sender_id} (empty).")
            return True
        lines = []
        for fid, fact, entity, conf, created, confirmed in facts:
            lines.append(f"[{fid}] ({entity}) {fact}")
        client.send_message(chat, f"[COMMAND] Stored facts for {sender_id}:\n" + "\n".join(lines))
        log.info(f"Processed !facts command for {sender_id} ({len(facts)} facts).")
        return True
    elif text.startswith("!forget"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            client.send_message(chat, "[COMMAND] Provide the fact ID to delete. Use !facts to see IDs.")
            return True
        try:
            fact_id = int(parts[1].strip())
        except ValueError:
            client.send_message(chat, f"[COMMAND] Invalid ID: {parts[1]}")
            return True
        if delete_fact(fact_id):
            client.send_message(chat, f"[COMMAND] Fact [{fact_id}] deleted.")
            log.info(f"Deleted fact {fact_id} for {sender_id}.")
        else:
            client.send_message(chat, f"[COMMAND] Fact [{fact_id}] not found.")
        return True
    elif text.startswith("!stats"):
        session = store.current_session(key)
        s = store.stats()
        fact_count = get_fact_count(sender_id)
        lines = [
            f"[COMMAND] Stats for {sender_id}:",
            f"- Sessions: {s['total_sessions']} total ({s['named_sessions']} named)",
            f"- Messages: {s['total_messages']}",
            f"- Facts stored: {fact_count}",
        ]
        if session:
            lines.append(f"- Current session: {session['id']} ({session['message_count']} msgs)")
        client.send_message(chat, "\n".join(lines))
        log.info(f"Processed !stats command for {sender_id}.")
        return True
    elif text.startswith("!reset"):
        deleted = store.reset_key(key)
        client.send_message(chat, f"[COMMAND] All sessions for this chat cleared ({deleted} deleted).")
        log.info(f"Reset {deleted} sessions for {sender_id} due to '!reset' command.")
        return True
    elif text.startswith("!pause"):
        client.send_message(chat, "[COMMAND] Bot is now paused!")
        is_bot_running = False
        log.info(f"Processed !pause command for {sender_id}.")
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


def handle_final_response(client: NewClient, chat, sender_id, sender_name, text,
                          is_group: bool, is_self: bool, session_id, session_note: str):
    """Generates and sends the reply for the current turn (single pipeline:
    SOUL.md system prompt + session history + this message)."""
    # Start timer for minimum response delay
    min_total_delay = random.uniform(2, 5)
    start_time = time.time()

    owner = is_self or is_owner_sender(sender_id, sender_name)
    # Away mode: the bot steps in as the owner's proxy for DMs when the owner
    # has been inactive. Groups use the @mention mechanism instead.
    is_away = (not is_group) and (not is_self) and away.is_away()
    if is_away:
        log.info(f"Owner is away (idle {away.idle_seconds()}s); responding as proxy for {sender_id}.")
    final_answer = generate_final_response(
        store, session_id, sender_id, text, sender_name,
        is_group, owner, session_note, is_away=is_away,
    )
    log.debug(f"Final answer generated: {final_answer}")
    if not final_answer or not final_answer.strip():
        log.info(f"No final response generated for {sender_id}.")
        return

    # Calculate remaining delay time
    elapsed = time.time() - start_time
    remaining = min_total_delay - elapsed
    if remaining > 0:
        time.sleep(remaining)

    client.send_message(chat, final_answer)
    client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_PAUSED, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
    store.append(session_id, ("assistant", final_answer), user_id=sender_id, sender=None,
                 timestamp=time.time())
    log.info(f"Sent final response to {sender_id}.")


def on_history_sync(client: NewClient, history: HistorySyncEv):
    """
    Processes historical messages from the sync data, storing them in the session store.
    The data structure is at `history.Data.conversations[...]`.
    """
    sync_type = getattr(history.Data, "syncType", None)
    log.info(f"Received history sync event with syncType: {sync_type}")
    log.debug(f"Full history sync data: {history}")

    if not hasattr(history.Data, "conversations"):
        log.info("No conversations found in HistorySyncEv.")
        return

    for conversation in history.Data.conversations:
        conv_id = conversation.ID  # e.g. '628123456789@s.whatsapp.net' or '123-456@g.us'
        is_group_conv = conv_id.endswith("@g.us")
        user_id = conv_id.split("@")[0]  # phone number or group id
        log.debug(f"Processing conversation {conv_id}")

        if is_group_conv:
            key = f"ch-{conv_id}"
        else:
            key = f"dm-{user_id}"
        session_id = store.ensure_session(key, user_id)

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
            role = "assistant" if from_me else "user"
            store.append(session_id, (role, message_content), user_id=user_id,
                         sender=None, timestamp=timestamp)
            log.debug(f"Saved {role} message (ts={timestamp}) into {session_id}.")


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


def _extract_facts_async(user_id, user_message):
    """Background thread: extract facts from a message without blocking the response."""
    try:
        extract_facts(user_id, user_message)
    except Exception as e:
        log.error(f"Fact extraction error for {user_id}: {e}")


def _cleanup_thread():
    """
    Background thread: runs every 6 hours to prune old session messages and stale facts.
    Also logs a memory report.
    """
    CLEANUP_INTERVAL = 6 * 3600  # 6 hours
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            pruned_msgs = store.prune_old_messages()
            pruned_facts = prune_stale_facts()
            fact_count = get_fact_count()
            log.info(
                f"[CLEANUP] Pruned {pruned_msgs} old messages, {pruned_facts} stale facts. "
                f"Total facts: {fact_count}."
            )
        except Exception as e:
            log.error(f"[CLEANUP] Error: {e}")


def start_cleanup_thread():
    """Start the background cleanup thread (daemon)."""
    t = threading.Thread(target=_cleanup_thread, daemon=True)
    t.start()
    log.info("Cleanup thread started (runs every 6h).")


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

        # Resolve LID -> phone before the self-chat check.
        # Self-chat JIDs from get_me() are phone-based, but incoming messages
        # may carry a LID. Without this, is_self_chat() can't match.
        # In groups the chat JID is the group, so resolve the actual sender instead.
        identity_jid = message.Info.MessageSource.Sender if is_group else chat
        sender_phone = resolve_sender_phone(client, identity_jid)
        if not is_group and sender_phone and sender_phone != sender_id:
            log.info(f"Resolved LID {sender_id} -> {sender_phone}")
            sender_id = sender_phone

        is_self = is_self_chat(chat, sender_id)
        # Fallback: compare phone parts (LID resolved) in case domain differs.
        # Self-chat JID may have a ':N' device suffix (e.g. '628...:17@s.whatsapp.net')
        # so strip that before comparing.
        if not is_self and _self_chat_jid and sender_phone:
            self_phone = _self_chat_jid.split("@", 1)[0].split(":", 1)[0]
            if sender_phone == self_phone:
                is_self = True
                log.info(f"Self-chat matched by phone: {sender_phone}")

        # Away mode: record the owner's activity (their own messages / self-chat).
        # Any message the owner sends resets the "away" timer.
        if is_self or is_owner_sender(sender_phone or sender_id, sender_name):
            away.mark_owner_active(timestamp)

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
            # Groups use one shared session (ch-<group>). With group_mention on,
            # every message is recorded into it (so the bot has read the whole
            # conversation); a reply is only generated when @mentioned.
            if not settings["group_mention"]:
                log.info(f"Skipping group message from {sender_id} (group_mention off).")
                return
            sender_id = resolve_sender_phone(client, message.Info.MessageSource.Sender) or sender_id
            if is_bot_mentioned(message, bot_jid):
                log.info(f"Group @mention (enabled): processing from {sender_name} ({sender_id}).")
            else:
                if not _whitelisted(sender_id):
                    log.info(f"Group context from {sender_id} not whitelisted; not recorded.")
                    return
                key = session_key_for(True, False, chat, sender_id)
                session_id = store.ensure_session(key, sender_id)
                store.append(session_id, ("user", text), user_id=sender_id,
                             sender=sender_name, timestamp=timestamp)
                log.info(f"Recorded group context from {sender_name} into {session_id}.")
                return
        elif is_self:
            # Self-chat replies (default on, toggleable)
            if not settings["self_chat"]:
                log.info(f"Skipping self-chat message (self_chat toggle off).")
                return
            log.info(f"Self-chat message: processing.")
        else:
            # DM replies (default off, toggleable)
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

        client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_COMPOSING, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)

        # Check for a command and process it if present.
        # Commands are control messages: they are not stored in the session.
        if handle_commands(client, chat, sender_id, sender_name, text, is_group, is_self):
            client.send_chat_presence(jid=chat, state=ChatPresence.CHAT_PRESENCE_PAUSED, media=ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
            return

        # Process file attachments if present
        if message.Info.Type == "media" and not message.Info.MediaType == "url":
            if handle_file(client, sender_id, message):
                return

        # Bot paused: self-chat still works in standby, others are skipped.
        if not is_bot_running and not is_self:
            log.info("Bot is paused; skipping message processing.")
            return

        # Whitelist: only reply to allowed phone numbers.
        if not _whitelisted(sender_phone or sender_id):
            log.info(f"User {sender_id} (phone={sender_phone}) not in reply whitelist; skipping response.")
            return

        # Rate limiting: only respond if under the limit
        if not can_respond_to_user(sender_id):
            log.info(f"Rate limit reached for {sender_id}; skipping response.")
            return

        # Session: one shared session per chat (DM / self / group).
        key = session_key_for(is_group, is_self, chat, sender_id)
        session_id = store.ensure_session(key, sender_id)
        session = store.current_session(key)
        session_note = _session_note(session)
        log.info(f"User {sender_id}: session={session_id}, "
                 f"messages={session['message_count'] if session else 0}")
        store.append(session_id, ("user", text), user_id=sender_id,
                     sender=sender_name, timestamp=timestamp)

        # Prune messages older than the memory cutoff (keeps the session lean).
        if session and session["message_count"] > 100:
            pruned = store.prune_old_messages(session_id)
            if pruned:
                log.info(f"Pruned {pruned} old messages from {session_id}.")

        # Summarize older turns once the session outgrows the verbatim tail.
        # This keeps the context window within budget while preserving long-term memory.
        if store.user_message_count(session_id) > VERBATIM_TURNS:
            summarize_session(store, session_id, sender_id)

        # Extract facts asynchronously (non-blocking).
        # Runs in a background thread so it doesn't delay the user's response.
        threading.Thread(
            target=_extract_facts_async,
            args=(sender_id, text),
            daemon=True
        ).start()

        log.info(f"Generating response for {sender_id}...")
        handle_final_response(client, chat, sender_id, sender_name, text,
                              is_group, is_self, session_id, session_note)
        record_user_response(sender_id)

    except Exception as e:
        log.error(f"Error in on_message handler: {e}")
        log.exception(e)
