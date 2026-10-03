"""
tts.py — one sentence chunk in, one complete audio blob out.

Three backends behind one call:

    say    macOS's own synthesiser, and the default. Offline, no key, no
           dependency, nothing to bill. Measured on this project's Mac with
           the en_GB voice Daniel: ~0.45s per sentence whatever its length —
           the cost is process startup, not synthesis — against 1.2-3.5s of
           audio, so it stays comfortably ahead of the mouth.
    piper  A neural voice, still local and still offline, at the price of one
           dependency (piper-tts, which brings onnxruntime) and a ~63 MB model
           per voice. Measured with en_GB-alan-medium on the same Mac: 0.67s
           per sentence warm (1.30s on the first call, while onnxruntime warms
           up) against 1.2-4.0s of audio. Slower than `say` and markedly less
           robotic.
    fish   Fish Audio, the original hosted voice. Needs FISH_API_KEY, and is
           reached only by asking for it: JARVIS_TTS_BACKEND=fish.

The Fish response is streamed so time-to-first-byte can be measured. Neither
local backend can stream: `say` seeks its output file to write the WAV header,
so `-o -` yields zero bytes (measured), and piper is handed the same kind of
output path. For those, `first_byte_sec` IS the total. Either way the chunk is
returned whole, because the browser decodes one complete blob per chunk and
`speech.ack_floor_seconds` needs a whole container to read a length out of.

Two things make the format change safe. The audio frame carries no MIME type
(server.py base64s it into a `{"type": "audio"}` message) and the browser's
`decodeAudioData` sniffs the container, so WAV needs no frontend change; and
`speech.wav_seconds` was added alongside `mp3_seconds` so the ack floor still
has a length to work from — without it every local chunk would be a blob with
no floor, which is the older, worse resume behaviour.

The text goes to both local backends over STDIN, never as an argument. It is
model-written and arrives through speech recognition, so a chunk beginning
"-r 500 ..." would otherwise be read as flags — the same reasoning
gh_lookup.py applies to `gh`, one step further because here the input is not
even the user's own words.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

import data_paths

log = logging.getLogger("jarvis.tts")

FISH_TTS_URL = "https://api.fish.audio/v1/tts"

BACKEND_SAY = "say"
BACKEND_PIPER = "piper"
BACKEND_FISH = "fish"
BACKENDS = (BACKEND_SAY, BACKEND_PIPER, BACKEND_FISH)
DEFAULT_BACKEND = BACKEND_SAY

# Daniel is macOS's en_GB male voice — the closest of the built-ins to a
# British butler, and present on a stock install. System Settings ->
# Accessibility -> Spoken Content offers a free "Daniel (Premium)" download
# that this same name then resolves to, so the setting need not change to get
# the better voice.
DEFAULT_SAY_VOICE = "Daniel"

# 22.05 kHz mono 16-bit: the rate `say` synthesises at, so asking for more
# only inflates the base64 in every audio frame. ~44 KB per spoken second.
# Piper's en_GB models emit the same rate, measured.
SAY_SAMPLE_RATE = 22050

# The piper voice, as a model NAME rather than a path — one `.onnx` and its
# `.onnx.json` in data_paths.voices_dir(). Kept in its own variable rather
# than sharing JARVIS_TTS_VOICE with `say`, so switching backend and back
# does not leave "Daniel" naming a model that does not exist.
DEFAULT_PIPER_VOICE = "en_GB-alan-medium"

# A settable voice may only NAME a model in the voices directory. The env var
# still takes a full path (a hand-edited .env is the user's own machine), but
# nothing arriving over HTTP gets to point onnxruntime at an arbitrary file.
# No anchors, and matched with `fullmatch`: `$` matches before a trailing
# newline, so `re.match` on an anchored pattern would accept "alan\n" as a
# whole-value check (tests/test_anchored_patterns.py pins this project-wide).
_SAFE_VOICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass
class SynthResult:
    audio: bytes
    first_byte_sec: float
    total_sec: float
    # Which backend actually spoke. Usually the configured one; `say` when
    # that one could not, so the caller can say so out loud rather than
    # leaving the user to notice a different voice.
    backend: str = BACKEND_SAY


# Typography a language model writes, and what a synthesiser does with it.
#
# Measured 2026-09-10 with piper / en_GB-alan-medium, after the user reported
# JARVIS "sometimes saying circumflex":
#
#     "a b"       0.65s
#     "a - b"     0.62s    a hyphen is silent, as it should be
#     "a, b"      0.96s    a comma is a small pause
#     "a — b"     2.18s    an em dash is +1.56s of SPEECH
#
# An em dash is PROSODY, not a word, and JARVIS's persona is built on them —
# so this was happening in most sentences he spoke. Nothing normalised text
# before synthesis; `tts.py` and `speech.py` only stripped it.
#
# Mapped to a comma rather than deleted: the dash is doing real work in the
# sentence, and removing it runs two clauses together. A comma is the closest
# thing a synthesiser reliably renders as a pause.
_SPEAKABLE = {
    "—": ",",   # em dash
    "–": ",",   # en dash
    "…": "...",  # ellipsis -> three stops, which every backend handles
    "’": "'",   # right single quote, the apostrophe a model actually writes
    "‘": "'",
    "“": '"',
    "”": '"',
    " ": " ",   # non-breaking space
    "­": "",    # soft hyphen: invisible, and read aloud by some voices
}

# ", ," and ",," from a dash the author had already punctuated around.
_DOUBLED = re.compile(r",\s*,+")
_SPACE_BEFORE_COMMA = re.compile(r"\s+,")


def speakable(text: Optional[str]) -> str:
    """`text` with typography a synthesiser would read ALOUD turned into
    punctuation it renders as prosody.

    Applied to every backend rather than to piper alone: the characters are
    wrong for a synthesiser in general, `say` and Fish were never measured
    with them, and a sentence that sounds right in one voice and says
    "circumflex" in another is worse than one rule.

    Ordinary text is returned unchanged — checked by a test, because the cost
    of this must fall on the sentences that need it and nothing else.
    """
    out = text or ""
    for bad, good in _SPEAKABLE.items():
        if bad in out:
            out = out.replace(bad, good)
    out = _SPACE_BEFORE_COMMA.sub(",", out)
    out = _DOUBLED.sub(",", out)
    return out


def resolve_backend(backend: Optional[str] = None) -> str:
    """The backend to use: the argument, else JARVIS_TTS_BACKEND, else `say`.

    An unrecognised name falls back to the default rather than raising or
    going silent — a typo in `.env` must not cost the user his voice.
    """
    name = (backend or os.getenv("JARVIS_TTS_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if name not in BACKENDS:
        log.warning(f"unknown JARVIS_TTS_BACKEND {name!r}; using {DEFAULT_BACKEND!r}")
        return DEFAULT_BACKEND
    return name


def resolve_voice(voice: Optional[str] = None) -> str:
    return (voice or os.getenv("JARVIS_TTS_VOICE") or DEFAULT_SAY_VOICE).strip()


def resolve_rate(rate: Optional[int] = None) -> int:
    """Words per minute, 0 meaning "the voice's own default"."""
    if rate:
        return int(rate)
    try:
        return int((os.getenv("JARVIS_TTS_RATE") or "0").strip() or 0)
    except ValueError:
        return 0


def _apply_honorific(text: str) -> str:
    """Address the user the way the Settings page says, wherever a canned line
    or the model says "sir". Dozens of fixed lines in server.py hard-code
    "sir"; this is the one place all of them pass through before being heard.

    From Settings: Name (USER_NAME) and Honorific (HONORIFIC: sir / ma'am /
    none). With a name set, each "sir" becomes the name about three times in
    four and the honorific otherwise -- the same "mostly by name" rule the
    brain's launch prompt gives. Honorific "none" drops the word (and the
    comma before it) when there is no name to say instead.
    """
    import random
    honorific = (os.getenv("HONORIFIC") or "sir").strip() or "sir"
    name = (os.getenv("USER_NAME") or "").strip()
    none = honorific.lower() == "none"
    if not name and honorific.lower() == "sir":
        return text
    if not name and none:
        # "Sir, the build is done." -> "The build is done."; "Done, sir." -> "Done."
        text = re.sub(r"\bSir\s*[,—–-]\s*(\w)", lambda m: m.group(1).upper(), text)
        return re.sub(r",?\s*\b[Ss]ir\b", "", text)

    def pick(m: re.Match) -> str:
        if name and (none or random.random() < 0.75):
            word = name
        else:
            word = honorific
        return word[:1].upper() + word[1:] if m.group()[0] == "S" else word

    return re.sub(r"\b[Ss]ir\b", pick, text)


def resolve_piper_voice(voice: Optional[str] = None) -> str:
    return (voice or os.getenv("JARVIS_PIPER_VOICE") or DEFAULT_PIPER_VOICE).strip()


def is_safe_voice_name(name: str) -> bool:
    """A bare model name, not a path. What the settings endpoint will store."""
    return bool(_SAFE_VOICE_NAME.fullmatch(name or ""))


def piper_bin() -> Optional[str]:
    """Where `piper` actually is.

    `shutil.which` is not enough and this is measured, not theoretical: the
    server runs as `.venv/bin/python server.py` WITHOUT the venv activated, so
    `.venv/bin` is not on PATH and `which("piper")` returns None while
    `.venv/bin/piper` sits right there. The interpreter's own directory is
    therefore looked at first — it is where a pip-installed console script for
    THIS interpreter lives.
    """
    override = (os.getenv("JARVIS_PIPER_BIN") or "").strip()
    if override:
        return override if os.path.exists(override) else shutil.which(override)
    # Both names, on every platform, and NOT `Path(...) / "piper"` alone.
    # The console script pip writes is named for the platform: `piper` on
    # POSIX, `piper.exe` on Windows. Testing only the bare name finds nothing
    # there, and the PATH fallback below cannot rescue it because
    # `.venv\Scripts` is not on PATH — which is the whole reason this looks
    # beside the interpreter at all.
    #
    # Measured 2026-09-10 on a Windows box with piper installed and its voice
    # downloaded: `piper_bin()` returned None, `backends_ready()` reported
    # piper unavailable, and JARVIS was mute on the one platform that has no
    # `say` to fall back to. It was found by RUNNING the product; nothing in
    # the suite could have caught it, because the suite never looks for a real
    # piper.
    #
    # Trying both names rather than branching on `os.name`: the list is two
    # items, the wrong one simply does not exist, and this file has no
    # business knowing which platform it is on.
    directory = Path(sys.executable).parent
    for candidate in ("piper", "piper.exe"):
        beside = directory / candidate
        if beside.is_file():
            return str(beside)
    return shutil.which("piper")


def piper_model_path(voice: Optional[str] = None) -> Optional[Path]:
    """The .onnx for this voice, or None if it is not downloaded.

    A name resolves inside `data_paths.voices_dir()`; a value that looks like
    a path is taken as one, so a hand-edited `.env` can point anywhere. Voices
    are never fetched here — a download on the voice path would put the
    network between the user and an answer, and 63 MB of it.
    """
    name = resolve_piper_voice(voice)
    if os.sep in name or name.endswith(".onnx"):
        candidate = Path(name).expanduser()
        return candidate if candidate.exists() else None
    candidate = data_paths.voices_dir() / f"{name}.onnx"
    return candidate if candidate.exists() else None


def installed_piper_voices() -> list[str]:
    """Every piper model actually on disk, by name.

    The settings panel offers these and nothing else. A voice typed by hand
    that has not been downloaded is not a mistake JARVIS can recover from
    mid-sentence — he simply goes quiet — so the UI must not be able to
    choose one.
    """
    try:
        return sorted(m.stem for m in data_paths.voices_dir().glob("*.onnx"))
    except OSError as e:
        log.debug(f"could not list piper voices: {e}")
        return []


def backends_ready(*, fish_key: str = "") -> dict[str, bool]:
    """Which backends could speak right now, for the settings panel.

    A backend the user cannot use should not look available in a dropdown —
    piper without its model is the common case, and it fails one sentence at
    a time rather than announcing itself.
    """
    return {
        BACKEND_SAY: shutil.which("say") is not None,
        BACKEND_PIPER: piper_bin() is not None and piper_model_path() is not None,
        # The caller decides what counts as a key: server.py knows about the
        # placeholder that .env.example ships with, and this module does not.
        BACKEND_FISH: bool((fish_key or "").strip()),
    }


async def synthesize_chunk(text: str, *, api_key: str = "", voice_id: str = "",
                           client: Optional[httpx.AsyncClient] = None,
                           latency: str = "balanced", timeout: float = 15.0,
                           backend: Optional[str] = None,
                           voice: Optional[str] = None,
                           rate: Optional[int] = None,
                           fallback: bool = True) -> Optional[SynthResult]:
    """Synthesise one chunk, or None if it could not be spoken at all.

    None is still the only failure mode: the scheduler treats a chunk it
    cannot speak as a chunk to fall back on text for, and a raised exception
    here would take the mouth down with it. But None is now the LAST resort —
    a configured backend that fails hands over to `say` (see below), so it
    means macOS itself would not speak either.
    """
    text = _apply_honorific(speakable(text).strip())
    if not text:
        return None
    chosen = resolve_backend(backend)

    if chosen == BACKEND_SAY:
        return await _synthesize_say(text, voice=resolve_voice(voice),
                                     rate=resolve_rate(rate), timeout=timeout)

    if chosen == BACKEND_FISH:
        result = await _synthesize_fish(text, api_key=api_key, voice_id=voice_id,
                                        client=client, latency=latency, timeout=timeout)
    else:
        result = await _synthesize_piper(text, voice=voice, timeout=timeout)
    if result is not None:
        return result

    # The configured backend could not speak this chunk. Silence is the worst
    # failure JARVIS has — a voice assistant that has gone quiet looks broken
    # rather than misconfigured — so `say`, which needs nothing, takes over.
    # It is never silent about it: the result carries the backend that really
    # spoke, and server.py says so out loud once.
    if not fallback:
        return None
    log.warning(f"TTS backend {chosen!r} failed; falling back to {BACKEND_SAY!r}")
    return await _synthesize_say(text, voice=resolve_voice(voice),
                                 rate=resolve_rate(rate), timeout=timeout)


async def _synthesize_say(text: str, *, voice: str, rate: int,
                          timeout: float) -> Optional[SynthResult]:
    """macOS `say` into a WAV file, read back whole."""
    if shutil.which("say") is None:
        log.error("no `say` on PATH: local TTS needs macOS "
                  "(or set JARVIS_TTS_BACKEND=piper, or =fish)")
        return None

    def argv(out: Path) -> list[str]:
        cmd = ["say", "-f", "-", "--file-format=WAVE",
               f"--data-format=LEI16@{SAY_SAMPLE_RATE}"]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-r", str(rate)]
        return cmd + ["-o", str(out)]

    result = await _render_wav(argv, text=text, timeout=timeout, label="say")
    if result is not None:
        result.backend = BACKEND_SAY
    return result


async def _synthesize_piper(text: str, *, voice: Optional[str],
                            timeout: float) -> Optional[SynthResult]:
    """piper into a WAV file, read back whole.

    Both failure modes name their remedy, because both are ordinary: the
    package is an optional dependency, and a voice is a 63 MB download that
    JARVIS deliberately will not fetch for you mid-sentence.
    """
    binary = piper_bin()
    if binary is None:
        log.error("no `piper` found: pip install piper-tts, or set JARVIS_TTS_BACKEND=say")
        return None
    model = piper_model_path(voice)
    if model is None:
        wanted = resolve_piper_voice(voice)
        log.error(f"piper voice {wanted!r} is not in {data_paths.voices_dir()}: "
                  f"download it with `python -m piper.download_voices "
                  f"--download-dir {data_paths.voices_dir()} {wanted}`")
        return None

    result = await _synthesize_piper_resident(text, model=model, timeout=timeout)
    if result is None:
        def argv(out: Path) -> list[str]:
            return [binary, "-m", str(model), "-f", str(out)]

        result = await _render_wav(argv, text=text, timeout=timeout, label="piper")
    if result is not None:
        result.backend = BACKEND_PIPER
    return result


# Piper kept loaded in this process, rather than a `piper` process per chunk.
# Measured on an i5-4670K (Windows) with en_GB-jenny_dioco-medium: the
# subprocess costs ~5.8s per sentence, nearly all of it onnxruntime start-up
# and model load; the resident voice speaks the same sentence in 0.2-0.3s
# after a one-time ~9s load. Any failure here returns None and the caller
# falls back to the subprocess, so this can only ever make him faster.
_piper_voices: dict[str, object] = {}
_piper_lock = threading.Lock()


def _resident_piper_voice(model: Path):
    key = str(model)
    with _piper_lock:
        voice = _piper_voices.get(key)
        if voice is None:
            from piper import PiperVoice
            t0 = time.monotonic()
            voice = PiperVoice.load(key)
            _piper_voices[key] = voice
            log.info(f"piper voice {model.stem!r} loaded in-process "
                     f"in {time.monotonic() - t0:.1f}s")
        return voice


def _render_piper_resident(text: str, model: Path) -> bytes:
    import io
    import wave
    voice = _resident_piper_voice(model)
    buf = io.BytesIO()
    with _piper_lock:                      # one ONNX session, one sentence at a time
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav)
    return buf.getvalue()


async def _synthesize_piper_resident(text: str, *, model: Path,
                                     timeout: float) -> Optional[SynthResult]:
    t0 = time.monotonic()
    try:
        # The first call loads the model; give that its own allowance rather
        # than failing the sentence and paying the subprocess as well.
        allowance = timeout if str(model) in _piper_voices else max(timeout, 60.0)
        audio = await asyncio.wait_for(
            asyncio.to_thread(_render_piper_resident, text, model), timeout=allowance)
    except Exception as e:
        log.warning(f"in-process piper failed ({e!r}); using the piper subprocess")
        return None
    if not audio:
        return None
    total = time.monotonic() - t0
    return SynthResult(audio, total, total)


def warm_piper() -> None:
    """Load the configured piper voice in the background, so the first thing
    JARVIS says does not wait for the model. Never raises."""
    def load() -> None:
        try:
            model = piper_model_path()
            if model is not None:
                _resident_piper_voice(model)
        except Exception as e:
            log.warning(f"could not preload the piper voice: {e!r}")
    threading.Thread(target=load, name="piper-warm", daemon=True).start()


async def _render_wav(make_argv, *, text: str, timeout: float,
                      label: str) -> Optional[SynthResult]:
    """Run a local synthesiser that writes a WAV file, and return its bytes.

    A file, not a pipe: WAV's header carries the data length, so a writer
    seeks back to fill it in — `say -o -` produces nothing at all (measured),
    and piper is handed a path for the same reason. The file lives in a
    per-call temporary directory, so two chunks synthesised at once cannot
    collide, and it is gone before this returns: audio JARVIS speaks is never
    left on disk.
    """
    t0 = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-tts-") as tmp:
            out = Path(tmp) / "chunk.wav"
            err = await _spawn_synth(make_argv(out), text=text, timeout=timeout)
            if err is not None:
                log.error(f"TTS `{label}` failed for {text[:40]!r}: {err}")
                return None
            try:
                audio = out.read_bytes()
            except OSError as e:
                log.error(f"TTS could not read `{label}` output: {e}")
                return None
    except OSError as e:                 # the temporary directory itself
        log.error(f"TTS could not make a working directory: {e}")
        return None

    if not audio:
        return None
    total = time.monotonic() - t0
    return SynthResult(audio, total, total)


async def _spawn_synth(argv: list[str], *, text: str,
                       timeout: float) -> Optional[str]:
    """Run a local synthesiser, feeding it `text` on stdin. None on success,
    else the reason.

    The single process boundary of this module, so tests mock it once rather
    than patching `asyncio.create_subprocess_exec` per call — the pattern
    `preflight._run_subprocess` and `dialog._osascript` already keep. Never
    raises: a failure here must cost one chunk of speech, not the mouth.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return f"could not spawn {argv[0]!r}: {e}"

    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(text.encode("utf-8")), timeout=timeout)
    except asyncio.TimeoutError:
        # Kill and reap, or the child outlives the utterance and is still
        # talking over the next one (notifier.py's rule for a wedged osascript).
        try:
            proc.kill()
            await proc.communicate()
        except Exception as e:
            log.debug(f"TTS could not reap a timed-out synthesiser: {e}")
        return f"timed out after {timeout}s"

    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return f"exit {proc.returncode}: {detail}" if detail else f"exit {proc.returncode}"
    return None


async def _synthesize_fish(text: str, *, api_key: str, voice_id: str,
                           client: Optional[httpx.AsyncClient],
                           latency: str, timeout: float) -> Optional[SynthResult]:
    if not api_key:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    t0 = time.monotonic()
    first: Optional[float] = None
    buf = bytearray()
    try:
        async with client.stream(
            "POST", FISH_TTS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"text": text, "reference_id": voice_id, "format": "mp3", "mp3_bitrate": 128, "latency": latency},
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                log.error(f"TTS {resp.status_code} for {text[:40]!r}")
                return None
            async for part in resp.aiter_bytes():
                if first is None:
                    first = time.monotonic() - t0
                buf.extend(part)
    except (httpx.HTTPError, OSError) as e:
        log.error(f"TTS error: {e}")
        return None
    finally:
        if own:
            try:
                await client.aclose()
            except Exception as e:      # never turn a clean None into an exception
                log.debug(f"TTS client close failed: {e}")
    if not buf:
        return None
    return SynthResult(bytes(buf), first if first is not None else 0.0,
                       time.monotonic() - t0, backend=BACKEND_FISH)
