import logging
import os
import segno

def configure_logging():
    # Create a logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Set the overall logging level to DEBUG

    # Define a common log format
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Create and configure a file handler (logs everything)
    file_handler = logging.FileHandler("app.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Log all messages (DEBUG and above)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler: keep the console clean for the interactive terminal prompt.
    # Only WARNING+ shows here (the noisy filter below silences neonize/whatsmeow chatter).
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    class NoisyFilter(logging.Filter):
        """Only let neonize/whatsmeow logs through if they are ERROR or higher."""
        NOISY = ("neonize", "whatsmeow")
        def filter(self, record):
            if any(record.name.startswith(p) for p in self.NOISY):
                return record.levelno >= logging.ERROR
            return True
    console_handler.addFilter(NoisyFilter())
    logger.addHandler(console_handler)

def main():
    configure_logging()

    from neonize.utils import log
    log.setLevel(logging.DEBUG)
    import signal
    from neonize.client import NewClient
    from neonize.events import (
        MessageEv,
        ConnectedEv,
        HistorySyncEv,
        QREv,
        event
    )
    from neonize.utils.enum import Presence
    from database import init_db
    from config import CONV_DB_PATH, NEO_DB_PATH
    from whatsapp import on_history_sync, on_message

    # A global event to handle interrupts
    stop_event = event

    def interrupted(*_):
        """Signal handler for Ctrl+C."""
        logging.info("Received interrupt, terminating.")
        os._exit(0)  # Forceful, immediate termination

    signal.signal(signal.SIGINT, interrupted)

    # Create the DB directory if it doesn't exist
    os.makedirs(os.path.dirname(CONV_DB_PATH), exist_ok=True)
    os.makedirs("messages", exist_ok=True)
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("converted", exist_ok=True)

    # Initialize DB
    init_db()

    # Terminal control commands (run in a background thread so the event loop keeps running)
    import threading
    from bot_settings import get_settings, toggle as toggle_setting, status_text

    def tprint(msg, level="info"):
        """Print to the terminal (clean) AND log to app.log."""
        prefix = {"info": "•", "ok": "✓", "err": "✗"}.get(level, "•")
        print(f"{prefix} {msg}")
        log_fn = {"info": logging.info, "ok": logging.info, "err": logging.error}[level]
        log_fn(f"Terminal: {msg}")

    def print_settings():
        print()
        for line in status_text().splitlines():
            print(line)
        print()

    def terminal_loop():
        tprint("Commands: status | replies <group_mention|dm|self_chat> <on|off> | pause | resume | quit")
        while True:
            try:
                line = input("bot> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            if cmd in ("quit", "exit", "q"):
                tprint("Exiting.", "info")
                os._exit(0)
            elif cmd == "status":
                print_settings()
            elif cmd == "replies":
                # replies <group_mention|dm|self_chat> <on|off>
                if len(parts) < 3:
                    tprint("Usage: replies <group_mention|dm|self_chat> <on|off>", "err")
                    continue
                key = parts[1].lower().replace("-", "_").replace(" ", "_")
                if key == "selfchat":
                    key = "self_chat"
                val = parts[2].lower()
                if val not in ("on", "off", "true", "false"):
                    tprint(f"Invalid value: {val}. Use 'on' or 'off'.", "err")
                    continue
                ok, msg = toggle_setting(key, val in ("on", "true"))
                if ok:
                    tprint(msg, "ok")
                    print_settings()
                else:
                    tprint(msg, "err")
            elif cmd == "pause":
                from whatsapp import set_bot_running
                set_bot_running(False)
                tprint("Bot paused.", "ok")
            elif cmd == "resume":
                from whatsapp import set_bot_running
                set_bot_running(True)
                tprint("Bot resumed.", "ok")
            else:
                tprint(f"Unknown command: {cmd}. Try: status, replies, pause, resume, quit", "err")

    threading.Thread(target=terminal_loop, daemon=True).start()

    # Create the client
    client = NewClient(NEO_DB_PATH)

    @client.event(ConnectedEv)
    def on_connected(client: NewClient, connected: ConnectedEv):
        try:
            client.send_presence(presence=Presence.AVAILABLE)
        except Exception as e:
            # whatsmeow can reject presence right after auth if PushName isn't
            # synced yet. Non-fatal — the bot still works.
            logging.warning(f"Could not send presence yet (non-fatal): {e}")
        logging.info("✓ Connected")

    @client.event(HistorySyncEv)
    def handle_history_sync(client: NewClient, history: HistorySyncEv):
        on_history_sync(client, history)

    @client.event(MessageEv)
    def handle_message(client: NewClient, message: MessageEv):
        on_message(client, message)

    @client.event(QREv)
    def handle_qr(client: NewClient, qr: QREv):
        """Handle QR code event."""
        logging.info("QR Code received.")
        if qr.Codes:
            qr_data_string = qr.Codes[0]
            try:
                qr_code = segno.make(qr_data_string)
                qr_code.terminal(compact=True)
            except Exception as e:
                logging.error(f"Failed to generate or print QR code: {e}")
                logging.error(f"QR Codes data received: {qr.Codes}")
        else:
            logging.warning("Received QREv with no QR codes data.")

    # Connect the client
    client.connect()

    # Keep the program running until a signal is received
    stop_event.wait()
    logging.info("Exiting...")

if __name__ == "__main__":
    main()
