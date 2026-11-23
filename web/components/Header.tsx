import Link from "next/link";
import { Suspense } from "react";

import { CategoryNav, LanguageToggle, ThemeToggle } from "@/components/HeaderClient";
import { SearchPalette, SearchTrigger } from "@/components/SearchPalette";
import { GetAppLink } from "@/components/pwa";
import { getT } from "@/lib/i18n/server";

export async function Logo() {
  const t = await getT();
  return (
    <Link href="/" className="flex shrink-0 items-center" aria-label={t("PolyPicker home")}>
      {/* eslint-disable-next-line @next/next/no-img-element -- static brand asset, two theme variants */}
      <img src="/brand/logo-light.png" alt="PolyPicker" width={143} height={36} className="h-8 w-auto sm:h-9 dark:hidden" />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/brand/logo-dark.png" alt="PolyPicker" width={143} height={36} className="hidden h-8 w-auto sm:h-9 dark:block" />
    </Link>
  );
}

export async function Header() {
  const t = await getT();
  return (
    <header className="sticky top-0 z-40 pt-[env(safe-area-inset-top)] border-b border-line bg-bg/90 backdrop-blur supports-[backdrop-filter]:bg-bg/75">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-3 sm:px-6">
        <Logo />
        <div className="hidden flex-1 md:block"><SearchTrigger variant="bar" /></div>
        <nav className="ml-auto flex items-center gap-1 text-sm">
          <span className="md:hidden"><SearchTrigger variant="icon" /></span>
          <Link href="/methodology" className="hidden rounded-lg px-3 py-1.5 font-medium text-muted hover:bg-surface-2 hover:text-ink sm:block">{t("How it works")}</Link>
          <LanguageToggle />
          <GetAppLink />
          <ThemeToggle />
        </nav>
      </div>
      <Suspense fallback={<div className="h-11" />}>
        <CategoryNav />
      </Suspense>
      <SearchPalette />
    </header>
  );
}
