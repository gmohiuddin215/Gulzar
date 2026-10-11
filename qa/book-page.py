#!/usr/bin/env python3
"""Set a page of text in the font, laid out like a printed Urdu book page.

    python3 qa/book-page.py FONT TEXT.txt OUT.png [--title T] [--chapter C] [--page N]

Each line of TEXT.txt is one line of the page, as broken in the book being
compared against; lines are justified to the measure by widening the word
spaces, except a short line (the last line of a paragraph), which is set
flush right. The measure is the widest line at natural spacing, and the
line pitch and page shape follow the proportions of a typical book page
(a 24-26 line text block under a running head with a double rule), so a
scan of the same page can be put beside it.
"""
import argparse

import cairosvg
import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

PITCH_TO_MEASURE = 55 / 930  # line pitch / text width, from a printed page
PAGE_TO_MEASURE = 1178 / 930  # page width / text width
PAGE_ASPECT = 1469 / 1178  # page height / page width
SHORT_LINE = 0.8  # lines narrower than this share of the measure are not justified
HEAD_SCALE = 0.8  # running head size, relative to the text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("font")
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("--title", default="", help="running head at the right")
    ap.add_argument("--chapter", default="", help="running head at the left")
    ap.add_argument("--page", default="", help="page number, in the middle")
    ap.add_argument("--width", type=int, default=1600, help="image width in pixels")
    ap.add_argument("--space", type=int, help="word space in font units (default: the font's own, "
                    "to preview a change before rebuilding)")
    ap.add_argument("--wrap", type=float, help="treat each line as a paragraph and break it to this "
                    "measure (font units) instead of keeping the given line breaks")
    ap.add_argument("--center", action="store_true", help="centre every line (verse) instead of justifying")
    ap.add_argument("--check-lines", action="store_true",
                    help="report any ink of one line touching the line above or below it")
    ap.add_argument("--source", help="draw the letters from this .glyphs source instead of the font "
                    "(positions still come from the font), to preview outline changes before rebuilding")
    args = ap.parse_args()

    tt = TTFont(args.font)
    glyphs = tt.getGlyphSet()
    hbfont = hb.Font(hb.Face(open(args.font, "rb").read()))

    def shape(text):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(hbfont, buf)
        x, out = 0, []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((hbfont.glyph_to_string(info.codepoint), x + pos.x_offset, pos.y_offset))
            x += pos.x_advance
        return out, x

    # the Urdu word space (a lone space would be shaped as the Latin one)
    space = args.space if args.space is not None else hbfont.get_glyph_h_advance(hbfont.get_glyph_from_name("space.urdu"))
    lines = [[shape(w) for w in line.split()] for line in open(args.text, encoding="utf-8").read().splitlines()]
    lines = [l for l in lines if l]
    if args.wrap:
        broken = []
        for para in lines:
            line, width = [], 0
            for word in para:
                extra = word[1] + (space if line else 0)
                if line and width + extra > args.wrap:
                    broken.append(line)
                    line, width = [], 0
                    extra = word[1]
                line.append(word)
                width += extra
            broken.append(line)
        lines = broken
    natural = [sum(w for _, w in l) + space * (len(l) - 1) for l in lines]
    measure = args.wrap or max(natural)
    pitch = measure * PITCH_TO_MEASURE
    page_w = measure * PAGE_TO_MEASURE
    page_h = page_w * PAGE_ASPECT
    margin = (page_w - measure) / 2

    paths = []

    source = None
    if args.source:
        import glyphsLib
        source = glyphsLib.GSFont(args.source)
        master = source.masters[0].id

    def draw_source(name, pen):
        layer = next(l for l in source.glyphs[name].layers if l.layerId == master)
        for path in layer.paths:
            nodes = path.nodes
            pen.moveTo(tuple(nodes[-1].position))
            buf = []
            for n in nodes:
                if n.type == "offcurve":
                    buf.append(tuple(n.position))
                elif buf:
                    pen.curveTo(*buf, tuple(n.position))
                    buf = []
                else:
                    pen.lineTo(tuple(n.position))
            pen.closePath()
        for comp in layer.components:
            draw_source(comp.name, TransformPen(pen, comp.transform))

    def draw(shaped, x0, base, s=1.0):
        for name, gx, gy in shaped:
            pen = SVGPathPen(glyphs)
            tpen = TransformPen(pen, (s, 0, 0, -s, x0 + gx * s, base - gy * s))
            if source is not None and name in source.glyphs:
                draw_source(name, tpen)
            else:
                glyphs[name].draw(tpen)
            paths.append(f"<path d='{pen.getCommands()}' fill-rule='nonzero'/>")

    # running head and double rule
    head = page_h * 0.06
    for text, where in ((args.title, "right"), (args.page, "middle"), (args.chapter, "left")):
        if not text:
            continue
        if where == "middle":
            text = "﴿ " + text[::-1] + " ﴾"  # digits are shaped in logical order here
        shaped, w = shape(text)
        w *= HEAD_SCALE
        x = margin if where == "left" else page_w - margin - w if where == "right" else (page_w - w) / 2
        draw(shaped, x, head, HEAD_SCALE)
    rule = head + pitch * 0.45
    rules = (f"<rect x='{margin}' y='{rule}' width='{measure}' height='{pitch * 0.035}'/>"
             f"<rect x='{margin}' y='{rule + pitch * 0.08}' width='{measure}' height='{pitch * 0.012}'/>")

    y = rule + pitch * 1.15
    line_spans = []
    for line, nat in zip(lines, natural):
        start = len(paths)
        words = sum(w for _, w in line)
        gap = space if args.center or nat < SHORT_LINE * measure or len(line) == 1 else (measure - words) / (len(line) - 1)
        x = page_w - margin - ((measure - nat) / 2 if args.center else 0)
        for shaped, w in line:
            x -= w
            draw(shaped, x, y)
            x -= gap
        line_spans.append((start, len(paths), [w for w in line]))
        y += pitch
    page_h = max(page_h, y + pitch * 0.2)

    if args.check_lines:
        import re
        import pathops
        from fontTools.svgLib.path import parse_path

        def ink(a, b):
            pen_path = pathops.Path()
            for svg in paths[a:b]:
                parse_path(re.search("d='([^']*)'", svg).group(1), pen_path.getPen())
            pen_path.simplify()
            return pen_path

        inks = [ink(a, b) for a, b, _ in line_spans]
        touching = 0
        for i in range(len(inks) - 1):
            both = pathops.op(inks[i], inks[i + 1], pathops.PathOp.INTERSECTION)
            if list(both.segments):
                touching += 1
                b = both.bounds
                print(f"lines {i + 1} and {i + 2} touch, at x {b[0]:.0f}-{b[2]:.0f} from the left edge")
        print(f"line check: {touching} of {len(inks) - 1} line pairs touch")

    scale = args.width / page_w
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{page_w * scale}' height='{page_h * scale}' "
           f"viewBox='0 0 {page_w} {page_h}'><rect width='100%' height='100%' fill='#fbfaf6'/>"
           f"<g fill='#111'>{rules}{''.join(paths)}</g></svg>")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=args.out)
    print(f"{len(lines)} lines, measure {measure:.0f}, pitch {pitch:.0f} units")


if __name__ == "__main__":
    main()
