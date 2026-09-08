# -*- coding: utf-8 -*-
from dataclasses import dataclass


@dataclass(frozen=True)
class TileDef:
    tile_id: int
    name: str

    solid: bool = False
    ladder: bool = False
    wall_climbable: bool = False

    # FIX68 traversal geometry. A one-way platform is still a solid support
    # surface, but actors may pass through its sides and underside. This is
    # used by the segmented rainforest branches; ordinary terrain keeps the
    # historical full-block behaviour.
    one_way_platform: bool = False

    # Optional semantic hints for renderer/audio integrations. Empty keeps the
    # historic name-based fallback; FIX68 tree pieces expose these so minimap
    # and footstep code do not need five new hard-coded ID branches.
    material_group: str = ""
    minimap_group: str = ""

    flammable: bool = False
    fuel: float = 0.0
    water_absorption: float = 0.0
    fertility: float = 0.0

    # Matter accounting.
    material_mass: float = 0.0
    burn_ash_fraction: float = 0.0

    # Thermal material parameters. These are gameplay-relative coefficients.
    thermal_conductivity: float = 0.22
    heat_capacity: float = 1.0
    solar_absorption: float = 0.55

    # Water-equivalent mass held by a solid phase, e.g. ICE.
    water_mass: float = 0.0

    # V0.7.0 systemic material properties. These are gameplay coefficients,
    # not SI-accurate constants. They let electricity/magnetism query material
    # behavior without hard-coded tile-ID branches.
    electrical_conductivity: float = 0.0
    magnetic_susceptibility: float = 0.0
    metal: bool = False
    corrosion_resistance: float = 0.5

    color: str = "#000000"
    top_color: str = "#000000"


AIR = 0
DIRT = 1
WOOD = 2
STONE = 3
LADDER = 4
GRASS_DIRT = 5
ICE = 6
ASH = 7
COPPER_ORE = 8
IRON_ORE = 9
GOLD_ORE = 10
MARBLE = 11
LIMESTONE = 12
MUD = 13
SWAMP_SOIL = 14
SAND = 15
JUNGLE_SOIL = 16
SNOW_DIRT = 17
VILLAGE_PATH = 18
SEA_SAND = 19
IGNEOUS_ROCK = 20

# FIX68 segmented rainforest canopy system. These are ordinary stable terrain
# IDs so map binary palettes and save deltas can persist every chopped segment.
# The tree graph itself is sparse metadata owned by TileWorld.
RAINFOREST_TRUNK = 21
RAINFOREST_BRANCH = 22
RAINFOREST_VINE = 23
RAINFOREST_CANOPY = 24
RAINFOREST_ROOT = 25
HONEYCOMB = 26

RAINFOREST_TREE_TILES = (
    RAINFOREST_TRUNK,
    RAINFOREST_BRANCH,
    RAINFOREST_VINE,
    RAINFOREST_CANOPY,
    RAINFOREST_ROOT,
)

# V0.7.6.0 terrain whose collision/render geometry follows the shared
# three-segment ground mask. Keep this tuple in the registry so renderer,
# physics, hydrology and tools all agree on what a partial tile means.
LAYERED_GROUND_TILES = (DIRT, GRASS_DIRT, MUD, SWAMP_SOIL, SAND, JUNGLE_SOIL, SNOW_DIRT, SEA_SAND)

# V0.7.6.5 shared 1/3-height geometry for every ordinary solid terrain block.
# ICE stays mass-driven through ice_layers.py, while porous hydrology continues
# to use LAYERED_GROUND_TILES so stone/ore/wood never become soil merely because
# they can now be layer-mined/rendered at LOW/MID/HIGH heights.
LAYERED_SOLID_TILES = (
    DIRT, WOOD, STONE, GRASS_DIRT, ASH,
    COPPER_ORE, IRON_ORE, GOLD_ORE, MARBLE, LIMESTONE,
    MUD, SWAMP_SOIL, SAND, JUNGLE_SOIL, SNOW_DIRT,
    VILLAGE_PATH, SEA_SAND, IGNEOUS_ROCK,
)

# FIX36 hydrology contract: every natural stone/ore/igneous material is
# absolutely impermeable.  Layer masks affect visible/collision height only;
# they never turn rock into porous soil or an abstract groundwater shortcut.
IMPERMEABLE_ROCK_TILES = (
    STONE, COPPER_ORE, IRON_ORE, GOLD_ORE, MARBLE, LIMESTONE, IGNEOUS_ROCK,
)

def is_hydrology_impermeable(tile_id):
    return int(tile_id) in IMPERMEABLE_ROCK_TILES


TILES = {
    AIR: TileDef(
        AIR,
        "air",
        thermal_conductivity=0.30,
        heat_capacity=0.75,
        solar_absorption=0.12,
        color="#000000",
        top_color="#000000",
    ),

    DIRT: TileDef(
        DIRT,
        "dirt",
        solid=True,
        flammable=True,
        fuel=0.32,
        water_absorption=0.85,
        fertility=0.70,
        material_mass=1.00,
        burn_ash_fraction=0.82,
        thermal_conductivity=0.36,
        heat_capacity=1.75,
        solar_absorption=0.72,
        color="#6C5238",
        top_color="#6C5238",
    ),

    GRASS_DIRT: TileDef(
        GRASS_DIRT,
        "grass_dirt",
        solid=True,
        flammable=True,
        fuel=0.42,
        water_absorption=0.90,
        fertility=0.95,
        material_mass=1.02,
        burn_ash_fraction=0.78,
        thermal_conductivity=0.34,
        heat_capacity=1.85,
        solar_absorption=0.62,
        color="#6C5238",
        top_color="#5C8D45",
    ),

    WOOD: TileDef(
        WOOD,
        "wood",
        solid=True,
        flammable=True,
        fuel=1.0,
        water_absorption=0.15,
        material_mass=0.65,
        burn_ash_fraction=0.16,
        thermal_conductivity=0.17,
        heat_capacity=1.30,
        solar_absorption=0.64,
        color="#87603A",
        top_color="#B08A58",
    ),

    STONE: TileDef(
        STONE,
        "stone",
        solid=True,
        wall_climbable=True,
        material_mass=1.45,
        thermal_conductivity=0.88,
        heat_capacity=2.50,
        solar_absorption=0.68,
        color="#676B74",
        top_color="#9297A0",
    ),

    LADDER: TileDef(
        LADDER,
        "ladder",
        solid=False,
        ladder=True,
        flammable=True,
        fuel=0.55,
        water_absorption=0.10,
        material_mass=0.0,
        burn_ash_fraction=0.12,
        thermal_conductivity=0.15,
        heat_capacity=1.10,
        solar_absorption=0.60,
        color="#A9773F",
        top_color="#C89A62",
    ),

    ICE: TileDef(
        ICE,
        "ice",
        solid=True,
        wall_climbable=False,
        flammable=False,
        material_mass=0.0,
        thermal_conductivity=1.15,
        heat_capacity=3.90,
        solar_absorption=0.32,
        water_mass=1.0,
        color="#7EC8ED",
        top_color="#BDEBFF",
    ),

    ASH: TileDef(
        ASH,
        "ash",
        solid=True,
        flammable=False,
        water_absorption=0.20,
        fertility=0.0,
        material_mass=0.0,
        burn_ash_fraction=1.0,
        thermal_conductivity=0.24,
        heat_capacity=1.20,
        solar_absorption=0.48,
        color="#595959",
        top_color="#7A7A7A",
    ),


    COPPER_ORE: TileDef(
        COPPER_ORE,
        "copper_ore",
        solid=True,
        wall_climbable=True,
        material_mass=1.65,
        thermal_conductivity=1.15,
        heat_capacity=2.25,
        solar_absorption=0.72,
        electrical_conductivity=0.82,
        magnetic_susceptibility=0.08,
        metal=True,
        corrosion_resistance=0.58,
        color="#70584B",
        top_color="#B76E45",
    ),

    IRON_ORE: TileDef(
        IRON_ORE,
        "iron_ore",
        solid=True,
        wall_climbable=True,
        material_mass=1.80,
        thermal_conductivity=1.30,
        heat_capacity=2.35,
        solar_absorption=0.70,
        electrical_conductivity=0.58,
        magnetic_susceptibility=1.00,
        metal=True,
        corrosion_resistance=0.44,
        color="#555A60",
        top_color="#9A8174",
    ),

    GOLD_ORE: TileDef(
        GOLD_ORE,
        "gold_ore",
        solid=True,
        wall_climbable=True,
        material_mass=1.95,
        thermal_conductivity=1.55,
        heat_capacity=2.10,
        solar_absorption=0.78,
        electrical_conductivity=0.96,
        magnetic_susceptibility=0.02,
        metal=True,
        corrosion_resistance=0.92,
        color="#675B38",
        top_color="#D5A92D",
    ),

    MARBLE: TileDef(
        MARBLE,
        "marble",
        solid=True,
        wall_climbable=True,
        material_mass=1.55,
        thermal_conductivity=0.92,
        heat_capacity=2.35,
        solar_absorption=0.48,
        color="#D9D7D3",
        top_color="#F2F0EC",
    ),

    LIMESTONE: TileDef(
        LIMESTONE,
        "limestone",
        solid=True,
        wall_climbable=True,
        material_mass=1.42,
        thermal_conductivity=0.78,
        heat_capacity=2.20,
        solar_absorption=0.58,
        color="#A39A7B",
        top_color="#C8BE99",
    ),

    MUD: TileDef(
        MUD,
        "mud",
        solid=True,
        flammable=False,
        water_absorption=0.96,
        fertility=0.58,
        material_mass=1.08,
        burn_ash_fraction=0.0,
        thermal_conductivity=0.44,
        heat_capacity=2.15,
        solar_absorption=0.78,
        color="#49372D",
        top_color="#5A4637",
    ),


    SWAMP_SOIL: TileDef(
        SWAMP_SOIL,
        "swamp_soil",
        solid=True,
        flammable=True,
        fuel=0.20,
        water_absorption=0.98,
        fertility=0.28,
        material_mass=1.06,
        burn_ash_fraction=0.76,
        thermal_conductivity=0.50,
        heat_capacity=2.35,
        solar_absorption=0.80,
        color="#514838",
        top_color="#53663A",
    ),

    SAND: TileDef(
        SAND,
        "sand",
        solid=True,
        flammable=False,
        # Sand is infertile but highly porous.  Older builds accidentally
        # excluded it from hydrology because fertility was used as a proxy for
        # porosity, leaving permanent desert puddles.
        water_absorption=0.98,
        fertility=0.0,
        material_mass=0.92,
        thermal_conductivity=0.31,
        heat_capacity=1.30,
        solar_absorption=0.86,
        color="#C9A85D",
        top_color="#E2C675",
    ),

    JUNGLE_SOIL: TileDef(
        JUNGLE_SOIL,
        "jungle_soil",
        solid=True,
        flammable=True,
        fuel=0.34,
        water_absorption=0.96,
        fertility=1.0,
        material_mass=1.04,
        burn_ash_fraction=0.80,
        thermal_conductivity=0.38,
        heat_capacity=2.05,
        solar_absorption=0.66,
        color="#59452F",
        top_color="#2F7A38",
    ),

    SNOW_DIRT: TileDef(
        SNOW_DIRT,
        "snow_dirt",
        solid=True,
        flammable=False,
        water_absorption=0.68,
        fertility=0.0,
        material_mass=1.02,
        thermal_conductivity=0.58,
        heat_capacity=2.35,
        solar_absorption=0.24,
        color="#665A4A",
        top_color="#E7EEF2",
    ),

    VILLAGE_PATH: TileDef(
        VILLAGE_PATH,
        "village_path",
        solid=True,
        flammable=False,
        water_absorption=0.45,
        fertility=0.0,
        material_mass=1.05,
        thermal_conductivity=0.46,
        heat_capacity=1.70,
        solar_absorption=0.60,
        color="#80694D",
        top_color="#A58B64",
    ),

    SEA_SAND: TileDef(
        SEA_SAND,
        "sea_sand",
        solid=True,
        flammable=False,
        # Coastal/marine sand is porous. Under the authored sea it starts
        # saturated, so seawater fills its pore space but does not drain the
        # entire ocean into an artificial groundwater sink.
        water_absorption=0.98,
        fertility=0.0,
        material_mass=0.96,
        thermal_conductivity=0.42,
        heat_capacity=1.55,
        solar_absorption=0.70,
        color="#B9A16D",
        top_color="#D8C28A",
    ),


    IGNEOUS_ROCK: TileDef(
        IGNEOUS_ROCK,
        "igneous_rock",
        solid=True,
        wall_climbable=True,
        flammable=False,
        material_mass=1.62,
        thermal_conductivity=0.95,
        heat_capacity=2.65,
        solar_absorption=0.76,
        color="#302D2E",
        top_color="#50494A",
    ),

    RAINFOREST_TRUNK: TileDef(
        RAINFOREST_TRUNK,
        "rainforest_trunk",
        solid=True,
        wall_climbable=True,
        material_group="wood",
        minimap_group="structure",
        flammable=True,
        fuel=1.55,
        water_absorption=0.08,
        material_mass=0.82,
        burn_ash_fraction=0.14,
        thermal_conductivity=0.15,
        heat_capacity=1.45,
        solar_absorption=0.78,
        color="#54351F",
        top_color="#7A5330",
    ),

    RAINFOREST_BRANCH: TileDef(
        RAINFOREST_BRANCH,
        "rainforest_branch",
        solid=True,
        one_way_platform=True,
        material_group="wood",
        minimap_group="structure",
        flammable=True,
        fuel=1.10,
        water_absorption=0.08,
        material_mass=0.46,
        burn_ash_fraction=0.14,
        thermal_conductivity=0.15,
        heat_capacity=1.30,
        solar_absorption=0.70,
        color="#654225",
        top_color="#967044",
    ),

    RAINFOREST_VINE: TileDef(
        RAINFOREST_VINE,
        "rainforest_vine",
        solid=False,
        ladder=True,
        material_group="wood",
        minimap_group="ladder",
        flammable=True,
        fuel=0.62,
        water_absorption=0.22,
        material_mass=0.12,
        burn_ash_fraction=0.10,
        thermal_conductivity=0.13,
        heat_capacity=1.05,
        solar_absorption=0.52,
        color="#2E7136",
        top_color="#58A34D",
    ),

    RAINFOREST_CANOPY: TileDef(
        RAINFOREST_CANOPY,
        "rainforest_canopy",
        solid=False,
        material_group="leaf",
        minimap_group="air",
        flammable=True,
        fuel=0.78,
        water_absorption=0.26,
        material_mass=0.10,
        burn_ash_fraction=0.08,
        thermal_conductivity=0.12,
        heat_capacity=1.10,
        solar_absorption=0.50,
        color="#1F642F",
        top_color="#3E9746",
    ),

    RAINFOREST_ROOT: TileDef(
        RAINFOREST_ROOT,
        "rainforest_root",
        solid=True,
        wall_climbable=True,
        material_group="wood",
        minimap_group="structure",
        flammable=True,
        fuel=1.75,
        water_absorption=0.10,
        material_mass=1.05,
        burn_ash_fraction=0.16,
        thermal_conductivity=0.16,
        heat_capacity=1.55,
        solar_absorption=0.82,
        color="#49301E",
        top_color="#76502F",
    ),

    HONEYCOMB: TileDef(
        HONEYCOMB,
        "honeycomb",
        solid=True,
        wall_climbable=True,
        material_group="hive",
        minimap_group="structure",
        flammable=False,
        water_absorption=0.0,
        material_mass=0.72,
        thermal_conductivity=0.16,
        heat_capacity=1.55,
        solar_absorption=0.52,
        color="#C88724",
        top_color="#F0BC3E",
    ),

}


def tile_def(tile_id):
    return TILES.get(tile_id, TILES[AIR])
