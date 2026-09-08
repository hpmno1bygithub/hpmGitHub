# -*- coding: utf-8 -*-
"""Procedural pixel animation generator for creature sprites.

FIX24 refines ground walking and connected slime deformation.
FIX26 keeps the FIX25 flying pass and adds a connected frog hop.

FIX25 adds a dedicated flying-creature pass:
1. Birds use an eight-frame wing-beat cycle with tucked legs instead of a
   biped/leg-walk motion.
2. Dragonfly/damselfly wings animate independently from the rigid body, using
   their existing translucent wing pixels for a high-frequency beat.

As before, every transform stays on integer pixel cells only: no rotation,
filtering, alpha interpolation, or image resampling.
"""
import math

TRANSPARENT = "#00000000"

QUADRUPEDS = {
    "boar", "wolf", "deer", "otter", "jaguar", "capybara", "snow_wolf",
    "mountain_goat", "village_dog", "village_rat", "infernal_goat",
}
LOW_QUADRUPEDS = {"sea_turtle", "crocodile", "sand_lizard", "cave_snorble", "flame_turtle", "crystal_lizard"}
HOPPERS = {"snow_hare"}
FROGS = {"swamp_frog"}
FISH = {"sardine", "mackerel", "sea_bass", "carp", "crucian_carp", "freshwater_bass"}
SNAKES = {"desert_snake", "swamp_snake", "jungle_snake"}
BIRDS = {"gull", "heron", "mallard", "kingfisher", "giant_bago_bird", "plane_monster"}
ARTHROPODS = {"shore_crab", "mangrove_crab", "desert_scorpion", "jungle_spider", "cave_spider", "giant_spider_monster", "desert_beetle"}
FLYING_INSECTS = {"dragonfly", "damselfly"}
SLIMES = {"slime", "swamp_slime", "ice_slime", "blue_poop", "burning_slime", "crystal_slime"}
BIPEDS = {"bandit", "villager", "zombie", "dungeon_bandit_mage", "fire_zombie", "lightning_zombie", "toilet_man", "speaker_man", "crystal_knight", "crystal_skeleton"}
BOSSES = {"abyss_colossus"}
BATS = {"cave_bat", "flying_imp"}
WORMS = {"giant_worm"}
CREEPERS = {"creeper"}
GHOSTS = {"ghost", "exploding_wisp"}


def _opaque(value):
    s = str(value or "").strip().upper()
    return bool(s) and not s.endswith("00")


def _blank(n):
    return [[TRANSPARENT for _x in range(n)] for _y in range(n)]


def _copy(grid):
    return [list(row) for row in grid]


def _normalize(grid):
    n = max(1, len(grid) if isinstance(grid, list) else 0)
    out = _blank(n)
    for y in range(min(n, len(grid) if isinstance(grid, list) else 0)):
        row = grid[y] if isinstance(grid[y], list) else []
        for x in range(min(n, len(row))):
            out[y][x] = str(row[x])
    return out


def _bounds(grid):
    xs = []
    ys = []
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if _opaque(value):
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0, 0, max(0, len(grid) - 1), max(0, len(grid) - 1))
    return (min(xs), min(ys), max(xs), max(ys))


def _clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


def _shift_selected(grid, selector, dx=0, dy=0, keep_joint=None):
    """Move selected opaque cells by an integer offset."""
    n = len(grid)
    src = _copy(grid)
    out = _copy(grid)
    selected = []
    for y, row in enumerate(src):
        for x, value in enumerate(row):
            if _opaque(value) and selector(x, y):
                selected.append((x, y, value))
                if keep_joint is None or not keep_joint(x, y):
                    out[y][x] = TRANSPARENT
    for x, y, value in selected:
        tx = x + int(dx)
        ty = y + int(dy)
        if 0 <= tx < n and 0 <= ty < n:
            out[ty][tx] = value
    return out


def _shift_all(grid, dx=0, dy=0):
    n = len(grid)
    out = _blank(n)
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if not _opaque(value):
                continue
            tx = x + int(dx)
            ty = y + int(dy)
            if 0 <= tx < n and 0 <= ty < n:
                out[ty][tx] = value
    return out


def _warp(grid, offset_fn):
    """Move every opaque source pixel to one integer destination."""
    n = len(grid)
    out = _blank(n)
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if not _opaque(value):
                continue
            dx, dy = offset_fn(x, y)
            tx = x + int(dx)
            ty = y + int(dy)
            if 0 <= tx < n and 0 <= ty < n:
                out[ty][tx] = value
    return out


def _squash(grid, factor, lift=0):
    """Nearest-cell vertical squash/stretch around the lowest opaque row."""
    x0, y0, x1, y1 = _bounds(grid)
    n = len(grid)
    out = _blank(n)
    ground = y1
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if not _opaque(value):
                continue
            ty = ground - int(round((ground - y) * float(factor))) - int(lift)
            ty = _clamp(ty, 0, n - 1)
            out[ty][x] = value
    return out


def _seal_small_gaps(grid, max_gap=1):
    """Reconnect tiny 1-cell cracks after integer warp/squash.

    This is especially important for blob-like creatures such as slimes where a
    detached top cap reads as layers splitting apart.
    """
    n = len(grid)
    out = _copy(grid)
    # Horizontal pass.
    for y in range(n):
        for x in range(1, n - 1):
            if _opaque(out[y][x]):
                continue
            for gap in range(1, max_gap + 1):
                left = x - gap
                right = x + gap
                if left >= 0 and right < n and _opaque(out[y][left]) and _opaque(out[y][right]):
                    out[y][x] = out[y][left]
                    break
    # Vertical pass.
    base = _copy(out)
    for x in range(n):
        for y in range(1, n - 1):
            if _opaque(base[y][x]):
                continue
            for gap in range(1, max_gap + 1):
                top = y - gap
                bottom = y + gap
                if top >= 0 and bottom < n and _opaque(base[top][x]) and _opaque(base[bottom][x]):
                    out[y][x] = base[top][x]
                    break
    return out


def _align_bottom(frame, base):
    """Keep the lowest opaque row locked to the original ground contact."""
    _, _, _, fy = _bounds(frame)
    _, _, _, by = _bounds(base)
    return _shift_all(frame, 0, int(by - fy))


def _profile(species):
    sp = str(species)
    if sp in QUADRUPEDS:
        return "quadruped"
    if sp in LOW_QUADRUPEDS:
        return "low_quadruped"
    if sp in HOPPERS:
        return "hopper"
    if sp in FROGS:
        return "frog"
    if sp in FISH:
        return "fish"
    if sp in SNAKES:
        return "snake"
    if sp in BIRDS:
        return "bird"
    if sp in BATS:
        return "bat"
    if sp in WORMS:
        return "worm"
    if sp in CREEPERS:
        return "creeper"
    if sp in GHOSTS:
        return "ghost"
    if sp in ARTHROPODS:
        return "arthropod"
    if sp in FLYING_INSECTS:
        return "flying_insect"
    if sp in SLIMES:
        return "slime"
    if sp in BOSSES:
        return "boss"
    if sp in BIPEDS:
        return "biped"
    return "generic"


def _quadruped_idle(base, low=False):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    front_x = x0 + int(round(w * 0.70))
    rear_x = x0 + int(round(w * 0.20))
    head_y = y0 + int(round(h * 0.55))
    tail_y = y0 + int(round(h * 0.55))

    f0 = _copy(base)
    f1 = _shift_selected(
        base,
        lambda x, y: x >= front_x and y <= head_y,
        0, -1 if not low else 0,
    )
    f2 = _copy(base)
    f3 = _shift_selected(
        base,
        lambda x, y: x <= rear_x and y <= tail_y,
        0, -1,
    )
    return [f0, f1, f2, f3]


def _quadruped_move(base, low=False):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    leg_y = y0 + int(round(h * (0.70 if not low else 0.77)))
    body_left = x0 + int(round(w * 0.14))
    body_right = x0 + int(round(w * 0.82))
    # FIX24: phase-corrected order. Compared with FIX23, the stride direction is
    # inverted so a right-facing animal now reads as stepping forward rather
    # than moonwalking backwards.
    phases = (0, -1, -1, 0, 1, 1)
    frames = []
    for fi, stride in enumerate(phases):
        def offsets(x, y, stride=stride, fi=fi):
            dx = 0
            dy = 0
            if y >= leg_y and body_left <= x <= body_right:
                rel = (x - body_left) / float(max(1, body_right - body_left + 1))
                slot = min(3, max(0, int(rel * 4.0)))
                direction = 1 if slot in (0, 2) else -1
                dx = int(stride * direction)
                if abs(stride) and ((slot + fi) % 2 == 0):
                    dy = -1
            if x <= x0 + int(round(w * 0.16)) and y <= y0 + int(round(h * 0.60)):
                dy += -1 if stride > 0 else (1 if stride < 0 else 0)
            if x >= x0 + int(round(w * 0.78)) and y <= y0 + int(round(h * 0.55)):
                dy += -1 if fi in (1, 4) and not low else 0
            return dx, dy
        frames.append(_warp(base, offsets))
    return frames


def _hopper_idle(base):
    return [
        _copy(base),
        _squash(base, 0.96, 0),
        _copy(base),
        _squash(base, 1.03, 0),
    ]


def _hopper_move(base):
    seq = (
        (0.94, 0), (1.04, 1), (1.08, 1),
        (1.00, 0), (0.92, 0), (1.02, 1),
    )
    return [_squash(base, factor, lift) for factor, lift in seq]


def _frog_connected_squash(base, factor):
    """Compress a frog without tearing its body into detached pixel layers."""
    out = _squash(base, factor, 0)
    out = _seal_small_gaps(out, 1)
    out = _align_bottom(out, base)
    return out


def _frog_idle(base):
    # Tiny breathing/compression only; feet remain planted and body connected.
    return [
        _copy(base),
        _frog_connected_squash(base, 0.97),
        _copy(base),
        _frog_connected_squash(base, 0.99),
    ]


def _frog_move(base):
    """Six-frame hop cycle with whole-body lift instead of sliced stretching."""
    crouch = _frog_connected_squash(base, 0.90)
    land = _frog_connected_squash(base, 0.88)
    # Airborne frames move the entire opaque body together, so no row can split
    # away from another even on the 24x24 authored frog canvas.
    return [
        crouch,
        _shift_all(base, 0, -1),
        _shift_all(base, 0, -2),
        _shift_all(base, 0, -1),
        land,
        _copy(base),
    ]


def _fish_idle(base):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    tail_end = x0 + int(round(w * 0.28))
    frames = []
    for dy in (0, -1, 0, 1):
        frames.append(_shift_selected(base, lambda x, y, te=tail_end: x <= te, 0, dy))
    return frames


def _fish_move(base):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    phases = (0.0, math.pi / 3, 2 * math.pi / 3, math.pi, 4 * math.pi / 3, 5 * math.pi / 3)
    frames = []
    for phase in phases:
        def offsets(x, y, phase=phase):
            rel = (x - x0) / float(max(1, w - 1))
            if rel > 0.58:
                return (0, 0)
            amp = 1.0 if rel > 0.25 else 1.6
            dy = int(round(math.sin(phase + rel * math.pi) * amp))
            return (0, dy)
        frames.append(_warp(base, offsets))
    return frames


def _snake_frames(base, moving):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    count = 6 if moving else 4
    frames = []
    for i in range(count):
        phase = (2.0 * math.pi * i) / float(count)
        amp = 1.0 if moving else 0.55
        def offsets(x, y, phase=phase, amp=amp):
            rel = (x - x0) / float(max(1, w - 1))
            dy = int(round(math.sin(rel * math.pi * 2.0 + phase) * amp))
            return (0, dy)
        frames.append(_warp(base, offsets))
    return frames


def _bird_idle(base):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    head_x = x0 + int(round(w * 0.68))
    head_y = y0 + int(round(h * 0.55))
    return [
        _copy(base),
        _shift_selected(base, lambda x, y: x >= head_x and y <= head_y, 0, -1),
        _copy(base),
        _shift_selected(base, lambda x, y: x >= head_x and y <= head_y, 0, 1),
    ]


def _most_common_colors(grid, x0, y0, x1, y1):
    counts = {}
    for y in range(max(0, int(y0)), min(len(grid), int(y1) + 1)):
        row = grid[y]
        for x in range(max(0, int(x0)), min(len(row), int(x1) + 1)):
            value = row[x]
            if _opaque(value):
                counts[value] = counts.get(value, 0) + 1
    return [pair[0] for pair in sorted(counts.items(), key=lambda pair: (-pair[1], str(pair[0])))]


def _bird_flight_base(base):
    """Remove dangling legs while retaining the authored body/head/tail.

    The body bottom is derived from row density rather than a fixed Y fraction,
    so the same rule works for gulls, ducks, kingfishers and tall herons.
    """
    x0, y0, x1, y1 = _bounds(base)
    row_counts = []
    for y in range(y0, y1 + 1):
        row_counts.append((y, sum(1 for value in base[y] if _opaque(value))))
    max_count = max([count for _y, count in row_counts] or [1])
    threshold = max(2, int(math.ceil(max_count * 0.50)))
    body_rows = [y for y, count in row_counts if count >= threshold]
    body_bottom = max(body_rows) if body_rows else y1

    out = _copy(base)
    for y in range(body_bottom + 1, len(out)):
        for x in range(len(out[y])):
            out[y][x] = TRANSPARENT
    return out, body_bottom


def _paint_tapered_segment(grid, ax, ay, tx, ty, color, root_thickness=2, min_thickness=1):
    """Raster a feather-like integer-pixel segment from root to wing tip."""
    out = _copy(grid)
    n = len(out)
    steps = max(1, abs(int(tx) - int(ax)), abs(int(ty) - int(ay)))
    verticalish = abs(int(ty) - int(ay)) >= abs(int(tx) - int(ax))
    for i in range(1, steps + 1):
        t = i / float(steps)
        x = int(round(int(ax) + (int(tx) - int(ax)) * t))
        y = int(round(int(ay) + (int(ty) - int(ay)) * t))
        thickness = max(int(min_thickness), int(round(float(root_thickness) * (1.0 - t))))
        for q in range(-thickness, thickness + 1):
            px = x + q if verticalish else x
            py = y if verticalish else y + q
            if 0 <= px < n and 0 <= py < n:
                out[py][px] = color
    return out


def _bird_move(base):
    """Eight-frame side-view flight loop: wings flap; feet are tucked away.

    FIX25 replaces the old lower-body/leg swing that made birds walk. The bird
    is lifted slightly inside its authored canvas to reserve room for the
    downstroke, then two layered feather strokes create a readable wing beat.
    """
    flight, body_bottom = _bird_flight_base(base)
    x0, y0, x1, _ = _bounds(flight)
    w = max(1, x1 - x0 + 1)
    h = max(1, body_bottom - y0 + 1)

    # Reserve 3 cells below the body for the downstroke; this is visual only.
    lift = min(3, max(2, len(base) - 1 - body_bottom + 1))
    flight = _shift_all(flight, 0, -lift)
    x0, y0, x1, _ = _bounds(flight)
    body_bottom -= lift

    anchor_x = x0 + int(round(w * 0.48))
    anchor_y = y0 + int(round(h * 0.55))
    palette = _most_common_colors(
        flight, x0, y0,
        min(x1, x0 + int(round(w * 0.68))),
        body_bottom,
    )
    primary = palette[0] if palette else "#B8C7CFFF"
    secondary = palette[1] if len(palette) > 1 else primary

    up = max(4, int(round(h * 0.72)))
    back = max(4, int(round(w * 0.38)))
    tips = (
        (-2, -up),
        (-max(3, back - 1), -max(3, up - 2)),
        (-back, -1),
        (-max(3, back - 1), 2),
        (-2, max(4, up - 1)),
        (-max(3, back - 1), 2),
        (-back, -1),
        (-max(3, back - 1), -max(3, up - 2)),
    )

    frames = []
    for dx, dy in tips:
        frame = _copy(flight)
        # Rear wing first, slightly shorter/different tone, then the near wing.
        frame = _paint_tapered_segment(
            frame,
            anchor_x + 1, anchor_y,
            anchor_x + 1 + int(round(dx * 0.78)),
            anchor_y + int(round(dy * 0.78)),
            secondary, 1, 1,
        )
        frame = _paint_tapered_segment(
            frame,
            anchor_x, anchor_y,
            anchor_x + dx, anchor_y + dy,
            primary, 2, 1,
        )
        frames.append(frame)
    return frames


def _arthropod_frames(base, moving):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    center = (x0 + x1) * 0.5
    lower = y0 + int(round(h * 0.58))
    count = 6 if moving else 4
    frames = []
    for fi in range(count):
        phase = (0, -1, 0, 1, 0, -1)[fi] if moving else (0, -1, 0, 1)[fi]
        def offsets(x, y, phase=phase, fi=fi):
            if y < lower:
                return (0, 0)
            side = -1 if x < center else 1
            dx = side * phase
            dy = -1 if moving and phase and ((x + fi) % 2 == 0) else 0
            return (dx, dy)
        frames.append(_warp(base, offsets))
    return frames


def _hex_alpha(value):
    s = str(value or "").strip().lstrip("#")
    if len(s) == 8:
        try:
            return int(s[-2:], 16)
        except Exception:
            return 255
    return 255


def _flying_insect_frames(base, moving, delicate=False):
    """High-frequency four-wing beat with a stable body.

    Default dragonfly/damselfly art already marks wings with translucent RGBA
    pixels. FIX25 moves only those wing cells, tapered from a fixed root, rather
    than shifting the whole upper half of the sprite (which visually tore the
    insect into pieces). Dragonflies use a wider beat; damselflies use a finer
    amplitude.
    """
    n = len(base)
    x0, y0, x1, y1 = _bounds(base)
    wing_cells = []
    body_cells = []
    for y, row in enumerate(base):
        for x, value in enumerate(row):
            if not _opaque(value):
                continue
            if _hex_alpha(value) < 250:
                wing_cells.append((x, y, value))
            else:
                body_cells.append((x, y, value))

    # Fallback for future insect art that has fully opaque wings.
    if not wing_cells:
        mid_y = y0 + int(round(max(1, y1 - y0 + 1) * 0.52))
        count = 8 if moving else 4
        seq = (0, -1, -2, -1, 0, 1, 2, 1) if moving else (0, -1, 0, 1)
        return [
            _shift_selected(base, lambda x, y, my=mid_y: y < my, 0, seq[i])
            for i in range(count)
        ]

    wing_x0 = min(x for x, _y, _value in wing_cells)
    wing_x1 = max(x for x, _y, _value in wing_cells)
    if body_cells:
        body_cy = sum(y for _x, y, _value in body_cells) / float(len(body_cells))
    else:
        body_cy = (y0 + y1) * 0.5

    phases = (0, 1, 2, 1, 0, -1, -2, -1) if moving else (0, 1, 0, -1)
    frames = []
    amp_scale = 0.65 if delicate else 1.0

    for fi, phase in enumerate(phases):
        out = _blank(n)
        # Opaque body/head/tail remain rigid and readable.
        for x, y, value in body_cells:
            out[y][x] = value

        sweep = math.sin((2.0 * math.pi * fi) / float(max(1, len(phases))))
        for x, y, value in wing_cells:
            rel = (x - wing_x0) / float(max(1, wing_x1 - wing_x0))
            rel = max(0.0, min(1.0, rel))
            side = -1 if y < body_cy else 1
            dy = int(round(side * phase * rel * amp_scale))
            dx = int(round(sweep * rel)) if moving else 0
            tx = x + dx
            ty = y + dy
            if 0 <= tx < n and 0 <= ty < n:
                out[ty][tx] = value

        # Pin the translucent wing roots so a high-frequency beat never looks
        # detached from the thorax on the 16x16 default canvas.
        for x, y, value in wing_cells:
            if x <= wing_x0 + 1:
                out[y][x] = value
        frames.append(out)
    return frames


def _slime_deform(base, phase, crawl=0.0, squash=0.0):
    """Connected blob deformation for slimes.

    Instead of pure stretch/squash, the body gently wriggles with a travelling
    wave while the bottom stays planted. A tiny gap sealing pass keeps the blob
    visually unified on a 16x16 canvas.
    """
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)

    def offsets(x, y):
        relx = (x - x0) / float(max(1, w - 1))
        rely = (y - y0) / float(max(1, h - 1))
        top_weight = max(0.0, 1.0 - rely)
        body_wave = math.sin(phase + relx * math.pi * 1.2)
        dx = int(round(body_wave * crawl * (0.25 + top_weight * 0.75)))
        dy = int(round(math.cos(phase + relx * math.pi * 1.1) * 0.55 * (0.15 + top_weight * 0.85)))
        dy += int(round((0.5 - rely) * squash))
        # Keep the bottom planted: it can lift slightly but should never sink.
        if y >= y1 - 1 and dy > 0:
            dy = 0
        return dx, dy

    out = _warp(base, offsets)
    out = _seal_small_gaps(out, 1)
    out = _align_bottom(out, base)
    return out


def _slime_idle(base):
    return [
        _copy(base),
        _slime_deform(base, 0.9, 0.18, -0.20),
        _copy(base),
        _slime_deform(base, 2.3, -0.12, 0.22),
    ]


def _slime_move(base):
    seq = [
        (0.0, 0.00, -0.25),
        (0.8, 0.80, 0.35),
        (1.7, 1.05, 0.48),
        (2.6, 0.20, 0.00),
        (3.5, -0.65, -0.18),
        (4.4, -0.95, 0.30),
    ]
    return [_slime_deform(base, phase, crawl, squash) for phase, crawl, squash in seq]


def _bat_frames(base, moving):
    """Membrane-wing beat for a side-view cave bat with a stable body/head."""
    x0,y0,x1,y1=_bounds(base); w=max(1,x1-x0+1)
    anchor_x=x0+int(round(w*0.58))
    seq=(0,-2,-4,-2,0,2,4,2) if moving else (0,-1,0,1)
    frames=[]
    for bend in seq:
        def offsets(x,y,bend=bend):
            if x>=anchor_x:return (0,0)
            rel=max(0.0,min(1.0,(anchor_x-x)/float(max(1,anchor_x-x0))))
            # Root barely moves; distal membrane has the full beat.
            dy=int(round(float(bend)*rel))
            return (0,dy)
        frame=_warp(base,offsets)
        frame=_seal_small_gaps(frame,1)
        frames.append(frame)
    return frames


def _worm_frames(base,moving):
    """Large continuous segmented crawl; gap sealing keeps the worm one body."""
    x0,y0,x1,y1=_bounds(base); w=max(1,x1-x0+1)
    count=6 if moving else 4; amp=1.35 if moving else 0.55
    frames=[]
    for i in range(count):
        phase=2.0*math.pi*i/float(count)
        def offsets(x,y,phase=phase):
            rel=(x-x0)/float(max(1,w-1))
            return (0,int(round(math.sin(rel*math.pi*2.0+phase)*amp)))
        frame=_warp(base,offsets)
        frame=_seal_small_gaps(frame,1)
        frame=_align_bottom(frame,base)
        frames.append(frame)
    return frames



def _creeper_frames(base, moving):
    """Tall four-foot cave bomber: tiny alternating feet plus body pulse."""
    x0,y0,x1,y1=_bounds(base); h=max(1,y1-y0+1); w=max(1,x1-x0+1)
    foot_y=y0+int(round(h*0.82)); mid=(x0+x1)*0.5
    seq=(0,-1,0,1) if not moving else (0,-1,-1,0,1,1)
    frames=[]
    for fi,stride in enumerate(seq):
        def offsets(x,y,stride=stride,fi=fi):
            if y>=foot_y:
                side=-1 if x<mid else 1
                return (side*stride,-1 if stride and ((x+fi)&1)==0 else 0)
            # Upper body stays coherent and only breathes by one cell.
            if moving and y<y0+int(h*.35) and fi in (2,5):
                return (0,-1)
            return (0,0)
        fr=_warp(base,offsets);fr=_seal_small_gaps(fr,1);fr=_align_bottom(fr,base);frames.append(fr)
    return frames


def _ghost_frames(base, moving):
    """Spectral float: head remains stable while the ragged tail waves."""
    x0,y0,x1,y1=_bounds(base);w=max(1,x1-x0+1);h=max(1,y1-y0+1)
    count=6 if moving else 4;amp=1.15 if moving else 0.55
    frames=[]
    for i in range(count):
        phase=2.0*math.pi*i/float(count)
        def offsets(x,y,phase=phase):
            relx=(x-x0)/float(max(1,w-1)); rely=(y-y0)/float(max(1,h-1))
            tail=max(0.0,(rely-.42)/.58)
            dx=int(round(math.sin(phase+relx*math.pi*1.5)*amp*tail))
            dy=int(round(math.sin(phase)*(.7 if moving else .35)*(1.0-tail*.35)))
            return dx,dy
        fr=_warp(base,offsets);fr=_seal_small_gaps(fr,1);frames.append(fr)
    return frames


def _pulse_attack(base, amount_seq):
    frames=[]
    for factor in amount_seq:
        fr=_squash(base,float(factor),0);fr=_seal_small_gaps(fr,1);fr=_align_bottom(fr,base);frames.append(fr)
    return frames


def _spectral_lunge(base):
    return [_shift_all(base,dx,dy) for dx,dy in ((0,0),(1,-1),(2,0),(1,1),(0,0))]

def _upper_reach_frames(base, amount_seq):
    """Simple connected upper-body reach used by zombie/mage attack clips."""
    x0,y0,x1,y1=_bounds(base); w=max(1,x1-x0+1); h=max(1,y1-y0+1)
    cut_y=y0+int(round(h*0.62)); front_x=x0+int(round(w*0.58))
    frames=[]
    for amount in amount_seq:
        frame=_shift_selected(base,lambda x,y,fx=front_x,cy=cut_y: x>=fx and y<=cy,int(amount),0)
        frame=_seal_small_gaps(frame,1)
        frames.append(frame)
    return frames


def _spider_attack(base):
    x0,y0,x1,y1=_bounds(base); mid=(x0+x1)//2
    return [_shift_selected(base,lambda x,y,m=mid: x>=m,a,0) for a in (0,1,2,1)]


def _worm_attack(base):
    return [_shift_all(base,a,0) for a in (0,1,2,1,0)]


def _biped_idle(base):
    x0, y0, x1, y1 = _bounds(base)
    h = max(1, y1 - y0 + 1)
    head_y = y0 + int(round(h * 0.28))
    return [
        _copy(base),
        _shift_selected(base, lambda x, y: y <= head_y, 0, -1),
        _copy(base),
        _shift_selected(base, lambda x, y: y <= head_y, 0, 1),
    ]


def _biped_move(base):
    x0, y0, x1, y1 = _bounds(base)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    center = (x0 + x1) * 0.5
    leg_y = y0 + int(round(h * 0.70))
    arm_y0 = y0 + int(round(h * 0.30))
    arm_y1 = y0 + int(round(h * 0.66))
    outer = w * 0.18
    strides = (0, -1, -1, 0, 1, 1)
    frames = []
    for fi, stride in enumerate(strides):
        def offsets(x, y, stride=stride, fi=fi):
            if y >= leg_y:
                direction = -1 if x < center else 1
                return (direction * stride, -1 if stride and ((fi + int(x)) % 2 == 0) else 0)
            if arm_y0 <= y <= arm_y1 and (x <= x0 + outer or x >= x1 - outer):
                direction = 1 if x < center else -1
                return (direction * stride, 0)
            return (0, 0)
        frames.append(_warp(base, offsets))
    return frames


def _boss_idle(base):
    """Heavy giant breathing/core pulse without tearing the armored body."""
    return [_copy(base), _squash(base,0.985,0), _copy(base), _squash(base,1.015,0)]


def _boss_move(base):
    frames=_biped_move(base);out=[]
    for i,fr in enumerate(frames):
        if i in (1,4): fr=_shift_all(fr,0,1)
        out.append(_seal_small_gaps(fr,1))
    return out


def _boss_slam(base):
    x0,y0,x1,y1=_bounds(base);h=max(1,y1-y0+1);cut=y0+int(round(h*.70));frames=[]
    for dy in (0,-1,-2,0,2,0):
        fr=_shift_selected(base,lambda x,y,cy=cut:y<=cy,0,dy)
        fr=_seal_small_gaps(fr,1);fr=_align_bottom(fr,base);frames.append(fr)
    return frames


def _boss_charge(base):
    return [_seal_small_gaps(_squash(base,f,0),1) for f in (1.0,.94,.90,.96,1.02,1.0)]


def _generic_idle(base):
    return [_copy(base), _squash(base, 0.98), _copy(base), _squash(base, 1.02)]


def _generic_move(base):
    x0, y0, x1, y1 = _bounds(base)
    lower = y0 + int(round(max(1, y1 - y0 + 1) * 0.65))
    seq = (0, -1, -1, 0, 1, 1)
    return [_shift_selected(base, lambda x, y, ly=lower: y >= ly, dx, 0) for dx in seq]


def generate_creature_animations(species, base_grid):
    """Generate runtime-ready ``idle`` and ``move`` animation dictionaries."""
    base = _normalize(base_grid)
    profile = _profile(species)
    idle_fps = 4.0

    if profile == "quadruped":
        idle = _quadruped_idle(base, False)
        move = _quadruped_move(base, False)
        move_fps = 8.0
    elif profile == "low_quadruped":
        idle = _quadruped_idle(base, True)
        move = _quadruped_move(base, True)
        move_fps = 7.0
    elif profile == "hopper":
        idle = _hopper_idle(base)
        move = _hopper_move(base)
        move_fps = 7.0
    elif profile == "frog":
        idle = _frog_idle(base)
        move = _frog_move(base)
        move_fps = 7.0
    elif profile == "fish":
        idle = _fish_idle(base)
        move = _fish_move(base)
        move_fps = 8.0
    elif profile == "snake":
        idle = _snake_frames(base, False)
        move = _snake_frames(base, True)
        move_fps = 8.0
    elif profile == "bird":
        idle = _bird_idle(base)
        move = _bird_move(base)
        move_fps = 8.0
    elif profile == "bat":
        idle = _bat_frames(base, False)
        move = _bat_frames(base, True)
        idle_fps = 6.0
        move_fps = 11.0
    elif profile == "worm":
        idle = _worm_frames(base, False)
        move = _worm_frames(base, True)
        move_fps = 7.0
    elif profile == "creeper":
        idle = _creeper_frames(base, False)
        move = _creeper_frames(base, True)
        idle_fps = 5.0
        move_fps = 8.0
    elif profile == "ghost":
        idle = _ghost_frames(base, False)
        move = _ghost_frames(base, True)
        idle_fps = 6.0
        move_fps = 8.0
    elif profile == "arthropod":
        idle = _arthropod_frames(base, False)
        move = _arthropod_frames(base, True)
        move_fps = 9.0
    elif profile == "flying_insect":
        idle = _flying_insect_frames(base, False, str(species) == "damselfly")
        move = _flying_insect_frames(base, True, str(species) == "damselfly")
        idle_fps = 8.0 if str(species) == "damselfly" else 10.0
        move_fps = 12.0 if str(species) == "damselfly" else 14.0
    elif profile == "slime":
        idle = _slime_idle(base)
        move = _slime_move(base)
        move_fps = 7.0
    elif profile == "boss":
        idle = _boss_idle(base)
        move = _boss_move(base)
        idle_fps = 3.0
        move_fps = 5.0
    elif profile == "biped":
        idle = _biped_idle(base)
        move = _biped_move(base)
        move_fps = 8.0
    else:
        idle = _generic_idle(base)
        move = _generic_move(base)
        move_fps = 7.0

    states = {
        "idle": {
            "fps": idle_fps,
            "loop": True,
            "frames": idle,
            "events": {"loop_start": 0, "loop_end": max(0, len(idle) - 1)},
            "generator": "creature_auto_v7",
        },
        "move": {
            "fps": move_fps,
            "loop": True,
            "frames": move,
            "events": {"loop_start": 0, "loop_end": max(0, len(move) - 1)},
            "generator": "creature_auto_v7",
        },
    }
    sp=str(species)
    if sp in ("flying_imp","infernal_goat","burning_slime","flame_turtle",
              "exploding_wisp","crystal_knight","crystal_slime",
              "crystal_skeleton","crystal_lizard"):
        state_name={
            "flame_turtle":"fire_cast", "exploding_wisp":"explode",
            "crystal_skeleton":"crystal_cast",
        }.get(sp,"attack")
        if sp=="flying_imp":frames=_bat_frames(base,True)[:6]
        elif sp=="exploding_wisp":frames=_pulse_attack(base,(1.00,.92,1.06,.88,1.15,1.00))
        elif sp in ("burning_slime","crystal_slime"):frames=_pulse_attack(base,(1.00,.90,.82,1.10,1.05,1.00))
        elif sp in ("crystal_knight","crystal_skeleton"):frames=_upper_reach_frames(base,(0,1,2,3,1,0))
        elif sp in ("infernal_goat","crystal_lizard"):frames=_pulse_attack(base,(1.00,.96,.91,1.04,1.08,1.00))
        else:frames=_upper_reach_frames(base,(0,0,1,2,1,0))
        action_frame=4 if sp=="exploding_wisp" else 3
        states[state_name]={"fps":10.0 if sp in ("flying_imp","exploding_wisp") else 9.0,
                            "loop":False,"frames":frames,"events":{"action":action_frame},
                            "generator":"creature_auto_fix62"}
    elif sp in ("zombie","fire_zombie","lightning_zombie"):
        frames=_upper_reach_frames(base,(0,1,2,2,1,0))
        state_name = "attack" if sp=="zombie" else ("fire_cast" if sp=="fire_zombie" else "lightning_cast")
        states[state_name]={"fps":9.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v7"}
    elif sp == "creeper":
        frames=_pulse_attack(base,(1.00,0.94,1.08,0.90,1.12,1.00))
        states["explode"]={"fps":10.0,"loop":False,"frames":frames,"events":{"action":4},"generator":"creature_auto_v7"}
    elif sp == "blue_poop":
        frames=[_slime_deform(base,p,c,s) for p,c,s in ((0,0,-.3),(.8,1.1,.4),(1.5,1.5,.6),(2.2,.6,.1),(3.0,0,-.2))]
        states["attack"]={"fps":9.0,"loop":False,"frames":frames,"events":{"action":2},"generator":"creature_auto_v7"}
    elif sp == "giant_bago_bird":
        frames=_bird_move(base)[:6]
        states["attack"]={"fps":11.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v7"}
    elif sp == "ghost":
        frames=_spectral_lunge(base)
        states["attack"]={"fps":9.0,"loop":False,"frames":frames,"events":{"action":2},"generator":"creature_auto_v7"}
    elif sp in ("cave_spider", "giant_spider_monster"):
        frames=_spider_attack(base)
        states["attack"]={"fps":9.0 if sp=="giant_spider_monster" else 10.0,"loop":False,"frames":frames,"events":{"action":2},"generator":"creature_auto_v8"}
    elif sp == "cave_snorble":
        frames=_pulse_attack(base,(1.00,0.94,0.90,1.05,1.10,1.00))
        states["attack"]={"fps":8.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v8"}
    elif sp == "plane_monster":
        frames=_bird_move(base)[:6]
        states["attack"]={"fps":12.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v8"}
    elif sp == "giant_worm":
        frames=_worm_attack(base)
        states["attack"]={"fps":8.0,"loop":False,"frames":frames,"events":{"action":2},"generator":"creature_auto_v7"}
    elif sp == "dungeon_bandit_mage":
        frames=_upper_reach_frames(base,(0,0,1,2,1,0))
        states["cast"]={"fps":9.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v7"}
    elif sp == "toilet_man":
        frames=_upper_reach_frames(base,(0,1,2,2,1,0))
        states["attack"]={"fps":8.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v7"}
    elif sp == "speaker_man":
        frames=_upper_reach_frames(base,(0,0,1,2,1,0))
        states["sound_cast"]={"fps":10.0,"loop":False,"frames":frames,"events":{"action":3},"generator":"creature_auto_v7"}
    elif sp == "abyss_colossus":
        states["slam"]={"fps":7.0,"loop":False,"frames":_boss_slam(base),"events":{"action":4},"generator":"creature_auto_v7"}
        states["charge"]={"fps":8.0,"loop":False,"frames":_boss_charge(base),"events":{"action":3},"generator":"creature_auto_v7"}
        fire=_upper_reach_frames(base,(0,1,2,2,1,0))
        lightning=_upper_reach_frames(base,(0,-1,-2,-2,-1,0))
        states["fire_cast"]={"fps":8.0,"loop":False,"frames":fire,"events":{"action":3},"generator":"creature_auto_v7"}
        states["lightning_cast"]={"fps":9.0,"loop":False,"frames":lightning,"events":{"action":3},"generator":"creature_auto_v7"}

    return {"profile": profile, "states": states}
