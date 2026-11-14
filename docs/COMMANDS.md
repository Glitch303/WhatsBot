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
!renamebot <new_name> - Change the bot's name
!new - Start a new session (previous one stays saved, resumable)
!title [name] - Set/show the title of the current session
!sessions [search] - List sessions for this chat (optionally search)
!resume <n|name|id> - Resume a previous session
!delete <n|name|id> - Delete a session
!memory - Show your session/memory stats
!facts - List stored facts about you
!forget <ID> - Delete a specific fact by ID
!stats - Show full memory/stats overview
!reset - Delete all sessions for this chat (fresh start)
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

### `!renamebot <new_name>`

Change the bot's display name (sent to the current chat).

```
!renamebot Yuki
```

---

### `!new`

End the current session and start a fresh one. The previous session is kept in the ledger (resumable with `!resume`).

```
!new
```

**Response:**
```
[COMMAND] New session started. Previous session saved (use !sessions to see it).
```

---

### `!title [name]`

Set the title of the current session, or show it when no name is given.

```
!title planning the trip
!title
```

**Response:**
```
[COMMAND] Session title set to: planning the trip
```
or
```
[COMMAND] Current session title: (untitled)
```

---

### `!sessions [search]`

List the session ledger for this chat (newest first, up to 15). Optionally filter by title/search text.

```
!sessions
!sessions trip
```

**Response:**
```
[COMMAND] Sessions for this chat:
1. s-3f2a1b9c8d7e (untitled) (12 msgs, 2026-11-14) — "halo, mau tanya soal..." [current]
2. s-9c8d7e6f5a4b planning the trip (34 msgs, 2026-11-10) — "oke, jadi kita mau ke..."

Resume with: !resume <number|name|id>
```

**Errors:**
```
[COMMAND] No sessions found.
[COMMAND] No sessions match "xyz".
```

---

### `!resume <n|name|id>`

Resume a previous session for this chat. Accepts the list number from `!sessions`, a title (exact or partial), or a session ID (full or `s-` prefix).

```
!resume 2
!resume trip
!resume s-9c8d7e6f5a4b
```

**Response:**
```
[COMMAND] Resumed session: planning the trip (s-9c8d7e6f5a4b, 34 msgs)
```

**Errors:**
```
[COMMAND] Provide a session number, name, or ID. Use !sessions to see them.
[COMMAND] No session matches "xyz".
```

> `!resume` with **no argument** resumes the bot (see below).

---

### `!delete <n|name|id>`

Delete a session (same reference forms as `!resume`). If it's the current one, a fresh replacement session is started.

```
!delete 2
```

**Response:**
```
[COMMAND] Deleted session: planning the trip (s-9c8d7e6f5a4b)
```

**Errors:**
```
[COMMAND] Provide a session number, name, or ID. Use !sessions to see them.
[COMMAND] No session matches "xyz".
```

---

### `!memory`

Show session/memory stats for the sender's current session.

```
!memory
```

**Response:**
```
[COMMAND] Session stats for 1234567890:
- Session: s-3f2a1b9c8d7e (12 msgs)
- Started: 2026-11-14 08:00
- Last activity: 2 min ago
- Summary: 0 chars
- Facts stored: 3
```

---

### `!facts`

List the most recently confirmed facts stored for the sender.

```
!facts
```

**Response:**
```
[COMMAND] Stored facts for 1234567890:
[12] (user) user's name is ninn
[11] (user) user lives in Jakarta
[9] (john) john is a software engineer
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
[COMMAND] Stats:
- Total sessions: 9 (2 named)
- Total messages: 153
- Facts stored: 3
- Current session: s-3f2a1b9c8d7e (12 msgs)
```

---

### `!reset`

Delete **all** sessions for this chat and start a fresh one.

```
!reset
```

**Response:**
```
[COMMAND] All sessions for this chat deleted (3 sessions). Fresh start.
```

> Facts are preserved (they are per-user, not per-session).

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

Resume the bot (no argument). With an argument, resumes a session (see above).

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
- group mention: off (ON = shared group session, speaks on @mention)
- dm:            ON
- self chat:     ON

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
- group mention: off (ON = shared group session, speaks on @mention)
- dm:            ON
- self chat:     ON

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
| `5` | Sessions (ledger overview) |
| `6` | Quit |
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

### Sessions view

Read-only overview of the session ledger: totals (sessions, named, messages) plus the current session per chat (key, ID, title, message count, state, last activity). Press Enter to return to the menu.

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
4. `!commands` — 2-5s random delay
5. `!renamebot` — 2-5s random delay
6. `!new` — 2-5s random delay
7. `!title` — 2-5s random delay
8. `!sessions` — 2-5s random delay
9. `!resume` — 2-5s random delay
10. `!delete` — 2-5s random delay
11. `!memory` — 2-5s random delay
12. `!facts` — 2-5s random delay
13. `!forget` — 2-5s random delay
14. `!stats` — 2-5s random delay
15. `!reset` — 2-5s random delay
16. `!pause` — 2-5s random delay
17. `!permanentstop` — 2-5s random delay
18. Any other `!...` — 2-5s random delay + "Unknown command"

> The random delay makes command responses feel less robotic. Reply-toggle commands (`!replies`, `!status`) skip the delay since they're control commands.
