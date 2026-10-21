"""Recognize transport failures for bounded, read-only Google API recovery."""
from __future__ import annotations

import json

from google.api_core import exceptions as api_errors
from google.auth import exceptions as auth_errors
from requests import exceptions as request_errors

TRANSIENT_CODES = {429, 500, 502, 503, 504}


def classify(error: Exception) -> str | None:
    if isinstance(error, api_errors.GoogleAPICallError) and error.code in TRANSIENT_CODES:
        return f"google_http_{error.code}"
    if isinstance(error, (auth_errors.TransportError, request_errors.ConnectionError,
                          request_errors.Timeout, TimeoutError, ConnectionError)):
        return "google_transport"
    if isinstance(error, auth_errors.RefreshError):
        # Impersonated-credential acquisition carries Google's JSON error as a
        # separate exception argument. Do not retry free-text authentication
        # errors, denied scopes or malformed provider responses.
        for argument in error.args:
            if not isinstance(argument, str):
                continue
            try:
                response = json.loads(argument)
            except ValueError:
                continue
            if isinstance(response, dict) and isinstance(response.get("error"), dict):
                code = response["error"].get("code")
                if type(code) is int and code in TRANSIENT_CODES:
                    return f"google_credential_http_{code}"
    return None
