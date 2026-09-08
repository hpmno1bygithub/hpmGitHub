# -*- coding: utf-8 -*-
"""FIX69 original Wuxia secondary-world authoring.

The module is deliberately an offline authoring helper.  Gameplay consumes the
saved map and ``assets/secondary_worlds/wuxia.json`` through the shared
secondary-world runtime; no per-frame Wuxia generator is kept alive.

The setting and creatures are original to this project.  The four districts
use broad Chinese landscape and Wuxia visual themes without reproducing a
particular film, game, character, or protected map layout.
"""
from __future__ import annotations

import json
import math
import os
import struct
import zlib

from map_editor.model import MapDocument


WORLD_WIDTH = 384
WORLD_HEIGHT = 80
REGIONS = (
    ("bamboo_forest", 0, 96, "wuxia_bamboo"),
    ("karst_rivers", 96, 192, "wuxia_karst"),
    ("stalactite_cave", 192, 288, "wuxia_stalactite"),
    ("graveyard", 288, 384, "wuxia_graveyard"),
)


def _solid(doc, x, y, material="stone"):
    if not doc.in_bounds(x, y):
        return
    doc.set("terrain", x, y, material)
    doc.erase_layer("ground_layer", x, y)
    doc.erase_layer("water", x, y)
    doc.erase_layer("lava", x, y)


def _air(doc, x, y):
    if not doc.in_bounds(x, y):
        return
    doc.erase_layer("terrain", x, y)
    doc.erase_layer("ground_layer", x, y)
    doc.erase_layer("water", x, y)
    doc.erase_layer("lava", x, y)


def _fill_below(doc, x, floor, top_material, deep_material="stone"):
    for y in range(int(floor), doc.height):
        _solid(doc, x, y, top_material if y < int(floor) + 3 else deep_material)


def _carve_rect(doc, x0, y0, x1, y1):
    for x in range(max(1, int(x0)), min(doc.width - 1, int(x1) + 1)):
        for y in range(max(1, int(y0)), min(doc.height - 1, int(y1) + 1)):
            _air(doc, x, y)


def _carve_round(doc, cx, cy, rx, ry):
    rx = max(2, int(rx)); ry = max(2, int(ry))
    for y in range(int(cy) - ry, int(cy) + ry + 1):
        ratio = float(y - int(cy)) / float(ry)
        span = int(round(rx * math.sqrt(max(0.0, 1.0 - ratio * ratio))))
        _carve_rect(doc, int(cx) - span, y, int(cx) + span, y)


def _carve_stepped_route(doc, points, height=5):
    for (x0, floor0), (x1, floor1) in zip(points, points[1:]):
        count = max(abs(int(x1) - int(x0)), abs(int(floor1) - int(floor0)), 1) * 2
        for step in range(count + 1):
            t = step / float(count)
            x = int(round(float(x0) + (float(x1) - float(x0)) * t))
            floor = int(round(float(floor0) + (float(floor1) - float(floor0)) * t))
            for ox in (-1, 0, 1):
                for y in range(floor - int(height), floor):
                    _air(doc, x + ox, y)
                _solid(doc, x + ox, floor, "limestone")


def _ladder(doc, x, top, bottom):
    for y in range(max(1, int(top)), min(doc.height - 1, int(bottom)) + 1):
        _air(doc, x, y)
        doc.set("terrain", x, y, "ladder")


def _decorate(doc, x, floor, kind, size=2):
    if doc.in_bounds(x, floor):
        doc.set("decoration", int(x), int(floor), (str(kind), int(size)))


def _trap(trap_id, kind, x, **values):
    row = {
        "id": str(trap_id), "type": str(kind), "x": int(x),
        "layer": "background_hazard", "diggable": False,
    }
    row.update(values)
    return row


def _portal(doc, portal_id, target_map, target_portal, tx, floor):
    tx = int(tx); floor = int(floor)
    # Doorway plus a four-tile interior arrival apron.  The larger range is
    # intentional: karst/tomb landmarks may otherwise leave the target's head
    # inside a foreground column even though the 3x3 trigger itself is clear.
    for x in range(tx - 1, tx + 6):
        for y in range(floor - 4, floor):
            _air(doc, x, y)
        _solid(doc, x, floor, "stone")
    _decorate(doc, tx, floor, "ink_gate_portal", 3)
    row = {
        "id": str(portal_id),
        "rect": [tx - 1, floor - 3, 3, 3],
        "arrival": [tx + 4, floor],
        "target_map": str(target_map),
        "target_portal": str(target_portal),
        "visual": "wuxia_ink_gate",
        "requires_floor_entry": True,
        "entry_floor": floor,
        "entry_y_tolerance_px": 8,
    }
    doc.metadata.setdefault("portals", []).append(row)
    return row


def _build_bamboo(doc):
    # A readable ground route plus two canopy routes.  Bamboo trunks use the
    # proven climbable rainforest trunk/branch tiles from FIX68, while their
    # colour/shape is replaced by the secondary-world background and decor.
    floors = {}
    for x in range(0, 96):
        floor = 49 + (1 if (x // 13) % 3 == 1 else 0) - (1 if 60 <= x < 73 else 0)
        floors[x] = floor
        _fill_below(doc, x, floor, "jungle_soil", "stone")
    for base_x, height in ((17, 13), (30, 18), (45, 15), (61, 20), (78, 16), (89, 12)):
        floor = floors[base_x]
        for y in range(floor - height, floor):
            doc.set("terrain", base_x, y, "rainforest_trunk")
        crown = floor - height
        for x in range(base_x - 4, base_x + 5):
            doc.set("terrain", x, crown + (1 if abs(x - base_x) > 2 else 0), "rainforest_branch")
        for y in range(crown + 2, min(floor - 1, crown + 9)):
            doc.set("terrain", base_x + 3, y, "rainforest_vine")
        _decorate(doc, base_x - 2, floor, "bamboo_cluster", 3)
    # Moon gate, tea shelter, stone lanterns and a lower ravine detour.
    for x, kind, size in (
        (9, "moon_gate", 3), (24, "stone_lantern", 1), (39, "tea_pavilion", 3),
        (55, "bamboo_wind_chime", 2), (72, "sword_training_post", 2),
        (91, "ink_boundary_stele", 3),
    ):
        _decorate(doc, x, floors[x], kind, size)
    for x in range(68, 76):
        floor = floors[x]
        _air(doc, x, floor)
        _air(doc, x, floor + 1)
        _solid(doc, x, floor + 2, "stone")
    for x in range(67, 77):
        doc.set("terrain", x, floors.get(x, 49) - 1, "wood")
    return floors


def _build_karst(doc):
    floors = {}
    for x in range(96, 192):
        # River valley undulates gently; stone outcrops give a distinct Guilin
        # silhouette without copying a real location.
        floor = 50 + int(round(2.0 * math.sin((x - 96) / 10.0)))
        floors[x] = floor
        _fill_below(doc, x, floor, "limestone", "stone")
    # Two water courses with physical wooden crossings.
    for lo, hi in ((112, 132), (151, 169)):
        bank_floor = max(floors[x] for x in range(lo, hi + 1))
        for x in range(lo, hi + 1):
            for y in range(floors[x], bank_floor + 3):
                _air(doc, x, y)
            _solid(doc, x, bank_floor + 3, "limestone")
            for y in range(bank_floor, bank_floor + 3):
                doc.set("water", x, y, 1.0 if y > bank_floor else 0.72)
            doc.set("terrain", x, bank_floor - 1, "wood")
    # Karst towers are climbable through short tunnel steps and ladders.
    for cx, tower_h in ((104, 12), (139, 17), (178, 14), (187, 20)):
        floor = floors[cx]
        for x in range(cx - 3, cx + 4):
            peak = floor - max(3, tower_h - abs(x - cx) * 3)
            for y in range(peak, floor):
                _solid(doc, x, y, "limestone")
        _carve_rect(doc, cx - 1, floor - 8, cx + 1, floor - 3)
        _ladder(doc, cx, floor - 8, floor - 1)
    for x, kind, size in (
        (100, "mist_stone_arch", 3), (119, "bamboo_raft", 2),
        (136, "waterfall_veil", 3), (158, "cormorant_post", 2),
        (176, "cliff_temple", 3), (188, "cave_mouth", 3),
    ):
        _decorate(doc, x, floors[x], kind, size)
    return floors


def _build_stalactite(doc):
    # Start with a solid envelope; carve an S-shaped main path, a high loop,
    # lower crystal pool, and two ladder shortcuts.
    for x in range(192, 288):
        for y in range(0, doc.height):
            _solid(doc, x, y, "limestone" if (x + y) % 5 else "stone")
    chambers = (
        (200, 49, 10, 8), (220, 42, 15, 10), (244, 53, 17, 11),
        (266, 37, 16, 10), (281, 50, 8, 8), (238, 25, 11, 7),
    )
    for values in chambers:
        _carve_round(doc, *values)
    _carve_stepped_route(doc, ((193, 50), (207, 49), (221, 43), (244, 54)), 6)
    _carve_stepped_route(doc, ((244, 54), (256, 48), (267, 38), (282, 50)), 6)
    _carve_stepped_route(doc, ((219, 42), (229, 33), (238, 31), (250, 39), (266, 38)), 5)
    _ladder(doc, 231, 26, 49)
    _ladder(doc, 272, 35, 50)
    # Shallow luminous pool; stone ledges ensure it is optional rather than a
    # forced swim corridor.
    for x in range(235, 253):
        for y in range(54, 58):
            _air(doc, x, y)
            doc.set("water", x, y, 0.78 if y == 54 else 1.0)
        _solid(doc, x, 58, "limestone")
    for x, floor, kind, size in (
        (198, 50, "stalactite_fang", 3), (214, 46, "echo_bell_rock", 2),
        (232, 31, "stone_lotus", 2), (244, 54, "luminous_cave_pool", 3),
        (260, 45, "stalactite_curtain", 3), (276, 48, "ancient_sword_mark", 3),
    ):
        _decorate(doc, x, floor, kind, size)
    return {x: 50 for x in range(192, 288)}


def _build_graveyard(doc):
    floors = {}
    for x in range(288, 384):
        floor = 49 + int(round(1.5 * math.sin((x - 288) / 8.0)))
        if x >= 340:
            floor = 48  # readable boss arena
        floors[x] = floor
        _fill_below(doc, x, floor, "ash", "stone")
    # Low burial mounds have open tomb passages beneath, joined into a loop.
    for cx in (302, 326):
        floor = floors[cx]
        for x in range(cx - 7, cx + 8):
            mound = floor - max(0, 4 - abs(x - cx) // 2)
            for y in range(mound, floor):
                _solid(doc, x, y, "ash")
        _carve_round(doc, cx, floor + 4, 7, 4)
    _carve_stepped_route(doc, ((295, 53), (310, 56), (326, 53), (339, 48)), 4)
    _ladder(doc, 310, 47, 56)
    # Boss arena walls and sealed-looking but passable sword gate.
    for y in range(37, 48):
        _solid(doc, 339, y, "marble")
    _carve_rect(doc, 338, 42, 340, 47)
    for x in range(340, 381):
        _solid(doc, x, 48, "marble")
        for y in range(43, 48):
            _air(doc, x, y)
    for x, kind, size in (
        (292, "crooked_gravestone", 2), (301, "dead_willow", 3),
        (315, "paper_talisman_tree", 3), (327, "mass_grave_stele", 3),
        (339, "sword_tomb_gate", 3), (350, "broken_sword_mound", 3),
        (363, "grandmaster_sword_stele", 3), (377, "soul_lantern", 2),
    ):
        _decorate(doc, x, floors[x], kind, size)
    return floors


def build_wuxia_world(seed=20260903):
    """Return one authored map containing four contiguous Wuxia districts."""
    doc = MapDocument(WORLD_WIDTH, WORLD_HEIGHT)
    for layer in doc.layers.values():
        layer.clear()
    bamboo = _build_bamboo(doc)
    karst = _build_karst(doc)
    _build_stalactite(doc)
    grave = _build_graveyard(doc)

    # Smooth physical seams between districts so entering never requires a
    # blind jump.  The cave seam has a five-cell-high opening.
    for x in range(92, 101):
        floor = int(round(49 + (x - 92) * (karst.get(100, 50) - 49) / 8.0))
        for y in range(floor - 5, floor):
            _air(doc, x, y)
        _solid(doc, x, floor, "limestone")
    _carve_stepped_route(doc, ((188, karst[188]), (195, 50), (201, 49)), 6)
    _carve_stepped_route(doc, ((281, 50), (289, grave[289]), (296, grave[296])), 6)

    traps = []
    for index, (x, floor, skin) in enumerate((
        (35, bamboo[35], "bamboo"), (82, bamboo[82], "bamboo"),
        (108, karst[108], "ink"), (181, karst[181], "ink"),
        (218, 43, "cave"), (250, 54, "cave"),
        (304, grave[304], "grave"), (331, grave[331], "grave"),
    ), 1):
        traps.append(_trap(
            "wuxia_spike_%02d" % index, "hidden_spike", x,
            floor=int(floor), trigger_width=1.05, damage=12.0 + index,
            interval=1.25, skin=skin,
        ))
    traps.extend((
        _trap("cave_dart_01", "wall_arrow", 226, y=39, floor=42,
              direction=[1.0, 0.0], interval=2.25, speed=370.0,
              damage=15.0, lifetime=3.2, delay=.35, skin="cave"),
        _trap("grave_soul_orb_01", "bouncing_fireball", 321, y=44,
              floor=49, direction=[1.0, -.25], interval=999.0,
              respawn=1.35, speed=230.0, damage=18.0, lifetime=5.4,
              delay=.8, skin="grave"),
    ))

    spawns = [
        {"species": "wuxia_jade_mantis", "x": 26, "floor": bamboo[26], "count": 3, "spacing": 8, "patrol_tiles": 3.0, "region": "bamboo_forest", "underground": False},
        {"species": "wuxia_bamboo_guard", "x": 51, "floor": bamboo[51], "count": 2, "spacing": 6, "patrol_tiles": 3.4, "region": "bamboo_forest", "underground": False},
        {"species": "wuxia_ink_crane", "x": 144, "floor": karst[144], "count": 3, "spacing": 8, "patrol_tiles": 4.0, "region": "karst_rivers", "underground": False},
        {"species": "wuxia_river_scale_beast", "x": 152, "floor": karst[152], "count": 2, "spacing": 16, "patrol_tiles": 3.0, "region": "karst_rivers", "underground": False},
        {"species": "wuxia_echo_sword_bat", "x": 240, "floor": 49, "count": 3, "spacing": 8, "patrol_tiles": 4.2, "region": "stalactite_cave", "underground": True},
        {"species": "wuxia_stone_salamander", "x": 239, "floor": 52, "count": 3, "spacing": 12, "patrol_tiles": 3.0, "region": "stalactite_cave", "underground": True},
        {"species": "wuxia_talisman_jiangshi", "x": 296, "floor": grave[296], "count": 3, "spacing": 8, "patrol_tiles": 3.0, "region": "graveyard", "underground": False},
        {"species": "wuxia_soul_lantern", "x": 323, "floor": grave[323], "count": 2, "spacing": 9, "patrol_tiles": 3.2, "region": "graveyard", "underground": False},
        {"species": "wuxia_sword_tomb_master", "x": 362, "floor": 48, "count": 1, "spacing": 1, "patrol_tiles": 8.0, "region": "graveyard", "underground": False, "unique_key": "wuxia.sword_tomb_master"},
    ]

    regions = [
        {"id": region, "bounds": [start, 0, end, WORLD_HEIGHT], "background_profile": profile}
        for region, start, end, profile in REGIONS
    ]
    doc.metadata.update({
        "name": "武俠次世界",
        "map_id": "wuxia_world",
        "map_type": "secondary_world",
        "theme": "wuxia_world",
        "generated": True,
        "generator": "v0.7.7.5-fix69-wuxia-original",
        "seed": int(seed),
        "width": WORLD_WIDTH,
        "height": WORLD_HEIGHT,
        "biome_version": 10,
        "biome_spans": [[0, 96, "rainforest"], [96, 192, "lake"], [192, 288, "plains"], [288, 384, "snow_mountain"]],
        "underground_only": False,
        "population_mode": "authored_only",
        "underground_population_mode": "authored_only",
        "prototype_entities": False,
        "portals": [],
        "dungeon_traps": traps,
        "trap_layer": "background_hazard",
        "trap_cells_indestructible": True,
        "creature_spawns": spawns,
        "spawn_abyss_boss": False,
        "secondary_world": {
            "world_id": "wuxia",
            "profile_id": "wuxia_world",
            "roster_id": "wuxia_world",
            "catalog": "assets/secondary_worlds/wuxia.json",
            "population_mode": "authored_only",
            "boss_unique": True,
            "regions": regions,
            "max_background_quads": 120,
            "max_attack_vfx_events": 24,
        },
        "encounter_zones": [
            {"id": "bamboo_canopy", "bounds": [8, 24, 92, 55], "role": "climb_and_ambush"},
            {"id": "karst_river", "bounds": [96, 28, 192, 58], "role": "bridges_and_aerial_pressure"},
            {"id": "stalactite_loops", "bounds": [192, 16, 288, 62], "role": "vertical_route_choice"},
            {"id": "graveyard_approach", "bounds": [288, 35, 340, 61], "role": "trap_gauntlet"},
            {"id": "sword_tomb_arena", "bounds": [340, 34, 382, 50], "role": "unique_boss"},
        ],
        "style_guide": {
            "original_setting": True,
            "districts": {
                "bamboo_forest": "high climbable bamboo, moon gate and canopy paths",
                "karst_rivers": "layered ink peaks, river bridges, raft and cliff shrine",
                "stalactite_cave": "rounded limestone chambers, echo route and luminous pool",
                "graveyard": "ash hills, paper talismans, broken swords and moonlit boss tomb",
            },
            "route_language": "one continuous ground route plus optional canopy, water, cave and tomb loops",
        },
    })
    doc.player_spawn = [10, bamboo[10]]
    _portal(doc, "to_main_world", "editor_map.json", "to_wuxia", 5, bamboo[5])
    # FIX69 closes the secondary-world graph into real loops.  Each gate owns
    # its own trigger and safe apron; none share an arrival cell.
    _portal(doc, "to_biolume_world", "biolume_sky_world.json", "to_wuxia_world", 184, karst[184])
    _portal(doc, "to_boss_dungeon", "boss_dungeon.json", "to_wuxia_graveyard", 318, grave[318])
    return doc


def save_wuxia_world(project_root, seed=20260903):
    root = os.path.abspath(project_root)
    maps = os.path.join(root, "maps")
    os.makedirs(maps, exist_ok=True)
    path = os.path.join(maps, "wuxia_world.json")
    doc = build_wuxia_world(seed)
    doc.save(path)
    return path, doc


# ---------------------------------------------------------------------------
# Original pixel silhouettes.  These helpers run only during packaging.

TRANSPARENT = "#00000000"


def _canvas(size):
    return [[TRANSPARENT for _ in range(size)] for _ in range(size)]


def _rect(grid, x0, y0, x1, y1, color):
    size = len(grid)
    for y in range(max(0, int(y0)), min(size, int(y1))):
        for x in range(max(0, int(x0)), min(size, int(x1))):
            grid[y][x] = color


def _line(grid, x0, y0, x1, y1, color, thickness=1):
    steps = max(abs(int(x1) - int(x0)), abs(int(y1) - int(y0)), 1)
    for i in range(steps + 1):
        t = i / float(steps)
        x = int(round(x0 + (x1 - x0) * t)); y = int(round(y0 + (y1 - y0) * t))
        _rect(grid, x - thickness // 2, y - thickness // 2, x + 1 + thickness // 2, y + 1 + thickness // 2, color)


def _copy(grid):
    return [list(row) for row in grid]


def _shift(grid, dx=0, dy=0):
    out = _canvas(len(grid)); size = len(grid)
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            nx = x + int(dx); ny = y + int(dy)
            if color != TRANSPARENT and 0 <= nx < size and 0 <= ny < size:
                out[ny][nx] = color
    return out


def _sprite(species, size):
    g = _canvas(size)
    ink = "#17202AFF"; white = "#EDE9D7FF"; jade = "#4EBB68FF"
    if species == "wuxia_jade_mantis":
        _rect(g, 13, 8, 19, 23, "#4AAE58FF"); _rect(g, 11, 12, 21, 20, jade)
        _rect(g, 13, 5, 19, 11, "#84D66DFF"); _rect(g, 14, 7, 15, 8, "#F2E36FFF")
        _line(g, 13, 11, 5, 20, "#B8E879FF", 2); _line(g, 19, 11, 27, 18, "#B8E879FF", 2)
        _line(g, 6, 20, 3, 14, white); _line(g, 26, 18, 29, 12, white)
        _line(g, 14, 5, 10, 1, ink); _line(g, 18, 5, 22, 1, ink)
        _line(g, 14, 22, 9, 29, ink, 2); _line(g, 18, 22, 23, 29, ink, 2)
    elif species == "wuxia_bamboo_guard":
        _rect(g, 8, 11, 24, 27, ink); _rect(g, 10, 8, 22, 19, white)
        _rect(g, 8, 6, 13, 11, ink); _rect(g, 20, 6, 25, 11, ink)
        _rect(g, 11, 11, 15, 15, ink); _rect(g, 18, 11, 22, 15, ink)
        _rect(g, 15, 16, 18, 19, ink); _rect(g, 11, 20, 22, 28, "#3F563DFF")
        _line(g, 7, 28, 27, 3, "#78C65AFF", 2); _rect(g, 6, 25, 12, 31, ink); _rect(g, 21, 25, 27, 31, ink)
    elif species == "wuxia_ink_crane":
        _rect(g, 12, 13, 23, 23, white); _rect(g, 7, 16, 17, 25, "#C9CED0FF")
        _line(g, 21, 15, 24, 7, white, 2); _rect(g, 23, 5, 27, 9, white); _rect(g, 24, 4, 27, 6, "#CF394BFF")
        _line(g, 27, 7, 31, 8, "#E1B84BFF"); _rect(g, 25, 6, 26, 7, ink)
        _line(g, 14, 23, 13, 31, "#C6974AFF"); _line(g, 19, 23, 20, 31, "#C6974AFF")
        _line(g, 8, 18, 2, 13, ink, 2); _line(g, 9, 20, 1, 22, ink, 2)
    elif species == "wuxia_river_scale_beast":
        _rect(g, 5, 14, 24, 24, "#337F91FF"); _rect(g, 10, 11, 27, 20, "#4EB3BAFF")
        _rect(g, 21, 8, 29, 17, "#65D0C7FF"); _rect(g, 26, 11, 28, 13, "#F5D35DFF")
        _line(g, 22, 9, 19, 4, "#E7E0A1FF"); _line(g, 27, 9, 30, 4, "#E7E0A1FF")
        _line(g, 6, 17, 1, 10, "#2E6578FF", 2); _line(g, 6, 21, 1, 27, "#2E6578FF", 2)
        for x in (10, 15, 20): _rect(g, x, 16, x + 2, 18, "#A8E7D8FF")
        _line(g, 10, 24, 8, 30, ink, 2); _line(g, 21, 24, 24, 30, ink, 2)
    elif species == "wuxia_echo_sword_bat":
        _rect(g, 13, 11, 20, 24, "#514070FF"); _rect(g, 14, 8, 19, 13, "#78609AFF")
        _line(g, 13, 12, 2, 6, "#352B50FF", 3); _line(g, 12, 15, 1, 20, "#725B91FF", 2)
        _line(g, 20, 12, 30, 5, "#352B50FF", 3); _line(g, 20, 15, 31, 20, "#725B91FF", 2)
        _line(g, 3, 7, 1, 22, "#C9D4E1FF"); _line(g, 29, 6, 31, 21, "#C9D4E1FF")
        _rect(g, 14, 10, 15, 11, "#F05A72FF"); _rect(g, 18, 10, 19, 11, "#F05A72FF")
    elif species == "wuxia_stone_salamander":
        _rect(g, 5, 15, 25, 24, "#A7683EFF"); _rect(g, 20, 12, 29, 22, "#C98B50FF")
        _line(g, 6, 18, 1, 12, "#795039FF", 2); _line(g, 6, 21, 1, 28, "#795039FF", 2)
        for x, y in ((10, 17), (15, 20), (20, 16), (24, 19)): _rect(g, x, y, x + 2, y + 2, "#E4BD65FF")
        _rect(g, 25, 15, 27, 17, ink); _line(g, 10, 23, 7, 29, ink, 2); _line(g, 21, 23, 24, 29, ink, 2)
    elif species == "wuxia_talisman_jiangshi":
        _rect(g, 10, 8, 23, 28, "#304D78FF"); _rect(g, 11, 5, 22, 12, "#8AA5A8FF")
        _rect(g, 8, 3, 25, 7, ink); _rect(g, 13, 11, 20, 23, "#D8C985FF")
        _rect(g, 15, 7, 19, 20, "#F1D65CFF"); _rect(g, 16, 9, 18, 11, "#C73739FF")
        _line(g, 10, 15, 3, 20, "#5A79A2FF", 3); _line(g, 23, 15, 30, 20, "#5A79A2FF", 3)
        _rect(g, 10, 27, 15, 32, ink); _rect(g, 19, 27, 24, 32, ink)
    elif species == "wuxia_soul_lantern":
        _rect(g, 12, 12, 21, 24, "#E6C14DFF"); _rect(g, 10, 14, 23, 20, "#8BE1C1FF")
        _rect(g, 13, 10, 20, 13, ink); _rect(g, 13, 23, 20, 26, ink)
        _line(g, 16, 10, 16, 4, "#D8E9C9FF"); _line(g, 11, 14, 6, 8, "#58B8A0AA", 2)
        _line(g, 22, 14, 27, 7, "#58B8A0AA", 2); _line(g, 11, 20, 5, 26, "#58B8A0AA", 2)
        _line(g, 22, 20, 28, 27, "#58B8A0AA", 2)
    else:  # sword-tomb master, 48px canvas
        _rect(g, 14, 15, 34, 42, "#31364CFF"); _rect(g, 17, 10, 31, 20, "#D8D1B2FF")
        _line(g, 7, 12, 40, 12, ink, 3); _line(g, 13, 15, 35, 6, "#69615CFF", 3)
        _rect(g, 20, 15, 23, 17, "#73E0D0FF"); _rect(g, 28, 15, 31, 17, "#73E0D0FF")
        _line(g, 14, 21, 5, 32, "#59627EFF", 4); _line(g, 34, 21, 43, 31, "#59627EFF", 4)
        _line(g, 34, 24, 45, 4, "#E8F6EFFF", 2); _line(g, 32, 26, 43, 8, "#70D7C7FF")
        _rect(g, 15, 41, 23, 48, ink); _rect(g, 27, 41, 35, 48, ink)
        for x, y in ((7, 20), (39, 19), (9, 38), (40, 37)):
            _rect(g, x, y, x + 2, y + 2, "#70D7C7AA")
    return g


def _attack_frame(base, color, long_arc=False):
    frame = _copy(base); size = len(frame)
    if long_arc:
        _line(frame, size * .10, size * .78, size * .88, size * .18, color, 2)
        _line(frame, size * .18, size * .86, size * .92, size * .32, "#FFFFFFFF", 1)
    else:
        _line(frame, size * .55, size * .22, size * .94, size * .48, color, 2)
        _line(frame, size * .94, size * .48, size * .62, size * .75, color, 2)
    return frame


def _asset_payload(species, size):
    base = _sprite(species, size)
    attack_color = "#8FFFE5FF" if species in ("wuxia_soul_lantern", "wuxia_sword_tomb_master") else "#FFEFA0FF"
    attack = _attack_frame(_shift(base, 1, 0), attack_color, species == "wuxia_sword_tomb_master")
    animations = {
        "idle": {"fps": 3.0, "loop": True, "frames": [base, _shift(base, 0, -1)]},
        "move": {"fps": 7.0, "loop": True, "frames": [_shift(base, -1, 0), _shift(base, 1, 0), base]},
        "attack": {"fps": 9.0, "loop": False, "frames": [base, attack, _shift(base, 2, 0)], "events": {"action": 1}},
    }
    if species == "wuxia_sword_tomb_master":
        wave = _attack_frame(_shift(base, 1, -1), "#70D7C7FF", True)
        slam = _copy(base)
        for x in range(5, size - 5, 4):
            _line(slam, x, size - 2, x + 2, size - 10, "#D5B866FF", 2)
        animations["sword_wave"] = {"fps": 8.0, "loop": False, "frames": [base, wave, _shift(base, -1, 0)], "events": {"action": 1}}
        animations["slam"] = {"fps": 8.0, "loop": False, "frames": [base, _shift(base, 0, 2), slam], "events": {"action": 2}}
    return {
        "format": "pyto_rpg_pixel_asset", "version": 6,
        "asset_id": "creature." + species, "category": "creature",
        "size": int(size), "pixel_mapping": "fixed_world_pixel",
        "pixel_world_scale": 2.5, "pixels": base, "animations": animations,
        "combat_bindings": {}, "auto_animation_profile": "boss" if size == 48 else "hostile",
    }


def _png_chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)


def _write_png(path, pixels):
    h = len(pixels); w = len(pixels[0]) if h else 0
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for value in row:
            text = str(value).lstrip("#")
            if len(text) == 6: text += "FF"
            try: raw.extend(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))
            except Exception: raw.extend((0, 0, 0, 0))
    data = b"\x89PNG\r\n\x1a\n"
    data += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    data += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += _png_chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle: handle.write(data)
    os.replace(tmp, path)


def author_wuxia_pixel_assets(project_root):
    """Write nine editable pixel sources/PNGs and merge their bindings."""
    root = os.path.abspath(project_root); assets = os.path.join(root, "assets")
    source_dir = os.path.join(assets, "pixel_sources")
    png_dir = os.path.join(assets, "creatures")
    os.makedirs(source_dir, exist_ok=True); os.makedirs(png_dir, exist_ok=True)
    species_rows = (
        ("wuxia_jade_mantis", 32), ("wuxia_bamboo_guard", 32),
        ("wuxia_ink_crane", 32), ("wuxia_river_scale_beast", 32),
        ("wuxia_echo_sword_bat", 32), ("wuxia_stone_salamander", 32),
        ("wuxia_talisman_jiangshi", 32), ("wuxia_soul_lantern", 32),
        ("wuxia_sword_tomb_master", 48),
    )
    registry_path = os.path.join(assets, "registry.json")
    with open(registry_path, "r", encoding="utf-8") as handle:
        registry = json.load(handle)
    bindings = registry.setdefault("bindings", {})
    for species, size in species_rows:
        payload = _asset_payload(species, size)
        source_rel = "pixel_sources/creature_%s.json" % species
        png_rel = "creatures/creature_%s.png" % species
        source_path = os.path.join(assets, *source_rel.split("/"))
        png_path = os.path.join(assets, *png_rel.split("/"))
        with open(source_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(source_path + ".tmp", source_path)
        _write_png(png_path, payload["pixels"])
        bindings["creature." + species] = {
            "category": "creature", "file": png_rel,
            "source_name": "fix69-original-wuxia-pixel-art",
            "frame_width": 0, "frame_height": 0, "frame_index": 0,
            "pixel_source": source_rel, "pixel_size": size,
            "pixel_mapping": "fixed_world_pixel", "pixel_world_scale": 2.5,
            "anchor": "feet", "preserve_aspect": True,
            "flip_with_facing": True, "has_animations": True,
            "has_combat_bindings": False,
            "animation_states": sorted(payload["animations"]),
        }
    with open(registry_path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(registry_path + ".tmp", registry_path)

    catalog_path = os.path.join(assets, "binding_catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    creature_ids = catalog.setdefault("categories", {}).setdefault("creature", [])
    for species, _size in species_rows:
        asset_id = "creature." + species
        if asset_id not in creature_ids: creature_ids.append(asset_id)
    with open(catalog_path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
    os.replace(catalog_path + ".tmp", catalog_path)
    return tuple("creature." + species for species, _size in species_rows)


__all__ = [
    "REGIONS", "WORLD_HEIGHT", "WORLD_WIDTH", "author_wuxia_pixel_assets",
    "build_wuxia_world", "save_wuxia_world",
]
