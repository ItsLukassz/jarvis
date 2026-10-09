"use strict";
const path = require("node:path");

// Every collaborator is injected so the whole lifecycle is testable without
// starting a process or opening a socket. The tests are the reason the hard
// part of this application has no Electron in it.
// startTimeoutMs: how long a server this process started may take to answer.
// Thirty seconds was the whole allowance, and it is not enough at login: the
// server needs ~14s on a warm machine (measured 2026-10-09), and at boot it
// competes with everything else Windows is starting, on a cold disk. Every
// login then ended in "the server did not answer". A server that DIES is
// still reported at once (the exit listener below), so a generous wait costs
// nothing when something is really wrong.
function createSupervisor({ repoRoot, origin, startTimeoutMs = 120000, deps = {} }) {
  const {
    findPython = require("./python").findPython,
    probe = require("./health").probe,
    waitForJarvis = require("./health").waitForJarvis,
    spawn = require("node:child_process").spawn,
    log = () => {},
  } = deps;

  let child = null;
  let startedByUs = false;

  async function start() {
    const found = await probe(origin);

    if (found === "jarvis") {
      // Attach. Never start a second: two servers means two brains on one
      // Claude subscription and two writers on one SQLite database, and
      // run_store is not built for that -- the second would be quietly wrong.
      log(`attaching to the JARVIS already serving ${origin}`);
      startedByUs = false;
      return { state: "attached", detail: origin };
    }

    if (found === "stranger") {
      // Refuse rather than guess. The likeliest occupant is a developer's
      // own JARVIS: started with the dev workflow's certs it serves HTTPS,
      // and to an http:// probe that is indistinguishable from a stranger.
      return {
        state: "occupied",
        detail: `Something is already using ${origin}, and it is not a JARVIS ` +
                `this application can talk to. If it is your own JARVIS started ` +
                `with cert.pem/key.pem, it is serving HTTPS -- stop it and let ` +
                `this application start one.`,
      };
    }

    const python = findPython(repoRoot);
    if (!python) {
      return {
        state: "failed",
        detail: `No Python interpreter found. Expected a venv at ` +
                `${path.join(repoRoot, ".venv")}.`,
      };
    }

    // --no-ssl: server.py turns HTTPS on by itself whenever the certs are
    // beside it, and this window loads http://. Host and port come from the
    // origin so the server listens exactly where the window will look.
    const { hostname, port } = new URL(origin);
    const args = ["server.py", "--host", hostname, "--port", port || "80", "--no-ssl"];
    log(`starting ${python} ${args.join(" ")}`);
    child = spawn(python, args, {
      cwd: repoRoot,
      stdio: "inherit",
      // UNVERIFIED until Task 8: without it, a console program spawned from
      // a GUI process on Windows is expected to open a console window.
      windowsHide: true,
    });
    startedByUs = true;

    // Listened for at once: an unhandled "error" on a ChildProcess is
    // thrown, and in Electron's main process that is the app crashing. And
    // a server that dies in its first second (a missing requirement) should
    // say so then, not after the whole health deadline.
    const died = new Promise((resolve) => {
      child.once("exit", (code, signal) => resolve(
        `The server exited (${signal || `code ${code}`}) before answering on ${origin}.`));
      child.once("error", (error) => resolve(
        `The server could not be started: ${error.code || error.message}.`));
    });

    const outcome = await Promise.race([
      waitForJarvis(origin, { timeoutMs: startTimeoutMs }).then((ok) => (ok ? "healthy" : "timeout")),
      died,
    ]);
    if (outcome === "healthy") return { state: "started", detail: origin };

    if (outcome === "timeout") {
      // Ours, and given up on: killed, so a server that limps up later is
      // not left running behind a message that says it failed.
      stop();
      return {
        state: "failed",
        detail: `The server was started but did not answer on ${origin}.`,
      };
    }
    child = null;
    return { state: "failed", detail: outcome };
  }

  function stop() {
    // The one boolean that must never be guessed at. Killing a server this
    // process attached to would take down somebody's dev server on exit.
    //
    // Measured on Windows, 2026-09-11: kill() on the venv's python.exe (a
    // redirector that runs the real interpreter as ITS child) took the whole
    // tree -- interpreter, brain, the brain's MCP child -- and freed the
    // port. So did this process exiting without calling stop() at all.
    // macOS: SIGTERM to the interpreter itself; UNVERIFIED.
    if (!startedByUs || !child) return;
    try {
      child.kill();
    } catch {
      // already gone
    }
    child = null;
  }

  return {
    start,
    stop,
    get startedByUs() { return startedByUs; },
  };
}

module.exports = { createSupervisor };
