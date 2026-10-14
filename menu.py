"""
Standby menu for WhatsBot.

Flow:
   1. Select model (openai / azure / openrouter / llamacpp)
   2. Settings (toggle dm, group_mention, self_chat, view context/memory)
   3. Connect to session (QR scan) -- establishes the WhatsApp login so the
      session shows "CONNECTED". Does NOT start the bot; the bot stays in
      standby so self-chat works.
   4. Start  -> switches to live log view (if connected; prompts to connect if not)
   5. Quit

After "Start", the terminal shows live logs. Type commands:
   pause | resume | replies <key> <on|off> | status | stop
"stop" returns to the standby menu (bot stays connected).
"""
import os
import sys
import signal
import threading
import time
from queue import Queue, Empty

# --- Ctrl+C flag: set by signal handler, checked by menu loop ---
_ctrl_c = [False]

def _ctrl_c_handler(signum, frame):
    """Set flag instead of raising."""
    _ctrl_c[0] = True
    print("\n")  # newline so the prompt doesn't get mangled
    time.sleep(0.1)

def install_ctrl_c_handler():
    signal.signal(signal.SIGINT, _ctrl_c_handler)

def check_and_reset_ctrl_c():
    """Returns True if Ctrl+C was pressed since last check."""
    if _ctrl_c[0]:
        _ctrl_c[0] = False
        return True
    return False

def _drain_stdin():
    """Flush any leftover characters from stdin so the next input() starts clean."""
    try:
        if not sys.stdin.isatty():
            return
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def _heal_terminal():
    """Verify and repair terminal state after input() calls."""
    if os.name != "posix":
        return
    try:
        fd = sys.stdin.fileno()
        if not sys.stdin.isatty():
            return
        import termios, fcntl, os as _os
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if flags & _os.O_NONBLOCK:
            os.set_blocking(fd, True)
        attrs = termios.tcgetattr(fd)
        lflag = attrs[3]
        if not (lflag & termios.ICANON):
            attrs[3] = lflag | termios.ICANON | termios.ECHO
            attrs[0] = attrs[0] | termios.IXON | termios.IXOFF | termios.ICRNL
            attrs[6][termios.VMIN] = 1
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        pass

# --- ANSI colors ---
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"

def _supports_color():
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return os.name == "posix"

_COLOR = _supports_color()

def c(color, text):
    if _COLOR:
        return f"{color}{text}{C.RESET}"
    return text

# --- Runtime overrides ---
_runtime = {
    "provider": None,
    "model": None,
    "base_url": None,
}

def get_runtime():
    return dict(_runtime)

def set_runtime(key, value):
    _runtime[key] = value

# --- Log capture: mirror all records to _log_queue ---
_log_queue = Queue()
_capture_handler = None

def install_log_capture():
    """Install a logging.Handler that mirrors all records to _log_queue."""
    import logging
    global _capture_handler
    if _capture_handler is not None:
        return
    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                _log_queue.put_nowait(msg)
            except Exception:
                pass
    _capture_handler = _CaptureHandler()
    _capture_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(_capture_handler)

def uninstall_log_capture():
    import logging
    global _capture_handler
    if _capture_handler is not None:
        logging.getLogger().removeHandler(_capture_handler)
        _capture_handler = None

# --- Menu rendering ---
def clear():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def print_banner():
    print()
    print(c(C.CYAN, "\n"))
    print(c(C.CYAN, "  +======================================+"))
    print(c(C.CYAN, "  |      WhatsBot  --  AI Assistant      |"))
    print(c(C.CYAN, "  +======================================+"))
    print()

def print_standby_menu(provider, connected):
    print()
    print(c(C.BOLD, "  +----------- WHATSBOT -----------+"))
    print(c(C.BOLD, "  |                                |"))

    # Model status
    from config import LLM_PROVIDER, LLAMACPP_BASE_URL
    from llm import get_client_and_model
    try:
        _, model = get_client_and_model()
        model_str = f"{provider or LLM_PROVIDER} / {model}"
        active_prov = provider or LLM_PROVIDER
        if active_prov == "llamacpp":
            models = fetch_llamacpp_models(LLAMACPP_BASE_URL)
            if models:
                for m in models:
                    if m["id"] == model and m["status"] == "loaded":
                        model_str += " " + c(C.GREEN, "(loaded)")
                        break
                else:
                    model_str += " " + c(C.YELLOW, "(not loaded)")
    except Exception as e:
        model_str = c(C.RED, f"error: {e}")
    print(f"  |  Model:     {model_str}")

    # Feature toggles
    from bot_settings import get_settings
    s = get_settings()
    print(f"  |  DM:        {'ON' if s['dm'] else 'off'}")
    print(f"  |  Group @:   {'ON' if s['group_mention'] else 'off'}")
    print(f"  |  Self-chat: {'ON' if s['self_chat'] else 'off'}")

    # Connection status -- prominently shown in standby menu
    conn = c(C.GREEN, "CONNECTED") if connected else c(C.YELLOW, "not connected")
    print(f"  |  Session:   {conn}")

    print(c(C.BOLD, "  |                                   |"))
    print(c(C.BOLD, "  +-----------------------------------+"))
    print()

    # Menu items (flat -- no separate Start/Stop)
    items = []
    items.append(("1", "Select model"))
    items.append(("2", "Settings"))
    if connected:
        items.append(("3", "Disconnect session (stop bot)"))
    else:
        items.append(("3", "Connect to session"))
    if connected:
        items.append(("4", "Start bot (connected)"))
    else:
        items.append(("4", "Start bot"))
    items.append(("5", "Quit"))

    for num, label in items:
        print(f"    [{num}] {label}")
    print()
    return items

def prompt_choice(max_num):
    """Read a menu choice. Returns int, or None on quit."""
    # Wait briefly for any pending input from the live view's input thread
    for _ in range(3):
        if _pending_input:
            break
        _pending_ready.wait(timeout=0)
        _pending_ready.clear()
    while True:
        # Check for pending input first
        if _pending_input:
            raw = _pending_input.pop(0)
            if raw.lower() in ("q", "quit", "exit"):
                return None
            if raw.isdigit() and 1 <= int(raw) <= max_num:
                return int(raw)
            # Invalid pending input -- fall through to normal input
        # Check for Ctrl+C -- return None to quit the menu
        if check_and_reset_ctrl_c():
            print(c(C.DIM, "\n  [Ctrl+C] Returning to menu..."))
            _heal_terminal()
            continue  # Re-display menu
        try:
            raw = input(c(C.BOLD, "  > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            _heal_terminal()
            return None
        # Heal terminal state after every successful input()
        _heal_terminal()
        if not raw:
            continue
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= max_num:
                return n
            print(c(C.RED, f"  Enter 1-{max_num}."))
        else:
            print(c(C.RED, f"  Enter a number (1-{max_num}) or 'quit'."))

def print_model_menu():
    from config import LLM_PROVIDER
    providers = ["openai", "azure", "openrouter", "llamacpp"]
    print()
    print(c(C.BOLD, "  Select LLM provider:"))
    for i, p in enumerate(providers, 1):
        marker = c(C.GREEN, "*") if p == (get_runtime()["provider"] or LLM_PROVIDER) else " "
        print(f"    [{i}] {p} {marker}")
    print()

def fetch_llamacpp_models(base_url):
    """Fetch available models from a llama.cpp server. Returns list of dicts."""
    import urllib.request
    import json
    try:
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        url += "/models"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        models = []
        for m in data.get("data", []):
            status = m.get("status", {}).get("value", "unknown")
            models.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "status": status,  # "loaded" or "unloaded"
            })
        return models
    except Exception as e:
        return None

def print_llamacpp_model_list(models, current_model):
    """Display the llama.cpp model list with global numbering and load status."""
    print()
    print(c(C.BOLD, "  Available models:"))
    for i, m in enumerate(models, 1):
        if m["status"] == "loaded":
            tag = c(C.GREEN, "[loaded]")
        else:
            tag = c(C.DIM, "[not loaded]")
        num = c(C.GREEN, f"[{i}]") if m["id"] == current_model else f"[{i}]"
        dim_id = c(C.DIM, "(" + m["id"] + ")")
        print(f"    {num} {m['name']}  {dim_id}  {tag}")
    print()

def prompt_llamacpp_model(models, default_model):
    """Interactive model picker for llama.cpp. Returns model ID or None."""
    if not models:
        # Server unreachable -- fall back to manual entry
        model = input(c(C.CYAN, "  Model name: ") or default_model).strip()
        _heal_terminal()
        return model

    current = get_runtime().get("model") or default_model
    print_llamacpp_model_list(models, current)

    # Build numbered list
    numbered = []
    for i, m in enumerate(models, 1):
        numbered.append((i, m))

    while True:
        raw = input(c(C.BOLD, "  Select model (1-" + str(len(numbered)) + ", Enter=current): ")).strip()
        _heal_terminal()
        if not raw:
            return current
        if raw.lower() in ("q", "quit"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(numbered):
            idx = int(raw) - 1
            m = models[idx]
            if m["status"] != "loaded":
                print(c(C.YELLOW, f"  Note: {m['name']} is not loaded. llama-swap will load it on first request."))
            return m["id"]
        match = [m for m in models if m["id"].lower() == raw.lower()]
        if match:
            return match[0]["id"]
        print(c(C.RED, f"  Enter 1-{len(numbered)} or a model ID."))

def prompt_model_choice():
    from config import LLM_PROVIDER, LLAMACPP_BASE_URL, LLAMACPP_MODEL
    providers = ["openai", "azure", "openrouter", "llamacpp"]
    while True:
        raw = input(c(C.BOLD, "  Provider (1-4, Enter=current): ")).strip()
        _heal_terminal()
        if not raw:
            return
        if raw.lower() in ("q", "quit"):
            return
        if raw.isdigit() and 1 <= int(raw) <= 4:
            choice = providers[int(raw) - 1]
            set_runtime("provider", choice)

            if choice == "openai":
                model = input(c(C.CYAN, "  Model (default gpt-4o-mini): ") or "gpt-4o-mini").strip()
                _heal_terminal()
                set_runtime("model", model)
                set_runtime("base_url", None)
            elif choice == "azure":
                model = input(c(C.CYAN, "  Deployment name: ")).strip()
                endpoint = input(c(C.CYAN, "  Endpoint: ")).strip()
                _heal_terminal()
                key = input(c(C.CYAN, "  Subscription key: ")).strip()
                _heal_terminal()
                set_runtime("model", model)
                set_runtime("base_url", endpoint)
                os.environ["AZURE_DEPLOYMENT_NAME"] = model
                os.environ["AZURE_ENDPOINT"] = endpoint
                os.environ["AZURE_SUBSCRIPTION_KEY"] = key
            elif choice == "openrouter":
                model = input(c(C.CYAN, "  Model (default openai/gpt-4o-mini): ") or "openai/gpt-4o-mini").strip()
                _heal_terminal()
                set_runtime("model", model)
                set_runtime("base_url", None)
            elif choice == "llamacpp":
                url = input(c(C.CYAN, "  Server URL (default http://localhost:8080/v1): ") or LLAMACPP_BASE_URL).strip()
                _heal_terminal()
                set_runtime("base_url", url)

                print(c(C.DIM, "  Fetching models..."))
                models = fetch_llamacpp_models(url)
                if models is None:
                    print(c(C.YELLOW, "  Could not reach server. Entering model name manually."))
                    model = input(c(C.CYAN, "  Model name: ") or LLAMACPP_MODEL).strip()
                    _heal_terminal()
                    set_runtime("model", model)
                else:
                    model = prompt_llamacpp_model(models, LLAMACPP_MODEL)
                    if model is None:
                        return
                    set_runtime("model", model)
            print(c(C.GREEN, f"\n  Model set to: {choice}"))
            return
        else:
            print(c(C.RED, "  Enter 1-4."))

def print_settings_menu():
    from bot_settings import get_settings
    from config import (
        VERBATIM_TURNS, CONTEXT_CHAR_BUDGET, MAX_FACTS_IN_CONTEXT,
        SESSION_GAP_SECONDS, MEMORY_CUTOFF_DAYS, MAX_REPLY_TOKENS,
    )
    s = get_settings()
    print()
    print(c(C.BOLD, "  Settings:"))
    print(f"    DM replies:       {'ON' if s['dm'] else 'off'}")
    print(f"    Group @mention:   {'ON' if s['group_mention'] else 'off'}")
    print(f"    Self-chat:        {'ON' if s['self_chat'] else 'off'}")
    print()
    print(c(C.DIM, f"  Context: {VERBATIM_TURNS} verbatim turns, {CONTEXT_CHAR_BUDGET} char budget"))
    print(c(C.DIM, f"  Facts: max {MAX_FACTS_IN_CONTEXT} in context, prune after {MEMORY_CUTOFF_DAYS}d"))
    print(c(C.DIM, f"  Session gap: {SESSION_GAP_SECONDS}s, reply cap: {MAX_REPLY_TOKENS} tokens"))
    print()

def toggle_setting(key):
    from bot_settings import get_settings, toggle
    s = get_settings()
    new_val = not s[key]
    ok, msg = toggle(key, new_val)
    if ok:
        print(c(C.GREEN, f"  {msg}"))
    else:
        print(c(C.RED, f"  {msg}"))

def print_settings_submenu():
    print(c(C.BOLD, "  1. Toggle DM replies"))
    print(c(C.BOLD, "  2. Toggle group @mention"))
    print(c(C.BOLD, "  3. Toggle self-chat"))
    print(c(C.BOLD, "  4. Back"))

def handle_settings_menu():
    while True:
        print_settings_menu()
        print_settings_submenu()
        choice = prompt_choice(4)
        if choice is None:
            return
        if choice == 1:
            toggle_setting("dm")
        elif choice == 2:
            toggle_setting("group_mention")
        elif choice == 3:
            toggle_setting("self_chat")
        elif choice == 4:
            return

# --- Live log view ---
_live_running = False

def _log_consumer():
    """Background thread: drain _log_queue and print to stdout."""
    while _live_running:
        try:
            msg = _log_queue.get(timeout=0.2)
            if "ERROR" in msg or "CRITICAL" in msg:
                print(c(C.RED, msg))
            elif "WARNING" in msg:
                print(c(C.YELLOW, msg))
            else:
                print(msg)
            sys.stdout.flush()
        except Empty:
            pass

# Shared buffer
_pending_input = []
_pending_ready = threading.Event()

def _read_stdin_line(timeout):
    """Read one line from stdin with a timeout, using select()."""
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            line = sys.stdin.readline()
            if not line:
                return ""  # EOF
            return line.rstrip("\n")
        return None  # timeout
    except (OSError, ValueError):
        import fcntl
        fd = sys.stdin.fileno()
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            os.set_blocking(fd, False)
            time.sleep(timeout)
            try:
                line = sys.stdin.readline()
            except (BlockingIOError, OSError):
                line = ""
            if not line:
                return ""
            return line.rstrip("\n")
        finally:
            os.set_blocking(fd, True)

def run_live_view(pause_flag):
    """
    Run the live log view. Blocks until 'stop' or 'quit' is typed.
    Returns "stop" or "quit".

    pause_flag: mutable list [bool] shared with the caller -- while True,
    messages are processed in standby mode (self-chat only).

    The MAIN thread is the sole reader of stdin (via select()).
    """
    global _live_running
    _live_running = True
    install_log_capture()

    log_thread = threading.Thread(target=_log_consumer, daemon=True)
    log_thread.start()

    print()
    print(c(C.GREEN + C.BOLD, "  Bot is RUNNING. Live logs below."))
    print(c(C.DIM, "  Commands: pause | resume | status | replies <key> <on|off> | stop | quit"))
    print(c(C.DIM, "  " + "-" * 50))
    sys.stdout.flush()

    paused = pause_flag[0]
    result = "stop"

    while _live_running:
        if check_and_reset_ctrl_c():
            print(c(C.DIM, "\n  [Ctrl+C] Press 'stop' to return to menu."))

        cmd = _read_stdin_line(timeout=0.1)
        if cmd is None:
            continue
        if cmd == "":
            break
        cmd = cmd.strip()
        if not cmd:
            continue
        cmd_lower = cmd.lower()

        if cmd_lower == "stop":
            print(c(C.CYAN, "\n  Stopping..."))
            _live_running = False
            result = "stop"
            break
        elif cmd_lower == "quit":
            print(c(C.CYAN, "\n  Quitting..."))
            _live_running = False
            result = "quit"
            break
        elif cmd_lower == "pause":
            if not paused:
                paused = True
                pause_flag[0] = True
                print(c(C.YELLOW, "\n  [PAUSED]"))
            else:
                print(c(C.DIM, "\n  Already paused."))
        elif cmd_lower == "resume":
            if paused:
                paused = False
                pause_flag[0] = False
                print(c(C.GREEN, "\n  [RESUMED]"))
            else:
                print(c(C.DIM, "\n  Not paused."))
        elif cmd_lower == "status":
            from bot_settings import status_text
            print(c(C.CYAN, "\n" + status_text()))
        elif cmd_lower.startswith("replies "):
            parts = cmd_lower.split()
            if len(parts) == 3:
                from bot_settings import toggle
                val = parts[2] == "on"
                ok, msg = toggle(parts[1], val)
                print(c(C.GREEN if ok else C.RED, f"\n  {msg}"))
            else:
                print(c(C.RED, "\n  Usage: replies <group_mention|dm|self_chat> <on|off>"))
        else:
            print(c(C.RED, f"\n  Unknown: {cmd}"))
            print(c(C.DIM, "  Commands: pause | resume | status | replies <key> <on|off> | stop | quit"))

    # Clean up -- inspired by hermes-agent's _recover_terminal_after_interrupt()
    _live_running = False
    log_thread.join(timeout=1)
    # There is NO input thread to join -- the main thread was the only stdin reader
    time.sleep(0.1)
    _heal_terminal()
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except Empty:
            break
    uninstall_log_capture()
    clear()
    sys.stdout.flush()
    return result

# --- Main menu loop ---
def run_menu(connect_fn, start_bot_fn, disconnect_fn, stop_bot_fn):
    """
    Main menu loop.

    Connect = session connect (QR scan). Establishes the WhatsApp login so the
    session shows "CONNECTED". Does NOT start the bot -- the bot stays in standby
    so self-chat works. The session stays connected.
    Start = activates the bot and enters live log view (only if the session is
    connected; otherwise prompts to connect first). On stop/resume, the bot
    returns to standby but the session stays connected.
    Disconnect = stop bot + session disconnect (back to menu).

    Args:
      connect_fn:    callable that connects to WhatsApp (QR scan).
                     Returns True on success.
      start_bot_fn:  callable that activates the bot while the session stays
                     connected (used by the [4] Start path).
      disconnect_fn: callable that stops the bot AND disconnects the session.
      stop_bot_fn:   callable that stops the bot only, leaving the session
                     connected (used by the [4] Start -> stop path).

    Returns:
      "quit" when the user chooses to exit.
    """
    from config import LLM_PROVIDER
    from bot_settings import get_settings

    connected = False
    pause_flag = [False]  # shared with the live view for pause/resume
    install_log_capture()
    install_ctrl_c_handler()
    print_banner()

    while True:
        # Re-install log capture (run_live_view uninstalls it on exit)
        install_log_capture()
        clear()
        sys.stdout.flush()
        provider = get_runtime()["provider"]
        items = print_standby_menu(provider, connected)
        sys.stdout.flush()
        choice = prompt_choice(len(items))

        if choice is None:
            # User typed 'quit' or Ctrl-C
            if connected:
                disconnect_fn()
            print(c(C.CYAN, "\n  Goodbye!"))
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()
            return "quit"

        if choice == 1:  # Select model
            print_model_menu()
            prompt_model_choice()
        elif choice == 2:  # Settings
            handle_settings_menu()
        elif choice == 3:  # Connect / Disconnect session
            if not connected:
                print(c(C.CYAN, "\n  Connecting to WhatsApp... (scan QR when prompted)"))
                if connect_fn():
                    connected = True
                    _heal_terminal()
                    print(c(C.GREEN, "  Connected! Session is live -- self-chat is available. Bot is in standby."))
                else:
                    _heal_terminal()
                    print(c(C.YELLOW, "  Session ended."))
            else:
                print(c(C.DIM, "\n  Disconnecting session (stopping bot)..."))
                sys.stdout.flush()
                disconnect_fn()
                _heal_terminal()
                connected = False
                print(c(C.YELLOW, "  Disconnected."))
                sys.stdout.flush()
                time.sleep(0.5)
        elif choice == 4:  # Start bot
            if connected:
                # Activate the bot (session stays connected; self-chat works)
                start_bot_fn()
                print(c(C.GREEN, "  Starting bot... activating + entering live view..."))
                from menu import run_live_view
                result = run_live_view(pause_flag)
                # User stopped/quit from live view -- return to standby;
                # session STAYS connected (self-chat still works).
                stop_bot_fn()
            else:
                print(c(C.YELLOW, "  Session not connected. Choose [3] Connect first."))
        elif choice == 5:  # Quit
            if connected:
                disconnect_fn()
            print(c(C.CYAN, "\n  Goodbye!"))
            sys.stdout.write("\033[0m\n")
            sys.stdout.flush()
            return "quit"
