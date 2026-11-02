"""Headless-browser PDF downloader.

Plain HTTP fails on most publishers because they gate the file behind a
JavaScript check or reject datacentre IPs outright. A real Chromium session
clears those checks and can also follow the download prompt that publishers
emit instead of streaming the file.

The browser is only used for the *fetch*. Discovery still goes through
Crossref/OpenAlex, and links come from Unpaywall, so nothing here guesses URLs.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse

from .models import Paper
from .pdf import (
    REDIRECTOR_HOSTS,
    DownloadReport,
    DownloadResult,
    PdfFetcher,
    target_path,
)

try:  # pragma: no cover - import guard
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception
    PlaywrightTimeout = Exception


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# The full Chromium build plus these flags clears the checks that reject the
# lightweight headless shell. Measured: this is what makes MDPI work.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""

BOT_MARKERS = (
    "just a moment",
    "not a bot",
    "captcha",
    "access denied",
    "checking your browser",
    "attention required",
    "are you a robot",
    "verify you are human",
    "bot manager",
)

# Give a JavaScript challenge this long to clear before calling it blocked.
CHALLENGE_WAIT_MS = 3000

# Hosts measured to present a challenge that never clears without a human.
# Waiting on them just wastes time, so they are skipped after a short probe.
HARD_CHALLENGE_HOSTS = (
    "pubs.rsc.org",
    "pubs.acs.org",
    "pubs.aip.org",
    "iopscience.iop.org",
    "www.sciencedirect.com",
    "linkinghub.elsevier.com",
    "www.tandfonline.com",
    "pmc.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "validate.perfdrive.com",
    "hal.science",
    "www.jove.com",
    "ieeexplore.ieee.org",
)

HARD_CHALLENGE_WAIT_MS = 2000


def _is_hard_challenge(host: str) -> bool:
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in HARD_CHALLENGE_HOSTS)


def chromium_env() -> Dict[str, str]:
    """Environment for the browser process.

    The conda-installed shared libraries (libatk, libgbm, ...) live under the
    environment prefix, which the dynamic loader does not search by default.
    """
    env = dict(os.environ)
    lib_dir = Path(sys.prefix) / "lib"
    existing = env.get("LD_LIBRARY_PATH", "")
    parts = [p for p in existing.split(":") if p]
    if lib_dir.exists() and str(lib_dir) not in parts:
        parts.insert(0, str(lib_dir))
    if parts:
        env["LD_LIBRARY_PATH"] = ":".join(parts)
    return env


def browser_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def full_chromium_available() -> bool:
    """True when the full Chromium build is installed, not just headless-shell."""
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.exists():
        return False
    for entry in cache.iterdir():
        if not entry.is_dir() or not entry.name.startswith("chromium-"):
            continue
        for sub in ("chrome-linux64", "chrome-linux"):
            if (entry / sub / "chrome").exists():
                return True
    return False


def _looks_blocked(title: str, body: str) -> bool:
    haystack = f"{title}\n{body[:4000]}".lower()
    return any(marker in haystack for marker in BOT_MARKERS)


@dataclass
class BrowserFetcher:
    root: Path = Path("output/pdfs")
    email: str = ""
    headless: bool = True
    timeout_ms: int = 8000
    challenge_wait_ms: int = CHALLENGE_WAIT_MS
    pause: float = 1.0
    channel: Optional[str] = "chromium"
    blocked_hosts: set = field(default_factory=set)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        # Unpaywall rejects requests without an email (HTTP 422), which would
        # silently turn every paper into "no open-access copy".
        self._helper = PdfFetcher(root=self.root, email=self.email)
        self._used_paths: set = set()
        if self.channel and not full_chromium_available():
            self.channel = None

    def _new_context(self, browser):
        context = browser.new_context(
            accept_downloads=True,
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Hong_Kong",
            viewport={"width": 1440, "height": 900},
        )
        context.add_init_script(STEALTH_SCRIPT)
        return context

    def _already_saved(self, paper) -> Optional[Path]:
        """Return an existing file for this paper, so a rerun resumes."""
        year = paper.year if paper.year.isdigit() else "unknown"
        from .pdf import first_author_surname, safe_name

        stem = (
            f"{first_author_surname(paper)}_{year}_"
            f"{safe_name(paper.journal, 'no journal', 60)}"
        )
        base = self.root / year / f"{stem}.pdf"
        if base.exists() and base.stat().st_size > 0:
            self._used_paths.add(str(base).lower())
            return base
        for index in range(2, 50):
            candidate = self.root / year / f"{stem} ({index}).pdf"
            if candidate.exists() and candidate.stat().st_size > 0:
                self._used_paths.add(str(candidate).lower())
                return candidate
        return None

    def _save(self, download, destination: Path) -> tuple[str, int]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(destination))
        data_start = destination.read_bytes()[:4]
        if data_start != b"%PDF":
            destination.unlink(missing_ok=True)
            return "not_pdf", 0
        return "saved", destination.stat().st_size

    def _attempt(self, context, url: str, destination: Path) -> tuple[str, str, int]:
        """Fetch one URL, then classify the outcome honestly.

        A single page is used for both phases: a PDF that is served inline is
        caught by the route handler, a PDF that is offered as a download is
        caught by the download event, and anything else leaves the page loaded
        so its HTML can be inspected without navigating twice.
        """
        host = urlparse(url).netloc.lower()
        if host in self.blocked_hosts:
            return "blocked", f"{host} rejects automated downloads", 0

        budget = (
            HARD_CHALLENGE_WAIT_MS if _is_hard_challenge(host) else self.timeout_ms
        )
        captured: Dict[str, object] = {}

        page = context.new_page()

        def on_route(route):
            if captured.get("data") is not None:
                try:
                    route.abort()
                except Exception:
                    pass
                return
            try:
                response = route.fetch()
                content_type = (response.headers.get("content-type") or "").lower()
                if "pdf" in content_type:
                    body = response.body()
                    if body[:4] == b"%PDF":
                        captured["data"] = body
                        try:
                            route.fulfill(
                                status=200,
                                headers={"content-type": "application/pdf"},
                                body=body,
                            )
                        except Exception:
                            pass
                        return
                route.fulfill(response=response)
            except Exception:
                try:
                    route.abort()
                except Exception:
                    pass

        def on_download(download):
            if captured.get("data") is not None:
                return
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                download.save_as(str(destination))
                data = destination.read_bytes()
                if data[:4] == b"%PDF":
                    captured["data"] = data
                else:
                    destination.unlink(missing_ok=True)
            except Exception:
                pass

        page.route("**/*", on_route)
        page.on("download", on_download)

        try:
            try:
                page.goto(url, timeout=budget, wait_until="commit")
            except Exception:
                pass

            deadline = time.time() + budget / 1000
            while time.time() < deadline and captured.get("data") is None:
                try:
                    page.wait_for_timeout(150)
                except Exception:
                    break

            data = captured.get("data")
            if isinstance(data, bytes):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                return "saved", "", len(data)

            # Nothing downloaded: work out why from the page already loaded.
            try:
                title = page.title()
                body = page.content()
            except Exception:
                title, body = "", ""

            if _looks_blocked(title, body):
                final = urlparse(page.url).netloc.lower()
                if final not in REDIRECTOR_HOSTS:
                    self.blocked_hosts.add(final)
                return "blocked", f"bot challenge from {final}", 0

            candidate = self._pdf_link_in_page(page, url)
            if candidate and candidate != url:
                try:
                    follow = min(budget, 6000)
                    with page.expect_download(timeout=follow) as pending:
                        try:
                            page.goto(candidate, timeout=follow, wait_until="commit")
                        except Exception as exc:
                            if "download is starting" not in str(exc).lower():
                                pass
                    download = pending.value
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    download.save_as(str(destination))
                    blob = destination.read_bytes()
                    if blob[:4] == b"%PDF":
                        return "saved", "", len(blob)
                    destination.unlink(missing_ok=True)
                except Exception:
                    pass

            final = urlparse(page.url).netloc.lower()
            return (
                "not_pdf",
                f"landing page on {final or 'unknown host'} without a downloadable file",
                0,
            )
        except Exception as exc:  # noqa: BLE001
            return "error", str(exc)[:120], 0
        finally:
            try:
                page.unroute("**/*", on_route)
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass

    @staticmethod
    def _pdf_link_in_page(page, base_url: str) -> Optional[str]:
        from urllib.parse import urljoin

        selectors = [
            'meta[name="citation_pdf_url"]',
        ]
        for selector in selectors:
            try:
                handle = page.query_selector(selector)
                if not handle:
                    continue
                value = handle.get_attribute("content")
                if value:
                    return urljoin(base_url, value)
            except Exception:
                continue
        try:
            links = page.eval_on_selector_all(
                "a",
                "els => els.map(e => e.href).filter(h => h && "
                "(h.includes('pdfdirect') || h.includes('articlepdf') || "
                "h.endsWith('.pdf') || h.includes('/pdf/')))",
            )
        except Exception:
            return None
        for link in links or []:
            lowered = link.lower()
            if any(skip in lowered for skip in ("supplement", "suppl", "supporting")):
                continue
            return link
        return None

    def fetch(
        self,
        papers: Iterable[Paper],
        log=None,
        include_closed: bool = False,
        oa_only: bool = True,
    ) -> DownloadReport:
        from .pdf import _STATUS_OPEN

        report = DownloadReport()
        todo = [p for p in papers if p.doi]
        if sync_playwright is None:
            raise RuntimeError(
                "playwright is not installed; run: pip install playwright && "
                "python -m playwright install chromium"
            )

        with sync_playwright() as p:
            launch_kwargs = {
                "headless": self.headless,
                "env": chromium_env(),
                "args": LAUNCH_ARGS,
            }
            if self.channel:
                launch_kwargs["channel"] = self.channel
            browser = p.chromium.launch(**launch_kwargs)
            context = self._new_context(browser)
            try:
                for index, paper in enumerate(todo, start=1):
                    if oa_only and not include_closed:
                        if (paper.oa_status or "") not in _STATUS_OPEN:
                            report.add(
                                DownloadResult(
                                    paper.doi,
                                    "closed",
                                    f"oa_status={paper.oa_status or 'unknown'}",
                                )
                            )
                            continue

                    existing = self._already_saved(paper)
                    if existing is not None:
                        report.add(
                            DownloadResult(
                                paper.doi,
                                "saved",
                                "already on disk",
                                str(existing),
                                existing.stat().st_size,
                            )
                        )
                        if log:
                            log(f"[{index}/{len(todo)}] skip (exists) {existing.name}")
                        continue

                    links = self._helper.candidate_links(paper)
                    if not links:
                        report.add(
                            DownloadResult(paper.doi, "no_link", "no open-access copy")
                        )
                        if log:
                            log(f"[{index}/{len(todo)}] {paper.doi} no open-access copy")
                        continue

                    path = target_path(paper, self.root, self._used_paths)
                    paper_started = time.time()
                    saved = False
                    last = ("error", "no usable link", 0)
                    for url in links:
                        status, detail, size = self._attempt(context, url, path)
                        last = (status, detail, size)
                        if status == "saved":
                            report.add(
                                DownloadResult(paper.doi, "saved", detail, str(path), size)
                            )
                            if log:
                                log(
                                    f"[{index}/{len(todo)}] saved {path.name} "
                                    f"({size // 1024} KB) in "
                                    f"{time.time() - paper_started:.1f}s"
                                )
                            saved = True
                            break
                        if status == "blocked" and "challenge" in detail:
                            break

                    if not saved:
                        status, detail, _ = last
                        report.add(DownloadResult(paper.doi, status, detail))
                        if log:
                            log(
                                f"[{index}/{len(todo)}] {status}: {paper.doi} "
                                f"({detail}) in {time.time() - paper_started:.1f}s"
                            )

                    if self.pause:
                        time.sleep(self.pause)
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass
        return report