# -*- coding: utf-8 -*-
"""Shared biome definitions used by generator, runtime spawning and tools."""

BIOME_OCEAN = "ocean"
BIOME_SWAMP = "swamp"
BIOME_LAKE = "lake"
BIOME_PLAINS = "plains"
BIOME_RAINFOREST = "rainforest"
BIOME_SNOW_MOUNTAIN = "snow_mountain"
BIOME_DESERT = "desert"
BIOME_VILLAGE = "village"

# V0.7.5.6 default world no longer centers on a village strip. The authored
# progression follows the requested hydrology chain from humid west/left to dry
# east/right, while keeping a dedicated rainforest belt for the climate loop.
BIOME_ORDER = (
    BIOME_OCEAN,
    BIOME_SWAMP,
    BIOME_LAKE,
    BIOME_PLAINS,
    BIOME_VILLAGE,
    BIOME_RAINFOREST,
    BIOME_SNOW_MOUNTAIN,
    BIOME_DESERT,
)

BIOME_LABELS = {
    BIOME_OCEAN: "海洋",
    BIOME_SWAMP: "沼澤",
    BIOME_LAKE: "湖泊",
    BIOME_PLAINS: "平原",
    BIOME_RAINFOREST: "雨林",
    BIOME_SNOW_MOUNTAIN: "雪山高地",
    BIOME_DESERT: "沙漠",
    BIOME_VILLAGE: "人類村莊",
}

BIOME_CLIMATE_PROFILE = {
    BIOME_OCEAN: "temperate",
    BIOME_SWAMP: "rainforest",
    BIOME_LAKE: "temperate",
    BIOME_PLAINS: "temperate",
    BIOME_RAINFOREST: "rainforest",
    BIOME_SNOW_MOUNTAIN: "alpine",
    BIOME_DESERT: "desert",
    BIOME_VILLAGE: "temperate",
}


def default_biome_spans(width):
    """Return contiguous biome spans [start_x, end_x, id].

    FIX32 restores a real village enclave so village_rat / bandit / village_dog
    / villager are part of ordinary world population instead of being defined
    assets that never receive a map habitat.
    """
    width = max(8, int(width))
    cuts = [
        0,
        int(round(width * 0.10)),   # open ocean
        int(round(width * 0.21)),   # swamp / mangrove
        int(round(width * 0.33)),   # lake basin
        int(round(width * 0.47)),   # plains
        int(round(width * 0.55)),   # village enclave
        int(round(width * 0.70)),   # rainforest
        int(round(width * 0.85)),   # snow mountain
        width,                      # desert
    ]
    spans = []
    for i, biome in enumerate(BIOME_ORDER):
        start = max(0, min(width - 1, cuts[i]))
        end = max(start + 1, min(width, cuts[i + 1]))
        spans.append([start, end, biome])
    spans[-1][1] = width
    return spans


def normalize_biome_spans(raw, width):
    width = max(1, int(width))
    out = []
    if isinstance(raw, (list, tuple)):
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            try:
                start = max(0, min(width - 1, int(row[0])))
                end = max(start + 1, min(width, int(row[1])))
            except Exception:
                continue
            biome = str(row[2])
            if biome not in BIOME_LABELS:
                continue
            out.append([start, end, biome])
    return out or default_biome_spans(width)


def biome_at_tile_x(tx, spans, width):
    tx = max(0, min(max(0, int(width) - 1), int(tx)))
    for start, end, biome in normalize_biome_spans(spans, width):
        if int(start) <= tx < int(end):
            return biome
    return BIOME_PLAINS
