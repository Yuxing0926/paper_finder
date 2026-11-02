"""Offline tests for CSV round-tripping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pf.export import read_csv, write_csv
from pf.models import CSV_COLUMNS, Paper


class TestCsvRoundTrip(unittest.TestCase):
    def _sample(self) -> Paper:
        return Paper(
            doi="10.1000/abc",
            title="Transparent conducting oxide films",
            authors=["Jane Smith", "Li Wei", "Wang Wu"],
            author_affiliations=[
                ["Tsinghua University", "Peking University"],
                [],
                ["MIT"],
            ],
            year="2020",
            journal="Thin Solid Films",
            abstract="An abstract with, a comma and \"quotes\".",
            citations=42,
            oa_status="gold",
            url="https://doi.org/10.1000/abc",
            openalex_id="W123",
            source="crossref+openalex",
            volume="700",
            pages="137-145",
            keywords=["TCO", "AZO thin films"],
            channel="TCO",
            pv_related=True,
        )

    def test_header_matches_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_csv([self._sample()], path)
            header = path.read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertEqual(header.split(","), CSV_COLUMNS)

    def test_roundtrip_preserves_fields(self):
        original = self._sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_csv([original], path)
            restored = read_csv(path)[0]
        self.assertEqual(restored.doi, original.doi)
        self.assertEqual(restored.authors, original.authors)
        self.assertEqual(restored.author_affiliations, original.author_affiliations)
        self.assertEqual(restored.affiliations, original.affiliations)
        self.assertEqual(restored.citations, 42)
        self.assertEqual(restored.keywords, original.keywords)
        self.assertTrue(restored.pv_related)
        self.assertEqual(restored.abstract, original.abstract)

    def test_blank_affiliation_stays_aligned(self):
        # The middle author has no institution; the empty cell must survive and
        # not shift MIT onto author 2.
        paper = self._sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_csv([paper], path)
            restored = read_csv(path)[0]
        self.assertEqual(restored.author_affiliations[0], ["Tsinghua University", "Peking University"])
        self.assertEqual(restored.author_affiliations[1], [])
        self.assertEqual(restored.author_affiliations[2], ["MIT"])
        self.assertIn("Tsinghua University | Peking University; ; MIT", restored.affiliations)

    def test_empty_citations_becomes_none(self):
        paper = Paper(doi="10.1/x", title="T", authors=["A B"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_csv([paper], path)
            restored = read_csv(path)[0]
        self.assertIsNone(restored.citations)

    def test_multiple_rows(self):
        papers = [self._sample(), Paper(doi="10.1/y", title="Second", authors=["C D"])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            self.assertEqual(write_csv(papers, path), 2)
            self.assertEqual(len(read_csv(path)), 2)


if __name__ == "__main__":
    unittest.main()