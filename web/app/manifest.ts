import type { MetadataRoute } from "next";

// Web App Manifest: makes PolyPicker installable to the home screen (Android/Chrome/Edge install
// prompt, iOS "Add to Home Screen") and run full-screen like a native app.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "PolyPicker — NBA forecasts",
    short_name: "PolyPicker",
    description: "Model-based NBA win probabilities, projected scores and player stat forecasts. Read-only; no wagering.",
    start_url: "/?source=pwa",
    scope: "/",
    display: "standalone",
    display_override: ["standalone", "minimal-ui"],
    orientation: "any",
    background_color: "#ffffff",
    theme_color: "#6345ed",
    categories: ["sports", "news"],
    lang: "en-US",
    dir: "ltr",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    // Long-press the app icon (Android) for these.
    shortcuts: [
      { name: "Today's games", short_name: "Today", url: "/?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Upcoming games", short_name: "Upcoming", url: "/?view=upcoming&source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Model accuracy", short_name: "Accuracy", url: "/accuracy?source=pwa-shortcut", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
    ],
    // Shown in Android's richer install dialog.
    screenshots: [
      { src: "/screenshots/mobile-slate.png", sizes: "780x1688", type: "image/png", form_factor: "narrow", label: "Today's NBA games with win probabilities" },
      { src: "/screenshots/mobile-game.png", sizes: "780x1688", type: "image/png", form_factor: "narrow", label: "Game forecast with margin distribution" },
      { src: "/screenshots/desktop-game.png", sizes: "1440x900", type: "image/png", form_factor: "wide", label: "Game forecast, player props and line explorer" },
    ],
  };
}
