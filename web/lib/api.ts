import "server-only";

import type { Envelope } from "./types";

const API_URL = process.env.API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Server-side GET against the read-only PolyPicker API. Returns null on 404. */
export async function api<T>(path: string, params?: Record<string, string | number | undefined | null>):
  Promise<Envelope<T> | null> {
  const url = new URL(API_URL.replace(/\/$/, "") + path);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.append(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, { headers: { accept: "application/json" } });
  } catch {
    throw new ApiError(503, "The PolyPicker data service is unreachable. Showing nothing rather than stale guesses.");
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      msg = (await res.json()).error?.message ?? msg;
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json();
}
