"""Command line entrypoint.

Typical use::

    python -m pf --all -o output/tco_all.csv
    python -m pf --channel TCO -o output/tco.csv
    python -m pf "transparent conductive oxide" --must "transparent|tco" "conduc*"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from . import __version__
from .export import read_csv, write_csv
from .filters import auto_groups, is_pv_related
from .models import Paper
from .refs import write_bibtex, write_ris
from .sources.crossref import Crossref
from .sources.openalex import OpenAlex, OpenAlexBudgetError, OpenAlexError
from .store import DEFAULT_PATH as DEFAULT_CACHE, Store

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_config(path: Optional[Path]) -> dict:
    candidate = Path(path) if path else DEFAULT_CONFIG
    if not candidate.exists():
        return {}
    with candidate.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pf",
        description="Collect journal metadata for a keyword set and export CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", nargs="*", help="free-text query (omit when using --all/--channel)")
    parser.add_argument("--all", action="store_true", help="run every channel in config.yaml")
    parser.add_argument("--channel", action="append", default=[], help="run one named channel (repeatable)")
    parser.add_argument(
        "--must",
        action="append",
        nargs="+",
        default=[],
        metavar="GROUP",
        help="required keyword group; terms inside a group are ORed with |, groups are ANDed",
    )
    parser.add_argument("--since", type=int, help="earliest publication year")
    parser.add_argument("--depth", type=int, help="max candidates scanned per query")
    parser.add_argument("--doc-type", help="Crossref type filter (default journal-article)")
    parser.add_argument("--match", choices=["title", "title_abstract"], help="field used for matching")
    parser.add_argument("-o", "--output", help="output CSV path")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--cache", help="path to the SQLite cache")
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not write the cache")
    parser.add_argument("--dry-run", action="store_true", help="discover only; do not call OpenAlex")
    parser.add_argument(
        "--download-pdfs",
        action="store_true",
        help="after writing the CSV, download open-access PDFs in a real browser",
    )
    parser.add_argument(
        "--pdf-dir",
        help="directory for downloaded PDFs (default output/pdfs)",
    )
    parser.add_argument(
        "--pdf-limit",
        type=int,
        help="stop after this many download attempts",
    )
    parser.add_argument(
        "--pdf-engine",
        choices=["browser", "http"],
        default="browser",
        help="browser clears publisher JS checks; http is faster but mostly blocked",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="run the browser with a visible window (helps with captchas)",
    )
    parser.add_argument(
        "--from-csv",
        metavar="PATH",
        help="skip discovery and download PDFs / write references for an existing CSV",
    )
    parser.add_argument(
        "--refs",
        action="store_true",
        help="write RIS and BibTeX files next to the CSV",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="also probe papers marked closed (they almost never have a free copy)",
    )
    parser.add_argument(
        "--version", action="version", version=f"paper_finder {__version__}"
    )
    return parser


def parse_must_specs(specs: Sequence[Sequence[str]]) -> List[List[str]]:
    """Turn CLI ``--must`` arguments into groups of terms."""
    groups: List[List[str]] = []
    for spec in specs:
        for chunk in spec:
            terms = [term.strip() for term in chunk.split("|") if term.strip()]
            if terms:
                groups.append(terms)
    return groups


def resolve_channels(args, config: dict) -> List[tuple[str, list]]:
    """Return ``[(channel_name, [query_spec, ...]), ...]`` for this run."""
    configured: Dict[str, list] = config.get("channels") or {}

    if args.all:
        return [(name, spec) for name, spec in configured.items()]
    if args.channel:
        selected = []
        for name in args.channel:
            if name not in configured:
                raise SystemExit(
                    f"Unknown channel {name!r}; available: {sorted(configured)}"
                )
            selected.append((name, configured[name]))
        return selected
    return []


def discover_papers(
    args,
    config: dict,
    channels: List[tuple[str, list]],
    defaults: dict,
    crossref: Crossref,
) -> Dict[str, dict]:
    """Run the discovery layer for every channel and return DOI -> record."""
    since = args.since if args.since is not None else defaults.get("since")
    depth = args.depth if args.depth is not None else defaults.get("depth", 4000)
    doc_type = args.doc_type or defaults.get("doc_type", "journal-article")
    match_field = args.match or defaults.get("match", "title")

    collected: Dict[str, dict] = {}

    def absorb(records: Dict[str, dict]) -> int:
        added = 0
        for doi, record in records.items():
            if doi not in collected:
                collected[doi] = record
                added += 1
        return added

    for name, specs in channels:
        for spec in specs:
            query = spec.get("query", "")
            must = spec.get("must") or []
            # A channel entry may widen matching to the abstract, which is how
            # deposition techniques are usually reported.
            spec_match = spec.get("match") or match_field
            spec_depth = int(spec.get("depth") or depth)
            log(f"[{name}] query={query!r} depth={spec_depth} since={since} match={spec_match}")
            records = crossref.discover(
                query=query,
                must=must,
                channel=name,
                since=since,
                depth=spec_depth,
                doc_type=doc_type,
                match_field=spec_match,
                log=log,
            )
            added = absorb(records)
            log(f"[{name}] kept {len(records)} for this query ({added} new)")

    if not channels:
        query = " ".join(args.query).strip()
        if not query:
            raise SystemExit("Provide a query, or use --all / --channel.")
        must = parse_must_specs(args.must) or auto_groups(query)
        log(f"[custom] query={query!r} must={must} depth={depth}")
        records = crossref.discover(
            query=query,
            must=must,
            channel="custom",
            since=since,
            depth=depth,
            doc_type=doc_type,
            match_field=match_field,
            log=log,
        )
        absorb(records)

    return collected


def build_papers(
    discovered: Dict[str, dict],
    works: Dict[str, dict],
    cached: Dict[str, Paper],
    pv_keywords: List[str],
    since: Optional[int] = None,
) -> List[Paper]:
    """Merge Crossref discovery, OpenAlex metadata and cache into Papers."""
    papers: List[Paper] = []
    for doi, record in discovered.items():
        if doi in works:
            paper = OpenAlex.to_paper(works[doi])
        elif doi in cached:
            paper = cached[doi]
        else:
            paper = Paper(
                doi=doi,
                title=record.get("title", ""),
                year=record.get("year", ""),
                journal=record.get("journal", ""),
                abstract=record.get("abstract", ""),
                url=record.get("url", f"https://doi.org/{doi}"),
                source="crossref",
            )

        if not paper.title:
            paper.title = record.get("title", "")
        if not paper.year:
            paper.year = record.get("year", "")
        if not paper.journal:
            paper.journal = record.get("journal", "")
        if not paper.abstract:
            paper.abstract = record.get("abstract", "")
        if not paper.url:
            paper.url = record.get("url", f"https://doi.org/{doi}")

        paper.channel = record.get("channel", paper.channel)
        if since and paper.year.isdigit() and int(paper.year) < since:
            continue
        paper.pv_related = is_pv_related(paper.title, paper.abstract, pv_keywords)
        papers.append(paper)

    papers.sort(key=lambda p: (p.channel, p.year or "0", p.title.lower()), reverse=False)
    return papers


def report(papers: List[Paper]) -> None:
    total = len(papers)
    if not total:
        log("No papers collected.")
        return
    with_abstract = sum(1 for p in papers if len(p.abstract) > 80)
    with_affil = sum(
        1 for p in papers if any(any(a) for a in p.author_affiliations)
    )
    with_journal = sum(1 for p in papers if p.journal)
    pv = sum(1 for p in papers if p.pv_related)
    by_channel: Dict[str, int] = {}
    for paper in papers:
        by_channel[paper.channel] = by_channel.get(paper.channel, 0) + 1

    log("")
    log("=" * 58)
    log(f"papers            : {total}")
    log(f"  by channel      : {', '.join(f'{k}={v}' for k, v in sorted(by_channel.items()))}")
    log(f"  journal present : {with_journal} ({with_journal / total:.0%})")
    log(f"  abstract present: {with_abstract} ({with_abstract / total:.0%})")
    log(f"  affiliation     : {with_affil} ({with_affil / total:.0%})")
    log(f"  pv_related      : {pv} ({pv / total:.0%})")
    log("=" * 58)


def run_pdf_download(
    papers: List[Paper],
    root: Path,
    email: str,
    limit: Optional[int],
    include_closed: bool = False,
    engine: str = "browser",
    show_browser: bool = False,
) -> None:
    """Download open-access PDFs, defaulting to a real browser session."""
    from .pdf import PdfFetcher
    from .refs import write_bibtex, write_ris

    targets = papers[:limit] if limit else papers
    log(f"\ndownloading PDFs for {len(targets)} papers into {root} ...")

    if engine == "browser":
        from .browser import BrowserFetcher, browser_available, full_chromium_available

        if not browser_available():
            log("! playwright chromium is not installed; falling back to http")
            log("  install with: pip install playwright && python -m playwright install chromium")
            engine = "http"
        else:
            if not full_chromium_available():
                log("! only headless-shell found; some publishers will refuse")
                log("  run: python -m playwright install chromium")
            fetcher = BrowserFetcher(root=root, email=email, headless=not show_browser)
            report = fetcher.fetch(
                targets, log=log, include_closed=include_closed
            )

    if engine == "http":
        fetcher = PdfFetcher(email=email, root=root)
        report = fetcher.fetch(targets, log=log, include_closed=include_closed)

    counts = report.summary()
    log("")
    log("=" * 58)
    for status, count in sorted(counts.items()):
        log(f"  {status:12s}: {count}")
    log("=" * 58)

    needs_manual = [r for r in report.results if r.status != "saved"]
    if needs_manual:
        # Name the failure list after the PDF directory so separate runs
        # (pdfs, pdfs2, ...) do not overwrite each other's lists.
        stem = root.name
        miss_path = root.parent / f"{stem}_failed.csv"
        by_doi = {p.doi: p for p in targets}
        rows = [by_doi[r.doi] for r in needs_manual if r.doi in by_doi]
        write_csv(rows, miss_path)
        refs_dir = root.parent / "references"
        ris_count = write_ris(rows, refs_dir / f"{stem}_failed.ris")
        write_bibtex(rows, refs_dir / f"{stem}_failed.bib")
        log(f"\n{len(rows)} of {len(targets)} papers still need manual fetching")
        log(f"  ({len(report.closed)} are closed access)")
        log(f"  list  : {miss_path}")
        log(f"  RIS   : {refs_dir / f'{stem}_failed.ris'} ({ris_count} records)")
        log(f"  BibTeX: {refs_dir / f'{stem}_failed.bib'}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(Path(args.config) if args.config else None)
    defaults = config.get("defaults") or {}
    email = config.get("email", "")
    pv_keywords = list(config.get("pv_keywords") or [])
    pdf_root = Path(args.pdf_dir) if args.pdf_dir else Path("output/pdfs")

    if args.from_csv:
        source = Path(args.from_csv)
        if not source.exists():
            log(f"No such CSV: {source}")
            return 2
        papers = read_csv(source)
        log(f"loaded {len(papers)} papers from {source}")
        if args.refs:
            refs_dir = source.parent / "references"
            ris = write_ris(papers, refs_dir / f"{source.stem}.ris")
            bib = write_bibtex(papers, refs_dir / f"{source.stem}.bib")
            log(f"wrote {ris} RIS records to {refs_dir / f'{source.stem}.ris'}")
            log(f"wrote {bib} BibTeX records to {refs_dir / f'{source.stem}.bib'}")
        if args.download_pdfs:
            run_pdf_download(papers, pdf_root, email, args.pdf_limit,
                             args.include_closed, args.pdf_engine, args.show_browser)
        if not args.refs and not args.download_pdfs:
            log("nothing to do: pass --download-pdfs and/or --refs")
        return 0

    try:
        channels = resolve_channels(args, config)
    except SystemExit as exc:
        log(str(exc))
        return 2

    output = Path(args.output) if args.output else Path("output/papers.csv")
    cache_path = Path(args.cache) if args.cache else DEFAULT_CACHE
    use_cache = bool(defaults.get("cache", True)) and not args.no_cache
    effective_since = args.since if args.since is not None else defaults.get("since")
    effective_since = int(effective_since) if effective_since else None

    crossref = Crossref(
        email=email,
        page_size=int(defaults.get("page_size", 1000)),
    )

    log(f"paper_finder {__version__}")
    discovered = discover_papers(args, config, channels, defaults, crossref)
    log(f"\ndiscovery finished: {len(discovered)} unique DOIs "
        f"({crossref.requests_made} Crossref requests)")

    if args.dry_run:
        by_channel: Dict[str, int] = {}
        for record in discovered.values():
            key = record.get("channel", "custom")
            by_channel[key] = by_channel.get(key, 0) + 1
        log("")
        log("=" * 58)
        log(f"would collect     : {len(discovered)} unique DOIs")
        log(f"  by channel      : {', '.join(f'{k}={v}' for k, v in sorted(by_channel.items()))}")
        log("=" * 58)
        log("dry run: skipping OpenAlex enrichment")
        return 0

    doi_list = list(discovered.keys())
    cached: Dict[str, Paper] = {}
    if use_cache and doi_list:
        with Store(cache_path) as store:
            cached = store.get_many(doi_list)
        log(f"cache: {len(cached)} of {len(doi_list)} DOIs already known")

    pending = [d for d in doi_list if d not in cached]
    works: Dict[str, dict] = {}
    openalex = OpenAlex(
        email=email,
        batch_size=int(defaults.get("oa_batch", 100)),
        min_remaining_usd=float(defaults.get("min_remaining_usd", 0.02)),
    )
    if pending:
        log(f"enriching {len(pending)} DOIs via OpenAlex...")
        try:
            works = openalex.enrich(pending, log=log)
        except OpenAlexBudgetError as exc:
            log(f"! {exc}")
            log("! continuing with the metadata already collected")
        except OpenAlexError as exc:
            log(f"! OpenAlex failed: {exc}")
        log(
            f"OpenAlex: {len(works)} matched, "
            f"cost ${openalex.cost_usd:.4f}, "
            f"remaining ${openalex.remaining_usd if openalex.remaining_usd is not None else '?'}"
        )
    else:
        log("nothing to enrich; everything came from cache")

    papers = build_papers(discovered, works, cached, pv_keywords, since=effective_since)

    if use_cache and papers:
        with Store(cache_path) as store:
            written = store.upsert_many(papers)
        log(f"cache: stored {written} papers at {cache_path}")

    count = write_csv(papers, output)
    report(papers)
    log(f"\nwrote {count} rows to {output}")

    if args.refs:
        refs_dir = output.parent / "references"
        ris = write_ris(papers, refs_dir / f"{output.stem}.ris")
        bib = write_bibtex(papers, refs_dir / f"{output.stem}.bib")
        log(f"wrote {ris} RIS records to {refs_dir / f'{output.stem}.ris'}")
        log(f"wrote {bib} BibTeX records to {refs_dir / f'{output.stem}.bib'}")

    if args.download_pdfs:
        run_pdf_download(papers, pdf_root, email, args.pdf_limit,
                             args.include_closed, args.pdf_engine, args.show_browser)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())