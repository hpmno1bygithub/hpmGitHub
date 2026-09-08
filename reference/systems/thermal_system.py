# -*- coding: utf-8 -*-
from config import (
    THERMAL_HZ,
    THERMAL_ACTIVE_HALO_TILES,
    THERMAL_PERF_RADIUS_X,
    THERMAL_PERF_RADIUS_Y,
    THERMAL_BACKGROUND_PHASE_HZ,
    THERMAL_BACKGROUND_MAX_WATER_CELLS,
    THERMAL_BACKGROUND_MAX_ICE_CELLS,
    CLIMATE_PROFILES,
    CLIMATE_PROFILE_BY_CHUNK_X,
    THERMAL_DIFFUSION_RATE,
    THERMAL_AIR_RELAX_RATE,
    THERMAL_SOLAR_SURFACE_RATE,
    THERMAL_FIRE_HEAT_RATE,
    THERMAL_FIRE_NEIGHBOR_FACTOR,
    THERMAL_WATER_EXCHANGE_RATE,
    THERMAL_OBJECT_EXCHANGE_RATE,
    THERMAL_WATER_COOLING_FACTOR,
    UNDERGROUND_GEOTHERMAL_MIN_C,
    UNDERGROUND_GEOTHERMAL_C_PER_TILE,
    UNDERGROUND_GEOTHERMAL_MAX_C,
    WATER_FREEZE_C,
    WATER_BOIL_C,
    WATER_FLASH_BOIL_C,
    WATER_FREEZE_MIN_AMOUNT,
    ICE_FREEZE_PROGRESS_PER_C_PER_SEC,
    ICE_MELT_PROGRESS_PER_C_PER_SEC,
    WATER_BOIL_RATE_PER_C_PER_SEC,
    FIRE_NOMINAL_TEMP_C,
    ICE_TILE_WATER_MASS,
    CHUNK_SIZE,
    TILE_SIZE,
    WATER_MASS_UNITS_PER_TILE,
    EVAP_UPDRAFT_PER_WATER_UNIT,
    FIREBALL_MELTED_WATER_MIN_C,
    CLIMATE_ICE_MELT_MULTIPLIER,
    CLIMATE_WATER_FREEZE_MULTIPLIER,
    THERMAL_EXPOSED_ICE_RELAX_RATE,
    ALPINE_SURFACE_FREEZE_MIN_AMOUNT,
    ALPINE_WATERBALL_LANDING_TEMP_C,
    POLAR_WATERBALL_LANDING_TEMP_C,
    ICE_ACCRETION_RATE_PER_SEC,
    COLD_SURFACE_RESERVOIR_MAX_MASS,
    ICE_STAGE_MIN_MASS,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import (
    AIR,
    ICE,
    tile_def,
)
from world.ice_layers import ice_stage_for_mass


class ThermalSystem:
    """Per-tile thermal simulation.

    State:
        env.temperature[(tx, ty)]       -> ambient/terrain temperature C
        env.water_temperature[(tx,ty)] -> liquid water temperature C
        object.temperature_c            -> dynamic object temperature C
        FireCell.temperature_c          -> fire temperature C

    The diffusion pass exchanges heat pair-wise, so diffusion itself does
    not create heat. Sun/fire are explicit heat sources. Water/objects
    exchange heat with their local tile.

    Climate profile is assigned per chunk and controls the meaning of
    sunlight for polar/rainforest/desert/etc. regions.
    """

    def __init__(
        self,
        world,
        env,
        chunk_streamer,
        weather_system,
        time_system=None,
        energy_ledger=None,
    ):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system
        self.time_system = time_system
        self.energy_ledger = energy_ledger

        # V0.6.2: two 6 Hz phases offset by half a period. Diffusion/heat
        # exchange and climate/phase-change no longer stack into one CPU spike.
        self.task = FixedRateTask(THERMAL_HZ, max_steps=3)  # compatibility
        self.diffusion_task = FixedRateTask(THERMAL_HZ, max_steps=2, phase=0.0)
        self.boundary_task = FixedRateTask(THERMAL_HZ, max_steps=2, phase=0.5)
        # Low-frequency sparse phase simulation for dynamic water/ice outside
        # the local thermal window. Full diffusion still sleeps off-screen.
        self.background_task = FixedRateTask(
            THERMAL_BACKGROUND_PHASE_HZ, max_steps=1, phase=0.75
        )
        self._background_water_cursor = 0
        self._background_ice_cursor = 0

        self.initialized = False

    # --------------------------------------------------------
    # Climate / initialization
    # --------------------------------------------------------

    def climate_profile_for_chunk(
        self,
        cx,
        cy,
    ):
        profile_id = self.env.climate_at_chunk(
            cx,
            cy,
            CLIMATE_PROFILE_BY_CHUNK_X.get(
                int(cx),
                "temperate",
            ),
        )

        return (
            profile_id,
            CLIMATE_PROFILES.get(
                profile_id,
                CLIMATE_PROFILES[
                    "temperate"
                ],
            ),
        )

    def register_climate_profile(
        self,
        profile_id,
        base_temp_c,
        solar_gain_c,
        night_temp_c=None,
        humidity=0.50,
    ):
        """Map-authoring API for custom thermal regions."""
        profile_id = str(
            profile_id
        )

        if night_temp_c is None:
            night_temp_c = float(
                base_temp_c
            ) - 6.0

        CLIMATE_PROFILES[
            profile_id
        ] = {
            "base_temp_c": float(
                base_temp_c
            ),
            "solar_gain_c": float(
                solar_gain_c
            ),
            "night_temp_c": float(
                night_temp_c
            ),
            "humidity": max(
                0.0,
                min(
                    1.0,
                    float(
                        humidity
                    ),
                ),
            ),
        }

        return profile_id

    def set_climate_region(
        self,
        cx0,
        cy0,
        cx1,
        cy1,
        profile_id,
    ):
        if profile_id not in CLIMATE_PROFILES:
            raise ValueError(
                "Unknown climate profile: "
                + str(
                    profile_id
                )
            )

        for cy in range(
            min(
                cy0,
                cy1,
            ),
            max(
                cy0,
                cy1,
            )
            + 1,
        ):
            for cx in range(
                min(
                    cx0,
                    cx1,
                ),
                max(
                    cx0,
                    cx1,
                )
                + 1,
            ):
                self.env.climate[
                    (
                        int(cx),
                        int(cy),
                    )
                ] = str(
                    profile_id
                )

    def _underground_geothermal_floor_c(self, tx, ty):
        """Minimum natural cave temperature at this depth.

        The surface climate may be polar, alpine, rainforest, etc., but that
        boundary is not copied unchanged through the whole vertical column.
        Explicit heat/cold sources are still free to move a cell away from this
        value; the floor is used only for the large-scale natural climate target
        and initial state.
        """
        try:
            depth = int(self.world.underground_depth(int(tx), int(ty)))
        except Exception:
            surface = self.world.backdrop_surface_row(int(tx))
            depth = 0 if surface is None else max(0, int(ty) - int(surface))
        if depth <= 0:
            return None
        return min(
            float(UNDERGROUND_GEOTHERMAL_MAX_C),
            float(UNDERGROUND_GEOTHERMAL_MIN_C)
            + float(depth) * float(UNDERGROUND_GEOTHERMAL_C_PER_TILE),
        )

    def _initial_temperature_for_tile(self, tx, ty):
        cx = int(tx) // CHUNK_SIZE
        cy = int(ty) // CHUNK_SIZE
        _profile_id, profile = self.climate_profile_for_chunk(cx, cy)
        natural = float(profile["base_temp_c"])
        geothermal = self._underground_geothermal_floor_c(tx, ty)
        if geothermal is not None:
            natural = max(natural, geothermal)
        return natural

    def _ensure_temperature_bounds(self, bounds):
        x0,y0,x1,y1=bounds
        for ty in range(int(y0), int(y1)+1):
            for tx in range(int(x0), int(x1)+1):
                key=(tx,ty)
                if key not in self.env.temperature:
                    self.env.temperature[key]=self._initial_temperature_for_tile(tx,ty)
        for (tx,ty), amount in tuple(self.env.water.items()):
            if amount > 0.0 and x0 <= tx <= x1 and y0 <= ty <= y1:
                self.env.water_temperature.setdefault((tx,ty),self.env.temperature.get((tx,ty),20.0))

    def initialize_world(self):
        """Lazy thermal bootstrap: do not allocate one Python float per world tile.

        Climate defaults are derivable from chunk coordinates. Temperatures are
        materialized only when a local thermal window becomes active.
        """
        # Preserve authored climate entries; default profiles remain implicit.
        for (tx,ty), amount in tuple(self.env.water.items()):
            if amount > 0.0:
                self.env.water_temperature.setdefault((tx,ty),20.0)
        self.initialized=True

    # --------------------------------------------------------
    # Thermal properties
    # --------------------------------------------------------

    def _tile_capacity(
        self,
        tx,
        ty,
    ):
        td = tile_def(
            self.world.get_tile(
                tx,
                ty,
            )
        )

        capacity = max(
            0.15,
            float(
                td.heat_capacity
            ),
        )

        # Wet soil and standing water increase thermal inertia.
        soil = self.env.soil.get(
            (
                tx,
                ty,
            )
        )

        if soil is not None:
            capacity += (
                max(
                    0.0,
                    min(
                        1.0,
                        soil.moisture,
                    ),
                )
                * 1.55
            )

        water = self.env.water_amount(
            tx,
            ty,
        )

        capacity += (
            water
            * 3.20
        )

        return capacity

    def _conductivity(
        self,
        ax,
        ay,
        bx,
        by,
    ):
        a = tile_def(
            self.world.get_tile(
                ax,
                ay,
            )
        )

        b = tile_def(
            self.world.get_tile(
                bx,
                by,
            )
        )

        return max(
            0.02,
            (
                float(
                    a.thermal_conductivity
                )
                + float(
                    b.thermal_conductivity
                )
            )
            * 0.5,
        )

    def _active_bounds(self):
        """Thermal work window centered on the player chunk.

        Collision/water streaming intentionally keeps a wider active region,
        but full per-tile diffusion across that whole 5x3 chunk rectangle was
        producing 15-30 ms Python spikes. Heat diffusion is slow, so V0.6.2
        keeps a smaller 3x3 thermal neighborhood and lets distant cells sleep.
        """
        if not self.chunk_streamer.active_chunks:
            return (
                0,
                0,
                self.world.width_tiles - 1,
                self.world.height_tiles - 1,
            )

        center_cx, center_cy = getattr(
            self.chunk_streamer,
            "player_chunk",
            (0, 0),
        )
        max_world_cx = max(0, (self.world.width_tiles - 1) // CHUNK_SIZE)
        max_world_cy = max(0, (self.world.height_tiles - 1) // CHUNK_SIZE)
        min_cx = max(0, int(center_cx) - int(THERMAL_PERF_RADIUS_X))
        max_cx = min(max_world_cx, int(center_cx) + int(THERMAL_PERF_RADIUS_X))
        min_cy = max(0, int(center_cy) - int(THERMAL_PERF_RADIUS_Y))
        max_cy = min(max_world_cy, int(center_cy) + int(THERMAL_PERF_RADIUS_Y))
        halo = int(THERMAL_ACTIVE_HALO_TILES)

        return (
            max(0, min_cx * CHUNK_SIZE - halo),
            max(0, min_cy * CHUNK_SIZE - halo),
            min(self.world.width_tiles - 1, (max_cx + 1) * CHUNK_SIZE - 1 + halo),
            min(self.world.height_tiles - 1, (max_cy + 1) * CHUNK_SIZE - 1 + halo),
        )

    def _sky_exposed(
        self,
        tx,
        ty,
    ):
        # V0.5.9: use TileWorld's dirty column surface cache instead of
        # repeatedly scanning every tile above this cell.
        return self.world.is_sky_exposed(tx, ty)

    # --------------------------------------------------------
    # Heat diffusion and sources
    # --------------------------------------------------------

    def _diffuse(
        self,
        dt,
        bounds,
    ):
        x0, y0, x1, y1 = bounds

        # Work with an energy-like delta so pair exchange is symmetric.
        energy_delta = {}

        def add_delta(key, value):
            energy_delta[key] = (
                energy_delta.get(
                    key,
                    0.0,
                )
                + value
            )

        for ty in range(
            y0,
            y1 + 1,
        ):
            for tx in range(
                x0,
                x1 + 1,
            ):
                ta = self.env.temperature_at(
                    tx,
                    ty,
                )

                for nx, ny in (
                    (
                        tx + 1,
                        ty,
                    ),
                    (
                        tx,
                        ty + 1,
                    ),
                ):
                    if (
                        nx > x1
                        or ny > y1
                    ):
                        continue

                    tb = self.env.temperature_at(
                        nx,
                        ny,
                    )

                    conduct = self._conductivity(
                        tx,
                        ty,
                        nx,
                        ny,
                    )

                    q = (
                        (
                            tb
                            - ta
                        )
                        * conduct
                        * THERMAL_DIFFUSION_RATE
                        * dt
                    )

                    add_delta(
                        (
                            tx,
                            ty,
                        ),
                        q,
                    )
                    add_delta(
                        (
                            nx,
                            ny,
                        ),
                        -q,
                    )

        for (
            tx,
            ty
        ), q in energy_delta.items():
            capacity = self._tile_capacity(
                tx,
                ty,
            )

            self.env.set_temperature(
                tx,
                ty,
                self.env.temperature_at(
                    tx,
                    ty,
                )
                + q
                / capacity,
            )

    def _apply_climate_and_sun(
        self,
        dt,
        bounds,
    ):
        x0, y0, x1, y1 = bounds

        # Values below are constant for every tile in the same chunk during
        # one thermal step. V0.6.1 recomputed climate profile, weather lookup
        # and daylight_factor thousands of times per pass.
        daylight_global = (
            self.time_system.daylight_factor()
            if self.time_system is not None
            else None
        )
        chunk_cache = {}

        for ty in range(y0, y1 + 1):
            cy = ty // CHUNK_SIZE
            for tx in range(x0, x1 + 1):
                cx = tx // CHUNK_SIZE
                ckey = (cx, cy)
                cached = chunk_cache.get(ckey)
                if cached is None:
                    profile_id, profile = self.climate_profile_for_chunk(cx, cy)
                    weather = self.weather_system.weather_at_chunk(cx, cy)
                    sunlight = max(0.0, min(1.0, float(weather.sunlight)))
                    daylight = (
                        float(daylight_global)
                        if daylight_global is not None
                        else sunlight
                    )
                    base_c = float(profile["base_temp_c"])
                    night_c = float(profile.get("night_temp_c", base_c - 6.0))
                    solar_gain = float(profile["solar_gain_c"])
                    regional_target = (
                        night_c
                        + (base_c - night_c) * daylight
                        + solar_gain * sunlight
                    )
                    cached = (profile_id, sunlight, solar_gain, regional_target)
                    chunk_cache[ckey] = cached

                profile_id, sunlight, solar_gain, regional_target = cached

                # V0.7.2.2: surface climate is an atmospheric boundary, not a
                # vertical refrigerator.  Caves below the immutable original
                # terrain line relax toward at least the geothermal floor.
                geothermal = self._underground_geothermal_floor_c(tx, ty)
                if geothermal is not None:
                    regional_target = max(float(regional_target), float(geothermal))
                    # There is no direct sun underground even if the live
                    # foreground column was mined open later.
                    sunlight = 0.0

                current = self.env.temperature_at(tx, ty)
                td = tile_def(self.world.get_tile(tx, ty))

                # Air naturally exchanges heat with the large-scale regional
                # climate boundary. This is an external energy flux.
                if not td.solid:
                    climate_delta = (
                        regional_target - current
                    ) * (THERMAL_AIR_RELAX_RATE * dt)
                    current += climate_delta
                    if self.energy_ledger is not None:
                        self.energy_ledger.record(
                            "climate_exchange",
                            climate_delta * self._tile_capacity(tx, ty),
                        )

                # Direct solar heating only reaches sky-exposed cells.
                sky_exposed = self._sky_exposed(tx, ty)
                if sky_exposed and self.world.get_tile(tx, ty) == ICE:
                    # ICE is solid, so old builds never relaxed it toward the
                    # desert air boundary.  Give exposed ice a weak convective
                    # coupling; desert does this several times faster than
                    # temperate grassland, alpine/polar much slower.
                    climate_heat = float(CLIMATE_ICE_MELT_MULTIPLIER.get(profile_id, 1.0))
                    current += (
                        float(regional_target) - current
                    ) * min(
                        1.0,
                        float(THERMAL_EXPOSED_ICE_RELAX_RATE)
                        * climate_heat
                        * float(dt),
                    )

                if sky_exposed:
                    solar = (
                        solar_gain
                        * sunlight
                        * float(td.solar_absorption)
                        * THERMAL_SOLAR_SURFACE_RATE
                        * dt
                    )
                    water = self.env.water_amount(tx, ty)
                    solar *= max(
                        0.25,
                        1.0 - water * THERMAL_WATER_COOLING_FACTOR,
                    )
                    capacity = self._tile_capacity(tx, ty)
                    current += solar / capacity
                    if self.energy_ledger is not None:
                        self.energy_ledger.record(
                            "solar_in",
                            max(0.0, solar),
                        )

                self.env.set_temperature(tx, ty, current)

    def _apply_fire_heat(
        self,
        dt,
    ):
        for (
            tx,
            ty
        ), fire in list(
            self.env.fire.items()
        ):
            temp_c = float(
                getattr(
                    fire,
                    "temperature_c",
                    FIRE_NOMINAL_TEMP_C,
                )
            )

            # Burning flame tends toward its nominal hot state.
            fire.temperature_c += (
                FIRE_NOMINAL_TEMP_C
                - temp_c
            ) * min(
                1.0,
                0.45
                * dt,
            )

            local = self.env.temperature_at(
                tx,
                ty,
            )

            heat = (
                THERMAL_FIRE_HEAT_RATE
                * max(
                    0.05,
                    fire.intensity,
                )
                * dt
            )

            if self.energy_ledger is not None:
                self.energy_ledger.record(
                    "fire_heat_in",
                    heat,
                )

            self.env.set_temperature(
                tx,
                ty,
                local
                + heat,
            )

            for nx, ny in (
                (
                    tx - 1,
                    ty,
                ),
                (
                    tx + 1,
                    ty,
                ),
                (
                    tx,
                    ty - 1,
                ),
                (
                    tx,
                    ty + 1,
                ),
            ):
                if not (
                    0 <= nx
                    < self.world.width_tiles
                    and 0 <= ny
                    < self.world.height_tiles
                ):
                    continue

                self.env.set_temperature(
                    nx,
                    ny,
                    self.env.temperature_at(
                        nx,
                        ny,
                    )
                    + heat
                    * THERMAL_FIRE_NEIGHBOR_FACTOR,
                )

    def _exchange_water_heat(
        self,
        dt,
        bounds,
    ):
        x0, y0, x1, y1 = bounds
        # Water outside the local thermal window sleeps; do not scan and
        # exchange heat for every puddle ever created in the world.
        for (tx, ty), amount in tuple(self.env.water.items()):
            if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                continue
            if amount <= 0.0:
                self.env.water_temperature.pop(
                    (
                        tx,
                        ty,
                    ),
                    None,
                )
                continue

            ambient = self.env.temperature_at(
                tx,
                ty,
            )

            water_temp = (
                self.env.water_temperature_at(
                    tx,
                    ty,
                    ambient,
                )
            )

            difference = (
                ambient
                - water_temp
            )

            # Approximate heat exchange with different heat capacities.
            q = (
                difference
                * THERMAL_WATER_EXCHANGE_RATE
                * dt
                * max(
                    0.08,
                    amount,
                )
            )

            water_capacity = max(
                0.25,
                amount
                * 4.18,
            )

            tile_capacity = self._tile_capacity(
                tx,
                ty,
            )

            water_temp += (
                q
                / water_capacity
            )

            ambient -= (
                q
                / tile_capacity
            )

            self.env.set_water_temperature(
                tx,
                ty,
                water_temp,
            )

            self.env.set_temperature(
                tx,
                ty,
                ambient,
            )

    def exchange_object_temperature(
        self,
        objects,
        dt,
    ):
        for obj in objects:
            if not getattr(
                obj,
                "active",
                True,
            ):
                continue

            tx = int(
                obj.x
                // TILE_SIZE
            )
            ty = int(
                obj.y
                // TILE_SIZE
            )

            if not (
                0 <= tx
                < self.world.width_tiles
                and 0 <= ty
                < self.world.height_tiles
            ):
                continue

            ambient = self.env.temperature_at(
                tx,
                ty,
            )

            current = float(
                getattr(
                    obj,
                    "temperature_c",
                    ambient,
                )
            )

            current += (
                ambient
                - current
            ) * min(
                1.0,
                THERMAL_OBJECT_EXCHANGE_RATE
                * dt,
            )

            obj.temperature_c = current

    def is_naturally_freezing_surface(self, tx, ty):
        """True for exposed alpine/polar surface where climate itself freezes.

        Underground geothermal cells are excluded.  In these surface regions
        ambient climate never melts ice by itself; explicit fire/fireball heat
        is required for thawing.
        """
        tx = int(tx); ty = int(ty)
        if self._underground_geothermal_floor_c(tx, ty) is not None:
            return False
        profile_id, profile = self.climate_profile_for_chunk(tx // CHUNK_SIZE, ty // CHUNK_SIZE)
        if profile_id not in ("alpine", "polar"):
            return False
        return float(profile.get("base_temp_c", 20.0)) <= float(WATER_FREEZE_C)

    def _has_active_fire_heat(self, tx, ty):
        for key in ((tx, ty), (tx-1, ty), (tx+1, ty), (tx, ty-1), (tx, ty+1)):
            fire = self.env.fire.get(key)
            if fire is not None and float(getattr(fire, "intensity", 0.0)) > 0.01:
                return True
        return False

    def _deposit_overflow_above(self, tx, ty, amount, temperature_c=None):
        """Conservatively return phase-change overflow to cells above."""
        left = max(0.0, float(amount))
        ay = int(ty) - 1
        while left > 1e-9 and ay >= 0:
            if self.world.get_tile(int(tx), ay) != AIR:
                ay -= 1
                continue
            current = self.env.water_amount(int(tx), ay)
            room = max(0.0, 1.0 - current)
            moved = min(room, left)
            if moved > 0.0:
                self.env.set_water(int(tx), ay, current + moved)
                if temperature_c is not None:
                    self.env.water_temperature[(int(tx), ay)] = float(temperature_c)
                left -= moved
            ay -= 1
        # In an impossibly full vertical column, keep any residual as hidden
        # cold-surface mass rather than silently deleting matter.
        if left > 1e-9:
            self.env.add_cold_surface_water(int(tx), int(ty), left)
        return left

    def _materialize_cold_surface_mass(self, tx, ty, total_mass, temperature_c=-2.0):
        total = max(0.0, float(total_mass))
        if total < float(ALPINE_SURFACE_FREEZE_MIN_AMOUNT):
            self.env.set_cold_surface_water(tx, ty, total)
            self.env.set_water(tx, ty, 0.0)
            return False
        if self.world.get_tile(tx, ty) != AIR:
            self.env.set_cold_surface_water(tx, ty, total)
            self.env.set_water(tx, ty, 0.0)
            return False
        ice_mass = min(1.0, total)
        overflow = max(0.0, total - ice_mass)
        self.env.set_cold_surface_water(tx, ty, 0.0)
        self.env.set_water(tx, ty, 0.0)
        self.world.set_tile(tx, ty, ICE, track_change=True)
        self.env.ice_mass[(tx, ty)] = ice_mass
        self.env.set_temperature(tx, ty, min(float(WATER_FREEZE_C), float(temperature_c)))
        self.env.water_temperature.pop((tx, ty), None)
        self.env.water_freeze_progress.pop((tx, ty), None)
        if overflow > 1e-9:
            self._deposit_overflow_above(tx, ty, overflow, temperature_c)
        return True

    # --------------------------------------------------------
    # Phase changes
    # --------------------------------------------------------

    def _freeze_water_cell(
        self,
        tx,
        ty,
        min_amount=None,
    ):
        amount = self.env.water_amount(
            tx,
            ty,
        )

        if min_amount is None:
            min_amount = WATER_FREEZE_MIN_AMOUNT

        if (
            amount
            < float(min_amount)
        ):
            return (
                False,
                float(amount),
            )

        if (
            self.world.get_tile(
                tx,
                ty,
            )
            != AIR
        ):
            return (
                False,
                float(amount),
            )

        cold = self.env.water_temperature_at(
            tx,
            ty,
            WATER_FREEZE_C,
        )

        # Exact mass stored in ICE tile; do not round it up to 1.0.
        self.env.set_water(
            tx,
            ty,
            0.0,
        )

        self.world.set_tile(
            tx,
            ty,
            ICE,
            track_change=True,
        )

        self.env.ice_mass[
            (
                tx,
                ty,
            )
        ] = float(
            amount
        )

        self.env.set_temperature(
            tx,
            ty,
            min(
                WATER_FREEZE_C,
                cold,
            ),
        )

        self.env.water_temperature.pop(
            (
                tx,
                ty,
            ),
            None,
        )

        self.env.water_freeze_progress.pop(
            (
                tx,
                ty,
            ),
            None,
        )

        return True

    def _melt_ice_cell(
        self,
        tx,
        ty,
        temperature_c=None,
    ):
        if (
            self.world.get_tile(
                tx,
                ty,
            )
            != ICE
        ):
            return 0.0

        mass = float(
            self.env.ice_mass.pop(
                (
                    tx,
                    ty,
                ),
                ICE_TILE_WATER_MASS,
            )
        )

        self.world.set_tile(
            tx,
            ty,
            AIR,
            track_change=True,
        )

        self.env.ice_melt_progress.pop(
            (
                tx,
                ty,
            ),
            None,
        )

        if mass > 0.0:
            self.env.set_water(
                tx,
                ty,
                min(1.0, mass),
            )

            self.env.water_temperature[
                (
                    tx,
                    ty,
                )
            ] = (
                WATER_FREEZE_C
                if temperature_c is None
                else float(
                    temperature_c
                )
            )

            overflow = max(
                0.0,
                mass
                - 1.0,
            )

            if overflow > 0.0:
                # An ice tile normally never exceeds 1.0, but keep the
                # transfer conservative if future magic creates a larger one.
                return overflow

        return 0.0

    def fireball_heat_ice(
        self,
        tx,
        ty,
        rank=1,
        fireball_temperature_c=520.0,
    ):
        """Advance ice exactly one phase: ICE -> liquid WATER.

        V0.7.2.3 removes the old high-rank ICE -> VAPOR shortcut.  A fireball
        that hits ice spends that impact melting it.  A subsequent fireball
        may then evaporate the resulting liquid through the normal water-hit
        path, which makes the state order visible and prevents free
        ICE/WATER refreeze loops.
        """
        if self.world.get_tile(tx, ty) != ICE:
            return "none"

        melt_temp = max(
            float(FIREBALL_MELTED_WATER_MIN_C),
            min(
                35.0,
                max(
                    3.0,
                    float(fireball_temperature_c) * 0.035,
                ),
            ),
        )

        self._melt_ice_cell(
            tx,
            ty,
            temperature_c=melt_temp,
        )

        self.env.set_temperature(
            tx,
            ty,
            max(
                self.env.temperature_at(tx, ty),
                melt_temp,
            ),
        )

        # Clear any stale freeze-progress accumulated before this melt.  The
        # liquid is warm ordinary water and will not instantly freeze merely
        # because another ice tile is adjacent.
        self.env.water_freeze_progress.pop((tx, ty), None)
        return "water"

    def create_ice_tile(
        self,
        tx,
        ty,
        water_mass,
        temperature_c=-15.0,
    ):
        if not (
            0 <= tx < self.world.width_tiles
            and 0 <= ty < self.world.height_tiles
        ):
            return (False, float(water_mass))

        if self.world.get_tile(tx, ty) != AIR:
            return (False, float(water_mass))

        mass = max(0.0, min(1.0, float(water_mass)))
        if mass <= 0.0:
            return (False, 0.0)

        existing = self.env.water_amount(tx, ty)

        # Existing liquid in this cell becomes part of the solid ice; if
        # total exceeds one tile, return extra to free water after freezing.
        total = mass + existing
        ice_mass = min(1.0, total)
        leftover = max(0.0, total - ice_mass)

        self.env.set_water(tx, ty, 0.0)
        self.world.set_tile(tx, ty, ICE, track_change=True)
        self.env.ice_mass[(tx, ty)] = ice_mass
        self.env.set_temperature(tx, ty, float(temperature_c))
        self.env.water_temperature.pop((tx, ty), None)
        self.env.water_freeze_progress.pop((tx, ty), None)
        return (True, leftover)

    def _phase_changes(
        self,
        dt,
        bounds,
    ):
        x0, y0, x1, y1 = bounds

        # Liquid freeze/boil.
        for (
            tx,
            ty
        ), amount in list(
            self.env.water.items()
        ):
            if not (
                x0 <= tx <= x1
                and y0 <= ty <= y1
            ):
                continue

            water_temp = (
                self.env.water_temperature_at(
                    tx,
                    ty,
                )
            )

            key = (
                tx,
                ty,
            )

            profile_id, _profile = self.climate_profile_for_chunk(
                int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE
            )
            freeze_min_amount = (
                float(ALPINE_SURFACE_FREEZE_MIN_AMOUNT)
                if profile_id in ("alpine", "polar")
                else float(WATER_FREEZE_MIN_AMOUNT)
            )

            # V0.7.4.2 freezing-surface reservoir. Very shallow water on snow
            # should not appear as a blue puddle that never reaches an ice
            # threshold. It is stored as conserved local frost/wet-snow mass
            # until enough accumulates to show low ice.
            natural_freeze = (
                self.is_naturally_freezing_surface(tx, ty)
                and not self._has_active_fire_heat(tx, ty)
            )
            below_key = (int(tx), int(ty) + 1)
            below_tile = (
                self.world.get_tile(*below_key)
                if below_key[1] < self.world.height_tiles
                else None
            )
            supported = below_tile is not None and tile_def(below_tile).solid
            if natural_freeze and supported:
                hidden = self.env.cold_surface_amount(tx, ty)
                total_surface = max(0.0, float(amount)) + max(0.0, float(hidden))

                # If a partial ice layer is directly below, feed it first.
                if below_tile == ICE:
                    old_mass = max(0.0, min(1.0, float(
                        self.env.ice_mass.get(below_key, ICE_TILE_WATER_MASS)
                    )))
                    capacity = max(0.0, 1.0 - old_mass)
                    transfer = min(capacity, total_surface)
                    if transfer > 1e-9:
                        old_stage = ice_stage_for_mass(old_mass)
                        new_mass = old_mass + transfer
                        self.env.ice_mass[below_key] = new_mass
                        self.env.set_temperature(
                            below_key[0], below_key[1],
                            min(self.env.temperature_at(*below_key), WATER_FREEZE_C)
                        )
                        if ice_stage_for_mass(new_mass) != old_stage:
                            try:
                                self.world.mark_tile_visual_dirty(*below_key)
                            except Exception:
                                pass
                        total_surface -= transfer

                self.env.set_water(tx, ty, 0.0)
                self.env.set_cold_surface_water(tx, ty, 0.0)

                if total_surface < float(freeze_min_amount):
                    self.env.set_cold_surface_water(tx, ty, total_surface)
                    self.env.water_freeze_progress.pop(key, None)
                    continue

                if self._materialize_cold_surface_mass(
                    tx, ty, total_surface,
                    temperature_c=min(float(water_temp), -0.5),
                ):
                    continue

                # Could not create ice in this cell; keep mass hidden.
                self.env.set_cold_surface_water(tx, ty, total_surface)
                continue

            # V0.7.4.1: cold water sitting on a partial ice layer thickens
            # that SAME layer first.  This produces low -> mid -> high ice
            # instead of spawning a second full-height ice tile above a shallow
            # frozen puddle. Exact mass is conserved in env.ice_mass.
            below_key = (int(tx), int(ty) + 1)
            if (
                water_temp < WATER_FREEZE_C
                and profile_id in ("alpine", "polar")
                and below_key[1] < self.world.height_tiles
                and self.world.get_tile(*below_key) == ICE
            ):
                old_mass = max(0.0, min(1.0, float(
                    self.env.ice_mass.get(below_key, ICE_TILE_WATER_MASS)
                )))
                capacity = max(0.0, 1.0 - old_mass)
                if capacity > 1e-9 and amount > 1e-9:
                    transfer_cap = (
                        float(ICE_ACCRETION_RATE_PER_SEC)
                        * float(CLIMATE_WATER_FREEZE_MULTIPLIER.get(profile_id, 1.0))
                        * float(dt)
                    )
                    transfer = min(float(amount), capacity, max(0.0, transfer_cap))
                    if transfer > 1e-9:
                        old_stage = ice_stage_for_mass(old_mass)
                        new_mass = min(1.0, old_mass + transfer)
                        self.env.ice_mass[below_key] = new_mass
                        self.env.set_water(tx, ty, max(0.0, float(amount) - transfer))
                        self.env.set_temperature(
                            below_key[0], below_key[1],
                            min(self.env.temperature_at(*below_key), float(water_temp), WATER_FREEZE_C)
                        )
                        amount = self.env.water_amount(tx, ty)
                        if ice_stage_for_mass(new_mass) != old_stage:
                            try:
                                self.world.mark_tile_visual_dirty(*below_key)
                            except Exception:
                                pass
                        if amount <= 1e-9:
                            self.env.water_freeze_progress.pop(key, None)
                            continue

            if (
                water_temp
                < WATER_FREEZE_C
                and amount
                >= freeze_min_amount
            ):
                progress = (
                    self.env.water_freeze_progress.get(
                        key,
                        0.0,
                    )
                    + (
                        WATER_FREEZE_C
                        - water_temp
                    )
                    * ICE_FREEZE_PROGRESS_PER_C_PER_SEC
                    * float(CLIMATE_WATER_FREEZE_MULTIPLIER.get(profile_id, 1.0))
                    * dt
                )

                self.env.water_freeze_progress[
                    key
                ] = progress

                if progress >= 1.0:
                    self._freeze_water_cell(
                        tx,
                        ty,
                        min_amount=freeze_min_amount,
                    )
                    continue
            else:
                self.env.water_freeze_progress.pop(
                    key,
                    None,
                )

            if (
                water_temp
                >= WATER_BOIL_C
            ):
                if (
                    water_temp
                    >= WATER_FLASH_BOIL_C
                ):
                    boil = amount
                else:
                    boil = min(
                        amount,
                        (
                            water_temp
                            - WATER_BOIL_C
                            + 1.0
                        )
                        * WATER_BOIL_RATE_PER_C_PER_SEC
                        * dt,
                    )

                if boil > 0.0:
                    self.env.set_water(
                        tx,
                        ty,
                        amount
                        - boil,
                    )

                    self.env.add_vapor(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        boil,
                    )

                    self.env.add_updraft(
                        tx // CHUNK_SIZE,
                        ty // CHUNK_SIZE,
                        boil
                        * WATER_MASS_UNITS_PER_TILE
                        * EVAP_UPDRAFT_PER_WATER_UNIT
                        * 1.25,
                    )

                    if self.energy_ledger is not None:
                        self.energy_ledger.record(
                            "phase_change",
                            boil * 2.26,
                        )

                    # Boiling consumes sensible heat; remaining water trends
                    # toward its phase-change temperature.
                    if (
                        self.env.water_amount(
                            tx,
                            ty,
                        )
                        > 0.0
                    ):
                        self.env.set_water_temperature(
                            tx,
                            ty,
                            max(
                                WATER_BOIL_C,
                                water_temp
                                - boil
                                * 24.0,
                            ),
                        )

        # Ice melting.
        for ty in range(
            y0,
            y1 + 1,
        ):
            for tx in range(
                x0,
                x1 + 1,
            ):
                if (
                    self.world.get_tile(
                        tx,
                        ty,
                    )
                    != ICE
                ):
                    continue

                # Compatibility repair for V0.7.4.1 saves: an ICE tile whose
                # conserved mass is below the new visible-low-ice threshold
                # must not remain as an invisible solid collision. Return it to
                # the hidden cold surface reservoir (or liquid outside a
                # freezing climate) before any other melt logic.
                existing_ice_mass = max(0.0, float(
                    self.env.ice_mass.get((tx, ty), ICE_TILE_WATER_MASS)
                ))
                if existing_ice_mass < float(ICE_STAGE_MIN_MASS):
                    self.env.ice_mass.pop((tx, ty), None)
                    self.env.ice_melt_progress.pop((tx, ty), None)
                    self.world.set_tile(tx, ty, AIR, track_change=True)
                    if (
                        self.is_naturally_freezing_surface(tx, ty)
                        and not self._has_active_fire_heat(tx, ty)
                    ):
                        self.env.add_cold_surface_water(tx, ty, existing_ice_mass)
                    elif existing_ice_mass > 0.0:
                        self.env.set_water(tx, ty, min(1.0, existing_ice_mass))
                        self.env.water_temperature[(tx, ty)] = max(0.1, WATER_FREEZE_C)
                    continue

                temp_c = self.env.temperature_at(
                    tx,
                    ty,
                )

                key = (
                    tx,
                    ty,
                )

                # In a naturally sub-zero alpine/polar surface climate, solar
                # or numerical diffusion alone cannot thaw ice. Only an active
                # flame/nearby fire (or the direct fireball ICE->WATER path)
                # may melt it.
                if (
                    self.is_naturally_freezing_surface(tx, ty)
                    and not self._has_active_fire_heat(tx, ty)
                ):
                    self.env.ice_melt_progress.pop(key, None)
                    if temp_c > WATER_FREEZE_C:
                        self.env.set_temperature(tx, ty, WATER_FREEZE_C)
                    continue

                if temp_c > WATER_FREEZE_C:
                    profile_id, _profile = self.climate_profile_for_chunk(
                        int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE
                    )
                    melt_multiplier = float(
                        CLIMATE_ICE_MELT_MULTIPLIER.get(profile_id, 1.0)
                    )
                    progress = (
                        self.env.ice_melt_progress.get(
                            key,
                            0.0,
                        )
                        + temp_c
                        * ICE_MELT_PROGRESS_PER_C_PER_SEC
                        * melt_multiplier
                        * dt
                    )

                    self.env.ice_melt_progress[
                        key
                    ] = progress

                    ice_mass_now = max(0.0, min(1.0, float(
                        self.env.ice_mass.get(key, ICE_TILE_WATER_MASS)
                    )))
                    # Thin/medium ice contains less latent mass, so it melts
                    # sooner than a full block under the same heat load.
                    if progress >= max(0.18, ice_mass_now):
                        self._melt_ice_cell(
                            tx,
                            ty,
                            temperature_c=min(
                                12.0,
                                max(
                                    0.1,
                                    temp_c
                                    * 0.25,
                                ),
                            ),
                        )
                else:
                    self.env.ice_melt_progress.pop(
                        key,
                        None,
                    )

    # --------------------------------------------------------
    # Sparse off-screen phase simulation
    # --------------------------------------------------------

    @staticmethod
    def _key_in_bounds(key, bounds):
        tx, ty = int(key[0]), int(key[1])
        x0, y0, x1, y1 = bounds
        return x0 <= tx <= x1 and y0 <= ty <= y1

    def _background_regional_target(self, tx, ty):
        tx = int(tx); ty = int(ty)
        profile_id, profile = self.climate_profile_for_chunk(
            tx // CHUNK_SIZE, ty // CHUNK_SIZE
        )
        base_c = float(profile["base_temp_c"])
        night_c = float(profile.get("night_temp_c", base_c - 6.0))
        solar_gain = float(profile.get("solar_gain_c", 0.0))
        daylight = (
            float(self.time_system.daylight_factor())
            if self.time_system is not None else 1.0
        )
        try:
            _pid, _rain, _cloud, sunlight, _temp, _wind = (
                self.weather_system._weather_values(tx // CHUNK_SIZE, ty // CHUNK_SIZE)
            )
            sunlight = max(0.0, min(1.0, float(sunlight)))
        except Exception:
            sunlight = max(0.0, min(1.0, daylight))
        target = night_c + (base_c - night_c) * daylight + solar_gain * sunlight
        geothermal = self._underground_geothermal_floor_c(tx, ty)
        if geothermal is not None:
            target = max(target, float(geothermal))
        return profile_id, profile, float(target)

    def _background_ice_step(self, dt, active_bounds):
        keys = tuple(getattr(self.env, "ice_mass", {}).keys())
        n = len(keys)
        if n <= 0:
            self._background_ice_cursor = 0
            return 0
        limit = min(n, max(1, int(THERMAL_BACKGROUND_MAX_ICE_CELLS)))
        start = int(self._background_ice_cursor) % n
        processed = 0
        for i in range(limit):
            key = keys[(start + i) % n]
            tx, ty = int(key[0]), int(key[1])
            if self._key_in_bounds(key, active_bounds):
                continue
            if self.world.get_tile(tx, ty) != ICE:
                self.env.ice_mass.pop(key, None)
                self.env.ice_melt_progress.pop(key, None)
                continue

            mass = max(0.0, min(1.0, float(
                self.env.ice_mass.get(key, ICE_TILE_WATER_MASS)
            )))
            if mass < float(ICE_STAGE_MIN_MASS):
                self.env.ice_mass.pop(key, None)
                self.env.ice_melt_progress.pop(key, None)
                self.world.set_tile(tx, ty, AIR, track_change=True)
                if (
                    self.is_naturally_freezing_surface(tx, ty)
                    and not self._has_active_fire_heat(tx, ty)
                ):
                    self.env.add_cold_surface_water(tx, ty, mass)
                elif mass > 0.0:
                    self.env.set_water(tx, ty, mass)
                    self.env.water_temperature[(tx, ty)] = max(0.1, WATER_FREEZE_C)
                processed += 1
                continue

            profile_id, _profile, target = self._background_regional_target(tx, ty)
            if self.is_naturally_freezing_surface(tx, ty) and not self._has_active_fire_heat(tx, ty):
                self.env.ice_melt_progress.pop(key, None)
                self.env.set_temperature(tx, ty, min(WATER_FREEZE_C, target))
                processed += 1
                continue

            current = self.env.temperature_at(tx, ty, WATER_FREEZE_C)
            if self._sky_exposed(tx, ty):
                relax = min(
                    1.0,
                    float(THERMAL_EXPOSED_ICE_RELAX_RATE)
                    * float(CLIMATE_ICE_MELT_MULTIPLIER.get(profile_id, 1.0))
                    * float(dt),
                )
                current += (target - current) * relax

            # Nearby fire must also continue melting ice while off-screen. We
            # use a cheap local heat approximation instead of running global
            # thermal diffusion/fire scans.
            if self._has_active_fire_heat(tx, ty):
                current += float(THERMAL_FIRE_HEAT_RATE) * 0.55 * float(dt)

            self.env.set_temperature(tx, ty, current)

            if current > WATER_FREEZE_C:
                progress = float(self.env.ice_melt_progress.get(key, 0.0))
                progress += (
                    current
                    * ICE_MELT_PROGRESS_PER_C_PER_SEC
                    * float(CLIMATE_ICE_MELT_MULTIPLIER.get(profile_id, 1.0))
                    * float(dt)
                )
                self.env.ice_melt_progress[key] = progress
                if progress >= max(0.18, mass):
                    self._melt_ice_cell(
                        tx, ty,
                        temperature_c=min(12.0, max(0.1, current * 0.25)),
                    )
            else:
                self.env.ice_melt_progress.pop(key, None)
            processed += 1

        self._background_ice_cursor = (start + limit) % max(1, n)
        return processed

    def _background_water_step(self, dt, active_bounds):
        keys = tuple(getattr(self.env, "water", {}).keys())
        n = len(keys)
        if n <= 0:
            self._background_water_cursor = 0
            return 0
        limit = min(n, max(1, int(THERMAL_BACKGROUND_MAX_WATER_CELLS)))
        start = int(self._background_water_cursor) % n
        processed = 0
        for i in range(limit):
            key = keys[(start + i) % n]
            tx, ty = int(key[0]), int(key[1])
            if self._key_in_bounds(key, active_bounds):
                continue
            amount = self.env.water_amount(tx, ty)
            if amount <= 1e-9:
                continue

            profile_id, _profile, target = self._background_regional_target(tx, ty)
            water_temp = self.env.water_temperature_at(
                tx, ty, self.env.temperature_at(tx, ty, target)
            )
            # Background water exchanges with the regional boundary only; we do
            # not run expensive tile-by-tile thermal diffusion off-screen.
            exchange = min(1.0, float(THERMAL_WATER_EXCHANGE_RATE) * 0.35 * float(dt))
            water_temp += (target - water_temp) * exchange
            self.env.set_water_temperature(tx, ty, water_temp)

            natural_freeze = (
                self.is_naturally_freezing_surface(tx, ty)
                and not self._has_active_fire_heat(tx, ty)
            )
            below_y = ty + 1
            supported = (
                below_y < self.world.height_tiles
                and tile_def(self.world.get_tile(tx, below_y)).solid
            )
            if natural_freeze and supported:
                hidden = self.env.cold_surface_amount(tx, ty)
                total = max(0.0, amount) + max(0.0, hidden)
                self.env.set_water(tx, ty, 0.0)
                self.env.set_cold_surface_water(tx, ty, 0.0)
                freeze_min = float(ALPINE_SURFACE_FREEZE_MIN_AMOUNT)
                if total < freeze_min:
                    self.env.set_cold_surface_water(tx, ty, total)
                else:
                    self._materialize_cold_surface_mass(
                        tx, ty, total, temperature_c=min(water_temp, -0.5)
                    )
                self.env.water_freeze_progress.pop(key, None)
                processed += 1
                continue

            freeze_min = float(WATER_FREEZE_MIN_AMOUNT)
            if water_temp < WATER_FREEZE_C and amount >= freeze_min:
                progress = float(self.env.water_freeze_progress.get(key, 0.0))
                progress += (
                    (WATER_FREEZE_C - water_temp)
                    * ICE_FREEZE_PROGRESS_PER_C_PER_SEC
                    * float(CLIMATE_WATER_FREEZE_MULTIPLIER.get(profile_id, 1.0))
                    * float(dt)
                )
                self.env.water_freeze_progress[key] = progress
                if progress >= 1.0:
                    self._freeze_water_cell(tx, ty, min_amount=freeze_min)
            else:
                self.env.water_freeze_progress.pop(key, None)
            processed += 1

        self._background_water_cursor = (start + limit) % max(1, n)
        return processed

    def _background_phase_changes(self, dt):
        active_bounds = self._active_bounds()
        self._background_ice_step(dt, active_bounds)
        self._background_water_step(dt, active_bounds)

    # --------------------------------------------------------
    # Public update
    # --------------------------------------------------------

    def update(
        self,
        dt,
        physics_objects=(),
    ):
        if not self.initialized:
            self.initialize_world()

        # Phase A: conservative local diffusion and direct heat exchange.
        for step in self.diffusion_task.consume(dt):
            bounds = self._active_bounds()
            self._ensure_temperature_bounds(bounds)
            self._diffuse(step, bounds)
            self._apply_fire_heat(step)
            self._exchange_water_heat(step, bounds)
            self.exchange_object_temperature(physics_objects, step)

        # Phase B runs half a thermal period later: climate boundary + phase
        # changes. Keeping these apart sharply lowers worst-frame CPU time.
        for step in self.boundary_task.consume(dt):
            bounds = self._active_bounds()
            self._ensure_temperature_bounds(bounds)
            self._apply_climate_and_sun(step, bounds)
            self._phase_changes(step, bounds)

        # V0.7.5.5: sparse dynamic phase states continue outside the local
        # thermal window at 1 Hz. This avoids full-world diffusion while making
        # freeze/melt progress catch up realistically when the player returns.
        for step in self.background_task.consume(dt):
            self._background_phase_changes(step)

    def condition_waterball_landing_temperature(self, tx, ty, source_temp_c):
        """Return the temperature of magic water after contacting local air/ground.

        In alpine/polar surface climates the water remains liquid for a few
        WaterSystem frames, spreads, then the normal thermal phase-change pass
        freezes it. Underground geothermal floors still prevent cave puddles
        from freezing merely because the surface biome is cold.
        """
        tx = int(tx); ty = int(ty)
        profile_id, _profile = self.climate_profile_for_chunk(
            tx//CHUNK_SIZE, ty//CHUNK_SIZE
        )
        if self._underground_geothermal_floor_c(tx, ty) is not None:
            return float(source_temp_c)
        if profile_id == "polar":
            return min(float(source_temp_c), float(POLAR_WATERBALL_LANDING_TEMP_C))
        if profile_id == "alpine":
            return min(float(source_temp_c), float(ALPINE_WATERBALL_LANDING_TEMP_C))
        return float(source_temp_c)

    def temperature_at_world(
        self,
        x,
        y,
    ):
        tx = max(
            0,
            min(
                self.world.width_tiles
                - 1,
                int(
                    x
                    // TILE_SIZE
                ),
            ),
        )

        ty = max(
            0,
            min(
                self.world.height_tiles
                - 1,
                int(
                    y
                    // TILE_SIZE
                ),
            ),
        )

        key=(tx,ty)
        if key in self.env.temperature:
            return float(self.env.temperature[key])
        return float(self._initial_temperature_for_tile(tx,ty))

    def climate_at_world(
        self,
        x,
        y,
    ):
        tx = max(
            0,
            int(
                x
                // TILE_SIZE
            ),
        )

        ty = max(
            0,
            int(
                y
                // TILE_SIZE
            ),
        )

        cx = (
            tx
            // CHUNK_SIZE
        )

        cy = (
            ty
            // CHUNK_SIZE
        )

        return self.env.climate_at_chunk(
            cx,
            cy,
            CLIMATE_PROFILE_BY_CHUNK_X.get(
                cx,
                "temperate",
            ),
        )
