"""safety_guard.py — refuse commands that would wreck the machine.

Runs JARVIS starts are `claude --dangerously-skip-permissions`: nothing asks
before a command runs. A misheard sentence, a prank, or text on a web page
could ask one to "delete system32". This is the floor under that:

  * `destructive_reason(text)` — used by server.py to refuse a spawn_run,
    start_build or steer_session whose wording asks for it, before anything
    starts.
  * As a script, a Claude Code PreToolUse hook (wired into every run by
    run_executor.py via --settings). Hooks run in every permission mode, so
    this still blocks the command when permission prompts are skipped.
    Exit code 2 blocks the tool call and tells the run why.

It is a deny-list of the clearly catastrophic — deleting the OS, a whole
drive or the user's home, formatting or repartitioning disks, wiping boot
config or shadow copies — not a sandbox. Ordinary project work is untouched.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Optional

_SYS = r"(?:[a-z]:[\\/]+windows\b|system32|syswow64|%systemroot%|%windir%|\$env:(?:systemroot|windir)|/mnt/[a-z]/windows\b)"
_ROOT = r"(?:[a-z]:[\\/]*\*?(?=[\s\"']|$)|/\*?(?=[\s\"']|$)|~/?\*?(?=[\s\"']|$)|\$home\b|%userprofile%(?=[\\/]?[\s\"']|$)|\$env:userprofile(?=[\\/]?[\s\"']|$))"
_DELETE = r"(?:\brm\b|\bdel\b|\berase\b|\brd\b|\brmdir\b|remove-item|\bri\b|\brmdir\b|shutil\.rmtree|os\.remove|unlink)"

_COMMAND_RULES: list[tuple[str, str]] = [
    (rf"{_DELETE}[^\n|;&]*{_SYS}", "deleting Windows system files"),
    (rf"{_DELETE}[^\n|;&]*\s[\"']?{_ROOT}", "deleting a whole drive or home folder"),
    (r"\bformat(?:\.com)?\s+[a-z]:", "formatting a drive"),
    (r"\b(?:format-volume|clear-disk|remove-partition|initialize-disk)\b", "erasing or repartitioning a disk"),
    (r"\bdiskpart\b", "repartitioning disks"),
    (r"\bmkfs(?:\.\w+)?\b|\bdd\b[^\n]*\bof=/dev/", "overwriting a disk"),
    (r"\bcipher\s+/w", "wiping free space"),
    (r"\bbcdedit\b", "changing the boot configuration"),
    (r"\bvssadmin\b[^\n]*\bdelete\b|\bwmic\b[^\n]*shadowcopy[^\n]*delete", "deleting system restore points"),
    (r"\breg(?:\.exe)?\s+delete\s+hk(?:lm|ey_local_machine)", "deleting system registry keys"),
    (r"\b(?:takeown|icacls)\b[^\n]*{0}".format(_SYS), "taking over Windows system files"),
]

_INTENT_RULES: list[tuple[str, str]] = [
    (r"\b(?:delete|remove|wipe|erase|destroy|nuke|rm)\b[^.!?\n]{0,40}\b(?:sys(?:tem)?\s?32|system\s?files|windows\s+folder|the\s+windows\s+directory|c:[\\/]*windows)",
     "deleting Windows system files"),
    (r"\b(?:format|wipe|erase|nuke)\b[^.!?\n]{0,25}\b(?:c\s?drive|c:|(?:my|the|whole|entire)\s+(?:hard\s+)?(?:drive|disk|ssd|computer|pc|system))",
     "wiping a drive"),
    (r"\b(?:delete|remove|wipe|erase)\b[^.!?\n]{0,20}\b(?:everything|all\s+(?:my\s+)?files)\b[^.!?\n]{0,25}\b(?:computer|pc|drive|disk|c:)",
     "deleting everything on the computer"),
]

_PROTECTED_WRITE = re.compile(rf"^\s*{_SYS}", re.IGNORECASE)


def destructive_reason(text: str) -> Optional[str]:
    """Why `text` (a command or a request in words) is refused, or None."""
    t = (text or "").lower()
    for pattern, why in _COMMAND_RULES + _INTENT_RULES:
        if re.search(pattern, t, re.IGNORECASE):
            return why
    return None


def _hook() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0                       # not ours to judge; never break the run
    tool = str(event.get("tool_name") or "")
    args = event.get("tool_input") or {}
    reason = None
    if tool in ("Bash", "PowerShell"):
        reason = destructive_reason(str(args.get("command") or ""))
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        if _PROTECTED_WRITE.search(path):
            reason = "writing into the Windows system folder"
    if reason:
        print(f"Blocked by JARVIS's safety guard: this would mean {reason}. "
              f"That is never allowed from a JARVIS run; tell the user instead.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_hook())
