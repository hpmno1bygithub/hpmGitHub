# -*- coding: utf-8 -*-
"""FIX69 original bioluminescent sky-world authoring.

This module deliberately contains only map/content authoring.  Portal travel,
combat damage and Metal presentation remain owned by their existing runtime
systems.  The visual direction uses floating land, giant luminous flora and a
living-network atmosphere, but all names, silhouettes and data are original to
PytoRPG.

The generated map is deterministic and uses ordinary runtime terrain for every
collision surface.  Decorative cells never pretend to be collision geometry;
the climb routes use the already-authoritative rainforest vine/trunk/branch
tiles and the hazards use the bounded dungeon-trap metadata contract.
"""
import math
import os

from map_editor.model import MapDocument


MAP_FILENAME = "biolume_sky_world.json"
WORLD_ID = "biolume.sky_world"
PROFILE_ID = "biolume.sky_depth"
ROSTER_ID = "biolume.native_roster"
CATALOG_PATH = "assets/secondary_worlds/biolume.json"


def _set_terrain(doc, tx, ty, material):
    if not doc.in_bounds(tx, ty):
        return
    doc.set("terrain", tx, ty, str(material))
    doc.erase_layer("water", tx, ty)
    doc.erase_layer("lava", tx, ty)


def _clear_cell(doc, tx, ty):
    if not doc.in_bounds(tx, ty):
        return
    doc.erase_layer("terrain", tx, ty)
    doc.erase_layer("ground_layer", tx, ty)
    doc.erase_layer("water", tx, ty)
    doc.erase_layer("lava", tx, ty)


def _floating_island(doc, cx, top, radius, depth, seed_offset=0):
    """Create a tapered physical island and return its surface rows by X."""
    surface = {}
    cx, top, radius, depth = int(cx), int(top), int(radius), int(depth)
    for tx in range(max(1, cx - radius), min(doc.width - 1, cx + radius + 1)):
        ratio = abs(float(tx - cx)) / max(1.0, float(radius))
        local_top = int(top + round(2.0 * ratio ** 1.7))
        local_depth = max(2, int(round(depth * math.sqrt(max(0.0, 1.0 - ratio ** 1.7)))))
        bottom = min(doc.height - 3, local_top + local_depth)
        surface[tx] = local_top
        for ty in range(local_top, bottom + 1):
            if ty == local_top:
                material = "jungle_soil"
            else:
                signature = (tx * 17 + ty * 31 + int(seed_offset) * 13) % 41
                material = "copper_ore" if signature == 3 else ("limestone" if signature < 19 else "stone")
            _set_terrain(doc, tx, ty, material)
            if ty == local_top:
                doc.set("ground_layer", tx, ty, 7)
    return surface


def _branch_stair(doc, points):
    """Make a one-way, jump-safe physical bridge through explicit cells."""
    used = []
    for tx, ty in points:
        tx, ty = int(tx), int(ty)
        if not doc.in_bounds(tx, ty):
            continue
        # A bridge may meet the tapered edge of an already-authored island.
        # Remove same-column overhang above the requested walking surface so a
        # visible branch cannot accidentally be buried under collision rock.
        for py in range(0, ty + 1):
            _clear_cell(doc, tx, py)
        doc.set("terrain", tx, ty, "rainforest_branch")
        used.append((tx, ty))
    return used


def _vine_route(doc, tx, top, bottom, landing_width=3):
    tx, top, bottom = int(tx), int(top), int(bottom)
    if top > bottom:
        top, bottom = bottom, top
    for ty in range(max(1, top + 1), min(doc.height - 1, bottom)):
        _clear_cell(doc, tx, ty)
        doc.set("terrain", tx, ty, "rainforest_vine")
    for px in range(tx - int(landing_width) // 2, tx + int(landing_width) // 2 + 1):
        _clear_cell(doc, px, top)
        doc.set("terrain", px, top, "rainforest_branch")
        _clear_cell(doc, px, bottom)
        doc.set("terrain", px, bottom, "rainforest_branch")


def _portal_apron(doc, x0, x1, floor):
    """Flatten one real platform and reserve three full doorway air rows."""
    for tx in range(int(x0), int(x1) + 1):
        for ty in range(max(0, int(floor) - 5), int(floor)):
            _clear_cell(doc, tx, ty)
        _set_terrain(doc, tx, int(floor), "jungle_soil")
        doc.set("ground_layer", tx, int(floor), 7)
        for ty in range(int(floor) + 1, min(doc.height - 2, int(floor) + 4)):
            _set_terrain(doc, tx, ty, "limestone")


def _decorate(doc, kind, cells, size=2):
    for tx, ty in cells:
        if doc.in_bounds(tx, ty):
            doc.set("decoration", int(tx), int(ty), (str(kind), int(size)))


def _portal_metadata():
    # Portal trigger occupies the three air cells immediately above the floor.
    # Arrival is two cells to the right, outside the trigger, so transition lock
    # can end without recursively re-entering the main world.
    return [
        {
            "id": "to_main_world",
            "rect": [4, 42, 3, 3],
            "arrival": [9, 45],
            "target_map": "editor_map.json",
            "target_portal": "to_biolume",
            "visual": "biolume_root_gate",
            "requires_floor_entry": True,
            "entry_floor": 45,
            "entry_y_tolerance_px": 8,
        },
        {
            "id": "to_wuxia_world",
            "rect": [216, 41, 3, 3],
            "arrival": [213, 44],
            "target_map": "wuxia_world.json",
            "target_portal": "to_biolume_world",
            "visual": "biolume_root_gate",
            "requires_floor_entry": True,
            "entry_floor": 44,
            "entry_y_tolerance_px": 8,
        },
    ]


def build_biolume_world(width=224, height=72):
    """Return the complete original bioluminescent secondary-world document."""
    width = max(192, int(width))
    height = max(64, int(height))
    doc = MapDocument(width, height)
    for layer in doc.layers.values():
        layer.clear()

    # Five main islands form a readable critical path; three high satellites
    # reward use of climbable living vines and give airborne enemies real room.
    island_a = _floating_island(doc, 27, 43, 26, 11, 1)
    island_b = _floating_island(doc, 75, 37, 20, 10, 2)
    island_c = _floating_island(doc, 126, 44, 23, 13, 3)
    island_d = _floating_island(doc, 174, 34, 20, 11, 4)
    island_e = _floating_island(doc, 207, 43, 16, 14, 5)
    _floating_island(doc, 66, 20, 11, 7, 6)
    _floating_island(doc, 117, 23, 10, 7, 7)
    _floating_island(doc, 170, 17, 12, 8, 8)

    # Main-route gaps stay visibly open beneath the bridges. Every rise changes
    # by at most one tile, except the deliberate vine climb into the high grove.
    _branch_stair(doc, ((51, 45), (52, 44), (53, 43), (54, 42), (55, 41), (56, 40), (57, 39)))
    _branch_stair(doc, ((95, 39), (96, 40), (97, 41), (98, 42), (99, 43), (100, 44), (101, 45), (102, 46)))
    _branch_stair(doc, ((190, 36), (191, 37), (192, 38), (193, 39), (194, 40), (195, 41), (196, 42), (197, 43)))
    _vine_route(doc, 152, 34, 46, landing_width=5)
    _vine_route(doc, 66, 20, 37, landing_width=3)
    _vine_route(doc, 117, 23, 44, landing_width=3)
    _vine_route(doc, 170, 17, 34, landing_width=3)

    # Both destination arrivals and trigger doorways use explicitly flattened
    # terrain. This is independent of the tapered island silhouette and avoids
    # spawning one row inside an edge slope after cross-world travel.
    _portal_apron(doc, 2, 12, 45)
    _portal_apron(doc, 211, 221, 44)

    # A dark physical catch basin prevents endless falling. It is intentionally
    # far below the islands and marked as a dangerous resonance bog.
    for tx in range(width):
        _set_terrain(doc, tx, height - 2, "stone")
        if 8 <= tx < width - 8 and tx % 3 != 0:
            doc.set("hazard", tx, height - 2, "swamp")

    # Small luminous pools are genuine water volumes held by impermeable stone.
    for x0, x1, floor in ((19, 24, 43), (118, 125, 44), (165, 170, 34)):
        for tx in range(x0, x1 + 1):
            doc.set("water", tx, floor - 1, 0.72)

    # Existing renderable decorations are deliberately reused; their collision
    # remains false and therefore cannot create invisible walls.
    _decorate(doc, "giant_mushroom", ((12, 43), (32, 43), (61, 37), (83, 37), (109, 44), (139, 44), (160, 34), (185, 34), (204, 43)), 3)
    _decorate(doc, "hanging_glow_vine", ((22, 43), (48, 45), (69, 37), (93, 39), (121, 44), (146, 46), (173, 34), (211, 43)), 3)
    _decorate(doc, "spore_pod", ((16, 43), (38, 43), (73, 37), (88, 37), (111, 44), (135, 44), (164, 34), (181, 34), (216, 43)), 2)
    _decorate(doc, "glow_pool", ((21, 42), (121, 43), (168, 33)), 3)
    _decorate(doc, "crystal_arch", ((43, 43), (102, 45), (156, 34), (199, 43)), 3)
    _decorate(doc, "root_arch", ((6, 45), (57, 39), (104, 44), (154, 34), (198, 43)), 3)
    _decorate(doc, "ceiling_crystal", ((62, 20), (70, 20), (113, 23), (121, 23), (165, 17), (175, 17)), 2)

    # Portal is visible but physical safety comes from the island floor.
    doc.set("decoration", 5, 45, ("crystal_arch", 3))
    doc.set("decoration", 217, 44, ("root_arch", 3))

    # Bounded authoritative hazards.  Presentation skin is a label only; the
    # existing trap system owns collision/damage and caps live projectiles.
    traps = [
        {"id": "biolume_spore_01", "type": "hidden_spike", "x": 42, "floor": int(island_a.get(42, 43)), "trigger_width": 1.15, "damage": 12.0, "interval": 1.35, "skin": "biolume"},
        {"id": "biolume_spore_02", "type": "hidden_spike", "x": 88, "floor": int(island_b.get(88, 37)), "trigger_width": 1.15, "damage": 14.0, "interval": 1.25, "skin": "biolume"},
        {"id": "biolume_pulse_01", "type": "wall_arrow", "x": 112, "y": 39, "direction": [1.0, -0.08], "interval": 2.6, "speed": 330.0, "damage": 13.0, "lifetime": 3.1, "delay": 0.4, "skin": "biolume"},
        {"id": "biolume_pulse_02", "type": "wall_arrow", "x": 177, "y": 29, "direction": [-1.0, 0.06], "interval": 2.35, "speed": 360.0, "damage": 15.0, "lifetime": 3.0, "delay": 0.9, "skin": "biolume"},
        {"id": "biolume_orb_01", "type": "bouncing_fireball", "x": 135, "y": 39, "direction": [1.0, -0.34], "speed": 225.0, "damage": 16.0, "lifetime": 5.4, "respawn": 1.3, "interval": 999.0, "delay": 0.65, "skin": "biolume"},
    ]

    creature_spawns = [
        {"species": "biolume_glider", "x": 31, "floor": 43, "count": 2, "spacing": 8, "patrol_tiles": 4.2, "underground": False},
        {"species": "biolume_stalker", "x": 62, "floor": 37, "count": 2, "spacing": 13, "patrol_tiles": 3.4, "underground": False},
        {"species": "biolume_sporeback", "x": 108, "floor": 44, "count": 2, "spacing": 16, "patrol_tiles": 2.7, "underground": False},
        {"species": "biolume_lantern_manta", "x": 137, "floor": 44, "count": 2, "spacing": 12, "patrol_tiles": 4.0, "underground": False},
        {"species": "biolume_root_sentinel", "x": 164, "floor": 34, "count": 2, "spacing": 13, "patrol_tiles": 2.8, "underground": False},
        {"species": "biolume_heartwarden", "x": 208, "floor": 43, "count": 1, "spacing": 1, "patrol_tiles": 4.5, "underground": False},
    ]

    doc.player_spawn = [9, 45]
    doc.metadata.update({
        "name": "生物發光異星界",
        "map_id": "biolume_sky_world",
        "map_type": "secondary_world",
        "theme": "biolume_sky",
        "background_theme": "biolume_sky",
        "background_profile": PROFILE_ID,
        "background_catalog": CATALOG_PATH,
        "background_layer_order": ["far", "mid", "near"],
        "background_draw_phase": "background",
        "background_collision": False,
        "background_quad_budget": 124,
        "generated": True,
        "generator": "fix69.biolume.original.v1",
        "seed": 6902026,
        "width": width,
        "height": height,
        # Suppress main-world biome rotation; only this roster is authored.
        "underground_only": True,
        "underground_population_mode": "authored_only",
        "prototype_entities": False,
        "spawn_abyss_boss": False,
        "biome_spans": [[0, width, "rainforest"]],
        "secondary_world": {
            "world_id": WORLD_ID,
            "profile_id": PROFILE_ID,
            "roster_id": ROSTER_ID,
            "boss_unique": True,
            "catalog": CATALOG_PATH,
        },
        "portals": _portal_metadata(),
        "creature_spawns": creature_spawns,
        "creature_respawn": {
            "hostile_seconds": 55.0,
            "passive_seconds": 80.0,
            "boss_mode": "never",
            "min_player_distance_tiles": 12.0,
            "retry_seconds": 8.0,
            "max_per_tick": 2,
            "species": {"biolume_heartwarden": {"enabled": False}},
        },
        "dungeon_traps": traps,
        "trap_layer": "background_hazard",
        "trap_cells_indestructible": True,
        "regions": [
            {"id": "biolume.resonance_grove", "name": "共鳴林庭", "bounds": [0, 0, 55, height], "style": "根網入口與低空發光孢子"},
            {"id": "biolume.floating_archipelago", "name": "浮岩群島", "bounds": [55, 0, 103, height], "style": "錯層浮島與藤蔓垂直路線"},
            {"id": "biolume.memory_basin", "name": "記憶光澤盆地", "bounds": [103, 0, 154, height], "style": "脈動水池與巨型菌傘"},
            {"id": "biolume.crown_canopy", "name": "冠層天庭", "bounds": [154, 0, 191, height], "style": "高空樹冠與水晶根橋"},
            {"id": "biolume.heart_sanctuary", "name": "心根聖域", "bounds": [191, 0, width, height], "style": "Boss 共感核心競技場"},
        ],
        "encounter_zones": [
            {"id": "arrival", "bounds": [2, 35, 48, 48], "role": "safe_portal_and_first_read"},
            {"id": "vertical_glider_hunt", "bounds": [52, 14, 99, 47], "role": "vertical_air_ambush"},
            {"id": "spore_crossfire", "bounds": [102, 20, 153, 51], "role": "ranged_crossfire_and_pool"},
            {"id": "crown_climb", "bounds": [151, 12, 193, 47], "role": "climb_route_elite_guard"},
            {"id": "heartwarden_arena", "bounds": [193, 29, 223, 59], "role": "unique_boss_arena"},
        ],
        "style_guide": {
            "originality": "original_bioluminescent_alien_ecology_no_film_assets_or_names",
            "palette": ["#08152F", "#173A57", "#36D6C4", "#9B72FF", "#FFE98A"],
            "architecture": "floating limestone roots, living bridges and luminous crown flora",
            "hazard": "reactive spore thorns and resonance pulses",
            "route_language": "readable ground spine plus optional climbable satellite loops",
        },
        "performance_contract": {
            "background_max_quads": 124,
            "vfx_max_events": 24,
            "vfx_max_quads_per_event": 18,
            "vfx_max_total_quads": 192,
            "trap_projectile_cap": 18,
            "authoritative_damage_separate_from_vfx": True,
        },
    })
    return doc


def save_biolume_world(project_root):
    root = os.path.abspath(project_root)
    maps_root = os.path.join(root, "maps")
    os.makedirs(maps_root, exist_ok=True)
    path = os.path.join(maps_root, MAP_FILENAME)
    doc = build_biolume_world()
    doc.save(path)
    return path, doc


__all__ = (
    "CATALOG_PATH", "MAP_FILENAME", "PROFILE_ID", "ROSTER_ID", "WORLD_ID",
    "build_biolume_world", "save_biolume_world",
)
