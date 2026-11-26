import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "PolyPicker — sports forecasts",
    short_name: "PolyPicker",
    description: "Model-based sports forecasts: win probabilities, projected scores and player stat forecasts. Read-only; no wagering.",
    start_url: "/?source=pwa",
    scope: "/",
    display: "standalone",
    display_override: ["standalone", "minimal-ui"],
    orientation: "any",
    background_color: "#021022",
    theme_color: "#021022",
    categories: ["sports", "news"],
    lang: "en-US",
    dir: "ltr",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],

    shortcuts: [
      { name: "Today's NBA games", short_name: "Today", url: "/nba?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Upcoming games", short_name: "Upcoming", url: "/nba?view=upcoming&source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "This week's NFL games", short_name: "NFL", url: "/nfl?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Soccer competitions", short_name: "Soccer", url: "/soccer?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Model accuracy", short_name: "Accuracy", url: "/nba/accuracy?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
    ],

    screenshots: [
      { src: "/screenshots/mobile-slate.png", sizes: "780x1688", type: "image/png", form_factor: "narrow", label: "Today's NBA games with win probabilities" },
      { src: "/screenshots/mobile-game.png", sizes: "780x1688", type: "image/png", form_factor: "narrow", label: "Game forecast with margin distribution" },
      { src: "/screenshots/desktop-game.png", sizes: "1440x900", type: "image/png", form_factor: "wide", label: "Game forecast, player props and line explorer" },
    ],
  };
}
