"""The floor under unattended runs: catastrophic commands are refused,
ordinary project work is not."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import safety_guard

BLOCKED = [
    "del /s /q C:\\Windows\\System32",
    "rd /s /q C:\\Windows",
    "Remove-Item -Recurse -Force C:\\Windows\\System32",
    "Remove-Item -Recurse -Force $env:windir",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /mnt/c/Windows",
    "Remove-Item -Recurse -Force C:\\",
    "del /f /s /q C:\\*",
    "format C: /q",
    "diskpart /s wipe.txt",
    "Clear-Disk -Number 0 -RemoveData",
    "cipher /w:C:",
    "bcdedit /delete {current}",
    "vssadmin delete shadows /all /quiet",
    "reg delete HKLM\\SYSTEM /f",
    "takeown /f C:\\Windows\\System32 /r",
    "delete system32",
    "Jarvis, please delete sys32 for me",
    "wipe my whole hard drive",
    "format the C drive",
    "delete all my files on the computer",
]

ALLOWED = [
    "npm run dev",
    "rm -rf node_modules",
    "rm -rf dist build",
    "del /q build\\*.log",
    "Remove-Item -Recurse -Force .\\dist",
    "python manage.py migrate",
    "git rm --cached secrets.txt",
    "Add a button that formats the date",
    "Build me a to-do app with a delete button",
    "Fix the bug in the system settings page",
    "echo C:\\Windows is where Windows lives",
]


@pytest.mark.parametrize("text", BLOCKED)
def test_catastrophic_requests_are_refused(text):
    assert safety_guard.destructive_reason(text), text


@pytest.mark.parametrize("text", ALLOWED)
def test_ordinary_work_is_not(text):
    assert safety_guard.destructive_reason(text) is None, text


def _run_hook(event: dict) -> subprocess.CompletedProcess:
    script = Path(safety_guard.__file__)
    return subprocess.run([sys.executable, str(script)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=30)


def test_the_hook_blocks_a_destructive_bash_command():
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
    assert r.returncode == 2 and "safety guard" in r.stderr


def test_the_hook_blocks_a_write_into_system32():
    r = _run_hook({"tool_name": "Write",
                   "tool_input": {"file_path": "C:\\Windows\\System32\\drivers\\etc\\hosts"}})
    assert r.returncode == 2


def test_the_hook_lets_ordinary_commands_through():
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "npm test"}})
    assert r.returncode == 0


def test_run_settings_reach_the_cli_as_a_file_not_as_json():
    # cmd.exe (the claude.cmd shim) reads a `|` in an argument as a pipe.
    import json
    import run_executor
    arg = run_executor._safety_settings()
    assert "|" not in arg and '"' not in arg
    with open(arg, encoding="utf-8") as f:
        assert "Edit" in json.load(f)["hooks"]["PreToolUse"][0]["matcher"]
