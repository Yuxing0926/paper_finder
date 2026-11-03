"""Offline tests for the OpenAlex discovery pass."""

from __future__ import annotations

import unittest

from pf.sources.openalex import OpenAlex, inverted_index_to_text


def _work(index: int) -> dict:
    return {
        "id": f"https://openalex.org/W{index}",
        "doi": f"https://doi.org/10.1/x{index}",
        "title": f"paper {index}",
        "publication_year": 2020,
    }


class FakeOpenAlex(OpenAlex):
    """Replaces the HTTP layer with a canned page-per-query mapping."""

    def __init__(self, pages_by_clause, counts=None):
        super().__init__(email="someone@example.com")
        self.pages_by_clause = pages_by_clause
        self.counts = counts or {}
        self.filters = []

    def _request(self, params):
        flt = params["filter"]
        self.filters.append(flt)
        clause = flt.split("title_and_abstract.search:")[1].split(",")[0]
        page = params["page"]
        pages = self.pages_by_clause.get(clause, {})
        return {
            "results": pages.get(page, []),
            "meta": {"count": self.counts.get(clause, 0)},
        }


class TestDiscovery(unittest.TestCase):
    def test_merges_clauses_and_dedupes(self):
        shared = [_work(i) for i in range(3)]
        fake = FakeOpenAlex(
            {
                "one": {1: [_work(0), _work(1), _work(2), _work(3)]},
                "two": {1: shared + [_work(4)]},
            },
            counts={"one": 4, "two": 4},
        )
        got = fake.discover(["one", "two"], "TCO_SPUTTER", since=2015)
        self.assertEqual(sorted(got), [f"10.1/x{i}" for i in range(5)])
        self.assertEqual(got["10.1/x0"]["channel"], "TCO_SPUTTER")

    def test_both_clauses_are_queried(self):
        fake = FakeOpenAlex(
            {"one": {1: [_work(0)]}, "two": {1: [_work(1)]}},
            counts={"one": 1, "two": 1},
        )
        fake.discover(["one", "two"], "TCO_SPUTTER", since=2015)
        self.assertEqual(len(fake.filters), 2)
        self.assertTrue(any("search:one" in f for f in fake.filters))
        self.assertTrue(any("search:two" in f for f in fake.filters))

    def test_since_is_applied_as_a_filter(self):
        fake = FakeOpenAlex({"one": {1: [_work(0)]}}, counts={"one": 1})
        fake.discover(["one"], "TCO_SPUTTER", since=2015)
        self.assertIn("from_publication_date:2015-01-01", fake.filters[0])

    def test_one_broad_clause_cannot_starve_the_others(self):
        # The first clause returns a full page forever; the second must still
        # get a turn even though the overall record cap is low.
        big = {p: [_work(p * 200 + i) for i in range(200)] for p in range(1, 30)}
        fake = FakeOpenAlex(
            {"broad": big, "narrow": {1: [_work(9000)]}},
            counts={"broad": 100000, "narrow": 1},
        )
        got = fake.discover(["broad", "narrow"], "TCO_SPUTTER", max_records=200)
        self.assertIn("10.1/x9000", got)

    def test_skips_records_without_a_doi(self):
        fake = FakeOpenAlex(
            {"one": {1: [{"id": "W1", "doi": None, "title": "no doi"}]}},
            counts={"one": 1},
        )
        self.assertEqual(fake.discover(["one"], "TCO_SPUTTER"), {})

    def test_stops_at_last_page(self):
        fake = FakeOpenAlex({"one": {1: [_work(0)], 2: []}}, counts={"one": 1})
        fake.discover(["one"], "TCO_SPUTTER")
        # A short page ends paging immediately.
        self.assertEqual(len(fake.filters), 1)


class TestInvertedIndex(unittest.TestCase):
    def test_rebuilds_word_order(self):
        index = {"Oxide": [1], "Transparent": [0], "films": [2]}
        self.assertEqual(inverted_index_to_text(index), "Transparent Oxide films")

    def test_handles_repeated_terms(self):
        index = {"the": [0, 3], "film": [1], "thin": [2]}
        self.assertEqual(inverted_index_to_text(index), "the film thin the")

    def test_empty(self):
        self.assertEqual(inverted_index_to_text(None), "")


if __name__ == "__main__":
    unittest.main()
