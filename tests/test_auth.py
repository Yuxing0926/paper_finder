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
