import { SOCCER_LEAGUES, type LeagueSlug } from "@/lib/soccer";

/**
 * Country flags as images: Windows doesn't draw flag emoji (🇪🇸 shows as the letters "ES"). Competitions without a
 * country flag (South American cups: 🌎) keep their emoji, which renders everywhere.
 */
const FLAG_IMG: Partial<Record<LeagueSlug, string>> = {
  epl: "gb-eng", laliga: "es", bundesliga: "de", seriea: "it", ligue1: "fr", ucl: "eu", uel: "eu", ecuador: "ec",
};

const SIZES = {
  sm: { w: 20, h: 15, cls: "h-[15px] w-5", emoji: "" },
  lg: { w: 32, h: 24, cls: "h-6 w-8", emoji: "text-2xl" },
} as const;

export function LeagueFlag({ slug, size = "sm" }: { slug: LeagueSlug; size?: keyof typeof SIZES }) {
  const s = SIZES[size];
  const code = FLAG_IMG[slug];
  if (!code) return <span aria-hidden className={s.emoji}>{SOCCER_LEAGUES[slug]?.flag}</span>;
  // eslint-disable-next-line @next/next/no-img-element -- 1 KB static PNG, no optimisation needed
  return <img src={`/flags/${code}.png`} alt="" aria-hidden width={s.w} height={s.h} className={`${s.cls} shrink-0 rounded-[3px] object-cover ring-1 ring-line`} />;
}
