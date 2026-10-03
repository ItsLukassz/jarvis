"""The PC-control tools: what they refuse, and what a reminder accepts.

Nothing here opens, closes or changes anything on the machine — the calls
that would are exercised only with names that match nothing.
"""
import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="pc_control.py is Win32")


@pytest.fixture
def server():
    import server as srv
    yield srv
    for task in list(srv._reminders):
        task.cancel()


def run(coro):
    return asyncio.run(coro)


def test_only_what_the_start_menu_lists_can_be_opened(server):
    said = run(server.tool_open_app({"name": "no-such-app-xyz"}))
    assert "Start menu" in said
    # A path or a command is not a Start-menu entry, so it opens nothing.
    assert "Start menu" in run(server.tool_open_app({"name": r"C:\Windows\System32\cmd.exe"}))


def test_unknown_windows_devices_and_actions_are_refused_in_a_sentence(server):
    assert "can't find" in run(server.tool_close_app({"name": "no-such-app-xyz"}))
    assert "can't find" in run(server.tool_set_audio_output({"device": "no-such-device-xyz"}))
    assert "play or pause" in run(server.tool_media_control({"action": "explode"}))
    assert "0 to 100" in run(server.tool_set_volume({"percent": "loud"}))


@pytest.mark.parametrize("args,expect", [
    ({"minutes": 20, "message": "check the oven"}, "Reminder set for"),
    ({"at": "18:30", "message": "call home"}, "Reminder set for 18:30"),
    ({"message": "no time given"}, "minutes or a time"),
    ({"at": "half six", "message": "x"}, "minutes or a time"),
    ({"minutes": 99999, "message": "x"}, "up to a day"),
    ({"minutes": -5, "message": "x"}, "up to a day"),
    ({"minutes": 5, "message": "   "}, "What should I remind"),
])
def test_a_reminder_needs_a_time_within_a_day_and_something_to_say(server, args, expect):
    async def go():
        return await server.tool_set_reminder(args)
    assert expect in run(go())


def test_every_pc_tool_is_an_acting_tool_and_only_offered_where_it_works(server):
    import jarvis_platform as jp
    pc_tools = {t for t, cap in jp.TOOL_CAPABILITIES.items() if cap == jp.CAP_PC_CONTROL}
    assert pc_tools and pc_tools <= server.ACTING_TOOLS
    assert pc_tools <= set(server.TOOL_HANDLERS)
    assert "read_clipboard" in server.TAINTING_TOOLS, "copied text is somebody else's words"
