"""window_capture.py — a picture of ONE app's window, on Windows.

`look_at_screen` photographs a whole display, which only shows what is in
front. This takes a single window by app name or title — Discord, Spotify, a
browser tab behind the game — using PrintWindow with PW_RENDERFULLCONTENT, so
it works while the window is covered by others. A minimized window has
nothing drawn to capture and is refused with a sentence saying so.

Gated by the same JARVIS_SCREEN_CAPTURE switch as `look_at_screen`: the
window's contents are as private as the screen's.

numpy does the pixel work (it is already here for faster-whisper) and the PNG
is written with zlib, so nothing new is installed.
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes
from dataclasses import dataclass

MAX_WIDTH = 1280                    # the same budget look_at_screen keeps to
_PW_RENDERFULLCONTENT = 0x2
_DIB_RGB_COLORS = 0
_BI_RGB = 0


@dataclass
class Found:
    hwnd: int
    app: str
    title: str


@dataclass
class Shot:
    png: bytes
    width: int
    height: int
    app: str
    title: str


class WindowCaptureError(Exception):
    """Its message is a sentence JARVIS can say."""


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def find(query: str) -> Found:
    """The best visible, titled window whose app name or title contains
    `query` (case-insensitive). An exact app-name match wins over a title
    match, so "chrome" picks Chrome rather than a window about Chrome."""
    from jarvis_platform.windows.screen import _process_name

    q = (query or "").strip().lower()
    if not q:
        raise WindowCaptureError("Which app would you like me to look at")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))

    best: tuple[int, Found] | None = None

    def visit(hwnd, _lparam):
        nonlocal best
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(min(n + 1, 512))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value.strip()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = _process_name(pid.value) or ""
        score = (3 if app.lower() == q else 2 if q in app.lower() else
                 1 if q in title.lower() else 0)
        if score and (best is None or score > best[0]):
            best = (score, Found(int(hwnd), app, title))
        return True

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proto(visit), 0)
    if best is None:
        raise WindowCaptureError("I can't find an open window by that name")
    return best[1]


def capture(query: str) -> Shot:
    """Blocking: find the window and photograph it."""
    import numpy as np

    win = find(query)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)

    if user32.IsIconic(win.hwnd):
        raise WindowCaptureError("That window is minimized, so there's nothing on it to see")
    rect = wintypes.RECT()
    user32.GetWindowRect(win.hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise WindowCaptureError("That window has no size to capture")

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not user32.PrintWindow(win.hwnd, mem_dc, _PW_RENDERFULLCONTENT):
            raise WindowCaptureError("That app wouldn't let me capture it")
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, _BI_RGB,
                                   0, 0, 0, 0, 0)
        pixels = (ctypes.c_ubyte * (w * h * 4))()
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, pixels, ctypes.byref(header), _DIB_RGB_COLORS):
            raise WindowCaptureError("I couldn't read that window's picture")
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)

    img = np.frombuffer(pixels, dtype=np.uint8).reshape(h, w, 4)[:, :, 2::-1]   # BGRA -> RGB
    if not img.any():
        raise WindowCaptureError("That app came back blank — some games and video players "
                                 "can't be captured this way")
    if w > MAX_WIDTH:                    # integer-step shrink: cheap, and plenty legible
        step = -(-w // MAX_WIDTH)
        img = img[::step, ::step]
    img = np.ascontiguousarray(img)
    return Shot(_png(img), img.shape[1], img.shape[0], win.app, win.title)


def _png(rgb) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
