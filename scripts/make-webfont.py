#!/usr/bin/env python3
"""Make the app and web versions of the font.

    python3 scripts/make-webfont.py fonts/ttf/GulzarNastaliq-Regular.ttf fonts/webfonts

Writes a TTF for apps (iOS, Android) and a WOFF2 for websites. Both drop
what a device never uses: the lookup-name debugging table the build adds
and the glyph-name list (post table version 3). Nothing that affects
shaping or rendering is changed; the font still passes OTS, which Chrome,
Firefox and Android WebView apply to downloaded fonts.
"""
import os
import sys

from fontTools.ttLib import TTFont


def main(src, outdir):
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    font = TTFont(src)
    if "Debg" in font:
        del font["Debg"]
    font["post"].formatType = 3.0
    ttf = os.path.join(outdir, base + ".ttf")
    font.save(ttf)
    font = TTFont(ttf)
    font.flavor = "woff2"
    woff2 = os.path.join(outdir, base + ".woff2")
    font.save(woff2)
    for path in (src, ttf, woff2):
        print(f"{path}: {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
