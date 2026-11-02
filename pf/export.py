"""CSV writer and reader."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import (
    CSV_COLUMNS,
    MULTI_AFFILIATION_SEP,
    Paper,
)


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


def _split(value: str, sep: str) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(sep)]


def read_csv(path: Path | str) -> List[Paper]:
    """Rebuild Papers from a CSV produced by :func:`write_csv`."""
    source = Path(path)
    papers: List[Paper] = []
    with source.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            authors = _split(row.get("authors", ""), ";")
            raw_affiliations = row.get("affiliations", "")
            affiliations = _split(raw_affiliations, ";")
            aligned: List[List[str]] = []
            for index in range(len(authors)):
                cell = affiliations[index] if index < len(affiliations) else ""
                parts = _split(cell, MULTI_AFFILIATION_SEP.strip())
                aligned.append(parts)
            citations = row.get("citations", "").strip()
            papers.append(
                Paper(
                    doi=(row.get("doi") or "").strip().lower(),
                    title=(row.get("title") or "").strip(),
                    authors=authors,
                    author_affiliations=aligned,
                    year=(row.get("year") or "").strip(),
                    journal=(row.get("journal") or "").strip(),
                    abstract=(row.get("abstract") or "").strip(),
                    citations=int(citations) if citations.isdigit() else None,
                    oa_status=(row.get("oa_status") or "").strip(),
                    url=(row.get("url") or "").strip(),
                    openalex_id=(row.get("openalex_id") or "").strip(),
                    source=(row.get("source") or "").strip(),
                    volume=(row.get("volume") or "").strip(),
                    pages=(row.get("pages") or "").strip(),
                    keywords=_split(row.get("keywords", ""), ";"),
                    channel=(row.get("channel") or "").strip(),
                    pv_related=(row.get("pv_related") or "").strip() == "yes",
                )
            )
    return papers