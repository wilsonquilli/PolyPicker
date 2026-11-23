import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <p className="text-sm font-semibold text-accent">404</p>
      <h1 className="mt-1 text-2xl font-bold">We couldn’t find that</h1>
      <p className="mt-2 text-sm text-muted">The game, team or player may not exist, or isn’t in our data yet.</p>
      <Link href="/" className="mt-6 inline-block rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg">Back to today’s games</Link>
    </div>
  );
}
