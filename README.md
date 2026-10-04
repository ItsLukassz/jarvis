# JARVIS

**Just A Rather Very Intelligent System — a voice for Claude Code.**

JARVIS is a British butler who sits on top of the Claude Code you already pay
for. You talk to him. He brainstorms a project with you out loud, one question
at a time; when you have settled on something he writes the design down as a
file in your project; then he starts a real Claude Code session on it and
drives it through plan → review → execute. While it runs he watches every
Claude Code session on your machine, and when one of them is stuck waiting on
a human he tells you which one, out loud, without you having to look.

> "Will do, sir."

![The JARVIS window while he is speaking. A frameless, see-through dark panel
with a glowing amber edge. In the centre a hollow globe of particles wired
together by faint lines, ringed by arcs, a tick dial and a radial spectrum of
bars standing out with his voice. On the left a Systems panel with segmented
bars for CPU, memory, graphics and video memory; on the right a Comms panel
logging what was said to him and his reply; underneath, the word SPEAKING in
wide letters and a Stop button. Volume, mute, pause, menu and close sit in a
glass dock at the top right.](docs/images/ui-main.png)

*The window you talk to: `frontend/src/orb.ts` and `frontend/src/style.css`,
rendered live. Everything takes its colour from what he is doing — blue idle,
cyan listening, violet thinking, amber speaking, grey paused — and the orb
holds still whenever the window is not in front, so it costs nothing while you
work.*

<img src="docs/images/ui-overlay.png" width="280" align="right"
     alt="The overlay: the same amber globe on its own, with a SPEAKING pill and a caption of what he is saying underneath.">

**The overlay.** When you talk to him from another app, the orb appears on its
own over whatever is on screen — transparent, click-through and never focused,
so it takes no keystroke from the game or document underneath — with a caption
of what he heard and what he is saying, then fades away. Where it appears and
how big it is are yours to set: Settings → Overlay Orb. (Shown here on black;
on your desktop only the globe and its labels are drawn.)

<br clear="right">

![A twenty-three second walkthrough of the JARVIS dashboard, looping. It opens
on Runs: a red "Needs Attention" panel over a failed run and a timed-out one,
then Active and History, every row carrying the project, the prompt, a status
pill, elapsed time and tokens. A run opens to show its prompt, cost, model and
live transcript. The Sessions tab shows every Claude Code conversation on the
machine grouped by project; clicking a blocked one swaps the right-hand column
from a tally into a red band reading "waiting on you for 51m — permission
prompt", with the question the CLI actually asked quoted underneath. Specs
shows a design document with big numbered sections you answer by voice.
Projects drills into one project's conversations, runs and build progress.
Usage ends on the subscription's two gauges — a five-hour window at 62 per
cent and a seven-day one at 84 per cent.](docs/images/dashboard-walkthrough.gif)

*The whole dashboard, clicked through. Fictional sample data throughout — the
projects, prompts, people and figures in every screenshot on this page are
invented.*

---

## What it costs

**No AI API usage, and none is possible.** JARVIS's brain is a Claude Code
process running on *your* Claude subscription — the same login you use in the
terminal. There is no Anthropic API key anywhere on the voice path, and there
is no way to accidentally put one there:

```python
# claude_env.py
SCRUBBED_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_")
SCRUBBED_ENV_KEYS = {"CLAUDECODE"}
```

Every Claude Code process JARVIS spawns — the brain and every build — is
launched through `claude_env.child_env()`, which strips **every** `ANTHROPIC_*`
variable out of the environment first. This is deliberate and it is not a
nicety: the CLI silently *prefers* an inherited `ANTHROPIC_API_KEY` over your
login, and `claude auth status` goes on reporting `loggedIn: true` while
billing quietly moves onto the key. So JARVIS removes the key rather than
trusting itself not to pass it. Leave one in your `.env` if you like — the
startup check will warn you it is there, and the brain will still never see it.

![The dashboard's Subscription panel, measured three minutes ago: a 5-hour
session gauge at 62 per cent that resets at 6:00pm, and a 7-day week gauge at 84
per cent that resets Sunday at 8:00am. Underneath, a note headed "what the CLI
reports": two windows, and no separate per-model
limit.](docs/images/dashboard-usage.png)

*So the number that matters is not a dollar figure — it is how much of your
subscription's two windows is gone. Fictional sample data.*

**And the voice is free too.** JARVIS speaks through macOS's own `say` —
offline, no key, no account. Measured on an M-series Mac with the en_GB voice
Daniel: about 0.45s per sentence whatever its length, against 1.2-3.5s of
audio, so he stays ahead of his own mouth. System Settings -> Accessibility ->
Spoken Content -> Manage Voices has a free "Daniel (Premium)" download that
the same setting picks up.

If `say` sounds too much like a satnav, **piper** is the middle option: a
neural voice that is still local and still offline, for one optional
dependency and a ~63 MB model. Measured with `en_GB-alan-medium` on the same
Mac: 0.67s per sentence warm, against `say`'s 0.45s.

```bash
pip install -r requirements-piper.txt
python -m piper.download_voices --download-dir data/voices en_GB-alan-medium
```

Then pick it in Settings → Voice, or set `JARVIS_TTS_BACKEND=piper`. The
panel only offers models you have actually downloaded, because a model JARVIS
does not have is not a mistake he can report mid-sentence — he would simply
stop speaking.

**And if a backend does fail, he does not go quiet.** Whatever is configured,
`say` takes over, and he tells you once: *"Piper has gone quiet, sir — I'm
using the system voice until it's sorted."* Silence is the one failure that
reads as "this thing is broken" rather than "this thing needs a file.

And if you would rather have a cloned or hosted voice, [Fish
Audio](https://fish.audio/) is still built in — set `FISH_API_KEY` and
`JARVIS_TTS_BACKEND=fish`. That key is read *only* when you ask for it, so one
left in `.env` from an earlier setup cannot quietly start billing. All three
live in one small, well-isolated file — see *Make it yours* below.

## What he does

- **Brainstorms out loud.** The conversation is the design phase. He asks one
  question at a time, offers two or three approaches, and does not start
  anything until you have agreed on one.
- **Writes the design down.** What you agreed goes on disk as
  `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` *inside the project
  being built* — before a single process is spawned. You can read it back by
  numbered section and approve it by voice, or open it on the dashboard.
- **Drives the build.** A build is a real `claude -p` session handed a brief
  that tells it to write a phased plan, review that plan against the spec,
  then execute it task by task under test-driven development, ticking the
  plan's checkboxes as it goes. "How far has it got" is answered by reading
  those checkboxes, not by guessing.
- **Watches every Claude Code session on the machine** — not just his own. Ask
  "which of my sessions are waiting on me?" and he checks live. He can post a
  message into one on either platform. Answering a permission prompt for one
  by pressing a single key is macOS only — Windows gives no way to aim a
  keystroke at one specific session, so JARVIS tells you and lets you press
  it yourself rather than guessing at the window.
- **Interrupts you when it matters.** A session that needs a human gets said
  out loud immediately; a session that merely finished gets batched into one
  sentence at the next pause. If nobody has the browser tab open, it becomes a
  desktop notification instead — Notification Centre on macOS, a toast on
  Windows.
- **Remembers.** Long-term memory is a folder of plain Markdown files, one
  fact per file, with an index the brain always sees. You can read and edit it
  in any text editor.
- **Records everything.** Every Claude Code process JARVIS starts is a *run*:
  a row in SQLite with its prompt, project, status, token usage and the full
  event stream. Watch them live at `/dashboard`.

![The Runs view of the JARVIS dashboard. A red "Needs Attention" panel holds a
failed run and a timed-out one, with the failure's exit code and error printed
under it. Below that, an Active panel with one run in progress and one queued,
then History. Every row shows the project, the prompt the run was given, a
status pill, how long it took, tokens spent, and the
time.](docs/images/dashboard-runs.png)

*The Runs view. Every Claude Code process JARVIS starts is a row here, with
the prompt that started it. Fictional sample data.*

The dashboard has six tabs — Runs, Sessions, Memory, Specs, Projects and
Usage. Usage shows what your subscription's five-hour and seven-day windows
have left, and who spent it.

![The Sessions view. On the left, a "Needs You" panel with two blocked
sessions, then every Claude Code session on the machine grouped by project and
labelled needs you, working, shell, idle, gone or unknown. On the right, the
selected session: a red band saying it has been waiting on you for 51 minutes
at a permission prompt, that only your keystroke can answer it, and, quoted
underneath, the question the CLI actually
asked.](docs/images/dashboard-sessions.png)

*Sessions, with a blocked one open beside the list. The reason a session is
stuck is the CLI's own words, not a guess. Fictional sample data.*

## Requirements

- **macOS, or Windows.** macOS is the platform JARVIS was built on and the
  one CI gates on; there its system integration goes through AppleScript.
  Windows is supported by a second implementation behind the same interface
  (`jarvis_platform/`) and runs the same test suite green on a real machine.
  It withdraws exactly one tool — `answer_dialog`, which sends a keystroke
  into a specific terminal window, and which Windows cannot aim safely.
  **Two caveats there, both real:** JARVIS has no default voice on Windows
  (`say` is macOS's, so you need `piper` or a Fish Audio key — see below),
  and letting him see your screen is off until you switch it on, because
  Windows has no permission prompt of its own to stand in the way. There is
  no Linux path.
- **Google Chrome.** Not a preference — a constraint. The microphone uses the
  Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`, see
  `frontend/src/voice.ts`), which Firefox has never implemented. There is no
  server-side transcription to fall back on.
- **Claude Code, installed and logged in.** `npm install -g
  @anthropic-ai/claude-code` (2.1.224 or newer), then run `claude` once and
  log in. This is what JARVIS runs on.
- **Python 3.11+** and **Node.js 18+**.
- **Nothing else on macOS.** The voice is local (`say`). `piper` is an
  optional install for a better local voice; a Fish Audio key is optional
  too, and only read with `JARVIS_TTS_BACKEND=fish`.
- **On Windows, a voice is the one thing you must choose.** `say` is macOS's
  own binary and there is no equivalent to fall back to, so install `piper`
  (`pip install -r requirements-piper.txt`, then download a voice) or set a
  Fish Audio key. Everything else is the same.

## Setup

```bash
git clone <your fork of this repo> jarvis
cd jarvis

cp .env.example .env

pip install -r requirements.txt
python -m playwright install chromium    # for read_page / look_at_page

cd frontend && npm install && cd ..
```

**Fill in the `.env`.** `.env.example` documents the lot, and nothing in it is
required — a copied `.env.example` already works:

```env
# JARVIS_TTS_BACKEND=say    # optional: say (default), piper, or fish
# JARVIS_TTS_VOICE=Daniel   # optional: any voice `say -v '?'` lists
# JARVIS_BRAIN_MODEL=sonnet # optional: the brain's model
# USER_NAME=Tony            # optional: what he calls you
```

Every one of those is also in **Settings → Voice** in the browser, where a
change takes effect on his next sentence rather than his next restart.

**Generate the certificates.** These are not optional:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
```

`server.py` serves HTTPS whenever `cert.pem` and `key.pem` sit beside it, and
`frontend/vite.config.ts` proxies `/api` and `/ws` to
`https://localhost:8340`. Without the pair the backend serves plain HTTP, the
dev server's proxy cannot reach it, and every API and WebSocket call through
the front end fails with a 500 while the page itself still loads — a confusing
way to spend an evening. Generate them once and forget about them.

Then, in two terminals:

```bash
python server.py --host 127.0.0.1        # terminal 1
cd frontend && npm run dev               # terminal 2
```

Open **Chrome** at `http://localhost:5173`, click the page once to allow
audio, and speak. The dashboard is at `http://localhost:5173/dashboard.html`.

> **Who can reach it.** `--host` defaults to `127.0.0.1` — this machine only.
> Everything JARVIS serves acts with your full authority: `POST /api/runs`
> spawns `claude --dangerously-skip-permissions`, `/api/sessions` reads every
> Claude Code conversation you have. A strict `Origin` check stands in front
> of every WebSocket and every state-changing route, so a web page you happen
> to be visiting cannot open `/ws/voice` and speak as you. There is nothing
> to configure: the page is same-origin, so the browser sends an `Origin` no
> page can forge.
>
> An `Origin` is only unforgeable when a *browser* sets it, though. Anything
> speaking raw HTTP can claim to be the dashboard, so a client with no
> `Origin` — a script, the brain's own MCP child — must instead present the
> token in `<data-dir>/jarvis/tool-token`, and on the network the loopback
> bind is the rest of the answer. `--host 0.0.0.0` still works if you mean
> it; set `JARVIS_ALLOWED_ORIGINS` to the address you will actually open the
> page at, or the browser will be turned away too.
>
> This is one of five deliberate trust decisions in this design — see
> [What this trusts, and why](#what-this-trusts-and-why) before you run it.

One more thing about Chrome: the microphone permission is scoped to the
**origin including the port**. If you restart Vite and it lands on 5174, the
grant does not follow, and Chrome will not re-prompt — it just stays denied,
silently. If the mic stops working after everything else looks right, check
that the port has not moved.

## Connections: bring your own

JARVIS ships connected to nothing, because the useful thing is not our guess at
what you use — it is the door. Any MCP server works. Put its `mcpServers` block
into `data/jarvis/connections.json`:

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@notionhq/notion-mcp-server"],
      "env": { "NOTION_TOKEN": "secret_..." }
    }
  }
}
```

That is the block from the server's own README, unchanged. Restart JARVIS and
ask him **"what are you connected to?"** — he answers from what actually
started, names anything that would not start and why, and tells you what it
costs him. (About 250 tokens of his context per tool, on every turn — a
measured figure, and counted against the tools that actually loaded rather than
the ones you meant to load. It is not charged against how much of your
conversation he remembers.)

Your other MCP servers are deliberately ignored. JARVIS runs with
`--strict-mcp-config`, so nothing in `~/.claude.json` or Claude Desktop reaches
him unless you put it in that file yourself — connecting something to JARVIS
should be a thing you did, not a thing you inherited.

Whatever a connected server returns is treated the way a web page is: reported,
never obeyed. You vouched for the server's code when you installed it; you did
not write the ticket, the email, or the shared page it hands back.

See `skills/jarvis-setup/SKILL.md` for the longer walkthrough — including what
to do when a server does not start.

Earlier versions of JARVIS came wired to Apple Calendar, Mail and Notes
instead. Those are gone: they cost three AppleScript permission prompts on
first launch, before you had heard him say anything, for an assistant whose job
is building software. A door you choose is worth more than three we picked.

## How it works

```
Microphone → Chrome Web Speech API → WebSocket → FastAPI (server.py)
                                                      │
                                                      ▼
                                        the brain (brain.py) — ONE long-lived
                                        `claude -p` process on your subscription
                                                      │
                    ┌─────────────────────────────────┼──────────────────────────────┐
                    ▼                                 ▼                              ▼
        speech.py → tts.py → speaker         MCP tools (jarvis_mcp.py       session_watch.py
                                             → POST /internal/tool)     (every Claude Code
                                                      │                  session on the machine)
                                                      ▼
                                        RunExecutor → run store (SQLite)
                                                      │
                                                      ▼
                                        /api/runs + /ws/runs → /dashboard
```

The brain is **one persistent process**, not a request per turn. Your
transcript is written to its stdin; its reply streams back and is split into
sentences as it arrives, so the first words are already being spoken while the
rest is still being written. It reaches JARVIS's own capabilities as MCP tools
over a stdio channel that forwards to `POST /internal/tool`. The exact set
is the allowlist at the top of `brain.py`, which is the list to read rather
than a number to quote here.

What it does *not* get is a way to act on something it has just read off the
web. A turn that used `WebSearch`, `WebFetch`, `read_page` or `look_at_page`
is refused every acting tool for the rest of that turn, and no acting tool
runs at all unless the turn began with you speaking. Text that arrives from a
web page, a screenshot or another session's transcript is untrusted, and is
treated that way.

Anything spawned as real work goes through one recorded pipeline, and two
invariants hold throughout it:

1. **A run always reaches a terminal state** — `succeeded`, `failed`,
   `timed_out` or `cancelled`. Never stuck in `running`.
2. **Every state transition is a database write before it is a
   notification.** The WebSocket is a cache-invalidation hint, never a source
   of truth; the dashboard reconciles against `/api/runs`.

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + Python (`server.py`) |
| Frontend | Vite + TypeScript + Three.js (voice UI), vanilla TS (dashboard) |
| Communication | WebSocket — JSON messages, base64 audio (WAV locally, MP3 from Fish) |
| Brain | One long-lived `claude -p` process, Sonnet by default, on your subscription |
| Voice | macOS `say` by default, piper or Fish Audio on request (and required on Windows); one call per sentence |
| System | One interface, two implementations (`jarvis_platform/`) — AppleScript on macOS, ctypes and PowerShell on Windows |
| Storage | SQLite for runs and usage; plain Markdown for memory |

### Key files

| File | Purpose |
|------|---------|
| `server.py` | The server: WebSocket handler, HTTP API, every `/internal/tool` handler |
| `brain.py` | The voice brain — spawning, turns, restarts, context rotation |
| `claude_env.py` | The environment every spawned child gets, including the `ANTHROPIC_*` scrub |
| `jarvis_mcp.py` | Stdio MCP server exposing JARVIS's tools to the brain |
| `speech.py` | Sentence splitting, echo rejection, barge-in, and the queue of everything JARVIS says |
| `tts.py` | Synthesis, all three backends — the whole voice, in one small file |
| `builds.py` | Spec, brief and plan: the pipeline behind a real multi-hour build |
| `specs.py` | The review surface — reading a design back by numbered section, and approving it |
| `run_store.py` | SQLite `runs` / `run_events`, and the six-value status enum |
| `run_executor.py` | Spawns runs and drives each to a terminal state |
| `stream_parser.py` | Pure parsing of Claude Code's stream-json output (no I/O) |
| `session_watch.py` | Watches every Claude Code session on the machine |
| `session_steer.py` | Posts a message into a running session's inbox socket |
| `dialog.py` | Presses one key in the Terminal tab that owns a session |
| `jarvis_memory.py` | Memory as a folder of Markdown files, not a database |
| `jarvis_home/CLAUDE.md` | JARVIS's persona and rules — the file to edit to change who he is |
| `preflight.py` | First-run checks: CLI version, login, Accessibility, the voice |
| `data_paths.py` | The single source of truth for where JARVIS writes |
| `frontend/src/voice.ts` | Web Speech API, audio playback |
| `frontend/src/orb.ts` | The Three.js particle orb |
| `frontend/src/dashboard/` | The dashboard (vanilla TS, no framework) |

## What this trusts, and why

Five things below are true on purpose. Each is a trade-off JARVIS's design
made deliberately, not a bug waiting for a fix — know them before you run it.

**A directory name can put words in JARVIS's mouth.** To say *which* session
he means — "hammer in Desktop," not "one of them" — JARVIS composes a voice
name out of directory names and a conversation's title, and accepts up to
~60 characters of letters, digits, spaces and light punctuation (`,.-/+'`)
in it (`_said_name` in `server.py`; `_plain_phrase` accepts the same class
for a session's `waitingFor`). Anyone who can create a directory on this
machine, or write a Claude Code roster file's `waitingFor` field, can put
those words into a sentence JARVIS speaks aloud. There is no separator, no
`<`, `>`, `"` or `=` in the allowed set, so it cannot forge a whole line or
close a wrapper — only words, never a fake instruction with structure. The
alternative is an assistant that cannot name what it is looking at.

**The taint gate stops action, not persuasion.** Anything JARVIS reads from
a web page, a file, another session, a run, the screen or a connected MCP
server is marked untrusted, and every acting tool is refused for the rest
of that turn — for the two tools that write memory, the rest of that
generation (`_untrusted_content_refusal`, `TAINTING_TOOLS`, `MEMORY_WRITERS`
in `server.py`). This does **not** mean text an attacker suggested is never
acted on — nothing tracks where a sentence in the brain's context came from,
so a suggestion planted by something JARVIS read is still sitting there when
you next speak. It means you have to ask again, in your own words, on a turn
that opened clean. That is the strongest guarantee this design can actually
keep, not a claim that the suggestion is gone.

**Every spawned run has full privileges.** `JARVIS_SKIP_PERMISSIONS`
defaults on, so every run passes `--dangerously-skip-permissions`
(`run_executor.py`) — there is no TTY to answer a permission prompt, so
without it a run hangs forever instead of doing its job. A run is therefore
a full-privilege process on your machine, in whatever directory you pointed
it at, scrubbed only of `ANTHROPIC_*`/`CLAUDE_CODE_*` environment variables
(`claude_env.py`), not of filesystem access. It can write anything you could
write by hand, including the Claude Code session roster and transcripts that
JARVIS himself later reads back as another session's words.

**Loopback is trusted.** Anything on this machine that can present an
`Origin` of `http://localhost:5173` through `5180`, or the API port itself
(`DEV_SERVER_PORTS`, `JARVIS_DEFAULT_HOST` in `web_auth.py`), gets the same
access a browser tab gets — including starting a run — because that is the
same window Vite's own dev-server restarts land in. Any other process on
your machine bound to one of those ports gets it too. `--host 0.0.0.0`
widens the same trust to your LAN and prints a warning when you do it. The
other door is the bearer token in `<data-dir>/jarvis/tool-token`, written
`0600` (`data_paths.ensure_tool_token`) so only your user account can read
it.

**A dropped audio ack degrades hearing for up to 45 seconds.** The browser
acks each chunk of speech as it finishes playing; JARVIS uses that to relax
echo rejection for the one- and two-word replies people actually interrupt
with ("now," "yes"). If the tab crashes or the socket drops, no more acks
arrive, that relaxation stays off, and a short reply matching JARVIS's last
sentence can be discarded as an echo of himself — until the `ack_timeout`
watchdog (45 seconds, `speech.py`) gives up on the chunk and settles it.
Reload the tab if a short answer seems to be getting ignored.

## Make it yours

This is a starting point, not an appliance. The whole idea is that you clone
it and bend it to what you do. The seams are deliberately obvious:

- **His personality** lives in `jarvis_home/CLAUDE.md` — how he speaks, what
  he refuses to say, what he does when he is unsure. It is copied into his
  data directory on first run; edit that copy and it is never overwritten, or
  edit the template and it ships to a fresh install. If you want something
  other than a British butler, this is the only file you need to touch.
- **His voice** is `tts.py`: one HTTP call, sixty-odd lines. Swap in a
  different provider, or a local model, without touching anything else.
- **His tools** are the `TOOL_HANDLERS` table in `server.py`, exposed to the
  brain through `jarvis_mcp.py` and gated by an allowlist in `brain.py`.
  Adding one means writing a handler and naming it in those two places.
- **The look** is `frontend/src/dashboard/theme/` — four CSS files, tokens
  first. `frontend/dashboard-preview.html` renders every component on one page
  against the real stylesheet, so you can redesign without running the
  backend. The screenshots above are renders of that page.
- **The orb** is `frontend/src/orb.ts`, self-contained Three.js.

Contributions are welcome, and the most useful ones are the ones this cannot
do yet: a Linux implementation of `jarvis_platform` (macOS and Windows are
both there, and the interface is the whole point), alternative TTS engines,
and a mobile client. Please open an issue before a large PR.

## Development

```bash
pytest
```

That runs the whole suite — around 1,640 tests — and touches neither the
network nor your screen. Nine browser tests are deselected by default because
they visit real URLs and `browser.py` launches Chromium with `headless=False`
on purpose, so they open windows on your desk. Run those deliberately:

```bash
pytest -m browser
```

No test spawns a real `claude` process, and none should. `tests/conftest.py`
sets `JARVIS_BRAIN_AUTOSTART=0` for the whole suite.

Everything JARVIS writes — the SQLite database, the memory folder, usage
tracking, the internal tool token — lives under `data/`, which is ignored in
full. Point `JARVIS_DATA_DIR` somewhere else to run an instance without
touching your real data:

```bash
JARVIS_DATA_DIR=/tmp/jarvis-scratch python server.py --host 127.0.0.1
```

## License

Free for personal, non-commercial use. Commercial use requires a license —
visit [ethanplus.ai](https://ethanplus.ai) for inquiries. See
[LICENSE](LICENSE) for details.

## Credits

Built by [Ethan](https://ethanplus.ai). Runs on
[Claude Code](https://claude.com/claude-code), and on
[Fish Audio](https://fish.audio) when you ask it to.

Inspired by the AI that started it all — Tony Stark's JARVIS.

> **Disclaimer:** This is an independent fan project and is not affiliated
> with, endorsed by, or connected to Marvel Entertainment, The Walt Disney
> Company, or any related entities. The JARVIS name and character are property
> of Marvel Entertainment.
