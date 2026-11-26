import { MyBets } from "@/components/MyBets";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("My bets"), description: t("Track the bets you placed and see your record, profit and model edge.") };
}

export default function BetsPage() {
  return <MyBets />;
}