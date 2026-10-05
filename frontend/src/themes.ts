/**
 * Colour themes: one colour per state, shared by the stylesheet (as --c-<state>
 * custom properties) and the orb. The choice lives in localStorage, so the
 * main window and the overlay — the same origin — read the same one.
 */

import type { Orb, OrbState } from "./orb";

export const THEMES: Record<string, Record<OrbState, string>> = {
  arc:       { idle: "#3d8bff", listening: "#22e3ff", thinking: "#a07bff", speaking: "#ffb347", compacting: "#3a5f8a" },
  crimson:   { idle: "#c2334a", listening: "#ff5468", thinking: "#ff8a3d", speaking: "#ffd166", compacting: "#5a2a33" },
  emerald:   { idle: "#1fa873", listening: "#3dffa8", thinking: "#22d3ee", speaking: "#eaff7a", compacting: "#2f5a4a" },
  synthwave: { idle: "#7a5cff", listening: "#ff4fd8", thinking: "#22e3ff", speaking: "#ffe14f", compacting: "#4a3a7a" },
  mono:      { idle: "#9aa4b2", listening: "#e6edf5", thinking: "#b8c4d6", speaking: "#ffffff", compacting: "#5b6470" },
};

const KEY = "jarvis-theme";

export function currentTheme(): string {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && saved in THEMES) return saved;
  } catch { /* storage unavailable: the default */ }
  return "arc";
}

export function saveTheme(name: string) {
  if (name in THEMES) localStorage.setItem(KEY, name);
}

export function applyTheme(orb?: Orb) {
  const colours = THEMES[currentTheme()];
  for (const [state, colour] of Object.entries(colours)) {
    document.documentElement.style.setProperty(`--c-${state}`, colour);
  }
  orb?.setPalette(colours);
}
