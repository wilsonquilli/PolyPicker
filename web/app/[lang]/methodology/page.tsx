import { Card } from "@/components/ui";
import { rich } from "@/lib/i18n/rich";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  return { title: (await getT())("How it works") };
}

export default async function Methodology() {
  const t = await getT();
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t("How PolyPicker works")}</h1>
        <p className="mt-2 text-muted">
          {t("PolyPicker looks like a prediction market, but nothing is traded. Every percentage comes from a statistical model that is checked against what actually happened.")}
        </p>
      </div>

      <Card className="p-6">
        <h2 className="mb-2 text-lg font-semibold">{t("Reading a forecast")}</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-muted">
          {["<b>Win probability</b>: how often the model expects a team to win games like this one. A 65% favorite should lose about 1 in 3 times.",
            "<b>Projected score</b>: the model’s expected points. It’s an average, not a prediction of the exact score.",
            "<b>Spread & total lines</b>: probabilities from the model’s distribution of possible margins and totals. They’re only shown when that distribution passed calibration checks on past games.",
            "<b>Player thresholds</b>: “20+” means at least 20; “over 20.5” means 21 or more; “over 20” pushes at exactly 20.",
          ].map((line) => <li key={line}>{rich(t(line), { b: (c) => <b className="text-ink">{c}</b> })}</li>)}
        </ul>
      </Card>

      <Card className="p-6">
        <h2 className="mb-2 text-lg font-semibold">{t("What goes into the models")}</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-muted">
          <li>{t("Team offensive and defensive efficiency (season-to-date and last 10 games), pace, an Elo rating, home court, rest and back-to-backs.")}</li>
          <li>{t("Player minutes trends, per-minute production, opponent defense and expected pace.")}</li>
          <li>{t("Only information available before each game. Same-day and later results are never used.")}</li>
          <li>{t("NBA player availability comes from ESPN’s injury report (out, doubtful, questionable, day-to-day). Players not on the report are judged from recent box scores. Forecasts refresh as the report changes, but a late scratch after the last refresh can still make a forecast wrong.")}</li>
          <li>{t("When an NBA game has a betting line, the forecast also uses the market: the sportsbook spread and total (via ESPN) and Polymarket’s price. Tested on past seasons, the market carries most of the predictive information; PolyPicker adds injury and lineup data on top and falls back to its own model when no line is posted yet.")}</li>
          <li>{t("NBA game forecasts rate every player from past games (box-score production and plus-minus, recent games weighted more) and add up the players expected to play tonight, so a missing star lowers his team’s chances.")}</li>
        </ul>
      </Card>
    </article>
  );
}