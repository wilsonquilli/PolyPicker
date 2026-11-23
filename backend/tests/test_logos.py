"""Club logo sync: downloads only missing logos, converts to 256 px WebP, writes the manifest."""

import io
import json

import pytest
from PIL import Image

from polypicker.soccer.etl import logos as L


def _png(w=500, h=400) -> bytes:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste((200, 0, 0, 255), (100, 100, 400, 300))        # 300x200 opaque logo inside a transparent border
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


def test_to_webp_trims_and_fits():
    im = Image.open(io.BytesIO(L.to_webp(_png())))
    assert im.format == "WEBP" and im.size == (L.SIZE, L.SIZE)
    x0, y0, x1, y1 = im.convert("RGBA").getchannel("A").getbbox()
    assert (x0, x1) == (0, L.SIZE)                             # border trimmed: the logo spans the full width
    assert y1 - y0 == pytest.approx(L.SIZE * 2 / 3, abs=2)     # aspect ratio kept, centred vertically


def test_sync_downloads_only_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "current_clubs", lambda engine: {1: "Club A", 2: "Club B", 3: "No Logo FC"})
    (tmp_path / "1.webp").write_bytes(b"existing")
    calls = []
    def fetch(tid):
        calls.append(tid)
        return None if tid == 3 else _png()
    manifest = tmp_path / "logos.json"
    out = L.sync_logos(engine=None, fetch=fetch, logo_dir=tmp_path, manifest=manifest, pause=0)
    assert calls == [2, 3]                                     # club 1 already had a logo
    assert out["downloaded"] == 1 and out["no_logo_at_espn"] == ["No Logo FC"]
    assert json.loads(manifest.read_text()) == [1, 2]
    assert (tmp_path / "1.webp").read_bytes() == b"existing"   # never re-downloaded without --refresh


def test_refresh_redownloads(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "current_clubs", lambda engine: {1: "Club A"})
    (tmp_path / "1.webp").write_bytes(b"old")
    L.sync_logos(engine=None, refresh=True, fetch=lambda tid: _png(), logo_dir=tmp_path, manifest=tmp_path / "m.json", pause=0)
    assert (tmp_path / "1.webp").read_bytes() != b"old"
