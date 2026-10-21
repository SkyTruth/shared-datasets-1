"""Transport classifications exclude authentication and invalid evidence."""
import json

from google.api_core import exceptions as api_errors
from google.auth import exceptions as auth_errors
import pytest
from requests import exceptions as request_errors

from scripts.gcp_read_retry import classify


@pytest.mark.parametrize("error", [api_errors.ServiceUnavailable("unavailable"),
    api_errors.InternalServerError("server"), api_errors.TooManyRequests("limited"),
    auth_errors.TransportError("transport"), request_errors.Timeout("timeout"),
    auth_errors.RefreshError("Unable to acquire impersonated credentials", json.dumps({"error": {"code": 503}}))])
def test_only_recognized_transient_google_boundaries_retry(error):
    assert classify(error) is not None


@pytest.mark.parametrize("error", [api_errors.Forbidden("denied"), api_errors.Unauthorized("invalid"),
    api_errors.NotFound("missing"), api_errors.BadRequest("configuration"),
    ValueError("invalid index"), RuntimeError("503 UNAVAILABLE"),
    auth_errors.RefreshError("503 unavailable"),
    auth_errors.RefreshError("Unable to acquire credentials", '{"error":{"code":403}}'),
    auth_errors.RefreshError("Unable to acquire credentials", '{"error":{"code":"503"}}'),
    auth_errors.RefreshError("Unable to acquire credentials", '{"error":"503"}')])
def test_authentication_configuration_unknown_and_malformed_responses_do_not_retry(error):
    assert classify(error) is None
