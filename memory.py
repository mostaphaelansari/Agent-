import atexit
import logging
import pathlib
import sqlite3

logger = logging.getLogger(__name__)

DB = pathlib.Path("memory.db")
_conn = sqlite3.connect(DB, check_same_thread=False)
_conn.execute(
    """CREATE TABLE IF NOT EXISTS turns(
        session_id TEXT, role TEXT, text TEXT,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )"""
)


@atexit.register
def _close() -> None:
    _conn.close()


def save_turn(session_id: str, role: str, text: str) -> None:
    _conn.execute(
        "INSERT INTO turns VALUES(?,?,?,CURRENT_TIMESTAMP)",
        (session_id, role, text),
    )
    _conn.commit()


def get_last_turns(session_id: str, k: int = 10) -> list[tuple[str, str]]:
    rows = _conn.execute(
        "SELECT role, text FROM turns WHERE session_id=? ORDER BY ROWID DESC LIMIT ?",
        (session_id, k),
    ).fetchall()
    return list(reversed(rows))


def build_context(session_id: str, k: int = 10, max_chars: int = 8000) -> str:
    """Return the last `k` turns joined into a single string, truncated to ~max_chars
    from the most recent end so older turns drop first when the budget is exceeded."""
    turns = get_last_turns(session_id, k=k)
    selected: list[tuple[str, str]] = []
    budget = max_chars
    for role, text in reversed(turns):
        line = f"{role}: {text}"
        if len(line) >= budget:
            break
        selected.append((role, text))
        budget -= len(line) + 1
    selected.reverse()
    if len(selected) < len(turns):
        logger.debug(
            "context truncated: kept %d/%d turns within %d chars",
            len(selected), len(turns), max_chars,
        )
    return "\n".join(f"{r}: {t}" for r, t in selected)
