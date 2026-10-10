#!/usr/bin/env python3
"""Reshape Gulzar towards a more calligraphic Nastaliq.

The parameters below come from a study of the proportions of a traditional
Noori-style Nastaliq: letter widths, heights, pen contrast and word spacing
were measured in nuqta (dot) units and compared with Gulzar's. No outlines
are taken from any other font: every change here is a geometric transform
of Gulzar's own outlines (scaling, shearing, directional stroke weighting).

Run once on the source:

    python3 scripts/nastaliq-reshape.py sources/Gulzar.glyphs

Each step records itself in the font's userData and is skipped if it has
already been applied, so running the script twice is harmless.
"""
import argparse
import json
import math
import os
import re
import sys

import glyphsLib
from glyphsLib.classes import Point

STAMP = "org.gulzar.nastaliqReshape"

# ---------------------------------------------------------------------------
# Step 1: scale, spacing
# ---------------------------------------------------------------------------

# Gulzar's letters are ~20% larger per em than in traditional Nastaliq book
# faces (median of bowl/stem sizes measured against the em). Raising the
# UPM scales the whole design down without touching any coordinates, so the
# unit-based shaping rules (kerning distances, dot drops...) stay valid.
NEW_UPM = 1250

# Gap between words. Traditional setting puts words almost touching
# (median gap ~0.5 nuqta, often tucked under each other); Gulzar's was ~1.4.
URDU_SPACE_WIDTH = 80


def step_scale_and_spacing(font):
    font.upm = NEW_UPM
    font.glyphs["space.urdu"].layers[0].width = URDU_SPACE_WIDTH


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

RASM = re.compile(r"^([A-Z_]+?)(isd|msd|i|m|f|u)(\d+)$")


def master_layer(font, glyph):
    mid = font.masters[0].id
    return next(l for l in glyph.layers if l.layerId == mid)


def contour_points(path):
    return [(n.position.x, n.position.y) for n in path.nodes]


def signed_area(pts):
    a = 0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        a += x0 * y1 - x1 * y0
    return a / 2


def point_in_polygon(x, y, pts):
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if (y0 > y) != (y1 > y):
            if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
    return inside


def outward_normals(layer):
    """For every on-curve node, the unit normal pointing away from the ink."""
    polys = [contour_points(p) for p in layer.paths]
    result = []
    for pi, path in enumerate(layer.paths):
        pts = polys[pi]
        ccw = signed_area(pts) > 0
        is_hole = any(
            point_in_polygon(pts[0][0], pts[0][1], other)
            for oi, other in enumerate(polys)
            if oi != pi
        )
        # (ty, -tx) is the right-hand side of the direction of travel;
        # that is outside the ink for CCW outers and CW holes.
        flip = -1 if ccw == is_hole else 1
        n = len(pts)
        normals = []
        for i, node in enumerate(path.nodes):
            if node.type == "offcurve":
                normals.append(None)
                continue
            px, py = pts[(i - 1) % n]
            nx_, ny_ = pts[(i + 1) % n]
            x, y = pts[i]
            tin = (x - px, y - py)
            tout = (nx_ - x, ny_ - y)
            acc = [0.0, 0.0]
            for tx, ty in (tin, tout):
                length = math.hypot(tx, ty)
                if length:
                    acc[0] += ty / length
                    acc[1] += -tx / length
            length = math.hypot(*acc) or 1
            normals.append((flip * acc[0] / length, flip * acc[1] / length))
        result.append(normals)
    return result


def displace_nodes(layer, fn):
    """Move each on-curve node by fn(normal); off-curve handles follow the
    on-curve node they belong to, so tangents are preserved."""
    all_normals = outward_normals(layer)
    for path, normals in zip(layer.paths, all_normals):
        nodes = path.nodes
        n = len(nodes)
        deltas = [None] * n
        for i, nrm in enumerate(normals):
            if nrm is not None:
                deltas[i] = fn(nrm)
        for i in range(n):
            if deltas[i] is not None:
                continue
            # off-curve: belongs to the previous on-curve if that is adjacent
            # (outgoing handle), else to the next one (incoming handle)
            if nodes[(i - 1) % n].type != "offcurve":
                deltas[i] = deltas[(i - 1) % n]
            else:
                j = (i + 1) % n
                while deltas[j] is None and nodes[j].type == "offcurve":
                    j = (j + 1) % n
                deltas[i] = deltas[j] if deltas[j] is not None else (0, 0)
        for node, (dx, dy) in zip(nodes, deltas):
            node.position = Point(node.position.x + dx, node.position.y + dy)


def embolden_along(layer, angle_deg, amount, sharpness=3.0):
    """Thicken strokes by Minkowski-like offsetting along one direction.

    A stroke whose outline normal is parallel to `angle_deg` gains 2*amount
    of thickness; one whose normal is perpendicular is unchanged. tanh()
    smooths the switch-over so that curves stay fair."""
    ux, uy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))

    def fn(nrm):
        dot = nrm[0] * ux + nrm[1] * uy
        k = amount * math.tanh(sharpness * dot)
        return (k * ux, k * uy)

    displace_nodes(layer, fn)


def layer_bounds(layer):
    xs = [n.position.x for p in layer.paths for n in p.nodes]
    return (min(xs), max(xs)) if xs else None


def anchor(layer, name):
    for a in layer.anchors:
        if a.name == name:
            return a
    return None


def decompose_components(font, layer):
    """Replace components with plain outlines (using untransformed geometry)."""
    if not layer.components:
        return
    from glyphsLib.classes import GSPath, GSNode

    for comp in list(layer.components):
        base = font.glyphs[comp.name]
        blayer = master_layer(font, base)
        decompose_components(font, blayer)
        a, b, c, d, tx, ty = comp.transform
        for path in blayer.paths:
            new = GSPath()
            new.closed = path.closed
            for node in path.nodes:
                x, y = node.position.x, node.position.y
                new.nodes.append(
                    GSNode(Point(a * x + c * y + tx, b * x + d * y + ty), node.type, node.smooth)
                )
            layer.paths.append(new)
    for comp in list(layer.components):
        layer.components.remove(comp)


# ---------------------------------------------------------------------------
# Step 2: proportions of joining and final forms
# ---------------------------------------------------------------------------

# Horizontal scale per (family, form). Values are 60% of the measured
# difference between Gulzar and the reference proportions (in nuqta units),
# capped at +/-20%. Joining forms get narrower; final and isolated forms
# get the generous, sweeping width typical of Nastaliq.
# KAF/GAF are handled in step 3 (squeezing them would steepen the sarkash).
# BARI_YE is left alone: its width drives the bari-ye overhang machinery.
# NUN and QAF keep their width: wider bowls look stretched.
# DAL, VAO, RE and do-chashmi HE finals are also wider in the reference, but
# widening them pushes them into the dots of the following letter.
WIDTH_SCALES = {
    ("BE", "i"): 0.92, ("TE", "i"): 0.92,
    ("BE", "isd"): 0.92, ("TE", "isd"): 0.92,
    ("SIN", "i"): 0.84, ("SIN", "m"): 0.88,
    ("SIN", "isd"): 0.84, ("SIN", "msd"): 0.88,
    ("MIM", "i"): 0.80, ("MIM", "m"): 0.87,
    ("HAYC", "i"): 0.92, ("HAYC", "m"): 0.92,
    ("JIM", "i"): 0.90, ("JIM", "m"): 0.84,
    ("FE", "i"): 0.84, ("FE", "m"): 0.89,
    ("AIN", "i"): 0.87,
    ("LAM", "m"): 0.93,
    ("SAD", "m"): 0.93,
    ("BE", "f"): 1.04, ("TE", "f"): 1.04,
    ("FE", "f"): 1.10, ("FE", "u"): 1.03,
    ("TOE", "f"): 1.04,
}

# Typical vertical stem thickness, used to keep stems at their original
# weight after horizontal scaling.
STEM = 80


def width_scale_for(name):
    m = RASM.match(name)
    if not m:
        return None
    return WIDTH_SCALES.get((m.group(1), m.group(2)))


def scale_origin(layer):
    """The x coordinate which must not move: the connection point."""
    ex, en = anchor(layer, "exit"), anchor(layer, "entry")
    if ex is not None:
        return ex.position.x  # initial/medial: keep the join to the left
    if en is not None:
        return en.position.x  # final: keep the join to the right
    b = layer_bounds(layer)
    return (b[0] + b[1]) / 2 if b else 0


def hscale_layer(layer, s, x0, shift=0, fixed_anchors=()):
    for path in layer.paths:
        for node in path.nodes:
            node.position = Point(x0 + (node.position.x - x0) * s + shift, node.position.y)
    for a in layer.anchors:
        if a.name in fixed_anchors:
            # move only half as far as the outline does
            sa = (1 + s) / 2
            a.position = Point(x0 + (a.position.x - x0) * sa + shift, a.position.y)
        else:
            a.position = Point(x0 + (a.position.x - x0) * s + shift, a.position.y)


# When a joining form is narrowed around its exit (left) join, its dots below
# move only half as far as the outline. Fully scaled, they would slide into
# the following letter (a jim head, mim or do-chashmi he); fully fixed, they
# would stick out to the right into the previous letter.
FIXED_WHEN_NARROWED = ("bottom",)


def step_proportions(font):
    plan = {}
    for g in font.glyphs:
        s = width_scale_for(g.name)
        if s:
            plan[g.name] = s
    # Pass 1: unpack components whose base glyph is not scaled identically.
    for name, s in plan.items():
        layer = master_layer(font, font.glyphs[name])
        if any(plan.get(c.name) != s for c in layer.components):
            decompose_components(font, layer)
    # Pass 2: compute each glyph's transform from its pristine geometry.
    transforms = {}
    for name, s in plan.items():
        layer = master_layer(font, font.glyphs[name])
        x0 = scale_origin(layer)
        b = layer_bounds(layer)
        if b is None:  # pure composite: use the component's extent
            comp = layer.components[0]
            bb = layer_bounds(master_layer(font, font.glyphs[comp.name]))
            b = (bb[0] + comp.transform[4], bb[1] + comp.transform[4]) if bb else (0, layer.width)
        is_init_or_medi = anchor(layer, "exit") is not None
        if is_init_or_medi:
            shift = 0  # left join fixed; right side bearing preserved below
        else:
            # keep the left side bearing (the gap before the next word)
            shift = b[0] - (x0 + (b[0] - x0) * s)
        transforms[name] = (s, x0, shift, b)
    # Pass 3: apply.
    for name, (s, x0, shift, b) in transforms.items():
        layer = master_layer(font, font.glyphs[name])
        old_width = layer.width
        en = anchor(layer, "entry")
        width_at_entry = en is not None and abs(en.position.x - old_width) < 2
        narrowed_joining = s < 1 and anchor(layer, "exit") is not None
        hscale_layer(layer, s, x0, shift, FIXED_WHEN_NARROWED if narrowed_joining else ())
        for comp in layer.components:
            cs, cx0, cshift, _ = transforms[comp.name]
            a, bb, c, d, tx, ty = comp.transform
            # T_glyph(o + p) = o' + T_comp(p)  =>  o' = s*o + (1-s)*x0 + shift - (1-s)*cx0 - cshift
            ntx = s * tx + (1 - s) * x0 + shift - (1 - cs) * cx0 - cshift
            comp.transform = (a, bb, c, d, ntx, ty)
        # Advance width: medials end at their entry; initials and isolates
        # keep their right side bearing; finals keep it relative to entry.
        right = b[1]
        new_right = x0 + (right - x0) * s + shift
        layer.width = round(new_right + (old_width - right))
        if width_at_entry:
            layer.width = round(en.position.x)
        if layer.paths:
            # keep vertical stems at their original weight
            embolden_along(layer, 0, STEM * (1 - s) / 2)
        for path in layer.paths:
            for node in path.nodes:
                node.position = Point(round(node.position.x), round(node.position.y))
        for a in layer.anchors:
            a.position = Point(round(a.position.x), round(a.position.y))


# ---------------------------------------------------------------------------
# Step 2b: bowls sit on the line
# ---------------------------------------------------------------------------

# In traditional Nastaliq the final bowls (noon, seen, laam, ain, ye...) dip
# only ~0.9 nuqta below the baseline, so a line of words sits evenly on the
# line; Gulzar's bowls hang ~2 nuqta below. For each family, the measured
# height (in nuqta) of the bottom of the bowl and of the entry join in the
# reference; each bowl is lifted, and if needed made a little shallower, to
# meet both. The shallowing is an even vertical scale of the whole bowl
# below the join (never more than 15%), so the bowl stays round instead of
# getting a flat bottom.
NUQTA = 176  # width of Gulzar's original dot, in font units
BOWLS = {
    #        bottom  entry (None: lift only, keeping the bowl's own depth)
    "NUN":   (-0.89, None),
    "QAF":   (-0.91, None),
    "SIN":   (-0.82, None),
    "LAM":   (-0.91, None),
    "AIN":   (-0.82, None),
    "CH_YE": (-0.85, None),
    "JIM":   (-1.05, None),
    "SAD":   (-0.88, None),
    "MIM":   (-2.59, None),
}
MAX_BOWL_SQUASH = 0.15


def outline_segments(layer, steps=8):
    """Polygonised outline, as a list of line segments."""
    segs = []
    for path in layer.paths:
        nodes = path.nodes
        n = len(nodes)
        start = next(i for i, nd in enumerate(nodes) if nd.type != "offcurve")
        i = start
        pts = []
        while True:
            j = (i + 1) % n
            ctrl = []
            while nodes[j].type == "offcurve":
                ctrl.append(nodes[j].position)
                j = (j + 1) % n
            p0, p1 = nodes[i].position, nodes[j].position
            if len(ctrl) == 2:
                c1, c2 = ctrl
                for k in range(steps):
                    t = k / steps
                    mt = 1 - t
                    pts.append((
                        mt**3 * p0.x + 3 * mt * mt * t * c1.x + 3 * mt * t * t * c2.x + t**3 * p1.x,
                        mt**3 * p0.y + 3 * mt * mt * t * c1.y + 3 * mt * t * t * c2.y + t**3 * p1.y,
                    ))
            else:
                pts.append((p0.x, p0.y))
            i = j
            if i == start:
                break
        segs += list(zip(pts, pts[1:] + pts[:1]))
    return segs


def ray_distance(origin, direction, segs, skip=2.0):
    ox, oy = origin
    dx, dy = direction
    best = None
    for (x0, y0), (x1, y1) in segs:
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-9:
            continue
        t = ((x0 - ox) * ey - (y0 - oy) * ex) / den
        u = ((x0 - ox) * dy - (y0 - oy) * dx) / den
        if t > skip and 0 <= u <= 1 and (best is None or t < best):
            best = t
    return best


def warp_strokes(layer, field, max_thickness=260):
    """Move every stroke by the displacement `field(x, y)` evaluated at the
    stroke's centre line (found by casting a ray across the stroke), so the
    stroke keeps its thickness however the field bends it."""
    segs = outline_segments(layer)

    def fn_for(pos):
        def fn(nrm):
            t = ray_distance(pos, (-nrm[0], -nrm[1]), segs)
            t = min(t if t else max_thickness, max_thickness)
            cx, cy = pos[0] - nrm[0] * t / 2, pos[1] - nrm[1] * t / 2
            return field(cx, cy)
        return fn

    all_normals = outward_normals(layer)
    moves = []
    for path, normals in zip(layer.paths, all_normals):
        moves.append([
            fn_for((nd.position.x, nd.position.y))(nrm) if nrm is not None else None
            for nd, nrm in zip(path.nodes, normals)
        ])
    for path, mv in zip(layer.paths, moves):
        nodes = path.nodes
        n = len(nodes)
        for i in range(n):
            if mv[i] is None:
                if nodes[(i - 1) % n].type != "offcurve":
                    mv[i] = mv[(i - 1) % n]
                else:
                    j = (i + 1) % n
                    while mv[j] is None:
                        j = (j + 1) % n
                    mv[i] = mv[j]
        for nd, (dx, dy) in zip(nodes, mv):
            nd.position = Point(round(nd.position.x + dx), round(nd.position.y + dy))
    for a in layer.anchors:
        dx, dy = field(a.position.x, a.position.y)
        a.position = Point(round(a.position.x + dx), round(a.position.y + dy))


def bowl_field(layer, target_bottom, target_entry):
    """Displacement field lifting a final bowl so that its bottom and its
    entry join land at the target heights (font units)."""
    bottom = min(min(p[0][1] for p in outline_segments(layer)), 0)
    entry = anchor(layer, "entry").position.y
    span = entry - bottom
    if target_entry is None:  # lift only
        target_entry = entry + (target_bottom - bottom)
    # bottom' = bottom + D + R, entry' = entry + D
    R = (target_bottom - bottom) - (target_entry - entry)
    R = max(0.0, min(R, MAX_BOWL_SQUASH * span))
    D = target_bottom - bottom - R

    def field(x, y):
        if y >= entry:
            return (0, D)
        return (0, D + R * (entry - y) / span)

    return field


def step_bowls(font):
    targets = []
    for g in font.glyphs:
        m = RASM.match(g.name)
        if m and m.group(1) in BOWLS and m.group(2) in ("f", "u"):
            targets.append((g.name, m.group(1)))
    for name, _ in targets:
        decompose_components(font, master_layer(font, font.glyphs[name]))
    # The field is worked out on the final form (which has the entry join)
    # and applied unchanged to the isolated form, which has the same bowl.
    fields = {}
    for family in {fam for _, fam in targets}:
        final = font.glyphs[family + "f1"]
        tb, te = BOWLS[family]
        fields[family] = bowl_field(
            master_layer(font, final), tb * NUQTA, None if te is None else te * NUQTA
        )
    for name, family in targets:
        warp_strokes(master_layer(font, font.glyphs[name]), fields[family])


# ---------------------------------------------------------------------------
# Step 3a: kaf / gaf sarkash
# ---------------------------------------------------------------------------

# The sarkash (the top stroke of kaf and gaf) is drawn in Nastaliq as a long,
# low diagonal, at about 28 degrees. Gulzar's lies at about 40 degrees. It is rotated rigidly about the point where it leaves the
# upright stem, easing in over the first part so the joint stays smooth.
SARKASH_ANGLE = 28
SARKASH_MAX_TURN = 10
# Compact forms with a steep sarkash are designed to clear the dots of the
# letter before; they keep their angle.
SARKASH_KEEP_ABOVE = 48


def find_sarkash(layer):
    """Pivot and angle of the sarkash: the two longest straight edges rising
    to the right are its upper and lower edges."""
    edges = []
    for path in layer.paths:
        nodes = path.nodes
        n = len(nodes)
        for i, nd in enumerate(nodes):
            nxt = nodes[(i + 1) % n]
            if nd.type == "offcurve" or nxt.type != "line":
                continue
            dx, dy = nxt.position.x - nd.position.x, nxt.position.y - nd.position.y
            if dx < 0:
                dx, dy = -dx, -dy
                lo, hi = nxt.position, nd.position
            else:
                lo, hi = nd.position, nxt.position
            angle = math.degrees(math.atan2(dy, dx))
            if 20 < angle < 75:
                edges.append((math.hypot(dx, dy), lo, hi, angle))
    edges.sort(key=lambda e: -e[0])
    if len(edges) < 2 or edges[1][0] < 150:
        return None
    (_, lo1, hi1, a1), (_, lo2, hi2, a2) = edges[:2]
    pivot = ((lo1.x + lo2.x) / 2, (lo1.y + lo2.y) / 2)
    tip = ((hi1.x + hi2.x) / 2, (hi1.y + hi2.y) / 2)
    return pivot, tip, (a1 + a2) / 2


def rotation_field(pivot, tip, turn_deg):
    px, py = pivot
    ux, uy = tip[0] - px, tip[1] - py
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length

    def field(x, y):
        t = ((x - px) * ux + (y - py) * uy) / length
        if t <= 0:
            return (0, 0)
        w = min(t / 0.2, 1.0)
        w = w * w * (3 - 2 * w)  # smoothstep
        a = math.radians(turn_deg * w)
        rx = px + (x - px) * math.cos(a) - (y - py) * math.sin(a)
        ry = py + (x - px) * math.sin(a) + (y - py) * math.cos(a)
        return (rx - x, ry - y)

    return field


def step_sarkash(font):
    # Medial forms sit inside tight letter groups, where a lower sarkash runs
    # into the dots of the letter before; they keep their angle.
    family = [g.name for g in font.glyphs if re.match(r"^[KG]AF(i|f|u|isd)\d+$", g.name)]
    pristine = {}
    for name in family:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
    plans = {}
    for name in family:
        if name.startswith("KAF"):
            found = find_sarkash(master_layer(font, font.glyphs[name]))
            if found:
                plans[name] = found
    for name in family:
        layer = master_layer(font, font.glyphs[name])
        found = plans.get("KAF" + name[3:]) or find_sarkash(layer)
        if not found:
            print(f"  {name}: no sarkash found, left alone")
            continue
        pivot, tip, angle = found
        if angle > SARKASH_KEEP_ABOVE:
            continue
        turn = -min(SARKASH_MAX_TURN, max(0, angle - SARKASH_ANGLE))
        if turn:
            warp_strokes(layer, rotation_field(pivot, tip, turn))


# The sarkash is also shorter in calligraphic Nastaliq: its tip is ~0.85 of
# Gulzar's distance from the stem (تک, تکبر, لگا). It is shortened along its
# own axis, easing in from the stem; the pen-cut head keeps its shape.
SARKASH_LENGTH = 0.86


def length_field(pivot, tip, scale):
    px, py = pivot
    ux, uy = tip[0] - px, tip[1] - py
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length

    def field(x, y):
        t = ((x - px) * ux + (y - py) * uy) / length
        if t <= 0:
            return (0, 0)
        # smooth ramp from the stem, then uniform; the head moves rigidly
        g = t * t / 0.4 if t < 0.2 else min(t, 1.0) - 0.1
        d = -(1 - scale) * length * g / 0.9
        return (d * ux, d * uy)

    return field


def step_sarkash_length(font):
    family = [g.name for g in font.glyphs if re.match(r"^[KG]AF(i|m|f|u|isd|msd|fs|us)\d+$", g.name)]
    plans = {}
    for name in family:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        if name.startswith("KAF"):
            plans[name] = find_sarkash(layer)
    for name in family:
        layer = master_layer(font, font.glyphs[name])
        found = plans.get("KAF" + name[3:]) or find_sarkash(layer)
        if not found:
            print(f"  {name}: no sarkash found, left alone")
            continue
        pivot, tip, _ = found
        warp_strokes(layer, length_field(pivot, tip, SARKASH_LENGTH))


# ---------------------------------------------------------------------------
# Step 3i: final kaf entry
# ---------------------------------------------------------------------------

# In تک the te sits on the hook of the final kaf, level with the kaf's base;
# calligraphers join it a little higher. The hook (and so the letters before
# it) is lifted, fading out into the body and up the upright of the kaf.
# The spacing rules group letters by the height of their joins, and were
# tuned to Gulzar's; the build keeps grouping these by their original height
# (see sources/rise-pins.json, read by sitecustomize.py).
FINAL_KAF_LIFT = 40
FINAL_KAFS = ("KAFf1", "GAFf1", "KAFfs1", "GAFfs1")
RISE_PINS = {}


def step_final_kaf_entry(font):
    for name in FINAL_KAFS:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        en = anchor(layer, "entry").position
        RISE_PINS[name] = en.y
        ex, ey = en.x, en.y

        def field(x, y, ex=ex, ey=ey):
            u = min(max((x - (ex - 205)) / 120, 0.0), 1.0)
            v = min(max(1 - (y - (ey + 40)) / 160, 0.0), 1.0)
            w = u * u * (3 - 2 * u) * v * v * (3 - 2 * v)
            return (0, FINAL_KAF_LIFT * w)

        warp_strokes(layer, field)


# ---------------------------------------------------------------------------
# Step 3j: isolated kaf and gaf
# ---------------------------------------------------------------------------

# The base of isolated kaf/gaf is short for Nastaliq: it runs ~3.1 nuqta to
# the left of the upright, against ~4.4 in the calligraphic form. It is
# lengthened most of the way (to ~4.2); the rest of the letter keeps its
# shape. Its sarkash is also a little long, and is shortened along its own
# direction (the second stroke of gaf follows it).
ISOLATED_KAF_BASE = 1.35
ISOLATED_KAF_SARKASH = 0.90


def step_isolated_kaf(font):
    plan = None
    for name in ("KAFu1", "GAFu1"):
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        xmin, xmax = layer_bounds(layer)
        plan = plan or find_sarkash(layer)
        pivot, tip, _ = plan
        warp_strokes(layer, length_field(pivot, tip, ISOLATED_KAF_SARKASH))
        # left side of the upright, measured on the stem above the base
        spans = []
        for path in layer.paths:
            pts = contour_points(path)
            n = len(pts)
            for i in range(n):
                (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
                y = 2.5 * NUQTA
                if (y1 <= y < y2) or (y2 <= y < y1):
                    spans.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xl = min(spans)
        s = ISOLATED_KAF_BASE
        warp_strokes(layer, lambda x, y, s=s, xl=xl: (-(s - 1) * (xl - x), 0) if x < xl else (0, 0))
        nmin, _ = layer_bounds(layer)
        dx = round(xmin - nmin)
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x + dx, nd.position.y)
        for a in layer.anchors:
            a.position = Point(a.position.x + dx, a.position.y)
        layer.width = layer.width + dx


# ---------------------------------------------------------------------------
# Step 3k: tilt of hamza
# ---------------------------------------------------------------------------

# The isolated hamza leans a little more in the calligraphic form and dips
# below the line. It is turned anticlockwise about its centre (a rigid turn,
# so its shape and weight are unchanged), set back on its left edge and
# lowered. Its upper tip ends in a straight cut with a tiny cap (from the
# tips step); the stroke-end step folds the cap into the cut and rounds it.
TILTS = {"HAMZAu1": 8}  # degrees, anticlockwise
LOWER = {"HAMZAu1": 60}  # font units


def fold_tiny_caps(layer, size=15):
    for path in layer.paths:
        nodes = list(path.nodes)
        i = 0
        while i < len(nodes):
            n = len(nodes)
            a, h1, h2, c = (nodes[(i + k) % n] for k in range(4))
            if (a.type == "line" and h1.type == h2.type == "offcurve" and c.type == "curve"
                    and math.hypot(c.position.x - a.position.x, c.position.y - a.position.y) < size):
                c.type = "line"
                for k in sorted({i % n, (i + 1) % n, (i + 2) % n}, reverse=True):
                    del nodes[k]
                continue
            i += 1
        path.nodes = nodes


def step_tilts(font):
    for name, deg in TILTS.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        pts = [nd.position for p in layer.paths for nd in p.nodes]
        x0, x1 = min(p.x for p in pts), max(p.x for p in pts)
        y0, y1 = min(p.y for p in pts), max(p.y for p in pts)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        for path in layer.paths:
            for nd in path.nodes:
                x, y = nd.position.x - cx, nd.position.y - cy
                nd.position = Point(cx + x * ca - y * sa, cy + x * sa + y * ca)
        pts = [nd.position for p in layer.paths for nd in p.nodes]
        dx = x0 - min(p.x for p in pts)
        dy = y0 - min(p.y for p in pts) - LOWER.get(name, 0)
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(round(nd.position.x + dx), round(nd.position.y + dy))


# ---------------------------------------------------------------------------
# Step 3l: dots of isolated tcheh and khah
# ---------------------------------------------------------------------------

# In the isolated چ the three dots sit low in the bowl, and the dot of خ
# sits low over the head; both are raised a little. Only these two
# precomposed letters change. Font units.
COMPONENT_SHIFTS = {
    "tcheh-ar": ("tdi", 0, 75),
    "khah-ar": ("sda", 0, 75),
}


def step_component_shifts(font):
    for name, (comp, dx, dy) in COMPONENT_SHIFTS.items():
        layer = master_layer(font, font.glyphs[name])
        for c in layer.components:
            if c.name == comp:
                c.position = Point(c.position.x + dx, c.position.y + dy)


# ---------------------------------------------------------------------------
# Step 3m: tail of isolated re
# ---------------------------------------------------------------------------

# The tail of the isolated re (and so of ڑ ز ژ, which are built on it) is
# short for Nastaliq. The part left of the neck is stretched horizontally,
# keeping its thickness, so the tail runs further along the line.
# The final re of کر, پر gets the same longer tail. Stretch, and the x
# where the neck turns into the tail (None: just right of the bottom anchor).
RE_TAIL = {"REu1": (1.25, None), "REf1": (1.25, 260)}


def step_re_tail(font):
    for name, (s, bend) in RE_TAIL.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        xmin, _ = layer_bounds(layer)
        xb = bend if bend is not None else anchor(layer, "bottom").position.x + 40
        warp_strokes(layer, lambda x, y, s=s, xb=xb: (-(s - 1) * (xb - x), 0) if x < xb else (0, 0))
        nmin, _ = layer_bounds(layer)
        dx = round(xmin - nmin)
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x + dx, nd.position.y)
        for a in layer.anchors:
            a.position = Point(a.position.x + dx, a.position.y)
        layer.width = layer.width + dx
        # the dots of ڑ ز ژ move with the letter
        for g in font.glyphs:
            lay = master_layer(font, g)
            if any(c.name == name for c in lay.components):
                for c in lay.components:
                    if c.name != name:
                        c.position = Point(c.position.x + dx, c.position.y)
                lay.width = lay.width + dx


# ---------------------------------------------------------------------------
# Step 3n: do-chashmi he
# ---------------------------------------------------------------------------

# Do-chashmi he is small in every form (isolated 2.8 x 1.9 nuqta, against
# ~3.2 x 2.3 in the calligraphic form). Each drawn form (the isolated one and
# the initial ones, on which the final and medial forms are built) is
# enlarged about its join on the line, moving the strokes rather than
# scaling them so the pen weight and shape are unchanged. The glyphs built
# from them get their joins moved to match. Growing a joined letter raises
# the join it offers the letter before it; the build keeps grouping these
# glyphs by their original join height (see RISE_PINS).
HAYA_SCALE = 1.12


def step_haya_size(font):
    s = HAYA_SCALE
    names = ["HAYAu1"] + [g.name for g in font.glyphs if re.match(r"^HAYAi\d+$", g.name)]
    for name in names:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        ex, en = anchor(layer, "exit"), anchor(layer, "entry")
        ox = ex.position.x if ex is not None else en.position.x

        def field(x, y, ox=ox):
            return ((s - 1) * (x - ox), (s - 1) * y)

        xmin, _ = layer_bounds(layer)
        old = {a.name: (a.position.x, a.position.y) for a in layer.anchors}
        warp_strokes(layer, field)
        nmin, _ = layer_bounds(layer)
        dx = round(xmin - nmin)
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x + dx, nd.position.y)
        for a in layer.anchors:
            a.position = Point(a.position.x + dx, a.position.y)
        layer.width += dx
        if en is not None:
            RISE_PINS[name] = round(old["entry"][1] - (old["exit"][1] if "exit" in old else 0))
        # glyphs built on this one (final and medial forms)
        for g in font.glyphs:
            lay = master_layer(font, g)
            if not any(c.name == name for c in lay.components):
                continue
            for a in lay.anchors:
                fx, fy = field(a.position.x, a.position.y)
                if a.name in ("entry", "exit"):
                    if a.name == "entry":
                        rise = a.position.y - (anchor(lay, "exit").position.y if anchor(lay, "exit") else 0)
                        RISE_PINS[g.name] = round(rise)
                a.position = Point(round(a.position.x + fx + dx), round(a.position.y + fy))
            lay.width += dx


# ---------------------------------------------------------------------------
# Step 3b: tall letters
# ---------------------------------------------------------------------------

# Alif is ~4.2 nuqta tall in traditional Nastaliq; Gulzar's is ~3.5, and its
# laam is short by a similar amount. Short ascenders make the strokes look
# heavy for the size of the letters. The stem is stretched in its middle, so
# the pen-cut head and the stroke width are unchanged. Values in nuqta.
# Initial and medial laam are kept short of the kaf sarkash (as in لیکن).
ASCENDERS = {
    ("ALIF", "u"): 0.50, ("ALIF", "f"): 0.30,
    ("LAM", "i"): 0.30, ("LAM", "isd"): 0.30, ("LAM", "m"): 0.30,
    ("LAM", "f"): 0.23, ("LAM", "u"): 0.23,
    ("LAM_ALIF", "u"): 0.50, ("LAM_ALIF", "f"): 0.50,
}
# Individual forms which need a different stretch. The laams which stand
# under a following kaf/gaf sarkash (لیکن LAMi2, لک/لکھا/الگ LAMi3, لگا
# LAMi8, ملک/بلکہ LAMm3) all get the same, shorter height, leaving ~0.6
# nuqta under the sarkash. LAMi13 (تعالیٰ, before a final ye) is lifted with
# the ye and rose well above the alif.
ASCENDER_OVERRIDES = {
    "LAMi2": -0.35, "LAMi3": -0.35, "LAMi8": -0.35, "LAMm3": -0.35,
    "LAMi13": -1.2,
}


def step_ascenders(font):
    targets = []
    for g in font.glyphs:
        m = RASM.match(g.name)
        if m and (m.group(1), m.group(2)) in ASCENDERS:
            amount = ASCENDER_OVERRIDES.get(g.name, ASCENDERS[(m.group(1), m.group(2))])
            if amount:
                targets.append((g.name, amount))
    for name, _ in targets:
        decompose_components(font, master_layer(font, font.glyphs[name]))
    for name, amount in targets:
        layer = master_layer(font, font.glyphs[name])
        ys = [nd.position.y for p in layer.paths for nd in p.nodes]
        top = max(ys)
        y0 = 0.35 * top  # stretch between 35% and 65% of the height
        y1 = 0.65 * top
        D = amount * NUQTA
        # For qaf (if listed) only the neck (the right part of the letter)
        # is stretched, not the left arm of the bowl. Measured without its
        # dots, the calligraphic qaf is no taller than Gulzar's own, so it
        # is currently not stretched.
        xs = [nd.position.x for p in layer.paths for nd in p.nodes]
        if name.startswith("QAF"):
            xa = min(xs) + 0.5 * (max(xs) - min(xs))
            xb = min(xs) + 0.7 * (max(xs) - min(xs))
        else:
            xa = xb = None

        def field(x, y, y0=y0, y1=y1, D=D, xa=xa, xb=xb):
            if y <= y0:
                return (0, 0)
            w = min((y - y0) / (y1 - y0), 1.0)
            w = w * w * (3 - 2 * w)
            if xa is not None:
                u = min(max((x - xa) / (xb - xa), 0.0), 1.0)
                w *= u * u * (3 - 2 * u)
            return (0, D * w)

        warp_strokes(layer, field)


# ---------------------------------------------------------------------------
# Step 3g: re neck
# ---------------------------------------------------------------------------

# Final and isolated re are 0.4-0.5 nuqta taller in calligraphic Nastaliq;
# Gulzar's look short in the neck (کر, پر, اور). The stroke is lengthened in
# its middle, upwards: the join at the top and the tip of the tail keep
# their shape, and the tail does not reach further down.
RE_NECK = {"REf1": 0.35, "REu1": 0.35}  # nuqta
RE_LIFT = 10  # font units


def step_re_neck(font):
    for name, amount in RE_NECK.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        ys = [nd.position.y for p in layer.paths for nd in p.nodes]
        lo, hi = min(ys), max(ys)
        y0, y1 = lo + 0.3 * (hi - lo), lo + 0.7 * (hi - lo)
        D = amount * NUQTA

        def field(x, y, y0=y0, y1=y1, D=D):
            # everything above the band moves up; the tail stays where it
            # is, clear of the dots of the next letter
            if y <= y0:
                return (0, 0)
            w = min((y - y0) / (y1 - y0), 1.0)
            return (0, D * w * w * (3 - 2 * w))

        warp_strokes(layer, field)
        # the tail tip dipped a few units below the baseline: rest it on it
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x, nd.position.y + RE_LIFT)
        for a in layer.anchors:
            a.position = Point(a.position.x, a.position.y + RE_LIFT)


# ---------------------------------------------------------------------------
# Step 3h: dal size
# ---------------------------------------------------------------------------

# Final dal is ~25% larger in the calligraphic forms (2.8 x 2.7 nuqta
# against 2.3 x 2.1), isolated dal ~12%. The letter is scaled about its
# foot on the baseline, below the join, moving the strokes rather than
# scaling them so the pen weight is unchanged; it is then shifted so the
# gap before the next word stays the same.
DAL_SCALE = {"DALf1": 1.20, "DALu1": 1.10}


def step_dal_size(font):
    for name, s in DAL_SCALE.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        b = layer_bounds(layer)
        en = anchor(layer, "entry")
        x0 = en.position.x if en is not None else b[1]

        def field(x, y, x0=x0, s=s):
            return ((s - 1) * (x - x0), (s - 1) * y)

        warp_strokes(layer, field)
        nb = layer_bounds(layer)
        dx = round(b[0] - nb[0])
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x + dx, nd.position.y)
        for an in layer.anchors:
            an.position = Point(an.position.x + dx, an.position.y)
        layer.width = layer.width + dx + round((s - 1) * (layer.width - x0)) if en is None else layer.width + dx


# ---------------------------------------------------------------------------
# Step 3c: dots of medial be/te/ye
# ---------------------------------------------------------------------------

# The dots under a medial be/te/ye sit a little to the right of where the
# calligrapher puts them, under the tooth. Values in font units.
MEDIAL_DOT_SHIFT = (-35, 30)


def step_medial_dots(font):
    dx, dy = MEDIAL_DOT_SHIFT
    for g in font.glyphs:
        m = RASM.match(g.name)
        if m and m.group(1) in ("BE", "TE") and m.group(2) in ("m", "msd"):
            a = anchor(master_layer(font, g), "bottom")
            if a is not None:
                a.position = Point(a.position.x + dx, a.position.y + dy)


# ---------------------------------------------------------------------------
# Step 3e: dot placement
# ---------------------------------------------------------------------------

# Dot (and other mark) anchors moved towards where a Nastaliq calligrapher
# puts the nuqtas, measured relative to each letter: the dot of sad/dad,
# ain/ghain and toe/zoe sits lower and further into the letter. About
# 60-75% of the measured difference is applied. Font units. (Medial dots
# are not moved further: calligraphers hang them left of the tooth, but in
# common endings like ین that only works with ligatures Gulzar lacks.)
DOT_MOVES = [
    # families, forms, anchor, dx, dy
    (("SAD",), ("i", "m", "f", "u"), "top", -150, -95),
    (("AIN",), ("u", "f"), "top", -60, -130),
    (("AIN",), ("i",), "top", -85, -100),
    (("AIN",), ("m",), "top", -140, 0),
    (("TOE",), ("u", "f"), "top", 0, -105),
    (("TOE",), ("i", "m"), "top", 0, -35),
    (("TE",), ("u", "f"), "top", 0, 40),
]


def step_dot_positions(font):
    for g in font.glyphs:
        m = RASM.match(g.name)
        if not m:
            continue
        for families, forms, name, dx, dy in DOT_MOVES:
            if m.group(1) in families and m.group(2) in forms:
                a = anchor(master_layer(font, g), name)
                if a is not None:
                    a.position = Point(a.position.x + dx, a.position.y + dy)


# ---------------------------------------------------------------------------
# Step 2d: bowl length
# ---------------------------------------------------------------------------

# The bowls of noon, seen/sheen and sad/dad still read a little drawn out
# compared with the calligraphic forms: the part of the bowl to the left of
# its lowest point is shortened, easing in from the bottom of the bowl so
# the curve stays smooth; the letter then moves back so the gap before the
# next word is unchanged.
BOWL_LENGTH = {"NUN": 0.90, "SIN": 0.90, "SAD": 0.92}


def step_bowl_length(font):
    for g in font.glyphs:
        m = RASM.match(g.name)
        if not (m and m.group(1) in BOWL_LENGTH and m.group(2) in ("f", "u")):
            continue
        layer = master_layer(font, g)
        decompose_components(font, layer)
        k = BOWL_LENGTH[m.group(1)]
        segs = outline_segments(layer)
        pts = [p for seg in segs for p in seg]
        xmin = min(p[0] for p in pts)
        low = min(pts, key=lambda p: p[1])
        pivot = low[0] + 0.3 * (low[0] - xmin)  # just right of the lowest point
        band = 0.5 * (pivot - xmin)

        def field(x, y, pivot=pivot, band=band, k=k):
            if x >= pivot:
                return (0, 0)
            d = pivot - x
            if d < band:  # ease in: displacement grows quadratically
                shift = (1 - k) * d * d / (2 * band)
            else:
                shift = (1 - k) * (d - band / 2)
            return (shift, 0)

        warp_strokes(layer, field)
        new_xmin = min(p[0] for seg in outline_segments(layer) for p in seg)
        dx = round(xmin - new_xmin)  # negative: move back left
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x + dx, nd.position.y)
        for a in layer.anchors:
            a.position = Point(a.position.x + dx, a.position.y)
        layer.width = layer.width + dx


# ---------------------------------------------------------------------------
# Step 3d: stroke tips
# ---------------------------------------------------------------------------

# Gulzar ends its free strokes (the tips of be, te, noon, qaf...) with a
# short straight cut. A qalam stroke lifts off into a thin, rounded tip:
# each short end cut is narrowed and replaced by a round cap. Cuts at the
# joins, and the pen-cut heads of the tall letters, are left alone.
TIP_MAX_CUT = 60  # longest end cut treated as a tip, in font units
TIP_NARROW = 0.2  # each corner moves this fraction of the cut inwards
TIP_JOIN_CLEARANCE = 90
# re already ends in a fine point; a cap there makes a knob on the line.
TIP_SKIP = re.compile(r"^(ALIF|LAM_ALIF|KAF|GAF|TOE|ALLAH|RE[uf])|^LAM[im]")


def round_tips(layer):
    from glyphsLib.classes import GSNode

    joins = [anchor(layer, n) for n in ("entry", "exit")]
    joins = [(a.position.x, a.position.y) for a in joins if a is not None]
    polys = [contour_points(p) for p in layer.paths]
    for pi, path in enumerate(layer.paths):
        pts = polys[pi]
        ccw = signed_area(pts) > 0
        is_hole = any(point_in_polygon(pts[0][0], pts[0][1], o) for oi, o in enumerate(polys) if oi != pi)
        flip = -1 if ccw == is_hole else 1
        nodes = list(path.nodes)
        n = len(nodes)
        out = []
        for i, nd in enumerate(nodes):
            prev = nodes[i - 1]
            if not (nd.type == "line" and prev.type != "offcurve"):
                out.append(nd)
                continue
            ax, ay = prev.position.x, prev.position.y
            bx, by = nd.position.x, nd.position.y
            length = math.hypot(bx - ax, by - ay)
            mx, my = (ax + bx) / 2, (ay + by) / 2
            if not (8 < length <= TIP_MAX_CUT) or any(
                math.hypot(mx - jx, my - jy) < TIP_JOIN_CLEARANCE for jx, jy in joins
            ):
                out.append(nd)
                continue
            # the cut is a tip only if both neighbouring edges run back the
            # same way (the two sides of a stroke ending here)
            ux, uy = (bx - ax) / length, (by - ay) / length
            nx, ny = flip * uy, -flip * ux  # outward normal of the cut
            before = nodes[i - 2].position
            after = nodes[(i + 1) % n].position
            if ((ax - before.x) * nx + (ay - before.y) * ny) <= 0 or (
                (after.x - bx) * nx + (after.y - by) * ny
            ) >= 0:
                out.append(nd)
                continue
            # narrow the tip, then close it with a round cap
            k = TIP_NARROW * length
            prev.position = Point(round(ax + ux * k), round(ay + uy * k))
            nb = (round(bx - ux * k), round(by - uy * k))
            w = length * (1 - 2 * TIP_NARROW)
            h = 0.65 * w
            pa = prev.position
            out.append(GSNode(Point(round(pa.x + nx * h), round(pa.y + ny * h)), "offcurve"))
            out.append(GSNode(Point(round(nb[0] + nx * h), round(nb[1] + ny * h)), "offcurve"))
            out.append(GSNode(Point(*nb), "curve"))
        path.nodes = out


def step_tips(font):
    for g in font.glyphs:
        if RASM.match(g.name) and not TIP_SKIP.match(g.name):
            layer = master_layer(font, g)
            if layer.paths:
                round_tips(layer)


# Calligraphic strokes never end in a hard cut: even the head of alif, the
# end of the kaf sarkash and the tail of re finish in a small round (Gulzar
# ends them with one or two short straight cuts). These remaining cut ends
# are rounded inside the outline: the two sides of the stroke are pulled
# back along their own direction and closed with a curve that just reaches
# the old cut, so nothing grows past the original shape (the tail of re
# stays on the line).
END_MAX_CUT = 90
END_SEGMENT = 80
END_RETRACT = 0.35  # of the cut width


def _unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d, d) if d else (0.0, 0.0, 0.0)


def round_ends(layer):
    from glyphsLib.classes import GSNode

    joins = [anchor(layer, n) for n in ("entry", "exit")]
    joins = [(a.position.x, a.position.y) for a in joins if a is not None]
    polys = [contour_points(p) for p in layer.paths]
    changed = False
    for pi, path in enumerate(layer.paths):
        pts = polys[pi]
        ccw = signed_area(pts) > 0
        is_hole = any(point_in_polygon(pts[0][0], pts[0][1], o) for oi, o in enumerate(polys) if oi != pi)
        flip = -1 if ccw == is_hole else 1
        nodes = list(path.nodes)
        n = len(nodes)
        i = 0
        while i < n:
            nd = nodes[i]
            if nd.type != "line" or nodes[i - 1].type == "offcurve":
                i += 1
                continue
            # the cut runs from node ia to node ib through straight lines
            ia, ib = (i - 1) % n, i
            nxt = nodes[(i + 1) % n]
            if nxt.type == "line":
                l1 = math.hypot(nd.position.x - nodes[ia].position.x, nd.position.y - nodes[ia].position.y)
                l2 = math.hypot(nxt.position.x - nd.position.x, nxt.position.y - nd.position.y)
                if l1 <= END_SEGMENT and l2 <= END_SEGMENT:
                    ib = (i + 1) % n
            A, B = nodes[ia].position, nodes[ib].position
            ux, uy, width = _unit(B.x - A.x, B.y - A.y)
            mx, my = (A.x + B.x) / 2, (A.y + B.y) / 2
            if not (8 < width <= END_MAX_CUT) or any(
                math.hypot(mx - jx, my - jy) < TIP_JOIN_CLEARANCE for jx, jy in joins
            ):
                i += 1
                continue
            nx, ny = flip * uy, -flip * ux
            before, after = nodes[ia - 1], nodes[(ib + 1) % n]
            # both sides of the stroke must run back from the cut
            ax, ay, _ = _unit(before.position.x - A.x, before.position.y - A.y)
            bx, by, _ = _unit(after.position.x - B.x, after.position.y - B.y)
            if ax * nx + ay * ny >= -0.2 or bx * nx + by * ny >= -0.2:
                i += 1
                continue
            h = END_RETRACT * width
            # don't pull a point back past its own handle
            ha = min(h, 0.6 * math.hypot(before.position.x - A.x, before.position.y - A.y))
            hb = min(h, 0.6 * math.hypot(after.position.x - B.x, after.position.y - B.y))
            A2 = Point(round(A.x + ax * ha), round(A.y + ay * ha))
            B2 = Point(round(B.x + bx * hb), round(B.y + by * hb))
            k = 0.1 * width
            c1 = Point(round(A.x + ux * k), round(A.y + uy * k))
            c2 = Point(round(B.x - ux * k), round(B.y - uy * k))
            cap = [GSNode(A2, nodes[ia].type), GSNode(c1, "offcurve"), GSNode(c2, "offcurve"), GSNode(B2, "curve")]
            if ib < ia:
                # the cut wraps round the start of the contour: rotate the
                # contour so it doesn't, and look at this cut again
                nodes = nodes[ia:] + nodes[:ia]
                i = 1
                continue
            nodes[ia:ib + 1] = cap
            n = len(nodes)
            changed = True
            i = ia + 4
        path.nodes = nodes
    return changed


def step_ends(font):
    for g in font.glyphs:
        if RASM.match(g.name):
            layer = master_layer(font, g)
            if g.name in TILTS:
                fold_tiny_caps(layer)
            if layer.paths:
                round_ends(layer)


# ---------------------------------------------------------------------------
# Step 5: line metrics
# ---------------------------------------------------------------------------

# Measured over a Bible translation set in the reshaped font, 99% of glyph
# tops are below 1.26 em and 99.9% of bottoms above -0.46 em. The default
# line height becomes 1.8 em (it was 2.7 em), which suits phones and web
# pages; the win metrics still cover the real glyph extents so nothing is
# clipped on Windows. Values in font units at NEW_UPM.
LINE_METRICS = {
    "typoAscender": 1625, "hheaAscender": 1625,
    "typoDescender": -625, "hheaDescender": -625,
    "typoLineGap": 0, "hheaLineGap": 0,
    "winAscent": 1550, "winDescent": -900,
}


def step_line_metrics(font):
    master = font.masters[0]
    for param in master.customParameters:
        if param.name in LINE_METRICS:
            param.value = LINE_METRICS[param.name]


# ---------------------------------------------------------------------------
# Step 6: naming
# ---------------------------------------------------------------------------

# The reshaped font is a Modified Version of Gulzar under the SIL OFL (which
# reserves no font name). It gets its own family name so it can be installed
# alongside Gulzar without the two being confused, and a neutral vendor ID
# ("UKWN") since it is not a Google Fonts release. The original copyright
# notice is kept.
FAMILY_NAME = "Gulzar Nastaliq"
VENDOR_ID = "UKWN"


def step_naming(font):
    font.familyName = FAMILY_NAME
    for param in font.customParameters:
        if param.name == "vendorID":
            param.value = VENDOR_ID


STEPS = [
    ("scale-and-spacing", step_scale_and_spacing),
    ("proportions", step_proportions),
    ("bowls", step_bowls),
    ("bowl-length", step_bowl_length),
    ("sarkash", step_sarkash),
    ("sarkash-length", step_sarkash_length),
    ("final-kaf-entry", step_final_kaf_entry),
    ("isolated-kaf", step_isolated_kaf),
    ("tilts", step_tilts),
    ("component-shifts", step_component_shifts),
    ("re-tail", step_re_tail),
    ("haya-size", step_haya_size),
    ("ascenders", step_ascenders),
    ("re-neck", step_re_neck),
    ("dal-size", step_dal_size),
    ("medial-dots", step_medial_dots),
    ("dot-positions", step_dot_positions),
    ("tips", step_tips),
    ("ends", step_ends),
    ("line-metrics", step_line_metrics),
    ("naming", step_naming),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glyphsfile")
    parser.add_argument("-o", "--output", help="output file (default: in place)")
    parser.add_argument("--only", help="comma-separated list of steps to run")
    args = parser.parse_args()

    font = glyphsLib.GSFont(args.glyphsfile)
    done = list(font.userData[STAMP] or [])
    only = args.only.split(",") if args.only else None
    for name, fn in STEPS:
        if only and name not in only:
            continue
        if name in done:
            print(f"{name}: already applied, skipping")
            continue
        print(f"{name}: applying")
        fn(font)
        done.append(name)
    font.userData[STAMP] = done
    out = args.output or args.glyphsfile
    font.save(out)
    if RISE_PINS:
        pins = os.path.join(os.path.dirname(os.path.abspath(out)), "rise-pins.json")
        with open(pins, "w") as fh:
            json.dump(RISE_PINS, fh, indent=1, sort_keys=True)
            fh.write("\n")


if __name__ == "__main__":
    sys.exit(main())
