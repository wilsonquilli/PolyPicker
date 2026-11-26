"use client";

// The combo grader's slip: legs the user picked, as `sport-gameid-pick` keys (the API's format), kept in localStorage
// so it survives reloads and is shared by every PickButton on the page. One leg per game: picking another outcome of
// the same game replaces the earlier one.

import { useSyncExternalStore } from "react";

export { legKey, type Sport } from "./home";

const STORE = "pp-slip";
const SETTLED = "pp-slip-settled";   // the slip last shown with every game finished
export const MAX_LEGS = 12;
const listeners = new Set<() => void>();
let cache: string | null = null;

const gameOf = (key: string) => key.split("-").slice(0, 2).join("-");
const VALID = /^(nba|nfl)-\d+-(home|away)$|^soccer-\d+-(home|draw|away)$/;

function read(): string {
  if (cache === null) {
    try {
      cache = localStorage.getItem(STORE) ?? "";
    } catch {
      cache = "";
    }
  }
  return cache;
}

function write(keys: string[]) {
  cache = keys.join(",");
  try {
    localStorage.setItem(STORE, cache);
  } catch {}
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORE) {
      cache = null;
      l();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    window.removeEventListener("storage", onStorage);
  };
}

const split = (s: string) => (s ? s.split(",") : []);

/** Current slip keys ("" on the server, so the first render matches). */
export function useSlip(): string[] {
  const raw = useSyncExternalStore(subscribe, read, () => "");
  return split(raw);
}

export const slip = {
  toggle(key: string): "added" | "removed" | "full" {
    const keys = split(read());
    if (keys.includes(key)) {
      write(keys.filter((k) => k !== key));
      return "removed";
    }
    const others = keys.filter((k) => gameOf(k) !== gameOf(key));
    if (others.length >= MAX_LEGS) return "full";
    write([...others, key]);
    return "added";
  },
  remove(key: string) {
    write(split(read()).filter((k) => k !== key));
  },
  replace(oldKey: string, newKey: string) {
    write(split(read()).map((k) => (k === oldKey ? newKey : k)));
  },
  set(keys: string[]) {
    const seen = new Set<string>();
    write(keys.filter((k) => VALID.test(k) && !seen.has(gameOf(k)) && seen.add(gameOf(k))).slice(0, MAX_LEGS));
  },
  clear() {
    write([]);
    try {
      localStorage.removeItem(SETTLED);
    } catch {}
  },
  /** Remember that this exact slip was shown with every game over: the next visit starts with an empty slip. */
  markSettled(keys: string[]) {
    try {
      localStorage.setItem(SETTLED, keys.join(","));
    } catch {}
  },
  /** On page load: clear the slip if it's the one already shown as finished. True if it was cleared. */
  clearIfSettled(): boolean {
    try {
      const done = localStorage.getItem(SETTLED);
      if (done === null) return false;
      localStorage.removeItem(SETTLED);
      if (done && done === read()) {
        write([]);
        return true;
      }
    } catch {}
    return false;
  },
};
