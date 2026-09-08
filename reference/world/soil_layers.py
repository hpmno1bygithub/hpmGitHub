# -*- coding: utf-8 -*-
"""Three-segment porous-ground geometry for dirt and sand surfaces.

Bits are ordered by physical height:
    LOW  = bottom third
    MID  = middle third
    HIGH = top third

The default mask is FULL, so old saves/maps remain unchanged until a surface
porous-ground tile is authored or layer-mined.  Water distribution is not stored separately: the
existing SoilCell.moisture is conserved and interpreted low -> mid -> high.
"""

SOIL_LAYER_LOW = 0x1
SOIL_LAYER_MID = 0x2
SOIL_LAYER_HIGH = 0x4
SOIL_LAYER_FULL = SOIL_LAYER_LOW | SOIL_LAYER_MID | SOIL_LAYER_HIGH


def normalize_soil_mask(mask):
    try:
        value = int(mask) & SOIL_LAYER_FULL
    except Exception:
        value = SOIL_LAYER_FULL
    return value


def soil_layer_count(mask):
    m = normalize_soil_mask(mask)
    return int(bool(m & SOIL_LAYER_LOW)) + int(bool(m & SOIL_LAYER_MID)) + int(bool(m & SOIL_LAYER_HIGH))


def soil_capacity_ratio(mask):
    return soil_layer_count(mask) / 3.0


def soil_vertical_bounds(mask):
    """Return occupied [top,bottom] ratios within the tile (0 top, 1 bottom)."""
    m = normalize_soil_mask(mask)
    if m == 0:
        return (1.0, 1.0)
    tops = []
    bottoms = []
    if m & SOIL_LAYER_HIGH:
        tops.append(0.0); bottoms.append(1.0/3.0)
    if m & SOIL_LAYER_MID:
        tops.append(1.0/3.0); bottoms.append(2.0/3.0)
    if m & SOIL_LAYER_LOW:
        tops.append(2.0/3.0); bottoms.append(1.0)
    return (min(tops), max(bottoms))


def soil_top_ratio(mask):
    return soil_vertical_bounds(mask)[0]


def soil_bottom_ratio(mask):
    return soil_vertical_bounds(mask)[1]


def next_layer_for_downward(mask):
    """Digging downward removes top -> middle -> bottom."""
    m = normalize_soil_mask(mask)
    for bit in (SOIL_LAYER_HIGH, SOIL_LAYER_MID, SOIL_LAYER_LOW):
        if m & bit:
            return bit
    return 0


def next_layer_for_upward(mask):
    """Digging upward removes bottom -> middle -> top."""
    m = normalize_soil_mask(mask)
    for bit in (SOIL_LAYER_LOW, SOIL_LAYER_MID, SOIL_LAYER_HIGH):
        if m & bit:
            return bit
    return 0


def water_distribution(total_moisture, mask, saturation=1.0):
    """Distribute existing soil water low -> middle -> high conservatively."""
    m = normalize_soil_mask(mask)
    remaining = max(0.0, float(total_moisture))
    per_layer = max(0.0, float(saturation)) / 3.0
    result = {SOIL_LAYER_LOW: 0.0, SOIL_LAYER_MID: 0.0, SOIL_LAYER_HIGH: 0.0}
    for bit in (SOIL_LAYER_LOW, SOIL_LAYER_MID, SOIL_LAYER_HIGH):
        if not (m & bit):
            continue
        put = min(per_layer, remaining)
        result[bit] = put
        remaining -= put
        if remaining <= 1e-12:
            break
    return result
