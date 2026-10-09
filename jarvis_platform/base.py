"""What a platform can do, and which tools depend on it.

JARVIS drives the machine it runs on, so a second platform is not a matter
of translating each subprocess — some of what he does has no equivalent
elsewhere, and some has one that has not been built yet. This module is how
that is stated, once, rather than discovered by a tool failing.

The rule, and it is the reason this is a set of capabilities rather than a
set of try/excepts: **a platform that cannot do something withdraws the
tool, it does not fake it.** A tool that always answers "not supported on
this platform" is described to the brain on every single turn at roughly
250 tokens, invites it to try anyway, and gives it something to apologise
for. A tool the brain never sees costs nothing and cannot be misused.

Capabilities are deliberately named after the *behaviour*, not the module
or the API. `screen_capture` is a thing JARVIS can or cannot do;
`screencapture` is how one platform happens to do it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger("jarvis.platform")


# --- the capabilities ------------------------------------------------------

#: Put a picture of the user's screen in front of the brain.
CAP_SCREEN_CAPTURE = "screen_capture"
#: Read which windows are open and which application is in front.
CAP_WINDOW_LIST = "window_list"
#: Open a visible terminal window at a directory, optionally running something.
CAP_TERMINAL = "terminal"
#: Open a URL or a local file in the user's browser.
CAP_BROWSER = "browser"
#: Open a file or directory in the user's editor.
CAP_EDITOR = "editor"
#: Send one keystroke to the terminal a specific Claude Code session runs in.
CAP_DIALOG_KEY = "dialog_key"
#: Post a message into another Claude Code session's inbox.
CAP_SESSION_STEER = "session_steer"
#: Post a native notification when no browser tab is listening.
CAP_NOTIFICATIONS = "notifications"
#: Open/close apps, media keys, windows, volume, clipboard, status (pc_control.py).
CAP_PC_CONTROL = "pc_control"

ALL_CAPABILITIES = frozenset({
    CAP_PC_CONTROL,
    CAP_SCREEN_CAPTURE,
    CAP_WINDOW_LIST,
    CAP_TERMINAL,
    CAP_BROWSER,
    CAP_EDITOR,
    CAP_DIALOG_KEY,
    CAP_SESSION_STEER,
    CAP_NOTIFICATIONS,
})


# --- which tools need which ------------------------------------------------
#
# Only the tools that depend on a platform capability appear here. Everything
# absent is portable and is never withdrawn: the run pipeline, the memory
# writers, the repository readers, the Playwright page tools, `github_repo`,
# `usage_status`, `connections`, `list_sessions` and the rest all work
# anywhere Python does.
#
# `run_command` shares CAP_TERMINAL with `open_in_terminal` because it is
# the same act — it opens a Terminal window and types a command into it
# (server.py's `_perform_run_command`), it does not run anything itself.

TOOL_CAPABILITIES: dict[str, str] = {
    "look_at_screen": CAP_SCREEN_CAPTURE,
    "look_at_window": CAP_SCREEN_CAPTURE,
    "open_app": CAP_PC_CONTROL,
    "close_app": CAP_PC_CONTROL,
    "media_control": CAP_PC_CONTROL,
    "window_control": CAP_PC_CONTROL,
    "set_volume": CAP_PC_CONTROL,
    "set_audio_output": CAP_PC_CONTROL,
    "read_clipboard": CAP_PC_CONTROL,
    "write_clipboard": CAP_PC_CONTROL,
    "create_file": CAP_PC_CONTROL,
    "find_files": CAP_PC_CONTROL,
    "notes": CAP_PC_CONTROL,
    "open_file": CAP_PC_CONTROL,
    "append_to_file": CAP_PC_CONTROL,
    "move_file": CAP_PC_CONTROL,
    "type_text": CAP_PC_CONTROL,
    "discord_control": CAP_PC_CONTROL,
    "play_music": CAP_PC_CONTROL,
    "pc_status": CAP_PC_CONTROL,
    "what_is_on_screen": CAP_WINDOW_LIST,
    "open_in_terminal": CAP_TERMINAL,
    "run_command": CAP_TERMINAL,
    "open_in_browser": CAP_BROWSER,
    "open_in_editor": CAP_EDITOR,
    "answer_dialog": CAP_DIALOG_KEY,
    "steer_session": CAP_SESSION_STEER,
}


# --- the sub-interfaces ----------------------------------------------------
#
# One per module as it physically moves behind the layer. A protocol written
# before its implementation would be an interface designed against a guess,
# so this list grows with `jarvis_platform/macos/` rather than ahead of it.


class Notifications(Protocol):
    """Getting the user's attention when nothing is listening on the voice
    channel. Implementations must never raise — see the macOS one."""

    def available(self) -> bool: ...

    async def notify(self, title: str, message: str, *,
                     subtitle: str = "") -> bool: ...


class _NoNotifications:
    """The answer on a platform whose notifications have not been built.

    Exactly what the macOS implementation already did when it found itself
    somewhere else: say so once in the log, report False, and never raise.
    The announcement path treats False as "nobody was told", which is true.
    """

    def available(self) -> bool:
        return False

    async def notify(self, title: str, message: str, *,
                     subtitle: str = "") -> bool:
        log.warning("notifications are not available on this platform")
        return False


NO_NOTIFICATIONS = _NoNotifications()


class Launcher(Protocol):
    """Putting something on the user's screen: a terminal, a page, a file.

    Each returns `{"success": bool, "confirmation": str}` — `confirmation`
    is a sentence fit to be spoken, because it usually is. `editor` also
    returns `"editor"`, the name of what actually opened.

    `terminal` takes `cwd` and `command` separately rather than a composed
    shell line, so the implementation can quote for its own shell. That is
    the whole reason this is an interface and not three free functions.
    """

    async def terminal(self, *, cwd: str = "", command: str = "") -> dict: ...

    async def browser(self, url: str, which: str = "chrome") -> dict: ...

    async def editor(self, path: str) -> dict: ...


class _NoLauncher:
    """A platform that cannot open windows yet.

    Unreachable in practice — every tool that calls a launcher is withdrawn
    by CAP_TERMINAL / CAP_BROWSER / CAP_EDITOR before it can be invoked, so
    this is the backstop behind that gate rather than the gate itself. It
    refuses in the shape callers already handle: `success` False and a
    sentence, never an exception.
    """

    _REFUSAL = {"success": False,
                "confirmation": "I can't open that on this machine, sir."}

    async def terminal(self, *, cwd: str = "", command: str = "") -> dict:
        return dict(self._REFUSAL)

    async def browser(self, url: str, which: str = "chrome") -> dict:
        return dict(self._REFUSAL)

    async def editor(self, path: str) -> dict:
        return dict(self._REFUSAL, editor="an editor")


NO_LAUNCHER = _NoLauncher()


# --- answering a prompt in somebody else's terminal ------------------------
#
# The most dangerous thing JARVIS does: a synthetic keystroke aimed at a
# window on the user's machine. Two of the three safety properties are
# platform-INDEPENDENT and therefore live here, not in an implementation:
#
# 1. The vocabulary is closed. Return, Escape, and a single digit 1-9. That
#    is the whole set, and it is a boundary rather than a convenience —
#    anything else is refused before any script is composed, so there is
#    never untrusted text to escape. This module never types text, on any
#    platform, and a second implementation does not get to widen it.
# 2. Nothing raises. Every path returns an outcome string, so the caller can
#    always say something useful and always has something to audit.
#
# The third — that the target is found by IDENTITY and never by focus — can
# only be kept by an implementation, because what identifies a terminal
# differs per platform. It is the protocol's central promise all the same;
# see `Dialogs.terminal_of`.

SENT = "sent"
NO_TERMINAL = "no_tty"        # the process has no terminal we can address
NOT_FOUND = "not_found"       # a terminal we cannot reach hosts it
NOT_PERMITTED = "not_permitted"   # the OS refused us
FAILED = "failed"             # the attempt errored, timed out, or said something odd
BAD_KEY = "bad_key"           # defensive: a key outside the closed vocabulary

# The wire values are unchanged from when this lived in `dialog.py` — they
# are recorded in the steer audit table, so a rename would orphan history.
# `no_tty` in particular is read as "no terminal of its own", not literally
# as a POSIX tty; Windows consoles have no tty and the outcome still applies.

_ALIASES = {
    "enter": "return",
    "return": "return",
    "yes": "return",       # "yes" answers a permission prompt with Return
    "y": "return",
    "escape": "escape",
    "esc": "escape",
    "cancel": "escape",
    "no": "escape",
    "n": "escape",
}


def normalize_key(key) -> str | None:
    """The closed vocabulary, or None. None means REFUSE — never interpret.

    Returns "return", "escape", or a single digit "1".."9". Anything else,
    including free text that merely starts with an accepted word, is None.
    """
    if not isinstance(key, str):
        return None
    k = key.strip().lower()
    if k in _ALIASES:
        return _ALIASES[k]
    if len(k) == 1 and k in "123456789":
        return k
    return None


def spoken_key(normalized: str) -> str:
    """How the read-back names the key. Must match what actually gets sent."""
    return {"return": "Return", "escape": "Escape"}.get(normalized, normalized)


class Dialogs(Protocol):
    """Press one key in the terminal a specific Claude Code session runs in."""

    async def terminal_of(self, pid) -> str | None:
        """An OPAQUE identity for the terminal that owns `pid`, or None.

        Callers compare these and never parse them: server.py groups a
        session's pids by this value to detect one spanning several
        terminals, and refuses rather than guessing when it does. On macOS
        it is a tty path; on another platform it may be a window handle.
        Nothing outside the implementation may assume either.
        """
        ...

    async def answer(self, pid: int, key: str) -> str:
        """Press `key` in the terminal owning `pid`. Never raises.

        Returns one of the outcome constants above. Only SENT means a
        keystroke actually left the machine's event queue.

        The implementation must find its target by identity, never by
        focus — aimed wrong, this types into whatever the user is actually
        working in. NOT_FOUND is a normal and correct answer, and must
        never be upgraded into a best guess.
        """
        ...


class _NoDialogs:
    """A platform with no way to aim a keystroke safely.

    Answers NOT_FOUND, which server.py already speaks as "another
    application is hosting it, so that one needs your own hand" — true, and
    the right thing to say. Reached only behind a withdrawn CAP_DIALOG_KEY.
    """

    async def terminal_of(self, pid) -> str | None:
        return None

    async def answer(self, pid: int, key: str) -> str:
        return NOT_FOUND


NO_DIALOGS = _NoDialogs()


# --- seeing the machine itself ---------------------------------------------
#
# A camera pointed at the user's life. A screenshot can hold a password, a
# private message, a client's data — so the rules that make this acceptable
# are protocol rules, binding on every implementation:
#
#   * Nothing here runs on a timer, speculatively, or as ambient context.
#     `server.ACTING_TOOLS` gates both tools to a turn the user drove.
#   * The capture exists on disk only for as long as it takes to shrink it
#     and read the bytes, and is removed on every path out.
#   * An implementation refuses rather than handing back something the brain
#     would describe wrongly. A confident answer about a blank picture is
#     the worst failure this feature has.


class ScreenError(Exception):
    """Something JARVIS could not see. The message is meant to be spoken."""


@dataclass
class Shot:
    """One capture, already shrunk to what the brain is shown."""
    png: bytes
    width: int
    height: int


@dataclass
class Window:
    app: str
    title: str
    frontmost: bool


@dataclass(frozen=True)
class CaptureGate:
    """How this host decides whether JARVIS may capture, in words to reuse.

    Exists so `preflight` can say something true about a refusal without
    knowing which platform it is on. It had macOS's answer written into it —
    the name of the permission, and a remedy naming System Settings — which
    was invisible while macOS was the only host declaring
    CAP_SCREEN_CAPTURE and became wrong the moment a second one did.

    `off_by_default` is the field that matters most, and it decides a
    STATUS. On macOS a refusal is a surprise worth speaking at startup: the
    user granted Screen Recording and something took it away. Where the gate
    is a JARVIS setting that ships OFF, "off" is the intended resting state,
    and announcing it every boot is nagging the user about a decision they
    already made — so it warns (logged, not spoken) rather than fails.
    """
    name: str            # as it would be said: "Screen Recording"
    off_by_default: bool
    remedy: str


class Screen(Protocol):
    """The machine's own screen, priced in two tiers.

    `windows()` is a few hundred bytes and no pixels; "what am I looking at"
    is usually answerable from it alone. `capture()` costs roughly 1,200
    tokens of image on the turn it is used. Callers should reach for the
    cheap one first — the same split `read_page` and `look_at_page` make for
    the web.
    """

    def permission_granted(self) -> bool | None:
        """Whether capture is permitted, asked WITHOUT prompting for it.

        True when JARVIS may capture — and a platform that requires no such
        permission returns True, not None, because "nothing stands in the
        way" is a real answer rather than an unknown one. False when it is
        refused. None ONLY when the probe itself could not be run, which is
        the one case a caller should report as "could not determine".

        Must never put a system dialog in front of the user: this is asked
        at startup and on a tool call, and neither may interrupt.
        """
        ...

    async def capture(self, display: int | None = None) -> Shot: ...

    async def windows(self) -> list[Window]: ...

    # What `permission_granted` is answering ABOUT, so a caller can report a
    # refusal in this platform's own terms. A constant on the module rather
    # than a method: it never varies at runtime, and the implementations here
    # are modules.
    CAPTURE_GATE: CaptureGate


class _NoScreen:
    """A platform whose eyes have not been built.

    Raises ScreenError with a speakable sentence, which is what every caller
    already handles — `server._screen_refusal` turns it straight into
    something JARVIS says. Reached only behind a withdrawn
    CAP_SCREEN_CAPTURE / CAP_WINDOW_LIST.
    """

    CAPTURE_GATE = CaptureGate(
        name="screen access",
        off_by_default=True,
        remedy="This platform has no screen support built, so there is "
               "nothing to grant.")

    def permission_granted(self) -> bool | None:
        return None

    async def capture(self, display: int | None = None) -> Shot:
        raise ScreenError("I can't see this machine's screen, sir")

    async def windows(self) -> list[Window]:
        raise ScreenError("I can't read what's open on this machine, sir")


NO_SCREEN = _NoScreen()


# --- files only their owner may read ---------------------------------------
#
# One file needs this and it is the loopback tool token, which admits its
# bearer to every acting tool and to every state-changing HTTP route. The
# POSIX statement of the requirement is "mode 0600, and do not follow
# symlinks"; neither half survives translation, so the requirement is stated
# here as behaviour and each platform keeps it its own way.


class PrivateFileUnsupported(RuntimeError):
    """This platform cannot promise a file only its owner can read.

    Deliberately NOT an OSError. `web_auth` wraps its `ensure_tool_token`
    call in `except Exception` and denies; startup lets this propagate and
    refuses to boot. Both fail closed, which is the point — an OSError
    would be at risk of being swallowed by code handling ordinary file
    trouble.
    """


class Secrets(Protocol):
    """Create and adopt a file only this user can read.

    Both return an OPEN file descriptor which the caller must close, or —
    for `create` — None when the file already exists. Returning a
    descriptor rather than a path is the whole point: it is what lets an
    implementation prove that the thing it checked is the thing the caller
    then reads.
    """

    def create_private(self, path) -> int | None:
        """A new private file, or None if one is already there.

        Must be atomic against a file that appears between the check and
        the create, and must never exist even briefly at permissions
        somebody else could read.
        """
        ...

    def adopt_private(self, path) -> int:
        """An existing file, proven to be ours, opened for reading.

        Must refuse — by raising — anything that is not a regular file this
        user owns, rather than replacing it: it is somebody else's file and
        deleting it is not ours to do. Must not be fooled by a link planted
        at the path, in whatever form this platform spells one.
        """
        ...

    def restrict(self, path) -> None:
        """Take an EXISTING file down to owner-only access. Raises on failure.

        The third verb, and it exists because "0600" is a POSIX spelling of
        the promise rather than the promise itself. `server._write_mcp_config`
        wrote `mcp.json` — which carries the tool token's path and a verbatim
        copy of every `env` block out of the user's `connections.json`, their
        Notion token, their GitHub token — and then called `path.chmod(0o600)`
        to tighten it. On Windows `chmod` only toggles the read-only
        ATTRIBUTE and restricts nobody, so on that platform the promise in
        that comment was not kept and the file kept whatever the directory
        granted.

        Distinct from `create_private` because the caller here has already
        written the content and wants it made private, not made afresh.
        """
        ...


class _NoSecrets:
    """A platform whose private-file story has not been written.

    Refuses loudly rather than degrading. The failure to avoid is a silent
    fallback that leaves the token at whatever permissions it was born with
    while the docstring still promises otherwise.
    """

    def create_private(self, path) -> int | None:
        raise PrivateFileUnsupported(
            f"cannot create {path} with owner-only access on this platform")

    def adopt_private(self, path) -> int:
        raise PrivateFileUnsupported(
            f"cannot prove {path} is private to this user on this platform")

    def restrict(self, path) -> None:
        raise PrivateFileUnsupported(
            f"cannot restrict {path} to this user on this platform")


NO_SECRETS = _NoSecrets()


@dataclass(frozen=True)
class Host:
    """One platform, what it can do, and how it does it.

    The sub-interface fields default to null objects so that an unknown
    platform is constructible and JARVIS still starts on it. That is the
    same choice `capabilities` makes: claim nothing, refuse cleanly, and
    let the portable two thirds of him work.
    """
    name: str
    capabilities: frozenset[str]
    notifications: Notifications = field(default=NO_NOTIFICATIONS)
    launcher: Launcher = field(default=NO_LAUNCHER)
    dialogs: Dialogs = field(default=NO_DIALOGS)
    screen: Screen = field(default=NO_SCREEN)
    secrets: Secrets = field(default=NO_SECRETS)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def allows_tool(self, tool: str) -> bool:
        """Whether this platform can run `tool` at all.

        A tool with no entry in TOOL_CAPABILITIES is portable, and portable
        is the default — a new tool has to opt IN to being platform-bound,
        so forgetting to list one leaves it working everywhere rather than
        silently disabled on the platform nobody tested.
        """
        needed = TOOL_CAPABILITIES.get(tool)
        return needed is None or needed in self.capabilities

    def withdrawn_tools(self) -> frozenset[str]:
        """Every tool this platform cannot offer. Empty on a complete host."""
        return frozenset(name for name, cap in TOOL_CAPABILITIES.items()
                         if cap not in self.capabilities)

    def filter_tools(self, names):
        """`names`, minus anything this platform cannot do, order preserved."""
        return [n for n in names if self.allows_tool(n)]
