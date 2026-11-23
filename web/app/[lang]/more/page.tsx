import Link from "next/link";

import { ThemeToggle } from "@/components/HeaderClient";
import { InstallButton } from "@/components/pwa";

export const metadata = { title: "More" };

const LINKS = [
  { href: "/teams", label: "Teams", sub: "All 30 teams, records and schedules" },
  { href: "/accuracy", label: "Accuracy", sub: "How well the forecasts have done" },
  { href: "/methodology", label: "How it works", sub: "Reading forecasts, inputs, disclaimer" },
  { href: "/install", label: "Install the app", sub: "Add PolyPicker to your home screen" },
];

// Phone "More" tab: the destinations that don't fit in the bottom tab bar.
export default function MorePage() {
  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4">
      <h1 className="text-2xl font-bold tracking-tight">More</h1>
      <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
        {LINKS.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="flex items-center justify-between gap-3 px-4 py-3.5 hover:bg-surface-2">
              <span>
                <span className="block font-medium">{l.label}</span>
                <span className="block text-xs text-muted">{l.sub}</span>
              </span>
              <span className="text-faint" aria-hidden>›</span>
            </Link>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between rounded-2xl border border-line bg-surface px-4 py-3">
        <span className="font-medium">Appearance</span>
        <ThemeToggle />
      </div>
      <InstallButton className="w-full" />
      <p className="text-xs text-muted">PolyPicker is informational only and does not offer wagering.</p>
    </div>
  );
}
