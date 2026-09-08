# -*- coding: utf-8 -*-
import random

from config import (
    FIRE_HZ,
    CHUNK_SIZE,
    FIRE_WATER_EXTINGUISH,
    FIRE_RAIN_EXTINGUISH,
    FIRE_SPREAD_CHANCE,
    SOIL_FIRE_WET_LIMIT,
    SOIL_LATERAL_SPREAD_FACTOR,
    FIREBALL_SOIL_WET_LIMIT,
    FIREBALL_SOIL_DRY_ON_HIT,
    FIREBALL_SOIL_EVAPORATE_TO,
    FIRE_NOMINAL_TEMP_C,
    FIRE_MIN_SUSTAIN_TEMP_C,
    FIRE_COLD_BURN_MIN_FACTOR,
    FIRE_HOT_BURN_MAX_FACTOR,
    WIND_MAX_SPEED,
    WIND_FIRE_DIRECTION_BIAS,
    WIND_FIRE_UPDRAFT_BIAS,
    WATER_MASS_UNITS_PER_TILE,
    EVAP_UPDRAFT_PER_WATER_UNIT,
    FIRE_STACK_INTENSITY_L1,
    FIRE_STACK_INTENSITY_L2,
    FIRE_STACK_INTENSITY_L3,
    FIRE_STACK_TEMP_L1,
    FIRE_STACK_TEMP_L2,
    FIRE_STACK_TEMP_L3,
    TREE_REGROW_TIME_FIRE_L1,
    TREE_REGROW_TIME_FIRE_L2,
    TREE_BURN_PROGRESS_RATE_L1,
    TREE_BURN_PROGRESS_RATE_L2,
    TREE_BURN_PROGRESS_RATE_L3,
    SWAMP_FIRE_WATER_PROTECT_THRESHOLD,
    SWAMP_FIRE_SOIL_PROTECT_MOISTURE,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import (
    AIR,
    DIRT,
    GRASS_DIRT,
    ASH,
    SWAMP_SOIL,
    tile_def,
)


class FireSystem:
    """Fuel-based fire with water/rain extinguishing and soil depth burn.

    V0.4.3 soil behavior:
      - grass/dirt contain a small amount of organic fuel
      - wet soil resists ignition
      - lateral fire spread through dirt is strongly reduced
      - downward soil penetration is explicit per ignition source
      - basic fireball penetration budget is 0, so it burns only the tile hit
    """

    SOIL_IDS = (
        DIRT,
        GRASS_DIRT,
        SWAMP_SOIL,
    )

    def __init__(self, world, env, chunk_streamer, weather_system):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system
        self.task = FixedRateTask(FIRE_HZ, max_steps=2)
        self.rng = random.Random(404)

    def _soil_moisture(self, tx, ty):
        soil = self.env.soil.get((tx, ty))
        if soil is None:
            return 0.0
        return float(soil.moisture)

    def _swamp_is_water_protected(
        self,
        tx,
        ty,
    ):
        if self.world.get_tile(
            tx,
            ty,
        ) != SWAMP_SOIL:
            return False

        standing = (
            self.env.water_amount(
                tx,
                ty,
            )
            + self.env.water_amount(
                tx,
                max(
                    0,
                    ty - 1,
                ),
            )
        )

        soil = self.env.soil.get(
            (
                tx,
                ty,
            )
        )

        moisture = (
            float(
                soil.moisture
            )
            if soil is not None
            else 0.0
        )

        return (
            standing
            >= SWAMP_FIRE_WATER_PROTECT_THRESHOLD
            or moisture
            >= SWAMP_FIRE_SOIL_PROTECT_MOISTURE
        )

    def _is_raining(self, tx, ty):
        weather = self.weather_system.weather_at_chunk(
            int(tx) // CHUNK_SIZE,
            int(ty) // CHUNK_SIZE,
        )
        return float(getattr(weather, "rain", 0.0)) > 0.10

    def _rain_blocks_ignition(self, tx, ty, source, plant=None):
        """Gameplay rain rule for V0.7.1.7.

        * A fireball cannot start a new fire anywhere in a raining chunk.
        * Exposed vegetation cannot be newly ignited by spread/environmental
          fire while rain is actually reaching it.
        Existing exposed vegetation fire is extinguished in _step().
        """
        if not self._is_raining(tx, ty):
            return False
        if str(source).startswith("fireball"):
            return True
        if plant is not None and self._sky_exposed(tx, ty):
            return True
        return False

    def _can_ignite(self, tx, ty, source="environment"):
        tile_id = self.world.get_tile(tx, ty)
        td = tile_def(tile_id)
        plant = self.env.plants.get((tx, ty))

        if self._rain_blocks_ignition(tx, ty, source, plant=plant):
            return False

        if not td.flammable and plant is None:
            return False

        ambient_c = self.env.temperature_at(
            tx,
            ty,
            20.0,
        )

        if (
            not str(source).startswith(
                "fireball"
            )
            and ambient_c
            <= FIRE_MIN_SUSTAIN_TEMP_C
        ):
            return False

        # Actual standing water always blocks ignition.  This keeps the
        # "fire meets water -> extinguish" rule independent from hidden soil
        # moisture values.
        same_water = self.env.water_amount(tx, ty)
        above_water = self.env.water_amount(tx, max(0, ty - 1))

        standing_water = (
            same_water
            + above_water
        )

        swamp_withered_plant = (
            tile_id == SWAMP_SOIL
            and plant is not None
            and getattr(
                plant,
                "state",
                "normal",
            )
            == "swamp_withered"
        )

        if standing_water > 0.08:
            # Dry, dead vegetation can still catch briefly above swamp water,
            # but the wet terrain underneath remains protected.
            if not swamp_withered_plant:
                return False

        if swamp_withered_plant:
            # The dead grass/tree stands above the saturated substrate and can
            # catch fire from spells or neighboring fire even though the swamp
            # soil itself remains too wet to burn.
            return True

        if tile_id in self.SOIL_IDS:
            moisture = self._soil_moisture(tx, ty)

            # Direct fireball drying is performed by MagicSystem with an
            # explicit per-projectile 5-unit evaporation budget.
            if str(source).startswith(
                "fireball"
            ):
                return (
                    moisture
                    <= FIREBALL_SOIL_WET_LIMIT
                )

            return (
                moisture
                < SOIL_FIRE_WET_LIMIT
            )

        return True

    def _sky_exposed(
        self,
        tx,
        ty,
    ):
        for sy in range(
            0,
            ty,
        ):
            if tile_def(
                self.world.get_tile(
                    tx,
                    sy,
                )
            ).solid:
                return False

        return True


    def _tree_regrowth_seconds(self, fire_stack):
        if int(fire_stack) <= 1:
            return TREE_REGROW_TIME_FIRE_L1
        return TREE_REGROW_TIME_FIRE_L2

    def _tree_burn_rate(self, fire_stack):
        fire_stack = max(1, min(3, int(fire_stack)))
        if fire_stack == 1:
            return TREE_BURN_PROGRESS_RATE_L1
        if fire_stack == 2:
            return TREE_BURN_PROGRESS_RATE_L2
        return TREE_BURN_PROGRESS_RATE_L3

    def _plant_tree_level(
        self,
        plant,
    ):
        if plant is None:
            return 0

        species = str(
            getattr(
                plant,
                "species",
                "",
            )
        )

        stage = max(
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

        if species in ("map_tree", "map_jungle_tree", "map_pine"):
            return stage

        if (
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
            return 3

        return 0

    def scorch_to_ash(self, tx, ty, source="fireball_l3_center"):
        """Immediately convert one flammable solid/plant cell to ASH.

        Used by the level-3 fireball center-impact rule. It only affects the
        actual impact cell. Matter is transferred to explicit ash/smoke
        reservoirs before the visual/solid ASH tile is placed.
        """
        tx = int(tx)
        ty = int(ty)
        if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
            return False

        tile_id = self.world.get_tile(tx, ty)
        td = tile_def(tile_id)
        plant = self.env.plants.get((tx, ty))

        if self._rain_blocks_ignition(tx, ty, source, plant=plant):
            return False

        if not td.solid:
            return False
        if not td.flammable and plant is None:
            return False

        # Standing water blocks the immediate scorch-to-ash effect.
        wet = self.env.water_amount(tx, ty) + self.env.water_amount(tx, max(0, ty - 1))
        if wet > 0.08:
            return False

        if (
            tile_id == SWAMP_SOIL
            and self._swamp_is_water_protected(
                tx,
                ty,
            )
        ):
            return False

        soil = self.env.soil.get((tx, ty))
        if soil is not None:
            residual = max(0.0, float(soil.moisture))
            if residual > 0.0:
                self.env.add_vapor(tx // CHUNK_SIZE, ty // CHUNK_SIZE, residual)

        tile_mass = max(0.0, float(td.material_mass))
        ash_fraction = max(0.0, min(1.0, float(td.burn_ash_fraction)))
        self.env.add_ash(tx, ty, tile_mass * ash_fraction)
        self.env.add_smoke(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
            tile_mass * (1.0 - ash_fraction),
        )

        if plant is not None:
            plant_mass = max(0.0, float(getattr(plant, "biomass", 0.035)))
            self.env.add_ash(tx, ty, plant_mass * 0.15)
            self.env.add_smoke(tx // CHUNK_SIZE, ty // CHUNK_SIZE, plant_mass * 0.85)

        self.env.fire.pop((tx, ty), None)
        self.env.soil.pop((tx, ty), None)
        self.env.plants.pop((tx, ty), None)
        self.env.ash_wet_time.pop((tx, ty), None)
        self.world.set_tile(tx, ty, ASH, track_change=True)
        return True

    def ignite(
        self,
        tx,
        ty,
        penetration_left=0,
        source="environment",
    ):
        if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
            return False

        if not self._can_ignite(tx, ty, source=source):
            return False

        td = tile_def(self.world.get_tile(tx, ty))
        plant = self.env.plants.get((tx, ty))

        # Soil drying for fireball impact is already bounded and accounted
        # by MagicSystem before ignite() is called.

        fuel = max(0.2, td.fuel)

        if plant is not None:
            fuel = max(
                fuel,
                0.45
                + plant.stage
                * 0.12,
            )

            # Bigger trees contain more combustible biomass and therefore do
            # not disappear after the same short base-fire duration as a
            # small tree.
            tree_level = self._plant_tree_level(
                plant
            )

            if tree_level == 1:
                fuel = max(
                    fuel,
                    0.72,
                )
            elif tree_level == 2:
                fuel = max(
                    fuel,
                    1.02,
                )
            elif tree_level >= 3:
                fuel = max(
                    fuel,
                    1.36,
                )

        direct_fireball = str(
            source
        ).startswith(
            "fireball"
        )

        existing = self.env.fire.get(
            (
                tx,
                ty,
            )
        )

        if direct_fireball:
            current_stack = (
                int(
                    getattr(
                        existing,
                        "stack_level",
                        0,
                    )
                )
                if existing is not None
                else 0
            )

            next_stack = min(
                3,
                current_stack
                + 1,
            )
        else:
            next_stack = max(
                1,
                int(
                    getattr(
                        existing,
                        "stack_level",
                        1,
                    )
                )
                if existing is not None
                else 1,
            )

        if next_stack <= 1:
            intensity = FIRE_STACK_INTENSITY_L1
            temperature_c = FIRE_STACK_TEMP_L1
        elif next_stack == 2:
            intensity = FIRE_STACK_INTENSITY_L2
            temperature_c = FIRE_STACK_TEMP_L2
        else:
            intensity = FIRE_STACK_INTENSITY_L3
            temperature_c = FIRE_STACK_TEMP_L3

        self.env.ignite(
            tx,
            ty,
            fuel=fuel,
            intensity=intensity,
            penetration_left=penetration_left,
            source=source,
            temperature_c=temperature_c,
            stack_add=(
                1
                if direct_fireball
                else 0
            ),
            stack_level=(
                None
                if direct_fireball
                else next_stack
            ),
        )

        return True

    def update(self, dt):
        for step in self.task.consume(dt):
            self._step(step)

    def _step(self, dt):
        random_ignitions = []
        forced_downward_ignitions = []

        for (tx, ty), fire in list(self.env.fire.items()):
            if not self.chunk_streamer.is_active(tx // CHUNK_SIZE, ty // CHUNK_SIZE):
                continue

            tile_id = self.world.get_tile(tx, ty)
            td = tile_def(tile_id)
            weather = self.weather_system.weather_at_chunk(
                tx // CHUNK_SIZE,
                ty // CHUNK_SIZE,
            )

            # ----------------------------------------------------------
            # Water and rain extinguish fire.
            # ----------------------------------------------------------
            # V0.7.1.7: rain freezes vegetation at exactly its current char
            # colour.  Do this BEFORE any further fuel/burn_progress update so
            # "rain starts now" means the visible gradient stops right now.
            plant_at_start = self.env.plants.get((tx, ty))
            if (
                plant_at_start is not None
                and float(getattr(weather, "rain", 0.0)) > 0.10
                and self._sky_exposed(tx, ty)
            ):
                plant_at_start.alive = True
                plant_at_start.state = "charred"
                self.env.fire.pop((tx, ty), None)
                continue

            same_water = self.env.water_amount(tx, ty)
            above_y = max(0, ty - 1)
            above_water = self.env.water_amount(tx, above_y)
            wet = same_water + above_water

            if wet > 0.02:
                plant_now = self.env.plants.get(
                    (
                        tx,
                        ty,
                    )
                )

                swamp_dead_fuel = (
                    tile_id == SWAMP_SOIL
                    and plant_now is not None
                    and getattr(
                        plant_now,
                        "state",
                        "normal",
                    )
                    == "swamp_withered"
                )

                water_cooling_factor = (
                    0.28
                    if swamp_dead_fuel
                    else 1.0
                )

                cool = min(
                    wet,
                    FIRE_WATER_EXTINGUISH,
                ) * water_cooling_factor

                fire.intensity -= cool
                fire.temperature -= cool
                fire.temperature_c = max(
                    self.env.temperature_at(
                        tx,
                        ty,
                        20.0,
                    ),
                    getattr(
                        fire,
                        "temperature_c",
                        FIRE_NOMINAL_TEMP_C,
                    )
                    - cool
                    * 190.0,
                )

                if above_water > 0:
                    evaporated = min(above_water, cool * 0.45)
                    self.env.set_water(
                        tx,
                        above_y,
                        above_water - evaporated,
                    )
                    self.env.add_vapor(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        evaporated,
                    )

                    self.env.add_updraft(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        evaporated
                        * WATER_MASS_UNITS_PER_TILE
                        * EVAP_UPDRAFT_PER_WATER_UNIT,
                    )

            rain_exposure = (
                1.0
                if self._sky_exposed(
                    tx,
                    ty,
                )
                else 0.0
            )

            if (
                weather.rain > 0.1
                and rain_exposure > 0.0
            ):
                fire.intensity -= (
                    FIRE_RAIN_EXTINGUISH
                    * weather.rain
                    * rain_exposure
                )
                fire.temperature_c = max(
                    self.env.temperature_at(
                        tx,
                        ty,
                        20.0,
                    ),
                    getattr(
                        fire,
                        "temperature_c",
                        FIRE_NOMINAL_TEMP_C,
                    )
                    - weather.rain
                    * 18.0
                    * rain_exposure,
                )

            # Fire dries soil while it burns.
            soil = self.env.soil.get((tx, ty))
            if soil is not None:
                dry = min(
                    soil.moisture,
                    0.045 * max(0.1, fire.intensity),
                )
                if dry > 0.0:
                    soil.moisture -= dry
                    self.env.add_vapor(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        dry,
                    )

                    self.env.add_updraft(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        dry
                        * WATER_MASS_UNITS_PER_TILE
                        * EVAP_UPDRAFT_PER_WATER_UNIT
                        * 0.80,
                    )

            # ----------------------------------------------------------
            # Consume fuel.
            # ----------------------------------------------------------
            ambient_c = self.env.temperature_at(
                tx,
                ty,
                20.0,
            )

            # Cold makes combustion harder/slower; warm dry air makes it
            # easier. This affects fuel consumption and spread, not just VFX.
            if ambient_c <= FIRE_MIN_SUSTAIN_TEMP_C:
                thermal_burn_factor = (
                    FIRE_COLD_BURN_MIN_FACTOR
                )
            else:
                thermal_burn_factor = max(
                    FIRE_COLD_BURN_MIN_FACTOR,
                    min(
                        FIRE_HOT_BURN_MAX_FACTOR,
                        0.62
                        + (
                            ambient_c
                            + 10.0
                        )
                        / 55.0,
                    ),
                )

            burn = (
                0.045
                * max(
                    0.05,
                    fire.intensity,
                )
                * (
                    0.75
                    + weather.wind
                    * 0.35
                )
                * thermal_burn_factor
            )
            fire.fuel -= burn
            fire.temperature = max(
                0.0,
                fire.temperature
                - 0.015,
            )

            fire.temperature_c = max(
                ambient_c,
                getattr(
                    fire,
                    "temperature_c",
                    FIRE_NOMINAL_TEMP_C,
                )
                - (
                    1.0
                    - thermal_burn_factor
                )
                * 8.0,
            )

            fire.intensity = max(
                0.0,
                min(
                    1.0,
                    fire.intensity
                    + 0.025
                    - weather.rain
                    * 0.03
                    * rain_exposure,
                ),
            )

            plant = self.env.plants.get(
                (
                    tx,
                    ty,
                )
            )

            if plant is not None:
                # Keep vegetation visible while it burns. Trees char
                # progressively instead of showing only a ground flame and
                # then disappearing.
                plant.burn_stack = max(
                    int(
                        getattr(
                            plant,
                            "burn_stack",
                            0,
                        )
                    ),
                    max(
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
                    ),
                )

                tree_level = self._plant_tree_level(
                    plant
                )

                plant.alive = True
                plant.state = "burning"

                if tree_level > 0:
                    # Larger trees char more slowly because there is more
                    # visible structure to burn through.
                    size_factor = {
                        1: 1.20,
                        2: 0.95,
                        3: 0.72,
                    }[
                        tree_level
                    ]

                    plant.burn_progress = min(
                        1.0,
                        float(
                            getattr(
                                plant,
                                "burn_progress",
                                0.0,
                            )
                        )
                        + self._tree_burn_rate(
                            plant.burn_stack
                        )
                        * size_factor
                        * dt,
                    )

                else:
                    plant.burn_progress = min(
                        1.0,
                        float(
                            getattr(
                                plant,
                                "burn_progress",
                                0.0,
                            )
                        )
                        + 0.30
                        * dt
                        * plant.burn_stack,
                    )

            # ----------------------------------------------------------
            # Normal spread. Dirt is deliberately poor at lateral spread.
            # ----------------------------------------------------------
            if fire.intensity > 0.45 and fire.fuel > 0.20:
                for nx, ny in (
                    (tx - 1, ty),
                    (tx + 1, ty),
                    (tx, ty - 1),
                    (tx, ty + 1),
                ):
                    if not (
                        0 <= nx < self.world.width_tiles
                        and 0 <= ny < self.world.height_tiles
                    ):
                        continue

                    if (nx, ny) in self.env.fire:
                        continue

                    if not self._can_ignite(nx, ny, source="environment"):
                        continue

                    neighbor_id = self.world.get_tile(nx, ny)

                    # Critical V0.4.3 rule:
                    # normal random spread is NOT allowed to dig downward
                    # into another soil layer.  Downward penetration is handled
                    # only after burnout and only if this fire has a remaining
                    # penetration budget.
                    if (
                        neighbor_id in self.SOIL_IDS
                        and nx == tx
                        and ny > ty
                    ):
                        continue

                    chance = (
                        FIRE_SPREAD_CHANCE
                        * fire.intensity
                        * (
                            0.7
                            + weather.wind
                        )
                        * thermal_burn_factor
                    )

                    # Wind now has direction. Downwind neighbors receive a
                    # higher ignition probability; upwind spread is reduced.
                    dx = nx - tx
                    dy = ny - ty
                    wind_x = float(
                        getattr(
                            weather,
                            "wind_x",
                            0.0,
                        )
                    )
                    wind_y = float(
                        getattr(
                            weather,
                            "wind_y",
                            0.0,
                        )
                    )

                    directional = (
                        dx * wind_x
                        + dy * wind_y
                    ) / max(1.0, WIND_MAX_SPEED)

                    if dy < 0:
                        directional += WIND_FIRE_UPDRAFT_BIAS

                    chance *= max(
                        0.22,
                        min(
                            2.4,
                            1.0
                            + directional
                            * WIND_FIRE_DIRECTION_BIAS,
                        ),
                    )

                    if neighbor_id in self.SOIL_IDS and nx != tx:
                        chance *= SOIL_LATERAL_SPREAD_FACTOR

                    if self.rng.random() < chance:
                        random_ignitions.append((nx, ny))

            # ----------------------------------------------------------
            # Burnout / layer removal.
            # ----------------------------------------------------------
            if fire.intensity <= 0.05 or fire.fuel <= 0.0:
                self.env.fire.pop((tx, ty), None)

                if fire.fuel > 0.0:
                    # Extinguished before complete burnout: keep the plant,
                    # preserve partial char, and let PlantSystem recover it.
                    surviving_plant = self.env.plants.get((tx, ty))
                    if surviving_plant is not None and getattr(surviving_plant, "state", "normal") == "burning":
                        surviving_plant.state = (
                            "swamp_withered"
                            if tile_id == SWAMP_SOIL
                            else "charred"
                        )
                        surviving_plant.alive = True

                if fire.fuel <= 0:
                    was_soil = tile_id in self.SOIL_IDS
                    burned_plant = self.env.plants.get((tx, ty))
                    fire_stack = max(1, min(3, int(getattr(fire, "stack_level", 1))))

                    # V0.7.1.7: a vegetation fire that reaches zero fuel is
                    # complete burnout. Trees/grass disappear instead of
                    # entering the old automatic deadwood-regrowth cycle.
                    tree_regrow_case = False

                    if tree_regrow_case:
                        total_recovery = self._tree_regrowth_seconds(fire_stack)
                        burned_plant.alive = True
                        burned_plant.stage = 3
                        burned_plant.state = "deadwood"
                        burned_plant.fruit = 0
                        burned_plant.burn_progress = 1.0
                        burned_plant.burn_stack = fire_stack
                        # Total visible recovery remains about 7 s / 14 s:
                        # first 35% as deadwood, then 65% bottom-up greening.
                        burned_plant.recovery_game_seconds = total_recovery * 0.35
                        burned_plant.regrow_total_game_seconds = total_recovery * 0.65
                        burned_plant.regrow_progress = 0.0
                        burned_plant.biomass = max(0.02, float(getattr(burned_plant, "biomass", 0.035)) * 0.55)
                        continue

                    swamp_water_protected = (
                        tile_id == SWAMP_SOIL
                        and self._swamp_is_water_protected(
                            tx,
                            ty,
                        )
                    )

                    if swamp_water_protected:
                        # Burn the dead grass/tree biomass, but do not convert
                        # the waterlogged swamp terrain itself into ash.
                        if burned_plant is not None:
                            plant_mass = max(
                                0.0,
                                float(
                                    getattr(
                                        burned_plant,
                                        "biomass",
                                        0.035,
                                    )
                                ),
                            )

                            self.env.add_ash(
                                tx,
                                ty,
                                plant_mass
                                * 0.15,
                            )

                            self.env.add_smoke(
                                tx // CHUNK_SIZE,
                                ty // CHUNK_SIZE,
                                plant_mass
                                * 0.85,
                            )

                            self.env.plants.pop(
                                (
                                    tx,
                                    ty,
                                ),
                                None,
                            )

                        continue

                    if td.flammable or burned_plant is not None:
                        old_soil = self.env.soil.get((tx, ty))
                        if old_soil is not None:
                            residual = max(0.0, float(old_soil.moisture))
                            if residual > 0.0:
                                self.env.add_vapor(
                                    tx // CHUNK_SIZE,
                                    ty // CHUNK_SIZE,
                                    residual,
                                )

                        tile_mass = max(0.0, float(td.material_mass))
                        ash_fraction = max(0.0, min(1.0, float(td.burn_ash_fraction)))
                        ash_mass = tile_mass * ash_fraction
                        smoke_mass = tile_mass - ash_mass
                        self.env.add_ash(tx, ty, ash_mass)
                        self.env.add_smoke(tx // CHUNK_SIZE, ty // CHUNK_SIZE, smoke_mass)

                        if burned_plant is not None:
                            plant_mass = max(0.0, float(getattr(burned_plant, 'biomass', 0.035)))
                            self.env.add_ash(tx, ty, plant_mass * 0.15)
                            self.env.add_smoke(tx // CHUNK_SIZE, ty // CHUNK_SIZE, plant_mass * 0.85)

                        next_tile = ASH if td.solid else AIR
                        self.world.set_tile(tx, ty, next_tile, track_change=True)
                        self.env.soil.pop((tx, ty), None)
                        self.env.plants.pop((tx, ty), None)
                        self.env.ash_wet_time.pop((tx, ty), None)

                        remaining = int(getattr(fire, "penetration_left", 0))
                        if was_soil and remaining > 0:
                            below_y = ty + 1
                            if below_y < self.world.height_tiles:
                                below_id = self.world.get_tile(tx, below_y)
                                inherited_source = getattr(fire, "source", "environment")
                                if (below_id in self.SOIL_IDS and self._can_ignite(tx, below_y, source=inherited_source)):
                                    forced_downward_ignitions.append((tx, below_y, remaining - 1, getattr(fire, "source", "environment")))

        # Apply after iteration so dictionary edits are safe.
        for tx, ty in random_ignitions:
            self.ignite(tx, ty)

        for tx, ty, penetration_left, source in forced_downward_ignitions:
            self.ignite(
                tx,
                ty,
                penetration_left=penetration_left,
                source=source,
            )
