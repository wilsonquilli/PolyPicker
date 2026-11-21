"use client";

import { useEffect, useState } from "react";

/** "updated 2h ago". Server renders the absolute ET time; the client swaps in a relative one. */
export function RelativeTime({ iso, prefix = "" }: { iso: string; prefix?: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    const update = () => setText(relative(iso));
    update();
    const id = setInterval(update, 60_000);
    return () => clearInterval(id);
  }, [iso]);
  const abs = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    timeZone: "America/New_York" }).format(new Date(iso)) + " ET";
  return (
    <time dateTime={iso} title={abs} suppressHydrationWarning>
      {prefix}{text ?? abs}
    </time>
  );
}

function relative(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) {
    const m = -s / 60;
    if (m < 60) return `in ${Math.round(m)}m`;
    if (m < 60 * 24) return `in ${Math.round(m / 60)}h`;
    return `in ${Math.round(m / 1440)}d`;
  }
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
