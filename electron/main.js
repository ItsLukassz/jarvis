"use strict";
const { app, BrowserWindow, Menu, Tray, dialog, screen, session, shell, systemPreferences } = require("electron");
const path = require("node:path");
const { createSupervisor } = require("./server");
const { sameOrigin, grantsPermission, dashboardUrl } = require("./policy");
const { sttWarning } = require("./backend");
const { findPython } = require("./python");
const {
  LOGIN_NAME, startAtLoginSupported, loginItem, launchedAtLogin, startsAtLogin,
} = require("./login");

const ORIGIN = process.env.JARVIS_ORIGIN || "http://127.0.0.1:8340";
const REPO_ROOT = path.resolve(__dirname, "..");
// To the console AND a file: started by Windows at login there is no console,
// and "it said the server was offline" is otherwise all anyone has to go on.
const APP_LOG = path.join(REPO_ROOT, "data", "jarvis-app.log");
const log = (m) => {
  console.log(`[jarvis] ${m}`);
  try {
    require("node:fs").appendFileSync(APP_LOG, `${new Date().toISOString()} ${m}\n`);
  } catch {
    // a log that cannot be written is not a reason to fail
  }
};
// Started by Windows at login: the tray, not a window. See login.js.
const STARTED_AT_LOGIN = launchedAtLogin(process.argv);

// HTTP and not HTTPS, deliberately. The certificates exist for one reason --
// frontend/vite.config.ts hard-codes an HTTPS proxy target -- and there is no
// Vite here. `http://127.0.0.1` is a secure context by the same rule that
// makes localhost one, so getUserMedia works and the openssl step disappears
// from this path. It does NOT disappear from the dev-server workflow, and
// since that leaves the certs beside server.py, the supervisor passes
// --no-ssl.

// One application per machine. A second copy would probe, find the first
// one's server, and attach to it; quitting the first would then kill the
// server out from under the second. The second copy shows the first instead.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Windows names the source of every notification -- the tray's balloons
  // are shown as toasts -- by the process's AppUserModelID. An unpackaged
  // Electron app's default is Electron's own, so Ken's first live run saw
  // "Electron" on JARVIS's notices. Windows-only API; the same name the
  // login entry uses (login.js), for the same reason.
  if (process.platform === "win32") app.setAppUserModelId(LOGIN_NAME);
  let win = null;
  let dashboard = null;   // the run monitor's own window, when it is open
  let overlay = null;     // the orb that appears over other apps while he is engaged
  let tray = null;        // held here so it is never garbage-collected away
  // The one flag that separates "the user closed the window" from "the
  // application is quitting". It is set in before-quit, which every quit
  // passes through -- the tray's Quit, Cmd+Q, anything that calls
  // app.quit(). Set only by the tray, any other quit would stop the server
  // in before-quit and then have the window's close handler cancel the quit
  // by hiding: a hidden JARVIS with no server behind it.
  let reallyQuitting = false;
  let toldAboutTheTray = false;
  const supervisor = createSupervisor({
    repoRoot: REPO_ROOT,
    origin: ORIGIN,
    // At login the machine is still starting everything else; see server.js.
    startTimeoutMs: STARTED_AT_LOGIN ? 300000 : 120000,
    deps: { log },
  });

  function showWindow() {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }

  app.on("second-instance", showWindow);

  // The login entry, as Windows is asked for it AND as it is read back:
  // getLoginItemSettings on Windows only answers for the same path and args.
  function ourLoginItem() {
    return loginItem(process.execPath, app.getAppPath());
  }

  function trayMenu() {
    const items = [
      { label: "Show JARVIS", click: showWindow },
      { label: "Dashboard", click: openDashboard },
    ];
    if (startAtLoginSupported(process.platform)) {
      items.push({
        label: "Start with Windows",
        type: "checkbox",
        // What Windows reports, not what this process last wrote -- read
        // by name; see startsAtLogin for why openAtLogin will not do.
        checked: startsAtLogin(app.getLoginItemSettings(ourLoginItem())),
        click: (item) => {
          app.setLoginItemSettings({ ...ourLoginItem(), openAtLogin: item.checked });
          tray.setContextMenu(trayMenu());
        },
      });
    }
    items.push(
      { type: "separator" },
      // Quit must be findable. An application a person cannot work out how
      // to exit is worse than one that simply closes when you close it.
      { label: "Quit JARVIS", click: () => app.quit() },
    );
    return Menu.buildFromTemplate(items);
  }

  function createTray() {
    tray = new Tray(path.join(__dirname, "tray-icon.png"));
    tray.setToolTip("JARVIS (listening)");
    tray.setContextMenu(trayMenu());
    tray.on("double-click", showWindow);
    if (STARTED_AT_LOGIN && process.platform === "win32") {
      tray.displayBalloon({
        title: "JARVIS started with Windows",
        content: "It is listening. Open it from the tray icon.",
        noSound: true,
      });
    }
  }

  // The first time the window is closed, say where JARVIS went. Closing
  // hides an application that still holds the microphone, and the person
  // who closed it has every reason to think it has gone. Once per run, and
  // silent -- a notice about listening should not make a sound. The balloon
  // is Windows-only; macOS gets nothing here yet.
  function explainTheTray() {
    if (toldAboutTheTray || !tray || process.platform !== "win32") return;
    toldAboutTheTray = true;
    tray.displayBalloon({
      title: "JARVIS is still listening",
      content: "Closing the window hides it. To quit, use the tray icon.",
      noSound: true,
    });
  }

  function grantMicrophone() {
    // Mandatory: Electron DENIES media unless this handler exists (measured
    // in 4-zero), in a way indistinguishable from the macOS TCC case below.
    // Every decision is logged -- the policy relies on Electron reporting the
    // requesting URL and the media types, and a wrong guess about either
    // would be a deaf JARVIS with nothing on screen to say why.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback, details) => {
      const granted = grantsPermission(permission, details, ORIGIN);
      log(`permission ${permission} from ${details && details.requestingUrl} ` +
          `mediaTypes=${JSON.stringify(details && details.mediaTypes)} -> ` +
          `${granted ? "granted" : "DENIED"}`);
      callback(granted);
    });
  }

  async function askMacOS() {
    // macOS only, and UNVERIFIED -- written from documentation on a Windows
    // box, which is the condition that cost this port two wrong premises. TCC
    // is a SECOND gate the handler above does not satisfy; a packaged build
    // also needs NSMicrophoneUsageDescription in its Info.plist. Install the
    // handler, still fail, and there is no way to tell which gate is shut.
    if (process.platform !== "darwin") return;
    try {
      const status = systemPreferences.getMediaAccessStatus("microphone");
      log(`macOS microphone TCC status: ${status}`);
      if (status !== "granted") {
        log(`askForMediaAccess -> ${await systemPreferences.askForMediaAccess("microphone")}`);
      }
    } catch (e) {
      log(`TCC probe failed: ${e.message}`);
    }
  }

  // The window holds the microphone, so it shows JARVIS and nothing else. A
  // link off the JARVIS origin goes to the user's own browser, and only an
  // http(s) one -- shell.openExternal hands any scheme to the OS.
  function openOutside(url) {
    try {
      const { protocol } = new URL(url);
      if (protocol === "http:" || protocol === "https:") shell.openExternal(url);
    } catch {
      // not a URL; nothing to open
    }
  }

  // Both windows show JARVIS and nothing else: off-origin navigation and every
  // window.open go to the user's own browser instead (openOutside).
  function lockToOrigin(w) {
    w.webContents.on("will-navigate", (event, url) => {
      if (sameOrigin(url, ORIGIN)) return;
      event.preventDefault();
      openOutside(url);
    });
    w.webContents.setWindowOpenHandler(({ url }) => {
      openOutside(url);
      return { action: "deny" };
    });
  }

  // The window's OWN icon -- the taskbar takes the shortcut's, but the title
  // bar would otherwise show Electron's -- and no menu bar: Electron's
  // default File/Edit/View menu is a developer's, with reload and DevTools
  // on it. removeMenu is per window on Windows and Linux; macOS keeps its
  // application menu, which Cmd+Q and copy/paste need there.
  const ICON = path.join(__dirname, "jarvis.ico");

  // The run monitor, in a window of its own so the voice window is never
  // navigated away from the orb and never stops listening. Closing it
  // closes it; the tray opens it again.
  function openDashboard() {
    if (dashboard) {
      if (dashboard.isMinimized()) dashboard.restore();
      dashboard.show();
      dashboard.focus();
      return;
    }
    dashboard = new BrowserWindow({
      width: 1200,
      height: 850,
      title: "JARVIS dashboard",
      icon: ICON,
      backgroundColor: "#111111",
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    dashboard.removeMenu();
    lockToOrigin(dashboard);
    dashboard.on("closed", () => { dashboard = null; });
    dashboard.loadURL(dashboardUrl(ORIGIN));
  }

  function createWindow() {
    win = new BrowserWindow({
      width: 1100,
      height: 800,
      title: "JARVIS",
      icon: ICON,
      // No frame, see-through: the page draws its own edge, drag strip and
      // close button (frontend/src/style.css). The price on Windows is that
      // a transparent window cannot be resized or maximised.
      frame: false,
      transparent: true,
      resizable: false,
      // At login, the tray and not a window -- still listening: phase 6's
      // Task 1 measured a never-shown window capturing 99.9%, no gesture.
      show: !STARTED_AT_LOGIN,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        // REQUIRED for tray residency. Measured in Task 1: a hidden window with
        // the default (true) captured 67.1% of its audio over ten minutes, the
        // same window visible captured 100.0%, and with this set to false it
        // captured 95.7%. Throttling starts within ~30s of hiding. Without this
        // line, closing JARVIS to the tray loses a third of what is said to it,
        // and it transcribes as garbage rather than failing cleanly.
        backgroundThrottling: false,
      },
    });
    win.removeMenu();
    lockToOrigin(win);
    // Closing HIDES. The server keeps running and JARVIS keeps listening --
    // the whole point of tray residency, and only sound because Task 1
    // measured audio surviving a hidden window (with backgroundThrottling
    // off, above).
    win.on("close", (event) => {
      if (reallyQuitting) return;
      event.preventDefault();
      win.hide();
      explainTheTray();
    });
    // Windows logoff and shutdown do NOT emit before-quit (Electron's
    // documentation; not exercised here), so the window hears it instead --
    // without this, the close handler above would hide and hold up the
    // session ending.
    win.on("session-end", () => { reallyQuitting = true; });
    win.on("closed", () => { win = null; });
    win.loadURL(ORIGIN);
    return win;
  }

  // The orb, over whatever is on screen, while he is hearing a request,
  // thinking or speaking -- and only when the main window is not already in
  // front. Click-through and never focused: it must not take a keystroke from
  // the game or document underneath. The page (frontend/src/overlay.ts) only
  // mirrors the main window, and asks to be shown or hidden through its
  // title, so the preload stays empty of privilege.
  // Where and how big: Settings > Overlay Orb, which reaches here in the
  // overlay page's title. It is a page's say-so, so nothing is taken on
  // trust: an unknown position means the default, the size is clamped.
  function placeOverlay(pos, sizeText) {
    const size = Math.min(720, Math.max(280, Math.round(Number(sizeText)) || 440));
    const [v, h] = String(pos).split("-");
    const area = screen.getPrimaryDisplay().workArea;
    const MARGIN = 24;
    const along = (start, length, where) =>
      where === "left" || where === "top" ? start + MARGIN
        : where === "center" || where === "middle" ? Math.round(start + (length - size) / 2)
          : start + length - size - MARGIN;
    overlay.setBounds({
      x: along(area.x, area.width, h || "center"),
      y: along(area.y, area.height, v || "bottom"),
      width: size,
      height: size,
    });
  }

  function createOverlay() {
    overlay = new BrowserWindow({
      frame: false,
      transparent: true,
      resizable: false,
      focusable: false,
      skipTaskbar: true,
      hasShadow: false,
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,      // hidden is its normal state
      },
    });
    overlay.setIgnoreMouseEvents(true);
    lockToOrigin(overlay);
    overlay.webContents.on("page-title-updated", (event, title) => {
      event.preventDefault();
      if (!overlay || !title.startsWith("jarvis-overlay:")) return;
      const [, mode, pos, sizeText] = title.split(":");
      placeOverlay(pos, sizeText);
      const inFront = win && win.isVisible() && !win.isMinimized() && win.isFocused();
      // "preview" is Settings showing the new place: wanted even in front.
      if (mode === "preview" || (mode === "on" && !inFront)) {
        overlay.setAlwaysOnTop(true, "screen-saver");
        overlay.showInactive();
        log(`overlay: shown (${mode}) at ${JSON.stringify(overlay.getBounds())}`);
      } else if (overlay.isVisible()) {
        overlay.hide();
        log("overlay: hidden");
      }
    });
    overlay.on("closed", () => { overlay = null; });
    placeOverlay("bottom-center", 440);
    overlay.loadURL(new URL("/?overlay", ORIGIN).href);
  }

  app.whenReady().then(async () => {
    grantMicrophone();
    await askMacOS();

    const result = await supervisor.start();
    log(`server: ${result.state} -- ${result.detail}`);

    if (result.state === "occupied" || result.state === "failed") {
      dialog.showErrorBox("JARVIS could not start", result.detail);
      app.quit();
      return;
    }
    createWindow();
    createOverlay();
    createTray();
    await warnIfDeaf();
  });

  // After the window, not before: a warning in front of a blank screen reads
  // like a crash, and this is information rather than a failure to start.
  // A status that cannot be read is logged and nothing is shown -- see
  // backend.js on not warning from a guess.
  async function warnIfDeaf() {
    try {
      const response = await fetch(`${ORIGIN}/api/settings/status`,
                                   { signal: AbortSignal.timeout(5000) });
      const warning = sttWarning(await response.json(),
                                 findPython(REPO_ROOT) || "python");
      if (!warning) return;
      log(`speech recognition: ${warning.split("\n")[0]}`);
      // Not hung off a window nobody can see: at login there is none showing.
      dialog.showMessageBox(win && win.isVisible() ? win : undefined,
                            { type: "warning", title: "JARVIS cannot hear", message: warning });
    } catch (e) {
      log(`could not read settings status: ${e.message}`);
    }
  }

  // Deliberately does nothing. Closing the window hides it; the application
  // exits through the tray's Quit, or any other app.quit().
  app.on("window-all-closed", () => {});

  app.on("before-quit", () => {
    reallyQuitting = true;
    if (overlay) overlay.destroy();
    supervisor.stop();
  });
}
