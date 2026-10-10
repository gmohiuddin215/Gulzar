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
import importlib.util
import json
import os
import sys

import glyphsLib
import uharfbuzz as hb

# glyph name: code points, scale, height of the lowest point above the
# baseline, and the parts as (text, x, y[, options]) in font units before
# scaling. Option "kashida": (length, rise): the initial ain of the part is
# drawn in its long-stroke form, moved over by `length` and up by `rise`,
# and its tail carried to the next letter by a drawn stroke (see
# step_honorifics in scripts/nastaliq-reshape.py), so the word spans the
# row beneath it.
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
        "parts": [("علیہ", -40, 960, {"kashida": (750, 330)}), ("رحمۃُ اللہ", 0, 0)],
    },
}
SIDE_BEARING = 40
KASHIDA_AIN = "AINi2"  # the initial ain with a long lower stroke


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


def with_kashida(glyphs, source, length, rise):
    """Swap the initial ain of a shaped word for the long-stroke ain, moved
    over and up from where it would join the next letter."""
    def anchors(name):
        return {a.name: (a.position.x, a.position.y) for a in source.glyphs[name].layers[0].anchors}

    rest = [g for g in glyphs if not g[0].startswith("AIN")]
    lam = max((g for g in rest if "entry" in anchors(g[0])), key=lambda g: g[1])
    ex, ey = anchors(lam[0])["entry"]
    ax, ay = anchors(KASHIDA_AIN)["exit"]
    ain = (KASHIDA_AIN, lam[1] + ex - ax + length, lam[2] + ey - ay + rise, {"kashida": lam})
    return rest + [ain]


def main(path):
    font = hb.Font(hb.Face(open(path, "rb").read()))
    here = os.path.dirname(os.path.abspath(__file__))
    source = glyphsLib.GSFont(os.path.join(here, "..", "sources", "Gulzar.glyphs"))
    spec = importlib.util.spec_from_file_location("reshape", os.path.join(here, "nastaliq-reshape.py"))
    reshape = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reshape)
    result = {}
    for name, spec in LAYOUTS.items():
        s = spec["scale"]
        comps = []
        for text, dx, dy, *opts in spec["parts"]:
            glyphs = [(g, x, y, None) for g, x, y in shape(font, text)]
            if opts and "kashida" in opts[0]:
                glyphs = with_kashida(glyphs, source, *opts[0]["kashida"])
            for g, x, y, opt in glyphs:
                comp = [g, (x + dx) * s, (y + dy) * s]
                if opt:
                    lam, lx, ly = opt["kashida"][:3]
                    comp.append({"kashida": [lam, (lx + dx) * s, (ly + dy) * s]})
                comps.append(comp)
        x0, y0, x1, _ = reshape.honorific_outline(source, comps, s).bounds
        dx, dy = SIDE_BEARING - x0, spec["bottom"] - y0
        for c in comps:
            c[1], c[2] = round(c[1] + dx), round(c[2] + dy)
            if len(c) > 3:
                k = c[3]["kashida"]
                k[1], k[2] = round(k[1] + dx), round(k[2] + dy)
        result[name] = {
            "unicodes": spec["unicodes"],
            "scale": s,
            "width": round(x1 - x0 + 2 * SIDE_BEARING),
            "components": comps,
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
