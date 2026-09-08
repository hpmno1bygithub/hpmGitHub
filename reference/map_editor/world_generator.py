# -*- coding: utf-8 -*-
"""Reality-inspired horizontal ecology generator for Pyto RPG V0.7.7.5.

Design goals:
- 768 x 32 default map: 3x wider and 1/3 as deep as V0.7.5.5 while
  preserving the same total cell count (24,576).
- Ecological sequence: ocean -> coastal mangrove swamp -> inland lake ->
  plains -> rainforest -> snow mountain -> desert.
- Ocean bottom is porous marine sand; pore flow is vertical-only.
- Lake bottom is ordinary saturated soil: pore flow is still vertical-only,
  while free surface water spreads normally only after the soil is full.
- Horizontal subsurface flow exists only in an authored bottom waterway.
- Lake interior never spawns terrestrial trees/grass.
- Mountain conifers stop at a simple gameplay treeline; summit is snow/rock.
"""
import math

from config import (
    WORLD_GEN_DEFAULT_WIDTH,
    WORLD_GEN_DEFAULT_HEIGHT,
    WORLD_GEN_SURFACE_ROW,
    WORLD_GEN_LAKE_SURFACE_ROW,
    UNDERGROUND_START_DEPTH_TILES,
    UNDERGROUND_FIRST_LEVEL_ROW,
    UNDERGROUND_SECOND_LEVEL_ROW,
    UNDERGROUND_DEEP_LEVEL_ROW,
    UNDERGROUND_MIN_WORLD_HEIGHT,
)
from map_editor.model import MapDocument
from systems.biome_system import BIOME_CREATURE_ROTATION
from world.soil_layers import SOIL_LAYER_FULL, SOIL_LAYER_LOW, SOIL_LAYER_MID
from world.biomes import (
    BIOME_OCEAN,
    BIOME_SWAMP,
    BIOME_LAKE,
    BIOME_PLAINS,
    BIOME_VILLAGE,
    BIOME_RAINFOREST,
    BIOME_SNOW_MOUNTAIN,
    BIOME_DESERT,
    default_biome_spans,
    biome_at_tile_x,
)


def _smoothstep(t):
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _hash01(seed, x, y=0, salt=0):
    n = (
        int(seed) * 374761393
        + int(x) * 668265263
        + int(y) * 2147483647
        + int(salt) * 1274126177
    ) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    n ^= n >> 16
    return (n & 0xFFFFFFFF) / 4294967295.0


def _value_noise_1d(seed, x, scale, salt=0):
    scale = max(1.0, float(scale))
    u = float(x) / scale
    x0 = math.floor(u)
    f = _smoothstep(u - x0)
    a = _hash01(seed, x0, salt=salt)
    b = _hash01(seed, x0 + 1, salt=salt)
    return a + (b - a) * f


def _value_noise_2d(seed, x, y, scale, salt=0):
    scale = max(1.0, float(scale))
    ux = float(x) / scale
    uy = float(y) / scale
    x0 = math.floor(ux)
    y0 = math.floor(uy)
    fx = _smoothstep(ux - x0)
    fy = _smoothstep(uy - y0)
    v00 = _hash01(seed, x0, y0, salt)
    v10 = _hash01(seed, x0 + 1, y0, salt)
    v01 = _hash01(seed, x0, y0 + 1, salt)
    v11 = _hash01(seed, x0 + 1, y0 + 1, salt)
    a = v00 + (v10 - v00) * fx
    b = v01 + (v11 - v01) * fx
    return a + (b - a) * fy


def _span_map(spans):
    return {str(row[2]): (int(row[0]), int(row[1])) for row in spans}


def _zone_t(x, spans, biome):
    start, end = _span_map(spans)[str(biome)]
    return max(0.0, min(1.0, (float(x) - start) / max(1.0, float(end - start))))


def _surface_height(seed, x, height, biome, spans):
    """Return top solid row. Smaller y means higher elevation."""
    t = _zone_t(x, spans, biome)
    broad = _value_noise_1d(seed, x, 48.0, 801) - 0.5
    fine = _value_noise_1d(seed, x, 15.0, 811) - 0.5

    if biome == BIOME_OCEAN:
        # Realistic continental shelf -> sandy beach. Deep marine sand rises
        # gradually toward a beach crest about one tile above mean sea level;
        # there is no artificial rock wall between sea and land.
        y = 22.0 - 11.0 * _smoothstep(t) + broad * 0.55
    elif biome == BIOME_SWAMP:
        # V0.7.6.4 shoreline transition: begin just above mean sea level, then
        # descend gently into the mangrove wetland. This keeps a real sandy
        # beach crest between ocean and swamp so startup liquid cannot spread
        # across the whole coastal biome, while avoiding an abrupt cliff.
        y = 11.35 + 1.10 * _smoothstep(t) + broad * 0.42 + fine * 0.22
    elif biome == BIOME_LAKE:
        # Rock-lined freshwater basin; shallow margins, deep middle.
        bowl = math.sin(math.pi * t) ** 1.25
        y = 12.0 + 8.0 * bowl + broad * 0.5
    elif biome == BIOME_PLAINS:
        # Low-relief grassland with gentle drainage away from the lake.
        y = 12.0 + broad * 1.2 + fine * 0.7
    elif biome == BIOME_VILLAGE:
        # A deliberately low-relief buildable enclave.  Gentle variation keeps
        # the road organic while avoiding house-sized cliffs.
        y = 11.6 + broad * 0.45 + fine * 0.22
    elif biome == BIOME_RAINFOREST:
        # Humid lowland/upland forest, slightly higher approaching mountain.
        y = 11.5 - 1.0 * _smoothstep(t) + broad * 1.0 + fine * 0.6
    elif biome == BIOME_SNOW_MOUNTAIN:
        # Broad ridge: forested lower slopes, snow/rock summit above treeline.
        ridge = math.sin(math.pi * t) ** 0.72
        y = 10.0 - 7.0 * ridge + broad * 0.8 + fine * 0.5
    elif biome == BIOME_DESERT:
        # Descending leeward basin with low dunes.
        dune = (_value_noise_1d(seed, x, 22.0, 831) - 0.5) * 1.6
        y = 11.0 + 3.0 * _smoothstep(t) + dune
    else:
        y = 12.0 + broad

    # FIX73 reserves six additional rows above the ordinary surface for real
    # traversable sky islands.  Shifting the complete profile (rather than only
    # sea level) preserves every beach/lake seam and mountain slope.
    y += float(int(WORLD_GEN_SURFACE_ROW) - 12)
    return max(2.0, min(float(height) - 4.0, float(y)))


def _surface_material(biome, x=None, spans=None):
    if biome == BIOME_OCEAN:
        return "sea_sand"
    if biome == BIOME_LAKE:
        # Freshwater basin floor is soil.  Existing lake columns are initialized
        # saturated on load, so the lake ponds above the soil instead of being
        # held by an artificial stone bathtub.
        return "dirt"
    if biome == BIOME_SWAMP:
        # A short sandy back-beach grades into mud/mangrove soil.
        if x is not None and spans is not None and _zone_t(x, spans, biome) < 0.065:
            return "sea_sand"
        return "swamp_soil"
    if biome == BIOME_PLAINS:
        return "grass_dirt"
    if biome == BIOME_VILLAGE:
        return "village_path"
    if biome == BIOME_RAINFOREST:
        return "jungle_soil"
    if biome == BIOME_SNOW_MOUNTAIN:
        return "snow_dirt"
    if biome == BIOME_DESERT:
        return "sand"
    return "dirt"


def _subsurface_material(seed, biome, depth, x, y, height):
    # Ocean seabed: several porous marine-sand layers over deeper geology.
    # Runtime initializes the submerged sea-sand aquifer saturated, so it can
    # be porous without continuously draining the ocean.
    if biome == BIOME_OCEAN:
        if depth <= 5:
            return "sea_sand"
        return "limestone" if _hash01(seed, x, y, 1502) > 0.60 else "stone"

    if biome == BIOME_LAKE:
        if depth <= 5:
            return "dirt"
        if depth <= 8:
            return "limestone"

    if biome == BIOME_SWAMP:
        if depth <= 2:
            return "mud"
        if depth <= 5:
            return "dirt"

    if biome == BIOME_PLAINS:
        if depth <= 4:
            return "dirt"

    if biome == BIOME_VILLAGE:
        if depth <= 3:
            return "dirt"
        if depth <= 6:
            return "limestone"

    if biome == BIOME_RAINFOREST:
        if depth <= 2:
            return "jungle_soil"
        if depth <= 5:
            return "dirt"

    if biome == BIOME_SNOW_MOUNTAIN:
        if depth <= 2:
            return "stone"
        if depth <= 7:
            return "stone"

    if biome == BIOME_DESERT:
        if depth <= 4:
            return "sand"
        if depth <= 7:
            return "limestone"

    # Thin underground: keep geology readable without expensive caves.
    geological = _value_noise_2d(seed, x, y, 18.0, salt=201)
    if geological > 0.83:
        material = "marble"
    elif geological < 0.18:
        material = "limestone"
    else:
        material = "stone"

    depth_ratio = float(y) / max(1.0, float(height - 1))
    ore = _value_noise_2d(seed, x, y, 4.0, salt=301)
    if depth_ratio > 0.78 and ore > 0.88:
        material = "gold_ore"
    elif depth_ratio > 0.62 and ore > 0.83:
        material = "iron_ore"
    elif ore > 0.78:
        material = "copper_ore"
    return material


def _ocean_water_surface_y(height):
    # FIX27: underground depth no longer changes sea level.  The old formula
    # scaled sea level with total map height, so expanding 32 -> 64 rows would
    # move the ocean from row 12 to row 24.  Surface ecology now has its own
    # stable vertical datum while all extra rows belong to the underground.
    return max(3, min(int(height) - 4, int(WORLD_GEN_SURFACE_ROW)))


def _lake_water_surface_y(height):
    return max(3, min(int(height) - 4, int(WORLD_GEN_LAKE_SURFACE_ROW)))


def _fill_water_body(doc, seed, x, biome, top, height, spans):
    if biome == BIOME_OCEAN:
        surface_y = _ocean_water_surface_y(height)
        # V0.7.6.3: author a hydrostatically settled column from frame zero.
        # The old map wrote ~0.96 into *every* underwater cell. Gravity then
        # packed those tiny voids downward after startup, so the initially high
        # sea visibly washed farther onto the beach before dropping.  A liquid
        # column at rest is full below and has one common fractional surface.
        if int(top) > surface_y:
            doc.set("water", x, surface_y, 0.64)
            for wy in range(surface_y + 1, int(top)):
                doc.set("water", x, wy, 1.0)
        return

    if biome == BIOME_SWAMP:
        # V0.7.6.4 keeps a dry back-beach immediately landward of the ocean.
        # Previously random swamp pools could spawn in the first coastal cells
        # on frame zero and visually read as seawater spilling past the beach.
        # The mangrove/tidal pools begin only after the beach transition.
        if _zone_t(x, spans, BIOME_SWAMP) < 0.16:
            return
        if top > 1 and _hash01(seed, x, salt=480) > 0.36:
            doc.set("water", x, top - 1, 0.42 + 0.36 * _hash01(seed, x, salt=481))
            doc.set("hazard", x, top - 1, "swamp")
        return

    if biome == BIOME_LAKE:
        surface_y = _lake_water_surface_y(height)
        # Keep the lake hydrostatically settled for the same reason as ocean:
        # one fractional free surface, full cells beneath it.
        if int(top) > surface_y:
            doc.set("water", x, surface_y, 0.66)
            for wy in range(surface_y + 1, int(top)):
                doc.set("water", x, wy, 1.0)
        return


def _decorate_surface(doc, seed, x, top, biome, spans, height):
    r = _hash01(seed, x, salt=401)

    if biome == BIOME_OCEAN:
        # Open ocean and sandy beach stay free of terrestrial vegetation.
        if r < 0.035 and int(top) > _ocean_water_surface_y(height):
            doc.set("decoration", x, top, ("stone", 1))
        return

    if biome == BIOME_SWAMP:
        t = _zone_t(x, spans, biome)
        # Back-beach transition first, then mangrove/reed wetland. This keeps
        # the shoreline visually sandy before dense wetland roots begin.
        if t < 0.065:
            if r > 0.72:
                doc.set("vegetation", x, top, ("reed", 1))
            return
        if r > 0.57:
            doc.set("vegetation", x, top, ("jungle_tree", 2 if r < 0.84 else 3))
        elif r > 0.22:
            doc.set("vegetation", x, top, ("reed", 2 if r < 0.52 else 3))
        return

    if biome == BIOME_LAKE:
        # Explicitly no grass/tree/fern on the lake bed. This also prevents
        # terrestrial vegetation from appearing under open water.
        if r < 0.07:
            doc.set("decoration", x, top, ("stone", 1))
        return

    if biome == BIOME_PLAINS:
        t = _zone_t(x, spans, biome)
        # Wetter riparian margin near lake: reeds/grass first; tree groves are
        # set farther inland instead of uniformly scattered.
        if t < 0.10 and r > 0.50:
            doc.set("vegetation", x, top, ("reed", 1 if r < 0.75 else 2))
        elif r > 0.83:
            doc.set("vegetation", x, top, ("tree", 2 if r < 0.95 else 3))
        elif r > 0.30:
            doc.set("vegetation", x, top, ("grass", 1 if r < 0.66 else 2))
        return

    if biome == BIOME_VILLAGE:
        start, end = _span_map(spans)[BIOME_VILLAGE]
        local = int(x) - int(start)
        if local % 13 == 4:
            doc.set("decoration", x, top, ("house", 2))
        elif local % 13 in (1, 7):
            doc.set("decoration", x, top, ("lamp", 1))
        elif local % 13 in (0, 8):
            doc.set("decoration", x, top, ("fence", 1))
        return

    if biome == BIOME_RAINFOREST:
        # FIX68 tall rainforest trees are assembled later from real structural
        # terrain segments. Keep this pass for the understory only, otherwise
        # the old 73px decorative tree would overlap the climbable megatree.
        if r > 0.18:
            doc.set("vegetation", x, top, ("fern", 2 if r < 0.38 else 3))
        return

    if biome == BIOME_SNOW_MOUNTAIN:
        # Simple elevation treeline. Peak (small row index) is snow/rock only;
        # conifers dominate lower subalpine slopes.
        if int(top) <= 5:
            if r < 0.18:
                doc.set("decoration", x, top, ("stone", 2))
            return
        if int(top) <= 7:
            if r > 0.78:
                doc.set("vegetation", x, top, ("pine", 1))
            elif r < 0.14:
                doc.set("decoration", x, top, ("stone", 1))
            return
        forest = 0.70 * r + 0.30 * _value_noise_1d(seed, x, 18.0, 905)
        if forest > 0.36:
            doc.set("vegetation", x, top, ("pine", 3 if forest > 0.76 else 2))
        elif r < 0.10:
            doc.set("decoration", x, top, ("stone", 1))
        return

    if biome == BIOME_DESERT:
        # Sparse desert scrub/cacti; mostly exposed sand and rock.
        if r > 0.82:
            doc.set("vegetation", x, top, ("cactus", 2 if r < 0.95 else 3))
        elif r < 0.13:
            doc.set("decoration", x, top, ("stone", 1 if r > 0.045 else 2))
        return


def _smooth_surface(surface, biomes):
    """Keep one continuous terrain profile across biome boundaries.

    V0.7.6.3 only smoothed points whose three samples belonged to the same
    biome. That preserved formula discontinuities exactly at ocean/swamp and
    other joins. V0.7.6.4 blends a short window around every boundary, then
    limits neighboring columns to less than one tile of vertical change. The
    LOW/MID/HIGH thirds retain visible slope detail without large stair steps.
    """
    surface = [float(v) for v in surface]
    for _ in range(3):
        out = list(surface)
        for x in range(1, len(surface) - 1):
            if biomes[x - 1] == biomes[x] == biomes[x + 1]:
                out[x] = surface[x - 1] * 0.20 + surface[x] * 0.60 + surface[x + 1] * 0.20
        surface = out

    # Explicitly bridge each biome seam over a broad enough distance that the
    # transition reads as terrain, not a boundary wall.
    boundaries = [x for x in range(1, len(surface)) if biomes[x] != biomes[x - 1]]
    radius = 12
    for b in boundaries:
        left = max(0, b - radius)
        right = min(len(surface) - 1, b + radius)
        if right <= left:
            continue
        y0 = float(surface[left])
        y1 = float(surface[right])
        span = float(right - left)
        for x in range(left, right + 1):
            u = (x - left) / span
            surface[x] = y0 + (y1 - y0) * _smoothstep(u)

    # With thirds available, ~0.72 tile is enough to preserve mountain slopes
    # but prevents the beach/biome joins from producing one-tile cliffs.
    max_step = 0.72
    for _ in range(4):
        for x in range(1, len(surface)):
            delta = surface[x] - surface[x - 1]
            if delta > max_step:
                surface[x] = surface[x - 1] + max_step
            elif delta < -max_step:
                surface[x] = surface[x - 1] - max_step
        for x in range(len(surface) - 2, -1, -1):
            delta = surface[x] - surface[x + 1]
            if delta > max_step:
                surface[x] = surface[x + 1] + max_step
            elif delta < -max_step:
                surface[x] = surface[x + 1] - max_step
    return surface


def _rainforest_surface_top_ratio(doc, tx, ty):
    mask = int(doc.get("ground_layer", int(tx), int(ty), SOIL_LAYER_FULL) or SOIL_LAYER_FULL)
    if mask == SOIL_LAYER_LOW:
        return 2.0 / 3.0
    if mask == (SOIL_LAYER_LOW | SOIL_LAYER_MID):
        return 1.0 / 3.0
    return 0.0


def _build_rainforest_giant_trees(doc, seed, surface, spans):
    """Assemble tall, climbable trees from bounded 16px tile segments.

    Collision is carried by root/trunk/branch terrain. Vines and canopy remain
    passable. ``segments`` records a support parent for deterministic pruning
    when an axe or fire removes a lower segment. Generation deliberately leaves
    at least eight ground columns between roots and stays below a sparse mobile
    budget instead of filling the whole rainforest with physics objects.
    """
    span_map = _span_map(spans)
    start, end = span_map.get(BIOME_RAINFOREST, (0, 0))
    start = max(5, int(start))
    end = min(int(doc.width) - 5, int(end))
    natural_surface = []
    for tx in range(start, end):
        ty = max(2, min(int(doc.height) - 4, int(math.floor(float(surface[tx])))))
        natural_surface.append([
            int(tx), int(ty), round(_rainforest_surface_top_ratio(doc, tx, ty), 6)
        ])

    segments = {}
    trees = []

    def add_segment(tree_id, tx, ty, role, parent, wood_yield=0, leaf_yield=0):
        tx, ty = int(tx), int(ty)
        key = (tx, ty)
        if not doc.in_bounds(tx, ty) or key in segments:
            return None
        # Structural trees only occupy authored air. This prevents a canopy
        # from replacing terrain when a seed produces a steep local slope.
        if doc.get("terrain", tx, ty, None) is not None:
            return None
        tile_name = {
            "root": "rainforest_root",
            "trunk": "rainforest_trunk",
            "branch": "rainforest_branch",
            "vine": "rainforest_vine",
            "canopy": "rainforest_canopy",
        }[str(role)]
        doc.set("terrain", tx, ty, tile_name)
        parent_row = None if parent is None else [int(parent[0]), int(parent[1])]
        segments[key] = [
            tx, ty, str(role), str(tree_id), parent_row,
            max(0, int(wood_yield)), max(0, int(leaf_yield)),
        ]
        return key

    candidate = start + 7 + int(_hash01(seed, start, salt=6801) * 3.0)
    index = 0
    previous_root = None
    while candidate < end - 7 and index < 10:
        tx = int(candidate)
        surface_y = max(2, min(int(doc.height) - 4, int(math.floor(float(surface[tx])))))
        left_y = int(math.floor(float(surface[max(start, tx - 1)])))
        right_y = int(math.floor(float(surface[min(end - 1, tx + 1)])))

        # Skip only unusually steep local columns. Normal LOW/MID/HIGH slopes
        # remain valid and the next deterministic spacing still leaves a path.
        if max(abs(surface_y - left_y), abs(surface_y - right_y)) <= 1:
            root_y = surface_y - 1
            requested_height = 7 + int(_hash01(seed, tx, salt=6802) * 3.0)
            height = min(requested_height, max(5, root_y - 3))
            if root_y - height + 1 >= 2:
                tree_id = "rainforest_giant_%02d" % (index + 1)
                root = add_segment(tree_id, tx, root_y, "root", None, wood_yield=3)
                if root is not None:
                    # Clear only the immediate undergrowth around the base.
                    # Neighboring ferns beyond this two-cell footprint remain.
                    for gx in range(tx - 1, tx + 2):
                        doc.erase_layer("vegetation", gx, surface_y)

                    trunk_keys = {root_y: root}
                    parent = root
                    top_y = root_y
                    for ty in range(root_y - 1, root_y - height, -1):
                        key = add_segment(
                            tree_id, tx, ty, "trunk", parent, wood_yield=2
                        )
                        if key is None:
                            break
                        trunk_keys[ty] = key
                        parent = key
                        top_y = ty

                    vine_side = -1 if _hash01(seed, tx, salt=6803) < 0.5 else 1
                    vine_x = tx + vine_side
                    vine_parent = root
                    for ty in range(root_y, top_y - 1, -1):
                        key = add_segment(
                            tree_id, vine_x, ty, "vine", vine_parent,
                            wood_yield=0, leaf_yield=0,
                        )
                        if key is not None:
                            vine_parent = key

                    branch_rows = sorted(set((
                        max(top_y + 1, root_y - max(3, height // 2)),
                        min(root_y - 2, top_y + 1),
                    )))
                    branch_keys = {}
                    branch_half = 3 + int(_hash01(seed, tx, salt=6804) > 0.55)
                    for by in branch_rows:
                        trunk_parent = trunk_keys.get(by, parent)
                        for direction in (-1, 1):
                            chain_parent = trunk_parent
                            for distance in range(1, branch_half + 1):
                                bx = tx + direction * distance
                                # The climb vine occupies one adjacent cell;
                                # leave it open while preserving graph support
                                # directly from the trunk.
                                if bx == vine_x:
                                    continue
                                key = add_segment(
                                    tree_id, bx, by, "branch", chain_parent,
                                    wood_yield=1,
                                )
                                if key is not None:
                                    chain_parent = key
                                    branch_keys[(direction, by)] = key

                    # Passable leaf mosaic. It is wide enough to read as a
                    # canopy but shares the same fixed tile cache as terrain.
                    canopy_top = max(0, top_y - 2)
                    for cy in range(canopy_top, min(root_y - 1, top_y + 2) + 1):
                        dy = cy - (top_y - 1)
                        half = 4 if abs(dy) <= 1 else 3
                        for dx in range(-half, half + 1):
                            if abs(dx) == half and _hash01(seed, tx + dx, cy, 6805) < 0.28:
                                continue
                            if dx < 0:
                                support = branch_keys.get((-1, branch_rows[0]), parent)
                            elif dx > 0:
                                support = branch_keys.get((1, branch_rows[0]), parent)
                            else:
                                support = parent
                            add_segment(
                                tree_id, tx + dx, cy, "canopy", support,
                                wood_yield=0, leaf_yield=1,
                            )

                    tree_cells = [row for row in segments.values() if row[3] == tree_id]
                    xs = [row[0] for row in tree_cells]
                    ys = [row[1] for row in tree_cells]
                    trees.append({
                        "id": tree_id,
                        "root": [int(tx), int(root_y)],
                        "surface_row": int(surface_y),
                        "height_tiles": int(root_y - top_y + 1),
                        "branch_rows": [int(v) for v in branch_rows],
                        "climb_column": int(vine_x),
                        "climb_modes": ["wall", "vine"],
                        "bounds": [min(xs), min(ys), max(xs), max(ys)],
                        "segment_count": len(tree_cells),
                    })
                    previous_root = tx
                    index += 1

        spacing = 10 + int(_hash01(seed, tx, salt=6806) * 4.0)
        # Eight clear ground columns is the hard path budget; actual generated
        # gaps are normally 9-12 because each root occupies one column.
        if previous_root is not None:
            spacing = max(9, spacing)
        candidate += spacing

    ordered_segments = [segments[key] for key in sorted(segments)]
    root_columns = sorted(int(row["root"][0]) for row in trees)
    ground_gaps = [b - a - 1 for a, b in zip(root_columns, root_columns[1:])]
    return {
        "format": "pyto_rpg_rainforest_tree_system",
        "version": 1,
        "tile_asset_prefix": "tile.rainforest_",
        "render_source": "terrain_tile_cache",
        "tree_count": len(trees),
        "segment_count": len(ordered_segments),
        "max_live_segments": 640,
        "minimum_clear_ground_columns": min(ground_gaps) if ground_gaps else max(0, end - start - 1),
        "minimum_required_clear_ground_columns": 8,
        "one_way_platform_role": "branch",
        "ladder_role": "vine",
        "wall_climb_role": "trunk",
        "drop_batch_limit": 2,
        "natural_surface": natural_surface,
        "trees": trees,
        "segments": ordered_segments,
    }


def _build_sky_islands(doc, seed, surface, spans):
    """Author a small, irregular archipelago in the reserved upper sky.

    Islands are ordinary solid terrain, so standing, mining and grappling use
    the same collision/material rules as the ground.  Metadata records safe
    top surfaces for habitat-aware creature placement without scanning the
    first solid row (which would otherwise confuse an island with the ground).
    """
    span_map = _span_map(spans)
    profiles = (
        ("mist_islet", BIOME_SWAMP, 0.72, "swamp_soil", "stone"),
        ("lake_bloom_islet", BIOME_LAKE, 0.58, "grass_dirt", "limestone"),
        ("wind_meadow_islet", BIOME_PLAINS, 0.67, "grass_dirt", "stone"),
        ("aurora_islet", BIOME_SNOW_MOUNTAIN, 0.08, "snow_dirt", "stone"),
        ("sun_dune_islet", BIOME_DESERT, 0.24, "sand", "igneous_rock"),
    )
    islands = []
    occupied = set()
    for index, (island_id, source_biome, ratio, top_material, core_material) in enumerate(profiles):
        start, end = span_map.get(source_biome, (8, doc.width - 8))
        center = max(start + 8, min(end - 9, int(start + (end - start) * ratio)))
        half = 7 + int(_hash01(seed, center, index, 7301) * 3.0)
        x0 = max(4, center - half)
        x1 = min(doc.width - 5, center + half)
        ground_clearance = min(
            int(math.floor(float(surface[x]))) for x in range(x0, x1 + 1)
        )
        base_floor = max(4, min(int(WORLD_GEN_SURFACE_ROW) - 7, ground_clearance - 7))
        top_rows = {}
        island_cells = []
        for x in range(x0, x1 + 1):
            dx = abs(x - center)
            edge = float(dx) / float(max(1, half))
            top = base_floor + (1 if edge > 0.72 else 0)
            # A tapered, noisy underside avoids a rectangular floating slab.
            thickness = max(1, int(round((1.0 - edge) * 4.2)))
            if _hash01(seed, x, index, 7302) > 0.64:
                thickness += 1
            bottom = min(ground_clearance - 4, top + thickness)
            if bottom < top:
                continue
            for air_y in range(max(0, top - 4), top):
                _carve_air(doc, x, air_y)
            _set_solid(doc, x, top, top_material)
            island_cells.append([int(x), int(top), str(top_material)])
            for y in range(top + 1, bottom + 1):
                material = core_material
                if y == top + 1 and top_material in ("grass_dirt", "swamp_soil", "sand"):
                    material = "dirt" if top_material != "sand" else "sand"
                _set_solid(doc, x, y, material)
                island_cells.append([int(x), int(y), str(material)])
            top_rows[x] = top
            occupied.add((x, top))

        if not top_rows:
            continue
        safe_columns = [
            x for x in range(x0 + 2, x1 - 1)
            if x in top_rows and all((x + offset) in top_rows for offset in (-1, 0, 1))
        ]
        if not safe_columns:
            safe_columns = [center]
        spawn_columns = []
        for fraction in (0.25, 0.50, 0.75):
            pos = min(len(safe_columns) - 1, int(round((len(safe_columns) - 1) * fraction)))
            tx = int(safe_columns[pos])
            row = [tx, int(top_rows[tx])]
            if row not in spawn_columns:
                spawn_columns.append(row)
        for tx, floor in spawn_columns:
            if top_material in ("grass_dirt", "swamp_soil") and _hash01(seed, tx, floor, 7304) > 0.44:
                doc.set("vegetation", tx, floor, ("grass", 1))
            elif top_material == "snow_dirt" and _hash01(seed, tx, floor, 7305) > 0.70:
                doc.set("decoration", tx, floor, ("stone", 1))
        ys = [row[1] for row in island_cells]
        islands.append({
            "id": str(island_id),
            "habitat": "sky_island",
            "source_biome": str(source_biome),
            "bounds": [int(x0), min(ys), int(x1), max(ys)],
            "spawn_columns": spawn_columns,
            "top_material": str(top_material),
            "core_material": str(core_material),
            "terrain_cells": len(island_cells),
            "solid_grappleable": True,
        })
    return {
        "format": "pyto_rpg_sky_islands",
        "version": 1,
        "habitat": "sky_island",
        "region_count": len(islands),
        "solid_grappleable": True,
        "regions": islands,
    }


def _surface_layer_mask(surface_value):
    """Quantize a continuous surface into bottom-up thirds of one tile.

    FULL => top at 0/3, LOW|MID => top at 1/3, LOW => top at 2/3.
    """
    base = math.floor(float(surface_value))
    frac = max(0.0, min(0.999999, float(surface_value) - float(base)))
    if frac < 1.0 / 6.0:
        return SOIL_LAYER_FULL
    if frac < 0.5:
        return SOIL_LAYER_LOW | SOIL_LAYER_MID
    return SOIL_LAYER_LOW


def _underground_waterway_row(height):
    # One-tile open channel with a solid floor beneath it.
    return max(6, int(height) - 4)


# ============================================================================
# FIX27 underground world
# ============================================================================

def _clear_cell_overlays(doc, x, y, keep_decoration=False):
    """Clear non-terrain layers from a cell being excavated."""
    for layer in ("ground_layer", "water", "lava", "honey", "fire", "vegetation", "hazard"):
        doc.erase_layer(layer, x, y)
    if not keep_decoration:
        doc.erase_layer("decoration", x, y)


def _carve_air(doc, x, y):
    if not doc.in_bounds(x, y):
        return
    doc.erase_layer("terrain", x, y)
    _clear_cell_overlays(doc, x, y)


def _set_solid(doc, x, y, material="stone"):
    if not doc.in_bounds(x, y):
        return
    doc.set("terrain", x, y, str(material))
    _clear_cell_overlays(doc, x, y)


def _surface_row_for_column(doc, x, search_limit=None):
    # Re-running the underground upgrade must not mistake an existing entrance
    # ladder for a very deep surface. Generated entrance metadata remembers the
    # original surface row at that exact X.
    meta=getattr(doc,"metadata",{}) or {}
    for info in meta.get("underground_entrances",()) or ():
        try:
            if int(info.get("x",-9999)) == int(x) and "surface_row" in info:
                return int(info["surface_row"])
        except Exception:
            pass
    limit = int(search_limit if search_limit is not None else min(doc.height, 30))
    limit = max(1, min(doc.height, limit))
    for y in range(limit):
        value = doc.get("terrain", x, y, None)
        if value is not None and str(value) != "ladder":
            return int(y)
    return min(max(2, int(WORLD_GEN_SURFACE_ROW)), doc.height - 2)


def _deep_geology_material(seed, x, y, height):
    """Geology used only by rows added beneath an existing authored map."""
    geological = _value_noise_2d(seed, x, y, 20.0, salt=2701)
    if geological > 0.86:
        material = "marble"
    elif geological < 0.15:
        material = "limestone"
    else:
        material = "stone"
    depth_ratio = float(y) / max(1.0, float(height - 1))
    ore = _value_noise_2d(seed, x, y, 4.5, salt=2711)
    if depth_ratio > 0.80 and ore > 0.90:
        return "gold_ore"
    if depth_ratio > 0.66 and ore > 0.86:
        return "iron_ore"
    if ore > 0.82:
        return "copper_ore"
    return material


def _carve_tunnel_band(doc, seed, base_row, salt, x_start=4, x_end=None, half_height=1):
    """Carve one walkable meandering tunnel with >=3 cells vertical clearance."""
    if x_end is None:
        x_end = doc.width - 5
    points=[]
    for x in range(max(2, int(x_start)), min(doc.width - 2, int(x_end)) + 1):
        surface = _surface_row_for_column(doc, x)
        wave = (_value_noise_1d(seed, x, 24.0, salt) - 0.5) * 4.0
        fine = (_value_noise_1d(seed, x, 8.0, salt + 1) - 0.5) * 1.6
        center = int(round(float(base_row) + wave + fine))
        center = max(surface + int(UNDERGROUND_START_DEPTH_TILES), center)
        center = max(3, min(doc.height - 5, center))
        for y in range(center - int(half_height), center + int(half_height) + 1):
            _carve_air(doc, x, y)
        # Keep a dependable floor beneath the corridor. Natural stone stays
        # climbable, which lets vertical side shafts work with wall climbing.
        floor_y = center + int(half_height) + 1
        if floor_y < doc.height - 1 and doc.get("terrain", x, floor_y, None) is None:
            _set_solid(doc, x, floor_y, "stone")
        points.append((x, center))
    return points


def _path_y_at(path, x, fallback):
    if not path:
        return int(fallback)
    x=int(x)
    best=min(path,key=lambda item: abs(int(item[0])-x))
    return int(best[1])


def _carve_cavern(doc, cx, cy, rx, ry):
    cx=int(cx); cy=int(cy); rx=max(2,int(rx)); ry=max(2,int(ry))
    for y in range(max(2,cy-ry), min(doc.height-2,cy+ry+1)):
        for x in range(max(1,cx-rx), min(doc.width-1,cx+rx+1)):
            nx=(x-cx)/float(rx)
            ny=(y-cy)/float(ry)
            if nx*nx + ny*ny <= 1.0:
                _carve_air(doc,x,y)


def _carve_corridor(doc, x0, y0, x1, y1, height=3):
    """L-shaped corridor between two underground points."""
    x0=int(x0); y0=int(y0); x1=int(x1); y1=int(y1)
    step=1 if x1>=x0 else -1
    for x in range(x0,x1+step,step):
        for y in range(y0-int(height)+1, y0+1):
            _carve_air(doc,x,y)
    vstep=1 if y1>=y0 else -1
    for y in range(y0,y1+vstep,vstep):
        for yy in range(y-int(height)+1,y+1):
            _carve_air(doc,x1,yy)


def _build_room(doc, x0, y0, w, h, material="limestone"):
    """Build a rectangular dungeon room. y1 is the solid floor row."""
    x0=max(1,int(x0)); y0=max(2,int(y0)); w=max(7,int(w)); h=max(6,int(h))
    x1=min(doc.width-2,x0+w-1); y1=min(doc.height-2,y0+h-1)
    # Boundary shell.
    for x in range(x0,x1+1):
        _set_solid(doc,x,y0,material)
        _set_solid(doc,x,y1,material)
    for y in range(y0,y1+1):
        _set_solid(doc,x0,y,material)
        _set_solid(doc,x1,y,material)
    # Interior air.
    for y in range(y0+1,y1):
        for x in range(x0+1,x1):
            _carve_air(doc,x,y)
    return (x0,y0,x1,y1)


def _open_room_door(doc, room, side="left"):
    x0,y0,x1,y1=room
    x=x0 if side=="left" else x1
    for y in (y1-1,y1-2):
        _carve_air(doc,x,y)
    return (x,y1-1)


def _place_ladder_column(doc, x, top_y, bottom_y):
    """Excavate a narrow shaft and fill it with non-solid ladder tiles."""
    x=int(x); top_y=int(top_y); bottom_y=int(bottom_y)
    if bottom_y < top_y:
        top_y,bottom_y=bottom_y,top_y
    top_y=max(1,top_y); bottom_y=min(doc.height-2,bottom_y)
    for y in range(top_y,bottom_y+1):
        _carve_air(doc,x,y)
        doc.set("terrain",x,y,"ladder")
        # Keep the ladder shaft one cell wide. The player occupies the ladder
        # cell itself while climbing, so an adjacent free-fall column is not
        # needed and would bypass the intended vertical traversal mechanic.
    return {"type":"ladder","x":x,"top":top_y,"bottom":bottom_y}


def _place_rock_climb_shaft(doc, x, top_y, bottom_y, wall_side="right"):
    """Open shaft beside a continuous climbable stone face."""
    x=int(x); top_y=int(top_y); bottom_y=int(bottom_y)
    if bottom_y < top_y:
        top_y,bottom_y=bottom_y,top_y
    shaft_x=x
    wall_x=x+1 if wall_side=="right" else x-1
    if not (1 <= shaft_x < doc.width-1 and 0 <= wall_x < doc.width):
        return None
    for y in range(max(1,top_y), min(doc.height-2,bottom_y)+1):
        _carve_air(doc,shaft_x,y)
        _set_solid(doc,wall_x,y,"stone")
    return {"type":"rock_climb","x":shaft_x,"wall_x":wall_x,"top":top_y,"bottom":bottom_y}


def _build_dungeon_cluster(doc, seed, center_x, top_y, style="limestone", index=0):
    """Two-level dungeon with rooms, a jail and an internal ladder."""
    center_x=int(center_x); top_y=int(top_y)
    width=30 + (index % 2) * 4
    x0=max(3,min(doc.width-width-4,center_x-width//2))
    # Upper hall and two lower rooms.
    hall=_build_room(doc,x0,top_y,width,8,style)
    lower_y=min(doc.height-12,top_y+11)
    left=_build_room(doc,x0,lower_y,width//2+2,8,"stone" if style=="marble" else style)
    right=_build_room(doc,x0+width//2-1,lower_y,width//2+1,8,style)

    # Doors toward surrounding tunnels and between lower cells.
    left_door=_open_room_door(doc,hall,"left")
    right_door=_open_room_door(doc,hall,"right")
    _open_room_door(doc,left,"right")
    _open_room_door(doc,right,"left")

    ladder_x=center_x
    _place_ladder_column(doc,ladder_x,hall[3]-3,right[1]+3)
    # Open ladder access through both room floors/ceilings.
    for y in range(hall[3]-2,right[1]+4):
        if doc.in_bounds(ladder_x,y):
            doc.set("terrain",ladder_x,y,"ladder")

    # Visual dungeon identity: lamps in hall, fences in lower jail cells.
    for lx in (x0+4,x0+width-5):
        if doc.in_bounds(lx,hall[3]-2):
            doc.set("decoration",lx,hall[3]-2,("lamp",1))
    jail_y=left[3]-1
    for fx in range(left[0]+3,left[2]-1,3):
        doc.set("decoration",fx,jail_y,("fence",2))
    doc.set("decoration",right[0]+4,right[3]-1,("stone",2))

    return {
        "type":"dungeon",
        "index":int(index),
        "style":str(style),
        "bounds":[int(x0),int(top_y),int(x0+width-1),int(lower_y+7)],
        "upper_left_door":[int(left_door[0]),int(left_door[1])],
        "upper_right_door":[int(right_door[0]),int(right_door[1])],
        "ladder_x":int(ladder_x),
    }


def _remove_old_generated_waterway(doc):
    meta=getattr(doc,"metadata",{}) or {}
    old_row=int(meta.get("underground_waterway_row",-1) or -1)
    if old_row < 0 or old_row >= doc.height:
        return
    # Only repair a row that looks like the generator's old continuous channel.
    sample=0; airish=0
    step=max(1,doc.width//32)
    for x in range(1,doc.width-1,step):
        sample+=1
        if doc.get("terrain",x,old_row,None) is None:
            airish+=1
    if sample and airish >= int(sample*0.70):
        seed=int(meta.get("seed",0) or 0)
        for x in range(1,doc.width-1):
            doc.erase_layer("water",x,old_row)
            if doc.get("terrain",x,old_row,None) is None:
                _set_solid(doc,x,old_row,_deep_geology_material(seed,x,old_row,max(doc.height,64)))



def _apply_underground_terracing(doc, seed, start_row):
    """Author 1/3, 2/3 and full geology on exposed underground floors.

    The pattern intentionally mixes long one-layer ramps with occasional
    two-layer scarps. FIX32 physics can auto-step one layer, while 2/3 and full
    cliffs still form meaningful route boundaries.
    """
    geologic={"stone","copper_ore","iron_ore","gold_ore","marble","limestone"}
    authored=0
    for x in range(2,doc.width-2):
        for y in range(max(2,int(start_row)),doc.height-2):
            tile=str(doc.get("terrain",x,y,None) or "")
            if tile not in geologic:
                continue
            if doc.get("terrain",x,y-1,None) is not None:
                continue
            # Smooth staircase: neighboring phases normally change by one
            # layer. Sparse scarps deliberately jump LOW<->FULL.
            phase=((x//4)+(y*3))%12
            seq=(7,7,7,3,3,3,1,1,1,3,3,3)
            mask=int(seq[phase])
            if _hash01(seed,x,y,3711) > 0.965:
                mask=1 if _hash01(seed,x,y,3712)<0.5 else 7
            doc.set("ground_layer",x,y,mask)
            authored+=1
    return authored


def _build_hive_ecosystem(doc, seed, span_map, level_rows):
    """Carve a hexagonal honeycomb chamber and author its colony."""
    start,end=span_map.get(BIOME_RAINFOREST,(int(doc.width*.55),int(doc.width*.70)))
    cx=max(start+25,min(end-25,int(start+(end-start)*0.22)))
    cy=int(level_rows[1])-1; rx=21; ry=9
    x0,x1=cx-rx,cx+rx; y0,y1=cy-ry,cy+ry
    for y in range(y0,y1+1):
        edge_ratio=abs(y-cy)/float(max(1,ry))
        half=max(8,int(round(rx*(1.0-edge_ratio*0.46))))
        left,right=cx-half,cx+half
        for x in range(left,right+1):
            boundary=(x-left<=1 or right-x<=1 or y-y0<=1 or y1-y<=1)
            if boundary:_set_solid(doc,x,y,"honeycomb")
            else:_carve_air(doc,x,y)
    # Open a dry entrance onto the middle tunnel.
    door_x=x0+2; floor=y1-1
    for yy in (floor-1,floor-2,floor-3):_carve_air(doc,door_x,yy)
    target_x=max(5,door_x-9)
    _carve_corridor(doc,door_x,floor-1,target_x,int(level_rows[1])+1,3)
    # Initial honey pockets demonstrate the liquid; every mined comb releases
    # more into the newly opened cell.
    honey_cells=[]
    for x in range(cx-10,cx+12,4):
        doc.set("honey",x,floor-1,0.42+0.10*int(_hash01(seed,x,floor,7410)>.5))
        honey_cells.append([int(x),int(floor-1)])
    zone={"id":"royal_hive","type":"hive_chamber","bounds":[x0,y0,x1,y1],
          "shape":"dense_hexagonal_honeycomb","honey_cells":honey_cells,
          "mining_releases_honey":True,"honey_phase_changes":False}
    spawns=[
        {"species":"queen_bee","x":cx,"floor":floor,"count":1,"patrol_tiles":7.0,"region_id":"royal_hive"},
        {"species":"worker_bee","x":x0+7,"floor":floor,"count":6,"spacing":5,"patrol_tiles":4.8,"region_id":"royal_hive"},
        {"species":"drone_bee","x":x0+10,"floor":floor,"count":3,"spacing":8,"patrol_tiles":5.2,"region_id":"royal_hive"},
    ]
    return zone,spawns


def _build_underground_lake_ecosystem(doc, seed, span_map, level_rows):
    """Carve a sealed deep lake with a dry access shelf above its waterline."""
    start,end=span_map.get(BIOME_LAKE,(int(doc.width*.22),int(doc.width*.36)))
    cx=max(start+30,min(end-30,int(start+(end-start)*0.58)))
    rx=29; cy=min(doc.height-16,int(level_rows[2])-2); ry=10
    x0,x1=cx-rx,cx+rx; y0,y1=cy-ry,cy+ry
    _carve_cavern(doc,cx,cy,rx,ry)
    water_top=cy-2; water_bottom=y1-1
    # Irregular stone/limestone basin with a broad water volume.
    for x in range(x0+2,x1-1):
        dx=abs(x-cx)/float(max(1,rx))
        floor=max(water_top+3,min(water_bottom,int(water_bottom-dx*3.0)))
        _set_solid(doc,x,floor,"limestone" if (x//3)%2 else "stone")
        for y in range(water_top,floor):
            _carve_air(doc,x,y)
            doc.set("water",x,y,1.0 if y>water_top else 0.82)
    # Dry gallery to the left, one row above the water surface.
    shelf_y=water_top
    for x in range(x0+1,x0+9):
        _set_solid(doc,x,shelf_y,"stone")
        for y in range(shelf_y-3,shelf_y):_carve_air(doc,x,y)
    target_x=max(5,x0-8)
    _carve_corridor(doc,x0+2,shelf_y-1,target_x,int(level_rows[1])+1,3)
    zone={"id":"ness_deep_lake","type":"underground_lake","bounds":[x0,y0,x1,y1],
          "water_bounds":[x0+9,water_top,x1-2,water_bottom],"permanent_lake":True,
          "resident_boss":"loch_ness_monster"}
    wb=list(zone["water_bounds"])
    spawns=[
        {"species":"loch_ness_monster","x":cx,"floor":water_bottom,"count":1,"water_bounds":wb,"patrol_tiles":10.0,"region_id":"ness_deep_lake"},
        {"species":"water_lizard","x":x0+12,"floor":water_bottom,"count":4,"spacing":10,"water_bounds":wb,"patrol_tiles":6.0,"region_id":"ness_deep_lake"},
        {"species":"water_snake","x":x0+16,"floor":water_bottom,"count":5,"spacing":8,"water_bounds":wb,"patrol_tiles":7.0,"region_id":"ness_deep_lake"},
    ]
    return zone,spawns


def _build_lost_world_ecosystem(doc, seed, span_map, level_rows):
    """Add a broad warm cavern so large prehistoric animals have real space."""
    start,end=span_map.get(BIOME_DESERT,(int(doc.width*.84),doc.width-4))
    cx=max(start+40,min(end-40,int(start+(end-start)*0.76)))
    cy=int(level_rows[1])+1; rx=37; ry=11
    _carve_cavern(doc,cx,cy,rx,ry)
    x0,x1=cx-rx,cx+rx; y0,y1=cy-ry,cy+ry; floor=y1
    for x in range(x0+2,x1-1):
        _set_solid(doc,x,floor,"jungle_soil")
        if (x-cx)%5==0:doc.set("vegetation",x,floor,("grass",1+(abs(x-cx)%3)))
    _carve_corridor(doc,x0+2,floor-1,max(5,x0-8),int(level_rows[1])+1,4)
    zone={"id":"lost_world_cavern","type":"lost_world","bounds":[x0,y0,x1,y1],
          "large_species_canvas":"64x64","climate":"warm_subterranean"}
    species=("tyrannosaurus","stegosaurus","brachiosaurus","spinosaurus",
             "triceratops","ankylosaurus","ornithomimus")
    positions=(x0+8,x0+18,x0+29,x0+40,x0+50,x0+61,x0+69)
    spawns=[]
    for name,x in zip(species,positions):
        spawns.append({"species":name,"x":min(x1-3,x),"floor":floor,"count":2 if name=="ornithomimus" else 1,
                       "spacing":4,"patrol_tiles":5.5,"region_id":"lost_world_cavern"})
    return zone,spawns


def _decorate_underground_ecology(doc, seed, spans, level_rows, dungeon_specs):
    """Create readable underground ecology districts inside the main world."""
    span_map=_span_map(spans)
    zones=[]
    ecosystem_spawns=[]

    # Bioluminescent moss belt below rainforest.
    rs,re=span_map.get(BIOME_RAINFOREST,(int(doc.width*.54),int(doc.width*.70)))
    moss=[max(4,rs+4),max(18,level_rows[0]-5),min(doc.width-5,re-3),min(doc.height-5,level_rows[1]+4)]
    zones.append({"id":"main_glow_moss","type":"glow_moss","bounds":moss})
    for x in range(moss[0],moss[2],4):
        for y in range(moss[1]+1,moss[3]):
            if doc.get("terrain",x,y,None) is not None and doc.get("terrain",x,y-1,None) is None:
                doc.set("decoration",x,y,("glow_moss",1+int(_hash01(seed,x,y,3801)*3)))
                break

    # Giant crystal mine under the snowy mountain belt.
    ss,se=span_map.get(BIOME_SNOW_MOUNTAIN,(int(doc.width*.70),int(doc.width*.85)))
    crystal=[max(4,ss+3),max(20,level_rows[1]-6),min(doc.width-5,se-3),min(doc.height-6,level_rows[2]+4)]
    zones.append({"id":"main_crystal_mine","type":"crystal_mine","bounds":crystal})
    for x in range(crystal[0],crystal[2],5):
        for y in range(crystal[1]+1,crystal[3]):
            if doc.get("terrain",x,y,None) is not None and doc.get("terrain",x,y-1,None) is None:
                if _hash01(seed,x,y,3810)>0.24:
                    doc.set("decoration",x,y,("crystal",2 if _hash01(seed,x,y,3811)<0.68 else 3))
                break

    # The deepest existing dungeon is tagged as the main-world boss gaol.
    if dungeon_specs:
        deepest=max(dungeon_specs,key=lambda d:int((d.get("bounds") or [0,0,0,0])[3]))
        zones.append({"id":"main_boss_gaol","type":"boss_dungeon","bounds":list(deepest.get("bounds",[]))})
        try:
            bx0,by0,bx1,by1=[int(v) for v in deepest.get("bounds",())]
            doc.set("decoration",(bx0+bx1)//2,by1-1,("boss_gate",3))
        except Exception:
            pass

    hive_zone,hive_spawns=_build_hive_ecosystem(doc,seed,span_map,level_rows)
    lake_zone,lake_spawns=_build_underground_lake_ecosystem(doc,seed,span_map,level_rows)
    lost_zone,lost_spawns=_build_lost_world_ecosystem(doc,seed,span_map,level_rows)
    zones.extend((hive_zone,lake_zone,lost_zone))
    ecosystem_spawns.extend(hive_spawns+lake_spawns+lost_spawns)

    # Bottom magma hell: several liquid-like lava pools connected upward to
    # the deepest traversal band.  Lava is a hazard overlay, not a solid tile.
    magma_y=max(level_rows[-1]+5,doc.height-8)
    magma_bounds=[3,magma_y-4,doc.width-4,doc.height-3]
    zones.append({"id":"main_magma_hell","type":"magma_hell","bounds":magma_bounds})
    pools=[]
    magma_ledges=[]
    for pi,frac in enumerate((0.20,0.48,0.76)):
        cx=int(doc.width*frac); half=8+int(_hash01(seed,pi,salt=3820)*7)
        floor=min(doc.height-3,magma_y+2)
        x0=max(3,cx-half);x1=min(doc.width-4,cx+half)
        for x in range(x0,x1+1):
            # Low chamber above magma.
            for y in range(max(level_rows[-1]+2,floor-4),floor):
                _carve_air(doc,x,y)
                doc.erase_layer("water",x,y)
            _set_solid(doc,x,floor,"stone")
            doc.set("lava",x,floor-1,1.0)
        # Two small basalt shelves create intentional, dry spawn/perch points
        # inside the magma habitat. They remain surrounded by visible lava but
        # never initialize a terrestrial creature inside a hazardous cell.
        for ledge_x in (min(x1-3,x0+3), max(x0+3,x1-3)):
            for x in range(max(x0,ledge_x-1),min(x1,ledge_x+1)+1):
                _set_solid(doc,x,floor-1,"igneous_rock")
            magma_ledges.append({"x":int(ledge_x),"floor":int(floor-1),"pool":int(pi)})
        # connect chamber to deep tunnel by a narrow vertical/diagonal route
        target_y=level_rows[-1]+1
        _carve_corridor(doc,cx,target_y,cx,floor-3,3)
        pools.append([x0,floor-1,x1,floor-1])
    zones[-1]["safe_ledges"]=magma_ledges

    # FIX35: the last row is a bottomless magma reservoir. Two wall vents feed
    # dedicated air shafts all the way down, so continuous generation can never
    # fill the entire cave network. The solver absorbs extra mass at this row.
    bottom=doc.height-1
    for x in range(1,doc.width-1):
        doc.erase_layer("terrain",x,bottom)
        doc.erase_layer("ground_layer",x,bottom)
        doc.set("lava",x,bottom,1.0)
    lava_sources=[]
    for sx in (max(8,int(doc.width*0.31)), min(doc.width-9,int(doc.width*0.69))):
        # Keep the historic groundwater conduit and its stone bed intact.
        sy=max(level_rows[-1]+2,_underground_waterway_row(doc.height)+2)
        sy=min(bottom-1,sy)
        # Source is visually embedded beside a rock wall; the cell itself and
        # the vertical drain shaft are always open to the reservoir.
        for yy in range(sy,bottom):
            doc.erase_layer("terrain",sx,yy)
            doc.erase_layer("ground_layer",sx,yy)
            doc.erase_layer("water",sx,yy)
        doc.set("lava",sx,sy,0.62)
        lava_sources.append([sx,sy,0.40])
    doc.metadata["lava_sources"]=lava_sources
    doc.metadata["lava_bottom_reservoir_row"]=bottom
    return zones,pools,ecosystem_spawns

def add_underground_world(doc, seed=None, target_height=UNDERGROUND_MIN_WORLD_HEIGHT):
    """Extend an existing ecology map downward while preserving its surface.

    This is the editor-safe upgrade path. The upper authored world remains in
    place; only deep geology/old generated waterway rows are changed.
    """
    if seed is None:
        seed=int((getattr(doc,"metadata",{}) or {}).get("seed",20260831) or 20260831)
    seed=int(seed)
    old_height=int(doc.height)
    target_height=max(int(target_height),int(UNDERGROUND_MIN_WORLD_HEIGHT),old_height)
    _remove_old_generated_waterway(doc)
    if target_height > old_height:
        doc.resize(doc.width,target_height)
        spans=(getattr(doc,"metadata",{}) or {}).get("biome_spans",default_biome_spans(doc.width))
        for x in range(doc.width):
            for y in range(old_height,target_height):
                if doc.get("terrain",x,y,None) is None:
                    _set_solid(doc,x,y,_deep_geology_material(seed,x,y,target_height))

    # Re-close the generated underground area before carving. This affects only
    # rows safely below each local surface and makes the operation deterministic
    # if the user presses "地下世界" more than once.
    spans=(getattr(doc,"metadata",{}) or {}).get("biome_spans",default_biome_spans(doc.width))
    safe_global=max(18,int(WORLD_GEN_SURFACE_ROW)+int(UNDERGROUND_START_DEPTH_TILES))
    for x in range(doc.width):
        local_surface=_surface_row_for_column(doc,x)
        start=max(safe_global,local_surface+int(UNDERGROUND_START_DEPTH_TILES))
        for y in range(start,doc.height-2):
            # Leave the last two rows as a stable world base.
            if str(doc.get("terrain",x,y,None) or "") == "ladder":
                doc.erase_layer("terrain",x,y)
            if doc.get("terrain",x,y,None) is None:
                _set_solid(doc,x,y,_deep_geology_material(seed,x,y,doc.height))
            doc.erase_layer("water",x,y)
            doc.erase_layer("lava",x,y)
            doc.erase_layer("honey",x,y)
            doc.erase_layer("fire",x,y)
            doc.erase_layer("vegetation",x,y)
            doc.erase_layer("hazard",x,y)
            # Generated underground decorations are rebuilt below.
            deco=doc.get("decoration",x,y,None)
            if isinstance(deco,(tuple,list)) and deco and str(deco[0]) in ("lamp","fence"):
                doc.erase_layer("decoration",x,y)

    # Three continuous traversal bands, plus natural chambers.
    level_rows=[
        min(doc.height-10,max(20,int(UNDERGROUND_FIRST_LEVEL_ROW))),
        min(doc.height-9,max(30,int(UNDERGROUND_SECOND_LEVEL_ROW))),
        min(doc.height-8,max(42,int(UNDERGROUND_DEEP_LEVEL_ROW))),
    ]
    tunnel_paths=[]
    for i,row in enumerate(level_rows):
        tunnel_paths.append(_carve_tunnel_band(doc,seed,row,2900+i*40,5,doc.width-6,1))

    cavern_rows=[]
    for i in range(14):
        cx=18 + int(_hash01(seed,i,salt=3001)*(doc.width-36))
        level=level_rows[i%len(level_rows)]
        cy=level + int(round((_hash01(seed,i,salt=3002)-0.5)*6.0))
        rx=4+int(_hash01(seed,i,salt=3003)*6)
        ry=2+int(_hash01(seed,i,salt=3004)*3)
        _carve_cavern(doc,cx,cy,rx,ry)
        cavern_rows.append([cx,cy,rx,ry])

    # Structured underground locations distributed under major land biomes.
    span_map=_span_map(spans)
    dungeon_specs=[]
    for idx,(biome,level,style,ratio) in enumerate((
        (BIOME_PLAINS,level_rows[1]-5,"limestone",0.56),
        (BIOME_RAINFOREST,level_rows[1]-3,"stone",0.58),
        (BIOME_DESERT,level_rows[2]-5,"marble",0.46),
    )):
        start,end=span_map.get(biome,(10,doc.width-10))
        center=int(start+(end-start)*ratio)
        dungeon=_build_dungeon_cluster(doc,seed,center,level,style,idx)
        dungeon_specs.append(dungeon)
        # Connect dungeon upper hall to nearest main tunnel on both sides.
        bx0,by0,bx1,by1=dungeon["bounds"]
        path_index=1 if idx<2 else 2
        left_target_x=max(5,bx0-8); right_target_x=min(doc.width-6,bx1+8)
        left_target_y=_path_y_at(tunnel_paths[path_index],left_target_x,level_rows[path_index])
        right_target_y=_path_y_at(tunnel_paths[path_index],right_target_x,level_rows[path_index])
        left_door_x,left_door_y=dungeon["upper_left_door"]
        right_door_x,right_door_y=dungeon["upper_right_door"]
        _carve_corridor(doc,left_door_x,left_door_y,left_target_x,left_target_y+1,3)
        _carve_corridor(doc,right_door_x,right_door_y,right_target_x,right_target_y+1,3)

    # Surface access: one ladder shaft in plains, one natural rock-climb shaft
    # in rainforest, and a second ladder into the desert/deep dungeon system.
    entrances=[]
    access_specs=(
        (BIOME_PLAINS,0.34,"ladder",0),
        (BIOME_RAINFOREST,0.30,"rock",0),
        (BIOME_DESERT,0.28,"ladder",1),
    )
    for biome,ratio,kind,path_index in access_specs:
        start,end=span_map.get(biome,(8,doc.width-8))
        x=max(3,min(doc.width-4,int(start+(end-start)*ratio)))
        surface=_surface_row_for_column(doc,x)
        top=surface-2
        target=_path_y_at(tunnel_paths[path_index],x,level_rows[path_index])
        bottom=max(surface+3,int(target)+1)
        # Open the entrance mouth through the surface while preserving a solid
        # lip on either side, so vertical travel is the intended path.
        for y in range(max(1,top),min(doc.height-2,bottom)+1):
            _carve_air(doc,x,y)
        if kind=="ladder":
            info=_place_ladder_column(doc,x,max(1,top),bottom)
        else:
            info=_place_rock_climb_shaft(doc,x,max(1,top),bottom,"right")
            if info:
                # Open a landing through the climb wall where it meets the
                # horizontal tunnel. The climb face ends above the landing, so
                # the tunnel remains traversable left↔right.
                for yy in range(max(1,int(target)-1),min(doc.height-1,int(target)+2)):
                    _carve_air(doc,int(info["wall_x"]),yy)
                info["landing_row"]=int(target)
        if info:
            info.update({"biome":str(biome),"surface_row":int(surface)})
            entrances.append(info)

    # Vertical links between underground bands. Alternate ladder and climb so
    # both existing movement mechanics are useful below ground.
    links=[]
    for i,x in enumerate((doc.width//4,doc.width//2,(doc.width*3)//4)):
        top_link=_path_y_at(tunnel_paths[0],x,level_rows[0])-1
        bottom_link=_path_y_at(tunnel_paths[2],x,level_rows[2])+1
        if i==1:
            link=_place_rock_climb_shaft(doc,x,top_link,bottom_link,"right")
            if link:
                landing_rows=[]
                for path_index in (0,1,2):
                    landing=_path_y_at(tunnel_paths[path_index],x,level_rows[path_index])
                    landing_rows.append(int(landing))
                    for yy in range(max(1,landing-1),min(doc.height-1,landing+2)):
                        _carve_air(doc,int(link["wall_x"]),yy)
                link["landing_rows"]=landing_rows
        else:
            link=_place_ladder_column(doc,x,top_link,bottom_link)
        if link: links.append(link)

    # The ONLY free horizontal groundwater conduit stays near the bottom, below
    # the ordinary caves/dungeons. It remains physically separate from tunnels.
    waterway_row=_underground_waterway_row(doc.height)
    if waterway_row+1 < doc.height:
        for x in range(1,doc.width-1):
            _carve_air(doc,x,waterway_row)
            _set_solid(doc,x,waterway_row+1,"stone")
            doc.set("water",x,waterway_row,0.24)

    # FIX32 underground geology/ecology pass happens after every corridor is
    # carved so partial-rock geometry follows the final traversable surface.
    terraced_count=_apply_underground_terracing(doc,seed,safe_global)
    underground_biomes,magma_pools,ecosystem_spawns=_decorate_underground_ecology(doc,seed,spans,level_rows,dungeon_specs)

    meta=doc.metadata
    meta.update({
        "generator":"v0.7.7.5-fix27-underground-world",
        "width":int(doc.width),
        "height":int(doc.height),
        "underground_version":1,
        "underground_start_row":int(safe_global),
        "underground_level_rows":[int(v) for v in level_rows],
        "underground_waterway_row":int(waterway_row),
        "underground_entrances":entrances,
        "underground_links":links,
        "underground_dungeons":dungeon_specs,
        "underground_caverns":cavern_rows,
        "underground_biomes":underground_biomes,
        "creature_spawns":ecosystem_spawns,
        "magma_pools":magma_pools,
        "underground_terraced_cells":int(terraced_count),
        "underground_rules":{
            "surface_to_underground_requires_ladder_or_climb":True,
            "ladder_no_stamina":True,
            "rock_climb_uses_stamina":True,
            "dungeon_walls_use_solid_rock":True,
            "bottom_waterway_is_only_horizontal_subsurface_water":True,
        },
    })
    return doc


def validate_underground_world(doc):
    """Validate only FIX27 underground traversal/content rules."""
    errors=[]
    meta=getattr(doc,"metadata",{}) or {}
    if int(doc.height) < int(UNDERGROUND_MIN_WORLD_HEIGHT):
        errors.append(f"地下世界深度不足：{doc.height} < {UNDERGROUND_MIN_WORLD_HEIGHT}")
    underground_rules=meta.get("underground_rules",{}) or {}
    if not bool(underground_rules.get("surface_to_underground_requires_ladder_or_climb",False)):
        errors.append("地下入口沒有標記梯子/攀岩通行規則")
    entrances=meta.get("underground_entrances",[]) or []
    if len(entrances) < 2:
        errors.append("地下世界至少需要兩個獨立地表入口")
    elif not any(str(row.get("type",""))=="ladder" for row in entrances if isinstance(row,dict)):
        errors.append("地下世界缺少梯子入口")
    elif not any(str(row.get("type",""))=="rock_climb" for row in entrances if isinstance(row,dict)):
        errors.append("地下世界缺少岩壁攀爬入口")
    dungeons=meta.get("underground_dungeons",[]) or []
    if len(dungeons) < 3:
        errors.append("地下城/地牢數量不足")
    levels=meta.get("underground_level_rows",[]) or []
    if len(levels) < 3 or not all(0 < int(v) < doc.height-2 for v in levels):
        errors.append("地下通道樓層資料不完整")
    for level in levels[:3]:
        air=0
        for x in range(4,doc.width-4,max(1,doc.width//48)):
            for y in range(max(1,int(level)-3),min(doc.height-1,int(level)+4)):
                if doc.get("terrain",x,y,None) is None:
                    air+=1
                    break
        if air < 6:
            errors.append(f"地下第 {level} 列缺少可通行空間")
            break
    return (not errors),tuple(errors)


def validate_generated_world(doc):
    """Validate the current project's non-negotiable world rules.

    This is intentionally cheap and runs only after random generation.  It
    prevents the editor from silently exporting a world that violates the
    hydrology/biome assumptions used by gameplay systems.
    """
    errors=[]
    meta=getattr(doc, "metadata", {}) or {}
    spans=meta.get("biome_spans", default_biome_spans(doc.width))
    order=[str(row[2]) for row in spans if isinstance(row,(list,tuple)) and len(row)>=3]
    expected=[BIOME_OCEAN,BIOME_SWAMP,BIOME_LAKE,BIOME_PLAINS,BIOME_VILLAGE,BIOME_RAINFOREST,BIOME_SNOW_MOUNTAIN,BIOME_DESERT]
    # The packaged main map closes the horizontal cylinder with a short ocean
    # coast after the desert.  Raw generator previews retain the canonical
    # eight-span order; both forms describe the same ecology contract.
    wrapped_expected = expected + [BIOME_OCEAN]
    valid_order = order == expected or (
        bool(meta.get("horizontal_wrap", False)) and order == wrapped_expected
    )
    if not valid_order:
        errors.append("生態區順序不是 ocean→swamp→lake→plains→village→rainforest→snow_mountain→desert")

    rules=meta.get("hydrology_rules", {})
    if not bool(rules.get("porous_vertical_only", False)):
        errors.append("透水層必須只允許垂直孔隙滲流")
    if bool(rules.get("soil_lateral_seepage", True)):
        errors.append("土壤/沙孔隙水不可水平滲流")
    if not bool(rules.get("horizontal_subsurface_only_in_waterway", False)):
        errors.append("地下水平水必須限制在自由水道")

    span_map=_span_map(spans)
    for biome, required_top in ((BIOME_OCEAN, "sea_sand"), (BIOME_LAKE, "dirt")):
        start,end=span_map.get(biome,(0,0))
        sample_step=max(1,(end-start)//24)
        for x in range(start,end,sample_step):
            top=None
            # FIX73 sky islands may be the first solid in a sampled column.
            # Surface-material validation begins at the shared ground datum.
            for y in range(max(0, int(WORLD_GEN_SURFACE_ROW)), doc.height):
                v=doc.get("terrain",x,y,None)
                if v is not None:
                    top=(y,str(v)); break
            if top is None:
                errors.append(f"{biome} X={x} 沒有地表")
                break
            if top[1] != required_top:
                errors.append(f"{biome} 地表材質錯誤：{top[1]}，應為 {required_top}")
                break

    # Lake/ocean open water may not receive terrestrial vegetation.
    for (x,y), veg in tuple(doc.layers.get("vegetation", {}).items()):
        biome=biome_at_tile_x(x,spans,doc.width)
        if biome in (BIOME_OCEAN,BIOME_LAKE) and doc.get("water",x,max(0,y-1),None) is not None:
            errors.append(f"{biome} 開放水域出現陸生植物 X={x}")
            break

    waterway=int(meta.get("underground_waterway_row", -1) or -1)
    if waterway < 0 or waterway+1 >= doc.height:
        errors.append("地下自由水道列缺失")
    else:
        for x in range(1,doc.width-1,max(1,doc.width//32)):
            if doc.get("terrain",x,waterway,None) is not None:
                errors.append("地下自由水道被實體圖塊堵住")
                break
            if doc.get("terrain",x,waterway+1,None) != "stone":
                errors.append("地下自由水道缺少不透水岩床")
                break

    underground_ok, underground_errors = validate_underground_world(doc)
    if not underground_ok:
        errors.extend(underground_errors)

    for (_key,mask) in tuple(doc.layers.get("ground_layer", {}).items()):
        if int(mask) not in (1,3,7):
            errors.append("分層圖塊只允許 LOW/MID/FULL = 1/3/7")
            break

    tree_system = meta.get("rainforest_tree_system", {})
    if not isinstance(tree_system, dict) or int(tree_system.get("version", 0) or 0) < 1:
        errors.append("缺少 FIX68 雨林巨木系統 metadata")
    else:
        trees = tree_system.get("trees", ()) or ()
        segments = tree_system.get("segments", ()) or ()
        if int(tree_system.get("tree_count", -1)) != len(trees):
            errors.append("雨林巨木 tree_count 與 trees 不一致")
        if int(tree_system.get("segment_count", -1)) != len(segments):
            errors.append("雨林巨木 segment_count 與 segments 不一致")
        if len(segments) > int(tree_system.get("max_live_segments", 640) or 640):
            errors.append("雨林巨木分段超過手機固定預算")
        if not trees:
            errors.append("雨林沒有生成可攀爬巨木")
        if int(tree_system.get("minimum_clear_ground_columns", 0) or 0) < 8 and len(trees) > 1:
            errors.append("雨林巨木間距不足，地面路線可能被封鎖")
        role_to_tile = {
            "root": "rainforest_root",
            "trunk": "rainforest_trunk",
            "branch": "rainforest_branch",
            "vine": "rainforest_vine",
            "canopy": "rainforest_canopy",
        }
        tree_roles = {}
        for row in segments:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                errors.append("雨林巨木 segment 格式錯誤")
                break
            tx, ty, role, tree_id = int(row[0]), int(row[1]), str(row[2]), str(row[3])
            expected_tile = role_to_tile.get(role)
            if expected_tile is None or doc.get("terrain", tx, ty, None) != expected_tile:
                errors.append(f"雨林巨木分段材質錯誤：{tree_id}/{role}@{tx},{ty}")
                break
            tree_roles.setdefault(tree_id, set()).add(role)
        for tree in trees:
            tree_id = str(tree.get("id", ""))
            roles = tree_roles.get(tree_id, set())
            if int(tree.get("height_tiles", 0) or 0) < 5:
                errors.append(f"雨林巨木高度不足：{tree_id}")
                break
            if not {"root", "trunk", "branch", "vine", "canopy"}.issubset(roles):
                errors.append(f"雨林巨木缺少攀爬/平台分段：{tree_id}")
                break
    sky_system = meta.get("sky_island_system", {})
    sky_regions = sky_system.get("regions", ()) if isinstance(sky_system, dict) else ()
    if int((sky_system or {}).get("version", 0) or 0) < 1 or len(sky_regions) < 4:
        errors.append("缺少可探索的 FIX73 空島群")
    else:
        for region in sky_regions:
            if not isinstance(region, dict) or not region.get("spawn_columns"):
                errors.append("空島缺少安全生物出生點")
                break
            for tx, floor in region.get("spawn_columns", ()):
                tx, floor = int(tx), int(floor)
                if doc.get("terrain", tx, floor, None) is None:
                    errors.append(f"空島出生點沒有實體支撐：{tx},{floor}")
                    break
                if any(doc.get("terrain", tx, floor-dy, None) is not None for dy in (1, 2, 3)):
                    errors.append(f"空島出生點上方空間不足：{tx},{floor}")
                    break
            if errors and errors[-1].startswith("空島"):
                break
    return (not errors), tuple(errors)

def generate_world(seed, width=WORLD_GEN_DEFAULT_WIDTH, height=WORLD_GEN_DEFAULT_HEIGHT):
    seed = int(seed)
    width = max(192, int(width))
    height = max(int(UNDERGROUND_MIN_WORLD_HEIGHT), int(height))
    doc = MapDocument(width=width, height=height)
    for layer in doc.layers.values():
        layer.clear()

    spans = default_biome_spans(width)
    biomes = [biome_at_tile_x(x, spans, width) for x in range(width)]
    surface = [_surface_height(seed, x, height, biomes[x], spans) for x in range(width)]
    surface = _smooth_surface(surface, biomes)

    # FIX46 lake hydrology: preserve a short raised dry bank on the plains
    # side *after* biome seam smoothing.  In FIX45 the seam interpolation
    # lowered the first plains columns beneath the authored lake free surface;
    # once the liquid solver woke after new-game/load, fractional lake water
    # escaped over the flat grassland and appeared as a mysterious surface
    # sheet. Two full-height bank columns stop the spill, followed by a gentle
    # LOW/MID/HIGH transition back into the plains.
    try:
        _span_lookup = _span_map(spans)
        _plains_start, _plains_end = _span_lookup[BIOME_PLAINS]
        _surface_offset = float(int(WORLD_GEN_SURFACE_ROW) - 12)
        _bank_profile = tuple(
            value + _surface_offset
            for value in (10.95, 11.10, 11.30, 11.48, 11.70, 11.92)
        )
        for _i, _target in enumerate(_bank_profile):
            _x = int(_plains_start) + _i
            if _x < int(_plains_end) and 0 <= _x < len(surface):
                surface[_x] = min(float(surface[_x]), float(_target))
    except Exception:
        pass

    initial_ground_layers = []
    sea_level = _ocean_water_surface_y(height)
    for x in range(width):
        surface_value = max(2.0, min(float(height - 4), float(surface[x])))
        top = max(2, min(height - 4, int(math.floor(surface_value))))
        biome = biomes[x]
        surface_material = _surface_material(biome, x=x, spans=spans)
        doc.set("terrain", x, top, surface_material)
        for y in range(top + 1, height):
            depth = y - top
            doc.set("terrain", x, y, _subsurface_material(seed, biome, depth, x, y, height))

        # V0.7.6.3: sand/sea-sand follows exactly the same LOW/MID/HIGH top
        # geometry as dirt, including the underwater beach/seabed slope.  The
        # renderer fills the empty fraction of a submerged partial sea-sand cell
        # with visual seawater, while authoritative water remains in open cells.
        # This removes one-tile staircase teeth without introducing lateral pore
        # flow through sand.
        if surface_material in ("dirt", "grass_dirt", "mud", "swamp_soil", "sand", "jungle_soil", "snow_dirt", "sea_sand"):
            mask = _surface_layer_mask(surface_value)
            if mask != SOIL_LAYER_FULL:
                initial_ground_layers.append([int(x), int(top), int(mask)])
                doc.set("ground_layer", x, top, int(mask))

        # Water is authored before vegetation so biome-specific decoration can
        # intentionally know whether the zone is open water or wetland.
        _fill_water_body(doc, seed, x, biome, top, height, spans)
        _decorate_surface(doc, seed, x, top, biome, spans, height)

    # FIX27: cave/tunnel/dungeon generation also authors the relocated bottom
    # groundwater conduit after every traversable underground layer exists.
    add_underground_world(doc, seed, target_height=height)
    waterway_row = int(doc.metadata.get("underground_waterway_row", _underground_waterway_row(height)))

    # FIX68 builds the rainforest *after* underground carving so tall canopy
    # tiles can never be mistaken for the natural surface by tunnel generation.
    rainforest_tree_system = _build_rainforest_giant_trees(
        doc, seed, surface, spans
    )
    # Build sky terrain last: underground generation must continue to read the
    # original ground surface rather than the underside of a floating island.
    sky_island_system = _build_sky_islands(doc, seed, surface, spans)

    span_map = _span_map(spans)
    plains_start, plains_end = span_map[BIOME_PLAINS]
    savanna_start=max(plains_start+8,int(plains_start+(plains_end-plains_start)*0.56))
    savanna_end=max(savanna_start+12,plains_end-5)
    savanna_columns=[]
    step=max(4,(savanna_end-savanna_start)//10)
    for tx in range(savanna_start,savanna_end,step):
        floor=max(2,min(height-2,int(math.floor(surface[tx]))))
        savanna_columns.append([int(tx),int(floor)])
        # Sparse dry grass makes the fauna district readable without replacing
        # the broad temperate biome or breaking the biome seam contract.
        if (tx-savanna_start)//step%2==0:
            doc.set("vegetation",tx,floor,("grass",1))
    fauna_regions={
        "savanna":{"label":"稀樹草原","bounds":[savanna_start,0,savanna_end,height-1],
                   "spawn_columns":savanna_columns,"parent_biome":BIOME_PLAINS},
    }
    spawn_x = max(plains_start + 8, min(plains_end - 8, plains_start + int((plains_end - plains_start) * 0.42)))
    spawn_y = int(math.floor(surface[spawn_x]))
    doc.player_spawn = [spawn_x, spawn_y]
    doc.metadata.update({
        "name": "generated_realistic_ecology_world",
        "generated": True,
        "generator": "v0.7.7.5-fix74-ecosystem-world",
        "seed": seed,
        "width": width,
        "height": height,
        "surface_row_at_spawn": spawn_y,
        "biome_spans": spans,
        "biome_version": 8,
        "layout": [
            "sky_island",
            "ocean",
            "swamp",
            "lake",
            "plains",
            "village",
            "rainforest",
            "snow_mountain",
            "desert",
        ],
        "initial_ground_layers": initial_ground_layers,
        "rainforest_tree_system": rainforest_tree_system,
        "rainforest_tree_count": int(rainforest_tree_system.get("tree_count", 0)),
        "rainforest_tree_segment_count": int(rainforest_tree_system.get("segment_count", 0)),
        "sky_island_system": sky_island_system,
        "sky_islands": list(sky_island_system.get("regions", ())),
        "sky_island_count": int(sky_island_system.get("region_count", 0)),
        "special_habitats": {
            "sky_island": {"label": "空島群", "source": "sky_islands"},
            "cave": {"label": "天然洞窟", "source": "underground_caverns"},
            "magma_hell": {"label": "熔岩地獄", "source": "underground_biomes.main_magma_hell.safe_ledges"},
            "savanna": {"label": "稀樹草原", "source": "fauna_regions.savanna"},
            "hive_chamber": {"label": "皇家蜂巢", "source": "underground_biomes.royal_hive"},
            "underground_lake": {"label": "尼斯地底湖", "source": "underground_biomes.ness_deep_lake"},
            "lost_world": {"label": "失落世界洞窟", "source": "underground_biomes.lost_world_cavern"},
        },
        "fauna_regions":fauna_regions,
        "underground_waterway_row": int(waterway_row),
        "background_catalog": "assets/underground_background_catalog.json",
        "background_profile_version": 1,
        "background_layer_order": ["far", "mid", "near"],
        "background_draw_phase": "background",
        "background_collision": False,
        "background_quad_budget": 128,
        "background_backdrop_reserve": 128,
        "background_generator": "fix68.data_driven_parallax.v1",
        "background_profile_mode": "underground_biome_bounds",
        "hydrology_rules": {
            "porous_vertical_only": True,
            "soil_lateral_seepage": False,
            "surface_free_water_lateral": True,
            "split_infiltration_and_surface_flow": True,
            "horizontal_subsurface_only_in_waterway": True,
            "sealed_ecosystem_exceptions": ["ness_deep_lake"],
            "sealed_nonporous_cavity_air_lock": True,
        },
        "biome_fauna": {
            str(biome): list(BIOME_CREATURE_ROTATION.get(biome, ()))
            for biome in (BIOME_OCEAN, BIOME_SWAMP, BIOME_LAKE, BIOME_PLAINS, BIOME_VILLAGE, BIOME_RAINFOREST, BIOME_SNOW_MOUNTAIN, BIOME_DESERT)
        },
        "editor_rules_version": 9,
        "ecology_notes": {
            "ocean_bottom": "saturated_porous_sea_sand",
            "lake_bottom": "saturated_soil_vertical_infiltration",
            "lake_terrestrial_vegetation": False,
            "mountain_treeline": True,
            "default_cells": width * height,
            "underground_world": True,
            "underground_access": "ladder_or_wall_climb",
            "rainforest_canopy": "segmented_climbable_platform_trees",
            "rainforest_ground_route_min_clear_columns": 8,
            "sky_island_region": True,
            "sky_island_access": "grapple_hover_or_aerial",
            "expanded_width_height_depth": [int(width), int(WORLD_GEN_SURFACE_ROW), int(height)],
        },
    })
    ok, errors = validate_generated_world(doc)
    if not ok:
        raise RuntimeError("Generated world violates rules: " + " | ".join(errors[:6]))
    return doc
