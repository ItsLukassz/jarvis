// One page, two windows. The overlay window (electron/main.js loads it with
// ?overlay) shows the orb only, and must never open a second microphone or a
// second voice socket -- so it never loads main.ts at all.
if (new URLSearchParams(location.search).has("overlay")) import("./overlay");
else import("./main");
