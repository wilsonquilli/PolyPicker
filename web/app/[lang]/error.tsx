"use client";

import { useT } from "@/components/i18n";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useT();
  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <p className="text-sm font-semibold text-loss">{t("Something went wrong")}</p>
      <h1 className="mt-1 text-2xl font-bold">{t("Data is temporarily unavailable")}</h1>
      <p className="mt-2 text-sm text-muted">{error.message ? t(error.message) : t("The forecast service didn’t respond.")}</p>
      <button onClick={reset} className="mt-6 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg">{t("Try again")}</button>
    </div>
  );
}
