"""Data model shared by every source and by the exporter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

CSV_COLUMNS = [
    "title",
    "authors",
    "affiliations",
    "year",
    "journal",
    "abstract",
    "doi",
    "citations",
    "oa_status",
    "url",
    "openalex_id",
    "source",
    "volume",
    "pages",
    "keywords",
    "channel",
    "pv_related",
]

AUTHOR_SEP = "; "
AFFILIATION_SEP = "; "
MULTI_AFFILIATION_SEP = " | "
KEYWORD_SEP = "; "


def norm_doi(doi: Optional[str]) -> str:
    """Normalise a DOI so the same work always produces the same cache key."""
    if not doi:
        return ""
    value = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.strip()


@dataclass
class Paper:
    """One journal article, merged from Crossref and OpenAlex."""

    doi: str = ""
    title: str = ""
    authors: List[str] = field(default_factory=list)
    author_affiliations: List[List[str]] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    abstract: str = ""
    citations: Optional[int] = None
    oa_status: str = ""
    url: str = ""
    openalex_id: str = ""
    source: str = "crossref"
    volume: str = ""
    pages: str = ""
    keywords: List[str] = field(default_factory=list)
    channel: str = ""
    pv_related: bool = False

    @property
    def affiliations(self) -> str:
        """Affiliations aligned to the author list, joined for a single cell."""
        cells: List[str] = []
        for index, _ in enumerate(self.authors):
            if index < len(self.author_affiliations):
                joined = MULTI_AFFILIATION_SEP.join(
                    a for a in self.author_affiliations[index] if a
                )
            else:
                joined = ""
            cells.append(joined)
        while cells and not cells[-1]:
            cells.pop()
        return AFFILIATION_SEP.join(cells)

    def to_row(self) -> List[str]:
        row = {
            "title": self.title,
            "authors": AUTHOR_SEP.join(self.authors),
            "affiliations": self.affiliations,
            "year": self.year,
            "journal": self.journal,
            "abstract": self.abstract,
            "doi": self.doi,
            "citations": "" if self.citations is None else str(self.citations),
            "oa_status": self.oa_status,
            "url": self.url,
            "openalex_id": self.openalex_id,
            "source": self.source,
            "volume": self.volume,
            "pages": self.pages,
            "keywords": KEYWORD_SEP.join(self.keywords),
            "channel": self.channel,
            "pv_related": "yes" if self.pv_related else "no",
        }
        return [row[name] for name in CSV_COLUMNS]

    def searchable_text(self) -> str:
        """Title plus abstract, used for the pv_related flag."""
        return f"{self.title} {self.abstract}"