"""Offline tests for the supplementary discovery sources."""

from __future__ import annotations

import unittest

from pf.sources.arxiv import Arxiv
from pf.sources.europepmc import EuropePmc


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>Magnetron sputtered ITO films</title>
    <summary>We grow ITO by magnetron sputtering.</summary>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00001v1"/>
    <arxiv:doi>10.1000/abc</arxiv:doi>
    <arxiv:journal_ref>Thin Solid Films 700 (2020)</arxiv:journal_ref>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2101.00002v1</id>
    <published>2021-02-02T00:00:00Z</published>
    <title>Another sputtered oxide</title>
    <summary>No DOI here.</summary>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00002v1"/>
  </entry>
</feed>
"""


class FakeResponse:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        return self.responses.pop(0) if self.responses else FakeResponse("", {})


class TestArxiv(unittest.TestCase):
    def test_parses_doi_and_pdf_link(self):
        client = Arxiv(pause=0, session=FakeSession([FakeResponse(text=ARXIV_XML)]))
        got = client.search("abs:test", "TCO_SPUTTER")
        self.assertIn("10.1000/abc", got)
        record = got["10.1000/abc"]
        self.assertEqual(record["year"], "2021")
        self.assertEqual(record["pdf_url"], "http://arxiv.org/pdf/2101.00001v1")
        self.assertEqual(record["channel"], "TCO_SPUTTER")

    def test_falls_back_to_arxiv_id_without_doi(self):
        client = Arxiv(pause=0, session=FakeSession([FakeResponse(text=ARXIV_XML)]))
        got = client.search("abs:test", "TCO_SPUTTER")
        self.assertIn("arxiv:2101.00002v1", got)

    def test_returns_empty_on_bad_xml(self):
        client = Arxiv(pause=0, retries=1, session=FakeSession([FakeResponse(text="<oops")]))
        self.assertEqual(client.search("abs:test", "TCO_SPUTTER"), {})


class TestEuropePmc(unittest.TestCase):
    def _payload(self):
        return {
            "hitCount": 2,
            "resultList": {
                "result": [
                    {
                        "doi": "10.1000/xyz",
                        "title": "Magnetron sputtered AZO",
                        "pubYear": "2020",
                        "journalInfo": {"journal": {"title": "Thin Solid Films"}},
                        "abstractText": "Films grown by magnetron sputtering.",
                    },
                    {
                        "pmid": "12345",
                        "title": "No DOI record",
                        "pubYear": "2019",
                    },
                ]
            },
        }

    def test_parses_fields(self):
        client = EuropePmc(pause=0, session=FakeSession([FakeResponse(payload=self._payload())]))
        got = client.search("test", "TCO_SPUTTER")
        self.assertIn("10.1000/xyz", got)
        self.assertEqual(got["10.1000/xyz"]["journal"], "Thin Solid Films")

    def test_falls_back_to_pmid(self):
        client = EuropePmc(pause=0, session=FakeSession([FakeResponse(payload=self._payload())]))
        got = client.search("test", "TCO_SPUTTER")
        self.assertIn("pmid:12345", got)

    def test_service_down_returns_empty_without_raising(self):
        # Europe PMC answers 503 constantly; a run must not die because of it.
        responses = [FakeResponse(status=503) for _ in range(4)]
        client = EuropePmc(pause=0, retries=4, session=FakeSession(responses))
        self.assertEqual(client.search("test", "TCO_SPUTTER"), {})


if __name__ == "__main__":
    unittest.main()
