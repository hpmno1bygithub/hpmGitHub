# -*- coding: utf-8 -*-
"""Pyto RPG MetalKit Renderer - Stage 2.

GPU in this stage: sky, TileMap, Player, Camera, simple NPC/item/boxes.
PytoUI/UIKit: controls and compact status HUD.
Environment visuals are rendered by Metal in this stage. Physics remains Python-authoritative.
"""

import ctypes
import math
import os
import json
import threading
import time
import traceback
from collections import deque

try:
    import numpy as _np
except Exception:
    _np = None

import pyto_ui as ui
import mainthread

try:
    import Metal  # noqa: F401
except Exception:
    Metal = None

try:
    import MetalKit  # noqa: F401
except Exception as exc:
    MetalKit = None
    _METALKIT_IMPORT_ERROR = repr(exc)
else:
    _METALKIT_IMPORT_ERROR = ''

from rubicon.objc import ObjCClass, ObjCInstance, NSObject, objc_method
from rubicon.objc.types import CGSize
from ios_host.objc_view import WrapperView
from ios_host.orientation import is_ipad
from ios_host.control_overlay import build_selector_overlay
from systems.underground_background import (
    build_background_plan,
    load_background_catalog,
)

from config import (
    VERSION, TARGET_FPS, TILE_SIZE, METAL_MAX_QUADS, METAL_STATUS_UPDATE_SECONDS,
    ALPINE_SURFACE_FREEZE_MIN_AMOUNT,
    METAL_WATER_MIN_VISIBLE, METAL_MAX_ENV_QUADS,
    METAL_TERRAIN_GPU_CHUNK_CACHE_LIMIT, METAL_TERRAIN_STREAM_MAX_QUADS,
    METAL_TERRAIN_STREAM_HARD_LIMIT,
    METAL_DYNAMIC_MAX_QUADS, METAL_BACKGROUND_MAX_QUADS,
    WATER_SUPPORT_THRESHOLD, WATER_RENDER_SEGMENTS, WATER_STREAM_WIDTH,
    WATER_SURFACE_SLOPE_LIMIT, WATER_FALL_PARTICLE_COUNT,
    OCEAN_WAVE_AMPLITUDE_PX, OCEAN_WAVE_SPEED, OCEAN_WAVE_WAVENUMBER,
    OCEAN_WAVE_SECONDARY_AMPLITUDE_PX, OCEAN_WAVE_SECONDARY_SPEED,
    OCEAN_WAVE_SECONDARY_WAVENUMBER, OCEAN_WAVE_MAX_CREST_PX,
    CONNECTED_WAVE_DECAY_PER_TILE, CONNECTED_WAVE_MIN_GAIN,
    CONNECTED_WAVE_PHASE_LAG_PER_TILE, CONNECTED_WAVE_SEARCH_MARGIN_TILES,
    WATER_FALL_PARTICLE_SPEED, WATER_RAIN_PARTICLES_PER_CHUNK,
    CHUNK_SIZE,
    MAX_HP, MAX_STAMINA, MAX_MANA,
    WIND_VISUAL_PARTICLES_PER_CHUNK,
    WIND_VISUAL_MIN_SPEED,
    WIND_VISUAL_SPEED_SCALE,
    WIND_VISUAL_ALPHA,
    RAIN_VISUAL_PARTICLES_PER_CHUNK,
    RAIN_VISUAL_MIN_PARTICLES,
    RAIN_VISUAL_STREAK_LENGTH,
    RAIN_VISUAL_ALPHA,
    RAIN_SPLASH_PARTICLES_PER_CHUNK,
    RAIN_VISUAL_STREAK_WIDTH,
    WIND_VISUAL_TRACE_WIDTH,
    WIND_VISUAL_TRACE_LENGTH,
    WIND_VISUAL_WEAK_DURATION,
    WIND_VISUAL_MEDIUM_DURATION,
    WIND_VISUAL_STRONG_DURATION,
    WIND_VISUAL_TRACE_WIDTH_WEAK,
    WIND_VISUAL_TRACE_WIDTH_MEDIUM,
    WIND_VISUAL_TRACE_WIDTH_STRONG,
    WIND_VISUAL_TRACE_LENGTH_WEAK,
    WIND_VISUAL_TRACE_LENGTH_MEDIUM,
    WIND_VISUAL_TRACE_LENGTH_STRONG,
    WIND_VISUAL_DENSITY_WEAK,
    WIND_VISUAL_DENSITY_MEDIUM,
    WIND_VISUAL_DENSITY_STRONG,
    RAIN_VISUAL_LIGHT_THRESHOLD,
    RAIN_VISUAL_LIGHT_SPEED,
    RAIN_VISUAL_HEAVY_SPEED,
    RAIN_VISUAL_LIGHT_DENSITY,
    RAIN_VISUAL_HEAVY_DENSITY,
    RAIN_VISUAL_STREAK_WIDTH_LIGHT,
    RAIN_VISUAL_STREAK_WIDTH_HEAVY,
    RAIN_VISUAL_STREAK_LENGTH_LIGHT,
    RAIN_VISUAL_STREAK_LENGTH_HEAVY,
    RAIN_VISUAL_COLOR_R,
    RAIN_VISUAL_COLOR_G,
    RAIN_VISUAL_COLOR_B,
    FIRE_STACK_WIDTH_L1,
    FIRE_STACK_WIDTH_L2,
    FIRE_STACK_WIDTH_L3,
    FIRE_STACK_HEIGHT_L1,
    FIRE_STACK_HEIGHT_L2,
    FIRE_STACK_HEIGHT_L3,
    ASH_REHYDRATE_SECONDS,
    ASH_REHYDRATE_BLEND_START,
    TILE_RENDER_OVERLAP,
    PERF_ENV_RENDER_HZ,
    PERF_WATER_RENDER_HZ,
    PERF_FAST_WIND_VFX,
    PERF_WIND_VFX_MAX_PARTICLES_PER_CHUNK,
    AIM_RETICLE_RADIUS,
    ENGINEERING_HUD_ENABLED,
    MOVEMENT_DEBUG_HUD_ENABLED,
    ATMOSPHERE_CLOUD_FULL_MASS,
    CLOUD_VISUAL_MIN_MASS,
    PIXEL_WORLD_SCALE,
    UNDERWATER_BUBBLE_MAX, UNDERWATER_GRASS_MAX,
    UNDERWATER_ANCHOR_REFRESH_SECONDS,
)

# V0.6.2.6: custom editor maps use one contiguous scene stream, one
# instance buffer and one draw call at byte offset 0. This avoids both
# real-device bridge failures observed so far: separate terrain buffers and
# non-zero dynamic offsets.
METAL_CUSTOM_SCENE_MAX_QUADS = (
    METAL_TERRAIN_STREAM_MAX_QUADS
    + METAL_MAX_ENV_QUADS * 2
    + METAL_DYNAMIC_MAX_QUADS
)
# V0.6.2.7: true single-buffer path. Sky and every visible world instance
# are uploaded together and rendered by one draw call from the same buffer.
METAL_TRUE_SCENE_MAX_QUADS = METAL_CUSTOM_SCENE_MAX_QUADS + METAL_BACKGROUND_MAX_QUADS

from engine.renderer import Renderer
from engine.pixel_art import PIXEL_MAPPING_FIXED, PIXEL_MAPPING_FIT, fixed_rect_to_world, fit_rect_to_world, world_anchor_xy
from world.tile_registry import AIR, ICE, DIRT, GRASS_DIRT, SAND, SEA_SAND, LAYERED_GROUND_TILES, LAYERED_SOLID_TILES, tile_def
from world.ice_layers import ice_height_ratio_for_mass
from world.soil_layers import SOIL_LAYER_FULL, soil_vertical_bounds, soil_top_ratio
from entities.item import Item
from entities.creature import Creature
from entities.chest import Chest
from systems.weapon_catalog import weapon_from_item
from systems.boss_weapon_render import (
    append_icon as append_boss_icon, append_visuals as append_boss_visuals,
    reserve_quads as boss_visual_reserve,
)
from systems.terrain_visuals import terrain_detail_quads
from systems.terrain_stream import (
    view_window as terrain_view_window, build_stream as build_terrain_stream,
    capacity_for as terrain_capacity_for, raster_fallback as terrain_raster_fallback,
)

MTL_PRIMITIVE_TYPE_TRIANGLE = 3
_LOADED_LIBRARIES = []

# Tiny Metal-native font used only for fixed inventory labels. Horizontal runs
# are merged into one quad, keeping the modal comfortably below the existing
# dynamic-instance budget.
_INV_FONT = {
    "A":("010","101","111","101","101"), "B":("110","101","110","101","110"),
    "D":("110","101","101","101","110"), "E":("111","100","110","100","111"),
    "G":("011","100","101","101","011"), "I":("111","010","010","010","111"),
    "L":("100","100","100","100","111"), "M":("101","111","111","101","101"),
    "O":("010","101","101","101","010"), "P":("110","101","110","100","100"),
    "Q":("010","101","101","111","011"), "R":("110","101","110","101","101"),
    "T":("111","010","010","010","010"), "U":("101","101","101","101","111"),
    "V":("101","101","101","101","010"), "X":("101","101","010","101","101"),
    "Y":("101","101","010","010","010"),
    "0":("111","101","101","101","111"), "1":("010","110","010","010","111"),
    "2":("110","001","010","100","111"), "3":("110","001","010","001","110"),
    "4":("101","101","111","001","001"), "5":("111","100","110","001","110"),
    "6":("011","100","111","101","111"), "7":("111","001","010","010","010"),
    "8":("111","101","111","101","111"), "9":("111","101","111","001","110"),
    "+":("000","010","111","010","000"), "-":("000","000","111","000","000"),
    "/":("001","001","010","100","100"), "<":("001","010","100","010","001"),
    ">":("100","010","001","010","100"), " ":("000","000","000","000","000"),
}


def _call0(obj, name):
    value = getattr(obj, name)
    return value() if callable(value) else value


def _ptr_value(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        v = value.value
        if v is not None:
            return int(v)
    except Exception:
        pass
    try:
        return int(value)
    except Exception:
        return 0


def create_default_metal_device():
    errors = []
    try:
        import Metal as _Metal
        fn = getattr(_Metal, 'MTLCreateSystemDefaultDevice', None)
        if fn is not None:
            device = fn()
            if device is not None:
                return device, 'Metal module function'
    except Exception as exc:
        errors.append('Metal module: %r' % (exc,))

    for path in ('/System/Library/Frameworks/Metal.framework/Metal', None):
        try:
            lib = ctypes.CDLL(path) if path else ctypes.CDLL(None)
            _LOADED_LIBRARIES.append(lib)
            fn = lib.MTLCreateSystemDefaultDevice
            fn.argtypes = []
            fn.restype = ctypes.c_void_p
            ptr = fn()
            if ptr:
                return ObjCInstance(ptr), ('ctypes Metal.framework' if path else 'ctypes current process')
        except Exception as exc:
            errors.append('%s: %r' % (path or 'CDLL(None)', exc))
    raise RuntimeError('Unable to obtain MTLDevice. Attempts:\n' + '\n'.join(errors))


def _hex_rgba(value, alpha=1.0):
    s = str(value).strip().lstrip('#')
    if len(s) != 6:
        return (1.0, 0.0, 1.0, float(alpha))
    return (
        int(s[0:2], 16) / 255.0,
        int(s[2:4], 16) / 255.0,
        int(s[4:6], 16) / 255.0,
        float(alpha),
    )


SKY_RGBA = _hex_rgba('#8FC8E8')
# V0.7.1.7 non-colliding visual back wall.  Foreground terrain covers this
# normally; mined cells reveal these depth bands instead of the sky colour.
# V0.7.2: keep a visible but restrained separation from foreground dirt
# (#6C5238).  This prevents future imported terrain art from being confused
# with the non-colliding back wall.
BACKDROP_SURFACE_RGBA = _hex_rgba('#62503F')
BACKDROP_SHALLOW_RGBA = _hex_rgba('#584838')
BACKDROP_DEEP_RGBA = _hex_rgba('#302C2A')
PLAYER_RGBA = _hex_rgba('#F1CD57')
PLAYER_CLIMB_RGBA = _hex_rgba('#58B99A')
PLAYER_ATTACK_RGBA = _hex_rgba('#ED8256')
PLAYER_HURT_RGBA = _hex_rgba('#F06E63')
# FIX67 wearable equipment is procedural pixel geometry in the same dynamic
# Metal instance batch as the player.  Keeping the palette here makes every
# iPhone/iPad path (authored player sprite and the one-quad fallback alike)
# produce the same readable silhouettes without UIKit subviews or textures.
EQUIPMENT_DARK_RGBA = _hex_rgba('#172234')
EQUIPMENT_STEEL_RGBA = _hex_rgba('#3D6487')
EQUIPMENT_SHIELD_RGBA = _hex_rgba('#55DDF4', 0.32)
EQUIPMENT_SHIELD_EDGE_RGBA = _hex_rgba('#B9F8FF', 0.92)
EQUIPMENT_ELECTRIC_RGBA = _hex_rgba('#F6D643')
EQUIPMENT_ELECTRIC_TIP_RGBA = _hex_rgba('#40372D')
EQUIPMENT_CHEEK_RGBA = _hex_rgba('#E84D3D')
EQUIPMENT_SKY_RGBA = _hex_rgba('#EEF9FA')
EQUIPMENT_SKY_BLUE_RGBA = _hex_rgba('#70CBEF')
NPC_RGBA = _hex_rgba('#88528B')
ITEM_RGBA = _hex_rgba('#E39A38')
BOX_RGBA = _hex_rgba('#9A7148')
SLIME_RGBA = _hex_rgba('#63C56E')
BOAR_RGBA = _hex_rgba('#9B6845')
CREATURE_SPECIES_RGBA = {
    "slime": SLIME_RGBA, "boar": BOAR_RGBA,
    "wolf": _hex_rgba('#777B82'), "deer": _hex_rgba('#A8794E'),
    "gull": _hex_rgba('#E6EAEE'), "shore_crab": _hex_rgba('#C36F49'),
    "sea_turtle": _hex_rgba('#5C8D56'), "heron": _hex_rgba('#B8C7CF'),
    "otter": _hex_rgba('#8C6A4C'), "mangrove_crab": _hex_rgba('#A85E3F'),
    "desert_scorpion": _hex_rgba('#8A6139'), "sand_lizard": _hex_rgba('#A28C51'),
    "desert_snake": _hex_rgba('#C2A55E'), "desert_beetle": _hex_rgba('#72543B'),
    "swamp_slime": _hex_rgba('#65894B'), "crocodile": _hex_rgba('#496B3E'),
    "swamp_snake": _hex_rgba('#607A42'), "swamp_frog": _hex_rgba('#73A85C'),
    "village_rat": _hex_rgba('#7B7169'), "bandit": _hex_rgba('#8B4E45'),
    "village_dog": _hex_rgba('#9A774D'), "villager": _hex_rgba('#B88963'),
    "jungle_spider": _hex_rgba('#493B4F'), "jaguar": _hex_rgba('#C38A39'),
    "jungle_snake": _hex_rgba('#4F8A46'), "capybara": _hex_rgba('#8C6A4C'),
    "snow_wolf": _hex_rgba('#CED7DE'), "mountain_goat": _hex_rgba('#D0C4A8'),
    "ice_slime": _hex_rgba('#8FD6E7'), "snow_hare": _hex_rgba('#E3E8EB'),
    "sardine": _hex_rgba('#8BB8C7'), "mackerel": _hex_rgba('#5D8FA5'),
    "sea_bass": _hex_rgba('#6FA09A'), "carp": _hex_rgba('#B69058'),
    "crucian_carp": _hex_rgba('#A99C70'), "freshwater_bass": _hex_rgba('#6F8B63'),
    "mallard": _hex_rgba('#667B52'), "kingfisher": _hex_rgba('#4C8FA8'),
    "dragonfly": _hex_rgba('#77A7A3'), "damselfly": _hex_rgba('#7F90B8'),
    "toilet_man": _hex_rgba('#DDE5E8'), "speaker_man": _hex_rgba('#343A46'),
    "abyss_colossus": _hex_rgba('#514D61'),
    "mummy":_hex_rgba('#D8C79A'),"sphinx_monster":_hex_rgba('#C69346'),"minos":_hex_rgba('#7A4931'),"isis_serpent":_hex_rgba('#2C8879'),"blue_scarab":_hex_rgba('#246EB6'),
    "flying_imp":_hex_rgba('#B54138'),"infernal_goat":_hex_rgba('#342D32'),
    "burning_slime":_hex_rgba('#F15A24'),"flame_turtle":_hex_rgba('#4A3434'),
    "exploding_wisp":_hex_rgba('#F04B38'),"crystal_knight":_hex_rgba('#315785'),
    "crystal_slime":_hex_rgba('#5CC7E8'),"crystal_skeleton":_hex_rgba('#718CA4'),
    "crystal_lizard":_hex_rgba('#397A91'),
}
CREATURE_HURT_RGBA = _hex_rgba('#F17868')
CREATURE_ATTACK_RGBA = _hex_rgba('#E18A43')
SWORD_BLADE_RGBA = _hex_rgba('#D9E0E7')
SWORD_EDGE_RGBA = _hex_rgba('#F5F7FA')
SWORD_HANDLE_RGBA = _hex_rgba('#76502B')
CHEST_WOOD_RGBA = _hex_rgba('#8A5428')
CHEST_DARK_RGBA = _hex_rgba('#4A2B18')
CHEST_GOLD_RGBA = _hex_rgba('#E3B94D')
CHEST_OPEN_RGBA = _hex_rgba('#33241B')
BUBBLE_RGBA = (0.72, 0.91, 1.0, 0.54)
SEAGRASS_RGBA = (0.12, 0.58, 0.34, 0.76)
SEAGRASS_TIP_RGBA = (0.22, 0.72, 0.42, 0.70)
LAVA_SPIT_RGBA = (1.0, 0.36, 0.03, 0.98)
LAVA_SPIT_CORE_RGBA = (1.0, 0.82, 0.12, 0.98)
BERRY_RGBA = _hex_rgba('#9A5BC2')

# FIX28 global minimap palette.  The minimap deliberately uses a compact set
# of semantic colors rather than reproducing every authored tile color.  This
# keeps the 768x64 world legible on a phone-sized overlay and lets horizontal
# runs merge into a small number of Metal quads.
MINIMAP_BG_RGBA = _hex_rgba('#0A0D12', 0.86)
MINIMAP_BORDER_RGBA = _hex_rgba('#E8EDF2', 0.86)
MINIMAP_AIR_RGBA = _hex_rgba('#11161C', 0.92)
MINIMAP_ROCK_RGBA = _hex_rgba('#626872', 0.94)
MINIMAP_SOIL_RGBA = _hex_rgba('#72543A', 0.94)
MINIMAP_GRASS_RGBA = _hex_rgba('#5E8E49', 0.96)
MINIMAP_SAND_RGBA = _hex_rgba('#B99A58', 0.96)
MINIMAP_SNOW_RGBA = _hex_rgba('#DCE6EA', 0.96)
MINIMAP_SWAMP_RGBA = _hex_rgba('#526C42', 0.96)
MINIMAP_WATER_RGBA = _hex_rgba('#3F83C6', 0.96)
MINIMAP_STRUCTURE_RGBA = _hex_rgba('#9A7046', 0.96)
MINIMAP_LADDER_RGBA = _hex_rgba('#E1B55B', 1.0)
MINIMAP_PLAYER_RGBA = _hex_rgba('#FF5E52', 1.0)
MINIMAP_CAMERA_RGBA = _hex_rgba('#FFFFFF', 0.90)
MINIMAP_ENTRANCE_RGBA = _hex_rgba('#FFD66A', 1.0)


def _item_color(item_id):
    item_id = str(item_id)
    if item_id in ("wood", "leaf"):
        return _hex_rgba('#B77B3F') if item_id == "wood" else _hex_rgba('#5FA84F')
    if item_id in ("berry", "fruit"):
        return _hex_rgba('#B458A7') if item_id == "berry" else _hex_rgba('#D66A48')
    if "copper" in item_id:
        return _hex_rgba('#C77B4A')
    if "iron" in item_id:
        return _hex_rgba('#A8ADB2')
    if "gold" in item_id:
        return _hex_rgba('#E5C14D')
    if item_id == "slime_gel":
        return _hex_rgba('#72D780')
    if item_id in ("raw_meat", "hide"):
        return _hex_rgba('#C56E6B') if item_id == "raw_meat" else _hex_rgba('#8D633F')
    if item_id in ("fur", "white_fur"):
        return _hex_rgba('#A6A29A') if item_id == "fur" else _hex_rgba('#E3E8EB')
    if item_id in ("toxic_sac", "scorpion_tail"):
        return _hex_rgba('#9A67B4')
    if item_id in ("chitin", "lizard_scale", "tooth", "horn", "snake_skin"):
        return _hex_rgba('#B6A57D')
    if item_id in ("frog_leg", "ice_crystal", "feather"):
        if item_id == "frog_leg":
            return _hex_rgba('#79A85D')
        if item_id == "ice_crystal":
            return _hex_rgba('#A9E2F2')
        return _hex_rgba('#E6EAEE')
    if item_id in ("silk", "cloth"):
        return _hex_rgba('#D6D0C7')
    if item_id == "coin":
        return _hex_rgba('#E2C14D')
    return ITEM_RGBA


class Float2(ctypes.Structure):
    _fields_ = [('x', ctypes.c_float), ('y', ctypes.c_float)]


class Float4(ctypes.Structure):
    _fields_ = [
        ('x', ctypes.c_float), ('y', ctypes.c_float),
        ('z', ctypes.c_float), ('w', ctypes.c_float),
    ]


class InstanceData(ctypes.Structure):
    _fields_ = [('center', Float2), ('half_size', Float2), ('color', Float4)]


class UniformData(ctypes.Structure):
    _fields_ = [('viewport', Float2), ('camera', Float2)]


class SceneSplitData(ctypes.Structure):
    _fields_ = [
        ('background_count', ctypes.c_uint32),
        ('terrain_count', ctypes.c_uint32),
        ('water_count', ctypes.c_uint32),
        ('environment_count', ctypes.c_uint32),
        ('dynamic_count', ctypes.c_uint32),
        ('pad0', ctypes.c_uint32),
        ('pad1', ctypes.c_uint32),
        ('pad2', ctypes.c_uint32),
    ]


SHADER_SOURCE = r'''
#include <metal_stdlib>
using namespace metal;

struct InstanceData {
    float2 center;
    float2 halfSize;
    float4 color;
};

struct UniformData {
    float2 viewport;
    float2 camera;
};

struct SceneSplitData {
    uint backgroundCount;
    uint terrainCount;
    uint waterCount;
    uint environmentCount;
    uint dynamicCount;
    uint pad0;
    uint pad1;
    uint pad2;
};

struct VertexOut {
    float4 position [[position]];
    float4 color;
};

vertex VertexOut vertex_main(
    uint vid [[vertex_id]],
    uint iid [[instance_id]],
    const device InstanceData* instances [[buffer(0)]],
    constant UniformData& uniforms [[buffer(1)]])
{
    const float2 corner[6] = {
        float2(-1.0, -1.0), float2(1.0, -1.0), float2(-1.0, 1.0),
        float2(-1.0, 1.0), float2(1.0, -1.0), float2(1.0, 1.0)
    };

    InstanceData inst = instances[iid];
    float2 worldPixel = inst.center + corner[vid] * inst.halfSize;
    float2 screenPixel = worldPixel - uniforms.camera;

    float2 ndc;
    ndc.x = (screenPixel.x / max(1.0, uniforms.viewport.x)) * 2.0 - 1.0;
    ndc.y = 1.0 - (screenPixel.y / max(1.0, uniforms.viewport.y)) * 2.0;

    VertexOut out;
    out.position = float4(ndc, 0.0, 1.0);
    out.color = inst.color;
    return out;
}

vertex VertexOut vertex_custom_scene(
    uint vid [[vertex_id]],
    uint iid [[instance_id]],
    const device InstanceData* backgroundInstances [[buffer(0)]],
    constant UniformData& uniforms [[buffer(1)]],
    const device InstanceData* terrainInstances [[buffer(2)]],
    const device InstanceData* waterInstances [[buffer(3)]],
    const device InstanceData* environmentInstances [[buffer(4)]],
    const device InstanceData* dynamicInstances [[buffer(5)]],
    constant SceneSplitData& split [[buffer(6)]])
{
    const float2 corner[6] = {
        float2(-1.0, -1.0), float2(1.0, -1.0), float2(-1.0, 1.0),
        float2(-1.0, 1.0), float2(1.0, -1.0), float2(1.0, 1.0)
    };

    InstanceData inst;
    uint local = iid;
    if (local < split.backgroundCount) {
        inst = backgroundInstances[local];
    } else {
        local -= split.backgroundCount;
        if (local < split.terrainCount) {
            inst = terrainInstances[local];
        } else {
            local -= split.terrainCount;
            if (local < split.waterCount) {
                inst = waterInstances[local];
            } else {
                local -= split.waterCount;
                if (local < split.environmentCount) {
                    inst = environmentInstances[local];
                } else {
                    local -= split.environmentCount;
                    inst = dynamicInstances[local];
                }
            }
        }
    }

    float2 worldPixel = inst.center + corner[vid] * inst.halfSize;
    float2 screenPixel = worldPixel - uniforms.camera;
    float2 ndc;
    ndc.x = (screenPixel.x / max(1.0, uniforms.viewport.x)) * 2.0 - 1.0;
    ndc.y = 1.0 - (screenPixel.y / max(1.0, uniforms.viewport.y)) * 2.0;
    VertexOut out;
    out.position = float4(ndc, 0.0, 1.0);
    out.color = inst.color;
    return out;
}

fragment float4 fragment_main(VertexOut in [[stage_in]])
{
    return in.color;
}
'''


class MetalRenderState:
    def __init__(self):
        self.lock = threading.RLock()
        self.snapshot = None
        self.device = None
        self.device_source = ''
        self.command_queue = None
        self.library = None
        self.pipeline = None
        self.custom_pipeline = None
        # V0.6.2.4: keep dynamic actors and custom terrain in separate
        # preallocated triple buffers. Both are created with MTKView setup and
        # both are written only from drawInMTKView, but every draw starts from
        # vertex-buffer offset 0. This avoids the non-zero tail-offset path that
        # made Player/NPC/item disappear on real Pyto/Rubicon devices.
        self.instance_buffers = []
        self.terrain_stream_buffers = []
        self.terrain_stream_capacities = []
        self.terrain_display_fallbacks = 0
        self.background_buffers = []
        self.uniform_buffers = []
        self.scene_split_buffers = []
        # FIX40 custom-map slow layers use dedicated renderer-owned rings.
        # They are populated only from drawInMTKView when their CPU generation
        # changes, so the simulation thread never makes Objective-C/Metal calls
        # and unchanged terrain/water/environment are not repacked at 60 Hz.
        self.water_stream_buffers = []
        self.environment_stream_buffers = []
        self.custom_terrain_source_key = None
        self.custom_water_source_key = None
        self.custom_environment_source_key = None
        self.custom_terrain_active_index = 0
        self.custom_water_active_index = 0
        self.custom_environment_active_index = 0
        self.custom_terrain_count = 0
        self.custom_water_count = 0
        self.custom_environment_count = 0
        self.custom_terrain_uploads = 0
        self.custom_water_uploads = 0
        self.custom_environment_uploads = 0
        self.dynamic_zero_offset_logged = False
        self.buffer_index = 0
        self.delegate = None
        self.stage = 'START'
        self.last_error = ''
        self.draw_errors = 0
        self.frames = 0
        self.fps = 0.0
        self._fps_frames = 0
        self._fps_time = time.monotonic()
        self.last_quad_count = 0
        self.last_terrain_quads = 0
        self.last_environment_quads = 0
        self.last_water_quads = 0
        self.last_dynamic_quads = 0
        self.running = True
        # FIX71 message bridge: render() may publish at 60/120 Hz while the
        # retained UIKit HUD is sampled much less frequently.  Hold the latest
        # non-empty EventBus message across several snapshots and give it a
        # serial so the monitor starts the 2.8 s toast exactly once.
        self.message_serial = 0
        self.message_text = ""
        self.message_latch_until = 0.0
        # FIX62 inventory is an immutable screen-space quad batch merged into
        # the existing dynamic draw. It has no per-slot UIKit/CoreAnimation
        # objects and no additional Metal draw call.
        self.inventory_overlay_quads = tuple()

        # V0.6.1 display clock bridge. MTKView owns presentation cadence; the
        # Python simulation thread waits for these pulses instead of running
        # an independent time.sleep(1/60) clock. A sequence counter is used
        # instead of Event so a slow simulation can skip stale pulses rather
        # than replaying a backlog and causing another catch-up hitch.
        self.display_condition = threading.Condition(self.lock)
        self.display_seq = 0
        self.display_time = time.monotonic()

    def signal_display_pulse(self):
        now = time.monotonic()
        with self.display_condition:
            self.display_seq += 1
            self.display_time = now
            self.display_condition.notify_all()
            return self.display_seq, self.display_time

    def wait_for_display_pulse(self, last_seq, timeout=0.25):
        with self.display_condition:
            if self.running and self.display_seq <= int(last_seq):
                self.display_condition.wait(timeout=max(0.001, float(timeout)))
            return self.display_seq, self.display_time

    def publish(self, snapshot):
        with self.lock:
            payload = dict(snapshot or {})
            try:
                message_now = float(payload.get('render_now', time.monotonic()))
            except Exception:
                message_now = time.monotonic()
            incoming_message = payload.get('message')
            if incoming_message:
                self.message_serial += 1
                self.message_text = str(incoming_message)
                # METAL_STATUS_UPDATE_SECONDS is currently below 0.4 s.  A
                # minimum 0.9 s latch tolerates a delayed/coalesced main-thread
                # monitor transaction without stretching the visible toast.
                self.message_latch_until = message_now + max(
                    0.9, float(METAL_STATUS_UPDATE_SECONDS) * 2.5,
                )
            if self.message_text and message_now <= self.message_latch_until:
                payload['message'] = self.message_text
            else:
                payload['message'] = None
                if message_now > self.message_latch_until:
                    self.message_text = ""
            payload['message_serial'] = int(self.message_serial)
            # FIX66 keeps selector artwork separate from the world batch.
            # Inventory can therefore suppress it without scanning/removing
            # quads, and ordinary frames reserve a bounded tail for controls.
            base = tuple(payload.get('dynamic_quads', ()) or ())
            selector = tuple(payload.get('selector_quads', ()) or ())
            payload['_inventory_base_dynamic'] = base
            overlay = tuple(self.inventory_overlay_quads or ())
            tail = overlay if overlay else selector
            if tail:
                room = max(0, int(METAL_DYNAMIC_MAX_QUADS) - len(tail))
                payload['dynamic_quads'] = tuple(base[:room]) + tail[:METAL_DYNAMIC_MAX_QUADS]
            else:
                payload['dynamic_quads'] = base[:METAL_DYNAMIC_MAX_QUADS]
            payload['custom_dynamic_count'] = min(
                int(METAL_DYNAMIC_MAX_QUADS), len(payload['dynamic_quads'])
            )
            self.snapshot = payload

    def set_inventory_overlay(self, quads, projection=None):
        """Replace the modal batch and atomically refresh its projection.

        The simulation is intentionally paused while the backpack is open.
        iPad Stage Manager can still resize the MTKView during that pause, so
        the last world snapshot may carry stale viewport/camera values.  The
        overlay geometry and Metal uniform must switch together under this
        lock or native hit targets and rendered slots no longer align.
        """
        with self.lock:
            self.inventory_overlay_quads = tuple(quads or ())[:METAL_DYNAMIC_MAX_QUADS]
            if self.snapshot is None:
                return
            payload = dict(self.snapshot)
            if isinstance(projection, dict):
                for key in ("viewport_w", "viewport_h", "camera_x", "camera_y"):
                    if key in projection:
                        payload[key] = float(projection[key])
            base = tuple(payload.get('_inventory_base_dynamic', payload.get('dynamic_quads', ())) or ())
            payload['_inventory_base_dynamic'] = base
            overlay = self.inventory_overlay_quads
            selector = tuple(payload.get('selector_quads', ()) or ())
            tail = overlay if overlay else selector
            if tail:
                room = max(0, int(METAL_DYNAMIC_MAX_QUADS) - len(tail))
                payload['dynamic_quads'] = tuple(base[:room]) + tail[:METAL_DYNAMIC_MAX_QUADS]
            else:
                payload['dynamic_quads'] = base[:METAL_DYNAMIC_MAX_QUADS]
            payload['custom_dynamic_count'] = min(
                int(METAL_DYNAMIC_MAX_QUADS), len(payload.get('dynamic_quads', ()))
            )
            self.snapshot = payload

    def get_snapshot(self):
        with self.lock:
            return self.snapshot

    def tick_frame(self, quad_count):
        self.frames += 1
        self.last_quad_count = int(quad_count)
        self._fps_frames += 1
        now = time.monotonic()
        elapsed = now - self._fps_time
        if elapsed >= 0.75:
            self.fps = self._fps_frames / elapsed
            self._fps_frames = 0
            self._fps_time = now

    def fail(self, stage, exc):
        self.stage = 'FAIL: ' + str(stage)
        self.last_error = '%s: %r' % (stage, exc)
        print('\n[Metal RPG Renderer ERROR]')
        print(self.last_error)
        traceback.print_exc()


def _write_instances(
    buffer_obj,
    quads,
    max_count=METAL_MAX_QUADS,
    offset_instances=0,
):
    """Upload instances to a shared MTLBuffer.

    V0.6.0 prefers NumPy's compiled C conversion path.  InstanceData is
    exactly eight contiguous float32 values (32 bytes), so an Nx8 float32
    ndarray can be copied directly without a Python per-quad ctypes loop.
    """
    count = min(len(quads), int(max_count))
    if count <= 0:
        return 0

    ptr = _ptr_value(_call0(buffer_obj, 'contents'))
    if not ptr:
        raise RuntimeError('MTLBuffer.contents returned null for instance buffer')
    ptr += int(offset_instances) * ctypes.sizeof(InstanceData)

    if (
        _np is not None
        and ctypes.sizeof(InstanceData) == 8 * ctypes.sizeof(ctypes.c_float)
    ):
        source = quads if count == len(quads) else quads[:count]
        packed = _np.asarray(source, dtype=_np.float32, order='C')
        if packed.ndim == 2 and packed.shape == (count, 8):
            if not packed.flags.c_contiguous:
                packed = _np.ascontiguousarray(packed, dtype=_np.float32)
            ctypes.memmove(
                ptr,
                int(packed.ctypes.data),
                int(count * 8 * ctypes.sizeof(ctypes.c_float)),
            )
            return count

    # Fallback for a Pyto build where NumPy is unavailable or the ABI differs.
    arr = (InstanceData * count)()
    for i in range(count):
        cx, cy, hx, hy, r, g, b, a = quads[i]
        arr[i].center = Float2(float(cx), float(cy))
        arr[i].half_size = Float2(float(hx), float(hy))
        arr[i].color = Float4(float(r), float(g), float(b), float(a))
    ctypes.memmove(ptr, ctypes.addressof(arr), ctypes.sizeof(InstanceData) * count)
    return count


def _new_immutable_instance_buffer(state, quads, max_count=METAL_MAX_QUADS):
    """Create a GPU buffer that is never mutated after publication.

    This is the key V0.6.0 path for terrain chunks and the lower-rate
    environment layer.  Python/ctypes conversion happens only when the source
    data changes, not on every 60 Hz Metal frame.
    """
    if state.device is None:
        return None, 0
    count = min(len(quads), int(max_count))
    if count <= 0:
        return None, 0
    size = int(count * ctypes.sizeof(InstanceData))
    buffer_obj = state.device.newBufferWithLength_options_(size, 0)
    if buffer_obj is None:
        raise RuntimeError('immutable MTLBuffer allocation returned nil')
    _write_instances(buffer_obj, quads, count)
    return buffer_obj, count


def _upload_visible_terrain(state, index, quads, bounds=None):
    """On the MTKView thread only: size for ALL visible terrain, not a prefix.

    A larger stream replaces just the selected ring slot. Old submitted
    buffers aren't resized in place. Allocation failure uses a complete
    lower-detail viewport proxy in the already allocated slot, not missing
    ground. The shader and single zero-offset draw remain unchanged.
    """
    required = len(quads)
    capacities = state.terrain_stream_capacities
    while len(capacities) < len(state.terrain_stream_buffers):
        capacities.append(int(METAL_TERRAIN_STREAM_MAX_QUADS))
    capacity = int(capacities[index])
    target = terrain_capacity_for(min(required, METAL_TERRAIN_STREAM_HARD_LIMIT),
                                  METAL_TERRAIN_STREAM_MAX_QUADS,
                                  METAL_TERRAIN_STREAM_HARD_LIMIT)
    if required > capacity and target > capacity:
        try:
            new_buffer = state.device.newBufferWithLength_options_(
                int(target * ctypes.sizeof(InstanceData)), 0)
        except Exception:
            new_buffer = None
        if new_buffer is not None:
            state.terrain_stream_buffers[index] = new_buffer
            capacities[index] = capacity = target
    if required > capacity:
        if bounds is None:
            # Old host test snapshots may omit the FIX100 diagnostic field.
            bounds = (min(q[0]-q[2] for q in quads), min(q[1]-q[3] for q in quads),
                      max(q[0]+q[2] for q in quads), max(q[1]+q[3] for q in quads))
        quads = terrain_raster_fallback(quads, bounds, capacity)
        state.terrain_display_fallbacks += 1
        if state.terrain_display_fallbacks == 1:
            print('FIX100: terrain memory guard; reduced display detail, no missing rows.')
    count = _write_instances(state.terrain_stream_buffers[index], quads, capacity)
    if count != len(quads):
        raise RuntimeError('Terrain upload would truncate visible geometry')
    return count


def _write_uniform(buffer_obj, viewport_w, viewport_h, camera_x, camera_y):
    uniform = UniformData(
        Float2(float(viewport_w), float(viewport_h)),
        Float2(float(camera_x), float(camera_y)),
    )
    ptr = _ptr_value(_call0(buffer_obj, 'contents'))
    if not ptr:
        raise RuntimeError('MTLBuffer.contents returned null for uniform buffer')
    ctypes.memmove(ptr, ctypes.addressof(uniform), ctypes.sizeof(UniformData))


def _write_scene_split(buffer_obj, background_count, terrain_count, water_count, environment_count, dynamic_count):
    split = SceneSplitData(
        int(background_count), int(terrain_count), int(water_count),
        int(environment_count), int(dynamic_count), 0, 0, 0,
    )
    ptr = _ptr_value(_call0(buffer_obj, 'contents'))
    if not ptr:
        raise RuntimeError('MTLBuffer.contents returned null for scene split buffer')
    ctypes.memmove(ptr, ctypes.addressof(split), ctypes.sizeof(SceneSplitData))


def _delegate_drawInMTKView_(self, view) -> None:
    state = getattr(self, 'render_state', None)
    if state is None or not state.running:
        return

    # V0.6.1: VSync is the master game clock. Signal first, then draw the most
    # recent complete snapshot. The simulation prepares the NEXT snapshot in
    # parallel, giving stable one-frame latency without two unsynchronised 60Hz
    # loops racing each other.
    state.signal_display_pulse()
    snapshot = state.get_snapshot()
    if snapshot is None or state.pipeline is None:
        return
    try:
        descriptor = view.currentRenderPassDescriptor
        drawable = view.currentDrawable
        if descriptor is None or drawable is None:
            return
        if (
            not state.instance_buffers
            or not state.terrain_stream_buffers
            or not state.background_buffers
            or not state.uniform_buffers
            or not state.scene_split_buffers
            or not state.water_stream_buffers
            or not state.environment_stream_buffers
        ):
            return

        index = state.buffer_index % len(state.instance_buffers)
        state.buffer_index += 1
        dynamic_buffer = state.instance_buffers[index]
        terrain_stream_buffer = state.terrain_stream_buffers[index]
        background_buffer = state.background_buffers[index]
        uniform_buffer = state.uniform_buffers[index]
        split_buffer = state.scene_split_buffers[index]

        background_quads = snapshot.get('background_quads', ())
        dynamic_quads = snapshot.get('dynamic_quads', ())

        # FIX40 segmented ONE-draw custom-map path. The previous true-single-
        # buffer implementation repacked sky + all visible terrain + water +
        # environment + actors into one NumPy array on every display frame.
        # Underwater scenes contain hundreds of liquid instances, so that CPU
        # copy became proportional to water coverage.  We now keep each slow
        # layer in its own renderer-owned MTLBuffer ring and select the proper
        # buffer in one vertex shader using instance_id. No vertex-buffer rebind occurs
        # inside the draw and the real-device one-draw safety contract remains.
        use_custom_scene = bool(snapshot.get('custom_scene_mode', False))
        background_count = _write_instances(
            background_buffer, background_quads, METAL_BACKGROUND_MAX_QUADS
        )
        dynamic_count = _write_instances(
            dynamic_buffer, dynamic_quads, METAL_DYNAMIC_MAX_QUADS
        )
        true_scene_count = 0
        custom_scene_count = 0
        terrain_count = 0
        water_count = 0
        environment_count = 0
        custom_terrain_buffer = None
        custom_water_buffer = None
        custom_environment_buffer = None

        if use_custom_scene:
            # Refresh only a layer whose CPU source generation changed.  Each
            # layer owns a 3-buffer ring, so a newly uploaded generation never
            # overwrites the MTLBuffer that an in-flight command buffer may
            # still be reading. This keeps every Objective-C buffer write on
            # the MTKView draw thread while eliminating full-scene 60 Hz packs.
            terrain_source_key = snapshot.get('custom_terrain_key')
            if terrain_source_key != state.custom_terrain_source_key:
                next_index = (int(state.custom_terrain_active_index) + 1) % len(state.terrain_stream_buffers)
                state.custom_terrain_count = _upload_visible_terrain(
                    state, next_index, snapshot.get('custom_terrain_quads', ()),
                    snapshot.get('terrain_stream_stats', {}).get('window'),
                )
                state.custom_terrain_active_index = next_index
                state.custom_terrain_source_key = terrain_source_key
                state.custom_terrain_uploads += 1
            custom_terrain_buffer = state.terrain_stream_buffers[int(state.custom_terrain_active_index)]
            terrain_count = int(state.custom_terrain_count)

            water_source_key = snapshot.get('custom_water_key')
            if water_source_key != state.custom_water_source_key:
                next_index = (int(state.custom_water_active_index) + 1) % len(state.water_stream_buffers)
                state.custom_water_count = _write_instances(
                    state.water_stream_buffers[next_index],
                    snapshot.get('custom_water_quads', ()),
                    METAL_MAX_ENV_QUADS,
                )
                state.custom_water_active_index = next_index
                state.custom_water_source_key = water_source_key
                state.custom_water_uploads += 1
            custom_water_buffer = state.water_stream_buffers[int(state.custom_water_active_index)]
            water_count = int(state.custom_water_count)

            environment_source_key = snapshot.get('custom_environment_key')
            if environment_source_key != state.custom_environment_source_key:
                next_index = (int(state.custom_environment_active_index) + 1) % len(state.environment_stream_buffers)
                state.custom_environment_count = _write_instances(
                    state.environment_stream_buffers[next_index],
                    snapshot.get('custom_environment_quads', ()),
                    METAL_MAX_ENV_QUADS,
                )
                state.custom_environment_active_index = next_index
                state.custom_environment_source_key = environment_source_key
                state.custom_environment_uploads += 1
            custom_environment_buffer = state.environment_stream_buffers[int(state.custom_environment_active_index)]
            environment_count = int(state.custom_environment_count)

            custom_scene_count = terrain_count + water_count + environment_count + dynamic_count
            true_scene_count = background_count + custom_scene_count
            _write_scene_split(
                split_buffer, background_count, terrain_count, water_count,
                environment_count, dynamic_count,
            )
            if not state.dynamic_zero_offset_logged:
                state.dynamic_zero_offset_logged = True
                px = float(snapshot.get('player_x', 0.0))
                py = float(snapshot.get('player_y', 0.0))
                cx = float(snapshot.get('camera_x', 0.0))
                cy = float(snapshot.get('camera_y', 0.0))
                print(
                    'CUSTOM SEGMENTED ONE-DRAW GPU OK:',
                    'total=', int(true_scene_count),
                    'terrain=', int(terrain_count),
                    'water=', int(water_count),
                    'env=', int(environment_count),
                    'dynamic=', int(dynamic_count),
                    'player_screen=(%.1f, %.1f)' % (px - cx, py - cy),
                )

        _write_uniform(
            uniform_buffer,
            snapshot['viewport_w'], snapshot['viewport_h'],
            snapshot['camera_x'], snapshot['camera_y'],
        )

        command_buffer = _call0(state.command_queue, 'commandBuffer')
        if command_buffer is None:
            return
        encoder = command_buffer.renderCommandEncoderWithDescriptor_(descriptor)
        if encoder is None:
            return
        total_count = 0

        def draw_batch(buffer_obj, count, offset_instances=0):
            nonlocal total_count
            if buffer_obj is None or int(count) <= 0:
                return
            byte_offset = int(offset_instances) * ctypes.sizeof(InstanceData)
            encoder.setVertexBuffer_offset_atIndex_(buffer_obj, byte_offset, 0)
            encoder.drawPrimitives_vertexStart_vertexCount_instanceCount_(
                MTL_PRIMITIVE_TYPE_TRIANGLE, 0, 6, int(count)
            )
            total_count += int(count)

        if use_custom_scene:
            # ONE draw call, all segment buffers bound once at offset 0. Empty
            # ranges reuse the background buffer only to satisfy Metal's buffer
            # binding validation; the shader never reads those ranges.
            if state.custom_pipeline is None:
                return
            encoder.setRenderPipelineState_(state.custom_pipeline)
            encoder.setVertexBuffer_offset_atIndex_(background_buffer, 0, 0)
            encoder.setVertexBuffer_offset_atIndex_(uniform_buffer, 0, 1)
            encoder.setVertexBuffer_offset_atIndex_(custom_terrain_buffer if custom_terrain_buffer is not None else background_buffer, 0, 2)
            encoder.setVertexBuffer_offset_atIndex_(custom_water_buffer if custom_water_buffer is not None else background_buffer, 0, 3)
            encoder.setVertexBuffer_offset_atIndex_(custom_environment_buffer if custom_environment_buffer is not None else background_buffer, 0, 4)
            encoder.setVertexBuffer_offset_atIndex_(dynamic_buffer, 0, 5)
            encoder.setVertexBuffer_offset_atIndex_(split_buffer, 0, 6)
            if int(true_scene_count) > 0:
                encoder.drawPrimitives_vertexStart_vertexCount_instanceCount_(
                    MTL_PRIMITIVE_TYPE_TRIANGLE, 0, 6, int(true_scene_count)
                )
                total_count = int(true_scene_count)
        else:
            encoder.setRenderPipelineState_(state.pipeline)
            encoder.setVertexBuffer_offset_atIndex_(uniform_buffer, 0, 1)
            draw_batch(background_buffer, background_count)
            terrain_count = 0
            for terrain_buffer, count in snapshot.get('terrain_batches', ()):
                draw_batch(terrain_buffer, count)
                terrain_count += int(count)

            water_batch = snapshot.get('water_batch')
            water_count = 0
            if water_batch:
                water_buffer, water_count_raw = water_batch
                water_count = int(water_count_raw)
                draw_batch(water_buffer, water_count_raw)

            environment_batch = snapshot.get('environment_batch')
            environment_count = 0
            if environment_batch:
                env_buffer, env_count = environment_batch
                environment_count = int(env_count)
                draw_batch(env_buffer, env_count)

            draw_batch(dynamic_buffer, dynamic_count, 0)

        _call0(encoder, 'endEncoding')
        command_buffer.presentDrawable_(drawable)
        _call0(command_buffer, 'commit')
        state.stage = 'METAL RPG DRAW OK / FIX40 SEGMENTED ONE-DRAW'
        state.last_terrain_quads = terrain_count
        state.last_water_quads = water_count
        state.last_environment_quads = environment_count
        state.last_dynamic_quads = dynamic_count
        state.tick_frame(total_count)
    except Exception as exc:
        state.draw_errors += 1
        if state.draw_errors <= 3:
            state.fail('drawInMTKView', exc)


def _delegate_mtkView_drawableSizeWillChange_(self, view, size: CGSize) -> None:
    return


_delegate_drawInMTKView_ = objc_method(_delegate_drawInMTKView_)
_delegate_mtkView_drawableSizeWillChange_ = objc_method(_delegate_mtkView_drawableSizeWillChange_)
_DELEGATE_CLASS_NAME = 'PytoRPGMetalDelegate_%d' % int(time.time_ns() % 1000000000000)
MetalRPGDelegate = ObjCClass(
    _DELEGATE_CLASS_NAME,
    (NSObject,),
    {
        'drawInMTKView_': _delegate_drawInMTKView_,
        'mtkView_drawableSizeWillChange_': _delegate_mtkView_drawableSizeWillChange_,
    },
)


class MetalRPGView(WrapperView):
    objc_class = ObjCClass('MTKView')

    def __init__(self, state):
        self.state = state
        super().__init__()

    def configure_view(self, view):
        state = self.state
        try:
            if MetalKit is None:
                raise RuntimeError('MetalKit import failed: ' + _METALKIT_IMPORT_ERROR)
            state.stage = 'DEVICE'
            device, source = create_default_metal_device()
            state.device = device
            state.device_source = source
            view.device = device
            view.preferredFramesPerSecond = int(TARGET_FPS)
            view.paused = False
            view.enableSetNeedsDisplay = False

            state.stage = 'COMMAND QUEUE'
            queue = _call0(device, 'newCommandQueue')
            if queue is None:
                raise RuntimeError('newCommandQueue returned nil')
            state.command_queue = queue

            state.stage = 'MSL COMPILE'
            library = device.newLibraryWithSource_options_error_(SHADER_SOURCE, None, None)
            if library is None:
                raise RuntimeError('newLibraryWithSource returned nil')
            state.library = library
            vertex_fn = library.newFunctionWithName_('vertex_main')
            fragment_fn = library.newFunctionWithName_('fragment_main')
            if vertex_fn is None or fragment_fn is None:
                raise RuntimeError('shader function lookup failed')

            state.stage = 'PIPELINE'
            desc = ObjCClass('MTLRenderPipelineDescriptor').new()
            desc.vertexFunction = vertex_fn
            desc.fragmentFunction = fragment_fn
            attachment = desc.colorAttachments.objectAtIndexedSubscript_(0)
            attachment.pixelFormat = view.colorPixelFormat
            # Standard alpha blending for water / fire overlays.
            attachment.blendingEnabled = True
            attachment.rgbBlendOperation = 0
            attachment.alphaBlendOperation = 0
            attachment.sourceRGBBlendFactor = 4
            attachment.destinationRGBBlendFactor = 5
            attachment.sourceAlphaBlendFactor = 1
            attachment.destinationAlphaBlendFactor = 5
            pipeline = device.newRenderPipelineStateWithDescriptor_error_(desc, None)
            if pipeline is None:
                raise RuntimeError('newRenderPipelineState returned nil')
            state.pipeline = pipeline

            # FIX40: custom editor maps keep the proven ONE draw call, but the
            # shader reads background/terrain/water/environment/dynamic from
            # separate buffers. Static terrain is no longer repacked at 60 Hz
            # just because the player is swimming in a large water body.
            custom_vertex_fn = library.newFunctionWithName_('vertex_custom_scene')
            if custom_vertex_fn is None:
                raise RuntimeError('custom scene shader function lookup failed')
            custom_desc = ObjCClass('MTLRenderPipelineDescriptor').new()
            custom_desc.vertexFunction = custom_vertex_fn
            custom_desc.fragmentFunction = fragment_fn
            custom_attachment = custom_desc.colorAttachments.objectAtIndexedSubscript_(0)
            custom_attachment.pixelFormat = view.colorPixelFormat
            custom_attachment.blendingEnabled = True
            custom_attachment.rgbBlendOperation = 0
            custom_attachment.alphaBlendOperation = 0
            custom_attachment.sourceRGBBlendFactor = 4
            custom_attachment.destinationRGBBlendFactor = 5
            custom_attachment.sourceAlphaBlendFactor = 1
            custom_attachment.destinationAlphaBlendFactor = 5
            custom_pipeline = device.newRenderPipelineStateWithDescriptor_error_(custom_desc, None)
            if custom_pipeline is None:
                raise RuntimeError('custom scene pipeline returned nil')
            state.custom_pipeline = custom_pipeline

            state.stage = 'GPU BUFFERS'
            # V0.6.2.6: the primary triple-buffered instance stream is large
            # enough for the whole visible custom-map scene. Built-in worlds
            # still use only the small dynamic portion.
            dynamic_bytes = int(
                METAL_CUSTOM_SCENE_MAX_QUADS * ctypes.sizeof(InstanceData)
            )
            terrain_stream_bytes = int(
                METAL_TERRAIN_STREAM_MAX_QUADS * ctypes.sizeof(InstanceData)
            )
            # FIX40 sky/backdrop is its own small segment; it no longer needs
            # to reserve enough memory for terrain + water + actors.
            background_bytes = int(METAL_BACKGROUND_MAX_QUADS * ctypes.sizeof(InstanceData))
            uniform_bytes = int(ctypes.sizeof(UniformData))
            split_bytes = int(ctypes.sizeof(SceneSplitData))
            slow_layer_bytes = int(METAL_MAX_ENV_QUADS * ctypes.sizeof(InstanceData))
            for _ in range(3):
                ib = device.newBufferWithLength_options_(dynamic_bytes, 0)
                tb = device.newBufferWithLength_options_(terrain_stream_bytes, 0)
                wb = device.newBufferWithLength_options_(slow_layer_bytes, 0)
                eb = device.newBufferWithLength_options_(slow_layer_bytes, 0)
                bb = device.newBufferWithLength_options_(background_bytes, 0)
                ub = device.newBufferWithLength_options_(uniform_bytes, 0)
                sb = device.newBufferWithLength_options_(split_bytes, 0)
                if ib is None or tb is None or wb is None or eb is None or bb is None or ub is None or sb is None:
                    raise RuntimeError('MTLBuffer allocation returned nil')
                state.instance_buffers.append(ib)
                state.terrain_stream_buffers.append(tb)
                state.terrain_stream_capacities.append(int(METAL_TERRAIN_STREAM_MAX_QUADS))
                state.water_stream_buffers.append(wb)
                state.environment_stream_buffers.append(eb)
                state.background_buffers.append(bb)
                state.uniform_buffers.append(ub)
                state.scene_split_buffers.append(sb)

            delegate = MetalRPGDelegate.new()
            delegate.render_state = state
            state.delegate = delegate
            view.delegate = delegate
            state.stage = 'READY / WAITING FOR GAME SNAPSHOT'

            print('')
            print('==============================================')
            print('Pyto RPG Metal Renderer Stage 2 setup OK')
            print('Device:', str(device.name))
            print('Device source:', source)
            print('Delegate class:', _DELEGATE_CLASS_NAME)
            print('Max quads:', METAL_MAX_QUADS)
            print('Instance upload:', 'NumPy C float32 path' if _np is not None else 'ctypes fallback')
            print('==============================================')
        except Exception as exc:
            state.fail(state.stage, exc)


class MetalKitRenderer(Renderer):
    def __init__(self, root, game):
        self.root = root
        self.game = game
        self.state = MetalRenderState()
        self.metal = MetalRPGView(self.state)
        self.root.add_subview(self.metal)

        # Proper gameplay HUD lives separately from the engineering debug
        # panel, so debug text can never cover HP/ST/MP again.
        self.hp_bg, self.hp_fill, self.hp_text = self._make_bar(
            "HP", ui.Color.rgb(0.79, 0.33, 0.33, 1.0)
        )
        self.st_bg, self.st_fill, self.st_text = self._make_bar(
            "ST", ui.Color.rgb(0.31, 0.66, 0.36, 1.0)
        )
        self.mp_bg, self.mp_fill, self.mp_text = self._make_bar(
            "MP", ui.Color.rgb(0.30, 0.52, 0.82, 1.0)
        )
        # FIX31 giant-boss HUD. Hidden unless an active streamed boss exists.
        self.boss_bg, self.boss_fill, self.boss_text = self._make_bar(
            "BOSS", ui.Color.rgb(0.72, 0.18, 0.34, 1.0)
        )
        self.boss_bg.hidden = True
        self.boss_text.font = ui.Font.bold_system_font_of_size(9.0)
        self.boss_text.text_alignment = ui.TextAlignment.CENTER

        self.status = ui.Label('Starting Metal RPG Renderer...')
        self.status.text_color = ui.Color.rgb(1, 1, 1, 0.96)
        self.status.background_color = ui.Color.rgb(0.03, 0.04, 0.06, 0.68)
        self.status.number_of_lines = 7
        self.status.font = ui.Font.system_font_of_size(7.5)
        self.status.corner_radius = 5
        self.status.hidden = True
        self.root.add_subview(self.status)

        # V0.6.2.7 always-visible compact runtime proof. This is UIKit, not
        # Metal, so it remains readable even if a Metal scene draw fails.
        self.runtime_diag = ui.Label("V0.7.7.5 FIX74")
        self.runtime_diag.text_color = ui.Color.rgb(1.0, 1.0, 1.0, 0.98)
        self.runtime_diag.background_color = ui.Color.rgb(0.02, 0.03, 0.04, 0.72)
        self.runtime_diag.text_alignment = ui.TextAlignment.CENTER
        self.runtime_diag.number_of_lines = 2
        self.runtime_diag.font = ui.Font.bold_system_font_of_size(7.5)
        self.runtime_diag.corner_radius = 5
        self.runtime_diag.user_interaction_enabled = False
        self.runtime_diag.hidden = True
        self.root.add_subview(self.runtime_diag)

        # V0.5.6.6:
        # Large movement/input panel for screen-recording diagnostics.
        # Kept separate from the general engineering HUD so LEFT/RIGHT state,
        # tap pulse, hold latch, wind drift and collision are readable in video.
        self.movement_status = ui.Label(
            "MOVE DEBUG\nwaiting..."
        )
        self.movement_status.text_color = ui.Color.rgb(
            1.0,
            1.0,
            1.0,
            0.98,
        )
        self.movement_status.background_color = ui.Color.rgb(
            0.02,
            0.03,
            0.04,
            0.82,
        )
        self.movement_status.number_of_lines = 8
        self.movement_status.font = ui.Font.bold_system_font_of_size(
            9.0
        )
        self.movement_status.corner_radius = 5
        self.movement_status.user_interaction_enabled = False
        self.movement_status.hidden = True
        self.root.add_subview(
            self.movement_status
        )

        # Metal mode previously consumed no EventBus messages, so errors like
        # "空氣水氣不足" happened silently. V0.4.14 restores a compact toast.
        self.message_label = ui.Label("")
        self.message_label.text_color = ui.Color.rgb(1, 1, 1, 1.0)
        self.message_label.background_color = ui.Color.rgb(
            0.03, 0.04, 0.06, 0.82
        )
        self.message_label.text_alignment = ui.TextAlignment.CENTER
        self.message_label.font = ui.Font.bold_system_font_of_size(9)
        self.message_label.corner_radius = 7
        self.message_label.hidden = True
        self.root.add_subview(self.message_label)

        self._last_message = ""
        self._last_message_serial = 0
        self._message_until = 0.0
        # Monitor/status updates are cosmetic.  Keep only the latest closure so
        # a temporarily busy UIKit thread can never accumulate stale HUD work.
        self._monitor_apply_lock = threading.Lock()
        self._monitor_apply_pending = False
        self._monitor_apply_latest = None

        self.viewport_w = 852.0
        self.viewport_h = 393.0
        self._inventory_hud_hidden = False
        # FIX64: a modal backpack must not leave MTKView running at 60 Hz.
        # While inventory is open the world is frozen, so continuously repacking
        # the same background/dynamic tuples only burns Python/ObjC bridge time
        # and can starve UIKit taps on real Pyto devices.  Modal mode pauses the
        # display link and renders exactly one frame for each inventory change.
        self._inventory_render_mode = False
        self._inventory_render_serial = 0
        # Opening/closing the backpack can happen faster than UIKit drains its
        # queue.  Keep only one pending MTKView pause/resume transaction and let
        # that transaction apply the latest desired mode.
        self._inventory_mode_apply_lock = threading.Lock()
        self._inventory_mode_apply_pending = False
        self._inventory_mode_apply_latest = False
        # At most one explicit MTKView draw task may wait on UIKit.  Repeated
        # inventory taps only mark the pending task dirty; it draws the newest
        # immutable snapshot instead of creating an unbounded run_async queue.
        self._draw_request_lock = threading.Lock()
        self._draw_request_pending = False
        self._draw_request_dirty = False
        # V0.5.9 terrain cache: one immutable quad batch per chunk.
        # Only a chunk whose local revision changes is rebuilt after mining.
        self._chunk_tile_cache = {}
        self._chunk_tile_cache_last_used = {}
        self._chunk_tile_cache_clock = 0
        self._chunk_tile_cache_limit = METAL_TERRAIN_GPU_CHUNK_CACHE_LIMIT
        self._terrain_gpu_uploads = 0
        # V0.6.2.3 custom-map CPU cache. No Objective-C/Metal call is made
        # from the Python simulation thread for this path. The MTKView draw
        # callback uploads this cached terrain into renderer-owned buffers.
        self._custom_terrain_cpu_key = None
        self._custom_terrain_cpu_quads = tuple()
        # FIX40: custom-map GPU uploads stay on the MTKView draw thread.
        # The simulation publishes CPU tuples plus generation keys only.
        # V0.6.4: editor-map terrain is cached per chunk on CPU. Mining one
        # tile patches only that chunk instead of re-running tile->quad
        # conversion across every visible chunk.
        self._custom_terrain_chunk_cache = {}
        self._custom_terrain_chunk_epoch = -1
        self._custom_terrain_stream_logged = False
        self._asset_registry_signature = None
        self._asset_registry_check_time = 0.0
        self._env_cache = tuple()
        self._env_gpu_batch = None
        self._env_gpu_uploads = 0
        self._env_cache_time = 0.0
        self._env_cache_generation = 0
        self._env_render_interval = 1.0 / max(1.0, PERF_ENV_RENDER_HZ)
        # V0.6.2: water is its own lightweight GPU batch. Flow can update at
        # 30 Hz without rebuilding rain/wind/plants/fire geometry each time.
        self._water_cache = tuple()
        self._water_gpu_batch = None
        self._water_gpu_uploads = 0
        self._water_cache_time = 0.0
        self._water_cache_generation = 0
        self._water_render_interval = 1.0 / max(1.0, PERF_WATER_RENDER_HZ)
        # 60 Hz visual interpolation over the 30 Hz authoritative liquid grid.
        # This removes the visible "one tile, pause, one tile" waterfall step
        # without doubling water physics work.
        self._water_visual_amounts = {}
        self._water_visual_time = time.monotonic()
        # FIX39: underwater ambience is render-only and bounded.  Anchor
        # discovery runs a few times per second; bubbles/grass never become
        # simulation entities, so swimming does not add physics workload.
        self._underwater_anchor_cache = (tuple(), tuple())
        self._underwater_anchor_key = None
        self._underwater_anchor_time = 0.0
        self._underwater_vfx_cache = tuple()
        self._underwater_vfx_cache_time = 0.0
        self._underwater_vfx_interval = 1.0 / 30.0
        # FIX28 global minimap. Terrain is sampled at a deliberately coarse
        # resolution and cached. Only player/camera markers are recomputed at
        # render cadence. This avoids scanning a 768x64 world every frame.
        self._minimap_runs = tuple()
        self._minimap_cache_time = 0.0
        self._minimap_cache_revision = None
        self._minimap_cache_size = (0, 0)
        self._minimap_cache_epoch = None
        self._minimap_sample_cols = 96
        self._minimap_sample_rows = 16
        # V0.6.2.5: PytoUI may report a default full-screen size for newly
        # created nested Views before the first root layout.  The HUD monitor
        # must not size HP/ST/MP fills until layout has established the small
        # bar frames, otherwise the child fill can become screen-sized and,
        # because UIKit subviews do not clip by default, cover the Metal view.
        self._ui_layout_ready = False
        self._monitor_thread = threading.Thread(
            target=self._monitor, name='MetalRPGMonitor', daemon=True
        )
        self._monitor_thread.start()

    def _make_bar(self, name, fill_color):
        bg = ui.View()
        bg.background_color = ui.Color.rgb(0.08, 0.10, 0.13, 0.92)
        bg.corner_radius = 4
        bg.border_width = 1
        bg.border_color = ui.Color.rgb(0.04, 0.05, 0.07, 1.0)
        self.root.add_subview(bg)

        # Start every HUD view tiny.  Some PytoUI builds give a newly-created
        # View a screen-sized default frame until its first explicit layout.
        bg.frame = (0.0, 0.0, 1.0, 1.0)
        try:
            bg.clips_to_bounds = True
        except Exception:
            try:
                bg.objc_view.clipsToBounds = True
            except Exception:
                pass

        fill = ui.View()
        fill.background_color = fill_color
        fill.corner_radius = 3
        fill.user_interaction_enabled = False
        fill.frame = (0.0, 0.0, 1.0, 1.0)
        bg.add_subview(fill)

        label = ui.Label(name)
        label.text_color = ui.Color.rgb(1, 1, 1, 1)
        label.font = ui.Font.bold_system_font_of_size(8)
        label.user_interaction_enabled = False
        bg.add_subview(label)
        return bg, fill, label

    @staticmethod
    def _update_bar(bg, fill, label, name, value, maximum):
        ratio = max(0.0, min(1.0, float(value) / max(1e-9, float(maximum))))
        # Hard safety clamp.  The designed HUD bars are <=145x15 points.
        # Never trust a transient pre-layout PytoUI width/height here.
        raw_w = float(getattr(bg, "width", 1.0) or 1.0)
        raw_h = float(getattr(bg, "height", 1.0) or 1.0)
        safe_w = max(1.0, min(145.0, raw_w))
        safe_h = max(1.0, min(15.0, raw_h))
        inner = max(0.0, safe_w - 4.0)
        fill.frame = (2.0, 2.0, inner * ratio, max(1.0, safe_h - 4.0))
        label.text = "%s %d" % (name, int(value))

    @staticmethod
    def _update_boss_bar(bg, fill, label, name, value, maximum):
        maximum=max(1.0,float(maximum));value=max(0.0,min(maximum,float(value)))
        ratio=value/maximum
        raw_w=max(1.0,min(460.0,float(getattr(bg,"width",1.0) or 1.0)))
        raw_h=max(1.0,min(24.0,float(getattr(bg,"height",1.0) or 1.0)))
        fill.frame=(2.0,2.0,max(0.0,raw_w-4.0)*ratio,max(1.0,raw_h-4.0))
        label.text="BOSS %s  %d / %d" % (str(name),int(value),int(maximum))

    def set_inventory_render_mode(self, enabled):
        """Switch MTKView between continuous and on-demand rendering.

        The Python-side desired mode changes immediately for request filtering.
        UIKit receives at most one pending pause/resume transaction; if the user
        opens and closes the backpack repeatedly before it runs, that single
        transaction applies only the newest state.
        """
        enabled = bool(enabled)
        self._inventory_render_mode = enabled
        self._inventory_render_serial += 1
        with self._inventory_mode_apply_lock:
            self._inventory_mode_apply_latest = enabled
            if self._inventory_mode_apply_pending:
                return True
            self._inventory_mode_apply_pending = True

        def _apply_latest():
            with self._inventory_mode_apply_lock:
                desired = bool(self._inventory_mode_apply_latest)
                self._inventory_mode_apply_pending = False
            try:
                view = self.metal.objc_view
                if desired:
                    # request_draw() owns explicit modal frames.
                    try:
                        view.enableSetNeedsDisplay = True
                    except Exception:
                        pass
                    view.paused = True
                else:
                    try:
                        view.enableSetNeedsDisplay = False
                    except Exception:
                        pass
                    view.paused = False
            except Exception as exc:
                print("INVENTORY Metal mode warning:", repr(exc))

        try:
            mainthread.run_async(_apply_latest)
            return True
        except Exception:
            with self._inventory_mode_apply_lock:
                self._inventory_mode_apply_pending = False
            return False

    def request_draw(self, force=False):
        """Request a coalesced explicit frame.

        Continuous gameplay normally uses MTKView's display link.  During the
        modal backpack or no-VSync fallback, FIX66 permits at most one queued
        main-thread draw task.  Later requests mark that task dirty so the
        newest snapshot is drawn without accumulating Objective-C callbacks.
        """
        if not bool(force) and not bool(self._inventory_render_mode):
            return False

        with self._draw_request_lock:
            if self._draw_request_pending:
                self._draw_request_dirty = True
                return True
            self._draw_request_pending = True
            self._draw_request_dirty = False

        modal = bool(self._inventory_render_mode)
        modal_serial = int(self._inventory_render_serial)

        def _request():
            schedule_latest = False
            try:
                # A close can happen before this queued main-thread draw runs.
                # Never re-pause MTKView with a stale modal draw request.
                if modal and (
                    not bool(self._inventory_render_mode)
                    or modal_serial != int(self._inventory_render_serial)
                ):
                    return
                view = self.metal.objc_view
                if modal:
                    try:
                        view.enableSetNeedsDisplay = True
                    except Exception:
                        pass
                    try:
                        view.paused = True
                    except Exception:
                        pass
                    try:
                        view.draw()
                        return
                    except Exception:
                        try:
                            view.setNeedsDisplay()
                            return
                        except Exception:
                            pass
                elif bool(force):
                    try:
                        view.draw()
                        return
                    except Exception:
                        try:
                            view.setNeedsDisplay()
                            return
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                with self._draw_request_lock:
                    schedule_latest = bool(self._draw_request_dirty)
                    self._draw_request_pending = False
                    self._draw_request_dirty = False
                # If data changed while this task was executing, enqueue only
                # one follow-up draw.  request_draw applies the same gate again.
                if schedule_latest and (bool(self._inventory_render_mode) or bool(force)):
                    self.request_draw(force=force)

        try:
            mainthread.run_async(_request)
            return True
        except Exception:
            with self._draw_request_lock:
                self._draw_request_pending = False
                self._draw_request_dirty = False
            return False

    def layout(self, w, h):
        self.viewport_w = max(1.0, float(w))
        self.viewport_h = max(1.0, float(h))
        self.metal.frame = (0, 0, self.viewport_w, self.viewport_h)

        bar_w = min(145.0, self.viewport_w * 0.18)
        bar_h = 15.0
        for index, (bg, _fill, label) in enumerate((
            (self.hp_bg, self.hp_fill, self.hp_text),
            (self.st_bg, self.st_fill, self.st_text),
            (self.mp_bg, self.mp_fill, self.mp_text),
        )):
            y = 9.0 + index * 19.0
            bg.frame = (12.0, y, bar_w, bar_h)
            label.frame = (6.0, 0.0, bar_w - 10.0, bar_h)

        # Immediately size child fills after the parent bars receive their
        # real frames.  This closes the startup race before the monitor thread
        # is allowed to touch the HUD.
        try:
            p = self.game.player
            self._update_bar(self.hp_bg, self.hp_fill, self.hp_text, "HP", p.hp, MAX_HP)
            self._update_bar(self.st_bg, self.st_fill, self.st_text, "ST", p.stamina, MAX_STAMINA)
            self._update_bar(self.mp_bg, self.mp_fill, self.mp_text, "MP", p.mana, MAX_MANA)
        except Exception:
            pass
        ipad=is_ipad()
        boss_w=min(520.0 if ipad else 420.0,max(300.0 if ipad else 260.0,self.viewport_w*0.46))
        self.boss_bg.frame=((self.viewport_w-boss_w)*0.5,54.0 if ipad else 48.0,boss_w,22.0 if ipad else 20.0)
        self.boss_text.frame=(5.0,0.0,boss_w-10.0,22.0 if ipad else 20.0)
        self._ui_layout_ready = True
        # A real-sized MTKView now exists. Kick one frame so the delegate can
        # begin producing display pulses even on Pyto builds where automatic
        # MTKView cadence starts late.
        self.request_draw(force=True)

        debug_x = bar_w + 28.0
        debug_w = max(250.0, min(500.0, self.viewport_w - debug_x - 176.0))
        self.status.frame = (
            debug_x,
            8.0,
            debug_w,
            72.0,
        )
        diag_w = min(360.0, max(240.0, self.viewport_w * 0.36))
        self.runtime_diag.frame = (
            (self.viewport_w - diag_w) * 0.5,
            8.0,
            diag_w,
            34.0,
        )

        movement_w = max(
            310.0,
            min(
                560.0,
                self.viewport_w
                - debug_x
                - 176.0,
            ),
        )

        self.movement_status.frame = (
            debug_x,
            84.0,
            movement_w,
            122.0,
        )

        message_w = min(
            430.0,
            self.viewport_w * 0.48,
        )
        self.message_label.frame = (
            (
                self.viewport_w
                - message_w
            )
            * 0.5,
            84.0,
            message_w,
            26.0,
        )

    @staticmethod
    def _quad(cx, cy, width, height, rgba):
        r, g, b, a = rgba
        return (
            float(cx), float(cy), float(width) * 0.5, float(height) * 0.5,
            float(r), float(g), float(b), float(a),
        )

    @staticmethod
    def _inventory_layout(viewport_w, viewport_h):
        w=max(1.0,float(viewport_w));h=max(1.0,float(viewport_h))
        pw=min(680.0,w-36.0);ph=min(330.0,h-28.0)
        px=(w-pw)*.5;py=(h-ph)*.5
        gx=px+14.0;gy=py+47.0;gw=pw*.68;gap=5.0
        sw=(gw-gap*5.0)/6.0;sh=(ph-63.0-gap*3.0)/4.0
        rx=gx+gw+14.0;rw=pw-(rx-px)-12.0
        return px,py,pw,ph,gx,gy,gw,sw,sh,gap,rx,rw

    @staticmethod
    def _inventory_action_metrics(panel_h):
        rh=max(1.0,float(panel_h)-61.0)
        detail_h=max(32.0,min(64.0,rh*.24))
        action_start=detail_h+18.0
        button_h=max(14.0,min(32.0,(rh-action_start-20.0)/5.0))
        equip_y=action_start+button_h+8.0
        step=button_h+4.0
        return detail_h,button_h,(action_start,equip_y,equip_y+step,equip_y+step*2.0,equip_y+step*3.0)

    def _inventory_text(self, out, text, screen_x, screen_y, scale, rgba, camera_x, camera_y):
        x=float(screen_x);y=float(screen_y);s=max(.65,float(scale))
        for char in str(text or "").upper():
            rows=_INV_FONT.get(char,_INV_FONT[" "])
            for row,bits in enumerate(rows):
                start=None
                for col in range(4):
                    lit=(col<3 and bits[col]=="1")
                    if lit and start is None:start=col
                    if (not lit) and start is not None:
                        width=(col-start)*s
                        out.append(self._quad(
                            float(camera_x)+x+(start*s)+width*.5,
                            float(camera_y)+y+row*s+s*.5,
                            width+.08,s+.08,rgba,
                        ));start=None
            x+=4.0*s

    def _inventory_frame(self, out, x, y, w, h, rgba, camera_x, camera_y, thickness=1.4):
        t=max(.8,float(thickness));cx=float(camera_x);cy=float(camera_y)
        out.extend((
            self._quad(cx+x+w*.5,cy+y+t*.5,w,t,rgba),
            self._quad(cx+x+w*.5,cy+y+h-t*.5,w,t,rgba),
            self._quad(cx+x+t*.5,cy+y+h*.5,t,h,rgba),
            self._quad(cx+x+w-t*.5,cy+y+h*.5,t,h,rgba),
        ))

    def _inventory_icon(self, out, weapon, cx, cy, camera_x, camera_y):
        """Low-quad pixel silhouettes; constant cost and no UIImage decoding."""
        x=float(camera_x)+float(cx);y=float(camera_y)+float(cy)
        dark=(.09,.13,.18,1.0);steel=(.68,.79,.88,1.0);edge=(.90,.98,1.0,1.0)
        gold=(.94,.63,.17,1.0);cyan=(.20,.86,1.0,1.0);brown=(.47,.26,.12,1.0)
        def q(dx,dy,w,h,c):out.append(self._quad(x+dx,y+dy,w,h,c))
        wid=str(weapon or "")
        if append_boss_icon(self, out, self.game, wid, x, y, span=34.0):
            return
        equipment_kind=self._equipment_visual_kind(wid)
        if wid in ("tool_grapple","grapple"):
            rope=(.72,.50,.24,1.0)
            for dx,dy in ((-8,6),(-5,8),(-1,8),(3,6),(5,3)):
                q(dx,dy,4.0,3.2,rope)
            q(6,-1,3.2,8.0,steel);q(6,-6,7.0,3.2,edge)
            q(1.5,-7.5,3.2,7.0,steel);q(10.5,-7.5,3.2,7.0,steel);return
        if equipment_kind=="double_jump":
            q(0,3,21,7,(.18,.49,.88,1.0));q(2,7,23,2.5,dark)
            q(-9,-1,5,8,(.78,.95,1.0,1.0));q(-12,-5,4,5,edge)
            q(7,1,4,3,cyan);return
        if equipment_kind=="air_dash":
            q(0,3,21,7,(.88,.26,.17,1.0));q(2,7,23,2.5,dark)
            q(-11,3,4,5,steel);q(-15,3,6,3,(1.0,.53,.08,.90))
            q(-18,3,3,2,(1.0,.92,.36,1.0));return
        if equipment_kind=="hover":
            q(0,1,20,6,(.55,.38,.86,1.0));q(0,5,24,3,cyan)
            q(-7,9,7,2,(.52,.95,1.0,.62));q(7,9,7,2,(.52,.95,1.0,.62));return
        if equipment_kind=="wind_wings":
            for side in (-1,1):
                for i in range(4):
                    q(side*(4+i*3), -2-i*2, 5, 11-i, cyan if i%2 else edge)
            q(0,2,5,10,gold);return
        if equipment_kind=="shield_armor":
            q(0,0,13,20,(.24,.43,.60,1.0));q(0,0,5,7,(.68,.96,1.0,1.0))
            q(-11,0,4,23,(.33,.86,.96,.45));q(11,0,4,23,(.33,.86,.96,.45))
            q(-13,0,2,23,edge);q(13,0,2,23,edge);return
        if equipment_kind=="electric_mouse":
            q(0,3,18,10,EQUIPMENT_ELECTRIC_RGBA)
            q(-6,-7,5,15,EQUIPMENT_ELECTRIC_RGBA);q(-6,-14,5,5,EQUIPMENT_ELECTRIC_TIP_RGBA)
            q(7,-7,5,17,EQUIPMENT_ELECTRIC_RGBA);q(7,-15,5,5,EQUIPMENT_ELECTRIC_TIP_RGBA)
            q(7,5,4,4,EQUIPMENT_CHEEK_RGBA);return
        if equipment_kind=="sky_puppy":
            q(0,2,17,11,EQUIPMENT_SKY_RGBA);q(-13,1,12,6,EQUIPMENT_SKY_RGBA)
            q(13,1,12,6,EQUIPMENT_SKY_RGBA);q(-18,3,4,4,EQUIPMENT_SKY_BLUE_RGBA)
            q(18,3,4,4,EQUIPMENT_SKY_BLUE_RGBA);q(5,0,3,3,(.16,.30,.38,1.0));return
        if wid=="flying_drone":
            q(0,0,18,8,dark);q(0,-1,9,7,cyan);q(-12,-6,16,2,steel);q(12,-6,16,2,steel)
            q(-12,-6,3,7,cyan);q(12,-6,3,7,cyan);q(0,5,4,4,gold);return
        if wid=="energy_bow":
            for off in (-10,-6,-2,2,6,10):q(abs(off)*.26-3,off,3,3,cyan)
            q(1,0,2,24,edge);q(7,0,12,2,cyan);return
        if wid=="whip":
            for i in range(8):q(-11+i*3.0,7-math.sin(i*.55)*12,3.2,3.2,brown)
            q(-13,9,5,8,gold);return
        if wid=="laser_gun":
            q(0,-2,24,8,dark);q(5,-2,12,4,cyan);q(-6,6,7,10,brown);q(14,-2,4,4,edge);return
        if wid=="yoyo":
            for i in range(6):q(-8+i*2.5,-8+i*2.2,2.4,2.4,steel)
            q(7,7,15,15,dark);q(7,7,11,11,(.72,.20,.78,1.0));q(7,7,4,4,edge);return
        if wid=="battle_top":
            q(0,-8,5,5,gold);q(0,-3,17,5,edge);q(0,2,23,8,(.85,.28,.12,1.0))
            q(0,7,13,5,steel);q(0,12,3,6,gold);return
        if wid=="rpg_launcher":
            q(0,-3,29,9,dark);q(2,-4,24,5,(.30,.52,.32,1.0));q(15,-4,5,10,edge)
            q(-11,5,7,12,brown);q(-4,5,8,4,gold);return
        if wid=="tnt":
            red=(.86,.10,.05,1.0);red_dark=(.55,.05,.03,1.0)
            q(-6,1,6,19,red_dark);q(0,0,7,21,red);q(6,1,6,19,red_dark)
            q(0,2,20,4,dark);q(3,-12,3,7,brown);q(7,-15,5,5,gold);return
        if wid=="battle_axe":
            q(-5,3,4,26,brown);q(3,-8,15,9,steel);q(7,-8,5,13,edge);return
        if wid=="war_hammer":
            q(-4,4,4,25,brown);q(2,-8,23,10,steel);q(13,-8,4,12,edge);return
        length=28 if wid in ("greatsword","spear") else (19 if wid=="dagger" else 23)
        thick=4 if wid=="greatsword" else (2.5 if wid=="spear" else 3)
        q(0,-2,length,thick,steel);q(length*.48,-2,4,4,edge);q(-length*.46,-2,7,5,brown)
        if wid!="spear":q(-length*.30,-2,3,11,gold)

    def _build_inventory_overlay(self, inventory_snapshot, page, base_snapshot):
        snap=dict(inventory_snapshot or {});base=dict(base_snapshot or {})
        vw=float(base.get('viewport_w',self.viewport_w));vh=float(base.get('viewport_h',self.viewport_h))
        camera_x=float(base.get('camera_x',0.0));camera_y=float(base.get('camera_y',0.0))
        px,py,pw,ph,gx,gy,gw,sw,sh,gap,rx,rw=self._inventory_layout(vw,vh)
        detail_h,button_h,action_y=self._inventory_action_metrics(ph)
        right_y=py+47.0
        out=[]
        def sq(cx,cy,w,h,c):out.append(self._quad(camera_x+cx,camera_y+cy,w,h,c))
        sq(vw*.5,vh*.5,vw+4.0,vh+4.0,(.015,.02,.03,.82))
        sq(px+pw*.5,py+ph*.5,pw,ph,(.055,.075,.105,.985))
        self._inventory_frame(out,px,py,pw,ph,(.26,.52,.68,1.0),camera_x,camera_y,2.0)
        pages=max(1,(len(snap.get('slots',()) or ())+23)//24);page=max(0,min(pages-1,int(page)))
        self._inventory_text(out,"BAG",px+16,py+13,2.3,(.88,.96,1.0,1.0),camera_x,camera_y)
        self._inventory_text(out,"<",px+128,py+13,2.3,(.48,.86,1.0,1.0),camera_x,camera_y)
        self._inventory_text(out,"%d/%d"%(page+1,pages),px+163,py+15,1.8,(.92,.95,1.0,1.0),camera_x,camera_y)
        self._inventory_text(out,">",px+232,py+13,2.3,(.48,.86,1.0,1.0),camera_x,camera_y)
        self._inventory_text(out,"X",px+pw-35,py+13,2.4,(1.0,.52,.50,1.0),camera_x,camera_y)

        slots=list(snap.get('slots',()) or ());selected=int(snap.get('selected_index',0));move=snap.get('move_source')
        equipped=str(getattr(self.game.melee,'selected_item_id',''))
        equipment_system=getattr(self.game,"equipment",None)
        try:
            equipped_wearables=set(v for v in self._equipped_visual_ids(self.game).values() if v)
        except Exception:
            equipped_wearables=set()
        start=page*24
        for local in range(24):
            col=local%6;row=local//6;index=start+local
            bx=gx+col*(sw+gap);by=gy+row*(sh+gap)
            chosen=index==selected;moving=move is not None and index==int(move)
            fill=(.18,.14,.07,.98) if chosen else ((.055,.17,.25,.98) if moving else (.075,.095,.125,.96))
            border=(1.0,.72,.22,1.0) if chosen else ((.25,.72,1.0,1.0) if moving else (.20,.27,.34,1.0))
            sq(bx+sw*.5,by+sh*.5,sw,sh,fill)
            self._inventory_frame(out,bx,by,sw,sh,border,camera_x,camera_y,1.35)
            self._inventory_text(out,str(index+1),bx+4,by+4,1.05,(.58,.67,.75,1.0),camera_x,camera_y)
            stack=slots[index] if 0<=index<len(slots) else None
            if stack:
                item_id=str(stack.get('item_id',''))
                wid=weapon_from_item(item_id)
                if wid:self._inventory_icon(out,wid,bx+sw*.52,by+sh*.53,camera_x,camera_y)
                elif self._equipment_visual_kind(item_id):
                    self._inventory_icon(out,item_id,bx+sw*.52,by+sh*.53,camera_x,camera_y)
                elif item_id == "tool_grapple":
                    self._inventory_icon(out,item_id,bx+sw*.52,by+sh*.53,camera_x,camera_y)
                else:
                    seed=sum(ord(c) for c in item_id)
                    color=(.30+(seed%5)*.08,.42+((seed//5)%4)*.08,.45+((seed//19)%4)*.08,1.0)
                    sq(bx+sw*.52,by+sh*.54,24.0,24.0,color)
                    sq(bx+sw*.52,by+sh*.50,14.0,14.0,(.72,.86,.90,1.0))
                count=max(0,int(stack.get('count',0)))
                # Keep the GPU batch strictly bounded even if imported save
                # data contains an abnormally large stack count.
                if count>1:self._inventory_text(out,"X%d"%min(999,count),bx+sw-30,by+sh-12,1.0,(1.0,1.0,1.0,1.0),camera_x,camera_y)
                item_equipped=(item_id==equipped or item_id.lower() in equipped_wearables)
                is_equipped=getattr(equipment_system,"is_equipped",None)
                if not item_equipped and callable(is_equipped):
                    try:item_equipped=bool(is_equipped(item_id))
                    except Exception:pass
                if item_equipped:
                    self._inventory_text(out,"E",bx+sw-11,by+4,1.1,(.40,1.0,.58,1.0),camera_x,camera_y)

        qty=max(1,int(snap.get('selected_quantity',1)))
        self._inventory_text(out,"QTY %d"%min(999,qty),rx,right_y+detail_h+4.0,1.25,(.82,.92,1.0,1.0),camera_x,camera_y)
        third=(rw-8.0)/3.0
        equip_gap=4.0;equip_half=(rw-equip_gap)*.5
        buttons=(("-",action_y[0],third,(.12,.19,.25,1.0),rx),
                 ("+",action_y[0],third,(.12,.19,.25,1.0),rx+third+4.0),
                 ("MAX",action_y[0],third,(.12,.19,.25,1.0),rx+(third+4.0)*2.0),
                 ("EQUIP",action_y[1],equip_half,(.48,.34,.08,1.0),rx),
                 ("UNEQUIP",action_y[1],equip_half,(.34,.18,.08,1.0),rx+equip_half+equip_gap),
                 ("MOVE",action_y[2],rw,(.10,.30,.45,1.0),rx),
                 ("DROP",action_y[3],rw,(.10,.28,.48,1.0),rx),
                 ("DEL",action_y[4],rw,(.46,.12,.15,1.0),rx))
        for label,off,bw,color,bx in buttons:
            by=right_y+off;sq(bx+bw*.5,by+button_h*.5,bw,button_h,color)
            self._inventory_frame(out,bx,by,bw,button_h,(.42,.55,.65,1.0),camera_x,camera_y,1.0)
            text_scale=max(.62,min(1.35,button_h/23.7,(bw-8.0)/max(4.0,len(label)*4.0)))
            text_w=len(label)*4*text_scale
            self._inventory_text(out,label,bx+(bw-text_w)*.5,by+(button_h-5*text_scale)*.5,text_scale,(1.0,1.0,1.0,1.0),camera_x,camera_y)
        return tuple(out[:METAL_DYNAMIC_MAX_QUADS])

    def publish_inventory_overlay(self, inventory_snapshot, page=0):
        base=self.state.get_snapshot()
        if base is None:return False
        # Use the live viewport/camera rather than the paused snapshot.  This
        # is essential when iPad Stage Manager resizes an open backpack.
        projection={
            "viewport_w":float(self.viewport_w),
            "viewport_h":float(self.viewport_h),
            "camera_x":float(getattr(self.game,"camera_x",base.get("camera_x",0.0))),
            "camera_y":float(getattr(self.game,"camera_y",base.get("camera_y",0.0))),
        }
        build_base=dict(base);build_base.update(projection)
        quads=self._build_inventory_overlay(inventory_snapshot,page,build_base)
        self.state.set_inventory_overlay(quads,projection=projection)
        self.request_draw()
        return True

    def clear_inventory_overlay(self):
        self.state.set_inventory_overlay(())
        self.request_draw()

    def set_hud_hidden(self, hidden):
        self._inventory_hud_hidden=bool(hidden)
        for view in (self.hp_bg,self.st_bg,self.mp_bg,self.boss_bg,self.status,
                     self.runtime_diag,self.movement_status,self.message_label):
            try:view.hidden=bool(hidden)
            except Exception:pass
        if not hidden:
            self.hp_bg.hidden=False;self.st_bg.hidden=False;self.mp_bg.hidden=False
            # FIX71 diagnostic/build proof stays permanently off-screen.
            self.runtime_diag.hidden=True
            self.status.hidden=True
            self.movement_status.hidden=True

    def _pixel_asset_quads(
        self, game, asset_id, left, top, width, height,
        clip_top=0.0, clip_bottom=1.0, flip_x=False,
        animation=None, animation_time=0.0, frame_index=None,
        visual_scale=1.0, rotation_radians=0.0,
    ):
        """Render AssetEditor pixels through the existing quad GPU path.

        FIX20 adds a fixed-density mapping: every authored pixel is exactly
        PIXEL_WORLD_SCALE world pixels regardless of creature/object size.
        Width/height now locate the world anchor only; they never stretch new
        pixel sources. Legacy FIX19 sources keep the former normalized-square
        path until the editor migrates/saves them.
        """
        assets=getattr(game,"assets",None)
        if assets is None or not hasattr(assets,"pixel_rects"):
            return None
        data=None
        if animation and hasattr(assets,"pixel_animation_rects"):
            data=assets.pixel_animation_rects(
                str(asset_id),str(animation),
                time_seconds=float(animation_time),frame_index=frame_index,
            )
        if data is None:
            data=assets.pixel_rects(str(asset_id))
        if data is None:
            return None

        size=max(1.0,float(data.get("size",1) or 1))
        try:visual_scale=max(0.25,min(3.0,float(visual_scale or 1.0)))
        except Exception:visual_scale=1.0
        try:rotation_radians=float(rotation_radians or 0.0)
        except Exception:rotation_radians=0.0
        rot_cos=math.cos(rotation_radians);rot_sin=math.sin(rotation_radians)
        ctop=max(0.0,min(1.0,float(clip_top)))
        cbottom=max(ctop,min(1.0,float(clip_bottom)))
        out=[]
        overlap=0.04

        # FIX111: logical artwork may explicitly fit a gameplay/world box.
        # Source resolution and actor/effect size are independent: one logical
        # pixel stays one editor cell while this mapping controls only the
        # world-space quad size.
        if str(data.get("pixel_mapping", "")) == PIXEL_MAPPING_FIT:
            category=str(data.get("category","") or "")
            mapping_category="player" if category=="equipment" else category
            anchor_x,anchor_y=world_anchor_xy(mapping_category,left,top,width,height)
            clip_y0=ctop*size; clip_y1=cbottom*size
            _rects=tuple(data.get("rects",()) or ())
            _trim=(str(data.get("world_fit","") or "") in ("actor_bounds_trimmed","feet_trimmed")) and bool(_rects)
            if _trim:
                _minx=min(r[0] for r in _rects);_maxx=max(r[2] for r in _rects);_miny=min(r[1] for r in _rects);_maxy=max(r[3] for r in _rects)
                _sw=max(1e-6,float(_maxx-_minx));_sh=max(1e-6,float(_maxy-_miny))
            for x0,y0,x1,y1,rgba in _rects:
                px0=float(x0); px1=float(x1)
                py0=max(clip_y0,float(y0)); py1=min(clip_y1,float(y1))
                if py1<=py0:continue
                if _trim:
                    _ax0=(px0-_minx)/_sw;_ax1=(px1-_minx)/_sw
                    if flip_x:_ax0,_ax1=1.-_ax1,1.-_ax0
                    _ay0=(py0-_miny)/_sh;_ay1=(py1-_miny)/_sh
                    wx0=left+_ax0*width;wx1=left+_ax1*width;wy0=top+_ay0*height;wy1=top+_ay1*height
                else:
                    wx0,wy0,wx1,wy1=fit_rect_to_world(
                        size,left,top,width,height,px0,py0,px1,py1,flip_x=flip_x,
                    )
                if wx1<=wx0 or wy1<=wy0:continue
                rcx=(wx0+wx1)*0.5; rcy=(wy0+wy1)*0.5
                rw=(wx1-wx0)*visual_scale; rh=(wy1-wy0)*visual_scale
                off_x=(rcx-anchor_x)*visual_scale; off_y=(rcy-anchor_y)*visual_scale
                rcx=anchor_x+off_x*rot_cos-off_y*rot_sin
                rcy=anchor_y+off_x*rot_sin+off_y*rot_cos
                if abs(rotation_radians)>1e-6:
                    rw,rh=(abs(rot_cos)*rw+abs(rot_sin)*rh,
                           abs(rot_sin)*rw+abs(rot_cos)*rh)
                out.append(self._quad(rcx,rcy,rw+overlap,rh+overlap,rgba))
            return tuple(out)

        # FIX20 fixed world-pixel density. The source pivot is determined by
        # category. Terrain uses top-left; actors/plants use bottom-centre feet;
        # any future center-anchored source uses its center.
        if str(data.get("pixel_mapping", "")) == PIXEL_MAPPING_FIXED:
            # The scale/pivot live in engine.pixel_art so editor and renderer
            # cannot drift apart. Metadata records the mapping but never sets a
            # per-object scale.
            category=str(data.get("category","") or "")
            # FIX67 wearable sources share player.default's 24px canvas and
            # declare a player-feet overlay anchor.  The shared pixel helper
            # predates the equipment category, so map it to the existing
            # player pivot here instead of treating it as a centre projectile.
            mapping_category="player" if category=="equipment" else category
            anchor_x,anchor_y=world_anchor_xy(mapping_category,left,top,width,height)
            clip_y0=ctop*size; clip_y1=cbottom*size
            for x0,y0,x1,y1,rgba in data.get("rects",()):
                px0=float(x0); px1=float(x1)
                py0=max(clip_y0,float(y0)); py1=min(clip_y1,float(y1))
                if py1<=py0:continue
                wx0,wy0,wx1,wy1=fixed_rect_to_world(
                    mapping_category,size,left,top,width,height,
                    px0,py0,px1,py1,flip_x=flip_x,
                )
                if wx1<=wx0 or wy1<=wy0:continue
                rcx=(wx0+wx1)*0.5;rcy=(wy0+wy1)*0.5
                rw=(wx1-wx0)*visual_scale;rh=(wy1-wy0)*visual_scale
                off_x=(rcx-anchor_x)*visual_scale;off_y=(rcy-anchor_y)*visual_scale
                rcx=anchor_x+off_x*rot_cos-off_y*rot_sin
                rcy=anchor_y+off_x*rot_sin+off_y*rot_cos
                # Instance quads are axis-aligned.  Rotate the authored run's
                # centre and use its tight AABB; thin projectile cores therefore
                # follow vx/vy without adding a second mesh or draw call.
                if abs(rotation_radians)>1e-6:
                    rw,rh=(abs(rot_cos)*rw+abs(rot_sin)*rh,
                           abs(rot_sin)*rw+abs(rot_cos)*rh)
                out.append(self._quad(
                    rcx,rcy,rw+overlap,rh+overlap,rgba,
                ))
            return tuple(out)

        # Legacy FIX19 normalized-square mapping. Kept deliberately for old
        # projects that have not yet opened/saved the asset in FIX20.
        span=max(float(width),float(height),1.0)*visual_scale
        cx_world=float(left)+float(width)*0.5
        cy_world=float(top)+float(height)*0.5
        canvas_left=cx_world-span*0.5
        canvas_top=cy_world-span*0.5
        for x0,y0,x1,y1,rgba in data.get("rects",()):
            ny0=max(ctop,float(y0)/size)
            ny1=min(cbottom,float(y1)/size)
            if ny1<=ny0:continue
            nx0=float(x0)/size; nx1=float(x1)/size
            if flip_x:nx0,nx1=1.0-nx1,1.0-nx0
            wx0=canvas_left+nx0*span; wx1=canvas_left+nx1*span
            wy0=canvas_top+ny0*span; wy1=canvas_top+ny1*span
            if wx1<=wx0 or wy1<=wy0:continue
            rcx=(wx0+wx1)*0.5;rcy=(wy0+wy1)*0.5
            rw=wx1-wx0;rh=wy1-wy0
            if abs(rotation_radians)>1e-6:
                off_x=rcx-cx_world;off_y=rcy-cy_world
                rcx=cx_world+off_x*rot_cos-off_y*rot_sin
                rcy=cy_world+off_x*rot_sin+off_y*rot_cos
                rw,rh=(abs(rot_cos)*rw+abs(rot_sin)*rh,
                       abs(rot_sin)*rw+abs(rot_cos)*rh)
            out.append(self._quad(
                rcx,rcy,rw+overlap,rh+overlap,rgba,
            ))
        return tuple(out)

    @staticmethod
    def _plant_asset_geometry(plant, bx, gy):
        """Return (asset_id,left,top,w,h) matching the normal stage geometry."""
        species = str(getattr(plant, "species", ""))
        if species == "moss" or species == "succession":
            # Environmental burn/regrow layers keep their original procedural
            # look; authored normal-state silhouette replaces only the base.
            if str(getattr(plant,"state","normal")) != "normal":return None
            level=max(1,min(3,int(getattr(plant,"stage",1))))
            aid="plant.moss" if species=="moss" else "plant.succession_"+{1:"low",2:"mid",3:"tree"}[level]
            w,h=(29.,8.) if species=="moss" else {1:(7.,12.),2:(22.,24.),3:(40.,54.)}[level]
            return (aid,float(bx)-w*.5,float(gy)-h,w,h)
        if species not in (
            "map_grass", "map_reed", "map_fern", "map_cactus",
            "map_tree", "map_jungle_tree", "map_pine",
        ):
            return None
        level = max(1, min(3, int(getattr(plant, "stage", 1))))
        # Stage-3 values exactly match AssetEditor builtin_world_size().
        full = {
            "map_grass": (21.5, 32.0),
            "map_reed": (22.0, 38.56),
            "map_fern": (21.0, 26.4),
            "map_cactus": (24.0, 46.0),
            "map_tree": (48.0, 73.4),
            "map_jungle_tree": (48.0, 73.4),
            "map_pine": (41.6, 70.0),
        }[species]
        stage_scale = {1: 0.46, 2: 0.70, 3: 1.0}[level]
        if species in ("map_tree", "map_jungle_tree", "map_pine"):
            stage_scale = {1: 0.44, 2: 0.69, 3: 1.0}[level]
        w = full[0] * stage_scale
        h = full[1] * stage_scale
        return ("plant." + species, float(bx) - w * 0.5, float(gy) - h, w, h)

    def _visible_chunk_coords(self, game, margin_tiles=2, camera_x=None, camera_y=None):
        margin = int(margin_tiles) * TILE_SIZE
        camera_x = float(game.camera_x if camera_x is None else camera_x)
        camera_y = float(game.camera_y if camera_y is None else camera_y)
        x1 = camera_x - margin
        y1 = camera_y - margin
        x2 = camera_x + float(game.viewport_w) + margin
        y2 = camera_y + float(game.viewport_h) + margin

        chunk_px = TILE_SIZE * CHUNK_SIZE
        min_cx = max(0, int(math.floor(x1 / chunk_px)))
        min_cy = max(0, int(math.floor(y1 / chunk_px)))
        max_cx = min(
            max(0, (game.world.width_tiles - 1) // CHUNK_SIZE),
            int(math.floor(max(x1, x2 - 1e-6) / chunk_px)),
        )
        max_cy = min(
            max(0, (game.world.height_tiles - 1) // CHUNK_SIZE),
            int(math.floor(max(y1, y2 - 1e-6) / chunk_px)),
        )

        for cy in range(min_cy, max_cy + 1):
            for cx in range(min_cx, max_cx + 1):
                yield cx, cy

    def _tile_render_batch(self, game, tx, ty):
        tile_id = game.world.get_tile(tx, ty)
        if tile_id == AIR:
            return tuple()

        td = tile_def(tile_id)
        wx = tx * TILE_SIZE
        wy = ty * TILE_SIZE
        body = _hex_rgba(td.color)
        topc = _hex_rgba(td.top_color)
        batch = []

        # FIX4: AssetEditor output is authoritative for edited tile assets.
        # Partial LOW/MID/HIGH terrain and partial ice clip the same source art
        # vertically, so gameplay geometry remains unchanged.
        asset_clip_top = 0.0
        asset_clip_bottom = 1.0
        if tile_id == ICE:
            try:
                _asset_mass = float(game.environment.state.ice_mass.get((tx, ty), td.water_mass))
            except Exception:
                _asset_mass = float(td.water_mass)
            _asset_ratio = ice_height_ratio_for_mass(_asset_mass)
            asset_clip_top = max(0.0, min(1.0, 1.0 - float(_asset_ratio)))
        elif tile_id in LAYERED_SOLID_TILES:
            try:
                _asset_mask = int(game.world.tile_layer_mask_at(tx, ty))
            except Exception:
                _asset_mask = SOIL_LAYER_FULL
            if _asset_mask <= 0:
                return tuple()
            asset_clip_top, asset_clip_bottom = soil_vertical_bounds(_asset_mask)

        custom_tile = self._pixel_asset_quads(
            game, "tile." + str(td.name),
            wx, wy, TILE_SIZE, TILE_SIZE,
            clip_top=asset_clip_top, clip_bottom=asset_clip_bottom,
        )
        if custom_tile is not None:
            return custom_tile

        if td.ladder:
            batch.append(self._quad(wx + TILE_SIZE * 0.33, wy + TILE_SIZE / 2, 5.0, TILE_SIZE, body))
            batch.append(self._quad(wx + TILE_SIZE * 0.67, wy + TILE_SIZE / 2, 5.0, TILE_SIZE, body))
            for frac in (0.22, 0.52, 0.82):
                batch.append(self._quad(wx + TILE_SIZE / 2, wy + TILE_SIZE * frac, TILE_SIZE * 0.38, 4.0, topc))
        elif tile_id == ICE:
            try:
                mass = float(game.environment.state.ice_mass.get((tx, ty), td.water_mass))
            except Exception:
                mass = float(td.water_mass)
            ratio = ice_height_ratio_for_mass(mass)
            if ratio > 0.0:
                try:
                    ice_rect = game.world.ice_rect_at(tx, ty)
                except Exception:
                    ice_rect = None
                if ice_rect is None:
                    left, top, right, bottom = wx, wy + TILE_SIZE*(1.0-ratio), wx + TILE_SIZE, wy + TILE_SIZE
                else:
                    left, top, right, bottom = ice_rect
                height = max(2.0, float(bottom) - float(top))
                center_y = float(bottom) - height * 0.5
                batch.append(self._quad(
                    wx + TILE_SIZE / 2,
                    center_y,
                    TILE_SIZE + TILE_RENDER_OVERLAP,
                    height + TILE_RENDER_OVERLAP,
                    body,
                ))
                batch.append(self._quad(
                    wx + TILE_SIZE / 2,
                    float(top) + 2.5,
                    TILE_SIZE * 0.90,
                    4.0,
                    topc,
                ))
        elif tile_id in LAYERED_SOLID_TILES:
            try:
                mask = int(game.world.tile_layer_mask_at(tx, ty))
            except Exception:
                mask = SOIL_LAYER_FULL
            if mask > 0:
                top_ratio, bottom_ratio = soil_vertical_bounds(mask)
                top_y = wy + TILE_SIZE * top_ratio
                bottom_y = wy + TILE_SIZE * bottom_ratio
                height = max(2.0, bottom_y - top_y)
                batch.append(self._quad(
                    wx + TILE_SIZE/2,
                    top_y + height*0.5,
                    TILE_SIZE + TILE_RENDER_OVERLAP,
                    height + TILE_RENDER_OVERLAP,
                    body,
                ))
                if tile_id == GRASS_DIRT or (td.top_color and td.top_color != td.color):
                    batch.append(self._quad(
                        wx + TILE_SIZE/2,
                        top_y + 3.5,
                        TILE_SIZE + TILE_RENDER_OVERLAP,
                        min(7.0, max(3.0, height*0.22)),
                        topc,
                    ))
        else:
            batch.append(self._quad(
                wx + TILE_SIZE / 2,
                wy + TILE_SIZE / 2,
                TILE_SIZE + TILE_RENDER_OVERLAP,
                TILE_SIZE + TILE_RENDER_OVERLAP,
                body,
            ))
            if td.top_color and td.top_color != td.color:
                batch.append(self._quad(
                    wx + TILE_SIZE / 2,
                    wy + 3.5,
                    TILE_SIZE + TILE_RENDER_OVERLAP,
                    7.0 + TILE_RENDER_OVERLAP,
                    topc,
                ))

        # FIX72: mineable materials retain the fixed five-slot terrain budget,
        # but no longer render as featureless checkerboard squares.  Detail is
        # clipped to the same fractional body bounds as collision, and unlike
        # horizontal neighbours add one staggered transition patch.
        if tile_id in LAYERED_SOLID_TILES and batch:
            if tile_id in LAYERED_SOLID_TILES:
                try:
                    detail_mask = int(game.world.tile_layer_mask_at(tx, ty))
                except Exception:
                    detail_mask = SOIL_LAYER_FULL
                detail_top_ratio, detail_bottom_ratio = soil_vertical_bounds(detail_mask)
            else:
                detail_top_ratio, detail_bottom_ratio = 0.0, 1.0
            detail_top = wy + TILE_SIZE * detail_top_ratio
            detail_bottom = wy + TILE_SIZE * detail_bottom_ratio

            def _terrain_neighbour(dx):
                ntx = int(tx) + int(dx)
                if ntx < 0 or ntx >= game.world.width_tiles:
                    return None
                nid = int(game.world.get_tile(ntx, ty))
                if nid not in LAYERED_SOLID_TILES:
                    return None
                ntd = tile_def(nid)
                try:
                    nmask = int(game.world.tile_layer_mask_at(ntx, ty))
                except Exception:
                    nmask = SOIL_LAYER_FULL
                if nmask <= 0:
                    return None
                ntop_ratio, nbottom_ratio = soil_vertical_bounds(nmask)
                return {
                    "name": str(ntd.name),
                    "color": _hex_rgba(ntd.color),
                    "top": wy + TILE_SIZE * ntop_ratio,
                    "bottom": wy + TILE_SIZE * nbottom_ratio,
                }

            slots_left = max(0, 5 - len(batch))
            for dcx, dcy, dw, dh, dcolor in terrain_detail_quads(
                td.name, tx, ty,
                wx, detail_top, wx + TILE_SIZE, detail_bottom,
                body, topc,
                left_neighbour=_terrain_neighbour(-1),
                right_neighbour=_terrain_neighbour(1),
                limit=slots_left,
            ):
                batch.append(self._quad(dcx, dcy, dw, dh, dcolor))
        return tuple(batch[:5])

    def _build_chunk_tile_cache(self, game, cx, cy):
        """Compatibility CPU cache: per-tile render batches."""
        tile_batches = [tuple() for _ in range(CHUNK_SIZE * CHUNK_SIZE)]
        start_tx = int(cx) * CHUNK_SIZE
        start_ty = int(cy) * CHUNK_SIZE
        end_tx = min(game.world.width_tiles, start_tx + CHUNK_SIZE)
        end_ty = min(game.world.height_tiles, start_ty + CHUNK_SIZE)
        for ty in range(start_ty, end_ty):
            ly = ty - start_ty
            for tx in range(start_tx, end_tx):
                lx = tx - start_tx
                tile_batches[ly * CHUNK_SIZE + lx] = self._tile_render_batch(game, tx, ty)
        return tuple(tile_batches)

    def _build_fixed_chunk_slots(self, game, cx, cy):
        """Return exactly five InstanceData slots per tile.

        Fixed slots make mining O(changed tiles): removing one block only zeros
        five 32-byte instances instead of compacting/reallocating the whole
        chunk buffer. Degenerate zero-size slots are essentially free for GPU.
        """
        slots_per_tile = 5
        zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        slots = [zero] * (CHUNK_SIZE * CHUNK_SIZE * slots_per_tile)
        start_tx = int(cx) * CHUNK_SIZE
        start_ty = int(cy) * CHUNK_SIZE
        end_tx = min(game.world.width_tiles, start_tx + CHUNK_SIZE)
        end_ty = min(game.world.height_tiles, start_ty + CHUNK_SIZE)
        for ty in range(start_ty, end_ty):
            ly = ty - start_ty
            for tx in range(start_tx, end_tx):
                lx = tx - start_tx
                tile_index = ly * CHUNK_SIZE + lx
                batch = self._tile_render_batch(game, tx, ty)
                base = tile_index * slots_per_tile
                for i, quad in enumerate(batch):
                    slots[base + i] = quad
        return slots

    @staticmethod
    def _copy_instance_buffer(src, dst, instance_count):
        if src is None or dst is None or instance_count <= 0:
            return
        sp = _ptr_value(_call0(src, 'contents'))
        dp = _ptr_value(_call0(dst, 'contents'))
        if not sp or not dp:
            raise RuntimeError('terrain MTLBuffer.contents returned null')
        ctypes.memmove(dp, sp, int(instance_count) * ctypes.sizeof(InstanceData))

    def _new_fixed_chunk_cache(self, game, cx, cy, revision):
        slots = self._build_fixed_chunk_slots(game, cx, cy)
        count = len(slots)
        size = int(count * ctypes.sizeof(InstanceData))
        buffers = []
        for _ in range(3):
            b = self.state.device.newBufferWithLength_options_(size, 0)
            if b is None:
                raise RuntimeError('terrain fixed MTLBuffer allocation returned nil')
            _write_instances(b, slots, count)
            buffers.append(b)
        game.world.clear_chunk_dirty_indices(cx, cy)
        return {
            'revision': revision,
            'buffers': tuple(buffers),
            'active': 0,
            'count': count,
        }

    def _authored_terrain_margin(self, game):
        # Fixed-density large tiles can extend beyond their anchor cell/chunk.
        # Read lightweight registry metadata once per registry generation.
        assets = getattr(game, 'assets', None)
        key = (id(assets), getattr(assets, 'signature', None))
        if getattr(self, '_terrain_margin_key', None) == key:
            return self._terrain_margin_tiles
        extent = float(TILE_SIZE)
        for aid, row in getattr(assets, 'bindings', {}).items():
            if not isinstance(row, dict) or not str(aid).startswith('tile.'):
                continue
            if not row.get('pixel_source'):
                continue
            try:
                size = int(row.get('pixel_size', 0) or 128)
            except (TypeError, ValueError):
                size = 128
            extent = max(extent, min(128, max(1, size)) * PIXEL_WORLD_SCALE)
        self._terrain_margin_key = key
        self._terrain_margin_tiles = max(2, int(math.ceil(extent/TILE_SIZE)) + 1)
        return self._terrain_margin_tiles

    def _visible_custom_map_terrain_payload(self, game, camera_x=None, camera_y=None):
        """Return cached editor-map terrain with O(changed-chunk) mining cost.

        V0.6.3 rebuilt every visible tile whenever *one* chunk revision changed.
        That made each shovel hit perform hundreds/thousands of Python
        get_tile/tile_def/tuple operations.  V0.6.4 keeps a per-chunk CPU
        render cache and patches only dirty local tile indices.
        """
        camera_x = float(game.camera_x if camera_x is None else camera_x)
        camera_y = float(game.camera_y if camera_y is None else camera_y)
        window = terrain_view_window(camera_x, camera_y, game.viewport_w, game.viewport_h, TILE_SIZE)
        coords = tuple(self._visible_chunk_coords(
            game, margin_tiles=self._authored_terrain_margin(game),
            camera_x=camera_x, camera_y=camera_y))
        epoch = int(getattr(game.world, 'cache_epoch', 0))
        assets = getattr(game, 'assets', None)
        generation = (id(game.world), epoch, id(assets), getattr(assets, 'signature', None))

        if getattr(self, '_terrain_generation', None) != generation:
            self._terrain_generation = generation
            self._custom_terrain_chunk_epoch = epoch
            self._custom_terrain_chunk_cache.clear()
            self._custom_terrain_cpu_key = None
            self._custom_terrain_cpu_quads = tuple()

        signature = []
        visible_set = set(coords)

        for cx, cy in coords:
            cx = int(cx)
            cy = int(cy)
            coord = (cx, cy)
            revision = int(game.world.chunk_revision(cx, cy))
            signature.append((cx, cy, revision))

            cached = self._custom_terrain_chunk_cache.get(coord)
            if cached is not None and int(cached['revision']) == revision:
                continue

            dirty = tuple(game.world.chunk_dirty_indices(cx, cy))

            if cached is not None and dirty:
                tile_batches = list(cached['tile_batches'])
                start_tx = cx * CHUNK_SIZE
                start_ty = cy * CHUNK_SIZE

                for local_index in dirty:
                    local_index = int(local_index)
                    if local_index < 0 or local_index >= CHUNK_SIZE * CHUNK_SIZE:
                        continue
                    lx = local_index % CHUNK_SIZE
                    ly = local_index // CHUNK_SIZE
                    tx = start_tx + lx
                    ty = start_ty + ly
                    if (
                        0 <= tx < game.world.width_tiles
                        and 0 <= ty < game.world.height_tiles
                    ):
                        tile_batches[local_index] = self._tile_render_batch(
                            game, tx, ty
                        )
                    else:
                        tile_batches[local_index] = tuple()

                tile_batches = tuple(tile_batches)
            else:
                tile_batches = self._build_chunk_tile_cache(game, cx, cy)

            flat_quads = tuple(
                quad
                for batch in tile_batches
                for quad in batch
            )
            self._custom_terrain_chunk_cache[coord] = {
                'revision': revision,
                'tile_batches': tile_batches,
                'flat_quads': flat_quads,
            }
            game.world.clear_chunk_dirty_indices(cx, cy)

        # The clip window is part of the generation key. Camera movement
        # inside one tile reuses the payload; crossing a tile/viewport boundary
        # repacks from cached geometry without rebuilding the terrain itself.
        key = (epoch, tuple(signature), window, generation)
        if self._custom_terrain_cpu_key != key:
            batches = (self._custom_terrain_chunk_cache[(int(cx), int(cy))]['flat_quads']
                       for cx, cy in coords
                       if (int(cx), int(cy)) in self._custom_terrain_chunk_cache)
            quads, stats = build_terrain_stream(batches, window, METAL_TERRAIN_STREAM_HARD_LIMIT)
            stats['visible_chunks'] = len(coords)
            stats['initial_capacity'] = int(METAL_TERRAIN_STREAM_MAX_QUADS)
            stats['capacity_needed'] = terrain_capacity_for(len(quads),
                    METAL_TERRAIN_STREAM_MAX_QUADS, METAL_TERRAIN_STREAM_HARD_LIMIT)
            self._terrain_stream_stats = stats
            if stats['simplified'] and not getattr(self, '_terrain_lod_logged', False):
                self._terrain_lod_logged = True
                print('FIX100: dense terrain display guard; original assets unchanged.')
            self._custom_terrain_cpu_key = key
            self._custom_terrain_cpu_quads = quads

            # Bound explored-world CPU render history. Only currently visible
            # chunks plus a small reserve are useful for this compact runtime.
            if len(self._custom_terrain_chunk_cache) > 24:
                for old_coord in tuple(self._custom_terrain_chunk_cache):
                    if old_coord not in visible_set:
                        self._custom_terrain_chunk_cache.pop(old_coord, None)
                    if len(self._custom_terrain_chunk_cache) <= 16:
                        break

            if not self._custom_terrain_stream_logged:
                self._custom_terrain_stream_logged = True
                print(
                    'MAP TERRAIN CPU READY:',
                    'chunks=', len(coords),
                    'quads=', len(self._custom_terrain_cpu_quads),
                    'world=', f'{game.world.width_tiles}x{game.world.height_tiles}',
                )

        return self._custom_terrain_cpu_key, self._custom_terrain_cpu_quads


    def _visible_terrain_gpu_batches(self, game):
        """Return persistent triple-buffered terrain chunks.

        V0.6.2 patches only changed tile slots. Continuous mining no longer
        reconstructs 256 tile definitions or allocates a new MTLBuffer per hit.
        """
        if self.state.device is None:
            return tuple()

        self._chunk_tile_cache_clock += 1
        clock = self._chunk_tile_cache_clock
        visible_keys = set()
        result = []
        slots_per_tile = 5

        for cx, cy in self._visible_chunk_coords(game, margin_tiles=2):
            key = (int(cx), int(cy))
            visible_keys.add(key)
            revision = (
                int(getattr(game.world, 'cache_epoch', 0)),
                game.world.chunk_revision(cx, cy),
            )
            cached = self._chunk_tile_cache.get(key)

            if cached is None:
                cached = self._new_fixed_chunk_cache(game, cx, cy, revision)
                self._chunk_tile_cache[key] = cached
                self._terrain_gpu_uploads += 1
            elif cached.get('revision') != revision:
                buffers = cached['buffers']
                old_index = int(cached['active'])
                new_index = (old_index + 1) % len(buffers)
                src = buffers[old_index]
                dst = buffers[new_index]
                count = int(cached['count'])
                dirty = tuple(game.world.chunk_dirty_indices(cx, cy))

                # If the runtime chunk vanished (fully excavated) or dirty
                # history is unavailable, rebuild the fixed array once. Normal
                # mining/placing patches only the changed local tile slots.
                if dirty and len(dirty) <= 64 and revision[1] >= 0:
                    self._copy_instance_buffer(src, dst, count)
                    zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    start_tx = int(cx) * CHUNK_SIZE
                    start_ty = int(cy) * CHUNK_SIZE
                    for tile_index in dirty:
                        tile_index = int(tile_index)
                        lx = tile_index % CHUNK_SIZE
                        ly = tile_index // CHUNK_SIZE
                        tx = start_tx + lx
                        ty = start_ty + ly
                        batch = list(self._tile_render_batch(game, tx, ty))
                        if len(batch) < slots_per_tile:
                            batch.extend([zero] * (slots_per_tile - len(batch)))
                        _write_instances(
                            dst,
                            batch,
                            slots_per_tile,
                            offset_instances=tile_index * slots_per_tile,
                        )
                else:
                    slots = self._build_fixed_chunk_slots(game, cx, cy)
                    _write_instances(dst, slots, count)

                cached['revision'] = revision
                cached['active'] = new_index
                game.world.clear_chunk_dirty_indices(cx, cy)
                self._terrain_gpu_uploads += 1

            self._chunk_tile_cache_last_used[key] = clock
            active_index = int(cached['active'])
            gpu_buffer = cached['buffers'][active_index]
            count = int(cached['count'])
            if gpu_buffer is not None and count > 0:
                result.append((gpu_buffer, count))

        if len(self._chunk_tile_cache) > self._chunk_tile_cache_limit:
            candidates = sorted(
                (
                    (last_used, key)
                    for key, last_used in self._chunk_tile_cache_last_used.items()
                    if key not in visible_keys
                ),
                key=lambda item: item[0],
            )
            remove_count = max(0, len(self._chunk_tile_cache) - self._chunk_tile_cache_limit)
            for _last_used, key in candidates[:remove_count]:
                self._chunk_tile_cache.pop(key, None)
                self._chunk_tile_cache_last_used.pop(key, None)

        return tuple(result)

    def _visible_tile_bounds(self, game, margin=2):
        m = int(margin)
        min_tx = max(
            0,
            int(math.floor(game.camera_x / TILE_SIZE)) - m,
        )
        min_ty = max(
            0,
            int(math.floor(game.camera_y / TILE_SIZE)) - m,
        )
        max_tx = min(
            game.world.width_tiles - 1,
            int(math.floor((game.camera_x + game.viewport_w) / TILE_SIZE)) + m,
        )
        max_ty = min(
            game.world.height_tiles - 1,
            int(math.floor((game.camera_y + game.viewport_h) / TILE_SIZE)) + m,
        )
        return min_tx, min_ty, max_tx, max_ty

    def _iter_visible_table_items(self, table, game, margin=2):
        """Iterate a tile-keyed sparse table by viewport, not world size.

        V0.5.8 scanned every water/soil/plant/fire entry and then rejected
        off-screen cells. That makes renderer cost grow forever as the player
        explores or digs. V0.5.9 performs O(visible tiles) dictionary lookups.
        """
        if not table:
            return

        missing = object()
        min_tx, min_ty, max_tx, max_ty = self._visible_tile_bounds(
            game,
            margin,
        )
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                value = table.get((tx, ty), missing)
                if value is not missing:
                    yield (tx, ty), value

    def _is_visible_tile(self, tx, ty, game, margin=2):
        """Return True when a tile overlaps the current camera viewport.

        Restored in V0.6.2.9.  The V0.5.9 visible-table refactor kept calls
        to this helper in environment rendering, but a later renderer cleanup
        accidentally removed the method itself.  That caused every render()
        to raise AttributeError before state.publish(snapshot), leaving the
        real device permanently at NO SNAPSHOT | VSYNC.
        """
        tx = int(tx)
        ty = int(ty)
        m = max(0, int(margin))
        wx = tx * TILE_SIZE
        wy = ty * TILE_SIZE
        pad = TILE_SIZE * m
        return not (
            wx + TILE_SIZE < float(game.camera_x) - pad
            or wy + TILE_SIZE < float(game.camera_y) - pad
            or wx > float(game.camera_x) + float(game.viewport_w) + pad
            or wy > float(game.camera_y) + float(game.viewport_h) + pad
        )

    def _first_solid_row(
        self,
        game,
        tx,
    ):
        # V0.5.9: rain roof queries share the world's dirty column cache.
        return game.world.first_solid_row(tx)

    def _rain_stop_y(
        self,
        game,
        wx,
    ):
        tx = int(
            max(
                0,
                min(
                    game.world.width_tiles - 1,
                    wx // TILE_SIZE,
                )
            )
        )

        first_solid = self._first_solid_row(
            game,
            tx,
        )

        if first_solid is None:
            solid_stop = game.world.height_tiles * TILE_SIZE
        else:
            try:
                top_ratio = float(game.world.backdrop_surface_top_ratio(tx))
            except Exception:
                top_ratio = 0.0
            # For current layered foreground use the live geometry; backdrop
            # ratio is a safe fallback and matches unedited columns.
            try:
                tile_id = game.world.get_tile(tx, int(first_solid))
                if tile_id in LAYERED_SOLID_TILES:
                    top_ratio = float(soil_top_ratio(game.world.tile_layer_mask_at(tx, int(first_solid))))
            except Exception:
                pass
            solid_stop = (float(first_solid) + max(0.0, min(1.0, top_ratio))) * TILE_SIZE - 2.0

        # Rain should terminate on the first free-water surface, not continue
        # visually through an ocean/lake until it reaches the seabed. The world
        # is only 32 rows deep in the default map, so this scan is tiny and only
        # runs for the handful of active rain streak columns.
        water_stop = None
        try:
            env = game.environment.state
            for ty in range(0, game.world.height_tiles):
                amount = float(env.water_amount(tx, ty))
                if amount < float(METAL_WATER_MIN_VISIBLE):
                    continue
                try:
                    top_y = float(game.environment.water.surface_y(tx, ty))
                except Exception:
                    top_y = (float(ty) + 1.0 - max(0.0, min(1.0, amount))) * TILE_SIZE
                water_stop = top_y - 1.0
                break
        except Exception:
            water_stop = None

        stop = solid_stop if water_stop is None else min(float(solid_stop), float(water_stop))
        return max(0.0, float(stop))

    def _background_weights(self,game, surface_only=False):
        try:meta=(game.map_loader.payload or {}).get("metadata",{}) if game.map_loader else {}
        except Exception:meta={}
        authored=str(meta.get("background_theme","") or "") if isinstance(meta,dict) else ""
        aliases={"egyptian_pyramid":"pyramid"}
        if authored:return ((aliases.get(authored,authored),1.0),)
        path=str(getattr(game,"current_map_path","") or "");mid=path.replace("\\","/").rsplit("/",1)[-1].rsplit(".",1)[0]
        special={"magma_hell":"magma_hell","crystal_mine":"crystal_mine","glow_moss_cavern":"glow_moss","underground_castle":"underground_castle","boss_dungeon":"boss_dungeon","egyptian_pyramid":"pyramid"}
        if mid in special:return ((special[mid],1.0),)
        if not surface_only and game.player.y/TILE_SIZE>=float(meta.get("underground_start_row",999)):return (("underground",1.0),)
        tx=game.player.x/TILE_SIZE;spans=list(getattr(getattr(game,"biomes",None),"spans",()) or ());blend=8.0
        for i,(a,b,theme) in enumerate(spans):
            if a<=tx<b:
                if i>0 and tx<a+blend:t=(tx-a)/blend;return ((spans[i-1][2],1-t),(theme,t))
                if i+1<len(spans) and tx>b-blend:t=(tx-b+blend)/blend;return ((theme,1-t),(spans[i+1][2],t))
                return ((theme,1.0),)
        return (("plains",1.0),)

    def _build_parallax_background_quads(self,game,cx,cy):
        # FIX69 linked worlds carry an already parsed, map-local catalog on the
        # gameplay object.  It has first refusal over FIX68 underground/main
        # palettes and emits at most half of the 256-instance background batch.
        self._secondary_world_background_active = False
        self._secondary_world_background_profile = ""
        try:
            secondary = getattr(game, "secondary_world", None)
            if bool(getattr(secondary, "active", False)):
                visual_now = time.monotonic()
                plan = secondary.build_background_plan(
                    camera_x=float(cx), camera_y=float(cy),
                    viewport_w=float(game.viewport_w), viewport_h=float(game.viewport_h),
                    visual_time=visual_now,
                    tile_x=float(game.player.x) / float(TILE_SIZE),
                    tile_y=float(game.player.y) / float(TILE_SIZE),
                    max_quads=METAL_BACKGROUND_MAX_QUADS // 2,
                )
                if str(getattr(plan, "profile_id", "") or ""):
                    self._secondary_world_background_active = True
                    self._secondary_world_background_profile = str(plan.profile_id)
                    self._fix68_background_active = False
                    self._fix68_background_profile = ""
                    return tuple(
                        self._quad(row.x, row.y, row.width, row.height, row.rgba)
                        for row in tuple(plan.quads)[:METAL_BACKGROUND_MAX_QUADS // 2]
                    )
        except Exception:
            # A malformed optional art catalog may never block the game/map.
            pass

        # FIX68 underground maps and the bounded zones in editor_map use one
        # data-driven, three-depth catalog.  Cache the parsed catalog on the
        # renderer and only stat it every two seconds; the 60 Hz render path
        # never re-opens JSON and all output remains in the existing 128-quad
        # half of the Metal background buffer.
        self._fix68_background_active = False
        self._fix68_background_profile = ""
        try:
            meta=(game.map_loader.payload or {}).get("metadata",{}) if game.map_loader else {}
            meta=meta if isinstance(meta,dict) else {}
            project_root=str(getattr(getattr(game,"save_manager",None),"project_root","") or "")
            visual_now=time.monotonic()
            cache_key=(project_root,str(meta.get("background_catalog","") or ""))
            cached_key=getattr(self,"_fix68_background_catalog_key",None)
            checked=float(getattr(self,"_fix68_background_catalog_checked",-9999.0))
            if cached_key!=cache_key or visual_now-checked>=2.0:
                loaded=load_background_catalog(project_root,meta,strict=False)
                self._fix68_background_catalog=loaded if isinstance(loaded,dict) else {}
                self._fix68_background_catalog_key=cache_key
                self._fix68_background_catalog_checked=visual_now
            plan=build_background_plan(
                project_root,metadata=meta,
                map_path=str(getattr(game,"current_map_path","") or ""),
                camera_x=float(cx),camera_y=float(cy),
                viewport_w=float(game.viewport_w),viewport_h=float(game.viewport_h),
                visual_time=visual_now,quad_budget=128,
                catalog=getattr(self,"_fix68_background_catalog",{}),
                include_base=True,strict=False,
                tile_x=float(game.player.x)/float(TILE_SIZE),
                tile_y=float(game.player.y)/float(TILE_SIZE),
            )
            if str(getattr(plan,"profile_id","") or ""):
                self._fix68_background_active = True
                self._fix68_background_profile = str(plan.profile_id)
                return tuple(
                    self._quad(row.x,row.y,row.width,row.height,row.rgba)
                    for row in tuple(plan.quads)[:METAL_BACKGROUND_MAX_QUADS//2]
                )
        except Exception:
            # Catalog/background authoring must remain presentation-only and
            # fail open to the proven palette renderer below.
            pass

        return self._build_surface_parallax_quads(game,cx,cy)

    def _build_authored_background_quads(self,game,cx,cy,budget=96):
        """Render FIX91 map-authored 16x16 background art as run-length quads.

        The editor stores only a tiny lossless pixel source.  Rendering converts
        contiguous same-colour pixels into screen-sized background quads and
        keeps a strict budget, so editable background art cannot grow UIKit or
        Metal allocations with map size.
        """
        try:
            meta=(getattr(getattr(game,'map_loader',None),'payload',None) or {}).get('metadata',{})
            aid=str(meta.get('background_asset','') or '') if isinstance(meta,dict) else ''
            # FIX91 shared background editor: if this map has no explicit
            # override, look for a saved override of the dominant procedural
            # theme.  Unsaved theme entries have no pixel-source file, so the
            # legacy parallax remains byte-for-byte unchanged until edited.
            if not aid:
                try:
                    weights=self._background_weights(game,surface_only=True)
                    theme=max(weights,key=lambda row:float(row[1]))[0] if weights else ''
                    if theme:aid='background.theme_'+str(theme)
                except Exception:aid=''
            if not aid:return ()
            root=str(getattr(getattr(game,'save_manager',None),'project_root','') or '')
            if not root:return ()
            reg_path=os.path.join(root,'assets','registry.json'); rel=''
            try:
                # FIX100: rendering already refreshes the shared registry.
                # Do not parse registry.json on every Metal presentation.
                assets = getattr(game, 'assets', None)
                if assets is not None and hasattr(assets, 'get'):
                    reg = assets.get(aid, {})
                else:
                    with open(reg_path, 'r', encoding='utf-8') as source:
                        reg = json.load(source).get('bindings', {}).get(aid, {})
                rel=str(reg.get('pixel_source','') or '') if isinstance(reg,dict) else ''
            except Exception:pass
            if not rel:rel='pixel_sources/'+aid.replace('.','_')+'.json'
            path=os.path.join(root,'assets',*rel.split('/'))
            mtime=os.path.getmtime(path)
            cache=getattr(self,'_fix91_authored_background_cache',None)
            key=(path,mtime)
            if not isinstance(cache,dict) or cache.get('key')!=key:
                with open(path, 'r', encoding='utf-8') as source:
                    data = json.load(source)
                pixels=data.get('pixels',[])
                if not (isinstance(pixels,list) and 1<=len(pixels)<=32):return ()
                cache={'key':key,'pixels':pixels};self._fix91_authored_background_cache=cache
            pixels=cache['pixels'];h=len(pixels);w=max((len(r) for r in pixels if isinstance(r,list)),default=0)
            if w<=0:return ()
            vw=float(game.viewport_w);vh=float(game.viewport_h);rows=[];limit=max(1,min(96,int(budget)))
            for yy,row in enumerate(pixels):
                if not isinstance(row,list):continue
                x=0
                while x<min(w,len(row)) and len(rows)<limit:
                    raw=str(row[x] or '#00000000');rgba=_hex_rgba(raw[:7] if len(raw)>=7 else '#000000')
                    alpha=1.0
                    try:
                        text=raw.lstrip('#');alpha=(int(text[6:8],16)/255.0) if len(text)>=8 else 1.0
                    except Exception:alpha=1.0
                    if alpha<=0.001:x+=1;continue
                    end=x+1
                    while end<min(w,len(row)) and str(row[end] or '').upper()==raw.upper():end+=1
                    col=(rgba[0],rgba[1],rgba[2],min(.92,rgba[3]*alpha))
                    cw=vw/w;ch=vh/h
                    rows.append(self._quad(cx+(x+end)*.5*cw,cy+(yy+.5)*ch,max(1.0,(end-x)*cw+1),max(1.0,ch+1),col))
                    x=end
                if len(rows)>=limit:break
            return tuple(rows)
        except Exception:
            return ()

    def _build_surface_parallax_quads(self,game,cx,cy):
        pal={"ocean":("#79BED8","#6D9CB4","#386F8B"),"lake":("#86C3CF","#6F9DA2","#3F7470"),"swamp":("#75968A","#516F62","#294B3B"),"plains":("#8FC8E8","#75A16B","#477B43"),"village":("#A7C8DB","#778B71","#4D6251"),"rainforest":("#6EAA91","#356A55","#173F32"),"snow_mountain":("#B9D3E3","#A9BBC8","#6E8795"),"desert":("#E7C27B","#D8A85B","#B87836"),"underground":("#252A2F","#353B40","#1D2024"),"dungeon":("#23232C","#303039","#1B1C24"),"magma":("#321318","#4B1716","#241014"),"crystal":("#1D2940","#263B58","#182238"),"pyramid":("#46301E","#9A7041","#332417"),"glow_moss":("#102724","#245447","#143B32"),"crystal_mine":("#111C31","#29496A","#172A47"),"underground_castle":("#171720","#302E3A","#1E1B29"),"boss_dungeon":("#100D17","#35213A","#211426"),"magma_hell":("#260D12","#581814","#2F1010")}
        weights=self._background_weights(game,surface_only=True);vw=game.viewport_w;vh=game.viewport_h;out=[]
        # FIX83: the silhouette is pinned to immutable ground, not vh*.56.
        from systems.surface_backdrop import surface_y
        anchor=surface_y(game.world,int(game.player.x//TILE_SIZE))
        ground_screen=(anchor-cy) if anchor is not None else vh*.65
        # Render-only animation must continue while gameplay simulation is
        # paused by the inventory modal, without advancing game time.
        visual_now=time.monotonic()
        def mix(idx):
            rows=[(_hex_rgba(pal.get(t,pal["plains"])[idx]),w) for t,w in weights];total=sum(w for c,w in rows) or 1;return tuple(sum(c[j]*w for c,w in rows)/total for j in range(4))
        out.append(self._quad(cx+vw*.5,cy+vh*.5,vw+4,vh+4,mix(0)))
        for theme,alpha in weights:
            sky,far,near=pal.get(theme,pal["plains"]);fc=_hex_rgba(far,alpha*.72);nc=_hex_rgba(near,alpha*.84)
            for factor,spacing,y,col in ((.07,170,ground_screen-22.,fc),(.22,105,ground_screen+2.,nc)):
                x=-(cx*factor)%spacing-spacing;i=0
                while x<vw+spacing:
                    h=48+(i%3)*9;out.append(self._quad(cx+x+spacing*.5,cy+y-h*.5,spacing+8,h,col))
                    if theme in ("rainforest","swamp","village","pyramid"):out.append(self._quad(cx+x+spacing*.55,cy+y-h,14,h*1.25,col))
                    if theme=="glow_moss":
                        pulse=.34+.18*math.sin(visual_now*1.7+i)
                        # Layered root columns, mushroom caps and drifting
                        # spores distinguish the biome even without foreground.
                        out.append(self._quad(cx+x+spacing*.22,cy+y-h*.68,7,42,(.16,.48,.34,.86*alpha)))
                        out.append(self._quad(cx+x+spacing*.34,cy+y-h*.74,8,22,(.24,.92,.67,pulse*alpha)))
                        out.append(self._quad(cx+x+spacing*.34,cy+y-h*.98,34,9,(.29,.73,.55,.72*alpha)))
                        out.append(self._quad(cx+x+spacing*.70,cy+y-h*.38,5,5,(.50,1.0,.78,(pulse+.12)*alpha)))
                        out.append(self._quad(cx+x+spacing*.79,cy+y-h*.83,3,31,(.28,.73,.51,.58*alpha)))
                    elif theme=="crystal_mine":
                        pulse=.28+.12*math.sin(visual_now*1.35+i*.8)
                        # Two depths of geode clusters plus a broken mining
                        # brace create a recognisable natural/industrial mix.
                        out.append(self._quad(cx+x+spacing*.30,cy+y-h*.70,10,44,(.28,.64,1.0,.38*alpha)))
                        out.append(self._quad(cx+x+spacing*.23,cy+y-h*.51,7,29,(.55,.84,1.0,.30*alpha)))
                        out.append(self._quad(cx+x+spacing*.68,cy+y-h*.46,7,31,(.55,.84,1.0,.32*alpha)))
                        out.append(self._quad(cx+x+spacing*.68,cy+y-h*.46,3,18,(.84,.98,1.0,pulse*alpha)))
                        out.append(self._quad(cx+x+spacing*.86,cy+y-h*.72,6,45,(.31,.27,.25,.62*alpha)))
                        out.append(self._quad(cx+x+spacing*.72,cy+y-h*.97,34,6,(.42,.34,.25,.56*alpha)))
                    elif theme=="underground_castle":
                        out.append(self._quad(cx+x+spacing*.30,cy+y-h*.76,9,58,(.22,.21,.29,.82)))
                        out.append(self._quad(cx+x+spacing*.72,cy+y-h*.76,9,58,(.22,.21,.29,.82)))
                        out.append(self._quad(cx+x+spacing*.51,cy+y-h*1.05,52,8,(.30,.27,.36,.80)))
                    elif theme=="boss_dungeon":
                        out.append(self._quad(cx+x+spacing*.50,cy+y-h*.78,16,68,(.31,.12,.28,.70)))
                        out.append(self._quad(cx+x+spacing*.50,cy+y-h*1.12,30,6,(.64,.22,.42,.38)))
                    elif theme=="magma_hell":
                        pulse=.48+.18*math.sin(visual_now*2.3+i*.7)
                        # Basalt teeth, lava falls, furnace arches and embers
                        # form an oppressive depth silhouette.
                        out.append(self._quad(cx+x+spacing*.18,cy+y-h*.59,12,49,(.11,.08,.10,.90*alpha)))
                        out.append(self._quad(cx+x+spacing*.34,cy+y-h*.42,10,32,(.17,.09,.10,.92*alpha)))
                        out.append(self._quad(cx+x+spacing*.62,cy+y-h*.70,9,58,(1.0,.25,.04,pulse*alpha)))
                        out.append(self._quad(cx+x+spacing*.62,cy+y-h*.42,18,5,(1.0,.62,.08,min(1.0,pulse+.18)*alpha)))
                        out.append(self._quad(cx+x+spacing*.84,cy+y-h*.67,7,53,(.29,.12,.12,.82*alpha)))
                        out.append(self._quad(cx+x+spacing*.84,cy+y-h*.96,37,7,(.37,.15,.13,.72*alpha)))
                        out.append(self._quad(cx+x+spacing*.46,cy+y-h*.91-(i%2)*11,4,5,(1.0,.45,.06,.62*alpha)))
                    x+=spacing;i+=1
        return tuple(out[:METAL_BACKGROUND_MAX_QUADS//2])

    def _build_backdrop_quads(self, game, render_camera_x, render_camera_y):
        from systems.surface_backdrop import backdrop_plan
        return tuple(self._quad(*q) for q in backdrop_plan(game.world,
                     render_camera_x, render_camera_y, game.viewport_w, game.viewport_h,
                     budget=112))

    def _fix83_background(self, game, cx, cy):
        from systems.surface_backdrop import visible_columns
        parallax=self._build_parallax_background_quads(game,cx,cy)
        authored=self._build_authored_background_quads(game,cx,cy,96)
        if authored:
            cap=max(8,METAL_BACKGROUND_MAX_QUADS//2-len(authored))
            parallax=tuple(parallax[:cap])+tuple(authored)
        secondary=bool(getattr(self,"_secondary_world_background_active",False))
        underground=bool(getattr(self,"_fix68_background_active",False))
        meta=(getattr(getattr(game,'map_loader',None),'payload',None) or {}).get('metadata',{})
        main=meta.get('map_id')=='main_world'
        if secondary or (underground and not main):
            return parallax
        columns=visible_columns(game.world,cx,game.viewport_w)
        surface_max=max((sy for _,sy in columns if sy is not None),default=cy-1.)
        if underground and surface_max<cy:
            return parallax
        # A cave profile must not replace the entire view while a surface opening
        # is still visible. Keep sky above ground and an opaque back wall below.
        sky=self._build_surface_parallax_quads(game,cx,cy) if underground else parallax
        if underground and authored:
            cap=max(8,128-len(authored));sky=tuple(sky[:cap])+tuple(authored)
        return tuple(sky[:128])+self._build_backdrop_quads(game,cx,cy)

    def _hide_subvisible_freezing_water(self, game, tx, ty, amount):
        try:
            env = game.environment.state
            if game.world.get_tile(int(tx), int(ty)) == ICE:
                return True
            if float(getattr(env, "cold_surface_water", {}).get((int(tx), int(ty)), 0.0)) > 1e-9:
                return True
            if float(amount) >= float(ALPINE_SURFACE_FREEZE_MIN_AMOUNT):
                return False
            thermal = game.environment.thermal
            if not thermal.is_naturally_freezing_surface(int(tx), int(ty)):
                return False
            if int(ty) + 1 >= game.world.height_tiles:
                return False
            return bool(tile_def(game.world.get_tile(int(tx), int(ty) + 1)).solid)
        except Exception:
            return False

    def _smoothed_visible_water(self, game):
        env = game.environment.state
        target = dict(self._iter_visible_table_items(env.water, game))
        now = time.monotonic()
        dt = max(0.0, min(0.050, now - float(self._water_visual_time)))
        self._water_visual_time = now
        # V0.6.4: authoritative liquid also runs at 60 Hz. Keep only a short
        # visual ease so seepage follows immediately instead of looking as if
        # the renderer is one or two physics ticks behind.
        blend = 1.0 - math.exp(-28.0 * dt) if dt > 0.0 else 1.0
        keys = set(target.keys()) | set(self._water_visual_amounts.keys())
        result = []
        for key in keys:
            tx, ty = key
            if not self._is_visible_tile(tx, ty, game, margin=3):
                self._water_visual_amounts.pop(key, None)
                continue
            desired = max(0.0, min(1.0, float(target.get(key, 0.0))))
            if key in self._water_visual_amounts:
                current = float(self._water_visual_amounts[key])
            else:
                # New falling cells fade in from a small amount rather than
                # popping into existence one whole tile lower.
                current = min(desired, 0.10)
            current += (desired - current) * blend
            if desired <= 1e-9 and current < 0.002:
                self._water_visual_amounts.pop(key, None)
                continue
            self._water_visual_amounts[key] = current
            result.append((key, current))
        return result

    def _connected_wave_distance(self, game, visible_water):
        """Return water-cell distance from a true ocean surface seed.

        V0.7.6.9 removes the biome-edge wave seam.  Wave energy is seeded only
        on the real ocean/atmosphere surface, then propagated through the
        physically connected free-water cells around the camera.  Only open
        surfaces use the result for displacement; submerged cells are merely
        the connectivity path.  A flooded beach shaft therefore inherits a
        damped continuation of the ocean wave instead of abruptly switching to
        flat water at the biome boundary.
        """
        env = game.environment.state
        water_map = env.water
        margin = max(4, int(CONNECTED_WAVE_SEARCH_MARGIN_TILES))
        # V0.7.7.0: connectivity does not need a Python BFS every rendered
        # frame. Cache for a short interval; active water still updates several
        # times per second, but Metal drawing stays smooth on iPhone.
        now = time.monotonic()
        # V0.7.7.1 performance: the previous fast path still rebuilt min/max
        # lists from every visible water tile on every rendered frame.  Cache
        # by coarse camera tile + terrain revision first, and only inspect the
        # water set when that coarse key expires. This keeps connected-wave
        # topology off the 60/120 Hz render hot path.
        coarse_cam = (
            int(float(getattr(game, "camera_x", 0.0)) // (TILE_SIZE * 6.0)),
            int(float(getattr(game, "camera_y", 0.0)) // (TILE_SIZE * 4.0)),
            int(getattr(game.world, "revision", 0)),
        )
        if getattr(self, "_connected_wave_cache_coarse", None) == coarse_cam:
            if now - float(getattr(self, "_connected_wave_cache_time", 0.0)) < 1.20:
                return getattr(self, "_connected_wave_cache_value", {})

        cells = set()
        # Smoothed visible cells are guaranteed to include what will be drawn.
        for (tx, ty), amount in visible_water:
            if float(amount) >= float(METAL_WATER_MIN_VISIBLE) * 0.35:
                cells.add((int(tx), int(ty)))

        # V0.7.7.1: do NOT rescan the world's entire sparse water dictionary
        # from the renderer. Visible/smoothed water already contains the cells
        # that can contribute pixels this frame. The old tuple(water_map.items())
        # scan made cost grow with every distant lake/puddle ever created.

        if not cells:
            self._connected_wave_cache_coarse=coarse_cam
            self._connected_wave_cache_time=now
            self._connected_wave_cache_value={}
            return {}

        biome_sys = (getattr(game, "biomes", None) or getattr(game, "biome_system", None))
        seeds = []
        for tx, ty in cells:
            # Wave source must itself be an exposed physical water surface.
            if float(water_map.get((tx, ty - 1), 0.0)) >= float(METAL_WATER_MIN_VISIBLE):
                continue
            try:
                biome_id = str(biome_sys.biome_at_tile(int(tx))) if biome_sys is not None else ""
            except Exception:
                biome_id = ""
            if biome_id != "ocean":
                continue
            try:
                seabed_row = game.world.backdrop_surface_row(int(tx))
                if seabed_row is not None and int(ty) >= int(seabed_row):
                    continue
            except Exception:
                pass
            seeds.append((tx, ty))

        if not seeds:
            self._connected_wave_cache_coarse=coarse_cam
            self._connected_wave_cache_time=now
            self._connected_wave_cache_value={}
            return {}

        dist = {}
        q = deque()
        for key in seeds:
            if key not in dist:
                dist[key] = 0
                q.append(key)

        # BFS through the water volume.  Air, solids and trapped-air cavities
        # are never traversed because they are absent from `cells`.
        max_nodes = 1024
        while q and len(dist) < max_nodes:
            x, y = q.popleft()
            nd = dist[(x, y)] + 1
            for nk in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if nk in cells and nk not in dist:
                    dist[nk] = nd
                    q.append(nk)
        self._connected_wave_cache_coarse=coarse_cam
        self._connected_wave_cache_time=now
        self._connected_wave_cache_value=dist
        return dist

    def _build_water_quads(self, game):
        """Build a compact liquid-only visual batch.

        The old environment batch rebuilt water, rain, wind, plants and fire in
        one large Python function. During active flow that made every liquid
        visual refresh pay for unrelated VFX. This path is intentionally simple
        and bounded: one body quad plus an optional surface highlight per
        visible water tile. Physics remains fully authoritative in WaterSystem.
        """
        env = game.environment.state
        water_map = env.water
        quads = []
        visible_water = self._smoothed_visible_water(game)
        wave_distance = self._connected_wave_distance(game, visible_water)
        # FIX40: one clock read per liquid batch, not one per water tile.
        visual_time = time.monotonic()
        full_interior_rows = {}
        for (tx, ty), raw_amount in visible_water:
            amount = max(0.0, min(1.0, float(raw_amount)))
            if amount < METAL_WATER_MIN_VISIBLE:
                continue
            if self._hide_subvisible_freezing_water(game, tx, ty, amount):
                continue

            try:
                current_cap = float(game.world.liquid_capacity_at(tx, ty))
                internal_floor = bool(game.world.liquid_has_internal_floor(tx, ty))
            except Exception:
                current_cap = 1.0
                internal_floor = False
            below_solid = (
                tile_def(game.world.get_tile(tx, ty + 1)).solid
                if ty + 1 < game.world.height_tiles
                else True
            )
            below_amount = float(water_map.get((tx, ty + 1), 0.0))
            falling = (not internal_floor and not below_solid and below_amount < WATER_SUPPORT_THRESHOLD)
            tile_left = tx * TILE_SIZE
            tile_top = ty * TILE_SIZE
            tile_bottom = (ty + 1) * TILE_SIZE
            # V0.7.7.4: draw exactly the liquid capacity owned by THIS cell.
            # LOW/MID terrain stores free water in its missing upper fraction;
            # an AIR cell above it stops at its own grid bottom.  The previous
            # support-below extension could double-use that fractional space and
            # made lake edges look lower than the center.
            fluid_bottom = float(tile_top + max(0.0, min(1.0, current_cap)) * TILE_SIZE)
            above_amount = float(water_map.get((tx, ty - 1), 0.0)) if ty > 0 else 0.0

            # Deep full water is visually identical across adjacent AIR cells.
            # Merge those cells horizontally into one opaque body quad per run.
            # Surface/partial/falling cells keep the exact old geometry.
            if (
                (not falling)
                and current_cap >= 0.999
                and amount >= 0.965
                and above_amount >= METAL_WATER_MIN_VISIBLE
            ):
                full_interior_rows.setdefault(int(ty), []).append(int(tx))
                continue

            if falling:
                width = max(4.0, TILE_SIZE * WATER_STREAM_WIDTH)
                height = max(5.0, TILE_SIZE * max(0.30, amount))
                quads.append(self._quad(
                    tile_left + TILE_SIZE * 0.5,
                    tile_top + TILE_SIZE * 0.5,
                    width,
                    min(TILE_SIZE + TILE_RENDER_OVERLAP, height),
                    (0.12, 0.50, 0.94, 1.0),
                ))
            else:
                # V0.7.6.4 ocean waves are renderer-only. Renderer now reads game.biomes (the actual runtime property), so the wave path is active. The authoritative
                # tile amount remains untouched so wave crests cannot create
                # water, alter rain interception or destabilize swimming.
                wave = 0.0
                is_open_surface = above_amount < METAL_WATER_MIN_VISIBLE

                # V0.7.6.9: do not gate waves directly by biome.  A true ocean
                # surface seeds wave energy and every physically connected
                # water cell can carry that energy across the shoreline.  Only
                # an exposed free surface is displaced, with distance damping.
                wave_dist = wave_distance.get((int(tx), int(ty)))
                is_wave_surface = bool(is_open_surface and wave_dist is not None)
                wave_gain = 0.0
                if is_wave_surface:
                    d = float(max(0, int(wave_dist)))
                    wave_gain = max(
                        float(CONNECTED_WAVE_MIN_GAIN),
                        math.exp(-float(CONNECTED_WAVE_DECAY_PER_TILE) * d),
                    )
                    phase_lag = d * float(CONNECTED_WAVE_PHASE_LAG_PER_TILE)
                    wave = wave_gain * (
                        float(OCEAN_WAVE_AMPLITUDE_PX)
                        * math.sin(
                            visual_time*float(OCEAN_WAVE_SPEED)
                            + tx*float(OCEAN_WAVE_WAVENUMBER)
                            - phase_lag
                        )
                        + float(OCEAN_WAVE_SECONDARY_AMPLITUDE_PX)
                        * math.sin(
                            visual_time*float(OCEAN_WAVE_SECONDARY_SPEED)
                            + tx*float(OCEAN_WAVE_SECONDARY_WAVENUMBER)
                            + 1.13 - phase_lag*0.65
                        )
                    )

                # Surface follows authoritative fractional capacity directly.
                # Cross-row hydrostatic leveling is handled by WaterSystem, not
                # by a renderer-only heuristic.
                base_top = fluid_bottom - max(2.0, TILE_SIZE * amount)
                # V0.7.6.3: do not clip ocean crests at the surface tile's
                # upper boundary.  That clipping was the main reason the wave
                # looked tiny.  A crest may visually enter the empty tile above
                # by a bounded amount; authoritative water remains unchanged.
                crest_limit = float(OCEAN_WAVE_MAX_CREST_PX) if is_wave_surface else 0.0
                visual_top = max(tile_top - crest_limit, min(fluid_bottom - 2.0, base_top + wave))
                height = max(2.0, fluid_bottom - visual_top)
                center_y = fluid_bottom - height * 0.5
                quads.append(self._quad(
                    tile_left + TILE_SIZE * 0.5,
                    center_y,
                    TILE_SIZE + TILE_RENDER_OVERLAP,
                    height + TILE_RENDER_OVERLAP,
                    (0.12, 0.50, 0.94, 1.0),
                ))
                if amount >= 0.10:
                    quads.append(self._quad(
                        tile_left + TILE_SIZE * 0.5,
                        visual_top + 1.5,
                        TILE_SIZE * 0.86,
                        2.0,
                        (0.55, 0.84, 1.0, 0.72),
                    ))
                    if is_wave_surface:
                        foam = min(1.0, abs(wave) / max(1.0, float(OCEAN_WAVE_AMPLITUDE_PX) + float(OCEAN_WAVE_SECONDARY_AMPLITUDE_PX)))
                        quads.append(self._quad(
                            tile_left + TILE_SIZE*(0.27 if ((tx + int(visual_time*2.0)) & 1)==0 else 0.70),
                            visual_top + 0.7,
                            TILE_SIZE*(0.16 + 0.10*foam),
                            1.4,
                            (0.90, 0.97, 1.0, 0.34 + 0.28*foam),
                        ))

            if len(quads) >= METAL_MAX_ENV_QUADS:
                break

        # Emit deep-water runs last. Since these are fully opaque interior
        # cells and never an exposed surface, merging is pixel-equivalent while
        # cutting a camera-full ocean from hundreds of instances to a few rows.
        for row_ty, xs in full_interior_rows.items():
            if not xs:
                continue
            xs = sorted(set(xs))
            run0 = prev = xs[0]
            for marker in xs[1:] + [None]:
                if marker is not None and marker == prev + 1:
                    prev = marker
                    continue
                count = prev - run0 + 1
                quads.append(self._quad(
                    (run0 + count * 0.5) * TILE_SIZE,
                    row_ty * TILE_SIZE + TILE_SIZE * 0.5,
                    count * TILE_SIZE + TILE_RENDER_OVERLAP,
                    TILE_SIZE + TILE_RENDER_OVERLAP,
                    (0.12, 0.50, 0.94, 1.0),
                ))
                if len(quads) >= METAL_MAX_ENV_QUADS:
                    return quads
                if marker is None:
                    break
                run0 = prev = marker

        # V0.7.6.6: no sea-sand-specific fake-water overlay is needed.
        # The normal water body now uses TileWorld.water_floor_y(), so every
        # partial support material (sand, soil, rock, etc.) shares one geometry
        # path and one water colour with no double-drawn seam.
        return quads

    def _cloud_visual_layout(self, game, cloud_cx, cloud_mass):
        """Return a continuous-looking cloud bank layout for one cloud column.

        cloud_water remains integer chunk-addressed for cheap conserved physics.
        For visuals, neighbor mass gradients shift both banks toward each other
        while transfer occurs. This makes real advection read as horizontal
        motion instead of one fixed bank fading out and another popping in.
        """
        env = game.environment.state
        cloud_cx = int(cloud_cx)
        center_tx = cloud_cx * CHUNK_SIZE + CHUNK_SIZE // 2
        if center_tx < 0 or center_tx >= game.world.width_tiles:
            return None
        try:
            surface_ty = int(game.world.first_solid_row(center_tx))
        except Exception:
            surface_ty = max(8, game.world.height_tiles // 4)
        # Lift broad cloud banks higher so low maps do not show clouds sitting inside lakes.
        cloud_ty = max(1, min(surface_ty - 12 - ((cloud_cx & 1) * 2), max(1, game.world.height_tiles // 5)))

        left_mass = max(0.0, float(env.cloud_amount(cloud_cx - 1, 0))) if cloud_cx > 0 else 0.0
        right_mass = max(0.0, float(env.cloud_amount(cloud_cx + 1, 0)))
        denom = max(0.001, float(cloud_mass) + 0.65*(left_mass + right_mass))
        gradient = max(-1.0, min(1.0, (right_mass - left_mass) / denom))
        shift_px = gradient * (CHUNK_SIZE * TILE_SIZE) * 0.26

        fullness = max(0.0, min(1.0, float(cloud_mass) / max(0.001, float(ATMOSPHERE_CLOUD_FULL_MASS))))
        width_scale = 0.88 + 0.38 * fullness
        cx_px = (center_tx + 0.5) * TILE_SIZE + shift_px
        cy_px = (cloud_ty + 0.5) * TILE_SIZE
        width_px = 7.1 * TILE_SIZE * width_scale
        return center_tx, cloud_ty, cx_px, cy_px, fullness, width_scale, width_px

    def _refresh_underwater_anchors(self, game, now):
        """Cache a tiny set of visible water cells for bubbles and grass.

        This deliberately performs O(viewport tiles) dictionary lookups only
        every UNDERWATER_ANCHOR_REFRESH_SECONDS. No particle participates in
        collision, water conservation, or the entity scene graph.
        """
        coarse = (
            int(float(getattr(game, "camera_x", 0.0)) // (TILE_SIZE * 4.0)),
            int(float(getattr(game, "camera_y", 0.0)) // (TILE_SIZE * 3.0)),
            int(getattr(game.world, "revision", 0)),
        )
        if self._underwater_anchor_key == coarse:
            if float(now) - float(self._underwater_anchor_time) < float(UNDERWATER_ANCHOR_REFRESH_SECONDS):
                return self._underwater_anchor_cache

        water = getattr(getattr(game, "environment", None), "state", None)
        water = getattr(water, "water", {}) if water is not None else {}
        bubbles = []
        grass = []
        try:
            min_tx, min_ty, max_tx, max_ty = self._visible_tile_bounds(game, margin=1)
        except Exception:
            return self._underwater_anchor_cache

        # Stable spatial hashing keeps the scene from visually reshuffling
        # every cache refresh while avoiding a random.Random allocation.
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                amount = float(water.get((tx, ty), 0.0) or 0.0)
                if amount < 0.18:
                    continue
                h = ((tx * 73856093) ^ (ty * 19349663)) & 0xFFFF
                if len(bubbles) < int(UNDERWATER_BUBBLE_MAX) and (h % 5) <= 2:
                    bubbles.append((int(tx), int(ty), (h % 997) / 997.0, amount))
                if len(grass) < int(UNDERWATER_GRASS_MAX) and amount >= 0.38:
                    try:
                        below_solid = (
                            ty + 1 >= int(game.world.height_tiles)
                            or bool(tile_def(game.world.get_tile(tx, ty + 1)).solid)
                        )
                    except Exception:
                        below_solid = False
                    # Sparse seagrass; no authored tile mutation.
                    if below_solid and (h % 7) in (0, 1, 3):
                        grass.append((int(tx), int(ty), (h % 613) / 613.0))
                if len(bubbles) >= int(UNDERWATER_BUBBLE_MAX) and len(grass) >= int(UNDERWATER_GRASS_MAX):
                    break
            if len(bubbles) >= int(UNDERWATER_BUBBLE_MAX) and len(grass) >= int(UNDERWATER_GRASS_MAX):
                break

        self._underwater_anchor_cache = (tuple(bubbles), tuple(grass))
        self._underwater_anchor_key = coarse
        self._underwater_anchor_time = float(now)
        return self._underwater_anchor_cache

    def _build_underwater_vfx_quads(self, game, now):
        # FIX40: bubbles/grass are ambience, not player-critical animation.
        # Rebuilding dozens of tiny Python tuples at 60 Hz was unnecessary in
        # the exact scene where CPU headroom is lowest. Cache at 30 Hz while
        # player/camera/weapon animation remains full-rate.
        if (
            self._underwater_vfx_cache
            and float(now) - float(self._underwater_vfx_cache_time) < float(self._underwater_vfx_interval)
        ):
            return self._underwater_vfx_cache
        bubbles, grass = self._refresh_underwater_anchors(game, now)
        quads = []
        water = getattr(getattr(game, "environment", None), "state", None)
        water = getattr(water, "water", {}) if water is not None else {}

        # Bubbles rise within real water cells.  At the top of the local cycle
        # they wrap rather than spawning/deleting Python objects.
        for tx, ty, phase, _cached_amount in bubbles:
            amount = float(water.get((tx, ty), 0.0) or 0.0)
            if amount < 0.12:
                continue
            travel = ((float(now) * (0.40 + phase * 0.22) + phase) % 1.0)
            bx = (tx + 0.22 + 0.56 * ((phase * 7.17) % 1.0)) * TILE_SIZE
            bottom = (ty + 1.0) * TILE_SIZE - 3.0
            top = max(ty * TILE_SIZE + 3.0, bottom - TILE_SIZE * max(0.22, amount))
            by = bottom - travel * max(3.0, bottom - top)
            size = 2.2 + 2.0 * ((phase * 11.0) % 1.0)
            quads.append(self._quad(bx, by, size, size, BUBBLE_RGBA))

        # Three tiny vertical segments approximate a flexible blade without
        # needing rotated geometry or an animation entity.
        for tx, ty, phase in grass:
            if float(water.get((tx, ty), 0.0) or 0.0) < 0.20:
                continue
            base_x = (tx + 0.5) * TILE_SIZE
            base_y = (ty + 1.0) * TILE_SIZE - 1.5
            total_h = 13.0 + 12.0 * ((phase * 5.31) % 1.0)
            seg_h = total_h / 3.0
            sway = math.sin(float(now) * 1.75 + phase * math.pi * 2.0) * 3.0
            for seg in range(3):
                frac = (seg + 0.5) / 3.0
                cx = base_x + sway * frac * frac
                cy = base_y - seg_h * (seg + 0.5)
                quads.append(self._quad(
                    cx, cy, 2.0 if seg < 2 else 1.5, seg_h + 0.8,
                    SEAGRASS_RGBA if seg < 2 else SEAGRASS_TIP_RGBA,
                ))
        self._underwater_vfx_cache = tuple(quads)
        self._underwater_vfx_cache_time = float(now)
        return self._underwater_vfx_cache

    def _build_rain_quads_fast(self, game):
        """60 Hz rain drawn from the underside of real conserved clouds.

        V0.7.5.2 rain used the full weather chunk height, so streaks appeared to
        start from arbitrary sky positions even though physical rain already
        consumed cloud_water.  V0.7.5.3 uses the exact cloud-bank layout for
        both X spread and Y origin.
        """
        env = game.environment.state
        visual_time = time.monotonic()
        quads = []
        max_total = 96

        for (cx, _cloud_cy), raw_cloud in tuple(getattr(env, "cloud_water", {}).items()):
            cloud_mass = max(0.0, float(raw_cloud))
            if cloud_mass < float(CLOUD_VISUAL_MIN_MASS):
                continue

            try:
                weather = game.environment.weather.weather_at_chunk(int(cx), 0)
            except Exception:
                weather = env.weather.get((int(cx), 0))
            rain = float(getattr(weather, "rain", 0.0)) if weather is not None else 0.0
            if rain <= 0.06:
                continue

            layout = self._cloud_visual_layout(game, int(cx), cloud_mass)
            if layout is None:
                continue
            _center_tx, _cloud_ty, cloud_x, cloud_y, fullness, _width_scale, cloud_width = layout

            # Horizontal cull only: cloud itself may be above the viewport while
            # its rain column still passes through the camera.
            if cloud_x + cloud_width*0.62 < game.camera_x or cloud_x - cloud_width*0.62 > game.camera_x + game.viewport_w:
                continue

            try:
                wind_x, _wind_y = game.environment.wind.wind_at_chunk(int(cx), 0)
            except Exception:
                wind_x, _wind_y = env.wind.get((int(cx), 0), (0.0, 0.0))

            if rain < RAIN_VISUAL_LIGHT_THRESHOLD:
                fall_speed = RAIN_VISUAL_LIGHT_SPEED
                streak_width = RAIN_VISUAL_STREAK_WIDTH_LIGHT
                base_length = RAIN_VISUAL_STREAK_LENGTH_LIGHT
                density = 0.62
                alpha_scale = 0.72
            else:
                fall_speed = RAIN_VISUAL_HEAVY_SPEED
                streak_width = RAIN_VISUAL_STREAK_WIDTH_HEAVY
                base_length = RAIN_VISUAL_STREAK_LENGTH_HEAVY
                density = 1.0
                alpha_scale = 0.90

            count = max(
                6,
                min(
                    22,
                    int(RAIN_VISUAL_PARTICLES_PER_CHUNK * density * (0.38 + rain * 0.55)),
                ),
            )

            rain_start_y = cloud_y + TILE_SIZE * (0.48 + 0.08*fullness)
            spread = cloud_width * 0.88
            for i in range(count):
                x_seed = (i * 0.61803398875 + int(cx) * 0.137) % 1.0
                rx = cloud_x + (x_seed - 0.5) * spread
                stop_y = self._rain_stop_y(game, rx)
                if stop_y <= rain_start_y + 3.0:
                    continue

                travel = max(8.0, stop_y - rain_start_y)
                # fall_speed is expressed in legacy normalized visual units;
                # scale it against actual cloud-to-ground travel so every drop
                # is born under the cloud rather than at a chunk boundary.
                y_phase = (
                    visual_time * fall_speed
                    + i * 0.171
                    + int(cx) * 0.083
                ) % 1.0
                ry = rain_start_y + y_phase * travel
                rx += float(wind_x) * y_phase * 0.22

                if ry >= stop_y:
                    continue
                if ry + base_length < game.camera_y or ry > game.camera_y + game.viewport_h:
                    continue

                streak_len = base_length * (0.82 + rain * 0.45)
                visible_h = min(streak_len, max(0.0, stop_y - ry))
                if visible_h < 4.0:
                    continue

                alpha = min(0.95, RAIN_VISUAL_ALPHA * alpha_scale * (0.82 + rain * 0.28))
                quads.append(self._quad(
                    rx, ry, streak_width, visible_h,
                    (RAIN_VISUAL_COLOR_R, RAIN_VISUAL_COLOR_G, RAIN_VISUAL_COLOR_B, alpha),
                ))
                if len(quads) >= max_total:
                    return quads

        return quads

    def _build_environment_quads(self, game, include_water=True):
        env = game.environment.state
        quads = []
        # FIX34: map-runtime magma and glow-moss decorations use a small
        # render-only pulse/wave.  FIX32 introduced those effects but forgot
        # to define the visual clock in this cached environment builder,
        # causing a repeated NameError("now") as soon as one of those
        # decorations became visible.
        now = time.monotonic()
        # V0.6.4.1: rain was moved to its own 60 Hz visual builder, but the
        # remaining wind/updraft VFX still use chunk pixel bounds.  Keep this
        # shared geometry constant local to the environment builder.
        chunk_px = CHUNK_SIZE * TILE_SIZE

        # Soil moisture overlay only exists while the terrain still exists.
        for (tx, ty), soil in self._iter_visible_table_items(env.soil, game):
            if not self._is_visible_tile(tx, ty, game):
                continue
            td = tile_def(game.world.get_tile(tx, ty))
            if not td.solid or td.water_absorption <= 0.0:
                continue
            m = max(0.0, min(1.0, float(soil.moisture)))
            if m < 0.18:
                continue
            rgba = (0.38 - 0.18*m, 0.26 + 0.20*m, 0.19 + 0.32*m, 0.62)
            # V0.7.6.2: moisture is stored inside the material, so its visual
            # indicator must hug the bottom of the remaining tile geometry.
            # Anchoring to ty*TILE_SIZE used to leave the strip floating in
            # mid-air for LOW/MID layered soil/sand tiles.
            band_h = 4.0 + 3.0*m
            bottom_y = (ty + 1) * TILE_SIZE - 2.0
            quads.append(self._quad(
                tx*TILE_SIZE + TILE_SIZE*0.5, bottom_y - band_h*0.5,
                TILE_SIZE - 8.0, band_h, rgba,
            ))
            if len(quads) >= METAL_MAX_ENV_QUADS:
                return quads


        # Wet ash gradually shifts from grey toward soil-brown during the
        # 5-second rehydration window. This is cosmetic; SoilSystem remains
        # authoritative and only changes ASH -> DIRT when the timer completes.
        for (tx, ty), wet_time in self._iter_visible_table_items(env.ash_wet_time, game):
            if not self._is_visible_tile(tx, ty, game):
                continue

            progress = max(
                0.0,
                min(
                    1.0,
                    float(wet_time) / max(0.001, ASH_REHYDRATE_SECONDS),
                ),
            )

            if progress <= ASH_REHYDRATE_BLEND_START:
                continue

            blend = (progress - ASH_REHYDRATE_BLEND_START) / max(
                0.001,
                1.0 - ASH_REHYDRATE_BLEND_START,
            )

            quads.append(
                self._quad(
                    tx * TILE_SIZE + TILE_SIZE * 0.5,
                    ty * TILE_SIZE + TILE_SIZE * 0.5,
                    TILE_SIZE - 2.0,
                    TILE_SIZE - 2.0,
                    (
                        0.40,
                        0.29,
                        0.19,
                        0.10 + 0.78 * blend,
                    ),
                )
            )

            # A darker damp band near the top makes the wetting direction
            # readable without changing any physics state.
            quads.append(
                self._quad(
                    tx * TILE_SIZE + TILE_SIZE * 0.5,
                    ty * TILE_SIZE + 5.0,
                    TILE_SIZE - 8.0,
                    6.0,
                    (0.24, 0.28, 0.30, 0.18 + 0.30 * blend),
                )
            )

            if len(quads) >= METAL_MAX_ENV_QUADS:
                return quads


        # --------------------------------------------------------
        # V0.7.5.2 gradual snow accumulation on newly exposed terrain.
        # The sparse cover is updated by WeatherSystem after mining exposes a
        # deeper alpine/polar surface. It is visual only; real water mass stays
        # in the existing hydrology reservoirs.
        # --------------------------------------------------------
        for (tx, ty), raw_cover in self._iter_visible_table_items(getattr(env, "snow_cover", {}), game):
            if not self._is_visible_tile(tx, ty, game):
                continue
            cover = max(0.0, min(1.0, float(raw_cover)))
            if cover <= 0.025:
                continue
            tile_id = game.world.get_tile(tx, ty)
            td = tile_def(tile_id)
            if not td.solid or tile_id == ICE:
                continue
            # Only the exposed top edge receives snow. The height grows from a
            # barely visible frost rim to a compact 6 px snow cap.
            top_y = ty * TILE_SIZE
            if tile_id in LAYERED_SOLID_TILES:
                try:
                    top_y += TILE_SIZE * soil_top_ratio(game.world.tile_layer_mask_at(tx, ty))
                except Exception:
                    pass
            cap_h = 1.2 + 5.2 * cover
            alpha = 0.28 + 0.66 * cover
            quads.append(self._quad(
                tx*TILE_SIZE + TILE_SIZE*0.5,
                top_y + cap_h*0.5,
                TILE_SIZE + 1.0,
                cap_h,
                (0.94, 0.975, 1.0, alpha),
            ))
            # A softer second strip avoids a hard white ruler-like edge.
            if cover > 0.30:
                quads.append(self._quad(
                    tx*TILE_SIZE + TILE_SIZE*0.5,
                    top_y + cap_h + 1.0,
                    TILE_SIZE*0.82,
                    2.0,
                    (0.80, 0.91, 0.98, 0.18 + 0.25*cover),
                ))
            if len(quads) >= METAL_MAX_ENV_QUADS:
                return quads


        # Water rendering is now Terraria-like:
        #
        # PHYSICS:
        #     authoritative tile volume remains in env.water.
        #
        # VISUAL:
        #     supported liquid is rendered as contiguous flat-height tiles;
        #     unsupported liquid is a waterfall ribbon plus small droplets.
        #
        # Particles are purely cosmetic. They never create or destroy mass.
        water_map = env.water if include_water else {}
        visual_time = time.monotonic()

        for (tx, ty), raw_amount in self._iter_visible_table_items(water_map, game):
            amount = float(raw_amount)

            if (
                amount < METAL_WATER_MIN_VISIBLE
                or not self._is_visible_tile(
                    tx,
                    ty,
                    game,
                )
            ):
                continue

            amount = max(
                0.0,
                min(
                    1.0,
                    amount,
                ),
            )

            below_solid = (
                tile_def(
                    game.world.get_tile(
                        tx,
                        ty + 1,
                    )
                ).solid
                if ty + 1
                < game.world.height_tiles
                else True
            )

            below_amount = float(
                water_map.get(
                    (tx, ty + 1),
                    0.0,
                )
            )

            above_amount = (
                float(
                    water_map.get(
                        (tx, ty - 1),
                        0.0,
                    )
                )
                if ty > 0
                else 0.0
            )

            falling = (
                not below_solid
                and below_amount
                < WATER_SUPPORT_THRESHOLD
            )

            tile_left = (
                tx
                * TILE_SIZE
            )

            tile_top = (
                ty
                * TILE_SIZE
            )

            tile_bottom = (
                (ty + 1)
                * TILE_SIZE
            )

            if falling:
                # Main waterfall ribbon.
                stream_w = (
                    TILE_SIZE
                    * max(
                        0.18,
                        min(
                            0.48,
                            WATER_STREAM_WIDTH
                            + amount
                            * 0.08,
                        ),
                    )
                )

                quads.append(
                    self._quad(
                        tile_left
                        + TILE_SIZE
                        * 0.5,
                        tile_top
                        + TILE_SIZE
                        * 0.5,
                        stream_w,
                        TILE_SIZE
                        + 2.0,
                        (
                            0.12,
                            0.49,
                            0.88,
                            0.53
                            + amount
                            * 0.18,
                        ),
                    )
                )

                # Inner highlight.
                quads.append(
                    self._quad(
                        tile_left
                        + TILE_SIZE
                        * 0.46,
                        tile_top
                        + TILE_SIZE
                        * 0.5,
                        max(
                            1.4,
                            stream_w
                            * 0.12,
                        ),
                        TILE_SIZE
                        - 3.0,
                        (
                            0.66,
                            0.91,
                            1.0,
                            0.36,
                        ),
                    )
                )

                # Cosmetic droplets moving downward inside/near the waterfall.
                for particle_i in range(
                    int(
                        WATER_FALL_PARTICLE_COUNT
                    )
                ):
                    seed = (
                        tx
                        * 0.371
                        + ty
                        * 0.173
                        + particle_i
                        * 0.289
                    )

                    phase = (
                        visual_time
                        * (
                            WATER_FALL_PARTICLE_SPEED
                            / TILE_SIZE
                        )
                        + seed
                    ) % 1.0

                    px = (
                        tile_left
                        + TILE_SIZE
                        * 0.5
                        + (
                            particle_i
                            - (
                                WATER_FALL_PARTICLE_COUNT
                                - 1
                            )
                            * 0.5
                        )
                        * 3.0
                    )

                    py = (
                        tile_top
                        + phase
                        * TILE_SIZE
                    )

                    quads.append(
                        self._quad(
                            px,
                            py,
                            2.2,
                            5.5,
                            (
                                0.58,
                                0.88,
                                1.0,
                                0.68,
                            ),
                        )
                    )

                if (
                    len(quads)
                    >= METAL_MAX_ENV_QUADS
                ):
                    return quads

                continue

            if (
                above_amount
                >= METAL_WATER_MIN_VISIBLE
            ):
                # Interior water tile.
                quads.append(
                    self._quad(
                        tile_left
                        + TILE_SIZE
                        * 0.5,
                        tile_top
                        + TILE_SIZE
                        * 0.5,
                        TILE_SIZE
                        + 1.2,
                        TILE_SIZE
                        + 1.2,
                        (
                            0.10,
                            0.44,
                            0.82,
                            0.64,
                        ),
                    )
                )

                if (
                    len(quads)
                    >= METAL_MAX_ENV_QUADS
                ):
                    return quads

                continue

            # Surface water.
            #
            # V0.4.9 interpolated across very different neighboring volumes.
            # That made giant diagonal blue wedges. Terraria-style liquid is
            # much clearer if each tile owns a flat height and the simulation
            # itself equalizes neighboring volumes.
            h = max(
                1.4,
                TILE_SIZE
                * amount,
            )
            biome_id = None
            try:
                biome_sys = (getattr(game, "biomes", None) or getattr(game, "biome_system", None))
                if biome_sys is not None:
                    biome_id = str(biome_sys.biome_at_tile(int(tx)))
            except Exception:
                biome_id = None
            ocean_wave = 0.0
            if biome_id == "ocean":
                ocean_wave = math.sin(visual_time * 5.2 + tx * 0.45) * 1.5
                h = max(2.0, h + ocean_wave * 0.35)

            quads.append(
                self._quad(
                    tile_left
                    + TILE_SIZE
                    * 0.5,
                    tile_bottom
                    - h
                    * 0.5,
                    TILE_SIZE
                    + 1.4,
                    h,
                    (
                        0.12,
                        0.49,
                        0.87,
                        0.61
                        + 0.10
                        * amount,
                    ),
                )
            )

            # One flat surface highlight per surface tile.
            quads.append(
                self._quad(
                    tile_left
                    + TILE_SIZE
                    * 0.5,
                    tile_bottom
                    - h
                    + 1.0 + ocean_wave * 0.20,
                    TILE_SIZE
                    + 1.3,
                    2.0,
                    (
                        0.66,
                        0.91,
                        1.0,
                        0.72,
                    ),
                )
            )
            if biome_id == "ocean":
                quads.append(self._quad(
                    tile_left + TILE_SIZE*0.26,
                    tile_bottom - h + 0.6 + ocean_wave*0.25,
                    TILE_SIZE*0.20, 1.6,
                    (0.90, 0.97, 1.0, 0.58),
                ))
                quads.append(self._quad(
                    tile_left + TILE_SIZE*0.70,
                    tile_bottom - h + 1.4 - ocean_wave*0.18,
                    TILE_SIZE*0.24, 1.4,
                    (0.90, 0.97, 1.0, 0.48),
                ))

            # Tiny edge drop only when this tile is meaningfully higher than
            # the neighbor. It is cosmetic and intentionally subtle.
            for direction in (-1, 1):
                neighbor_amount = float(
                    water_map.get(
                        (
                            tx + direction,
                            ty,
                        ),
                        0.0,
                    )
                )

                drop = (
                    amount
                    - neighbor_amount
                )

                if (
                    drop
                    > WATER_SURFACE_SLOPE_LIMIT
                    and neighbor_amount
                    < amount
                    * 0.55
                ):
                    edge_x = (
                        tile_left
                        if direction < 0
                        else tile_left
                        + TILE_SIZE
                    )

                    quads.append(
                        self._quad(
                            edge_x,
                            tile_bottom
                            - h
                            + min(
                                8.0,
                                h
                                * 0.20,
                            ),
                            2.0,
                            min(
                                12.0,
                                max(
                                    3.0,
                                    h
                                    * 0.30,
                                ),
                            ),
                            (
                                0.48,
                                0.83,
                                1.0,
                                0.48,
                            ),
                        )
                    )

            if (
                len(quads)
                >= METAL_MAX_ENV_QUADS
            ):
                return quads

        # --------------------------------------------------------
        # V0.7.5.2 broad atmospheric vapor + cloud visualization.
        #
        # Natural evaporation is stored at chunk scale in env.vapor. Instead
        # of one narrow vertical steam pillar, draw a diffuse horizontal haze
        # that rises over a wide area. Fire-created steam remains a separate
        # reactive effect and can still look concentrated.
        # --------------------------------------------------------
        for (vapor_cx, _vapor_cy), raw_vapor in tuple(getattr(env, "evaporation_haze", {}).items()):
            amount = max(0.0, float(raw_vapor))
            if amount <= 0.08:
                continue
            center_tx = int(vapor_cx) * CHUNK_SIZE + CHUNK_SIZE // 2
            if center_tx < 0 or center_tx >= game.world.width_tiles:
                continue
            # Do not draw broad near-surface vapor plumes over permanent open-water basins.
            biome_id = None
            try:
                biome_sys = (getattr(game, "biomes", None) or getattr(game, "biome_system", None))
                if biome_sys is not None:
                    biome_id = str(biome_sys.biome_at_tile(center_tx))
            except Exception:
                biome_id = None
            if biome_id in ("ocean", "lake"):
                continue
            try:
                surface_ty = int(game.world.first_solid_row(center_tx))
            except Exception:
                surface_ty = max(8, game.world.height_tiles // 4)
            fullness = max(0.0, min(1.0, amount / 1.6))
            # Haze occupies several tiles horizontally and several gentle
            # layers vertically; offsets are deterministic so it does not
            # shimmer or require particle allocations. Lift it above the surface
            # so steam does not appear trapped inside the lake body.
            base_x = (center_tx + 0.5) * TILE_SIZE
            base_y = max(1.0, (surface_ty - 4.6) * TILE_SIZE)
            haze = (
                (-2.5, -0.2, 4.8, 0.26, 0.13),
                (-0.6, -0.9, 5.6, 0.30, 0.11),
                ( 1.5, -1.6, 4.2, 0.24, 0.09),
                (-1.8, -2.3, 3.6, 0.20, 0.07),
            )
            for ox, oy, w_tiles, h_tiles, a0 in haze:
                quads.append(self._quad(
                    base_x + ox*TILE_SIZE,
                    base_y + oy*TILE_SIZE,
                    w_tiles*TILE_SIZE*(0.72 + 0.42*fullness),
                    max(3.0, h_tiles*TILE_SIZE),
                    (0.86, 0.93, 0.98, a0 + 0.16*fullness),
                ))
                if len(quads) >= METAL_MAX_ENV_QUADS:
                    return quads

        # Conserved cloud water is drawn as a broad cloud bank. A single cloud
        # column already spans ~6-9 tiles; neighboring columns overlap into a
        # continuous front rather than a sequence of tiny isolated puffs.
        for (cloud_cx, _cloud_cy), raw_cloud in tuple(getattr(env, "cloud_water", {}).items()):
            cloud_mass = max(0.0, float(raw_cloud))
            if cloud_mass < float(CLOUD_VISUAL_MIN_MASS):
                continue

            layout = self._cloud_visual_layout(game, int(cloud_cx), cloud_mass)
            if layout is None:
                continue
            center_tx, cloud_ty, cx_px, cy_px, fullness, width_scale, _cloud_width = layout

            # Use a generous visibility halo because the bank is intentionally
            # much wider than its chunk center.
            if not (
                self._is_visible_tile(center_tx, cloud_ty, game, margin=7)
                or self._is_visible_tile(center_tx - 5, cloud_ty, game, margin=7)
                or self._is_visible_tile(center_tx + 5, cloud_ty, game, margin=7)
            ):
                continue

            alpha = 0.20 + 0.50 * fullness
            shade = 0.985 - 0.20 * fullness

            parts = (
                (-3.10,  0.22, 3.10, 0.58),
                (-2.05, -0.02, 3.65, 0.88),
                (-0.75, -0.18, 4.10, 1.05),
                ( 0.70, -0.13, 4.25, 1.02),
                ( 2.20,  0.00, 3.70, 0.82),
                ( 3.45,  0.20, 2.70, 0.56),
                ( 0.15,  0.42, 6.80, 0.42),
            )
            for ox, oy, w_tiles, h_tiles in parts:
                quads.append(self._quad(
                    cx_px + ox*TILE_SIZE,
                    cy_px + oy*TILE_SIZE,
                    w_tiles*TILE_SIZE*width_scale,
                    h_tiles*TILE_SIZE,
                    (shade, shade, min(1.0, shade + 0.025), alpha),
                ))
                if len(quads) >= METAL_MAX_ENV_QUADS:
                    return quads

        # --------------------------------------------------------
        # Rain visual is intentionally excluded from this slow environment
        # cache in V0.6.4. It is rebuilt by _build_rain_quads_fast() at the
        # display cadence so rainfall no longer moves in 30 Hz steps.

        # --------------------------------------------------------
        # V0.7.0 sparse reactive element visualization. These loops scale
        # with visible active element cells only, never total world size.
        # --------------------------------------------------------
        reactive_styles = (
            ("steam", (0.82, 0.90, 0.96, 0.34), 0.68),
            ("poison_gas", (0.72, 0.55, 0.88, 0.46), 0.82),
            ("acid", (0.70, 0.92, 0.18, 0.64), 0.88),
            ("poison_liquid", (0.50, 0.18, 0.68, 0.66), 0.86),
            ("electric_shock", (1.00, 0.90, 0.45, 0.72), 0.42),
        )
        for field_name, base_rgba, size_scale in reactive_styles:
            table = getattr(env, field_name, {})
            for (tx, ty), raw in self._iter_visible_table_items(table, game):
                if not self._is_visible_tile(tx, ty, game):
                    continue
                amount = max(0.0, min(1.0, float(raw)))
                if amount <= 0.015:
                    continue
                w = TILE_SIZE * float(size_scale)
                h = w
                if field_name in ("steam", "poison_gas"):
                    h *= 0.76
                rgba = (base_rgba[0], base_rgba[1], base_rgba[2], base_rgba[3] * (0.55 + 0.45 * amount))
                quads.append(self._quad(
                    tx*TILE_SIZE + TILE_SIZE*0.5,
                    ty*TILE_SIZE + TILE_SIZE*0.5,
                    w, h, rgba,
                ))
                if field_name == "electric_shock":
                    quads.append(self._quad(
                        tx*TILE_SIZE + TILE_SIZE*0.5,
                        ty*TILE_SIZE + TILE_SIZE*0.5,
                        max(2.0, TILE_SIZE*0.08), TILE_SIZE*0.78,
                        (1.00,0.95,0.62,0.84),
                    ))
                if len(quads) >= METAL_MAX_ENV_QUADS:
                    return quads

        # --------------------------------------------------------
        # Airflow / wind tracer particles
        # --------------------------------------------------------
        # V0.4.17:
        #   weak / medium / strong wind traces persist for roughly
        #   2 / 4 / 8 seconds and scale in density / width / length.
        # These particles do not apply force. They only visualize the real
        # WindSystem vector, including evaporation-generated updraft.
        for (
            cx,
            cy
        ), vector in env.wind.items():
            try:
                wind_x, wind_y = vector
            except Exception:
                continue

            speed = math.hypot(
                float(
                    wind_x
                ),
                float(
                    wind_y
                ),
            )

            updraft = float(
                env.updraft.get(
                    (
                        cx,
                        cy,
                    ),
                    0.0,
                )
            )

            if (
                speed
                < WIND_VISUAL_MIN_SPEED
                and updraft
                < 2.0
            ):
                continue

            chunk_left = (
                cx
                * chunk_px
            )
            chunk_top = (
                cy
                * chunk_px
            )
            chunk_right = (
                chunk_left
                + chunk_px
            )
            chunk_bottom = (
                chunk_top
                + chunk_px
            )

            if (
                chunk_right
                < game.camera_x
                or chunk_left
                > game.camera_x
                + game.viewport_w
                or chunk_bottom
                < game.camera_y
                or chunk_top
                > game.camera_y
                + game.viewport_h
            ):
                continue

            normalized = min(
                1.0,
                max(
                    0.0,
                    speed
                    / 145.0,
                ),
            )

            up_norm = min(
                1.0,
                updraft / 32.0,
            )

            visual_strength = max(
                normalized,
                up_norm,
            )

            if visual_strength < 0.34:
                duration = WIND_VISUAL_WEAK_DURATION
                trace_w = WIND_VISUAL_TRACE_WIDTH_WEAK
                trace_len = WIND_VISUAL_TRACE_LENGTH_WEAK
                density_scale = WIND_VISUAL_DENSITY_WEAK
            elif visual_strength < 0.68:
                duration = WIND_VISUAL_MEDIUM_DURATION
                trace_w = WIND_VISUAL_TRACE_WIDTH_MEDIUM
                trace_len = WIND_VISUAL_TRACE_LENGTH_MEDIUM
                density_scale = WIND_VISUAL_DENSITY_MEDIUM
            else:
                duration = WIND_VISUAL_STRONG_DURATION
                trace_w = WIND_VISUAL_TRACE_WIDTH_STRONG
                trace_len = WIND_VISUAL_TRACE_LENGTH_STRONG
                density_scale = WIND_VISUAL_DENSITY_STRONG

            count = max(
                5,
                int(
                    WIND_VISUAL_PARTICLES_PER_CHUNK
                    * density_scale
                    * (
                        0.42
                        + visual_strength
                        * 0.78
                    )
                ),
            )
            if PERF_FAST_WIND_VFX:
                count = min(count, int(PERF_WIND_VFX_MAX_PARTICLES_PER_CHUNK))

            for i in range(
                count
            ):
                seed_x = (
                    i
                    * 0.754877666
                    + cx
                    * 0.193
                    + cy
                    * 0.047
                ) % 1.0

                seed_y = (
                    i
                    * 0.569840291
                    + cy
                    * 0.211
                    + cx
                    * 0.083
                ) % 1.0

                age = (
                    visual_time
                    + i
                    * 0.137
                    + cx
                    * 0.091
                    + cy
                    * 0.053
                ) % duration

                life = age / max(
                    0.001,
                    duration,
                )

                origin_x = (
                    chunk_left
                    + seed_x
                    * chunk_px
                )

                origin_y = (
                    chunk_top
                    + seed_y
                    * chunk_px
                )

                # Advect the tracer using the LOCAL wind at its origin rather
                # than the raw chunk wind. A tracer born behind a wall no
                # longer slides horizontally as if the wall were transparent.
                if PERF_FAST_WIND_VFX:
                    if game.environment.is_underground_world(origin_x, origin_y):
                        origin_wind_x = 0.0
                        origin_wind_y = min(0.0, float(wind_y))
                    else:
                        origin_wind_x, origin_wind_y = float(wind_x), float(wind_y)
                else:
                    origin_wind_x, origin_wind_y = (
                        game.environment.wind_at_world(
                            origin_x,
                            origin_y,
                        )
                    )

                px = (
                    chunk_left
                    + (
                        seed_x
                        * chunk_px
                        + origin_wind_x
                        * age
                        * WIND_VISUAL_SPEED_SCALE
                    )
                    % chunk_px
                )

                py = (
                    chunk_top
                    + (
                        seed_y
                        * chunk_px
                        + origin_wind_y
                        * age
                        * WIND_VISUAL_SPEED_SCALE
                    )
                    % chunk_px
                )

                if PERF_FAST_WIND_VFX:
                    if game.environment.is_underground_world(px, py):
                        local_wind_x = 0.0
                        local_wind_y = min(0.0, float(wind_y))
                    else:
                        local_wind_x, local_wind_y = float(wind_x), float(wind_y)
                else:
                    local_wind_x, local_wind_y = (
                        game.environment.wind_at_world(
                            px,
                            py,
                        )
                    )

                local_speed = math.hypot(
                    float(
                        local_wind_x
                    ),
                    float(
                        local_wind_y
                    ),
                )

                if (
                    local_speed
                    < WIND_VISUAL_MIN_SPEED
                    * 0.34
                ):
                    continue

                local_ratio = min(
                    1.0,
                    local_speed
                    / max(
                        WIND_VISUAL_MIN_SPEED,
                        speed,
                    ),
                )

                # Soft fade in/out but keep lines visible for the requested
                # weak/medium/strong lifetime.
                fade = (
                    0.55
                    + 0.45
                    * (
                        1.0
                        - abs(
                            2.0
                            * life
                            - 1.0
                        )
                    )
                )

                if abs(
                    local_wind_x
                ) >= abs(
                    local_wind_y
                ):
                    pw = trace_len
                    ph = trace_w

                    trace_x0 = (
                        px
                        - pw
                        * 0.5
                    )

                    trace_y0 = py

                    trace_x1 = (
                        px
                        + pw
                        * 0.5
                    )

                    trace_y1 = py
                else:
                    pw = trace_w
                    ph = trace_len

                    trace_x0 = px

                    trace_y0 = (
                        py
                        - ph
                        * 0.5
                    )

                    trace_x1 = px

                    trace_y1 = (
                        py
                        + ph
                        * 0.5
                    )

                # Never draw an airflow line on top of / through a solid tile.
                # This is a visual impermeability rule matching the physics.
                if (
                    not PERF_FAST_WIND_VFX
                    and game.environment.wind_segment_hits_solid(
                        trace_x0,
                        trace_y0,
                        trace_x1,
                        trace_y1,
                    )
                ):
                    continue

                alpha = min(
                    0.82,
                    WIND_VISUAL_ALPHA
                    * (
                        0.90
                        + visual_strength
                        * 0.85
                    )
                    * fade
                    * max(
                        0.18,
                        local_ratio,
                    ),
                )

                quads.append(
                    self._quad(
                        px,
                        py,
                        pw,
                        ph,
                        (
                            0.92,
                            0.96,
                            1.0,
                            alpha,
                        ),
                    )
                )

                if (
                    len(
                        quads
                    )
                    >= METAL_MAX_ENV_QUADS
                ):
                    return quads

        # --------------------------------------------------------
        # FIX74 honey: an independent viscous liquid. It is intentionally
        # amber rather than routed through the blue water batch, and it has no
        # wave/temperature/phase rendering state.
        # --------------------------------------------------------
        try:
            _honey_table=game.environment.state.honey
        except Exception:
            _honey_table={}
        for (tx,ty),amount in self._iter_visible_table_items(_honey_table,game):
            amount=max(0.0,min(1.0,float(amount)))
            if amount < 0.015:continue
            h=max(2.0,TILE_SIZE*amount)
            cx=tx*TILE_SIZE+TILE_SIZE*0.5
            cy=(ty+1)*TILE_SIZE-h*0.5
            quads.append(self._quad(cx,cy,TILE_SIZE+0.8,h+0.5,(0.88,0.53,0.055,0.90)))
            if float(_honey_table.get((int(tx),int(ty)-1),0.0))<=0.015:
                quads.append(self._quad(cx,cy-h*0.5+2.0,TILE_SIZE*0.90,3.8,(1.0,0.78,0.18,0.90)))
            if len(quads)>=METAL_MAX_ENV_QUADS:return quads

        # --------------------------------------------------------
        # FIX35 dynamic lava. Flat volume surface: deliberately NO water-style
        # wave displacement. Cooling interpolates orange magma toward dark
        # igneous rock before the simulation commits the solid tile.
        # The main lava body is behind actors. FIX37 adds only the *free-surface*
        # lip to the dynamic foreground pass so feet can be visibly immersed.
        # --------------------------------------------------------
        try:
            _lava_table=game.environment.state.lava
            _cool_table=game.environment.state.lava_cooling
        except Exception:
            _lava_table={} ; _cool_table={}
        for (tx,ty),amount in self._iter_visible_table_items(_lava_table,game):
            amount=max(0.0,min(1.0,float(amount)))
            if amount < 0.015:continue
            cool=max(0.0,min(1.0,float(_cool_table.get((int(tx),int(ty)),0.0))))
            # orange -> dark basalt during quench
            hot=(0.92,0.18,0.025); cold=(0.22,0.20,0.20)
            r=hot[0]*(1.0-cool)+cold[0]*cool
            g=hot[1]*(1.0-cool)+cold[1]*cool
            b=hot[2]*(1.0-cool)+cold[2]*cool
            h=max(2.0,TILE_SIZE*amount)
            cx=tx*TILE_SIZE+TILE_SIZE*0.5
            cy=(ty+1)*TILE_SIZE-h*0.5
            quads.append(self._quad(cx,cy,TILE_SIZE+0.8,h+0.5,(r,g,b,0.96)))
            if cool < 0.90 and h>5.0:
                glow=(1.0-cool)*0.86
                quads.append(self._quad(cx,cy-h*0.5+2.3,TILE_SIZE*0.88,4.0,(1.0,0.46,0.055,0.35+0.55*glow)))
            if len(quads)>=METAL_MAX_ENV_QUADS:return quads

        # --------------------------------------------------------
        # Map-editor runtime overlays: swamp hazards / decorative stones.
        # --------------------------------------------------------
        map_runtime = getattr(
            game,
            "map_runtime",
            None,
        )

        if map_runtime is not None:
            # AssetEditor -> MapEditor custom tiles. Collision metadata and
            # rendering share the exact same cell table, preventing invisible
            # custom walls.
            for (tx,ty),asset_id in getattr(map_runtime,"custom_map_cells",{}).items():
                if not self._is_visible_tile(tx,ty,game):continue
                custom=self._pixel_asset_quads(game,str(asset_id),tx*TILE_SIZE,ty*TILE_SIZE,TILE_SIZE,TILE_SIZE)
                if custom is not None:
                    quads.extend(custom)
                    if len(quads)>=METAL_MAX_ENV_QUADS:return quads

            for (
                tx,
                ty
            ), hazard in map_runtime.hazards.items():
                if not self._is_visible_tile(tx, ty, game):
                    continue
                if hazard == "swamp":
                    quads.append(self._quad(
                        tx*TILE_SIZE+TILE_SIZE*0.5, ty*TILE_SIZE+TILE_SIZE*0.72,
                        TILE_SIZE*0.92, TILE_SIZE*0.48, (0.18,0.34,0.14,0.72),
                    ))
                elif hazard == "lava":
                    # Legacy maps are migrated to env.lava by MapLoader. Keep
                    # this branch intentionally empty to avoid double drawing.
                    continue

            for (
                tx,
                ty
            ), decoration in map_runtime.decorations.items():
                if not self._is_visible_tile(
                    tx,
                    ty,
                    game,
                ):
                    continue

                kind, size = decoration

                size = max(1, min(3, int(size)))
                try:
                    deco_ground_y = float(game.world.surface_anchor_y(tx, ty))
                except Exception:
                    deco_ground_y = float((ty + 1) * TILE_SIZE)
                bx = tx * TILE_SIZE + TILE_SIZE * 0.5

                # FIX90 user-edited decoration image overrides its procedural
                # silhouette, with the same surface feet anchor.
                _edited_decor=self._pixel_asset_quads(game,"decoration."+str(kind),bx-20,deco_ground_y-40,40,40,visual_scale=(.46,.70,1.0)[size-1])
                if _edited_decor is not None:
                    quads.extend(_edited_decor[:max(0,METAL_MAX_ENV_QUADS-len(quads))])
                    if len(quads)>=METAL_MAX_ENV_QUADS:return quads
                    continue

                if kind == "stone":
                    w = {1: 10.0, 2: 18.0, 3: 28.0}[size]
                    h = {1: 7.0, 2: 12.0, 3: 17.0}[size]
                    quads.append(self._quad(
                        bx, deco_ground_y - h * 0.5, w, h,
                        (0.44, 0.46, 0.49, 1.0),
                    ))

                elif kind == "fence":
                    # FIX27 dungeon jail bars: thin iron bars plus two rails.
                    bar_h = TILE_SIZE * (0.72 + 0.06 * (size - 1))
                    base_y = deco_ground_y - bar_h * 0.5
                    for ox in (-11.0, 0.0, 11.0):
                        quads.append(self._quad(
                            bx + ox, base_y, 3.0, bar_h,
                            (0.34, 0.35, 0.37, 1.0),
                        ))
                    quads.append(self._quad(
                        bx, deco_ground_y - bar_h * 0.28, 29.0, 3.0,
                        (0.40, 0.41, 0.43, 1.0),
                    ))
                    quads.append(self._quad(
                        bx, deco_ground_y - bar_h * 0.72, 29.0, 3.0,
                        (0.40, 0.41, 0.43, 1.0),
                    ))

                elif kind == "lamp":
                    # Wall torch / dungeon lamp. It is visual-only; FireSystem
                    # remains the authoritative source for gameplay fire.
                    quads.append(self._quad(
                        bx, deco_ground_y - 12.0, 4.0, 19.0,
                        (0.38, 0.25, 0.13, 1.0),
                    ))
                    quads.append(self._quad(
                        bx, deco_ground_y - 25.0, 10.0, 12.0,
                        (0.95, 0.56, 0.15, 0.95),
                    ))
                    quads.append(self._quad(
                        bx, deco_ground_y - 27.0, 5.0, 7.0,
                        (1.0, 0.86, 0.34, 1.0),
                    ))

                elif kind == "house":
                    w=38.0+8.0*(size-1); h=30.0+7.0*(size-1)
                    quads.append(self._quad(bx,deco_ground_y-h*0.50,w,h,(0.48,0.29,0.16,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-h-7.0,w*1.12,14.0,(0.30,0.18,0.12,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-10.0,9.0,20.0,(0.22,0.14,0.10,1.0)))

                elif kind == "pyramid_exterior":
                    # FIX45 legacy compatibility: deliberately render nothing.
                    # The pyramid silhouette now consists exclusively of real
                    # foreground terrain, which is the same geometry used by
                    # collision/climbing/mining. Old map saves may still carry
                    # this decoration ID, but it must never recreate a second
                    # non-physical hull.
                    continue
                elif kind in ("egypt_column", "hieroglyph", "sarcophagus", "sphinx_statue"):
                    colors={"egypt_column":(.66,.49,.29,1),"hieroglyph":(.20,.62,.68,1),"sarcophagus":(.73,.52,.25,1),"sphinx_statue":(.62,.46,.28,1)}
                    h={"egypt_column":54,"hieroglyph":36,"sarcophagus":44,"sphinx_statue":32}[kind]
                    w={"egypt_column":14,"hieroglyph":22,"sarcophagus":24,"sphinx_statue":52}[kind]
                    quads.append(self._quad(bx,deco_ground_y-h*.5,w,h,colors[kind]))
                elif kind == "brazier":
                    pulse=.82+.18*math.sin(float(now)*4.1+tx*.61);quads.append(self._quad(bx,deco_ground_y-5,21,8,(.34,.21,.13,1)));quads.append(self._quad(bx,deco_ground_y-16,12,20,(1,.48,.10,.85*pulse)))

                elif kind == "glow_moss":
                    pulse=0.82+0.18*math.sin(float(now)*2.2+tx*0.37)
                    quads.append(self._quad(bx,deco_ground_y-3.0,TILE_SIZE*(0.55+0.10*size),6.0,(0.12,0.55,0.38,0.88)))
                    quads.append(self._quad(bx-7.0,deco_ground_y-8.0,7.0,8.0,(0.25,0.95,0.68,0.72*pulse)))
                    quads.append(self._quad(bx+8.0,deco_ground_y-6.0,5.0,6.0,(0.38,1.00,0.76,0.62*pulse)))

                elif kind == "crystal":
                    h=20.0+10.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*0.48,10.0+4.0*size,h,(0.36,0.72,1.00,0.88)))
                    quads.append(self._quad(bx-10.0,deco_ground_y-h*0.35,7.0,h*0.68,(0.62,0.88,1.00,0.90)))
                    quads.append(self._quad(bx+11.0,deco_ground_y-h*0.27,6.0,h*0.52,(0.48,0.62,1.00,0.82)))

                elif kind == "root_arch":
                    h=34.0+8.0*size;w=26.0+7.0*size;col=(.20,.38,.27,1.0)
                    quads.append(self._quad(bx-w*.42,deco_ground_y-h*.45,6.0,h*.90,col))
                    quads.append(self._quad(bx+w*.42,deco_ground_y-h*.45,6.0,h*.90,col))
                    quads.append(self._quad(bx,deco_ground_y-h+3.0,w,7.0,col))
                    quads.append(self._quad(bx,deco_ground_y-h+4.0,w*.44,3.0,(.31,.84,.59,.52)))

                elif kind == "mine_support":
                    h=34.0+5.0*size;w=27.0+5.0*size;wood=(.34,.23,.14,1.0);edge=(.51,.34,.18,1.0)
                    quads.append(self._quad(bx-w*.42,deco_ground_y-h*.48,6.0,h,wood))
                    quads.append(self._quad(bx+w*.42,deco_ground_y-h*.48,6.0,h,wood))
                    quads.append(self._quad(bx,deco_ground_y-h+2.0,w,7.0,edge))
                    quads.append(self._quad(bx,deco_ground_y-h*.52,w*.82,3.0,wood))

                elif kind == "crystal_lantern":
                    pulse=.70+.25*math.sin(float(now)*2.8+tx*.43)
                    quads.append(self._quad(bx,deco_ground_y-17.0,4.0,24.0,(.25,.28,.34,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-31.0,13.0,15.0,(.38,.82,1.0,.42*pulse)))
                    quads.append(self._quad(bx,deco_ground_y-31.0,6.0,10.0,(.76,.96,1.0,pulse)))

                elif kind == "castle_banner":
                    h=28.0+7.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.55,17.0,h,(.38,.10,.18,.96)))
                    quads.append(self._quad(bx,deco_ground_y-h+1.0,23.0,4.0,(.55,.48,.34,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-h*.55,5.0,9.0,(.72,.44,.20,.92)))

                elif kind == "stone_throne":
                    quads.append(self._quad(bx,deco_ground_y-24.0,31.0,48.0,(.29,.29,.34,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-13.0,21.0,19.0,(.39,.37,.43,1.0)))
                    quads.append(self._quad(bx-17.0,deco_ground_y-9.0,7.0,18.0,(.22,.22,.27,1.0)))
                    quads.append(self._quad(bx+17.0,deco_ground_y-9.0,7.0,18.0,(.22,.22,.27,1.0)))

                elif kind in ("rune_obelisk","abyss_altar"):
                    pulse=.58+.32*math.sin(float(now)*2.2+tx*.29)
                    if kind=="rune_obelisk":
                        h=34.0+8.0*size
                        quads.append(self._quad(bx,deco_ground_y-h*.5,13.0,h,(.22,.16,.27,1.0)))
                        quads.append(self._quad(bx,deco_ground_y-h*.62,5.0,14.0,(.82,.25,.58,pulse)))
                    else:
                        quads.append(self._quad(bx,deco_ground_y-7.0,50.0,14.0,(.20,.13,.24,1.0)))
                        quads.append(self._quad(bx,deco_ground_y-17.0,28.0,8.0,(.42,.18,.39,1.0)))
                        quads.append(self._quad(bx,deco_ground_y-28.0,10.0,18.0,(.82,.24,.61,.56*pulse)))

                elif kind == "magma_totem":
                    pulse=.72+.24*math.sin(float(now)*3.4+tx*.51);h=31.0+8.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.48,17.0,h,(.18,.15,.16,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-h*.56,7.0,h*.45,(1.0,.24,.04,.55*pulse)))
                    quads.append(self._quad(bx,deco_ground_y-h+2.0,24.0,7.0,(.42,.18,.12,1.0)))

                elif kind == "giant_mushroom":
                    pulse=.72+.22*math.sin(float(now)*1.9+tx*.41)
                    h=31.0+9.0*size;w=31.0+8.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.38,8.0+2.0*size,h*.76,(.62,.77,.61,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-h*.78,w,12.0+3.0*size,(.20,.72,.55,.98)))
                    quads.append(self._quad(bx,deco_ground_y-h*.86,w*.62,7.0,(.46,1.0,.76,.50*pulse)))
                    for ox in (-w*.27,0.0,w*.27):
                        quads.append(self._quad(bx+ox,deco_ground_y-h*.80,4.0,4.0,(.78,1.0,.88,pulse)))

                elif kind == "hanging_glow_vine":
                    pulse=.60+.28*math.sin(float(now)*2.1+tx*.33);h=38.0+11.0*size
                    for i,ox in enumerate((-10.0,0.0,11.0)):
                        vh=h-(i%2)*12.0
                        quads.append(self._quad(bx+ox,deco_ground_y-vh*.55,3.0,vh,(.17,.55,.39,.96)))
                        quads.append(self._quad(bx+ox+(-4.0 if i%2 else 4.0),deco_ground_y-vh*.36,7.0,4.0,(.30,.92,.61,.78)))
                        quads.append(self._quad(bx+ox,deco_ground_y-vh+2.0,6.0,7.0,(.64,1.0,.78,pulse)))

                elif kind == "spore_pod":
                    pulse=.58+.34*math.sin(float(now)*2.6+tx*.47);h=17.0+5.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.36,4.0,h*.72,(.24,.56,.38,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-h,15.0+3.0*size,17.0+3.0*size,(.33,.83,.58,.84)))
                    quads.append(self._quad(bx,deco_ground_y-h,7.0+size,8.0+size,(.79,1.0,.82,pulse)))
                    for ox,oy in ((-12,-9),(13,-15),(-7,-25)):
                        quads.append(self._quad(bx+ox,deco_ground_y-h+oy,3.0,3.0,(.66,1.0,.77,.50*pulse)))

                elif kind == "moss_stalactite":
                    h=42.0+10.0*size
                    for ox,scale in ((-12.0,.72),(0.0,1.0),(13.0,.58)):
                        sh=h*scale
                        quads.append(self._quad(bx+ox,deco_ground_y-h+sh*.46,8.0,sh,(.19,.40,.33,1.0)))
                        quads.append(self._quad(bx+ox,deco_ground_y-h+sh*.92,4.0,7.0,(.34,.91,.63,.76)))

                elif kind == "root_cluster":
                    h=35.0+8.0*size;root=(.22,.39,.25,1.0)
                    quads.append(self._quad(bx,deco_ground_y-h*.48,9.0,h,root))
                    quads.append(self._quad(bx-13.0,deco_ground_y-h*.36,5.0,h*.62,root))
                    quads.append(self._quad(bx+15.0,deco_ground_y-h*.29,6.0,h*.50,root))
                    quads.append(self._quad(bx-17.0,deco_ground_y-3.0,27.0,6.0,(.18,.50,.33,1.0)))
                    quads.append(self._quad(bx+17.0,deco_ground_y-3.0,25.0,6.0,(.18,.50,.33,1.0)))

                elif kind == "glow_pool":
                    pulse=.64+.24*math.sin(float(now)*2.4+tx*.19);w=42.0+12.0*size
                    quads.append(self._quad(bx,deco_ground_y-3.0,w,7.0,(.12,.45,.38,.96)))
                    quads.append(self._quad(bx,deco_ground_y-5.0,w*.72,4.0,(.30,1.0,.76,.54*pulse)))
                    for ox in (-w*.33,-w*.10,w*.17,w*.36):
                        quads.append(self._quad(bx+ox,deco_ground_y-11.0-abs(ox)*.10,3.0,8.0,(.55,1.0,.84,.60*pulse)))

                elif kind == "ceiling_crystal":
                    pulse=.62+.25*math.sin(float(now)*1.8+tx*.27);h=43.0+9.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.55,13.0,h,(.24,.58,.88,.92)))
                    quads.append(self._quad(bx,deco_ground_y-h*.44,5.0,h*.72,(.68,.94,1.0,.62*pulse)))
                    quads.append(self._quad(bx-13.0,deco_ground_y-h*.68,8.0,h*.54,(.37,.72,1.0,.86)))
                    quads.append(self._quad(bx+14.0,deco_ground_y-h*.76,7.0,h*.40,(.49,.80,1.0,.82)))

                elif kind == "crystal_arch":
                    h=44.0+8.0*size;w=38.0+8.0*size;col=(.30,.68,1.0,.90)
                    quads.append(self._quad(bx-w*.43,deco_ground_y-h*.43,10.0,h*.86,col))
                    quads.append(self._quad(bx+w*.43,deco_ground_y-h*.43,10.0,h*.86,col))
                    quads.append(self._quad(bx,deco_ground_y-h+3.0,w,9.0,(.45,.80,1.0,.92)))
                    quads.append(self._quad(bx,deco_ground_y-h+3.0,w*.56,4.0,(.80,.97,1.0,.72)))

                elif kind == "crystal_vein":
                    pulse=.55+.30*math.sin(float(now)*2.0+tx*.49)
                    for ox,oy,w,h in ((-17,-13,15,5),(-7,-20,18,6),(7,-27,17,6),(18,-34,13,5)):
                        quads.append(self._quad(bx+ox,deco_ground_y+oy,w,h,(.35,.72,1.0,.84)))
                        quads.append(self._quad(bx+ox,deco_ground_y+oy,w*.45,2.0,(.80,.97,1.0,pulse)))

                elif kind == "shattered_crystal":
                    for ox,h,col in ((-16.0,20.0,(.30,.62,.94,.90)),(-6.0,34.0,(.48,.82,1.0,.92)),(7.0,17.0,(.35,.70,1.0,.88)),(17.0,27.0,(.63,.89,1.0,.90))):
                        quads.append(self._quad(bx+ox,deco_ground_y-h*.48,7.0,h,col))
                    quads.append(self._quad(bx,deco_ground_y-3.0,49.0,5.0,(.22,.48,.72,.92)))

                elif kind == "mine_cart":
                    quads.append(self._quad(bx,deco_ground_y-14.0,39.0,21.0,(.29,.27,.27,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-24.0,45.0,5.0,(.48,.39,.30,1.0)))
                    quads.append(self._quad(bx-13.0,deco_ground_y-3.5,9.0,9.0,(.15,.15,.17,1.0)))
                    quads.append(self._quad(bx+13.0,deco_ground_y-3.5,9.0,9.0,(.15,.15,.17,1.0)))
                    quads.append(self._quad(bx+7.0,deco_ground_y-16.0,7.0,8.0,(.40,.76,1.0,.74)))

                elif kind in ("hoist_chain","hanging_chain"):
                    h=41.0+10.0*size;col=(.38,.42,.47,1.0) if kind=="hoist_chain" else (.30,.24,.25,1.0)
                    for i in range(6):
                        oy=h*(float(i)/5.0)
                        quads.append(self._quad(bx+(-2.0 if i%2 else 2.0),deco_ground_y-h+oy,7.0,4.0,col))
                    if kind=="hoist_chain":
                        quads.append(self._quad(bx,deco_ground_y-2.0,28.0,5.0,(.43,.34,.24,1.0)))
                    else:
                        quads.append(self._quad(bx,deco_ground_y-4.0,19.0,8.0,(.47,.16,.13,1.0)))

                elif kind == "crystal_shrine":
                    pulse=.58+.32*math.sin(float(now)*2.3+tx*.31)
                    quads.append(self._quad(bx,deco_ground_y-6.0,52.0,12.0,(.20,.37,.55,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-15.0,34.0,8.0,(.31,.56,.78,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-33.0,14.0,33.0,(.47,.84,1.0,.91)))
                    quads.append(self._quad(bx,deco_ground_y-33.0,6.0,22.0,(.89,.99,1.0,pulse)))
                    quads.append(self._quad(bx,deco_ground_y-32.0,38.0,4.0,(.40,.78,1.0,.42*pulse)))

                elif kind == "obsidian_spire":
                    h=26.0+12.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.47,15.0,h,(.12,.10,.14,1.0)))
                    quads.append(self._quad(bx-10.0,deco_ground_y-h*.30,9.0,h*.58,(.19,.13,.18,1.0)))
                    quads.append(self._quad(bx+11.0,deco_ground_y-h*.23,8.0,h*.43,(.23,.12,.15,1.0)))
                    quads.append(self._quad(bx+2.0,deco_ground_y-h*.54,3.0,h*.52,(.75,.12,.08,.72)))

                elif kind == "lava_fall":
                    pulse=.70+.22*math.sin(float(now)*3.3+tx*.23);h=42.0+11.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.52,18.0,h,(.78,.13,.035,.88)))
                    quads.append(self._quad(bx-3.0,deco_ground_y-h*.54,7.0,h*.92,(1.0,.40,.04,.92)))
                    quads.append(self._quad(bx+4.0,deco_ground_y-h*.60,4.0,h*.68,(1.0,.79,.12,pulse)))
                    quads.append(self._quad(bx,deco_ground_y-3.0,38.0,7.0,(.95,.24,.025,.78)))

                elif kind == "ember_vent":
                    pulse=.58+.34*math.sin(float(now)*4.2+tx*.37)
                    quads.append(self._quad(bx,deco_ground_y-4.0,31.0,8.0,(.24,.16,.17,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-9.0,18.0,4.0,(1.0,.28,.04,.82)))
                    for ox,oy in ((-9,-18),(5,-27),(12,-39),(-3,-48)):
                        quads.append(self._quad(bx+ox,deco_ground_y+oy,4.0,6.0,(1.0,.48,.06,pulse)))

                elif kind == "bone_pile":
                    bone=(.72,.64,.52,1.0)
                    quads.append(self._quad(bx,deco_ground_y-5.0,43.0,9.0,(.30,.22,.20,1.0)))
                    for ox,oy,w,h in ((-14,-11,23,5),(5,-14,25,5),(-2,-21,5,19),(16,-9,5,15)):
                        quads.append(self._quad(bx+ox,deco_ground_y+oy,w,h,bone))
                    quads.append(self._quad(bx-4.0,deco_ground_y-27.0,14.0,12.0,(.80,.72,.59,1.0)))

                elif kind == "hell_furnace":
                    pulse=.66+.27*math.sin(float(now)*3.0+tx*.25)
                    quads.append(self._quad(bx,deco_ground_y-28.0,51.0,56.0,(.22,.12,.13,1.0)))
                    quads.append(self._quad(bx,deco_ground_y-26.0,29.0,31.0,(.72,.13,.035,.94)))
                    quads.append(self._quad(bx,deco_ground_y-25.0,15.0,24.0,(1.0,.52,.06,pulse)))
                    quads.append(self._quad(bx,deco_ground_y-58.0,59.0,8.0,(.39,.17,.15,1.0)))
                    quads.append(self._quad(bx-19.0,deco_ground_y-36.0,6.0,43.0,(.43,.21,.18,1.0)))
                    quads.append(self._quad(bx+19.0,deco_ground_y-36.0,6.0,43.0,(.43,.21,.18,1.0)))

                elif kind == "demon_statue":
                    stone=(.25,.16,.19,1.0);h=48.0+7.0*size
                    quads.append(self._quad(bx,deco_ground_y-h*.38,24.0,h*.70,stone))
                    quads.append(self._quad(bx,deco_ground_y-h*.79,29.0,24.0,(.31,.17,.20,1.0)))
                    quads.append(self._quad(bx-18.0,deco_ground_y-h*.94,10.0,22.0,stone))
                    quads.append(self._quad(bx+18.0,deco_ground_y-h*.94,10.0,22.0,stone))
                    quads.append(self._quad(bx-7.0,deco_ground_y-h*.80,4.0,4.0,(1.0,.20,.04,.92)))
                    quads.append(self._quad(bx+7.0,deco_ground_y-h*.80,4.0,4.0,(1.0,.20,.04,.92)))

                elif kind == "hell_rune":
                    pulse=.54+.36*math.sin(float(now)*2.8+tx*.28);w=48.0+6.0*size
                    quads.append(self._quad(bx,deco_ground_y-3.0,w,6.0,(.50,.08,.08,.76)))
                    quads.append(self._quad(bx,deco_ground_y-6.0,w*.63,3.0,(1.0,.18,.04,pulse)))
                    quads.append(self._quad(bx-15.0,deco_ground_y-14.0,4.0,20.0,(.85,.12,.06,.80)))
                    quads.append(self._quad(bx+15.0,deco_ground_y-14.0,4.0,20.0,(.85,.12,.06,.80)))
                    quads.append(self._quad(bx,deco_ground_y-22.0,34.0,4.0,(1.0,.34,.05,.64*pulse)))

                elif kind in ("portal","boss_gate"):
                    gate_h=42.0+8.0*(size-1); gate_w=26.0+6.0*(size-1)
                    col=(0.38,0.20,0.68,0.92) if kind=="portal" else (0.32,0.08,0.12,0.98)
                    quads.append(self._quad(bx-gate_w*0.42,deco_ground_y-gate_h*0.5,6.0,gate_h,col))
                    quads.append(self._quad(bx+gate_w*0.42,deco_ground_y-gate_h*0.5,6.0,gate_h,col))
                    quads.append(self._quad(bx,deco_ground_y-gate_h+3.0,gate_w,7.0,col))
                    inner=(0.62,0.38,0.95,0.38) if kind=="portal" else (0.75,0.18,0.12,0.38)
                    quads.append(self._quad(bx,deco_ground_y-gate_h*0.44,gate_w*0.65,gate_h*0.72,inner))

                else:
                    # FIX69 world catalogs may introduce decoration IDs
                    # without adding another long renderer branch.  Unknown
                    # secondary-world art receives a visible 3-8 quad motif;
                    # the common environment batch remains strictly capped.
                    secondary = getattr(game, "secondary_world", None)
                    parts_for = getattr(secondary, "decoration_parts", None)
                    if bool(getattr(secondary, "active", False)) and callable(parts_for):
                        room = max(0, int(METAL_MAX_ENV_QUADS) - len(quads))
                        try:
                            parts = tuple(parts_for(kind, size, float(now)) or ())[:min(8, room)]
                        except Exception:
                            parts = ()
                        for dx, dy, width, height, rgba in parts:
                            quads.append(self._quad(
                                bx + float(dx), deco_ground_y + float(dy),
                                float(width), float(height), rgba,
                            ))
                        if len(quads) >= METAL_MAX_ENV_QUADS:
                            return quads[:METAL_MAX_ENV_QUADS]

        # FIX90 authored visual stamps: indexed by chunk, never a global scan.
        # They intentionally do not create collisions, attacks or item pickups.
        _visuals=getattr(game,"editor_visuals",{})
        for _ck in self._visible_chunk_coords(game,margin_tiles=16):
            for _v in _visuals.get(_ck,()):
                _vx=(float(_v.get("x",0))+.5)*TILE_SIZE
                _vy=(float(_v.get("y",0))+.5)*TILE_SIZE
                _aid=str(_v.get("asset",""))
                _feet=_aid.split(".",1)[0] in ("plant","creature","player","equipment","decoration")
                _vq=self._pixel_asset_quads(game,"editor.visual:"+_aid,_vx-20,_vy-(40 if _feet else 20),40,40,
                    visual_scale=float(_v.get("scale",1)),flip_x=bool(_v.get("flip",False)),
                    animation=_v.get("animation") or None,animation_time=float(now))
                if _vq:quads.extend(_vq[:max(0,METAL_MAX_ENV_QUADS-len(quads))])
                if len(quads)>=METAL_MAX_ENV_QUADS:return quads

        # Plants / ecological succession. stage 0 is invisible seed bank.
        for (tx, ty), plant in self._iter_visible_table_items(env.plants, game):
            if (
                not plant.alive
                or plant.stage <= 0
                or not self._is_visible_tile(tx, ty, game)
            ):
                continue

            stage = max(1, min(6, int(plant.stage)))
            bx = tx * TILE_SIZE + TILE_SIZE * 0.5
            # V0.7.6.6: all vegetation uses the same authoritative surface
            # anchor as terrain/ice/water. This also repairs legacy map entries
            # that store vegetation in the air cell immediately above ground.
            try:
                gy = float(game.world.plant_anchor_y(tx, ty, getattr(plant, "species", "")))
            except Exception:
                gy = float(ty * TILE_SIZE)

            # FIX4: map-editor vegetation can now be replaced by the saved
            # pixel source. Fire/frost/environment overlays are still appended
            # later by their existing systems, so only the base silhouette is replaced.
            _plant_geo = self._plant_asset_geometry(plant, bx, gy)
            if _plant_geo is not None:
                _aid, _left, _top, _w, _h = _plant_geo
                try:
                    _plant_clock = float(getattr(game.time, "elapsed_real_seconds", 0.0))
                except Exception:
                    _plant_clock = 0.0
                # Stable per-cell phase prevents every tree/grass tuft from
                # swaying on the exact same frame. It is render-only.
                _plant_clock += ((int(tx)*17 + int(ty)*31) & 15) * 0.071
                _plant_custom = self._pixel_asset_quads(
                    game, _aid, _left, _top, _w, _h,
                    animation="sway", animation_time=_plant_clock,
                )
                if _plant_custom is not None:
                    quads.extend(_plant_custom)
                    if len(quads) >= METAL_MAX_ENV_QUADS:
                        return quads
                    continue

            if plant.species == "moss":
                # Underground moss: a low blue-green carpet, never a grass/tree
                # stalk.  It hugs the top edge of the supporting soil tile.
                moss_color = (0.18, 0.48, 0.38, 0.96)
                moss_light = (0.26, 0.60, 0.46, 0.92)
                quads.append(self._quad(bx, gy-2.5, TILE_SIZE*0.72, 5.0, moss_color))
                quads.append(self._quad(bx-TILE_SIZE*0.22, gy-5.5, TILE_SIZE*0.20, 4.0, moss_light))
                quads.append(self._quad(bx+TILE_SIZE*0.20, gy-4.0, TILE_SIZE*0.16, 3.0, moss_light))

            elif plant.species == "succession":
                plant_state = getattr(plant, "state", "normal")
                burn_progress = max(
                    0.0,
                    min(1.0, float(getattr(plant, "burn_progress", 0.0))),
                )

                if stage <= 1:
                    # Level-1 grass = current small grass.
                    height = 12.0
                    grass_color = (0.22, 0.62, 0.24, 1.0)

                    if plant_state == "swamp_withered":
                        grass_color = (0.48, 0.39, 0.20, 1.0)

                    elif plant_state == "swamp_recovering":
                        progress = max(
                            0.0,
                            min(
                                1.0,
                                float(
                                    getattr(
                                        plant,
                                        "regrow_progress",
                                        0.0,
                                    )
                                ),
                            ),
                        )

                        grass_color = (
                            0.48 - 0.26 * progress,
                            0.39 + 0.23 * progress,
                            0.20 + 0.04 * progress,
                            1.0,
                        )

                    elif plant_state in ("burning", "charred"):
                        grass_color = (
                            0.22 + 0.16 * burn_progress,
                            0.62 - 0.42 * burn_progress,
                            0.24 - 0.12 * burn_progress,
                            1.0,
                        )
                    quads.append(
                        self._quad(
                            bx, gy - height * 0.5,
                            7.0, height, grass_color,
                        )
                    )
                elif stage == 2:
                    # Level-2 grass = about 2x taller.
                    height = 24.0
                    grass_color = (0.22, 0.62, 0.24, 1.0)
                    leaf_color = (0.28, 0.70, 0.27, 1.0)

                    if plant_state == "swamp_withered":
                        grass_color = (0.48, 0.39, 0.20, 1.0)
                        leaf_color = (0.55, 0.43, 0.22, 1.0)

                    elif plant_state == "swamp_recovering":
                        progress = max(
                            0.0,
                            min(
                                1.0,
                                float(
                                    getattr(
                                        plant,
                                        "regrow_progress",
                                        0.0,
                                    )
                                ),
                            ),
                        )

                        grass_color = (
                            0.48 - 0.26 * progress,
                            0.39 + 0.23 * progress,
                            0.20 + 0.04 * progress,
                            1.0,
                        )

                        leaf_color = (
                            0.55 - 0.27 * progress,
                            0.43 + 0.27 * progress,
                            0.22 + 0.05 * progress,
                            1.0,
                        )

                    elif plant_state in ("burning", "charred"):
                        grass_color = (
                            0.22 + 0.18 * burn_progress,
                            0.62 - 0.44 * burn_progress,
                            0.24 - 0.13 * burn_progress,
                            1.0,
                        )
                        leaf_color = (
                            0.28 + 0.16 * burn_progress,
                            0.70 - 0.50 * burn_progress,
                            0.27 - 0.15 * burn_progress,
                            1.0,
                        )
                    quads.append(
                        self._quad(
                            bx, gy - height * 0.5,
                            10.0, height, grass_color,
                        )
                    )
                    quads.append(
                        self._quad(
                            bx - 6, gy - 9, 8, 5, leaf_color,
                        )
                    )
                    quads.append(
                        self._quad(
                            bx + 6, gy - 11, 8, 5, leaf_color,
                        )
                    )
                    # Deterministic berry-bush subset; ToolSystem uses the same
                    # coordinate hash so visible berries and harvested berries
                    # always agree across saves.
                    if (((int(tx) * 92821) ^ (int(ty) * 68917)) % 7 == 0):
                        quads.append(self._quad(bx-5, gy-10, 4, 4, BERRY_RGBA))
                        quads.append(self._quad(bx+4, gy-15, 4, 4, BERRY_RGBA))
                        quads.append(self._quad(bx+7, gy-7, 3, 3, BERRY_RGBA))
                else:
                    # Level-3 = tree. Burn damage is visualized in four
                    # top-to-bottom segments while FireSystem burns fuel.
                    trunk_h = 48.0
                    trunk_w = 8.0
                    canopy_w = 40.0
                    canopy_h = 24.0
                    canopy_y = gy - trunk_h + canopy_h * 0.30

                    trunk_color = (0.43, 0.28, 0.14, 1.0)
                    canopy_color = (0.18, 0.55, 0.22, 1.0)
                    accent_color = (0.22, 0.64, 0.25, 1.0)

                    if plant_state in (
                        "deadwood",
                        "swamp_withered",
                    ):
                        canopy_color = (0.42, 0.34, 0.22, 0.92)
                        accent_color = (0.50, 0.40, 0.26, 0.78)

                        if plant_state == "swamp_withered":
                            trunk_color = (0.34, 0.27, 0.19, 1.0)

                    elif plant_state in (
                        "regrowing",
                        "swamp_recovering",
                    ):
                        progress = max(0.0, min(1.0, float(getattr(plant, "regrow_progress", 0.0))))
                        canopy_color = (
                            0.42 - 0.24 * progress,
                            0.34 + 0.21 * progress,
                            0.22,
                            0.95,
                        )
                        accent_color = (
                            0.50 - 0.28 * progress,
                            0.40 + 0.24 * progress,
                            0.26 - 0.01 * progress,
                            0.82,
                        )
                    elif plant_state in ("burning", "charred"):
                        canopy_color = (
                            0.18 + 0.20 * burn_progress,
                            0.55 - 0.35 * burn_progress,
                            0.22 - 0.10 * burn_progress,
                            1.0,
                        )
                        accent_color = (
                            0.22 + 0.18 * burn_progress,
                            0.64 - 0.42 * burn_progress,
                            0.25 - 0.12 * burn_progress,
                            1.0,
                        )

                    quads.append(
                        self._quad(
                            bx, gy - trunk_h * 0.5, trunk_w, trunk_h, trunk_color
                        )
                    )
                    quads.append(
                        self._quad(
                            bx, canopy_y, canopy_w, canopy_h, canopy_color
                        )
                    )
                    quads.append(
                        self._quad(
                            bx - canopy_w * 0.24, canopy_y + 3, canopy_w * 0.46, canopy_h * 0.72, accent_color
                        )
                    )

                    if plant_state in ("burning", "charred"):
                        # Segment 1: canopy top.
                        seg_alpha = max(0.0, min(0.90, burn_progress / 0.25 * 0.90))
                        if seg_alpha > 0.01:
                            quads.append(self._quad(
                                bx, canopy_y - canopy_h * 0.24, canopy_w * 0.94, canopy_h * 0.48,
                                (0.12, 0.09, 0.07, seg_alpha),
                            ))

                        # Segment 2: canopy bottom.
                        seg_alpha = max(0.0, min(0.88, (burn_progress - 0.25) / 0.25 * 0.88))
                        if seg_alpha > 0.01:
                            quads.append(self._quad(
                                bx, canopy_y + canopy_h * 0.24, canopy_w * 0.88, canopy_h * 0.48,
                                (0.15, 0.10, 0.07, seg_alpha),
                            ))

                        # Segment 3: upper trunk.
                        seg_alpha = max(0.0, min(0.88, (burn_progress - 0.50) / 0.25 * 0.88))
                        if seg_alpha > 0.01:
                            quads.append(self._quad(
                                bx, gy - trunk_h * 0.70, trunk_w + 2.0, trunk_h * 0.42,
                                (0.10, 0.08, 0.06, seg_alpha),
                            ))

                        # Segment 4: lower trunk.
                        seg_alpha = max(0.0, min(0.86, (burn_progress - 0.75) / 0.25 * 0.86))
                        if seg_alpha > 0.01:
                            quads.append(self._quad(
                                bx, gy - trunk_h * 0.22, trunk_w + 2.0, trunk_h * 0.44,
                                (0.10, 0.08, 0.06, seg_alpha),
                            ))

                    if plant_state in (
                        "regrowing",
                        "swamp_recovering",
                    ):
                        progress = max(0.0, min(1.0, float(getattr(plant, "regrow_progress", 0.0))))
                        regrow_h = trunk_h * max(0.04, progress)
                        quads.append(
                            self._quad(
                                bx, gy - regrow_h * 0.5, trunk_w + 1.0, regrow_h, (0.18, 0.58, 0.20, 0.88)
                            )
                        )

                        # After the green reaches the branches, the canopy
                        # fills from bottom toward the top.
                        canopy_fill = max(0.0, min(1.0, (progress - 0.48) / 0.52))
                        if canopy_fill > 0.0:
                            fill_h = canopy_h * canopy_fill
                            quads.append(self._quad(
                                bx,
                                canopy_y + canopy_h * 0.5 - fill_h * 0.5,
                                canopy_w * 0.94,
                                fill_h,
                                (0.18, 0.57, 0.22, 0.82),
                            ))

                    if plant_state == "normal" and plant.fruit > 0:
                        for fruit_i in range(min(4, int(plant.fruit))):
                            fx = bx + (-12 + fruit_i * 8)
                            fy = canopy_y + (fruit_i % 2) * 6
                            quads.append(
                                self._quad(
                                    fx, fy, 5, 5,
                                    (0.82, 0.18, 0.18, 1.0),
                                )
                            )
            elif plant.species == "map_grass":
                # Editor grass has explicit low / medium / high levels.
                level = max(
                    1,
                    min(
                        3,
                        int(
                            plant.stage
                        ),
                    ),
                )

                height = {
                    1: 11.0,
                    2: 21.0,
                    3: 32.0,
                }[
                    level
                ]

                width = {
                    1: 7.0,
                    2: 10.0,
                    3: 13.0,
                }[
                    level
                ]

                map_state = getattr(
                    plant,
                    "state",
                    "normal",
                )

                burn_progress = max(
                    0.0,
                    min(1.0, float(getattr(plant, "burn_progress", 0.0))),
                )
                map_grass_color = (0.22, 0.62, 0.24, 1.0)
                map_leaf_color = (0.28, 0.70, 0.27, 1.0)

                if map_state == "swamp_withered":
                    map_grass_color = (0.48, 0.39, 0.20, 1.0)
                    map_leaf_color = (0.55, 0.43, 0.22, 1.0)

                elif map_state == "swamp_recovering":
                    progress = max(
                        0.0,
                        min(
                            1.0,
                            float(getattr(plant, "regrow_progress", 0.0)),
                        ),
                    )
                    map_grass_color = (
                        0.48 - 0.26 * progress,
                        0.39 + 0.23 * progress,
                        0.20 + 0.04 * progress,
                        1.0,
                    )
                    map_leaf_color = (
                        0.55 - 0.27 * progress,
                        0.43 + 0.27 * progress,
                        0.22 + 0.05 * progress,
                        1.0,
                    )

                elif map_state in ("burning", "charred"):
                    map_grass_color = (
                        0.22 + 0.20 * burn_progress,
                        0.62 - 0.46 * burn_progress,
                        0.24 - 0.13 * burn_progress,
                        1.0,
                    )
                    map_leaf_color = (
                        0.28 + 0.18 * burn_progress,
                        0.70 - 0.52 * burn_progress,
                        0.27 - 0.15 * burn_progress,
                        1.0,
                    )

                quads.append(
                    self._quad(
                        bx,
                        gy
                        - height
                        * 0.5,
                        width,
                        height,
                        map_grass_color,
                    )
                )

                if level >= 2:
                    quads.append(
                        self._quad(
                            bx - 6,
                            gy
                            - height
                            * 0.58,
                            8,
                            5,
                            map_leaf_color,
                        )
                    )

                if level >= 2 and (((int(tx) * 92821) ^ (int(ty) * 68917)) % 7 == 0):
                    quads.append(self._quad(bx-5, gy-height*0.50, 4, 4, BERRY_RGBA))
                    quads.append(self._quad(bx+5, gy-height*0.64, 4, 4, BERRY_RGBA))

                if level >= 3:
                    quads.append(
                        self._quad(
                            bx + 7,
                            gy
                            - height
                            * 0.72,
                            9,
                            5,
                            map_leaf_color,
                        )
                    )

            elif plant.species == "map_reed":
                level = max(1, min(3, int(getattr(plant, "stage", 1))))
                stem_h = {1:18.0, 2:28.0, 3:38.0}[level]
                stem_offsets = (-5.0, 0.0, 5.0) if level >= 2 else (-2.0, 2.0)
                stem_color = (0.47, 0.58, 0.20, 1.0)
                head_color = (0.44, 0.28, 0.13, 1.0)
                for off in stem_offsets:
                    sh = stem_h * (0.86 if off < 0 else (1.0 if off == 0.0 else 0.90))
                    quads.append(self._quad(bx+off, gy-sh*0.5, 3.0, sh, stem_color))
                    quads.append(self._quad(bx+off, gy-sh+4.0, 4.6, max(5.0, sh*0.24), head_color))
                if level >= 3:
                    quads.append(self._quad(bx-8.0, gy-stem_h*0.44, 6.0, 3.0, (0.58,0.70,0.28,1.0)))
                    quads.append(self._quad(bx+8.0, gy-stem_h*0.52, 6.0, 3.0, (0.58,0.70,0.28,1.0)))

            elif plant.species == "map_fern":
                level = max(1, min(3, int(getattr(plant, "stage", 1))))
                base_color = (0.17, 0.50, 0.22, 1.0)
                light_color = (0.24, 0.66, 0.30, 1.0)
                fronds = {1:3, 2:5, 3:7}[level]
                spread = {1:8.0, 2:12.0, 3:16.0}[level]
                frond_h = {1:13.0, 2:18.0, 3:24.0}[level]
                quads.append(self._quad(bx, gy-4.0, 4.0, 8.0, (0.14,0.36,0.18,1.0)))
                for i in range(fronds):
                    frac = 0.0 if fronds == 1 else (i / float(fronds - 1) - 0.5)
                    off = frac * spread
                    fh = frond_h * (1.0 - abs(frac) * 0.28)
                    color = light_color if i % 2 else base_color
                    quads.append(self._quad(bx+off, gy-fh*0.60, max(4.0, 6.0-abs(frac)*2.0), fh, color))

            elif plant.species == "map_cactus":
                level = max(1, min(3, int(getattr(plant, "stage", 1))))
                trunk_h = {1:20.0, 2:32.0, 3:46.0}[level]
                trunk_w = {1:9.0, 2:11.0, 3:13.0}[level]
                body_color = (0.24, 0.56, 0.30, 1.0)
                light_color = (0.31, 0.66, 0.36, 1.0)
                quads.append(self._quad(bx, gy-trunk_h*0.5, trunk_w, trunk_h, body_color))
                # rib highlight
                quads.append(self._quad(bx-2.0, gy-trunk_h*0.5, 1.4, trunk_h-2.0, light_color))
                quads.append(self._quad(bx+2.0, gy-trunk_h*0.5, 1.4, trunk_h-2.0, light_color))
                if level >= 2:
                    arm_y = gy - trunk_h*0.58
                    quads.append(self._quad(bx-8.0, arm_y, 7.0, 10.0, body_color))
                    quads.append(self._quad(bx-10.0, arm_y-5.0, 4.0, 10.0, body_color))
                if level >= 3:
                    arm_y = gy - trunk_h*0.48
                    quads.append(self._quad(bx+8.0, arm_y, 7.0, 12.0, body_color))
                    quads.append(self._quad(bx+10.0, arm_y-6.0, 4.0, 12.0, body_color))
                for spike_off in (-3.5, 0.0, 3.5):
                    quads.append(self._quad(bx+spike_off, gy-trunk_h*0.84, 1.0, 2.0, (0.93,0.93,0.82,1.0)))

            elif plant.species in ("map_tree", "map_jungle_tree"):
                # Explicit map-editor wood/tree height levels.
                level = max(
                    1,
                    min(
                        3,
                        int(
                            plant.stage
                        ),
                    ),
                )

                trunk_h = {
                    1: 28.0,
                    2: 46.0,
                    3: 68.0,
                }[
                    level
                ]

                trunk_w = {
                    1: 6.0,
                    2: 8.0,
                    3: 10.0,
                }[
                    level
                ]

                canopy_w = {
                    1: 22.0,
                    2: 34.0,
                    3: 48.0,
                }[
                    level
                ]

                canopy_h = {
                    1: 15.0,
                    2: 22.0,
                    3: 30.0,
                }[
                    level
                ]

                canopy_y = (
                    gy
                    - trunk_h
                    + canopy_h
                    * 0.32
                )

                map_state = getattr(
                    plant,
                    "state",
                    "normal",
                )

                trunk_color = (
                    0.43,
                    0.28,
                    0.14,
                    1.0,
                )

                canopy_color = (
                    0.18,
                    0.55,
                    0.22,
                    1.0,
                )
                biome_here = None
                try:
                    biome_sys = (getattr(game, "biomes", None) or getattr(game, "biome_system", None))
                    if biome_sys is not None:
                        biome_here = str(biome_sys.biome_at_tile(int(tx)))
                except Exception:
                    biome_here = None
                if plant.species == "map_jungle_tree" and biome_here == "swamp":
                    trunk_color = (0.36, 0.26, 0.15, 1.0)
                    canopy_color = (0.15, 0.40, 0.20, 1.0)

                if map_state == "swamp_withered":
                    trunk_color = (
                        0.34,
                        0.27,
                        0.19,
                        1.0,
                    )

                    canopy_color = (
                        0.42,
                        0.34,
                        0.22,
                        0.94,
                    )

                elif map_state in ("burning", "charred"):
                    burn_progress = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                getattr(
                                    plant,
                                    "burn_progress",
                                    0.0,
                                )
                            ),
                        ),
                    )

                    trunk_color = (
                        0.43
                        - 0.24
                        * burn_progress,
                        0.28
                        - 0.15
                        * burn_progress,
                        0.14
                        - 0.07
                        * burn_progress,
                        1.0,
                    )

                    canopy_color = (
                        0.18
                        + 0.18
                        * burn_progress,
                        0.55
                        - 0.36
                        * burn_progress,
                        0.22
                        - 0.11
                        * burn_progress,
                        1.0,
                    )

                elif map_state == "swamp_recovering":
                    progress = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                getattr(
                                    plant,
                                    "regrow_progress",
                                    0.0,
                                )
                            ),
                        ),
                    )

                    canopy_color = (
                        0.42 - 0.24 * progress,
                        0.34 + 0.21 * progress,
                        0.22,
                        0.96,
                    )

                quads.append(
                    self._quad(
                        bx,
                        gy
                        - trunk_h
                        * 0.5,
                        trunk_w,
                        trunk_h,
                        trunk_color,
                    )
                )
                if plant.species == "map_jungle_tree" and biome_here == "swamp":
                    quads.append(self._quad(bx-8.0, gy-6.0, 4.0, 14.0, trunk_color))
                    quads.append(self._quad(bx+8.0, gy-6.0, 4.0, 14.0, trunk_color))
                    quads.append(self._quad(bx-4.0, gy-4.0, 3.0, 10.0, trunk_color))
                    quads.append(self._quad(bx+4.0, gy-4.0, 3.0, 10.0, trunk_color))

                quads.append(
                    self._quad(
                        bx,
                        canopy_y,
                        canopy_w,
                        canopy_h,
                        canopy_color,
                    )
                )

            elif plant.species == "map_pine":
                # V0.7.5.3 explicit conifer geometry. Older builds loaded pine
                # as a generic legacy stalk, while frost overlays assumed a
                # full tree height; that mismatch made some touching pines look
                # as if they never frosted. Pine now has real tree-sized tiers.
                level = max(1, min(3, int(plant.stage)))
                trunk_h = {1:30.0, 2:48.0, 3:70.0}[level]
                trunk_w = {1:5.0, 2:7.0, 3:9.0}[level]
                canopy_w = {1:24.0, 2:38.0, 3:52.0}[level]
                canopy_h = {1:27.0, 2:42.0, 3:58.0}[level]
                state_now = str(getattr(plant, "state", "normal"))
                burn_progress = max(0.0, min(1.0, float(getattr(plant, "burn_progress", 0.0))))
                trunk_color = (0.34, 0.24, 0.14, 1.0)
                needle_color = (0.12, 0.37, 0.22, 1.0)
                needle_light = (0.16, 0.46, 0.27, 1.0)
                if state_now in ("deadwood", "swamp_withered"):
                    needle_color = (0.34, 0.31, 0.22, 0.95)
                    needle_light = (0.40, 0.36, 0.24, 0.90)
                elif state_now in ("burning", "charred"):
                    needle_color = (
                        0.12 + 0.20*burn_progress,
                        0.37 - 0.26*burn_progress,
                        0.22 - 0.12*burn_progress,
                        1.0,
                    )
                    needle_light = needle_color

                quads.append(self._quad(bx, gy-trunk_h*0.5, trunk_w, trunk_h, trunk_color))
                # Rectangular branch tiers form a clear pixel-conifer silhouette.
                tiers = (
                    (0.86, 0.34),
                    (0.66, 0.48),
                    (0.46, 0.64),
                    (0.27, 0.80),
                )
                for idx, (height_frac, width_frac) in enumerate(tiers):
                    cy_branch = gy - trunk_h * height_frac
                    bw = canopy_w * width_frac
                    bh = max(5.0, canopy_h * (0.13 + 0.015*idx))
                    quads.append(self._quad(
                        bx, cy_branch, bw, bh,
                        needle_light if idx % 2 else needle_color,
                    ))

                # Alpine/polar pines are intrinsically snow covered. This is a
                # climate visual, separate from transient contact frost.
                climate_now = env.climate_at_chunk(int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE, "temperate")
                if climate_now in ("alpine", "polar") and state_now != "burning":
                    snow_alpha = 0.82 if state_now == "normal" else 0.48
                    for idx, (height_frac, width_frac) in enumerate(tiers):
                        cy_branch = gy - trunk_h * height_frac
                        bw = canopy_w * width_frac
                        quads.append(self._quad(
                            bx, cy_branch - max(2.0, canopy_h*0.055),
                            bw*0.90, max(2.5, canopy_h*0.075),
                            (0.90, 0.96, 1.0, snow_alpha),
                        ))

            else:
                stage_legacy = max(1, min(4, stage))
                height = 7.0 + stage_legacy * 6.0
                width = 5.0 if plant.species == 'grass' else 7.0
                legacy_burn = max(0.0, min(1.0, float(getattr(plant, 'burn_progress', 0.0))))
                legacy_state = getattr(plant, 'state', 'normal')
                legacy_color = (0.24,0.62,0.25,1.0)
                if legacy_state in ('burning', 'charred', 'deadwood'):
                    legacy_color = (
                        0.24 + 0.20 * legacy_burn,
                        0.62 - 0.45 * legacy_burn,
                        0.25 - 0.13 * legacy_burn,
                        1.0,
                    )
                quads.append(self._quad(bx, gy-height*0.5, width, height, legacy_color))
                if stage_legacy >= 2:
                    quads.append(self._quad(bx-5, gy-height*0.62, 8, 4, (0.31,0.72,0.30,1.0)))
                    quads.append(self._quad(bx+5, gy-height*0.46, 8, 4, (0.31,0.72,0.30,1.0)))
                if plant.species == 'berry' and (plant.fruit > 0 or stage_legacy >= 4):
                    quads.append(self._quad(bx+5, gy-height+4, 7, 7, (0.82,0.18,0.20,1.0)))

            # ----------------------------------------------------
            # V0.7.5.3 contact frost on the vegetation's *visible body*.
            # The old test searched only a 5x5 square around the root tile.
            # Tall/wide trees (especially map_pine/map_jungle_tree) therefore
            # missed ICE touching their crown/branches.  We now derive a tile
            # contact envelope from each plant's rendered size and scan that
            # envelope. Any real ICE mass touching the plant produces frost.
            # ----------------------------------------------------
            plant_state_now = str(getattr(plant, "state", "normal"))
            if plant_state_now not in ("burning",):
                species = str(getattr(plant, "species", "grass"))
                lvl = max(1, min(3, int(getattr(plant, "stage", 1))))

                if species == "map_pine":
                    body_w = {1:24.0, 2:38.0, 3:52.0}[lvl]
                    body_h = {1:30.0, 2:48.0, 3:70.0}[lvl]
                elif species in ("map_tree", "map_jungle_tree"):
                    body_w = {1:22.0, 2:34.0, 3:48.0}[lvl]
                    body_h = {1:28.0, 2:46.0, 3:68.0}[lvl]
                elif species == "succession" and lvl >= 3:
                    body_w, body_h = 40.0, 48.0
                elif species == "map_grass":
                    body_w = {1:7.0, 2:10.0, 3:13.0}[lvl]
                    body_h = {1:11.0, 2:21.0, 3:32.0}[lvl]
                else:
                    body_w = 16.0 if lvl <= 1 else 24.0
                    body_h = 12.0 if lvl <= 1 else 26.0

                half_tiles = max(0, int(math.ceil(body_w / max(1.0, 2.0*TILE_SIZE))))
                up_tiles = max(1, int(math.ceil(body_h / max(1.0, TILE_SIZE))))
                min_x = int(tx) - half_tiles - 1
                max_x = int(tx) + half_tiles + 1
                min_y = int(ty) - up_tiles - 1
                max_y = int(ty)

                frost_strength = 0.0
                for ny in range(min_y, max_y + 1):
                    for nx in range(min_x, max_x + 1):
                        if not (0 <= nx < game.world.width_tiles and 0 <= ny < game.world.height_tiles):
                            continue
                        if game.world.get_tile(nx, ny) != ICE:
                            continue
                        try:
                            mass = float(env.ice_mass.get((nx, ny), tile_def(ICE).water_mass))
                        except Exception:
                            mass = 1.0
                        if mass <= 0.0:
                            continue
                        frost_strength = max(frost_strength, min(1.0, 0.45 + mass))

                if frost_strength > 0.0:
                    frost = max(0.18, min(0.82, 0.26 + 0.56*frost_strength))
                    if species == "map_pine":
                        th = {1:30.0, 2:48.0, 3:70.0}[lvl]
                        tw = {1:24.0, 2:38.0, 3:52.0}[lvl]
                        for height_frac, width_frac in ((0.86,0.34),(0.66,0.48),(0.46,0.64),(0.27,0.80)):
                            quads.append(self._quad(
                                bx, gy-th*height_frac-2.0, tw*width_frac*0.92, 3.4,
                                (0.88,0.96,1.0,frost),
                            ))
                    elif species in ("map_tree", "map_jungle_tree") or (species == "succession" and lvl >= 3):
                        if species in ("map_tree", "map_jungle_tree"):
                            tw = {1:22.0, 2:34.0, 3:48.0}[lvl]
                            th = {1:28.0, 2:46.0, 3:68.0}[lvl]
                            ch = {1:15.0, 2:22.0, 3:30.0}[lvl]
                        else:
                            tw, th, ch = 40.0, 48.0, 24.0
                        canopy_y = gy - th + ch*0.30
                        quads.append(self._quad(bx, canopy_y - ch*0.30, tw*0.88, 4.0, (0.90,0.97,1.0,frost)))
                        quads.append(self._quad(bx-tw*0.24, canopy_y+1.5, tw*0.26, 5.0, (0.82,0.93,1.0,frost*0.76)))
                        quads.append(self._quad(bx+tw*0.25, canopy_y+2.0, tw*0.24, 4.5, (0.82,0.93,1.0,frost*0.72)))
                    else:
                        h = body_h
                        quads.append(self._quad(bx, gy-h+2.0, max(8.0, body_w*0.55), 3.0, (0.90,0.97,1.0,frost)))
                        quads.append(self._quad(bx-5.0, gy-h*0.68, 5.0, 2.6, (0.82,0.93,1.0,frost*0.76)))
                        quads.append(self._quad(bx+5.0, gy-h*0.58, 5.0, 2.6, (0.82,0.93,1.0,frost*0.72)))

            if len(quads) >= METAL_MAX_ENV_QUADS:
                return quads

        def append_flame_vfx(
            cx,
            cy,
            w,
            h,
            tier,
            alpha_scale=1.0,
        ):
            alpha_scale = max(
                0.15,
                min(
                    1.0,
                    float(
                        alpha_scale
                    ),
                ),
            )

            quads.append(
                self._quad(
                    cx,
                    cy,
                    w,
                    h,
                    (
                        1.0,
                        0.26,
                        0.05,
                        0.78
                        * alpha_scale,
                    ),
                )
            )

            quads.append(
                self._quad(
                    cx
                    + 2.0,
                    cy
                    - h
                    * 0.20,
                    w
                    * 0.48,
                    h
                    * 0.58,
                    (
                        1.0,
                        0.80,
                        0.12,
                        0.90
                        * alpha_scale,
                    ),
                )
            )

            if tier >= 2:
                quads.append(
                    self._quad(
                        cx
                        - w
                        * 0.22,
                        cy
                        - h
                        * 0.07,
                        w
                        * 0.36,
                        h
                        * 0.48,
                        (
                            1.0,
                            0.42,
                            0.05,
                            0.76
                            * alpha_scale,
                        ),
                    )
                )

            if tier >= 3:
                quads.append(
                    self._quad(
                        cx
                        + w
                        * 0.24,
                        cy
                        - h
                        * 0.11,
                        w
                        * 0.34,
                        h
                        * 0.52,
                        (
                            1.0,
                            0.54,
                            0.06,
                            0.80
                            * alpha_scale,
                        ),
                    )
                )

        # Fire size is physically tiered by stack 1/2/3. When a tree is
        # burning, the VFX coverage additionally scales to the tree's height:
        # small tree -> small attached flame,
        # medium tree -> medium attached flame,
        # large tree -> large attached flame.
        #
        # This changes visual coverage only; FireSystem stack temperature and
        # energy still come from the actual fire source.
        for (tx, ty), fire in self._iter_visible_table_items(env.fire, game):
            if not self._is_visible_tile(tx, ty, game):
                continue

            intensity = max(
                0.08,
                min(
                    1.0,
                    float(
                        fire.intensity
                    ),
                ),
            )

            stack_level = max(
                1,
                min(
                    3,
                    int(
                        getattr(
                            fire,
                            "stack_level",
                            1,
                        )
                    ),
                ),
            )

            plant = env.plants.get(
                (
                    tx,
                    ty,
                )
            )

            tree_level = 0
            trunk_h = 0.0

            if plant is not None:
                species = str(
                    getattr(
                        plant,
                        "species",
                        "",
                    )
                )

                if species == "map_tree":
                    tree_level = max(
                        1,
                        min(
                            3,
                            int(
                                getattr(
                                    plant,
                                    "stage",
                                    1,
                                )
                            ),
                        ),
                    )

                    trunk_h = {
                        1: 28.0,
                        2: 46.0,
                        3: 68.0,
                    }[
                        tree_level
                    ]

                elif (
                    species == "succession"
                    and int(
                        getattr(
                            plant,
                            "stage",
                            0,
                        )
                    )
                    >= 3
                ):
                    tree_level = 3
                    trunk_h = 48.0

            visual_tier = (
                max(
                    stack_level,
                    tree_level,
                )
                if tree_level > 0
                else stack_level
            )

            if visual_tier == 1:
                base_w = FIRE_STACK_WIDTH_L1
                base_h = FIRE_STACK_HEIGHT_L1
            elif visual_tier == 2:
                base_w = FIRE_STACK_WIDTH_L2
                base_h = FIRE_STACK_HEIGHT_L2
            else:
                base_w = FIRE_STACK_WIDTH_L3
                base_h = FIRE_STACK_HEIGHT_L3

            strength_scale = (
                0.72
                + 0.28
                * intensity
            )

            w = (
                base_w
                * strength_scale
            )

            h = (
                base_h
                * strength_scale
            )

            cx = (
                tx
                * TILE_SIZE
                + TILE_SIZE
                * 0.5
            )

            top = (
                ty
                * TILE_SIZE
            )

            if tree_level <= 0:
                # Normal ground / grass fire.
                append_flame_vfx(
                    cx,
                    top
                    - h
                    * 0.35,
                    w,
                    h,
                    visual_tier,
                    strength_scale,
                )

            else:
                # Tree fire is attached to the vegetation itself instead of
                # existing only at the bottom tile.
                if tree_level == 1:
                    positions = (
                        0.42,
                    )

                elif tree_level == 2:
                    positions = (
                        0.28,
                        0.73,
                    )

                else:
                    positions = (
                        0.20,
                        0.54,
                        0.88,
                    )

                for index, fraction in enumerate(
                    positions
                ):
                    local_scale = (
                        0.62
                        + 0.10
                        * index
                    )

                    append_flame_vfx(
                        cx
                        + (
                            -3.0
                            if index % 2 == 0
                            else 4.0
                        ),
                        top
                        - trunk_h
                        * fraction,
                        w
                        * local_scale,
                        h
                        * (
                            0.58
                            + 0.08
                            * index
                        ),
                        visual_tier,
                        strength_scale,
                    )

            if len(quads) >= METAL_MAX_ENV_QUADS:
                return quads

        return quads

    def _append_creature_sprite(self, quads, c, ccolor, visual_budget=1800):
        # FIX104: preserve detailed six-boss silhouettes under bounded load.
        from systems.fix104_visuals import append_body
        if append_body(self,quads,c,visual_budget):
            return

        species = str(getattr(c, "species", ""))
        cx = float(c.x)
        cy = float(c.y) - float(c.height()) * 0.5
        w = float(c.width())
        h = float(c.height())

        # FIX4: draw edited creature pixel art first; preserve creature physics
        # dimensions and feet anchor. Facing mirrors the artwork horizontally.
        _behavior = str(getattr(c, "behavior_state", "idle"))
        _moving = (
            abs(float(getattr(c, "vx", 0.0))) > 1.0
            or abs(float(getattr(c, "vy", 0.0))) > 1.0
            or _behavior in ("swim","waterbird","fly_low","chase","wander","attack","recoil")
        )
        # FIX19: authored AI actions can request any named animation state.
        # Ordinary locomotion keeps the compact idle/move compatibility path.
        _override = str(getattr(c, "animation_state_override", "") or "")
        _anim_state = _override if _override else ("move" if _moving else "idle")
        if species == "giant_python" and not _override and float(getattr(c,"hurt_timer",0.0)) > 0.0:
            _anim_state = "hurt"
        custom = self._pixel_asset_quads(
            self.game, "creature." + species,
            cx - w * 0.5, float(c.y) - h, w, h,
            flip_x=(int(getattr(c, "facing", 1)) < 0),
            animation=_anim_state,
            animation_time=float(getattr(c, "visual_animation_time", getattr(c, "motion_time", 0.0))),
        )
        if custom is not None:
            quads.extend(custom)
            if float(getattr(c, "hurt_timer", 0.0)) > 0.0:
                quads.append(self._quad(cx, cy, w, h, (1.0, 0.25, 0.20, 0.20)))
            return

        # generic body fallback
        quads.append(self._quad(cx, cy, w, h, ccolor))
        dark = (max(0.0, ccolor[0]-0.12), max(0.0, ccolor[1]-0.12), max(0.0, ccolor[2]-0.12), ccolor[3])
        light = (min(1.0, ccolor[0]+0.12), min(1.0, ccolor[1]+0.12), min(1.0, ccolor[2]+0.12), ccolor[3])
        # simple silhouette accents per species/group.
        if species in ("desert_snake", "swamp_snake", "jungle_snake"):
            quads.append(self._quad(cx-w*0.22, cy-h*0.08, w*0.24, h*0.20, light))
            quads.append(self._quad(cx+w*0.28, cy-h*0.22, w*0.16, h*0.16, light))
        elif species in ("desert_scorpion", "desert_beetle", "jungle_spider", "mangrove_crab", "shore_crab"):
            quads.append(self._quad(cx-w*0.38, cy+h*0.10, w*0.18, 3.0, dark))
            quads.append(self._quad(cx+w*0.38, cy+h*0.10, w*0.18, 3.0, dark))
            quads.append(self._quad(cx, cy-h*0.34, w*0.20, h*0.18, dark))
        elif species in ("swamp_frog",):
            quads.append(self._quad(cx-w*0.20, cy+h*0.22, w*0.18, h*0.18, dark))
            quads.append(self._quad(cx+w*0.20, cy+h*0.22, w*0.18, h*0.18, dark))
        elif species in ("crocodile",):
            quads.append(self._quad(cx-w*0.32, cy-h*0.08, w*0.36, h*0.20, dark))
            quads.append(self._quad(cx+w*0.32, cy+h*0.06, w*0.26, h*0.16, light))
        elif species in ("wolf", "snow_wolf", "village_dog", "jaguar", "boar", "deer", "mountain_goat", "capybara", "otter", "sea_turtle"):
            quads.append(self._quad(cx-w*0.30, cy-h*0.18, w*0.22, h*0.26, light))
            quads.append(self._quad(cx+w*0.24, cy-h*0.06, w*0.26, h*0.16, dark))
            if species in ("deer", "mountain_goat"):
                quads.append(self._quad(cx-w*0.34, cy-h*0.34, 3.0, h*0.18, dark))
                quads.append(self._quad(cx-w*0.26, cy-h*0.34, 3.0, h*0.16, dark))
            if species == "sea_turtle":
                quads.append(self._quad(cx, cy, w*0.72, h*0.72, dark))
        elif species in ("sardine", "mackerel", "sea_bass", "carp", "crucian_carp", "freshwater_bass"):
            # Narrow body + tail gives a readable swimming silhouette without
            # importing heavyweight sprite animation yet.
            tail_x = cx - (w * 0.55 if int(getattr(c, "facing", 1)) >= 0 else -w * 0.55)
            quads.append(self._quad(cx, cy, w*0.78, h*0.68, light))
            quads.append(self._quad(tail_x, cy, w*0.22, h*0.55, dark))
            quads.append(self._quad(cx, cy-h*0.34, w*0.22, 2.0, dark))
        elif species in ("mallard", "kingfisher"):
            quads.append(self._quad(cx, cy, w*0.72, h*0.58, light))
            quads.append(self._quad(cx+w*0.34, cy-h*0.14, w*0.22, h*0.28, dark))
            quads.append(self._quad(cx-w*0.08, cy+h*0.10, w*0.34, 3.0, dark))
        elif species in ("dragonfly", "damselfly"):
            quads.append(self._quad(cx, cy, w*0.46, max(2.0, h*0.35), dark))
            quads.append(self._quad(cx-w*0.22, cy-h*0.10, w*0.42, 2.0, light))
            quads.append(self._quad(cx+w*0.22, cy+h*0.10, w*0.42, 2.0, light))
        elif species in ("gull", "heron"):
            quads.append(self._quad(cx, cy, w*0.86, h*0.42, light))
            quads.append(self._quad(cx-w*0.14, cy-h*0.28, w*0.12, h*0.22, light))
            quads.append(self._quad(cx+w*0.36, cy-h*0.02, w*0.18, 3.0, dark))
        elif species in ("slime", "swamp_slime", "ice_slime"):
            quads.append(self._quad(cx, cy-h*0.10, w*0.60, h*0.34, light))

    def _append_authored_creature_vfx(self, quads, game, budget=1100):
        system=getattr(game,'creature_system',None)
        snap=getattr(system,'authored_creature_vfx_snapshot',None)
        if not callable(snap) or budget<4:return 0
        try:rows=tuple(snap() or ())
        except Exception:return 0
        from systems.fix104_visuals import stamp
        start=len(quads); remaining=int(budget)
        for row in rows:
            if remaining<4:break
            _frame=row.get('frame_index',None)
            if _frame is None:
                data=game.assets.pixel_animation_rects(str(row.get('asset','')),str(row.get('state','effect')),time_seconds=float(row.get('age',0.)))
            else:
                data=game.assets.pixel_animation_rects(str(row.get('asset','')),str(row.get('state','effect')),frame_index=int(_frame))
            if not data:continue
            box=row.get('box')
            if not isinstance(box,(tuple,list)) or len(box)!=4:continue
            used=stamp(self,quads,data,tuple(float(v) for v in box),int(row.get('facing',1))<0,min(remaining,220),fit_visible=False)
            remaining-=used
        return len(quads)-start

    def _append_creature_attack_feedback(self, quads, game):
        """Draw FIX68 creature attack packets in one bounded Metal batch.

        CreatureSystem publishes these packets only after an authoritative AI
        action or collision decision.  This renderer consumes world positions
        and presentation metadata without reading damage or changing gameplay.
        Every pattern is made from the existing axis-aligned quad primitive so
        all species share one draw call and no UIKit object is created.
        """
        creature_system = getattr(game, "creature_system", None)
        snapshot = getattr(creature_system, "creature_vfx_snapshot", None)
        if not callable(snapshot):
            return 0
        try:
            packets = tuple(snapshot(max_events=24, max_quads=192) or ())
        except Exception:
            return 0

        start_count = len(quads)
        total_cap = 192

        for packet in packets:
            if len(quads) - start_count >= total_cap:
                break
            try:
                event_cap = max(0, min(
                    18,
                    int(packet.get("quad_budget", 0) or 0),
                    total_cap - (len(quads) - start_count),
                ))
            except Exception:
                event_cap = 0
            if event_cap <= 0:
                continue

            event_start = len(quads)
            colors = tuple(packet.get("colors", ()) or ())
            colors = tuple(
                tuple(float(v) for v in color[:4])
                for color in colors
                if isinstance(color, (tuple, list)) and len(color) >= 4
            ) or ((1.0, 1.0, 1.0, 0.90),)
            while len(colors) < 3:
                colors += (colors[-1],)

            progress = max(0.0, min(1.0, float(packet.get("progress", 0.0) or 0.0)))
            fade = max(0.05, (1.0 - progress) ** 0.62)
            scale = max(0.10, min(2.50, float(packet.get("scale", 1.0) or 1.0)))
            radius = max(3.0, min(160.0, float(packet.get("radius", 30.0) or 30.0))) * scale
            source_x = float(packet.get("x", 0.0) or 0.0)
            source_y = float(packet.get("y", 0.0) or 0.0)
            target_x = float(packet.get("target_x", source_x) or source_x)
            target_y = float(packet.get("target_y", source_y) or source_y)
            direction_x = float(packet.get("direction_x", 1.0) or 0.0)
            direction_y = float(packet.get("direction_y", 0.0) or 0.0)
            direction_length = max(1e-6, math.hypot(direction_x, direction_y))
            direction_x /= direction_length
            direction_y /= direction_length
            perpendicular_x = -direction_y
            perpendicular_y = direction_x
            style = str(packet.get("style", "claw_slash") or "claw_slash")
            phase = str(packet.get("phase", "impact") or "impact")
            outcome = str(packet.get("outcome", "released") or "released")

            radial_styles = {
                "slime_splash", "toxic_splash", "water_burst", "fire_burst",
                "ice_shards", "crystal_shards", "earth_spikes",
                "ground_burst", "explosion_ring", "arcane_runes", "sonic_rings",
            }
            if phase == "impact" and style not in radial_styles:
                center_x, center_y = target_x, target_y
            else:
                center_x, center_y = source_x, source_y

            def color(index, alpha_scale=1.0):
                base = colors[int(index) % len(colors)]
                return (
                    max(0.0, min(1.0, base[0])),
                    max(0.0, min(1.0, base[1])),
                    max(0.0, min(1.0, base[2])),
                    max(0.0, min(1.0, base[3] * fade * float(alpha_scale))),
                )

            def add(x, y, width, height, rgba):
                if len(quads) - event_start >= event_cap:
                    return False
                if len(quads) - start_count >= total_cap:
                    return False
                quads.append(self._quad(
                    float(x), float(y), max(1.5, float(width)),
                    max(1.5, float(height)), rgba,
                ))
                return True

            expansion = radius * (0.28 + 0.72 * progress)

            if style == 'vine_whip':
                # A visible forward lash from the plant, not a generic ring.
                reach=min(128.,radius)*(0.55+0.45*math.sin(min(1.,progress)*math.pi))
                sign=1. if direction_x>=0 else -1.
                for j in range(14):
                    t=j/13.
                    bend=math.sin(t*math.pi*1.6+progress*5.)*(1.-t)*14.
                    add(source_x+sign*reach*t,source_y+bend,
                        9.5,4.+2.*(1.-t),color(j%3))
            elif style in ("claw_slash", "spectral_claw", "golden_slash", "metal_sparks"):
                # Three staggered, forward cuts.  Point chains read as angled
                # pixel slashes even though the shared primitive is axis-aligned.
                for lane in (-1, 0, 1):
                    for step in range(4):
                        t = (step + 1.0) / 5.0
                        bend = (lane * 6.0 + math.sin((t + progress) * math.pi) * 3.0) * scale
                        add(
                            center_x + direction_x * radius * (t - 0.42) + perpendicular_x * bend,
                            center_y + direction_y * radius * (t - 0.42) + perpendicular_y * bend,
                            3.2 + (3 - step) * 0.45, 3.2 + (3 - step) * 0.45,
                            color(step + lane, 0.72 + 0.09 * step),
                        )
            elif style == "fang_arc":
                for fang in (-1, 0, 1):
                    side = fang * radius * 0.22
                    for step in range(3):
                        reach = radius * (0.12 + step * 0.16)
                        add(
                            center_x - direction_x * reach + perpendicular_x * side,
                            center_y - direction_y * reach + perpendicular_y * side,
                            3.0 + step * 1.0, 4.2 + step * 1.2,
                            color(step, 0.90),
                        )
            elif style == "venom_sting":
                for step in range(8):
                    t = step / 7.0
                    wobble = math.sin(t * math.pi * 2.0 + progress * 5.0) * 3.5 * scale
                    add(
                        source_x + direction_x * radius * t + perpendicular_x * wobble,
                        source_y + direction_y * radius * t + perpendicular_y * wobble,
                        3.0 + t * 1.8, 3.0 + t * 1.8,
                        color(step, 0.55 + 0.40 * t),
                    )
                add(target_x, target_y, 7.0 * scale, 7.0 * scale, color(0, 1.0))
            elif style in ("speed_burst", "dive_wake"):
                for lane in (-2, -1, 1, 2):
                    for step in range(3):
                        distance = radius * (0.18 + step * 0.20)
                        side = lane * 3.5 * scale
                        add(
                            source_x - direction_x * distance + perpendicular_x * side,
                            source_y - direction_y * distance + perpendicular_y * side,
                            4.0 + step * 4.0, 2.4 + abs(lane) * 0.35,
                            color(step + lane, 0.48 + step * 0.18),
                        )
            elif style == "lightning_burst":
                for step in range(12):
                    t = step / 11.0
                    zig = ((step % 2) * 2.0 - 1.0) * (4.0 + (step % 3)) * scale
                    add(
                        source_x + (target_x - source_x) * t + perpendicular_x * zig,
                        source_y + (target_y - source_y) * t + perpendicular_y * zig,
                        3.6 if step % 3 else 5.0, 3.6 if step % 3 else 5.0,
                        color(step, 0.70 + 0.25 * (step % 2)),
                    )
            elif style in ("arcane_runes", "sonic_rings"):
                rings = 2 if event_cap >= 16 else 1
                points = max(6, event_cap // rings)
                for ring in range(rings):
                    ring_radius = expansion * (0.58 + ring * 0.38)
                    for index in range(points):
                        angle = index * math.pi * 2.0 / points + progress * (2.4 if ring else -1.8)
                        add(
                            center_x + math.cos(angle) * ring_radius,
                            center_y + math.sin(angle) * ring_radius,
                            3.0 + ring, 3.0 + ring, color(index + ring, 0.78),
                        )
            else:
                # Splash, elemental shards, ground bursts and explosions use
                # distinct radial silhouettes selected by style and palette.
                shard = style in ("ice_shards", "crystal_shards", "earth_spikes", "metal_sparks")
                splash = style in ("slime_splash", "toxic_splash", "water_burst", "fire_burst")
                points = max(1, event_cap)
                for index in range(points):
                    angle = index * math.pi * 2.0 / points
                    wave = 0.78 + 0.22 * math.sin(index * 2.37 + progress * 7.0)
                    distance = expansion * wave
                    size = (5.4 + (index % 3) * 1.2) if shard else (4.0 + (index % 2) * 1.4)
                    if splash:
                        distance *= 0.62 + 0.38 * abs(math.sin(angle))
                    add(
                        center_x + math.cos(angle) * distance,
                        center_y + math.sin(angle) * distance,
                        size * scale, (size * (1.45 if shard else 1.0)) * scale,
                        color(index, 0.72 + 0.22 * (index % 3) / 2.0),
                    )

            # A blocked impact receives one bright centre flash when budget is
            # available; shield geometry itself remains owned by equipment.
            if outcome == "blocked":
                add(target_x, target_y, 7.5 * scale, 7.5 * scale, color(0, 1.0))

        return len(quads) - start_count


    @staticmethod
    def _minimap_semantic_code(game, tx, ty):
        """Return one compact terrain class for the global minimap."""
        world = getattr(game, "world", None)
        if world is None:
            return "air"

        # Free water is a gameplay layer, not a terrain tile, and therefore
        # must be checked before AIR/solid classification.
        try:
            env_state = getattr(getattr(game, "environment", None), "state", None)
            if env_state is not None and float(env_state.water_amount(tx, ty)) > 0.08:
                return "water"
        except Exception:
            pass

        try:
            tile_id = world.get_tile(int(tx), int(ty))
            td = tile_def(tile_id)
        except Exception:
            return "air"

        # FIX68 segmented rainforest tiles declare their minimap intent in the
        # shared registry: trunk/root/branch are structure, vine is ladder and
        # passable canopy remains air. Future authored materials can reuse the
        # same hint without another renderer-only name list.
        hint = str(getattr(td, "minimap_group", "") or "")
        if hint in (
            "air", "rock", "soil", "grass", "sand", "snow", "swamp",
            "water", "structure", "ladder",
        ):
            return hint

        if bool(getattr(td, "ladder", False)):
            return "ladder"
        if not bool(getattr(td, "solid", False)):
            return "air"

        name = str(getattr(td, "name", "") or "")
        if name in ("grass_dirt",):
            return "grass"
        if name in ("sand", "sea_sand"):
            return "sand"
        if name in ("snow_dirt", "ice"):
            return "snow"
        if name in ("swamp_soil", "jungle_soil", "mud"):
            return "swamp"
        if name in ("dirt", "ash"):
            return "soil"
        if name in ("wood", "village_path"):
            return "structure"
        # Stone, limestone, marble and every ore intentionally collapse to
        # rock so caves/tunnels remain visually obvious at tiny scale.
        return "rock"

    @staticmethod
    def _minimap_color(code):
        return {
            "air": MINIMAP_AIR_RGBA,
            "rock": MINIMAP_ROCK_RGBA,
            "soil": MINIMAP_SOIL_RGBA,
            "grass": MINIMAP_GRASS_RGBA,
            "sand": MINIMAP_SAND_RGBA,
            "snow": MINIMAP_SNOW_RGBA,
            "swamp": MINIMAP_SWAMP_RGBA,
            "water": MINIMAP_WATER_RGBA,
            "structure": MINIMAP_STRUCTURE_RGBA,
            "ladder": MINIMAP_LADDER_RGBA,
        }.get(str(code), MINIMAP_ROCK_RGBA)

    def _refresh_minimap_cache(self, game, now):
        """Sample full world topology into horizontally merged runs.

        96x16 samples represent a 768x64 FIX27/28 world at 8x4 tiles per
        sample. Refresh is limited to roughly 1.3 Hz, with immediate rebuild on
        terrain revision/size changes. Water can therefore change on the map
        without burdening the 60 Hz renderer.
        """
        world = getattr(game, "world", None)
        if world is None:
            self._minimap_runs = tuple()
            return
        width = max(1, int(getattr(world, "width_tiles", 1) or 1))
        height = max(1, int(getattr(world, "height_tiles", 1) or 1))
        revision = int(getattr(world, "revision", 0) or 0)
        epoch = int(getattr(world, "cache_epoch", 0) or 0)
        size_key = (width, height)
        due = (float(now) - float(self._minimap_cache_time)) >= 0.75
        changed = (
            epoch != self._minimap_cache_epoch or
            revision != self._minimap_cache_revision or
            size_key != self._minimap_cache_size
        )
        if self._minimap_runs and not due and not changed:
            return

        cols = max(24, min(int(self._minimap_sample_cols), width))
        rows = max(8, min(int(self._minimap_sample_rows), height))
        step_x = float(width) / float(cols)
        step_y = float(height) / float(rows)

        grid = []
        for sy in range(rows):
            ty = min(height - 1, max(0, int((sy + 0.5) * step_y)))
            row = []
            for sx in range(cols):
                tx = min(width - 1, max(0, int((sx + 0.5) * step_x)))
                row.append(self._minimap_semantic_code(game, tx, ty))
            grid.append(row)

        # Convert each row to color runs. Air is retained because it makes
        # tunnels/caves explicit against the dark frame rather than invisible.
        runs = []
        for sy, row in enumerate(grid):
            start = 0
            current = row[0] if row else "air"
            for sx in range(1, cols + 1):
                code = row[sx] if sx < cols else None
                if code != current:
                    runs.append((start, sx, sy, current))
                    if sx < cols:
                        start = sx
                        current = code
        self._minimap_runs = tuple(runs)
        self._minimap_cache_time = float(now)
        self._minimap_cache_revision = revision
        self._minimap_cache_size = size_key
        self._minimap_cache_epoch = epoch

    def _build_global_minimap_quads(self, game, render_camera_x, render_camera_y, now):
        """Build a screen-fixed global minimap using ordinary Metal quads."""
        self._refresh_minimap_cache(game, now)
        world = getattr(game, "world", None)
        if world is None:
            return []

        width_tiles = max(1, int(getattr(world, "width_tiles", 1) or 1))
        height_tiles = max(1, int(getattr(world, "height_tiles", 1) or 1))
        cols = max(24, min(int(self._minimap_sample_cols), width_tiles))
        rows = max(8, min(int(self._minimap_sample_rows), height_tiles))

        viewport_w = max(1.0, float(getattr(game, "viewport_w", self.viewport_w) or self.viewport_w))
        viewport_h = max(1.0, float(getattr(game, "viewport_h", self.viewport_h) or self.viewport_h))

        # Keep clear of iPhone safe-area close/save/load buttons and the right
        # aim stick.  On an 852x393 landscape viewport this is roughly x=560..742,
        # y=56..126.
        ipad = is_ipad()
        map_w = max(190.0 if ipad else 150.0, min(250.0 if ipad else 190.0, viewport_w * (0.20 if ipad else 0.225)))
        map_h = max(80.0 if ipad else 62.0, min(108.0 if ipad else 78.0, viewport_h * (0.16 if ipad else 0.195)))
        right_margin = 150.0 if ipad else 110.0
        screen_left = max(12.0 if ipad else 8.0, viewport_w - right_margin - map_w)
        screen_top = 66.0 if ipad else 56.0
        pad = 3.0
        inner_left = screen_left + pad
        inner_top = screen_top + pad
        inner_w = max(1.0, map_w - pad * 2.0)
        inner_h = max(1.0, map_h - pad * 2.0)

        # All dynamic world quads pass through the camera transform. Adding the
        # current camera origin converts screen-space minimap coordinates back
        # into world coordinates, so the minimap stays fixed on screen.
        world_left = float(render_camera_x) + screen_left
        world_top = float(render_camera_y) + screen_top
        quads = [
            self._quad(world_left + map_w * 0.5, world_top + map_h * 0.5, map_w, map_h, MINIMAP_BG_RGBA),
        ]

        cell_w = inner_w / float(cols)
        cell_h = inner_h / float(rows)
        for sx0, sx1, sy, code in self._minimap_runs:
            x0 = float(render_camera_x) + inner_left + float(sx0) * cell_w
            x1 = float(render_camera_x) + inner_left + float(sx1) * cell_w
            cy = float(render_camera_y) + inner_top + (float(sy) + 0.5) * cell_h
            quads.append(self._quad((x0 + x1) * 0.5, cy, max(0.8, x1 - x0 + 0.15), max(0.8, cell_h + 0.15), self._minimap_color(code)))

        # Dungeon/underground entrances are important navigational landmarks.
        try:
            payload = getattr(getattr(game, "map_loader", None), "payload", None)
            meta = payload.get("metadata", {}) if isinstance(payload, dict) else {}
            entrances = meta.get("underground_entrances", []) if isinstance(meta, dict) else []
            for entry in entrances or []:
                if not isinstance(entry, dict):
                    continue
                ex = float(entry.get("x", 0.0))
                ey = float(entry.get("top", 0.0))
                mx = float(render_camera_x) + inner_left + (ex / float(width_tiles)) * inner_w
                my = float(render_camera_y) + inner_top + (ey / float(height_tiles)) * inner_h
                quads.append(self._quad(mx, my, 3.8, 3.8, MINIMAP_ENTRANCE_RGBA))
        except Exception:
            pass

        # FIX32 world-graph portal markers. Purple = ordinary door/cave; boss
        # gates remain slightly redder so submap routes are visible globally.
        try:
            payload=getattr(getattr(game,"map_loader",None),"payload",None)
            meta=payload.get("metadata",{}) if isinstance(payload,dict) else {}
            for row in (meta.get("portals",[]) or []):
                if not isinstance(row,dict):continue
                arrival=row.get("arrival",())
                if not isinstance(arrival,(list,tuple)) or len(arrival)<2:continue
                ex=float(arrival[0]);ey=float(arrival[1])
                mx=float(render_camera_x)+inner_left+(ex/float(width_tiles))*inner_w
                my=float(render_camera_y)+inner_top+(ey/float(height_tiles))*inner_h
                quads.append(self._quad(mx,my,3.6,3.6,(0.72,0.38,1.0,0.96)))
        except Exception:
            pass

        # Current camera rectangle.
        view_tx = max(0.0, float(render_camera_x) / float(TILE_SIZE))
        view_ty = max(0.0, float(render_camera_y) / float(TILE_SIZE))
        view_tw = viewport_w / float(TILE_SIZE)
        view_th = viewport_h / float(TILE_SIZE)
        rx0 = inner_left + (view_tx / float(width_tiles)) * inner_w
        ry0 = inner_top + (view_ty / float(height_tiles)) * inner_h
        rw = max(3.0, min(inner_w, (view_tw / float(width_tiles)) * inner_w))
        rh = max(3.0, min(inner_h, (view_th / float(height_tiles)) * inner_h))
        rx0 = max(inner_left, min(inner_left + inner_w - rw, rx0))
        ry0 = max(inner_top, min(inner_top + inner_h - rh, ry0))
        line = 1.1
        wx0 = float(render_camera_x) + rx0
        wy0 = float(render_camera_y) + ry0
        quads.extend((
            self._quad(wx0 + rw * 0.5, wy0, rw, line, MINIMAP_CAMERA_RGBA),
            self._quad(wx0 + rw * 0.5, wy0 + rh, rw, line, MINIMAP_CAMERA_RGBA),
            self._quad(wx0, wy0 + rh * 0.5, line, rh, MINIMAP_CAMERA_RGBA),
            self._quad(wx0 + rw, wy0 + rh * 0.5, line, rh, MINIMAP_CAMERA_RGBA),
        ))

        # Player marker is intentionally larger than one map sample so it
        # remains readable while traversing deep dungeons.
        p = getattr(game, "player", None)
        if p is not None:
            ptx = max(0.0, min(float(width_tiles), float(getattr(p, "x", 0.0)) / float(TILE_SIZE)))
            pty = max(0.0, min(float(height_tiles), float(getattr(p, "y", 0.0)) / float(TILE_SIZE)))
            pmx = float(render_camera_x) + inner_left + (ptx / float(width_tiles)) * inner_w
            pmy = float(render_camera_y) + inner_top + (pty / float(height_tiles)) * inner_h
            quads.append(self._quad(pmx, pmy, 4.8, 4.8, MINIMAP_PLAYER_RGBA))
            quads.append(self._quad(pmx, pmy, 1.7, 1.7, MINIMAP_CAMERA_RGBA))

        # Crisp frame last so map contents cannot cover it.
        border = 1.5
        quads.extend((
            self._quad(world_left + map_w * 0.5, world_top, map_w, border, MINIMAP_BORDER_RGBA),
            self._quad(world_left + map_w * 0.5, world_top + map_h, map_w, border, MINIMAP_BORDER_RGBA),
            self._quad(world_left, world_top + map_h * 0.5, border, map_h, MINIMAP_BORDER_RGBA),
            self._quad(world_left + map_w, world_top + map_h * 0.5, border, map_h, MINIMAP_BORDER_RGBA),
        ))
        return quads

    @staticmethod
    def _equipment_visual_id(value):
        """Return an equipment id without depending on one catalog class.

        FIX67's renderer is deliberately a read-only client of the equipment
        system.  A live build can return an id, catalog dictionary or item
        object from ``equipped_item``; older test/fallback hosts can expose the
        same value through a slot dictionary.  Normalising at this boundary
        keeps presentation failures from affecting gameplay or save loading.
        """
        if value is None or isinstance(value, bool):
            return ""
        if isinstance(value, dict):
            for key in ("equipment_id", "item_id", "id", "key"):
                found = value.get(key)
                if found:
                    return str(found)
            nested = value.get("definition") or value.get("item")
            if nested is not None and nested is not value:
                return MetalKitRenderer._equipment_visual_id(nested)
            return ""
        if isinstance(value, (tuple, list)):
            return MetalKitRenderer._equipment_visual_id(value[0] if value else None)
        for attr in ("equipment_id", "item_id", "id", "key"):
            try:
                found = getattr(value, attr, None)
                if found:
                    return str(found)
            except Exception:
                pass
        return str(value)

    def _equipped_visual_ids(self, game):
        """Read the three wearable slots with safe legacy/test fallbacks."""
        equipment = getattr(game, "equipment", None)
        if equipment is None:
            return {"foot": "", "body": "", "head": ""}

        slot_ids = None
        primary_getter = getattr(equipment, "equipped_item", None)
        if not callable(primary_getter):
            visual_layers = getattr(equipment, "visual_layers", None)
            if callable(visual_layers):
                try:
                    layers = visual_layers()
                    if isinstance(layers, dict):
                        slot_ids = layers
                except Exception:
                    pass
            player_slot_ids = getattr(equipment, "player_slot_ids", None)
            if slot_ids is None and callable(player_slot_ids):
                try:
                    slot_ids = player_slot_ids()
                except Exception:
                    slot_ids = None

        out = {}
        for slot_index, slot in enumerate(("foot", "body", "head")):
            value = None
            if callable(primary_getter):
                try:
                    value = primary_getter(slot)
                except Exception:
                    value = None
            if value is None:
                getter = getattr(equipment, "get_equipped", None)
                if callable(getter):
                    try:
                        value = getter(slot)
                    except Exception:
                        value = None
            if value is None and isinstance(slot_ids, dict):
                value = slot_ids.get(slot)
            if value is None and isinstance(slot_ids, (tuple, list)) and slot_index < len(slot_ids):
                value = slot_ids[slot_index]
            if value is None:
                for name in ("equipped_slots", "slots", "equipment"):
                    table = getattr(equipment, name, None)
                    if isinstance(table, dict) and slot in table:
                        value = table.get(slot)
                        break
            if value is None:
                # Small compatibility path for lightweight diagnostic doubles.
                value = getattr(equipment, slot, None)
            if value is None:
                equipped = getattr(equipment, "equipped", None)
                if callable(equipped):
                    try:
                        candidate = equipped(slot)
                        if candidate is not False:
                            value = candidate
                    except Exception:
                        pass
                elif isinstance(equipped, dict):
                    value = equipped.get(slot)
            out[slot] = self._equipment_visual_id(value).strip().lower()
        return out

    @staticmethod
    def _equipment_visual_kind(item_id):
        """Map canonical FIX67 ids and compact aliases to one visual kind."""
        value = str(item_id or "").strip().lower().replace("-", "_")
        aliases = (
            ("double_jump_shoes", "double_jump"),
            ("air_dash_shoes", "air_dash"),
            ("hover_shoes", "hover"),
            ("shield_armor", "shield_armor"),
            ("wind_god_wings", "wind_wings"),
            ("electric_mouse_helmet", "electric_mouse"),
            ("sky_puppy_helmet", "sky_puppy"),
        )
        for suffix, kind in aliases:
            if value == suffix or value.endswith("." + suffix) or value.endswith("_" + suffix):
                return kind
        return ""

    def _append_equipment_visuals(
        self, quads, game, player, render_player_x, render_player_y,
        animation_state, animation_frame, now, phase="front", equipment_slots=None,
    ):
        """Append posture-aware wearables to the existing actor instance batch.

        ``behind`` emits only translucent shield interiors. ``front`` emits the
        crisp shield rim and the three character overlays.  Calling the two
        phases around the authored/fallback player body gives shields depth
        while still requiring exactly the renderer's existing draw call.
        """
        slots = equipment_slots if isinstance(equipment_slots, dict) else self._equipped_visual_ids(game)
        foot_kind = self._equipment_visual_kind(slots.get("foot"))
        body_kind = self._equipment_visual_kind(slots.get("body"))
        head_kind = self._equipment_visual_kind(slots.get("head"))
        if not (foot_kind or body_kind or head_kind):
            return

        x = float(render_player_x)
        y = float(render_player_y)
        facing = 1.0 if int(getattr(player, "facing", 1) or 1) >= 0 else -1.0
        pose = str(animation_state or getattr(player, "state", "stand") or "stand").lower()
        frame = max(0, int(animation_frame or 0))
        grounded = bool(getattr(player, "grounded", False))
        double_jump_active = (not grounded) and bool(getattr(player, "double_jump_used", False))
        air_dash_active = (not grounded) and bool(getattr(player, "air_dash_active", False))
        hover_active = (not grounded) and bool(getattr(player, "hover_active", False))

        moving_pose = pose in ("walk", "run", "crouch_walk", "crouch_run", "crawl", "climb")
        bob = -1.0 if moving_pose and frame % 2 else 0.0
        flat = pose in ("prone", "crawl", "roll")
        crouched = pose.startswith("crouch")

        if flat:
            head_x = x + facing * (8.0 if pose == "roll" else 14.0)
            head_y = y - (16.0 if pose == "roll" else 13.0) + bob
            body_x = x - facing * 2.0
            body_y = y - 11.0 + bob
            torso_w, torso_h = (22.0, 14.0) if pose == "roll" else (28.0, 10.0)
            if pose == "roll":
                shoes = ((x-facing*11.0, y-5.0), (x+facing*9.0, y-4.0))
            else:
                shoes = ((x-facing*15.0, y-3.0), (x-facing*8.0, y-5.0))
        elif crouched:
            head_x = x + facing * 3.5
            head_y = y - 29.0 + bob
            body_x = x - facing * 1.5
            body_y = y - 18.0 + bob
            torso_w, torso_h = 20.0, 17.0
            shoes = ((x+facing*7.0, y-3.0), (x-facing*5.0, y-4.0))
        else:
            head_x = x + (facing * 1.5 if pose in ("jump", "fall") else 0.0)
            head_y = y - 44.0 + bob
            body_x = x
            body_y = y - 27.0 + bob
            torso_w, torso_h = 19.0, 23.0
            stride = 2.5 if moving_pose and frame % 4 in (1, 2) else 0.0
            if pose == "climb":
                shoes = ((x+5.0, y-5.0-frame%2*2.0), (x-5.0, y-7.0+(frame%2)*2.0))
            else:
                shoes = ((x+facing*(5.0+stride), y-3.0), (x-facing*(5.0+stride), y-4.0))

        # FIX83: editable wings are behind the player rather than covering the
        # head/weapon. Use a dedicated flight clock, independent of walk frames.
        if body_kind == "wind_wings" and phase == "behind":
            flying = bool(getattr(player, "wing_flying", False))
            try:
                wing_quads = self._pixel_asset_quads(
                    game, "equipment.wind_god_wings", x-12, y-58, 24, 58,
                    flip_x=(facing < 0), animation="fly" if flying else "idle",
                    frame_index=int(float(getattr(player,"wing_animation_time",0))*7) % 4 if flying else 0)
            except Exception:
                wing_quads = None
            if wing_quads is not None:
                quads.extend(wing_quads)
            else:
                for side in (-1,1):
                    for i in range(5):
                        quads.append(self._quad(body_x+side*(11+i*6),body_y-i*4,8,20-i*2,(.46,.86,.90,1)))
        if body_kind == "wind_wings" and phase == "front":
            # Per-copy seconds + bar; rendered in the existing Metal instance
            # batch, no UIKit label creation during long gameplay sessions.
            left=max(0.0,min(300.0,float(getattr(player,"wing_time_remaining",0))))
            bar_y=y-max(60.0,float(player.height())+12.0)
            quads.append(self._quad(x,bar_y,46,5,(.06,.12,.17,1)))
            if left>0:quads.append(self._quad(x-22+22*left/300,bar_y,44*left/300,3,(.46,.91,.86,1)))
            self._inventory_text(quads, str(int(math.ceil(left))), x-9, bar_y-11, 1.3, (.90,1.,.92,1), 0, 0)

        # The shield interior belongs behind the player, but its rim belongs in
        # front.  Its dimensions track the current physics posture so a prone
        # character never appears protected by a floating standing-size wall.
        if body_kind == "shield_armor":
            current_w = max(18.0, float(player.width()))
            current_h = max(18.0, float(player.height()))
            shield_half_span = current_w * 0.5 + 8.0
            shield_h = current_h + 6.0
            block_timer = max(0.0, float(getattr(player, "shield_block_timer", 0.0) or 0.0))
            block_strength = max(0.0, min(1.5, float(getattr(player, "shield_block_strength", 1.0) or 1.0)))
            raw_side = getattr(player, "shield_block_side", 0)
            hit_side = 0
            try:
                hit_side = 1 if float(raw_side) > 0.0 else (-1 if float(raw_side) < 0.0 else 0)
            except Exception:
                side_text = str(raw_side or "").lower()
                if side_text in ("front", "forward"):
                    hit_side = int(facing)
                elif side_text in ("back", "rear"):
                    hit_side = -int(facing)
                elif side_text == "left":
                    hit_side = -1
                elif side_text == "right":
                    hit_side = 1
            hit_active = block_timer > 0.0
            impact_expand = (3.0 + 2.0 * block_strength) if hit_active else 0.0
            pulse = 0.5 + 0.5 * math.sin(float(now) * 7.0)

            for side in (-1, 1):
                side_hit = hit_active and (hit_side == 0 or hit_side == side)
                sx = x + side * (shield_half_span + (impact_expand if side_hit else 0.0))
                sw = 6.0 + (3.5 * block_strength if side_hit else 0.0)
                sh = shield_h + (7.0 * block_strength if side_hit else 0.0)
                shield_y = y - sh * 0.5
                if phase == "behind":
                    alpha_value = (0.52 + 0.16*pulse) if side_hit else (0.22 + 0.07*pulse)
                    quads.append(self._quad(sx, shield_y, sw, sh, (
                        EQUIPMENT_SHIELD_RGBA[0], EQUIPMENT_SHIELD_RGBA[1],
                        EQUIPMENT_SHIELD_RGBA[2], alpha_value,
                    )))
                    # A travelling scan pixel makes the energy plane readable
                    # even on a bright sky, at one additional instance/side.
                    scan_y = shield_y - sh*.38 + ((float(now)*19.0 + side*7.0) % max(4.0, sh*.76))
                    quads.append(self._quad(sx-side*0.7, scan_y, sw+2.0, 2.0, (
                        .67, .97, 1.0, .34 if not side_hit else .78,
                    )))
                elif phase == "front":
                    edge_alpha = 1.0 if side_hit else .84
                    edge = (EQUIPMENT_SHIELD_EDGE_RGBA[0], EQUIPMENT_SHIELD_EDGE_RGBA[1],
                            EQUIPMENT_SHIELD_EDGE_RGBA[2], edge_alpha)
                    outer_x = sx + side * sw * .52
                    quads.append(self._quad(outer_x, shield_y, 2.4 if side_hit else 1.8, sh, edge))
                    quads.append(self._quad(sx, shield_y-sh*.5, sw+3.0, 2.1, edge))
                    quads.append(self._quad(sx, shield_y+sh*.5, sw+3.0, 2.1, edge))
                    if side_hit:
                        for spark_i in range(3):
                            spark_y = shield_y + (spark_i-1)*sh*.27
                            spark_x = outer_x + side*(4.0+spark_i*2.0)
                            quads.append(self._quad(spark_x, spark_y, 3.0, 3.0, (.84,1.0,1.0,.92)))

        if phase != "front":
            return

        # FIX67 authored equipment sources use the same 24px standing canvas,
        # state names and resolved frame index as player.default.  Draw those
        # editable overlays when registered; the compact geometry below is the
        # mandatory fallback for old projects or a missing/corrupt asset.
        authored_slots = set()
        player_visual_w = float(player.width("stand"))
        player_visual_h = float(player.height("stand"))
        for slot_name in ("foot", "body", "head"):
            item_id = str(slots.get(slot_name, "") or "")
            if slot_name == "body" and body_kind == "wind_wings":
                continue  # already drawn behind the player
            if not self._equipment_visual_kind(item_id):
                continue
            try:
                custom = self._pixel_asset_quads(
                    game, item_id,
                    x-player_visual_w*.5, y-player_visual_h,
                    player_visual_w, player_visual_h,
                    flip_x=(facing < 0.0),
                    animation=pose, frame_index=frame,
                )
            except Exception:
                custom = None
            if custom is not None:
                quads.extend(custom)
                authored_slots.add(slot_name)

        # Ability-state accents are runtime motion feedback, not replacement
        # artwork. Keep them visible even when the editable authored overlay is
        # present; otherwise a successful air dash/hover would lose its trail
        # merely because the 24x24 shoe sprite loaded correctly.
        if "foot" in authored_slots and not grounded:
            if foot_kind == "double_jump":
                glow_alpha = .78 if double_jump_active else .34
                for shoe_x, shoe_y in shoes:
                    quads.append(self._quad(
                        shoe_x-facing*1.5, shoe_y+5.0,
                        5.5 if double_jump_active else 3.8, 2.5,
                        (.52,.90,1.0,glow_alpha),
                    ))
            elif foot_kind == "air_dash" and air_dash_active:
                for trail_i in range(4):
                    distance = 9.0 + trail_i*6.0
                    quads.append(self._quad(
                        x-facing*distance, y-5.0+(trail_i%2)*2.0,
                        8.0-trail_i*.9, 3.0-trail_i*.35,
                        (1.0,.56 if trail_i<2 else .82,.10,
                         max(.20,.88-trail_i*.18)),
                    ))
            elif foot_kind == "hover" and hover_active:
                pulse_alpha = .62 + .20*math.sin(float(now)*8.0)
                for shoe_x, shoe_y in shoes:
                    quads.append(self._quad(
                        shoe_x, shoe_y+5.0, 9.0, 2.8,
                        (.40,.94,1.0,pulse_alpha),
                    ))

        # Body plate stays compact so the authored torso remains visible around
        # it.  Shoulder placement mirrors with facing and follows all postures.
        if body_kind == "shield_armor" and "body" not in authored_slots:
            plate_w = torso_w * .72
            plate_h = torso_h * .78
            quads.append(self._quad(body_x, body_y, plate_w, plate_h, EQUIPMENT_STEEL_RGBA))
            quads.append(self._quad(body_x-facing*plate_w*.34, body_y, 3.0, plate_h*.92, EQUIPMENT_DARK_RGBA))
            quads.append(self._quad(body_x+facing*plate_w*.34, body_y-plate_h*.05, 3.0, plate_h*.78, (.42,.77,.94,1.0)))
            quads.append(self._quad(body_x+facing*plate_w*.46, body_y-plate_h*.38, 5.0, 4.0, (.58,.90,1.0,1.0)))
            quads.append(self._quad(body_x, body_y, 4.0, 5.0, (.69,.96,1.0,.96)))

        # Two small side-view shoes are sufficient to preserve gait while
        # their palette/silhouette clearly communicates each air ability.
        for shoe_index, (shoe_x, shoe_y) in enumerate(shoes):
            if "foot" in authored_slots:
                break
            rear = -facing
            if foot_kind == "double_jump":
                quads.append(self._quad(shoe_x, shoe_y, 8.0, 5.0, (.22,.57,.91,1.0)))
                quads.append(self._quad(shoe_x+facing*1.0, shoe_y+2.4, 9.0, 2.0, EQUIPMENT_DARK_RGBA))
                quads.append(self._quad(shoe_x+rear*4.8, shoe_y-2.0, 3.2, 4.0, (.78,.95,1.0,.96)))
                quads.append(self._quad(shoe_x+rear*6.0, shoe_y-4.0, 2.4, 3.0, (.88,.99,1.0,.86)))
                if not grounded and shoe_index == 0:
                    quads.append(self._quad(shoe_x+rear*2.0, shoe_y+6.0, 5.0 if double_jump_active else 4.0, 3.0, (.52,.90,1.0,.76 if double_jump_active else .42)))
            elif foot_kind == "air_dash":
                quads.append(self._quad(shoe_x, shoe_y, 8.5, 5.0, (.88,.27,.18,1.0)))
                quads.append(self._quad(shoe_x+facing*1.0, shoe_y+2.4, 9.5, 2.0, EQUIPMENT_DARK_RGBA))
                quads.append(self._quad(shoe_x+rear*4.8, shoe_y, 3.2, 3.4, (.34,.39,.45,1.0)))
                if not grounded and (air_dash_active or abs(float(getattr(player, "vx", 0.0))) > 20.0):
                    flame = (8.0 if air_dash_active else 4.0) + (shoe_index % 2)*2.0
                    quads.append(self._quad(shoe_x+rear*(7.0+flame*.25), shoe_y, flame, 2.8, (1.0,.56,.10,.82)))
                    quads.append(self._quad(shoe_x+rear*(8.0+flame*.45), shoe_y, flame*.45, 1.6, (1.0,.92,.38,.92)))
            elif foot_kind == "hover":
                quads.append(self._quad(shoe_x, shoe_y-1.0, 8.5, 4.2, (.55,.39,.86,1.0)))
                quads.append(self._quad(shoe_x, shoe_y+2.0, 11.0, 2.4, (.32,.88,1.0,1.0)))
                glow_alpha = .30 + (.34 if hover_active else (.22 if not grounded else 0.0)) + .12*math.sin(float(now)*8.0+shoe_index)
                quads.append(self._quad(shoe_x, shoe_y+5.0, 8.0, 2.5, (.40,.94,1.0,max(.14,glow_alpha))))

        # Helmet anchors are pose-specific and all asymmetric pixels multiply
        # their X offset by facing, so crouch/crawl/roll never leave a floating
        # headpiece behind the player.
        if head_kind == "electric_mouse" and "head" not in authored_slots:
            quads.append(self._quad(head_x, head_y-2.0, 19.0, 9.0, EQUIPMENT_ELECTRIC_RGBA))
            quads.append(self._quad(head_x-facing*7.5, head_y+3.5, 4.0, 8.0, EQUIPMENT_ELECTRIC_RGBA))
            quads.append(self._quad(head_x-facing*5.5, head_y-13.0, 4.5, 18.0, EQUIPMENT_ELECTRIC_RGBA))
            quads.append(self._quad(head_x-facing*5.5, head_y-20.0, 4.5, 5.0, EQUIPMENT_ELECTRIC_TIP_RGBA))
            quads.append(self._quad(head_x+facing*6.5, head_y-14.5, 5.0, 21.0, EQUIPMENT_ELECTRIC_RGBA))
            quads.append(self._quad(head_x+facing*6.5, head_y-22.5, 5.0, 5.0, EQUIPMENT_ELECTRIC_TIP_RGBA))
            quads.append(self._quad(head_x+facing*8.0, head_y+3.0, 3.5, 3.5, EQUIPMENT_CHEEK_RGBA))
            quads.append(self._quad(head_x+facing*4.2, head_y-3.0, 2.2, 2.2, (.12,.10,.08,1.0)))
        elif head_kind == "sky_puppy" and "head" not in authored_slots:
            quads.append(self._quad(head_x, head_y-2.0, 19.0, 10.0, EQUIPMENT_SKY_RGBA))
            quads.append(self._quad(head_x-facing*7.5, head_y+3.5, 4.0, 8.0, EQUIPMENT_SKY_RGBA))
            quads.append(self._quad(head_x-facing*14.0, head_y-1.0, 14.0, 6.5, EQUIPMENT_SKY_RGBA))
            quads.append(self._quad(head_x-facing*20.0, head_y+1.0, 5.0, 5.0, EQUIPMENT_SKY_BLUE_RGBA))
            quads.append(self._quad(head_x+facing*14.0, head_y-1.0, 14.0, 6.5, EQUIPMENT_SKY_RGBA))
            quads.append(self._quad(head_x+facing*20.0, head_y+1.0, 5.0, 5.0, EQUIPMENT_SKY_BLUE_RGBA))
            quads.append(self._quad(head_x+facing*4.5, head_y-3.0, 2.4, 2.4, (.16,.30,.38,1.0)))
            quads.append(self._quad(head_x+facing*7.4, head_y+2.0, 3.2, 2.2, (.91,.64,.69,.86)))

    def _append_weapon_attack_accents(
        self, quads, game, player, render_player_x, render_player_y, now,
    ):
        """Add one bounded, reach-correct presentation layer to hand attacks.

        The legacy FIX54/art-authored body and effect remain untouched.  These
        sparse pixels only communicate the already-authoritative reach, speed
        and weight; they never feed collision or damage state.
        """
        melee = getattr(game, "melee", None)
        if melee is None or getattr(game, "action_mode", None) != getattr(game, "ACTION_WEAPON", "weapon"):
            return
        if float(getattr(melee, "swing_timer", 0.0) or 0.0) <= 0.0:
            return
        weapon = str(getattr(melee, "selected_weapon", "sword") or "sword")
        if weapon in ("flying_drone", "bee_swarm", "pressure_cannon", "alien_cannon", "quake_hammer"):
            return
        heavy = str(getattr(melee, "current_attack_kind", "") or "") == "heavy"
        try:
            progress = max(0.0, min(1.0, float(melee.attack_progress)))
        except Exception:
            progress = .5
        try:
            wd = melee.weapon_def(weapon) or {}
        except Exception:
            wd = {}
        try:
            reach_tiles = float(melee.effective_reach_tiles(weapon, heavy=heavy))
        except Exception:
            reach_key = "heavy_reach_tiles" if heavy else "reach_tiles"
            reach_tiles = float(wd.get(reach_key, wd.get("reach_tiles", 1.0)) or 1.0)
        centered_reach = max(18.0, float(TILE_SIZE) * max(.25, reach_tiles))
        try:
            profile = melee.presentation_profile(weapon) or {}
            scale_key = "heavy_vfx_scale" if heavy else "vfx_scale"
            visual_scale = max(.8, min(1.8, float(profile.get(scale_key, 1.0) or 1.0)))
        except Exception:
            visual_scale = 1.0

        facing = 1.0 if int(getattr(player, "facing", 1) or 1) >= 0 else -1.0
        hand_x = float(render_player_x) + facing * 7.0
        hand_y = float(render_player_y) - float(player.height()) * .53
        # MeleeSystem measures forward reach after the body-front edge, while
        # this visual originates at the hand (centre + 7px). Include that gap
        # so the outer pixel reaches the hitbox far edge. Radial whip uses the
        # player centre and therefore keeps ``centered_reach`` below.
        body_front_gap = max(0.0, float(player.width())*.5-7.0)
        reach = centered_reach + body_front_gap
        style = str((wd.get("heavy_style") if heavy else wd.get("attack_style")) or "slash")
        palette = {
            "sword": (.70,.91,1.0), "dagger": (.88,.96,1.0),
            "spear": (.50,.91,1.0), "battle_axe": (.62,.91,.53),
            "war_hammer": (1.0,.73,.30), "greatsword": (.62,.78,1.0),
            "energy_bow": (.25,.88,1.0), "whip": (.96,.66,.29),
            "laser_gun": (.30,.94,1.0),
        }.get(weapon, (.78,.91,1.0))
        accents = []
        budget = 48

        def emit(cx, cy, width, height, alpha_value=1.0, color=None):
            if len(accents) >= budget:
                return
            base = palette if color is None else color
            accents.append(self._quad(
                cx, cy, width, height,
                (base[0], base[1], base[2], max(0.0, min(1.0, alpha_value))),
            ))

        def basis(angle_degrees):
            radians = math.radians(float(angle_degrees))
            dx = facing * math.cos(radians)
            dy = math.sin(radians)
            return dx, dy, -dy, facing * math.cos(radians)

        # Ranged launchers use their real aim vector; their projectile trails
        # below communicate travel/reflection after leaving the hand.
        if weapon in ("energy_bow", "laser_gun"):
            try:
                aim_x, aim_y = game.aim_direction()
                aim_x = float(aim_x); aim_y = float(aim_y)
            except Exception:
                aim_x, aim_y = facing, 0.0
            aim_len = max(1e-6, math.hypot(aim_x, aim_y))
            aim_x /= aim_len; aim_y /= aim_len
            launch = max(0.0, math.sin(min(1.0, progress*1.35)*math.pi))
            for streak in range(6 if heavy else 4):
                distance = 9.0 + streak*5.0 + launch*10.0
                lateral = (streak-(2.5 if heavy else 1.5))*2.3
                emit(
                    hand_x+aim_x*distance-aim_y*lateral,
                    hand_y+aim_y*distance+aim_x*lateral,
                    (8.0+launch*8.0) if weapon=="laser_gun" else 4.0,
                    2.2 if weapon=="laser_gun" else 3.0,
                    .24+.58*launch,
                )
            if heavy:
                for spark in range(6):
                    angle = spark*math.pi/3.0 + float(now)*3.0
                    radius = 7.0 + 7.0*launch
                    emit(hand_x+aim_x*18.0+math.cos(angle)*radius,
                         hand_y+aim_y*18.0+math.sin(angle)*radius,3.0,3.0,.42+.48*launch)
            quads.extend(accents)
            return

        if weapon == "whip" and heavy:
            # Authoritative radial reach, independent from a 24px hand sprite.
            radius = centered_reach * (.68 + .32*math.sin(math.pi*progress))
            head_angle = progress*math.pi*2.0*1.18
            for point in range(24):
                angle = head_angle - (23-point)*.050
                local_radius = radius*(.80+.20*point/23.0)
                emit(float(render_player_x)+math.cos(angle)*local_radius,
                     float(render_player_y)-float(player.height())*.46+math.sin(angle)*local_radius,
                     3.0 if point<21 else 5.0,3.0 if point<21 else 5.0,
                     .18+.68*point/23.0)
            quads.extend(accents)
            return

        a0 = float((wd.get("heavy_start_angle") if heavy else wd.get("start_angle")) or -70.0)
        a1 = float((wd.get("heavy_end_angle") if heavy else wd.get("end_angle")) or 30.0)
        smooth_progress = progress*progress*(3.0-2.0*progress)
        current_angle = a0+(a1-a0)*smooth_progress

        if style in ("stab", "thrust", "power_stab", "lance_breaker", "whip_snap"):
            pulse = max(0.0, math.sin(math.pi*progress))
            visible_reach = reach*(.42+.58*(pulse**.55))
            dx,dy,pxv,pyv = basis(current_angle)
            lines = 3 if heavy else 2
            for line in range(lines):
                lateral = (line-(lines-1)*.5)*(5.0 if heavy else 3.5)
                for step in range(9):
                    t = (step+1)/9.0
                    dist = visible_reach*t
                    emit(hand_x+dx*dist+pxv*lateral,hand_y+dy*dist+pyv*lateral,
                         3.0+1.4*t,2.4+1.2*t,(.14+.68*t)*(.48+.52*pulse))
            # The leading pressure pixel always reaches the collision far edge
            # at peak extension, preventing an invisible spear/whip hit.
            emit(hand_x+dx*visible_reach,hand_y+dy*visible_reach,
                 7.0*visual_scale,7.0*visual_scale,.92)
        else:
            # Three fading arc ghosts provide force direction while the outer
            # samples land exactly on the authoritative slash radius.
            for ghost in range(3):
                ghost_progress = max(0.0, progress-ghost*.075)
                ghost_smooth = ghost_progress*ghost_progress*(3.0-2.0*ghost_progress)
                angle = a0+(a1-a0)*ghost_smooth
                dx,dy,_pxv,_pyv = basis(angle)
                for radial in range(5):
                    rr = reach*(.50+.125*radial)
                    emit(hand_x+dx*rr,hand_y+dy*rr,
                         (5.4-ghost*.8)*visual_scale,(5.4-ghost*.8)*visual_scale,
                         (.72-ghost*.19)*(.62+.38*math.sin(math.pi*progress)))
            if heavy:
                dx,dy,pxv,pyv = basis(current_angle)
                end_x=hand_x+dx*reach;end_y=hand_y+dy*reach
                for pressure in range(8):
                    angle=pressure*math.pi*.25
                    radius=7.0+4.0*math.sin(math.pi*progress)
                    emit(end_x+pxv*math.cos(angle)*radius+dx*math.sin(angle)*radius*.20,
                         end_y+pyv*math.cos(angle)*radius+dy*math.sin(angle)*radius*.20,
                         3.0,3.0,.32+.50*math.sin(math.pi*progress))
        quads.extend(accents)

    @staticmethod
    def _impact_value(packet, key, default=None):
        if isinstance(packet, dict):
            return packet.get(key, default)
        return getattr(packet, key, default)

    def _append_weapon_impact_feedback(self, quads, game, now):
        """Render at most eight short-lived impact packets in <=80 quads."""
        melee = getattr(game, "melee", None)
        rows = getattr(melee, "impact_feedback", None)
        if not isinstance(rows, (tuple, list)):
            rows = None
        if rows is None:
            for name in ("weapon_impact", "weapon_impact_snapshot", "last_weapon_impact"):
                candidate = getattr(game, name, None)
                if isinstance(candidate, (tuple, list)):
                    rows = candidate
                    break
                if candidate is not None:
                    rows = (candidate,)
                    break
        if not rows:
            return

        impact_quads = []
        budget = 80
        for packet in tuple(rows)[-8:]:
            try:
                ttl = max(0.0, float(self._impact_value(packet, "ttl", .16) or 0.0))
                duration = max(.05, float(self._impact_value(packet, "duration", .30) or .30))
                if ttl <= 0.0:
                    continue
                age = 1.0-max(0.0,min(1.0,ttl/duration))
                magnitude = max(.10,min(1.0,float(self._impact_value(packet,"magnitude",.5) or .5)))
                scale = max(.75,min(1.8,float(self._impact_value(packet,"vfx_scale",1.0) or 1.0)))
                heavy = bool(self._impact_value(packet,"heavy",False))
                weapon = str(self._impact_value(packet,"weapon","sword") or "sword")
                cx = float(self._impact_value(packet,"x",0.0) or 0.0)
                cy = float(self._impact_value(packet,"y",0.0) or 0.0)
                serial = int(self._impact_value(packet,"serial",0) or 0)
                dx = float(self._impact_value(packet,"camera_impulse_x",0.0) or 0.0)
                dy = float(self._impact_value(packet,"camera_impulse_y",0.0) or 0.0)
                direction_len = math.hypot(dx,dy)
                if direction_len <= 1e-6:
                    dx = 1.0 if int(getattr(getattr(game,"player",None),"facing",1) or 1)>=0 else -1.0
                    dy = -.16;direction_len=math.hypot(dx,dy)
                dx/=direction_len;dy/=direction_len
                colors = {
                    "sword":((.78,.94,1.0),(.98,1.0,1.0)),
                    "dagger":((.70,.91,1.0),(.96,1.0,1.0)),
                    "spear":((.36,.84,1.0),(.91,1.0,1.0)),
                    "battle_axe":((.48,.82,.36),(.91,1.0,.68)),
                    "war_hammer":((.90,.57,.20),(1.0,.91,.57)),
                    "greatsword":((.48,.68,1.0),(.88,.96,1.0)),
                    "energy_bow":((.14,.78,1.0),(.82,1.0,1.0)),
                    "whip":((.82,.47,.20),(1.0,.84,.48)),
                    "laser_gun":((.08,.76,1.0),(.86,1.0,1.0)),
                    "flying_drone":((1.0,.33,.08),(1.0,.88,.32)),
                    "tnt":((1.0,.16,.05),(1.0,.83,.22)),
                }.get(weapon,((.65,.86,1.0),(.96,1.0,1.0)))
                fade = max(.0,1.0-age)

                def impact_emit(px,py,w,h,color,alpha_value):
                    if len(impact_quads)>=budget:return
                    impact_quads.append(self._quad(px,py,w,h,(color[0],color[1],color[2],max(0.0,min(1.0,alpha_value)))))

                core_size=(8.0+13.0*magnitude)*(1.12 if heavy else 1.0)*scale
                impact_emit(cx,cy,core_size,core_size,colors[0],.28*fade)
                impact_emit(cx-dx*1.5,cy-dy*1.5,core_size*.42,core_size*.42,colors[1],.94*fade)
                spark_count=6 if heavy else 4
                base_angle=math.atan2(dy,dx)+serial*.71
                for spark in range(spark_count):
                    fan=(spark-(spark_count-1)*.5)*(.38 if heavy else .46)
                    angle=base_angle+fan+math.sin(serial*1.7+spark)*.13
                    distance=(5.0+age*(20.0+14.0*magnitude))*(.72+.12*spark)
                    spark_x=cx+math.cos(angle)*distance
                    spark_y=cy+math.sin(angle)*distance
                    impact_emit(spark_x,spark_y,(5.5+6.0*magnitude)*scale,2.2+1.4*magnitude,
                                colors[1] if spark%2 else colors[0],(.88-.09*spark)*fade)
                if heavy and len(impact_quads)<budget-1:
                    pressure=10.0+age*(19.0+12.0*magnitude)
                    pxv=-dy;pyv=dx
                    impact_emit(cx+pxv*pressure,cy+pyv*pressure,4.0,4.0,colors[0],.58*fade)
                    impact_emit(cx-pxv*pressure,cy-pyv*pressure,4.0,4.0,colors[0],.58*fade)
                if weapon=="tnt":
                    # TNT remains readable after the thrown entity is consumed.
                    # The short radial fire/smoke burst is bounded, while the
                    # terrain overlay shows the authoritative 1-cell or 3x3
                    # footprint returned by ToolSystem rather than estimating it.
                    ring_radius=(7.0+age*(30.0 if heavy else 22.0))*scale
                    for flame_i in range(10 if heavy else 7):
                        ar=flame_i*math.pi*2.0/float(10 if heavy else 7)+serial*.31
                        wobble=1.0+.16*math.sin(serial+flame_i*1.7)
                        impact_emit(
                            cx+math.cos(ar)*ring_radius*wobble,
                            cy+math.sin(ar)*ring_radius*wobble,
                            (6.4 if heavy else 5.2)*scale,
                            (6.4 if heavy else 5.2)*scale,
                            colors[1] if flame_i%3 else colors[0],
                            (.86-.045*flame_i)*fade,
                        )
                    cells=self._impact_value(packet,"pattern_cells",())
                    valid=[]
                    if isinstance(cells,(tuple,list)):
                        for cell in cells[:9]:
                            if isinstance(cell,(tuple,list)) and len(cell)>=2:
                                try:valid.append((int(cell[0]),int(cell[1])))
                                except Exception:pass
                    if valid:
                        min_tx=min(v[0] for v in valid);max_tx=max(v[0] for v in valid)
                        min_ty=min(v[1] for v in valid);max_ty=max(v[1] for v in valid)
                        left=min_tx*TILE_SIZE;right=(max_tx+1)*TILE_SIZE
                        top=min_ty*TILE_SIZE;bottom=(max_ty+1)*TILE_SIZE
                        grid_alpha=(.52 if heavy else .40)*fade
                        grid_col=colors[1]
                        thickness=2.0 if heavy else 1.6
                        impact_emit((left+right)*.5,top,(right-left),thickness,grid_col,grid_alpha)
                        impact_emit((left+right)*.5,bottom,(right-left),thickness,grid_col,grid_alpha)
                        impact_emit(left,(top+bottom)*.5,thickness,(bottom-top),grid_col,grid_alpha)
                        impact_emit(right,(top+bottom)*.5,thickness,(bottom-top),grid_col,grid_alpha)
                        if max_tx-min_tx>=2:
                            for tx_line in (min_tx+1,min_tx+2):
                                xx=tx_line*TILE_SIZE
                                impact_emit(xx,(top+bottom)*.5,1.2,(bottom-top),grid_col,.30*fade)
                        if max_ty-min_ty>=2:
                            for ty_line in (min_ty+1,min_ty+2):
                                yy=ty_line*TILE_SIZE
                                impact_emit((left+right)*.5,yy,(right-left),1.2,grid_col,.30*fade)
            except Exception:
                continue
        quads.extend(impact_quads[:budget])

    def _append_weapon_world_visuals(
        self, quads, game, player, render_player_x, render_player_y, now,
    ):
        """Render persistent weapon VFX independently from the selected mode.

        Gameplay owns every timer, projectile coordinate and reflection.  This
        renderer only resolves the AssetEditor binding and emits ordinary
        quads, so changing to tool/magic cannot make a live arrow or laser
        invisible while it is still able to deal damage.
        """
        melee=getattr(game,"melee",None)
        assets=getattr(game,"assets",None)
        if melee is None:return
        fallback_weapon=str(getattr(melee,"selected_weapon","sword") or "sword")
        fallback_facing=1 if int(getattr(player,"facing",1))>=0 else -1
        # Only the new secondary layer uses this counter. Core FIX54/authored
        # art keeps its existing bounded sources, while trails/halos can never
        # contribute more than 320 instances even with forty live projectiles.
        secondary_count=0
        secondary_budget=320

        def secondary(cx,cy,width,height,rgba):
            nonlocal secondary_count
            if secondary_count>=secondary_budget:return False
            quads.append(self._quad(cx,cy,width,height,rgba));secondary_count+=1
            return True

        if assets is not None:
            for fx in tuple(getattr(melee,"active_vfx",()) or ()):
                try:
                    fx_weapon=str(fx.get("weapon",fallback_weapon) or fallback_weapon)
                    asset_id="weapon."+fx_weapon
                    effect_state=str(fx.get("effect_state","normal_effect") or "normal_effect")
                    effect_time=max(0.0,float(fx.get("elapsed",0.0) or 0.0))
                    binding=fx.get("binding",{}) if isinstance(fx.get("binding",{}),dict) else {}
                    anchor=str(binding.get("anchor","weapon") or "weapon")
                    ox=float(binding.get("offset_x",0.0) or 0.0)
                    oy=float(binding.get("offset_y",0.0) or 0.0)
                    facing=1 if int(fx.get("facing",fallback_facing))>=0 else -1
                    if anchor=="ground":
                        cx=render_player_x+facing*(TILE_SIZE*.72+ox);cy=render_player_y-18.0+oy
                    elif anchor=="player":
                        cx=render_player_x+facing*ox;cy=render_player_y+oy
                    elif anchor=="hand":
                        cx=render_player_x+facing*(7.0+ox);cy=render_player_y-player.height()*.53+oy
                    else:
                        cx=render_player_x+facing*(35.0+ox);cy=render_player_y-player.height()*.53+3.0+oy
                    effect_quads=self._pixel_asset_quads(
                        game,asset_id,cx-40.0,cy-40.0,80.0,80.0,
                        flip_x=(facing<0),animation=effect_state,
                        animation_time=effect_time,
                        visual_scale=binding.get("scale",1.0),
                    )
                    if effect_quads is not None:quads.extend(effect_quads)
                    # The effect canvas is allowed to remain 24px: this sparse
                    # outer pressure halo expands in world space and therefore
                    # cannot be cropped by the authored source dimensions.
                    duration=max(.05,float(fx.get("duration",.25) or .25))
                    phase=max(0.0,min(1.0,effect_time/duration))
                    scale=max(.8,min(2.0,float(binding.get("scale",1.0) or 1.0)))
                    halo_color={
                        "sword":(.72,.92,1.0),"dagger":(.84,.96,1.0),
                        "spear":(.46,.88,1.0),"battle_axe":(.54,.86,.44),
                        "war_hammer":(1.0,.68,.24),"greatsword":(.58,.76,1.0),
                        "energy_bow":(.20,.86,1.0),"whip":(.94,.61,.25),
                        "laser_gun":(.18,.91,1.0),"flying_drone":(1.0,.35,.08),
                        "yoyo":(.42,.94,1.0),"battle_top":(1.0,.48,.16),
                        "rpg_launcher":(1.0,.30,.10),
                        "tnt":(1.0,.22,.06),
                    }.get(fx_weapon,(.72,.90,1.0))
                    radius=(14.0+phase*30.0)*scale
                    for halo_i in range(6):
                        angle=halo_i*math.pi/3.0+facing*.18
                        secondary(cx+math.cos(angle)*radius,cy+math.sin(angle)*radius,
                                  3.2*scale,3.2*scale,
                                  (halo_color[0],halo_color[1],halo_color[2],max(.08,(1.0-phase)*.58)))
                except Exception:
                    pass

        for shot in tuple(getattr(melee,"slash_projectiles",()) or ()):
            try:
                if float(shot.get("delay",0.0) or 0.0)>0.0:continue
                kind=str(shot.get("kind","greatsword_crescent") or "greatsword_crescent")
                sx=float(shot.get("x",0.0));sy=float(shot.get("y",0.0))
                sr=max(2.0,float(shot.get("radius",4.0) or 4.0))
                sf=1 if int(shot.get("facing",fallback_facing))>=0 else -1
                life=max(0.0,float(shot.get("life",0.0) or 0.0))
                max_life=max(.001,float(shot.get("max_life",life or 1.0) or 1.0))
                age=1.0-max(0.0,min(1.0,life/max_life))
                vx=float(shot.get("vx",sf));vy=float(shot.get("vy",0.0))
                speed=max(1e-6,math.hypot(vx,vy));ux=vx/speed;uy=vy/speed
                travel_angle=math.atan2(uy,ux)
                recorded_trail=list(shot.get("trail",()) or ())

                # Authoritative history is a world-space second layer. It is
                # retained even when an authored core replaces the procedural
                # projectile, so 24px art never makes a long flight invisible.
                if kind=="energy_arrow":
                    arrow_scale=max(.85,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                    for ti,pos in enumerate(recorded_trail[-6:]):
                        trail_alpha=.14+.52*(ti+1)/float(max(1,min(6,len(recorded_trail))))
                        secondary(float(pos[0]),float(pos[1]),
                                  (2.5+ti*.18)*arrow_scale,(2.5+ti*.18)*arrow_scale,
                                  (.15,.76,1.0,trail_alpha))
                elif kind=="greatsword_crescent":
                    crescent_scale=max(1.0,min(2.0,float(shot.get("visual_scale",1.0) or 1.0)))
                    crescent_radius=max(9.0,sr)*crescent_scale
                    geometry_scale=2.0 if str(shot.get("weapon", ""))=="master_sword" else 1.0
                    for ti,pos in enumerate(recorded_trail[-5:]):
                        trail_alpha=.10+.36*(ti+1)/float(max(1,min(5,len(recorded_trail))))
                        for normal in (-.68,0.0,.68):
                            secondary(float(pos[0])-uy*normal*crescent_radius,
                                      float(pos[1])+ux*normal*crescent_radius,
                                      3.5*crescent_scale*geometry_scale,3.5*crescent_scale*geometry_scale,
                                      (SWORD_EDGE_RGBA[0],SWORD_EDGE_RGBA[1],SWORD_EDGE_RGBA[2],trail_alpha))

                # A laser's history is the authoritative reflected path.  An
                # authored effect may replace its moving core, never the path.
                if kind=="laser":
                    visible_trail=recorded_trail[-14:]
                    for ti,pos in enumerate(visible_trail):
                        if ti%2 and ti<len(visible_trail)-2:continue
                        trail_alpha=.16+.62*(ti+1)/float(max(1,len(visible_trail)))
                        trail_size=4.6 if bool(shot.get("heavy",False)) else 3.4
                        secondary(float(pos[0]),float(pos[1]),trail_size,trail_size,(.12,.78,1.0,trail_alpha))
                elif kind=="yoyo":
                    # A lightweight dotted chain connects hand and authoritative
                    # yo-yo body. Segment count is hard-capped for Metal/Pyto.
                    hand_x=render_player_x
                    hand_y=render_player_y-float(player.height())*.52
                    rope_dx=sx-hand_x;rope_dy=sy-hand_y
                    rope_len=max(1.0,math.hypot(rope_dx,rope_dy))
                    rope_steps=max(2,min(18,int(rope_len/8.0)+1))
                    for ri in range(1,rope_steps):
                        rt=float(ri)/float(rope_steps)
                        secondary(hand_x+rope_dx*rt,hand_y+rope_dy*rt,2.2,2.2,(.72,.86,.92,.78))
                    for ti,pos in enumerate(recorded_trail[-6:]):
                        secondary(float(pos[0]),float(pos[1]),3.0,3.0,(.22,.80,1.0,.12+ti*.07))
                elif kind=="battle_top":
                    for ti,pos in enumerate(recorded_trail[-7:]):
                        secondary(float(pos[0]),float(pos[1]),4.0+ti*.25,2.6,(1.0,.42,.12,.10+ti*.07))
                elif kind=="rocket":
                    for ti,pos in enumerate(recorded_trail[-8:]):
                        fade=(ti+1)/float(max(1,min(8,len(recorded_trail))))
                        secondary(float(pos[0])-ux*3.0,float(pos[1])-uy*3.0,
                                  3.0+fade*2.2,3.0+fade*2.2,(1.0,.24+.45*fade,.06,.18+.56*fade))
                elif kind=="tnt":
                    # Sparse smoke follows the ballistic path; the live fuse is
                    # brighter near the authoritative projectile position.
                    for ti,pos in enumerate(recorded_trail[-8:]):
                        fade=(ti+1)/float(max(1,min(8,len(recorded_trail))))
                        secondary(float(pos[0])-ux*2.0,float(pos[1])-uy*2.0,
                                  3.2+fade*2.0,3.2+fade*2.0,
                                  (.30+.12*fade,.27+.10*fade,.24+.08*fade,.12+.36*fade))
                    fuse_phase=float(now)*15.0+int(shot.get("heavy",False))*1.3
                    secondary(sx+math.cos(fuse_phase)*5.5,sy-6.0+math.sin(fuse_phase)*2.2,
                              3.8,3.8,(1.0,.84,.18,.96))

                authored_drawn=False
                if bool(shot.get("authored_visual",False)) and assets is not None:
                    binding=shot.get("binding",{}) if isinstance(shot.get("binding",{}),dict) else {}
                    ox=float(binding.get("offset_x",0.0) or 0.0)
                    oy=float(binding.get("offset_y",0.0) or 0.0)
                    # Projectile offsets are local: X follows travel and Y is
                    # perpendicular.  This remains stable after a laser bounce.
                    core_x=sx+ux*ox-uy*oy;core_y=sy+uy*ox+ux*oy
                    effect_state=str(shot.get("effect_state","heavy_effect") or "heavy_effect")
                    effect_quads=self._pixel_asset_quads(
                        game,"weapon."+str(shot.get("weapon",fallback_weapon)),
                        core_x-40.0,core_y-40.0,80.0,80.0,
                        animation=effect_state,animation_time=max(0.0,max_life-life),
                        visual_scale=binding.get("scale",1.0),
                        rotation_radians=travel_angle,
                    )
                    if effect_quads is not None:
                        quads.extend(effect_quads);authored_drawn=True

                if kind=="energy_arrow":
                    # Bright arrowhead remains readable over snow/water even
                    # when the editable authored core is very dark or narrow.
                    arrow_scale=max(.85,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                    secondary(sx+ux*5.0,sy+uy*5.0,5.2*arrow_scale,5.2*arrow_scale,(.90,1.0,1.0,.94))
                    if not authored_drawn:
                        for ti in range(5):
                            trail_alpha=max(.16,.82-ti*.14)
                            quads.append(self._quad(sx-ux*ti*4.0,sy-uy*ti*4.0,4.4-ti*.38,4.4-ti*.38,(.18,.78,1.0,trail_alpha)))
                        quads.append(self._quad(sx+ux*4.0,sy+uy*4.0,5.0,5.0,(.90,1.0,1.0,.98)))
                elif kind=="laser":
                    if not authored_drawn:
                        # Build an aim-aligned short beam from bounded square
                        # pixels. This is visibly a line at every angle and never
                        # falls back to the old single glowing point.
                        beam_length=max(10.0,min(36.0,float(shot.get("beam_length",18.0) or 18.0)))
                        beam_steps=max(4,min(9,int(beam_length/3.0)+1))
                        thickness=4.8 if bool(shot.get("heavy",False)) else 3.6
                        for li in range(beam_steps):
                            lt=float(li)/float(max(1,beam_steps-1))
                            bx=sx-ux*beam_length*lt;by=sy-uy*beam_length*lt
                            secondary(bx,by,thickness+3.2,thickness+3.2,(.10,.70,1.0,.18+.14*(1.0-lt)))
                            secondary(bx,by,thickness,thickness,(.88,1.0,1.0,.98-.36*lt))
                    for bi in range(min(3,int(shot.get("bounces",0) or 0))):
                        ar=now*9.0+bi*2.1
                        quads.append(self._quad(sx+math.cos(ar)*(7+bi*3),sy+math.sin(ar)*(7+bi*3),2.8,2.8,(.42,.94,1.0,.72)))
                elif kind=="yoyo":
                    if not authored_drawn:
                        yscale=max(.8,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                        spin=float(shot.get("spin",age*math.pi*12.0) or 0.0)
                        secondary(sx,sy,sr*2.10*yscale,sr*2.10*yscale,(.05,.16,.28,.92))
                        secondary(sx,sy,sr*1.55*yscale,sr*1.55*yscale,(.18,.78,1.0,.98))
                        secondary(sx+math.cos(spin)*sr*.45,sy+math.sin(spin)*sr*.45,
                                  sr*.48,sr*.48,(.90,1.0,1.0,1.0))
                elif kind=="battle_top":
                    if not authored_drawn:
                        tscale=max(.8,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                        spin=float(shot.get("spin",age*math.pi*16.0) or 0.0)
                        secondary(sx,sy,sr*2.20*tscale,sr*.90*tscale,(.16,.12,.22,.96))
                        secondary(sx+math.cos(spin)*sr*.32,sy+math.sin(spin)*sr*.16,
                                  sr*1.45*tscale,sr*.62*tscale,(1.0,.46,.12,.98))
                        secondary(sx,sy+sr*.55*tscale,3.0*tscale,5.0*tscale,(.92,.94,1.0,.95))
                elif kind=="rocket":
                    if not authored_drawn:
                        rscale=max(.8,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                        for ri in range(6):
                            rt=float(ri)/5.0
                            rx=sx-ux*(rt*18.0-4.0);ry=sy-uy*(rt*18.0-4.0)
                            color=(.90,.94,1.0,.98) if ri<3 else (.28,.34,.40,.98)
                            secondary(rx,ry,5.4*rscale,5.4*rscale,color)
                        secondary(sx+ux*6.0,sy+uy*6.0,4.2*rscale,4.2*rscale,(1.0,.28,.08,1.0))
                elif kind=="tnt":
                    if not authored_drawn:
                        tscale=max(.8,min(1.8,float(shot.get("visual_scale",1.0) or 1.0)))
                        # Three-stick red bundle with a dark retaining band and
                        # white fuse spark; axis-aligned squares preserve pixel art.
                        secondary(sx-5.0*tscale,sy,6.2*tscale,15.0*tscale,(.72,.07,.04,.98))
                        secondary(sx,sy,6.2*tscale,16.5*tscale,(.91,.10,.05,.99))
                        secondary(sx+5.0*tscale,sy,6.2*tscale,15.0*tscale,(.72,.07,.04,.98))
                        secondary(sx,sy,17.0*tscale,3.8*tscale,(.12,.10,.10,.99))
                        secondary(sx+3.0*tscale,sy-10.0*tscale,3.2*tscale,6.2*tscale,(.66,.48,.22,.96))
                        secondary(sx+5.5*tscale,sy-13.0*tscale,4.2*tscale,4.2*tscale,(1.0,.86,.25,1.0))
                elif not authored_drawn:
                    crescent_scale=max(1.0,float(shot.get("visual_scale",1.0) or 1.0))
                    visual_sr=max(9.0,sr)*crescent_scale
                    # FIX88: collision radius already doubled the height. Also
                    # double arc curvature, pixel thickness and trail spacing;
                    # otherwise the new moon would only be stretched vertically.
                    geometry_scale=2.0 if str(shot.get("weapon", ""))=="master_sword" else 1.0
                    for trail_index in range(3):
                        tx=sx-sf*trail_index*(7.0+age*4.0)*crescent_scale*geometry_scale
                        trail_alpha=max(.18,.98-trail_index*.28)
                        col=(SWORD_EDGE_RGBA[0],SWORD_EDGE_RGBA[1],SWORD_EDGE_RGBA[2],trail_alpha)
                        for oy in (-.92,-.64,-.30,.08,.44,.76,.98):
                            curve=(1.0-abs(oy))*7.0*crescent_scale*geometry_scale
                            size=(5.8 if trail_index==0 else 4.2)*crescent_scale*geometry_scale
                            quads.append(self._quad(tx+sf*curve,sy+oy*visual_sr,size,size,col))
            except Exception:
                continue

    def render(self, game):
        render_t0 = time.perf_counter()
        terrain_build_ms = 0.0
        rain_build_ms = 0.0

        # FIX4: registry changes invalidate render caches. This also makes a
        # saved AssetEditor change visible without relying on stale Pyto module state.
        _assets = getattr(game, "assets", None)
        _asset_now = time.monotonic()
        if _assets is not None and _asset_now - self._asset_registry_check_time >= 0.50:
            self._asset_registry_check_time = _asset_now
            try:
                _assets.refresh_if_changed()
            except Exception:
                pass
            _sig = getattr(_assets, "signature", None)
            if _sig != self._asset_registry_signature:
                self._asset_registry_signature = _sig
                self._custom_terrain_cpu_key = None
                self._custom_terrain_chunk_cache.clear()
                self._chunk_tile_cache.clear()
                self._env_cache_time = 0.0

        try:
            _has_custom_tiles = bool(_assets and _assets.category_bindings("tile"))
        except Exception:
            _has_custom_tiles = False
        use_streamed_scene = bool(getattr(game, "custom_map_loaded", False)) or _has_custom_tiles

        alpha = max(
            0.0,
            min(
                1.0,
                float(
                    getattr(
                        game,
                        "render_alpha",
                        0.0,
                    )
                ),
            ),
        )

        render_camera_x = (
            float(getattr(game, "prev_camera_x", game.camera_x))
            + (float(game.camera_x) - float(getattr(game, "prev_camera_x", game.camera_x)))
            * alpha
        )
        render_camera_y = (
            float(getattr(game, "prev_camera_y", game.camera_y))
            + (float(game.camera_y) - float(getattr(game, "prev_camera_y", game.camera_y)))
            * alpha
        )

        # FIX67 impact camera is presentation-only. MeleeSystem owns the short
        # decaying impulse; the renderer samples it without sleeping, changing
        # simulation dt or writing camera/game state.  The radial clamp keeps
        # even diagonal heavy hits within a strict six-pixel screen offset.
        now = time.monotonic()
        _impact_melee = getattr(game, "melee", None)
        try:
            _kick_x = float(getattr(_impact_melee, "camera_impulse_x", 0.0) or 0.0)
            _kick_y = float(getattr(_impact_melee, "camera_impulse_y", 0.0) or 0.0)
        except Exception:
            _kick_x = _kick_y = 0.0
        _impact_serial = int(getattr(_impact_melee, "impact_serial", 0) or 0)
        if abs(_kick_x) + abs(_kick_y) <= 0.01:
            _camera_packet = None
            _camera_rows = getattr(_impact_melee, "impact_feedback", None)
            if isinstance(_camera_rows, (tuple, list)) and _camera_rows:
                _camera_packet = _camera_rows[-1]
            else:
                for _camera_name in ("weapon_impact", "weapon_impact_snapshot", "last_weapon_impact"):
                    _candidate = getattr(game, _camera_name, None)
                    if isinstance(_candidate, (tuple, list)) and _candidate:
                        _camera_packet = _candidate[-1];break
                    if _candidate is not None:
                        _camera_packet = _candidate;break
            if _camera_packet is not None:
                try:
                    _kick_x = float(self._impact_value(_camera_packet,"camera_impulse_x",0.0) or 0.0)
                    _kick_y = float(self._impact_value(_camera_packet,"camera_impulse_y",0.0) or 0.0)
                    _impact_serial = int(self._impact_value(_camera_packet,"serial",_impact_serial) or _impact_serial)
                except Exception:
                    _kick_x = _kick_y = 0.0
        if abs(_kick_x) + abs(_kick_y) > 0.01:
            _shake_phase = now * 76.0 + _impact_serial * 2.173
            _primary = .72 + .28 * math.sin(_shake_phase)
            _cross = .18 * math.cos(_shake_phase * 1.31)
            _shake_x = _kick_x * _primary + _kick_y * _cross
            _shake_y = _kick_y * _primary - _kick_x * _cross * .72
            _shake_len = math.hypot(_shake_x, _shake_y)
            if _shake_len > 6.0:
                _shake_x *= 6.0 / _shake_len
                _shake_y *= 6.0 / _shake_len
            render_camera_x += _shake_x
            render_camera_y += _shake_y

        # V0.6.0: terrain is no longer merged into a Python tuple every frame.
        # Each visible chunk points at an immutable GPU-resident MTLBuffer.
        if use_streamed_scene:
            terrain_t0 = time.perf_counter()
            custom_terrain_key, custom_terrain_quads = self._visible_custom_map_terrain_payload(
                game, camera_x=render_camera_x, camera_y=render_camera_y)
            terrain_build_ms = (time.perf_counter() - terrain_t0) * 1000.0
            terrain_batches = tuple()
            # No Metal allocation here: drawInMTKView uploads this cached tuple
            # only when custom_terrain_key changes.
        else:
            custom_terrain_key, custom_terrain_quads = None, tuple()
            terrain_batches = self._visible_terrain_gpu_batches(game)

        background_quads = self._fix83_background(game, render_camera_x, render_camera_y)
        from systems.cloud_islands import cloud_bank_quads
        meta=(getattr(getattr(game,'map_loader',None),'payload',None) or {}).get('metadata',{})
        # Reserve back-wall coverage first; clouds may use only the spare budget.
        from systems.fauna99_combat import background_fauna_quads
        fauna=background_fauna_quads(self,game,render_camera_x,render_camera_y,
                budget=max(0,METAL_BACKGROUND_MAX_QUADS-len(background_quads)))
        if fauna: background_quads=tuple(background_quads)+tuple(fauna)
        budget=max(0,METAL_BACKGROUND_MAX_QUADS-len(background_quads))
        clouds=cloud_bank_quads(meta,render_camera_x,render_camera_y,game.viewport_w,game.viewport_h,budget=budget)
        if clouds: background_quads=tuple(background_quads)+tuple(self._quad(*r) for r in clouds)

        if (
            self._water_cache_time <= 0.0
            or now - self._water_cache_time >= self._water_render_interval
        ):
            self._water_cache = tuple(self._build_water_quads(game))
            self._water_cache_generation += 1
            if use_streamed_scene:
                # Custom map: MTKView draw callback owns the slow-layer ring.
                self._water_gpu_batch = None
            else:
                self._water_gpu_batch = _new_immutable_instance_buffer(
                    self.state, self._water_cache, max_count=METAL_MAX_ENV_QUADS
                )
                self._water_gpu_uploads += 1
            self._water_cache_time = now

        if (
            self._env_cache_time <= 0.0
            or now - self._env_cache_time >= self._env_render_interval
        ):
            self._env_cache = tuple(self._build_environment_quads(game, include_water=False))
            self._env_cache_generation += 1
            if use_streamed_scene:
                self._env_gpu_batch = None
            else:
                self._env_gpu_batch = _new_immutable_instance_buffer(
                    self.state, self._env_cache, max_count=METAL_MAX_ENV_QUADS
                )
                self._env_gpu_uploads += 1
            self._env_cache_time = now

        water_batch = self._water_gpu_batch
        environment_batch = self._env_gpu_batch

        # Only actors, projectiles and the reticle remain in the streamed 60 Hz
        # layer. In normal play this is tens of instances instead of thousands.
        rain_t0 = time.perf_counter()
        quads = list(self._build_rain_quads_fast(game))
        # FIX39 bubbles + swaying seagrass are bounded render-only VFX.
        quads.extend(self._build_underwater_vfx_quads(game, now))
        rain_build_ms = (time.perf_counter() - rain_t0) * 1000.0

        # Dynamic rigid objects. Rendering does not change their state.
        for obj in game.physics_objects:
            if obj.active:
                quads.append(self._quad(obj.x, obj.y-obj.height()*0.5, obj.width(), obj.height(), BOX_RGBA))

        npc = game.npc
        if npc.active:
            quads.append(self._quad(npc.x, npc.y-npc.height()*0.5, npc.width(), npc.height(), NPC_RGBA))

        # Every world drop is a scene Item.  This includes mined materials,
        # chopped vegetation, creature loot and inventory-dropped stacks.
        for entity in tuple(game.scene.entities):
            if isinstance(entity, Item) and entity.active and not entity.picked:
                _drop_weapon = weapon_from_item(getattr(entity, "item_id", ""))
                if _drop_weapon:
                    if not self._is_visible_tile(int(entity.x//TILE_SIZE), int(entity.y//TILE_SIZE), game, margin=1):
                        continue
                    if append_boss_icon(self, quads, game, _drop_weapon,
                                        entity.x, entity.y-17.0, span=30.0):
                        continue
                _iid=str(getattr(entity,"item_id",""))
                _edited_item=self._pixel_asset_quads(game,"item."+_iid,entity.x-20,entity.y-35,40,40)
                if _edited_item is not None:
                    quads.extend(_edited_item);continue
                size = 15.0 + min(5.0, max(0.0, float(entity.count - 1)) * 0.6)
                quads.append(self._quad(
                    entity.x, entity.y - size * 0.5, size, size,
                    _item_color(getattr(entity, "item_id", "item")),
                ))

        # FIX39 treasure chests are persistent scene entities.  Opened chests
        # remain visible but no longer block interaction with nearby drops/NPCs.
        for chest in tuple(getattr(game, "chests", ()) or ()):
            if not getattr(chest, "active", True):
                continue
            if not self._is_visible_tile(int(chest.x // TILE_SIZE), int((chest.y - 2.0) // TILE_SIZE), game, margin=1):
                continue
            cx = float(chest.x)
            floor_y = float(chest.y)
            _chest_aid="item.chest_open" if bool(getattr(chest,"opened",False)) else "item.chest_closed"
            _edited_chest=self._pixel_asset_quads(game,_chest_aid,cx-20,floor_y-35,40,40)
            if _edited_chest is not None:
                quads.extend(_edited_chest);continue
            body_color = CHEST_OPEN_RGBA if bool(getattr(chest, "opened", False)) else CHEST_WOOD_RGBA
            quads.append(self._quad(cx, floor_y - 8.0, 30.0, 16.0, body_color))
            quads.append(self._quad(cx, floor_y - 15.5, 31.5, 4.0, CHEST_DARK_RGBA))
            if bool(getattr(chest, "opened", False)):
                quads.append(self._quad(cx - 5.0, floor_y - 25.0, 25.0, 4.5, CHEST_WOOD_RGBA))
            else:
                quads.append(self._quad(cx, floor_y - 19.0, 30.0, 8.0, CHEST_WOOD_RGBA))
                quads.append(self._quad(cx, floor_y - 9.0, 5.0, 8.0, CHEST_GOLD_RGBA))

        # FIX39 ballistic lava spits.  These are bounded gameplay projectiles,
        # unlike the underwater ambience above, so their position comes from the
        # authoritative LavaSpitSystem.
        for spit in tuple(getattr(getattr(game, "lava_spit", None), "spits", ()) or ()):
            if not bool(getattr(spit, "active", True)):
                continue
            sx = float(getattr(spit, "x", 0.0))
            sy = float(getattr(spit, "y", 0.0))
            quads.append(self._quad(sx, sy, 8.0, 8.0, LAVA_SPIT_RGBA))
            quads.append(self._quad(sx, sy - 1.0, 3.5, 3.5, LAVA_SPIT_CORE_RGBA))
            vy = float(getattr(spit, "vy", 0.0))
            if vy < -35.0:
                quads.append(self._quad(sx, sy + 6.0, 4.0, 7.0, (1.0, 0.28, 0.02, 0.45)))


        # FIX44 pyramid mechanisms live in a non-diggable metadata/background
        # layer.  Their small authoritative state is rendered in the existing
        # dynamic batch; no extra Metal draw call or Scene entity is created.
        _trap_system = getattr(game, "pyramid_traps", None)
        if _trap_system is not None:
            _px = float(getattr(game.player, "x", 0.0))
            _py = float(getattr(game.player, "y", 0.0))
            _rx = max(float(getattr(game, "viewport_w", 852.0)) * .90, TILE_SIZE * 12.0)
            _ry = max(float(getattr(game, "viewport_h", 393.0)) * 1.05, TILE_SIZE * 8.0)
            for _trap in tuple(getattr(_trap_system, "traps", ()) or ()):
                _kind = str(getattr(_trap, "kind", ""))
                _skin = str(getattr(_trap, "skin", "stone") or "stone")
                _tx = float(getattr(_trap, "tx", 0.0)); _ty = float(getattr(_trap, "ty", 0.0))
                _wx = (_tx + .5) * TILE_SIZE
                _wy = (float(getattr(_trap, "floor", 0)) if _kind == "hidden_spike" else _ty + .5) * TILE_SIZE
                if abs(_wx - _px) > _rx or abs(_wy - _py) > _ry:
                    continue
                if _kind == "hidden_spike":
                    _floor_y = float(getattr(_trap, "floor", 0)) * TILE_SIZE
                    _warning = str(getattr(_trap, "state", "")) == "warning"
                    _pulse = .58 + .42 * math.sin(now * 18.0 + _tx)
                    _skin_plate={
                        "glow_moss":(.18,.58,.37,.88),"crystal":(.25,.62,.86,.88),
                        "castle":(.38,.27,.31,.90),"boss":(.58,.18,.43,.90),
                        "magma":(.68,.19,.08,.92),
                        "wuxia":(.22,.43,.27,.92),"bamboo":(.22,.49,.25,.92),
                        "cave":(.50,.45,.36,.92),"ink":(.13,.26,.27,.94),
                        "grave":(.35,.30,.40,.94),"biolume":(.18,.66,.62,.92),
                    }.get(_skin,(.36,.27,.17,.78))
                    _plate_col = (.95, .34, .16, .94) if _warning else _skin_plate
                    quads.append(self._quad(_wx, _floor_y - 1.8, TILE_SIZE * .78, 3.6, _plate_col))
                    _ext = max(0.0, min(1.0, float(getattr(_trap, "extension", 0.0))))
                    if _warning:
                        quads.append(self._quad(_wx, _floor_y - 4.0, TILE_SIZE * .54, 2.0, (1.0, .48, .12, .34 + .42 * _pulse)))
                    if _ext > .01:
                        _h = TILE_SIZE * .84 * _ext
                        for _ox in (-TILE_SIZE * .25, 0.0, TILE_SIZE * .25):
                            _spike_col={"glow_moss":(.24,.70,.42,1.0),"crystal":(.42,.82,1.0,1.0),"castle":(.50,.51,.56,1.0),"boss":(.64,.24,.52,1.0),"magma":(.30,.23,.24,1.0),"wuxia":(.71,.78,.59,1.0),"bamboo":(.62,.85,.48,1.0),"cave":(.78,.71,.56,1.0),"ink":(.47,.82,.76,1.0),"grave":(.66,.57,.74,1.0),"biolume":(.43,.98,.86,1.0)}.get(_skin,(.69,.72,.67,1.0))
                            quads.append(self._quad(_wx + _ox, _floor_y - _h * .44, 4.5, _h * .78, _spike_col))
                            quads.append(self._quad(_wx + _ox, _floor_y - _h * .88, 2.2, _h * .24, (.94,.96,1.0,1.0) if _skin=="crystal" else (.94,.90,.72,1.0)))
                else:
                    _dx = float(getattr(_trap, "dx", 1.0))
                    _face_x = _wx - _dx * TILE_SIZE * .47
                    _base={"glow_moss":(.16,.42,.29,1.0),"crystal":(.20,.42,.66,1.0),"castle":(.28,.27,.31,1.0),"boss":(.39,.14,.34,1.0),"magma":(.40,.16,.11,1.0),"wuxia":(.18,.31,.22,1.0),"bamboo":(.14,.34,.19,1.0),"cave":(.35,.31,.27,1.0),"ink":(.10,.22,.23,1.0),"grave":(.25,.20,.29,1.0),"biolume":(.09,.35,.39,1.0)}.get(_skin,(.30,.22,.16,1.0) if _kind=="wall_arrow" else (.42,.18,.10,1.0))
                    quads.append(self._quad(_face_x, _wy, 8.0, 24.0, _base))
                    quads.append(self._quad(_face_x + _dx * 2.0, _wy, 3.0, 11.0, (.06, .05, .05, 1.0)))
                    if _kind == "bouncing_fireball":
                        quads.append(self._quad(_face_x - _dx * 3.0, _wy, 3.5, 18.0, (.85, .45, .13, .92)))

            for _shot in tuple(getattr(_trap_system, "projectiles", ()) or ()):
                if not bool(getattr(_shot, "active", False)):
                    continue
                _sx = float(getattr(_shot, "x", 0.0)); _sy = float(getattr(_shot, "y", 0.0))
                _shot_skin=str(getattr(_shot,"skin","stone") or "stone")
                if str(getattr(_shot, "kind", "")) == "arrow":
                    _dir = 1.0 if float(getattr(_shot, "vx", 0.0)) >= 0.0 else -1.0
                    _shaft={"crystal":(.30,.72,1.0,1.0),"castle":(.54,.50,.46,1.0),"boss":(.78,.28,.64,1.0),"magma":(.78,.29,.12,1.0),"glow_moss":(.29,.74,.48,1.0),"wuxia":(.64,.78,.49,1.0),"bamboo":(.58,.79,.40,1.0),"cave":(.73,.66,.51,1.0),"ink":(.40,.73,.69,1.0),"grave":(.63,.51,.68,1.0),"biolume":(.29,.94,.84,1.0)}.get(_shot_skin,(.70,.56,.34,1.0))
                    quads.append(self._quad(_sx, _sy, 21.0, 3.2, _shaft))
                    quads.append(self._quad(_sx + _dir * 11.0, _sy, 5.0, 6.0, (.76,.96,1.0,1.0) if _shot_skin=="crystal" else (.84,.88,.83,1.0)))
                    quads.append(self._quad(_sx - _dir * 11.0, _sy, 4.0, 9.0, (.48, .26, .16, .92)))
                else:
                    _pulse = .82 + .18 * math.sin(now * 12.0 + _sx * .03)
                    _orb={"glow_moss":(.22,.84,.51,.96),"crystal":(.30,.72,1.0,.96),"castle":(.56,.25,.68,.96),"boss":(.86,.18,.62,.96),"magma":(1.0,.25,.03,.96),"wuxia":(.35,.70,.31,.96),"bamboo":(.35,.73,.25,.96),"cave":(.67,.56,.38,.96),"ink":(.20,.58,.58,.96),"grave":(.52,.37,.61,.96),"biolume":(.31,.82,.86,.96)}.get(_shot_skin,(1.0,.25,.03,.96))
                    _core={"glow_moss":(.72,1.0,.70,_pulse),"crystal":(.84,.98,1.0,_pulse),"castle":(.90,.65,1.0,_pulse),"boss":(1.0,.62,.88,_pulse),"magma":(1.0,.86,.18,_pulse),"wuxia":(.91,1.0,.66,_pulse),"bamboo":(.88,1.0,.57,_pulse),"cave":(.96,.86,.64,_pulse),"ink":(.69,1.0,.93,_pulse),"grave":(.88,.70,1.0,_pulse),"biolume":(.78,1.0,.95,_pulse)}.get(_shot_skin,(1.0,.86,.18,_pulse))
                    quads.append(self._quad(_sx, _sy, 15.0, 15.0, _orb))
                    quads.append(self._quad(_sx - 1.0, _sy - 1.0, 7.0, 7.0, _core))
                    _vx = float(getattr(_shot, "vx", 0.0)); _vy = float(getattr(_shot, "vy", 0.0))
                    _mag = max(1.0, math.hypot(_vx, _vy))
                    quads.append(self._quad(_sx - _vx / _mag * 8.0, _sy - _vy / _mag * 8.0, 6.0, 6.0, (1.0, .18, .02, .42)))
        # Targetable monster/animal actors.  A short hurt flash makes sword
        # contact readable even before sprite assets are imported.
        #
        # FIX48: ChunkStreamer intentionally keeps a generous simulation ring,
        # but drawing every detailed pixel creature in those off-screen chunks
        # could consume all 768 dynamic Metal instances in the pyramid.  The
        # player is appended after creatures, so that overflow made the player,
        # weapon and magic visuals disappear while gameplay still worked.
        # Cull by the actual viewport and reserve a tail budget for the player
        # and combat VFX.  If a single very detailed creature would cross that
        # budget, keep a one-quad silhouette rather than sacrificing the player.
        # FIX68 adds a bounded 192-quad creature-attack layer ahead of the
        # existing player/weapon tail.  Reserve both portions up front so a
        # crowded screen can only simplify creature sprites; it can never
        # truncate the player or authoritative combat feedback.
        _actor_reserve = 680 + 192 + boss_visual_reserve(game)
        _creature_budget = max(64, int(METAL_DYNAMIC_MAX_QUADS) - _actor_reserve)
        _has_creature_vfx = callable(getattr(
            getattr(game, "creature_system", None),
            "creature_vfx_snapshot", None,
        ))
        for c in getattr(game, "creatures", ()):
            if not c.active or getattr(c,"background_only",False):
                continue
            try:
                ccx = int(c.x // (TILE_SIZE * CHUNK_SIZE))
                ccy = int(c.y // (TILE_SIZE * CHUNK_SIZE))
                if not game.chunk_streamer.is_active(ccx, ccy):
                    continue
            except Exception:
                pass
            try:
                _cmargin = max(96.0, float(c.width()) * 2.0, float(c.height()) * 2.0)
                if (
                    float(c.x) < float(render_camera_x) - _cmargin
                    or float(c.x) > float(render_camera_x) + float(game.viewport_w) + _cmargin
                    or float(c.y) < float(render_camera_y) - _cmargin
                    or float(c.y) > float(render_camera_y) + float(game.viewport_h) + _cmargin
                ):
                    continue
            except Exception:
                pass
            if float(getattr(c, "hurt_timer", 0.0)) > 0.0:
                ccolor = CREATURE_HURT_RGBA
            elif float(getattr(c, "attack_anim_timer", 0.0)) > 0.0:
                ccolor = CREATURE_ATTACK_RGBA
            else:
                ccolor = CREATURE_SPECIES_RGBA.get(
                    str(getattr(c, "species", "")), SLIME_RGBA
                )
            _before_creature = len(quads)
            self._append_creature_sprite(quads, c, ccolor, max(4,min(1800,_creature_budget-len(quads)-3)))
            if len(quads) > _creature_budget:
                del quads[_before_creature:]
                quads.append(self._quad(
                    float(c.x), float(c.y) - float(c.height()) * 0.5,
                    float(c.width()), float(c.height()), ccolor,
                ))
            hp_ratio = max(0.0, min(1.0, float(c.hp) / max(1.0, float(c.max_hp))))
            if hp_ratio < 0.999 and len(quads) < _creature_budget:
                quads.append(self._quad(
                    c.x - c.width()*0.5 + c.width()*hp_ratio*0.5,
                    c.y - c.height() - 5.0,
                    c.width()*hp_ratio, 3.0,
                    (0.85, 0.20, 0.18, 0.95),
                ))
            # FIX68's catalog owns authored-action presentation.  Keep this
            # FIX65 fallback only for older runtimes that do not publish the
            # bounded packet snapshot, otherwise the same attack is drawn
            # twice.
            _aid=str(getattr(c,"ai_action_id","") or "")
            if (not _has_creature_vfx) and _aid and len(quads)<_creature_budget-18:
                _sp=str(getattr(c,"species","") or "");_t=float(getattr(c,"ai_action_elapsed",0.0));_phase=_t*12.0
                _cx=float(c.x);_cy=float(c.y)-float(c.height())*.52
                if _sp in ("flying_imp","infernal_goat","burning_slime","flame_turtle","exploding_wisp"):
                    for _vi in range(5):
                        _ar=_phase+_vi*1.26;_rr=7.0+_vi*2.0
                        quads.append(self._quad(_cx+math.cos(_ar)*_rr,_cy+math.sin(_ar)*_rr,3.4,3.4,(1.0,.24+.10*(_vi%2),.04,.42+.09*_vi)))
                    if _sp=="exploding_wisp":
                        for _vi in range(10):
                            _ar=_vi*math.pi*.2;_rr=18.0+5.0*math.sin(_phase)
                            quads.append(self._quad(_cx+math.cos(_ar)*_rr,_cy+math.sin(_ar)*_rr,3.0,3.0,(1.0,.65,.12,.78)))
                elif _sp.startswith("crystal_"):
                    _face=1.0 if int(getattr(c,"facing",1))>=0 else -1.0
                    for _vi in range(7):
                        _ar=-1.2+_vi*.38
                        _rr=12.0+_vi*3.0
                        quads.append(self._quad(_cx+_face*math.cos(_ar)*_rr,_cy+math.sin(_ar)*_rr,3.6,3.6,(.38,.86,1.0,.42+.07*_vi)))

        # FIX112 authored effect tracks are world-space and independent from
        # the body canvas. They are drawn before the legacy/procedural packet
        # layer so keyframe-bound art is never clipped to the boss sprite box.
        self._append_authored_creature_vfx(quads, game, 1100)
        if _has_creature_vfx:
            self._append_creature_attack_feedback(quads, game)
        else:
            # Compatibility residue for pre-FIX68 creature systems only.
            for _fx in tuple(getattr(getattr(game,"creature_system",None),"explosion_vfx",()) or ()):
                _life=max(0.0,float(_fx.get("life",0.0)));_dur=max(.01,float(_fx.get("duration",.38)));_q=1.0-_life/_dur
                _cx=float(_fx.get("x",0.0));_cy=float(_fx.get("y",0.0))
                for _vi in range(16):
                    _ar=_vi*math.pi*2.0/16.0;_rr=8.0+_q*58.0
                    quads.append(self._quad(_cx+math.cos(_ar)*_rr,_cy+math.sin(_ar)*_rr,5.5,5.5,(1.0,.20+.35*(_vi%3)/2.0,.04,max(.05,_life/_dur*.85))))

        p = game.player
        render_player_x = (
            float(getattr(p, "prev_x", p.x))
            + (float(p.x) - float(getattr(p, "prev_x", p.x)))
            * alpha
        )
        render_player_y = (
            float(getattr(p, "prev_y", p.y))
            + (float(p.y) - float(getattr(p, "prev_y", p.y)))
            * alpha
        )

        pcolor = PLAYER_RGBA
        if float(getattr(p, 'hurt_timer', 0.0)) > 0.0:
            pcolor = PLAYER_HURT_RGBA
        elif p.state == 'climb':
            pcolor = PLAYER_CLIMB_RGBA
        elif getattr(p, 'attack_timer', 0.0) > 0:
            pcolor = PLAYER_ATTACK_RGBA
        _player_anim_id = str(getattr(getattr(game, "player_animation", None), "animation_id", "player_idle") or "player_idle")
        _player_state = _player_anim_id[7:] if _player_anim_id.startswith("player_") else _player_anim_id
        _player_anim_time = float(getattr(getattr(game, "player_animation", None), "time", 0.0) or 0.0)
        # FIX17: the gameplay animation clock now owns authored link/loop
        # keyframe ranges. Render the exact resolved frame instead of letting
        # AssetRegistry independently restart/loop from elapsed time.
        _player_anim_frame = int(getattr(getattr(game, "player_animation", None), "frame_index", 0) or 0)
        _equipment_slots = self._equipped_visual_ids(game)
        # FIX15: authored player animations share one standing-size source
        # coordinate system and one foot anchor.  Crouch/prone collision boxes
        # remain physically smaller, but the renderer must NOT shrink the whole
        # sprite to that hitbox; the artist draws the lowered posture inside the
        # same NxN canvas. This is what makes crouch/crawl look like a motion
        # rather than a vertically squashed standing character.
        _player_visual_w = float(p.width("stand"))
        _player_visual_h = float(p.height("stand"))
        self._append_equipment_visuals(
            quads, game, p, render_player_x, render_player_y,
            _player_state, _player_anim_frame, now, phase="behind",
            equipment_slots=_equipment_slots,
        )
        _player_custom = self._pixel_asset_quads(
            game, "player.default",
            render_player_x - _player_visual_w*0.5,
            render_player_y - _player_visual_h,
            _player_visual_w, _player_visual_h,
            flip_x=(int(getattr(p, "facing", 1)) < 0),
            animation=_player_state, animation_time=_player_anim_time,
            frame_index=_player_anim_frame,
        )
        if _player_custom is not None:
            quads.extend(_player_custom)
            if float(getattr(p, 'hurt_timer', 0.0)) > 0.0:
                quads.append(self._quad(render_player_x, render_player_y-p.height()*0.5, p.width(), p.height(), (1.0,0.25,0.20,0.20)))
        else:
            quads.append(
                self._quad(
                    render_player_x,
                    render_player_y - p.height()*0.5,
                    p.width(),
                    p.height(),
                    pcolor,
                )
            )
        self._append_equipment_visuals(
            quads, game, p, render_player_x, render_player_y,
            _player_state, _player_anim_frame, now, phase="front",
            equipment_slots=_equipment_slots,
        )

        # FIX37 foreground magma lip. The full body remains in the environment
        # batch, but the top free surface is redrawn after actors. This prevents
        # the old visual where the player's entire body/feet appeared in front of
        # a lava pool while physics said the lower body was immersed.
        try:
            _lava_table=game.environment.state.lava
            _cool_table=game.environment.state.lava_cooling
            _lava_system=game.environment.lava
            for (ltx,lty),lamount in self._iter_visible_table_items(_lava_table,game):
                lamount=max(0.0,min(1.0,float(lamount)))
                if lamount<0.015:
                    continue
                # Draw only the externally visible top of a vertical lava stack.
                if float(_lava_table.get((int(ltx),int(lty)-1),0.0))>0.015:
                    continue
                lcool=max(0.0,min(1.0,float(_cool_table.get((int(ltx),int(lty)),0.0))))
                hot=(1.0,0.42,0.045);cold=(0.28,0.24,0.20)
                lr=hot[0]*(1.0-lcool)+cold[0]*lcool
                lg=hot[1]*(1.0-lcool)+cold[1]*lcool
                lb=hot[2]*(1.0-lcool)+cold[2]*lcool
                sy=float(_lava_system.surface_y(ltx,lty))
                quads.append(self._quad(
                    ltx*TILE_SIZE+TILE_SIZE*0.5,sy+1.8,
                    TILE_SIZE+0.8,4.2,(lr,lg,lb,0.98),
                ))
                if len(quads)>=METAL_DYNAMIC_MAX_QUADS:
                    break
        except Exception:
            pass

        # FIX46 weapon silhouettes, charge poses and dedicated heavy effects.
        # Everything remains in the existing bounded dynamic quad batch so the
        # extra presentation does not add UIKit views/draw calls on iPhone.
        if getattr(game, "action_mode", None) == getattr(game, "ACTION_WEAPON", "weapon"):
            facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
            melee = getattr(game, "melee", None)
            weapon = getattr(melee, "selected_weapon", "sword") if melee is not None else "sword"
            try:
                wd = melee.weapon_def()
            except Exception:
                wd = {}
            wd = wd or {}
            length = max(14.0, float(wd.get("visual_length", 31.0) or 31.0))
            thick = max(2.5, float(wd.get("visual_width", 5.0) or 5.0))
            normal_style = str(wd.get("attack_style", "slash") or "slash")
            heavy_style = str(wd.get("heavy_style", "") or "")
            swinging = float(getattr(melee, "swing_timer", 0.0)) > 0.0 if melee is not None else False
            heavy = swinging and str(getattr(melee, "current_attack_kind", "")) == "heavy"
            style = heavy_style if heavy and heavy_style else normal_style
            try:
                progress = float(melee.attack_progress) if swinging else 1.0
            except Exception:
                progress = 1.0
            progress = max(0.0, min(1.0, progress))
            charge_seconds = max(0.0, float(getattr(game, "weapon_charge_seconds", 0.0)))
            try:
                charge_threshold = max(0.10, float(melee.charge_threshold))
            except Exception:
                charge_threshold = max(0.10, float(wd.get("heavy_charge_seconds", 0.55) or 0.55))
            charge_ratio = max(0.0, min(1.0, charge_seconds / charge_threshold))
            charging = (not swinging) and charge_seconds > 0.015
            hand_x = render_player_x + facing * 7.0
            hand_y = render_player_y - p.height() * 0.53

            def _lerp(a, b, t):
                return float(a) + (float(b) - float(a)) * max(0.0, min(1.0, float(t)))

            def _smooth(t):
                t = max(0.0, min(1.0, float(t)))
                return t * t * (3.0 - 2.0 * t)

            def _basis(angle_deg):
                rad = math.radians(float(angle_deg))
                dx = facing * math.cos(rad)
                dy = math.sin(rad)
                # Screen-space perpendicular for ornaments/guards.
                return dx, dy, -dy, facing * math.cos(rad)

            def _chain(angle_deg, draw_length, width, color, start_offset=3.0, spacing=None):
                dx, dy, _px, _py = _basis(angle_deg)
                total = max(2.0, float(draw_length))
                step = float(spacing) if spacing is not None else max(2.6, min(5.2, float(width) * 0.72))
                count = max(2, int(math.ceil(total / max(1.5, step))))
                seg = max(2.4, float(width))
                ex = hand_x; ey = hand_y
                for i in range(count + 1):
                    dist = float(start_offset) + total * (i / float(max(1, count)))
                    ex = hand_x + dx * dist
                    ey = hand_y + dy * dist
                    quads.append(self._quad(ex, ey, seg, seg, color))
                return ex, ey, dx, dy

            idle_angle = {
                "sword": -48.0, "dagger": -28.0, "spear": -60.0,
                "battle_axe": -71.0, "war_hammer": -73.0, "greatsword": -64.0,
                "energy_bow": -12.0, "whip": -58.0, "laser_gun": -8.0,
            }.get(weapon, -48.0)

            if charging:
                # Visibly pull the weapon into its authored heavy wind-up.
                heavy_start = float(wd.get("heavy_start_angle", wd.get("start_angle", -90.0)) or -90.0)
                attack_angle = _lerp(idle_angle, heavy_start, _smooth(charge_ratio) * 0.86)
                extension = 0.78 + 0.16 * charge_ratio
            elif not swinging:
                attack_angle = idle_angle
                extension = 0.78 if weapon == "spear" else 0.73
            else:
                a0 = float((wd.get("heavy_start_angle") if heavy else wd.get("start_angle")) or -70.0)
                a1 = float((wd.get("heavy_end_angle") if heavy else wd.get("end_angle")) or 30.0)
                if style in ("stab", "thrust", "power_stab", "lance_breaker"):
                    pulse = math.sin(math.pi * progress)
                    if style == "lance_breaker":
                        extension = 0.38 + 1.18 * pulse
                    elif style == "power_stab":
                        extension = 0.42 + 0.72 * pulse
                    else:
                        extension = (0.45 if style == "stab" else 0.36) + (0.55 if style == "stab" else 0.72) * pulse
                    attack_angle = _lerp(a0, a1, progress)
                elif style == "dagger_flurry":
                    # The hand itself snaps through several short knife cuts;
                    # the dedicated ghost knives below provide the rapid-flash
                    # multi-blade presentation requested for the hunter knife.
                    attack_angle = _lerp(a0, a1, 0.5 + 0.5*math.sin(progress*math.pi*7.0))
                    extension = 0.86 + 0.22*math.sin(progress*math.pi)**2
                elif style == "ground_slam":
                    if progress < 0.36:
                        attack_angle = _lerp(-58.0, a0, _smooth(progress / 0.36))
                    else:
                        q = (progress - 0.36) / 0.64
                        attack_angle = _lerp(a0, a1, q * q)
                    extension = 1.05
                elif style == "horizontal_cleave":
                    attack_angle = _lerp(a0, a1, _smooth(progress))
                    extension = 1.13
                elif style == "whip_snap":
                    attack_angle = _lerp(a0, a1, _smooth(progress))
                    extension = 0.45 + 0.78 * math.sin(math.pi * progress)
                elif style == "whip_spin":
                    attack_angle = _lerp(a0, a1, progress)
                    extension = 1.12
                else:
                    attack_angle = _lerp(a0, a1, _smooth(progress))
                    extension = 1.08 if heavy else (1.02 if style == "wide_slash" else 0.97)

            # FIX54 AssetEditor-authored weapon art. Saving weapon.<id> in the
            # editor immediately becomes the runtime source; unsaved weapons
            # keep the stable procedural renderer below. Attack/effect frames
            # are selected from normalized gameplay progress, so visual frames
            # and gameplay keyframe events cannot drift with render FPS.
            authored_weapon = False
            authored_effect = False
            _weapon_asset_id = "weapon." + str(weapon)
            _weapon_assets = getattr(game, "assets", None)
            _weapon_state = "idle"
            _weapon_frame = None
            if charging:
                _weapon_state = "heavy_charge"
            elif swinging:
                _weapon_state = "heavy_attack" if heavy else "normal_attack"
            try:
                _winfo = _weapon_assets.animation_info(_weapon_asset_id, _weapon_state) if _weapon_assets is not None else None
            except Exception:
                _winfo = None
            if isinstance(_winfo, dict):
                _wcount = max(1, int(_winfo.get("frame_count", 1) or 1))
                if charging:
                    _weapon_frame = min(_wcount - 1, int(round(charge_ratio * max(0, _wcount - 1))))
                elif swinging:
                    _weapon_frame = min(_wcount - 1, int(round(progress * max(0, _wcount - 1))))
            _weapon_span = 60.0
            _custom_weapon = None if weapon in ("flying_drone", "bee_swarm") else self._pixel_asset_quads(
                game, _weapon_asset_id,
                hand_x - _weapon_span * 0.5, hand_y - _weapon_span * 0.5,
                _weapon_span, _weapon_span,
                flip_x=(facing < 0), animation=_weapon_state,
                animation_time=(float(now) if _weapon_state == "idle" else 0.0),
                frame_index=_weapon_frame,
            )
            if weapon in ("flying_drone", "bee_swarm"):
                # Companion art is emitted at its authoritative world position
                # below, never attached to the player's hand.
                authored_weapon = True
            elif _custom_weapon is not None:
                authored_weapon = True
                quads.extend(_custom_weapon)

            # FIX58 default bindings explicitly use render_mode=fix54, keeping
            # the accepted crescent/flurry/spiral/leaves/rock-burst effects
            # below.  Only a VFX pair actually edited in AssetEditor switches to
            # authored mode and suppresses its corresponding procedural effect.
            if authored_weapon and _weapon_assets is not None:
                _attack_state = "heavy_attack" if heavy else "normal_attack"
                try:_binding = _weapon_assets.combat_binding(_weapon_asset_id, _attack_state)
                except Exception:_binding = None
                authored_effect = (
                    isinstance(_binding, dict) and
                    str(_binding.get("render_mode","authored") or "authored").lower() == "authored"
                )

            if not authored_weapon:
                # Distinct pixel silhouettes.  Normal and heavy attacks share the
                # same recognizable weapon model; only pose/effects change.
                if weapon == "spear":
                    end_x, end_y, dx, dy = _chain(attack_angle, length * extension, 3.0, SWORD_HANDLE_RGBA, 0.5, 3.3)
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    tip_x = end_x + dx * 6.5; tip_y = end_y + dy * 6.5
                    quads.append(self._quad(tip_x, tip_y, 8.0, 8.0, SWORD_BLADE_RGBA))
                    quads.append(self._quad(tip_x + dx*4.0, tip_y + dy*4.0, 4.2, 4.2, SWORD_EDGE_RGBA))
                    quads.append(self._quad(end_x + pxv*4.0, end_y + pyv*4.0, 4.0, 4.0, SWORD_BLADE_RGBA))
                    quads.append(self._quad(end_x - pxv*4.0, end_y - pyv*4.0, 4.0, 4.0, SWORD_BLADE_RGBA))
                elif weapon == "energy_bow":
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    for off in (-12.0, -8.0, -4.0, 0.0, 4.0, 8.0, 12.0):
                        curve = (abs(off) / 12.0) ** 2 * 8.0
                        quads.append(self._quad(hand_x + _dx*curve + pxv*off, hand_y + _dy*curve + pyv*off, 3.2, 3.2, (0.25,0.78,0.98,1.0)))
                    quads.append(self._quad(hand_x + _dx*5.0, hand_y + _dy*5.0, 4.0, 4.0, (0.78,0.98,1.0,1.0)))
                elif weapon == "whip":
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    segments=max(7,int(length*extension/5.0))
                    for wi in range(segments):
                        t=wi/float(max(1,segments-1)); dist=4.0+t*length*extension
                        bend=math.sin(t*math.pi)*(10.0 if swinging else 5.0)*math.sin(progress*math.pi+1.2)
                        quads.append(self._quad(hand_x+_dx*dist+pxv*bend,hand_y+_dy*dist+pyv*bend,3.6 if wi<3 else 2.8,3.6 if wi<3 else 2.8,(0.58,0.32,0.16,1.0)))
                elif weapon == "laser_gun":
                    end_x,end_y,dx,dy=_chain(attack_angle,length*0.72,5.2,(0.18,0.29,0.42,1.0),0.0,4.4)
                    quads.append(self._quad(end_x+dx*4.0,end_y+dy*4.0,8.0,6.0,(0.28,0.86,0.98,1.0)))
                    quads.append(self._quad(end_x+dx*8.0,end_y+dy*8.0,3.2,3.2,(0.90,1.0,1.0,1.0)))
                    quads.append(self._quad(hand_x-dx*2.0,hand_y-dy*2.0,7.0,8.0,SWORD_HANDLE_RGBA))
                elif weapon == "battle_axe":
                    end_x, end_y, dx, dy = _chain(attack_angle, length * extension, 3.6, SWORD_HANDLE_RGBA, 0.5, 3.8)
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    # Asymmetric crescent-like axe head rather than a square block.
                    for off, size in ((0.0, 9.0), (5.0, 8.0), (9.0, 6.0), (-5.0, 7.0)):
                        quads.append(self._quad(end_x + pxv*off, end_y + pyv*off, size+3.0, size, SWORD_BLADE_RGBA))
                    quads.append(self._quad(end_x + pxv*10.0 + dx*2.0, end_y + pyv*10.0 + dy*2.0, 4.0, 4.0, SWORD_EDGE_RGBA))
                elif weapon == "war_hammer":
                    end_x, end_y, dx, dy = _chain(attack_angle, length * extension, 4.0, SWORD_HANDLE_RGBA, 0.0, 4.0)
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    # Thick double-ended iron hammer head with bright striking face.
                    for off in (-7.0, -3.5, 0.0, 3.5, 7.0):
                        quads.append(self._quad(end_x + pxv*off, end_y + pyv*off, 7.5, 8.5, SWORD_BLADE_RGBA))
                    quads.append(self._quad(end_x + pxv*10.0, end_y + pyv*10.0, 5.0, 9.5, SWORD_EDGE_RGBA))
                else:
                    grip_len = 7.0 if weapon == "dagger" else (10.0 if weapon == "greatsword" else 8.0)
                    _chain(attack_angle, grip_len, max(3.0, thick * 0.60), SWORD_HANDLE_RGBA, 0.0, 3.0)
                    blade_start = grip_len - 0.5
                    blade_len = length * extension
                    end_x, end_y, dx, dy = _chain(attack_angle, blade_len, thick, SWORD_BLADE_RGBA, blade_start, max(3.0, thick*0.62))
                    _dx, _dy, pxv, pyv = _basis(attack_angle)
                    if weapon == "dagger":
                        quads.append(self._quad(hand_x + dx*6.0 + pxv*4.0, hand_y + dy*6.0 + pyv*4.0, 4.0, 4.0, SWORD_HANDLE_RGBA))
                        quads.append(self._quad(hand_x + dx*6.0 - pxv*4.0, hand_y + dy*6.0 - pyv*4.0, 4.0, 4.0, SWORD_HANDLE_RGBA))
                        quads.append(self._quad(end_x + dx*2.5, end_y + dy*2.5, 3.5, 3.5, SWORD_EDGE_RGBA))
                    elif weapon == "greatsword":
                        # Oversized crossguard, thick spine and bright tip.
                        for off in (-8.0, -4.0, 4.0, 8.0):
                            quads.append(self._quad(hand_x + dx*8.0 + pxv*off, hand_y + dy*8.0 + pyv*off, 5.0, 5.0, SWORD_HANDLE_RGBA))
                        _chain(attack_angle, blade_len*0.82, max(2.5, thick*0.34), SWORD_EDGE_RGBA, blade_start+2.0, 5.0)
                        quads.append(self._quad(end_x + dx*3.0, end_y + dy*3.0, 6.0, 6.0, SWORD_EDGE_RGBA))
                    else:  # iron sword
                        for off in (-5.0, 5.0):
                            quads.append(self._quad(hand_x + dx*7.0 + pxv*off, hand_y + dy*7.0 + pyv*off, 4.0, 4.0, SWORD_HANDLE_RGBA))
                        _chain(attack_angle, blade_len*0.72, max(2.3, thick*0.30), SWORD_EDGE_RGBA, blade_start+3.0, 5.0)
                        quads.append(self._quad(end_x + dx*2.0, end_y + dy*2.0, 4.0, 4.0, SWORD_EDGE_RGBA))

            # The 24x24 weapon clip is the hand/body pose only.  Runtime whip
            # reach is a separate world-space track matching the 2.28-tile hit
            # volume, so a distant target can never be hit by an invisible tip.
            if weapon=="whip" and swinging and (not heavy) and style=="whip_snap" and not authored_effect:
                _wdx,_wdy,_wpx,_wpy=_basis(attack_angle)
                _reach=TILE_SIZE*max(.5,float(wd.get("reach_tiles",2.28) or 2.28))
                _snap=max(0.0,math.sin(math.pi*progress))
                _draw_reach=_reach*(.44+.56*(_snap**.55))
                _segments=28
                for _wi in range(_segments):
                    _t=_wi/float(_segments-1)
                    _dist=10.0+max(0.0,_draw_reach-10.0)*_t
                    _bend=math.sin(_t*math.pi)*(5.0+15.0*(1.0-_snap))*math.sin(progress*math.pi*1.7+_t*1.1)
                    _size=3.7 if _wi<4 else (4.8 if _wi==_segments-1 else 2.9)
                    _alpha=.54+.44*_t
                    quads.append(self._quad(
                        hand_x+_wdx*_dist+_wpx*_bend,
                        hand_y+_wdy*_dist+_wpy*_bend,
                        _size,_size,(.78,.51,.27,_alpha),
                    ))

            # Fully charged glints tell the player that releasing now will use
            # the weapon's heavy profile without adding another text label.
            if charging and charge_ratio >= 0.98 and weapon!="flying_drone":
                for gi in range(3):
                    phase = (float(now) * 6.0 + gi * 2.1)
                    rr = 10.0 + gi * 5.0
                    gx = hand_x + math.cos(phase) * rr
                    gy = hand_y + math.sin(phase) * rr
                    quads.append(self._quad(gx, gy, 3.0, 3.0, SWORD_EDGE_RGBA))

            if heavy and swinging and not authored_effect:
                dx, dy, pxv, pyv = _basis(attack_angle)
                if style in ("crescent_flash", "flying_crescent"):
                    # A broad crescent flash follows the sword arc at the moment
                    # of impact. Greatsword also launches an authoritative copy.
                    flash_ok = (0.20 <= progress <= (0.50 if style == "flying_crescent" else 0.82))
                    if flash_ok:
                        radius = length * (1.08 if weapon == "sword" else 1.18)
                        for ai in range(-3, 4):
                            ar = math.radians(attack_angle + ai * 9.0)
                            ax = hand_x + facing * math.cos(ar) * radius
                            ay = hand_y + math.sin(ar) * radius
                            quads.append(self._quad(ax, ay, 5.0 + (3-abs(ai))*0.6, 5.0 + (3-abs(ai))*0.6, SWORD_EDGE_RGBA))
                elif style == "dagger_flurry":
                    # FIX47 hunter-knife heavy: many short knives strobe across
                    # the forward attack volume in a rapid, staggered sequence.
                    # They are bounded render quads only; damage remains one
                    # authoritative heavy attack in MeleeSystem.
                    for fi in range(11):
                        local = progress*1.72 - fi*0.105
                        if local < 0.0 or local > 0.58:
                            continue
                        fade = max(0.0, math.sin((local/0.58)*math.pi))
                        ga = -34.0 + ((fi*29) % 58) - 10.0*math.sin(local*math.pi*2.0)
                        gdx,gdy,gpx,gpy = _basis(ga)
                        forward = 13.0 + (fi%5)*7.0 + local*24.0
                        lateral = ((fi%4)-1.5)*4.0
                        gx = hand_x + gdx*forward + gpx*lateral
                        gy = hand_y + gdy*forward + gpy*lateral + 7.0 + ((fi%3)-1)*3.0
                        blade_col=(SWORD_BLADE_RGBA[0],SWORD_BLADE_RGBA[1],SWORD_BLADE_RGBA[2],0.30+0.68*fade)
                        edge_col=(SWORD_EDGE_RGBA[0],SWORD_EDGE_RGBA[1],SWORD_EDGE_RGBA[2],0.36+0.64*fade)
                        handle_col=(SWORD_HANDLE_RGBA[0],SWORD_HANDLE_RGBA[1],SWORD_HANDLE_RGBA[2],0.28+0.64*fade)
                        quads.append(self._quad(gx-gdx*5.0,gy-gdy*5.0,4.0,4.0,handle_col))
                        quads.append(self._quad(gx-gdx*1.5+gpx*3.0,gy-gdy*1.5+gpy*3.0,3.4,3.4,handle_col))
                        quads.append(self._quad(gx-gdx*1.5-gpx*3.0,gy-gdy*1.5-gpy*3.0,3.4,3.4,handle_col))
                        for bi in range(4):
                            dist=2.0+bi*3.1
                            quads.append(self._quad(gx+gdx*dist,gy+gdy*dist,3.6,3.6,blade_col))
                        quads.append(self._quad(gx+gdx*14.0,gy+gdy*14.0,3.0,3.0,edge_col))
                elif style == "lance_breaker":
                    # FIX61 rebuilt spear heavy: a strong forward core, three
                    # expanding pressure rings and lateral speed streaks. The
                    # silhouette remains readable instead of dissolving into
                    # the former tiny helix dots.
                    pulse = math.sin(math.pi * progress)
                    reach = length * (0.42 + 0.95 * _smooth(progress))
                    for si in range(10):
                        dist = 8.0 + reach * (si / 9.0)
                        alpha = .36 + .62 * (si / 9.0)
                        quads.append(self._quad(hand_x+dx*dist,hand_y+dy*dist,5.2 if si>6 else 3.8,5.2 if si>6 else 3.8,(.56,.92,1.0,alpha)))
                    tip_x=hand_x+dx*(reach+7.0);tip_y=hand_y+dy*(reach+7.0)
                    quads.append(self._quad(tip_x,tip_y,8.0,8.0,(.94,1.0,1.0,.98)))
                    for ring in range(3):
                        local=progress-ring*.11
                        if local<0.0 or local>.82:continue
                        center_dist=18.0+ring*13.0+local*24.0
                        rr=5.0+local*13.0
                        alpha=max(.15,.82*(1.0-local/.82))
                        for ai in range(12):
                            ar=ai*math.pi*2.0/12.0
                            rx=hand_x+dx*center_dist+pxv*math.cos(ar)*rr+dx*math.sin(ar)*rr*.18
                            ry=hand_y+dy*center_dist+pyv*math.cos(ar)*rr+dy*math.sin(ar)*rr*.18
                            quads.append(self._quad(rx,ry,3.1,3.1,(.25,.80,1.0,alpha)))
                    for streak in range(4):
                        off=(streak-1.5)*7.0
                        quads.append(self._quad(hand_x+dx*(20.0+streak*9.0)+pxv*off,hand_y+dy*(20.0+streak*9.0)+pyv*off,10.0+12.0*pulse,2.4,(.55,.91,1.0,.30+.35*pulse)))
                elif style == "ground_slam" and progress >= 0.52:
                    # FIX50: make the hammer impact readable before terrain is
                    # actually removed. Bigger stone burst + short dust fan.
                    impact_x = render_player_x + facing * TILE_SIZE * 0.72
                    impact_y = render_player_y + 1.0
                    burst = max(0.0, min(1.0, (progress - 0.52) / 0.48))
                    dust_col = (0.86, 0.80, 0.68, 0.42 + 0.24*(1.0-burst))
                    for ri in range(10):
                        side = -1.0 if ri % 2 == 0 else 1.0
                        spread = (ri//2 + 1) * 4.8 * side
                        lift = math.sin(min(1.0, burst)*math.pi) * (10.0 + (ri%4)*3.5)
                        size = 4.8 + (ri % 3) * 0.9
                        quads.append(self._quad(impact_x + spread, impact_y - lift, size, size, MINIMAP_ROCK_RGBA))
                    for di in range(6):
                        dxo = (di - 2.5) * 7.0
                        dyo = math.sin(burst * math.pi) * (2.5 + di*0.6)
                        quads.append(self._quad(impact_x + dxo, impact_y - dyo, 8.0 + di*0.5, 3.2, dust_col))
                elif style == "horizontal_cleave" and 0.18 <= progress <= 0.82:
                    # Grass/leaf flecks make the utility of the axe readable.
                    for fi in range(8):
                        dist = 12.0 + fi*4.2
                        lift = math.sin(progress*math.pi + fi*0.7) * 7.0
                        fx = hand_x + facing*dist
                        fy = hand_y + lift + (fi%3-1)*4.0
                        quads.append(self._quad(fx, fy, 3.4, 3.4, SEAGRASS_RGBA if fi%2 else SEAGRASS_TIP_RGBA))
                elif style == "energy_arrow_rain" and progress >= .24:
                    burst=max(0.0,min(1.0,(progress-.24)/.76))
                    for ai in range(9):
                        ar=-math.pi*.78+(ai/8.0)*math.pi*.56
                        rr=12.0+30.0*burst
                        ax=hand_x+facing*math.cos(ar)*rr
                        ay=hand_y+math.sin(ar)*rr
                        quads.append(self._quad(ax,ay,3.2,3.2,(.25,.85,1.0,max(.18,.82*(1.0-burst*.65)))))
                elif style == "whip_spin":
                    radius=TILE_SIZE*float(wd.get("heavy_reach_tiles",2.38) or 2.38)*(.48+.50*math.sin(math.pi*progress))
                    head_angle=progress*math.pi*2.0*1.16
                    for wi in range(30):
                        ar=head_angle-(29-wi)*.075
                        rr=radius*(.58+.42*wi/29.0)
                        alpha=.24+.72*wi/29.0
                        quads.append(self._quad(render_player_x+math.cos(ar)*rr,render_player_y-p.height()*.46+math.sin(ar)*rr,3.2 if wi<26 else 4.6,3.2 if wi<26 else 4.6,(.78,.51,.27,alpha)))
                elif style == "laser_ricochet_10" and .18 <= progress <= .62:
                    flash=1.0-abs(progress-.40)/.22
                    for ri in range(4):
                        ar=ri*math.pi*.5+progress*math.pi
                        rr=7.0+ri*3.0
                        quads.append(self._quad(hand_x+dx*18.0+math.cos(ar)*rr,hand_y+dy*18.0+math.sin(ar)*rr,3.0,3.0,(.35,.92,1.0,.30+.60*max(0.0,flash))))
                elif style == "tnt_demolition":
                    # Heavy TNT uses a visibly overcharged fuse before the 3x3
                    # demolition bundle leaves the hand.
                    pulse=max(0.0,math.sin(progress*math.pi))
                    for fi in range(8):
                        ar=fi*math.pi*.25+float(now)*5.0
                        rr=8.0+9.0*pulse+(fi%2)*3.0
                        col=(1.0,.82,.18,.35+.55*pulse) if fi%3 else (1.0,.20,.05,.42+.48*pulse)
                        quads.append(self._quad(hand_x+math.cos(ar)*rr,hand_y+math.sin(ar)*rr,3.4,3.4,col))

        # FIX67 second-layer trails stay independent from FIX54/authored core
        # art and terminate at the same reach used by authoritative collision.
        self._append_weapon_attack_accents(
            quads,game,p,render_player_x,render_player_y,now,
        )

        # Authored flipbooks and authoritative projectiles may outlive the hand
        # attack or a mode switch, so they deliberately render outside the
        # ACTION_WEAPON branch.
        self._append_weapon_world_visuals(
            quads,game,p,render_player_x,render_player_y,now,
        )

        append_boss_visuals(self, quads, game, now=now, alpha=alpha)

        # FIX71 bounded grappling-hook presentation.  The gameplay system owns
        # one hook slot; rendering samples at most 48 pixel-chain links and
        # never retains the mutable snapshot across frames.
        try:
            _grapple_state=dict(game.tools.grapple_render_state() or {})
        except Exception:
            _grapple_state={}
        if bool(_grapple_state.get("visible",False)):
            _gx1=float(render_player_x)
            _gy1=float(render_player_y)-float(p.height())*.45
            _ghx0=float(_grapple_state.get("prev_hook_x",_grapple_state.get("hook_x",_gx1)))
            _ghy0=float(_grapple_state.get("prev_hook_y",_grapple_state.get("hook_y",_gy1)))
            _ghx=_ghx0+(float(_grapple_state.get("hook_x",_ghx0))-_ghx0)*alpha
            _ghy=_ghy0+(float(_grapple_state.get("hook_y",_ghy0))-_ghy0)*alpha
            _gdx=_ghx-_gx1;_gdy=_ghy-_gy1;_glen=math.hypot(_gdx,_gdy)
            if _glen>1e-6:
                _gux=_gdx/_glen;_guy=_gdy/_glen
                _links=max(1,min(48,int(_glen/7.0)+1))
                for _gi in range(1,_links):
                    _gt=_gi/float(_links)
                    _gcx=_gx1+_gdx*_gt;_gcy=_gy1+_gdy*_gt
                    quads.append(self._quad(_gcx+1.0,_gcy+1.0,3.2,3.2,(.05,.07,.10,.82)))
                    _metal=(.58,.69,.76,.96) if _gi%2 else (.35,.47,.56,.96)
                    quads.append(self._quad(_gcx,_gcy,2.2,2.2,_metal))
                # Pixel hook head: shaft plus two backward-facing prongs.
                _back_x=-_gux;_back_y=-_guy;_perp_x=-_guy;_perp_y=_gux
                for _si in range(4):
                    _sd=float(_si)*3.0
                    quads.append(self._quad(_ghx+_back_x*_sd,_ghy+_back_y*_sd,4.0,4.0,(.70,.82,.88,1.0)))
                for _side in (-1.0,1.0):
                    for _pi in range(3):
                        _pd=4.0+float(_pi)*3.0
                        quads.append(self._quad(
                            _ghx+_back_x*_pd+_perp_x*_side*float(_pi+1)*2.1,
                            _ghy+_back_y*_pd+_perp_y*_side*float(_pi+1)*2.1,
                            4.0,4.0,(.82,.91,.94,1.0),
                        ))

        # FIX71 renders the two additional formation members independently;
        # the retained FIX62 block immediately below remains the richer
        # presentation for unit zero.  Every path/trail/explosion is sourced
        # from its own bounded DroneUnit packet.
        _drone_group=getattr(game,"drone",None)
        try:_extra_drones=tuple(_drone_group.render_instances())[1:3]
        except Exception:_extra_drones=()
        for _du in _extra_drones:
            _uds=str(_du.get("state","inactive") or "inactive")
            _udx=float(_du.get("prev_x",_du.get("x",0.0)))+(float(_du.get("x",0.0))-float(_du.get("prev_x",_du.get("x",0.0))))*alpha
            _udy=float(_du.get("prev_y",_du.get("y",0.0)))+(float(_du.get("y",0.0))-float(_du.get("prev_y",_du.get("y",0.0))))*alpha
            if bool(_du.get("active",False)) and _uds!="explosion":
                _umx=float(_du.get("x",_udx))-float(_du.get("prev_x",_udx));_umy=float(_du.get("y",_udy))-float(_du.get("prev_y",_udy))
                _uml=max(1e-6,math.hypot(_umx,_umy));_ufacing=1 if _umx>=0.0 else -1
                _uanim="heavy_attack" if _uds=="heavy_dive" else ("normal_attack" if _uds in ("dive","return") else "idle")
                _uq=self._pixel_asset_quads(
                    game,"weapon.flying_drone",_udx-40.0,_udy-40.0,80.0,80.0,
                    flip_x=(_ufacing<0),animation=_uanim,
                    animation_time=max(0.0,float(_du.get("attack_animation_offset",0.0))+float(_du.get("state_elapsed",0.0))),
                    rotation_radians=math.atan2(_umy,_umx),
                )
                if _uq is not None:quads.extend(_uq)
                else:
                    quads.append(self._quad(_udx,_udy,24.0,10.0,(.08,.16,.24,1.0)))
                    quads.append(self._quad(_udx,_udy-1.0,12.0,8.0,(.20,.88,1.0,1.0)))
                    quads.append(self._quad(_udx-16.0,_udy-7.0,20.0,2.5,(.70,.82,.90,1.0)))
                    quads.append(self._quad(_udx+16.0,_udy-7.0,20.0,2.5,(.70,.82,.90,1.0)))
                _utrail=list(_du.get("trail",()) or ())[-8:]
                for _uti,_upos in enumerate(_utrail):
                    _uta=.10+.50*(_uti+1)/float(max(1,len(_utrail)))
                    quads.append(self._quad(float(_upos[0]),float(_upos[1]),3.0+_uti*.20,3.0+_uti*.20,(.12,.78,1.0,_uta)))
                if _uds=="heavy_dive":
                    for _uri in range(2):
                        _uar=now*10.0+_uri*math.pi+float(_du.get("phase_offset",0.0))
                        quads.append(self._quad(_udx+math.cos(_uar)*12.0,_udy+math.sin(_uar)*12.0,5.0,5.0,(1.0,.25,.08,.88)))
            if float(_du.get("explosion_timer",0.0) or 0.0)>0.0:
                _uex=float(_du.get("explosion_x",_udx));_uey=float(_du.get("explosion_y",_udy))
                _udur=max(.01,float(_du.get("explosion_duration",.52) or .52))
                _ulife=max(0.0,min(1.0,float(_du.get("explosion_timer",0.0))/_udur));_up=1.0-_ulife
                for _uri in range(2):
                    _urr=15.0+_up*(42.0+_uri*17.0)
                    for _uai in range(12):
                        _uar=_uai*math.pi/6.0+_uri*.18
                        quads.append(self._quad(_uex+math.cos(_uar)*_urr,_uey+math.sin(_uar)*_urr,5.5-_uri*.7,5.5-_uri*.7,(1.0,.20+.22*_uri,.03,max(.06,_ulife*(.76-_uri*.16)))))

        # FIX62 companion drone. This block is independent of action mode and
        # selected weapon so an already-committed kamikaze dive/explosion
        # remains visible after equipment immediately falls back.
        _drone=getattr(game,"drone",None)
        if _drone is not None and (bool(getattr(_drone,"active",False)) or float(getattr(_drone,"explosion_timer",0.0))>0.0):
            _ds=str(getattr(_drone,"state","inactive") or "inactive")
            _dx=float(getattr(_drone,"prev_x",getattr(_drone,"x",0.0)))+(float(getattr(_drone,"x",0.0))-float(getattr(_drone,"prev_x",getattr(_drone,"x",0.0))))*alpha
            _dy=float(getattr(_drone,"prev_y",getattr(_drone,"y",0.0)))+(float(getattr(_drone,"y",0.0))-float(getattr(_drone,"prev_y",getattr(_drone,"y",0.0))))*alpha
            _drone_assets=getattr(game,"assets",None)
            try:_normal_binding=_drone_assets.combat_binding("weapon.flying_drone","normal_attack") if _drone_assets is not None else None
            except Exception:_normal_binding=None
            try:_heavy_binding=_drone_assets.combat_binding("weapon.flying_drone","heavy_attack") if _drone_assets is not None else None
            except Exception:_heavy_binding=None
            _normal_authored=isinstance(_normal_binding,dict) and str(_normal_binding.get("render_mode","runtime") or "runtime").lower()=="authored"
            _heavy_authored=isinstance(_heavy_binding,dict) and str(_heavy_binding.get("render_mode","runtime") or "runtime").lower()=="authored"
            _move_x=float(getattr(_drone,"x",_dx))-float(getattr(_drone,"prev_x",_dx))
            _move_y=float(getattr(_drone,"y",_dy))-float(getattr(_drone,"prev_y",_dy))
            _move_len=math.hypot(_move_x,_move_y)
            if _move_len<=1e-6:
                _move_x=float(getattr(p,"facing",1) or 1);_move_y=0.0;_move_len=max(1.0,abs(_move_x))
            _move_ux=_move_x/_move_len;_move_uy=_move_y/_move_len
            _drone_angle=math.atan2(_move_uy,_move_ux)
            _drone_facing=1 if _move_ux>=0.0 else -1
            if _ds!="explosion":
                _pending=getattr(_drone,"pending_attack",None)
                _drone_selected=str(getattr(getattr(game,"melee",None),"selected_weapon",""))=="flying_drone"
                _drone_charging=(
                    _drone_selected and
                    getattr(game,"action_mode",None)==getattr(game,"ACTION_WEAPON","weapon") and
                    float(getattr(game,"weapon_charge_seconds",0.0) or 0.0)>.015 and
                    _ds in ("orbit","return")
                )
                if isinstance(_pending,dict):
                    _anim="heavy_attack" if bool(_pending.get("heavy",False)) else "normal_attack"
                    _anim_time=max(0.0,float(_pending.get("duration",0.0) or 0.0)-float(_pending.get("delay",0.0) or 0.0))
                elif _drone_charging:
                    _anim="heavy_charge";_anim_time=max(0.0,float(getattr(game,"weapon_charge_seconds",0.0) or 0.0))
                elif _ds=="heavy_dive":
                    _anim="heavy_attack";_anim_time=max(0.0,float(getattr(_drone,"attack_animation_offset",0.0) or 0.0)+float(getattr(_drone,"state_elapsed",0.0) or 0.0))
                elif _ds in ("dive","return"):
                    _anim="normal_attack";_anim_time=max(0.0,float(getattr(_drone,"attack_animation_offset",0.0) or 0.0)+float(getattr(_drone,"attack_vfx_elapsed",getattr(_drone,"state_elapsed",0.0)) or 0.0))
                else:
                    _anim="idle";_anim_time=float(now)
                _dq=self._pixel_asset_quads(
                    game,"weapon.flying_drone",_dx-40.0,_dy-40.0,80.0,80.0,
                    flip_x=(_drone_facing<0),animation=_anim,animation_time=_anim_time,
                )
                if _dq is not None:
                    quads.extend(_dq)
                else:
                    quads.append(self._quad(_dx,_dy,24.0,10.0,(.08,.16,.24,1.0)))
                    quads.append(self._quad(_dx,_dy-1.0,12.0,8.0,(.20,.88,1.0,1.0)))
                    quads.append(self._quad(_dx-16.0,_dy-7.0,20.0,2.5,(.70,.82,.90,1.0)))
                    quads.append(self._quad(_dx+16.0,_dy-7.0,20.0,2.5,(.70,.82,.90,1.0)))
                _normal_effect_drawn=False
                if _normal_authored and _ds in ("dive","return"):
                    _nb=_normal_binding or {}
                    _nox=float(_nb.get("offset_x",0.0) or 0.0);_noy=float(_nb.get("offset_y",0.0) or 0.0)
                    _ncx=_dx+_move_ux*_nox-_move_uy*_noy;_ncy=_dy+_move_uy*_nox+_move_ux*_noy
                    _nq=self._pixel_asset_quads(
                        game,"weapon.flying_drone",_ncx-40.0,_ncy-40.0,80.0,80.0,
                        animation=str(_nb.get("effect_state","normal_effect") or "normal_effect"),
                        animation_time=max(0.0,float(getattr(_drone,"attack_vfx_elapsed",0.0) or 0.0)),
                        visual_scale=_nb.get("scale",1.0),rotation_radians=_drone_angle,
                    )
                    if _nq is not None:quads.extend(_nq);_normal_effect_drawn=True
                # The eight-point authoritative flight history remains as a
                # second layer even when authored dive art is present.
                _trail=list(getattr(_drone,"trail",()) or ())[-8:]
                for _ti,_pos in enumerate(_trail):
                    _ta=(.08 if _normal_effect_drawn else .10)+(.42 if _normal_effect_drawn else .54)*(_ti+1)/float(max(1,len(_trail)))
                    _trail_size=(2.7 if _normal_effect_drawn else 3.0)+_ti*.22
                    quads.append(self._quad(float(_pos[0]),float(_pos[1]),_trail_size,_trail_size,(.12,.78,1.0,_ta)))
                if isinstance(_pending,dict):
                    _total=max(.01,float(_pending.get("duration",.01)))
                    _wind=max(0.0,min(1.0,1.0-float(_pending.get("delay",0.0))/_total))
                    _pc=(1.0,.27,.06,.78) if bool(_pending.get("heavy",False)) else (.20,.90,1.0,.72)
                    _rr=10.0+_wind*8.0
                    for _pi in range(8):
                        _ar=_pi*math.pi*.25+now*4.0
                        quads.append(self._quad(_dx+math.cos(_ar)*_rr,_dy+math.sin(_ar)*_rr,3.2,3.2,_pc))
                if _drone_charging:
                    try:_charge_threshold=max(.10,float(game.melee.charge_threshold))
                    except Exception:_charge_threshold=.55
                    _charge_ratio=max(0.0,min(1.0,float(getattr(game,"weapon_charge_seconds",0.0) or 0.0)/_charge_threshold))
                    if _charge_ratio>=.98:
                        for _gi in range(3):
                            _ar=now*6.0+_gi*2.1;_rr=10.0+_gi*5.0
                            quads.append(self._quad(_dx+math.cos(_ar)*_rr,_dy+math.sin(_ar)*_rr,3.0,3.0,SWORD_EDGE_RGBA))
                if _ds=="heavy_dive":
                    for _ri in range(2):
                        _ar=now*10.0+_ri*math.pi
                        quads.append(self._quad(_dx+math.cos(_ar)*12.0,_dy+math.sin(_ar)*12.0,5.0,5.0,(1.0,.25,.08,.88)))
            else:
                _ex=float(getattr(_drone,"explosion_x",_dx));_ey=float(getattr(_drone,"explosion_y",_dy))
                _duration=max(.01,float(getattr(_drone,"explosion_duration",.46)))
                _life=max(0.0,min(1.0,float(getattr(_drone,"explosion_timer",0.0))/_duration))
                _progress=1.0-_life
                _heavy_effect_drawn=False
                if _heavy_authored:
                    _hb=_heavy_binding or {}
                    _hcx=_ex+float(_hb.get("offset_x",0.0) or 0.0)
                    _hcy=_ey+float(_hb.get("offset_y",0.0) or 0.0)
                    _hq=self._pixel_asset_quads(
                        game,"weapon.flying_drone",_hcx-40.0,_hcy-40.0,80.0,80.0,
                        animation=str(_hb.get("effect_state","heavy_effect") or "heavy_effect"),
                        animation_time=max(0.0,_duration-float(getattr(_drone,"explosion_timer",0.0) or 0.0)),
                        visual_scale=_hb.get("scale",1.0),
                    )
                    if _hq is not None:quads.extend(_hq);_heavy_effect_drawn=True
                # Explosion perimeter reaches the same radius advertised by
                # gameplay. It surrounds (rather than replaces) authored art.
                try:
                    _explosion_radius=max(12.0,float(getattr(_drone,"row",{}).get("heavy_explosion_radius",84.0) or 84.0))
                    _dprofile=getattr(game,"melee",None).presentation_profile("flying_drone")
                    _explosion_radius*=max(.5,min(1.5,float((_dprofile or {}).get("explosion_radius_scale",1.0) or 1.0)))
                except Exception:
                    _explosion_radius=84.0
                _perimeter_radius=14.0+(_explosion_radius-14.0)*min(1.0,_progress*1.35)
                for _outer_i in range(16):
                    _outer_angle=_outer_i*math.pi/8.0+_progress*.22
                    quads.append(self._quad(
                        _ex+math.cos(_outer_angle)*_perimeter_radius,
                        _ey+math.sin(_outer_angle)*_perimeter_radius,
                        4.2,4.2,(1.0,.43 if _outer_i%2 else .72,.06,max(.08,_life*.62)),
                    ))
                if not _heavy_effect_drawn:
                    for _ring in range(3):
                        _rr=15.0+_progress*(42.0+_ring*17.0)
                        _ring_alpha=max(.06,_life*(.78-_ring*.16))
                        for _ai in range(12):
                            _ar=_ai*math.pi*2.0/12.0+_ring*.18
                            quads.append(self._quad(_ex+math.cos(_ar)*_rr,_ey+math.sin(_ar)*_rr,5.5-_ring*.7,5.5-_ring*.7,(1.0,.18+.22*_ring,.03,_ring_alpha)))
                    quads.append(self._quad(_ex,_ey,36.0+_progress*30.0,36.0+_progress*30.0,(1.0,.70,.10,.28*_life)))

        # Collision-confirmed sparks are deliberately appended after weapon,
        # projectile and drone bodies so the contact point remains readable.
        self._append_weapon_impact_feedback(quads,game,now)

        # Fixed-radius directional crosshair.
        #
        # The joystick controls angle only. The crosshair orbits the character
        # at a constant radius and follows the interpolated player position,
        # so it cannot drift across the whole screen.
        try:
            aim_dx, aim_dy = (
                game.aim_direction()
            )
        except Exception:
            aim_dx = float(
                getattr(
                    p,
                    "facing",
                    1,
                )
                or 1
            )
            aim_dy = 0.0

        aim_origin_x = (
            render_player_x
        )
        aim_origin_y = (
            render_player_y
            - p.height()
            * 0.45
        )

        cross_x = (
            aim_origin_x
            + float(
                aim_dx
            )
            * AIM_RETICLE_RADIUS
        )

        cross_y = (
            aim_origin_y
            + float(
                aim_dy
            )
            * AIM_RETICLE_RADIUS
        )

        cross_color = (
            1.0,
            1.0,
            1.0,
            0.92,
        )

        quads.append(
            self._quad(
                cross_x,
                cross_y,
                22.0,
                2.0,
                cross_color,
            )
        )

        quads.append(
            self._quad(
                cross_x,
                cross_y,
                2.0,
                22.0,
                cross_color,
            )
        )

        quads.append(
            self._quad(
                cross_x,
                cross_y,
                5.0,
                5.0,
                (
                    0.10,
                    0.12,
                    0.15,
                    0.92,
                ),
            )
        )

        # Fire/water projectile trajectory comes from Python MagicSystem.
        for fb in game.magic.fireballs:
            if fb.active:
                _custom=self._pixel_asset_quads(game,"magic.fireball",fb.x-20.,fb.y-20.,40.,40.,visual_scale=float(fb.radius)/8.)
                if _custom is not None:
                    quads.extend(_custom);continue
                d = fb.radius*2.1
                quads.append(self._quad(fb.x, fb.y, d, d, (1.0,0.28,0.04,0.96)))
                quads.append(self._quad(fb.x-1, fb.y-1, d*0.48, d*0.48, (1.0,0.84,0.18,1.0)))
        for wb in game.magic.waterballs:
            if wb.active:
                _custom=self._pixel_asset_quads(game,"magic.waterball",wb.x-20.,wb.y-20.,40.,40.,visual_scale=float(wb.radius)/8.)
                if _custom is not None:
                    quads.extend(_custom);continue
                d = wb.radius*2.15
                quads.append(self._quad(wb.x, wb.y, d, d, (0.10,0.50,0.96,0.91)))
                quads.append(self._quad(wb.x-1, wb.y-1, d*0.45, d*0.45, (0.65,0.90,1.0,0.95)))

        for ib in game.magic.iceballs:
            if ib.active:
                _custom=self._pixel_asset_quads(game,"magic.iceball",ib.x-20.,ib.y-20.,40.,40.,visual_scale=float(ib.radius)/8.)
                if _custom is not None:
                    quads.extend(_custom);continue
                d = ib.radius*2.20
                quads.append(
                    self._quad(
                        ib.x,
                        ib.y,
                        d,
                        d,
                        (0.38,0.80,1.0,0.94),
                    )
                )
                quads.append(
                    self._quad(
                        ib.x-1,
                        ib.y-1,
                        d*0.43,
                        d*0.43,
                        (0.86,0.97,1.0,1.0),
                    )
                )

        # FIX30 hostile dungeon magic / elemental hazards.
        for mb in getattr(getattr(game, "creature_system", None), "enemy_magic_bolts", ()):
            if not getattr(mb, "active", False):
                continue
            element=str(getattr(mb,"element","arcane"))
            if element=="fire_column":
                w=float(getattr(mb,"width",20.0) or 20.0);h=float(getattr(mb,"height",60.0) or 60.0)
                quads.append(self._quad(mb.x,mb.y,w,h,(1.00,0.28,0.06,0.84)))
                quads.append(self._quad(mb.x,mb.y+2.0,w*.48,h*.78,(1.00,0.78,0.16,0.94)))
                quads.append(self._quad(mb.x,mb.y-h*.32,w*.28,h*.22,(1.00,0.94,0.48,0.90)))
            elif element=="lightning":
                d=float(getattr(mb,"radius",7.0))*2.2
                quads.append(self._quad(mb.x,mb.y,d,d,(1.00,0.82,0.12,0.96)))
                quads.append(self._quad(mb.x-1,mb.y-1,d*.38,d*.70,(1.00,0.98,0.66,1.0)))
                quads.append(self._quad(mb.x+2,mb.y-2,d*.18,d*.92,(0.52,0.76,1.00,0.92)))
            elif element=="fire":
                d=float(getattr(mb,"radius",7.0))*2.25
                quads.append(self._quad(mb.x,mb.y,d,d,(1.0,.24,.04,.96)))
                quads.append(self._quad(mb.x-1,mb.y-1,d*.46,d*.46,(1.0,.86,.18,1.0)))
                quads.append(self._quad(mb.x-float(getattr(mb,"vx",0.0))*.025,mb.y-float(getattr(mb,"vy",0.0))*.025,d*.38,d*.38,(.92,.10,.03,.44)))
            elif element in ('seed','wind','cloud'):
                d=float(getattr(mb,'radius',7.))*2.
                vx=float(getattr(mb,'vx',0.));vy=float(getattr(mb,'vy',0.))
                if element=='seed':
                    quads.append(self._quad(mb.x,mb.y,d*.8,d,(.62,.39,.18,1.)))
                    quads.append(self._quad(mb.x-1,mb.y-2,d*.4,d*.6,(.96,.80,.41,1.)))
                    quads.append(self._quad(mb.x-vx*.03,mb.y-vy*.03,5.,3.,(.42,.70,.39,.72)))
                elif element=='wind':
                    for k in range(3):
                        quads.append(self._quad(mb.x-k*vx*.014,mb.y-k*vy*.014+k*3.,d*(1.-k*.19),3.,(.62,.93,.94,.95-k*.2)))
                else:
                    quads.append(self._quad(mb.x,mb.y,d,d*.8,(.62,.76,.81,.95)))
                    quads.append(self._quad(mb.x-2,mb.y-3,d*.66,d*.62,(.92,.96,.92,1.)))
                    quads.append(self._quad(mb.x-vx*.03,mb.y-vy*.03,6.,4.,(.91,.93,.84,.54)))
            elif element=="crystal":
                d=float(getattr(mb,"radius",7.0))*2.35
                quads.append(self._quad(mb.x,mb.y,d*.72,d,(.22,.75,1.0,.95)))
                quads.append(self._quad(mb.x+2,mb.y-2,d*.30,d*.70,(.84,.98,1.0,1.0)))
                quads.append(self._quad(mb.x-d*.38,mb.y,d*.35,d*.35,(.30,.57,.92,.52)))
            else:
                d=float(getattr(mb, "radius", 7.0))*2.2
                quads.append(self._quad(mb.x, mb.y, d, d, (0.62,0.26,0.92,0.96)))
                quads.append(self._quad(mb.x-1, mb.y-1, d*0.42, d*0.42, (0.91,0.72,1.00,1.0)))

        # V0.7.2.1 Electric ball: yellow/gold core and pale-yellow discharge.
        for eb in game.magic.electricballs:
            if eb.active:
                _custom=self._pixel_asset_quads(game,"magic.electricball",eb.x-20.,eb.y-20.,40.,40.,visual_scale=float(eb.radius)/8.)
                if _custom is not None:
                    quads.extend(_custom);continue
                d = eb.radius*2.25
                quads.append(self._quad(eb.x, eb.y, d, d, (1.00,0.78,0.12,0.94)))
                quads.append(self._quad(eb.x-1, eb.y-1, d*0.48, d*0.48, (1.00,0.96,0.58,1.0)))
                quads.append(self._quad(eb.x+2, eb.y-2, d*0.18, d*0.62, (1.00,0.88,0.32,0.86)))

        # FIX28 always-visible global minimap. It is appended after world
        # effects so it remains visually on top, while the expanded dynamic
        # buffer budget keeps gameplay actors/effects from being truncated.
        quads.extend(self._build_global_minimap_quads(
            game, render_camera_x, render_camera_y, now
        ))

        # FIX32 portal transition: one full-screen Metal quad. No extra UIKit
        # controller/view is created, which keeps iPhone/iPad Pro behavior
        # identical and avoids native presentation churn during map changes.
        fade=max(0.0,min(1.0,float(getattr(game,"map_transition_alpha",0.0))))
        if fade>0.001:
            fade_quad=self._quad(
                float(render_camera_x)+float(game.viewport_w)*0.5,
                float(render_camera_y)+float(game.viewport_h)*0.5,
                float(game.viewport_w)+6.0,float(game.viewport_h)+6.0,
                (0.0,0.0,0.0,fade),
            )
            # The black transition mask is correctness-critical.  Reserve the
            # final dynamic slot even in a pathological max-VFX scene instead
            # of allowing the ordinary head-slice below to discard it.
            if len(quads)>=int(METAL_DYNAMIC_MAX_QUADS):
                del quads[max(0,int(METAL_DYNAMIC_MAX_QUADS)-1):]
            quads.append(fade_quad)

        # FIX66 GPU-native selector overlay.  The three high-frequency touch
        # targets are transparent UIControls; all changing color/icon state is
        # appended as one bounded Metal tail.  During inventory or map fade the
        # selector tail is omitted entirely.
        selector_quads = tuple()
        if not bool(getattr(self, "_inventory_hud_hidden", False)) and fade <= 0.001:
            try:
                selector_quads = build_selector_overlay(
                    game.viewport_w, game.viewport_h,
                    render_camera_x, render_camera_y,
                    tool_id=getattr(getattr(game, "tools", None), "selected_tool", "pickaxe"),
                    magic_id=getattr(getattr(game, "magic", None), "selected_magic", "fireball"),
                    action_mode=getattr(game, "action_mode", "tool"),
                    weapon_id=getattr(getattr(game, "melee", None), "selected_weapon", "sword"),
                    ipad=is_ipad(),
                )
            except Exception as exc:
                # Rendering the world must remain fail-open even if imported
                # save data contains an unknown selector value.
                selector_quads = tuple()
                if not bool(getattr(self, "_selector_overlay_error_logged", False)):
                    self._selector_overlay_error_logged = True
                    print("FIX66 selector overlay warning:", repr(exc))

        # FIX40 segmented custom scene.  Keep the proven single draw call but
        # publish independent GPU-resident layer buffers; never concatenate the
        # full terrain+water+environment scene into a Python tuple at 60 Hz.
        if use_streamed_scene:
            custom_scene_mode = True
            custom_terrain_count = len(custom_terrain_quads)
            custom_water_count = min(len(self._water_cache), int(METAL_MAX_ENV_QUADS))
            custom_environment_count = min(len(self._env_cache), int(METAL_MAX_ENV_QUADS))
            custom_dynamic_count = min(len(quads), int(METAL_DYNAMIC_MAX_QUADS))
        else:
            custom_scene_mode = False
            custom_terrain_count = 0
            custom_water_count = 0
            custom_environment_count = 0
            custom_dynamic_count = 0

        # FIX31 select one streamed giant boss for the global boss health bar.
        boss_visible=False; boss_name=""; boss_hp=0.0; boss_max_hp=1.0
        try:
            candidates=[]
            for _c in getattr(game,"creatures",()):
                if not bool(getattr(_c,"active",False)) or not bool(getattr(_c,"boss",False)):
                    continue
                ccx=int(_c.x//(TILE_SIZE*CHUNK_SIZE));ccy=int(_c.y//(TILE_SIZE*CHUNK_SIZE))
                if not game.chunk_streamer.is_active(ccx,ccy):
                    continue
                d2=(float(_c.x)-float(game.player.x))**2+(float(_c.y)-float(game.player.y))**2
                candidates.append((d2,_c))
            if candidates:
                candidates.sort(key=lambda row:row[0]);_boss=candidates[0][1]
                boss_visible=True
                boss_name=str(getattr(_boss,"boss_title","") or getattr(_boss,"name","Boss"))
                boss_hp=float(getattr(_boss,"hp",0.0));boss_max_hp=float(getattr(_boss,"max_hp",1.0))
        except Exception:
            pass

        event_message = game.events.pop_message()
        try:
            ui_command_pending = int(getattr(game.ui_commands, "pending", 0))
            ui_command_dropped = int(getattr(game.ui_commands, "dropped", 0))
        except Exception:
            ui_command_pending = 0
            ui_command_dropped = 0

        if not ENGINEERING_HUD_ENABLED:
            render_total_ms = (time.perf_counter() - render_t0) * 1000.0
            self.state.publish({
                'viewport_w': float(game.viewport_w),
                'viewport_h': float(game.viewport_h),
                'camera_x': float(render_camera_x),
                'camera_y': float(render_camera_y),
                'background_quads': background_quads,
                'terrain_batches': terrain_batches,
                'custom_terrain_key': custom_terrain_key,
                'custom_terrain_quads': custom_terrain_quads,
                'terrain_stream_stats': dict(getattr(self, '_terrain_stream_stats', {})),
                'water_batch': water_batch,
                'environment_batch': environment_batch,
                'custom_water_key': int(self._water_cache_generation),
                'custom_water_quads': self._water_cache,
                'custom_environment_key': int(self._env_cache_generation),
                'custom_environment_quads': self._env_cache,
                'dynamic_quads': tuple(quads[:METAL_DYNAMIC_MAX_QUADS]),
                'selector_quads': selector_quads,
                'custom_scene_mode': bool(custom_scene_mode),
                'custom_terrain_count': int(custom_terrain_count),
                'custom_water_count': int(custom_water_count),
                'custom_environment_count': int(custom_environment_count),
                'custom_dynamic_count': int(custom_dynamic_count),
                'terrain_gpu_uploads': int(self._terrain_gpu_uploads),
                'water_gpu_uploads': int(self._water_gpu_uploads),
            'environment_gpu_uploads': int(self._env_gpu_uploads),
                'player_x': float(render_player_x),
                'player_y': float(render_player_y),
                'player_state': str(p.state),
                'hp': float(p.hp),
                'stamina': float(p.stamina),
                'mana': float(p.mana),
                'boss_visible': bool(boss_visible), 'boss_name': boss_name, 'boss_hp': boss_hp, 'boss_max_hp': boss_max_hp,
                'magic': str(getattr(game.magic, 'selected_name', '?')),
                'magic_charge_seconds': float(getattr(game, 'magic_aim_charge_seconds', 0.0)),
                'weapon_charge_seconds': float(getattr(game, 'weapon_charge_seconds', 0.0)),
                'tool': str(getattr(getattr(game, 'tools', None), 'selected_name', '?')),
                'action_mode': str(getattr(game, 'action_mode_name', '工具')),
                'action_ms': float(getattr(game, 'action_debug', {}).get('last_ms', 0.0)),
                'ui_command_pending': int(ui_command_pending),
                'ui_command_dropped': int(ui_command_dropped),
                'message': event_message,
                'render_now': float(now),
                'render_total_ms': float(render_total_ms),
                'terrain_build_ms': float(terrain_build_ms),
                'rain_build_ms': float(rain_build_ms),
                'loop_stage': str(getattr(game, 'loop_stage', '?')),
            })
            return

        stats = game.environment_stats()
        audit_cache = getattr(game, "debug_audit", {})
        matter_audit = audit_cache.get("matter")
        energy_audit = audit_cache.get("energy")
        if matter_audit is None:
            matter_audit = game.environment.matter.audit(game)
        if energy_audit is None:
            energy_audit = game.environment.energy.snapshot()
        wind_x, wind_y = game.environment.wind_at_world(
            p.x,
            p.y,
        )
        game_time_text = game.time.formatted_time
        weather = game.environment.weather_at_world(
            p.x,
            p.y,
        )

        local_temp_c = (
            game.environment.temperature_at_world(
                p.x,
                p.y,
            )
        )

        climate_id = (
            game.environment.climate_at_world(
                p.x,
                p.y,
            )
        )

        player_tx = int(
            p.x
            // TILE_SIZE
        )
        player_ty = int(
            p.y
            // TILE_SIZE
        )

        nearby_water_temp_c = (
            game.environment.state.water_temperature_at(
                player_tx,
                player_ty,
                local_temp_c,
            )
        )

        physics_debug = dict(
            getattr(
                game,
                'physics_debug',
                {},
            )
        )

        jump_input_debug = (
            game.input.debug_action(
                "jump"
            )
            if hasattr(
                game.input,
                "debug_action",
            )
            else {}
        )

        left_input_debug = (
            game.input.debug_action(
                "move_left"
            )
            if hasattr(
                game.input,
                "debug_action",
            )
            else {}
        )

        right_input_debug = (
            game.input.debug_action(
                "move_right"
            )
            if hasattr(
                game.input,
                "debug_action",
            )
            else {}
        )

        move_axis_x = (
            int(
                game.input.axis_x()
            )
            if hasattr(
                game.input,
                "axis_x",
            )
            else 0
        )

        movement_debug = dict(
            getattr(
                p,
                "movement_debug",
                {},
            )
        )

        magic_ledger = (
            game.magic.ledger.snapshot()
        )

        player_chunk = (
            int(
                player_tx
                // CHUNK_SIZE
            ),
            int(
                player_ty
                // CHUNK_SIZE
            ),
        )

        local_updraft = float(
            game.environment.state.updraft.get(
                player_chunk,
                0.0,
            )
        )

        magic_available_h2o = (
            game.magic.available_condensable_vapor(
                p.x,
                p.y,
            )
        )

        # event_message was already consumed once above.

        self.state.publish({
            'viewport_w': float(game.viewport_w),
            'viewport_h': float(game.viewport_h),
            'camera_x': float(render_camera_x),
            'camera_y': float(render_camera_y),
            'background_quads': background_quads,
            'terrain_batches': terrain_batches,
            'custom_terrain_key': custom_terrain_key,
                'custom_terrain_quads': custom_terrain_quads,
                'terrain_stream_stats': dict(getattr(self, '_terrain_stream_stats', {})),
            'water_batch': water_batch,
            'environment_batch': environment_batch,
            'custom_water_key': int(self._water_cache_generation),
            'custom_water_quads': self._water_cache,
            'custom_environment_key': int(self._env_cache_generation),
            'custom_environment_quads': self._env_cache,
            'dynamic_quads': tuple(quads[:METAL_DYNAMIC_MAX_QUADS]),
            'selector_quads': selector_quads,
                'custom_scene_mode': bool(custom_scene_mode),
                'custom_terrain_count': int(custom_terrain_count),
                'custom_water_count': int(custom_water_count),
                'custom_environment_count': int(custom_environment_count),
                'custom_dynamic_count': int(custom_dynamic_count),
            'terrain_gpu_uploads': int(self._terrain_gpu_uploads),
            'water_gpu_uploads': int(self._water_gpu_uploads),
                'environment_gpu_uploads': int(self._env_gpu_uploads),
            'player_x': float(render_player_x),
            'player_y': float(render_player_y),
            'player_state': str(p.state),
            'hp': float(p.hp),
            'stamina': float(p.stamina),
            'mana': float(p.mana),
            'boss_visible': bool(boss_visible), 'boss_name': boss_name, 'boss_hp': boss_hp, 'boss_max_hp': boss_max_hp,
            'magic': str(getattr(game.magic, 'selected_name', '?')),
            'action_mode': str(getattr(game, 'action_mode_name', '工具')),
            'action_ms': float(getattr(game, 'action_debug', {}).get('last_ms', 0.0)),
            'ui_command_pending': int(ui_command_pending),
            'ui_command_dropped': int(ui_command_dropped),
            'magic_charge_level': int(
                getattr(
                    p,
                    'attack_charge_level',
                    1,
                )
            ),
            'magic_charge_seconds': float(
                getattr(
                    p,
                    'attack_charge_seconds',
                    0.0,
                )
            ),
            'magic_mana_spent': float(
                magic_ledger.get(
                    'mana_spent',
                    0.0,
                )
            ),
            'magic_water_created': float(
                magic_ledger.get(
                    'water_created_units',
                    0.0,
                )
            ),
            'magic_ice_created': float(
                magic_ledger.get(
                    'ice_created_units',
                    0.0,
                )
            ),
            'magic_fire_energy_created': float(
                magic_ledger.get(
                    'fire_energy_created',
                    0.0,
                )
            ),
            'magic_fire_evaporated': float(
                magic_ledger.get(
                    'fire_water_evaporated_units',
                    0.0,
                )
            ),
            'magic_fire_energy_evap_used': float(
                magic_ledger.get(
                    'fire_energy_evap_used',
                    0.0,
                )
            ),
            'local_updraft': float(
                local_updraft
            ),
            'magic_available_h2o': float(
                magic_available_h2o
            ),
            'message': event_message,
            'render_now': float(
                time.monotonic()
            ),
            'world_revision': int(getattr(game.world, 'revision', 0)),
            'water_count': int(stats.get('water',0)),
            'water_mass': float(game.environment.water.total_mass()),
            'water_total': float(matter_audit['water']['total']),
            'water_delta': float(matter_audit['water_delta']),
            'water_soil': float(matter_audit['water']['soil']),
            'water_vapor': float(matter_audit['water']['vapor']),
            'water_ground': float(matter_audit['water']['groundwater']),
            'water_ice': float(
                matter_audit['water'].get(
                    'ice',
                    0.0,
                )
            ),
            'water_projectile_mass': float(matter_audit['water']['projectile']),
            'local_temp_c': float(local_temp_c),
            'nearby_water_temp_c': float(nearby_water_temp_c),
            'climate': str(climate_id),
            'material_delta': float(matter_audit['material_delta']),
            'water_rain_added': float(
                getattr(
                    game.environment.water,
                    'last_debug',
                    {},
                ).get(
                    'rain_added',
                    0.0,
                )
            ),
            'fire_count': int(stats.get('fire',0)),
            'plant_count': int(stats.get('plants',0)),
            'fireball_count': int(stats.get('fireballs',0)),
            'waterball_count': int(stats.get('waterballs',0)),
            'iceball_count': int(stats.get('iceballs',0)),
            'electricball_count': int(stats.get('electricballs',0)),
            'weather': str(getattr(weather,'profile','clear')),
            'game_time': str(game_time_text),
            'elapsed_game_seconds': float(game.time.elapsed_game_seconds),
            'wind_x': float(wind_x),
            'wind_y': float(wind_y),
            'energy_stored': float(energy_audit['stored']),
            'energy_delta': float(energy_audit['stored_delta']),
            'energy_solar': float(energy_audit['flux'].get('solar_in', 0.0)),
            'energy_fire': float(energy_audit['flux'].get('fire_heat_in', 0.0)),
            'energy_wind': float(energy_audit['wind']),
            'physics_debug': physics_debug,
            'jump_input_debug': jump_input_debug,
            'left_input_debug': left_input_debug,
            'right_input_debug': right_input_debug,
            'move_axis_x': int(move_axis_x),
            'player_vx': float(getattr(p, 'vx', 0.0)),
            'player_vy': float(getattr(p, 'vy', 0.0)),
            'player_grounded': bool(
                getattr(
                    p,
                    'grounded',
                    False,
                )
            ),
            'player_facing': int(
                getattr(
                    p,
                    'facing',
                    1,
                )
            ),
            'player_run_mode': bool(
                getattr(
                    p,
                    'run_mode',
                    False,
                )
            ),
            'player_map_speed_multiplier': float(
                getattr(
                    p,
                    'map_speed_multiplier',
                    1.0,
                )
            ),
            'movement_debug': movement_debug,
            'input_backend_name': str(
                getattr(
                    game,
                    'input_backend_name',
                    '?',
                )
            ),
        })

    def _schedule_monitor_apply(self, callback):
        """Coalesce cosmetic HUD work to one outstanding main-thread task."""
        if callback is None:
            return False
        with self._monitor_apply_lock:
            self._monitor_apply_latest = callback
            if self._monitor_apply_pending:
                return True
            self._monitor_apply_pending = True

        def run_latest():
            with self._monitor_apply_lock:
                fn = self._monitor_apply_latest
                self._monitor_apply_latest = None
                self._monitor_apply_pending = False
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass

        try:
            mainthread.run_async(run_latest)
            return True
        except Exception:
            with self._monitor_apply_lock:
                self._monitor_apply_pending = False
            return False

    def _apply_message_snapshot(self, snap):
        """Apply one latched EventBus toast serial on UIKit's main thread."""
        if not isinstance(snap, dict):
            return False
        message = snap.get("message")
        try:
            serial = int(snap.get("message_serial", 0))
        except Exception:
            serial = 0
        now = time.monotonic()
        if message and serial != int(self._last_message_serial):
            self._last_message_serial = serial
            self._last_message = str(message)
            self.message_label.text = self._last_message
            self.message_label.hidden = False
            self._message_until = now + 2.8
            return True
        if not self.message_label.hidden and now >= self._message_until:
            self.message_label.hidden = True
        return False

    def _monitor(self):
        while self.state.running:
            if not bool(getattr(self, "_ui_layout_ready", False)):
                time.sleep(0.05)
                continue
            s = self.state
            snap = s.get_snapshot()
            try:
                device_name = str(s.device.name) if s.device is not None else '-'
            except Exception:
                device_name = '?'

            if not ENGINEERING_HUD_ENABLED:
                def apply_fast(snap=snap):
                    try:
                        if bool(getattr(self, "_inventory_hud_hidden", False)):
                            return
                        if snap is None:
                            return

                        self._apply_message_snapshot(snap)

                        self._update_bar(
                            self.hp_bg, self.hp_fill, self.hp_text,
                            "HP", snap["hp"], MAX_HP,
                        )
                        self._update_bar(
                            self.st_bg, self.st_fill, self.st_text,
                            "ST", snap["stamina"], MAX_STAMINA,
                        )
                        self._update_bar(
                            self.mp_bg, self.mp_fill, self.mp_text,
                            "MP", snap["mana"], MAX_MANA,
                        )
                        _bv=bool(snap.get("boss_visible",False))
                        self.boss_bg.hidden=not _bv
                        if _bv:
                            self._update_boss_bar(self.boss_bg,self.boss_fill,self.boss_text,
                                snap.get("boss_name","Boss"),snap.get("boss_hp",0.0),snap.get("boss_max_hp",1.0))
                    except Exception:
                        pass

                self._schedule_monitor_apply(apply_fast)

                time.sleep(METAL_STATUS_UPDATE_SECONDS)
                continue

            if snap is None:
                lines = [
                    'Waiting for game snapshot...',
                    '',
                    '',
                    '',
                ]
            else:
                pd = snap.get('physics_debug', {})
                lines = [
                    '%s | T %.1fC | %s/%s | Wind=(%.0f,%.0f) Lift=%.0f' % (
                        snap['game_time'],
                        snap['local_temp_c'],
                        snap['climate'],
                        snap['weather'],
                        snap['wind_x'],
                        snap['wind_y'],
                        max(
                            0.0,
                            -snap['wind_y'],
                        )),
                    'Magic=%s Charge=L%d %.2fs | MPused=%.0f | W+=%.0fu I+=%.0fu F-E+=%.0f' % (
                        snap.get('magic','?'),
                        snap.get('magic_charge_level',1),
                        snap.get('magic_charge_seconds',0.0),
                        snap.get('magic_mana_spent',0.0),
                        snap.get('magic_water_created',0.0),
                        snap.get('magic_ice_created',0.0),
                        snap.get('magic_fire_energy_created',0.0)),
                    'Fire evap=%.1fu | Eevap=%.0f | World H2O(open)=%.1f free=%.1f soil=%.1f' % (
                        snap.get('magic_fire_evaporated',0.0),
                        snap.get('magic_fire_energy_evap_used',0.0),
                        snap['water_total'],
                        snap['water_mass'],
                        snap['water_soil']),
                    'P=(%.0f,%.0f) %s Vx=%.0f AX=%d L%d R%d | J D%d P%d R%d H%.2f q%d/%d' % (
                        snap['player_x'],
                        snap['player_y'],
                        snap['player_state'],
                        float(snap.get('player_vx',0.0)),
                        int(snap.get('move_axis_x',0)),
                        int(snap.get('left_input_debug',{}).get('frame_down',False)),
                        int(snap.get('right_input_debug',{}).get('frame_down',False)),
                        int(snap.get('jump_input_debug',{}).get('frame_down',False)),
                        int(snap.get('jump_input_debug',{}).get('pressed',False)),
                        int(snap.get('jump_input_debug',{}).get('released',False)),
                        float(snap.get('jump_input_debug',{}).get('held_seconds',0.0)),
                        int(snap.get('jump_input_debug',{}).get('press_seq',0)),
                        int(snap.get('jump_input_debug',{}).get('release_seq',0))),
                ]

            if snap is None:
                movement_text = (
                    "MOVE DEBUG | waiting for snapshot..."
                )
            else:
                ld = snap.get(
                    "left_input_debug",
                    {},
                )
                rd = snap.get(
                    "right_input_debug",
                    {},
                )
                md = snap.get(
                    "movement_debug",
                    {},
                )

                movement_text = (
                    "MOVE %s | AX=%+d Vx=%+.1f Req=%+.1f Hit=%d State=%s G=%d\n"
                    "LEFT  %-4s live=%d frame=%d P=%d R=%d H=%.3f q=%d/%d | EVT=%s age=%.2f\n"
                    "      TD=%d UI=%d UO=%d CX=%d DX=%d DE=%d FR=%d\n"
                    "RIGHT %-4s live=%d frame=%d P=%d R=%d H=%.3f q=%d/%d | EVT=%s age=%.2f\n"
                    "      TD=%d UI=%d UO=%d CX=%d DX=%d DE=%d FR=%d\n"
                    "SPD base=%.1f eff=%.1f map=%.2f run=%d face=%s | WindX=%+.1f drift=%+.1f\n"
                    "POS X=%.1f Y=%.1f | V=(%+.1f,%+.1f) | CamX=%.1f"
                ) % (
                    snap.get(
                        "input_backend_name",
                        "?",
                    ),
                    int(
                        snap.get(
                            "move_axis_x",
                            0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_vx",
                            0.0,
                        )
                    ),
                    float(
                        md.get(
                            "requested_vx",
                            0.0,
                        )
                    ),
                    int(
                        bool(
                            md.get(
                                "hit_horizontal",
                                False,
                            )
                        )
                    ),
                    snap.get(
                        "player_state",
                        "?",
                    ),
                    int(
                        bool(
                            snap.get(
                                "player_grounded",
                                False,
                            )
                        )
                    ),
                    str(
                        ld.get(
                            "mode",
                            "OFF",
                        )
                    ),
                    int(
                        bool(
                            ld.get(
                                "live_down",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            ld.get(
                                "frame_down",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            ld.get(
                                "pressed",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            ld.get(
                                "released",
                                False,
                            )
                        )
                    ),
                    float(
                        ld.get(
                            "live_held_seconds",
                            0.0,
                        )
                    ),
                    int(
                        ld.get(
                            "press_seq",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "release_seq",
                            0,
                        )
                    ),
                    str(
                        ld.get(
                            "platform_last_event",
                            "",
                        )
                    ),
                    float(
                        ld.get(
                            "platform_event_age",
                            0.0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_touch_down",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_up_inside",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_up_outside",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_cancel",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_drag_exit",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_drag_enter",
                            0,
                        )
                    ),
                    int(
                        ld.get(
                            "platform_force_release",
                            0,
                        )
                    ),
                    str(
                        rd.get(
                            "mode",
                            "OFF",
                        )
                    ),
                    int(
                        bool(
                            rd.get(
                                "live_down",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            rd.get(
                                "frame_down",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            rd.get(
                                "pressed",
                                False,
                            )
                        )
                    ),
                    int(
                        bool(
                            rd.get(
                                "released",
                                False,
                            )
                        )
                    ),
                    float(
                        rd.get(
                            "live_held_seconds",
                            0.0,
                        )
                    ),
                    int(
                        rd.get(
                            "press_seq",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "release_seq",
                            0,
                        )
                    ),
                    str(
                        rd.get(
                            "platform_last_event",
                            "",
                        )
                    ),
                    float(
                        rd.get(
                            "platform_event_age",
                            0.0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_touch_down",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_up_inside",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_up_outside",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_cancel",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_drag_exit",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_drag_enter",
                            0,
                        )
                    ),
                    int(
                        rd.get(
                            "platform_force_release",
                            0,
                        )
                    ),
                    float(
                        md.get(
                            "base_speed",
                            0.0,
                        )
                    ),
                    float(
                        md.get(
                            "effective_speed",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_map_speed_multiplier",
                            1.0,
                        )
                    ),
                    int(
                        bool(
                            snap.get(
                                "player_run_mode",
                                False,
                            )
                        )
                    ),
                    (
                        "R"
                        if int(
                            snap.get(
                                "player_facing",
                                1,
                            )
                        ) >= 0
                        else "L"
                    ),
                    float(
                        md.get(
                            "wind_x",
                            snap.get(
                                "wind_x",
                                0.0,
                            ),
                        )
                    ),
                    float(
                        md.get(
                            "wind_drift",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_x",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_y",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_vx",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "player_vy",
                            0.0,
                        )
                    ),
                    float(
                        snap.get(
                            "camera_x",
                            0.0,
                        )
                    ),
                )

            text = (
                'V%s | %s | GPU %s | FPS %.1f Q%d | Err %d%s\n'
                '%s\n%s\n%s\n%s'
            ) % (
                VERSION, s.stage, device_name, s.fps, s.last_quad_count,
                s.draw_errors, (' '+s.last_error[:55]) if s.last_error else '',
                lines[0], lines[1], lines[2], lines[3],
            )

            def apply(
                text=text,
                movement_text=movement_text,
                snap=snap,
            ):
                try:
                    if bool(getattr(self, "_inventory_hud_hidden", False)):
                        return
                    self.status.text = text
                    self.movement_status.text = (
                        movement_text
                    )

                    if snap is not None:
                        self._apply_message_snapshot(snap)

                        self._update_bar(
                            self.hp_bg, self.hp_fill, self.hp_text,
                            "HP", snap["hp"], MAX_HP,
                        )
                        self._update_bar(
                            self.st_bg, self.st_fill, self.st_text,
                            "ST", snap["stamina"], MAX_STAMINA,
                        )
                        self._update_bar(
                            self.mp_bg, self.mp_fill, self.mp_text,
                            "MP", snap["mana"], MAX_MANA,
                        )
                        _bv=bool(snap.get("boss_visible",False))
                        self.boss_bg.hidden=not _bv
                        if _bv:
                            self._update_boss_bar(self.boss_bg,self.boss_fill,self.boss_text,
                                snap.get("boss_name","Boss"),snap.get("boss_hp",0.0),snap.get("boss_max_hp",1.0))
                except Exception:
                    pass
            self._schedule_monitor_apply(apply)
            time.sleep(METAL_STATUS_UPDATE_SECONDS)

    def close(self):
        self.state.running = False
        try:
            with self.state.display_condition:
                self.state.display_condition.notify_all()
        except Exception:
            pass
        try:
            self.metal.objc_view.paused = True
        except Exception:
            pass
