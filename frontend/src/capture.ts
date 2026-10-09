/**
 * capture.ts — the page hearing an utterance and handing the audio to the
 * server, for the `whisper` STT backend.
 *
 * Only used when `JARVIS_STT_BACKEND` is not `browser`. On the default
 * install nothing here runs and `voice.ts` keeps doing what it always did.
 *
 * WHY THE PAGE STILL DOES THE LISTENING, when the point of phase 4 is to move
 * recognition off the browser:
 *
 * The browser applies acoustic echo cancellation to `getUserMedia`, using its
 * own render stream as the reference. Measured 2026-09-10 — `{audio: true}`
 * grants `echoCancellation: true`, and capturing through it while JARVIS
 * spoke transcribed the user with no trace of him, where a raw microphone tap
 * of the same moment transcribed JARVIS instead. That cancellation is only
 * available here, in the page, where the audio is also being played. Ship the
 * raw microphone to the server and it is thrown away.
 *
 * So: the page hears, the server recognises. Endpointing lives with the audio
 * and the echo cancellation; transcription lives where the model is.
 *
 * The endpointer this replaces is Chrome's, and `speech.py` records its
 * measured behaviour — it closes a segment when his voice tails off and hands
 * the user's FIRST WORD over as a final on its own, 1-2s later. That is the
 * bar, and it is not an easy one: see `SILENCE_MS`, which was set too short
 * on the first attempt and fragmented a sentence into three turns within a
 * minute of a live run.
 */

/**
 * A KNOWN COMPROMISE, stated rather than left to be discovered:
 * `ScriptProcessorNode` is deprecated. `AudioWorklet` is its replacement and
 * runs the audio callback off the main thread, which is the right place for
 * it.
 *
 * ScriptProcessor is used here anyway because AudioWorklet needs its
 * processor in a separate module fetched at runtime, and getting that served
 * correctly through Vite in both dev and build is a build problem rather than
 * an audio one — worth solving, but not worth solving first, and not worth
 * getting wrong in the commit that introduces the whole path.
 *
 * The work in the callback is one pass over 4096 floats to find a peak, which
 * is trivial next to the Three.js orb already running on that thread. If
 * capture ever stutters, this is the first thing to move.
 */
export interface Capture {
  start(): void;
  /** `discard`: drop a half-heard utterance instead of sending it (mute). */
  stop(discard?: boolean): void;
  /** Close the current utterance now -- the server heard "that's it, Jarvis". */
  endNow(): void;
  /** Wake-word mode: record nothing until `wake()`; stream the mic for the
   *  server's wake-word model meanwhile. */
  setWakeGated(on: boolean): void;
  /** The server heard the wake word: start the utterance, from a moment ago. */
  wake(): void;
  /** Conversation mode: for `ms`, whatever is said next counts as said to
   *  him -- no name, no wake word. */
  followUp(ms: number): void;
  readonly running: boolean;
}

// While the user is speaking, the last PEEK_WINDOW_MS go to the server every
// PEEK_EVERY_MS so it can listen for "that's it, Jarvis" and end the
// utterance without waiting for SILENCE_MS of quiet.
const PEEK_EVERY_MS = 1500;
const PEEK_WINDOW_MS = 4000;
// ...but not before the utterance is this long. An ordinary command ends on
// silence well inside it, and every peek is a transcription that competes
// with the real one for the same model: measured, a 3.6s command took 6.7s to
// come back while peeks were in flight, and 0.9s on its own.
const PEEK_AFTER_MS = 5000;

// 16 kHz mono is what every whisper-family model resamples to internally.
// Sending it at that rate removes a conversion, and a conversion is a place
// for a sample-rate mismatch to hide.
const RATE = 16000;

// Voice activity, as a fraction of full scale. The room floor measured on the
// development machine was 3/32767 (~0.0001); ordinary speech peaked around
// 3000 (~0.09). This sits well above the floor and well below the voice.
const SPEECH_LEVEL = 0.02;

// A fixed level is deaf to the room. With a game or a video playing, the
// speakers alone sat at 0.02-0.045 at the microphone: every recording started
// on noise, never saw "silence", and ran to the 30s cap before he answered.
// So the bar follows the ambient level -- an estimate that drops quickly and
// rises slowly, so a sentence does not drag it up -- and speech is whatever
// stands NOISE_FACTOR above it, within [SPEECH_LEVEL, SPEECH_LEVEL_MAX].
// ponytail: a peak-follower, not real voice detection; loud speech-like noise
// (a video's dialogue) still passes. Silero VAD in the page is the upgrade.
const NOISE_FACTOR = 2;
const NOISE_FALL = 0.2;
const NOISE_RISE = 0.005;
const SPEECH_LEVEL_MAX = 0.08;

// How much silence closes an utterance.
//
// Was 700ms, which was wrong and a live run said so within a minute. Natural
// speech has gaps longer than that inside one sentence, and every gap became
// an utterance boundary. Seen 2026-09-10, one sentence arriving as three:
//
//     stt(base.en): Pie charts.
//     stt(base.en): What would you like to do
//     stt(base.en): That I can then put into my table.
//
// Each fragment then reaches the brain as a separate turn, which is worse
// than a late transcript: it answers half a question.
//
// The reasoning behind 700ms was borrowed from the wrong place — speech.py's
// ECHO grace is short so a generous tail does not eat the START of a reply,
// but that is about echo, not endpointing. Chrome's endpointer, which this
// replaces and which `speech.py` measured, closes on a longer gap.
//
// 1400 until 2026-10-04; 1100 buys 0.3s on every single reply and is still
// well clear of the 700 that fragmented. Raise it again if sentences split.
const SILENCE_MS = 1100;

// Below this, it was a cough or a door. Above it, somebody is dictating and
// the model's context window is the limit; both ends get cut.
const MIN_MS = 250;
const MAX_MS = 30000;
// Wake-word mode. The recording starts PREROLL_MS before the wake word was
// confirmed, so "Hey Jarvis" itself is in it; and a woken utterance is a
// command, not a dictation, so it is cut much sooner if the room never goes
// quiet.
const PREROLL_MS = 2500;
const MAX_WOKEN_MS = 15000;

function encodeWav(samples: Float32Array, rate: number): ArrayBuffer {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buf);
  const put = (off: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i));
  };
  put(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  put(8, "WAVE");
  put(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);              // PCM
  view.setUint16(22, 1, true);              // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  put(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return buf;
}

function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  // In chunks: String.fromCharCode(...bytes) on a whole utterance overflows
  // the argument limit and throws, which would lose the sentence.
  const STEP = 0x8000;
  for (let i = 0; i < bytes.length; i += STEP) {
    binary += String.fromCharCode(...bytes.subarray(i, i + STEP));
  }
  return btoa(binary);
}

/**
 * @param send      hands one finished utterance to the server
 * @param onEvent   the same mic-event channel `voice.ts` uses, so a capture
 *                  that goes deaf is visible in the server log next to the
 *                  transcripts rather than only in a console nobody has open
 */
export function createCapture(
  send: (base64Wav: string, woken: boolean) => void,
  onEvent: (what: string) => void = () => {},
  peek: (base64Wav: string) => void = () => {},
  frame: (base64Pcm: string) => void = () => {},
): Capture {
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let node: ScriptProcessorNode | null = null;
  let running = false;

  let chunks: Float32Array[] = [];
  let samples = 0;
  let speaking = false;
  let quietFor = 0;
  let sincePeek = 0;
  let floor = 0;                // the ambient level, see NOISE_FACTOR
  let wakeGated = false;
  let woken = false;            // this utterance was started by the wake word
  let preroll: Float32Array[] = [];
  let followUpUntil = 0;        // performance.now() until which a reply needs no name

  const reset = () => {
    chunks = [];
    samples = 0;
    speaking = false;
    quietFor = 0;
    sincePeek = 0;
    woken = false;
  };

  // The tail of the utterance so far, for the server to check for the stop phrase.
  const sendPeek = () => {
    const want = Math.min(samples, Math.round((PEEK_WINDOW_MS / 1000) * RATE));
    const tail = new Float32Array(want);
    let at = want;
    for (let i = chunks.length - 1; i >= 0 && at > 0; i--) {
      const c = chunks[i];
      const take = Math.min(c.length, at);
      tail.set(c.subarray(c.length - take), at - take);
      at -= take;
    }
    peek(toBase64(encodeWav(tail, RATE)));
  };

  const flush = () => {
    const ms = (samples / RATE) * 1000;
    if (ms < MIN_MS) {
      reset();
      return;
    }
    const all = new Float32Array(samples);
    let at = 0;
    for (const c of chunks) {
      all.set(c, at);
      at += c.length;
    }
    const wasWoken = woken;
    reset();
    onEvent(`captured ${(ms / 1000).toFixed(1)}s`);
    send(toBase64(encodeWav(all, RATE)), wasWoken);
  };

  const onAudio = (e: AudioProcessingEvent) => {
    const input = e.inputBuffer.getChannelData(0);
    let peak = 0;
    for (const s of input) {
      const a = Math.abs(s);
      if (a > peak) peak = a;
    }
    const ms = (input.length / RATE) * 1000;

    if (wakeGated && !speaking && performance.now() >= followUpUntil) {
      // Nothing is recorded until the server says his name was said. Keep the
      // last few seconds, and hand this block to the wake-word model.
      preroll.push(new Float32Array(input));
      while (preroll.length * ms > PREROLL_MS) preroll.shift();
      const pcm = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        pcm[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
      }
      frame(toBase64(pcm.buffer));
      return;
    }

    floor += (peak - floor) * (peak < floor ? NOISE_FALL : NOISE_RISE);
    const bar = Math.min(SPEECH_LEVEL_MAX, Math.max(SPEECH_LEVEL, floor * NOISE_FACTOR));

    if (peak >= bar) {
      if (!speaking) {
        speaking = true;
        // Started inside the follow-up window: it is a reply to him.
        woken = performance.now() < followUpUntil;
        onEvent(woken ? "speech started (follow-up)" : "speech started");
      }
      quietFor = 0;
    } else if (speaking) {
      quietFor += ms;
    }

    if (speaking) {
      chunks.push(new Float32Array(input));
      samples += input.length;
      // A sentence long enough to be a monologue is cut here rather than
      // grown without bound: the model has a context window and the server
      // has a frame limit, and hitting either loses the whole thing instead
      // of the tail.
      if (quietFor >= SILENCE_MS || (samples / RATE) * 1000 >= (woken ? MAX_WOKEN_MS : MAX_MS)) {
        flush();
        return;
      }
      sincePeek += ms;
      if (sincePeek >= PEEK_EVERY_MS && (samples / RATE) * 1000 >= PEEK_AFTER_MS) {
        sincePeek = 0;
        sendPeek();
      }
    }
  };

  return {
    get running() {
      return running;
    },
    async start() {
      if (running) return;
      running = true;
      try {
        // `{audio: true}` and not a hand-written constraint set: it grants
        // echoCancellation, noiseSuppression and autoGainControl, and the
        // first of those is the entire reason capture happens in the page.
        // Asking for them individually risks turning one off by writing the
        // list out.
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        ctx = new AudioContext({ sampleRate: RATE });
        const src = ctx.createMediaStreamSource(stream);
        node = ctx.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = onAudio;
        src.connect(node);
        node.connect(ctx.destination);
        onEvent(`capture started (${stream.getAudioTracks()[0]?.label ?? "?"})`);
      } catch (err) {
        running = false;
        const e = err as Error;
        onEvent(`capture failed: ${e.name}: ${e.message}`);
      }
    },
    setWakeGated(on: boolean) {
      wakeGated = on;
      preroll = [];
    },
    followUp(ms: number) {
      followUpUntil = performance.now() + ms;
    },
    wake() {
      if (!running || speaking) return;
      chunks = preroll;
      preroll = [];
      samples = chunks.reduce((n, c) => n + c.length, 0);
      speaking = true;
      woken = true;
      quietFor = 0;
      sincePeek = 0;
      onEvent("speech started (wake word)");
    },
    endNow() {
      // A reply that arrives after this utterance already ended on silence
      // must not cut the NEXT one short in its first second.
      if (speaking && samples >= RATE) {
        onEvent("ended by stop phrase");
        flush();
      }
    },
    stop(discard = false) {
      running = false;
      // Muting throws away a half-heard sentence; any other stop sends it.
      if (speaking) {
        if (discard) reset();
        else flush();
      }
      node?.disconnect();
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close();
      node = null;
      stream = null;
      ctx = null;
      onEvent("capture stopped");
    },
  };
}
