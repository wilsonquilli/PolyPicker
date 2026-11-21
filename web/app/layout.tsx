import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { Header } from "@/components/Header";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "PolyPicker — NBA forecasts", template: "%s · PolyPicker" },
  description: "Model-based NBA win probabilities, projected scores and player stat forecasts. Read-only; no wagering.",
};

// Runs before first paint: apply the saved (or system) theme to avoid a light/dark flash.
const themeScript = `try{var t=localStorage.getItem("pp-theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="flex min-h-full flex-col">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
          Skip to content
        </a>
        <Header />
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">{children}</main>
        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1400px] flex-col gap-2 px-4 py-6 text-xs text-muted sm:px-6">
            <p>
              <strong className="text-ink">PolyPicker is informational only.</strong> Forecasts are statistical estimates,
              not guarantees or financial advice. PolyPicker does not accept, place or facilitate wagers.
            </p>
            <p>
              Data derived from NBA.com via the Eoin Moore historical NBA dataset. Not affiliated with the NBA or any team.{" "}
              <Link href="/methodology" className="underline hover:text-ink">How forecasts work</Link>
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
