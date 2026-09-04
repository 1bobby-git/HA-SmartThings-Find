"""Classify authentication input without logging sensitive values."""

from __future__ import annotations

from collections.abc import Mapping

# These names are associated with account.samsung.com browser state. They are
# used only to provide a safer error after SmartThings Find has rejected the
# submitted Cookie header. A valid SmartThings Find cookie is never rejected
# merely because one of these names is present.
_SAMSUNG_ACCOUNT_COOKIE_MARKERS = frozenset(
    {
        "_common_physicaladdresstext",
        "_common_pwafterfewmonth",
        "change-password-later",
        "g_enabled_idps",
        "sa_analytics_sid",
        "sa_did",
        "sa_did_temp",
        "sa_trace",
        "stk",
        "usawswipsessionid",
    }
)


def has_samsung_account_cookie_markers(
    cookies: Mapping[str, object],
) -> bool:
    """Return whether cookie names resemble Samsung Account browser state."""
    names = {str(name).casefold() for name in cookies}
    return bool(names & _SAMSUNG_ACCOUNT_COOKIE_MARKERS)
