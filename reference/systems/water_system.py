# -*- coding: utf-8 -*-
from config import (
    WATER_HZ,
    CHUNK_SIZE,
    RAIN_ADD_PER_STEP,
    BASE_EVAPORATION,
    WATER_FALL_FLOW,
    WATER_SIDE_FLOW,
    RAIN_SOIL_DIRECT,
    SOIL_SATURATION,
    SOIL_ABSORB_RATE,
    WATER_SUPPORT_THRESHOLD,
    WATER_STACK_THRESHOLD,
    WATER_FLOW_EPSILON,
    WATER_LOWER_SETTLE_FLOW,
    WATER_LOWER_SCAN_RADIUS,
    WATER_LEVEL_RELAX,
    TILE_SIZE,
    WATER_FREEZE_C,
    WATER_BOIL_C,
    WATER_MASS_UNITS_PER_TILE,
    EVAP_UPDRAFT_PER_WATER_UNIT,
    RAIN_PHYSICAL_COLUMN_STEP,
    RAIN_PHYSICAL_COLUMN_MASS_SCALE,
    ASH_REHYDRATE_CONTACT_EPSILON,
    ENGINEERING_HUD_ENABLED,
    CLIMATE_WATER_EVAP_MULTIPLIER,
    CLIMATE_INFILTRATION_MULTIPLIER,
    DESERT_SURFACE_SATURATION_RATIO,
    DESERT_SATURATED_SURFACE_EVAP_MULTIPLIER,
    DESERT_SATURATED_SURFACE_MIN_EVAP_RATE,
    DESERT_SAND_DIRECT_INFILTRATION_RATE,
    DESERT_SAND_PORE_HOLD_PER_TILE,
    DESERT_SAND_MAX_PERCOLATION_TILES,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import tile_def, ASH, AIR, LAYERED_GROUND_TILES
from world.environment_state import SoilCell
from world.soil_layers import soil_capacity_ratio, SOIL_LAYER_FULL
from systems.water_fast_numpy import FastWaterSolver


class WaterSystem:
    """Terraria-style lightweight tile liquid.

    Physical state:
        env.water[(tx, ty)] = volume in one tile, normally 0..1.

    V0.4.10 rules:
    1. gravity first;
    2. lower row fills outward before an upper row is allowed to remain;
    3. supported water equalizes horizontally;
    4. only an almost-full water tile may support a stable tile above;
    5. rain and evaporation are rate-per-second, independent of WATER_HZ;
    6. particles / smooth surfaces are renderer-only and never own mass.

    This is deliberately not Navier-Stokes. It follows the useful platform
    game model used by tile-liquid games: volume per tile + settling.
    """

    def __init__(self, world, env, chunk_streamer, weather_system):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system
        self.task = FixedRateTask(WATER_HZ, max_steps=5)
        self.tick_index = 0
        # Pyto ships NumPy as a compiled extension. Use it for liquid flow so
        # the hot loop runs in C instead of thousands of Python dict ops.
        self.fast_solver = FastWaterSolver(self.world, self.env)

        self.last_debug = {
            "mass_before": 0.0,
            "mass_after": 0.0,
            "rain_added": 0.0,
            "active_cells": 0,
        }

    def update(self, dt):
        for step in self.task.consume(dt):
            self._step(step)

    # --------------------------------------------------------
    # Basic helpers
    # --------------------------------------------------------

    def total_mass(self):
        return sum(float(v) for v in self.env.water.values())

    def _active(self, tx, ty):
        return self.chunk_streamer.is_active(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
        )

    @staticmethod
    def _chunk(tx, ty):
        return (
            int(tx) // CHUNK_SIZE,
            int(ty) // CHUNK_SIZE,
        )

    def _in_world(self, tx, ty):
        return (
            0 <= tx < self.world.width_tiles
            and 0 <= ty < self.world.height_tiles
        )

    def _soil_capacity_at(self, tx, ty):
        try:
            tile_id = int(self.world.get_tile(int(tx), int(ty)))
        except Exception:
            return float(SOIL_SATURATION)
        if tile_id in LAYERED_GROUND_TILES:
            mask = int(getattr(self.env, "soil_layers", {}).get(
                (int(tx), int(ty)), SOIL_LAYER_FULL
            ))
            return float(SOIL_SATURATION) * max(0.0, min(1.0, soil_capacity_ratio(mask)))
        return float(SOIL_SATURATION)

    def _solver_bounds(self):
        """Return a bounded liquid simulation window around active chunks.

        V0.5.8 copied/sorted *all* water cells every liquid tick and only then
        skipped inactive cells. Cost therefore grew with every puddle ever
        created. V0.5.9 builds the working set from active chunks plus the
        maximum horizontal pressure-scan halo, making tick cost independent of
        distant/sleeping liquid.
        """
        active = self.chunk_streamer.active_chunks
        if not active:
            return (
                0,
                0,
                self.world.width_tiles - 1,
                self.world.height_tiles - 1,
            )

        min_cx = min(cx for cx, _cy in active)
        max_cx = max(cx for cx, _cy in active)
        min_cy = min(cy for _cx, cy in active)
        max_cy = max(cy for _cx, cy in active)

        halo_x = max(1, int(WATER_LOWER_SCAN_RADIUS))
        halo_y = 1

        return (
            max(0, min_cx * CHUNK_SIZE - halo_x),
            max(0, min_cy * CHUNK_SIZE - halo_y),
            min(
                self.world.width_tiles - 1,
                (max_cx + 1) * CHUNK_SIZE - 1 + halo_x,
            ),
            min(
                self.world.height_tiles - 1,
                (max_cy + 1) * CHUNK_SIZE - 1 + halo_y,
            ),
        )

    def _solid(self, tx, ty):
        if not self._in_world(tx, ty):
            return True
        try:
            return float(self.world.liquid_capacity_at(tx, ty)) <= 1e-7
        except Exception:
            if self.world.get_tile(tx, ty) == AIR and getattr(
                self.world, "liquid_blocked_by_air_pressure", lambda _x, _y: False
            )(tx, ty):
                return True
            return tile_def(self.world.get_tile(tx, ty)).solid

    def _liquid_capacity(self, tx, ty):
        if not self._in_world(tx, ty):
            return 0.0
        try:
            return max(0.0, min(1.0, float(self.world.liquid_capacity_at(tx, ty))))
        except Exception:
            return 0.0 if self._solid(tx, ty) else 1.0

    def _surface_solid_row(self, tx):
        # V0.5.9: O(1) after the edited column has been recomputed once.
        return self.world.first_solid_row(tx)

    def _soil_cell(self, tx, ty):
        if not self._in_world(tx, ty):
            return None

        td = tile_def(
            self.world.get_tile(tx, ty)
        )

        if not (
            td.solid
            and td.water_absorption > 0
        ):
            return None

        return self.env.soil.setdefault(
            (tx, ty),
            SoilCell(
                moisture=0.0,
                fertility=max(0.0, td.fertility),
            ),
        )

    # --------------------------------------------------------
    # Source deposition
    # --------------------------------------------------------

    def deposit(
        self,
        tx,
        ty,
        amount,
        temperature_c=None,
    ):
        """Deposit liquid without silently deleting overflow.

        Important for waterball/rain:
        - fill the target row horizontally first;
        - only after reachable cells on that level are full may water stack
          into the row above.

        This is a source-placement rule. The normal liquid solver continues
        settling the result on subsequent water ticks.
        """
        remaining = max(
            0.0,
            float(
                amount
            ),
        )
        tx = int(tx)
        ty = int(ty)

        if temperature_c is None:
            temperature_c = self.env.temperature_at(
                tx,
                ty,
                20.0,
            )

        source_temperature = float(
            temperature_c
        )

        # Source deposits may be much smaller than the per-tick movement
        # epsilon (for example rain_rate * 1/30s). Do not discard those.
        if remaining <= 0.0:
            return 0.0

        # Try this row, target first then nearest left/right.
        candidates = [(tx, ty)]
        for d in range(1, WATER_LOWER_SCAN_RADIUS + 1):
            candidates.append((tx - d, ty))
            candidates.append((tx + d, ty))

        for cx, cy in candidates:
            if remaining <= 1e-12:
                break

            if not self._in_world(cx, cy):
                continue

            # Solid tiles are boundaries. For side candidates, stop naturally
            # by not depositing into them. The solver will still find open
            # directions from cells before the wall.
            if self._solid(cx, cy):
                continue

            current = self.env.water_amount(cx, cy)
            capacity = max(0.0, self._liquid_capacity(cx, cy) - current)

            if capacity <= 1e-12:
                continue

            put = min(
                remaining,
                capacity,
            )

            old_temp = self.env.water_temperature_at(
                cx,
                cy,
                self.env.temperature_at(
                    cx,
                    cy,
                    source_temperature,
                ),
            )

            new_amount = (
                current
                + put
            )

            if new_amount > 1e-12:
                mixed_temp = (
                    old_temp
                    * current
                    + source_temperature
                    * put
                ) / new_amount
            else:
                mixed_temp = source_temperature

            self.env.set_water(
                cx,
                cy,
                new_amount,
            )

            self.env.set_water_temperature(
                cx,
                cy,
                mixed_temp,
            )

            remaining -= put

        # Only after the current row cannot accept more volume do we create
        # an upper layer. Recursion depth is naturally tiny for spell/rain
        # quantities.
        if remaining > 1e-12 and ty > 0:
            return self.deposit(
                tx,
                ty - 1,
                remaining,
                temperature_c=source_temperature,
            )

        # If the world column is completely full up to the top boundary,
        # return the unplaced liquid to atmospheric vapor instead of deleting
        # it. This makes deposit() a conservation-safe sink for all callers.
        if remaining > 1e-12:
            cx, cy = self._chunk(tx, max(0, ty))
            self.env.add_vapor(cx, cy, remaining)
            remaining = 0.0

        return remaining

    # --------------------------------------------------------
    # Soil absorption
    # --------------------------------------------------------

    def _absorb_into_soil_below(self, tx, water_ty, amount, dt=None):
        if amount <= 0.0:
            return 0.0

        # Super-cooled surface water in alpine/polar regions must remain on
        # the ground long enough to spread and freeze.  Letting the generic
        # soil pass absorb it first made waterballs vanish into snow/soil before
        # ThermalSystem could perform the visible liquid -> ice phase change.
        profile = self.env.climate_at_chunk(
            int(tx) // CHUNK_SIZE, int(water_ty) // CHUNK_SIZE, "temperate"
        )
        water_temp = self.env.water_temperature_at(
            tx, water_ty, self.env.temperature_at(tx, water_ty, 20.0)
        )
        if profile in ("alpine", "polar") and float(water_temp) < WATER_FREEZE_C:
            return amount

        soil = self._soil_cell(
            tx,
            water_ty + 1,
        )

        if soil is None:
            return amount

        soil_capacity = self._soil_capacity_at(tx, water_ty + 1)
        capacity = max(
            0.0,
            soil_capacity - soil.moisture,
        )

        if capacity <= WATER_FLOW_EPSILON:
            return amount

        below_td = tile_def(self.world.get_tile(tx, water_ty + 1))
        if dt is not None:
            profile = self.env.climate_at_chunk(
                int(tx) // CHUNK_SIZE, int(water_ty + 1) // CHUNK_SIZE, "temperate"
            )
            climate_rate = float(CLIMATE_INFILTRATION_MULTIPLIER.get(profile, 1.0))
            # V0.7.7.4 split-flow rule: ponded/free water is never swallowed
            # instantaneously by a dry dirt tile.  A limited portion seeps into
            # pore space while the remaining free water can spread sideways in
            # the 30 Hz WaterSystem during the same real-time interval.
            material_mult = 2.0 if below_td.name in ("sand", "sea_sand") else 1.0
            max_absorb = max(
                WATER_FLOW_EPSILON,
                float(SOIL_ABSORB_RATE) * climate_rate * material_mult * float(dt),
            )
            absorbed = min(amount, capacity, max_absorb)
        else:
            absorbed = min(amount, capacity)

        soil.moisture = min(
            soil_capacity,
            soil.moisture + absorbed,
        )

        return max(
            0.0,
            amount - absorbed,
        )

    def _deposit_to_underground_waterway(self, tx, amount, temperature_c=12.0):
        """Deposit vertically percolated water into the authored bottom waterway.

        This is the only hydrology bridge that turns pore water back into
        horizontally mobile underground free water.  No side-to-side movement
        occurs inside soil/sand itself.
        """
        tx = int(tx)
        amount = max(0.0, float(amount))
        row = getattr(self.world, "underground_waterway_row", None)
        if amount <= 1e-12 or row is None:
            return 0.0
        row = int(row)
        if not (0 <= tx < self.world.width_tiles and 0 <= row < self.world.height_tiles):
            return 0.0
        if self.world.get_tile(tx, row) != AIR:
            return 0.0
        current = self.env.water_amount(tx, row)
        room = max(0.0, 1.0 - current)
        moved = min(amount, room)
        if moved <= 0.0:
            return 0.0
        self.env.set_water(tx, row, current + moved)
        try:
            old_t = self.env.water_temperature_at(tx, row, float(temperature_c))
            mixed = (old_t * current + float(temperature_c) * moved) / max(1e-12, current + moved)
            self.env.set_water_temperature(tx, row, mixed)
        except Exception:
            pass
        return moved

    def _route_desert_sand_infiltration(self, tx, water_ty, amount):
        """Route surface water through a porous desert sand column.

        Sand stays solid for player collision, but hydrologically it is not a
        sealed basin floor. A small amount is retained in pore moisture while
        the remainder moves down the sand column, emerges into an open cavity
        when one exists, or enters the conserved groundwater reservoir.
        """
        tx = int(tx); water_ty = int(water_ty)
        remaining = max(0.0, float(amount))
        if remaining <= WATER_FLOW_EPSILON:
            return 0.0
        y = water_ty + 1
        if y >= self.world.height_tiles:
            return 0.0
        if tile_def(self.world.get_tile(tx, y)).name != "sand":
            return 0.0

        moved = 0.0
        scanned = 0
        last_sand_y = y
        while (
            remaining > WATER_FLOW_EPSILON
            and y < self.world.height_tiles
            and scanned < int(DESERT_SAND_MAX_PERCOLATION_TILES)
            and tile_def(self.world.get_tile(tx, y)).name == "sand"
        ):
            last_sand_y = y
            soil = self._soil_cell(tx, y)
            if soil is not None:
                pore_cap = min(
                    self._soil_capacity_at(tx, y),
                    float(DESERT_SAND_PORE_HOLD_PER_TILE),
                )
                room = max(0.0, pore_cap - float(soil.moisture))
                held = min(remaining, room)
                if held > 0.0:
                    soil.moisture += held
                    remaining -= held
                    moved += held
            y += 1
            scanned += 1

        # If the sand opens into a mined/cave air cell, let the infiltrated
        # water physically emerge there before using groundwater storage.
        if remaining > WATER_FLOW_EPSILON and y < self.world.height_tiles:
            if self.world.get_tile(tx, y) == AIR:
                current = self.env.water_amount(tx, y)
                room = max(0.0, 1.0 - current)
                emerged = min(remaining, room)
                if emerged > 0.0:
                    self.env.set_water(tx, y, current + emerged)
                    source_temp = self.env.water_temperature_at(
                        tx, water_ty, self.env.temperature_at(tx, water_ty, 20.0)
                    )
                    self.env.set_water_temperature(tx, y, source_temp)
                    remaining -= emerged
                    moved += emerged

        # FIX36: never teleport the remainder past a rock/ore/igneous barrier
        # into the underground waterway.  Only the sand cells actually scanned
        # above, or the immediately adjacent open cell reached at ``y``, may
        # receive this infiltration.  Any blocked remainder stays as surface
        # water and can continue only after a real vertical path exists.
        return moved

    def _surface_evaporation_rate(self, tx, ty, weather):
        water_temp = self.env.water_temperature_at(
            tx, ty, self.env.temperature_at(tx, ty, 20.0)
        )
        if water_temp <= WATER_FREEZE_C:
            temperature_factor = 0.08
        elif water_temp < 20.0:
            temperature_factor = 0.25 + water_temp / 20.0 * 0.75
        elif water_temp < 80.0:
            temperature_factor = 1.0 + (water_temp - 20.0) * 0.045
        else:
            temperature_factor = 3.7 + (water_temp - 80.0) * 0.18

        profile = self.env.climate_at_chunk(
            int(tx) // CHUNK_SIZE, int(ty) // CHUNK_SIZE, "temperate"
        )
        climate_evap = float(CLIMATE_WATER_EVAP_MULTIPLIER.get(profile, 1.0))
        evap_rate = (
            BASE_EVAPORATION
            * climate_evap
            * temperature_factor
            * max(0.05, float(weather.sunlight))
            * (0.55 + float(weather.wind) * 0.65)
        )

        if profile == "desert" and ty + 1 < self.world.height_tiles:
            below_td = tile_def(self.world.get_tile(tx, ty + 1))
            if below_td.name == "sand":
                evap_rate = max(
                    evap_rate * float(DESERT_SATURATED_SURFACE_EVAP_MULTIPLIER),
                    float(DESERT_SATURATED_SURFACE_MIN_EVAP_RATE),
                )
        return float(evap_rate), str(profile)

    def _surface_infiltration_exchange(self, tx, ty, amount, dt, profile=None):
        amount = max(0.0, float(amount))
        if amount <= 0.0 or ty + 1 >= self.world.height_tiles:
            return 0.0
        if profile is None:
            profile = self.env.climate_at_chunk(
                int(tx) // CHUNK_SIZE, int(ty) // CHUNK_SIZE, "temperate"
            )
        below_td = tile_def(self.world.get_tile(tx, ty + 1))
        if float(getattr(below_td, "water_absorption", 0.0)) <= 0.0:
            return 0.0
        if below_td.name == "sand" and profile == "desert":
            return self._route_desert_sand_infiltration(tx, ty, amount)
        leftover = self._absorb_into_soil_below(tx, ty, amount, dt=dt)
        return max(0.0, amount - leftover)

    def _desert_sand_infiltration_pass(self, dt):
        """High-rate infiltration BEFORE water is allowed to pool sideways."""
        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return 0.0

        bounds = self._solver_bounds()
        x0, y0, x1, y1 = bounds
        index = getattr(self.fast_solver, "water_by_chunk", {})
        candidate = set()
        if index:
            c0x, c1x = x0 // CHUNK_SIZE, x1 // CHUNK_SIZE
            c0y, c1y = y0 // CHUNK_SIZE, y1 // CHUNK_SIZE
            for cy in range(c0y, c1y + 1):
                for cx in range(c0x, c1x + 1):
                    candidate.update(index.get((cx, cy), ()))
        else:
            candidate.update(
                (int(tx), int(ty))
                for (tx, ty), value in tuple(self.env.water.items())
                if float(value) > WATER_FLOW_EPSILON
                and x0 <= int(tx) <= x1 and y0 <= int(ty) <= y1
            )

        infiltrated_total = 0.0
        max_per_cell = float(DESERT_SAND_DIRECT_INFILTRATION_RATE) * dt
        for tx, ty in tuple(candidate):
            current = self.env.water_amount(tx, ty)
            if current <= WATER_FLOW_EPSILON or not self._active(tx, ty):
                continue
            if ty + 1 >= self.world.height_tiles:
                continue
            if self.env.climate_at_chunk(tx // CHUNK_SIZE, ty // CHUNK_SIZE, "temperate") != "desert":
                continue
            if tile_def(self.world.get_tile(tx, ty + 1)).name != "sand":
                continue

            # Keep at least half the visible surface water available for the
            # paired evaporation path later in the same tick.
            requested = min(current * 0.5, max_per_cell)
            moved = self._route_desert_sand_infiltration(tx, ty, requested)
            if moved <= 0.0:
                continue
            self.env.set_water(tx, ty, max(0.0, current - moved))
            infiltrated_total += moved

        return infiltrated_total

    def _fast_evaporation_pass(self, dt):
        """Apply exposed-surface water exchange when NumPy owns liquid flow.

        V0.7.5.5 pairs visible evaporation with infiltration when the ground
        below is porous: surface water tries to split 50/50 between upward
        vapor and downward seepage, and both branches advance at the same rate.
        Rock-bottom ocean/lake basins therefore keep their water, while melt
        water on absorbent ground no longer seems to vanish without seepage.
        """
        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return 0.0
        candidate = set()
        index = getattr(self.fast_solver, "water_by_chunk", {})
        for chunk in tuple(self.chunk_streamer.active_chunks):
            candidate.update(index.get((int(chunk[0]), int(chunk[1])), ()))
        if not candidate:
            return 0.0

        evaporated_total = 0.0
        for tx, ty in tuple(candidate):
            current = self.env.water_amount(tx, ty)
            if current <= WATER_FLOW_EPSILON or not self._active(tx, ty):
                continue
            if ty > 0 and self.env.water_amount(tx, ty - 1) > WATER_FLOW_EPSILON:
                continue

            weather = self.weather_system.weather_at_chunk(
                int(tx) // CHUNK_SIZE, int(ty) // CHUNK_SIZE
            )
            evap_rate, profile = self._surface_evaporation_rate(tx, ty, weather)
            desired_branch = min(current * 0.5, max(0.0, evap_rate * dt))
            infiltrated = 0.0
            if desired_branch > 0.0:
                infiltrated = self._surface_infiltration_exchange(
                    tx, ty, desired_branch, dt, profile=profile
                )

            if infiltrated > 0.0:
                evap_amount = min(current - infiltrated, infiltrated)
            else:
                evap_amount = min(current, desired_branch)

            total_remove = infiltrated + evap_amount
            if total_remove <= 0.0:
                continue

            self.env.set_water(tx, ty, max(0.0, current - total_remove))
            if evap_amount > 0.0:
                cx, cy = self._chunk(tx, ty)
                self.env.add_vapor(cx, cy, evap_amount)
                haze_key = (int(cx), 0)
                self.env.evaporation_haze[haze_key] = min(
                    4.0,
                    float(self.env.evaporation_haze.get(haze_key, 0.0)) + evap_amount * 8.0,
                )
                evaporated_total += evap_amount
        return evaporated_total

    # --------------------------------------------------------
    # Rain
    # --------------------------------------------------------

    def _top_water_cell_in_column(self, tx, stop_ty=None):
        tx = int(tx)
        max_ty = self.world.height_tiles if stop_ty is None else min(self.world.height_tiles, int(stop_ty) + 1)
        for ty in range(max(0, max_ty)):
            if self.env.water_amount(tx, ty) > WATER_FLOW_EPSILON:
                return int(ty)
        return None

    def _add_rain(self, dt):
        """Condensed cloud water -> rain; no water is created from nothing.

        V0.7.4.0 treats the atmosphere as one horizontal column per chunk-X.
        Older builds looped every active chunk-Y and could rain multiple times
        onto the same surface column.
        """
        added = 0.0
        active_cx = sorted({int(cx) for cx, _cy in self.chunk_streamer.active_chunks})
        for cx in active_cx:
            weather = self.weather_system.weather_at_chunk(cx, 0)
            if weather.rain <= 0.08:
                continue

            start_tx = cx * CHUNK_SIZE
            end_tx = min(self.world.width_tiles, start_tx + CHUNK_SIZE)
            stride = max(1, int(RAIN_PHYSICAL_COLUMN_STEP), 4)
            phase = (int(self.tick_index) + int(cx) * 3) % stride
            for tx in range(start_tx + phase, end_tx, stride):
                solid_ty = self._surface_solid_row(tx)
                if solid_ty is None:
                    continue
                td = tile_def(self.world.get_tile(tx, solid_ty))
                requested = (
                    RAIN_SOIL_DIRECT
                    * weather.rain
                    * dt
                    * RAIN_PHYSICAL_COLUMN_MASS_SCALE
                    * stride
                )
                actual = self.env.take_precipitable_cloud(cx, 0, requested)
                if actual <= 0.0:
                    continue
                added += actual

                # If rain hits an existing lake/ocean first, join that free
                # water body at its surface. Do not tunnel the rainfall through
                # the water column and hydrate the seabed directly.
                water_ty = self._top_water_cell_in_column(tx, stop_ty=solid_ty)
                if water_ty is not None:
                    self.deposit(tx, water_ty, actual)
                    continue

                if self.world.get_tile(tx, solid_ty) == ASH:
                    key = (tx, solid_ty)
                    self.env.ash_wet_time[key] = max(
                        ASH_REHYDRATE_CONTACT_EPSILON,
                        float(self.env.ash_wet_time.get(key, 0.0)),
                    )

                # Porosity, not fertility, controls rain infiltration.
                if td.water_absorption > 0.2:
                    soil = self.env.soil.setdefault(
                        (tx, solid_ty),
                        SoilCell(moisture=0.0, fertility=max(0.0, td.fertility)),
                    )
                    capacity = max(0.0, self._soil_capacity_at(tx, solid_ty) - soil.moisture)
                    absorbed = min(actual, capacity)
                    soil.moisture += absorbed
                    overflow = actual - absorbed
                    if overflow > 1e-12 and solid_ty > 0:
                        self.deposit(tx, solid_ty - 1, overflow)
                elif solid_ty > 0:
                    self.deposit(tx, solid_ty - 1, actual)

        return added

    # --------------------------------------------------------
    # Query helpers
    # --------------------------------------------------------

    def is_falling_cell(
        self,
        tx,
        ty,
        snapshot=None,
    ):
        if ty + 1 >= self.world.height_tiles:
            return False

        try:
            if self.world.liquid_has_internal_floor(tx, ty):
                return False
        except Exception:
            pass
        if self._solid(
            tx,
            ty + 1,
        ):
            return False

        source = (
            snapshot
            if snapshot is not None
            else self.env.water
        )

        below = float(
            source.get(
                (tx, ty + 1),
                0.0,
            )
        )

        return (
            below
            < WATER_SUPPORT_THRESHOLD
        )

    def surface_y(self, tx, ty):
        """World-Y of the authoritative free surface in this liquid cell.

        Since V0.7.7.0 a LOW/MID terrain tile owns its missing upper fraction
        as real liquid capacity.  Therefore the surface must be derived from
        *this cell's capacity*, not from the top of the support tile below.
        That removes the old lake-edge depression / center hump visual caused
        by extending an AIR-cell water quad into a neighboring partial tile.
        """
        cap = max(0.0, min(1.0, self._liquid_capacity(tx, ty)))
        amount = max(0.0, min(cap, self.env.water_amount(tx, ty)))
        return (float(int(ty)) + cap - amount) * TILE_SIZE

    def depth_at_world(self, x, y):
        """Return immersion depth from the same fractional cell geometry as flow/render.

        FIX39 uses the already-authoritative NumPy active window when the sample
        lies inside it. Swimming asks for head/chest depth every 60 Hz frame;
        avoiding repeated Python tile/capacity lookups removes that work from the
        player's hottest water path without changing any liquid physics.
        """
        tx = int(x // TILE_SIZE)
        sample_ty = int(y // TILE_SIZE)
        best = 0.0
        solver = getattr(self, "fast_solver", None)
        grid = getattr(solver, "water_grid", None) if solver is not None else None
        caps = getattr(solver, "capacity_grid", None) if solver is not None else None
        if grid is not None and caps is not None:
            try:
                sample_rows = tuple(
                    ty for ty in (sample_ty, sample_ty - 1)
                    if 0 <= ty < self.world.height_tiles
                )
                # Only use the array fast path when every row needed by this
                # geometric query is represented by the current active window.
                # Off-window callers (editor/debug/remote entities) fall back to
                # the sparse authoritative table instead of returning a false 0.
                if sample_rows and all(solver._in_window(tx, ty) for ty in sample_rows):
                    for ty in sample_rows:
                        lx, ly = solver._local(tx, ty)
                        cap = max(0.0, min(1.0, float(caps[ly, lx])))
                        amount = max(0.0, min(cap, float(grid[ly, lx])))
                        if amount <= WATER_FLOW_EPSILON:
                            continue
                        top = (float(ty) + cap - amount) * TILE_SIZE
                        bottom = (float(ty) + cap) * TILE_SIZE
                        if float(y) < top or float(y) > bottom:
                            continue
                        best = max(best, min(TILE_SIZE * amount, float(y) - top))
                    return best
            except Exception:
                best = 0.0

        for ty in (sample_ty, sample_ty - 1):
            if ty < 0 or ty >= self.world.height_tiles:
                continue
            cap = max(0.0, min(1.0, self._liquid_capacity(tx, ty)))
            amount = max(0.0, min(cap, float(self.env.water_amount(tx, ty))))
            if amount <= WATER_FLOW_EPSILON:
                continue
            top = (float(ty) + cap - amount) * TILE_SIZE
            bottom = (float(ty) + cap) * TILE_SIZE
            if float(y) < top or float(y) > bottom:
                continue
            best = max(best, min(TILE_SIZE * amount, float(y) - top))
        return best

    # --------------------------------------------------------
    # Liquid settling
    # --------------------------------------------------------

    @staticmethod
    def _get(working, tx, ty):
        return float(
            working.get(
                (tx, ty),
                0.0,
            )
        )

    @staticmethod
    def _set_working(
        working,
        tx,
        ty,
        value,
    ):
        key = (
            int(tx),
            int(ty),
        )

        value = max(
            0.0,
            float(value),
        )

        if value <= 1e-12:
            working.pop(
                key,
                None,
            )
        else:
            # Movement code never intentionally overfills a cell.
            working[key] = min(
                1.0,
                value,
            )

    def _move_mass(
        self,
        working,
        sx,
        sy,
        dx,
        dy,
        amount,
    ):
        amount = max(
            0.0,
            float(amount),
        )

        if amount <= WATER_FLOW_EPSILON:
            return 0.0

        source = self._get(
            working,
            sx,
            sy,
        )

        target = self._get(
            working,
            dx,
            dy,
        )

        capacity = max(
            0.0,
            1.0 - target,
        )

        moved = min(
            source,
            amount,
            capacity,
        )

        if moved <= WATER_FLOW_EPSILON:
            return 0.0

        thermal = getattr(
            self,
            "_flow_temp_working",
            None,
        )

        source_key = (
            sx,
            sy,
        )
        target_key = (
            dx,
            dy,
        )

        if thermal is not None:
            source_temp = float(
                thermal.get(
                    source_key,
                    self.env.temperature_at(
                        sx,
                        sy,
                        20.0,
                    ),
                )
            )

            target_temp = float(
                thermal.get(
                    target_key,
                    self.env.temperature_at(
                        dx,
                        dy,
                        source_temp,
                    ),
                )
            )

            new_target_mass = (
                target
                + moved
            )

            if new_target_mass > 1e-12:
                thermal[
                    target_key
                ] = (
                    target_temp
                    * target
                    + source_temp
                    * moved
                ) / new_target_mass

        self._set_working(
            working,
            sx,
            sy,
            source - moved,
        )

        self._set_working(
            working,
            dx,
            dy,
            target + moved,
        )

        if (
            thermal is not None
            and source
            - moved
            <= 1e-12
        ):
            thermal.pop(
                source_key,
                None,
            )

        return moved

    def _feed_lower_row(
        self,
        working,
        tx,
        ty,
        direction_order,
    ):
        """Move upper-layer water into the nearest vacancy in the row below.

        The scan only crosses non-solid lower-row cells. Full cells may be
        crossed; the first not-full cell becomes the destination.

        This is the key anti-pyramid rule: a lower connected row is filled
        outward before a stable second row can build upward.
        """
        current = self._get(
            working,
            tx,
            ty,
        )

        if current <= WATER_FLOW_EPSILON:
            return

        lower_y = ty + 1

        if (
            lower_y
            >= self.world.height_tiles
        ):
            return

        for direction in direction_order:
            if current <= WATER_FLOW_EPSILON:
                break

            for distance in range(
                1,
                WATER_LOWER_SCAN_RADIUS + 1,
            ):
                nx = (
                    tx
                    + direction
                    * distance
                )

                if not self._in_world(
                    nx,
                    lower_y,
                ):
                    break

                if self._solid(
                    nx,
                    lower_y,
                ):
                    # Solid wall terminates this lower-row path.
                    break

                candidate_amount = self._get(
                    working,
                    nx,
                    lower_y,
                )

                if (
                    candidate_amount
                    >= 1.0
                    - WATER_FLOW_EPSILON
                ):
                    # Full liquid tile can conduct pressure to the next tile.
                    continue

                moved = self._move_mass(
                    working,
                    tx,
                    ty,
                    nx,
                    lower_y,
                    min(
                        WATER_LOWER_SETTLE_FLOW,
                        current,
                    ),
                )

                current -= moved
                break

    def _step(self, dt):
        if getattr(self.fast_solver, "available", False):
            self.tick_index += 1
            audit_mass = (
                ENGINEERING_HUD_ENABLED
                or self.tick_index % max(1, int(round(WATER_HZ))) == 1
            )
            mass_before = (
                self.fast_solver.total_mass()
                if audit_mass
                else float(self.last_debug.get("mass_after", 0.0))
            )
            debug = self.fast_solver.step(self, dt)
            mass_after = (
                self.fast_solver.total_mass()
                if audit_mass
                else float(self.last_debug.get("mass_after", mass_before))
            )
            debug["mass_before"] = float(mass_before)
            debug["mass_after"] = float(mass_after)
            debug["fast_numpy"] = True
            self.last_debug = debug
            return

        self._step_legacy(dt)

    def _step_legacy(self, dt):
        self.tick_index += 1
        self._desert_sand_infiltration_pass(dt)

        # Whole-world water sums are diagnostics, not simulation. Running them
        # at 30 Hz made cost scale with all sleeping water in the world.
        audit_mass = (
            ENGINEERING_HUD_ENABLED
            or self.tick_index % max(1, int(round(WATER_HZ))) == 1
        )
        mass_before = (
            self.total_mass()
            if audit_mass
            else float(self.last_debug.get("mass_after", 0.0))
        )

        rain_added = self._add_rain(dt)

        x0, y0, x1, y1 = self._solver_bounds()

        # Build only the active liquid window. Dictionary lookup over a fixed
        # tile rectangle is bounded by active chunk radius, not world history.
        working = {}
        region_existing_keys = []
        region_existing_set = set()
        # Row buckets are built while scanning the bounded window. This keeps
        # the gravity ordering without sorting every active water key at 30Hz.
        row_keys = [[] for _ in range(max(0, y1 - y0 + 1))]
        flow_temperatures = {}

        for ty in range(y0, y1 + 1):
            row = row_keys[ty - y0]
            for tx in range(x0, x1 + 1):
                key = (tx, ty)
                amount = float(self.env.water.get(key, 0.0))
                if amount <= 1e-12:
                    continue

                working[key] = amount
                region_existing_keys.append(key)
                region_existing_set.add(key)
                row.append(tx)
                flow_temperatures[key] = float(
                    self.env.water_temperature.get(
                        key,
                        self.env.temperature_at(tx, ty, 20.0),
                    )
                )

        self._flow_temp_working = flow_temperatures

        # Soil capacity pass before movement, limited to active cells.
        for tx, ty in region_existing_keys:
            amount = self._get(working, tx, ty)
            if (
                amount <= WATER_FLOW_EPSILON
                or not self._active(tx, ty)
            ):
                continue

            leftover = self._absorb_into_soil_below(
                tx,
                ty,
                amount,
                dt=dt,
            )

            if abs(leftover - amount) > 1e-12:
                self._set_working(
                    working,
                    tx,
                    ty,
                    leftover,
                )

        reverse_x = bool(
            self.tick_index & 1
        )

        direction_order = (
            (1, -1)
            if reverse_x
            else (-1, 1)
        )

        # Gravity order is row-descending. Within each row we alternate X
        # direction each tick, matching the old sorted-key behavior without
        # allocating/sorting a Python tuple list.
        ordered_cells = []
        for ty in range(y1, y0 - 1, -1):
            xs = row_keys[ty - y0]
            iterator = reversed(xs) if reverse_x else iter(xs)
            for tx in iterator:
                ordered_cells.append((tx, ty))

        for tx, ty in ordered_cells:
            current = self._get(
                working,
                tx,
                ty,
            )

            if (
                current <= WATER_FLOW_EPSILON
                or not self._active(
                    tx,
                    ty,
                )
            ):
                continue

            # ------------------------------------------------
            # 1) Gravity first
            # ------------------------------------------------
            if (
                ty + 1
                < self.world.height_tiles
                and not self._solid(
                    tx,
                    ty + 1,
                )
            ):
                below = self._get(
                    working,
                    tx,
                    ty + 1,
                )

                capacity = max(
                    0.0,
                    1.0 - below,
                )

                moved = self._move_mass(
                    working,
                    tx,
                    ty,
                    tx,
                    ty + 1,
                    min(
                        current,
                        capacity,
                        WATER_FALL_FLOW,
                    ),
                )

                current -= moved

            if current <= WATER_FLOW_EPSILON:
                continue

            below_solid = self._solid(
                tx,
                ty + 1,
            )

            below_amount = (
                1.0
                if below_solid
                else self._get(
                    working,
                    tx,
                    ty + 1,
                )
            )

            # ------------------------------------------------
            # 2) Upper liquid feeds lower horizontal front
            # ------------------------------------------------
            if (
                not below_solid
                and below_amount
                >= WATER_STACK_THRESHOLD
            ):
                self._feed_lower_row(
                    working,
                    tx,
                    ty,
                    direction_order,
                )

                current = self._get(
                    working,
                    tx,
                    ty,
                )

                if current <= WATER_FLOW_EPSILON:
                    continue

            # A cell is only a stable horizontal surface when sitting on
            # solid terrain or on an almost-full water cell.
            supported = (
                below_solid
                or below_amount
                >= WATER_SUPPORT_THRESHOLD
            )

            # ------------------------------------------------
            # 3) Horizontal equalization
            # ------------------------------------------------
            if supported:
                for direction in direction_order:
                    current = self._get(
                        working,
                        tx,
                        ty,
                    )

                    if current <= WATER_FLOW_EPSILON:
                        break

                    nx = tx + direction

                    if (
                        not self._in_world(
                            nx,
                            ty,
                        )
                        or self._solid(
                            nx,
                            ty,
                        )
                    ):
                        continue

                    neighbor = self._get(
                        working,
                        nx,
                        ty,
                    )

                    difference = (
                        current
                        - neighbor
                    )

                    if difference <= 0.018:
                        continue

                    # Pair target would be halfway between both cells.
                    desired_flow = (
                        difference
                        * 0.5
                        * WATER_LEVEL_RELAX
                    )

                    self._move_mass(
                        working,
                        tx,
                        ty,
                        nx,
                        ty,
                        min(
                            WATER_SIDE_FLOW,
                            desired_flow,
                        ),
                    )

            # ------------------------------------------------
            # 4) Evaporation as per-second rate
            # ------------------------------------------------
            current = self._get(
                working,
                tx,
                ty,
            )

            if current > WATER_FLOW_EPSILON:
                weather = self.weather_system.weather_at_chunk(
                    tx // CHUNK_SIZE,
                    ty // CHUNK_SIZE,
                )

                above_amount = self._get(
                    working,
                    tx,
                    ty - 1,
                ) if ty > 0 else 0.0

                exposed = (
                    above_amount
                    <= WATER_FLOW_EPSILON
                )

                if exposed:
                    water_temp = (
                        self.env.water_temperature_at(
                            tx,
                            ty,
                            self.env.temperature_at(
                                tx,
                                ty,
                                20.0,
                            ),
                        )
                    )

                    # Temperature is now a major evaporation parameter:
                    # cold water evaporates slowly, hot water rapidly.
                    if water_temp <= WATER_FREEZE_C:
                        temperature_factor = 0.08
                    elif water_temp < 20.0:
                        temperature_factor = (
                            0.25
                            + water_temp
                            / 20.0
                            * 0.75
                        )
                    elif water_temp < 80.0:
                        temperature_factor = (
                            1.0
                            + (
                                water_temp
                                - 20.0
                            )
                            * 0.045
                        )
                    else:
                        temperature_factor = (
                            3.7
                            + (
                                water_temp
                                - 80.0
                            )
                            * 0.18
                        )

                    profile = self.env.climate_at_chunk(
                        tx // CHUNK_SIZE, ty // CHUNK_SIZE, "temperate"
                    )
                    climate_evap = float(CLIMATE_WATER_EVAP_MULTIPLIER.get(profile, 1.0))
                    evap_rate = (
                        BASE_EVAPORATION
                        * climate_evap
                        * temperature_factor
                        * max(0.05, weather.sunlight)
                        * (0.55 + weather.wind * 0.65)
                    )

                    # V0.7.5.3 desert overflow rule. Fresh water is still
                    # allowed to infiltrate porous sand first. Once the sand
                    # directly below the standing puddle is near saturation,
                    # the exposed remainder evaporates immediately at a
                    # desert-only accelerated rate instead of persisting as a
                    # long-lived surface lake. Natural evaporation never adds
                    # gameplay updraft; only its conserved vapor/haze changes.
                    if profile == "desert" and ty + 1 < self.world.height_tiles:
                        below_td = tile_def(self.world.get_tile(tx, ty + 1))
                        if below_td.name == "sand":
                            evap_rate = max(
                                evap_rate * float(DESERT_SATURATED_SURFACE_EVAP_MULTIPLIER),
                                float(DESERT_SATURATED_SURFACE_MIN_EVAP_RATE),
                            )

                    evap = evap_rate * dt
                    evaporated = min(current, evap)
                    self._set_working(
                        working,
                        tx,
                        ty,
                        current - evaporated,
                    )
                    if evaporated > 0.0:
                        cx, cy = self._chunk(
                            tx,
                            ty,
                        )

                        # Natural evaporation joins the atmospheric water
                        # reservoir directly. It is rendered as a wide diffuse
                        # vapor field and never creates physical lift; only
                        # magic fire / burning / explosions create updraft.
                        self.env.add_vapor(cx, cy, evaporated)
                        haze_key = (int(cx), 0)
                        self.env.evaporation_haze[haze_key] = min(4.0, float(self.env.evaporation_haze.get(haze_key, 0.0)) + evaporated * 8.0)

        # Soil capacity pass after movement, still on the bounded working set.
        for tx, ty in tuple(working.keys()):
            amount = self._get(working, tx, ty)
            if (
                amount <= WATER_FLOW_EPSILON
                or not self._active(tx, ty)
            ):
                continue

            leftover = self._absorb_into_soil_below(
                tx,
                ty,
                amount,
                dt=dt,
            )

            if abs(leftover - amount) > 1e-12:
                self._set_working(
                    working,
                    tx,
                    ty,
                    leftover,
                )

        # Commit only changed cells instead of deleting/re-inserting every
        # liquid dictionary entry on every 30 Hz tick. This removes a large
        # allocation/GC source while water is settling.
        local_temp_map = getattr(self, "_flow_temp_working", {})
        for tx, ty in region_existing_keys:
            key = (tx, ty)
            amount = self._get(working, tx, ty)
            if amount <= 1e-12:
                self.env.water.pop(key, None)
                self.env.water_temperature.pop(key, None)
                continue

            old_amount = float(self.env.water.get(key, 0.0))
            if abs(old_amount - amount) > 1e-12:
                self.env.set_water(tx, ty, amount)

            new_temp = float(local_temp_map.get(
                key,
                self.env.temperature_at(tx, ty, 20.0),
            ))
            old_temp = float(self.env.water_temperature.get(
                key,
                self.env.temperature_at(tx, ty, 20.0),
            ))
            if abs(old_temp - new_temp) > 1e-9:
                self.env.set_water_temperature(tx, ty, new_temp)

        for tx, ty in (set(working.keys()) - region_existing_set):
            amount = self._get(working, tx, ty)
            if amount <= 1e-12:
                continue
            self.env.set_water(tx, ty, amount)
            self.env.set_water_temperature(
                tx,
                ty,
                local_temp_map.get(
                    (tx, ty),
                    self.env.temperature_at(tx, ty, 20.0),
                ),
            )

        if audit_mass:
            mass_after = self.total_mass()
        else:
            # Preserve the most recently audited value for HUD/debug consumers.
            mass_after = float(self.last_debug.get("mass_after", mass_before))

        self.last_debug = {
            "mass_before": mass_before,
            "mass_after": mass_after,
            "rain_added": rain_added,
            "active_cells": sum(
                1
                for tx, ty in working.keys()
                if self._active(tx, ty)
            ),
            "solver_cells": len(working),
            "world_cells": len(self.env.water),
        }
