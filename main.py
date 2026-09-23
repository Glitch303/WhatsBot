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

    # Create and configure a console handler (logs only INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Only log INFO and above to the console
    console_handler.setFormatter(formatter)
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

    # Create the client
    client = NewClient(NEO_DB_PATH)

    @client.event(ConnectedEv)
    def on_connected(client: NewClient, connected: ConnectedEv):
        client.send_presence(presence=Presence.AVAILABLE)
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
