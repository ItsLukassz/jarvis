"""stt.py — where speech recognition happens.

The ear's version of `tts.py`, and deliberately the same shape: named
backends, a readiness map, and a fallback rather than a failure. The mouth
already learned those lessons; the ear should not learn them again.

**Phase 4a ships with `browser` as the default, so nothing changes.** The
recogniser is still Chrome's, in the page, exactly as it is today. This module
exists so that a local backend can be chosen later without the choice being
spread across `server.py` and `voice.ts` — see docs/plans/phase-4-speech.md.

**What is deliberately NOT here yet.** 4-zero measured, in a real Electron
renderer (Electron 44.3.0 / Chrome 152.0.7977.78):

    defined?          YES
    start() result:   ERROR event: error=network

`webkitSpeechRecognition` IS defined inside Electron. The constructor works,
`start()` succeeds, `onstart` fires, and only then does it fail — because
Chromium's speech service is a Google web service reached with keys only
Google's own builds carry.

So the obvious capability check, which `frontend/src/voice.ts:152` already
performs to pick its constructor:

    window.SpeechRecognition || window.webkitSpeechRecognition

reports that everything is fine in the one environment where it is not. A
decision about whether the browser can hear must rest on what happened when
somebody actually spoke, never on whether the API exists.

That rule lives in `frontend/src/voice.ts`, where the recogniser is and where
the decision is made, and NOT in a Python mirror of it here. A copy on this
side would have no caller — the server's `mic` frame is logged and drives
nothing by deliberate design — and this project's rule is that a capability
is declared in the same commit as its implementation, never before, or the
whole thing becomes a wish list. When a local backend exists and the server
has a reason to know, that is the commit to add it in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import re
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.stt")

# A bare model name: `base.en`, `small.en`, `distil-large-v3`. No separator
# of any kind, so nothing here can name a directory.
_MODEL_NAME_RE = re.compile(r"[\w.\-]{1,40}")

BACKEND_BROWSER = "browser"
BACKEND_WHISPER = "whisper"
BACKENDS = (BACKEND_BROWSER, BACKEND_WHISPER)
DEFAULT_BACKEND = BACKEND_BROWSER

# `base.en`, not `small.en`, and the domain prompt below is what makes that
# defensible. Measured 2026-09-10 on twenty utterances recorded through a real
# microphone (`.agents/stt-bakeoff-2026-09-10/`):
#
#     base.en alone              4/5 tool-argument words   0.38s   141 MB
#     base.en + domain prompt    5/5                       0.40s   141 MB
#     small.en alone             5/5                       1.02s   464 MB
#
# Same accuracy on the words that matter, 2.5x the speed, a third of the disk.
# The prompt is not an optimisation on top of the model choice, it IS the
# model choice — drop it and this should become `small.en`.
DEFAULT_MODEL = "base.en"

# `initial_prompt` shares the model's context window with the audio, so this
# cannot grow without bound. An attacker who can create directories chooses
# what is in it otherwise — see `domain_prompt`.
PROMPT_MAX_CHARS = 400


def resolve_backend(backend: Optional[str] = None) -> str:
    """The backend to use: the argument, else JARVIS_STT_BACKEND, else browser.

    An unrecognised name falls back to the default rather than raising or
    going deaf. `tts.resolve_backend`'s reasoning, for the ear: a typo in
    `.env` must not cost the user his voice, and it must not cost him his
    microphone either.
    """
    name = (backend or os.getenv("JARVIS_STT_BACKEND")
            or DEFAULT_BACKEND).strip().lower()
    if name not in BACKENDS:
        log.warning(f"unknown JARVIS_STT_BACKEND {name!r}; "
                    f"using {DEFAULT_BACKEND!r}")
        return DEFAULT_BACKEND
    return name


def resolve_model(model: Optional[str] = None) -> str:
    """The whisper model name: the argument, else JARVIS_STT_MODEL, else
    `base.en`.

    A NAME, never a path. `faster_whisper` will happily load a model
    directory, and what it loads it executes — the same rail
    `JARVIS_PIPER_VOICE` has, and for the same reason: these values are
    writable over HTTP through the settings endpoint.
    """
    name = (model or os.getenv("JARVIS_STT_MODEL") or DEFAULT_MODEL).strip()
    if not name or not _MODEL_NAME_RE.fullmatch(name):
        if name != DEFAULT_MODEL:
            log.warning(f"JARVIS_STT_MODEL {name!r} is not a bare model name; "
                        f"using {DEFAULT_MODEL!r}")
        return DEFAULT_MODEL
    return name


def _faster_whisper_available() -> bool:
    """Is the optional package installed? Import, not `find_spec`, because a
    broken install that imports badly is not available either."""
    try:
        import faster_whisper                                  # noqa: F401
    except Exception:
        return False
    return True


def model_is_cached(name: Optional[str] = None) -> bool:
    """Has the model already been downloaded?

    Checked separately from the package for the reason `tts.backends_ready`
    checks `piper_model_path()` and not just `piper_bin()`: the model is a
    further ~141 MB, `faster_whisper` fetches it lazily on first use, and a
    download on the voice path would stall the first thing the user ever says.
    Better to report not-ready and let `preflight` say so.
    """
    name = resolve_model(name)
    root = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    hub = root / "hub" if root.name != "hub" else root
    try:
        return any(d.is_dir() and d.name.endswith(f"faster-whisper-{name}")
                   for d in hub.iterdir())
    except OSError:
        return False


def domain_prompt(names) -> Optional[str]:
    """A whisper `initial_prompt` biasing the recogniser toward the words that
    reach TOOLS AS ARGUMENTS — project names, mostly.

    This is the difference between 4/5 and 5/5 on the measured set. A
    mangled project name is a failed action, not an ugly transcript, so these
    words are worth more than their share of the sentence.

    `None` rather than a stock sentence when there is nothing to bias toward:
    a prompt full of words the user never says would push the recogniser
    toward vocabulary that is not theirs.

    **Every name is filtered.** These come off disk — `Path(cwd).name` out of
    another process's file — the same untrusted string the `plain_name`
    regime walls everywhere else, and this one reaches a model. `plain_phrase`
    is imported lazily rather than copied: duplicating a security-relevant
    regex is how the two copies drift, and importing `brain` at module scope
    would drag its whole dependency chain into a module the tests import on
    its own.
    """
    if not names:
        return None
    from brain import plain_phrase                             # noqa: PLC0415
    clean = [p for p in (plain_phrase(n) for n in names) if p]
    if not clean:
        return None
    out = "Projects and terms: " + ", ".join(clean) + "."
    return out[:PROMPT_MAX_CHARS].rstrip(", ") if len(out) > PROMPT_MAX_CHARS else out


def backends_ready() -> dict[str, bool]:
    """Which backends could actually run on this machine.

    `browser` is always True: the recogniser is in the page and this process
    installs nothing for it.

    Read that carefully, because it is a statement about THIS machine and not
    about whether the recogniser works where the page is running. Electron is
    exactly the case where those two answers differ — see the module
    docstring. Nothing here can answer the second question, and nothing here
    pretends to.

    `whisper` needs both halves — the optional package AND the model already
    on disk. Either missing and it is not offerable.
    """
    return {
        BACKEND_BROWSER: True,
        BACKEND_WHISPER: _faster_whisper_available() and model_is_cached(),
    }


# The loaded model, kept for the life of the process. This is the whole reason
# `faster-whisper` was chosen over whisper.cpp's CLI: measured 2026-09-10, that
# reloads 141 MB on every invocation and pays 0.59s fixed per call, which is
# MORE than the transcription itself. A long-lived server pays it once.
_MODEL_CACHE: dict[str, object] = {}
_MODEL_LOCK = threading.Lock()


def _make_whisper(name: str):
    """The WhisperModel, on the GPU when JARVIS_STT_DEVICE=cuda.

    Measured on an RTX 3060 Ti: medium.en on CUDA (float16) transcribes a
    sentence in 0.8s against base.en's 1.9s on an i5-4670K, and is far more
    accurate on accents and on his name. The CUDA libraries come from the
    nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels in this venv, whose DLL
    folders Windows will not search unless told. Any CUDA failure falls back
    to the CPU rather than leaving him deaf.
    """
    from faster_whisper import WhisperModel                    # noqa: PLC0415
    device = (os.getenv("JARVIS_STT_DEVICE") or "cpu").strip().lower()
    if device == "cuda":
        import glob                                            # noqa: PLC0415
        for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            model = WhisperModel(name, device="cuda", compute_type="float16")
            log.info(f"whisper {name} loaded on the GPU")
            return model
        except Exception as e:
            log.warning(f"whisper on CUDA failed ({e!r}); using the CPU")
    return WhisperModel(name, device="cpu", compute_type="int8")


def _loaded_model(name: str):
    """A callable `(path, prompt) -> text`, with the model held across calls.

    Isolated behind a function so the tests can substitute it: nothing in the
    suite may load a model or reach the network, and `faster-whisper` is not
    installed on the macOS gate at all.
    """
    with _MODEL_LOCK:      # the startup preload and a first sentence must not load it twice
        return _loaded_model_locked(name)


def _loaded_model_locked(name: str):
    runner = _MODEL_CACHE.get(name)
    if runner is None:
        model = _make_whisper(name)

        def runner(path: str, prompt: Optional[str]) -> str:
            # vad_filter: Silero VAD cuts non-speech out before whisper sees
            # it. Without it a noisy room (peaks above capture.ts's
            # SPEECH_LEVEL) sends clips of near-silence, and whisper invents
            # stock phrases from them -- "Thank you very much.", "All right.",
            # "Good job." -- which JARVIS then answers as if spoken to.
            # "Jarvis." first, so the recogniser expects his name -- it is
            # the wake word, and unprimed it comes out "Jarmus" or "Jairus".
            segments, _info = model.transcribe(
                path, language="en", beam_size=5,
                initial_prompt="Jarvis." + (f" {prompt}" if prompt else ""),
                vad_filter=True, condition_on_previous_text=False)
            # `transcribe` returns a GENERATOR — the work happens here, not
            # above, which is also where the latency figures were measured.
            return " ".join(s.text for s in segments
                            if not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.7))

        _MODEL_CACHE[name] = runner
    return runner


def warm() -> None:
    """Load the whisper model in the background at startup, so the first
    sentence does not wait for it (medium.en takes several seconds to load).
    Only an already-downloaded model; never raises."""
    import threading                                           # noqa: PLC0415

    def load() -> None:
        try:
            name = resolve_model()
            if _faster_whisper_available() and model_is_cached(name):
                _loaded_model(name)
        except Exception as e:
            log.warning(f"could not preload whisper: {e!r}")
    threading.Thread(target=load, name="whisper-warm", daemon=True).start()


async def transcribe(audio: bytes, *, projects=None,
                     model: Optional[str] = None) -> Optional[str]:
    """One utterance of WAV audio to text, or None. Never raises.

    `None` rather than an exception is `tts.synthesize_chunk`'s rule for the
    mouth applied to the ear: this runs inside the WebSocket handler, and an
    engine that fails mid-sentence must not take the socket with it. The
    caller decides what to say about silence.

    **Not ready means None, not "download it now".** The model is a ~141 MB
    fetch and this is the voice path; the first thing a user ever says must
    not be the thing that pays for the install. `preflight` and
    `backends_ready()` are where that gets reported.

    The audio goes to a TEMP file and is removed afterwards. It is the user's
    voice: it does not belong in `data/`, which is a directory they read.

    Blocking work goes to a thread — the model is CPU-bound and the event
    loop is also driving the brain, the run pipeline and every WebSocket.
    """
    name = resolve_model(model)
    if not _faster_whisper_available() or not model_is_cached(name):
        log.warning("stt: whisper backend is not ready; nothing transcribed")
        return None

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="jarvis-stt-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(audio)
        runner = _loaded_model(name)
        text = await asyncio.to_thread(runner, tmp, domain_prompt(projects))
        return (text or "").strip() or None
    except Exception:
        log.warning("stt: transcription failed", exc_info=True)
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                log.debug("stt: could not remove %s", tmp, exc_info=True)
