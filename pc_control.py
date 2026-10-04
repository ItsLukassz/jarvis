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
       "alt": 0x12, "win": 0x5B, "shift": 0x10, "right": 0x27}


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


def status() -> str:
    """CPU, memory, the biggest memory users, and the graphics card."""
    import psutil
    mem = psutil.virtual_memory()
    lines = [f"CPU {psutil.cpu_percent(interval=0.5):.0f} percent busy.",
             f"Memory {mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used."]
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
