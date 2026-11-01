"""CSV writer."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from .models import CSV_COLUMNS, Paper


def write_csv(papers: Iterable[Paper], path: Path | str) -> int:
    """Write papers to ``path`` and return the number of data rows written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows: Sequence[Paper] = list(papers)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for paper in rows:
            writer.writerow(paper.to_row())
    return len(rows)