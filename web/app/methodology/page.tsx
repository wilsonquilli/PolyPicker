import Link from "next/link";

import { Card } from "@/components/ui";

export const metadata = { title: "How it works" };

export default function Methodology() {
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">How PolyPicker works</h1>
        <p className="mt-2 text-muted">
          PolyPicker looks like a prediction market, but nothing is traded. Every percentage comes from a statistical model
          that is checked against what actually happened.
        </p>
      </div>

      <Card className="p-6">
        <h2 className="mb-2 text-lg font-semibold">Reading a forecast</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-muted">
          <li><b className="text-ink">Win probability</b>: how often the model expects a team to win games like this one. A 65% favorite should lose about 1 in 3 times.</li>
          <li><b className="text-ink">Projected score</b>: the model’s expected points. It’s an average, not a prediction of the exact score.</li>
          <li><b className="text-ink">Spread &amp; total lines</b>: probabilities from the model’s distribution of possible margins and totals. They’re only shown when that distribution passed calibration checks on past games.</li>
          <li><b className="text-ink">Player thresholds</b>: “20+” means at least 20; “over 20.5” means 21 or more; “over 20” pushes at exactly 20.</li>
        </ul>
      </Card>

      <Card className="p-6">
        <h2 className="mb-2 text-lg font-semibold">What goes into the models</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-muted">
          <li>Team offensive and defensive efficiency (season-to-date and last 10 games), pace, an Elo rating, home court, rest and back-to-backs.</li>
          <li>Player minutes trends, per-minute production, opponent defense and expected pace.</li>
          <li>Only information available before each game. Same-day and later results are never used.</li>
          <li>Player availability is inferred from recent box scores (played / did not play / inactive). PolyPicker doesn’t yet have an injury-report feed, so late scratches can make a forecast wrong.</li>
        </ul>
      </Card>

      <Card className="p-6">
        <h2 className="mb-2 text-lg font-semibold">Honesty rules</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-muted">
          <li>Published forecasts are never edited. Updates create a new revision, and old ones stay in the archive.</li>
          <li>Every forecast shows when it was published, its data cutoff and model version.</li>
          <li>Accuracy is reported openly, including against simple baselines, on the <Link href="/accuracy" className="text-accent underline">Accuracy</Link> page.</li>
          <li>Backtests (forecasts regenerated after the fact) are always labeled and never presented as live picks.</li>
        </ul>
      </Card>

      <Card className="border-warn/40 bg-warn-soft p-6">
        <h2 className="mb-2 text-lg font-semibold">Disclaimer</h2>
        <p className="text-sm">
          PolyPicker is an independent informational service. Forecasts are estimates, not guaranteed outcomes or financial
          advice. PolyPicker does not accept or place wagers, hold funds or facilitate gambling. Data is derived from NBA.com via
          a public research dataset; PolyPicker isn’t affiliated with the NBA or its teams.
        </p>
      </Card>
    </article>
  );
}
