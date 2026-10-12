#!/usr/bin/env python3
"""Render the Urdu text used in README.md as images in the font itself.

    python3 scripts/readme-images.py fonts/ttf/GulzarNastaliq-Regular.ttf documentation/readme
"""
import sys
import cairosvg
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HONORIFICS = [
    # (file, code points, typed phrase)
    ("fdfa", "FDFA", "صلی اللہ علیہ وسلم"),
    ("fd4c", "FD4C", "صلی اللہ علیہ وآلہٖ وسلم"),
    ("fd41", "FD41", "رضی اللہ عنہ"),
    ("fd42", "FD42", "رضی اللہ عنہا"),
    ("fd43", "FD43", "رضی اللہ عنہم"),
    ("fd44", "FD44", "رضی اللہ عنہما"),
    ("fd45", "FD45", "رضی اللہ عنہن"),
    ("0613", "0613", "رضی اللہ عنہ (small sign)"),
    ("0612", "0612", "رحمۃ اللہ علیہ"),
    ("fdff", "FDFF", "عزوجل"),
    ("fd4b", "FD4B", "قدس سرہ"),
    ("fd47", "FD47", "علیہ السلام"),
    ("fd48", "FD48", "علیہم السلام"),
    ("fd49", "FD49", "علیہما السلام"),
    ("fd4d", "FD4D", "علیہا السلام"),
    ("0611", "0611", "علیہ السلام (small sign)"),
    ("fd4a", "FD4A", "علیہ الصلوۃ والسلام"),
]

SAMPLES = {
    "title": ("گلزار نستعلیق", 84),
    "typed": ("حضرت محمد صلی اللہ علیہ وسلم، حضرت موسیٰ علیہ السلام، حضرت ابوبکر رضی اللہ عنہ", 44),
    "typed2": ("حضرت فاطمہ رضی اللہ عنہا، امام احمد رحمۃ اللہ علیہ", 44),
    "kaf": ("کی، گی، کیے، گئے، اگر، مگر، کم، گھنٹوں", 44),
    "laam": ("لکھا، لگا، ملک، الگ", 44),
    "joins": ("صحیح، صل", 44),
    "spacing": ("ذخیرہ، دین، مؤطا، دیہی", 44),
    "dots": ("نقل، یحیی، آغاز، ادیبوں، نشیبی، مستقبل، کابینہ، بست، تربیت، نئی، تقریباً", 44),
}


def render(font_path, text, size, out, pad=18, fill="#1d2b2a", bg="#f6f3ee"):
    tt = TTFont(font_path)
    gs = tt.getGlyphSet()
    upm = tt["head"].unitsPerEm
    face = hb.Font(hb.Face(open(font_path, "rb").read()))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(face, buf)
    x, glyphs = 0, []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        glyphs.append((face.glyph_to_string(info.codepoint), x + pos.x_offset, pos.y_offset))
        x += pos.x_advance
    bp = BoundsPen(gs)
    for name, gx, gy in glyphs:
        gs[name].draw(TransformPen(bp, (1, 0, 0, 1, gx, gy)))
    x0, y0, x1, y1 = bp.bounds
    s = size / upm
    w, h = (x1 - x0) * s + 2 * pad, (y1 - y0) * s + 2 * pad
    paths = []
    for name, gx, gy in glyphs:
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (s, 0, 0, -s, pad + (gx - x0) * s, pad + (y1 - gy) * s)))
        paths.append(f"<path fill='{fill}' d='{pen.getCommands()}'/>")
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w:.0f}' height='{h:.0f}'>"
           f"<rect width='100%' height='100%' rx='10' fill='{bg}'/>{''.join(paths)}</svg>")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=out)


if __name__ == "__main__":
    font, outdir = sys.argv[1:3]
    for key, cps, _ in HONORIFICS:
        render(font, chr(int(cps, 16)), 96, f"{outdir}/h-{key}.png")
    for key, (text, size) in SAMPLES.items():
        render(font, text, size, f"{outdir}/{key}.png")
