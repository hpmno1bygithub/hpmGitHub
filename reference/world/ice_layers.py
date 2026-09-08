# -*- coding: utf-8 -*-
"""Mass-conserving three-stage ice geometry."""
from config import (
    ICE_STAGE_LOW_MAX_MASS, ICE_STAGE_MID_MAX_MASS,
    ICE_STAGE_LOW_HEIGHT_RATIO, ICE_STAGE_MID_HEIGHT_RATIO,
    ICE_STAGE_HIGH_HEIGHT_RATIO, ICE_STAGE_MIN_MASS,
)

def clamp_ice_mass(mass):
    return max(0.0, min(1.0, float(mass)))

def ice_stage_for_mass(mass):
    m = clamp_ice_mass(mass)
    if m < float(ICE_STAGE_MIN_MASS):
        return 0
    if m < float(ICE_STAGE_LOW_MAX_MASS):
        return 1
    if m < float(ICE_STAGE_MID_MAX_MASS):
        return 2
    return 3

def ice_height_ratio_for_mass(mass):
    stage = ice_stage_for_mass(mass)
    if stage <= 0:
        return 0.0
    if stage == 1:
        return float(ICE_STAGE_LOW_HEIGHT_RATIO)
    if stage == 2:
        return float(ICE_STAGE_MID_HEIGHT_RATIO)
    return float(ICE_STAGE_HIGH_HEIGHT_RATIO)

def ice_stage_name(mass):
    return ("none", "low", "mid", "high")[ice_stage_for_mass(mass)]
