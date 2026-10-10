#!/usr/bin/env python3
"""Lay out the stacked honorific ligatures from the font's own words.

    python3 scripts/honorific-layouts.py fonts/ttf/GulzarNastaliq-Regular.ttf

Books set رضی اللہ عنہ and رحمۃ اللہ علیہ as small stacked ligatures after a
name. Each one here is written with the font itself: every part is shaped
as an ordinary word, and the parts are placed in rows and scaled down.
The glyphs and their positions go to sources/honorifics.json, which the
"honorifics" step of scripts/nastaliq-reshape.py draws into the font.
Run it again (and rebuild) after letters used here change shape.
"""
import json
import os
import sys

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# glyph name: code points, scale, height of the lowest point above the
# baseline, and the parts as (text, x, y) in font units before scaling
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
        "scale": 0.5,
        "bottom": 60,
        "parts": [("علیہ", 1120, 830), ("رحمۃ اللہ", 0, 0)],
    },
}
SIDE_BEARING = 40


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


def main(path):
    glyphset = TTFont(path).getGlyphSet()
    font = hb.Font(hb.Face(open(path, "rb").read()))
    result = {}
    for name, spec in LAYOUTS.items():
        s = spec["scale"]
        parts = [
            (g, (x + dx) * s, (y + dy) * s)
            for text, dx, dy in spec["parts"]
            for g, x, y in shape(font, text)
        ]
        bounds = BoundsPen(glyphset)
        for g, x, y in parts:
            glyphset[g].draw(TransformPen(bounds, (s, 0, 0, s, x, y)))
        x0, y0, x1, _ = bounds.bounds
        dx, dy = SIDE_BEARING - x0, spec["bottom"] - y0
        result[name] = {
            "unicodes": spec["unicodes"],
            "scale": s,
            "width": round(x1 - x0 + 2 * SIDE_BEARING),
            "components": [[g, round(x + dx), round(y + dy)] for g, x, y in parts],
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
