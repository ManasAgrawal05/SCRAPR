"""Regenerate static/logo.svg: the word SCRAPR as outlined vector letters (one colour per letter).

Outlines, not a web font, so the logo renders identically in <img> tags and as the favicon, and the font file
itself is never shipped with the site or the repository.

    pip install fonttools
    python tools/make_logo.py /path/to/PaintMarker.otf
"""
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

WORD = "SCRAPR"
COLORS = ["#4285f4", "#ea4335", "#fbbc05", "#4285f4", "#34a853", "#ea4335"]
W, H, PAD = 400, 110, 6        # the viewBox the pages already size the logo to
TRACKING = -0.02               # extra space between letters, in em (negative = tighter)

font = TTFont(sys.argv[1])
glyphs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
names = [cmap[ord(c)] for c in WORD]

# lay the letters out left to right in font units, then fit the whole word into the viewBox
x, placed = 0.0, []
for name in names:
    pen = BoundsPen(glyphs)
    glyphs[name].draw(pen)
    placed.append((name, x, pen.bounds))
    x += glyphs[name].width + TRACKING * upm
x_min = min(px + b[0] for _, px, b in placed)
x_max = max(px + b[2] for _, px, b in placed)
y_min = min(b[1] for _, _, b in placed)
y_max = max(b[3] for _, _, b in placed)

scale = min((W - 2 * PAD) / (x_max - x_min), (H - 2 * PAD) / (y_max - y_min))
left = (W - (x_max - x_min) * scale) / 2 - x_min * scale     # centre horizontally
base = (H - (y_max - y_min) * scale) / 2 + y_max * scale     # centre vertically (SVG y points down)

paths = []
for (name, px, _), color in zip(placed, COLORS):
    pen = SVGPathPen(glyphs)
    glyphs[name].draw(pen)
    paths.append(f'  <path fill="{color}" transform="translate({left + px * scale:.2f} {base:.2f}) scale({scale:.5f} {-scale:.5f})" d="{pen.getCommands()}"/>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="SCRAPR">\n'
       + "\n".join(paths) + "\n</svg>\n")
out = Path(__file__).resolve().parent.parent / "static" / "logo.svg"
out.write_text(svg)
print(f"wrote {out} ({len(svg)} bytes), font scale {scale:.4f}")
