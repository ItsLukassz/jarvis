"""Who is speaking, and whether they said his name.

Two optional local models, both off until switched on in Settings:

  * **Voice lock.** A speaker-embedding model (3D-Speaker ERes2Net, via
    sherpa-onnx) turns an utterance into a vector; an utterance is answered
    only if its vector is close to the one learned from the user's own voice.
    Measured on five synthetic voices: the same voice scored 0.84 or more
    (0.65 on a 1.6s "Jarvis, stop"), a different one 0.51 at most.
  * **Wake-word listener.** openWakeWord's "hey jarvis" model scores the
    microphone continuously, 80ms at a time, on the CPU. With it on, the page
    records nothing until that score fires -- so whisper stops transcribing
    the room. Measured: "Hey Jarvis, ..." scored 0.9-1.0 on four voices and
    unrelated speech 0.00; a bare "Jarvis" was anywhere from 0.06 to 0.97,
    which is why the setting says "Hey Jarvis".

The models are files under `data/models` (machine-local, never committed).
A missing package or model means the feature reports itself unavailable; it
never makes him deaf.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import wave
from pathlib import Path
from typing import Optional

import data_paths

log = logging.getLogger("jarvis.hearing")

SPEAKER_MODEL = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
WAKE_MODEL = "hey_jarvis_v0.1.onnx"
WAKE_SUPPORT = ("melspectrogram.onnx", "embedding_model.onnx")

ENROLL_SAMPLES = 3
ENROLL_MIN_SECONDS = 1.5
DEFAULT_THRESHOLD = 0.5
DEFAULT_GPU_TEMP = 85.0
WAKE_THRESHOLD = 0.5

_lock = threading.Lock()
_extractor = None
_wake = None
_enrolling: list = []          # embeddings collected so far; empty when not enrolling
_enroll_left = 0


def models_dir() -> Path:
    return data_paths.data_dir() / "models"


def _state_path() -> Path:
    return data_paths.data_dir() / "jarvis" / "hearing.json"


def load() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return {"voice_lock": bool(state.get("voice_lock")),
            "wake_engine": bool(state.get("wake_engine")),
            "threshold": float(state.get("threshold") or DEFAULT_THRESHOLD),
            "embedding": state.get("embedding") or None,
            # Not hearing, but the same kind of thing: switches Settings owns.
            "follow_up": bool(state.get("follow_up")),
            "perf_watch": bool(state.get("perf_watch")),
            "perf_gpu_temp": float(state.get("perf_gpu_temp") or DEFAULT_GPU_TEMP)}


def save(**changes) -> dict:
    state = {**load(), **changes}
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def status() -> dict:
    """What Settings shows. Never the embedding itself."""
    state = load()
    return {"voice_lock": state["voice_lock"], "wake_engine": state["wake_engine"],
            "threshold": state["threshold"], "enrolled": state["embedding"] is not None,
            "follow_up": state["follow_up"], "perf_watch": state["perf_watch"],
            "perf_gpu_temp": state["perf_gpu_temp"],
            "enrolling": _enroll_left,
            "voice_lock_available": (models_dir() / SPEAKER_MODEL).is_file(),
            "wake_available": all((models_dir() / n).is_file()
                                  for n in (WAKE_MODEL, *WAKE_SUPPORT))}


# ── voice lock ──────────────────────────────────────────────────────────────

def _samples(wav: bytes):
    """16 kHz mono float32 out of the WAV the page sends."""
    import numpy as np
    with wave.open(io.BytesIO(wav)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            pcm = pcm[::w.getnchannels()]
        return pcm.astype(np.float32) / 32768, w.getframerate()


def _embedding(wav: bytes):
    import numpy as np
    global _extractor
    with _lock:
        if _extractor is None:
            import sherpa_onnx
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(models_dir() / SPEAKER_MODEL), num_threads=1))
        pcm, rate = _samples(wav)
        stream = _extractor.create_stream()
        stream.accept_waveform(rate, pcm)
        stream.input_finished()
        vector = np.array(_extractor.compute(stream))
    return vector / (np.linalg.norm(vector) or 1.0), len(pcm) / rate


def is_user(wav: bytes) -> tuple[bool, Optional[float]]:
    """(answer it?, similarity). Anything that goes wrong answers yes: a lock
    that fails shut is a JARVIS who has stopped hearing his own user."""
    state = load()
    if not state["voice_lock"] or state["embedding"] is None:
        return True, None
    try:
        import numpy as np
        vector, _ = _embedding(wav)
        score = float(np.array(state["embedding"]) @ vector)
        return score >= state["threshold"], score
    except Exception:
        log.warning("voice lock could not score an utterance; letting it through", exc_info=True)
        return True, None


def start_enrolling() -> None:
    global _enroll_left
    _enrolling.clear()
    _enroll_left = ENROLL_SAMPLES


def enrolling() -> bool:
    return _enroll_left > 0


def enroll_sample(wav: bytes) -> str:
    """Take one utterance as a sample of the user's voice; the sentence to show."""
    global _enroll_left
    import numpy as np
    try:
        vector, seconds = _embedding(wav)
    except Exception:
        log.warning("voice lock could not read a sample", exc_info=True)
        return "I couldn't use that one. Say another sentence."
    if seconds < ENROLL_MIN_SECONDS:
        return "That was too short to learn from. Say a full sentence."
    _enrolling.append(vector)
    _enroll_left -= 1
    if _enroll_left > 0:
        return f"Voice sample {len(_enrolling)} of {ENROLL_SAMPLES} saved. Say another sentence."
    mean = np.mean(_enrolling, axis=0)
    mean /= np.linalg.norm(mean) or 1.0
    _enrolling.clear()
    save(embedding=[round(float(x), 6) for x in mean], voice_lock=True)
    return "Voice learned. Voice lock is on."


# ── wake word ───────────────────────────────────────────────────────────────

def wake_score(pcm16: bytes) -> float:
    """Feed one block of 16 kHz int16 audio; the model's score after it."""
    import numpy as np
    global _wake
    with _lock:
        if _wake is None:
            from openwakeword.model import Model
            d = models_dir()
            _wake = Model(wakeword_models=[str(d / WAKE_MODEL)], inference_framework="onnx",
                          melspec_model_path=str(d / WAKE_SUPPORT[0]),
                          embedding_model_path=str(d / WAKE_SUPPORT[1]))
        scores = _wake.predict(np.frombuffer(pcm16, dtype=np.int16))
    return float(next(iter(scores.values())))


def wake_reset() -> None:
    with _lock:
        if _wake is not None:
            _wake.reset()
