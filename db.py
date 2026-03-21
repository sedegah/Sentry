import sqlite3
from datetime import datetime
from typing import Optional

DB_NAME = "sentry.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS uptime_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            latency REAL,
            timestamp DATETIME NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def log_status(name: str, url: str, status: str, latency: Optional[float]) -> None:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO uptime_logs (name, url, status, latency, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, url, status, latency, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
