/**
 * JARVIS — Settings Panel
 *
 * Overlay panel for API keys, connection status, preferences, and system info.
 * Slides in from the right with glass-morphism styling.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StatusResponse {
  claude_code_installed: boolean;
  server_port: number;
  uptime_seconds: number;
  tts_backend: string;
  tts_voice: string;
  tts_piper_voice: string;
  tts_piper_voices: string[];
  tts_backends_ready: Record<string, boolean>;
  tts_fallback_from: string | null;
  env_keys_set: {
    fish_audio: boolean;
    fish_voice_id: boolean;
    user_name: string;
  };
  // Whether this host gates capture with a JARVIS setting at all, and where
  // that setting stands. Two facts, not one: macOS gates capture through
  // System Settings, so the section stays hidden there rather than drawing a
  // switch JARVIS does not own.
  screen_capture_gated: boolean;
  screen_capture: boolean;
}

interface PreferencesResponse {
  user_name: string;
  honorific: string;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let panelEl: HTMLElement | null = null;
let isOpen = false;
let isFirstTimeSetup = false;
let setupStep = 0; // 0=fish, 1=name, 2=done

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return res.json();
}

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Panel HTML
// ---------------------------------------------------------------------------

function buildPanelHTML(): string {
  return `
    <div class="settings-backdrop" id="settings-backdrop"></div>
    <div class="settings-panel" id="settings-panel-inner">
      <div class="settings-header">
        <h2>Settings</h2>
        <button class="settings-close" id="settings-close">&times;</button>
      </div>

      <div class="settings-welcome" id="settings-welcome" style="display:none">
        <p>Welcome to JARVIS. Let's get you set up.</p>
      </div>

      <div class="settings-body">

        <!-- Voice -->
        <section class="settings-section" id="section-voice">
          <h3>Voice</h3>

          <div class="settings-field">
            <label>Synthesiser</label>
            <select id="input-tts-backend">
              <option value="say">macOS say — local, fastest</option>
              <option value="piper">piper — local, neural</option>
              <option value="fish">Fish Audio — hosted, needs a key</option>
            </select>
          </div>

          <div class="settings-field" id="field-say-voice">
            <label>System voice</label>
            <input type="text" id="input-tts-voice" placeholder="Daniel" />
          </div>

          <div class="settings-field" id="field-piper-voice">
            <label>piper model</label>
            <select id="input-piper-voice"></select>
          </div>

          <div class="settings-actions">
            <button class="settings-btn" id="btn-preview-voice">Preview</button>
            <button class="settings-btn primary" id="btn-save-voice">Save Voice</button>
          </div>
          <p class="settings-voice-note" id="tts-voice-note"></p>
        </section>

        <!-- API Keys -->
        <section class="settings-section" id="section-api-keys">
          <h3>API Keys</h3>

          <div class="settings-field">
            <label>Fish Audio API Key</label>
            <div class="settings-input-row">
              <input type="password" id="input-fish-key" placeholder="Fish Audio key..." />
              <button class="settings-btn" id="btn-test-fish">Test</button>
              <span class="status-dot" id="status-fish"></span>
            </div>
          </div>

          <div class="settings-field">
            <label>Fish Voice ID</label>
            <div class="settings-input-row">
              <input type="text" id="input-fish-voice-id" placeholder="612b878b113047d9a770c069c8b4fdfe" />
              <button class="settings-btn" id="btn-save-voice-id">Save</button>
            </div>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-keys">Save Keys</button>
          </div>
          <p class="settings-restart-notice" id="keys-restart-notice" style="display:none">
            Saved. Restart the server (menu &rarr; Restart Server) for this to take effect.
          </p>
        </section>

        <!-- Connection Status -->
        <section class="settings-section" id="section-status">
          <h3>Connection Status</h3>
          <div class="status-grid">
            <div class="status-row"><span class="status-dot" id="status-claude-cli"></span><span>Claude Code CLI</span></div>
            <div class="status-row"><span class="status-dot" id="status-server"></span><span>Server</span><span class="status-detail" id="status-server-detail"></span></div>
          </div>
        </section>

        <!-- User Preferences -->
        <section class="settings-section" id="section-preferences">
          <h3>User Preferences</h3>

          <div class="settings-field">
            <label>Your Name</label>
            <input type="text" id="input-user-name" placeholder="Your name" />
          </div>

          <div class="settings-field">
            <label>Honorific</label>
            <select id="input-honorific">
              <option value="sir">Sir</option>
              <option value="ma'am">Ma'am</option>
              <option value="none">None</option>
            </select>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-prefs">Save Preferences</button>
          </div>
        </section>

        <!-- Screen capture. Hidden entirely unless this host is one whose
             gate is JARVIS's own setting: on macOS Screen Recording lives in
             System Settings and a toggle here would be a switch that does
             nothing. Saved on change rather than behind a Save button --
             revoking must be one click, or the consent model is weaker than
             the one it replaces. -->
        <section class="settings-section" id="section-screen" hidden>
          <h3>Screen</h3>

          <div class="settings-field">
            <label>
              <input type="checkbox" id="input-screen-capture" />
              Let JARVIS see your screen
            </label>
            <p class="settings-hint">
              Off by default. Windows asks nobody before a program reads the
              screen, so this switch is JARVIS's own. Reading window TITLES
              needs no permission and is unaffected.
            </p>
          </div>
        </section>

        <!-- The orb that appears over other apps. Kept in this browser's own
             storage and applied as it is changed: the overlay window reads it
             and shows itself for a moment so the choice can be seen. -->
        <section class="settings-section" id="section-overlay">
          <h3>Overlay Orb</h3>

          <div class="settings-field">
            <label>Where it appears</label>
            <div class="overlay-grid" id="overlay-pos">
              ${["top", "middle", "bottom"].flatMap((v) => ["left", "center", "right"].map((h) =>
                `<button type="button" data-pos="${v}-${h}" title="${v} ${h}" aria-label="${v} ${h}"></button>`)).join("")}
            </div>
          </div>

          <div class="settings-field">
            <label>Size <span id="overlay-size-value"></span></label>
            <input type="range" id="overlay-size" min="280" max="720" step="20" />
            <p class="settings-hint">
              It appears while JARVIS is hearing a request, thinking or
              speaking, and only when this window is not in front.
            </p>
          </div>
        </section>

        <!-- System Info -->
        <section class="settings-section" id="section-sysinfo">
          <h3>System Info</h3>
          <div class="sysinfo-grid">
            <div class="sysinfo-row"><span class="sysinfo-label">Server port</span><span id="sysinfo-port">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">Uptime</span><span id="sysinfo-uptime">--</span></div>
          </div>
        </section>

        <!-- Setup Navigation (first-time only) -->
        <div class="setup-nav" id="setup-nav" style="display:none">
          <button class="settings-btn primary" id="btn-setup-next">Next</button>
        </div>

      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Panel lifecycle
// ---------------------------------------------------------------------------

function createPanel(): HTMLElement {
  const container = document.createElement("div");
  container.id = "settings-container";
  container.innerHTML = buildPanelHTML();
  document.body.appendChild(container);
  return container;
}

function setDotStatus(id: string, status: "green" | "red" | "yellow" | "off") {
  const dot = document.getElementById(id);
  if (!dot) return;
  dot.className = "status-dot";
  if (status !== "off") dot.classList.add(`status-${status}`);
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

async function loadStatus() {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");

    setDotStatus("status-claude-cli", status.claude_code_installed ? "green" : "red");
    setDotStatus("status-server", "green");

    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = `port ${status.server_port} | up ${formatUptime(status.uptime_seconds)}`;

    // The Fish key is only a problem when Fish is the voice in use. On the
    // local backend a missing key is not a fault to report red — it is a key
    // nothing reads, so the honest dot is the absence of a reading.
    const fishInUse = status.tts_backend === "fish";
    setDotStatus("status-fish",
      status.env_keys_set.fish_audio ? "green" : fishInUse ? "red" : "off");

    renderVoiceSection(status);
    renderScreenSection(status);

    // System info
    const portEl = document.getElementById("sysinfo-port");
    if (portEl) portEl.textContent = String(status.server_port);
    const upEl = document.getElementById("sysinfo-uptime");
    if (upEl) upEl.textContent = formatUptime(status.uptime_seconds);

    return status;
  } catch (e) {
    console.error("[settings] failed to load status:", e);
    // EVERY dot, not just the server's. The CLI and Fish dots are drawn from
    // fields of the answer that never arrived, so leaving them green states
    // as fact something this call failed to find out. "off" is the absence
    // of a reading, which is what we have.
    setDotStatus("status-server", "red");
    setDotStatus("status-claude-cli", "off");
    setDotStatus("status-fish", "off");
    const voiceNote = document.getElementById("tts-voice-note");
    if (voiceNote) voiceNote.textContent = "";
    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = "no answer from the server";
    for (const id of ["sysinfo-port", "sysinfo-uptime"]) {
      const node = document.getElementById(id);
      if (node) node.textContent = "—";
    }
    return null;
  }
}

function renderScreenSection(status: StatusResponse) {
  const section = document.getElementById("section-screen");
  if (section) section.hidden = !status.screen_capture_gated;
  const box = document.getElementById("input-screen-capture") as HTMLInputElement | null;
  if (box) box.checked = status.screen_capture;
}

async function loadPreferences() {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    const nameEl = document.getElementById("input-user-name") as HTMLInputElement;
    const honEl = document.getElementById("input-honorific") as HTMLSelectElement;
    if (nameEl) nameEl.value = prefs.user_name || "";
    if (honEl) honEl.value = prefs.honorific || "sir";
  } catch (e) {
    console.error("[settings] failed to load preferences:", e);
  }
}

// Overlay orb: position and size, shared with overlay.ts through localStorage.
function wireOverlaySettings() {
  const grid = document.getElementById("overlay-pos");
  const size = document.getElementById("overlay-size") as HTMLInputElement | null;
  const sizeValue = document.getElementById("overlay-size-value");
  if (!grid || !size || !sizeValue) return;
  let saved: { pos?: string; size?: number } = {};
  try { saved = JSON.parse(localStorage.getItem("jarvis-overlay") ?? "{}"); } catch { /* defaults */ }
  const cfg = { pos: saved.pos ?? "bottom-center", size: saved.size ?? 440 };
  const channel = new BroadcastChannel("jarvis-overlay");

  const render = () => {
    grid.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset.pos === cfg.pos));
    size.value = String(cfg.size);
    sizeValue.textContent = `${cfg.size} px`;
  };
  const save = () => {
    localStorage.setItem("jarvis-overlay", JSON.stringify(cfg));
    channel.postMessage({ type: "config" });      // the overlay shows itself in the new place
    render();
  };
  grid.addEventListener("click", (e) => {
    const pos = (e.target as HTMLElement).dataset.pos;
    if (pos) { cfg.pos = pos; save(); }
  });
  size.addEventListener("input", () => { cfg.size = Number(size.value); save(); });
  render();
}

function wireEvents() {
  wireOverlaySettings();
  // Close
  document.getElementById("settings-close")?.addEventListener("click", closeSettings);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeSettings);

  // Save keys
  document.getElementById("input-tts-backend")?.addEventListener("change", async () => {
    const chosen = (document.getElementById("input-tts-backend") as HTMLSelectElement).value;
    const status = await apiGet<StatusResponse>("/api/settings/status");
    showVoiceFieldsFor(chosen, status);
  });

  // Saved the moment it moves, not behind a Save button: revoking has to be
  // one click. The checkbox is put back from the SERVER's answer afterwards,
  // so a write that was refused cannot leave the page showing an eye that is
  // open when it is shut.
  document.getElementById("input-screen-capture")?.addEventListener("change", async () => {
    const box = document.getElementById("input-screen-capture") as HTMLInputElement;
    try {
      await apiPost("/api/settings/keys", {
        key_name: "JARVIS_SCREEN_CAPTURE",
        key_value: box.checked ? "true" : "false",
      });
    } catch (e) {
      console.error("[settings] could not save the screen capture switch:", e);
    }
    await loadStatus();
  });

  // Hear the piper voice in the dropdown before saving it.
  document.getElementById("btn-preview-voice")?.addEventListener("click", async () => {
    const voice = (document.getElementById("input-piper-voice") as HTMLSelectElement).value.trim();
    if (!voice) return;
    const res = await fetch("/api/settings/voice/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice }),
    });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    audio.volume = Number(localStorage.getItem("jarvis-volume") ?? "100") / 100;
    audio.onended = () => URL.revokeObjectURL(url);
    audio.play();
  });

  document.getElementById("btn-save-voice")?.addEventListener("click", async () => {
    const backend = (document.getElementById("input-tts-backend") as HTMLSelectElement).value;
    const sayVoice = (document.getElementById("input-tts-voice") as HTMLInputElement).value.trim();
    const piperVoice = (document.getElementById("input-piper-voice") as HTMLSelectElement).value.trim();

    // Both voices are saved whichever backend is chosen, so switching back
    // and forth keeps each one's own voice.
    await apiPost("/api/settings/keys", { key_name: "JARVIS_TTS_BACKEND", key_value: backend });
    if (sayVoice) await apiPost("/api/settings/keys", { key_name: "JARVIS_TTS_VOICE", key_value: sayVoice });
    if (piperVoice) await apiPost("/api/settings/keys", { key_name: "JARVIS_PIPER_VOICE", key_value: piperVoice });
    await loadStatus();
  });

  document.getElementById("btn-save-keys")?.addEventListener("click", async () => {
    const fishKey = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();

    let savedAny = false;
    if (fishKey) {
      await apiPost("/api/settings/keys", { key_name: "FISH_API_KEY", key_value: fishKey });
      savedAny = true;
    }
    await loadStatus();

    const notice = document.getElementById("keys-restart-notice");
    if (notice) notice.style.display = savedAny ? "block" : "none";
  });

  // Save voice ID
  document.getElementById("btn-save-voice-id")?.addEventListener("click", async () => {
    const voiceId = (document.getElementById("input-fish-voice-id") as HTMLInputElement).value.trim();
    if (voiceId) {
      await apiPost("/api/settings/keys", { key_name: "FISH_VOICE_ID", key_value: voiceId });
    }
  });

  // Test Fish
  document.getElementById("btn-test-fish")?.addEventListener("click", async () => {
    setDotStatus("status-fish", "yellow");
    const key = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-fish", { key_value: key || undefined });
      setDotStatus("status-fish", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-fish", "red");
    }
  });

  // Save preferences
  document.getElementById("btn-save-prefs")?.addEventListener("click", async () => {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement).value.trim();
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement).value;
    await apiPost("/api/settings/preferences", { user_name, honorific });
    await loadStatus();
  });

  // Setup next button
  document.getElementById("btn-setup-next")?.addEventListener("click", advanceSetup);
}

function renderVoiceSection(status: StatusResponse) {
  const select = document.getElementById("input-tts-backend") as HTMLSelectElement | null;
  const ready = status.tts_backends_ready || {};

  if (select) {
    // A backend that cannot speak is still LISTED — hiding it makes "why is
    // piper not here?" a question with no answer on the page — but it says so
    // and cannot be chosen by accident. The one in use is never disabled: the
    // user must be able to see what he is currently set to.
    for (const opt of Array.from(select.options)) {
      const usable = ready[opt.value] !== false;
      opt.disabled = !usable && opt.value !== status.tts_backend;
      opt.textContent = opt.textContent.replace(/ — unavailable$/, "")
        + (usable ? "" : " — unavailable");
    }
    select.value = status.tts_backend;
  }

  const sayVoice = document.getElementById("input-tts-voice") as HTMLInputElement | null;
  if (sayVoice && document.activeElement !== sayVoice) sayVoice.value = status.tts_voice || "";
  // Only models that are actually downloaded may be chosen. Typing a name
  // JARVIS does not have is not a recoverable mistake — he goes silent
  // mid-sentence, which is what "piper stopped working" looked like.
  const piperVoice = document.getElementById("input-piper-voice") as HTMLSelectElement | null;
  if (piperVoice && document.activeElement !== piperVoice) {
    const installed = status.tts_piper_voices || [];
    const current = status.tts_piper_voice || "";
    const names = installed.includes(current) || !current
      ? installed : [...installed, current];
    piperVoice.textContent = "";
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = installed.includes(name) ? name : `${name} — not downloaded`;
      opt.disabled = !installed.includes(name);
      piperVoice.appendChild(opt);
    }
    piperVoice.disabled = installed.length === 0;
    if (current) piperVoice.value = current;
  }

  showVoiceFieldsFor(select?.value || status.tts_backend, status);
}

// He says this out loud once, when it happens. The panel is where it stays
// readable afterwards — a spoken sentence is gone the moment it is said.
function fallbackNotice(status: StatusResponse): string {
  if (!status.tts_fallback_from) return "";
  return `${status.tts_fallback_from} could not speak, so he is using macOS say instead. `;
}

function showVoiceFieldsFor(backend: string, status: StatusResponse) {
  const sayField = document.getElementById("field-say-voice");
  if (sayField) sayField.style.display = backend === "say" ? "" : "none";
  const piperField = document.getElementById("field-piper-voice");
  if (piperField) piperField.style.display = backend === "piper" ? "" : "none";

  const note = document.getElementById("tts-voice-note");
  if (!note) return;
  const fallback = backend === status.tts_backend ? fallbackNotice(status) : "";
  if (backend === "fish") {
    note.textContent = fallback + (status.env_keys_set.fish_audio
      ? "Hosted: every sentence is a request to fish.audio, billed to that account."
      : "Fish Audio needs a key in API Keys below, and a server restart to pick it up.");
  } else if (backend === "piper") {
    const installed = status.tts_piper_voices || [];
    if (installed.length === 0) {
      note.textContent = fallback + "No piper models are downloaded. In the repo: "
        + "`pip install -r requirements-piper.txt` then "
        + "`python -m piper.download_voices --download-dir data/voices en_GB-alan-medium`.";
    } else if (!installed.includes(status.tts_piper_voice)) {
      note.textContent = fallback + `${status.tts_piper_voice} is set but not downloaded. `
        + "Pick one of the installed models, or download that one and reload.";
    } else {
      note.textContent = fallback + "Local and offline. Better than macOS say, and about a fifth of a "
        + "second slower per sentence. Models live in data/voices.";
    }
  } else {
    note.textContent = fallback + "Local, offline and instant. `say -v '?'` in a terminal lists the voices; "
      + "System Settings → Accessibility → Spoken Content adds better ones.";
  }
}

// ---------------------------------------------------------------------------
// First-time setup wizard
// ---------------------------------------------------------------------------

function enterSetupMode() {
  isFirstTimeSetup = true;
  setupStep = 0;

  const welcome = document.getElementById("settings-welcome");
  if (welcome) welcome.style.display = "block";

  const nav = document.getElementById("setup-nav");
  if (nav) nav.style.display = "flex";

  // Hide sections except API keys — the voice section included: the wizard
  // only ever appears when the backend is Fish and its key is missing.
  const voice = document.getElementById("section-voice");
  if (voice) voice.style.display = "none";
  showSetupStep(0);
}

function showSetupStep(step: number) {
  const sections = ["section-api-keys", "section-preferences"];
  sections.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (step === 0 && i === 0) el.style.display = "";
    else if (step === 1 && i === 1) el.style.display = "";
    else el.style.display = "none";
  });

  const nextBtn = document.getElementById("btn-setup-next");
  if (nextBtn) {
    if (step === 0) nextBtn.textContent = "Next: Set Your Name";
    else if (step === 1) nextBtn.textContent = "Finish Setup";
    else nextBtn.style.display = "none";
  }
}

async function advanceSetup() {
  setupStep++;
  if (setupStep >= 2) {
    // Done — save everything and close
    isFirstTimeSetup = false;
    const welcome = document.getElementById("settings-welcome");
    if (welcome) welcome.style.display = "none";
    const nav = document.getElementById("setup-nav");
    if (nav) nav.style.display = "none";

    // Show all sections
    ["section-voice", "section-api-keys", "section-status", "section-preferences",
     "section-sysinfo"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = "";
    });

    closeSettings();
    return;
  }
  showSetupStep(setupStep);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function openSettings() {
  if (isOpen) return;
  isOpen = true;

  if (!panelEl) {
    panelEl = createPanel();
    wireEvents();
  }

  panelEl.style.display = "block";

  // Trigger animation
  requestAnimationFrame(() => {
    panelEl!.classList.add("open");
  });

  // Load data
  const status = await loadStatus();
  await loadPreferences();

  // Check for first-time setup. A missing Fish key only means "not set up"
  // when Fish is the backend; the local voice needs nothing entered here, and
  // greeting that user with a setup wizard asks them for a key they will
  // never use.
  if (status && status.tts_backend === "fish" && !status.env_keys_set.fish_audio) {
    enterSetupMode();
  }
}

export function closeSettings() {
  if (!panelEl || !isOpen) return;
  isOpen = false;
  panelEl.classList.remove("open");
  setTimeout(() => {
    if (panelEl) panelEl.style.display = "none";
  }, 300);
}

export function isSettingsOpen(): boolean {
  return isOpen;
}

/**
 * Check if first-time setup is needed and auto-open.
 */
export async function checkFirstTimeSetup(): Promise<boolean> {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");
    // Only a voice that needs a key is a reason to open Settings unasked: with
    // a local voice (piper, say) a missing Fish key is not a missing anything.
    if (status.tts_backend === "fish" && !status.env_keys_set.fish_audio) {
      openSettings();
      return true;
    }
  } catch {
    // Server not ready yet, skip
  }
  return false;
}
