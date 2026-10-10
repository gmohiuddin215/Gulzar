#!/usr/bin/env python3
"""Run the shaping tests in qa/shaping_tests directly and summarise them.

This uses the same building blocks as fontbakery's shaping checks
(stringbrewer for pattern expansion, collidoscope for collision detection)
but prints plain counts, which makes it easy to compare two builds:

    python3 qa/shaping-report.py fonts/ttf/GulzarNastaliq-Regular.ttf [-o failures.txt]
"""
import argparse
import glob
import json
import os
import sys
from multiprocessing import Pool

import uharfbuzz as hb
from collidoscope import Collidoscope
from stringbrewer import StringBrewer

FONT = None
FONTDATA = None
COLS = {}


def _init(path):
    global FONT, FONTDATA
    FONTDATA = open(path, "rb").read()
    FONT = hb.Font(hb.Face(FONTDATA))


def _collidoscope(path, config):
    key = json.dumps(config, sort_keys=True)
    if key not in COLS:
        COLS[key] = Collidoscope(path, config, direction="RTL")
    return COLS[key]


def serialize(text):
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(FONT, buf)
    # Same format as `hb-shape`: name=cluster@xoffset,yoffset+advance
    items = []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        item = f"{FONT.glyph_to_string(info.codepoint)}={info.cluster}"
        if pos.x_offset or pos.y_offset:
            item += f"@{pos.x_offset},{pos.y_offset}"
        items.append(item + f"+{pos.x_advance}")
    return buf, "|".join(items)


def run_test(args):
    path, filename, configuration, test = args
    defaults = configuration.get("defaults", {})
    allowed = test.get("allowedcollisions", defaults.get("allowedcollisions", []))
    failures = []
    if test.get("input_type", defaults.get("input_type")) == "pattern":
        strings = StringBrewer(
            recipe=test["input"], ingredients=configuration["ingredients"]
        ).generate_all()
    else:
        strings = [test["input"]]
    colconf = test.get("collidoscope", configuration.get("collidoscope"))
    for text in strings:
        buf, serialized = serialize(text)
        if "expectation" in test:
            expected = test["expectation"]
            got = serialized.strip("[]")
            if "=" not in expected:
                got = "|".join(g.split("=")[0] for g in got.split("|"))
            if got != expected:
                failures.append(("regression", filename, text, expected, got))
        if colconf:
            col = _collidoscope(path, colconf)
            glyphs = col.get_glyphs(text, buf=buf)
            bumps = [f"{c.glyph1}/{c.glyph2}" for c in col.has_collisions(glyphs)]
            bumps = [b for b in bumps if b not in allowed]
            if bumps:
                failures.append(("collision", filename, text, " ".join(bumps), ""))
    return len(strings), failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("font")
    parser.add_argument("-d", "--directory", default="qa/shaping_tests")
    parser.add_argument("-o", "--output")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()
    path = os.path.abspath(args.font)

    jobs = []
    for filename in sorted(glob.glob(os.path.join(args.directory, "*.json"))):
        doc = json.load(open(filename))
        for test in doc["tests"]:
            jobs.append((path, os.path.basename(filename), doc.get("configuration", {}), test))

    totals = {}
    allfailures = []
    with Pool(args.jobs, initializer=_init, initargs=(path,)) as pool:
        for (filename, (count, failures)) in zip(
            [j[1] for j in jobs], pool.imap(run_test, jobs, chunksize=8)
        ):
            t = totals.setdefault(filename, {"strings": 0, "collision": 0, "regression": 0})
            t["strings"] += count
            for f in failures:
                t[f[0]] += 1
            allfailures.extend(failures)

    for filename, t in totals.items():
        print(f"{filename:24} strings {t['strings']:7d}  collisions {t['collision']:5d}  regressions {t['regression']:4d}")
    print(f"{'TOTAL':24} strings {sum(t['strings'] for t in totals.values()):7d}  "
          f"collisions {sum(t['collision'] for t in totals.values()):5d}  "
          f"regressions {sum(t['regression'] for t in totals.values()):4d}")
    if args.output:
        with open(args.output, "w") as fh:
            for f in allfailures:
                fh.write("\t".join(f) + "\n")


if __name__ == "__main__":
    sys.exit(main())
