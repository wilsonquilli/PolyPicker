"use client";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <p className="text-sm font-semibold text-loss">Something went wrong</p>
      <h1 className="mt-1 text-2xl font-bold">Data is temporarily unavailable</h1>
      <p className="mt-2 text-sm text-muted">{error.message || "The forecast service didn’t respond."}</p>
      <button onClick={reset} className="mt-6 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg">Try again</button>
    </div>
  );
}
