"""Offline tests for PDF naming and reference exports."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pf.models import Paper
from pf.pdf import first_author_surname, safe_name, target_path
from pf.refs import write_bibtex, write_ris


class TestSafeName(unittest.TestCase):
    def test_strips_illegal_characters(self):
        self.assertEqual(safe_name('A/B: "C"?'), "A B C")

    def test_collapses_whitespace(self):
        self.assertEqual(safe_name("Thin   Solid\nFilms"), "Thin Solid Films")

    def test_falls_back_when_empty(self):
        self.assertEqual(safe_name("", fallback="no journal"), "no journal")

    def test_truncates(self):
        self.assertLessEqual(len(safe_name("x" * 200)), 120)


class TestAuthorSurname(unittest.TestCase):
    def test_last_word_of_display_name(self):
        paper = Paper(authors=["Ming-Show Wong"])
        self.assertEqual(first_author_surname(paper), "Wong")

    def test_family_name_comma_format(self):
        paper = Paper(authors=["Wong, Ming-Show"])
        self.assertEqual(first_author_surname(paper), "Wong")

    def test_no_authors(self):
        self.assertEqual(first_author_surname(Paper()), "Unknown")

    def test_non_ascii_preserved(self):
        paper = Paper(authors=["Κουτσουπίδου Α."])
        self.assertEqual(first_author_surname(paper), "Α")


class TestTargetPath(unittest.TestCase):
    def test_year_directory_and_stem(self):
        paper = Paper(
            authors=["Jane Smith"], year="2024", journal="Thin Solid Films"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = target_path(paper, Path(tmp), set())
            self.assertEqual(path.parent.name, "2024")
            self.assertEqual(path.name, "Smith_2024_Thin Solid Films.pdf")

    def test_missing_year_uses_unknown(self):
        paper = Paper(authors=["Jane Smith"], year="", journal="J. Appl. Phys.")
        with tempfile.TemporaryDirectory() as tmp:
            path = target_path(paper, Path(tmp), set())
            self.assertEqual(path.parent.name, "unknown")

    def test_collision_gets_suffix(self):
        paper = Paper(authors=["Jane Smith"], year="2024", journal="Nature")
        used: set = set()
        with tempfile.TemporaryDirectory() as tmp:
            first = target_path(paper, Path(tmp), used)
            second = target_path(paper, Path(tmp), used)
            self.assertNotEqual(first, second)
            self.assertIn("(2)", second.name)


class TestReferenceExport(unittest.TestCase):
    def setUp(self):
        self.papers = [
            Paper(
                doi="10.1000/abc",
                title="Transparent conducting oxide films",
                authors=["Jane Smith", "Li Wei"],
                year="2020",
                journal="Thin Solid Films",
                volume="700",
                pages="137-145",
                abstract="An abstract.",
                keywords=["TCO", "AZO"],
                oa_status="closed",
                channel="TCO",
                url="https://doi.org/10.1000/abc",
            )
        ]

    def test_ris_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.ris"
            count = write_ris(self.papers, path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(count, 1)
            self.assertIn("TY  - JOUR", text)
            self.assertIn("TI  - Transparent conducting oxide films", text)
            self.assertIn("AU  - Jane Smith", text)
            self.assertIn("AU  - Li Wei", text)
            self.assertIn("SP  - 137", text)
            self.assertIn("EP  - 145", text)
            self.assertIn("DO  - 10.1000/abc", text)
            self.assertTrue(text.strip().endswith("ER  -"))

    def test_ris_skips_records_without_doi(self):
        papers = [Paper(title="No DOI here")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.ris"
            self.assertEqual(write_ris(papers, path), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_bibtex_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.bib"
            count = write_bibtex(self.papers, path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(count, 1)
            self.assertIn("@article{smith2020_1,", text)
            self.assertIn("author = {Jane Smith and Li Wei}", text)
            self.assertIn("journal = {Thin Solid Films}", text)
            self.assertIn("doi = {10.1000/abc}", text)

    def test_bibtex_escapes_ampersand(self):
        papers = [Paper(doi="10.1/x", title="Oxide & films", authors=["A B"], year="2020")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.bib"
            write_bibtex(papers, path)
            self.assertIn(r"Oxide \& films", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

class TestBrowserHelpers(unittest.TestCase):
    def test_hard_challenge_detection(self):
        from pf.browser import _is_hard_challenge

        self.assertTrue(_is_hard_challenge("pubs.rsc.org"))
        self.assertTrue(_is_hard_challenge("www.sciencedirect.com"))
        self.assertFalse(_is_hard_challenge("www.nature.com"))
        self.assertFalse(_is_hard_challenge("example.com"))

    def test_bot_marker_detection(self):
        from pf.browser import _looks_blocked

        self.assertTrue(_looks_blocked("Just a moment...", ""))
        self.assertTrue(_looks_blocked("", "please complete the captcha"))
        self.assertTrue(_looks_blocked("Access Denied", ""))
        self.assertFalse(_looks_blocked("A real article title", "<html>body</html>"))

    def test_chromium_env_prepends_lib_dir(self):
        import sys

        from pf.browser import chromium_env

        env = chromium_env()
        self.assertIn(str(Path(sys.prefix) / "lib"), env.get("LD_LIBRARY_PATH", "").split(":"))
