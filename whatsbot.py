#!/usr/bin/env python3
"""
WhatsBot CLI — control the headless service.

Usage:
    whatsbot status           Show bot status
    whatsbot logs             Live log view (Ctrl+C to stop)
    whatsbot start            Start the bot
    whatsbot stop             Stop the bot
    whatsbot connect          Connect (show QR if needed)
    whatsbot disconnect       Disconnect
    whatsbot quit             Quit the service
    whatsbot toggle dm        Toggle DM replies (on/off)
    whatsbot toggle group     Toggle group mentions
    whatsbot toggle self      Toggle self-chat
    whatsbot away auto|on|off Set away override
    whatsbot away-status      Show away status
    whatsbot model provider X Set LLM provider (openai/azure/openrouter/llamacpp)
    whatsbot model name X     Set LLM model
"""

import argparse
import json
import sys
import time

import control_server


SOCKET_PATH = control_server.SOCKET_PATH


def cmd_status():
    """Show bot status."""
    try:
        result = control_server.send_command(SOCKET_PATH, "command", "status")
        if not result["ok"]:
            print(f"Error: {result.get('error')}")
            return 1
        data = result["data"]
        print("WhatsBot Status")
        print("=" * 40)
        print(f"  Connected:  {'Yes' if data['connected'] else 'No'}")
        print(f"  Running:    {'Yes' if data['running'] else 'No'}")
        print(f"  Away:       {'Yes' if data['away'] else 'No'}")
        print(f"  Away override: {data['away_override']}")
        if data["away_idle_seconds"] >= 0:
            print(f"  Idle:         {data['away_idle_seconds']}s")
        print(f"  Settings:   {json.dumps(data['settings'])}")
        rt = data.get("runtime", {})
        print(f"  Runtime:    provider={rt.get('provider') or 'default'}, model={rt.get('model') or 'default'}")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


def cmd_logs():
    """Live log view."""
    try:
        print("WhatsBot Live Logs (Ctrl+C to stop)")
        print("=" * 40)
        for record in control_server.stream_logs(SOCKET_PATH):
            ts = time.strftime("%H:%M:%S", time.localtime(record.get("ts", time.time())))
            level = record.get("level", "INFO")
            msg = record.get("msg", "")
            if level in ("ERROR", "CRITICAL"):
                print(f"\033[91m{ts} [{level}] {msg}\033[0m")
            elif level == "WARNING":
                print(f"\033[93m{ts} [{level}] {msg}\033[0m")
            else:
                print(f"{ts} [{level}] {msg}")
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


def cmd_command(cmd, args=None):
    """Send a one-off command."""
    try:
        result = control_server.send_command(SOCKET_PATH, "command", cmd, args)
        if not result["ok"]:
            print(f"Error: {result.get('error')}")
            return 1
        if result.get("data") is not None:
            print(json.dumps(result["data"], indent=2))
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


def cmd_toggle(setting):
    """Toggle a setting."""
    return cmd_command("toggle", {"setting": setting})


def cmd_away(state):
    """Set away override."""
    return cmd_command("away", {"state": state})


def cmd_away_status():
    """Show away status."""
    return cmd_command("away_status")


def cmd_model_provider(provider):
    """Set LLM provider."""
    return cmd_command("model_provider", {"provider": provider})


def cmd_model_name(model):
    """Set LLM model."""
    return cmd_command("model_name", {"model": model})


def main():
    parser = argparse.ArgumentParser(description="WhatsBot CLI")
    sub = parser.add_subparsers(dest="command", help="Command")

    # Status
    sub.add_parser("status", help="Show bot status")

    # Logs
    sub.add_parser("logs", help="Live log view")

    # Start/stop
    sub.add_parser("start", help="Start the bot")
    sub.add_parser("stop", help="Stop the bot")

    # Connect/disconnect
    sub.add_parser("connect", help="Connect (show QR if needed)")
    sub.add_parser("disconnect", help="Disconnect")

    # Quit
    sub.add_parser("quit", help="Quit the service")

    # Toggle settings
    toggle_parser = sub.add_parser("toggle", help="Toggle a setting")
    toggle_parser.add_argument("setting", choices=["dm", "group", "self"],
                               help="Setting to toggle (dm/group/self)")

    # Away mode
    away_parser = sub.add_parser("away", help="Set away override")
    away_parser.add_argument("state", choices=["auto", "on", "off"],
                             help="Override state")
    sub.add_parser("away-status", help="Show away status")

    # Model
    model_parser = sub.add_parser("model", help="Set LLM model")
    model_sub = model_parser.add_subparsers(dest="model_cmd", help="Model command")
    mp_provider = model_sub.add_parser("provider", help="Set provider")
    mp_provider.add_argument("provider", help="Provider name")
    mp_name = model_sub.add_parser("name", help="Set model")
    mp_name.add_argument("model", help="Model name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "status":
        return cmd_status()
    elif args.command == "logs":
        return cmd_logs()
    elif args.command == "start":
        return cmd_command("start")
    elif args.command == "stop":
        return cmd_command("stop")
    elif args.command == "connect":
        return cmd_command("connect")
    elif args.command == "disconnect":
        return cmd_command("disconnect")
    elif args.command == "quit":
        return cmd_command("quit")
    elif args.command == "toggle":
        setting_map = {"dm": "dm", "group": "group_mention", "self": "self_chat"}
        return cmd_toggle(setting_map[args.setting])
    elif args.command == "away":
        state_map = {"auto": "auto", "on": "away", "off": "present"}
        return cmd_away(state_map[args.state])
    elif args.command == "away-status":
        return cmd_away_status()
    elif args.command == "model":
        if args.model_cmd == "provider":
            return cmd_model_provider(args.provider)
        elif args.model_cmd == "name":
            return cmd_model_name(args.model)
        else:
            model_parser.print_help()
            return 1
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
