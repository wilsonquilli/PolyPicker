/** Sports, Polymarket-style. Each sport owns a URL prefix (/nba, /nfl) and its own sub-navigation. */
export type Sport = { prefix: string; href: string; label: string; league: string; icon: string; ready: boolean };

export const SPORTS: Sport[] = [
  { prefix: "/nba", href: "/nba", label: "Basketball", league: "NBA", icon: "🏀", ready: true },
  { prefix: "/nfl", href: "/nfl", label: "Football", league: "NFL", icon: "🏈", ready: true },
  { prefix: "/soccer", href: "/soccer", label: "Soccer", league: "10 competitions", icon: "⚽", ready: true },
];
