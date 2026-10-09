"""The Windows host, tested from a Mac.

Everything here mocks the one subprocess seam per module, so it runs on any
platform — which is the point. What it CANNOT prove is that the argv it
pins actually do what they claim on Windows; that needs a real box. So the
tests are written to be the correction surface: each one pins a decision,
and sample command output is quoted from documentation rather than
measurement. Replace a sample with a real one and the failure will say
exactly what has to move.

Read `jarvis_platform/windows/secrets.py`'s module docstring first.
"""

import asyncio
import os
import stat
import sys

import pytest

import jarvis_platform as jp
from jarvis_platform.base import PrivateFileUnsupported
from jarvis_platform.windows import WINDOWS
from jarvis_platform.windows import launcher as win_launcher
from jarvis_platform.windows import notifications as win_notify
from jarvis_platform.windows import secrets as win_secrets


# --- the host declares only what exists ------------------------------------

def test_windows_declares_only_what_it_has_an_implementation_for():
    """The capability set is the honest one, not the aspirational one. Add
    a capability in the same commit as its implementation, never before —
    the whole design fails the moment this list is a wish."""
    assert WINDOWS.capabilities == {jp.CAP_NOTIFICATIONS, jp.CAP_TERMINAL,
                                    jp.CAP_BROWSER, jp.CAP_EDITOR,
                                    jp.CAP_SESSION_STEER,
                                    jp.CAP_WINDOW_LIST,
                                    jp.CAP_SCREEN_CAPTURE,
                                    jp.CAP_PC_CONTROL}       # pc_control.py


def test_windows_withdraws_only_the_one_tool_it_cannot_do():
    """`steer_session` left this list when its transport was written, not
    before: the roster publishes a named pipe here and `session_steer` writes
    to it, exercised over a real pipe in tests/test_session_steer.py.
    `look_at_screen` left it once the pixels had a consent model to sit
    behind — `JARVIS_SCREEN_CAPTURE`, shipped off.

    That the switch defaults to OFF and the tool is still offered is not a
    hole in "withdraw, never fake": a capability says what is BUILT here, and
    macOS declares this one while TCC is free to refuse every call. See
    tests/test_windows_screen.py.

    `answer_dialog` is permanent: there is no mapping from a pid to a
    specific console window here (measured — one owning pid across every
    window handle, because the owner is the terminal HOST and not the session
    inside it), and aiming a synthetic keystroke by focus instead is the one
    thing `base.Dialogs` forbids outright.
    """
    assert WINDOWS.withdrawn_tools() == {"answer_dialog"}


def test_windows_still_offers_everything_portable():
    for tool in ("spawn_run", "start_build", "remember", "recall", "read_file",
                 "search_repo", "repo_overview", "list_sessions", "run_status",
                 "read_page", "look_at_page", "github_repo", "usage_status"):
        assert WINDOWS.allows_tool(tool) is True, tool


def test_the_unbuilt_provider_is_the_null_object_not_a_stub():
    """`dialogs` has no Windows implementation and must be the base null
    object — which refuses — rather than something that half-answers.

    `screen` is no longer among them: it is a real module, and now of both
    tiers rather than one. Whether it may photograph anything on a given day
    is `JARVIS_SCREEN_CAPTURE`, which is a permission and not a capability.
    """
    from jarvis_platform import base
    from jarvis_platform.windows import screen as win_screen
    assert WINDOWS.dialogs is base.NO_DIALOGS
    assert WINDOWS.screen is win_screen
    assert WINDOWS.screen is not base.NO_SCREEN
    assert WINDOWS.secrets is win_secrets


# --- the launcher's quoting, which is why the interface exists --------------

def test_windows_terminal_passes_the_directory_as_its_own_argument(monkeypatch):
    """`wt -d <dir>` needs no quoting of `dir` whatever it contains,
    because it is its own argv entry. That is the whole reason `terminal`
    takes cwd and command apart instead of a composed shell line."""
    monkeypatch.setattr(win_launcher.shutil, "which",
                        lambda name: r"C:\wt.exe" if name == "wt.exe" else None)
    argv = win_launcher._terminal_argv(r"C:\Program Files\my project", "")
    assert argv == [r"C:\wt.exe", "-d", r"C:\Program Files\my project"]


def test_windows_terminal_never_posix_quotes(monkeypatch):
    """`shlex.quote` here would be actively wrong — it is POSIX, and this
    is cmd.exe. A path with a space must not come back with backslashes or
    single quotes in it."""
    monkeypatch.setattr(win_launcher.shutil, "which",
                        lambda name: r"C:\wt.exe" if name == "wt.exe" else None)
    argv = win_launcher._terminal_argv(r"C:\a b\c", "npm run dev")
    assert argv == [r"C:\wt.exe", "-d", r"C:\a b\c", "cmd.exe", "/k", "npm run dev"]
    assert not any("'" in part or "\\ " in part for part in argv)


def test_the_cmd_fallback_no_longer_composes_a_cd_at_all(monkeypatch):
    """This replaces a test that asserted the composed `cd /d "<dir>" && ...`
    line was built correctly. It was — and it never worked, because Python
    escapes the quotes as \\" and cmd.exe reads that literally. The test
    passed for four months on a branch nobody had run.

    The directory is the spawn's `cwd` now, so there is no `cd` to get right
    and no drive to change. See the tests at the bottom of this file for the
    rule that replaced the string.
    """
    monkeypatch.setattr(win_launcher.shutil, "which",
                        lambda name: r"C:\cmd.exe" if name == "cmd.exe" else None)
    argv = win_launcher._terminal_argv(r"D:\work\chitauri", "npm test")
    assert argv == [r"C:\cmd.exe", "/k", "npm test"]
    assert not any("cd /d" in a for a in argv)


def test_windows_editor_asks_for_the_cmd_shim_by_its_full_name(monkeypatch):
    """The Windows VS Code installer puts `code.cmd` on PATH — confirmed on
    a real box. The full name is asked for FIRST so the result does not
    depend on PATHEXT, which happens to resolve a bare `code` to it as well.

    Not because a `.cmd` cannot be spawned: it can, measured 2026-09-09.
    See jarvis_platform/windows/launcher.py for what it costs instead."""
    asked = []
    monkeypatch.setattr(win_launcher.shutil, "which",
                        lambda name: asked.append(name) or None)
    monkeypatch.setattr(win_launcher.os, "startfile", lambda p: None, raising=False)
    import asyncio
    asyncio.run(win_launcher.editor(r"C:\x\y.py"))
    assert asked[0] == "code.cmd"


# --- notifications keep the argv discipline, bought differently ------------

def test_untrusted_text_never_enters_the_notification_script():
    """The macOS version passes it as argv because osascript hands argv
    over as data. PowerShell has no argv for a script read from stdin, so
    the same property is bought with the environment. Either way there is
    no escaping step, because there is nothing to escape."""
    payload = '"; Remove-Item -Recurse C:\\ #'
    assert payload not in win_notify._NOTIFY_SCRIPT
    # The script reads variables, it does not carry values.
    for var in (win_notify._ENV_TITLE, win_notify._ENV_MESSAGE,
                win_notify._ENV_SUBTITLE):
        assert f"$env:{var}" in win_notify._NOTIFY_SCRIPT


# Both of the next two describe the module when it is NOT on its own
# platform, which is the state this file was written in. Run them ON Windows
# and `available()` is correctly True and `notify()` correctly hands a toast
# off, so the assertions below can only hold off-Windows — measured, on a
# real box: both returned True. Skipped rather than inverted, because the
# property each one pins (inert off-platform) is still worth pinning, and
# the Windows-side property is NOT "notify returns True": a toast handed to
# an unregistered AUMID also returns True and displays nothing. That one
# cannot be settled from a test at all — see docs/plans/windows-handoff.md.
_off_windows_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="describes the module off its own platform; on Windows both are True")


@_off_windows_only
def test_notifications_are_unavailable_off_windows():
    assert win_notify.available() is False


@_off_windows_only
@pytest.mark.asyncio
async def test_a_notification_that_cannot_be_posted_reports_false_not_raises():
    """A fallback that raises would break the announcement it stands in
    for. False means 'nobody was told', which is true."""
    assert await win_notify.notify("t", "m", subtitle="s") is False


# --- the token: parsing is the part a real box has to confirm --------------

# MEASURED on a real Windows 11 box (2026-09-08), no longer quoted from
# documentation. The shapes the Mac guessed were right; one detail was not,
# and it is kept below because it is the one a future edit could get wrong.
_WHOAMI_SAMPLE = '"desktop-abc\\ken","S-1-5-21-1111111111-2222222222-3333333333-1001"\n'

_ICACLS_OURS = (
    "C:\\data\\jarvis\\tool-token DESKTOP-ABC\\ken:(F)\n"
    "\n"
    "Successfully processed 1 files; Failed processing 0 files\n")

# The correction: real inherited ACEs carry an (I) flag before the rights,
# which the documentation sample omitted. `_parse_aces` splits on the LAST
# colon-paren group and was unaffected — measured, it returned the same three
# names from the real output — but a sample that cannot occur is a sample
# that stops testing the thing it names, so this is the real text.
_ICACLS_INHERITED = (
    "C:\\data\\jarvis\\tool-token NT AUTHORITY\\SYSTEM:(I)(F)\n"
    "                            BUILTIN\\Administrators:(I)(F)\n"
    "                            DESKTOP-ABC\\ken:(I)(F)\n"
    "\n"
    "Successfully processed 1 files; Failed processing 0 files\n")


def test_whoami_parsing_yields_the_account_and_its_sid():
    assert win_secrets._parse_whoami(_WHOAMI_SAMPLE) == (
        "desktop-abc\\ken",
        "S-1-5-21-1111111111-2222222222-3333333333-1001")


def test_whoami_parsing_refuses_anything_it_does_not_recognise():
    """None means "we do not know who we are", and the caller refuses
    rather than granting the file to a guess."""
    for junk in ("", "\n", "not csv at all", '"just-one-field"'):
        assert win_secrets._parse_whoami(junk) is None


def test_icacls_parsing_reads_one_ace_and_many():
    path = "C:\\data\\jarvis\\tool-token"
    assert win_secrets._parse_aces(_ICACLS_OURS, path) == ["desktop-abc\\ken"]
    assert win_secrets._parse_aces(_ICACLS_INHERITED, path) == [
        "nt authority\\system", "builtin\\administrators", "desktop-abc\\ken"]


def test_a_file_with_inherited_aces_is_not_ours(monkeypatch):
    """The ownership test. Windows has no cheap stdlib `getuid`, so what
    is checked instead is stronger for this purpose: a DACL that is not
    exactly what we would have written means we did not write it, and
    adopting it would mean trusting a token somebody else chose and knows."""
    monkeypatch.setattr(win_secrets, "_identity",
                        ("desktop-abc\\ken", "S-1-5-21-1"))
    monkeypatch.setattr(win_secrets, "_run",
                        lambda *a: (0, _ICACLS_INHERITED))
    assert win_secrets._granted_only_to_us("C:\\data\\jarvis\\tool-token") is False

    monkeypatch.setattr(win_secrets, "_run", lambda *a: (0, _ICACLS_OURS))
    assert win_secrets._granted_only_to_us("C:\\data\\jarvis\\tool-token") is True


def test_the_refusal_says_which_of_three_things_went_wrong(monkeypatch):
    """A bare False collapsed three different failures into one silence, and
    the refusal it produces is the whole of what a user sees at startup.

    Told "not granted solely to this user" about a file JARVIS wrote itself
    ten milliseconds earlier, they go looking for an intruder rather than at
    the ACL that actually disagreed. The Windows CI leg did exactly this on
    every run — refusing its own freshly created token, while the same code
    accepted it on an ordinary desktop account — and the message as first
    written could not say which of the three it had hit. Naming the
    principals is what identified the cause: they were EXPLICIT ACEs, which
    `/inheritance:r` does not touch. `_lock_down` clears them and retries
    now, so this reason reaches a user far less often — but it is what found
    the bug, and it is what the next unfamiliar DACL will be read through.
    """
    path = "C:\\data\\jarvis\\tool-token"
    monkeypatch.setattr(win_secrets, "_identity",
                        ("desktop-abc\\ken", "S-1-5-21-1"))

    # 1. icacls would not run at all.
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (5, "Access is denied."))
    why = win_secrets._dacl_mismatch(path)
    assert why and "icacls exited 5" in why and "Access is denied" in why

    # 2. it ran, and said something this module cannot read.
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (0, "surprising\n"))
    why = win_secrets._dacl_mismatch(path)
    assert why and "could not parse" in why

    # 3. the DACL is genuinely somebody else's — and BOTH sides are named,
    #    which is the whole point: the reader can see what was found and
    #    what was expected without running icacls themselves.
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (0, _ICACLS_INHERITED))
    why = win_secrets._dacl_mismatch(path)
    assert why and "nt authority\\system" in why and "desktop-abc\\ken" in why

    # ...and nothing to say when it is ours.
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (0, _ICACLS_OURS))
    assert win_secrets._dacl_mismatch(path) is None


def test_the_adoption_refusal_carries_the_reason(monkeypatch, tmp_path):
    """The reason has to survive as far as the exception, because that is
    what reaches the user — `ensure_tool_token` runs before anything else."""
    token = tmp_path / "tool-token"
    token.write_text("planted", encoding="utf-8")
    monkeypatch.setattr(win_secrets, "_identity",
                        ("desktop-abc\\ken", "S-1-5-21-1"))
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (0, _ICACLS_INHERITED))

    with pytest.raises(OSError) as caught:
        win_secrets.adopt_private(token)

    said = str(caught.value)
    assert "not granted solely to this user" in said, "the sentence survives"
    assert "nt authority\\system" in said, "and now says what it actually saw"


def test_the_lockdown_breaks_inheritance_and_grants_by_sid(monkeypatch):
    """`/inheritance:r` REMOVES the inherited ACEs; `:e` would copy
    Administrators and SYSTEM in as explicit ones, which is the opposite of
    what is wanted. The grant names a SID because a domain account's name
    is ambiguous and its SID is not."""
    seen = {}
    monkeypatch.setattr(win_secrets, "_identity", ("desktop-abc\\ken", "S-1-5-21-9"))

    def _fake_run(*argv):
        # Two calls now: the change, then the read that VERIFIES it. They are
        # told apart by the flags rather than by call order, so this does not
        # quietly pass if the two are ever swapped.
        if "/grant:r" in argv:
            seen["argv"] = list(argv)
            return 0, ""
        return 0, ("C:\\x\\tool-token DESKTOP-ABC\\ken:(F)\n"
                   "\n"
                   "Successfully processed 1 files; Failed processing 0 files\n")

    monkeypatch.setattr(win_secrets, "_run", _fake_run)
    win_secrets._lock_down("C:\\x\\tool-token")
    assert seen["argv"] == ["icacls", "C:\\x\\tool-token", "/inheritance:r",
                            "/grant:r", "*S-1-5-21-9:F"]


def test_a_lockdown_that_reports_success_but_changed_nothing_still_raises(
        monkeypatch):
    """icacls exiting 0 says the command was ACCEPTED, not that the DACL now
    reads the way this module promises.

    That gap is not hypothetical: it is what the Windows CI runner did on
    every run, with our grant correctly added and nothing removed.
    Unverified, the file is created, reported private, and only refused
    later by `adopt_private` — far from the cause, and after a token the
    promise does not cover has already been written into it.

    The case that reaches HERE is now the one the reset could not save: the
    listing keeps its extra ACEs after both passes. The ordinary runner
    shape is handled instead by
    `test_a_lockdown_that_left_explicit_aces_resets_and_tries_again`, which
    is where the (I)-versus-explicit distinction is pinned.
    """
    monkeypatch.setattr(win_secrets, "_identity",
                        ("runnervm\\runneradmin", "S-1-5-21-9"))

    def _accepted_but_ineffective(*argv):
        if "/grant:r" in argv:
            # Exit 0 with a body saying it did nothing. icacls really does
            # this, and that line is what tells "it declined" apart from "it
            # worked and something undid it afterwards" — unanswerable from
            # the rc alone, which is why the message quotes this.
            return 0, "Successfully processed 0 files; Failed processing 1 files"
        if "/reset" in argv:
            return 0, "Successfully processed 1 files; Failed processing 0 files"
        return 0, ("C:\\x\\tool-token RUNNERVM\\runneradmin:(F)\n"
                   "                 NT AUTHORITY\\SYSTEM:(I)(F)\n"
                   "                 BUILTIN\\Administrators:(I)(F)\n"
                   "                 OWNER RIGHTS:(I)(F)\n"
                   "\n"
                   "Successfully processed 1 files; Failed processing 0 files\n")

    monkeypatch.setattr(win_secrets, "_run", _accepted_but_ineffective)

    with pytest.raises(PrivateFileUnsupported) as caught:
        win_secrets._lock_down("C:\\x\\tool-token")

    said = str(caught.value)
    assert "reported success" in said, "the distinction is the whole point"
    assert "nt authority\\system" in said, "and it names what was left behind"
    assert "Failed processing 1 files" in said, "and quotes icacls's own words"


def test_a_token_that_cannot_be_locked_down_is_deleted_not_left(monkeypatch, tmp_path):
    """The failure this module exists to prevent: a token sitting at
    inherited permissions while every docstring promises otherwise."""
    path = tmp_path / "tool-token"
    monkeypatch.setattr(win_secrets, "_identity", ("desktop-abc\\ken", "S-1-5-21-9"))
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (1, "access denied"))

    with pytest.raises(PrivateFileUnsupported):
        win_secrets.create_private(path)
    assert not path.exists(), "an unprotected token was left on disk"


def test_an_unknown_sid_refuses_rather_than_granting_to_a_guess(monkeypatch):
    monkeypatch.setattr(win_secrets, "_identity", None)
    monkeypatch.setattr(win_secrets, "_run", lambda *a: (1, "whoami is missing"))
    with pytest.raises(PrivateFileUnsupported):
        win_secrets._current_identity()


# --- the protocol still refuses where nothing is built ---------------------

def test_a_host_with_no_secrets_refuses_loudly():
    """The failure to avoid is a silent fallback that leaves the token
    readable while the docstring still promises otherwise."""
    host = jp.Host(name="plan9", capabilities=frozenset())
    for call in (lambda: host.secrets.create_private("/tmp/x"),
                 lambda: host.secrets.adopt_private("/tmp/x")):
        with pytest.raises(PrivateFileUnsupported):
            call()


def test_the_macos_secrets_still_keep_their_own_promise(tmp_path):
    """The POSIX implementation moved into the platform layer verbatim.
    This is the same guarantee, asserted from its new home."""
    if os.name == "nt":
        pytest.skip("POSIX semantics")
    from jarvis_platform.macos import secrets as mac_secrets
    path = tmp_path / "tool-token"
    fd = mac_secrets.create_private(path)
    assert fd is not None
    os.close(fd)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert mac_secrets.create_private(path) is None


# --- the explicit ACEs the CI runner actually had --------------------------

# MEASURED, by reproducing the runner's DACL on a real Windows 11 box: add
# SYSTEM, Administrators and OWNER RIGHTS to a file EXPLICITLY, run the
# lock-down, and this is what comes back -- our grant added and nothing
# removed, which is exactly what the runner reported.
_ICACLS_RUNNER = (
    "C:\\x\\tool-token RUNNERVM\\runneradmin:(F)\n"
    "                  OWNER RIGHTS:(F)\n"
    "                  BUILTIN\\Administrators:(F)\n"
    "                  NT AUTHORITY\\SYSTEM:(F)\n"
    "\n"
    "Successfully processed 1 files; Failed processing 0 files\n")

_ICACLS_RUNNER_AFTER_RESET = (
    "C:\\x\\tool-token RUNNERVM\\runneradmin:(F)\n"
    "\n"
    "Successfully processed 1 files; Failed processing 0 files\n")


def test_a_lockdown_that_left_explicit_aces_resets_and_tries_again(monkeypatch):
    """The Windows CI leg's whole failure, and the correction of a guess.

    `/inheritance:r` removes only the ACEs marked `(I)`, and `/grant:r`
    replaces the grant for the principal it NAMES. An EXPLICIT ACE for
    anybody else survives both, so the pair leaves the file exactly as wide
    as it found it. The runner's temp files carry SYSTEM, Administrators and
    OWNER RIGHTS explicitly -- NOT inherited, which is what the commit
    before this one assumed -- which is why identical code yields one ACE on
    an ordinary desktop account and four there. Sixty three of that leg's
    sixty five failures were this.

    `icacls <file> /reset` discards every explicit ACE and restores
    inheritance; the lock-down after it leaves the one ACE this module
    promises. Both halves are driven against the real tool by
    `test_the_reset_and_lockdown_really_do_this_to_a_real_file` below.
    """
    calls = []
    monkeypatch.setattr(win_secrets, "_identity",
                        ("runnervm\\runneradmin", "S-1-5-21-9"))

    def _fake_run(*argv):
        calls.append(list(argv))
        if "/grant:r" in argv or "/reset" in argv:
            return 0, "Successfully processed 1 files; Failed processing 0 files"
        # A listing: four principals until the reset, one after it.
        done = any("/reset" in c for c in calls)
        return 0, (_ICACLS_RUNNER_AFTER_RESET if done else _ICACLS_RUNNER)

    monkeypatch.setattr(win_secrets, "_run", _fake_run)
    win_secrets._lock_down("C:\\x\\tool-token")

    flags = [c[2] if len(c) > 2 else "" for c in calls]
    assert flags == ["/inheritance:r", "", "/reset", "/inheritance:r", ""], \
        "grant, verify, reset, grant, verify -- in that order"


def test_the_reset_is_a_retry_and_never_the_opening_move(monkeypatch):
    """`/reset` restores the INHERITED permissions for the moment between
    the two calls, so doing it unconditionally would briefly widen a file
    that was already narrow -- and `restrict()` is called on files that
    already hold secrets. Reached only once the verification has found the
    DACL wider than promised, it cannot widen what was not already wide.
    """
    calls = []
    monkeypatch.setattr(win_secrets, "_identity", ("desktop-abc\\ken", "S-1-5-21-9"))

    def _fake_run(*argv):
        calls.append(list(argv))
        return (0, "") if "/grant:r" in argv else (0, _ICACLS_OURS)

    monkeypatch.setattr(win_secrets, "_run", _fake_run)
    win_secrets._lock_down("C:\\data\\jarvis\\tool-token")

    assert not any("/reset" in c for c in calls), \
        "the DACL was already ours; nothing should have been reset"


def test_an_unreadable_ace_list_is_not_reset_on_a_guess(monkeypatch):
    """The other half of the same rule. A listing this module cannot parse
    says nothing about what is in it, so clearing the file's ACEs on that
    basis would be widening it on a guess. It refuses instead."""
    calls = []
    monkeypatch.setattr(win_secrets, "_identity", ("desktop-abc\\ken", "S-1-5-21-9"))

    def _fake_run(*argv):
        calls.append(list(argv))
        return (0, "") if "/grant:r" in argv else (0, "surprising\n")

    monkeypatch.setattr(win_secrets, "_run", _fake_run)
    with pytest.raises(PrivateFileUnsupported) as caught:
        win_secrets._lock_down("C:\\x\\tool-token")

    assert not any("/reset" in c for c in calls)
    assert "could not parse" in str(caught.value)


@pytest.mark.skipif(sys.platform != "win32", reason="drives the real icacls")
def test_the_reset_and_lockdown_really_do_this_to_a_real_file(tmp_path):
    """The measurement the three mocked tests above stand on.

    This file's premise is that it mocks the one subprocess seam and so runs
    anywhere; the cost is that it cannot prove the argv do what they claim.
    This one pays that cost on a real box, for the single decision where a
    wrong guess cost the Windows leg sixty three failures.

    It reproduces the runner's DACL rather than waiting to meet it again:
    the three principals are added EXPLICITLY, which is the state that
    defeats `/inheritance:r`.
    """
    token = tmp_path / "tool-token"
    token.write_bytes(b"secret")

    # S-1-5-18 SYSTEM, S-1-5-32-544 Administrators, S-1-3-4 OWNER RIGHTS.
    # By SID, because those names are localised on a non-English Windows.
    rc, out = win_secrets._run(
        "icacls", str(token), "/grant", "*S-1-5-18:(F)",
        "*S-1-5-32-544:(F)", "*S-1-3-4:(F)")
    if rc != 0:                                          # pragma: no cover
        pytest.skip(f"could not stage the explicit ACEs: {out.strip()[:200]}")

    account, _sid = win_secrets._current_identity()
    staged, _why = win_secrets._dacl_principals(token)
    assert staged is not None and len(staged) > 1, \
        "the staging must actually have produced the runner's shape"

    win_secrets._lock_down(token)

    assert win_secrets._dacl_principals(token)[0] == [account.lower()], \
        "one ACE, this user, against the real tool"
    assert token.read_bytes() == b"secret", "and the contents are untouched"


# --- the fallback branch, measured on a real box 2026-09-09 ----------------
#
# `_terminal_argv`'s cmd.exe branch had never been run. It composed
#     cd /d "<dir>" && <command>
# into ONE argv element, and Python's list2cmdline then escaped those quotes
# as \" — CommandLineToArgvW's convention, which cmd.exe does not share; it
# has no escape character inside quotes, as this module's own docstring says.
# Measured: cmd answered "The filename, directory name, or volume label
# syntax is incorrect.", `cd /d` failed, `&&` short-circuited, the command
# never ran, and `_spawn` still reported success.
#
# The cure is to stop making the directory into text. It travels as the
# spawn's `cwd`, the way it travels as `-d` on the wt branch, so there is
# nothing left to quote. The tests below pin the RULE rather than a string.

def _only(name_wanted):
    return lambda name: rf"C:\{name}" if name == name_wanted else None


def test_no_argv_element_the_terminal_is_launched_with_contains_a_quote(monkeypatch):
    """The root cause, as the rule that prevents it.

    Python escapes an embedded `"` as `\\"` when it builds the command line,
    and cmd.exe reads `\\"` literally — so any argv element carrying a quote
    arrives corrupted. Nothing here may contain one.
    """
    for which_name in ("wt.exe", "cmd.exe"):
        monkeypatch.setattr(win_launcher.shutil, "which", _only(which_name))
        argv = win_launcher._terminal_argv(r"D:\work\a b\c", "npm test")
        offenders = [a for a in argv if '"' in a]
        assert not offenders, (
            f"[{which_name}] these carry a quote Python will escape as "
            f'\\" and cmd.exe will read literally: {offenders!r}')


def test_the_cmd_fallback_does_not_put_the_directory_in_a_shell_line(monkeypatch):
    """No `cd` at all. The directory is data, not text — same property the
    wt branch gets from `-d`."""
    monkeypatch.setattr(win_launcher.shutil, "which", _only("cmd.exe"))
    argv = win_launcher._terminal_argv(r"D:\work\chitauri", "npm test")
    assert argv == [r"C:\cmd.exe", "/k", "npm test"]
    assert not any("cd " in a for a in argv)


def test_the_cmd_fallback_with_no_command_is_just_the_shell(monkeypatch):
    monkeypatch.setattr(win_launcher.shutil, "which", _only("cmd.exe"))
    assert win_launcher._terminal_argv(r"D:\work\chitauri", "") == [r"C:\cmd.exe"]


def _spawn_recorder(monkeypatch):
    seen = {}

    async def fake_spawn(argv, cwd=None, new_console=False):
        seen.update(argv=argv, cwd=cwd, new_console=new_console)
        return True, ""

    monkeypatch.setattr(win_launcher, "_spawn", fake_spawn)
    return seen


def test_terminal_hands_the_directory_to_the_spawn_as_cwd(monkeypatch):
    """Where the directory actually goes now."""
    monkeypatch.setattr(win_launcher.shutil, "which", _only("cmd.exe"))
    seen = _spawn_recorder(monkeypatch)
    asyncio.run(win_launcher.terminal(cwd=r"D:\work\a b\c", command="npm test"))
    assert seen["cwd"] == r"D:\work\a b\c"


def test_the_cmd_fallback_asks_for_a_console_of_its_own(monkeypatch):
    """`cmd.exe` spawned the ordinary way is a headless child with its stdio
    on a pipe — measured, its prompt arrived on the PARENT's pipe. That is
    not a terminal the user can see or type into, so the fallback must ask
    for a new console."""
    monkeypatch.setattr(win_launcher.shutil, "which", _only("cmd.exe"))
    seen = _spawn_recorder(monkeypatch)
    asyncio.run(win_launcher.terminal(cwd=r"D:\work\x", command=""))
    assert seen["new_console"] is True


def test_windows_terminal_does_not_ask_for_a_console(monkeypatch):
    """`wt` draws its own window; a console handed to it would be a stray
    empty one."""
    monkeypatch.setattr(win_launcher.shutil, "which", _only("wt.exe"))
    seen = _spawn_recorder(monkeypatch)
    asyncio.run(win_launcher.terminal(cwd=r"D:\work\x", command=""))
    assert seen["new_console"] is False


def test_the_wt_branch_is_not_also_given_a_cwd(monkeypatch):
    """`wt` carries the directory as `-d` already. Handing it `cwd` as well
    would change nothing except for a directory that does not exist, where
    the spawn would begin failing on the branch that has always worked —
    a behaviour change bought for no benefit."""
    monkeypatch.setattr(win_launcher.shutil, "which", _only("wt.exe"))
    seen = _spawn_recorder(monkeypatch)
    asyncio.run(win_launcher.terminal(cwd=r"D:\work\x", command=""))
    assert seen["cwd"] is None
    assert seen["argv"] == [r"C:\wt.exe", "-d", r"D:\work\x"]


@pytest.mark.asyncio
async def test_the_new_console_spawn_does_not_pass_a_windows_only_flag_off_windows(monkeypatch):
    """`creationflags` raises ValueError on POSIX — not an OSError, so it
    would escape `_spawn`'s handler. And it IS reachable off Windows: the
    flag is asked for when there is no `wt.exe` on PATH, and there is none
    on a Mac, where this whole file runs on the macOS gate."""
    seen = {}

    async def fake_exec(*argv, **kw):
        seen.update(kw)

        class P:
            returncode = 0
        return P()

    monkeypatch.setattr(win_launcher.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(win_launcher, "_CREATE_NEW_CONSOLE", 0)   # i.e. POSIX
    ok, err = await win_launcher._spawn(["x"], cwd=None, new_console=True)
    assert ok, err
    assert "creationflags" not in seen, (
        "passed a Windows-only argument on a platform that rejects it")


def test_the_path_is_recognised_however_the_console_spelled_it():
    """A user called Š: icacls prints the folder as the console's code page
    can ("S"), so the path's TEXT no longer leads the line -- only its length
    does. Read by text alone, the whole first line became a "principal" and
    JARVIS refused the token it had just created."""
    path = "/".join(["C:", "Users", "Lukáš Š", "data", "jarvis", "tool-token"]).replace("/", chr(92))
    printed = path.replace("š", "s").replace("Š", "S")
    assert printed != path and len(printed) == len(path)
    listing = f"{printed} DESKTOP-ABC{chr(92)}Lukás S:(F)\n\nSuccessfully processed 1 files\n"
    assert win_secrets._parse_aces(listing, path) == [f"desktop-abc{chr(92)}lukás s"]
