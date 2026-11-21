import Link from "next/link";

export const metadata = { title: "Offline" };

// Precached by the service worker; shown when offline and the requested page was never saved.
export default function OfflinePage() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <svg className="mx-auto text-faint" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <path d="M2 8.8a15 15 0 0 1 4.2-2.7M22 8.8A15 15 0 0 0 10 4.9M5 12.6a10 10 0 0 1 3-1.9M19 12.6a10 10 0 0 0-5.2-2.4M8.5 16.4a5 5 0 0 1 7 0M12 20h.01M3 3l18 18" />
      </svg>
      <h1 className="mt-4 text-2xl font-bold">You’re offline</h1>
      <p className="mt-2 text-sm text-muted">
        This page hasn’t been saved on your device yet. Pages you’ve opened before are available offline,
        but forecasts may be out of date until you reconnect.
      </p>
      <Link href="/" className="mt-6 inline-block rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg">Try today’s games</Link>
    </div>
  );
}
