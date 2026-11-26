"use client";

// "My bets": bets the user says they placed, kept in this browser (the API is read-only by design, so there are no
// accounts). Each bet is graded through the same endpoint as the combo grader, and once settled its result is stored
// here so it never needs re-fetching. Export / import (JSON) moves the history between devices.

import { useSyncExternalStore } from "react";

const STORE = "pp-bets";
const listeners = new Set<() => void>();
let cache: Bet[] | null = null;

export type BetStatus = "open" | "live" | "won" | "lost" | "void";

export interface BetLeg {
  key: string; // sport-gameid-pick
  label: string; // "Lions to win" (snapshot at placement)
  matchup: string; // "CHI @ DET"
  p_model: number;
  result?: "won" | "lost" | "void" | null;
}

export interface Bet {
  id: string;
  created: string; // ISO
  legs: BetLeg[];
  stake: number;
  odds: number; // American odds the user got
  book?: string;
  chance: number; // our chance that every leg wins, when placed
  market: number | null; // the market's chance, when placed
  status: BetStatus;
  settled_at?: string;
  payout?: number | null; // user's actual return (overrides the computed one, e.g. when a void leg reduced the odds)
}

export const decimalOdds = (a: number) => 1 + (a > 0 ? a / 100 : 100 / -a);
export const americanFromP = (p: number) => (p >= 0.5 ? Math.round((-100 * p) / (1 - p)) : Math.round((100 * (1 - p)) / p));

/** Money back (stake included) for a settled bet; null while open. */
export function returned(b: Bet): number | null {
  if (b.status === "open" || b.status === "live") return null;
  if (b.payout != null) return b.payout;
  if (b.status === "lost") return 0;
  if (b.status === "void") return b.stake;
  return b.stake * decimalOdds(b.odds);
}

export const profit = (b: Bet) => {
  const r = returned(b);
  return r === null ? null : r - b.stake;
};

/** Our expected return per $1 when the bet was placed (chance x decimal odds - 1). */
export const edgeAtPlacement = (b: Bet) => b.chance * decimalOdds(b.odds) - 1;

function read(): Bet[] {
  if (cache === null) {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE) ?? "[]");
      cache = Array.isArray(raw) ? (raw as Bet[]).filter(valid) : [];
    } catch {
      cache = [];
    }
  }
  return cache;
}

function valid(b: unknown): b is Bet {
  const x = b as Bet;
  return !!x && typeof x.id === "string" && Array.isArray(x.legs) && x.legs.length > 0 && typeof x.stake === "number"
    && x.stake > 0 && typeof x.odds === "number" && Math.abs(x.odds) >= 100;
}

function write(bets: Bet[]) {
  cache = bets;
  try {
    localStorage.setItem(STORE, JSON.stringify(bets));
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

const EMPTY: Bet[] = [];

/** All bets, newest first ([] on the server so the first render matches). */
export function useBets(): Bet[] {
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

export const bets = {
  add(b: Omit<Bet, "id" | "created" | "status">): Bet {
    const bet: Bet = { ...b, id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`, created: new Date().toISOString(), status: "open" };
    write([bet, ...read()]);
    return bet;
  },
  update(id: string, patch: Partial<Bet>) {
    write(read().map((b) => (b.id === id ? { ...b, ...patch } : b)));
  },
  remove(id: string) {
    write(read().filter((b) => b.id !== id));
  },
  export(): string {
    return JSON.stringify({ app: "polypicker", version: 1, bets: read() }, null, 2);
  },
  /** Merge an export into this device's bets (same id = kept once). Returns how many were added. */
  import(json: string): number {
    const data = JSON.parse(json);
    const incoming = (Array.isArray(data) ? data : data?.bets ?? []).filter(valid) as Bet[];
    const have = new Set(read().map((b) => b.id));
    const fresh = incoming.filter((b) => !have.has(b.id));
    write([...read(), ...fresh].sort((a, b) => b.created.localeCompare(a.created)));
    return fresh.length;
  },
};
