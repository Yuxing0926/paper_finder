"""Europe PMC discovery.

Strong on abstracts and author affiliations, weak on materials science, and the
service is genuinely flaky (503s that clear on retry). Enabled as a supplement:
it occasionally supplies a paper and an abstract the other two indexes lack.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import requests

API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcError(RuntimeError):
    pass


class EuropePmc:
    def __init__(
        self,
        timeout: int = 45,
        retries: int = 4,
        pause: float = 1.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.pause = pause
        self.requests_made = 0
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": "paper_finder/0.1 (mailto:469900626@qq.com)"}
        )

    def search(
        self,
        query: str,
        channel: str,
        page_size: int = 100,
        log=None,
    ) -> Dict[str, dict]:
        params = {
            "query": query,
            "format": "json",
            "pageSize": min(page_size, 1000),
            "resultType": "core",
        }
        payload = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
                self.requests_made += 1
                if response.status_code >= 500:
                    raise EuropePmcError(f"HTTP {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                break
            except Exception:
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        if payload is None:
            # Not fatal: Europe PMC being down must not stop a run.
            if log:
                log(f"  [epmc] {query[:44]!r}: unavailable, skipped")
            return {}

        results = (payload.get("resultList") or {}).get("result") or []
        if log:
            log(f"  [epmc] {query[:44]!r}: {len(results)} of {payload.get('hitCount')}")

        collected: Dict[str, dict] = {}
        for item in results:
            doi = (item.get("doi") or "").strip().lower()
            pmid = item.get("pmid") or ""
            key = doi or (f"pmid:{pmid}" if pmid else "")
            if not key or key in collected:
                continue
            title = " ".join((item.get("title") or "").split())
            if not title:
                continue
            journal = ""
            journal_info = item.get("journalInfo") or {}
            if isinstance(journal_info.get("journal"), dict):
                journal = journal_info["journal"].get("title") or ""
            collected[key] = {
                "doi": doi,
                "key": key,
                "title": title,
                "year": str(item.get("pubYear") or ""),
                "journal": journal,
                "abstract": " ".join((item.get("abstractText") or "").split()),
                "url": f"https://doi.org/{doi}" if doi else "",
                "channel": channel,
                "source": "europepmc",
            }
        if self.pause:
            time.sleep(self.pause)
        return collected