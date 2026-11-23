import { LEAGUE_SLUGS } from "@/lib/soccer";

// Every soccer league shares these pages; the slug comes from the URL (/soccer/epl, /soccer/laliga).
export function generateStaticParams() {
  return LEAGUE_SLUGS.map((league) => ({ league }));
}

// Unknown slugs are rejected with notFound() inside each page's Suspense boundary (reading params here,
// outside Suspense, would block instant navigation under Cache Components).
export default function LeagueLayout({ children }: LayoutProps<"/[lang]/soccer/[league]">) {
  return children;
}
