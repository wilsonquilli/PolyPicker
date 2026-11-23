import Image from "next/image";

import { Card } from "@/components/ui";
import { InstallButton } from "@/components/pwa";
import { rich } from "@/lib/i18n/rich";
import { getT } from "@/lib/i18n/server";

export async function generateMetadata() {
  const t = await getT();
  return { title: t("Install the app"), description: t("Add PolyPicker to your home screen on iPhone, iPad, Android or desktop. Free, no app store.") };
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent">{n}</span>
      <span className="text-sm">{children}</span>
    </li>
  );
}

const Share = ({ label }: { label: string }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="-mt-0.5 inline" aria-label={label}>
    <path d="M12 3v12M7 8l5-5 5 5" /><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />
  </svg>
);

export default async function InstallPage() {
  const t = await getT();
  const icons = { share: <Share label={t("Share icon")} />, dots: <b aria-label={t("menu")}>⋮</b> };
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-wrap items-center gap-4">
        <Image src="/icons/icon-192.png" alt="" width={64} height={64} unoptimized className="rounded-2xl border border-line shadow-card" />
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{t("Get the PolyPicker app")}</h1>
          <p className="text-muted">{t("Free. Installs straight from your browser in a few seconds. No App Store or Google Play needed.")}</p>
        </div>
        <InstallButton />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">{t("iPhone & iPad")}</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>{rich(t("Open PolyPicker in <b>Safari</b> (on iOS 16.4+, Chrome and Edge work too)."))}</Step>
            <Step n={2}>{rich(t("Tap the <b>Share</b> button {share} (bottom of the screen on iPhone, top on iPad)."), icons)}</Step>
            <Step n={3}>{rich(t("Scroll down and tap <b>Add to Home Screen</b>."))}</Step>
            <Step n={4}>{rich(t("Tap <b>Add</b>. PolyPicker appears on your home screen and opens full-screen."))}</Step>
          </ol>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">Android</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>{rich(t("Open the site in <b>Chrome</b>, <b>Edge</b> or <b>Samsung Internet</b>."))}</Step>
            <Step n={2}>{rich(t("Tap <b>Install</b> when the banner appears, or the button at the top of this page."))}</Step>
            <Step n={3}>{rich(t("No banner? Open the browser menu {dots} and tap <b>Install app</b> (or <b>Add to Home screen</b>)."), icons)}</Step>
            <Step n={4}>{t("Confirm. The app shows up in your app drawer and home screen.")}</Step>
          </ol>
        </Card>
        <Card className="p-5 md:col-span-2">
          <h2 className="mb-3 font-semibold">{t("Computer (Chrome, Edge)")}</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>{rich(t("Click the <b>install</b> icon at the right end of the address bar, or the button above."))}</Step>
            <Step n={2}>{t("PolyPicker opens in its own window and can be pinned to your dock or taskbar.")}</Step>
          </ol>
        </Card>
      </div>

      <Card className="p-5 text-sm text-muted">
        <h2 className="mb-2 font-semibold text-ink">{t("Good to know")}</h2>
        <ul className="list-disc space-y-1.5 pl-5">
          <li>{t("The app always shows the latest forecasts. Updates install automatically; there’s nothing to update by hand.")}</li>
          <li>{t("Pages you’ve opened stay available offline, clearly marked as possibly out of date.")}</li>
          <li>{t("No account, no tracking for install, and no wagering. PolyPicker is informational only.")}</li>
          <li>{rich(t("To remove it: long-press the icon and choose <b>Remove App</b> (iOS) or <b>Uninstall</b> (Android)."))}</li>
        </ul>
      </Card>
    </div>
  );
}
