import random
# -*- coding: utf-8 -*-
from config import (
    SOIL_HZ,
    CHUNK_SIZE,
    SOIL_SATURATION,
    SOIL_ABSORB_RATE,
    SOIL_INITIAL_MOISTURE,
    SOIL_FIELD_CAPACITY,
    SOIL_VERTICAL_PERCOLATION_RATE,
    SOIL_LATERAL_CAPILLARY_RATE,
    SOIL_GROUNDWATER_RATE,
    SOIL_LATERAL_MIN_DIFFERENCE,
    SOIL_EVAP_RATE,
    WATER_MASS_UNITS_PER_TILE,
    EVAP_UPDRAFT_PER_WATER_UNIT,
    ASH_REHYDRATE_SECONDS,
    ASH_REHYDRATE_WATER_THRESHOLD,
    ASH_REHYDRATE_TRIGGER_WATER,
    ASH_REHYDRATE_CONTACT_EPSILON,
    ASH_RECOVERED_SOIL_MOISTURE,
    ASH_SOIL_FERTILITY,
    ASH_RECOVERY_TO_GRASS_DIRT,
    SOIL_PLANT_CAP_WEIGHTS,
    SWAMP_FORM_SECONDS,
    SWAMP_RECOVER_SECONDS,
    SWAMP_STANDING_WATER_THRESHOLD,
    SWAMP_DRY_WATER_THRESHOLD,
    SWAMP_WITHER_RECOVERY_SECONDS,
    CLIMATE_SOIL_EVAP_MULTIPLIER,
    CLIMATE_INFILTRATION_MULTIPLIER,
    DESERT_GROUNDWATER_EVAP_RATE,
    DESERT_SAND_FIELD_CAPACITY,
    DESERT_GROUNDWATER_DRAIN_MULTIPLIER,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import (
    tile_def,
    DIRT,
    GRASS_DIRT,
    ASH,
    SWAMP_SOIL,
    SEA_SAND,
    AIR,
    LAYERED_GROUND_TILES,
)
from world.environment_state import SoilCell
from world.soil_layers import soil_capacity_ratio, SOIL_LAYER_FULL


class SoilSystem:
    """Porous soil-water simulation.

    Required V0.4.11 behavior:
    - a soil tile stops accepting water at 100%;
    - water then percolates into the soil layer below;
    - pore water moves vertically only; neighboring soil does not receive lateral seepage;
    - if all reachable soil is saturated, surface water remains and ponds;
    - evaporation transfers water to atmospheric vapor;
    - deep drainage reaches the authored underground waterway only through a continuous real vertical path.
    """

    def __init__(
        self,
        world,
        env,
        chunk_streamer,
        weather_system,
    ):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system

        # V0.6.2: split the former monolithic 6 Hz soil tick into two
        # half-cycle phases. Total physical cadence is unchanged, but heavy
        # transport/ecology passes no longer land in the same 60 Hz frame.
        self.task = FixedRateTask(SOIL_HZ, max_steps=4)  # compatibility
        self.transport_task = FixedRateTask(SOIL_HZ, max_steps=2, phase=0.0)
        self.ecology_task = FixedRateTask(SOIL_HZ, max_steps=2, phase=0.5)
        self.rng = random.Random(24020)
        self._soil_chunk_cache = {}
        self._soil_chunk_revision = {}
        self._ensure_timer = 0.0

    def _chunk(self, tx, ty):
        return (
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
        )

    def _grass_skin_allowed(self, tx, ty):
        try:
            return bool(self.world.can_support_grass_skin(int(tx), int(ty)))
        except Exception:
            try:
                surface = self.world.backdrop_surface_row(int(tx))
            except Exception:
                surface = self.world.first_solid_row(int(tx))
            if surface is not None and int(ty) > int(surface):
                return False
            if int(ty) > 0 and tile_def(self.world.get_tile(int(tx), int(ty) - 1)).solid:
                return False
            return True

    def _climate_profile(self, tx, ty):
        return self.env.climate_at_chunk(int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE, "temperate")

    def _climate_multiplier(self, table, tx, ty):
        return float(table.get(self._climate_profile(tx, ty), 1.0))

    def _absorbent(self, tx, ty):
        if not (
            0 <= tx
            < self.world.width_tiles
            and 0 <= ty
            < self.world.height_tiles
        ):
            return False

        td = tile_def(
            self.world.get_tile(
                tx,
                ty,
            )
        )

        # Porosity is hydrology, fertility is ecology.  Sand/snow/path tiles
        # may absorb water even when they cannot grow plants.
        return (
            td.solid
            and td.water_absorption > 0
        )

    def _soil_capacity(self, tx, ty):
        """Water capacity of the material actually remaining in a soil tile.

        V0.7.6.0 dirt and sand share the same three-segment ground geometry.
        A 1/3 tile therefore cannot hide a full tile of pore water.
        """
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

    def _ensure_cell(
        self,
        tx,
        ty,
        initial=0.0,
    ):
        if not self._absorbent(
            tx,
            ty,
        ):
            return None

        td = tile_def(
            self.world.get_tile(
                tx,
                ty,
            )
        )
        capacity = self._soil_capacity(tx, ty)
        key = (int(tx), int(ty))
        cell = self.env.soil.get(key)
        if cell is None:
            cell = SoilCell(
                moisture=max(0.0, min(capacity, float(initial))),
                fertility=max(0.0, td.fertility),
                plant_cap=self._random_plant_cap(),
            )
            self.env.soil[key] = cell
        else:
            # Mining may have removed one or two dirt segments since the
            # previous ecology tick.  Clamp only to the remaining physical
            # volume; ToolSystem has already conserved/released any excess.
            cell.moisture = max(0.0, min(capacity, float(cell.moisture)))
        return cell


    def _random_plant_cap(self):
        roll = self.rng.random()
        acc = 0.0
        for stage, weight in SOIL_PLANT_CAP_WEIGHTS:
            acc += float(weight)
            if roll <= acc:
                return int(stage)
        return int(SOIL_PLANT_CAP_WEIGHTS[-1][0])

    def _runtime_chunks(self):
        """Small ecology window around the player.

        The collision streamer is intentionally wider (5x3). Soil transport is
        slow and does not need every streamed chunk at once; 3x3 is enough and
        lets distant soil sleep like tile games do.
        """
        pcx, pcy = getattr(self.chunk_streamer, "player_chunk", (0, 0))
        active = self.chunk_streamer.active_chunks
        result = []
        for cy in range(int(pcy) - 1, int(pcy) + 2):
            for cx in range(int(pcx) - 1, int(pcx) + 2):
                if (cx, cy) in active:
                    result.append((cx, cy))
        return tuple(result)

    def ensure_active_soil(self, initial=0.0):
        # V0.7.0: soil cells are materialized only in the local ecology window.
        # World-load calls pass SOIL_INITIAL_MOISTURE; later newly placed soil
        # uses the default 0.0 so it does not create water from nothing.
        for cx, cy in self._runtime_chunks():
            sx = cx * CHUNK_SIZE
            ex = min(
                self.world.width_tiles,
                (cx + 1)
                * CHUNK_SIZE,
            )

            sy = cy * CHUNK_SIZE
            ey = min(
                self.world.height_tiles,
                (cy + 1)
                * CHUNK_SIZE,
            )

            for ty in range(
                sy,
                ey,
            ):
                for tx in range(
                    sx,
                    ex,
                ):
                    if self._absorbent(
                        tx,
                        ty,
                    ):
                        self._ensure_cell(
                            tx,
                            ty,
                            initial=float(initial),
                        )

    def _active_soil_items(self):
        """Return cached soil membership for the local 3x3 ecology window.

        Cell objects remain mutable, so cached tuples stay current. Membership
        is rebuilt only when that terrain chunk revision changes. Continuous
        water movement no longer rescans thousands of tiles just to rediscover
        the same soil keys.
        """
        items = []
        missing = object()
        for cx, cy in self._runtime_chunks():
            key = (int(cx), int(cy))
            revision = int(self.world.chunk_revision(cx, cy))
            if self._soil_chunk_revision.get(key) != revision:
                chunk_items = []
                sx = int(cx) * CHUNK_SIZE
                ex = min(self.world.width_tiles, sx + CHUNK_SIZE)
                sy = int(cy) * CHUNK_SIZE
                ey = min(self.world.height_tiles, sy + CHUNK_SIZE)
                for ty in range(sy, ey):
                    for tx in range(sx, ex):
                        cell = self.env.soil.get((tx, ty), missing)
                        if cell is not missing:
                            chunk_items.append(((tx, ty), cell))
                self._soil_chunk_cache[key] = tuple(chunk_items)
                self._soil_chunk_revision[key] = revision
            items.extend(self._soil_chunk_cache.get(key, ()))
        return items

    def update(self, dt):
        # Phase A: infiltration + vertical transport.
        self._ensure_timer += max(0.0, float(dt))
        for step in self.transport_task.consume(dt):
            if self._ensure_timer >= 1.0:
                self._ensure_timer = 0.0
                self.ensure_active_soil()
            active_items = self._active_soil_items()
            self._surface_infiltration(step, active_items)
            self._vertical_percolation(step, active_items)

        # Phase B is offset by half a soil period: lateral/ecology/evaporation.
        for step in self.ecology_task.consume(dt):
            active_items = self._active_soil_items()
            self._ash_rehydrate(step)
            self._lateral_capillary(step, active_items)
            self._swamp_cycle(step)
            self._evaporation(step, active_items)
            self._groundwater_evaporation(step)

    def _transfer(
        self,
        source,
        target,
        amount,
        target_key=None,
    ):
        amount = max(0.0, float(amount))
        if amount <= 1e-9:
            return 0.0

        if target_key is None:
            target_capacity = float(SOIL_SATURATION)
        else:
            target_capacity = self._soil_capacity(*target_key)
        capacity = max(0.0, target_capacity - float(target.moisture))

        moved = min(amount, max(0.0, float(source.moisture)), capacity)
        if moved <= 1e-9:
            return 0.0

        source.moisture -= moved
        target.moisture += moved
        source.moisture = max(0.0, float(source.moisture))
        target.moisture = min(target_capacity, float(target.moisture))
        return moved


    def _surface_infiltration(
        self,
        dt,
        active_items,
    ):
        # Surface free water -> soil. This path moves exactly the amount
        # removed from free water into soil moisture.
        for (tx, ty), soil in active_items:

            if not self._absorbent(
                tx,
                ty,
            ):
                continue

            above_y = ty - 1

            # Fractional LOW/MID ground may contain free water in the same
            # tile's empty upper volume. Prefer that contact source; otherwise
            # use the traditional cell immediately above a full tile.
            source_y = ty
            water = self.env.water_amount(tx, source_y)
            if water <= 0.001:
                if above_y < 0:
                    continue
                source_y = above_y
                water = self.env.water_amount(tx, source_y)

            if water <= 0.001:
                continue

            profile = self._climate_profile(tx, ty)
            incoming_temp = self.env.water_temperature_at(
                tx, source_y, self.env.temperature_at(tx, source_y, 20.0)
            )
            if profile in ("alpine", "polar") and float(incoming_temp) < 0.0:
                # Preserve super-cooled runoff on the surface so the thermal
                # phase-change pass can visibly freeze it after it spreads.
                continue

            capacity = max(
                0.0,
                self._soil_capacity(tx, ty)
                - soil.moisture,
            )

            if capacity <= 1e-9:
                # Saturated: do not delete or clamp the free water.
                continue

            local_temp_c = self.env.temperature_at(
                tx,
                ty,
                20.0,
            )

            # Frozen soil is much less permeable, but not mathematically zero.
            if local_temp_c <= -5.0:
                permeability = 0.06
            elif local_temp_c < 0.0:
                permeability = (
                    0.06
                    + (
                        local_temp_c
                        + 5.0
                    )
                    / 5.0
                    * 0.24
                )
            else:
                permeability = 1.0

            permeability *= self._climate_multiplier(
                CLIMATE_INFILTRATION_MULTIPLIER, tx, ty
            )

            # V0.7.7.4: infiltration and surface spreading happen in
            # parallel.  Previously ordinary dirt could consume up to its full
            # remaining pore capacity in one 3 Hz soil tick, so a newly opened
            # lake/cavity appeared to wait for soil saturation before free water
            # could level.  Limit pore uptake to a per-second rate; WaterSystem
            # keeps the rest as mobile surface water.
            material_name = tile_def(self.world.get_tile(tx, ty)).name
            material_mult = 2.0 if material_name in ("sand", "sea_sand") else 1.0
            max_absorb = (
                float(SOIL_ABSORB_RATE)
                * permeability
                * self._climate_multiplier(CLIMATE_INFILTRATION_MULTIPLIER, tx, ty)
                * material_mult
                * float(dt)
            )

            absorbed = min(
                water,
                capacity,
                max_absorb,
            )

            water_temp = (
                self.env.water_temperature_at(
                    tx,
                    source_y,
                    self.env.temperature_at(
                        tx,
                        source_y,
                        local_temp_c,
                    ),
                )
            )

            soil.moisture += absorbed

            self.env.set_water(
                tx,
                source_y,
                water
                - absorbed,
            )

            if absorbed > 0.0:
                self.env.set_temperature(
                    tx,
                    ty,
                    local_temp_c
                    + (
                        water_temp
                        - local_temp_c
                    )
                    * min(
                        0.55,
                        absorbed
                        * 0.45,
                    ),
                )

    def _deposit_to_underground_waterway(self, tx, amount, temperature_c=12.0):
        """Vertical recharge endpoint for porous ground.

        Soil and sand never exchange moisture sideways. Water may re-enter a
        horizontally mobile state only in the authored open underground
        waterway row, where the normal free-water solver takes over.
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

    def _vertical_percolation(
        self,
        dt,
        active_items,
    ):
        # Top-to-bottom iteration allows a wet upper layer to feed the layer
        # below. Only water above field capacity drains under gravity.
        # active_items is already deterministic top-to-bottom, so no global
        # key list or O(N log N) world sort is needed.
        for (tx, ty), source in active_items:

            source_td = tile_def(self.world.get_tile(tx, ty))
            # Coarse desert sand cannot retain water like loam.  Its low field
            # capacity lets fresh water pulse downward through several sand
            # layers before the exposed surface has time to evaporate it all.
            field_capacity = float(SOIL_FIELD_CAPACITY)
            if source_td.name == "sand":
                field_capacity = min(field_capacity, float(DESERT_SAND_FIELD_CAPACITY))
            elif source_td.name == "sea_sand":
                # Submerged marine sand is already pore-saturated and should
                # not siphon the ocean. Exposed beach sand, however, drains
                # vertically like coarse sand.
                try:
                    biome_id = str(self.env.biome_at_chunk(tx // CHUNK_SIZE, ty // CHUNK_SIZE, ""))
                except Exception:
                    biome_id = ""
                if biome_id == "ocean":
                    field_capacity = self._soil_capacity(tx, ty)
                else:
                    field_capacity = min(field_capacity, 0.08)

            mobile = max(
                0.0,
                source.moisture
                - field_capacity,
            )

            if mobile <= 1e-9:
                continue

            below_y = (
                ty + 1
            )

            if self._absorbent(
                tx,
                below_y,
            ):
                target = self._ensure_cell(
                    tx,
                    below_y,
                    initial=0.0,
                )

                local_temp_c = self.env.temperature_at(
                    tx,
                    ty,
                    20.0,
                )

                if local_temp_c <= -5.0:
                    permeability = 0.08
                elif local_temp_c < 0.0:
                    permeability = 0.08 + (
                        local_temp_c + 5.0
                    ) / 5.0 * 0.32
                else:
                    permeability = 1.0

                permeability *= self._climate_multiplier(
                    CLIMATE_INFILTRATION_MULTIPLIER, tx, ty
                )

                self._transfer(
                    source,
                    target,
                    min(
                        mobile,
                        SOIL_VERTICAL_PERCOLATION_RATE
                        * permeability
                        * dt,
                    ),
                    target_key=(tx, below_y),
                )
                continue

            # FIX36: pore water may continue only into the *immediately*
            # adjacent cell.  The old code teleported excess moisture to the
            # authored underground-waterway row whenever the tile below was
            # non-porous; a solid stone/ore layer was therefore bypassed and
            # looked as if rock itself were leaking.
            below_id = self.world.get_tile(tx, below_y)
            below_td = tile_def(below_id)
            if below_td.solid:
                # Stone, ore, igneous rock, ice, wood, etc. are real barriers.
                # Water remains stored in the porous cell above until a real
                # opening/path is mined.
                continue

            # A real open cell directly below lets pore water emerge as free
            # water.  If this happens to be the authored underground channel,
            # the normal WaterSystem can then move it horizontally there.
            try:
                cap = float(self.world.liquid_capacity_at(tx, below_y))
            except Exception:
                cap = 1.0
            current = float(self.env.water_amount(tx, below_y))
            room = max(0.0, cap - current)
            if room <= 1e-9:
                continue
            drain_multiplier = self._climate_multiplier(
                CLIMATE_INFILTRATION_MULTIPLIER, tx, ty
            )
            if source_td.name in ("sand", "sea_sand"):
                drain_multiplier *= float(DESERT_GROUNDWATER_DRAIN_MULTIPLIER)
            drain = min(
                mobile, room,
                SOIL_GROUNDWATER_RATE * drain_multiplier * dt,
            )
            if drain > 0.0:
                self.env.set_water(tx, below_y, current + drain)
                try:
                    local_temp_c = self.env.temperature_at(tx, ty, 20.0)
                    self.env.set_water_temperature(tx, below_y, local_temp_c)
                except Exception:
                    pass
                source.moisture = max(0.0, float(source.moisture) - drain)

    def _lateral_capillary(self, dt, active_items):
        """Disabled by V0.7.6.0 hydrology rule.

        Pore water in dirt/sand is gravity-only and never crosses into the
        neighboring X column. Horizontal subsurface transfer is reserved for
        free water inside the authored underground waterway.
        """
        return 0.0

    def _evaporation(
        self,
        dt,
        active_items,
    ):
        for (tx, ty), soil in active_items:

            # Only exposed soil evaporates directly.
            above_y = (
                ty - 1
            )

            if (
                above_y >= 0
                and tile_def(
                    self.world.get_tile(
                        tx,
                        above_y,
                    )
                ).solid
            ):
                continue

            weather = self.weather_system.weather_at_chunk(
                tx // CHUNK_SIZE,
                ty // CHUNK_SIZE,
            )

            local_temp_c = self.env.temperature_at(
                tx,
                ty,
                20.0,
            )

            if local_temp_c <= 0.0:
                temperature_factor = 0.08
            elif local_temp_c < 20.0:
                temperature_factor = (
                    0.25
                    + local_temp_c
                    / 20.0
                    * 0.75
                )
            else:
                temperature_factor = min(
                    5.0,
                    1.0
                    + (
                        local_temp_c
                        - 20.0
                    )
                    * 0.055,
                )

            evap = min(
                soil.moisture,
                SOIL_EVAP_RATE
                * temperature_factor
                * max(
                    0.03,
                    weather.sunlight,
                )
                * (
                    1.0
                    - weather.cloud
                    * 0.55
                )
                * self._climate_multiplier(CLIMATE_SOIL_EVAP_MULTIPLIER, tx, ty)
                * dt,
            )

            if evap <= 0.0:
                continue

            soil.moisture -= evap

            cx, cy = self._chunk(
                tx,
                ty,
            )

            self.env.add_vapor(
                cx,
                cy,
                evap,
            )
            haze_key = (int(cx), 0)
            self.env.evaporation_haze[haze_key] = min(4.0, float(self.env.evaporation_haze.get(haze_key, 0.0)) + evap * 6.0)

            # Natural evaporation moves water into the atmosphere but does
            # NOT create gameplay lift. Updraft is reserved for fire/explosions.


    def _groundwater_evaporation(self, dt):
        """Hot desert porous columns slowly return deep water to the sky.

        This is intentionally chunk-scale.  Percolation puts conserved water
        into groundwater; desert heat then moves that mass into visible steam
        at the surface, which ReactionSystem later converts to atmospheric
        vapor and AtmosphereSystem can condense into clouds.
        """
        # Groundwater is already a sparse chunk reservoir. Let desert deep
        # water continue evaporating even after the player leaves, instead of
        # tying it to the local ecology window. Cost scales with wet columns,
        # not with total world size.
        active_cx = {int(cx) for cx, _cy in self._runtime_chunks()}
        active_cx.update(int(cx) for cx, _cy in tuple(self.env.groundwater.keys()))
        for cx in active_cx:
            if self.env.climate_at_chunk(cx, 0, "temperate") != "desert":
                continue
            # Sum any vertically keyed groundwater reservoirs in this x-column.
            keys = [k for k in tuple(self.env.groundwater.keys()) if int(k[0]) == cx]
            available = sum(max(0.0, float(self.env.groundwater.get(k, 0.0))) for k in keys)
            if available <= 1e-9:
                continue
            moved = min(available, float(DESERT_GROUNDWATER_EVAP_RATE) * float(dt))
            remain_to_take = moved
            for key in keys:
                if remain_to_take <= 1e-12:
                    break
                have = max(0.0, float(self.env.groundwater.get(key, 0.0)))
                take = min(have, remain_to_take)
                left = have - take
                if left <= 1e-9:
                    self.env.groundwater.pop(key, None)
                else:
                    self.env.groundwater[key] = left
                remain_to_take -= take
            actual = moved - remain_to_take
            if actual <= 1e-9:
                continue
            tx = max(0, min(self.world.width_tiles-1, cx*CHUNK_SIZE + CHUNK_SIZE//2))
            surface = self.world.first_solid_row(tx)
            # Groundwater evaporation is hydrology, not a heat-blast. Feed
            # the chunk-scale vapor reservoir directly; renderer shows it as
            # a broad diffuse haze instead of one vertical steam column.
            self.env.add_vapor(cx, 0, actual)
            haze_key = (int(cx), 0)
            self.env.evaporation_haze[haze_key] = min(4.0, float(self.env.evaporation_haze.get(haze_key, 0.0)) + actual * 9.0)

    def _swamp_cycle(
        self,
        dt,
    ):
        """Grassland <-> swamp transition driven by real standing water.

        Formation:
            GRASS_DIRT + saturated soil + persistent surface pond
                -> SWAMP_SOIL

        Recovery:
            SWAMP_SOIL + no surface pond for long enough
                -> GRASS_DIRT

        This timer is intentionally separate from soil moisture transport.
        Water in lower soil layers is never pulled upward to satisfy a dry
        surface layer.
        """
        for cx, cy in self._runtime_chunks():
            sx = cx * CHUNK_SIZE
            ex = min(
                self.world.width_tiles,
                (
                    cx
                    + 1
                )
                * CHUNK_SIZE,
            )

            sy = cy * CHUNK_SIZE
            ey = min(
                self.world.height_tiles,
                (
                    cy
                    + 1
                )
                * CHUNK_SIZE,
            )

            for ty in range(
                sy,
                ey,
            ):
                for tx in range(
                    sx,
                    ex,
                ):
                    tile_id = self.world.get_tile(
                        tx,
                        ty,
                    )

                    if tile_id not in (
                        DIRT,
                        GRASS_DIRT,
                        SWAMP_SOIL,
                    ):
                        self.env.swamp_wet_time.pop(
                            (
                                tx,
                                ty,
                            ),
                            None,
                        )
                        self.env.swamp_dry_time.pop(
                            (
                                tx,
                                ty,
                            ),
                            None,
                        )
                        continue

                    key = (
                        tx,
                        ty,
                    )

                    soil = self.env.soil.get(
                        key
                    )

                    if soil is None:
                        continue

                    above_y = max(0, ty - 1)

                    # V0.7.7.3: a LOW/MID topsoil cell can physically share
                    # its missing upper fraction with free water.  Older swamp
                    # logic only looked at ty-1, so a perfectly valid puddle
                    # sitting inside the same fractional tile was invisible to
                    # the ecology timer.  Count both locations.
                    pond = min(1.0,
                        self.env.water_amount(tx, ty)
                        + self.env.water_amount(tx, above_y)
                    )

                    # Permanent lake/ocean bed is saturated sediment, not a
                    # marsh transition.  Dynamic swamp conversion is reserved
                    # for terrestrial/wetland biomes with shallow ponding.
                    try:
                        biome_here = str(self.env.biome_at_chunk(
                            tx // CHUNK_SIZE, ty // CHUNK_SIZE, ""
                        ))
                    except Exception:
                        biome_here = ""
                    permanent_open_water = biome_here in ("lake", "ocean")

                    if tile_id in (DIRT, GRASS_DIRT):
                        # A grassland tile only becomes swamp after the soil
                        # body is effectively saturated and free water is
                        # actually standing above it.
                        local_capacity = max(1e-9, self._soil_capacity(tx, ty))
                        flooded = (
                            (not permanent_open_water)
                            and soil.moisture >= local_capacity - max(0.01, local_capacity * 0.02)
                            and pond >= SWAMP_STANDING_WATER_THRESHOLD
                        )

                        if flooded:
                            clock = (
                                float(
                                    self.env.swamp_wet_time.get(
                                        key,
                                        0.0,
                                    )
                                )
                                + dt
                            )

                            self.env.swamp_wet_time[
                                key
                            ] = clock

                            if clock >= SWAMP_FORM_SECONDS:
                                self.world.set_tile(
                                    tx,
                                    ty,
                                    SWAMP_SOIL,
                                    track_change=True,
                                )

                                self.env.swamp_wet_time.pop(
                                    key,
                                    None,
                                )

                                self.env.swamp_dry_time.pop(
                                    key,
                                    None,
                                )

                                plant = self.env.plants.get(
                                    key
                                )

                                if plant is not None:
                                    plant.state = "swamp_withered"
                                    plant.regrow_progress = 0.0
                                    plant.recovery_game_seconds = 0.0
                        else:
                            self.env.swamp_wet_time.pop(
                                key,
                                None,
                            )

                    else:
                        # SWAMP_SOIL remains swamp while surface water is
                        # present. Once ponding disappears, the terrain needs
                        # a sustained dry interval before returning grassland.
                        if pond <= SWAMP_DRY_WATER_THRESHOLD:
                            clock = (
                                float(
                                    self.env.swamp_dry_time.get(
                                        key,
                                        0.0,
                                    )
                                )
                                + dt
                            )

                            self.env.swamp_dry_time[
                                key
                            ] = clock

                            if clock >= SWAMP_RECOVER_SECONDS:
                                self.world.set_tile(
                                    tx,
                                    ty,
                                    GRASS_DIRT if self._grass_skin_allowed(tx, ty) else DIRT,
                                    track_change=True,
                                )

                                self.env.swamp_dry_time.pop(
                                    key,
                                    None,
                                )

                                self.env.swamp_wet_time.pop(
                                    key,
                                    None,
                                )

                                plant = self.env.plants.get(
                                    key
                                )

                                if (
                                    plant is not None
                                    and getattr(
                                        plant,
                                        "state",
                                        "normal",
                                    )
                                    == "swamp_withered"
                                ):
                                    plant.state = "swamp_recovering"
                                    plant.regrow_progress = 0.0
                                    plant.regrow_total_game_seconds = (
                                        SWAMP_WITHER_RECOVERY_SECONDS
                                    )
                        else:
                            self.env.swamp_dry_time.pop(
                                key,
                                None,
                            )

    def _ash_rehydrate(
        self,
        dt,
    ):
        # V0.4.22:
        # Water contact STARTS the ash -> soil reaction.
        # The old logic required a puddle to stay physically present for the
        # full 5 seconds, but Terraria-style surface water often flows away
        # immediately. That made ash appear permanently stuck.
        for cx, cy in self._runtime_chunks():
            sx = (
                cx
                * CHUNK_SIZE
            )
            ex = min(
                self.world.width_tiles,
                (
                    cx
                    + 1
                )
                * CHUNK_SIZE,
            )

            sy = (
                cy
                * CHUNK_SIZE
            )
            ey = min(
                self.world.height_tiles,
                (
                    cy
                    + 1
                )
                * CHUNK_SIZE,
            )

            for ty in range(
                sy,
                ey,
            ):
                for tx in range(
                    sx,
                    ex,
                ):
                    key = (
                        tx,
                        ty,
                    )

                    if self.world.get_tile(
                        tx,
                        ty,
                    ) != ASH:
                        self.env.ash_wet_time.pop(
                            key,
                            None,
                        )
                        continue

                    wet = (
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

                    # Any meaningful water contact starts hydration.
                    if (
                        wet
                        >= ASH_REHYDRATE_TRIGGER_WATER
                    ):
                        self.env.ash_wet_time[
                            key
                        ] = max(
                            ASH_REHYDRATE_CONTACT_EPSILON,
                            float(
                                self.env.ash_wet_time.get(
                                    key,
                                    0.0,
                                )
                            ),
                        )

                    if key not in self.env.ash_wet_time:
                        continue

                    clock = (
                        float(
                            self.env.ash_wet_time.get(
                                key,
                                0.0,
                            )
                        )
                        + dt
                    )

                    if (
                        clock
                        >= ASH_REHYDRATE_SECONDS
                    ):
                        recovered_tile = (
                            GRASS_DIRT
                            if (
                                ASH_RECOVERY_TO_GRASS_DIRT
                                and self._grass_skin_allowed(tx, ty)
                            )
                            else DIRT
                        )

                        self.world.set_tile(
                            tx,
                            ty,
                            recovered_tile,
                            track_change=True,
                        )

                        self.env.ash_wet_time.pop(
                            key,
                            None,
                        )

                        self.env.ash.pop(
                            key,
                            None,
                        )

                        self.env.soil[
                            key
                        ] = SoilCell(
                            moisture=min(
                                SOIL_SATURATION,
                                max(
                                    ASH_RECOVERED_SOIL_MOISTURE,
                                    wet
                                    * 0.65,
                                ),
                            ),
                            fertility=ASH_SOIL_FERTILITY,
                            plant_cap=self._random_plant_cap(),
                        )
                    else:
                        self.env.ash_wet_time[
                            key
                        ] = clock

    def _step(self, dt):
        # Build the bounded active working set once and reuse it across all
        # high-cost soil passes in this tick.
        active_items = self._active_soil_items()
        self._surface_infiltration(
            dt,
            active_items,
        )
        self._ash_rehydrate(
            dt
        )
        self._vertical_percolation(
            dt,
            active_items,
        )
        self._lateral_capillary(
            dt,
            active_items,
        )
        self._swamp_cycle(
            dt
        )
        self._evaporation(
            dt,
            active_items,
        )
