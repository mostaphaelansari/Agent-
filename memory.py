import sqlite3
import pathlib

DB = pathlib.Path("memory.db")
_conn = sqlite3.connect(DB, check_same_thread=False)
_conn.execute("""CREATE TABLE IF NOT EXISTS turns(
    session_id TEXT, role TEXT, text TEXT,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")

def save_turn(session_id, role, text):
    _conn.execute("INSERT INTO turns VALUES(?,?,?,CURRENT_TIMESTAMP)",
                  (session_id, role, text))
    _conn.commit()

def get_last_turns(session_id, k=10):
    rows = _conn.execute(
        "SELECT role, text FROM turns WHERE session_id=? ORDER BY ROWID DESC LIMIT ?",
        (session_id, k)).fetchall()
    return list(reversed(rows))
