import Link from "next/link";

import type { T } from "@/lib/i18n";
import { SOCIAL } from "@/lib/social";

/** Site footer: logo, tagline, social links (set in lib/social.ts) and the data attributions the sources require. */
export function Footer({ t }: { t: T }) {
  const links = [
    { key: "linkedin", label: "LinkedIn", href: SOCIAL.LINKEDIN, icon: <LinkedInIcon /> },
    { key: "github", label: "GitHub", href: SOCIAL.GITHUB, icon: <GitHubIcon /> },
    { key: "email", label: t("Email"), href: SOCIAL.EMAIL ? `mailto:${SOCIAL.EMAIL}` : "", icon: <MailIcon /> },
  ];
  return (
    <footer className="border-t border-line pb-[calc(64px+env(safe-area-inset-bottom))] md:pb-0">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-8 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1.5">
            <Link href="/" aria-label="PolyPicker" className="self-start">
              {/* eslint-disable-next-line @next/next/no-img-element -- same static logo as the header */}
              <img src="/brand/logo-light.png" alt="PolyPicker" width={143} height={36} className="h-8 w-auto dark:hidden" />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/brand/logo-dark.png" alt="PolyPicker" width={143} height={36} className="hidden h-8 w-auto dark:block" />
            </Link>
            <p className="text-sm text-muted">{t("The World's Largest Prediction Market to the Prediction Market")}</p>
          </div>
          <ul className="flex items-center gap-2">
              {links.map((l) => (
                <li key={l.key}>
                  {/* Until a link is set in lib/social.ts the icon shows but goes nowhere. */}
                  <a href={l.href || undefined} aria-label={l.label} title={l.label}
                    {...(l.key === "email" ? {} : { target: "_blank", rel: "noopener noreferrer" })}
                    className="flex h-10 w-10 items-center justify-center rounded-full border border-line bg-surface text-muted transition hover:-translate-y-px hover:border-accent/40 hover:text-ink">
                    {l.icon}
                  </a>
                </li>
              ))}
            </ul>
        </div>
        <p className="text-[11px] leading-relaxed text-faint">
          {t("NBA data from NBA.com. NFL data from nflverse (CC BY 4.0) and ESPN. Soccer data from ESPN and openfootball; Football data provided by the Football-Data.org API.")}
        </p>
      </div>
    </footer>
  );
}

const svg = { width: 18, height: 18, viewBox: "0 0 24 24", "aria-hidden": true } as const;

function LinkedInIcon() {
  return (
    <svg {...svg} fill="currentColor">
      <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.34V9h3.42v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.07 2.07 0 1 1 0-4.13 2.07 2.07 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg {...svg} fill="currentColor">
      <path d="M12 .3a12 12 0 0 0-3.8 23.38c.6.12.83-.26.83-.57v-2.1c-3.34.73-4.04-1.6-4.04-1.6-.55-1.4-1.34-1.77-1.34-1.77-1.08-.74.09-.73.09-.73 1.2.09 1.83 1.24 1.83 1.24 1.07 1.83 2.8 1.3 3.49 1 .1-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.14-.3-.54-1.52.1-3.18 0 0 1-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.28-1.55 3.29-1.23 3.29-1.23.64 1.66.24 2.88.12 3.18a4.65 4.65 0 0 1 1.23 3.22c0 4.61-2.8 5.63-5.48 5.92.42.36.81 1.1.81 2.22v3.29c0 .32.21.7.82.58A12 12 0 0 0 12 .3" />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg {...svg} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
    </svg>
  );
}
