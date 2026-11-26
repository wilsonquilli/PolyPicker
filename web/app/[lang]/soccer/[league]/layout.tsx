import { LEAGUE_SLUGS } from "@/lib/soccer";

export function generateStaticParams() {
  return LEAGUE_SLUGS.map((league) => ({ league }));
}

export default function LeagueLayout({ children }: LayoutProps<"/[lang]/soccer/[league]">) {
  return children;
}
