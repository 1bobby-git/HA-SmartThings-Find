"""Normalize callbacks from Samsung's native-app account sign-in flow."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .const import (
    SAMSUNG_ACCOUNT_APP_REDIRECT_URI,
    SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL,
)

_REQUIRED_CALLBACK_FIELDS = frozenset(
    {"state", "auth_server_url", "code", "retValue"}
)
_MS_APP_URI_RE = re.compile(r"ms-app://[^\s\"'<>]+", re.IGNORECASE)
_TRAILING_PROSE = ".,);]}`'\""


class SamsungAccountCallbackError(ValueError):
    """Raised when a supplied Samsung Account callback is malformed."""


class SamsungAccountCallbackUnavailable(SamsungAccountCallbackError):
    """Raised when the browser exposed only a callback-free completion page."""


def _effective_port(parsed) -> int | None:
    """Return an explicit or scheme-default port, rejecting malformed ports."""
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError:
        return -1
    if parsed.scheme.lower() == "https":
        return 443
    if parsed.scheme.lower() == "http":
        return 80
    return None


def _target_matches(actual, expected) -> bool:
    """Compare a parsed URL with a fixed callback target without query data."""
    return (
        actual.username is None
        and actual.password is None
        and actual.scheme.lower() == expected.scheme.lower()
        and (actual.hostname or "").lower() == (expected.hostname or "").lower()
        and _effective_port(actual) == _effective_port(expected)
        and actual.path.rstrip("/") == expected.path.rstrip("/")
    )


def _present_fields(parsed) -> set[str]:
    """Return non-empty callback field names from query and fragment."""
    present: set[str] = set()
    for encoded in (parsed.query, parsed.fragment):
        for name, values in parse_qs(
            encoded,
            keep_blank_values=True,
            strict_parsing=False,
        ).items():
            if any(str(value).strip() for value in values):
                present.add(name)
    return present


def _require_callback_fields(parsed, *, completion_page: bool) -> None:
    missing = _REQUIRED_CALLBACK_FIELDS - _present_fields(parsed)
    if not missing:
        return
    if completion_page:
        raise SamsungAccountCallbackUnavailable(
            "Samsung completion page does not expose the encrypted callback fields"
        )
    raise SamsungAccountCallbackError(
        "Samsung native-app callback is missing required authentication fields"
    )


def _clean_candidate(value: str) -> str:
    """Extract a callback URL from a URL, browser error message, or HTML text."""
    text = html.unescape(str(value or "").strip())
    text = text.replace(r"ms-app:\/\/", "ms-app://")
    text = text.replace(r"\u0026", "&")

    match = _MS_APP_URI_RE.search(text)
    candidate = match.group(0) if match else text
    candidate = candidate.strip().strip("`\"'")
    return candidate.rstrip(_TRAILING_PROSE)


def normalize_samsung_account_callback(value: str) -> str:
    """Return the exact ``ms-app://`` callback expected by samsung-re-find.

    Supported inputs:
    - a complete native-app callback;
    - text containing that callback;
    - Samsung's HTTPS ``signInComplete`` URL when all encrypted callback
      parameters are present in the query or fragment;
    - a raw query/fragment field block containing every required field.

    A bare ``signInComplete`` URL intentionally fails. It has no authorization
    material and cannot be repaired or fetched by Home Assistant because the
    data belongs to the user's separate browser session.
    """
    candidate = _clean_candidate(value)
    if not candidate:
        raise SamsungAccountCallbackError("Samsung callback is empty")

    expected_app = urlsplit(SAMSUNG_ACCOUNT_APP_REDIRECT_URI)
    expected_completion = urlsplit(SAMSUNG_ACCOUNT_SIGN_IN_COMPLETE_URL)

    # Developer tools may expose only the query/form field block.
    if "://" not in candidate and "=" in candidate:
        raw_fields = candidate.lstrip("?#")
        parsed_fields = urlsplit(f"placeholder://callback?{raw_fields}")
        _require_callback_fields(parsed_fields, completion_page=True)
        return urlunsplit(
            (
                expected_app.scheme,
                expected_app.netloc,
                expected_app.path,
                raw_fields,
                "",
            )
        )

    try:
        parsed = urlsplit(candidate)
    except ValueError as err:
        raise SamsungAccountCallbackError(
            "Samsung callback URL could not be parsed"
        ) from err

    if _target_matches(parsed, expected_app):
        _require_callback_fields(parsed, completion_page=False)
        return urlunsplit(
            (
                expected_app.scheme,
                expected_app.netloc,
                expected_app.path,
                parsed.query,
                parsed.fragment,
            )
        )

    if _target_matches(parsed, expected_completion):
        _require_callback_fields(parsed, completion_page=True)
        return urlunsplit(
            (
                expected_app.scheme,
                expected_app.netloc,
                expected_app.path,
                parsed.query,
                parsed.fragment,
            )
        )

    raise SamsungAccountCallbackError(
        "Samsung callback target does not match the supported account flow"
    )
