"""
Runtime bot settings that can be toggled at runtime via:
  - the bot's own self-chat (e.g. "!replies dm on")
  - the terminal (e.g. "replies dm on" / "status")

Group replies are always disabled (hardcoded, not toggleable).
"""
import os
import threading

_lock = threading.Lock()

# Defaults: group mention OFF, DM OFF, self-chat ON
_bot_settings = {
    "group_mention": False,  # reply to @mentions in groups
    "dm": False,             # reply to direct messages
    "self_chat": True,       # reply in the bot's own self-chat
}


def get_settings():
    """Return a copy of the current settings."""
    with _lock:
        return dict(_bot_settings)


def toggle(key, value):
    """Set a setting. Returns (success, message)."""
    with _lock:
        if key not in _bot_settings:
            return False, f"Unknown setting: {key}"
        if isinstance(value, bool):
            _bot_settings[key] = value
            return True, f"{key} = {'on' if value else 'off'}"
        return False, f"Invalid value: {value}"


def status_text():
    s = get_settings()
    return (
        "Bot reply settings:\n"
        f"- group mention: {'ON' if s['group_mention'] else 'off'}\n"
        f"- dm:            {'ON' if s['dm'] else 'off'}\n"
        f"- self chat:     {'ON' if s['self_chat'] else 'off'}\n"
        "- group (always): OFF (hardcoded)\n\n"
        "Toggle: !replies <group_mention|dm|self_chat> <on|off>"
    )


if __name__ == "__main__":
    print(status_text())
