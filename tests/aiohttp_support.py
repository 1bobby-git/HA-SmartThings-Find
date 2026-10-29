"""Load the real aiohttp package for tests that exercise its cookie jar.

Older test modules install a bare ``aiohttp`` stub when the package has not been
imported yet. Those modules keep their own references, so dropping the stub from
``sys.modules`` lets later modules import the real package when it is installed.
"""

from __future__ import annotations

import importlib.util
import sys


def real_aiohttp_available() -> bool:
    current = sys.modules.get("aiohttp")
    if current is not None and getattr(current, "__spec__", None) is None:
        del sys.modules["aiohttp"]
    try:
        if importlib.util.find_spec("aiohttp") is None:
            return False
    except ValueError:
        return False
    import aiohttp  # noqa: F401

    return True
