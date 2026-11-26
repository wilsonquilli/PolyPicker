import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { notFound } from "next/navigation";
import { lang as rootLang } from "next/root-params";
import { Suspense, ViewTransition } from "react";
import { BackToTop, BootScript, ThemeSync } from "@/components/client";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { Splash } from "@/components/Splash";
import { LangProvider } from "@/components/i18n";
import { BottomNav, InstallBanner, OfflineBanner, ServiceWorkerRegistrar } from "@/components/pwa";
import { isLang, LANGS } from "@/lib/i18n";
import { dictFor, tFor } from "@/lib/i18n/server";
import splashScreens from "@/lib/splash-screens.json";
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
    title: { default: t("PolyPicker | The Prediction to the Prediction"), template: "%s · PolyPicker" },
    description: t("Model-based sports forecasts: win probabilities, projected scores and player stat forecasts. Read-only; no wagering."),
    applicationName: "PolyPicker",
    appleWebApp: { capable: true, title: "PolyPicker", statusBarStyle: "default", startupImage: splashScreens },
    formatDetection: { telephone: false },
  };
}


export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1117" },
  ],
  viewportFit: "cover", 
};

const themeScript = `try{if(sessionStorage.getItem("pp-splash")==="1")document.documentElement.classList.add("pp-splash-seen");else{sessionStorage.setItem("pp-splash","1");window.__ppSplashFirst=1}}catch(e){}try{var t=localStorage.getItem("pp-theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

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
        <Splash />
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
        <Footer t={t} />
        </LangProvider>
      </body>
    </html>
  );
}