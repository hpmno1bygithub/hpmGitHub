# -*- coding: utf-8 -*-
"""FIX81 pixel-space portal contact on full/low/middle supporting terrain.

Door art follows TileWorld.surface_anchor_y. Trigger geometry uses the same
surface rather than flooring Player.y to an old integer row. No terrain edits,
new portals, network lookups, or automatic activation are performed here.
"""
import math
from config import TILE_SIZE


def doorway(row):
    if not isinstance(row, dict):
        return None
    try:
        rect = row.get('rect', ())
        x, y, w, h = [float(v) for v in rect[:4]]
        floor = int(row.get('entry_floor', y + h))
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            return None
        return x, y, w, h, floor
    except (ValueError, TypeError, OverflowError):
        return None


def anchor_y(world, row):
    spec = doorway(row)
    if spec is None:
        return None
    x, y, w, h, floor = spec
    tx = int(x + w // 2)
    # Strict landmarks never follow an excavation into a lower tunnel.
    if bool(row.get('requires_floor_entry', False)):
        top = world.solid_top_y(tx, floor)
        return float(top) if top is not None else float(floor * TILE_SIZE)
    return float(world.surface_anchor_y(tx, floor))


def support_tops(world, row, left, right):
    spec = doorway(row)
    if spec is None:
        return ()
    x, y, w, h, floor = spec
    lo = max(int(x), int(math.floor(left / TILE_SIZE)))
    hi = min(int(math.ceil(x + w)) - 1, int(math.floor((right - .001) / TILE_SIZE)))
    out = []
    for tx in range(lo, hi + 1):
        top = world.solid_top_y(tx, floor)
        if top is not None:
            out.append(float(top))
    return tuple(out)


def contains_player(world, row, player, x=None, y=None):
    """Body-width contact, with feet inside the real-height doorway.

    The support row may be fractional. A floor BELOW the authored supporting
    cell does not count for floor-restricted landmarks such as the pyramid.
    """
    spec = doorway(row)
    if spec is None:
        return False
    rx, ry, rw, rh, floor = spec
    try:
        px = float(player.x if x is None else x)
        feet = float(player.y if y is None else y)
        half_width = float(player.width()) * .5
        if not all(math.isfinite(v) for v in (px, feet, half_width)):
            return False
        left, right = px - half_width, px + half_width
        if right <= rx * TILE_SIZE or left >= (rx + rw) * TILE_SIZE:
            return False
        anchor = anchor_y(world, row)
        tops = support_tops(world, row, left, right)
        bottom = max((anchor,) + tops)
        return anchor - rh * TILE_SIZE <= feet <= bottom + 3.25
    except (ValueError, TypeError, OverflowError, AttributeError):
        return False


def floor_entry_allowed(world, row, player, physics):
    if not isinstance(row, dict):
        return False
    if not bool(row.get('requires_floor_entry', False)):
        return True
    if doorway(row) is None:
        return False
    try:
        box = player.bbox()
        tops = support_tops(world, row, float(box[0]), float(box[2]))
        tolerance = min(10.0, max(2.0, float(row.get('entry_y_tolerance_px', 8.0))))
        if not tops or min(abs(float(player.y) - top) for top in tops) > tolerance:
            return False
        # Ask collision geometry, not a possibly stale grounded animation flag.
        return bool(physics.grounded(player))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return False
