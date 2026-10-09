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


def test_create_file_writes_only_new_files_in_the_users_own_folders(tmp_path, monkeypatch):
    import pc_control
    monkeypatch.setattr(pc_control.Path, "home", classmethod(lambda cls: tmp_path))
    made = pc_control.create_file("Desktop/notes.txt", "milk")
    assert made == str(tmp_path / "Desktop" / "notes.txt")
    assert (tmp_path / "Desktop" / "notes.txt").read_text(encoding="utf-8") == "milk"
    for bad in ("Desktop/notes.txt",                       # exists: never overwritten
                ".ssh/authorized_keys",                    # a dot-folder
                "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/x.bat",
                str(tmp_path.parent / "elsewhere.txt"),    # same drive, outside home
                r"C:\Windows\System32\drivers\etc\hosts2",
                "Desktop/setup.exe"):
        with pytest.raises(pc_control.PcError):
            pc_control.create_file(bad, "x")
    assert (tmp_path / "Desktop" / "notes.txt").read_text(encoding="utf-8") == "milk"


def test_files_are_found_by_name_and_only_moved_or_grown_in_the_users_folders(tmp_path, monkeypatch):
    import pc_control
    monkeypatch.setattr(pc_control.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Documents").mkdir()
    lst = tmp_path / "Documents" / "Shopping List.txt"
    lst.write_text("eggs", encoding="utf-8")
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "setup.exe").write_bytes(b"MZ")

    assert pc_control.find_files("shopping list") == [lst]
    pc_control.append_file("shopping list", "milk")          # by name, no listing first
    assert lst.read_text(encoding="utf-8") == "eggs\nmilk\n"

    moved = pc_control.move_file("shopping list", "groceries")   # a bare name renames in place
    assert moved == str(tmp_path / "Documents" / "groceries.txt")
    with pytest.raises(pc_control.PcError):
        pc_control.move_file("groceries", ".ssh/x.txt")
    with pytest.raises(pc_control.PcError):
        pc_control.open_file("setup")                        # a program is never "opened"


def test_only_his_name_with_a_stop_word_and_nothing_else_stops_him():
    import server
    for said in ("Jarvis, stop.", "stop, Jarvis!", "Hey Jarvis, shut up", "Jervis stop talking"):
        assert server._is_voice_stop(said), said
    for said in ("stop", "Jarvis, stop the music", "Travis be quiet", "that's enough"):
        assert not server._is_voice_stop(said), said


def test_notes_are_dated_lines_in_one_file_and_read_back_by_day(tmp_path, monkeypatch):
    import pc_control
    monkeypatch.setattr(pc_control.Path, "home", classmethod(lambda cls: tmp_path))
    assert pc_control.notes_read() == []
    pc_control.note_add("buy a   new mouse")
    pc_control.note_add("call the bank")
    today = pc_control.notes_read()
    assert len(today) == 2 and today[0].endswith("buy a new mouse")
    notes = tmp_path / pc_control.NOTES_FILE
    notes.write_text("- [2020-01-01 09:00] ancient\n" + notes.read_text(encoding="utf-8"),
                     encoding="utf-8")
    assert len(pc_control.notes_read()) == 2            # not the old one
    with pytest.raises(pc_control.PcError):
        pc_control.note_add("   ")


def test_a_reminder_is_on_disk_until_it_is_said_and_comes_back_after_a_restart(tmp_path, monkeypatch):
    import asyncio
    import time
    import server
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    scheduled = []
    monkeypatch.setattr(server, "_schedule_reminder",
                        lambda due, message, missed=False: scheduled.append((message, missed)))
    said = asyncio.run(server.tool_set_reminder({"message": "tea", "minutes": 5}))
    assert said.startswith("Reminder set for")
    assert [r["message"] for r in server._saved_reminders()] == ["tea"]

    now = time.time()
    server._save_reminders(server._saved_reminders() + [
        {"due": now - 60, "message": "missed while off"},
        {"due": now - 3 * 86400, "message": "days stale"}])
    scheduled.clear()
    server._restore_reminders()                          # "the server started again"
    assert sorted(scheduled) == [("missed while off", True), ("tea", False)]
    assert "days stale" not in [r["message"] for r in server._saved_reminders()]
