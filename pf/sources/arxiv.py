"""arXiv discovery.

Small for this topic (a few dozen papers) but every hit is downloadable, and
arXiv never challenges a script. Used as a supplement, not a primary index.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Dict, Optional

import requests

API_URL = "https://export.arxiv.org/api/query"

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}


class ArxivError(RuntimeError):
    pass


def _field(query: str) -> str:
    """arXiv wants explicit field prefixes; default to abstract+title search."""
    return query


class Arxiv:
    def __init__(
        self,
        pause: float = 3.0,
        timeout: int = 60,
        retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        # arXiv asks for one request every three seconds.
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        self.requests_made = 0
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": "paper_finder/0.1 (https://github.com/Yuxing0926/paper_finder)"}
        )

    def search(
        self,
        query: str,
        channel: str,
        max_results: int = 200,
        log=None,
    ) -> Dict[str, dict]:
        """Return normalised-DOI-or-arxiv-id -> record."""
        params = {
            "search_query": query,
            "start": 0,
            "max_results": min(max_results, 500),
            "sortBy": "relevance",
        }
        payload = None
        last: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
                self.requests_made += 1
                response.raise_for_status()
                payload = response.text
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(3 * (attempt + 1))
        if payload is None:
            raise ArxivError(f"arXiv request failed: {last}")

        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            # Malformed feed: skip this query rather than abort the run.
            if log:
                log(f"  [arxiv] {query[:44]!r}: unparseable response, skipped")
            return {}

        total = root.findtext("os:totalResults", namespaces=NS) or "0"
        entries = root.findall("a:entry", NS)
        if log:
            log(f"  [arxiv] {query[:44]!r}: {len(entries)} of {total}")

        collected: Dict[str, dict] = {}
        for entry in entries:
            doi = (entry.findtext("arxiv:doi", namespaces=NS) or "").strip().lower()
            identity = (entry.findtext("a:id", namespaces=NS) or "").strip()
            arxiv_id = identity.rsplit("/", 1)[-1] if identity else ""
            key = doi or (f"arxiv:{arxiv_id}" if arxiv_id else "")
            if not key or key in collected:
                continue
            title = " ".join((entry.findtext("a:title", namespaces=NS) or "").split())
            if not title:
                continue
            published = entry.findtext("a:published", namespaces=NS) or ""
            pdf_url = ""
            for link in entry.findall("a:link", NS):
                if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                    pdf_url = link.get("href") or ""
                    break
            journal_ref = (entry.findtext("arxiv:journal_ref", namespaces=NS) or "").strip()
            collected[key] = {
                "doi": doi,
                "key": key,
                "title": title,
                "year": published[:4],
                "journal": journal_ref,
                "abstract": " ".join(
                    (entry.findtext("a:summary", namespaces=NS) or "").split()
                ),
                "url": pdf_url or identity,
                "pdf_url": pdf_url,
                "channel": channel,
                "source": "arxiv",
            }
        if self.pause:
            time.sleep(self.pause)
        return collected