import asyncio

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _never_spawn_a_real_brain(monkeypatch):
    """server.lifespan builds the brain but must not start `claude` under test."""
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")


@pytest.fixture(autouse=True)
def _never_really_synthesise(monkeypatch):
    """No test may spawn a real synthesiser.

    `say` and `piper` are subprocesses like `osascript` and `claude`, and the
    suite fakes those at the seam. This became load-bearing when a failing
    backend started falling back to `say`: a test that mocked only the hosted
    transport would quietly run the real thing on the developer's Mac. A test
    that wants a local backend sets its own `_spawn_synth`, and monkeypatch
    lets the later one win.
    """
    import tts

    async def _blocked(argv, *, text, timeout):
        raise AssertionError(
            f"a test tried to spawn {argv[0]!r} for real; mock tts._spawn_synth")

    monkeypatch.setattr(tts, "_spawn_synth", _blocked)


@pytest.fixture(autouse=True)
def _never_post_a_real_notification(monkeypatch, request):
    """No test may spam the developer's Notification Centre.

    Patched on the notifications MODULE object rather than on `server` or on
    the host, so the `importlib.reload(server_module)` that several test
    fixtures do cannot hand the real implementation back. That is also why
    the macOS host exposes modules rather than class instances.
    test_notifier.py is exempt: it tests notify() itself and mocks the
    subprocess boundary directly.

    EVERY implementation is patched, not just the one this platform runs.
    Naming `jarvis_platform.macos` alone made this rail silently inert off
    macOS — `current()` is `WINDOWS` there, so the patch landed on a module
    nothing calls and the suite posted real Windows toasts throughout a run
    (observed 2026-09-08). Patching by platform is what created that hole,
    so the list is deliberately not conditioned on `sys.platform`: an
    implementation that cannot run here is patched anyway, at no cost, and
    the rail cannot rot the next time a host is added.
    """
    # Two files are exempt, and for one reason: they test `notify` ITSELF and
    # mock its own subprocess boundary, so patching it out from under them
    # replaces the thing under test with an assertion.
    #
    # `test_windows_platform` joined `test_notifier` the moment this rail
    # started patching every implementation rather than only the macOS one.
    # That change was right — it closed a hole where the rail was inert off
    # macOS — but it broke a test that calls `windows.notifications.notify`
    # directly to prove it answers False rather than raising when it cannot
    # post. On macOS that call is safe (`available()` is False there, so
    # nothing is ever spawned) and the rail turned it into the very failure
    # it exists to prevent. Caught by the macOS CI leg, which is the only
    # place it could be caught.
    if request.module.__name__.endswith(("test_notifier",
                                         "test_windows_platform")):
        return
    from jarvis_platform.macos import notifications as macos_notifier
    from jarvis_platform.windows import notifications as windows_notifier

    async def _blocked(*args, **kwargs):
        raise AssertionError("a test tried to post a real notification; mock "
                             "the notifications module for this platform")

    for module in (macos_notifier, windows_notifier):
        monkeypatch.setattr(module, "notify", _blocked)


@pytest.fixture(autouse=True)
def _no_startup_announcement(request, monkeypatch):
    """No test may have JARVIS speak at startup.

    `server._run_preflight` speaks whenever a check FAILS, and on any machine
    without `say` the voice check does fail — so under `TestClient` this fires
    in most of the suite. It is a fire-and-forget task
    (`server.py`: `_background.add(task := asyncio.create_task(...))`), and
    `TestClient` runs the app on ITS OWN THREAD, so the announcement is still
    in flight while fixtures and test bodies run on the main thread. Measured
    with a probe on 2026-09-09:

        preflight-start     asyncio-portal-...
        startup-returned    MainThread
        speech-is           SpeechScheduler
        after-first-get     -
        preflight-end       asyncio-portal-...
        synth               "3 things need attention, sir: ..."

    It outlives startup AND the first request, and it reaches
    `_synth_for_speech` for real.

    That is a cross-thread race against any test that touches voice state,
    and it cost one: `test_voice_settings.py::
    test_the_status_endpoint_shows_the_fallback`. Its `fallen_back` fixture
    sets `JARVIS_TTS_BACKEND=piper` and swaps in a fresh `_voice_fallback`
    dict; the in-flight announcement then reads the now-piper env, gets
    `say` back, and writes "piper" into that fresh dict before the first
    assertion reads it. It failed on one Windows CI run and passed on the
    next of the SAME commit, which is what a race looks like from outside.

    Patched on `preflight` rather than on `server`, because several fixtures
    `importlib.reload(server)` and would hand the real one back — the same
    reasoning as the notification rail above. `spoken_summary` rather than
    `run_checks`, because the harm is the SPEECH: the checks still run, so a
    test watching what preflight concluded sees exactly what it saw before.

    Two files are exempt, and the second one is a lesson. `test_preflight`
    tests `spoken_summary` itself. `test_preflight_runs_at_startup` exists
    to prove the announcement HAPPENS -- preflight was once dead code that
    nobody called, and an expired login reached the user as "my language
    systems are down" and nothing else, four restarts running. Silencing it
    there would delete the guard against exactly that.

    That second file was missed when this rail was written, and the full
    suite caught it. The search had been for `spoken_summary` -- the
    function being patched -- and this file never names it: it calls
    `server._run_preflight()` directly and asserts on a fake `speech`. Search
    for the BEHAVIOUR a rail suppresses, not for the symbol it patches.
    Neither exempt file starts a `TestClient`, so neither is the leak this
    guards against.
    """
    if request.module.__name__.endswith(("test_preflight",
                                         "test_preflight_runs_at_startup")):
        return
    import preflight
    monkeypatch.setattr(preflight, "spoken_summary", lambda checks: "")


@pytest.fixture(autouse=True)
def _never_touch_the_real_projects_folder(monkeypatch, tmp_path):
    """No test may create a directory in the user's real ~/Projects.

    `create_project` writes into JARVIS_PROJECTS_DIR (default ~/Projects) and
    will create that root if it is missing, so the default is redirected into
    a tmp_path for every test — the same reasoning as JARVIS_DATA_DIR. A test
    that wants its own root still sets the variable itself; this only fills in
    a safe default.
    """
    monkeypatch.setenv("JARVIS_PROJECTS_DIR", str(tmp_path / "projects-root"))


# Settings a developer may legitimately have in their own `.env` that would
# change what the suite is testing. `server.py` loads `.env` into `os.environ`
# at IMPORT, so these arrive before any fixture runs and no amount of pointing
# JARVIS_ENV_FILE somewhere else undoes them — that rail is about WRITES.
#
# Found by running the product: setting JARVIS_STT_BACKEND=whisper for a live
# test broke three tests that assert the default is `browser`. The tests were
# right and the isolation was missing, which is the more useful half — anyone
# who actually uses the local backend would have hit this on every run.
_DEVELOPER_PREFERENCES = ("JARVIS_STT_BACKEND", "JARVIS_STT_MODEL",
                          "JARVIS_TTS_BACKEND", "JARVIS_TTS_VOICE",
                          "JARVIS_PIPER_VOICE", "JARVIS_PIPER_BIN",
                          "HONORIFIC", "USER_NAME", "JARVIS_WAKE_WORD",
                          "JARVIS_WAKE_FOLLOW_UP", "JARVIS_QUIET_ORIGINS",
                          "JARVIS_STT_DEVICE", "JARVIS_SCREEN_CAPTURE",
                          "JARVIS_TOOL_ACK")


@pytest.fixture(autouse=True)
def _the_developers_own_preferences_do_not_reach_the_suite(monkeypatch):
    """A test asserts what JARVIS does by default, not what this machine does.

    A test that wants one of these still sets it itself — the same rule the
    data-dir and dotenv rails follow.
    """
    for name in _DEVELOPER_PREFERENCES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _never_write_to_the_live_dotenv(monkeypatch, tmp_path):
    """No test may write into the developer's live `.env`.

    Found the hard way: the settings endpoints write straight into the
    repository's own .env, so a test that posted a preference silently
    rewrote the developer's real configuration — and a test written to
    prove `.env` line injection injected the line for real. Same reasoning
    as JARVIS_DATA_DIR; a test that wants its own file still sets the
    variable itself.
    """
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / "dotenv" / ".env"))


@pytest.fixture(autouse=True)
def _never_write_to_the_live_data_dir(monkeypatch, tmp_path):
    """No test may write into the user's real `data/`.

    Most tests already set JARVIS_DATA_DIR (and still do — this only fills in
    a safe default), but a test that merely drives the brain writes there too
    now that a rate-limit event is persisted: without this, running the suite
    overwrote the live usage reading with a fixture's fake one.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data-dir"))


@pytest_asyncio.fixture(autouse=True)
async def _no_run_left_mid_flight():
    """No test may end with a run's driver still starting its child.

    The CI hang, twice, on the macOS runner's Python 3.12 and never on 3.13:
    a test spawned a run, asserted on the row, and returned in the same
    millisecond — while `RunExecutor._drive` was still inside
    `asyncio.create_subprocess_exec`, before the child existed. The loop's
    teardown then cancelled that task mid-spawn, and 3.12's subprocess
    transport never completes a cancellation delivered there: the suite sat
    in `_cancel_all_tasks` until GitHub killed the job 25 minutes later.
    3.13 completes it, which is why no local run ever showed it.

    So every driver alive at the end of a test is waited for here, inside
    the test's own loop, before the runner closes it. A test's fake `claude`
    exits in milliseconds, so the wait is normally nothing; a driver that
    is queued or reading forever is cancelled only after it has had time to
    get past the spawn, which is the one place cancellation must not land.
    """
    yield
    me = asyncio.current_task()

    def _alive(qualname: str) -> list:
        return [t for t in asyncio.all_tasks()
                if t is not me and not t.done()
                and getattr(t.get_coro(), "__qualname__", "") == qualname]

    # First, the exact place: asyncio's own pipe-connection task, which
    # exists only between fork and "the child is up". Whoever spawned it
    # (a run driver, the brain, a fake `osascript`) is parked on it. Let it
    # finish — milliseconds — and yield once so the spawner moves on.
    connecting = _alive("BaseSubprocessTransport._connect_pipes")
    if connecting:
        await asyncio.wait(connecting, timeout=5)
        await asyncio.sleep(0)
    # Then a run driver still going: give it time to end on its own (a
    # test's fake claude exits at once) before it is cancelled somewhere
    # safe to cancel.
    drivers = _alive("RunExecutor._drive")
    if not drivers:
        return
    _done, pending = await asyncio.wait(drivers, timeout=10)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5)


@pytest.fixture
def needs_symlinks(tmp_path):
    """Skip unless this machine will let the test BUILD a symlink.

    Creating one on Windows needs SeCreateSymbolicLinkPrivilege -- Developer
    Mode or an elevated shell -- and a stock account has neither: measured,
    `OSError [WinError 1314] A required privilege is not held by the
    client`. PROBED rather than assumed from `sys.platform`, so a machine
    with Developer Mode on runs these rather than skipping them.

    Read the skip as what it is. The tests that ask for this are refusals --
    a symlink out of a project, out of the memory folder, a symlinked data
    dir -- so on a machine that skips them, that half of containment is
    UNPROVEN. Not proven weak: the production checks resolve both sides and
    are not platform-specific, so there is no reason to expect them to fail
    here. But no evidence either, and the difference matters.
    """
    probe = tmp_path / "_symlink_probe"
    try:
        probe.symlink_to(tmp_path)
    except OSError as e:
        pytest.skip(f"cannot create a symlink to test with ({e.strerror}); "
                    "symlink containment is unproven on this machine")
    probe.unlink()


def can_name_a_file(name: str) -> bool:
    """Will this filesystem accept a file or directory with that name?

    Several tests attack through the NAME itself: a newline in it forges a
    line of JARVIS's own speech in a header line, a quote closes the
    untrusted wrapper. Windows refuses both outright -- measured, the Win32
    layer rejects control characters and a quote in a path component -- so
    there the file cannot be created and the test has nothing to attack
    with.

    Callers skip on a False, and should say what that skip means: a NARROWER
    threat surface, not an untested one. The wall in `server` is unchanged
    and every other input still exercises it; what is absent is only the
    ability to build that particular shape.
    """
    import pathlib
    import tempfile
    probe = pathlib.Path(tempfile.mkdtemp()) / name
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return False
    return True
