"""Which platform JARVIS is running on, and what it can therefore do.

Named `jarvis_platform` and not `platform`, which would be a top-level
package in the repository root and therefore ahead of the standard library
on `sys.path` for the whole process — `server.py` runs from that directory,
so a package called `platform` would shadow the stdlib module of that name
for uvicorn, httpx and Playwright alike. The `jarvis_` prefix follows
`jarvis_memory` and `jarvis_mcp`.

Selection happens once, at import. `current()` is a function rather than a
constant so a test can put a different host in front of it (see
`jarvis_platform.fake`) without reloading every module that reads it.
"""

from __future__ import annotations

import sys

from . import base  # noqa: F401  (server.py reaches base.SENT etc. through this)
from .base import (  # noqa: F401  (re-exported: this is the public surface)
    ALL_CAPABILITIES,
    CAP_BROWSER,
    CAP_DIALOG_KEY,
    CAP_EDITOR,
    CAP_NOTIFICATIONS,
    CAP_PC_CONTROL,
    CAP_SCREEN_CAPTURE,
    CAP_SESSION_STEER,
    CAP_TERMINAL,
    CAP_WINDOW_LIST,
    TOOL_CAPABILITIES,
    Host,
)
from .macos import MACOS
from .windows import WINDOWS


def _detect() -> Host:
    if sys.platform == "darwin":
        return MACOS
    if sys.platform == "win32":
        return WINDOWS
    # Deliberately not a raise. JARVIS must be able to start on a platform
    # whose host has not been written yet — the portable two thirds of him
    # (the run pipeline, memory, the dashboard, the repository readers) work
    # perfectly well, and the platform-bound tools withdraw themselves rather
    # than failing one at a time. What is NOT allowed is pretending: an
    # unknown platform declares nothing.
    return Host(name=sys.platform, capabilities=frozenset())


_HOST: Host = _detect()


def current() -> Host:
    """The host this process is running on."""
    return _HOST


def can(capability: str) -> bool:
    return _HOST.can(capability)


def allows_tool(tool: str) -> bool:
    return _HOST.allows_tool(tool)


def filter_tools(names):
    return _HOST.filter_tools(names)


def withdrawn_tools() -> frozenset[str]:
    return _HOST.withdrawn_tools()
