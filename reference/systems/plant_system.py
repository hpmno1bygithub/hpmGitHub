# -*- coding: utf-8 -*-
import random

from config import (
    PLANT_HZ,
    CHUNK_SIZE,
    PLANT_GERMINATION_GRASS,
    PLANT_GERMINATION_BERRY,
    PLANT_STAGE_STEP,
    PLANT_SEED_BIOMASS,
    PLANT_BIOMASS_PER_GROWTH,
    PLANT_FRUIT_MASS,
    AUTO_TOPSOIL_GERMINATION_MOISTURE,
    AUTO_TOPSOIL_GERMINATION_TEMP_MIN,
    AUTO_TOPSOIL_GERMINATION_TEMP_MAX,
    AUTO_TOPSOIL_GERMINATION_GAME_SECONDS,
    AUTO_TOPSOIL_DRY_RESET_RATE,
    AUTO_TOPSOIL_GERMINATION_DELAY_MIN_SCALE,
    AUTO_TOPSOIL_GERMINATION_DELAY_MAX_SCALE,
    TOPSOIL_GREEN_SKIN_DELAY_RATIO,
    SUCCESSION_WATER_USE_PER_GAME_HOUR,
    SUCCESSION_BIOMASS_PER_GAME_HOUR,
    SUCCESSION_MATURE_FRUIT_INTERVAL_GAME_HOURS,
    SUCCESSION_TREE_MAX_FRUIT,
    SOIL_PLANT_CAP_WEIGHTS,
    SUCCESSION_STAGE_GAME_SECONDS_V20,
    TREE_PARTIAL_BURN_RECOVERY_RATE,
    SWAMP_WITHER_RECOVERY_SECONDS,
)
from systems.fixed_scheduler import FixedRateTask
from world.environment_state import PlantCell
from world.tile_registry import (
    AIR,
    DIRT,
    GRASS_DIRT,
    SWAMP_SOIL,
    tile_def,
)


class PlantSystem:
    """Topsoil ecology and simple 3-level succession.

    V0.4.20 maps ecology to the requested gameplay presentation:
      stage 1 -> short grass
      stage 2 -> tall grass
      stage 3 -> tree

    Each soil tile receives a random maximum growth cap (1/2/3). A cap-1 tile
    can only ever host short grass; cap-2 can reach tall grass; cap-3 can grow
    into a tree.

    Trees can enter:
      normal -> deadwood -> regrowing -> normal
    after being burned by stack-1 or stack-2 fire.
    """

    SPECIES = {
        "grass": dict(
            moisture_min=0.18,
            moisture_max=0.88,
            temp_min=5,
            temp_max=38,
            growth_rate=0.055,
            germination=PLANT_GERMINATION_GRASS,
            fruit_stage=99,
        ),
        "berry": dict(
            moisture_min=0.32,
            moisture_max=0.86,
            temp_min=12,
            temp_max=32,
            growth_rate=0.038,
            germination=PLANT_GERMINATION_BERRY,
            fruit_stage=4,
        ),
    }

    def __init__(
        self,
        world,
        env,
        chunk_streamer,
        weather_system,
        time_system=None,
    ):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system
        self.time_system = time_system
        self.task = FixedRateTask(PLANT_HZ, max_steps=2)
        self.topsoil_wet_clock = {}
        self.rng = random.Random(42020)

    def random_topsoil_cap(self):
        roll = self.rng.random()
        acc = 0.0
        for stage, weight in SOIL_PLANT_CAP_WEIGHTS:
            acc += float(weight)
            if roll <= acc:
                return int(stage)
        return int(SOIL_PLANT_CAP_WEIGHTS[-1][0])

    def plant(self, tx, ty, species="grass"):
        """Manual planting remains available and is matter-conserving."""
        tx = int(tx)
        ty = int(ty)
        cx = tx // CHUNK_SIZE
        cy = ty // CHUNK_SIZE

        mass = self.env.take_organic(cx, cy, PLANT_SEED_BIOMASS)
        if mass < PLANT_SEED_BIOMASS * 0.999:
            self.env.add_organic(cx, cy, mass)
            return False

        max_stage = 3
        soil = self.env.soil.get((tx, ty))
        if soil is not None:
            max_stage = max(1, min(3, int(getattr(soil, "plant_cap", 3))))

        self.env.plants[(tx, ty)] = PlantCell(
            species=species,
            growth=0.0,
            stage=0,
            fruit=0,
            alive=True,
            biomass=mass,
            max_stage=max_stage,
            state="normal",
        )
        return True

    def update(self, dt):
        for step in self.task.consume(dt):
            game_dt = self.time_system.last_game_dt if self.time_system is not None else step
            if self.time_system is not None:
                game_dt = step * self.time_system.time_scale
            self._step(step, max(0.0, game_dt))

    def _is_exposed_fertile_topsoil(self, tx, ty):
        td = tile_def(self.world.get_tile(tx, ty))
        if not (td.solid and td.fertility > 0.0 and td.water_absorption > 0.0):
            return False
        # "Exposed" here only means the soil has an open top face where a
        # plant could physically occupy space.  It does NOT imply sunlight:
        # underground exposed soil uses this path to grow moss.  Grass-skin
        # permission is checked separately against the immutable surface line.
        if ty <= 0:
            return True
        return (
            self.world.get_tile(tx, ty - 1) == AIR
            and self.env.water_amount(tx, ty - 1) < 0.22
        )

    def _germination_delay(
        self,
        tx,
        ty,
    ):
        # Deterministic pseudo-random staggering. This avoids a whole field
        # popping at once while still allowing many wet topsoil tiles to grow.
        h = (
            (
                int(tx)
                * 73856093
            )
            ^ (
                int(ty)
                * 19349663
            )
        ) & 0xFFFF

        t = (
            h
            / 65535.0
        )

        scale = (
            AUTO_TOPSOIL_GERMINATION_DELAY_MIN_SCALE
            + (
                AUTO_TOPSOIL_GERMINATION_DELAY_MAX_SCALE
                - AUTO_TOPSOIL_GERMINATION_DELAY_MIN_SCALE
            )
            * t
        )

        return (
            AUTO_TOPSOIL_GERMINATION_GAME_SECONDS
            * scale
        )


    def _is_underground_tile(self, tx, ty):
        """Classify ecology against the immutable pre-mining surface."""
        try:
            return bool(self.world.is_underground(int(tx), int(ty)))
        except Exception:
            try:
                surface = self.world.backdrop_surface_row(int(tx))
            except Exception:
                surface = self.world.first_solid_row(int(tx))
            return surface is not None and int(ty) > int(surface)

    def _enforce_topsoil_skin_invariant(self):
        """Remove impossible grass-skin tiles in active terrain.

        This also repairs older saves/maps that already contain GRASS_DIRT
        underground or buried below another solid soil tile.  Moisture is kept;
        only the visual/terrain skin returns to ordinary DIRT.
        """
        for tx, ty in self.chunk_streamer.iter_active_tile_coords():
            if self.world.get_tile(tx, ty) != GRASS_DIRT:
                continue
            try:
                allowed = self.world.can_support_grass_skin(tx, ty)
            except Exception:
                allowed = (not self._is_underground_tile(tx, ty) and
                           (ty <= 0 or self.world.get_tile(tx, ty - 1) == AIR))
            if not allowed:
                self.world.set_tile(tx, ty, DIRT, track_change=True)

    def _make_moss(self, tx, ty, soil, wet_seconds=0.0):
        key = (int(tx), int(ty))
        cx = int(tx) // CHUNK_SIZE
        cy = int(ty) // CHUNK_SIZE
        seed_mass = self.env.take_organic(cx, cy, PLANT_SEED_BIOMASS)
        if seed_mass < PLANT_SEED_BIOMASS * 0.999:
            self.env.add_organic(cx, cy, seed_mass)
            return False
        self.env.plants[key] = PlantCell(
            species="moss",
            growth=0.0,
            stage=1,
            fruit=0,
            alive=True,
            biomass=seed_mass,
            age_game_seconds=0.0,
            wet_game_seconds=max(0.0, float(wet_seconds)),
            fruit_clock_game_seconds=0.0,
            max_stage=1,
            state="normal",
        )
        return True

    def _auto_germinate_topsoil(self, game_dt):
        # V0.5.9: soil may contain thousands of sleeping cells in a deep world.
        # Only inspect the player's active chunk window.
        for tx, ty in self.chunk_streamer.iter_active_tile_coords():
            soil = self.env.soil.get((tx, ty))
            if soil is None:
                continue

            plant = self.env.plants.get((tx, ty))
            if plant is not None:
                self.topsoil_wet_clock.pop((tx, ty), None)
                continue

            if (
                self.world.get_tile(
                    tx,
                    ty,
                )
                == SWAMP_SOIL
            ):
                self.topsoil_wet_clock.pop(
                    (
                        tx,
                        ty,
                    ),
                    None,
                )
                continue

            if not self._is_exposed_fertile_topsoil(tx, ty):
                self.topsoil_wet_clock.pop((tx, ty), None)
                continue

            local_temp_c = self.env.temperature_at(tx, ty, 20.0)
            weather = self.weather_system.weather_at_chunk(tx // CHUNK_SIZE, ty // CHUNK_SIZE)

            underground = self._is_underground_tile(tx, ty)
            suitable = (
                soil.moisture >= AUTO_TOPSOIL_GERMINATION_MOISTURE
                and AUTO_TOPSOIL_GERMINATION_TEMP_MIN <= local_temp_c <= AUTO_TOPSOIL_GERMINATION_TEMP_MAX
                and (underground or weather.sunlight >= 0.05)
            )

            key = (tx, ty)
            clock = float(self.topsoil_wet_clock.get(key, 0.0))
            if suitable:
                clock += game_dt
            else:
                clock = max(0.0, clock - game_dt * AUTO_TOPSOIL_DRY_RESET_RATE)

            required_delay = self._germination_delay(
                tx,
                ty,
            )

            skin_delay = (
                required_delay
                * TOPSOIL_GREEN_SKIN_DELAY_RATIO
            )

            tile_id = self.world.get_tile(
                tx,
                ty,
            )

            # Underground has no direct sun. Once a damp exposed soil cell has
            # completed the same germination wait, it produces moss only. The
            # terrain itself stays brown (never GRASS_DIRT), and moss has a hard
            # max stage of 1 so it can never become grass/tree succession.
            if underground and clock >= required_delay:
                if self._make_moss(tx, ty, soil, wet_seconds=clock):
                    self.topsoil_wet_clock.pop(key, None)
                continue

            # Ecology is now explicitly two-stage:
            #
            #   brown wet soil -> green-skin soil -> visible grass
            #
            # This prevents a brown DIRT block from visually jumping directly
            # to a grass plant.
            if (
                suitable
                and not underground
                and tile_id == DIRT
                and clock >= skin_delay
                and self.world.can_support_grass_skin(tx, ty)
            ):
                self.world.set_tile(
                    tx,
                    ty,
                    GRASS_DIRT,
                    track_change=True,
                )

                tile_id = GRASS_DIRT

            if clock < required_delay:
                if clock > 0.0:
                    self.topsoil_wet_clock[key] = clock
                else:
                    self.topsoil_wet_clock.pop(key, None)
                continue

            # DIRT-based succession may only create visible grass after the
            # terrain itself has already become GRASS_DIRT.
            if self.world.get_tile(
                tx,
                ty,
            ) == DIRT:
                if not self.world.can_support_grass_skin(tx, ty):
                    self.topsoil_wet_clock.pop(key, None)
                    continue
                self.world.set_tile(
                    tx,
                    ty,
                    GRASS_DIRT,
                    track_change=True,
                )

                self.topsoil_wet_clock[
                    key
                ] = clock

                continue

            cx = tx // CHUNK_SIZE
            cy = ty // CHUNK_SIZE
            seed_mass = self.env.take_organic(cx, cy, PLANT_SEED_BIOMASS)
            if seed_mass < PLANT_SEED_BIOMASS * 0.999:
                self.env.add_organic(cx, cy, seed_mass)
                continue

            self.env.plants[key] = PlantCell(
                species="succession",
                growth=0.0,
                stage=1,
                fruit=0,
                alive=True,
                biomass=seed_mass,
                age_game_seconds=0.0,
                wet_game_seconds=clock,
                fruit_clock_game_seconds=0.0,
                max_stage=max(1, min(3, int(getattr(soil, "plant_cap", 3)))),
                state="normal",
            )
            self.topsoil_wet_clock.pop(key, None)

    def _succession_stage(
        self,
        wet_seconds,
        max_stage,
    ):
        # Germination itself is already stage 1 (visible grass).
        # The old 0/1/2/3 mapping accidentally pushed a newly germinated plant
        # back to invisible stage 0 until another long wet-time threshold.
        thresholds = SUCCESSION_STAGE_GAME_SECONDS_V20

        if (
            wet_seconds
            >= thresholds[3]
        ):
            stage = 3
        elif (
            wet_seconds
            >= thresholds[2]
        ):
            stage = 2
        else:
            stage = 1

        return max(
            1,
            min(
                int(
                    max_stage
                ),
                int(
                    stage
                ),
            ),
        )

    def _update_swamp_vegetation(
        self,
        plant,
        real_dt,
    ):
        state = getattr(
            plant,
            "state",
            "normal",
        )

        if state == "swamp_withered":
            # Waterlogging has killed/withheld active growth. The plant stays
            # visible as dry/dead vegetation and consumes no growth water.
            return True

        if state == "swamp_recovering":
            total = max(
                1.0,
                float(
                    getattr(
                        plant,
                        "regrow_total_game_seconds",
                        SWAMP_WITHER_RECOVERY_SECONDS,
                    )
                    or SWAMP_WITHER_RECOVERY_SECONDS
                ),
            )

            plant.regrow_progress = min(
                1.0,
                float(
                    getattr(
                        plant,
                        "regrow_progress",
                        0.0,
                    )
                )
                + real_dt
                / total,
            )

            if plant.regrow_progress >= 1.0:
                plant.state = "normal"
                plant.regrow_progress = 1.0

            return True

        return False

    def _update_tree_recovery(self, plant, real_dt):
        # Active fire owns burn progression. Ecology pauses while burning.
        if plant.state == "burning":
            return True

        # V0.7.1.7: rain/water-extinguished vegetation is a persistent charred
        # object.  Its burn_progress is deliberately NOT reduced, so the exact
        # green->brown colour present when rain arrived is preserved.
        if plant.state == "charred":
            return True

        # Legacy saves may contain the old deadwood state. Keep it as permanent
        # deadwood rather than silently regrowing under the new fire rules.
        if plant.state == "deadwood":
            return True

        if plant.state == "regrowing":
            total = max(1.0, float(plant.regrow_total_game_seconds) or 4.0)
            plant.regrow_progress = min(
                1.0,
                float(plant.regrow_progress) + real_dt / total,
            )
            if plant.regrow_progress >= 1.0:
                plant.state = "normal"
                plant.regrow_progress = 1.0
                plant.burn_progress = 0.0
                plant.burn_stack = 0
            return True

        # A fire extinguished before complete burnout leaves temporary char.
        if float(getattr(plant, "burn_progress", 0.0)) > 0.0:
            plant.burn_progress = max(
                0.0,
                float(plant.burn_progress)
                - TREE_PARTIAL_BURN_RECOVERY_RATE * real_dt,
            )
            if plant.burn_progress <= 0.0:
                plant.burn_stack = 0

        return False

    def _grow_succession(self, tx, ty, plant, soil, weather, real_dt, game_dt):
        local_temp_c = self.env.temperature_at(tx, ty, float(weather.temperature))
        plant.age_game_seconds += game_dt
        plant.max_stage = max(1, min(3, int(getattr(soil, "plant_cap", getattr(plant, "max_stage", 3)))))

        if self._update_swamp_vegetation(
            plant,
            real_dt,
        ):
            return

        if self._update_tree_recovery(plant, real_dt):
            return

        irrigated = (
            soil.moisture >= AUTO_TOPSOIL_GERMINATION_MOISTURE
            and 3.0 <= local_temp_c <= 40.0
            and weather.sunlight >= 0.04
            and self._is_exposed_fertile_topsoil(tx, ty)
        )

        if irrigated:
            plant.wet_game_seconds += game_dt
            hours = game_dt / 3600.0
            water_requested = SUCCESSION_WATER_USE_PER_GAME_HOUR * hours * (0.35 + 0.22 * max(1, plant.stage))
            transpired = min(
                max(
                    0.0,
                    soil.moisture,
                ),
                max(
                    0.0,
                    water_requested,
                ),
            )

            if transpired > 0.0:
                soil.moisture -= transpired
                self.env.add_vapor(
                    tx // CHUNK_SIZE,
                    ty // CHUNK_SIZE,
                    transpired,
                )

            water_growth_factor = (
                transpired
                / water_requested
                if water_requested > 1e-9
                else 0.0
            )

            requested_mass = (
                SUCCESSION_BIOMASS_PER_GAME_HOUR
                * hours
                * (
                    0.55
                    + max(
                        0.0,
                        min(
                            1.0,
                            soil.fertility,
                        ),
                    )
                )
                * (
                    0.35
                    + max(
                        0.0,
                        min(
                            1.0,
                            weather.sunlight,
                        ),
                    )
                )
                * max(
                    0.0,
                    min(
                        1.0,
                        water_growth_factor,
                    ),
                )
            )
            gained = self.env.take_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, requested_mass)
            plant.biomass += gained
            plant.growth += gained / max(1e-9, PLANT_BIOMASS_PER_GROWTH)
        else:
            plant.wet_game_seconds = max(0.0, plant.wet_game_seconds - game_dt * 0.18)

        plant.stage = self._succession_stage(plant.wet_game_seconds, plant.max_stage)

        if plant.stage >= 3 and irrigated:
            plant.fruit_clock_game_seconds += game_dt
            interval = SUCCESSION_MATURE_FRUIT_INTERVAL_GAME_HOURS * 3600.0
            if plant.fruit_clock_game_seconds >= interval and plant.fruit < SUCCESSION_TREE_MAX_FRUIT:
                fruit_mass = self.env.take_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, PLANT_FRUIT_MASS)
                if fruit_mass >= PLANT_FRUIT_MASS * 0.999:
                    plant.fruit += 1
                    plant.biomass += fruit_mass
                    plant.fruit_clock_game_seconds -= interval
                else:
                    self.env.add_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, fruit_mass)

    def _grow_moss(self, tx, ty, plant, soil, weather, real_dt, game_dt):
        # Moss is the terminal underground vegetation stage: it survives on
        # moisture/nutrients but never progresses into grass or trees.
        plant.stage = 1
        plant.max_stage = 1
        plant.fruit = 0
        plant.age_game_seconds += game_dt
        if getattr(plant, "state", "normal") in ("burning", "charred", "deadwood"):
            return
        if not self._is_underground_tile(tx, ty):
            # If terrain editing truly exposes the cell above the original
            # surface, keep existing moss rather than instant-converting it.
            return
        if soil.moisture >= AUTO_TOPSOIL_GERMINATION_MOISTURE * 0.72:
            hours = game_dt / 3600.0
            use = SUCCESSION_WATER_USE_PER_GAME_HOUR * 0.22 * hours
            taken = min(max(0.0, soil.moisture), max(0.0, use))
            soil.moisture -= taken
            if taken > 0.0:
                self.env.add_vapor(tx // CHUNK_SIZE, ty // CHUNK_SIZE, taken)

    def _grow_legacy(self, tx, ty, plant, soil, weather, real_dt, game_dt):
        if getattr(plant, "state", "normal") in ("burning", "charred", "deadwood"):
            return
        cfg = self.SPECIES.get(plant.species, self.SPECIES["grass"])
        local_temp_c = self.env.temperature_at(tx, ty, float(weather.temperature))
        plant.age_game_seconds += game_dt

        good = (
            cfg["moisture_min"] <= soil.moisture <= cfg["moisture_max"]
            and cfg["temp_min"] <= local_temp_c <= cfg["temp_max"]
            and weather.sunlight >= 0.12
        )

        if good:
            requested_growth = (
                cfg["growth_rate"] * (0.45 + weather.sunlight * 0.8) * (0.5 + soil.fertility * 0.6) * real_dt
            )
            requested_mass = requested_growth * PLANT_BIOMASS_PER_GROWTH
            gained_mass = self.env.take_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, requested_mass)
            scale = gained_mass / requested_mass if requested_mass > 1e-12 else 0.0
            actual_growth = requested_growth * scale
            plant.growth += actual_growth
            plant.biomass += gained_mass

            germ = cfg["germination"]
            if plant.growth < germ:
                plant.stage = 0
            else:
                plant.stage = min(4, 1 + int((plant.growth - germ) // PLANT_STAGE_STEP))

            if plant.stage >= cfg["fruit_stage"] and plant.fruit < 3:
                fruit_mass = self.env.take_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, PLANT_FRUIT_MASS)
                if fruit_mass >= PLANT_FRUIT_MASS * 0.999:
                    plant.fruit += 1
                    plant.biomass += fruit_mass
                else:
                    self.env.add_organic(tx // CHUNK_SIZE, ty // CHUNK_SIZE, fruit_mass)
        elif soil.moisture < 0.05:
            plant.growth = max(0.0, plant.growth - 0.012 * real_dt)
            if plant.growth < cfg["germination"]:
                plant.stage = 0

    def _step(self, real_dt, game_dt):
        self._enforce_topsoil_skin_invariant()
        self._auto_germinate_topsoil(game_dt)

        for (tx, ty), plant in list(self.env.plants.items()):
            if not self.chunk_streamer.is_active(tx // CHUNK_SIZE, ty // CHUNK_SIZE):
                continue
            if not plant.alive:
                continue

            soil = self.env.soil.get((tx, ty))
            if soil is None:
                continue

            weather = self.weather_system.weather_at_chunk(tx // CHUNK_SIZE, ty // CHUNK_SIZE)

            if str(
                plant.species
            ).startswith(
                "map_"
            ):
                # Map-authored plants keep fixed size, but can still become
                # swamp-withered and later recover after drainage.
                self._update_swamp_vegetation(
                    plant,
                    real_dt,
                )
                continue

            # Existing saves from V0.7.2.0 may already contain dynamically
            # grown grass/trees inside caves. Convert only dynamic succession,
            # never map-authored vegetation, to the new underground moss rule.
            if plant.species == "succession" and self._is_underground_tile(tx, ty):
                plant.species = "moss"
                plant.stage = 1
                plant.max_stage = 1
                plant.fruit = 0

            if plant.species == "moss":
                self._grow_moss(tx, ty, plant, soil, weather, real_dt, game_dt)
            elif plant.species == "succession":
                self._grow_succession(tx, ty, plant, soil, weather, real_dt, game_dt)
            else:
                self._grow_legacy(tx, ty, plant, soil, weather, real_dt, game_dt)
