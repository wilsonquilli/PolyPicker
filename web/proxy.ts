import { NextResponse, type NextRequest } from "next/server";

/**
 * Language without changing URLs: every page lives under app/[lang]; the visible URL stays /nba, /soccer/epl...
 *
 * - `pp-lang` cookie (set by the EN/ES switch in the header) picks the language; first visit falls back to the
 *   browser's Accept-Language (Spanish speakers get Spanish), then English.
 * - `/es/...` or `/en/...` links (shareable) set the cookie and redirect to the plain URL.
 */
type Lang = "en" | "es";
export const LANG_COOKIE = "pp-lang";
const YEAR = 60 * 60 * 24 * 365;

function fromHeader(header: string | null): Lang {
  // "es-EC,es;q=0.9,en;q=0.8" -> first listed language that we support
  for (const part of (header ?? "").split(",")) {
    const code = part.trim().slice(0, 2).toLowerCase();
    if (code === "es" || code === "en") return code;
  }
  return "en";
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const prefix = pathname.match(/^\/(en|es)(?=\/|$)/);
  if (prefix) {
    const res = NextResponse.redirect(new URL((pathname.slice(3) || "/") + search, request.url));
    res.cookies.set(LANG_COOKIE, prefix[1], { path: "/", maxAge: YEAR, sameSite: "lax" });
    return res;
  }
  const saved = request.cookies.get(LANG_COOKIE)?.value;
  const lang: Lang = saved === "es" || saved === "en" ? saved : fromHeader(request.headers.get("accept-language"));
  const res = NextResponse.rewrite(new URL(`/${lang}${pathname === "/" ? "" : pathname}${search}`, request.url));
  res.headers.set("Vary", "Cookie, Accept-Language"); // same URL, two languages: shared caches must key on these
  return res;
}

export const config = {
  // Pages only: not Next internals, the API proxy, or files (icons, sw.js, manifest.webmanifest...).
  matcher: ["/((?!_next/|api/|.*\\.[a-zA-Z0-9]+$).*)"],
};
