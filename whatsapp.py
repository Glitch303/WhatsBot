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

from config import ADMIN_NUMBERS
from database import delete_messages, get_messages, save_message
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
# Julkiset promptit (admin voi muokata, resetoi käynnistyksessä)
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
        client.send_message(chat, "[SYSTEM] Tiedosto tallennettiin onnistuneesti ja käytetään jatkossa vastausten tuottamisessa.")
    except Exception as e:
        log.error(f"Error saving converted file: {e}")
        client.send_message(chat, f"[SYSTEM] Tapahtui virhe tiedostoa tallentaessa: {e}")

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

    # Add a variable delay before responding
    delay = random.uniform(2, 5)
    time.sleep(delay)
    if text.startswith("!files"):
        # Get list of files in the downloads folder
        downloads_folder = "./downloads"
        files = sorted(os.listdir(downloads_folder))
        if not files:
            client.send_message(chat, "[KOMENTO] Ei tiedostoja.")
        else:
            files_list = "\n".join([f"{i+1}. {file}" for i, file in enumerate(files)])
            client.send_message(chat, f"[KOMENTO] Kansiosta löytyvät tiedostot:\n{files_list}")
        log.info(f"Processed !files command for {sender_id}.")
        return True
    elif text.startswith("!removefile"):
        # Remove a file from the downloads folder by ID or filename
        parts = text.split(" ", 1)
        if len(parts) < 2:
            client.send_message(chat, "[KOMENTO] Anna poistettavan tiedoston ID-numero tai nimi.")
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
                client.send_message(chat, f"[KOMENTO] Virheellinen ID-numero: {file_identifier}")
                return True
        else:
            # Treat as filename
            if file_identifier in files:
                filename = file_identifier
            else:
                client.send_message(chat, f"[KOMENTO] Tiedostoa ei löytynyt: {file_identifier}")
                return True

        dl_path = os.path.join(downloads_folder, filename)
        base_filename = os.path.splitext(os.path.basename(filename))[0]
        txt_filename = f"{base_filename}.txt"
        conv_path = os.path.join("./converted", txt_filename)

        os.remove(dl_path)
        if os.path.exists(conv_path):
            os.remove(conv_path)
        client.send_message(chat, f"[KOMENTO] Tiedosto poistettu: {filename}")
        log.info(f"Processed !removefile command for {sender_id} and file {filename}.")
        return True
    elif text.startswith("!commands") or text.startswith("!komennot"):
        commands = ["!commands - Näytä käytettävissä olevat komennot",
                    "!files - Listaa tiedostot downloads-kansiosta",
                    "!removefile <ID tai tiedostonimi> - Poista tiedosto downloads-kansiosta",
                    "!prompts - Näytä käytettävissä olevat promptit",
                    "!editprompt <promptin_nimi> <uusi_promptin_sisältö> - Muokkaa promptia",
                    "!renamebot <uusi_botin_nimi> - Vaihda botin nimi",
                    "!reset - Tyhjennä keskusteluhistoria numerollesi",
                    "!pause - Pysäytä botti",
                    "!resume - Jatka botin toimintaa",
                    "!permanentstop - Sammuta botti pysyvästi"]
        commands_joined = "\n".join(commands)
        client.send_message(chat, f"[KOMENTO] Käytettävissä olevat komennot:\n{commands_joined}")
        log.info(f"Processed !commands command for {sender_id}.")
        return True
    elif text.startswith("!prompts"):
        prompts = [
            f"greeting: {public_prompts['greeting']}",
            f"final_response: {public_prompts['final_response']}"
        ]
        prompts_joined = "\n".join(prompts)
        client.send_message(chat, f"[KOMENTO] Julkiset promptit (muokattavissa):\n\n{prompts_joined}")
        log.info(f"Processed !prompts command for {sender_id}.")
        return True
    elif text.startswith("!editprompt"):
        parts = text.split(" ", 2)
        if len(parts) < 3:
            client.send_message(chat, "[KOMENTO] Anna muokattava promptin nimi ja uusi sisältö.")
            return True
        prompt_name = parts[1]
        new_prompt_content = parts[2]
        if prompt_name not in public_prompts:
            client.send_message(chat, f"[KOMENTO] Tuntematon promptin nimi: {prompt_name}")
            return True
        public_prompts[prompt_name] = new_prompt_content
        client.send_message(chat, f"[KOMENTO] Julkinen prompti {prompt_name} päivitetty.")
        log.info(f"Processed !editprompt command for {sender_id} and prompt {prompt_name}.")
        return True
    elif text.startswith("!renamebot"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            client.send_message(chat, "[KOMENTO] Anna uusi botin nimi.")
            return True
        new_bot_name = parts[1].strip()
        if not new_bot_name:
            client.send_message(chat, "[KOMENTO] Anna uusi botin nimi.")
            return True
        client.send_message(chat, f"[KOMENTO] Botin nimi päivitetty: {new_bot_name}.")
        log.info(f"Processed !renamebot command for {sender_id}. New name: {new_bot_name}")
        return True
    elif text.startswith("!reset"):
        client.send_message(chat, "[KOMENTO] Keskusteluhistoria tyhjennetty!")
        delete_messages(sender_id)
        log.info(f"Cleared conversation history for {sender_id} due to '!reset' command.")
        return True
    elif text.startswith("!pause"):
        client.send_message(chat, "[KOMENTO] Botti on nyt pysäytetty!")
        is_bot_running = False
        log.info(f"Processed !pause command for {sender_id}.")
        return True
    elif text.startswith("!resume"):
        client.send_message(chat, "[KOMENTO] Botti on nyt jatkanut toimintaansa!")
        is_bot_running = True
        log.info(f"Processed !resume command for {sender_id}.")
        return True
    elif text.startswith("!permanentstop"):
        client.send_message(chat, "[KOMENTO] Botti sammuu nyt pysyvästi!")
        log.info(f"Processed !permanentstop command for {sender_id}.")
        os._exit(0)
        return True
    elif text.startswith("!"):
        client.send_message(chat, "[KOMENTO] Tuntematon komento. Käytä !commands nähdäksesi käytettävissä olevat komennot.")
        log.info(f"Processed unknown command for {sender_id}.")
        return True

    return False

def handle_final_response(client: NewClient, chat, sender_id, text):
    """Generates and sends the final response using the LLM, with watchdog check."""
    from llm import call_watchdog_llm

    # Start timer for minimum response delay
    min_total_delay = random.uniform(2, 5)
    start_time = time.time()

    is_relevant, response = call_watchdog_llm(text, private_prompts["watchdog"])
    if not is_relevant:
        # Calculate remaining delay time
        elapsed = time.time() - start_time
        remaining = min_total_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

        client.send_message(chat, response)
        log.info(f"Watchdog prevented response for {sender_id}. Message not relevant.")
        return

    final_answer = generate_final_response(
        user_id=sender_id,
        user_text=text,
        public_prompt=public_prompts["final_response"],
        private_prompt=private_prompts["final_response"]
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

        # Skip messages from the bot itself
        if chat.User == sender_id and from_me:
            log.info(f"Skipping message from bot itself: {sender_id}")
            return

        # Skip group, edit, and view once messages
        if is_group or is_edit or is_viewonce:
            log.info(f"Skipping group/edit/view once message from {sender_id}.")
            return

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

        # Rate limiting: only respond if under the limit
        if not can_respond_to_user(sender_id):
            log.info(f"Rate limit reached for {sender_id}; skipping response.")
            return

        # Determine if this is the first message from the user
        previous_messages = get_messages(sender_id)
        is_first_message = len(previous_messages) == 1
        log.info(f"User {sender_id} has {len(previous_messages)} previous messages; is_first_message={is_first_message}")

        # Process greeting for a first-time message
        if is_first_message:
            handle_greeting(client, chat, sender_id, sender_name, text)
            record_user_response(sender_id)
            return

        log.info(f"Generating final response for {sender_id}...")
        handle_final_response(client, chat, sender_id, text)
        record_user_response(sender_id)

    except Exception as e:
        log.error(f"Error in on_message handler: {e}")
        log.exception(e)
