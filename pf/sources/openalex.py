"""OpenAlex enrichment layer.

Only bulk DOI lookups are used: ``filter=doi:a|b|c`` returns up to 100 records
per call, costs a hundredth of a *search* call, and single-record lookups are
free. That keeps a 5,700 paper run at roughly half a cent of the free daily
budget.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

import requests

API_URL = "https://api.openalex.org/works"

SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "publication_year",
        "primary_location",
        "authorships",
        "abstract_inverted_index",
        "cited_by_count",
        "open_access",
        "biblio",
        "keywords",
    ]
)

MAX_BATCH = 100


class OpenAlexError(RuntimeError):
    pass


class OpenAlexBudgetError(OpenAlexError):
    """Raised when the account's remaining daily budget is too low to continue."""


def inverted_index_to_text(index: Optional[dict]) -> str:
    """Rebuild abstract text from OpenAlex's ``{term: [positions]}`` format."""
    if not index:
        return ""
    positioned: List[tuple] = []
    for term, positions in index.items():
        for position in positions:
            positioned.append((position, term))
    positioned.sort(key=lambda pair: pair[0])
    return " ".join(term for _, term in positioned)


def _journal_of(work: dict) -> str:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return " ".join(str(source.get("display_name") or "").split())


def _authors_of(work: dict) -> tuple[List[str], List[List[str]]]:
    authors: List[str] = []
    affiliations: List[List[str]] = []
    for authorship in work.get("authorships") or []:
        person = authorship.get("author") or {}
        name = " ".join(str(person.get("display_name") or "").split())
        if not name:
            continue
        authors.append(name)
        institutions = [
            " ".join(str(inst.get("display_name") or "").split())
            for inst in (authorship.get("institutions") or [])
            if inst.get("display_name")
        ]
        affiliations.append(institutions)
    return authors, affiliations


def _pages_of(work: dict) -> str:
    biblio = work.get("biblio") or {}
    first = biblio.get("first_page") or ""
    last = biblio.get("last_page") or ""
    if first and last and last != first:
        return f"{first}-{last}"
    return first or last or ""


def _keywords_of(work: dict) -> List[str]:
    names: List[str] = []
    for entry in work.get("keywords") or []:
        label = entry.get("display_name") or entry.get("keyword") or ""
        label = " ".join(str(label).split())
        if label and label not in names:
            names.append(label)
    return names


class OpenAlex:
    """Batched DOI lookup client with budget awareness."""

    def __init__(
        self,
        email: str = "",
        batch_size: int = MAX_BATCH,
        pause: float = 0.2,
        timeout: int = 90,
        retries: int = 3,
        min_remaining_usd: float = 0.02,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.batch_size = min(batch_size, MAX_BATCH)
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        self.min_remaining_usd = min_remaining_usd
        self.cost_usd = 0.0
        self.remaining_usd: Optional[float] = None
        self.requests_made = 0
        self.session = session or requests.Session()
        contact = f" (mailto:{email})" if email else ""
        self.session.headers.update(
            {
                "User-Agent": f"paper_finder/0.1{contact}",
                "Accept": "application/json",
            }
        )
        self.email = email

    def _request(self, params: dict) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
                self.requests_made += 1
                remaining = response.headers.get("X-RateLimit-Remaining-USD")
                if remaining is not None:
                    try:
                        self.remaining_usd = float(remaining)
                    except ValueError:
                        pass
                cost = response.headers.get("X-RateLimit-Cost-USD")
                if cost is not None:
                    try:
                        self.cost_usd += float(cost)
                    except ValueError:
                        pass
                if response.status_code == 429 or response.status_code >= 500:
                    raise OpenAlexError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - retried then re-raised
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(2**attempt)
        raise OpenAlexError(f"OpenAlex request failed: {last_error}")

    def _check_budget(self) -> None:
        if (
            self.remaining_usd is not None
            and self.remaining_usd < self.min_remaining_usd
        ):
            raise OpenAlexBudgetError(
                f"OpenAlex daily budget nearly exhausted "
                f"(remaining ${self.remaining_usd:.4f} < ${self.min_remaining_usd:.2f})"
            )

    def fetch_batch(self, dois: Iterable[str]) -> Dict[str, dict]:
        doi_list = [d for d in dois if d]
        if not doi_list:
            return {}
        params = {
            "filter": "doi:" + "|".join(doi_list),
            "per_page": len(doi_list),
            "select": SELECT_FIELDS,
        }
        if self.email:
            params["mailto"] = self.email
        payload = self._request(params)
        results: Dict[str, dict] = {}
        for work in payload.get("results") or []:
            from ..models import norm_doi

            key = norm_doi(work.get("doi"))
            if key:
                results[key] = work
        return results

    def enrich(self, dois: List[str], log=None) -> Dict[str, dict]:
        """Fetch metadata for each DOI, returning normalised DOI -> work."""
        unique = list(dict.fromkeys(d for d in dois if d))
        collected: Dict[str, dict] = {}
        total = len(unique)
        for start in range(0, total, self.batch_size):
            self._check_budget()
            batch = unique[start : start + self.batch_size]
            try:
                data = self.fetch_batch(batch)
            except OpenAlexBudgetError:
                raise
            except OpenAlexError as exc:
                if log:
                    log(f"  ! OpenAlex batch {start} failed: {exc}")
                continue
            collected.update(data)
            if log:
                log(
                    f"  batch {start // self.batch_size + 1:3d} "
                    f"({start + len(batch):5d}/{total}) "
                    f"matched={len(data):3d} total={len(collected):5d} "
                    f"cost=${self.cost_usd:.4f}"
                )
            if self.pause and start + self.batch_size < total:
                time.sleep(self.pause)
        return collected

    @staticmethod
    def to_paper(work: dict):
        from ..models import Paper, norm_doi

        authors, affiliations = _authors_of(work)
        open_access = work.get("open_access") or {}
        abstract = inverted_index_to_text(work.get("abstract_inverted_index"))
        paper = Paper(
            doi=norm_doi(work.get("doi")),
            title=" ".join(str(work.get("title") or "").split()),
            authors=authors,
            author_affiliations=affiliations,
            year=str(work.get("publication_year") or ""),
            journal=_journal_of(work),
            abstract=abstract,
            citations=work.get("cited_by_count"),
            oa_status=open_access.get("oa_status") or "",
            url=work.get("doi") or open_access.get("oa_url") or "",
            openalex_id=(work.get("id") or "").rsplit("/", 1)[-1],
            source="crossref+openalex",
            volume=str((work.get("biblio") or {}).get("volume") or ""),
            pages=_pages_of(work),
            keywords=_keywords_of(work),
        )
        return paper