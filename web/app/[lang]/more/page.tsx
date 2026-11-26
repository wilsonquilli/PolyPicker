import Link from "next/link";
import { ThemeToggle } from "@/components/HeaderClient";
import { InstallButton } from "@/components/pwa";
import { SPORTS } from "@/lib/sports";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("More") };
}

const LINKS = [
  { href: "/bets", label: "My bets", sub: "Your tracked bets, record and profit" },
  { href: "/nba/teams", label: "NBA teams", sub: "All 30 teams, records and schedules" },
  { href: "/nba/value", label: "NBA value finder", sub: "Bets where our numbers beat the price" },
  { href: "/nba/accuracy", label: "NBA accuracy", sub: "How well the forecasts have done" },
  { href: "/methodology", label: "How it works", sub: "Reading forecasts, inputs, disclaimer" },
  { href: "/nfl/teams", label: "NFL teams", sub: "All 32 teams, records and schedules" },
  { href: "/nfl/accuracy", label: "NFL accuracy", sub: "How well the NFL forecasts have done" },
  { href: "/soccer", label: "Soccer competitions", sub: "European leagues and cups, South America and Ecuador" },
  { href: "/install", label: "Install the app", sub: "Add PolyPicker to your home screen" },
];

export default async function MorePage() {
  const t = await getT();
  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4">
      <h1 className="text-2xl font-bold tracking-tight">{t("More")}</h1>
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {SPORTS.map((s) => (
          <li key={s.prefix}>
            <Link href={s.href} className="flex items-center gap-2 rounded-2xl border border-line bg-surface px-4 py-3 font-medium hover:bg-surface-2">
              <span aria-hidden>{s.icon}</span>{t(s.label)}
              {!s.ready && <span className="ml-auto rounded bg-accent-soft px-1.5 py-px text-[10px] font-semibold text-accent">{t("Soon")}</span>}
            </Link>
          </li>
        ))}
      </ul>
      <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
        {LINKS.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="flex items-center justify-between gap-3 px-4 py-3.5 hover:bg-surface-2">
              <span>
                <span className="block font-medium">{t(l.label)}</span>
                <span className="block text-xs text-muted">{t(l.sub)}</span>
              </span>
              <span className="text-faint" aria-hidden>›</span>
            </Link>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between rounded-2xl border border-line bg-surface px-4 py-3">
        <span className="font-medium">{t("Appearance")}</span>
        <ThemeToggle />
      </div>
      <InstallButton className="w-full" />
      <p className="text-xs text-muted">{t("PolyPicker is informational only and does not offer wagering.")}</p>
    </div>
  );
}