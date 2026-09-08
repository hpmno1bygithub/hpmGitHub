# -*- coding: utf-8 -*-
"""FIX100 viewport-first terrain packing for the existing one-draw Metal path.

No world/material/asset mutation and no Metal calls.  Quads use center x/y,
HALF width/height and straight RGBA, exactly as InstanceData does.  Normal
packing is lossless inside the viewport.  Only pathological over-budget views
use a bounded display-only raster; never drop a row-major suffix of terrain.
"""
import math
from array import array


def view_window(camera_x, camera_y, viewport_w, viewport_h, tile_size=40.0, padding=1):
    """Stable tile-aligned window: covers camera interpolation and edge shake."""
    step = max(1.0, float(tile_size))
    x, y = float(camera_x), float(camera_y)
    w, h = max(1.0, float(viewport_w)), max(1.0, float(viewport_h))
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError('Non-finite terrain viewport')
    pad = max(1, int(padding))
    return ((math.floor(x / step) - pad) * step,
            (math.floor(y / step) - pad) * step,
            (math.ceil((x + w) / step) + pad) * step,
            (math.ceil((y + h) / step) + pad) * step)


def clip_quad(q, bounds):
    """Discard OFF-screen geometry before applying any upload limit."""
    x, y, hw, hh = q[:4]
    if hw <= 0.0 or hh <= 0.0 or q[7] <= 0.001:
        return None
    left, top, right, bottom = bounds
    ql, qt, qr, qb = x-hw, y-hh, x+hw, y+hh
    if qr <= left or ql >= right or qb <= top or qt >= bottom:
        return None
    if ql >= left and qr <= right and qt >= top and qb <= bottom:
        return q
    l, t, r, b = max(ql, left), max(qt, top), min(qr, right), min(qb, bottom)
    return ((l+r)*0.5, (t+b)*0.5, (r-l)*0.5, (b-t)*0.5, *q[4:8])


def capacity_for(required, base=8192, ceiling=65536):
    """Power-of-two growth, bounded, and only when the visible payload needs it."""
    required, base, ceiling = int(required), int(base), int(ceiling)
    if required < 0 or base < 1 or ceiling < base or required > ceiling:
        raise ValueError('Terrain stream capacity outside safety bounds')
    capacity = base
    while capacity < required:
        capacity = min(ceiling, capacity * 2)
    return capacity


def _rgba32(rgba):
    channels = [max(0, min(255, round(float(v)*255))) for v in rgba]
    return (channels[0] << 24) | (channels[1] << 16) | (channels[2] << 8) | channels[3]


def _over(src, dst):
    """Straight-alpha source-over, only used by the extreme-load display LOD."""
    sa, da = src & 255, dst & 255
    if sa >= 255 or not da:
        return src
    a = sa + da * (255-sa) / 255.0
    if a <= 0:
        return 0
    values = []
    for shift in (24, 16, 8):
        s, d = (src >> shift) & 255, (dst >> shift) & 255
        values.append(round((s*sa + d*da*(255-sa)/255.0) / a))
    return (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | round(a)


def raster_fallback(quads, bounds, limit):
    """Bounded nearest-cell terrain proxy, preserving alpha and paint order.

    Normal scenes never call this. At most ``limit`` cells and output quads
    exist regardless of authored pixel complexity. No image library required.
    Tiny occupied runs mark at least one cell, so a thin vine isn't removed
    merely because it falls between sample centers. Source artwork is untouched.
    """
    limit = max(1, int(limit))
    left, top, right, bottom = (float(v) for v in bounds)
    w, h = max(1.0, right-left), max(1.0, bottom-top)
    cols = min(limit, max(1, int(math.sqrt(limit*w/h))))
    rows = max(1, min(int(limit//cols), int(math.ceil(h/(w/cols)))))
    dx, dy = w/cols, h/rows
    grid = array('I', [0]) * (cols*rows)
    color_cache = {}
    for raw in quads:
        q = clip_quad(raw, bounds)
        if q is None:
            continue
        x, y, hw, hh = q[:4]
        color_key = tuple(q[4:8])
        color = color_cache.get(color_key)
        if color is None:
            color = _rgba32(color_key)
            # A source with many unique colours must not create unbounded
            # auxiliary cache state in addition to the bounded pixel grid.
            if len(color_cache) < 1024:
                color_cache[color_key] = color
        x0 = max(0, int(math.ceil((x-hw-left)/dx - .5)))
        x1 = min(cols, int(math.ceil((x+hw-left)/dx - .5)))
        y0 = max(0, int(math.ceil((y-hh-top)/dy - .5)))
        y1 = min(rows, int(math.ceil((y+hh-top)/dy - .5)))
        if x1 <= x0:
            x0 = max(0, min(cols-1, int((x-left)/dx))); x1 = x0+1
        if y1 <= y0:
            y0 = max(0, min(rows-1, int((y-top)/dy))); y1 = y0+1
        if (color & 255) >= 255:
            fill = array('I', [color]) * (x1-x0)
            for yy in range(y0, y1):
                start = yy*cols+x0
                grid[start:start+x1-x0] = fill
        else:
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    i = yy*cols+xx
                    grid[i] = _over(color, grid[i])

    # Lossless same-colour run merge on the display-only grid.
    active, output = {}, []
    def emit(rect):
        x0, y0, x1, y1, c = rect
        output.append((left+(x0+x1)*dx*.5, top+(y0+y1)*dy*.5,
                       (x1-x0)*dx*.5, (y1-y0)*dy*.5,
                       ((c >> 24)&255)/255., ((c >> 16)&255)/255.,
                       ((c >> 8)&255)/255., (c&255)/255.))
    for yy in range(rows):
        next_active = {}
        xx = 0
        while xx < cols:
            c = grid[yy*cols+xx]
            if not (c & 255):
                xx += 1; continue
            start = xx; xx += 1
            while xx < cols and grid[yy*cols+xx] == c:
                xx += 1
            key = (start, xx, c)
            old = active.pop(key, None)
            next_active[key] = (start, old[1] if old else yy, xx, yy+1, c)
        for rect in active.values():
            emit(rect)
        active = next_active
    for rect in active.values():
        emit(rect)
    assert len(output) <= limit
    return tuple(output)


def build_stream(batches, bounds, limit=65536):
    """Pack all intersecting quads; never truncate a complete tile or chunk."""
    visible = []
    raw_count = 0
    for batch in batches:
        raw_count += len(batch)
        for raw in batch:
            q = clip_quad(raw, bounds)
            if q is not None:
                visible.append(q)
    visible_count = len(visible)
    reduced = visible_count > int(limit)
    quads = raster_fallback(visible, bounds, limit) if reduced else tuple(visible)
    return quads, dict(candidate_quads=raw_count, visible_quads=visible_count,
                       emitted_quads=len(quads), offscreen_quads=raw_count-visible_count,
                       simplified=bool(reduced), dropped_terrain_suffix=False,
                       window=list(bounds))
