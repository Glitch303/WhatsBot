# Commands

Complete reference for all WhatsApp admin commands and terminal/menu commands.

---

## WhatsApp Commands

All commands must be sent from a number in `ADMIN_NUMBERS` (international format, no `+`).

### `!commands`

List all available commands.

```
!commands
```

**Response:**
```
[COMMAND] Available commands:
!commands - Show available commands
!files - List files in the downloads folder
!removefile <ID or filename> - Remove a file from the downloads folder
!prompts - Show available prompts
!editprompt <prompt_name> <new_content> - Edit a prompt
!renamebot <new_name> - Change the bot's name
!memory - Show your session/memory stats
!facts - List stored facts about you
!forget <ID> - Delete a specific fact by ID
!stats - Show full memory/stats overview
!reset - Clear your conversation history
!pause - Pause the bot
!resume - Resume the bot
!permanentstop - Stop the bot permanently
```

---

### `!files`

List all files in the `downloads/` folder.

```
!files
```

**Response:**
```
[COMMAND] Files in the folder:
1. report.pdf
2. notes.txt
```

Or:
```
[COMMAND] No files.
```

---

### `!removefile <ID or filename>`

Remove a file from `downloads/` and its converted `.txt` from `converted/`.

```
!removefile 1
!removefile report.pdf
```

**Response:**
```
[COMMAND] File removed: report.pdf
```

**Errors:**
```
[COMMAND] Provide the ID number or name of the file to remove.
[COMMAND] Invalid ID number: 99
[COMMAND] File not found: missing.txt
```

---

### `!prompts`

Show the current editable (public) prompts.

```
!prompts
```

**Response:**
```
[COMMAND] Public prompts (editable):

greeting:
Hi! I'm here.

final_response:
Say farewell to "USER_NAME_HERE".
```

> Private prompts (personality, watchdog, summarization, fact extraction) are not editable at runtime.

---

### `!editprompt <name> <new_content>`

Edit a public prompt. The change is immediate (applies to the next response). Resets on restart.

```
!editprompt greeting Hello! How are you, USER_NAME_HERE?
!editprompt final_response Goodbye, USER_NAME_HERE! See you soon.
```

**Response:**
```
[COMMAND] Public prompt greeting updated.
```

**Errors:**
```
[COMMAND] Provide the prompt name and new content.
[COMMAND] Unknown prompt name: foo
```

**Valid prompt names:** `greeting`, `final_response`

---

### `!renamebot <new_name>`

Change the bot's display name (cosmetic only — updates the log, doesn't change the WhatsApp profile name).

```
!renamebot Yuki
```

**Response:**
```
[COMMAND] Bot name updated: Yuki.
```

---

### `!memory`

Show session/memory statistics for the sender.

```
!memory
```

**Response:**
```
[COMMAND] Memory stats for 6287711076107:
- Total messages: 42
- Current session: 15 min
- Last message: 2 min ago
- New session: no
- Est. storage: 3 KB
```

---

### `!facts`

List the most recently confirmed facts stored for the sender.

```
!facts
```

**Response:**
```
[COMMAND] Stored facts for 6287711076107:
[12] (user) user's name is ninn
[11] (user) user lives in Jakarta
[9] (syahid) syahid is a software engineer
```

Or:
```
[COMMAND] No facts stored yet.
```

---

### `!forget <ID>`

Delete a specific fact by ID (from `!facts`).

```
!forget 12
```

**Response:**
```
[COMMAND] Fact [12] deleted.
```

**Errors:**
```
[COMMAND] Provide the fact ID to delete. Use !facts to see IDs.
[COMMAND] Invalid ID: abc
[COMMAND] Fact [999] not found.
```

---

### `!stats`

Show a full memory/stats overview.

```
!stats
```

**Response:**
```
[COMMAND] Stats for 6287711076107:
- Messages: 42
- Facts stored: 3
- Summary: 847 chars
- Last message: 2 min ago
- Est. storage: 3 KB
```

---

### `!reset`

Clear all conversation history for the sender's number.

```
!reset
```

**Response:**
```
[COMMAND] Conversation history cleared!
```

> This deletes from the `messages` table only. Facts and summaries are preserved.

---

### `!pause`

Pause the bot. Self-chat still works; other messages are skipped.

```
!pause
```

**Response:**
```
[COMMAND] Bot is now paused!
```

---

### `!resume`

Resume the bot.

```
!resume
```

**Response:**
```
[COMMAND] Bot has resumed!
```

---

### `!permanentstop`

Stop the bot permanently (hard exit, `os._exit(0)`).

```
!permanentstop
```

**Response:**
```
[COMMAND] Bot is shutting down permanently!
```

> The process terminates immediately. The WhatsApp session is dropped.

---

### `!replies <key> <on|off>`

Toggle reply behavior at runtime.

```
!replies dm on
!replies dm off
!replies self_chat on
!replies self_chat off
!replies group_mention on
!replies group_mention off
```

**Response:**
```
✓ dm = on

Bot reply settings:
- group mention: off
- dm:            ON
- self chat:     ON
- group (always): OFF (hardcoded)

Toggle: !replies <group_mention|dm|self_chat> <on|off>
```

**Key aliases:** `self_chat`, `self-chat`, `selfchat` all work.

**Errors:**
```
✗ Unknown setting: foo
✗ Invalid value: maybe. Use 'on' or 'off'.
```

---

### `!replies` (no args)

Show current reply settings (same as `!status`).

```
!replies
```

---

### `!status`

Show current reply settings.

```
!status
```

**Response:**
```
Bot reply settings:
- group mention: off
- dm:            ON
- self chat:     ON
- group (always): OFF (hardcoded)

Toggle: !replies <group_mention|dm|self_chat> <on|off>
```

---

### Unknown commands

```
!foo
```

**Response:**
```
[COMMAND] Unknown command. Use !commands to see available commands.
```

---

## Terminal / Menu Commands

### Menu (standby)

| Input | Action |
|-------|--------|
| `1` | Select model |
| `2` | Settings |
| `3` | Connect / Disconnect session |
| `4` | Start bot |
| `5` | Quit |
| `q` / `quit` / `exit` | Quit |
| `Ctrl+C` | Return to menu (from live view) |

### Model picker

| Input | Action |
|-------|--------|
| `1`–`4` | Select provider (openai, azure, openrouter, llamacpp) |
| `Enter` | Keep current provider |
| `q` / `quit` | Cancel |

After selecting a provider, you're prompted for model details (varies by provider). For `llamacpp`, a model list is fetched from the server.

### Settings submenu

| Input | Action |
|-------|--------|
| `1` | Toggle DM replies |
| `2` | Toggle group @mention |
| `3` | Toggle self-chat |
| `4` | Back to main menu |

### Live view (when bot is running)

| Input | Action |
|-------|--------|
| `pause` | Pause the bot (self-chat still works) |
| `resume` | Resume the bot |
| `status` | Show reply settings |
| `replies <key> <on\|off>` | Toggle reply behavior |
| `stop` | Return to menu (bot stops, session stays connected) |
| `quit` | Exit the program |
| `Ctrl+C` | Print hint (press `stop` to return to menu) |

---

## Command Processing Order

Commands are checked in this order inside `handle_commands()`:

1. `!replies` / `!toggle` / `!status` — **no delay** (control commands)
2. `!files` — 2-5s random delay
3. `!removefile` — 2-5s random delay
4. `!commands` / `!komennot` — 2-5s random delay
5. `!prompts` — 2-5s random delay
6. `!editprompt` — 2-5s random delay
7. `!renamebot` — 2-5s random delay
8. `!memory` — 2-5s random delay
9. `!facts` — 2-5s random delay
10. `!forget` — 2-5s random delay
11. `!stats` — 2-5s random delay
12. `!reset` — 2-5s random delay
13. `!pause` — 2-5s random delay
14. `!resume` — 2-5s random delay
15. `!permanentstop` — 2-5s random delay
16. Any other `!...` — 2-5s random delay + "Unknown command"

> The random delay makes command responses feel less robotic. Reply-toggle commands (`!replies`, `!status`) skip the delay since they're control commands.
