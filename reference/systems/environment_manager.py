# -*- coding: utf-8 -*-
import math
import time
from config import (
    GROUND_ROW,
    TILE_SIZE,
    CHUNK_SIZE,
    SOIL_INITIAL_MOISTURE,
    ATMOSPHERIC_WATER_INITIAL_PER_CHUNK,
    ORGANIC_MATTER_INITIAL_PER_CHUNK,
    WIND_OCCLUSION_LOOKBACK_TILES,
    WIND_WALL_SHADOW_TILES,
    WIND_WALL_NEAR_FACTOR,
    WIND_WALL_FAR_FACTOR,
    WIND_TURBULENCE_VERTICAL_RATIO,
    WIND_SLOPE_MAX_STEP_TILES,
    WIND_SLOPE_VERTICAL_RATIO,
    WIND_WALL_ZERO_FLOW_TILES,
    WIND_WALL_SHADOW_TILES_V23,
    WIND_WALL_RECOVERY_FACTOR_V23,
    WIND_WALL_TURBULENCE_RATIO_V23,
    WIND_TRACER_SOLID_CLIP_SAMPLES,
)
from world.environment_state import EnvironmentState, SoilCell
from world.tile_registry import AIR, WOOD, STONE
from systems.weather_system import WeatherSystem
from systems.astronomy_system import AtmosphericAstronomySystem
from systems.water_system import WaterSystem
from systems.lava_system import LavaSystem
from systems.honey_system import HoneySystem
from systems.soil_system import SoilSystem
from systems.plant_system import PlantSystem
from systems.fire_system import FireSystem
from systems.matter_ledger import MatterLedger
from systems.thermal_system import ThermalSystem
from systems.energy_ledger import EnergyLedger
from systems.wind_system import WindSystem
from systems.atmosphere_system import AtmosphereSystem
from systems.electrical_system import ElectricalSystem
from systems.reaction_system import ReactionSystem
from world.tile_registry import tile_def


class EnvironmentManager:
    def __init__(self, world, chunk_streamer, events, time_system=None):
        self.world = world
        self.chunk_streamer = chunk_streamer
        self.events = events
        self.time_system = time_system
        self.state = EnvironmentState()
        try:
            self.world.attach_environment_state(self.state)
        except Exception:
            pass
        # Live stage is read directly by the UIKit diagnostic thread. If the
        # simulation ever stalls, the screen shows the exact subsystem rather
        # than a stale snapshot.
        self.last_stage = "idle"
        self.last_stage_started = time.monotonic()
        self.last_stage_ms = 0.0
        self.stage_max_ms = {}

        self.astronomy = AtmosphericAstronomySystem(self.time_system)
        self.weather = WeatherSystem(
            self.state,
            self.chunk_streamer,
            self.time_system,
            astronomy_system=self.astronomy,
            world=self.world,
        )
        self.water = WaterSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.weather,
        )
        self.lava = LavaSystem(self.world, self.state, self.chunk_streamer)
        self.honey = HoneySystem(self.world, self.state, self.chunk_streamer)

        self.soil = SoilSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.weather,
        )
        self.plants = PlantSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.weather,
            self.time_system,
        )
        self.fire = FireSystem(
            self.world, self.state, self.chunk_streamer, self.weather
        )
        self.energy = EnergyLedger(
            self.world,
            self.state,
        )

        self.thermal = ThermalSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.weather,
            self.time_system,
            self.energy,
        )

        self.wind = WindSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.weather,
            self.energy,
        )

        self.atmosphere = AtmosphereSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            self.wind,
        )

        self.electrical = ElectricalSystem(
            self.state,
            self.energy,
            world=self.world,
            chunk_streamer=self.chunk_streamer,
        )

        self.reactions = ReactionSystem(
            self.world,
            self.state,
            self.chunk_streamer,
            thermal=self.thermal,
            wind_system=self.wind,
            energy=self.energy,
        )

        self.matter = MatterLedger(
            self.world,
            self.state,
        )

    def initialize_from_world(self):
        """Initialize simulation state from whatever terrain is already loaded.

        This is the custom-map path. It deliberately does NOT add the old
        garden, stone pit, wood patch, or debug fire.
        """
        self.reactions.reset()
        self.state.clear_dynamic()
        self.energy.reset()
        self.plants.topsoil_wet_clock.clear()

        # V0.7.0: do not instantiate millions of SoilCell Python objects for
        # a Terraria-scale map. Only the local 3x3 ecology window is awake.
        self.soil.ensure_active_soil(initial=SOIL_INITIAL_MOISTURE)

        # Atmosphere/organic reservoirs are lazy per ACTIVE chunk.
        self._ensure_active_reservoirs()

        self.thermal.initialize_world()
        self.reactions.seed_existing()

    def build_test_environment(self):
        # Pre-create all absorbent soil cells so their initial moisture is
        # part of the world's initial water inventory.
        for ty in range(self.world.height_tiles):
            for tx in range(self.world.width_tiles):
                td = tile_def(self.world.get_tile(tx, ty))
                if td.solid and td.water_absorption > 0 and td.fertility > 0:
                    self.state.soil[(tx, ty)] = SoilCell(
                        SOIL_INITIAL_MOISTURE, td.fertility, self.plants.random_topsoil_cap()
                    )

        # Atmospheric/organic reservoirs are created only near the player.
        self._ensure_active_reservoirs()

        # Garden
        for tx in range(22, 30):
            self.state.soil[(tx, GROUND_ROW)] = SoilCell(0.28 if tx % 2 == 0 else 0.12, 0.90, self.plants.random_topsoil_cap())
        self.plants.plant(23, GROUND_ROW, "grass")
        self.plants.plant(25, GROUND_ROW, "berry")
        self.plants.plant(27, GROUND_ROW, "berry")
        self.plants.plant(29, GROUND_ROW, "grass")

        # Stone-lined pit
        for tx in range(64, 71):
            self.world.set_tile(tx, GROUND_ROW, AIR, track_change=False)
            self.world.set_tile(tx, GROUND_ROW + 1, STONE, track_change=False)
        self.world.set_tile(63, GROUND_ROW, STONE, track_change=False)
        self.world.set_tile(71, GROUND_ROW, STONE, track_change=False)
        for tx in range(65, 70):
            self.state.set_water(tx, GROUND_ROW, 0.80)

        # Flammable wood patch
        for tx in range(74, 82):
            self.world.set_tile(tx, GROUND_ROW - 1, WOOD, track_change=False)
        self.fire.ignite(
            77,
            GROUND_ROW - 1,
        )

        self.thermal.initialize_world()
        self.reactions.seed_existing()

    def reset_test_environment(self):
        self.reactions.reset()
        self.state.clear_dynamic()
        self.energy.reset()
        self.plants.topsoil_wet_clock.clear()
        self.build_test_environment()

    def _run_stage(self, name, func, *args):
        self.last_stage = str(name)
        self.last_stage_started = time.monotonic()
        t0 = self.last_stage_started
        result = func(*args)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self.last_stage_ms = elapsed_ms
        if elapsed_ms > float(self.stage_max_ms.get(name, 0.0)):
            self.stage_max_ms[name] = elapsed_ms
        self.last_stage = "idle"
        self.last_stage_started = time.monotonic()
        return result

    def _ensure_active_reservoirs(self):
        """Seed finite atmospheric/organic reservoirs only for active chunks.

        Sleeping/unvisited chunks allocate nothing. Entering a chunk initializes
        its local environmental boundary once; afterwards its state is retained.
        """
        for cx, cy in tuple(self.chunk_streamer.active_chunks):
            # Atmosphere is one horizontal column per chunk-X. Older builds
            # seeded 12 units for every vertical chunk-Y, so descending into a
            # cave silently multiplied atmospheric water.
            self.state.vapor.setdefault(
                (int(cx), 0), ATMOSPHERIC_WATER_INITIAL_PER_CHUNK
            )
            self.state.organic.setdefault(
                (int(cx), int(cy)), ORGANIC_MATTER_INITIAL_PER_CHUNK
            )

    def update(
        self,
        dt,
        physics_objects=(),
    ):
        self._ensure_active_reservoirs()

        # V0.6.3: this dispatcher is called at 60 Hz. Each subsystem keeps its
        # own FixedRateTask, so expensive low-frequency work lands on separate
        # frames instead of being bunched into a 30 Hz environment mega-tick.
        self._run_stage("weather", self.weather.update, dt)
        self._run_stage("thermal", self.thermal.update, dt, physics_objects)
        self._run_stage("wind", self.wind.update, dt)
        self._run_stage("atmosphere", self.atmosphere.update, dt)
        self._run_stage("water", self.water.update, dt)
        self._run_stage("lava", self.lava.update, dt)
        self._run_stage("honey", self.honey.update, dt)
        self._run_stage("soil", self.soil.update, dt)
        self._run_stage("plants", self.plants.update, dt)
        self._run_stage("fire", self.fire.update, dt)
        self._run_stage("electrical", self.electrical.update, dt)
        self._run_stage("reaction", self.reactions.update, dt)


    def weather_at_world(self, x, y):
        size = TILE_SIZE * CHUNK_SIZE
        return self.weather.weather_at_chunk(
            int(x // size),
            int(y // size),
        )

    def temperature_at_world(self, x, y):
        return self.thermal.temperature_at_world(
            x,
            y,
        )

    def climate_at_world(self, x, y):
        return self.thermal.climate_at_world(
            x,
            y,
        )

    def register_climate_profile(
        self,
        profile_id,
        base_temp_c,
        solar_gain_c,
        night_temp_c=None,
        humidity=0.50,
    ):
        return self.thermal.register_climate_profile(
            profile_id,
            base_temp_c,
            solar_gain_c,
            night_temp_c,
            humidity,
        )

    def set_climate_region(
        self,
        cx0,
        cy0,
        cx1,
        cy1,
        profile_id,
    ):
        return self.thermal.set_climate_region(
            cx0,
            cy0,
            cx1,
            cy1,
            profile_id,
        )

    def _wind_surface_row(
        self,
        tx,
    ):
        # V0.5.9: shared cached column-height query. Deep tunnels no longer
        # make wind sampling scan from y=0 on every call.
        return self.world.first_solid_row(int(tx))

    def _wind_is_slope(
        self,
        obstacle_tx,
        direction,
    ):
        # A one-tile stepped terrain profile is interpreted as a slope.
        up_tx = (
            obstacle_tx
            - direction
        )

        down_tx = (
            obstacle_tx
            + direction
        )

        up_row = self._wind_surface_row(
            up_tx
        )

        mid_row = self._wind_surface_row(
            obstacle_tx
        )

        down_row = self._wind_surface_row(
            down_tx
        )

        if (
            up_row is None
            or mid_row is None
            or down_row is None
        ):
            return (
                False,
                0.0,
            )

        step_a = (
            mid_row
            - up_row
        )

        step_b = (
            down_row
            - mid_row
        )

        if (
            abs(
                step_a
            )
            > WIND_SLOPE_MAX_STEP_TILES
            or abs(
                step_b
            )
            > WIND_SLOPE_MAX_STEP_TILES
        ):
            return (
                False,
                0.0,
            )

        if (
            step_a == 0
            and step_b == 0
        ):
            return (
                False,
                0.0,
            )

        average_step = (
            step_a
            + step_b
        ) * 0.5

        return (
            True,
            float(
                average_step
            ),
        )

    def _wind_surface_redirect(
        self,
        tx,
        ty,
        direction,
        wind_x,
        wind_y,
    ):
        # If the air point is close to a one-tile stepped surface, bend the
        # horizontal flow along the local terrain tangent.
        surface_here = self._wind_surface_row(
            tx
        )

        if surface_here is None:
            return (
                wind_x,
                wind_y,
            )

        clearance = (
            surface_here
            - ty
        )

        if not (
            1
            <= clearance
            <= 2
        ):
            return (
                wind_x,
                wind_y,
            )

        up_row = self._wind_surface_row(
            tx
            - direction
        )

        down_row = self._wind_surface_row(
            tx
            + direction
        )

        if (
            up_row is None
            or down_row is None
        ):
            return (
                wind_x,
                wind_y,
            )

        step_total = (
            down_row
            - up_row
        )

        if (
            abs(
                step_total
            )
            > (
                WIND_SLOPE_MAX_STEP_TILES
                * 2
            )
            or step_total == 0
        ):
            return (
                wind_x,
                wind_y,
            )

        # World Y grows downward. Positive step means the terrain descends in
        # the wind direction, so the air follows it downward; negative rises.
        slope_y = (
            step_total
            * 0.5
            * abs(
                wind_x
            )
            * WIND_SLOPE_VERTICAL_RATIO
        )

        return (
            wind_x,
            wind_y
            + slope_y,
        )

    def wind_segment_hits_solid(
        self,
        x0,
        y0,
        x1,
        y1,
        samples=None,
    ):
        if samples is None:
            samples = WIND_TRACER_SOLID_CLIP_SAMPLES

        samples = max(
            2,
            int(
                samples
            ),
        )

        for i in range(
            samples
            + 1
        ):
            t = (
                i
                / samples
            )

            x = (
                float(
                    x0
                )
                + (
                    float(
                        x1
                    )
                    - float(
                        x0
                    )
                )
                * t
            )

            y = (
                float(
                    y0
                )
                + (
                    float(
                        y1
                    )
                    - float(
                        y0
                    )
                )
                * t
            )

            tx = int(
                x
                // TILE_SIZE
            )

            ty = int(
                y
                // TILE_SIZE
            )

            if not (
                0 <= tx
                < self.world.width_tiles
                and 0 <= ty
                < self.world.height_tiles
            ):
                continue

            if tile_def(
                self.world.get_tile(
                    tx,
                    ty,
                )
            ).solid:
                return True

        return False

    def _wind_wall_shadow(
        self,
        x,
        y,
        wind_x,
        wind_y,
    ):
        if abs(
            wind_x
        ) < 1e-6:
            return (
                wind_x,
                wind_y,
            )

        tx = int(
            x
            // TILE_SIZE
        )

        ty = int(
            y
            // TILE_SIZE
        )

        if not (
            0 <= tx
            < self.world.width_tiles
            and 0 <= ty
            < self.world.height_tiles
        ):
            return (
                wind_x,
                wind_y,
            )

        direction = (
            1
            if wind_x > 0.0
            else -1
        )

        nearest_wall_distance = None

        for distance in range(
            1,
            WIND_OCCLUSION_LOOKBACK_TILES
            + 1,
        ):
            sample_tx = (
                tx
                - direction
                * distance
            )

            if not (
                0 <= sample_tx
                < self.world.width_tiles
            ):
                break

            if not tile_def(
                self.world.get_tile(
                    sample_tx,
                    ty,
                )
            ).solid:
                continue

            is_slope, slope_step = (
                self._wind_is_slope(
                    sample_tx,
                    direction,
                )
            )

            if is_slope:
                # Air follows a stepped slope instead of being killed by it.
                slope_y = (
                    slope_step
                    * abs(
                        wind_x
                    )
                    * WIND_SLOPE_VERTICAL_RATIO
                )

                return (
                    wind_x,
                    wind_y
                    + slope_y,
                )

            nearest_wall_distance = distance
            break

        if nearest_wall_distance is None:
            return self._wind_surface_redirect(
                tx,
                ty,
                direction,
                wind_x,
                wind_y,
            )

        distance = max(
            1,
            int(
                nearest_wall_distance
            ),
        )

        # A solid wall is impermeable. Immediately behind it there is no
        # forward through-flow at all; farther downstream, wind may gradually
        # recover as air curls around the top/edges and the wake mixes.
        if (
            distance
            <= WIND_WALL_ZERO_FLOW_TILES
        ):
            forward_factor = 0.0
        else:
            wake_span = max(
                1,
                WIND_WALL_SHADOW_TILES_V23
                - WIND_WALL_ZERO_FLOW_TILES,
            )

            t = min(
                1.0,
                (
                    distance
                    - WIND_WALL_ZERO_FLOW_TILES
                )
                / wake_span,
            )

            # Smooth recovery from zero, never jumping abruptly behind wall.
            smooth_t = (
                t
                * t
                * (
                    3.0
                    - 2.0
                    * t
                )
            )

            forward_factor = (
                WIND_WALL_RECOVERY_FACTOR_V23
                * smooth_t
            )

        sheltered_x = (
            wind_x
            * forward_factor
        )

        time_value = float(
            getattr(
                self.time_system,
                "elapsed_real_seconds",
                0.0,
            )
        )

        phase = (
            tx
            * 0.91
            + ty
            * 1.37
            + time_value
            * 2.8
        )

        # Wake turbulence is transverse, not a fake continuation of the
        # incoming horizontal wind. Keep it deliberately tiny.
        turbulence = (
            math.sin(
                phase
            )
            * abs(
                wind_x
            )
            * WIND_WALL_TURBULENCE_RATIO_V23
            * (
                1.0
                - forward_factor
            )
        )

        vertical_factor = min(
            1.0,
            forward_factor
            + 0.08,
        )

        return (
            sheltered_x,
            wind_y
            * vertical_factor
            + turbulence,
        )

    def is_underground_world(self, x, y):
        """True when a world-space point is below the outdoor terrain surface.

        Caves/tunnels below the first surface tile do not receive ambient
        horizontal weather wind.  The column surface lookup is cached by
        TileWorld, so this is cheap enough for player physics and VFX.
        """
        tx = int(float(x) // TILE_SIZE)
        ty = int(float(y) // TILE_SIZE)
        if not (0 <= tx < self.world.width_tiles):
            return False
        surface_row = self._wind_surface_row(tx)
        if surface_row is None:
            return False
        return ty > int(surface_row)

    def wind_at_world(
        self,
        x,
        y,
    ):
        size = (
            TILE_SIZE
            * CHUNK_SIZE
        )

        base_x, base_y = (
            self.wind.wind_at_chunk(
                int(
                    x
                    // size
                ),
                int(
                    y
                    // size
                ),
            )
        )

        # Local chemistry-created airflow is sparse and only sampled at the
        # queried tile; no world scan is involved.
        rtx = int(float(x) // TILE_SIZE)
        rty = int(float(y) // TILE_SIZE)
        reaction_wind = float(self.state.reaction_wind.get((rtx, rty), 0.0))
        if reaction_wind > 0.0 and not self.is_underground_world(x, y):
            base_x = float(base_x) + reaction_wind * 42.0

        # V0.6.5: ambient horizontal weather wind does not propagate through
        # underground caves.  Only an existing upward convection component
        # (negative world-Y), produced by combustion/fire lift, is preserved.
        if self.is_underground_world(x, y):
            return (
                0.0,
                min(0.0, float(base_y)),
            )

        return self._wind_wall_shadow(
            float(
                x
            ),
            float(
                y
            ),
            float(
                base_x
            ),
            float(
                base_y
            ),
        )

    def stats(self):
        return {
            "water": len(self.state.water),
            "fire": len(self.state.fire),
            "soil": len(self.state.soil),
            "plants": sum(1 for p in self.state.plants.values() if p.alive),
            "steam": len(self.state.steam),
            "poison_gas": len(self.state.poison_gas),
            "acid": len(self.state.acid),
            "poison_liquid": len(self.state.poison_liquid),
            "reaction_queue": len(self.reactions.queue),
            "vapor": sum(self.state.vapor.values()),
            "groundwater": sum(self.state.groundwater.values()),
        }
