"""Locate and download open-access PDFs for papers already in the CSV.

Two things this deliberately does *not* do: it never bypasses a paywall, and it
never retries a host that has already answered with a hard block. Publishers
such as MDPI and Wiley serve 403 to every scripted request, so a per-host
breaker keeps the run from burning minutes on hosts that will never yield.

Unpaywall supplies the candidate links; OpenAlex fills the gaps for records
whose Unpaywall entry has only a landing page.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from .models import Paper

UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
OPENALEX_URL = "https://api.openalex.org/works"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/pdf,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Hosts that only issue redirects. Never blacklist these; the real server is
# whichever host they land on.
REDIRECTOR_HOSTS = {
    "doi.org",
    "dx.doi.org",
    "www.doi.org",
    "hdl.handle.net",
    "handle.net",
    "www.handle.net",
}

# Hosts observed to reject every scripted download with 403/202.
BLOCKED_HOSTS = {
    "www.mdpi.com",
    "mdpi.com",
    "onlinelibrary.wiley.com",
    "ietresearch.onlinelibrary.wiley.com",
    "pubs.acs.org",
    "pubs.aip.org",
    "pubs.rsc.org",
    "www.sciencedirect.com",
    "linkinghub.elsevier.com",
    "link.springer.com",
    "www.nature.com",
    "pmc.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "www.jove.com",
    "validate.perfdrive.com",
    "www.tandfonline.com",
    "ieeexplore.ieee.org",
    "www.scientific.net",
    "doaj.org",
    "opg.optica.org",
    "aip.scitation.org",
}

_STATUS_OPEN = {"gold", "hybrid", "bronze", "green", "diamond"}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str, fallback: str = "untitled", limit: int = 120) -> str:
    """Turn a title or journal name into a filesystem-safe fragment."""
    cleaned = _UNSAFE.sub(" ", text or "")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return fallback
    return cleaned[:limit].strip(" .") or fallback


def first_author_surname(paper: Paper) -> str:
    if not paper.authors:
        return "Unknown"
    first = paper.authors[0].strip()
    if not first:
        return "Unknown"
    if "," in first:
        return safe_name(first.split(",")[0], "Unknown", 40)
    parts = first.split()
    return safe_name(parts[-1], "Unknown", 40)


def target_path(paper: Paper, root: Path, used: set) -> Path:
    """Build ``<root>/<year>/<surname>_<year>_<journal>.pdf``, deduplicated."""
    year = paper.year if paper.year.isdigit() else "unknown"
    surname = first_author_surname(paper)
    journal = safe_name(paper.journal, "no journal", 60)
    stem = f"{surname}_{year}_{journal}"
    candidate = root / year / f"{stem}.pdf"
    counter = 2
    while str(candidate).lower() in used or candidate.exists():
        candidate = root / year / f"{stem} ({counter}).pdf"
        counter += 1
    used.add(str(candidate).lower())
    return candidate


@dataclass
class DownloadResult:
    doi: str
    status: str          # saved | no_link | blocked | not_pdf | http_error | error | skipped
    detail: str = ""
    path: Optional[str] = None
    bytes_written: int = 0


@dataclass
class DownloadReport:
    results: List[DownloadResult] = field(default_factory=list)

    def add(self, result: DownloadResult) -> None:
        self.results.append(result)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def saved(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == "saved"]

    @property
    def pending(self) -> List[DownloadResult]:
        """Records a human still has to fetch: no open copy or a hard block."""
        return [
            r
            for r in self.results
            if r.status in {"no_link", "blocked", "not_pdf", "http_error", "error"}
        ]

    @property
    def closed(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == "closed"]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for result in self.results:
            counts[result.status] += 1
        return dict(counts)


class PdfFetcher:
    def __init__(
        self,
        email: str = "",
        api_key: str = "",
        root: Path | str = Path("output/pdfs"),
        timeout: int = 45,
        pause: float = 0.5,
        max_bytes: int = 80 * 1024 * 1024,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.email = email
        self.api_key = api_key
        self.root = Path(root)
        self.timeout = timeout
        self.pause = pause
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        contact = f" (mailto:{email})" if email else ""
        self.api = requests.Session()
        self.api.headers.update(
            {
                "User-Agent": f"paper_finder/0.1{contact}",
                "Accept": "application/json",
            }
        )
        self._blocked_hosts = set(BLOCKED_HOSTS)
        self._used_paths: set = set()
        self._seen_landing: Dict[str, Optional[str]] = {}

    def _unpaywall(self, doi: str) -> dict:
        if doi in self._seen_landing:
            return {}
        if not self.email:
            # Unpaywall answers 422 without an email; remember nothing so a
            # later run with an email configured can still work.
            return {}
        params = {"email": self.email}
        for attempt in range(3):
            try:
                response = self.api.get(
                    UNPAYWALL_URL.format(doi=doi), params=params, timeout=30
                )
                if response.status_code == 404:
                    self._seen_landing[doi] = None
                    return {}
                response.raise_for_status()
                payload = response.json()
                self._seen_landing[doi] = (payload.get("best_oa_location") or {}).get("url")
                return payload
            except Exception:  # noqa: BLE001
                if attempt < 2:
                    time.sleep(2**attempt)
        # Transient failure: do not cache, so a retry can still succeed.
        return {}

    def _openalex_pdf(self, doi: str) -> List[str]:
        params = {
            "filter": f"doi:{doi}",
            # open_access.oa_url is where arXiv and many repositories put the
            # only direct file link; omitting it from select meant those papers
            # were reported as having no open-access copy at all.
            "select": "id,open_access,best_oa_location,locations",
        }
        if self.email:
            params["mailto"] = self.email
        if self.api_key:
            # Without this every lookup draws on the shared anonymous budget,
            # which a few hundred downloads exhaust; the failures then look
            # like "no open-access copy" when the truth is "rate limited".
            params["api_key"] = self.api_key
        try:
            response = self.api.get(OPENALEX_URL, params=params, timeout=30)
            response.raise_for_status()
            results = response.json().get("results") or []
        except Exception:
            return []
        if not results:
            return []
        work = results[0]
        urls = []

        def add(candidate):
            if candidate and candidate not in urls:
                urls.append(candidate)

        open_access = work.get("open_access") or {}
        add(open_access.get("oa_url"))
        add((work.get("best_oa_location") or {}).get("pdf_url"))
        for location in work.get("locations") or []:
            add(location.get("pdf_url"))
        return urls

    def candidate_links(self, paper: Paper) -> List[str]:
        links: List[str] = []
        payload = self._unpaywall(paper.doi)
        for location in payload.get("oa_locations") or []:
            url = location.get("url_for_pdf") or location.get("url")
            if url:
                links.append(url)
        for fallback in self._openalex_pdf(paper.doi) or []:
            links.append(fallback)
        # preserve order, drop duplicates, drop unusable local/proxy targets
        usable = []
        for url in dict.fromkeys(links):
            host = urlparse(url).netloc.lower()
            if host.startswith("localhost") or host.startswith("127.0.0.1"):
                continue
            usable.append(url)
        return usable

    def _try_download(
        self, url: str, destination: Path, allow_landing: bool = True
    ) -> tuple[str, str, int, Optional[str]]:
        """Attempt a download.

        Returns ``(status, detail, bytes_written, pdf_url)``. When the response
        is HTML but advertises a PDF via ``citation_pdf_url``, ``pdf_url`` is
        returned so the caller can retry once against the real file.

        Only the host that actually served the response is ever blacklisted:
        redirectors such as doi.org must stay usable.
        """
        host = urlparse(url).netloc.lower()
        if host in self._blocked_hosts:
            return "blocked", f"{host} rejects scripted downloads", 0, None
        try:
            with self.session.get(
                url, timeout=self.timeout, allow_redirects=True, stream=True
            ) as response:
                final_host = urlparse(response.url).netloc.lower()
                if final_host in self._blocked_hosts:
                    return "blocked", f"{final_host} rejects scripted downloads", 0, None
                if response.status_code in (401, 402, 403, 451, 202):
                    if final_host not in REDIRECTOR_HOSTS:
                        self._blocked_hosts.add(final_host)
                    return (
                        "blocked",
                        f"HTTP {response.status_code} from {final_host}",
                        0,
                        None,
                    )
                if response.status_code >= 400:
                    return (
                        "http_error",
                        f"HTTP {response.status_code} from {final_host}",
                        0,
                        None,
                    )

                content_type = response.headers.get("Content-Type", "")
                iterator = response.iter_content(chunk_size=65536)
                first = next(iterator, b"")
                if not first.startswith(b"%PDF"):
                    if b"not a bot" in first[:4000].lower() or b"captcha" in first[:4000].lower():
                        return "blocked", f"bot challenge from {final_host}", 0, None
                    if allow_landing:
                        landing = self._pdf_from_landing(first, response.url, iterator)
                        if landing:
                            return "not_pdf", "landing page resolved", 0, landing
                    return (
                        "not_pdf",
                        f"{content_type.split(';')[0] or 'unknown'} from {final_host}",
                        0,
                        None,
                    )

                destination.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                try:
                    with destination.open("wb") as handle:
                        handle.write(first)
                        written = len(first)
                        for piece in iterator:
                            if not piece:
                                continue
                            handle.write(piece)
                            written += len(piece)
                            if written > self.max_bytes:
                                raise IOError("file exceeded size limit")
                except Exception:
                    destination.unlink(missing_ok=True)
                    return "http_error", "download aborted (size limit)", 0, None
                return "saved", "", written, None
        except requests.RequestException as exc:
            return "error", str(exc)[:120], 0, None

    def _pdf_from_landing(
        self, head: bytes, base_url: str, iterator
    ) -> Optional[str]:
        """Look for a ``citation_pdf_url`` hint inside an HTML landing page."""
        if not head:
            return None
        from urllib.parse import urljoin

        limit = 400 * 1024
        buffer = bytearray(head)
        try:
            for piece in iterator:
                if not piece:
                    continue
                buffer.extend(piece)
                if len(buffer) > limit:
                    break
        except Exception:
            pass
        try:
            text = buffer.decode("utf-8", errors="ignore")
        except Exception:
            return None
        for pattern in (
            r'citation_pdf_url"\s+content="([^"]+)"',
            r'content="([^"]+)"\s+name="citation_pdf_url"',
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = urljoin(base_url, match.group(1))
                host = urlparse(candidate).netloc.lower()
                if host and host not in self._blocked_hosts:
                    return candidate
        return None

    def fetch(
        self, papers: Iterable[Paper], log=None, include_closed: bool = False
    ) -> DownloadReport:
        report = DownloadReport()
        todo = [p for p in papers if p.doi]
        for index, paper in enumerate(todo, start=1):
            if not include_closed and (paper.oa_status or "") not in _STATUS_OPEN:
                report.add(
                    DownloadResult(
                        paper.doi,
                        "closed",
                        f"oa_status={paper.oa_status or 'unknown'}",
                    )
                )
                continue

            links = self.candidate_links(paper)
            if not links:
                report.add(DownloadResult(paper.doi, "no_link", "no open-access copy"))
                if log:
                    log(f"[{index}/{len(todo)}] {paper.doi} no open-access copy")
                continue

            path = target_path(paper, self.root, self._used_paths)
            saved = False
            last_status, last_detail = "error", "no usable link"
            for url in links:
                status, detail, size, retry_url = self._try_download(url, path)
                if retry_url:
                    status, detail, size, retry_url = self._try_download(
                        retry_url, path, allow_landing=False
                    )
                last_status, last_detail = status, detail
                if status == "saved":
                    report.add(DownloadResult(paper.doi, "saved", detail, str(path), size))
                    if log:
                        log(f"[{index}/{len(todo)}] saved {path.name} ({size // 1024} KB)")
                    saved = True
                    break
            if not saved:
                report.add(DownloadResult(paper.doi, last_status, last_detail))
                if log:
                    log(f"[{index}/{len(todo)}] {last_status}: {paper.doi} ({last_detail})")
            if self.pause:
                time.sleep(self.pause)
        return report