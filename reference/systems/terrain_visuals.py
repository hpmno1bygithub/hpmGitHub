# -*- coding: utf-8 -*-
"""Deterministic low-cost pixel detail for ordinary mineable terrain.

Metal terrain chunks reserve five quads per tile.  The renderer already uses
one body quad and, for many materials, one surface quad; this module therefore
returns at most three tiny rectangles.  Different material families receive
different silhouettes/colours, while a staggered edge patch softens hard seams
between unlike neighbouring blocks without changing collision geometry.
"""


_ORE_NAMES = {"copper_ore", "iron_ore", "gold_ore"}
_ROCK_NAMES = {"stone", "marble", "limestone", "igneous_rock"}
_SOIL_NAMES = {"dirt", "grass_dirt", "mud", "swamp_soil", "jungle_soil", "snow_dirt"}
_SAND_NAMES = {"sand", "sea_sand"}


def _seed(name, tx, ty):
    value = 2166136261
    for ch in str(name):
        value = ((value ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    value ^= (int(tx) * 374761393) & 0xFFFFFFFF
    value ^= (int(ty) * 668265263) & 0xFFFFFFFF
    return value & 0xFFFFFFFF


def _mix(a, b, amount):
    t = max(0.0, min(1.0, float(amount)))
    return tuple(float(a[i]) * (1.0 - t) + float(b[i]) * t for i in range(3)) + (
        min(float(a[3]), float(b[3])),
    )


def _tone(color, amount):
    if amount >= 0.0:
        return _mix(color, (1.0, 1.0, 1.0, color[3]), amount)
    return _mix(color, (0.0, 0.0, 0.0, color[3]), -amount)


def _rect(left, top, right, bottom, cx, cy, width, height, color):
    width = max(1.5, min(float(width), max(1.5, right - left)))
    height = max(1.5, min(float(height), max(1.5, bottom - top)))
    cx = max(left + width * 0.5, min(right - width * 0.5, float(cx)))
    cy = max(top + height * 0.5, min(bottom - height * 0.5, float(cy)))
    return (cx, cy, width, height, color)


def _transition_patch(name, body, seed, left, top, right, bottom, neighbours):
    candidates = []
    for side, info in neighbours:
        if not info or str(info.get("name", "")) == str(name):
            continue
        overlap_top = max(float(top), float(info.get("top", top)))
        overlap_bottom = min(float(bottom), float(info.get("bottom", bottom)))
        if overlap_bottom - overlap_top < 4.0:
            continue
        candidates.append((side, info, overlap_top, overlap_bottom))
    if not candidates:
        return None
    side, info, overlap_top, overlap_bottom = candidates[seed % len(candidates)]
    neighbour_color = info.get("color", body)
    color = _mix(body, neighbour_color, 0.34)
    available = overlap_bottom - overlap_top
    height = min(10.0, max(4.0, available * 0.34))
    offset = float((seed >> 9) % 7) / 7.0
    cy = overlap_top + height * 0.5 + max(0.0, available - height) * offset
    width = 3.0 + float((seed >> 6) & 1)
    cx = left + width * 0.5 if side == "left" else right - width * 0.5
    return _rect(left, top, right, bottom, cx, cy, width, height, color)


def terrain_detail_quads(
    name, tx, ty, left, top, right, bottom, body_rgba, top_rgba,
    left_neighbour=None, right_neighbour=None, limit=3,
):
    """Return up to ``limit`` pixel-detail rectangles inside one solid shape.

    Each tuple is ``(center_x, center_y, width, height, rgba)``.  The function
    is pure and deterministic so chunk rebuilds never make terrain shimmer.
    """
    limit = max(0, min(3, int(limit)))
    if limit <= 0 or right - left < 3.0 or bottom - top < 3.0:
        return tuple()

    name = str(name or "")
    seed = _seed(name, tx, ty)
    width = float(right) - float(left)
    height = float(bottom) - float(top)
    x1 = left + width * (0.22 + 0.10 * ((seed >> 2) % 4))
    x2 = left + width * (0.62 + 0.07 * ((seed >> 5) % 3))
    y1 = top + height * (0.26 + 0.08 * ((seed >> 8) % 4))
    y2 = top + height * (0.62 + 0.07 * ((seed >> 11) % 3))
    details = []

    if name in _ORE_NAMES:
        # Metal-rich angular nuggets: two unequal shards plus a glint.
        ore = _tone(top_rgba, 0.08)
        shadow = _tone(body_rgba, -0.22)
        details.append(_rect(left, top, right, bottom, x1, y1, 5.0, 3.0, ore))
        details.append(_rect(left, top, right, bottom, x2, y2, 3.0, 6.0, ore))
        details.append(_rect(left, top, right, bottom, x2 + 3.5, y2 - 3.0, 3.5, 1.8, shadow))
    elif name in _ROCK_NAMES:
        # Offset fracture segments avoid a repeated checkerboard appearance.
        vein = _tone(top_rgba if name == "marble" else body_rgba, 0.16)
        crack = _tone(body_rgba, -0.24)
        details.append(_rect(left, top, right, bottom, x1, y1, 2.0, 7.0, crack))
        details.append(_rect(left, top, right, bottom, x1 + 3.5, y1 + 3.0, 7.0, 2.0, crack))
        details.append(_rect(left, top, right, bottom, x2, y2, 5.5, 2.0, vein))
    elif name in _SAND_NAMES:
        light = _tone(top_rgba, 0.10)
        dark = _tone(body_rgba, -0.15)
        details.append(_rect(left, top, right, bottom, x1, y1, 8.0, 1.8, light))
        details.append(_rect(left, top, right, bottom, x2, y2, 4.0, 2.0, dark))
        details.append(_rect(left, top, right, bottom, x1 + 7.0, y2 - 4.0, 2.0, 2.0, light))
    elif name in _SOIL_NAMES:
        root = _tone(body_rgba, -0.24)
        pebble = _tone(top_rgba, 0.10)
        details.append(_rect(left, top, right, bottom, x1, y1, 6.0, 2.0, root))
        details.append(_rect(left, top, right, bottom, x1 + 2.0, y1 + 3.0, 2.0, 6.0, root))
        details.append(_rect(left, top, right, bottom, x2, y2, 4.0, 3.0, pebble))
    elif name == "wood":
        grain = _tone(body_rgba, -0.22)
        details.append(_rect(left, top, right, bottom, x1, y1, 10.0, 2.0, grain))
        details.append(_rect(left, top, right, bottom, x2, y2, 7.0, 2.0, grain))
        details.append(_rect(left, top, right, bottom, x2 + 2.0, y2 - 3.0, 2.0, 5.0, grain))
    elif name == "village_path":
        seam = _tone(body_rgba, -0.20)
        details.append(_rect(left, top, right, bottom, x1, y1, 2.0, 8.0, seam))
        details.append(_rect(left, top, right, bottom, x1 + 4.0, y1 + 3.0, 8.0, 2.0, seam))
        details.append(_rect(left, top, right, bottom, x2, y2, 6.0, 2.0, _tone(top_rgba, 0.08)))
    else:  # ash and any future mineable material receive neutral flecks.
        details.append(_rect(left, top, right, bottom, x1, y1, 5.0, 2.0, _tone(body_rgba, 0.14)))
        details.append(_rect(left, top, right, bottom, x2, y2, 3.0, 3.0, _tone(body_rgba, -0.18)))
        details.append(_rect(left, top, right, bottom, x1 + 8.0, y2, 2.0, 2.0, _tone(top_rgba, 0.06)))

    transition = _transition_patch(
        name, body_rgba, seed, left, top, right, bottom,
        (("left", left_neighbour), ("right", right_neighbour)),
    )
    if transition is not None and limit > 0:
        # Reserve one slot for a soft, staggered boundary instead of dropping
        # it after the three material marks have filled the GPU tile budget.
        details = details[:max(0, limit - 1)] + [transition]
    return tuple(details[:limit])


__all__ = ["terrain_detail_quads"]
