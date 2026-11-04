"""The API key must reach every OpenAlex request, and never leak to git."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from pf.cli import resolve_api_key
from pf.sources.openalex import OpenAlex


class RecordingSession:
    """Stands in for requests.Session and records the params it receives."""

    def __init__(self):
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        return Response()


class Response:
    status_code = 200
    headers = {"X-RateLimit-Remaining-USD": "1.0"}

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [], "meta": {"count": 0}}


class TestKeyPropagation(unittest.TestCase):
    def test_key_sent_on_lookup(self):
        session = RecordingSession()
        client = OpenAlex(email="a@b.c", api_key="SECRET", session=session)
        client._request({"filter": "doi:10.1/x"})
        self.assertEqual(session.calls[0]["api_key"], "SECRET")

    def test_key_sent_on_discovery(self):
        session = RecordingSession()
        client = OpenAlex(email="a@b.c", api_key="SECRET", session=session)
        client.discover(["magnetron"], "TCO_SPUTTER", since=2015)
        self.assertEqual(session.calls[0]["api_key"], "SECRET")

    def test_email_sent_alongside_key(self):
        session = RecordingSession()
        client = OpenAlex(email="a@b.c", api_key="SECRET", session=session)
        client._request({"filter": "doi:10.1/x"})
        self.assertEqual(session.calls[0]["mailto"], "a@b.c")

    def test_no_key_means_no_param(self):
        session = RecordingSession()
        client = OpenAlex(email="a@b.c", session=session)
        client._request({"filter": "doi:10.1/x"})
        self.assertNotIn("api_key", session.calls[0])

    def test_caller_params_are_not_mutated(self):
        session = RecordingSession()
        client = OpenAlex(api_key="SECRET", session=session)
        params = {"filter": "doi:10.1/x"}
        client._request(params)
        self.assertNotIn("api_key", params)


class TestKeyResolution(unittest.TestCase):
    def test_environment_wins_over_config(self):
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "from-env"}):
            self.assertEqual(resolve_api_key({"openalex_api_key": "from-config"}), "from-env")

    def test_config_used_when_environment_empty(self):
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": ""}):
            self.assertEqual(resolve_api_key({"openalex_api_key": "from-config"}), "from-config")

    def test_empty_when_neither_present(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_api_key({}), "")


if __name__ == "__main__":
    unittest.main()


class TestPdfKeyAndLinks(unittest.TestCase):
    """Regressions for two bugs that made open-access PDFs look unavailable."""

    def _fetcher(self, api_key="SECRET"):
        from pf.pdf import PdfFetcher

        return PdfFetcher(email="a@b.c", api_key=api_key, root="/tmp/x")

    def test_openalex_lookup_sends_the_api_key(self):
        from pf.pdf import PdfFetcher

        sent = {}

        class Sess:
            headers = {}

            def get(self, url, params=None, timeout=None):
                sent.update(params or {})

                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"results": []}

                return R()

        fetcher = PdfFetcher(email="a@b.c", api_key="SECRET", root="/tmp/x")
        fetcher.api = Sess()
        fetcher._openalex_pdf("10.1/x")
        self.assertEqual(sent.get("api_key"), "SECRET")

    def test_open_access_oa_url_is_a_candidate(self):
        from pf.pdf import PdfFetcher

        class Sess:
            headers = {}

            def get(self, url, params=None, timeout=None):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "results": [
                                {
                                    "open_access": {
                                        "oa_url": "https://arxiv.org/pdf/1610.08807"
                                    },
                                    "best_oa_location": None,
                                    "locations": [],
                                }
                            ]
                        }

                return R()

        fetcher = PdfFetcher(email="a@b.c", api_key="K", root="/tmp/x")
        fetcher.api = Sess()
        urls = fetcher._openalex_pdf("10.48550/arxiv.1610.08807")
        self.assertIn("https://arxiv.org/pdf/1610.08807", urls)

    def test_oa_url_comes_before_location_pdf_urls(self):
        from pf.pdf import PdfFetcher

        class Sess:
            headers = {}

            def get(self, url, params=None, timeout=None):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "results": [
                                {
                                    "open_access": {"oa_url": "https://first/pdf"},
                                    "locations": [{"pdf_url": "https://second/pdf"}],
                                }
                            ]
                        }

                return R()

        fetcher = PdfFetcher(email="a@b.c", api_key="K", root="/tmp/x")
        fetcher.api = Sess()
        self.assertEqual(
            fetcher._openalex_pdf("10.1/x"), ["https://first/pdf", "https://second/pdf"]
        )

    def test_duplicate_urls_are_collapsed(self):
        from pf.pdf import PdfFetcher

        class Sess:
            headers = {}

            def get(self, url, params=None, timeout=None):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "results": [
                                {
                                    "open_access": {"oa_url": "https://same/pdf"},
                                    "best_oa_location": {"pdf_url": "https://same/pdf"},
                                    "locations": [{"pdf_url": "https://same/pdf"}],
                                }
                            ]
                        }

                return R()

        fetcher = PdfFetcher(email="a@b.c", api_key="K", root="/tmp/x")
        fetcher.api = Sess()
        self.assertEqual(fetcher._openalex_pdf("10.1/x"), ["https://same/pdf"])
