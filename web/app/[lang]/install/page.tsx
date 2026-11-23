import Image from "next/image";

import { Card } from "@/components/ui";
import { InstallButton } from "@/components/pwa";

export const metadata = { title: "Install the app", description: "Add PolyPicker to your home screen on iPhone, iPad, Android or desktop. Free, no app store." };

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent">{n}</span>
      <span className="text-sm">{children}</span>
    </li>
  );
}

const Share = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="-mt-0.5 inline" aria-label="Share icon">
    <path d="M12 3v12M7 8l5-5 5 5" /><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />
  </svg>
);
const Dots = () => <b aria-label="menu">⋮</b>;

export default function InstallPage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-wrap items-center gap-4">
        <Image src="/icons/icon-192.png" alt="" width={64} height={64} className="rounded-2xl shadow-card" />
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Get the PolyPicker app</h1>
          <p className="text-muted">Free. Installs straight from your browser in a few seconds. No App Store or Google Play needed.</p>
        </div>
        <InstallButton />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">iPhone &amp; iPad</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>Open PolyPicker in <b>Safari</b> (on iOS 16.4+, Chrome and Edge work too).</Step>
            <Step n={2}>Tap the <b>Share</b> button <Share /> (bottom of the screen on iPhone, top on iPad).</Step>
            <Step n={3}>Scroll down and tap <b>Add to Home Screen</b>.</Step>
            <Step n={4}>Tap <b>Add</b>. PolyPicker appears on your home screen and opens full-screen.</Step>
          </ol>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">Android</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>Open the site in <b>Chrome</b>, <b>Edge</b> or <b>Samsung Internet</b>.</Step>
            <Step n={2}>Tap <b>Install</b> when the banner appears, or the button at the top of this page.</Step>
            <Step n={3}>No banner? Open the browser menu <Dots /> and tap <b>Install app</b> (or <b>Add to Home screen</b>).</Step>
            <Step n={4}>Confirm. The app shows up in your app drawer and home screen.</Step>
          </ol>
        </Card>
        <Card className="p-5 md:col-span-2">
          <h2 className="mb-3 font-semibold">Computer (Chrome, Edge)</h2>
          <ol className="flex flex-col gap-2.5">
            <Step n={1}>Click the <b>install</b> icon at the right end of the address bar, or the button above.</Step>
            <Step n={2}>PolyPicker opens in its own window and can be pinned to your dock or taskbar.</Step>
          </ol>
        </Card>
      </div>

      <Card className="p-5 text-sm text-muted">
        <h2 className="mb-2 font-semibold text-ink">Good to know</h2>
        <ul className="list-disc space-y-1.5 pl-5">
          <li>The app always shows the latest forecasts. Updates install automatically; there’s nothing to update by hand.</li>
          <li>Pages you’ve opened stay available offline, clearly marked as possibly out of date.</li>
          <li>No account, no tracking for install, and no wagering. PolyPicker is informational only.</li>
          <li>To remove it: long-press the icon and choose <b>Remove App</b> (iOS) or <b>Uninstall</b> (Android).</li>
        </ul>
      </Card>
    </div>
  );
}
