/**
 * JARVIS — Main entry point.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createVoiceInput, createAudioPlayer, createMicMonitor } from "./voice";
import { createCapture } from "./capture";
import { createSocket } from "./ws";
import { openSettings, checkFirstTimeSetup } from "./settings";
import { applyTheme } from "./themes";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking" | "compacting";
let currentState: State = "idle";
let isMuted = false;
let isPaused = false;
const earsOff = () => isMuted || isPaused;

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;

function showError(msg: string) {
  errorEl.textContent = msg;
  errorEl.style.opacity = "1";
  setTimeout(() => {
    errorEl.style.opacity = "0";
  }, 5000);
}

const stateWordEl = document.getElementById("state-word")!;

// The overlay window mirrors this page (see overlay.ts).
const overlayChannel = new BroadcastChannel("jarvis-overlay");
// Settings announces a change of theme (or of the overlay's place) on it.
overlayChannel.onmessage = ({ data }) => {
  if (data?.type === "config") applyTheme(orb);
};

// The big word under the orb. #status-text beneath it is left for notices.
function updateStatus(state: State) {
  const labels: Record<State, string> = {
    idle: isPaused ? "paused" : isMuted ? "muted" : "standby",
    listening: "listening",
    thinking: "thinking",
    speaking: "speaking",
    compacting: "refreshing",
  };
  stateWordEl.textContent = labels[state];
  statusEl.textContent = "";
  overlayChannel.postMessage({ type: "state", state, word: labels[state] });
}

// ── Comms panel: what was heard and what he said ──
const logEl = document.getElementById("log")!;
function addLog(who: "you" | "jarvis", text: string) {
  overlayChannel.postMessage({ type: who === "you" ? "heard" : "said", text });
  logEl.querySelector(".log-empty")?.remove();
  const last = logEl.lastElementChild as HTMLElement | null;
  if (who === "jarvis" && last?.dataset.who === "jarvis") {
    // His reply arrives a sentence at a time: one entry, not six.
    last.lastElementChild!.textContent += " " + text;
    return;
  }
  const line = document.createElement("div");
  line.className = "log-line";
  line.dataset.who = who;
  const tag = document.createElement("span");
  tag.textContent = who === "you" ? "You" : "Jarvis";
  const body = document.createElement("p");
  body.textContent = text;
  line.append(tag, body);
  logEl.appendChild(line);
  while (logEl.children.length > 5) logEl.firstElementChild!.remove();
}

// ---------------------------------------------------------------------------
// Init components
// ---------------------------------------------------------------------------

const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
const orb = createOrb(canvas);

const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
const socket = createSocket(WS_URL);

applyTheme(orb);

const audioPlayer = createAudioPlayer();
orb.setAnalyser(audioPlayer.getAnalyser());

// Volume slider, remembered across restarts.
const volumeEl = document.getElementById("volume") as HTMLInputElement;
volumeEl.value = localStorage.getItem("jarvis-volume") ?? "100";
audioPlayer.setVolume(Number(volumeEl.value) / 100);
volumeEl.addEventListener("input", () => {
  audioPlayer.setVolume(Number(volumeEl.value) / 100);
  localStorage.setItem("jarvis-volume", volumeEl.value);
});
volumeEl.addEventListener("click", (e) => e.stopPropagation());

let muteMicDuringSpeech = false;

function transition(newState: State) {
  if (newState === currentState) return;
  currentState = newState;
  document.body.dataset.state = newState;      // the stylesheet takes its accent from this
  const btn = document.getElementById("hush");
  if (btn) (btn as HTMLButtonElement).hidden = newState !== "speaking";
  orb.setState(newState as OrbState);
  updateStatus(newState);

  if (earsOff()) return;
  if (newState === "speaking" && muteMicDuringSpeech) {
    voiceInput.pause();
  } else {
    voiceInput.resume();
  }
}

// ---------------------------------------------------------------------------
// Voice input
// ---------------------------------------------------------------------------

const voiceInput = createVoiceInput(
  (text: string) => {
    // The server decides whether this is echo, a barge-in, or a new turn.
    micMonitor.sawSpeech();
    socket.send({ type: "transcript", text, isFinal: true });
  },
  (text: string) => {
    micMonitor.sawSpeech();
    socket.send({ type: "interim", text });
  },
  (msg: string) => {
    showError(msg);
  },
  (event: string) => {
    // Mirror the recogniser's lifecycle to the server log. Going deaf is a
    // browser-side failure the server cannot otherwise see at all, and the
    // console it used to be confined to is never open when it happens.
    socket.send({ type: "mic", text: event });
  }
);

// ── the local speech backend ──────────────────────────────────────────────
// When the server is doing the recognising, the PAGE still does the hearing:
// the browser's echo cancellation only exists here, where the audio is also
// being played, and shipping the raw microphone instead throws it away.
// Measured 2026-09-10 — see frontend/src/capture.ts.
//
// The server is asked which backend it has rather than the page guessing.
// On the default install this is `browser`, nothing below runs, and the
// recogniser above stays in charge exactly as it always has.
const capture = createCapture(
  (data: string, woken: boolean) => {
    micMonitor.sawSpeech();
    socket.send({ type: "audio_in", data, woken });
  },
  (event: string) => {
    socket.send({ type: "mic", text: `capture: ${event}` });
  },
  (data: string) => {
    socket.send({ type: "audio_peek", data });
  },
  (data: string) => {
    socket.send({ type: "audio_frame", data });
  }
);

// The dedicated wake-word listener (Settings > Hearing). Asked at start and
// again whenever Settings changes it.
function syncHearing() {
  fetch("/api/hearing")
    .then((r) => r.json())
    .then((h) => capture.setWakeGated(Boolean(h?.wake_engine)))
    .catch(() => capture.setWakeGated(false));      // unknown: listen the ordinary way
}
syncHearing();
window.addEventListener("jarvis-hearing", syncHearing);

// A PROMISE, resolved before either recogniser is started — not a `stop()`
// fired at load.
//
// The first attempt did stop the browser recogniser here, and it did nothing:
// `voiceInput.start()` runs a second later from the kick-off below, so it
// stopped something that had not started and then the timer started it again.
// Both recognisers then ran at once and every sentence was transcribed twice,
// by two engines, with different words. Seen live 2026-09-10:
//
//     stt(base.en): That I can then put into my table.
//     User: That I can then put into my table.
//     User: Pie charts. What would you like to do that I can then pull ...
//
// Whichever engine is going to listen, exactly one of them starts.
const sttBackend: Promise<string> = fetch("/api/settings/status")
  .then((r) => r.json())
  .then((s) => (typeof s?.stt_backend === "string" ? s.stt_backend : "browser"))
  // Unreachable status is not a reason to go deaf: fall back to the
  // recogniser that needs no server-side anything.
  .catch(() => "browser");

const usingLocalStt = () => sttBackend.then((b) => b !== "browser");

// A live meter for the microphone itself. If this moves when you speak, the
// microphone is working — whatever else is or is not happening. It answers
// "is it even hearing me?" without a log, a console or anyone to ask.
const micDot = document.createElement("div");
micDot.id = "mic-level";
micDot.title = "microphone input";
document.body.appendChild(micDot);

const micMonitor = createMicMonitor(
  (level: number) => {
    const pct = Math.min(100, Math.round(level * 900));
    micDot.style.setProperty("--level", `${pct}%`);
    micDot.classList.toggle("is-hot", level > 0.02);
  },
  (event: string) => {
    socket.send({ type: "mic", text: event });
    // Proven deaf: sound going in, nothing coming out. Do not wait for the
    // rotation timer to happen along — measured once at 21 seconds, all of
    // it lost. Rebuild the recogniser now.
    // Only when the browser IS the recogniser. With the local backend there
    // is nothing here to rebuild, and rebuilding it would start the second
    // ear this file exists to prevent.
    if (event.startsWith("DEAF")) {
      usingLocalStt().then((local) => {
        if (!local) voiceInput.restart("deaf: audio in, no results");
      });
    }
  }
);

// ── stopping him ──────────────────────────────────────────────────────────
// Escape, or the button that appears while he is talking. Not a spoken word:
// his voice comes back through the microphone garbled, and a mis-hear that
// looked like "stop" would cut him off at random. A keystroke cannot be
// misheard.
const hushBtn = document.createElement("button");
hushBtn.id = "hush";
hushBtn.type = "button";
hushBtn.textContent = "Stop";
hushBtn.title = "Stop speaking (Esc)";
hushBtn.hidden = true;
document.body.appendChild(hushBtn);

function hush() {
  if (currentState !== "speaking") return;
  // Locally first: the round trip is real and silence should be instant.
  audioPlayer.stop();
  socket.send({ type: "hush" });
  transition(earsOff() ? "idle" : "listening");
}

hushBtn.addEventListener("click", hush);
window.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key === "Escape") { e.preventDefault(); hush(); }
});

audioPlayer.onPlayed((utt, idx) => {
  socket.send({ type: "played", utt, idx });
});

// End of speech is the server's call (`status: idle` after every chunk is
// acked); a transient empty queue mid-utterance must not flip the UI.
audioPlayer.onFinished(() => {});

audioPlayer.onNeedsGesture(() => {
  showError("Click anywhere to enable audio");
});

// ---------------------------------------------------------------------------
// WebSocket messages
// ---------------------------------------------------------------------------

socket.onMessage((msg) => {
  const type = msg.type as string;

  if (type === "config") {
    muteMicDuringSpeech = Boolean(msg.muteMicDuringSpeech);
  } else if (type === "audio") {
    const data = msg.data as string;
    if (data) {
      if (currentState !== "speaking") transition("speaking");
      audioPlayer.enqueue(data, Number(msg.utt), Number(msg.idx));
    }
    if (msg.text) addLog("jarvis", String(msg.shown ?? msg.text));
  } else if (type === "heard") {
    addLog("you", String(msg.text));
  } else if (type === "stop") {
    audioPlayer.stop();
    transition(earsOff() ? "idle" : "listening");
  } else if (type === "wake") {
    // His name was heard: record from a moment ago, and show the orb at once.
    capture.wake();
    overlayChannel.postMessage({ type: "wake" });
  } else if (type === "end_utterance") {
    // The server heard "that's it, Jarvis": stop listening to this command now.
    capture.endNow();
  } else if (type === "drop_queued") {
    audioPlayer.dropQueued();
  } else if (type === "status") {
    const state = msg.state as string;
    if (state === "thinking") transition("thinking");
    else if (state === "speaking") transition("speaking");
    else if (state === "compacting") transition("compacting");
    else if (state === "idle") transition(earsOff() ? "idle" : "listening");
  } else if (type === "text") {
    // A chunk TTS could not voice: show it instead of losing it
    console.log("[JARVIS]", msg.text);
    statusEl.textContent = String(msg.text);
  } else if (type === "notice") {
    // Shown, never spoken. The server sends one when it is about to be busy
    // for a few seconds (a context rotation), and an empty string to clear it.
    // Without it the pause looks like a crash.
    const text = String(msg.text ?? "");
    statusEl.textContent = text;
    if (text) console.log("[notice]", text);
  }
});

// ---------------------------------------------------------------------------
// Kick off
// ---------------------------------------------------------------------------

// Start listening after a brief delay for the orb to render — with whichever
// ear the server actually has. Awaited, so the two can never both start.
setTimeout(async () => {
  if (await usingLocalStt()) capture.start();
  else voiceInput.start();
  if (currentState !== "speaking") transition("listening");
}, 1000);

// Resume AudioContext on ANY user interaction (browser autoplay policy)
function ensureAudioContext() {
  const ctx = audioPlayer.getAnalyser().context as AudioContext;
  if (ctx.state === "suspended") {
    ctx.resume().then(() => console.log("[audio] context resumed"));
  }
}
document.addEventListener("click", ensureAudioContext);
document.addEventListener("touchstart", ensureAudioContext);
document.addEventListener("keydown", ensureAudioContext, { once: true });

// Try to resume audio context on load
ensureAudioContext();

// ---------------------------------------------------------------------------
// UI Controls
// ---------------------------------------------------------------------------

const btnMute = document.getElementById("btn-mute")!;
const btnMenu = document.getElementById("btn-menu")!;
const menuDropdown = document.getElementById("menu-dropdown")!;
const btnRestart = document.getElementById("btn-restart")!;
const btnFixSelf = document.getElementById("btn-fix-self")!;

// Mute and Pause both close the ears; Pause also freezes the brain.
function syncEars() {
  const off = earsOff();
  // The server is told too, so anything already on its way -- a sentence
  // being transcribed at the moment of the click -- is dropped, not answered.
  socket.send({ type: "mute", muted: off });
  updateStatus(currentState);      // standby / muted / paused, even with no state change
  if (off) {
    voiceInput.pause();
    micMonitor.pause();            // let go of the microphone entirely
    transition("idle");
  } else {
    voiceInput.resume();
    micMonitor.resume();
    transition("listening");
  }
  // With the local (whisper) backend the page's `capture` is the ear, not
  // `voiceInput` -- pausing only the latter left him hearing everything.
  usingLocalStt().then((local) => {
    if (!local) return;
    if (off) capture.stop(true);
    else capture.start();
  });
}

btnMute.addEventListener("click", (e) => {
  e.stopPropagation();
  isMuted = !isMuted;
  btnMute.classList.toggle("muted", isMuted);
  syncEars();
});

// Pause: the brain process stops and the speech model leaves the GPU, so he
// costs the machine almost nothing. Resuming brings both back in seconds.
const btnPause = document.getElementById("btn-pause")!;
btnPause.addEventListener("click", (e) => {
  e.stopPropagation();
  isPaused = !isPaused;
  btnPause.classList.toggle("muted", isPaused);
  btnPause.title = isPaused ? "Resume JARVIS" : "Pause JARVIS (frees CPU, memory and GPU)";
  syncOrb();
  document.body.classList.toggle("is-paused", isPaused);
  socket.send({ type: "pause", paused: isPaused });
  syncEars();
});

btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = menuDropdown.style.display === "none" ? "block" : "none";
});

document.addEventListener("click", () => {
  menuDropdown.style.display = "none";
});

btnRestart.addEventListener("click", async (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  statusEl.textContent = "restarting...";
  try {
    await fetch("/api/restart", { method: "POST" });
    // Wait a few seconds then reload
    setTimeout(() => window.location.reload(), 4000);
  } catch {
    statusEl.textContent = "restart failed";
  }
});

btnFixSelf.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  // Activate work mode on the WebSocket session (JARVIS becomes Claude Code's voice)
  // Milestone 1 has no tools yet; "Fix yourself" returns as a brain tool later.
  statusEl.textContent = "fix-yourself is not available in this build";
});

// HUD clock
const clockEl = document.getElementById("hud-clock")!;
const tick = () => {
  clockEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};
tick();
setInterval(tick, 1000);

// His voice for the overlay's orb, while he is speaking.
const overlayFreq = new Uint8Array(audioPlayer.getAnalyser().frequencyBinCount);
setInterval(() => {
  if (currentState !== "speaking") return;
  audioPlayer.getAnalyser().getByteFrequencyData(overlayFreq);
  overlayChannel.postMessage({ type: "freq", data: overlayFreq.slice(0, 64) });
}, 50);

// The window has no frame of its own; closing hides it to the tray.
document.getElementById("btn-close")!.addEventListener("click", () => window.close());

// Nothing is animated for a window nobody is looking at: behind another app,
// minimised or in the tray, the orb holds still and costs no GPU. (The overlay
// is its own window and is unaffected.)
function syncOrb() {
  orb.setPaused(isPaused || document.hidden || !document.hasFocus());
}
window.addEventListener("focus", syncOrb);
window.addEventListener("blur", syncOrb);
document.addEventListener("visibilitychange", syncOrb);
syncOrb();

// Systems panel
function gauge(id: string, pct: number | null, label: string) {
  const el = document.getElementById(id)!;
  el.querySelector("b")!.textContent = pct === null ? "n/a" : label;
  el.style.setProperty("--v", `${pct ?? 0}%`);
  el.classList.toggle("is-high", (pct ?? 0) >= 85);
}
async function pollStats() {
  if (isPaused || document.hidden || !document.hasFocus()) return;
  try {
    const s = await (await fetch("/api/pc/stats")).json();
    if (s.cpu === undefined) return;
    gauge("g-cpu", s.cpu, `${Math.round(s.cpu)}%`);
    gauge("g-ram", s.ram, `${s.ram_gb} GB`);
    gauge("g-gpu", s.gpu, `${Math.round(s.gpu)}% · ${Math.round(s.gpu_temp)}°`);
    gauge("g-vram", s.vram, `${Math.round(s.vram)}%`);
  } catch { /* the server is restarting; the next poll will find it */ }
}
pollStats();
setInterval(pollStats, 3000);

// Settings button
const btnSettings = document.getElementById("btn-settings")!;
btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  openSettings();
});

// First-time setup detection — check after a short delay for server readiness
setTimeout(() => {
  checkFirstTimeSetup();
}, 2000);
