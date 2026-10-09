"""pc_control.py — JARVIS's hands on a Windows PC.

Open and close apps, media keys, window control, system volume and output
device, the clipboard, and a status reading. Windows only; server.py wraps
each function as a tool and the platform layer withdraws them elsewhere.

Everything here is low-stakes and reversible by design:
  * `open_app` starts only what the Start menu already lists (plus a few
    built-ins) — never a path or command the brain wrote.
  * `close_app` asks the window to close (WM_CLOSE), the same as clicking X,
    so an app with unsaved work still gets to prompt. Nothing is killed.

`PcError`'s message is a sentence JARVIS can say. Every function is blocking;
callers run them with `asyncio.to_thread`.
"""
from __future__ import annotations

import ctypes
import difflib
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

import window_capture


class PcError(Exception):
    """Its message is a sentence JARVIS can say."""


# ── apps ────────────────────────────────────────────────────────────────────

_BUILTINS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe",
    "file explorer": "explorer.exe", "explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:",
}


def _start_menu() -> dict[str, Path]:
    """Every Start-menu shortcut by lower-cased name. .url covers Steam games."""
    found: dict[str, Path] = {}
    for base in (os.getenv("APPDATA"), os.getenv("PROGRAMDATA")):
        if not base:
            continue
        root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for ext in ("*.lnk", "*.url"):
            for link in root.rglob(ext):
                name = link.stem.lower()
                if "uninstall" not in name:
                    found.setdefault(name, link)
    return found


def open_app(name: str) -> str:
    """Start an app from the Start menu. Returns the name actually opened."""
    q = " ".join((name or "").lower().split())
    if not q:
        raise PcError("Which app should I open")
    if q in _BUILTINS:
        os.startfile(_BUILTINS[q])
        return q
    apps = _start_menu()
    match = (q if q in apps else
             next((n for n in sorted(apps, key=len) if q in n), None) or
             next(iter(difflib.get_close_matches(q, apps, n=1, cutoff=0.75)), None))
    if match is None:
        raise PcError("I can't find that in the Start menu")
    os.startfile(str(apps[match]))
    return match


_WM_CLOSE = 0x0010


def close_app(name: str) -> str:
    """Ask an app's window to close, as clicking its X would."""
    win = window_capture.find(name)
    ctypes.windll.user32.PostMessageW(wintypes.HWND(win.hwnd), _WM_CLOSE, 0, 0)
    return win.app


# ── keys ────────────────────────────────────────────────────────────────────

_KEYEVENTF_KEYUP = 0x2
_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
       "alt": 0x12, "win": 0x5B, "shift": 0x10, "right": 0x27,
       "ctrl": 0x11, "enter": 0x0D, "f11": 0x7A, "f12": 0x7B}


def _tap(*keys: str) -> None:
    user32 = ctypes.windll.user32
    for k in keys:
        user32.keybd_event(_VK[k], 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(_VK[k], 0, _KEYEVENTF_KEYUP, 0)


def media(action: str) -> None:
    """play_pause / next / previous / stop — the keyboard's media keys."""
    if action not in ("play_pause", "next", "previous", "stop"):
        raise PcError("I can play or pause, skip, go back, or stop")
    _tap(action)


# ── windows ─────────────────────────────────────────────────────────────────

_SW = {"minimize": 6, "maximize": 3, "restore": 9}


def window(name: str, action: str) -> str:
    """focus / minimize / maximize / restore / move_to_other_screen."""
    win = window_capture.find(name)
    user32 = ctypes.windll.user32
    hwnd = wintypes.HWND(win.hwnd)
    if action in _SW:
        user32.ShowWindow(hwnd, _SW[action])
        return win.app
    if action not in ("focus", "move_to_other_screen"):
        raise PcError("I can focus, minimize, maximize, restore or move a window")
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW["restore"])
    _tap("alt")                      # Windows refuses SetForegroundWindow from the background otherwise
    user32.SetForegroundWindow(hwnd)
    if action == "move_to_other_screen":
        _tap("win", "shift", "right")
    return win.app


# ── sound ───────────────────────────────────────────────────────────────────

def _com():
    import comtypes
    comtypes.CoInitialize()          # per worker thread; harmless when repeated
    return comtypes


def _master_volume():
    comtypes = _com()
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                     comtypes.CLSCTX_INPROC_SERVER)
    device = enum.GetDefaultAudioEndpoint(0, 1)          # eRender, eMultimedia
    # QueryInterface, not ctypes.cast: a cast pointer is released twice and
    # takes the process down with an access violation.
    return device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL,
                           None).QueryInterface(IAudioEndpointVolume)


def volume(percent: int | None = None) -> int:
    """Set the system volume (0-100) when given; return the level either way."""
    vol = _master_volume()
    if percent is not None:
        vol.SetMasterVolumeLevelScalar(max(0, min(100, int(percent))) / 100, None)
    return round(vol.GetMasterVolumeLevelScalar() * 100)


def _outputs() -> dict[str, str]:
    """Active output devices: friendly name -> device id."""
    _com()
    from pycaw.pycaw import AudioUtilities
    return {d.FriendlyName: d.id for d in AudioUtilities.GetAllDevices()
            if d.id and d.id.startswith("{0.0.0.") and d.state.value == 1 and d.FriendlyName}


def audio_outputs() -> list[str]:
    return sorted(_outputs())


def set_audio_output(name: str) -> str:
    """Make the output device whose name contains `name` the default."""
    q = (name or "").strip().lower()
    devices = _outputs()
    match = next((n for n in sorted(devices, key=len) if q and q in n.lower()), None)
    if match is None:
        raise PcError("I can't find a sound output by that name")
    comtypes = _com()
    from pycaw.api.policyconfig import IPolicyConfig
    from pycaw.constants import CLSID_CPolicyConfigClient
    policy = comtypes.CoCreateInstance(CLSID_CPolicyConfigClient, IPolicyConfig, comtypes.CLSCTX_ALL)
    for role in (0, 1, 2):           # console, multimedia, communications
        policy.SetDefaultEndpoint(devices[match], role)
    return match


# ── clipboard ───────────────────────────────────────────────────────────────

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
_NO_WINDOW = 0x08000000              # CREATE_NO_WINDOW: no console flash


def clipboard_read() -> str:
    out = subprocess.run(_PS + ["[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                         capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    return out.stdout.decode("utf-8", errors="replace").strip()


def clipboard_write(text: str) -> None:
    subprocess.run(_PS + ["[Console]::InputEncoding=[Text.Encoding]::UTF8; "
                          "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                   input=text.encode("utf-8"), timeout=10, check=True, creationflags=_NO_WINDOW)


# ── files ───────────────────────────────────────────────────────────────────

FILE_MAX_CHARS = 200_000
# Never written into, wherever they sit in the path: the OS, installed
# programs, and the per-user folder that holds Startup and every app's config.
_NO_WRITE_DIRS = {"windows", "program files", "program files (x86)", "programdata",
                  "appdata", "$recycle.bin", "system volume information"}
# Text written under one of these names would be a broken program, not a file.
_NO_WRITE_SUFFIXES = {".exe", ".dll", ".sys", ".scr", ".msi", ".com", ".lnk"}


def _check_writable(target: Path) -> None:
    """Raise unless `target` (resolved) is somewhere JARVIS may write."""
    home = Path.home().resolve()
    if target.suffix.lower() in _NO_WRITE_SUFFIXES:
        raise PcError("I don't create files of that kind")
    if home in target.parents:
        inside = [part.lower() for part in target.relative_to(home).parts[:-1]]
        own = Path(__file__).resolve().parent
        if (set(inside) & _NO_WRITE_DIRS or any(part.startswith(".") for part in inside)
                or own == target.parent or own in target.parents):
            raise PcError("I don't touch files there")
    elif target.drive.lower() == home.drive.lower():
        raise PcError("On this drive I only work in your own folders")
    elif {part.lower() for part in target.parts} & _NO_WRITE_DIRS:
        raise PcError("I don't touch files there")


# Where "that PDF I downloaded" is looked for.
_SEARCH_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
_SEARCH_DEPTH = 3
_SEARCH_MAX_SEEN = 50_000
# Opening one of these would RUN it; open_file is for documents.
_NO_OPEN_SUFFIXES = _NO_WRITE_SUFFIXES | {".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf",
                                          ".hta", ".py", ".pyw", ".jar", ".reg", ".cpl", ".pif"}


def find_files(query: str, folder: str = "", days: float = 0) -> list[Path]:
    """Files whose NAME contains every word of `query`, newest first.

    Looked for in the user's Desktop, Documents, Downloads, Pictures, Videos
    and Music (or just `folder`, relative to home), three levels deep.
    ponytail: a bounded os.walk, not an index; Windows Search is the upgrade
    if the folders grow past what a walk covers in a second or two.
    """
    import time
    home = Path.home().resolve()
    words = (query or "").lower().split()
    if folder:
        root = (home / folder).resolve()
        if home != root and home not in root.parents:
            raise PcError("I only look in your own folders")
        roots = [root]
    else:
        roots = [home / name for name in _SEARCH_FOLDERS]
    newer_than = time.time() - days * 86400 if days and days > 0 else 0
    hits: list[tuple[float, Path]] = []
    seen = 0
    for root in roots:
        for here, dirs, files in os.walk(root):
            depth = len(Path(here).relative_to(root).parts)
            dirs[:] = [] if depth >= _SEARCH_DEPTH else [
                d for d in dirs if not d.startswith(".") and d.lower() not in _NO_WRITE_DIRS
                and d != "node_modules"]
            for name in files:
                seen += 1
                if seen > _SEARCH_MAX_SEEN:
                    break
                if all(w in name.lower() for w in words):
                    try:
                        mtime = os.path.getmtime(os.path.join(here, name))
                    except OSError:
                        continue
                    if mtime >= newer_than:
                        hits.append((mtime, Path(here) / name))
    return [path for _, path in sorted(hits, reverse=True)]


def _resolve(name: str) -> Path:
    """An existing file: a real path (absolute, or relative to home), else the
    newest file whose name matches. So "shopping list" finds the list without
    the brain ever having to read a directory listing first."""
    raw = os.path.expandvars(os.path.expanduser((name or "").strip().strip('"')))
    if not raw:
        raise PcError("Which file")
    direct = Path(raw) if Path(raw).is_absolute() else Path.home() / raw
    if direct.is_file():
        return direct.resolve()
    hits = find_files(Path(raw).name)
    if not hits:
        raise PcError("I couldn't find a file like that")
    return hits[0].resolve()


def open_file(name: str) -> str:
    """Open a document with its default program; returns the path opened."""
    target = _resolve(name)
    if target.suffix.lower() in _NO_OPEN_SUFFIXES:
        raise PcError("That's a program or a script, and I only open documents")
    try:
        os.startfile(target)                                   # noqa: S606
    except OSError:
        raise PcError("Windows couldn't open that file") from None
    return str(target)


def append_file(name: str, text: str) -> str:
    """Add `text` as new lines at the end of an existing text file."""
    target = _resolve(name)
    _check_writable(target)
    if len(text) > FILE_MAX_CHARS or target.stat().st_size > 5_000_000:
        raise PcError("That's more than I'll add to one file")
    try:
        existing = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        raise PcError("That isn't a plain text file, so I've left it alone") from None
    gap = "" if not existing or existing.endswith("\n") else "\n"
    try:
        with open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(f"{gap}{text.rstrip()}\n")
    except OSError:
        raise PcError("Windows wouldn't let me change that file") from None
    return str(target)


def move_file(name: str, to: str) -> str:
    """Move or rename one file; never onto an existing one. `to` is a folder
    (the name is kept), a full path, or just a new name (it stays put, and
    keeps its extension if the new name has none)."""
    import shutil
    source = _resolve(name)
    raw = os.path.expandvars(os.path.expanduser((to or "").strip().strip('"')))
    if not raw:
        raise PcError("Where should it go")
    if not any(sep in raw for sep in "/\\") and not (Path.home() / raw).is_dir():
        dest = source.with_name(raw if Path(raw).suffix else raw + source.suffix)
    else:
        dest = Path(raw) if Path(raw).is_absolute() else Path.home() / raw
        if dest.is_dir():
            dest = dest / source.name
    dest = dest.resolve()
    _check_writable(source)
    _check_writable(dest)
    if dest.exists():
        raise PcError("There's already a file with that name there")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
    except OSError:
        raise PcError("Windows wouldn't let me move that file") from None
    return str(dest)


# ── typing ──────────────────────────────────────────────────────────────────

TYPE_MAX_CHARS = 2000
_KEYEVENTF_UNICODE = 0x4


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def type_text(text: str) -> int:
    """Type `text` into whichever window has the keyboard. A line break is
    Shift+Enter -- a new line in a chat box or an editor -- and never a bare
    Enter, so dictation cannot send a message or run a command by itself."""
    text = (text or "")[:TYPE_MAX_CHARS]
    if not text:
        raise PcError("There was nothing to type")
    user32 = ctypes.windll.user32
    for line_no, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if line_no:
            _tap("shift", "enter")
        units = line.encode("utf-16-le")
        events = []
        for i in range(0, len(units), 2):
            unit = int.from_bytes(units[i:i + 2], "little")
            for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
                event = _INPUT(type=1)                         # INPUT_KEYBOARD
                event.ki = _KEYBDINPUT(0, unit, flags, 0, None)
                events.append(event)
        if events:
            user32.SendInput(len(events), (_INPUT * len(events))(*events), ctypes.sizeof(_INPUT))
    return len(text)


# ── discord, music ──────────────────────────────────────────────────────────

# Discord has no mute key until the user gives it one. These are the two
# JARVIS presses; they are set once in Discord > Settings > Keybinds.
_DISCORD_KEYS = {"mute": ("ctrl", "alt", "f11"), "deafen": ("ctrl", "alt", "f12")}


def discord(action: str) -> None:
    if action not in _DISCORD_KEYS:
        raise PcError("I can toggle mute or deafen in Discord")
    _tap(*_DISCORD_KEYS[action])


def music_search(query: str) -> None:
    """Open Spotify on a search for `query`.
    ponytail: opens the results, does not press play -- starting a named track
    needs Spotify's Web API (an app registration and a login)."""
    from urllib.parse import quote
    if not (query or "").strip():
        raise PcError("What should I look for")
    try:
        os.startfile("spotify:search:" + quote(query.strip()[:120]))   # noqa: S606
    except OSError:
        raise PcError("Spotify doesn't seem to be installed") from None


def create_file(path: str, content: str) -> str:
    """Create a NEW text file and return where it went.

    `path` is absolute, or relative to the user's home folder, so
    "Desktop/notes.txt" is the desktop. It is a creator, not an editor: an
    existing file is never overwritten, because a misheard name must not cost
    anyone their work. On the system drive only the user's own folders are
    written to (not AppData, not dot-folders such as .ssh, not JARVIS's own
    folder); other drives are allowed outside their system folders.
    """
    raw = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"')))
    if not raw:
        raise PcError("I need a name for the file")
    if len(content) > FILE_MAX_CHARS:
        raise PcError("That's more text than I'll put in one file")
    home = Path.home().resolve()
    target = Path(raw)
    target = (target if target.is_absolute() else home / target).resolve()
    _check_writable(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "x", encoding="utf-8", newline="") as fh:
            fh.write(content)
    except FileExistsError:
        raise PcError("There's already a file with that name, and I won't overwrite it") from None
    except OSError:
        raise PcError("Windows wouldn't let me create that file") from None
    return str(target)


# ── status ──────────────────────────────────────────────────────────────────

def stats() -> dict:
    """The numbers behind the page's Systems panel. Percentages, 0-100."""
    import psutil
    mem = psutil.virtual_memory()
    out = {"cpu": psutil.cpu_percent(interval=None), "ram": mem.percent,
           "ram_gb": round(mem.used / 2**30, 1), "gpu": None, "vram": None, "gpu_temp": None}
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
            creationflags=_NO_WINDOW).stdout.strip().splitlines()[0]
        temp, util, used, total = [float(x) for x in gpu.split(",")]
        out.update(gpu=util, vram=round(100 * used / total), gpu_temp=temp)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, ZeroDivisionError):
        pass
    return out


NOTES_FILE = "Documents/Jarvis Notes.md"


def note_add(text: str) -> str:
    """Append one dated line to the running notes file; returns its path."""
    from datetime import datetime
    text = " ".join((text or "").split())
    if not text:
        raise PcError("There was nothing to note")
    target = Path.home() / NOTES_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text[:1000]}\n")
    except OSError:
        raise PcError("Windows wouldn't let me write the notes file") from None
    return str(target)


def notes_read(days: float = 1) -> list[str]:
    """The notes from the last `days` days (today only by default), oldest first."""
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=max(1.0, days) - 1)).strftime("%Y-%m-%d")
    try:
        lines = (Path.home() / NOTES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.startswith("- [") and line[3:13] >= since][-60:]


def status() -> str:
    """CPU, memory, who is using them, the network, and the graphics card."""
    import time
    import psutil
    mem = psutil.virtual_memory()
    # Per-process CPU needs two readings a moment apart; the network rate
    # comes out of the same pause.
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    net0 = psutil.net_io_counters()
    psutil.cpu_percent(None)
    time.sleep(1.0)
    net1 = psutil.net_io_counters()
    busy: dict[str, float] = {}
    for p in procs:
        try:
            share = p.cpu_percent(None) / (psutil.cpu_count() or 1)
        except psutil.Error:
            continue
        name = (p.info.get("name") or "").removesuffix(".exe")
        if name and name != "System Idle Process":
            busy[name] = busy.get(name, 0) + share
    top_cpu = [(n, v) for n, v in sorted(busy.items(), key=lambda kv: kv[1], reverse=True)[:5]
               if v >= 1]
    lines = [f"CPU {psutil.cpu_percent(None):.0f} percent busy.",
             ("Busiest on the CPU: " + ", ".join(f"{n} {v:.0f} percent" for n, v in top_cpu) + "."
              if top_cpu else "Nothing much is using the CPU."),
             f"Memory {mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used.",
             # ponytail: the whole machine's rate. Windows gives no per-program
             # network figure without an ETW trace (admin); Task Manager's
             # Network column is the place to see which program it is.
             f"Network: {(net1.bytes_recv - net0.bytes_recv) * 8 / 1e6:.1f} megabits a second down, "
             f"{(net1.bytes_sent - net0.bytes_sent) * 8 / 1e6:.1f} up, for the whole PC "
             f"(which program is not something I can see)."]
    by_name: dict[str, float] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            by_name[p.info["name"]] = by_name.get(p.info["name"], 0) + p.info["memory_info"].rss
        except (psutil.Error, AttributeError, TypeError):
            continue
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines.append("Biggest memory users: " + ", ".join(
        f"{name.removesuffix('.exe')} {rss / 2**30:.1f} GB" for name, rss in top) + ".")
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW).stdout.strip().splitlines()[0]
        name, temp, util, used, total = [x.strip() for x in gpu.split(",")]
        lines.append(f"Graphics card {name}: {temp} degrees, {util} percent busy, "
                     f"{int(used) / 1024:.1f} of {int(total) / 1024:.1f} GB of its memory used.")
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        lines.append("No graphics card reading is available.")
    # ponytail: no CPU temperature — Windows exposes none without admin or a vendor tool.
    return " ".join(lines)
