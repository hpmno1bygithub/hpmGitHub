# -*- coding: utf-8 -*-
"""Pyto RPG V0.2 configuration."""

VERSION = '0.7.7.7-FIX131-creature-ecology-elements'

# World
TILE_SIZE = 40
# Regular fixed-mapping art keeps the 16px tile density (40/16 = 2.5 world
# units per authored pixel).  FIX111+ fit_world_box actors are different: their
# source resolution is authoring detail, while collision/world size is mapped
# separately.  FIX116 therefore allows a true native 96x96 boss source without
# making the boss three times larger in gameplay.
PIXEL_ART_BASE_RESOLUTION = 16
PIXEL_WORLD_SCALE = float(TILE_SIZE) / float(PIXEL_ART_BASE_RESOLUTION)
CHUNK_SIZE = 16
WORLD_WIDTH_TILES = 96
WORLD_HEIGHT_TILES = 18
GROUND_ROW = 12

# Game loop
TARGET_FPS = 60
MAX_FRAME_DT = 0.05

# FIX77 optional Cython/C++ acceleration. The game always retains the existing
# NumPy/Python code path and automatically falls back when the extension has not
# been built, cannot be imported, or fails a runtime validation check.
NATIVE_ACCELERATION_ENABLED = True

# V0.6.1: the simulation no longer free-runs on Python time.sleep().
# MTKView/VSync pulses pace the background simulation thread.
DISPLAY_SYNC_GAME_LOOP = True
DISPLAY_SYNC_WAIT_TIMEOUT = 0.25
# Small phase tolerance prevents 16.5/16.8 ms VSync jitter from producing
# an alternating 0-step / 2-step fixed-update pattern.
DISPLAY_SYNC_STEP_THRESHOLD = 0.70

# Player
GRAVITY = 1250.0
WALK_SPEED = 145.0
RUN_SPEED = 235.0
AIR_CONTROL = 0.85

# V0.6.5.5 horizontal response shaping. Input is received immediately, but
# velocity approaches the requested speed over a few 60 Hz physics frames so
# movement starts/stops continuously instead of stepping 0 -> full speed.
PLAYER_MOVE_ACCEL_GROUND = 1800.0
PLAYER_MOVE_DECEL_GROUND = 2600.0
PLAYER_MOVE_TURN_ACCEL = 3400.0
PLAYER_MOVE_ACCEL_AIR = 1400.0
PLAYER_MOVE_DECEL_AIR = 1800.0
JUMP_SPEED = 455.0

# V0.7.6.1 fully-submerged swimming. Repeated jump presses become discrete
# upward kicks while water drag keeps sinking much slower than in air.
PLAYER_SWIM_KICK_SPEED = 225.0
PLAYER_SWIM_GRAVITY_SCALE = 0.22
PLAYER_SWIM_VERTICAL_DRAG_PER_SECOND = 2.6
PLAYER_SWIM_MAX_SINK_SPEED = 150.0
PLAYER_SWIM_MAX_RISE_SPEED = 265.0

# FIX37: magma is a real dense liquid body, not only a rendered hazard.
# A character can enter it, but deep lava lifts the feet toward the free
# surface instead of allowing normal walking on the rock floor underneath.
PLAYER_LAVA_MOVE_SPEED_SCALE = 0.30
PLAYER_LAVA_SUPPORT_MIN_DEPTH_PX = 8.0
PLAYER_LAVA_SURFACE_IMMERSION_PX = 5.0
PLAYER_LAVA_BUOYANCY_RISE_SPEED = 190.0

CLIMB_SPEED = 105.0
WALL_STAMINA_COST = 18.0
STAMINA_RECOVERY = 16.0

ROLL_SPEED = 355.0
ROLL_TIME = 0.38

MAX_HP = 100.0
MAX_STAMINA = 100.0
MAX_MANA = 100.0

# V0.7.2.5 close-range interaction / melee / creature combat
# Item pickup is intentionally much shorter than NPC talk range.  A world
# pickup should require walking to the drop instead of vacuuming loot from
# multiple tiles away.
ITEM_PICKUP_RADIUS_PX = TILE_SIZE * 1.10
NPC_INTERACT_RADIUS_PX = TILE_SIZE * 2.35

# Axe is a true melee tool.  Reach is measured from the front edge of the
# player's body and extends exactly one terrain tile in player.facing.
AXE_FRONT_REACH_TILES = 1.00
MELEE_FRONT_REACH_TILES = 1.00

# FIX67 weapon-impact presentation budget.  Combat systems publish a small,
# bounded semantic packet when an authoritative hit occurs.  Presentation
# layers may use it for hit-stop, camera impulse, particles, and sound gain;
# the packet never decides whether damage happened.  The hard caps keep a
# dense arrow rain or ricocheting laser from stacking unbounded feedback.
WEAPON_HIT_STOP_MAX_SECONDS = 0.090
WEAPON_CAMERA_IMPULSE_MAX_PX = 6.0
WEAPON_IMPACT_FEEDBACK_LIMIT = 8
WEAPON_IMPACT_FEEDBACK_TTL = 0.30
WEAPON_IMPACT_COALESCE_SECONDS = 0.045

# Hostile creature combat.  Detection may be wider, but damage requires body
# contact / a tiny contact margin and respects a short player hurt immunity.
CREATURE_AGGRO_RADIUS_PX = TILE_SIZE * 6.25
CREATURE_AGGRO_VERTICAL_PX = TILE_SIZE * 2.25
# FIX65 prone stealth.  A player who was already prone before an enemy became
# aware is ignored until roughly body-to-body range.  Once awareness is set on
# that creature, changing posture cannot clear it.
CREATURE_PRONE_DETECTION_RADIUS_PX = TILE_SIZE * 1.55
CREATURE_PRONE_DETECTION_VERTICAL_PX = TILE_SIZE * 1.40
CREATURE_ATTACK_CONTACT_MARGIN_PX = 0.0
CREATURE_ATTACK_COOLDOWN_SECONDS = 1.25
PLAYER_HURT_INVULNERABILITY_SECONDS = 0.32
CREATURE_HIT_KNOCKBACK_SPEED = 155.0

# Direct magic damage to targetable creatures, indexed by charge level 1..3.
# World element effects still run independently when a projectile hits terrain.
MAGIC_CREATURE_DAMAGE_FIRE = (18.0, 28.0, 42.0)
MAGIC_CREATURE_DAMAGE_WATER = (7.0, 12.0, 18.0)
MAGIC_CREATURE_DAMAGE_ICE = (12.0, 20.0, 30.0)
MAGIC_CREATURE_DAMAGE_ELECTRIC = (14.0, 24.0, 38.0)
MAGIC_CREATURE_KNOCKBACK = 115.0
MAGIC_CREATURE_ICE_SLOW_SECONDS = (0.55, 0.85, 1.20)
MAGIC_CREATURE_ELECTRIC_STUN_SECONDS = (3.0, 3.0, 3.0)

# FIX131 five-element creature combat:
#   WATER > FIRE; FIRE > EARTH and ICE; EARTH > ELECTRIC; ELECTRIC > WATER.
# Advantage = 2.0x, reverse matchup = 0.5x, unrelated = 1.0x.
MAGIC_ELEMENT_ADVANTAGE_MULTIPLIER = 2.00
MAGIC_ELEMENT_RESIST_MULTIPLIER = 0.50

# Newly loaded sessions get a brief no-damage grace period.  This preserves
# the HP value stored in a save but prevents hazards/hostile actors from
# subtracting HP before the first visible controllable frame.
PLAYER_STARTUP_DAMAGE_GRACE_SECONDS = 2.0

# V0.5.1
MANA_RECOVERY_PER_SECOND = 10.0

# Continuous variable-height jump.
#
# Jump starts immediately with the minimum impulse. While the button remains
# held, upward lift is continuously accumulated. Therefore every longer hold
# time produces a higher jump until the maximum hold time is reached.
JUMP_MIN_INITIAL_SCALE = 0.70
JUMP_HOLD_MAX_SECONDS = 0.45

# A press slightly before the grounded flag settles is remembered briefly.
JUMP_INPUT_BUFFER_SECONDS = 0.12
JUMP_HOLD_BASE_ACCEL = 560.0
JUMP_HOLD_RAMP_ACCEL = 380.0

# Prevent visible gaps between adjacent Metal tile quads.
TILE_RENDER_OVERLAP = 0.60

# Renderer
MAX_VISIBLE_TILE_VIEWS = 320


# Chunk streaming
ACTIVE_CHUNK_RADIUS_X = 2
ACTIVE_CHUNK_RADIUS_Y = 1

# Save
SAVE_SLOT_NAME = "save_01.json"



# V0.4.1 Environment + Magic
WATER_HZ = 30.0
FIRE_HZ = 5.0
SOIL_HZ = 3.0
PLANT_HZ = 1.0
WEATHER_HZ = 1.0
WEATHER_BACKGROUND_SNOW_HZ = 0.5
NPC_AI_HZ = 5.0
MAX_WATER_VIEWS = 96
MAX_FIRE_VIEWS = 48
MAX_SOIL_VIEWS = 96
MAX_PLANT_VIEWS = 64
MAX_PHYSICS_OBJECT_VIEWS = 16
MAX_FIREBALL_VIEWS = 12
RAIN_ADD_PER_STEP = 0.12
BASE_EVAPORATION = 0.0035
FIRE_WATER_EXTINGUISH = 0.35
FIRE_RAIN_EXTINGUISH = 0.12
FIRE_SPREAD_CHANCE = 0.16
SOIL_ABSORB_RATE = 0.18
SOIL_DRAIN_RATE = 0.007
SOIL_EVAP_RATE = 0.012
WEATHER_PROFILE_SECONDS = 30.0

# Water tuning for smoother / less floaty behavior.
# V0.7.7.1: liquid physics runs at 30 Hz while Metal keeps rendering at display rate.
# Tile-liquid games generally do not need a 60 Hz full liquid solve; visual
# interpolation hides the lower simulation cadence and halves the hottest
# hydrology workload on Pyto/iPhone.
WATER_FALL_FLOW = 0.96
WATER_SIDE_FLOW = 0.82
# V0.7.7.4: adjacent water columns may expose their free surface in
# different tile rows at a sloped lake bank/cavity.  A cheap O(width) bridge
# exchanges only the two *surface cells*, so the body levels quickly without
# restoring the expensive/unsafe whole-basin redistribution pass.
WATER_SURFACE_BRIDGE_RELAX = 0.86
WATER_SURFACE_BRIDGE_MAX_FLOW = 0.38
WATER_SURFACE_BRIDGE_EPSILON = 0.010
# V0.7.6.9: NumPy liquid uses a few cheap C-vectorized relaxation substeps.
# This lets a newly opened shoreline shaft settle to a level free surface
# instead of freezing into a visible staircase while preserving mass.
WATER_SOLVER_SUBSTEPS = 1
WATER_LATERAL_SUPPORT_THRESHOLD = 0.42
WATER_DIAGONAL_FALL_FLOW = 0.78
WATER_SPAN_RELAX = 0.0
WATER_SPAN_MAX_TILES = 32
# V0.7.6.2: a floating ICE barrier must not behave like a sealed dam.
# When liquid is demonstrably connected underneath the ice, this virtual
# pressure bridge moves mass across the surface barrier while conserving
# total water. It only applies to ICE and never to soil pore-water.
WATER_FLOATING_ICE_PRESSURE_RELAX = 0.82
WATER_FLOATING_ICE_PRESSURE_MAX_FLOW = 0.34
WATER_FLOATING_ICE_PRESSURE_MIN_COLUMN = 0.12

# V0.7.6.3 renderer-only ocean surface waves.  The previous values were
# mostly clipped at the top edge of the surface tile, so crests looked nearly
# flat even though the sine amplitude was non-zero.  The renderer now allows
# a crest to rise into the empty tile above; these larger values remain visual
# only and never modify authoritative env.water mass.
OCEAN_WAVE_AMPLITUDE_PX = 10.0
OCEAN_WAVE_SPEED = 1.35
OCEAN_WAVE_WAVENUMBER = 0.22
OCEAN_WAVE_SECONDARY_AMPLITUDE_PX = 4.2
OCEAN_WAVE_SECONDARY_SPEED = 2.15
OCEAN_WAVE_SECONDARY_WAVENUMBER = -0.12
OCEAN_WAVE_MAX_CREST_PX = 15.0
# Connected free-water surfaces inherit ocean wave energy across biome edges.
# Distance is measured through the connected water cells in the visible active
# region, so flooded side pools/tunnels receive a damped continuation rather
# than an abrupt wave/no-wave border.
CONNECTED_WAVE_DECAY_PER_TILE = 0.035
CONNECTED_WAVE_MIN_GAIN = 0.28
CONNECTED_WAVE_PHASE_LAG_PER_TILE = 0.10
CONNECTED_WAVE_SEARCH_MARGIN_TILES = 12
WATER_MIN_VISIBLE = 0.12
RAIN_SOIL_DIRECT = 0.050
RAIN_SURFACE_SPILL = 0.060

# Fireball magic
FIREBALL_MANA_COST = 8.0
FIREBALL_SPEED = 380.0
FIREBALL_LIFETIME = 1.25
FIREBALL_POWER = 1.0
FIREBALL_GRAVITY = 460.0
FIREBALL_MAX_FALL_SPEED = 620.0

# Soil burning
SOIL_FIRE_WET_LIMIT = 0.62
SOIL_LATERAL_SPREAD_FACTOR = 0.18

# V0.4.3 basic fireball burns through only the directly hit tile.
FIREBALL_BURN_PENETRATION = 0


# V0.4.4 direct fireball ignition vs. soil moisture.
# Ambient fire remains conservative (SOIL_FIRE_WET_LIMIT), but a direct
# fireball hit can ignite moderately damp soil unless it is nearly saturated.
FIREBALL_SOIL_WET_LIMIT = 1.01
FIREBALL_SOIL_DRY_ON_HIT = 0.18
FIREBALL_SOIL_EVAPORATE_TO = 0.30

# Vertical camera dead-zone.  Player may jump inside the zone without making
# the camera bob; falling below the lower line makes the camera follow down.
CAMERA_VERTICAL_UPPER = 0.30
CAMERA_VERTICAL_LOWER = 0.67
CAMERA_VERTICAL_FOLLOW_SPEED = 7.0


# V0.4.5 waterball / magic switch
WATERBALL_MANA_COST = 6.0
WATERBALL_SPEED = 350.0
WATERBALL_LIFETIME = 1.55
WATERBALL_POWER = 1.0
WATERBALL_GRAVITY = 520.0
WATERBALL_MAX_FALL_SPEED = 700.0
WATERBALL_AMOUNT = 1.0
WATERBALL_SPLASH_RADIUS = 1

# V0.7.2 Electric-ball magic
# Electric magic creates charge/energy, not matter.  The projectile itself is
# ballistic like the existing elemental balls, while impact propagation is
# delegated to ElectricalSystem so water, wet soil and conductive ores all use
# the same bounded network rules.
ELECTRICBALL_MANA_COST = 9.0
ELECTRICBALL_SPEED = 430.0
ELECTRICBALL_LIFETIME = 1.35
ELECTRICBALL_POWER = 1.0
ELECTRICBALL_GRAVITY = 300.0
ELECTRICBALL_MAX_FALL_SPEED = 560.0
ELECTRICBALL_CHARGE = 1.20
ELECTRICBALL_ENERGY_UNITS = 90.0
ELECTRICBALL_WATER_SHOCK_SCALE = 1.35
ELECTRICBALL_DRY_SHOCK_SCALE = 0.60

# V0.7.2.1 Level-3 electrolysis / poison-gas rules.
# Only a fully charged level-3 electric ball can electrolyse standing water.
# The removed water mass is converted into a sparse poison-gas quantity, so
# the visible effect never creates matter from nothing.
ELECTRICBALL_L3_ELECTROLYSIS_WATER = 0.45
ELECTRICBALL_L3_POISON_GAS_YIELD = 1.0
POISON_GAS_LIFETIME_SECONDS = 8.0
POISON_GAS_FIREBALL_CHAIN_MAX_CELLS = 96
POISON_GAS_EXPOSURE_THRESHOLD = 0.025
POISON_GAS_DOSE_RISE_PER_SEC = 0.95
POISON_GAS_DOSE_DECAY_PER_SEC = 0.55
POISON_GAS_DAMAGE_MIN_PER_SEC = 0.25
POISON_GAS_DAMAGE_MAX_PER_SEC = 3.20
TOXIC_SYSTEM_HZ = 10.0


# V0.4.6 soil saturation / germination tuning
SOIL_SATURATION = 1.0
PLANT_GERMINATION_GRASS = 0.55
PLANT_GERMINATION_BERRY = 0.72
PLANT_STAGE_STEP = 0.45


# Metal renderer migration - Stage 1
USE_METAL_RENDERER = True
METAL_MAX_QUADS = 4096
METAL_STATUS_UPDATE_SECONDS = 0.35


# V0.4.8 fixed-step physics safety
# V0.6.0: keep gameplay simulation aligned with the 60 Hz presentation loop.
# The former 120 Hz Python loop doubled bridge/collision/update overhead and
# could enter multi-step catch-up bursts after one slow frame.  A 60 Hz fixed
# step remains deterministic while leaving substantially more CPU headroom for
# Metal submission, touch handling and future sprite assets.
PHYSICS_FIXED_HZ = 60.0
PHYSICS_MAX_CATCHUP_STEPS = 2
PHYSICS_MAX_ACCUMULATOR = 0.05
# Collision alone may sub-step when a body travels farther than this in one
# 60 Hz update. This replaces the old strategy of running the entire game at
# 120 Hz just to keep fast rigid-body travel small.
PHYSICS_COLLISION_SUBSTEP_PX = 8.0
PHYSICS_BOX_PUSH_ACCEL = 720.0
PHYSICS_OBJECT_MAX_SPEED_X = 700.0
PHYSICS_OBJECT_MAX_SPEED_Y = 900.0

# Metal Stage 2 environment visual budget
METAL_WATER_MIN_VISIBLE = 0.025
METAL_MAX_ENV_QUADS = 1400

# V0.6.0 GPU-resident rendering. Terrain chunk buffers are created once and
# replaced only when that chunk revision changes. Environment is uploaded at
# its own lower visual cadence; only small actor/projectile data is streamed
# every display frame.
METAL_TERRAIN_GPU_CHUNK_CACHE_LIMIT = 32
# V0.6.2.2: reliable custom-map terrain stream. These buffers are allocated
# together with the renderer on the iOS/Metal setup thread, then reused in a
# triple-buffer ring. The visible terrain batch is rebuilt only when the set of
# visible chunks or one of their revisions changes; normal sub-chunk camera
# movement does not re-upload terrain every frame.
METAL_TERRAIN_STREAM_MAX_QUADS = 8192
# FIX100: 8192 is the INITIAL allocation, not a row-major scene truncation.
# Cull to a padded viewport first; grow the terrain ring only when needed.
# Ceiling: 3 x 65536 x 32 bytes = 6 MiB of terrain GPU buffers, never per tile.
METAL_TERRAIN_STREAM_HARD_LIMIT = 65536
# FIX62 includes a 24-slot modal inventory in the same existing dynamic batch.
# A bounded instance buffer covers the ordinary gameplay batch plus detailed
# fully populated page (slot frames, icons, capped counts and every action row)
# without truncating the controls appended after the slots.
METAL_DYNAMIC_MAX_QUADS = 8192  # FIX104 bounded detailed-boss batch (no per-pixel UIKit)
METAL_BACKGROUND_MAX_QUADS = 256

# V0.7.3 imported artwork.  The textured layer is optional: when registry.json
# has no valid bindings, the renderer follows the exact geometric path used by
# the previous build.  Each bound image receives its own offset-0 triple buffer
# to avoid the Rubicon/Metal tail-offset issue seen on real iPhones.
METAL_TEXTURE_ASSETS_ENABLED = False
METAL_TEXTURE_MAX_ASSETS_PER_FRAME = 32
METAL_TEXTURE_MAX_INSTANCES_PER_ASSET = 512
METAL_TEXTURE_TILE_MARGIN_TILES = 1

# Low-frequency console telemetry for real-device Pyto profiling.  This is
# intentionally sparse so logging itself cannot become the performance issue.
PERF_RUNTIME_LOG_SECONDS = 5.0


# V0.4.9 water physics / render coupling
# Free-falling water should mostly move vertically instead of fanning sideways
# through the air. Sideways flow begins after the water is supported by ground
# or a nearly-full water cell below it.
WATER_SUPPORT_THRESHOLD = 0.985
WATER_FLOW_EPSILON = 0.004
WATER_RENDER_SEGMENTS = 1
WATER_STREAM_WIDTH = 0.34


# ============================================================
# V0.4.10 Terraria-like liquid settling
# ============================================================

# A cell above another water cell should not become a stable upper layer
# until the lower layer is essentially full.
WATER_STACK_THRESHOLD = 0.985

# Before keeping water in an upper row, search the contiguous lower-row
# water front for the nearest vacancy and feed it first.
WATER_LOWER_SETTLE_FLOW = 0.44
WATER_LOWER_SCAN_RADIUS = 8

# Pair-wise horizontal relaxation on a supported row.
WATER_LEVEL_RELAX = 0.92

# Rendering: physical water stays tile-volume based; these parameters only
# control Metal presentation and never create/destroy water mass.
WATER_SURFACE_SLOPE_LIMIT = 0.06
WATER_FALL_PARTICLE_COUNT = 3
WATER_FALL_PARTICLE_SPEED = 170.0
WATER_RAIN_PARTICLES_PER_CHUNK = 9

# Rain constants above are interpreted as PER-SECOND rates in V0.4.10.
# V0.4.9 accidentally applied them once per water tick, so increasing
# WATER_HZ multiplied rainfall and made water appear to overflow forever.


# ============================================================
# V0.4.11 conserved matter / porous soil
# ============================================================

# In this game model, 1.0 water unit is the amount required to fill one
# 40x40 liquid tile or to raise one soil tile from 0% to 100% moisture.
SOIL_INITIAL_MOISTURE = 0.20
SOIL_FIELD_CAPACITY = 0.35

# Porous-flow rates are per second. Gravity is intentionally stronger
# than lateral capillary flow.
SOIL_VERTICAL_PERCOLATION_RATE = 0.42
SOIL_LATERAL_CAPILLARY_RATE = 0.0  # V0.7.6.1: pore water is vertical-only
SOIL_GROUNDWATER_RATE = 0.012
SOIL_LATERAL_MIN_DIFFERENCE = 0.035

# Atmospheric-water reservoir per world chunk.
# Rain/environment systems may use it. V0.4.15 water/ice magic is a
# mana-created boundary source and no longer requires atmospheric vapor.
ATMOSPHERIC_WATER_INITIAL_PER_CHUNK = 12.0

# Legacy V0.4.11 condensation threshold. Kept for backward-compatible
# test/config imports; V0.4.15 spell casting does not use it.
WATERBALL_MIN_CAST_MASS = 0.999

# Solid-matter accounting. Burned terrain is converted into ash + smoke.
MATTER_AUDIT_TOLERANCE = 1e-5

# Finite organic/carbon reservoir used by plant growth. New biomass is a
# transfer from this reservoir, not spontaneous matter creation.
ORGANIC_MATTER_INITIAL_PER_CHUNK = 4.0
PLANT_SEED_BIOMASS = 0.035
PLANT_BIOMASS_PER_GROWTH = 0.055
PLANT_FRUIT_MASS = 0.015


# ============================================================
# V0.4.12 Thermal simulation / climate / ice magic
# ============================================================

THERMAL_HZ = 2.0
THERMAL_ACTIVE_HALO_TILES = 2

# Region climate is assigned per chunk. The same sun does not mean the same
# thermal target in Antarctica, rainforest, temperate land, and desert.
CLIMATE_PROFILES = {
    "polar": {
        "base_temp_c": -18.0,
        "solar_gain_c": 5.0,
        "night_temp_c": -25.0,
        "humidity": 0.45,
    },
    "alpine": {
        "base_temp_c": -4.0,
        "solar_gain_c": 8.0,
        "night_temp_c": -10.0,
        "humidity": 0.40,
    },
    "temperate": {
        "base_temp_c": 18.0,
        "solar_gain_c": 11.0,
        "night_temp_c": 12.0,
        "humidity": 0.55,
    },
    "rainforest": {
        "base_temp_c": 25.0,
        "solar_gain_c": 7.0,
        "night_temp_c": 22.0,
        "humidity": 0.88,
    },
    "desert": {
        "base_temp_c": 32.0,
        "solar_gain_c": 22.0,
        "night_temp_c": 13.0,
        "humidity": 0.12,
    },
}

# Test-world climate map by chunk X. This is also the map-authoring interface:
# change these profile IDs when designing regions.
CLIMATE_PROFILE_BY_CHUNK_X = {
    0: "temperate",
    1: "rainforest",
    2: "rainforest",
    3: "desert",
    4: "alpine",
    5: "polar",
}

# Ambient thermal dynamics.
THERMAL_DIFFUSION_RATE = 0.42
THERMAL_AIR_RELAX_RATE = 0.16
THERMAL_SOLAR_SURFACE_RATE = 0.32
THERMAL_FIRE_HEAT_RATE = 34.0
THERMAL_FIRE_NEIGHBOR_FACTOR = 0.28
THERMAL_WATER_EXCHANGE_RATE = 0.72
THERMAL_OBJECT_EXCHANGE_RATE = 0.28
THERMAL_WATER_COOLING_FACTOR = 0.50

# V0.7.2.2 underground thermal boundary. Surface climate (polar/alpine/etc.)
# must not be copied unchanged into deep caves.  Below the immutable original
# terrain line the world has a geothermal minimum temperature that rises with
# depth.  This prevents an ordinary underground puddle from freezing merely
# because its X chunk belongs to the surface polar biome, while explicit cold
# sources (ice magic / deliberately sub-zero water) can still freeze it.
UNDERGROUND_GEOTHERMAL_MIN_C = 8.0
UNDERGROUND_GEOTHERMAL_C_PER_TILE = 0.16
UNDERGROUND_GEOTHERMAL_MAX_C = 28.0

# Phase change.
WATER_FREEZE_C = 0.0
WATER_BOIL_C = 100.0
WATER_FLASH_BOIL_C = 125.0
WATER_FREEZE_MIN_AMOUNT = 0.985
ICE_FREEZE_PROGRESS_PER_C_PER_SEC = 0.022
ICE_MELT_PROGRESS_PER_C_PER_SEC = 0.018
WATER_BOIL_RATE_PER_C_PER_SEC = 0.040

# Fire/temperature coupling.
FIRE_NOMINAL_TEMP_C = 620.0
FIRE_MIN_SUSTAIN_TEMP_C = -18.0
FIRE_COLD_BURN_MIN_FACTOR = 0.28
FIRE_HOT_BURN_MAX_FACTOR = 1.35

# Fireball heat.
FIREBALL_TEMPERATURE_C = 520.0
FIREBALL_RANK = 1
# Deprecated in V0.7.2.3: fireballs no longer skip ICE -> VAPOR.
FIREBALL_ICE_VAPORIZE_RANK = 999

# Waterball temperature.
WATERBALL_TEMPERATURE_C = 12.0

# Iceball magic. Iceball is condensed atmospheric water, so it carries mass.
ICEBALL_MANA_COST = 10.0
ICEBALL_SPEED = 330.0
ICEBALL_LIFETIME = 1.60
ICEBALL_POWER = 1.0
ICEBALL_GRAVITY = 470.0
ICEBALL_MAX_FALL_SPEED = 660.0
ICEBALL_AMOUNT = 1.0
ICEBALL_TEMPERATURE_C = -28.0
ICEBALL_MIN_CAST_MASS = 0.999

# Ice tiles contain conserved water mass.
ICE_TILE_WATER_MASS = 1.0

# V0.7.4.1 partial ice.  ICE keeps one tile id and exact conserved ice_mass,
# while rendering/collision quantize that mass into three visible thicknesses.
# This avoids manufacturing a full block from a shallow puddle.
ICE_STAGE_LOW_MAX_MASS = 0.34
ICE_STAGE_MID_MAX_MASS = 0.67
ICE_STAGE_LOW_HEIGHT_RATIO = 0.28
ICE_STAGE_MID_HEIGHT_RATIO = 0.58
ICE_STAGE_HIGH_HEIGHT_RATIO = 1.00
ICE_STAGE_MIN_MASS = 0.04
# Cold water resting immediately above partial ice freezes onto the existing
# layer first, so low -> medium -> full before a second ice cell is created.
ICE_ACCRETION_RATE_PER_SEC = 0.95

# Player health recovery. Damage of any source restarts the delay. Poison gas
# intentionally suppresses regeneration until the poison dose has cleared.
PLAYER_HP_REGEN_DELAY_SECONDS = 4.0
PLAYER_HP_REGEN_PER_SECOND = 1.35

# Creature contact attacks are discrete bites/strikes rather than a continuous
# overlap drain. After a hit the attacker recoils and must separate before it
# can arm another attack.
CREATURE_ATTACK_RECOIL_SECONDS = 0.28
CREATURE_ATTACK_REARM_GAP_PX = TILE_SIZE * 0.38


# ============================================================
# V0.4.13 Time / Energy / Ecology / Thermally-driven Wind
# ============================================================

# World time. Simulation systems still use fixed dt, but now also receive a
# deterministic absolute game clock. 60 = one real second advances one game
# minute, which keeps ecology testable on iPhone without making growth instant.
GAME_TIME_SCALE = 60.0
GAME_START_DAY = 1
GAME_START_HOUR = 8
GAME_START_MINUTE = 0
GAME_DAY_SECONDS = 24.0 * 60.0 * 60.0

# Daylight model. Regional climate still controls how strongly sunlight heats.
SUNRISE_HOUR = 6.0
SUNSET_HOUR = 18.0

# Ecological succession. Every exposed fertile top-soil tile has an implicit
# dormant seed bank. When it remains moist, biomass is transferred from the
# chunk organic reservoir into a visible plant.
AUTO_TOPSOIL_GERMINATION_MOISTURE = 0.20
AUTO_TOPSOIL_GERMINATION_TEMP_MIN = 4.0
AUTO_TOPSOIL_GERMINATION_TEMP_MAX = 38.0
AUTO_TOPSOIL_GERMINATION_GAME_SECONDS = 4.0 * 60.0
AUTO_TOPSOIL_DRY_RESET_RATE = 2.0

# Succession stages, expressed in accumulated continuously-wet game time.
# 0 dormant seed, 1 grass, 2 dense grass, 3 sprout, 4 sapling,
# 5 young tree, 6 mature tree.
SUCCESSION_STAGE_GAME_SECONDS = (
    0.0,
    10.0 * 60.0,
    35.0 * 60.0,
    75.0 * 60.0,
    2.5 * 60.0 * 60.0,
    5.0 * 60.0 * 60.0,
    9.0 * 60.0 * 60.0,
)
SUCCESSION_WATER_USE_PER_GAME_HOUR = 0.045
SUCCESSION_BIOMASS_PER_GAME_HOUR = 0.028
SUCCESSION_MATURE_FRUIT_INTERVAL_GAME_HOURS = 3.0
SUCCESSION_TREE_MAX_FRUIT = 4

# Energy ledger stage 1. Temperatures are converted to Kelvin for a positive
# thermal-energy proxy. This is a gameplay energy accounting layer, not yet a
# full SI/Joule thermodynamics solver.
ENERGY_REFERENCE_KELVIN = 273.15
ENERGY_AUDIT_TOLERANCE = 1e-4
ENERGY_SOLAR_UNIT = 1.0
ENERGY_COMBUSTION_UNIT = 1.0
ENERGY_WIND_UNIT = 0.5

# Wind field. Wind is computed per chunk from horizontal/vertical temperature
# gradients plus a small weather background term. Vectors are in px/s for
# gameplay forces; fire converts them to directional spread bias.
WIND_HZ = 2.0
WIND_TEMP_GRADIENT_GAIN = 7.5
WIND_BASE_WEATHER_GAIN = 24.0
WIND_MAX_SPEED = 145.0
WIND_SMOOTH_RATE = 0.32
WIND_OBJECT_DRAG_AREA = 0.0018
WIND_OBJECT_MAX_ACCEL = 260.0
WIND_FIRE_DIRECTION_BIAS = 1.35
WIND_FIRE_UPDRAFT_BIAS = 0.25

# Electricity is active from V0.7.2 onward. Charge is sparse and decays;
# propagation is bounded to conductive cells near the impact.
ELECTRIC_CHARGE_DECAY_PER_SEC = 0.08
LIGHTNING_ENERGY_DEFAULT = 1200.0


# ============================================================
# V0.4.14 Atmospheric moisture + magic condensation fix
# ============================================================

# Chunk boundaries are simulation partitions, NOT physical walls.
# Water/Ice magic can collect atmospheric H2O from a local air mass spanning
# nearby chunks, instead of requiring >= 1.0 vapor in exactly one chunk.
MAGIC_VAPOR_GATHER_RADIUS_CHUNKS = 2

# Atmospheric H2O slowly mixes across chunk boundaries. Wind transports vapor
# downwind while diffusion smooths sharp chunk-to-chunk humidity discontinuities.
ATMOSPHERE_HZ = 2.0
ATMOSPHERE_VAPOR_DIFFUSION_RATE = 0.10
ATMOSPHERE_VAPOR_ADVECTION_RATE = 0.08
ATMOSPHERE_MIN_TRANSFER = 1e-7


# ============================================================
# V0.7.4.0 Atmospheric astronomy / biome hydrology
# ============================================================
# Climate multipliers are deliberately gameplay-scaled rather than SI units.
# They make the same water/ice packet behave differently in desert, temperate,
# humid rainforest and alpine climates while all mass transfers remain explicit.
CLIMATE_SOLAR_FLUX_MULTIPLIER = {
    "desert": 1.22,
    "temperate": 1.00,
    "rainforest": 0.86,
    "alpine": 0.82,
    "polar": 0.65,
}
CLIMATE_WATER_EVAP_MULTIPLIER = {
    "desert": 24.0,
    "temperate": 1.0,
    "rainforest": 24.0,
    "alpine": 0.28,
    "polar": 0.12,
}
CLIMATE_SOIL_EVAP_MULTIPLIER = {
    "desert": 5.5,
    "temperate": 1.0,
    "rainforest": 5.5,
    "alpine": 0.24,
    "polar": 0.10,
}
CLIMATE_INFILTRATION_MULTIPLIER = {
    "desert": 2.15,
    "temperate": 1.0,
    "rainforest": 1.15,
    "alpine": 0.35,
    "polar": 0.18,
}
CLIMATE_WATER_FREEZE_MULTIPLIER = {
    "desert": 0.20,
    "temperate": 1.0,
    "rainforest": 0.75,
    "alpine": 4.5,
    "polar": 5.5,
}
CLIMATE_ICE_MELT_MULTIPLIER = {
    "desert": 3.4,
    "temperate": 1.0,
    "rainforest": 1.10,
    "alpine": 0.20,
    "polar": 0.06,
}
# Surface solids need a weak climate boundary too. Without this, an ice tile is
# insulated from the desert air because only AIR used to relax to regional temp.
THERMAL_EXPOSED_ICE_RELAX_RATE = 0.22

# V0.7.5.5 sparse off-screen climate phase simulation.  We do not run full
# thermal diffusion outside the player window; only dynamic water/ice phase
# states are advanced at low frequency.
THERMAL_BACKGROUND_PHASE_HZ = 1.0
THERMAL_BACKGROUND_MAX_WATER_CELLS = 256
THERMAL_BACKGROUND_MAX_ICE_CELLS = 256

# Sand is porous even though it is infertile. Desert drainage is intentionally
# fast and the deep reservoir can return to the atmosphere under strong heat.
DESERT_GROUNDWATER_EVAP_RATE = 1.20
DESERT_SAND_FIELD_CAPACITY = 0.0
DESERT_GROUNDWATER_DRAIN_MULTIPLIER = 3.5
DESERT_GROUNDWATER_STEAM_FRACTION = 1.0
# V0.7.5.5: sand is never a sealed catchment floor.  Surface liquid gets a
# dedicated high-rate infiltration pass before horizontal pooling.  A small
# pore fraction may remain in the sand column; the rest is conservatively
# routed to groundwater (or an open cavity below the sand).
DESERT_SAND_DIRECT_INFILTRATION_RATE = 12.0
DESERT_SAND_PORE_HOLD_PER_TILE = 0.06
DESERT_SAND_MAX_PERCOLATION_TILES = 28
# Any liquid still exposed after the infiltration attempt evaporates quickly.
# This no longer waits for the top sand cell to reach an arbitrary saturation
# threshold; the order is explicitly: infiltration first -> evaporation second.
DESERT_SURFACE_SATURATION_RATIO = 0.0
DESERT_SATURATED_SURFACE_EVAP_MULTIPLIER = 18.0
DESERT_SATURATED_SURFACE_MIN_EVAP_RATE = 0.90

# Cloud water is a real conserved reservoir: vapor -> cloud -> precipitation.
ATMOSPHERE_CLOUD_FULL_MASS = 4.0
ATMOSPHERE_CLOUD_RAIN_THRESHOLD = 0.55
ATMOSPHERE_CLOUD_ADVECTION_RATE = 1.18
ATMOSPHERE_CLOUD_DIFFUSION_RATE = 0.025
ATMOSPHERE_CLOUD_MIN_TRANSFER = 1e-6
ATMOSPHERE_PREVAILING_EAST_SPEED = 58.0
ATMOSPHERE_CONDENSE_THRESHOLD = {
    "desert": 15.0,
    "temperate": 10.5,
    "rainforest": 8.5,
    "alpine": 9.0,
    "polar": 8.0,
}
ATMOSPHERE_CONDENSE_RATE = {
    "desert": 0.12,
    "temperate": 0.22,
    "rainforest": 0.34,
    "alpine": 0.28,
    "polar": 0.32,
}
# Long-run rainfall target across eligible surface climates:
# rainforest 60%, desert 10%, other/temperate 30%.  Alpine and polar are
# deliberately excluded from this rain-routing budget; their snow/frost is
# maintained by the cold-surface system rather than warm rain.
ATMOSPHERE_PRECIP_TARGET_SHARE = {
    "rainforest": 0.60,
    "desert": 0.10,
    "temperate": 0.30,
}
ATMOSPHERE_PRECIP_EXCLUDED_PROFILES = ("alpine", "polar")
# Climate-only fallback when biome metadata is unavailable. The normal map
# path uses WeatherSystem's biome-aware, area-normalized 60/10/30 router.
ATMOSPHERE_PRECIP_EFFICIENCY = {
    "desert": 1.0 / 6.0,
    "temperate": 0.25,
    "rainforest": 1.0,
    "alpine": 0.0,
    "polar": 0.0,
}
CLOUD_VISUAL_MIN_MASS = 0.08

# Cold highland waterballs remain liquid briefly, spread through WaterSystem,
# then freeze naturally from their sub-zero landing temperature.
ALPINE_WATERBALL_LANDING_TEMP_C = -18.0
POLAR_WATERBALL_LANDING_TEMP_C = -26.0
ALPINE_SURFACE_FREEZE_MIN_AMOUNT = 0.04
# In naturally freezing surface climates, smaller amounts are conserved in a
# hidden snow/frost reservoir until enough mass exists to create visible low ice.
COLD_SURFACE_RESERVOIR_MAX_MASS = ALPINE_SURFACE_FREEZE_MIN_AMOUNT

# Debug/UI: show how much condensable vapor is reachable by the current spell.
MAGIC_AVAILABLE_H2O_DISPLAY_RADIUS = MAGIC_VAPOR_GATHER_RADIUS_CHUNKS


# ============================================================
# V0.4.15 Magic-created matter / energy
# ============================================================

# IMPORTANT MODEL CHANGE:
# The world is an OPEN system. Sun, rain, climate boundaries, etc. do not
# need to form one globally closed matter/energy box.
#
# Conservation is enforced for the matter/energy CREATED BY A SPELL after the
# spell has created it. Mana is the source boundary.

# Internal liquid solver uses 1.0 = one full 40x40 water tile.
# For gameplay display, one full tile is defined as 10 water-mass units.
WATER_MASS_UNITS_PER_TILE = 10.0

# Waterball:
#   MP -> 10 units of water matter
# The projectile therefore carries exactly 1.0 internal liquid unit.
WATERBALL_MAGIC_WATER_UNITS = 10.0

# Iceball:
#   MP -> 10 units water-equivalent matter in a cold/solid phase.
ICEBALL_MAGIC_WATER_UNITS = 10.0

# Fireball:
#   MP -> finite spell energy.
# Each basic fireball may evaporate at most 5 water-mass units.
FIREBALL_MAGIC_ENERGY_UNITS = 100.0
FIREBALL_EVAPORATE_WATER_UNITS = 5.0
FIREBALL_ENERGY_PER_EVAP_WATER_UNIT = 10.0

# Soil is considered too wet to ignite above this internal moisture fraction.
# With 10 units in one saturated soil tile:
#   1st fireball: 10 -> 5 units, still too wet
#   2nd fireball:  5 -> 0 units, then ignition can start
FIREBALL_SOIL_WET_LIMIT = 0.35

# Evaporation -> rising air.
# "updraft" is a transient chunk-scale vertical-airflow impulse.
EVAP_UPDRAFT_PER_WATER_UNIT = 8.0
EVAP_UPDRAFT_DECAY_PER_SECOND = 0.70
EVAP_UPDRAFT_MAX_SPEED = 120.0

# Wind visual particles are cosmetic tracers of the real WindSystem vector.
WIND_VISUAL_PARTICLES_PER_CHUNK = 18
WIND_VISUAL_MIN_SPEED = 8.0
WIND_VISUAL_SPEED_SCALE = 0.65
WIND_VISUAL_ALPHA = 0.36

# V0.4.17 wind readability tuning
# Weak / medium / strong wind keep visible traces for 2 / 4 / 8 seconds.
WIND_VISUAL_WEAK_DURATION = 2.0
WIND_VISUAL_MEDIUM_DURATION = 4.0
WIND_VISUAL_STRONG_DURATION = 8.0
WIND_VISUAL_TRACE_WIDTH_WEAK = 2.2
WIND_VISUAL_TRACE_WIDTH_MEDIUM = 3.2
WIND_VISUAL_TRACE_WIDTH_STRONG = 4.4
WIND_VISUAL_TRACE_LENGTH_WEAK = 16.0
WIND_VISUAL_TRACE_LENGTH_MEDIUM = 24.0
WIND_VISUAL_TRACE_LENGTH_STRONG = 34.0
WIND_VISUAL_DENSITY_WEAK = 0.70
WIND_VISUAL_DENSITY_MEDIUM = 1.10
WIND_VISUAL_DENSITY_STRONG = 1.65

# Rain visual upgrade. These do not create extra physical rain mass.
RAIN_VISUAL_PARTICLES_PER_CHUNK = 28
RAIN_VISUAL_MIN_PARTICLES = 8
RAIN_VISUAL_STREAK_LENGTH = 34.0
RAIN_VISUAL_ALPHA = 0.90
RAIN_SPLASH_PARTICLES_PER_CHUNK = 4

# V0.4.17 rain readability tuning
# Light / heavy rain are rendered differently so they are easy to distinguish.
RAIN_VISUAL_LIGHT_THRESHOLD = 0.45
RAIN_VISUAL_LIGHT_SPEED = 0.32
RAIN_VISUAL_HEAVY_SPEED = 0.54
RAIN_VISUAL_LIGHT_DENSITY = 0.60
RAIN_VISUAL_HEAVY_DENSITY = 1.00
RAIN_VISUAL_STREAK_WIDTH_LIGHT = 2.4
RAIN_VISUAL_STREAK_WIDTH_HEAVY = 3.6
RAIN_VISUAL_STREAK_LENGTH_LIGHT = 24.0
RAIN_VISUAL_STREAK_LENGTH_HEAVY = 38.0

# Use liquid-water blue so rain is visually grouped with water, while wind
# remains a pale / air-like color.
RAIN_VISUAL_COLOR_R = 0.18
RAIN_VISUAL_COLOR_G = 0.58
RAIN_VISUAL_COLOR_B = 0.94

# Legacy names kept for compatibility in a few places.
RAIN_VISUAL_STREAK_WIDTH = RAIN_VISUAL_STREAK_WIDTH_HEAVY
WIND_VISUAL_TRACE_WIDTH = WIND_VISUAL_TRACE_WIDTH_MEDIUM
WIND_VISUAL_TRACE_LENGTH = WIND_VISUAL_TRACE_LENGTH_MEDIUM


# ============================================================
# V0.4.18 Charged magic / horizontal wind / fire lift
# ============================================================

# Attack hold thresholds.
MAGIC_CHARGE_LEVEL1_MAX = 0.50
MAGIC_CHARGE_LEVEL2_MAX = 1.00
MAGIC_CHARGE_LEVEL3_MAX = 2.00

# Fire / water effect multipliers:
# L1 = 1x, L2 = 2x, L3 = 3x of L2 = 6x L1.
MAGIC_LEVEL_MULTIPLIER_L1 = 1.0
MAGIC_LEVEL_MULTIPLIER_L2 = 2.0
MAGIC_LEVEL_MULTIPLIER_L3 = 6.0

# Ice is area based instead of the 1/2/6 multiplier.
ICE_LEVEL1_TILES = 1
ICE_LEVEL2_TILES = 3
ICE_LEVEL3_TILES = 5

# V0.7.2.3: Ice magic never chain-freezes an entire connected pond.
# Solid-surface L3 still creates the normal 5-tile line, while a direct
# water hit may freeze at most seven contiguous surface-water cells.
ICE_WATER_CHAIN_LEVEL1_TILES = 1
ICE_WATER_CHAIN_LEVEL2_TILES = 3
ICE_WATER_CHAIN_LEVEL3_TILES = 7
ICE_WATER_CHAIN_MIN_AMOUNT = 0.08

# A fireball advances ice by one phase only: ICE -> liquid WATER.
# A later fireball can then spend heat/evaporation capacity on WATER -> STEAM.
FIREBALL_MELTED_WATER_MIN_C = 12.0

# Level-3 fire/water burst count.  Six L1 fragments preserve the requested
# 6x level-1 total effect without creating extra matter/energy.
MAGIC_LEVEL3_FRAGMENT_COUNT = 6
MAGIC_FRAGMENT_SPEED_SCALE = 0.82
MAGIC_FRAGMENT_LIFETIME_SCALE = 0.72

# Projectile size readability.
MAGIC_RADIUS_LEVEL1 = 8.0
MAGIC_RADIUS_LEVEL2 = 10.5
MAGIC_RADIUS_LEVEL3 = 13.0

# Fire-source lift. Ambient wind is horizontal; vertical lift is local.
WIND_FIRE_LIFT_PER_INTENSITY = 42.0
WIND_FIRE_LIFT_TEMP_GAIN = 0.35
WIND_FIRE_LIFT_MAX = 125.0

# Fireball creates transient hot-air lift while flying / on impact.
FIREBALL_LIFT_LEVEL1 = 16.0
FIREBALL_LIFT_LEVEL2 = 34.0
FIREBALL_LIFT_LEVEL3 = 72.0
FIREBALL_LIFT_DECAY_PER_SECOND = 0.90

# Actual horizontal wind speed tiers.
WIND_WEAK_TARGET_SPEED = 34.0
WIND_MEDIUM_TARGET_SPEED = 72.0
WIND_STRONG_TARGET_SPEED = 118.0

# Player / wind interaction.
PLAYER_TAILWIND_SPEED_BONUS = 0.36
PLAYER_HEADWIND_SPEED_PENALTY = 0.30
PLAYER_GROUND_WIND_DRIFT = 0.10
PLAYER_AIR_WIND_DRIFT = 0.28
PLAYER_WIND_VERTICAL_ACCEL = 3.2
PLAYER_TAILWIND_JUMP_BONUS = 0.18
PLAYER_FIRE_LIFT_JUMP_BONUS = 0.38


# ============================================================
# V0.4.19 Fire stacking / 3-tile L3 burst
# ============================================================

FIRE_STACK_MAX = 3

# Visual / simulation tier for repeated direct fireball hits on the same tile.
FIRE_STACK_INTENSITY_L1 = 0.46
FIRE_STACK_INTENSITY_L2 = 0.73
FIRE_STACK_INTENSITY_L3 = 1.00

FIRE_STACK_TEMP_L1 = 560.0
FIRE_STACK_TEMP_L2 = 680.0
FIRE_STACK_TEMP_L3 = 790.0

# Lift multipliers for small / medium / large flames.
FIRE_STACK_LIFT_L1 = 0.58
FIRE_STACK_LIFT_L2 = 1.00
FIRE_STACK_LIFT_L3 = 1.62

# Renderer sizes for small / medium / large fire.
FIRE_STACK_WIDTH_L1 = 11.0
FIRE_STACK_WIDTH_L2 = 17.0
FIRE_STACK_WIDTH_L3 = 24.0

FIRE_STACK_HEIGHT_L1 = 18.0
FIRE_STACK_HEIGHT_L2 = 29.0
FIRE_STACK_HEIGHT_L3 = 42.0

# Level-3 fire/water impact footprint.
# Exactly three surface tiles: center + one tile on either side.
MAGIC_LEVEL3_BURST_TILES = 3


# ============================================================
# V0.4.20 Ash / Grass / Tree regrowth
# ============================================================
ASH_REHYDRATE_SECONDS = 5.0
ASH_REHYDRATE_WATER_THRESHOLD = 0.08
ASH_SOIL_FERTILITY = 0.55

SOIL_PLANT_CAP_WEIGHTS = (
    (1, 0.50),
    (2, 0.35),
    (3, 0.15),
)

SUCCESSION_STAGE_GAME_SECONDS_V20 = (
    0.0,
    10.0 * 60.0,
    35.0 * 60.0,
    75.0 * 60.0,
)

TREE_REGROW_TIME_FIRE_L1 = 7.0
TREE_REGROW_TIME_FIRE_L2 = 14.0


# ============================================================
# V0.4.21 Progressive tree burn / ash transition
# ============================================================
TREE_BURN_PROGRESS_RATE_L1 = 0.16
TREE_BURN_PROGRESS_RATE_L2 = 0.24
TREE_BURN_PROGRESS_RATE_L3 = 0.42
TREE_PARTIAL_BURN_RECOVERY_RATE = 0.08

# L3 fireball: only the actual impact tile receives the immediate ash effect.
L3_FIREBALL_CENTER_ASH = True

# Cosmetic ash-to-soil blend. Simulation still changes ASH -> DIRT at 5 s.
ASH_REHYDRATE_BLEND_START = 0.10


# ============================================================
# V0.4.22 Grass coverage / ash hydration / wind occlusion
# ============================================================

# Natural germination should not happen in perfect lock-step.
AUTO_TOPSOIL_GERMINATION_DELAY_MIN_SCALE = 0.78
AUTO_TOPSOIL_GERMINATION_DELAY_MAX_SCALE = 1.42

# Physical rain now samples every exposed surface column. Per-column rain is
# scaled so total rain mass stays near the previous every-other-column model.
RAIN_PHYSICAL_COLUMN_STEP = 1
RAIN_PHYSICAL_COLUMN_MASS_SCALE = 0.50

# Ash hydration is triggered by contact; after contact the 5-second reaction
# continues even if the thin surface puddle flows away.
ASH_REHYDRATE_TRIGGER_WATER = 0.015
ASH_REHYDRATE_CONTACT_EPSILON = 0.001

# Tile-level wind shelter / turbulence.
WIND_OCCLUSION_LOOKBACK_TILES = 7
WIND_WALL_SHADOW_TILES = 6
WIND_WALL_NEAR_FACTOR = 0.05
WIND_WALL_FAR_FACTOR = 0.52
WIND_TURBULENCE_VERTICAL_RATIO = 0.055

# A stepped terrain profile with at most a one-tile rise/drop per horizontal
# tile is treated as a slope rather than a sheer wind-blocking wall.
WIND_SLOPE_MAX_STEP_TILES = 1
WIND_SLOPE_VERTICAL_RATIO = 0.52

ASH_RECOVERED_SOIL_MOISTURE = 0.28


# ============================================================
# V0.4.23 strict wall impermeability / wake
# ============================================================

# First N tiles directly behind a sheer wall have zero forward through-flow.
WIND_WALL_ZERO_FLOW_TILES = 3

# Extend the wake so air recovers gradually rather than appearing to pass
# through the wall immediately.
WIND_WALL_SHADOW_TILES_V23 = 10
WIND_WALL_RECOVERY_FACTOR_V23 = 0.28

# Tiny vertical turbulence remains in the wake, but should stay visually weak.
WIND_WALL_TURBULENCE_RATIO_V23 = 0.025

# Tracer lines are not allowed to overlap/cross solid tiles.
WIND_TRACER_SOLID_CLIP_SAMPLES = 8


# ============================================================
# V0.4.24 green-skin topsoil before visible grass
# ============================================================

# Wet brown dirt first becomes a green-skin soil tile, then a visible grass
# plant may germinate later. This keeps terrain-state change and plant growth
# as two separate ecological stages.
TOPSOIL_GREEN_SKIN_DELAY_RATIO = 0.35

# Ash hydration ends as GRASS_DIRT rather than plain brown DIRT.
# Visible grass is still delayed by the PlantSystem germination clock.
ASH_RECOVERY_TO_GRASS_DIRT = True


# ============================================================
# V0.5.0 Map Editor / custom map runtime
# ============================================================
EDITOR_MAP_RELATIVE_PATH = "maps/editor_map.json"
MAP_FORMAT_VERSION = 2

# Gameplay effects for editor-placeable map materials.
SWAMP_DAMAGE_PER_SECOND = 8.0
MUD_PLAYER_SPEED_MULTIPLIER = 0.55

# Editor grid.
MAP_EDITOR_VISIBLE_COLUMNS = 22
MAP_EDITOR_VISIBLE_ROWS = 12
MAP_EDITOR_MAX_UNDO = 16

# Larger editor documents are independent from the small built-in test world.
# Exported maps resize TileWorld at load time, so underground maps can be much
# deeper than WORLD_HEIGHT_TILES.
MAP_EDITOR_DEFAULT_WIDTH = 1024
MAP_EDITOR_DEFAULT_HEIGHT = 96
MAP_EDITOR_SURFACE_ROW = 18

# Reserve screen margins so edge cells/buttons are not buried against iPhone
# rounded corners / home-indicator edges.
MAP_EDITOR_SAFE_MARGIN_X = 18.0
MAP_EDITOR_SAFE_MARGIN_Y = 10.0

# Continuous viewport panning while an editor arrow is held.
MAP_EDITOR_PAN_TILES_PER_SECOND = 12.0

# Procedural world generation defaults.
WORLD_GEN_DEFAULT_WIDTH = 1024
WORLD_GEN_DEFAULT_HEIGHT = 96
WORLD_GEN_SURFACE_RATIO = 0.19
WORLD_GEN_SURFACE_ROW = 18
WORLD_GEN_LAKE_SURFACE_ROW = 17
UNDERGROUND_START_DEPTH_TILES = 8
UNDERGROUND_FIRST_LEVEL_ROW = 34
UNDERGROUND_SECOND_LEVEL_ROW = 58
UNDERGROUND_DEEP_LEVEL_ROW = 80
UNDERGROUND_MIN_WORLD_HEIGHT = 96


# ============================================================
# V0.5.6 Rain / swamp ecology
# ============================================================

# Rain-profile chunks now alternate between rain and non-rain periods instead
# of raining forever.
RAIN_EVENT_PERIOD_SECONDS = 18.0
RAIN_EVENT_DUTY = 0.48

# Standing surface water converts grassland into swamp after sustained soaking.
SWAMP_FORM_SECONDS = 8.0
SWAMP_RECOVER_SECONDS = 10.0
SWAMP_STANDING_WATER_THRESHOLD = 0.18
SWAMP_DRY_WATER_THRESHOLD = 0.025

# Swamp terrain damage remains the same as editor-authored swamp hazards.
SWAMP_SOIL_DAMAGE_PER_SECOND = 8.0

# A swamp tile remains protected from ash conversion while either standing
# water or strongly saturated soil is present.
SWAMP_FIRE_WATER_PROTECT_THRESHOLD = 0.06
SWAMP_FIRE_SOIL_PROTECT_MOISTURE = 0.72

# Vegetation standing in a swamp becomes visibly withered.
SWAMP_WITHER_RECOVERY_SECONDS = 6.0


# ============================================================
# V0.5.6.3 iPhone input backend
# ============================================================
# IMPORTANT:
# The experimental transparent UIKit overlay can successfully appear in Pyto
# while still swallowing all touches before they reach the Python callbacks.
# Keep it disabled until a Pyto/Rubicon-specific UITouch probe is proven.
USE_NATIVE_FINGER_ID = False


# ============================================================
# V0.5.6.5 Direction input state machine
# ============================================================

# Very quick taps are treated as finite movement pulses rather than a
# persistent held-direction state.
DIRECTION_TAP_PULSE_SECONDS = 0.11

# Holding longer than this enters continuous movement mode.
DIRECTION_HOLD_THRESHOLD_SECONDS = 0.075


# ============================================================
# V0.5.6.7 iPhone gameplay input
# ============================================================

# Use per-button native UIKit UIControl target/action events.
#
# This is NOT the old full-screen Native Finger-ID overlay.
# Each gameplay button owns only its own rectangle and uses the standard
# UIControl touchDown / touchUpInside / touchUpOutside / touchCancel events.
USE_UIKIT_UICONTROL_INPUT = True


# ============================================================
# V0.5.8 Performance / Tool / Aim
# ============================================================

# World ecology is intentionally entered below the 60 Hz gameplay step.
# Individual systems still keep their own FixedRateTask frequencies, so e.g.
# WATER_HZ remains authoritative without paying EnvironmentManager dispatch on
# every player frame.
PERF_ENVIRONMENT_HZ = 60.0
PERF_ENVIRONMENT_MAX_STEPS = 1

# Dynamic environment visuals (water/fire/plants/weather traces) can refresh
# below the player/camera render rate without making controls feel sluggish.
PERF_ENV_RENDER_HZ = 20.0

# V0.6.2: liquid visuals are split from the expensive ecology/weather/VFX
# environment layer. Water gets a smoother 30 Hz lightweight batch while
# plants/fire/wind/rain decorations rebuild at 10 Hz.
PERF_WATER_RENDER_HZ = 30.0

# Wind tracers are cosmetic. Per-particle local wind queries + solid segment
# ray tests were a major Python spike when fireballs created strong updraft.
# The fast path uses the already-simulated chunk vector instead.
PERF_FAST_WIND_VFX = True
PERF_WIND_VFX_MAX_PARTICLES_PER_CHUNK = 12

# Thermal simulation is deliberately more local than collision/water streaming.
# Temperature diffusion is slow and does not need five horizontal chunks of
# high-frequency Python work around the player.
THERMAL_PERF_RADIUS_X = 0
THERMAL_PERF_RADIUS_Y = 0

# Heavy engineering audit/debug HUD is off during normal play.
ENGINEERING_HUD_ENABLED = False
MOVEMENT_DEBUG_HUD_ENABLED = False

# Aiming reticle / direction.
#
# V0.5.8 moved the crosshair freely through the whole screen. That made the
# distance between player and reticle look like it affected aiming.
#
# V0.5.8.1 makes the reticle a DIRECTION indicator:
#     player center + normalized aim direction * fixed radius.
#
# The visible radius is deliberately short so the reticle remains near the
# character. Projectile/tool logic uses the same normalized direction but its
# own fixed reach / virtual ballistic distance.
AIM_RETICLE_RADIUS_TILES = 1.35
AIM_RETICLE_RADIUS = TILE_SIZE * AIM_RETICLE_RADIUS_TILES

# Virtual distance used only to construct a stable ballistic target.
# Joystick magnitude / reticle distance never changes this.
AIM_MAGIC_DIRECTION_DISTANCE_TILES = 6.0
AIM_MAGIC_DIRECTION_DISTANCE = (
    TILE_SIZE * AIM_MAGIC_DIRECTION_DISTANCE_TILES
)

AIM_JOYSTICK_RADIUS = 38.0
AIM_JOYSTICK_DEADZONE = 0.10
AIM_DIRECTION_DEADZONE = 0.10
# V0.6.5.2: right stick once again represents an ABSOLUTE direction, but the
# crosshair rotates toward a newly selected stick direction instead of snapping.
# At 900 deg/s, even a full 180-degree mismatch transitions over ~0.20 s.
AIM_SOFT_SNAP_TURN_SPEED_DEG = 900.0
# Once the remaining angular error is this small, lock exactly to the stick.
AIM_SOFT_SNAP_LOCK_DEG = 2.0
AIM_SOFT_SNAP_EPSILON = 1e-5

# Tools / mining. Attack is now reserved for physical/tool use.
TOOL_REACH_TILES = 3.25
TOOL_HIT_COOLDOWN = 0.10
TOOL_SHOVEL_POWER = 1.0  # legacy compatibility; shovel is merged into pickaxe
TOOL_PICKAXE_POWER = 1.0
TOOL_AXE_POWER = 1.0


# V0.7.0 Reactive World foundation
REACTION_HZ = 20.0
REACTION_MAX_CELLS_PER_TICK = 384
REACTION_NEIGHBOR_RADIUS = 1
REACTION_EPSILON = 0.02
REACTION_WATER_ELECTRIC_LONG_SECONDS = 2.0
REACTION_TRANSIENT_DECAY_PER_SEC = 1.8
REACTION_GAS_DECAY_PER_SEC = 0.035
REACTION_LIQUID_DECAY_PER_SEC = 0.004
REACTION_STEAM_SPREAD_PER_SEC = 0.32
REACTION_GAS_SPREAD_PER_SEC = 0.20
REACTION_WATER_FIRE_RATE = 0.24
REACTION_WATER_POISON_RATE = 0.18
REACTION_POISON_STEAM_RATE = 0.16
REACTION_ACID_ELECTRIC_RATE = 0.12
MAGNET_DEFAULT_RADIUS_TILES = 8
MAGNET_MAX_SCAN_CELLS = 1024
MAGNET_FORCE_SCALE = 1.0

# V0.7.0 event-driven conductive network
ELECTRIC_NETWORK_MAX_CELLS = 512
ELECTRIC_NETWORK_MIN_CHARGE = 0.025
ELECTRIC_WATER_CONDUCTIVITY = 0.68
ELECTRIC_ACID_CONDUCTIVITY = 0.90
ELECTRIC_POISON_LIQUID_CONDUCTIVITY = 0.46
ELECTRIC_JOULE_HEAT_SCALE = 1.8

# FIX32 underground magma biome
LAVA_CONTACT_DAMAGE_PER_SECOND = 28.0
LAVA_BURN_DAMAGE_PER_SECOND = 5.0
LAVA_BURN_SECONDS = 3.0


# FIX35 dynamic magma liquid
LAVA_HZ = 12.0
LAVA_FALL_FLOW = 0.58
LAVA_SIDE_FLOW = 0.16
LAVA_FLOW_EPSILON = 0.003
LAVA_SOURCE_RATE_PER_SECOND = 0.42
LAVA_COOL_RATE_PER_WATER = 0.72
LAVA_WATER_CONSUME_RATE = 0.30
LAVA_STEAM_YIELD = 0.55
LAVA_BOTTOM_RESERVOIR_ROWS = 1
LAVA_MIN_VISIBLE = 0.015


# ============================================================
# FIX39 Underwater / Lava Spit / Chest Equipment
# ============================================================
# Water visuals keep physics at 30 Hz but rebuild the Metal liquid batch at a
# stable 30 Hz. The player/actors continue rendering at display rate.
UNDERWATER_BUBBLE_MAX = 18
UNDERWATER_GRASS_MAX = 14
UNDERWATER_ANCHOR_REFRESH_SECONDS = 0.55

# Lava surface eruption projectiles. Bounded count protects Pyto/iPhone.
LAVA_SPIT_MAX_ACTIVE = 10
LAVA_SPIT_SPAWN_MIN_SECONDS = 0.75
LAVA_SPIT_SPAWN_MAX_SECONDS = 1.55
LAVA_SPIT_SPEED_MIN = 215.0
LAVA_SPIT_SPEED_MAX = 330.0
LAVA_SPIT_GRAVITY = 620.0
LAVA_SPIT_DIRECT_DAMAGE = 13.0

# Chest interaction / world loot.
CHEST_INTERACT_RADIUS_PX = 82.0
CHEST_SURFACE_MARGIN_TILES = 5
CHEST_MAX_PER_SURFACE_MAP = 10
