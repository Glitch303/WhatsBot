import argparse
import logging
import os
import threading
import time

def configure_logging():
    """Configure logging — no file handler in headless mode (logs go to log stream)."""
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

def main():
    parser = argparse.ArgumentParser(description="WhatsBot")
    parser.add_argument("--headless", action="store_true", help="Run as headless service (no menu)")
    args = parser.parse_args()

    configure_logging()

    from neonize.utils import log
    # neonize.utils.log calls logging.basicConfig() which resets root to INFO —
    # re-apply DEBUG after the import.
    logging.getLogger().setLevel(logging.DEBUG)
    log.setLevel(logging.DEBUG)
    from neonize.client import NewClient
    from neonize.events import MessageEv, ConnectedEv, HistorySyncEv, QREv
    from neonize.utils.enum import Presence
    import segno

    from database import init_db
    from config import CONV_DB_PATH, NEO_DB_PATH
    from whatsapp import on_history_sync, on_message, start_cleanup_thread, set_bot_running, is_bot_running
    from menu import run_menu, get_runtime, set_runtime

    if args.headless:
        from control_server import start_control_server, stop_control_server, SOCKET_PATH
        import away
        import bot_settings

    # --- Setup ---
    os.makedirs(os.path.dirname(CONV_DB_PATH), exist_ok=True)
    os.makedirs("messages", exist_ok=True)
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("converted", exist_ok=True)
    init_db()
    start_cleanup_thread()

    # --- Shared state ---
    state = {"client": None, "connected": False}
    connect_event = threading.Event()  # set when ConnectedEv fires
    pause_flag = [False]  # used by live view for pause/resume

    # --- Connect: establish WhatsApp session (login only) ---
    # Connect does NOT start the bot or enter the live view -- those are
    # separate menu steps. It only logs the bot in so the session shows as
    # "CONNECTED" and self-chat works while the bot is in standby.
    def connect_fn():
        connect_event.clear()
        client = NewClient(NEO_DB_PATH)
        state["client"] = client

        @client.event(ConnectedEv)
        def on_connected(c, ev):
            try:
                c.send_presence(presence=Presence.AVAILABLE)
            except Exception as e:
                logging.warning(f"Could not send presence yet (non-fatal): {e}")
            logging.info("Connected to WhatsApp")
            # NOTE: the bot is left in standby (is_bot_running=False) so that
            # self-chat works while connected. Starting the bot is [4] Start.
            set_bot_running(False)
            state["connected"] = True
            connect_event.set()

        @client.event(HistorySyncEv)
        def handle_history_sync(c, ev):
            on_history_sync(c, ev)

        @client.event(MessageEv)
        def handle_message(c, ev):
            on_message(c, ev)

        @client.event(QREv)
        def handle_qr(c, ev):
            logging.info("QR Code received. Scan with WhatsApp (Linked Devices).")
            if ev.Codes:
                try:
                    segno.make(ev.Codes[0]).terminal(compact=True)
                except Exception as e:
                    logging.error(f"Failed to display QR: {e}")

        # Run connect() in a daemon thread -- it blocks in the event loop
        def _run_connect():
            try:
                client.connect()
            except Exception as e:
                logging.error(f"Connection error: {e}")
                state["client"] = None
                connect_event.set()  # unblock the menu

        t = threading.Thread(target=_run_connect, daemon=True)
        t.start()

        # Wait for the ConnectedEv callback (up to 120s for QR scan)
        for _ in range(120):
            if connect_event.is_set():
                if not state["connected"]:
                    return False
                # Session connected -- do NOT start the bot or enter live view
                # (those are the separate [4] Start step). Keep session alive.
                return True
            from menu import check_and_reset_ctrl_c
            if check_and_reset_ctrl_c():
                logging.info("Connection aborted (Ctrl+C)")
                # Use the shared disconnect_fn to reset state consistently
                disconnect_fn()
                return False
            connect_event.wait(timeout=1)
        logging.warning("Connection timed out (120s)")
        return False

    # --- Disconnect: stop bot + session disconnect ---
    def disconnect_fn():
        set_bot_running(False)
        logging.info("Bot stopped (standby)")
        if state["client"]:
            def _do_disconnect():
                try:
                    state["client"].disconnect()
                except Exception as e:
                    logging.warning(f"Disconnect error: {e}")
            t = threading.Thread(target=_do_disconnect, daemon=True)
            t.start()
        state["client"] = None
        state["connected"] = False
        logging.info("Disconnected from WhatsApp")

    # --- Start the bot only: keep the session connected.
    # This is [4] Start, so the activates processing while the session stays live
    # (self-chat keeps working; other messages are processed too).
    def start_bot_fn():
        set_bot_running(True)
        state["connected"] = True  # session remains connected

    # --- Stop the bot only: leave the session connected.
    # This is [4] Start -> Stop, so the bot returns to standby without dropping
    # the session (self-chat keeps working).
    def stop_bot_fn():
        set_bot_running(False)
        logging.info("Bot stopped (session stays connected)")
        state["connected"] = True  # session remains connected

    # --- Headless mode: start control server, auto-connect, auto-start ---
    if args.headless:
        # Status getter for control server
        def get_status():
            import bot_settings
            st = away.status()
            return {
                "connected": state["connected"],
                "running": is_bot_running,
                "away": st["away"],
                "away_override": st["override"],
                "away_idle_seconds": st["idle_seconds"],
                "settings": bot_settings.get_settings(),
                "runtime": get_runtime(),
            }

        # Wire up callbacks for control server
        callbacks = {
            "connect": connect_fn,
            "disconnect": disconnect_fn,
            "start": start_bot_fn,
            "stop": stop_bot_fn,
            "status": get_status,
            "toggle": lambda key, value=None: bot_settings.toggle(key, value),
            "get_setting": lambda key: bot_settings.get_settings().get(key, False),
            "away": lambda state_val: (away.set_override(state_val), away.status())[1],
            "away_status": away.status,
            "model_provider": lambda p: set_runtime("provider", p),
            "model_name": lambda m: set_runtime("model", m),
            "quit": stop_bot_fn,
        }

        # Start control server
        logging.info("Starting headless service...")
        control = start_control_server(SOCKET_PATH, callbacks)

        # Install log stream handler (pushes logs to connected CLI clients)
        class HeadlessLogStream(logging.Handler):
            def emit(self, record):
                try:
                    control.broadcast_log(record)
                except Exception:
                    pass
        headless_log = HeadlessLogStream()
        headless_log.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logging.getLogger().addHandler(headless_log)

        # Auto-connect
        if connect_fn():
            logging.info("Connected (headless)")
            # Auto-start bot
            start_bot_fn()
            logging.info("Bot started (headless)")
            # Wait forever (until killed)
            while True:
                time.sleep(60)
        else:
            logging.warning("Connection failed (headless) — waiting for reconnect")

        # Shutdown
        control.shutdown()
    else:
        # --- Run the menu loop ---
        try:
            run_menu(connect_fn, start_bot_fn, disconnect_fn, stop_bot_fn)
        except KeyboardInterrupt:
            logging.info("Interrupted.")
        finally:
            disconnect_fn()
            logging.info("Exited.")
    # Hard exit -- the neonize event loop thread may not fully stop
    os._exit(0)

if __name__ == "__main__":
    main()
