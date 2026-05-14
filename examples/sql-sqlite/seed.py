"""Seed a SQLite database with a tiny interaction log for the SQL data-source example."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB = Path(__file__).parent / "events.db"

# 5 users × 10 items gives enough rows for a train/test split without
# hitting irspack's minimum-observations guard.
_USERS = [f"u{u}" for u in range(1, 6)]
_ITEMS = [f"i{i}" for i in range(1, 11)]

_START = date(2026, 1, 1)


def seed() -> None:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE events (
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            event_at TEXT NOT NULL
        );
        """
    )
    # Each user interacts with every item so the matrix is dense enough for
    # evaluation (recall@5 requires at least 1 held-out item per test user).
    # Dates are spaced one day apart per row so they parse cleanly as ISO-8601.
    rows = []
    seq = 0
    for user in _USERS:
        for item in _ITEMS:
            event_date = (_START + timedelta(days=seq)).isoformat()
            rows.append((user, item, event_date))
            seq += 1
    con.executemany("INSERT INTO events VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    print(f"Seeded {len(rows)} rows into {DB}")


if __name__ == "__main__":
    seed()
