"""SQLite cache keyed by DOI so repeat runs skip known papers.

The cache stores the *serialised Paper* as JSON. It is deliberately schema
free: reruns after a field is added still work, they simply come back with
defaults for the missing keys.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from pathlib import Path
from typing import Dict, Iterable

from .models import Paper

DEFAULT_PATH = Path("output/cache.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    doi TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    channel TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _to_json(paper: Paper) -> str:
        return json.dumps(asdict(paper), ensure_ascii=False)

    @staticmethod
    def _from_json(payload: str) -> Paper:
        data = json.loads(payload)
        known = {f.name for f in fields(Paper)}
        return Paper(**{k: v for k, v in data.items() if k in known})

    def upsert_many(self, papers: Iterable[Paper]) -> int:
        rows = [
            (paper.doi, self._to_json(paper), paper.channel)
            for paper in papers
            if paper.doi
        ]
        if not rows:
            return 0
        self._conn.executemany(
            """
            INSERT INTO papers (doi, payload, channel) VALUES (?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
                payload = excluded.payload,
                channel = excluded.channel,
                updated_at = datetime('now')
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_many(self, dois: Iterable[str]) -> Dict[str, Paper]:
        wanted = [d for d in dict.fromkeys(dois) if d]
        found: Dict[str, Paper] = {}
        chunk = 500
        for start in range(0, len(wanted), chunk):
            batch = wanted[start : start + chunk]
            placeholders = ",".join("?" for _ in batch)
            cursor = self._conn.execute(
                f"SELECT doi, payload FROM papers WHERE doi IN ({placeholders})",
                batch,
            )
            for doi, payload in cursor.fetchall():
                found[doi] = self._from_json(payload)
        return found

    def count(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM papers")
        return int(cursor.fetchone()[0])

    def clear(self) -> None:
        self._conn.execute("DELETE FROM papers")
        self._conn.commit()