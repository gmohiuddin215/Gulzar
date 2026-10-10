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
# baseline (or "align": a letter and the height to set it at), and the
# parts as (text, x, y[, options]) in font units before
# scaling. Option "kashida": (length, rise): the initial ain of the part is
# drawn in its long-stroke form, moved over by `length` and up by `rise`,
# and its tail carried to the next letter by a drawn stroke (see
# step_honorifics in scripts/nastaliq-reshape.py), so the word spans the
# row beneath it. Options "lower" / "shorten": {glyph: (y0, factor, x0, x1)}:
# in this part only, that letter's rising stroke is brought down, or its
# falling tail drawn up (see honorific_outline in scripts/nastaliq-reshape.py).
# Option "nudge": {glyph: (dx, dy)}: in this part only, that glyph (a dot,
# a vowel mark) is moved by so much. Option "shrink": {glyph: (factor,
# [marks])}: in this part only, that letter is drawn smaller about the point
# where it joins the letter before, and the listed marks move in with it.
LAYOUTS = {
    "radiAllahuAnhu": {
        "unicodes": ["0613", "FD41"],
        "scale": 0.5,
        "bottom": 60,
        # رضی and عنہٗ side by side, اللہُ above them in the middle
        "parts": [("عنہٗ", 0, 0), ("رضی", 860, 0), ("اللہُ", 320, 760)],
    },
    # the same for a woman: اللہُ sits above, as clear of the row beneath as
    # in رضی اللہُ عنہٗ
    "radiAllahuAnhaa": {
        "unicodes": ["FD42"],
        "scale": 0.5,
        "align": ("REu1", 135),  # رضی level with رضی اللہُ عنہٗ
        "parts": [("عنہا", 0, 0, {"nudge": {"haydb": (-40, 70)}}), ("رضی", 1019, 0),
                  ("اللہُ", 417, 0, {"clear": 51})],
    },
    # for two: عنہما tucks its ain over the ye of رضی, whose rising tail is
    # brought down just enough to end inside the ain's stroke; اللہُ sits above ہما
    "radiAllahuAnhumaa": {
        "unicodes": ["FD44"],
        "scale": 0.5,
        "align": ("REu1", 135),  # رضی level with رضی اللہُ عنہٗ
        "parts": [("عنہُما", 0, 0, {"nudge": {"sda": (0, -100)}}),
                  ("رضی", 760, 0, {"lower": {"CH_YEf1": (150, 0.95, 200, 320)}}),
                  ("اللہُ", 60, 888)],
    },
    # for men: the same, with عنہم
    "radiAllahuAnhum": {
        "unicodes": ["FD43"],
        "scale": 0.5,
        "align": ("REu1", 135),  # رضی level with رضی اللہُ عنہٗ
        # its mim's long tail is drawn up to a short one, as in Quranic
        # hands, so the honorific isn't taller than the others
        "parts": [("عنہم", 0, -324, {"shorten": {"MIMf1": (150, 0.35, 200, 300)}}),
                  ("رضی", 760, 0, {"lower": {"CH_YEf1": (150, 0.95, 200, 320)}}),
                  ("اللہُ", 60, 0, {"clear": 28})],
    },
    # for women: the same, with عنہنَّ (shadda and zabar on the noon)
    "radiAllahuAnhunna": {
        "unicodes": ["FD45"],
        "scale": 0.5,
        "align": ("REu1", 135),  # رضی level with رضی اللہُ عنہٗ
        # its final noon is drawn smaller, nearer the size of the ye, with its
        # dot and shadda-zabar; the first noon's dot sits nearer its tooth
        # and the he hook under the he
        "parts": [("عنہنَّ", 0, -218, {"shrink": {"NUNf1": (0.8, ["sdi", "SHADDA_FATHA"])},
                                    "nudge": {"sda": (0, -100), "haydb": (-220, 0)}}),
                  ("رضی", 1079, 0, {"lower": {"CH_YEf1": (150, 0.95, 200, 320)}}),
                  ("اللہُ", 300, 0, {"clear": 28})],
    },
    # the durood ﷺ: صلی at the right with اللہ above it, علیہ beside it, و
    # above the he of علیہ and سلم above that, its mim's head over the vav
    "sallallahuAlayhiWasallam": {
        "unicodes": ["FDFA"],
        "scale": 0.45,
        "bottom": 40,
        "parts": [("صلی", 2872, -178), ("اللہ", 2769, 1166), ("علیہ", 1919, 92),
                  ("و", 1935, 306), ("سلم", 1681, 430)],
    },
    "rahmatullahAlayh": {
        "unicodes": ["0612"],
        "scale": 0.44,
        "bottom": 60,
        # علیہ on top, its ain drawn out to span رحمۃُ اللہ beneath it
        "parts": [("علیہ", -240, 700, {"kashida": (470, 216)}), ("اللہ", 0, 0), ("رحمۃُ", 1020, 0)],
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


def columns(path, step=8):
    """Ink extent of a pathops Path in narrow vertical slices: {x: (bottom, top)}."""
    import pathops

    x0, _, x1, _ = path.bounds
    out = {}
    x = x0
    while x < x1:
        r = pathops.Path()
        r.moveTo(x, -5000)
        r.lineTo(x + step, -5000)
        r.lineTo(x + step, 5000)
        r.lineTo(x, 5000)
        r.close()
        cut = pathops.op(path, r, pathops.PathOp.INTERSECTION)
        if cut.bounds and cut.bounds[2] > cut.bounds[0]:
            out[round(x / step)] = (cut.bounds[1], cut.bounds[3])
        x += step
    return out


def clearance(below, part, step=8):
    """How far `part` sits above `below`: the smallest vertical gap between
    them, over the slices where both have ink."""
    a, b = columns(below, step), columns(part, step)
    gaps = [b[k][0] - a[k][1] for k in b if k in a]
    return min(gaps) if gaps else None


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
            if opts and "clear" in opts[0]:
                # sit this part just above what is already placed, the given
                # distance clear of it at the closest point
                below = reshape.honorific_outline(source, comps, s)
                trial = reshape.honorific_outline(source, [[g, (x + dx) * s, y * s] for g, x, y, _ in glyphs], s)
                dy = (opts[0]["clear"] - clearance(below, trial)) / s
            if opts and "kashida" in opts[0]:
                glyphs = with_kashida(glyphs, source, *opts[0]["kashida"])
            reshapes = {k: opts[0][k] for k in ("lower", "shorten") if opts and k in opts[0]}
            nudge = opts[0].get("nudge", {}) if opts else {}
            size = {}
            for letter, (f, marks) in (opts[0].get("shrink", {}) if opts else {}).items():
                # the letter is drawn smaller about the point where it joins
                # the letter before it, and its marks move in with it
                g0 = next(g for g in glyphs if g[0] == letter)
                ex, ey = next((a.position.x, a.position.y) for a in source.glyphs[letter].layers[0].anchors
                              if a.name == "entry")
                cx, cy = g0[1] + ex, g0[2] + ey
                glyphs = [(g, x + (1 - f) * ex, y + (1 - f) * ey, o) if g == letter else
                          (g, cx + f * (x - cx), cy + f * (y - cy), o) if g in marks else (g, x, y, o)
                          for g, x, y, o in glyphs]
                size[letter] = f
            for g, x, y, opt in glyphs:
                nx, ny = nudge.get(g, (0, 0))
                comp = [g, (x + dx + nx) * s, (y + dy + ny) * s]
                if g in size:
                    comp.append({"size": size[g]})
                for kind, letters in reshapes.items():
                    if g in letters:
                        comp.append({kind: letters[g]})
                if opt:
                    lam, lx, ly = opt["kashida"][:3]
                    comp.append({"kashida": [lam, (lx + dx) * s, (ly + dy) * s]})
                comps.append(comp)
        x0, y0, x1, _ = reshape.honorific_outline(source, comps, s).bounds
        dx = SIDE_BEARING - x0
        if "align" not in spec:
            dy = spec["bottom"] - y0
        else:
            # set a given letter at a given height instead, so a set of
            # honorifics shares one line whatever hangs below it
            letter, height = spec["align"]
            dy = height - next(c[2] for c in comps if c[0] == letter)
        for c in comps:
            c[1], c[2] = round(c[1] + dx), round(c[2] + dy)
            if len(c) > 3 and "kashida" in c[3]:
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
