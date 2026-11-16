#!/usr/bin/env python3
"""
Attachable TUI for WhatsBot — talks to the running headless service over
the Unix socket. Provides the same interactive menu as the old in-process
menu, but can be attached to and detached from freely.

Usage:
    whatsbot menu
    python tui.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from control_server import send_command, SOCKET_PATH
import menu


def get_status():
    result = send_command(SOCKET_PATH, "command", "status", {})
    return result.get("data", {})


def cmd_connect():
    result = send_command(SOCKET_PATH, "command", "connect", {})
    return result


def cmd_disconnect():
    result = send_command(SOCKET_PATH, "command", "disconnect", {})
    return result


def cmd_start():
    result = send_command(SOCKET_PATH, "command", "start", {})
    return result


def cmd_stop():
    result = send_command(SOCKET_PATH, "command", "stop", {})
    return result


def cmd_toggle(setting):
    result = send_command(SOCKET_PATH, "command", "toggle", {"setting": setting})
    return result


def cmd_away(state):
    result = send_command(SOCKET_PATH, "command", "away", {"state": state})
    return result


def cmd_model_provider(provider):
    result = send_command(SOCKET_PATH, "command", "model_provider", {"provider": provider})
    return result


def cmd_model_name(name):
    result = send_command(SOCKET_PATH, "command", "model_name", {"name": name})
    return result


def run():
    """Run the interactive TUI menu."""
    menu.install_ctrl_c_handler()
    menu.install_log_capture()

    while True:
        status = get_status()
        connected = status.get("connected", False)
        running = status.get("running", False)
        runtime = status.get("runtime", {})

        menu.clear()
        menu.print_banner()

        items = menu.print_standby_menu(runtime.get("provider"), connected)
        choice = menu.prompt_choice(len(items))

        if choice is None:
            break

        label = next(i[1] for i in items if i[0] == str(choice))

        if label.startswith("Select model"):
            menu.print_model_menu()
            provider = menu.prompt_model_choice()
            if provider:
                cmd_model_provider(provider)
            menu.clear()

        elif label.startswith("Settings"):
            menu.handle_settings_menu()
            menu.clear()

        elif label.startswith("Connect"):
            result = cmd_connect()
            if result.get("ok"):
                print(menu.c(menu.C.GREEN, "  Connected"))
            else:
                print(menu.c(menu.C.RED, f"  Connect failed: {result.get('error')}"))
            input("  Press Enter to continue...")
            menu.clear()

        elif label.startswith("Disconnect"):
            result = cmd_disconnect()
            if result.get("ok"):
                print(menu.c(menu.C.GREEN, "  Disconnected"))
            input("  Press Enter to continue...")
            menu.clear()

        elif label.startswith("Start"):
            result = cmd_start()
            if result.get("ok"):
                print(menu.c(menu.C.GREEN, "  Bot started"))
            else:
                print(menu.c(menu.C.RED, f"  Start failed: {result.get('error')}"))
            input("  Press Enter to continue...")
            menu.clear()

        elif label.startswith("Stop"):
            result = cmd_stop()
            if result.get("ok"):
                print(menu.c(menu.C.GREEN, "  Bot stopped"))
            input("  Press Enter to continue...")
            menu.clear()

        elif label.startswith("Quit"):
            break

        elif label.startswith("Sessions"):
            menu.print_sessions_menu()
            input("  Press Enter to continue...")
            menu.clear()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
    finally:
        menu.uninstall_log_capture()