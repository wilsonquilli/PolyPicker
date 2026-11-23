import type { NextConfig } from "next";

// Where the PolyPicker FastAPI backend lives, as seen from the Next.js server.
const API_URL = (process.env.API_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Browser code calls the API through this same origin (/api/v1/...). That works on phones
  // (where "localhost" would be the phone), in the installed app, and needs no CORS.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_URL}/:path*` }];
  },
  // Sports live under their own prefix (/nba, /nfl, /soccer); "/" is the multi-sport home page. Links from
  // before the move (bookmarks, installed apps, shared URLs) keep working.
  async redirects() {
    return [
      ...["games", "teams", "players"].map((p) => ({ source: `/${p}/:path*`, destination: `/nba/${p}/:path*`, permanent: true })),
      { source: "/teams", destination: "/nba/teams", permanent: true },
      { source: "/standings", destination: "/nba/standings", permanent: true },
      { source: "/accuracy", destination: "/nba/accuracy", permanent: true },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
      {
        // The service worker must never be cached, so app updates reach installed users.
        source: "/sw.js",
        headers: [
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Security-Policy", value: "default-src 'self'; script-src 'self'" },
        ],
      },
    ];
  },
};

export default nextConfig;
