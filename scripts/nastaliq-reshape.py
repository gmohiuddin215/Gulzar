#!/usr/bin/env python3
"""Reshape Gulzar towards a more calligraphic Nastaliq.

The parameters below come from a study of the proportions of traditional
Urdu Nastaliq: letter widths, heights, pen contrast and word spacing were
measured in nuqta (dot) units and compared with Gulzar's. No outlines
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
# Set a little under the measured median after comparing a printed book page.
URDU_SPACE_WIDTH = 65


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
                # where the outline doubles back on itself (the cut at the
                # end of a curl, as in the head of fe) there is no outward
                # side: moving the point pushes the curl into the stroke
                # beside it and kinks the edge, so it stays put
                p, q, r = nodes[(i - 1) % n].position, nodes[i].position, nodes[(i + 1) % n].position
                a, b = (q.x - p.x, q.y - p.y), (r.x - q.x, r.y - q.y)
                la, lb = math.hypot(*a), math.hypot(*b)
                if la and lb and (a[0] * b[0] + a[1] * b[1]) / (la * lb) < -0.5:
                    deltas[i] = (0, 0)
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

# The final bowls (noon, seen, laam, ain, ye...) are first lifted from
# Gulzar's ~2 nuqta below the baseline so that each family's bowl starts at
# a measured height: for each family, the height (in nuqta) of the bottom of
# the bowl and of the entry join. If needed a bowl is made a little
# shallower to meet both; the shallowing is an even vertical scale of the
# whole bowl below the join (never more than 15%), so the bowl stays round
# instead of getting a flat bottom.
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
HAYA_JOIN_KEEP = 140  # font units around the join left unchanged


def step_haya_size(font):
    s = HAYA_SCALE
    names = ["HAYAu1"] + [g.name for g in font.glyphs if re.match(r"^HAYAi\d+$", g.name)]
    for name in names:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        ex, en = anchor(layer, "exit"), anchor(layer, "entry")
        ox = ex.position.x if ex is not None else en.position.x

        # only what is above the line grows, and the growth fades out near
        # the join on the left, so the joining stroke keeps exactly its old
        # shape where it meets the next letter (no ledge or step there)
        def field(x, y, ox=ox):
            d = math.hypot(x - ox, y) / HAYA_JOIN_KEEP
            w = 1.0 if d >= 1 else d * d * (3 - 2 * d)
            return ((s - 1) * (x - ox) * w, (s - 1) * max(y, 0) * w)

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


# Before ye (and before seen and do-chashmi he: کسی، کھی، کھے), and in the
# forms of short words like کم، کیے، گئی، اگر، مگر، دیگر, the kaf and gaf stand
# taller than in their other forms.
# Their upright stem is shortened: the stem between KAF_YE_STEM (as shares of
# the letter's height) is drawn down, and the sarkash above it moves down
# whole, keeping its length and slope; the joins stay where they are. کی/گی
# come down most. The medial forms before ye (لکی، سکے) keep their height:
# lower, their sarkash comes too near a laam before them. The medial form
# before re (مگر، دیگر) comes down a little only where no dots stand under
# its sarkash (see LOWERED_COPIES).
KAF_YE_LOWER = {"KAFi13": 0.26, "GAFi13": 0.26, "KAFi11": 0.10, "GAFi11": 0.10,
                "KAFi24": 0.10, "GAFi24": 0.10, "KAFi5": 0.20, "GAFi5": 0.20,
                "KAFi2": 0.18, "GAFi2": 0.18, "KAFi9": 0.18, "GAFi9": 0.18,
                "KAFi17": 0.18, "GAFi17": 0.18}
KAF_YE_STEM = (0.12, 0.62)


def step_kaf_before_ye(font):
    for name, lower in KAF_YE_LOWER.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        top = max(n.position.y for p in layer.paths for n in p.nodes)
        y0, y1 = (f * top for f in KAF_YE_STEM)
        drop = lower * top

        def field(x, y, y0=y0, y1=y1, drop=drop):
            if y <= y0:
                return (0, 0)
            w = min((y - y0) / (y1 - y0), 1.0)
            return (0, -drop * w * w * (3 - 2 * w))

        warp_strokes(layer, field)

# Before sad, zad and toe (مصنف، مضمون، مطلب) the initial mim's head sits
# close against the following letter. Its connecting stroke is drawn out a
# little: the stroke between MIM_SAD_STRETCH is stretched so the head moves
# MIM_SAD_LONGER further from the join, which stays where it is.
MIM_SAD_FORMS = ("MIMi10",)
MIM_SAD_STRETCH = (60, 280)
MIM_SAD_LONGER = 60


def step_mim_before_sad(font):
    for name in MIM_SAD_FORMS:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        x0, x1 = MIM_SAD_STRETCH

        def field(x, y):
            if x <= x0:
                return (0, 0)
            w = min((x - x0) / (x1 - x0), 1.0)
            return (MIM_SAD_LONGER * w * w * (3 - 2 * w), 0)

        warp_strokes(layer, field)
        layer.width += MIM_SAD_LONGER

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
# Step 3o: final re takes the tail of the isolated re
# ---------------------------------------------------------------------------

# The isolated re has the better tail end: a wedge that sits flat on the
# line and ends in a blunt round, where the final re of کر, پر tapers to a
# point. Along most of the tail the two letters lie on top of each other
# (within a few units), so the final re keeps everything right of CUT and
# takes the isolated re's tail end left of it, overlapping by OVERLAP so the
# outline runs on smoothly. Font units.
RE_FINAL_TAIL = {"REf1": ("REu1", 140, 1)}


def _to_skia(layer, dx=0):
    import pathops
    sk = pathops.Path()
    for path in layer.paths:
        nodes = list(path.nodes)
        last = nodes[-1].position
        sk.moveTo(last.x + dx, last.y)
        buf = []
        for nd in nodes:
            p = nd.position
            if nd.type == "offcurve":
                buf.append(p)
            elif len(buf) == 2:
                sk.cubicTo(buf[0].x + dx, buf[0].y, buf[1].x + dx, buf[1].y, p.x + dx, p.y)
                buf = []
            else:
                sk.lineTo(p.x + dx, p.y)
                buf = []
        sk.close()
    return sk


def _rect(x0, y0, x1, y1):
    import pathops
    r = pathops.Path()
    r.moveTo(x0, y0)
    r.lineTo(x1, y0)
    r.lineTo(x1, y1)
    r.lineTo(x0, y1)
    r.close()
    return r


def _from_skia(sk, layer):
    from glyphsLib.classes import GSNode, GSPath

    paths = []
    cur = None
    for verb, pts in sk.segments:
        if verb == "moveTo":
            cur = [GSNode(Point(round(pts[0][0]), round(pts[0][1])), "line")]
        elif verb == "lineTo":
            cur.append(GSNode(Point(round(pts[0][0]), round(pts[0][1])), "line"))
        elif verb == "curveTo":
            cur.append(GSNode(Point(round(pts[0][0]), round(pts[0][1])), "offcurve"))
            cur.append(GSNode(Point(round(pts[1][0]), round(pts[1][1])), "offcurve"))
            cur.append(GSNode(Point(round(pts[2][0]), round(pts[2][1])), "curve"))
        elif verb == "qCurveTo":
            # promote quadratic to cubic
            (cx, cy), (ex, ey) = pts[0], pts[-1]
            sx, sy = cur[-1].position.x, cur[-1].position.y
            cur.append(GSNode(Point(round(sx + 2 / 3 * (cx - sx)), round(sy + 2 / 3 * (cy - sy))), "offcurve"))
            cur.append(GSNode(Point(round(ex + 2 / 3 * (cx - ex)), round(ey + 2 / 3 * (cy - ey))), "offcurve"))
            cur.append(GSNode(Point(round(ex), round(ey)), "curve"))
        elif verb in ("closePath", "endPath"):
            if cur and len(cur) > 1:
                first = cur.pop(0)  # the start point is repeated by the last segment
                if (cur[-1].position.x, cur[-1].position.y) != (first.position.x, first.position.y):
                    cur.append(GSNode(first.position, "line"))
                # drop zero-length segments left by the boolean operation
                clean = []
                for nd in cur:
                    clean.append(nd)
                    if nd.type != "offcurve" and len(clean) > 1:
                        seg = [clean.pop()]
                        while clean and clean[-1].type == "offcurve":
                            seg.insert(0, clean.pop())
                        prev = clean[-1].position if clean else cur[-1].position
                        if all(abs(q.position.x - prev.x) <= 1 and abs(q.position.y - prev.y) <= 1 for q in seg):
                            continue
                        clean.extend(seg)
                # the contour may end on its own start point: drop that last segment
                if len(clean) > 1:
                    first = clean[0].position
                    seg = [clean[-1]]
                    k = len(clean) - 2
                    while k >= 0 and clean[k].type == "offcurve":
                        seg.insert(0, clean[k])
                        k -= 1
                    if all(abs(q.position.x - first.x) <= 1 and abs(q.position.y - first.y) <= 1 for q in seg):
                        clean = clean[: k + 1]
                cur = clean
                gp = GSPath()
                gp.nodes = cur
                gp.closed = True
                paths.append(gp)
            cur = None
    layer.paths = paths


def step_re_final_tail(font):
    import pathops

    for name, (src, cut, overlap) in RE_FINAL_TAIL.items():
        dst = master_layer(font, font.glyphs[name])
        donor = master_layer(font, font.glyphs[src])
        decompose_components(font, dst)
        big = 5000
        own = pathops.op(_to_skia(dst), _rect(cut - overlap, -big, big, big), pathops.PathOp.INTERSECTION)
        tail = pathops.op(_to_skia(donor), _rect(-big, -big, cut + overlap, big), pathops.PathOp.INTERSECTION)
        merged = pathops.op(own, tail, pathops.PathOp.UNION)
        _from_skia(merged, dst)


# ---------------------------------------------------------------------------
# Step 3p: re after fe/qaf, and the weight of re
# ---------------------------------------------------------------------------

# The long re after fe and qaf (فر, قر, and فز, which uses the same re) is
# lengthened a little along its tail. Every re is then made a little heavier
# in the middle of its stroke, fading to nothing at the join and at the end
# of the tail so those keep their shape.
RE_LONG_TAIL = {"REf2": 1.10}
RE_WEIGHT = 5  # font units added on each side at the middle of the stroke
RE_WEIGHT_GLYPHS = ("REu1", "REf1", "REf2", "REf3")


def step_re_weight(font):
    for name, scale in RE_LONG_TAIL.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        pts = [(nd.position.x, nd.position.y) for p in layer.paths for nd in p.nodes]
        ymax = max(p[1] for p in pts)
        tip = min(pts, key=lambda q: q[0] + q[1])
        top = max(pts, key=lambda q: q[0] + q[1])
        pivot = ((top[0] + tip[0]) / 2 + 0.25 * (top[0] - tip[0]), (top[1] + tip[1]) / 2 + 0.25 * (top[1] - tip[1]))
        warp_strokes(layer, length_field(pivot, tip, scale))
    for name in RE_WEIGHT_GLYPHS:
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        ys = [nd.position.y for p in layer.paths for nd in p.nodes]
        y0, y1 = min(ys), max(ys)
        ym, h = (y0 + y1) / 2, (y1 - y0)
        normals = outward_normals(layer)
        for path, nrms in zip(layer.paths, normals):
            nodes = list(path.nodes)
            n = len(nodes)
            moves = [None] * n
            for i, (nd, nrm) in enumerate(zip(nodes, nrms)):
                if nrm is None:
                    continue
                w = math.exp(-(((nd.position.y - ym) / (0.28 * h)) ** 2))
                moves[i] = (nrm[0] * RE_WEIGHT * w, nrm[1] * RE_WEIGHT * w)
            for i in range(n):
                if moves[i] is None:
                    j = (i - 1) % n if nodes[(i - 1) % n].type != "offcurve" else (i + 1) % n
                    while moves[j] is None:
                        j = (j + 1) % n
                    moves[i] = moves[j]
            for nd, (dx, dy) in zip(nodes, moves):
                nd.position = Point(round(nd.position.x + dx), round(nd.position.y + dy))


# ---------------------------------------------------------------------------
# Step 3q: vao on the line
# ---------------------------------------------------------------------------

# The tail of vao dips a little below the line while the tail of re rests on
# it; in اور the two tails stand side by side and should end level. Vao is
# raised so its tail ends at the height of the re's. The final vao (کو) is
# built on the isolated one and its join moves up with it (the build keeps
# grouping it by its original join height).
def step_vao_lift(font):
    vao = master_layer(font, font.glyphs["VAOu1"])
    ref = master_layer(font, font.glyphs["REu1"])
    lift = min(nd.position.y for p in ref.paths for nd in p.nodes) - min(nd.position.y for p in vao.paths for nd in p.nodes)
    for path in vao.paths:
        for nd in path.nodes:
            nd.position = Point(nd.position.x, nd.position.y + lift)
    for a in vao.anchors:
        a.position = Point(a.position.x, a.position.y + lift)
    for g in font.glyphs:
        lay = master_layer(font, g)
        if any(c.name == "VAOu1" for c in lay.components):
            for a in lay.anchors:
                if a.name == "entry":
                    RISE_PINS[g.name] = round(a.position.y)
                a.position = Point(a.position.x, a.position.y + lift)


# ---------------------------------------------------------------------------
# Step 3r: long re sits higher
# ---------------------------------------------------------------------------

# The long re of پیرو, بیر, تیر hangs ~1.2 nuqta below the line, against
# ~0.9 in the calligraphic form. The whole letter (and so the letters
# joined before it) is raised without changing its shape; the build keeps
# grouping it by its original join height.
RE_RAISE = {"REf3": 50}  # font units


def step_re_raise(font):
    for name, dy in RE_RAISE.items():
        layer = master_layer(font, font.glyphs[name])
        decompose_components(font, layer)
        en = anchor(layer, "entry")
        if en is not None:
            RISE_PINS[name] = round(en.position.y)
        for path in layer.paths:
            for nd in path.nodes:
                nd.position = Point(nd.position.x, nd.position.y + dy)
        for a in layer.anchors:
            a.position = Point(a.position.x, a.position.y + dy)


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


def _runs_on(p, q, u):
    # True when the edge p->q carries on in the direction of the cut: the cut
    # is then a pen-cut corner (under the head of mim), not the end of a
    # stroke, and rounding it leaves a knob
    px, py = (p.x, p.y) if hasattr(p, "x") else p
    qx, qy = (q.x, q.y) if hasattr(q, "x") else q
    d = math.hypot(qx - px, qy - py)
    return bool(d) and abs(((qx - px) * u[0] + (qy - py) * u[1]) / d) > 0.9


def _tucked_in(polys, pi, mx, my, nx, ny):
    # Some letters are drawn as overlapping pieces (initial ain: the lower
    # stroke runs into the body). Where a stroke ends inside another piece
    # its cut is hidden: it is not a free end, and rounding it leaves a notch.
    px, py = mx + 12 * nx, my + 12 * ny
    return any(point_in_polygon(px, py, o) for oi, o in enumerate(polys) if oi != pi)


def round_tips(layer, keep_corners=False):
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
            ) >= 0 or keep_corners and (_runs_on(before, (ax, ay), (ux, uy)) or _runs_on((bx, by), after, (ux, uy))):
                out.append(nd)
                continue
            if _tucked_in(polys, pi, mx, my, nx, ny):
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
                round_tips(layer, keep_corners=g.name.startswith("MIM"))


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
            if ax * nx + ay * ny >= -0.2 or bx * nx + by * ny >= -0.2 or _tucked_in(polys, pi, mx, my, nx, ny):
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


# The sad (صح، صحیح، مصحف، صل، صم) comes in on a slanting stroke whose
# square-cut end peaks just left of where the hairline of the loop rises,
# leaving a small ear and a notch at the join. The notch is filled with a
# curve that leaves the top of the stroke along its own direction and runs
# into the hairline along its own, so the stroke flows straight into the
# loop. The curve becomes the top edge there: the notch is filled and the
# tip of the peak above the curve is cut away.
# the forms whose stroke and bowl are drawn the same way (others differ and
# are left as they are)
SAD_JOIN = ("SADi7", "SADm7", "SADi5", "SADm5", "SADi9", "SADm9", "SADi13", "SADm13")
SAD_JOIN_LEAVE = 130  # how far back along the stroke the curve leaves it
SAD_JOIN_ARRIVE = 100  # how far up the hairline the curve meets it
SAD_JOIN_HANDLE = 0.5  # handle length, as a share of the chord


def _bez(p, t):
    """Point and derivative of a cubic at t."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1 - t
    pt = (u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
          u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3)
    d = (3 * u * u * (x1 - x0) + 6 * u * t * (x2 - x1) + 3 * t * t * (x3 - x2),
         3 * u * u * (y1 - y0) + 6 * u * t * (y2 - y1) + 3 * t * t * (y3 - y2))
    return pt, d


def _bez_at_distance(p, dist):
    """Parameter where a cubic is first `dist` (straight line) from its start."""
    t = 0.0
    while t < 1 and math.dist(_bez(p, t)[0], p[0]) < dist:
        t += 0.002
    return min(t, 1.0)


def _bez_split(p, t):
    """The two halves of a cubic split at t."""
    lerp = lambda a, b: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    a, b, c = lerp(p[0], p[1]), lerp(p[1], p[2]), lerp(p[2], p[3])
    d, e = lerp(a, b), lerp(b, c)
    f = lerp(d, e)
    return (p[0], a, d, f), (f, e, c, p[3])


def sad_join(layer):
    import pathops

    paths = [[(n.position.x, n.position.y, n.type) for n in p.nodes] for p in layer.paths]
    stroke, bowl = sorted(paths, key=len)
    # the top corner of the stroke's square end, and the stroke's top edge
    # running back from it
    i = next(k for k, n in enumerate(stroke) if n[2] == "line")
    top = [stroke[(i + k) % len(stroke)][:2] for k in range(4)]
    # the foot of the hairline: two on-curve points a few units apart (its
    # tiny cap), the first reached down the hairline's outer edge
    m = len(bowl)
    j = next(k for k in range(m) if bowl[k][2] != "offcurve" and bowl[(k + 3) % m][2] != "offcurve"
             and math.dist(bowl[k][:2], bowl[(k + 3) % m][:2]) < 30
             and bowl[(k + 1) % m][2] == bowl[(k + 2) % m][2] == "offcurve")
    hair = [bowl[(j - 3 + k) % m][:2] for k in range(4)][::-1]  # from the foot upward
    corner, foot = top[0], hair[0]
    ta, tb = _bez_at_distance(top, SAD_JOIN_LEAVE), _bez_at_distance(hair, SAD_JOIN_ARRIVE)
    (a, da), (b, db) = _bez(top, ta), _bez(hair, tb)
    da, db = _unit(-da[0], -da[1]), _unit(*db)  # toward the corner; up the hairline
    h = SAD_JOIN_HANDLE * math.dist(a, b)
    patch = pathops.Path()
    patch.moveTo(*a)
    patch.cubicTo(a[0] + da[0] * h, a[1] + da[1] * h, b[0] - db[0] * h, b[1] - db[1] * h, *b)
    _, p1, p2, p3 = _bez_split(hair, tb)[0][::-1]  # back down the hairline to its foot
    patch.cubicTo(*p1, *p2, *p3)
    # and home through the ink: under the foot, under the corner, under the
    # point where the curve left the stroke
    for x, y in ((foot[0], foot[1] - 20), (corner[0] - 10, corner[1] - 30), (a[0], a[1] - 25)):
        patch.lineTo(x, y)
    patch.close()
    # everything above the curve, left of the hairline: the peak
    cap = pathops.Path()
    cap.moveTo(*a)
    cap.cubicTo(a[0] + da[0] * h, a[1] + da[1] * h, b[0] - db[0] * h, b[1] - db[1] * h, *b)
    cap.lineTo(b[0], b[1] + 200)
    cap.lineTo(a[0], b[1] + 200)
    cap.close()
    sk = _skia(paths)
    sk = pathops.op(sk, cap, pathops.PathOp.DIFFERENCE)
    sk = pathops.op(sk, patch, pathops.PathOp.UNION)
    _from_skia(sk, layer)


def step_sad_join(font):
    for name in SAD_JOIN:
        if name in font.glyphs:
            sad_join(master_layer(font, font.glyphs[name]))



# A straight cut between two sharp outward corners (the left side of the
# head of fe, the head of dal, the tip of be) reads as a flat facet in a
# pen-drawn letter. Each corner of such a cut gets a small round, CORNER_ROUND
# of the cut's length but no more than CORNER_ROUND_MAX, leaving the
# straight part between. Cuts hidden inside the ink of another stroke, and
# the cuts at joins (which must meet the next letter exactly), are left as
# they are.
CORNER_ROUND = 0.3
CORNER_ROUND_MAX = 40
CORNER_MIN_TURN = 35  # degrees: gentler corners are left sharp
CORNER_CUT = (12, 220)  # lengths of straight segment treated as a cut
CORNER_JOIN_CLEARANCE = 80


def _turn(u, v):
    return math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))


def round_corners(layer):
    from glyphsLib.classes import GSNode

    joins = []
    for name in ("entry", "exit"):
        a = anchor(layer, name)
        if a is not None:
            joins.append(((a.position.x, a.position.y), (a.position.x + JOIN_CUT[0], a.position.y + JOIN_CUT[1])))
    polys = [contour_points(p) for p in layer.paths]
    for pi, path in enumerate(layer.paths):
        others = [o for oi, o in enumerate(polys) if oi != pi]
        pts = polys[pi]
        orient = 1 if signed_area(pts) > 0 else -1
        if any(point_in_polygon(pts[0][0], pts[0][1], o) for o in others):
            orient = -orient  # a hole winds the other way
        nodes = list(path.nodes)
        cuts = []
        n = len(nodes)
        for i in range(n):
            a, b = nodes[i], nodes[(i + 1) % n]
            if a.type == "offcurve" or b.type != "line":
                continue
            A, B = (a.position.x, a.position.y), (b.position.x, b.position.y)
            length = math.dist(A, B)
            if not CORNER_CUT[0] <= length <= CORNER_CUT[1]:
                continue
            pin, pout = nodes[(i - 1) % n].position, nodes[(i + 2) % n].position
            d = (B[0] - A[0], B[1] - A[1])
            t1 = _turn((A[0] - pin.x, A[1] - pin.y), d) * orient
            t2 = _turn(d, (pout.x - B[0], pout.y - B[1])) * orient
            if t1 < CORNER_MIN_TURN or t2 < CORNER_MIN_TURN:
                continue
            if any(min(math.dist(A, q), math.dist(B, q), _seg_distance(q, *j)) < CORNER_JOIN_CLEARANCE
                   for j in joins for q in (A, B, ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2))):
                continue
            # hidden inside another stroke: nothing to see
            if any(point_in_polygon(P[0], P[1], o) for o in others for P in (A, B)):
                continue
            cuts.append((i, (i + 1) % n, length))
        if not cuts:
            continue
        # replace each corner (node index) by a short curve; work from the end
        corners = {}
        for ia, ib, length in cuts:
            r = min(CORNER_ROUND * length, CORNER_ROUND_MAX)
            corners[ia] = max(corners.get(ia, 0), r)
            corners[ib] = max(corners.get(ib, 0), r)
        for ci in sorted(corners, reverse=True):
            r = corners[ci]
            prev, here, nxt = nodes[ci - 1], nodes[ci], nodes[(ci + 1) % len(nodes)]
            P, H, N = prev.position, here.position, nxt.position
            ux, uy, lin = _unit(P.x - H.x, P.y - H.y)
            vx, vy, lout = _unit(N.x - H.x, N.y - H.y)
            ra, rb = min(r, 0.45 * lin), min(r, 0.45 * lout)
            if ra < 3 or rb < 3:
                continue
            A2 = Point(round(H.x + ux * ra), round(H.y + uy * ra))
            B2 = Point(round(H.x + vx * rb), round(H.y + vy * rb))
            c1 = Point(round(H.x + ux * ra * 0.45), round(H.y + uy * ra * 0.45))
            c2 = Point(round(H.x + vx * rb * 0.45), round(H.y + vy * rb * 0.45))
            new = [GSNode(A2, here.type), GSNode(c1, "offcurve"), GSNode(c2, "offcurve"), GSNode(B2, "curve")]
            if prev.type == "offcurve":
                # the incoming curve now ends at A2: its last handle moves with it
                prev.position = Point(round(P.x + (A2.x - H.x)), round(P.y + (A2.y - H.y)))
            if nxt.type == "offcurve":
                nxt.position = Point(round(N.x + (B2.x - H.x)), round(N.y + (B2.y - H.y)))
            nodes[ci:ci + 1] = new
        path.nodes = nodes


def step_corners(font):
    for g in font.glyphs:
        if RASM.match(g.name):
            layer = master_layer(font, g)
            if layer.paths:
                round_corners(layer)


# The head of fe and qaf has a straight left side (a long pen cut, ~160
# units), which reads as a flat facet once the letter is narrowed. It is
# drawn as a gentle outward curve, bulging FE_HEAD_BULGE of its length.
FE_HEAD = re.compile(r"^(FE|QAF)(i\d+|u1|f1)$")
FE_HEAD_EDGE = (150, 175)  # length of the straight side
FE_HEAD_BULGE = 0.08


# Middle letters lift the letters before them less: each middle letter's
# rise from its exit to its entry is drawn MEDIAL_RISE as high, by leaning
# the letter between the two joins (strokes keep their thickness). Words
# then climb at a gentler slope and need less line height.
MEDIAL_RISE = 0.7


def step_medial_rise(font):
    for g in font.glyphs:
        m = RASM.match(g.name)
        if not m or m.group(2) not in ("m", "msd"):
            continue
        layer = master_layer(font, g)
        anchors = {a.name: a.position for a in layer.anchors}
        if "entry" not in anchors or "exit" not in anchors:
            continue
        (ex, ey), (xx, xy) = (anchors["entry"].x, anchors["entry"].y), (anchors["exit"].x, anchors["exit"].y)
        rise = ey - xy
        if rise <= 0 or ex - xx < 20:
            continue
        decompose_components(font, layer)
        if not layer.paths:
            continue
        drop = (1 - MEDIAL_RISE) * rise

        def field(x, y, xx=xx, span=ex - xx, drop=drop):
            return (0, -drop * min(1, max(0, (x - xx) / span)))

        warp_strokes(layer, field)


# The feet of vao, dal and re (and the letters drawn on them) run out further
# to the left, in a longer, flatter tail; the upper part of the letter and
# its join stay as they are.
FOOT_REACH = {"u": 1.12, "f": 1.18}


def step_foot_reach(font):
    for g in font.glyphs:
        m = RASM.match(g.name)
        if not m or m.group(1) not in ("VAO", "DAL", "RE") or m.group(2) not in FOOT_REACH:
            continue
        k = FOOT_REACH[m.group(2)]
        layer = master_layer(font, g)
        decompose_components(font, layer)
        if not layer.paths:
            continue
        pts = [nd.position for p in layer.paths for nd in p.nodes]
        right = max(p.x for p in pts)
        y0, y1 = min(p.y for p in pts), max(p.y for p in pts)
        full, none = y0 + 0.3 * (y1 - y0), y0 + 0.65 * (y1 - y0)

        def field(x, y, right=right, k=k, full=full, none=none):
            w = min(1, max(0, (none - y) / (none - full)))
            return ((x - right) * (k - 1) * w, 0)

        left = min(p.x for p in pts)
        warp_strokes(layer, field)
        # the letter is set wider by what its foot gained, so the space
        # before the next letter stays as it was
        grown = left - min(nd.position.x for p in layer.paths for nd in p.nodes)
        for p in layer.paths:
            for nd in p.nodes:
                nd.position = Point(nd.position.x + grown, nd.position.y)
        for a in layer.anchors:
            a.position = Point(a.position.x + grown, a.position.y)
        layer.width = round(layer.width + grown)


def step_fe_head(font):
    from glyphsLib.classes import GSNode

    for g in font.glyphs:
        if not FE_HEAD.match(g.name):
            continue
        layer = master_layer(font, g)
        polys = [contour_points(p) for p in layer.paths]
        for pi, path in enumerate(layer.paths):
            nodes = list(path.nodes)
            for i in range(len(nodes)):
                a, b = nodes[i - 1], nodes[i]
                if b.type != "line" or a.type == "offcurve":
                    continue
                A, B = a.position, b.position
                ux, uy, length = _unit(B.x - A.x, B.y - A.y)
                if not FE_HEAD_EDGE[0] <= length <= FE_HEAD_EDGE[1] or abs(uy) < 0.9:
                    continue
                # outward: the side of the edge away from the ink
                mx, my = (A.x + B.x) / 2, (A.y + B.y) / 2
                nx, ny = uy, -ux
                if any(point_in_polygon(mx + nx * 5, my + ny * 5, o) for o in polys):
                    nx, ny = -nx, -ny
                h = FE_HEAD_BULGE * length / 0.75
                c1 = Point(round(A.x + (B.x - A.x) / 3 + nx * h), round(A.y + (B.y - A.y) / 3 + ny * h))
                c2 = Point(round(A.x + 2 * (B.x - A.x) / 3 + nx * h), round(A.y + 2 * (B.y - A.y) / 3 + ny * h))
                nodes[i:i + 1] = [GSNode(c1, "offcurve"), GSNode(c2, "offcurve"), GSNode(B, "curve")]
                break
            path.nodes = nodes

# Some letters are drawn a little lower (or taller) in one context only, where the same
# form is used elsewhere at full height: each is copied to a new glyph
# (LOWERED_COPIES: form -> (copy, share of its height, stem)), the copy's
# upright stem shortened as in step_kaf_before_ye, and
# sources/build/fez/connections.fez puts the copy in that context.
#   - The laam before kaf and gaf (لکھا، لگا، ملک، الگ), so it does not tower
#     over the shortened kaf; the forms are shared with لیں، حوالہ، جلد.
#   - The medial kaf and gaf before re (مگر، دیگر) where no dots stand above
#     the letters before it: lower, its sarkash would run into them (فنگر،
#     شکر، کنفیگر).
#   - The other way: the initial kaf and gaf of کیا، گیا stand taller again
#     (a negative share) than in کیے، گئے، کتاب, where they are shortened.
LAM_KAF_STEM = (0.25, 0.75)
LOWERED_COPIES = {
    "LAMi3": ("LAMi93", 0.07, LAM_KAF_STEM),
    "LAMi8": ("LAMi98", 0.07, LAM_KAF_STEM),
    "LAMm3": ("LAMm93", 0.07, LAM_KAF_STEM),
    "KAFm16": ("KAFm96", 0.10, KAF_YE_STEM),
    "GAFm16": ("GAFm96", 0.10, KAF_YE_STEM),
    "KAFi2": ("KAFi92", -0.15, KAF_YE_STEM),
    "GAFi2": ("GAFi92", -0.15, KAF_YE_STEM),
}


def step_lowered_copies(font):
    from glyphsLib.classes import GSAnchor, GSGlyph, GSLayer

    master = font.masters[0]
    for name, (copy, lower, stem) in LOWERED_COPIES.items():
        src = font.glyphs[name]
        layer0 = master_layer(font, src)
        glyph = GSGlyph(copy)
        glyph.category, glyph.subCategory = src.category, src.subCategory
        font.glyphs.append(glyph)
        layer = GSLayer()
        layer.layerId = layer.associatedMasterId = master.id
        glyph.layers.append(layer)
        _set_paths(layer, _glyph_paths(font, name, (1, 0, 0, 1, 0, 0)))
        layer.width = layer0.width
        for a in layer0.anchors:
            layer.anchors.append(GSAnchor(a.name, Point(a.position.x, a.position.y)))
        top = max(n.position.y for p in layer.paths for n in p.nodes)
        y0, y1 = (f * top for f in stem)
        drop = lower * top

        def field(x, y, y0=y0, y1=y1, drop=drop):
            if y <= y0:
                return (0, 0)
            w = min((y - y0) / (y1 - y0), 1.0)
            return (0, -drop * w * w * (3 - 2 * w))

        warp_strokes(layer, field)
        for a in layer.anchors:
            dx, dy = field(a.position.x, a.position.y)
            a.position = Point(a.position.x, round(a.position.y + dy))


# ---------------------------------------------------------------------------
# Step 4b: honorifics
# ---------------------------------------------------------------------------

# Books set رضی اللہ عنہ and رحمۃ اللہ علیہ after a name as small stacked
# ligatures, raised off the line, rather than as the tiny ر-ض / ر-ح marks.
# scripts/honorific-layouts.py writes them from the font's own words to
# sources/honorifics.json; here they are drawn as outlines (scaled copies of
# the letters) into new spacing glyphs that take over the code points of the
# marks. The marks themselves stay in the font, unencoded.
HONORIFICS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sources", "honorifics.json")

# The durood ligatures hung far below the line (to -0.7 em) beside the name
# they follow; they are set smaller and raised so they sit on the line.
DUROOD = ()  # both durood ligatures are now drawn by the honorifics step
DUROOD_SCALE = 0.55
DUROOD_BOTTOM = 40
DUROOD_SIDE = 60


def _glyph_paths(font, name, transform):
    """Paths of a glyph with its components flattened, as lists of (x, y, type)."""
    a, b, c, d, e, f = transform
    layer = master_layer(font, font.glyphs[name])
    out = []
    for path in layer.paths:
        out.append([(a * n.position.x + c * n.position.y + e, b * n.position.x + d * n.position.y + f, n.type)
                    for n in path.nodes])
    for comp in layer.components:
        ca, cb, cc, cd, ce, cf = comp.transform
        out += _glyph_paths(font, comp.name, (a * ca + c * cb, b * ca + d * cb, a * cc + c * cd,
                                              b * cc + d * cd, a * ce + c * cf + e, b * ce + d * cf + f))
    return out


def _set_paths(layer, paths):
    from glyphsLib.classes import GSNode, GSPath

    layer.paths = []
    for pts in paths:
        p = GSPath()
        p.nodes = [GSNode(Point(round(x), round(y)), t) for x, y, t in pts]
        p.closed = True
        layer.paths.append(p)


# A word in a stacked honorific can be drawn out to span the row beneath it
# (علیہ over رحمۃُ اللہ): the long-stroke initial ain is raised and moved over,
# and its tail is carried to the lam by a new stroke. The stroke leaves the
# tail at the tail's own angle and width, sags in an arc, and arrives at
# the thick base of the lam (where a joining stroke meets it) at the lam's
# width, so it grows slightly heavier as it goes, like a calligrapher's
# drawn-out ain.
KASHIDA_CUT = 150  # x in the ain where its own tail gives way to the stroke
# Where the stroke leaves the lam: a point on the round of its neck (top)
# and of its base (bottom), each where the lam's own curve is already
# heading down at the angle the stroke leaves at, so the lam's outline runs
# straight on into the stroke. Leaving slightly downward, the stroke dips
# below the lam's base mid-way and rises back into the lam, as a pen does.
KASHIDA_LAM_BASE = {"LAMm2": ((266, 86), (222, -49))}
# How the top and bottom edges leave the lam: angle (degrees) and handle
# length (share of the chord).
KASHIDA_LEAVE = ((-6.4, 0.36), (-10.6, 0.2))
KASHIDA_HANDLE = 0.42  # handle length at the ain end, as a share of the chord
KASHIDA_OVERLAP = 14  # the stroke runs on into the tail by this much, hidden


def _skia(paths):
    import pathops

    sk = pathops.Path()
    for pts in paths:
        sk.moveTo(*pts[-1][:2])
        buf = []
        for x, y, t in pts:
            if t == "offcurve":
                buf.append((x, y))
            elif len(buf) == 2:
                sk.cubicTo(*buf[0], *buf[1], x, y)
                buf = []
            else:
                sk.lineTo(x, y)
                buf = []
        sk.close()
    sk.simplify()
    return sk


def _edges_at(paths, x):
    """Top and bottom of a stroke outline where it crosses x."""
    pts = []
    for path in paths:
        start = max(i for i, p in enumerate(path) if p[2] != "offcurve")
        prev, buf = path[start], []
        for p in path[start + 1:] + path[:start + 1]:
            if p[2] == "offcurve":
                buf.append(p)
                continue
            seg = [prev] + buf + [p] if len(buf) == 2 else [prev, p]
            for k in range(1, 33):
                t = k / 32
                if len(seg) == 4:
                    pts.append(tuple(sum(c * q[i] for c, q in zip(((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3), seg)) for i in (0, 1)))
                else:
                    pts.append((prev[0] + t * (p[0] - prev[0]), prev[1] + t * (p[1] - prev[1])))
            prev, buf = p, []
    ys = [y0 + (x - x0) * (y1 - y0) / (x1 - x0)
          for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]) if (x0 - x) * (x1 - x) <= 0 and x0 != x1]
    return max(ys), min(ys)


def _kashida(font, ain, lam, lam_pos):
    """The long-stroke ain, in its own coordinates, with its tail carried to a
    lam at lam_pos by a drawn stroke."""
    import pathops

    paths = _glyph_paths(font, ain, (1, 0, 0, 1, 0, 0))
    stroke, body = paths[:1], paths[1:]
    cut = KASHIDA_CUT
    tail = pathops.op(_skia(stroke), _rect(cut, -1000, 3000, 3000), pathops.PathOp.INTERSECTION)
    # the corners of the cut, and the tail's direction there, exactly as the
    # boolean operation made them
    tangents = {}
    prev = None
    for verb, pts in tail.segments:
        if verb != "curveTo":
            prev = pts[-1] if pts else prev
            continue
        if abs(prev[0] - cut) < 0.01:  # a curve leaving the cut
            tangents[prev[1]] = (pts[0][1] - prev[1]) / (pts[0][0] - prev[0])
        if abs(pts[-1][0] - cut) < 0.01:  # a curve arriving at it
            tangents[pts[-1][1]] = (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
        prev = pts[-1]
    top, bottom = max(tangents), min(tangents)
    at, ab = tangents[top], tangents[bottom]
    (tx, ty), (bx, by) = KASHIDA_LAM_BASE[lam]
    lx, ly = lam_pos
    sk = pathops.Path()

    def edge(p0, p1, leave, arrive):
        # leaves the lam as its joins do and reaches the tail at the tail's
        # own angle; between them the pen sweeps through in one movement
        chord = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        angle, share = leave
        h0, h1 = share * chord, KASHIDA_HANDLE * chord
        angle, a = math.radians(angle), math.atan(arrive)
        return ((p0[0] + h0 * math.cos(angle), p0[1] + h0 * math.sin(angle)),
                (p1[0] - h1 * math.cos(a), p1[1] - h1 * math.sin(a)), p1)

    t0, b0 = (lx + tx, ly + ty), (lx + bx, ly + by)
    t1, b1 = (cut, top), (cut, bottom)
    o = KASHIDA_OVERLAP
    sk.moveTo(*t0)
    sk.cubicTo(*[c for pt in edge(t0, t1, KASHIDA_LEAVE[0], at) for c in pt])
    # the overlap's far corners sit inside the tail, which curves away
    sk.lineTo(cut + o, top + at * o - 0.15 * (top - bottom))
    sk.lineTo(cut + o, bottom + ab * o + 0.15 * (top - bottom))
    sk.lineTo(*b1)
    c1, c2, _ = edge(b0, b1, KASHIDA_LEAVE[1], ab)
    sk.cubicTo(*c2, *c1, *b0)
    sk.close()
    out = pathops.op(tail, sk, pathops.PathOp.UNION)
    out = pathops.op(out, _skia(body), pathops.PathOp.UNION)
    # The lam's neck runs level for a few units past where the stroke leaves
    # it going down; this trims that sliver off above the stroke's top edge.
    from fontTools.misc.bezierTools import splitCubicAtT

    first = splitCubicAtT(t0, *edge(t0, t1, KASHIDA_LEAVE[0], at), 0.1)[0]
    trim = pathops.Path()
    trim.moveTo(*t0)
    trim.cubicTo(*first[1], *first[2], *first[3])
    trim.lineTo(first[3][0], t0[1] + 15)
    trim.lineTo(t0[0], t0[1] + 15)
    trim.close()
    return out, trim


# A letter drawn as one long toothless sweep (the seen of السلام in an
# honorific): it starts on the cut where the letter before it ends, which for
# the laam is a shorter cut than the standard one (SWEEP_ENTRY_CUT), leaving
# it down and to the left in SWEEP_ENTRY_TURN.
SWEEP_ENTRY_CUT = (-33, -88)
SWEEP_ENTRY_TURN = ((-1, -0.3), (-1, -0.05))  # top edge, bottom edge


def _sweep(font, name, extra, dip):
    """The letter drawn instead as one long sweep of the pen, without teeth:
    from where it joins the letter before (its entry) to where the next one
    joins it (its exit, carried `extra` further on), sagging by `dip` on the
    way. Both ends sit on the cuts of the neighbouring letters, so the stroke
    runs on from one into the other."""
    import pathops

    layer = master_layer(font, font.glyphs[name])
    e, x = anchor(layer, "entry").position, anchor(layer, "exit").position
    e0 = (e.x, e.y)
    e1 = (e.x + SWEEP_ENTRY_CUT[0], e.y + SWEEP_ENTRY_CUT[1])
    x0 = (x.x - extra, x.y)
    x1 = (x0[0] + JOIN_CUT[0], x0[1] + JOIN_CUT[1])
    span = e0[0] - x0[0]
    (dt, db), level = SWEEP_ENTRY_TURN, (1, 0)

    def handle(p, d, share):
        length = math.hypot(*d)
        return (p[0] + d[0] / length * share * span, p[1] + d[1] / length * share * span - dip)

    sk = pathops.Path()
    sk.moveTo(*e0)
    sk.cubicTo(*handle(e0, dt, 0.3), *handle(x0, level, 0.4), *x0)
    sk.lineTo(*x1)
    sk.cubicTo(*handle(x1, level, 0.4), *handle(e1, db, 0.3), *e1)
    sk.close()
    return sk


def honorific_outline(font, components, scale):
    """All the letters of a stacked honorific as one outline (a pathops Path)."""
    import pathops
    from fontTools.pens.transformPen import TransformPen

    out, trims = pathops.Path(), []
    for comp, x, y, *opts in components:
        if opts and ("lower" in opts[0] or "shorten" in opts[0]):
            # a letter whose rising stroke is brought down ("lower": heights
            # above y0) or whose falling tail is drawn up ("shorten": depths
            # below y0) to fit the honorific: scaled by the factor towards y0,
            # left of x0, fading out by x1
            up = "lower" in opts[0]
            y0, factor, x0, x1 = opts[0]["lower" if up else "shorten"]

            def weight(px):
                return 1 if px <= x0 else 0 if px >= x1 else (x1 - px) / (x1 - x0)

            def move(px, py):
                if (py <= y0) if up else (py >= y0):
                    return py
                return y0 + (py - y0) * (1 - (1 - factor) * weight(px))

            paths = [[(px, move(px, py), t) for px, py, t in p]
                     for p in _glyph_paths(font, comp, (1, 0, 0, 1, 0, 0))]
            piece = _skia(paths)
        elif opts and "size" in opts[0]:
            # a letter drawn smaller within the honorific
            f = opts[0]["size"]
            fx, fy = f if isinstance(f, (list, tuple)) else (f, f)
            piece = _skia(_glyph_paths(font, comp, (fx, 0, 0, fy, 0, 0)))
        elif opts and "sweep" in opts[0]:
            piece = _sweep(font, comp, *opts[0]["sweep"])
        elif opts and "kashida" in opts[0]:
            lam, lx, ly = opts[0]["kashida"]
            piece, trim = _kashida(font, comp, lam, ((lx - x) / scale, (ly - y) / scale))
            cut = pathops.Path()
            trim.draw(TransformPen(cut.getPen(), (scale, 0, 0, scale, x, y)))
            trims.append(cut)
        else:
            piece = _skia(_glyph_paths(font, comp, (1, 0, 0, 1, 0, 0)))
        placed = pathops.Path()
        piece.draw(TransformPen(placed.getPen(), (scale, 0, 0, scale, x, y)))
        out = pathops.op(out, placed, pathops.PathOp.UNION)
    for cut in trims:
        out = pathops.op(out, cut, pathops.PathOp.DIFFERENCE)
    return out


def step_honorifics(font):
    from glyphsLib.classes import GSGlyph, GSLayer

    with open(HONORIFICS) as fh:
        layouts = json.load(fh)
    master = font.masters[0]
    for name, spec in layouts.items():
        codes = set(spec["unicodes"])
        for g in font.glyphs:
            if codes & set(g.unicodes or []):
                g.unicodes = [u for u in g.unicodes if u not in codes]
        glyph = GSGlyph(name)
        glyph.unicodes = spec["unicodes"]
        glyph.category, glyph.subCategory = "Symbol", "Other"
        font.glyphs.append(glyph)
        layer = GSLayer()
        layer.layerId = layer.associatedMasterId = master.id
        glyph.layers.append(layer)
        _from_skia(honorific_outline(font, spec["components"], spec["scale"]), layer)
        layer.width = spec["width"]
    # an empty mark that harakat typed inside an honorific phrase become (see
    # scripts/honorific-ligatures.py)
    mark = GSGlyph("honorificMark")
    mark.category, mark.subCategory = "Mark", "Nonspacing"
    font.glyphs.append(mark)
    layer = GSLayer()
    layer.layerId = layer.associatedMasterId = master.id
    layer.width = 0
    mark.layers.append(layer)
    for name in DUROOD:
        layer = master_layer(font, font.glyphs[name])
        paths = _glyph_paths(font, name, (DUROOD_SCALE, 0, 0, DUROOD_SCALE, 0, 0))
        xs = [x for p in paths for x, _, t in p if t != "offcurve"]
        ys = [y for p in paths for _, y, t in p if t != "offcurve"]
        dx, dy = DUROOD_SIDE - min(xs), DUROOD_BOTTOM - min(ys)
        _set_paths(layer, [[(x + dx, y + dy, t) for x, y, t in p] for p in paths])
        layer.components = []
        layer.width = round(max(xs) - min(xs) + 2 * DUROOD_SIDE)


# ---------------------------------------------------------------------------
# Copybook details
# ---------------------------------------------------------------------------

# Nashist o kursi, measured on the copybook's ruled page: four lines two qat
# apart. The qat is taken from the letters' heights on that page against
# their heights here (jim, sad, ain, seen, toe, fe, dal, re average 176
# units), which is also the width of the dot.
#   line 3 runs through the floor of be, fe, kaf: KURSI_LINE, measured from
#     the floor of be (about a quarter of it above the line, the rest below);
#   the bowls (noon, seen, sad, ain, jim, qaf, laam, ye) reach line 4, two
#     qat lower: each is moved down whole, the final forms as well (the
#     letters joined before them come down with them), so no bowl is drawn
#     out of its shape;
#   dal and re hang across line 3, their foot 1 and 1.15 qat below it;
#   the stem of toe (isolated and final) starts just under line 1, 3.9 qat
#   above line 3.
# This runs after the honorifics, which are laid out with the letters as they
# were. Its numerals are four qat tall; Gulzar's were about three. They are
# drawn larger by NUMERAL_SCALE about their foot (on their own outlines, so
# the shapes they share with other signs stay as they are).
QAT = 176
KURSI_LINE = 109
BOWL_FAMILIES = ("NUN", "QAF", "SIN", "LAM", "AIN", "CH_YE", "JIM", "SAD")
BOWL_BOTTOM = KURSI_LINE - 2.05 * QAT
HANGING_FOOT = {"DAL": KURSI_LINE - 1.0 * QAT, "RE": KURSI_LINE - 1.15 * QAT}
TOE_TOP = KURSI_LINE + 3.9 * QAT
TOE_STEM_ZONE = 300  # the top of toe drawn out over this much of its stem
NUMERAL_SCALE = 1.25


def _shift_glyph(layer, dy):
    for p in layer.paths:
        for nd in p.nodes:
            nd.position = Point(nd.position.x, round(nd.position.y + dy))
    for a in layer.anchors:
        a.position = Point(a.position.x, round(a.position.y + dy))


def step_copybook_details(font):
    toe = master_layer(font, font.glyphs["TOEu1"])
    toe_rise = TOE_TOP - max(p[0][1] for p in outline_segments(toe))
    for g in font.glyphs:
        m = RASM.match(g.name)
        if not m:
            continue
        family, form = m.group(1), m.group(2)
        layer = master_layer(font, g)
        if not layer.paths:
            continue
        ys = [p[0][1] for p in outline_segments(layer)]
        if family in BOWL_FAMILIES and form in ("f", "u"):
            _shift_glyph(layer, BOWL_BOTTOM - min(ys))
        elif family in HANGING_FOOT and form == "u":
            _shift_glyph(layer, HANGING_FOOT[family] - min(ys))
        elif family == "TOE" and form in ("u", "f") and toe_rise > 0:
            # the stem is drawn out at its top, over TOE_STEM_ZONE
            y0 = max(ys) - TOE_STEM_ZONE

            def lift(y):
                return round(y + toe_rise * (y - y0) / TOE_STEM_ZONE) if y > y0 else y

            for p in layer.paths:
                for nd in p.nodes:
                    nd.position = Point(nd.position.x, lift(nd.position.y))
            for a in layer.anchors:
                a.position = Point(a.position.x, lift(a.position.y))
    s = NUMERAL_SCALE
    numerals = [g for g in font.glyphs
                if any(0x660 <= int(u, 16) <= 0x669 or 0x6F0 <= int(u, 16) <= 0x6F9 for u in g.unicodes or [])]
    # outlines first: some numerals are drawn from others
    drawn = {g.name: _glyph_paths(font, g.name, (s, 0, 0, s, 0, 0)) for g in numerals}
    for g in numerals:
        layer = master_layer(font, g)
        _set_paths(layer, [[(round(x), round(y), t) for x, y, t in p] for p in drawn[g.name]])
        layer.components = []
        for a in layer.anchors:
            a.position = Point(round(a.position.x * s), round(a.position.y * s))
        layer.width = round(layer.width * s)

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
    ("kaf-before-ye", step_kaf_before_ye),
    ("mim-before-sad", step_mim_before_sad),
    ("re-neck", step_re_neck),
    ("re-final-tail", step_re_final_tail),
    ("re-weight", step_re_weight),
    ("vao-lift", step_vao_lift),
    ("re-raise", step_re_raise),
    ("dal-size", step_dal_size),
    ("medial-dots", step_medial_dots),
    ("dot-positions", step_dot_positions),
    ("tips", step_tips),
    ("ends", step_ends),
    ("sad-join", step_sad_join),
    ("corners", step_corners),
    ("fe-head", step_fe_head),
    ("medial-rise", step_medial_rise),
    ("foot-reach", step_foot_reach),
    ("lowered-copies", step_lowered_copies),
    ("honorifics", step_honorifics),
    # after the honorifics, which are laid out with the letters as they were
    ("copybook-details", step_copybook_details),
    ("line-metrics", step_line_metrics),
    ("naming", step_naming),
]


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------

# Letters join through a pen cut that is the same on both sides of the join:
# from the entry or exit anchor to a point JOIN_CUT below it, the outgoing
# letter's edge lies exactly on the incoming letter's. A step that reshapes a
# letter (narrowing it, stretching its stem, resizing its bowl) moves this
# edge with the letter while the anchor moves by its own rule, so the two
# edges no longer meet and every join shows a small step or spur. Around
# each step the outline at the cut is put back where it was relative to its
# anchor: rigidly within JOIN_HOLD of the cut, fading out by JOIN_FADE, so
# the rest of the letter keeps the step's reshaping.
JOIN_CUT = (-47, -135)
JOIN_HOLD = 30
JOIN_FADE = 110
# Steps that move points (rather than rebuild outlines); the join edge is
# held around each of them.
JOIN_SAFE_STEPS = {
    "proportions", "bowls", "bowl-length", "sarkash", "sarkash-length", "final-kaf-entry",
    "isolated-kaf", "tilts", "re-tail", "haya-size", "ascenders", "re-neck", "re-final-tail",
    "re-weight", "vao-lift", "re-raise", "dal-size", "kaf-before-ye", "mim-before-sad",
}


def _seg_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if not L else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _join_snapshot(font):
    snap = {}
    for g in font.glyphs:
        if not RASM.match(g.name):
            continue
        layer = master_layer(font, g)
        # composites are matched against their flattened outline: the steps
        # decompose them before reshaping
        flat = _glyph_paths(font, g.name, (1, 0, 0, 1, 0, 0))
        joins = []
        for name in ("entry", "exit"):
            a = anchor(layer, name)
            if a is None:
                continue
            ax, ay = a.position.x, a.position.y
            on = [(x, y) for p in flat for x, y, t in p if t != "offcurve"]
            # the full cut where the letter has one, else just the join point
            bottom = (ax + JOIN_CUT[0], ay + JOIN_CUT[1])
            has_cut = any(math.hypot(x - ax, y - ay) <= 4 for x, y in on) and \
                any(math.hypot(x - bottom[0], y - bottom[1]) <= 4 for x, y in on)
            joins.append((name, (ax, ay), bottom if has_cut else (ax, ay)))
        if joins:
            snap[g.name] = ([[(x, y) for x, y, _ in p] for p in flat], joins)
    return snap


def _join_restore(font, snap):
    for gname, (before, joins) in snap.items():
        layer = master_layer(font, font.glyphs[gname])
        if layer.components or [len(p) for p in before] != [len(p.nodes) for p in layer.paths]:
            continue  # still a composite, or the outline was rebuilt: no point-for-point match
        moved = []
        for name, a0, b0 in joins:
            a = anchor(layer, name)
            if a is not None:
                moved.append((a0, b0, (a.position.x - a0[0], a.position.y - a0[1])))
        for path, pts in zip(layer.paths, before):
            nodes = path.nodes
            n = len(nodes)
            hold = []  # (weight, dx, dy) per point
            for p in pts:
                best = None
                for a0, b0, (dx, dy) in moved:
                    d = _seg_distance(p, a0, b0)
                    if d < JOIN_FADE and (best is None or d < best[0]):
                        best = (d, dx, dy)
                if best is None:
                    hold.append((0.0, 0, 0))
                    continue
                d, dx, dy = best
                w = 1.0 if d <= JOIN_HOLD else 1 - (d - JOIN_HOLD) / (JOIN_FADE - JOIN_HOLD)
                hold.append((w * w * (3 - 2 * w), dx, dy))
            # a handle is held at least as firmly as its own point, so the
            # stroke still leaves the join at its original angle
            for i in range(n):
                if nodes[i].type != "offcurve":
                    continue
                j = (i - 1) % n if nodes[(i - 1) % n].type != "offcurve" else (i + 1) % n
                if hold[j][0] > hold[i][0]:
                    hold[i] = hold[j]
            for node, p, (w, dx, dy) in zip(nodes, pts, hold):
                if not w:
                    continue
                q = node.position
                tx, ty = p[0] + dx, p[1] + dy
                node.position = Point(round(q.x + w * (tx - q.x)), round(q.y + w * (ty - q.y)))


def keeping_joins(fn):
    def step(font):
        snap = _join_snapshot(font)
        fn(font)
        _join_restore(font, snap)
    return step


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
        (keeping_joins(fn) if name in JOIN_SAFE_STEPS else fn)(font)
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
