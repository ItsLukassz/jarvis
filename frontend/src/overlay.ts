/**
 * JARVIS — the overlay.
 *
 * A small transparent, click-through, always-on-top window (electron/main.js)
 * that shows the orb over whatever is on screen while he is engaged, then
 * fades away. It is a mirror, not a second JARVIS: no socket, no microphone,
 * no audio. The main page tells it what is happening over a BroadcastChannel.
 */

import { createOrb, type OrbState } from "./orb";
import { applyTheme } from "./themes";
import "./style.css";

document.documentElement.classList.add("overlay");

const orb = createOrb(document.getElementById("orb-canvas") as HTMLCanvasElement, 1.35);
orb.setPaused(true);                     // nothing is drawn while it is hidden
applyTheme(orb);

// His voice, as the main page's analyser hears it.
const freq = new Uint8Array(64);
orb.setAnalyser({
  frequencyBinCount: freq.length,
  getByteFrequencyData: (out: Uint8Array) => out.set(freq.subarray(0, out.length)),
} as unknown as AnalyserNode);

const wordEl = document.getElementById("state-word")!;
const caption = document.createElement("div");
caption.id = "overlay-caption";
document.body.appendChild(caption);

// The main process shows and hides the window on this title: the page needs
// no privileged bridge to say one bit.
// It carries where and how big as well (Settings > Overlay Orb, kept in
// localStorage), which the main process validates before using.
function setTitle(mode: "on" | "off" | "preview") {
  let cfg: { pos?: string; size?: number } = {};
  try { cfg = JSON.parse(localStorage.getItem("jarvis-overlay") ?? "{}"); } catch { /* defaults */ }
  document.title = `jarvis-overlay:${mode}:${cfg.pos ?? "bottom-center"}:${cfg.size ?? 440}`;
}
let hideTimer = 0;
let shown = false;

function show(mode: "on" | "preview" = "on") {
  clearTimeout(hideTimer);
  if (shown && mode === "on") return;
  shown = true;
  setTitle(mode);
  orb.setPaused(false);
  document.body.classList.add("on");
}
setTitle("off");                         // puts the window where Settings says, hidden

function hideSoon(ms: number) {
  clearTimeout(hideTimer);
  hideTimer = window.setTimeout(() => {
    document.body.classList.remove("on");          // fade, then let go of the window
    hideTimer = window.setTimeout(() => {
      shown = false;
      caption.textContent = "";
      orb.setPaused(true);
      setTitle("off");
    }, 450);
  }, ms);
}

new BroadcastChannel("jarvis-overlay").onmessage = ({ data }) => {
  if (data.type === "config") {
    applyTheme(orb);
    // Settings changed: appear in the new place for a moment, even though the
    // main window is in front.
    caption.textContent = "This is where I will appear.";
    caption.dataset.who = "said";
    show("preview");
    hideSoon(2500);
  } else if (data.type === "wake") {
    orb.setState("listening");
    document.body.dataset.state = "listening";
    wordEl.textContent = "listening";
    caption.textContent = "";
    show();
    hideSoon(20000);                 // a wake that never became a request
  } else if (data.type === "freq") {
    freq.set(data.data);
  } else if (data.type === "heard" || data.type === "said") {
    caption.textContent = data.text;
    caption.dataset.who = data.type;
    show();
  } else if (data.type === "state") {
    const state = data.state as OrbState;
    orb.setState(state);
    document.body.dataset.state = state;
    wordEl.textContent = data.word;
    freq.fill(0);
    if (state === "thinking" || state === "speaking") show();
    else if (shown) hideSoon(state === "listening" ? 3500 : 800);
  }
};
