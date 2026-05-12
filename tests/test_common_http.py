from __future__ import annotations

import io
import unittest
import urllib.error
import urllib.request

from ingestion.common import http


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def getcode(self) -> int:
        return self.status

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


class CommonHttpTests(unittest.TestCase):
    def test_request_with_retries_returns_success_payload(self):
        request = urllib.request.Request("https://example.test/source")

        outcome, payload = http.request_with_retries(
            request,
            timeout_seconds=10,
            opener=lambda _request, *, timeout: FakeResponse(200, b"ok"),
            response_reader=lambda response: response.read(),
        )

        self.assertEqual(outcome.status, http.STATUS_SUCCESS)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.http_status, 200)
        self.assertEqual(payload, b"ok")

    def test_request_with_retries_classifies_not_ready_status(self):
        request = urllib.request.Request("https://example.test/source")

        def opener(_request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                404,
                "not found",
                hdrs=None,
                fp=None,
            )

        outcome, payload = http.request_with_retries(
            request,
            timeout_seconds=10,
            not_ready_status_codes=frozenset({404}),
            opener=opener,
        )

        self.assertEqual(outcome.status, http.STATUS_NOT_READY)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.http_status, 404)
        self.assertIsNone(payload)

    def test_request_with_retries_retries_transient_status(self):
        request = urllib.request.Request("https://example.test/source")
        calls = []

        def opener(_request, *, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    500,
                    "server error",
                    hdrs=None,
                    fp=None,
                )
            return FakeResponse(200, b"ok")

        outcome, payload = http.request_with_retries(
            request,
            timeout_seconds=10,
            retry_backoff_seconds=0,
            opener=opener,
            response_reader=lambda response: response.read(),
        )

        self.assertEqual(outcome.status, http.STATUS_SUCCESS)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(payload, b"ok")
        self.assertEqual(len(calls), 2)

    def test_request_with_retries_returns_exhausted_transient(self):
        request = urllib.request.Request("https://example.test/source")

        def opener(_request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "unavailable",
                hdrs=None,
                fp=None,
            )

        outcome, payload = http.request_with_retries(
            request,
            timeout_seconds=10,
            max_attempts=2,
            retry_backoff_seconds=0,
            opener=opener,
        )

        self.assertEqual(outcome.status, http.STATUS_TRANSIENT)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(outcome.http_status, 503)
        self.assertIsNone(payload)

    def test_request_with_retries_classifies_fatal_status(self):
        request = urllib.request.Request("https://example.test/source")

        def opener(_request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "unauthorized",
                hdrs=None,
                fp=None,
            )

        outcome, payload = http.request_with_retries(
            request,
            timeout_seconds=10,
            opener=opener,
        )

        self.assertEqual(outcome.status, http.STATUS_FATAL)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.http_status, 401)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
