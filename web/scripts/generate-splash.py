"""Generate iOS launch screens ("apple-touch-startup-image") from the master logo.

iOS shows these while an installed PolyPicker opens (Android builds its launch screen from the web manifest's
background_color + icon instead). One PNG per device screen size, portrait (+ landscape for iPads), with the
logo centred on the brand navy. Re-run after changing the logo:

    backend/.venv/bin/python web/scripts/generate-splash.py

Writes web/public/splash/*.png and web/lib/splash-screens.json (read by the root layout's metadata).
"""
import json
from pathlib import Path

from PIL import Image

WEB = Path(__file__).resolve().parents[1]
LOGO = WEB / "design" / "PolyPickerLogo.png"      # white emblem + wordmark on transparent
OUT = WEB / "public" / "splash"
MANIFEST = WEB / "lib" / "splash-screens.json"
NAVY = (2, 16, 34)                                  # same as the app icon background (#021022)

# (css width, css height, device pixel ratio, tablet?)  portrait CSS size, as iOS reports it
DEVICES = [
    (440, 956, 3, False), (402, 874, 3, False), (430, 932, 3, False), (393, 852, 3, False), (390, 844, 3, False),
    (428, 926, 3, False), (375, 812, 3, False), (414, 896, 3, False), (414, 896, 2, False), (414, 736, 3, False),
    (375, 667, 2, False), (320, 568, 2, False),
    (1032, 1376, 2, True), (1024, 1366, 2, True), (834, 1210, 2, True), (834, 1194, 2, True), (820, 1180, 2, True),
    (810, 1080, 2, True), (768, 1024, 2, True), (744, 1133, 2, True),
]


def render(w: int, h: int, logo: Image.Image) -> Image.Image:
    img = Image.new("RGB", (w, h), NAVY)
    target = int(min(w, h) * (0.56 if min(w, h) < 1500 else 0.42))   # logo width: ~56% of the short side on phones
    lg = logo.resize((target, round(logo.height * target / logo.width)), Image.LANCZOS)
    img.paste(lg, ((w - lg.width) // 2, (h - lg.height) // 2 - h // 40), lg)   # a touch above centre, like native apps
    return img


def main() -> None:
    logo = Image.open(LOGO).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    screens = []
    for cw, ch, dpr, tablet in DEVICES:
        for orient in (("portrait", "landscape") if tablet else ("portrait",)):
            pw, ph = (cw * dpr, ch * dpr) if orient == "portrait" else (ch * dpr, cw * dpr)
            name = f"launch-{pw}x{ph}.png"
            render(pw, ph, logo).save(OUT / name, optimize=True)
            screens.append({"url": f"/splash/{name}", "media": f"(device-width: {cw}px) and (device-height: {ch}px) and "
                                                               f"(-webkit-device-pixel-ratio: {dpr}) and (orientation: {orient})"})
    MANIFEST.write_text(json.dumps(screens, indent=1) + "\n")
    print(f"{len(screens)} launch screens -> {OUT}")


if __name__ == "__main__":
    main()
