"""Dédup persistante entre runs : SQLite (data/seen_jobs.db, versionnée dans git)."""
import sqlite3
from datetime import date
from pathlib import Path

from job_hunter.models import RawJob

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    fingerprint TEXT PRIMARY KEY,
    first_seen  DATE NOT NULL,
    last_seen   DATE NOT NULL,
    source      TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_last_seen ON seen_jobs(last_seen);
"""


class SeenJobsDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def is_new(self, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_jobs WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return row is None

    def mark_seen(self, fingerprint: str, job: RawJob, day: date | None = None) -> None:
        """Insert si nouveau, sinon met à jour last_seen (first_seen intact)."""
        d = (day or date.today()).isoformat()
        self._conn.execute(
            """INSERT INTO seen_jobs (fingerprint, first_seen, last_seen, source, url, title, company)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET last_seen = excluded.last_seen""",
            (fingerprint, d, d, job.source, job.url, job.title, job.company),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
