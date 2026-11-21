import Link from "next/link";
import { Suspense } from "react";

import { CategoryNav, SearchBox, ThemeToggle } from "@/components/HeaderClient";

export function Logo() {
  return (
    <Link href="/" className="flex shrink-0 items-center gap-2" aria-label="PolyPicker home">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="9" fill="var(--accent)" />
        <path d="M9 22V10h7.2a4.4 4.4 0 0 1 0 8.8H12.4" fill="none" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="21.5" cy="21.5" r="2.4" fill="white" />
      </svg>
      <span className="text-[17px] font-bold tracking-tight">PolyPicker</span>
    </Link>
  );
}

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/90 backdrop-blur supports-[backdrop-filter]:bg-bg/75">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-3 sm:px-6">
        <Logo />
        <div className="hidden flex-1 md:block">
          <SearchBox />
        </div>
        <nav className="ml-auto flex items-center gap-1 text-sm">
          <Link href="/accuracy" className="hidden rounded-lg px-3 py-1.5 font-medium text-muted hover:bg-surface-2 hover:text-ink sm:block">Accuracy</Link>
          <Link href="/methodology" className="hidden rounded-lg px-3 py-1.5 font-medium text-muted hover:bg-surface-2 hover:text-ink sm:block">How it works</Link>
          <ThemeToggle />
        </nav>
      </div>
      <div className="px-4 pb-2 md:hidden">
        <SearchBox />
      </div>
      <Suspense fallback={<div className="h-11" />}>
        <CategoryNav />
      </Suspense>
    </header>
  );
}
