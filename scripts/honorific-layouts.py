#!/usr/bin/env python3
"""Lay out the stacked honorific ligatures from the font's own words.

    python3 scripts/honorific-layouts.py fonts/ttf/GulzarNastaliq-Regular.ttf

Books set رضی اللہُ عنہٗ and رحمۃُ اللہ علیہ as small stacked ligatures after a
name. Each one here is written with the font itself: every part is shaped
as an ordinary word, and the parts are placed in rows and scaled down.
The glyphs and their positions go to sources/honorifics.json, which the
"honorifics" step of scripts/nastaliq-reshape.py draws into the font.
Run it again (and rebuild) after letters used here change shape.
"""
import json
import os
import sys

import glyphsLib
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.filterPen import FilterPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# glyph name: code points, scale, height of the lowest point above the
# baseline, and the parts as (text, x, y[, options]) in font units before
# scaling. Option "kashida": the initial ain of the part is drawn in its
# long-stroke form and that stroke is lengthened by this many units, so the
# word spans the row beneath it.
LAYOUTS = {
    "radiAllahuAnhu": {
        "unicodes": ["0613", "FD41"],
        "scale": 0.5,
        "bottom": 60,
        # رضی and عنہٗ side by side, اللہُ above them in the middle
        "parts": [("عنہٗ", 0, 0), ("رضی", 900, 0), ("اللہُ", 340, 760)],
    },
    "rahmatullahAlayh": {
        "unicodes": ["0612"],
        "scale": 0.44,
        "bottom": 60,
        # علیہ on top, its ain drawn out to span رحمۃُ اللہ beneath it
        "parts": [("علیہ", -40, 960, {"kashida": 950}), ("رحمۃُ اللہ", 0, 0)],
    },
}
SIDE_BEARING = 40
# The ain whose lower stroke is lengthened, and the stretch of that stroke
# (between these x positions in the glyph) that takes up the extra length.
KASHIDA_AIN = "AINi2"
KASHIDA_RAMP = (40, 140)


def shape(font, text):
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    x, out = 0, []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = font.glyph_to_string(info.codepoint)
        if not name.startswith("space"):
            out.append((name, x + pos.x_offset, pos.y_offset))
        x += pos.x_advance
    return out


def stretched(pen, ramp, extra):
    """A pen that moves points right of the ramp by `extra`, blending across it."""
    x0, x1 = ramp

    def fx(pt):
        x, y = pt
        if x >= x1:
            return (x + extra, y)
        if x > x0:
            return (x + extra * (x - x0) / (x1 - x0), y)
        return (x, y)

    class Pen(FilterPen):
        def moveTo(self, pt):
            self._outPen.moveTo(fx(pt))

        def lineTo(self, pt):
            self._outPen.lineTo(fx(pt))

        def curveTo(self, *pts):
            self._outPen.curveTo(*map(fx, pts))

        def qCurveTo(self, *pts):
            self._outPen.qCurveTo(*[p if p is None else fx(p) for p in pts])

    return Pen(pen)


def with_kashida(glyphs, source, extra):
    """Swap the initial ain of a shaped word for the long-stroke ain, joined
    to the next letter by its anchors, with its stroke lengthened."""
    def anchor(name, which):
        layer = source.glyphs[name].layers[0]
        a = next(a for a in layer.anchors if a.name == which)
        return a.position.x, a.position.y

    rest = [g for g in glyphs if not g[0].startswith("AIN")]
    nxt = max((g for g in rest if "entry" in [a.name for a in source.glyphs[g[0]].layers[0].anchors]),
              key=lambda g: g[1])
    ex, ey = anchor(nxt[0], "entry")
    ax, ay = anchor(KASHIDA_AIN, "exit")
    ain = (KASHIDA_AIN, nxt[1] + ex - ax, nxt[2] + ey - ay, [*KASHIDA_RAMP, extra])
    return rest + [ain]


def main(path):
    glyphset = TTFont(path).getGlyphSet()
    font = hb.Font(hb.Face(open(path, "rb").read()))
    source = glyphsLib.GSFont(os.path.join(os.path.dirname(__file__), "..", "sources", "Gulzar.glyphs"))
    result = {}
    for name, spec in LAYOUTS.items():
        s = spec["scale"]
        parts = []
        for text, dx, dy, *opts in spec["parts"]:
            glyphs = [(g, x, y, None) for g, x, y in shape(font, text)]
            if opts and "kashida" in opts[0]:
                glyphs = with_kashida(glyphs, source, opts[0]["kashida"])
            parts += [(g, (x + dx) * s, (y + dy) * s, st) for g, x, y, st in glyphs]
        bounds = BoundsPen(glyphset)
        for g, x, y, st in parts:
            pen = TransformPen(bounds, (s, 0, 0, s, x, y))
            glyphset[g].draw(stretched(pen, st[:2], st[2]) if st else pen)
        x0, y0, x1, _ = bounds.bounds
        dx, dy = SIDE_BEARING - x0, spec["bottom"] - y0
        result[name] = {
            "unicodes": spec["unicodes"],
            "scale": s,
            "width": round(x1 - x0 + 2 * SIDE_BEARING),
            "components": [[g, round(x + dx), round(y + dy)] + ([st] if st else []) for g, x, y, st in parts],
        }
    out = os.path.join(os.path.dirname(__file__), "..", "sources", "honorifics.json")
    # one component per line keeps the file readable and its diffs small
    lines = []
    for name, r in result.items():
        comps = ",\n".join("    " + json.dumps(c, ensure_ascii=False) for c in r["components"])
        head = {k: v for k, v in r.items() if k != "components"}
        lines.append(f' "{name}": {json.dumps(head)[:-1]}, "components": [\n{comps}\n ]}}')
    with open(out, "w") as fh:
        fh.write("{\n" + ",\n".join(lines) + "\n}\n")


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
