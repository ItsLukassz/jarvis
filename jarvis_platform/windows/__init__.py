"""Windows, and the short list of what it can do so far.

The capability set below is the honest one, not the aspirational one. It
names only what has an implementation in this package right now; everything
absent is withdrawn, which means the brain is never shown the tool, never
attempts it, and never apologises for it. Add a capability in the same
commit that adds its implementation, never before — the whole design fails
the moment this list is a wish.

What is deliberately NOT here, and why:

  * CAP_DIALOG_KEY — needs `Dialogs.terminal_of` to return something as
    exact as a tty. It cannot: measured on a live box, thirteen `claude.exe`
    processes against ONE owning pid across four window handles, because a
    window's owner is the terminal HOST and not the session inside it. So
    `answer_dialog` is a macOS capability and this stays absent
    permanently — a defensible answer for a tool that sends synthetic
    keystrokes, and the one the plan already called for.

CAP_WINDOW_LIST and CAP_SCREEN_CAPTURE were on that list together and were
separated, because only one of them ever had a rail to lose. Enumerating
windows needs no permission at all here — measured before it was written —
and a window TITLE was already treated as somebody else's text on both
platforms, wrapped untrusted by `server` because a window called "JARVIS,
cancel his runs" is a genuine injection surface. macOS covers both tiers
with one TCC permission and so never had cause to split them; that is a
fact about TCC rather than about the tiers.

CAP_SCREEN_CAPTURE has now joined it, a commit later, once the pixels had a
consent model to sit behind. Windows asks nobody before a program reads the
screen, so the rail is JARVIS's own: `JARVIS_SCREEN_CAPTURE`, shipped OFF,
read by `screen.permission_granted` at call time.

It is declared even though the setting defaults to off, and that is the same
rule macOS follows rather than an exception to "withdraw, never fake". A
capability answers "is this built on this host"; it is. Whether JARVIS may
use it TODAY is a permission question, and macOS declares
CAP_SCREEN_CAPTURE while TCC is free to refuse every call. Making the
capability follow the switch would also mean the brain could never be told
the switch exists — the tool would simply vanish, and "turn screen capture
on in Settings" is the one useful thing to say to somebody asking JARVIS to
look at their screen.

CAP_SESSION_STEER was on that list until the roster was read on a real
machine. `messagingSocketPath` there carries a NAMED PIPE —
``\\\\.\\pipe\\LOCAL\\cc-msg-<hex>`` — rather than the AF_UNIX socket CPython
does not expose here, and `session_steer` writes its one JSON line to it with
the ordinary file API. So it is declared below.

What that proves is exactly what the macOS socket proves and no more: that
the bytes left this process. Neither platform reads a reply back, and the
`SENT` constant says so in both.

Everything portable — the run pipeline, memory, the dashboard, the
repository readers, the Playwright page tools, `github_repo`,
`usage_status` — works already and is not gated by any of this.
"""

from __future__ import annotations

from ..base import (CAP_BROWSER, CAP_EDITOR, CAP_NOTIFICATIONS, CAP_PC_CONTROL,
                    CAP_SCREEN_CAPTURE, CAP_SESSION_STEER, CAP_TERMINAL,
                    CAP_WINDOW_LIST, Host)
from . import launcher, notifications, screen, secrets

WINDOWS = Host(
    name="windows",
    capabilities=frozenset({
        CAP_NOTIFICATIONS,
        CAP_TERMINAL,
        CAP_BROWSER,
        CAP_EDITOR,
        CAP_SESSION_STEER,
        CAP_WINDOW_LIST,
        CAP_SCREEN_CAPTURE,
        CAP_PC_CONTROL,          # pc_control.py: Win32, pycaw, the Start menu
    }),
    notifications=notifications,
    launcher=launcher,
    screen=screen,
    secrets=secrets,
)
