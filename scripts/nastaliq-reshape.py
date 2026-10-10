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
import math
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
    ("SIN", "f"): 1.05,
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
    #        bottom  entry
    "NUN":   (-0.89, 2.96),
    "QAF":   (-0.91, 2.90),
    "SIN":   (-0.82, 3.01),
    "LAM":   (-0.91, 1.97),
    "AIN":   (-0.82, 3.00),
    "CH_YE": (-0.85, 2.86),
    "JIM":   (-1.05, 4.19),
    "SAD":   (-0.88, 3.39),
    "MIM":   (-2.59, 3.86),
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
        fields[family] = bowl_field(master_layer(font, final), tb * NUQTA, te * NUQTA)
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


# ---------------------------------------------------------------------------
# Step 3b: tall letters
# ---------------------------------------------------------------------------

# Alif is ~4.2 nuqta tall in traditional Nastaliq; Gulzar's is ~3.5, and its
# laam is short by a similar amount. Short ascenders make the strokes look
# heavy for the size of the letters. The stem is stretched in its middle, so
# the pen-cut head and the stroke width are unchanged. Values in nuqta.
ASCENDERS = {
    ("ALIF", "u"): 0.50, ("ALIF", "f"): 0.30,
    ("LAM", "i"): 0.76, ("LAM", "isd"): 0.76, ("LAM", "m"): 0.60,
    ("LAM", "f"): 0.23, ("LAM", "u"): 0.23,
    ("LAM_ALIF", "u"): 0.50, ("LAM_ALIF", "f"): 0.50,
}


def step_ascenders(font):
    targets = []
    for g in font.glyphs:
        m = RASM.match(g.name)
        if m and (m.group(1), m.group(2)) in ASCENDERS:
            targets.append((g.name, ASCENDERS[(m.group(1), m.group(2))]))
    for name, _ in targets:
        decompose_components(font, master_layer(font, font.glyphs[name]))
    for name, amount in targets:
        layer = master_layer(font, font.glyphs[name])
        ys = [nd.position.y for p in layer.paths for nd in p.nodes]
        top = max(ys)
        y0 = 0.35 * top  # stretch between 35% and 65% of the height
        y1 = 0.65 * top
        D = amount * NUQTA

        def field(x, y, y0=y0, y1=y1, D=D):
            if y <= y0:
                return (0, 0)
            w = min((y - y0) / (y1 - y0), 1.0)
            return (0, D * w * w * (3 - 2 * w))

        warp_strokes(layer, field)


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


STEPS = [
    ("scale-and-spacing", step_scale_and_spacing),
    ("proportions", step_proportions),
    ("bowls", step_bowls),
    ("sarkash", step_sarkash),
    ("ascenders", step_ascenders),
    ("line-metrics", step_line_metrics),
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
    font.save(args.output or args.glyphsfile)


if __name__ == "__main__":
    sys.exit(main())
