"""Away mode: the bot steps in as the owner's proxy when the owner is inactive.

The owner is considered "away" once they have not been active for
``AWAY_AFTER_MINUTES`` (env-adjustable). Activity is detected from the
owner's own messages (matched by ``OWNER_NUMBER`` / pushname) and from the
self-chat. The state can be overridden from the menu or ``!away``:

  auto     derive from the owner's last activity (default)
  present  force the owner to be present (away mode off)
  away     force the owner to be away (away mode on)

State is persisted in the session store's ``bot_state`` table so it survives
restarts. On the very first run (no persisted timestamp) the owner is assumed
present; on later restarts the persisted timestamp is used as-is.
"""

import time

from config import AWAY_AFTER_MINUTES, OWNER_NAME
from sessionstore import store

OVERRIDE_AUTO = "auto"
OVERRIDE_PRESENT = "present"
OVERRIDE_AWAY = "away"

_KEY_OVERRIDE = "away_override"
_KEY_LAST_ACTIVE = "owner_last_active"
_KEY_ANNOUNCED_PREFIX = "away_announced:"


def _now() -> float:
    return time.time()


def get_override() -> str:
    """Current override (auto | present | away)."""
    state = store.get_state(_KEY_OVERRIDE, OVERRIDE_AUTO)
    return state if state in (OVERRIDE_AUTO, OVERRIDE_PRESENT, OVERRIDE_AWAY) else OVERRIDE_AUTO


def set_override(state: str) -> str:
    """Set the override. Unknown values fall back to auto."""
    state = (state or "").strip().lower()
    if state not in (OVERRIDE_AUTO, OVERRIDE_PRESENT, OVERRIDE_AWAY):
        state = OVERRIDE_AUTO
    store.set_state(_KEY_OVERRIDE, state)
    if state != OVERRIDE_AWAY:
        clear_away_announced()  # leaving forced-away: re-announce next time
    return state


def mark_owner_active(ts: float = None) -> None:
    """Record that the owner just was active (sent a message / self-chat)."""
    store.set_state(_KEY_LAST_ACTIVE, str(ts if ts is not None else _now()))
    clear_away_announced()  # owner back: next away period re-announces


def owner_last_active() -> float:
    """Epoch seconds of the owner's last observed activity (0 if never)."""
    try:
        return float(store.get_state(_KEY_LAST_ACTIVE, "0"))
    except (TypeError, ValueError):
        return 0.0


def is_away(now: float = None) -> bool:
    """True if the owner is currently away (override wins, else timing)."""
    state = get_override()
    if state == OVERRIDE_PRESENT:
        return False
    if state == OVERRIDE_AWAY:
        return True
    last = owner_last_active()
    if last <= 0:
        return False  # never observed -> assume present until activity is seen
    return (_now() if now is None else now) - last > AWAY_AFTER_MINUTES * 60


def idle_seconds() -> int:
    """Seconds since the owner's last activity (-1 if never observed)."""
    last = owner_last_active()
    return int(_now() - last) if last > 0 else -1


def status() -> dict:
    """Snapshot for the menu / ``!away`` status."""
    return {
        "override": get_override(),
        "away": is_away(),
        "idle_seconds": idle_seconds(),
        "threshold_minutes": AWAY_AFTER_MINUTES,
    }


def away_announced(session_id: str) -> bool:
    """Whether the owner-is-away notice was already sent in this session."""
    return bool(session_id) and store.get_state(_KEY_ANNOUNCED_PREFIX + session_id, "") == "1"


def mark_away_announced(session_id: str) -> None:
    """Record that the away notice was sent in this session."""
    if session_id:
        store.set_state(_KEY_ANNOUNCED_PREFIX + session_id, "1")


def clear_away_announced() -> int:
    """Forget all per-session away-notice flags (owner active / away ended)."""
    return store.delete_state_prefix(_KEY_ANNOUNCED_PREFIX)


def away_context(session_id: str = "", announced: bool = False) -> str:
    """System-prompt note for away mode. The LLM crafts the wording (not
    hardcoded). First reply after going away: announce that the owner is away.
    Later replies in the same session: keep chatting without re-announcing."""
    if not announced:
        return (
            "## Owner is away\n"
            f"Your owner {OWNER_NAME} is currently away from WhatsApp and cannot reply "
            "right now. You are stepping in as their assistant. In THIS reply, let the "
            "person know your owner is away (for example: they can leave a message, or "
            "they can chat with you), then keep chatting with them on your owner's "
            "behalf. Write it naturally in your own voice -- do not reuse a fixed or "
            "boilerplate sentence."
        )
    return (
        "## Owner is away\n"
        f"Your owner {OWNER_NAME} is still away from WhatsApp. You are their "
        "assistant. Keep chatting with the person naturally on your owner's behalf. "
        "You have already let them know the owner is away -- do not repeat that "
        "unless they ask or it is clearly needed."
    )


# On the very first run (no persisted timestamp) assume the owner is present;
# they become "away" only after AWAY_AFTER_MINUTES of inactivity. Subsequent
# restarts keep the persisted timestamp.
if owner_last_active() <= 0:
    mark_owner_active()
