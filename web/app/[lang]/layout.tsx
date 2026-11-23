import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { notFound } from "next/navigation";
import { lang as rootLang } from "next/root-params";
import { Suspense, ViewTransition } from "react";

import { BackToTop, BootScript, ThemeSync } from "@/components/client";
import { Header } from "@/components/Header";
import { LangProvider } from "@/components/i18n";
import { BottomNav, InstallBanner, OfflineBanner, ServiceWorkerRegistrar } from "@/components/pwa";
import { isLang, LANGS } from "@/lib/i18n";
import { dictFor, tFor } from "@/lib/i18n/server";
import "../globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export function generateStaticParams() {
  return LANGS.map((lang) => ({ lang }));
}

export async function generateMetadata(): Promise<Metadata> {
  const l = await rootLang();
  const t = tFor(isLang(l) ? l : "en");
  return {
    title: { default: t("PolyPicker — sports forecasts"), template: "%s · PolyPicker" },
    description: t("Model-based sports forecasts: win probabilities, projected scores and player stat forecasts. Read-only; no wagering."),
    applicationName: "PolyPicker",
    appleWebApp: { capable: true, title: "PolyPicker", statusBarStyle: "default" },
    formatDetection: { telephone: false },
  };
}


export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1117" },
  ],
  viewportFit: "cover", // lets the bottom tab bar sit above the iPhone home indicator via safe-area insets
};

// Runs before first paint: apply the saved (or system) theme to avoid a light/dark flash.
const themeScript = `try{var t=localStorage.getItem("pp-theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

export default async function RootLayout({ children }: LayoutProps<"/[lang]">) {
  const l = await rootLang();
  if (!isLang(l)) notFound();
  const t = tFor(l);
  return (
    <html lang={l} suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <head>
        <BootScript code={themeScript} />
      </head>
      <body className="flex min-h-full flex-col">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
          {t("Skip to content")}
        </a>
        <LangProvider lang={l} dict={dictFor(l)}>
        <ThemeSync />
        <ServiceWorkerRegistrar />
        <OfflineBanner />
        <Header />
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6"><ViewTransition default="page-fade">{children}</ViewTransition></main>
        <Suspense fallback={null}>
          <BottomNav />
        </Suspense>
        <InstallBanner />
        <BackToTop />
        <footer className="border-t border-line pb-[calc(64px+env(safe-area-inset-bottom))] md:pb-0">
          <div className="mx-auto flex max-w-[1400px] flex-col gap-2 px-4 py-6 text-xs text-muted sm:px-6">
            <p>
              <strong className="text-ink">{t("PolyPicker is informational only.")}</strong>{" "}
              {t("Forecasts are statistical estimates, not guarantees or financial advice. PolyPicker does not accept, place or facilitate wagers.")}
            </p>
            <p>
              {t("NBA data from NBA.com (via the Eoin Moore dataset and NBA.com’s live feed). NFL data from nflverse (CC BY 4.0) with live scores from ESPN. Soccer data from ESPN and openfootball (public domain); Football data provided by the Football-Data.org API. Not affiliated with the NBA, the NFL, any soccer league or confederation, ESPN or any team.")}{" "}
              <Link href="/methodology" className="underline hover:text-ink">{t("How forecasts work")}</Link>
              {" · "}
              <Link href="/install" className="underline hover:text-ink">{t("Install the app")}</Link>
            </p>
          </div>
        </footer>
        </LangProvider>
      </body>
    </html>
  );
}
