"""The platform layer: what this machine can do, and what that withdraws.

On macOS every capability is declared, so nothing is ever withdrawn here and
the machinery would go completely unexercised on the machine it was written
on. That is what `fake_host` is for — the first proof that a tool can be
taken away cleanly must not arrive on Windows, on the day the whole port
depends on it.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import brain
import jarvis_mcp
import jarvis_platform as jp
from jarvis_platform.base import TOOL_CAPABILITIES
from jarvis_platform.fake import fake_host


@pytest.fixture
def as_host(monkeypatch):
    """Run the rest of the test as if this were a different machine."""
    def _use(host):
        monkeypatch.setattr(jp, "_HOST", host)
        return host
    return _use


# --- the macOS baseline: nothing changes ----------------------------------

def test_macos_declares_every_capability_so_nothing_is_withdrawn():
    """The acceptance criterion for the whole refactor: on a Mac, this
    layer is invisible."""
    from jarvis_platform.macos import MACOS
    assert MACOS.capabilities == jp.ALL_CAPABILITIES
    assert MACOS.withdrawn_tools() == frozenset()
    assert MACOS.filter_tools(sorted(TOOL_CAPABILITIES)) == sorted(TOOL_CAPABILITIES)


def test_an_unknown_platform_declares_nothing_rather_than_guessing():
    """JARVIS still starts — the portable two thirds of him work — but he
    claims nothing he has not been shown to do."""
    host = jp.Host(name="plan9", capabilities=frozenset())
    assert host.withdrawn_tools() == frozenset(TOOL_CAPABILITIES)
    assert host.allows_tool("spawn_run") is True     # portable, still offered


# --- withdrawal ------------------------------------------------------------

def test_a_portable_tool_is_never_withdrawn():
    """Portable is the default, so a new tool has to opt IN to being
    platform-bound. Forgetting to list one leaves it working everywhere
    rather than silently dead on the platform nobody tested."""
    barren = fake_host(capabilities=frozenset())
    for tool in ("spawn_run", "remember", "read_file", "list_sessions",
                 "usage_status", "read_page"):
        assert barren.allows_tool(tool) is True, tool


def test_losing_one_capability_withdraws_exactly_its_tools():
    host = fake_host(without={jp.CAP_TERMINAL})
    assert host.withdrawn_tools() == {"open_in_terminal", "run_command"}
    assert host.allows_tool("open_in_browser") is True


def test_every_capability_gated_tool_is_a_tool_that_exists(as_host):
    """A typo in TOOL_CAPABILITIES withdraws nothing and says nothing.

    Checked against the server's own registry rather than a second list, so
    renaming a tool and forgetting the mapping fails here.
    """
    import server
    for name in TOOL_CAPABILITIES:
        assert name in server.TOOL_HANDLERS, f"{name} is not a registered tool"


# --- consumer 1: the brain's --tools allowlist -----------------------------

def test_granted_tools_drops_what_the_platform_cannot_do(as_host):
    as_host(fake_host(without={jp.CAP_SCREEN_CAPTURE, jp.CAP_DIALOG_KEY}))
    granted = brain.granted_tools([])
    assert "mcp__jarvis__look_at_screen" not in granted
    assert "mcp__jarvis__look_at_window" not in granted
    assert "mcp__jarvis__answer_dialog" not in granted
    assert "mcp__jarvis__what_is_on_screen" in granted     # window list survives
    assert "mcp__jarvis__spawn_run" in granted


def test_granted_tools_never_withdraws_what_is_not_ours(as_host):
    """The CLI's own tools and a user's declared server are not JARVIS's to
    withdraw on platform grounds."""
    as_host(fake_host(capabilities=frozenset()))
    granted = brain.granted_tools(["notion"])
    assert "WebSearch" in granted and "WebFetch" in granted
    assert "mcp__notion" in granted


# --- consumer 2: the MCP child's tools/list --------------------------------

def test_the_mcp_child_offers_everything_when_nothing_is_disabled(monkeypatch):
    monkeypatch.delenv("JARVIS_DISABLED_TOOLS", raising=False)
    assert jarvis_mcp.offered_tools() is jarvis_mcp.TOOL_SPECS


def test_the_mcp_child_drops_disabled_tools_from_its_schema(monkeypatch):
    """A withdrawn tool costs no schema on any turn — which is the whole
    reason withdrawal beats a handler that answers 'not supported'."""
    monkeypatch.setenv("JARVIS_DISABLED_TOOLS", "answer_dialog, look_at_screen")
    offered = {spec["name"] for spec in jarvis_mcp.offered_tools()}
    assert "answer_dialog" not in offered and "look_at_screen" not in offered
    assert "spawn_run" in offered


def test_the_mcp_child_ignores_a_blank_or_padded_variable(monkeypatch):
    monkeypatch.setenv("JARVIS_DISABLED_TOOLS", " , ,")
    assert jarvis_mcp.disabled_tools() == set()


# --- consumer 3: the generated mcp.json ------------------------------------

def test_mcp_config_says_nothing_when_the_platform_can_do_everything(
        monkeypatch, tmp_path, as_host):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import server
    importlib.reload(server)
    # A complete host is INSTALLED rather than assumed. The name of this test
    # is a condition, and a machine that withdraws anything does not meet it —
    # so on Windows this asserted the opposite of what it says. Its twin below
    # installs an incomplete host in exactly the same way.
    as_host(fake_host())
    import json
    home = server.data_paths.brain_home()
    home.mkdir(parents=True, exist_ok=True)
    path = server._write_mcp_config(home)
    env = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jarvis"]["env"]
    assert "JARVIS_DISABLED_TOOLS" not in env


def test_mcp_config_carries_the_withdrawn_list_to_the_child(
        monkeypatch, tmp_path, as_host):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import server
    importlib.reload(server)
    as_host(fake_host(without={jp.CAP_DIALOG_KEY}))
    import json
    home = server.data_paths.brain_home()
    home.mkdir(parents=True, exist_ok=True)
    path = server._write_mcp_config(home)
    env = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jarvis"]["env"]
    assert env["JARVIS_DISABLED_TOOLS"] == "answer_dialog"


# --- consumer 4: the dispatch gate -----------------------------------------

def test_a_withdrawn_tool_is_refused_at_the_endpoint(monkeypatch, tmp_path,
                                                     as_host):
    """Belt and braces behind the two lists above: a brain older than the
    current capability set, or something calling /internal/tool directly,
    still cannot reach a handler this machine cannot honour."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import server
    importlib.reload(server)
    token = server.data_paths.ensure_tool_token()
    as_host(fake_host(without={jp.CAP_WINDOW_LIST}))

    with TestClient(server.app,
                    headers={"Origin": "http://localhost:5173"}) as c:
        r = c.post("/internal/tool",
                   json={"tool": "what_is_on_screen", "arguments": {}},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "unknown tool" in r.json()["text"].lower()


# --- consumer 5: preflight -------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_skips_checks_for_capabilities_this_platform_lacks(
        as_host, monkeypatch):
    """A permission check for a permission the platform does not have is a
    category error, not a warning — and a check that warns forever on a
    healthy machine teaches people to ignore the preflight."""
    import preflight
    as_host(fake_host(without={jp.CAP_DIALOG_KEY, jp.CAP_SCREEN_CAPTURE}))

    async def _fake_subprocess(*args, **kwargs):
        return 0, "", ""
    monkeypatch.setattr(preflight, "_run_subprocess", _fake_subprocess)

    checks = await preflight.run_checks(timeout=0.5)
    names = {c.name for c in checks}
    assert "accessibility" not in names
    assert "screen_recording" not in names
    assert "claude_cli" in names          # unconditional checks still run


@pytest.mark.asyncio
async def test_preflight_runs_every_check_on_a_complete_host(as_host, monkeypatch):
    import preflight
    as_host(fake_host())

    async def _fake_subprocess(*args, **kwargs):
        return 0, "", ""
    monkeypatch.setattr(preflight, "_run_subprocess", _fake_subprocess)

    names = {c.name for c in await preflight.run_checks(timeout=0.5)}
    assert {"accessibility", "screen_recording"} <= names


# --- the sub-interfaces ----------------------------------------------------

def test_the_macos_host_carries_the_real_notifications_module():
    from jarvis_platform.macos import MACOS, notifications
    assert MACOS.notifications is notifications


@pytest.mark.asyncio
async def test_a_host_without_notifications_declines_rather_than_raising():
    """The announcement path calls this when nothing is listening on the
    voice channel and must never be broken by it — so an unbuilt platform
    answers "nobody was told", which is true, instead of throwing into the
    watcher."""
    host = jp.Host(name="plan9", capabilities=frozenset())
    assert host.notifications.available() is False
    assert await host.notifications.notify("t", "m", subtitle="s") is False


def test_the_macos_host_carries_the_real_launcher():
    from jarvis_platform.macos import MACOS, launcher
    assert MACOS.launcher is launcher


@pytest.mark.asyncio
async def test_the_launcher_quotes_its_own_cwd_and_leaves_the_command_alone():
    """Quoting belongs to the shell being quoted for, which is why `terminal`
    takes cwd and command apart rather than a composed line. The command is
    deliberately NOT quoted — a start command is meant to be a command, and
    its callers have already been through `builds.command_problem`."""
    import shlex
    from jarvis_platform.macos import launcher

    seen = {}

    async def _fake_exec(*argv, **kwargs):
        seen["script"] = argv[2]
        raise OSError("not actually running osascript")

    import asyncio as _asyncio
    real = _asyncio.create_subprocess_exec
    _asyncio.create_subprocess_exec = _fake_exec
    try:
        with pytest.raises(OSError):
            await launcher.terminal(cwd="/tmp/a b", command="npm run dev")
    finally:
        _asyncio.create_subprocess_exec = real

    assert f"cd {shlex.quote('/tmp/a b')} && npm run dev" in seen["script"]


@pytest.mark.asyncio
async def test_a_host_without_a_launcher_refuses_in_the_shape_callers_expect():
    """Unreachable behind the capability gate, but it must refuse the way
    every caller already handles rather than raising."""
    host = jp.Host(name="plan9", capabilities=frozenset())
    for result in (await host.launcher.terminal(cwd="/tmp"),
                   await host.launcher.browser("https://example.com"),
                   await host.launcher.editor("/tmp/x")):
        assert result["success"] is False
        assert result["confirmation"]


def test_the_macos_host_carries_the_real_dialogs():
    from jarvis_platform.macos import MACOS, dialogs
    assert MACOS.dialogs is dialogs


def test_the_closed_vocabulary_is_the_protocol_not_one_platforms_idea():
    """It lives in `base` because a second implementation does not get to
    widen it. Return, Escape, one digit — and nothing that merely starts
    with an accepted word."""
    from jarvis_platform import base
    assert base.normalize_key("yes") == "return"
    assert base.normalize_key("  ESC ") == "escape"
    assert base.normalize_key("7") == "7"
    for refused in ("yes please", "return true", "", "0", "10", "rm -rf /",
                    None, 3, "\n", "return; rm -rf /"):
        assert base.normalize_key(refused) is None, refused

    # Surrounding whitespace is stripped, and that is safe rather than lax:
    # what comes back is one of the fixed tokens, never a substring of the
    # input. Nothing the caller wrote can reach the script it composes.
    assert base.normalize_key("y\n") == "return"
    assert base.normalize_key(" 3 ") == "3"
    allowed = {"return", "escape", *"123456789"}
    for candidate in ("y\n", " 3 ", "ESC", "Enter", "cancel", "no"):
        out = base.normalize_key(candidate)
        assert out is None or out in allowed, (candidate, out)


@pytest.mark.asyncio
async def test_a_host_without_dialogs_presses_nothing_and_says_not_found():
    """NOT_FOUND is what server.py already speaks as 'another application is
    hosting it, so that one needs your own hand' — true, and the right thing
    to say. It must never be upgraded into a best guess."""
    from jarvis_platform import base
    host = jp.Host(name="plan9", capabilities=frozenset())
    assert await host.dialogs.terminal_of(4242) is None
    assert await host.dialogs.answer(4242, "return") == base.NOT_FOUND


def test_the_outcome_wire_values_are_unchanged():
    """They are written into the steer audit table, so a rename orphans
    history. `no_tty` is read as 'no terminal of its own', not literally as
    a POSIX tty — Windows consoles have none and the outcome still applies."""
    from jarvis_platform import base
    assert (base.SENT, base.NO_TERMINAL, base.NOT_FOUND,
            base.NOT_PERMITTED, base.FAILED, base.BAD_KEY) == \
        ("sent", "no_tty", "not_found", "not_permitted", "failed", "bad_key")


def test_the_macos_host_carries_the_real_screen():
    from jarvis_platform.macos import MACOS, screen
    assert MACOS.screen is screen


def test_every_sub_interface_is_wired_on_the_macos_host():
    """The four modules that moved. A host missing one silently falls back
    to a null object that refuses everything, which would look like a
    permissions problem rather than a wiring mistake."""
    from jarvis_platform import base
    from jarvis_platform.macos import MACOS
    for name, null in (("notifications", base.NO_NOTIFICATIONS),
                       ("launcher", base.NO_LAUNCHER),
                       ("dialogs", base.NO_DIALOGS),
                       ("screen", base.NO_SCREEN)):
        assert getattr(MACOS, name) is not null, name


@pytest.mark.asyncio
async def test_a_host_without_a_screen_refuses_with_something_speakable():
    from jarvis_platform import base
    host = jp.Host(name="plan9", capabilities=frozenset())
    assert host.screen.permission_granted() is None
    for call in (host.screen.capture(), host.screen.windows()):
        with pytest.raises(base.ScreenError) as caught:
            await call
        assert str(caught.value).endswith("sir")


def test_no_permission_needed_means_granted_not_unknown():
    """A platform that requires no such permission answers True. None is
    reserved for "the probe itself could not be run", which is the only
    case a caller should report as 'could not determine' — otherwise
    preflight warns forever on a machine where nothing is wrong."""
    from jarvis_platform.base import Screen
    assert Screen.permission_granted.__doc__ is not None
    doc = Screen.permission_granted.__doc__
    assert "returns True, not None" in doc
    assert "could not be run" in doc


def test_fake_host_keeps_the_real_providers_it_was_not_asked_about():
    """Learned the hard way. A test installing a recording launcher used to
    lose `secrets` with it, and every server test calls `ensure_tool_token`
    at boot — so the failure surfaced nowhere near the provider that had
    actually been swapped."""
    from jarvis_platform import base
    from jarvis_platform.fake import fake_host

    recorder = object()
    host = fake_host(launcher=recorder)
    assert host.launcher is recorder
    # THIS machine's providers, not macOS's. `fake_host` keeps "the REAL
    # host's" — its own words — and the real host is whichever one is running
    # the suite. Naming MACOS asserted the property only on a Mac and
    # asserted something plainly false anywhere else.
    real = jp.current()
    assert host.secrets is real.secrets
    assert host.screen is real.screen
    assert host.notifications is real.notifications
    assert host.dialogs is real.dialogs

    # ...and a test that genuinely wants one absent says so.
    assert fake_host(secrets=base.NO_SECRETS).secrets is base.NO_SECRETS
