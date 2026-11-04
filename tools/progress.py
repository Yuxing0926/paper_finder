#!/usr/bin/env python3
"""Live progress for a running pdf download.

Usage:
    python tools/progress.py [LOG] [--total N] [--pdf-dir DIR]

Parses the log a download writes to stderr and prints a one-line summary:
papers attempted, PDFs saved, current rate, and an ETA. Safe to run while the
download is in progress; it only reads the log file and counts files on disk.

Run it in a watch loop for a live view:

    watch -n 5 python tools/progress.py /tmp/full609.log --total 609
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*)$", re.MULTILINE)
# Only real outcome lines count. An older build also wrote "[n/N] gave up after
# 45s: doi", which is a note, not a result -- counting it made the totals exceed
# the paper count.
OUTCOME = re.compile(r"^(saved|blocked|not_pdf|no_link|error|closed|skip)\b")


def parse(path: Path) -> dict:
    text = path.read_text(errors="ignore") if path.exists() else ""
    matches = LINE.findall(text)
    statuses: dict[str, int] = {}
    last_index = 0
    saved = 0
    for index, _total, body in matches:
        if not OUTCOME.match(body):
            continue
        last_index = max(last_index, int(index))
        if body.startswith("saved"):
            saved += 1
        key = body.split(":", 1)[0].split(" ", 1)[0]
        statuses[key] = statuses.get(key, 0) + 1
    attempted = sum(statuses.values())
    return {
        "attempted": attempted,
        "last_index": last_index,
        "saved": saved,
        "statuses": statuses,
        "text": text,
    }


def pdf_stats(directory: Path) -> tuple[int, int]:
    files = list(directory.rglob("*.pdf")) if directory.exists() else []
    return len(files), sum(f.stat().st_size for f in files)


def bar(done: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    filled = int(width * min(done / total, 1.0))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default="/tmp/full609.log")
    ap.add_argument("--total", type=int, default=0)
    ap.add_argument("--pdf-dir", default="output/pdfs3")
    args = ap.parse_args()

    log_path = Path(args.log)
    data = parse(log_path)
    files, size = pdf_stats(Path(args.pdf_dir))

    total = args.total
    if not total:
        match = re.search(r"downloading PDFs for (\d+) papers", data["text"])
        total = int(match.group(1)) if match else data["last_index"]

    done = data["attempted"]
    pct = (done / total * 100) if total else 0
    running = "running" if _alive() else "stopped"

    print(f"{bar(done, total)} {done}/{total}  {pct:5.1f}%   {running}")
    print(f"  pdfs on disk : {files}   {size / 1024 / 1024:.0f} MB")
    print(f"  saved        : {data['saved']}")
    if data["statuses"]:
        breakdown = ", ".join(
            f"{k}={v}" for k, v in sorted(data["statuses"].items(), key=lambda kv: -kv[1])
        )
        print(f"  outcomes     : {breakdown}")
    return 0


def _alive() -> bool:
    """True when a pf download process is running."""
    proc = Path("/proc")
    if not proc.exists():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode(errors="ignore")
        except OSError:
            continue
        if "pf" in cmdline and "--download-pdfs" in cmdline:
            return True
    return False


if __name__ == "__main__":
    sys.exit(main())