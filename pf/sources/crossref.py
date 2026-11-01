"""Crossref discovery layer.

Crossref's works endpoint is free and, in the polite pool, effectively
unlimited. We use it to *find* candidate DOIs, then let OpenAlex fill in the
metadata. Pagination is offset based and Crossref rejects ``offset + rows >
10000``, so deep paging is capped by construction.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from ..filters import matches_groups
from ..models import norm_doi

API_URL = "https://api.crossref.org/works"

SELECT_FIELDS = ",".join(
    [
        "DOI",
        "title",
        "abstract",
        "author",
        "container-title",
        "issued",
        "published-print",
        "published-online",
        "type",
        "URL",
        "is-referenced-by-count",
    ]
)

MAX_OFFSET_WINDOW = 10000


class CrossrefError(RuntimeError):
    pass


def _clean_abstract(raw: Optional[str]) -> str:
    if not raw:
        return ""
    import re

    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.split())


def _title_of(item: dict) -> str:
    titles = item.get("title") or []
    if titles:
        return " ".join(str(titles[0]).split())
    return ""


def _year_of(item: dict) -> str:
    for key in ("issued", "published-print", "published-online"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def _journal_of(item: dict) -> str:
    containers = item.get("container-title") or []
    if containers:
        return " ".join(str(containers[0]).split())
    return ""


def _authors_of(item: dict) -> tuple[List[str], List[List[str]]]:
    authors: List[str] = []
    affiliations: List[List[str]] = []
    for person in item.get("author") or []:
        given = (person.get("given") or "").strip()
        family = (person.get("family") or "").strip()
        name = " ".join(part for part in (given, family) if part)
        if not name:
            name = (person.get("name") or "").strip()
        if not name:
            continue
        authors.append(name)
        affs = [
            " ".join(str(entry.get("name", "")).split())
            for entry in (person.get("affiliation") or [])
            if entry.get("name")
        ]
        affiliations.append(affs)
    return authors, affiliations


class Crossref:
    """Thin Crossref client with retries and polite-pool headers."""

    def __init__(
        self,
        email: str = "",
        page_size: int = 1000,
        pause: float = 1.0,
        timeout: int = 60,
        retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.page_size = min(page_size, 1000)
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        contact = f" (mailto:{email})" if email else ""
        self.session.headers.update(
            {
                "User-Agent": f"paper_finder/0.1{contact}",
                "Accept": "application/json",
            }
        )
        self.requests_made = 0

    def _get(self, params: dict) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
                self.requests_made += 1
                if response.status_code == 429 or response.status_code >= 500:
                    raise CrossrefError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.json()["message"]
            except Exception as exc:  # noqa: BLE001 - retried then re-raised
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(2**attempt)
        raise CrossrefError(f"Crossref request failed: {last_error}")

    def page(
        self,
        query: str,
        offset: int,
        since: Optional[int] = None,
        doc_type: str = "journal-article",
        rows: Optional[int] = None,
    ) -> dict:
        filters = []
        if since:
            filters.append(f"from-pub-date:{since}-01-01")
        if doc_type:
            filters.append(f"type:{doc_type}")
        params = {
            "query.title": query,
            "rows": rows or self.page_size,
            "offset": offset,
            "select": SELECT_FIELDS,
        }
        if filters:
            params["filter"] = ",".join(filters)
        return self._get(params)

    def discover(
        self,
        query: str,
        must: List[List[str]],
        channel: str,
        since: Optional[int] = None,
        depth: int = 4000,
        doc_type: str = "journal-article",
        match_field: str = "title",
        log=None,
    ) -> Dict[str, dict]:
        """Collect matching DOIs for one query, up to ``depth`` candidates.

        Returns a mapping of normalised DOI -> lightweight record.
        """
        collected: Dict[str, dict] = {}
        seen = 0
        offset = 0
        rows = min(self.page_size, depth)

        while seen < depth:
            remaining_window = MAX_OFFSET_WINDOW - offset
            if remaining_window <= 0:
                break
            rows = min(rows, remaining_window, depth - seen)
            if rows <= 0:
                break
            try:
                message = self.page(query, offset, since, doc_type, rows)
            except CrossrefError as exc:
                if log:
                    log(f"  ! Crossref page offset={offset} failed: {exc}")
                break

            items = message.get("items") or []
            if not items:
                break

            for item in items:
                doi = norm_doi(item.get("DOI"))
                if not doi or doi in collected:
                    continue
                title = _title_of(item)
                if not title:
                    continue
                haystack = title
                if match_field == "title_abstract":
                    haystack = f"{title} {_clean_abstract(item.get('abstract'))}"
                if not matches_groups(haystack, must):
                    continue
                collected[doi] = {
                    "doi": doi,
                    "title": title,
                    "year": _year_of(item),
                    "journal": _journal_of(item),
                    "abstract": _clean_abstract(item.get("abstract")),
                    "url": item.get("URL") or f"https://doi.org/{doi}",
                    "channel": channel,
                }

            seen += len(items)
            offset += len(items)
            if log:
                log(
                    f"  offset={offset:5d} scanned={seen:5d} "
                    f"kept={len(collected):4d} channel={channel}"
                )
            if len(items) < rows:
                break
            if self.pause:
                time.sleep(self.pause)

        return collected