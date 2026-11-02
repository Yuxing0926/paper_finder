"""Reference-manager exports (RIS and BibTeX).

Used for papers whose full text cannot be fetched automatically: the file is
imported into Zotero or EndNote, which then resolves the PDF through an
institutional subscription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import Paper

RIS_TYPE = "JOUR"


def _ris_lines(paper: Paper) -> List[str]:
    lines: List[str] = [f"TY  - {RIS_TYPE}"]
    if paper.title:
        lines.append(f"TI  - {paper.title}")
    for author in paper.authors:
        if author:
            lines.append(f"AU  - {author}")
    if paper.year:
        lines.append(f"PY  - {paper.year}")
    if paper.journal:
        lines.append(f"T2  - {paper.journal}")
    if paper.volume:
        lines.append(f"VL  - {paper.volume}")
    if paper.pages:
        if "-" in paper.pages:
            start, _, end = paper.pages.partition("-")
            lines.append(f"SP  - {start.strip()}")
            lines.append(f"EP  - {end.strip()}")
        else:
            lines.append(f"SP  - {paper.pages.strip()}")
    if paper.abstract:
        lines.append(f"AB  - {paper.abstract}")
    if paper.keywords:
        for keyword in paper.keywords:
            lines.append(f"KW  - {keyword}")
    if paper.doi:
        lines.append(f"DO  - {paper.doi}")
    if paper.url:
        lines.append(f"UR  - {paper.url}")
    if paper.openalex_id:
        lines.append(f"AN  - {paper.openalex_id}")
    status = paper.oa_status or "unknown"
    lines.append(f"N1  - oa_status: {status}; channel: {paper.channel or 'n/a'}")
    lines.append("ER  - ")
    return lines


def write_ris(papers: Iterable[Paper], path: Path | str) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [p for p in papers if p.doi]
    with target.open("w", encoding="utf-8") as handle:
        for paper in rows:
            handle.write("\n".join(_ris_lines(paper)) + "\n\n")
    return len(rows)


def _bib_escape(text: str) -> str:
    return (text or "").replace("{", "\\{").replace("}", "\\}").replace("&", "\\&")


def _bib_key(paper: Paper, index: int) -> str:
    surname = "anon"
    if paper.authors:
        first = paper.authors[0]
        surname = first.split(",")[0] if "," in first else first.split()[-1]
    surname = "".join(ch for ch in surname if ch.isalnum()) or "anon"
    year = paper.year if paper.year.isdigit() else "0000"
    return f"{surname.lower()}{year}_{index}"


def write_bibtex(papers: Iterable[Paper], path: Path | str) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [p for p in papers if p.doi]
    with target.open("w", encoding="utf-8") as handle:
        for index, paper in enumerate(rows, start=1):
            fields: List[str] = []
            if paper.title:
                fields.append(f"  title = {{{_bib_escape(paper.title)}}}")
            if paper.authors:
                joined = " and ".join(_bib_escape(a) for a in paper.authors if a)
                fields.append(f"  author = {{{joined}}}")
            if paper.journal:
                fields.append(f"  journal = {{{_bib_escape(paper.journal)}}}")
            if paper.year:
                fields.append(f"  year = {{{paper.year}}}")
            if paper.volume:
                fields.append(f"  volume = {{{_bib_escape(paper.volume)}}}")
            if paper.pages:
                fields.append(f"  pages = {{{_bib_escape(paper.pages)}}}")
            if paper.doi:
                fields.append(f"  doi = {{{paper.doi}}}")
            if paper.url:
                fields.append(f"  url = {{{paper.url}}}")
            if paper.abstract:
                fields.append(f"  abstract = {{{_bib_escape(paper.abstract)}}}")
            if paper.keywords:
                fields.append(f"  keywords = {{{_bib_escape(', '.join(paper.keywords))}}}")
            body = ",\n".join(fields)
            handle.write(f"@article{{{_bib_key(paper, index)},\n{body}\n}}\n\n")
    return len(rows)