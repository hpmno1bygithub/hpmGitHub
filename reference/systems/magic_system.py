# -*- coding: utf-8 -*-
import math
from collections import deque
from systems.creature_attributes import elemental_multiplier
from config import (
    TILE_SIZE,
    FIREBALL_MANA_COST,
    FIREBALL_SPEED,
    FIREBALL_LIFETIME,
    FIREBALL_POWER,
    FIREBALL_GRAVITY,
    FIREBALL_MAX_FALL_SPEED,
    FIREBALL_BURN_PENETRATION,
    WATERBALL_MANA_COST,
    WATERBALL_SPEED,
    WATERBALL_LIFETIME,
    WATERBALL_POWER,
    WATERBALL_GRAVITY,
    WATERBALL_MAX_FALL_SPEED,
    WATERBALL_AMOUNT,
    WATERBALL_SPLASH_RADIUS,
    SOIL_SATURATION,
    FIREBALL_SOIL_EVAPORATE_TO,
    CHUNK_SIZE,
    WATERBALL_MIN_CAST_MASS,
    FIREBALL_TEMPERATURE_C,
    FIREBALL_RANK,
    WATERBALL_TEMPERATURE_C,
    ICEBALL_MANA_COST,
    ICEBALL_SPEED,
    ICEBALL_LIFETIME,
    ICEBALL_POWER,
    ICEBALL_GRAVITY,
    ICEBALL_MAX_FALL_SPEED,
    ICEBALL_AMOUNT,
    ICEBALL_TEMPERATURE_C,
    ICEBALL_MIN_CAST_MASS,
    MAGIC_VAPOR_GATHER_RADIUS_CHUNKS,
    WATER_MASS_UNITS_PER_TILE,
    WATERBALL_MAGIC_WATER_UNITS,
    ICEBALL_MAGIC_WATER_UNITS,
    FIREBALL_MAGIC_ENERGY_UNITS,
    FIREBALL_EVAPORATE_WATER_UNITS,
    FIREBALL_ENERGY_PER_EVAP_WATER_UNIT,
    EVAP_UPDRAFT_PER_WATER_UNIT,
    MAGIC_LEVEL_MULTIPLIER_L1,
    MAGIC_LEVEL_MULTIPLIER_L2,
    MAGIC_LEVEL_MULTIPLIER_L3,
    ICE_LEVEL1_TILES,
    ICE_LEVEL2_TILES,
    ICE_LEVEL3_TILES,
    ICE_WATER_CHAIN_LEVEL1_TILES,
    ICE_WATER_CHAIN_LEVEL2_TILES,
    ICE_WATER_CHAIN_LEVEL3_TILES,
    ICE_WATER_CHAIN_MIN_AMOUNT,
    MAGIC_LEVEL3_FRAGMENT_COUNT,
    MAGIC_FRAGMENT_SPEED_SCALE,
    MAGIC_FRAGMENT_LIFETIME_SCALE,
    MAGIC_RADIUS_LEVEL1,
    MAGIC_RADIUS_LEVEL2,
    MAGIC_RADIUS_LEVEL3,
    FIREBALL_LIFT_LEVEL1,
    FIREBALL_LIFT_LEVEL2,
    FIREBALL_LIFT_LEVEL3,
    MAGIC_LEVEL3_BURST_TILES,
    L3_FIREBALL_CENTER_ASH,
    ASH_REHYDRATE_CONTACT_EPSILON,
    MAGIC_CHARGE_LEVEL1_MAX,
    MAGIC_CHARGE_LEVEL2_MAX,
    MAGIC_CHARGE_LEVEL3_MAX,
    ELECTRICBALL_MANA_COST,
    ELECTRICBALL_SPEED,
    ELECTRICBALL_LIFETIME,
    ELECTRICBALL_POWER,
    ELECTRICBALL_GRAVITY,
    ELECTRICBALL_MAX_FALL_SPEED,
    ELECTRICBALL_CHARGE,
    ELECTRICBALL_ENERGY_UNITS,
    ELECTRICBALL_WATER_SHOCK_SCALE,
    ELECTRICBALL_DRY_SHOCK_SCALE,
    ELECTRICBALL_L3_ELECTROLYSIS_WATER,
    ELECTRICBALL_L3_POISON_GAS_YIELD,
    POISON_GAS_LIFETIME_SECONDS,
    POISON_GAS_FIREBALL_CHAIN_MAX_CELLS,
    MAGIC_CREATURE_DAMAGE_FIRE,
    MAGIC_CREATURE_DAMAGE_WATER,
    MAGIC_CREATURE_DAMAGE_ICE,
    MAGIC_CREATURE_DAMAGE_ELECTRIC,
    MAGIC_CREATURE_KNOCKBACK,
    MAGIC_CREATURE_ICE_SLOW_SECONDS,
    MAGIC_CREATURE_ELECTRIC_STUN_SECONDS,
    MAGIC_ELEMENT_ADVANTAGE_MULTIPLIER,
    MAGIC_ELEMENT_RESIST_MULTIPLIER,
)
from entities.fireball import Fireball
from entities.waterball import Waterball
from entities.iceball import Iceball
from entities.electricball import ElectricBall
from entities.creature import Creature
from world.tile_registry import (
    tile_def,
    DIRT,
    GRASS_DIRT,
    ICE,
    AIR,
    ASH,
)
from world.environment_state import SoilCell
from systems.magic_ledger import MagicLedger


class MagicSystem:
    """Selectable fire / water / ice / electric projectile magic.

    Elemental projectiles follow the shared aiming/ballistic path, then hand
    their impact to the corresponding world system (fire, water, thermal,
    electrical/reaction).

    Fireball:
      - water extinguishes it
      - direct hit can ignite burnable material

    Waterball:
      - direct hit extinguishes fire
      - stone/non-absorbent surface -> becomes standing water
      - soil/grass -> whole ball is absorbed into SoilCell moisture
      - existing water -> merges into that water cell
    """

    MAGIC_FIRE = "fireball"
    MAGIC_WATER = "waterball"
    MAGIC_ICE = "iceball"
    MAGIC_ELECTRIC = "electricball"

    def __init__(self, player, world, environment, events, scene=None):
        self.player = player
        self.world = world
        self.environment = environment
        self.events = events
        self.scene = scene

        self.selected_magic = self.MAGIC_FIRE
        self.fireballs = []
        self.waterballs = []
        self.iceballs = []
        self.electricballs = []
        self._id_seq = 0

        # V0.7.5.9: underwater ice formed by an iceball is not teleported to
        # the surface.  It enters a short-lived buoyancy queue and swaps upward
        # through the same water column one cell at a time.  Repeated casts
        # therefore rise independently and stop only at the water surface or
        # when they contact an earlier floating ice block.
        self._buoyant_ice = {}
        self._buoyant_ice_step_seconds = 0.14

        self.last_water_source = {
            "requested": 0.0,
            "available": 0.0,
            "taken": 0.0,
            "chunks": (),
            "success": True,
        }

        # Tracks only spell-created matter/energy. The world itself is open.
        self.ledger = MagicLedger()

        # V0.6.5: magic is selected here, but casting is routed by GameApp's
        # action mode.  The aim joystick supplies direction only.
        self.action_messages_enabled = False

    # --------------------------------------------------------
    # Selection
    # --------------------------------------------------------

    @property
    def selected_name(self):
        return {
            self.MAGIC_FIRE: "火球",
            self.MAGIC_WATER: "水球",
            self.MAGIC_ICE: "冰球",
            self.MAGIC_ELECTRIC: "電球",
        }.get(
            self.selected_magic,
            "火球",
        )

    def set_magic(self, magic_id):
        if magic_id not in (
            self.MAGIC_FIRE,
            self.MAGIC_WATER,
            self.MAGIC_ICE,
            self.MAGIC_ELECTRIC,
        ):
            return self.selected_name
        self.selected_magic = magic_id
        return self.selected_name

    def toggle_magic(self, steps=1):
        order = (
            self.MAGIC_FIRE,
            self.MAGIC_WATER,
            self.MAGIC_ICE,
            self.MAGIC_ELECTRIC,
        )

        try:
            index = order.index(
                self.selected_magic
            )
        except ValueError:
            index = 0

        try:
            step_count = max(1, int(steps))
        except Exception:
            step_count = 1
        self.selected_magic = order[
            (index + step_count) % len(order)
        ]

        self.events.emit(
            'message',
            text=f'魔法切換：{self.selected_name}',
        )
        return self.selected_name

    # --------------------------------------------------------
    # Conserved spell-water gathering
    # --------------------------------------------------------

    def _world_chunk(
        self,
        x,
        y,
    ):
        chunk_px = (
            CHUNK_SIZE
            * TILE_SIZE
        )

        max_cx = max(
            0,
            (
                self.world.width_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        max_cy = max(
            0,
            (
                self.world.height_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        cx = max(
            0,
            min(
                max_cx,
                int(
                    x
                    // chunk_px
                ),
            ),
        )

        cy = max(
            0,
            min(
                max_cy,
                int(
                    y
                    // chunk_px
                ),
            ),
        )

        return (
            cx,
            cy,
        )

    def _vapor_chunk_order(
        self,
        world_x,
        world_y,
        radius=None,
    ):
        if radius is None:
            radius = (
                MAGIC_VAPOR_GATHER_RADIUS_CHUNKS
            )

        center_cx, center_cy = (
            self._world_chunk(
                world_x,
                world_y,
            )
        )

        max_cx = max(
            0,
            (
                self.world.width_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        max_cy = max(
            0,
            (
                self.world.height_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        candidates = []

        for dy in range(
            -radius,
            radius + 1,
        ):
            for dx in range(
                -radius,
                radius + 1,
            ):
                distance = (
                    abs(
                        dx
                    )
                    + abs(
                        dy
                    )
                )

                if distance > radius:
                    continue

                cx = (
                    center_cx
                    + dx
                )

                cy = (
                    center_cy
                    + dy
                )

                if not (
                    0 <= cx <= max_cx
                    and 0 <= cy <= max_cy
                ):
                    continue

                candidates.append(
                    (
                        distance,
                        abs(
                            dy
                        ),
                        abs(
                            dx
                        ),
                        cx,
                        cy,
                    )
                )

        candidates.sort()

        return tuple(
            (
                cx,
                cy,
            )
            for (
                _distance,
                _ady,
                _adx,
                cx,
                cy,
            ) in candidates
        )

    def available_condensable_vapor(
        self,
        world_x=None,
        world_y=None,
        radius=None,
    ):
        if world_x is None:
            world_x = self.player.x

        if world_y is None:
            world_y = self.player.y

        return sum(
            self.environment.state.vapor_amount(
                cx,
                cy,
            )
            for cx, cy
            in self._vapor_chunk_order(
                world_x,
                world_y,
                radius,
            )
        )

    def _take_vapor_from_local_air_mass(
        self,
        amount,
        world_x=None,
        world_y=None,
    ):
        """Atomically gather H2O from nearby atmospheric chunks.

        If the neighborhood does not contain enough mass, every partial
        withdrawal is rolled back. Therefore a failed spell never loses H2O.
        """
        requested = max(
            0.0,
            float(
                amount
            ),
        )

        if world_x is None:
            # IMPORTANT: use PLAYER location, not projectile spawn point.
            # Spawn offsets can cross a chunk boundary and must not change
            # which atmospheric reservoir is considered "near the caster".
            world_x = self.player.x

        if world_y is None:
            world_y = self.player.y

        chunks = self._vapor_chunk_order(
            world_x,
            world_y,
        )

        available = sum(
            self.environment.state.vapor_amount(
                cx,
                cy,
            )
            for cx, cy in chunks
        )

        if (
            available
            + 1e-12
            < requested
        ):
            self.last_water_source = {
                "requested": requested,
                "available": available,
                "taken": 0.0,
                "chunks": chunks,
                "success": False,
            }

            return 0.0

        remaining = requested
        withdrawals = []

        for cx, cy in chunks:
            if remaining <= 1e-12:
                break

            taken = (
                self.environment.state.take_vapor(
                    cx,
                    cy,
                    remaining,
                )
            )

            if taken > 0.0:
                withdrawals.append(
                    (
                        cx,
                        cy,
                        taken,
                    )
                )

                remaining -= taken

        taken_total = (
            requested
            - remaining
        )

        # Transaction safety: rollback if floating-point/pathological state
        # prevented the complete requested amount from being collected.
        if remaining > 1e-9:
            for cx, cy, taken in withdrawals:
                self.environment.state.add_vapor(
                    cx,
                    cy,
                    taken,
                )

            self.last_water_source = {
                "requested": requested,
                "available": available,
                "taken": 0.0,
                "chunks": chunks,
                "success": False,
            }

            return 0.0

        self.last_water_source = {
            "requested": requested,
            "available": available,
            "taken": taken_total,
            "chunks": tuple(
                (
                    cx,
                    cy,
                    taken,
                )
                for cx, cy, taken
                in withdrawals
            ),
            "success": True,
        }

        return taken_total

        # --------------------------------------------------------
# --------------------------------------------------------
    # --------------------------------------------------------
    # Magic matter / energy helpers
    # --------------------------------------------------------

    @staticmethod
    def internal_water_to_units(
        internal_amount,
    ):
        return max(
            0.0,
            float(
                internal_amount
            ),
        ) * WATER_MASS_UNITS_PER_TILE

    @staticmethod
    def water_units_to_internal(
        units,
    ):
        return max(
            0.0,
            float(
                units
            ),
        ) / WATER_MASS_UNITS_PER_TILE

    def _evaporation_updraft(
        self,
        tx,
        ty,
        internal_amount,
    ):
        units = self.internal_water_to_units(
            internal_amount
        )

        if units <= 0.0:
            return

        self.environment.state.add_updraft(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
            units
            * EVAP_UPDRAFT_PER_WATER_UNIT,
        )

    def _release_fireball_energy(
        self,
        fb,
        tx=None,
        ty=None,
    ):
        remaining = max(
            0.0,
            float(
                getattr(
                    fb,
                    "energy_units",
                    0.0,
                )
            ),
        )

        if remaining <= 0.0:
            return 0.0

        if (
            tx is not None
            and ty is not None
            and 0 <= tx < self.world.width_tiles
            and 0 <= ty < self.world.height_tiles
        ):
            current = (
                self.environment.state.temperature_at(
                    tx,
                    ty,
                    20.0,
                )
            )

            self.environment.state.set_temperature(
                tx,
                ty,
                current
                + remaining
                * 0.06,
            )

        self.ledger.record_fire_heat(
            remaining
        )

        fb.energy_units = 0.0
        return remaining

    def _evaporate_soil_with_fireball(
        self,
        tx,
        ty,
        fb,
    ):
        soil = self.environment.state.soil.get(
            (
                tx,
                ty,
            )
        )

        if soil is None:
            return 0.0

        capacity_units = max(
            0.0,
            float(
                getattr(
                    fb,
                    "evap_capacity_units",
                    FIREBALL_EVAPORATE_WATER_UNITS,
                )
            ),
        )

        if capacity_units <= 0.0:
            return 0.0

        available_units = self.internal_water_to_units(
            soil.moisture
        )

        evaporated_units = min(
            available_units,
            capacity_units,
        )

        if evaporated_units <= 0.0:
            return 0.0

        evaporated_internal = (
            self.water_units_to_internal(
                evaporated_units
            )
        )

        soil.moisture = max(
            0.0,
            soil.moisture
            - evaporated_internal,
        )

        self.environment.state.add_vapor(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
            evaporated_internal,
        )

        self._evaporation_updraft(
            tx,
            ty,
            evaporated_internal,
        )

        energy_used = min(
            float(
                getattr(
                    fb,
                    "energy_units",
                    FIREBALL_MAGIC_ENERGY_UNITS,
                )
            ),
            evaporated_units
            * FIREBALL_ENERGY_PER_EVAP_WATER_UNIT,
        )

        fb.energy_units = max(
            0.0,
            float(
                getattr(
                    fb,
                    "energy_units",
                    0.0,
                )
            )
            - energy_used,
        )

        fb.evap_capacity_units = max(
            0.0,
            capacity_units
            - evaporated_units,
        )

        self.ledger.record_fire_evaporation(
            evaporated_units,
            energy_used,
        )

        return evaporated_units

    def _evaporate_free_water_with_fireball(
        self,
        tx,
        ty,
        fb,
    ):
        water = (
            self.environment.state.water_amount(
                tx,
                ty,
            )
        )

        if water <= 0.0:
            return 0.0

        capacity_units = max(
            0.0,
            float(
                getattr(
                    fb,
                    "evap_capacity_units",
                    FIREBALL_EVAPORATE_WATER_UNITS,
                )
            ),
        )

        if capacity_units <= 0.0:
            return 0.0

        available_units = self.internal_water_to_units(
            water
        )

        evaporated_units = min(
            available_units,
            capacity_units,
        )

        evaporated_internal = (
            self.water_units_to_internal(
                evaporated_units
            )
        )

        old_water_temp = (
            self.environment.state.water_temperature_at(
                tx,
                ty,
                self.environment.state.temperature_at(
                    tx,
                    ty,
                    20.0,
                ),
            )
        )

        remaining = max(
            0.0,
            water
            - evaporated_internal,
        )

        self.environment.state.set_water(
            tx,
            ty,
            remaining,
        )

        if remaining > 1e-12:
            self.environment.state.set_water_temperature(
                tx,
                ty,
                old_water_temp
                + (
                    fb.temperature_c
                    - old_water_temp
                )
                * 0.12,
            )

        self.environment.state.add_vapor(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
            evaporated_internal,
        )

        self._evaporation_updraft(
            tx,
            ty,
            evaporated_internal,
        )

        energy_used = min(
            float(
                getattr(
                    fb,
                    "energy_units",
                    FIREBALL_MAGIC_ENERGY_UNITS,
                )
            ),
            evaporated_units
            * FIREBALL_ENERGY_PER_EVAP_WATER_UNIT,
        )

        fb.energy_units = max(
            0.0,
            float(
                getattr(
                    fb,
                    "energy_units",
                    0.0,
                )
            )
            - energy_used,
        )

        fb.evap_capacity_units = max(
            0.0,
            capacity_units
            - evaporated_units,
        )

        self.ledger.record_fire_evaporation(
            evaporated_units,
            energy_used,
        )

        return evaporated_units

    def _level_multiplier(
        self,
        level,
    ):
        return {
            1: MAGIC_LEVEL_MULTIPLIER_L1,
            2: MAGIC_LEVEL_MULTIPLIER_L2,
            3: MAGIC_LEVEL_MULTIPLIER_L3,
        }.get(
            int(
                level
            ),
            MAGIC_LEVEL_MULTIPLIER_L1,
        )

    def _ice_tile_count(
        self,
        level,
    ):
        return {
            1: ICE_LEVEL1_TILES,
            2: ICE_LEVEL2_TILES,
            3: ICE_LEVEL3_TILES,
        }.get(
            int(
                level
            ),
            ICE_LEVEL1_TILES,
        )

    def _radius_for_level(
        self,
        level,
    ):
        return {
            1: MAGIC_RADIUS_LEVEL1,
            2: MAGIC_RADIUS_LEVEL2,
            3: MAGIC_RADIUS_LEVEL3,
        }.get(
            int(
                level
            ),
            MAGIC_RADIUS_LEVEL1,
        )

    def _fireball_lift_for_level(
        self,
        level,
    ):
        return {
            1: FIREBALL_LIFT_LEVEL1,
            2: FIREBALL_LIFT_LEVEL2,
            3: FIREBALL_LIFT_LEVEL3,
        }.get(
            int(
                level
            ),
            FIREBALL_LIFT_LEVEL1,
        )

    def _pulse_fireball_lift(
        self,
        x,
        y,
        level,
        impact_scale=1.0,
    ):
        tx, ty = self._cell_from_world(
            x,
            y,
        )

        if not (
            0 <= tx
            < self.world.width_tiles
            and 0 <= ty
            < self.world.height_tiles
        ):
            return

        self.environment.state.pulse_fireball_lift(
            tx // CHUNK_SIZE,
            ty // CHUNK_SIZE,
            self._fireball_lift_for_level(
                level
            )
            * max(
                0.0,
                float(
                    impact_scale
                ),
            ),
        )

    def _impact_normal(
        self,
        hit_tx,
        hit_ty,
        impact_x,
        impact_y,
    ):
        center_x = (
            hit_tx
            + 0.5
        ) * TILE_SIZE

        center_y = (
            hit_ty
            + 0.5
        ) * TILE_SIZE

        dx = (
            impact_x
            - center_x
        )

        dy = (
            impact_y
            - center_y
        )

        if abs(
            dx
        ) > abs(
            dy
        ):
            return (
                1.0
                if dx > 0.0
                else -1.0,
                0.0,
            )

        return (
            0.0,
            1.0
            if dy > 0.0
            else -1.0,
        )

    def _fan_directions(
        self,
        normal_x,
        normal_y,
        count,
    ):
        tangent_x = -normal_y
        tangent_y = normal_x

        if count <= 1:
            return [
                (
                    normal_x,
                    normal_y,
                )
            ]

        directions = []

        for i in range(
            count
        ):
            if count == 1:
                lateral = 0.0
            else:
                lateral = (
                    -1.20
                    + (
                        2.40
                        * i
                        / (
                            count
                            - 1
                        )
                    )
                )

            dx = (
                normal_x
                * 0.92
                + tangent_x
                * lateral
            )

            dy = (
                normal_y
                * 0.92
                + tangent_y
                * lateral
            )

            length = max(
                1e-6,
                (
                    dx
                    * dx
                    + dy
                    * dy
                ) ** 0.5,
            )

            directions.append(
                (
                    dx
                    / length,
                    dy
                    / length,
                )
            )

        return directions

    # Spawn / cast
    # --------------------------------------------------------

    def _spawn_origin(self, facing):
        y = self.player.y - self.player.height() * 0.62
        x = self.player.x + facing * (self.player.width() * 0.55 + 10)
        return float(x), float(y)

    def _spawn_fireball(
        self,
        x,
        y,
        facing,
        level=1,
        fragment=False,
        vx=None,
        vy=None,
        energy_units=None,
        evap_capacity_units=None,
    ):
        self._id_seq += 1

        level = max(
            1,
            min(
                3,
                int(
                    level
                ),
            ),
        )

        multiplier = (
            self._level_multiplier(
                level
            )
        )

        if energy_units is None:
            energy_units = (
                FIREBALL_MAGIC_ENERGY_UNITS
                * multiplier
            )

        if evap_capacity_units is None:
            evap_capacity_units = (
                FIREBALL_EVAPORATE_WATER_UNITS
                * multiplier
            )

        if vx is None:
            vx = (
                float(
                    facing
                )
                * FIREBALL_SPEED
            )

        if vy is None:
            vy = 0.0

        fb = Fireball(
            entity_id=f'fireball_{self._id_seq}',
            x=float(
                x
            ),
            y=float(
                y
            ),
            vx=float(
                vx
            ),
            vy=float(
                vy
            ),
            radius=self._radius_for_level(
                level
            ),
            life=(
                FIREBALL_LIFETIME
                * (
                    MAGIC_FRAGMENT_LIFETIME_SCALE
                    if fragment
                    else 1.0
                )
            ),
            power=(
                FIREBALL_POWER
                * multiplier
            ),
            temperature_c=(
                FIREBALL_TEMPERATURE_C
                * (
                    1.0
                    + 0.12
                    * (
                        level
                        - 1
                    )
                )
            ),
            energy_units=float(
                energy_units
            ),
            evap_capacity_units=float(
                evap_capacity_units
            ),
            level=level,
            fragment=bool(
                fragment
            ),
            active=True,
        )

        self.fireballs.append(
            fb
        )

        return fb

    def _spawn_waterball(
        self,
        x,
        y,
        facing,
        water_mass,
        level=1,
        fragment=False,
        vx=None,
        vy=None,
    ):
        self._id_seq += 1

        level = max(
            1,
            min(
                3,
                int(
                    level
                ),
            ),
        )

        if vx is None:
            vx = (
                float(
                    facing
                )
                * WATERBALL_SPEED
            )

        if vy is None:
            vy = 0.0

        wb = Waterball(
            entity_id=f'waterball_{self._id_seq}',
            x=float(
                x
            ),
            y=float(
                y
            ),
            vx=float(
                vx
            ),
            vy=float(
                vy
            ),
            radius=self._radius_for_level(
                level
            ),
            life=(
                WATERBALL_LIFETIME
                * (
                    MAGIC_FRAGMENT_LIFETIME_SCALE
                    if fragment
                    else 1.0
                )
            ),
            power=(
                WATERBALL_POWER
                * self._level_multiplier(
                    level
                )
            ),
            temperature_c=WATERBALL_TEMPERATURE_C,
            water_mass=float(
                water_mass
            ),
            level=level,
            fragment=bool(
                fragment
            ),
            active=True,
        )

        self.waterballs.append(
            wb
        )

        return wb

    def _spawn_iceball(
        self,
        x,
        y,
        facing,
        water_mass,
        level=1,
        vx=None,
        vy=None,
    ):
        self._id_seq += 1

        level = max(
            1,
            min(
                3,
                int(
                    level
                ),
            ),
        )

        if vx is None:
            vx = (
                float(facing)
                * ICEBALL_SPEED
            )

        if vy is None:
            vy = 0.0

        ib = Iceball(
            entity_id=f'iceball_{self._id_seq}',
            x=float(
                x
            ),
            y=float(
                y
            ),
            vx=float(vx),
            vy=float(vy),
            radius=self._radius_for_level(
                level
            ),
            life=ICEBALL_LIFETIME,
            power=(
                ICEBALL_POWER
                * self._ice_tile_count(
                    level
                )
            ),
            water_mass=float(
                water_mass
            ),
            temperature_c=ICEBALL_TEMPERATURE_C,
            level=level,
            fragment=False,
            active=True,
        )

        self.iceballs.append(
            ib
        )

        return ib

    def _spawn_electricball(
        self, x, y, facing, level=1, vx=None, vy=None, charge=None, energy_units=None
    ):
        self._id_seq += 1
        level=max(1,min(3,int(level)))
        multiplier=self._level_multiplier(level)
        if vx is None:
            vx=float(facing)*ELECTRICBALL_SPEED
        if vy is None:
            vy=0.0
        if charge is None:
            charge=ELECTRICBALL_CHARGE*multiplier
        if energy_units is None:
            energy_units=ELECTRICBALL_ENERGY_UNITS*multiplier
        eb=ElectricBall(
            entity_id=f'electricball_{self._id_seq}', x=float(x), y=float(y),
            vx=float(vx), vy=float(vy), radius=self._radius_for_level(level),
            life=ELECTRICBALL_LIFETIME, power=ELECTRICBALL_POWER*multiplier,
            charge=float(charge), energy_units=float(energy_units), level=level, active=True,
        )
        self.electricballs.append(eb)
        return eb

    def _charge_level_from_seconds(
        self,
        seconds,
    ):
        seconds = max(
            0.0,
            min(
                MAGIC_CHARGE_LEVEL3_MAX,
                float(seconds),
            ),
        )

        if seconds < MAGIC_CHARGE_LEVEL1_MAX:
            return 1
        if seconds < MAGIC_CHARGE_LEVEL2_MAX:
            return 2
        return 3

    def _ballistic_velocity_to_target(
        self,
        x,
        y,
        target_x,
        target_y,
        nominal_speed,
        gravity,
    ):
        dx = float(target_x) - float(x)
        dy = float(target_y) - float(y)

        distance = max(
            1.0,
            math.hypot(dx, dy),
        )

        # Choose a stable time-of-flight from the nominal projectile speed,
        # then solve y(t)=target including gravity. This makes the visible
        # ballistic arc pass through the crosshair instead of aiming straight
        # at it and falling below.
        flight_time = max(
            0.12,
            min(
                1.35,
                distance
                / max(
                    80.0,
                    float(nominal_speed),
                ),
            ),
        )

        vx = dx / flight_time
        vy = (
            dy
            - 0.5
            * float(gravity)
            * flight_time
            * flight_time
        ) / flight_time

        return float(vx), float(vy)

    def cast_at(
        self,
        target_x,
        target_y,
        charge_seconds=0.0,
    ):
        level = self._charge_level_from_seconds(
            charge_seconds
        )

        self._on_attack({
            'target_x': float(target_x),
            'target_y': float(target_y),
            'charge_seconds': float(charge_seconds),
            'level': int(level),
        })

    def _emit_cast_audio(self, magic_id, level, x, y):
        try:
            self.events.emit(
                "audio_magic_cast",
                magic=str(magic_id),
                level=int(level),
                x=float(x),
                y=float(y),
            )
        except Exception:
            pass

    def _on_attack(
        self,
        payload,
    ):
        target_x = payload.get(
            'target_x',
            None,
        )
        target_y = payload.get(
            'target_y',
            None,
        )

        if target_x is None:
            facing = int(
                payload.get(
                    'facing',
                    self.player.facing,
                )
            ) or 1
        else:
            facing = (
                1
                if float(target_x) >= float(self.player.x)
                else -1
            )

        level = max(
            1,
            min(
                3,
                int(
                    payload.get(
                        "level",
                        1,
                    )
                ),
            ),
        )

        x, y = self._spawn_origin(
            facing
        )

        aim_target = (
            None
            if target_x is None or target_y is None
            else (
                float(target_x),
                float(target_y),
            )
        )

        if self.selected_magic == self.MAGIC_ELECTRIC:
            multiplier=self._level_multiplier(level)
            mana_cost=ELECTRICBALL_MANA_COST*multiplier
            if self.player.mana < mana_cost:
                self.events.emit('message', text=f'MP 不足，無法施放 {level} 級電球')
                return
            energy_units=ELECTRICBALL_ENERGY_UNITS*multiplier
            charge=ELECTRICBALL_CHARGE*multiplier
            self.player.mana=max(0.0,self.player.mana-mana_cost)
            self.ledger.cast_electric(mana_cost,energy_units)
            try:
                self.environment.energy.record("electric_in", energy_units)
            except Exception:
                pass
            electric_vx=electric_vy=None
            if aim_target is not None:
                electric_vx,electric_vy=self._ballistic_velocity_to_target(
                    x,y,aim_target[0],aim_target[1],ELECTRICBALL_SPEED,ELECTRICBALL_GRAVITY
                )
            self._spawn_electricball(
                x,y,facing,level=level,vx=electric_vx,vy=electric_vy,
                charge=charge,energy_units=energy_units,
            )
            self._emit_cast_audio(self.MAGIC_ELECTRIC, level, x, y)
            if self.action_messages_enabled:
                self.events.emit('message', text=f'{level} 級電球：電荷 {charge:.2f}')
            return

        if (
            self.selected_magic
            == self.MAGIC_ICE
        ):
            tile_count = (
                self._ice_tile_count(
                    level
                )
            )

            mana_cost = (
                ICEBALL_MANA_COST
                * tile_count
            )

            if (
                self.player.mana
                < mana_cost
            ):
                self.events.emit(
                    'message',
                    text=(
                        f'MP 不足，無法施放 '
                        f'{level} 級冰球'
                    ),
                )
                return

            created_units = (
                ICEBALL_MAGIC_WATER_UNITS
                * tile_count
            )

            created_internal = (
                created_units
                / WATER_MASS_UNITS_PER_TILE
            )

            self.player.mana = max(
                0.0,
                self.player.mana
                - mana_cost,
            )

            self.ledger.cast_ice(
                mana_cost,
                created_units,
            )

            ice_vx = None
            ice_vy = None
            if aim_target is not None:
                ice_vx, ice_vy = self._ballistic_velocity_to_target(
                    x, y,
                    aim_target[0],
                    aim_target[1],
                    ICEBALL_SPEED,
                    ICEBALL_GRAVITY,
                )

            self._spawn_iceball(
                x,
                y,
                facing,
                created_internal,
                level=level,
                vx=ice_vx,
                vy=ice_vy,
            )
            self._emit_cast_audio(self.MAGIC_ICE, level, x, y)

            if self.action_messages_enabled:
                self.events.emit(
                    'message',
                    text=(
                        f'{level} 級冰球：'
                        f'{tile_count} 格冰量'
                    ),
                )
            return

        multiplier = (
            self._level_multiplier(
                level
            )
        )

        if (
            self.selected_magic
            == self.MAGIC_WATER
        ):
            mana_cost = (
                WATERBALL_MANA_COST
                * multiplier
            )

            if (
                self.player.mana
                < mana_cost
            ):
                self.events.emit(
                    'message',
                    text=(
                        f'MP 不足，無法施放 '
                        f'{level} 級水球'
                    ),
                )
                return

            created_units = (
                WATERBALL_MAGIC_WATER_UNITS
                * multiplier
            )

            created_internal = (
                created_units
                / WATER_MASS_UNITS_PER_TILE
            )

            self.player.mana = max(
                0.0,
                self.player.mana
                - mana_cost,
            )

            self.ledger.cast_water(
                mana_cost,
                created_units,
            )

            water_vx = None
            water_vy = None
            if aim_target is not None:
                water_vx, water_vy = self._ballistic_velocity_to_target(
                    x, y,
                    aim_target[0],
                    aim_target[1],
                    WATERBALL_SPEED,
                    WATERBALL_GRAVITY,
                )

            self._spawn_waterball(
                x,
                y,
                facing,
                created_internal,
                level=level,
                vx=water_vx,
                vy=water_vy,
            )
            self._emit_cast_audio(self.MAGIC_WATER, level, x, y)

            if self.action_messages_enabled:
                self.events.emit(
                    'message',
                    text=(
                        f'{level} 級水球：'
                        f'{created_units:.0f} 單位水'
                    ),
                )
            return

        mana_cost = (
            FIREBALL_MANA_COST
            * multiplier
        )

        if (
            self.player.mana
            < mana_cost
        ):
            self.events.emit(
                'message',
                text=(
                    f'MP 不足，無法施放 '
                    f'{level} 級火球'
                ),
            )
            return

        energy_units = (
            FIREBALL_MAGIC_ENERGY_UNITS
            * multiplier
        )

        evap_units = (
            FIREBALL_EVAPORATE_WATER_UNITS
            * multiplier
        )

        self.player.mana = max(
            0.0,
            self.player.mana
            - mana_cost,
        )

        self.ledger.cast_fire(
            mana_cost,
            energy_units,
        )

        fire_vx = None
        fire_vy = None
        if aim_target is not None:
            fire_vx, fire_vy = self._ballistic_velocity_to_target(
                x, y,
                aim_target[0],
                aim_target[1],
                FIREBALL_SPEED,
                FIREBALL_GRAVITY,
            )

        self._spawn_fireball(
            x,
            y,
            facing,
            level=level,
            vx=fire_vx,
            vy=fire_vy,
            energy_units=energy_units,
            evap_capacity_units=evap_units,
        )
        self._emit_cast_audio(self.MAGIC_FIRE, level, x, y)

        if self.action_messages_enabled:
            self.events.emit(
                'message',
                text=(
                    f'{level} 級火球：'
                    f'{energy_units:.0f} 能量；'
                    f'最多蒸發 {evap_units:.0f} 單位水'
                ),
            )

    def _velocity_normal(
        self,
        vx,
        vy,
    ):
        length = max(
            1e-6,
            (
                vx
                * vx
                + vy
                * vy
            ) ** 0.5,
        )

        # Fragments spread back/outward from the collision direction.
        return (
            -vx
            / length,
            -vy
            / length,
        )

    def _three_tile_burst_targets(
        self,
        hit_tx,
        hit_ty,
        impact_x=None,
        impact_y=None,
        force_horizontal=False,
    ):
        count = max(
            1,
            int(
                MAGIC_LEVEL3_BURST_TILES
            ),
        )

        half = (
            count
            // 2
        )

        if force_horizontal:
            horizontal = True
        else:
            center_x = (
                hit_tx
                + 0.5
            ) * TILE_SIZE

            center_y = (
                hit_ty
                + 0.5
            ) * TILE_SIZE

            dx = (
                0.0
                if impact_x is None
                else impact_x
                - center_x
            )

            dy = (
                -1.0
                if impact_y is None
                else impact_y
                - center_y
            )

            # Top/bottom hit -> spread left/right.
            # Side-wall hit -> spread vertically.
            horizontal = (
                abs(
                    dy
                )
                >= abs(
                    dx
                )
            )

        targets = []

        for offset in range(
            -half,
            half + 1,
        ):
            if horizontal:
                tx = (
                    hit_tx
                    + offset
                )
                ty = hit_ty
            else:
                tx = hit_tx
                ty = (
                    hit_ty
                    + offset
                )

            if not (
                0 <= tx
                < self.world.width_tiles
                and 0 <= ty
                < self.world.height_tiles
            ):
                continue

            targets.append(
                (
                    tx,
                    ty,
                )
            )

        return targets

    def _vaporize_level3_ice_cluster(self, fb, hit_tx, hit_ty):
        """L3 fire directly vaporizes up to three connected ice cells.

        Lower fire ranks keep the explicit ICE -> WATER step. Level 3 is the
        high-energy exception requested by gameplay: up to three contiguous
        ice tiles are converted straight into atmospheric vapor. Actual
        ice_mass is used, so thin/mid ice never creates extra water.
        """
        start = (int(hit_tx), int(hit_ty))
        if self.world.get_tile(*start) != ICE:
            return 0, 0.0

        queue = deque([start])
        seen = {start}
        targets = []
        while queue and len(targets) < 3:
            tx, ty = queue.popleft()
            if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
                continue
            if self.world.get_tile(tx, ty) != ICE:
                continue
            targets.append((tx, ty))
            # Prefer horizontal continuation first; it reads naturally when
            # melting a frozen pond, then vertical neighbors.
            for nx, ny in ((tx - 1, ty), (tx + 1, ty), (tx, ty - 1), (tx, ty + 1)):
                key = (nx, ny)
                if key in seen:
                    continue
                seen.add(key)
                if 0 <= nx < self.world.width_tiles and 0 <= ny < self.world.height_tiles:
                    if self.world.get_tile(nx, ny) == ICE:
                        queue.append(key)

        total_internal = 0.0
        env = self.environment.state
        fallback_mass = max(0.0, float(tile_def(ICE).water_mass))
        for tx, ty in targets:
            key = (tx, ty)
            mass = max(0.0, float(env.ice_mass.get(key, fallback_mass)))
            if mass <= 1e-12:
                mass = fallback_mass
            total_internal += mass
            self.world.set_tile(tx, ty, AIR)
            env.ice_mass.pop(key, None)
            env.ice_melt_progress.pop(key, None)
            env.water_freeze_progress.pop(key, None)
            env.cold_surface_water.pop(key, None)
            env.snow_cover.pop(key, None)
            env.add_vapor(tx // CHUNK_SIZE, ty // CHUNK_SIZE, mass)

        units = self.internal_water_to_units(total_internal)
        if units > 0.0:
            self.ledger.record_water_to_vapor(units)
            energy = max(0.0, float(getattr(fb, "energy_units", 0.0)))
            self.ledger.record_fire_evaporation(units, energy)
            fb.energy_units = 0.0
            fb.evap_capacity_units = 0.0
            self._pulse_fireball_lift(fb.x, fb.y, 3, impact_scale=1.85)
        self.events.emit(
            "message",
            text=f"3 級火球：直接汽化 {len(targets)} 格冰（{units:.1f} 水量）",
        )
        fb.active = False
        return len(targets), total_internal

    def _explode_level3_fireball(
        self,
        fb,
        hit_tx,
        hit_ty,
        impact_x=None,
        impact_y=None,
        force_horizontal=False,
    ):
        targets = (
            self._three_tile_burst_targets(
                hit_tx,
                hit_ty,
                impact_x,
                impact_y,
                force_horizontal=force_horizontal,
            )
        )

        if not targets:
            self._release_fireball_energy(
                fb,
                hit_tx,
                hit_ty,
            )
            fb.active = False
            return

        energy_each = (
            max(
                0.0,
                fb.energy_units,
            )
            / len(
                targets
            )
        )

        evap_each = (
            max(
                0.0,
                fb.evap_capacity_units,
            )
            / len(
                targets
            )
        )

        for tx, ty in targets:
            self._id_seq += 1

            # This is an impact packet, not another flying projectile.
            # Each target receives one SMALL flame stack increment while the total
            # L3 spell energy remains conserved across exactly three tiles.
            impact_fb = Fireball(
                entity_id=(
                    f'fireburst_{self._id_seq}'
                ),
                x=(
                    tx
                    + 0.5
                )
                * TILE_SIZE,
                y=(
                    ty
                    + 0.5
                )
                * TILE_SIZE,
                vx=0.0,
                vy=0.0,
                radius=MAGIC_RADIUS_LEVEL1,
                life=0.0,
                power=FIREBALL_POWER,
                temperature_c=fb.temperature_c,
                energy_units=energy_each,
                evap_capacity_units=evap_each,
                level=1,
                fragment=True,
                active=False,
            )

            self._ignite_hit_tile(
                tx,
                ty,
                impact_fb,
            )

        # V0.4.21: the level-3 explosion can instantly scorch ONLY the
        # actual impact tile to ash. The two neighboring burst tiles remain
        # normal small-fire ignition targets.
        if L3_FIREBALL_CENTER_ASH:
            self.environment.fire.scorch_to_ash(
                hit_tx,
                hit_ty,
                source="fireball_l3_center",
            )

        fb.energy_units = 0.0
        fb.evap_capacity_units = 0.0
        fb.active = False

        self._pulse_fireball_lift(
            fb.x,
            fb.y,
            3,
            impact_scale=1.55,
        )

        self.events.emit(
            "message",
            text=(
                "3 級火球爆炸："
                "限制在 3 格範圍，"
                "落點灰燼 + 鄰格小火"
            ),
        )

    def _split_level3_waterball(
        self,
        wb,
        hit_tx,
        hit_ty,
        impact_x=None,
        impact_y=None,
        force_horizontal=False,
    ):
        targets = (
            self._three_tile_burst_targets(
                hit_tx,
                hit_ty,
                impact_x,
                impact_y,
                force_horizontal=force_horizontal,
            )
        )

        if not targets:
            self._return_waterball_to_vapor(
                wb
            )
            wb.active = False
            return

        mass_each = (
            max(
                0.0,
                wb.water_mass,
            )
            / len(
                targets
            )
        )

        center_x = (
            hit_tx
            + 0.5
        ) * TILE_SIZE

        center_y = (
            hit_ty
            + 0.5
        ) * TILE_SIZE

        if impact_x is None:
            normal_x = 0.0
            normal_y = -1.0
        else:
            dx = (
                impact_x
                - center_x
            )
            dy = (
                impact_y
                - center_y
            )

            if abs(
                dx
            ) > abs(
                dy
            ):
                normal_x = (
                    1.0
                    if dx > 0.0
                    else -1.0
                )
                normal_y = 0.0
            else:
                normal_x = 0.0
                normal_y = (
                    1.0
                    if dy > 0.0
                    else -1.0
                )

        for tx, ty in targets:
            self._extinguish_fire(
                tx,
                ty,
            )

            if tile_def(
                self.world.get_tile(
                    tx,
                    ty,
                )
            ).solid:
                shifted_impact_x = (
                    (
                        tx
                        + 0.5
                    )
                    * TILE_SIZE
                    + normal_x
                    * (
                        TILE_SIZE
                        * 0.52
                    )
                )

                shifted_impact_y = (
                    (
                        ty
                        + 0.5
                    )
                    * TILE_SIZE
                    + normal_y
                    * (
                        TILE_SIZE
                        * 0.52
                    )
                )

                self._water_impact_solid(
                    tx,
                    ty,
                    shifted_impact_x,
                    shifted_impact_y,
                    mass_each,
                    wb.temperature_c,
                )
            elif (
                self.environment.state.water_amount(
                    tx,
                    ty,
                )
                > 0.02
            ):
                self._water_impact_water(
                    tx,
                    ty,
                    mass_each,
                    wb.temperature_c,
                )
            else:
                landing_temp = self._condition_waterball_landing_temp(
                    tx, ty, wb.temperature_c
                )
                self.environment.water.deposit(
                    tx,
                    ty,
                    mass_each,
                    temperature_c=landing_temp,
                )

                self.ledger.record_water_to_free(
                    self.internal_water_to_units(
                        mass_each
                    )
                )

        wb.water_mass = 0.0
        wb.active = False

        self.events.emit(
            "message",
            text=(
                "3 級水球散開："
                "限制在 3 格範圍"
            ),
        )

    def _ice_line_targets(
        self,
        hit_tx,
        hit_ty,
        impact_x,
        impact_y,
        count,
    ):
        primary = (
            self._ice_place_target(
                hit_tx,
                hit_ty,
                impact_x,
                impact_y,
            )
        )

        if primary is None:
            return []

        px, py = primary

        center_x = (
            hit_tx
            + 0.5
        ) * TILE_SIZE

        center_y = (
            hit_ty
            + 0.5
        ) * TILE_SIZE

        dx = (
            impact_x
            - center_x
        )
        dy = (
            impact_y
            - center_y
        )

        # Hit top/bottom surface -> horizontal ice row.
        # Hit side wall -> vertical ice column.
        horizontal = (
            abs(
                dy
            )
            >= abs(
                dx
            )
        )

        half = (
            count
            // 2
        )

        targets = []

        for offset in range(
            -half,
            half + 1,
        ):
            if horizontal:
                tx = (
                    px
                    + offset
                )
                ty = py
            else:
                tx = px
                ty = (
                    py
                    + offset
                )

            if not (
                0 <= tx
                < self.world.width_tiles
                and 0 <= ty
                < self.world.height_tiles
            ):
                continue

            targets.append(
                (
                    tx,
                    ty,
                )
            )

        return targets

    # --------------------------------------------------------
    # Collision helpers
    # --------------------------------------------------------

    def _cell_from_world(self, x, y):
        return int(x // TILE_SIZE), int(y // TILE_SIZE)

    def _creature_hit_at(self, x, y, radius):
        """Return the nearest live targetable creature touched by a projectile."""
        if self.scene is None:
            return None
        candidates = []
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only:
                continue
            if not entity.active or float(getattr(entity, "hp", 0.0)) <= 0.0:
                continue
            rect = entity.bbox()
            if self._circle_aabb_overlap(float(x), float(y), float(radius), rect):
                cx = float(entity.x)
                cy = float(entity.y) - float(entity.height()) * 0.5
                d2 = (float(x)-cx)**2 + (float(y)-cy)**2
                candidates.append((d2, entity))
        if not candidates:
            return None
        candidates.sort(key=lambda row: row[0])
        return candidates[0][1]

    def _swept_creature_hit(self, x0, y0, x1, y1, radius):
        """Continuous creature hit test along one projectile sub-step."""
        dx = float(x1) - float(x0)
        dy = float(y1) - float(y0)
        dist = math.hypot(dx, dy)
        step = max(1.5, min(4.0, float(radius) * 0.45))
        count = max(1, int(math.ceil(dist / step)))
        for i in range(1, count + 1):
            f = i / count
            x = float(x0) + dx * f
            y = float(y0) + dy * f
            creature = self._creature_hit_at(x, y, radius)
            if creature is not None:
                return creature, x, y
        return None

    @staticmethod
    def _level_value(values, level):
        idx = max(0, min(2, int(level) - 1))
        return float(values[idx])

    def _damage_creature_with_magic(self, creature, element, level, vx=0.0, vy=0.0):
        """Apply direct spell damage and short element-specific combat states."""
        if creature is None or getattr(creature,"background_only",False) or not creature.active or creature.hp <= 0.0:
            return 0.0
        tables = {
            "fire": MAGIC_CREATURE_DAMAGE_FIRE,
            "water": MAGIC_CREATURE_DAMAGE_WATER,
            "ice": MAGIC_CREATURE_DAMAGE_ICE,
            "electric": MAGIC_CREATURE_DAMAGE_ELECTRIC,
        }
        # FIX131 five-element matchup:
        # 水剋火；火剋土與冰；土剋電；電剋水。
        # 相剋 2.0x、反向 0.5x、無關係 1.0x。
        damage = self._level_value(tables.get(element, MAGIC_CREATURE_DAMAGE_FIRE), level)
        target_element = str(getattr(creature, "element_type", "earth") or "earth").lower()
        mult = float(elemental_multiplier(str(element), target_element))
        damage *= mult
        matchup = "advantage" if mult > 1.0 else ("resist" if mult < 1.0 else "neutral")
        try:
            creature.provoke_by_player()
        except Exception:
            creature.provoked_by_player = True
            creature.player_detected = True
        creature.hp = max(0.0, float(creature.hp) - damage)
        creature.hurt_timer = max(float(getattr(creature, "hurt_timer", 0.0)), 0.18)

        # Knockback follows projectile travel, not the aiming reticle after hit.
        direction = 1.0 if float(vx) >= 0.0 else -1.0
        creature.vx = direction * float(MAGIC_CREATURE_KNOCKBACK)

        if element == "ice":
            creature.slow_timer = max(
                float(getattr(creature, "slow_timer", 0.0)),
                self._level_value(MAGIC_CREATURE_ICE_SLOW_SECONDS, level),
            )
        elif element == "electric":
            creature.stun_timer = max(
                float(getattr(creature, "stun_timer", 0.0)),
                self._level_value(MAGIC_CREATURE_ELECTRIC_STUN_SECONDS, level),
            )

        element_name = {
            "fire": "火球", "water": "水球", "ice": "冰球", "electric": "電球"
        }.get(element, "魔法")
        matchup_text = " 剋制!" if matchup == "advantage" else (" 抵抗" if matchup == "resist" else "")
        stun_text = " 麻痺3秒" if element == "electric" else ""
        self.events.emit(
            "message",
            text=(
                f"{element_name}L{max(1,min(3,int(level)))}命中 {creature.name}：-{damage:.0f}"
                f"{matchup_text}{stun_text} HP {creature.hp:.0f}/{creature.max_hp:.0f}"
            ),
        )
        return damage

    def _splash_water_at_creature(self, creature, water_mass, temperature_c):
        """Keep water-magic matter in the world after an actor impact."""
        mass = max(0.0, float(water_mass))
        if mass <= 0.0:
            return
        tx = int(float(creature.x) // TILE_SIZE)
        ty = int((float(creature.y) - 2.0) // TILE_SIZE)
        target = None
        for ox, oy in ((0,0), (-1,0), (1,0), (0,-1)):
            cx, cy = tx+ox, ty+oy
            if not (0 <= cx < self.world.width_tiles and 0 <= cy < self.world.height_tiles):
                continue
            if not tile_def(self.world.get_tile(cx, cy)).solid:
                target = (cx, cy)
                break
        if target is None:
            # No open cell: return to atmosphere rather than deleting matter.
            self.environment.state.add_vapor(tx//CHUNK_SIZE, ty//CHUNK_SIZE, mass)
            self.ledger.record_water_to_vapor(self.internal_water_to_units(mass))
            return
        self.environment.water.deposit(
            target[0], target[1], mass, temperature_c=temperature_c
        )
        self.ledger.record_water_to_free(self.internal_water_to_units(mass))

    def _solid_hit_cell(self, x, y, radius):
        samples = (
            (x, y + radius),
            (x + radius, y),
            (x - radius, y),
            (x, y - radius),
            (x + radius * 0.70, y + radius * 0.70),
            (x - radius * 0.70, y + radius * 0.70),
            (x + radius * 0.70, y - radius * 0.70),
            (x - radius * 0.70, y - radius * 0.70),
        )

        for sx, sy in samples:
            tx, ty = self._cell_from_world(sx, sy)
            if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
                continue
            if tile_def(self.world.get_tile(tx, ty)).solid:
                return tx, ty
        return None

    @staticmethod
    def _circle_aabb_overlap(
        x,
        y,
        radius,
        rect,
    ):
        x1, y1, x2, y2 = rect
        nearest_x = max(
            x1,
            min(
                x2,
                x,
            ),
        )
        nearest_y = max(
            y1,
            min(
                y2,
                y,
            ),
        )
        dx = x - nearest_x
        dy = y - nearest_y
        return (
            dx * dx
            + dy * dy
            <= radius * radius
        )

    def _plant_collision_rect(
        self,
        tx,
        ty,
        plant,
    ):
        if (
            plant is None
            or not getattr(
                plant,
                "alive",
                False,
            )
            or int(
                getattr(
                    plant,
                    "stage",
                    0,
                )
            ) <= 0
        ):
            return None

        species = str(
            getattr(
                plant,
                "species",
                "",
            )
        )
        stage = max(
            1,
            int(
                getattr(
                    plant,
                    "stage",
                    1,
                )
            ),
        )
        center_x = (
            tx * TILE_SIZE
            + TILE_SIZE * 0.5
        )
        try:
            ground_y = float(self.world.plant_anchor_y(tx, ty, species))
        except Exception:
            ground_y = float(ty * TILE_SIZE)

        if species in ("map_grass", "map_reed", "map_fern", "map_cactus"):
            level = max(
                1,
                min(
                    3,
                    stage,
                ),
            )
            width = {
                1: 7.0,
                2: 10.0,
                3: 13.0,
            }[level]
            height = {
                1: 11.0,
                2: 21.0,
                3: 32.0,
            }[level]
            return (
                center_x - width * 0.5,
                ground_y - height,
                center_x + width * 0.5,
                ground_y,
            )

        if species in ("map_tree", "map_jungle_tree", "map_pine"):
            level = max(
                1,
                min(
                    3,
                    stage,
                ),
            )
            trunk_h = {
                1: 28.0,
                2: 46.0,
                3: 68.0,
            }[level]
            canopy_w = {
                1: 22.0,
                2: 34.0,
                3: 48.0,
            }[level]
            canopy_h = {
                1: 15.0,
                2: 22.0,
                3: 30.0,
            }[level]
            top = (
                ground_y
                - trunk_h
                - canopy_h * 0.20
            )
            return (
                center_x - canopy_w * 0.5,
                top,
                center_x + canopy_w * 0.5,
                ground_y,
            )

        if species == "succession":
            if stage <= 1:
                width = 7.0
                height = 12.0
            elif stage == 2:
                width = 14.0
                height = 24.0
            else:
                width = 40.0
                height = 54.0

            return (
                center_x - width * 0.5,
                ground_y - height,
                center_x + width * 0.5,
                ground_y,
            )

        # Generic vegetation fallback.
        return (
            center_x - 6.0,
            ground_y - 16.0,
            center_x + 6.0,
            ground_y,
        )

    def _plant_hit_cell(
        self,
        x,
        y,
        radius,
    ):
        center_tx, center_ty = (
            self._cell_from_world(
                x,
                y,
            )
        )

        # Trees are keyed by their ground tile but extend upward for nearly
        # two tiles, so inspect ground rows below the projectile as well as
        # neighboring columns.
        for base_ty in range(
            center_ty - 1,
            center_ty + 4,
        ):
            for base_tx in range(
                center_tx - 1,
                center_tx + 2,
            ):
                plant = self.environment.state.plants.get(
                    (
                        base_tx,
                        base_ty,
                    )
                )
                if plant is None:
                    continue

                rect = self._plant_collision_rect(
                    base_tx,
                    base_ty,
                    plant,
                )
                if (
                    rect is not None
                    and self._circle_aabb_overlap(
                        x,
                        y,
                        radius,
                        rect,
                    )
                ):
                    return (
                        base_tx,
                        base_ty,
                    )

        return None

    def _swept_fireball_hit(
        self,
        x0,
        y0,
        x1,
        y1,
        radius,
    ):
        """Return the first world interaction intersected by the trajectory.

        Checking the swept segment rather than only the projectile's final
        frame position prevents fireballs from tunneling through thin grass,
        tree trunks/canopies, and tile edges.
        """
        dx = x1 - x0
        dy = y1 - y0
        distance = (
            dx * dx
            + dy * dy
        ) ** 0.5

        max_step = max(
            1.5,
            min(
                4.0,
                radius * 0.45,
            ),
        )
        count = max(
            1,
            int(
                math.ceil(
                    distance
                    / max_step
                )
            ),
        )

        for i in range(
            1,
            count + 1,
        ):
            f = i / count
            x = x0 + dx * f
            y = y0 + dy * f

            tx, ty = self._cell_from_world(
                x,
                y,
            )
            if not (
                0 <= tx < self.world.width_tiles
                and 0 <= ty < self.world.height_tiles
            ):
                continue

            if (
                self.environment.state.water_amount(
                    tx,
                    ty,
                )
                > 0.02
            ):
                return (
                    "water",
                    tx,
                    ty,
                    x,
                    y,
                )

            plant_hit = self._plant_hit_cell(
                x,
                y,
                radius,
            )
            if plant_hit is not None:
                return (
                    "plant",
                    plant_hit[0],
                    plant_hit[1],
                    x,
                    y,
                )

            solid_hit = self._solid_hit_cell(
                x,
                y,
                radius,
            )
            if solid_hit is not None:
                return (
                    "solid",
                    solid_hit[0],
                    solid_hit[1],
                    x,
                    y,
                )

        return None

    # --------------------------------------------------------
    # Fireball behavior
    # --------------------------------------------------------

    def _ignite_hit_tile(
        self,
        tx,
        ty,
        fb,
    ):
        tile_id = self.world.get_tile(
            tx,
            ty,
        )

        evaporated_units = 0.0

        if tile_id in (
            DIRT,
            GRASS_DIRT,
        ):
            evaporated_units = (
                self._evaporate_soil_with_fireball(
                    tx,
                    ty,
                    fb,
                )
            )

        # Fireball's remaining energy heats the target after phase-change work.
        self.environment.state.set_temperature(
            tx,
            ty,
            max(
                self.environment.state.temperature_at(
                    tx,
                    ty,
                    20.0,
                ),
                FIREBALL_TEMPERATURE_C
                * 0.18,
            ),
        )

        ok = self.environment.fire.ignite(
            tx,
            ty,
            penetration_left=FIREBALL_BURN_PENETRATION,
            source="fireball_basic",
        )

        self._release_fireball_energy(
            fb,
            tx,
            ty,
        )

        if tile_id in (
            DIRT,
            GRASS_DIRT,
        ):
            soil = self.environment.state.soil.get(
                (
                    tx,
                    ty,
                )
            )

            remaining_units = (
                0.0
                if soil is None
                else self.internal_water_to_units(
                    soil.moisture
                )
            )

            if ok:
                self.events.emit(
                    'message',
                    text=(
                        f'火球蒸發 {evaporated_units:.1f} 單位水；'
                        f'土壤剩 {remaining_units:.1f}，已點燃'
                    ),
                )
                return True

            same_water = (
                self.environment.state.water_amount(
                    tx,
                    ty,
                )
            )

            above_water = (
                self.environment.state.water_amount(
                    tx,
                    max(
                        0,
                        ty - 1,
                    ),
                )
            )

            if (
                same_water
                + above_water
                > 0.08
            ):
                self.events.emit(
                    'message',
                    text=(
                        f'火球蒸發 {evaporated_units:.1f} 單位；'
                        '仍有表面積水，無法點燃'
                    ),
                )
            else:
                self.events.emit(
                    'message',
                    text=(
                        f'火球蒸發 {evaporated_units:.1f} 單位水；'
                        f'土壤仍有 {remaining_units:.1f} 單位，尚未乾到可燃'
                    ),
                )

            return False

        if ok:
            self.events.emit(
                'message',
                text='火球命中並點燃目標',
            )
            return True

        self.events.emit(
            'message',
            text='火球命中，但目標不可燃',
        )
        return False

    def _absorb_water_into_soil_network(
        self,
        tx,
        ty,
        incoming,
        water_temperature_c,
    ):
        """Deposit a finite water packet into connected porous soil.

        Priority:
            1. impacted soil
            2. deeper soil layers (gravity)
            3. neighboring soil columns

        Every cell has a hard saturation limit. Nothing is deleted: any mass
        that cannot fit returns as leftover surface water.
        """
        remaining = max(
            0.0,
            float(
                incoming
            ),
        )

        absorbed_total = 0.0

        candidates = []

        max_depth = 6
        lateral_radius = 2

        # Gravity-first:
        #   impacted column -> deeper layers
        # then neighboring columns are allowed to absorb.
        column_offsets = [
            0,
        ]

        for radius in range(
            1,
            lateral_radius + 1,
        ):
            column_offsets.extend(
                (
                    -radius,
                    radius,
                )
            )

        for dx in column_offsets:
            cx = (
                tx
                + dx
            )

            if not (
                0 <= cx
                < self.world.width_tiles
            ):
                continue

            for depth in range(
                0,
                max_depth,
            ):
                cy = (
                    ty
                    + depth
                )

                if cy >= self.world.height_tiles:
                    break

                td = tile_def(
                    self.world.get_tile(
                        cx,
                        cy,
                    )
                )

                if not (
                    td.water_absorption
                    >= 0.20
                ):
                    continue

                candidates.append(
                    (
                        cx,
                        cy,
                        td,
                    )
                )

        visited = set()

        for cx, cy, td in candidates:
            if remaining <= 1e-12:
                break

            key = (
                cx,
                cy,
            )

            if key in visited:
                continue

            visited.add(
                key
            )

            soil = (
                self.environment.state.soil.setdefault(
                    key,
                    SoilCell(
                        moisture=0.0,
                        fertility=max(
                            0.20,
                            td.fertility,
                        ),
                    ),
                )
            )

            capacity = max(
                0.0,
                SOIL_SATURATION
                - soil.moisture,
            )

            if capacity <= 1e-12:
                continue

            absorbed = min(
                remaining,
                capacity,
            )

            soil.moisture = min(
                SOIL_SATURATION,
                soil.moisture
                + absorbed,
            )

            remaining -= absorbed
            absorbed_total += absorbed

            # Incoming water exchanges heat with every soil cell it enters.
            local_temp = (
                self.environment.state.temperature_at(
                    cx,
                    cy,
                    20.0,
                )
            )

            self.environment.state.set_temperature(
                cx,
                cy,
                local_temp
                + (
                    water_temperature_c
                    - local_temp
                )
                * min(
                    0.65,
                    absorbed
                    * 0.55,
                ),
            )

        return (
            absorbed_total,
            remaining,
        )

    def _condition_waterball_landing_temp(self, tx, ty, temperature_c):
        try:
            return float(self.environment.thermal.condition_waterball_landing_temperature(
                int(tx), int(ty), float(temperature_c)
            ))
        except Exception:
            return float(temperature_c)

    # --------------------------------------------------------
    # Waterball behavior
    # --------------------------------------------------------

    def _extinguish_fire(self, tx, ty):
        """Direct hit removes fire; one-tile splash strongly weakens nearby fire."""
        extinguished = False

        for oy in range(-WATERBALL_SPLASH_RADIUS, WATERBALL_SPLASH_RADIUS + 1):
            for ox in range(-WATERBALL_SPLASH_RADIUS, WATERBALL_SPLASH_RADIUS + 1):
                if abs(ox) + abs(oy) > WATERBALL_SPLASH_RADIUS:
                    continue

                key = (tx + ox, ty + oy)
                fire = self.environment.state.fire.get(key)
                if fire is None:
                    continue

                if ox == 0 and oy == 0:
                    self.environment.state.fire.pop(key, None)
                else:
                    fire.intensity -= 0.70
                    fire.temperature -= 0.70
                    fire.temperature_c = max(
                        self.environment.state.temperature_at(
                            key[0],
                            key[1],
                            20.0,
                        ),
                        getattr(
                            fire,
                            "temperature_c",
                            620.0,
                        )
                        - 260.0,
                    )
                    if fire.intensity <= 0.08:
                        self.environment.state.fire.pop(key, None)

                extinguished = True

        return extinguished

    def _surface_water_cell(self, tx, ty, impact_x, impact_y):
        """Choose the adjacent air cell on the side actually struck.

        For the common ground impact, this returns the cell immediately above
        the solid tile, so the water can then flow/settle using WaterSystem.
        """
        center_x = (tx + 0.5) * TILE_SIZE
        center_y = (ty + 0.5) * TILE_SIZE
        dx = impact_x - center_x
        dy = impact_y - center_y

        candidates = []

        if abs(dy) >= abs(dx):
            candidates.append((tx, ty - 1 if dy < 0 else ty + 1))
            candidates.append((tx - 1 if dx < 0 else tx + 1, ty))
        else:
            candidates.append((tx - 1 if dx < 0 else tx + 1, ty))
            candidates.append((tx, ty - 1))

        # Ground-friendly fallback order.
        candidates.extend(((tx, ty - 1), (tx - 1, ty), (tx + 1, ty)))

        for cx, cy in candidates:
            if not (0 <= cx < self.world.width_tiles and 0 <= cy < self.world.height_tiles):
                continue
            if not tile_def(self.world.get_tile(cx, cy)).solid:
                return cx, cy

        return None

    def _water_impact_solid(
        self,
        tx,
        ty,
        impact_x,
        impact_y,
        water_mass,
        water_temperature_c=WATERBALL_TEMPERATURE_C,
    ):
        tile_id = self.world.get_tile(
            tx,
            ty,
        )

        td = tile_def(
            tile_id
        )

        water_temperature_c = self._condition_waterball_landing_temp(
            tx, ty, water_temperature_c
        )
        cold_surface_water = water_temperature_c < 0.0

        extinguished = self._extinguish_fire(
            tx,
            ty,
        )

        if tile_id == ASH:
            # Direct water-magic contact starts ash hydration immediately.
            key = (
                tx,
                ty,
            )

            self.environment.state.ash_wet_time[
                key
            ] = max(
                ASH_REHYDRATE_CONTACT_EPSILON,
                float(
                    self.environment.state.ash_wet_time.get(
                        key,
                        0.0,
                    )
                ),
            )

        # Soil/grass: absorb only the REMAINING moisture capacity.
        # A 100% saturated soil tile cannot delete any more water.
        # Excess water becomes a normal water cell above the ground and is
        # then free to spread to neighboring soil / collect in a depression.
        if td.water_absorption >= 0.20 and not cold_surface_water:
            incoming = max(
                0.0,
                float(
                    water_mass
                ),
            )

            absorbed, leftover = (
                self._absorb_water_into_soil_network(
                    tx,
                    ty,
                    incoming,
                    water_temperature_c,
                )
            )

            self.ledger.record_water_to_soil(
                self.internal_water_to_units(
                    absorbed
                )
            )

            if leftover > 0.001:
                target = self._surface_water_cell(
                    tx,
                    ty,
                    impact_x,
                    impact_y,
                )

                if target is not None:
                    wx, wy = target

                    self.environment.water.deposit(
                        wx,
                        wy,
                        leftover,
                        temperature_c=water_temperature_c,
                    )

                    self.ledger.record_water_to_free(
                        self.internal_water_to_units(
                            leftover
                        )
                    )

            absorbed_units = (
                self.internal_water_to_units(
                    absorbed
                )
            )

            leftover_units = (
                self.internal_water_to_units(
                    leftover
                )
            )

            if leftover > 0.001:
                text = (
                    f'土壤網路吸收 {absorbed_units:.1f} 單位；'
                    f'剩餘 {leftover_units:.1f} 單位形成積水'
                )
            else:
                text = (
                    f'水球 {WATERBALL_MAGIC_WATER_UNITS:.0f} 單位'
                    f'全部進入土壤/下層/鄰土'
                )

            if extinguished:
                text += '，並熄滅火焰'

            self.events.emit(
                'message',
                text=text,
            )
            return

        # Stone / non-absorbent material: water remains in the world and is
        # handed to WaterSystem so a pit can accumulate it naturally.
        target = self._surface_water_cell(tx, ty, impact_x, impact_y)
        if target is not None:
            wx, wy = target
            self.environment.water.deposit(
                wx,
                wy,
                water_mass,
                temperature_c=water_temperature_c,
            )

            self.ledger.record_water_to_free(
                self.internal_water_to_units(
                    water_mass
                )
            )

            self.events.emit(
                'message',
                text='水球落地形成積水' + ('，並熄滅火焰' if extinguished else ''),
            )
        elif extinguished:
            self.events.emit('message', text='水球熄滅火焰')

    def _water_impact_water(
        self,
        tx,
        ty,
        water_mass,
        water_temperature_c=WATERBALL_TEMPERATURE_C,
    ):
        water_temperature_c = self._condition_waterball_landing_temp(
            tx, ty, water_temperature_c
        )
        self._extinguish_fire(tx, ty)
        self.environment.water.deposit(
            tx,
            ty,
            water_mass,
            temperature_c=water_temperature_c,
        )

        self.ledger.record_water_to_free(
            self.internal_water_to_units(
                water_mass
            )
        )

        self.events.emit(
            'message',
            text=f'水球融入積水 ({water_mass:.2f})',
        )


    # --------------------------------------------------------
    # Iceball behavior
    # --------------------------------------------------------

    def _return_iceball_to_vapor(
        self,
        ib,
    ):
        mass = max(
            0.0,
            float(
                getattr(
                    ib,
                    "water_mass",
                    0.0,
                )
            ),
        )

        if mass <= 0.0:
            return

        chunk_px = (
            CHUNK_SIZE
            * TILE_SIZE
        )

        max_cx = max(
            0,
            (
                self.world.width_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        max_cy = max(
            0,
            (
                self.world.height_tiles
                - 1
            )
            // CHUNK_SIZE,
        )

        cx = max(
            0,
            min(
                max_cx,
                int(
                    max(
                        0.0,
                        ib.x,
                    )
                    // chunk_px
                ),
            ),
        )

        cy = max(
            0,
            min(
                max_cy,
                int(
                    max(
                        0.0,
                        ib.y,
                    )
                    // chunk_px
                ),
            ),
        )

        self.environment.state.add_vapor(
            cx,
            cy,
            mass,
        )

        self.ledger.record_water_to_vapor(
            self.internal_water_to_units(
                mass
            )
        )

        ib.water_mass = 0.0

    def _ice_place_target(
        self,
        hit_tx,
        hit_ty,
        impact_x,
        impact_y,
    ):
        # Existing water cell can freeze in-place.
        center_tx, center_ty = self._cell_from_world(
            impact_x,
            impact_y,
        )

        if (
            0 <= center_tx
            < self.world.width_tiles
            and 0 <= center_ty
            < self.world.height_tiles
            and self.world.get_tile(
                center_tx,
                center_ty,
            )
            == AIR
        ):
            return (
                center_tx,
                center_ty,
            )

        # Otherwise place the new ice on the struck face. This also allows
        # repeated iceballs to build ice on existing ICE tiles.
        return self._surface_water_cell(
            hit_tx,
            hit_ty,
            impact_x,
            impact_y,
        )

    def _water_ice_chain_limit(self, level):
        return {
            1: ICE_WATER_CHAIN_LEVEL1_TILES,
            2: ICE_WATER_CHAIN_LEVEL2_TILES,
            3: ICE_WATER_CHAIN_LEVEL3_TILES,
        }.get(int(level), ICE_WATER_CHAIN_LEVEL1_TILES)

    def _tile_overlaps_player(self, tx, ty):
        """Do not materialize a solid ICE tile inside the player's body.

        The old pond-wide chain could turn the player's current water cell
        solid and the collision solver then had no valid local position, which
        looked like the character was suddenly launched/ejected.
        """
        try:
            px1, py1, px2, py2 = self.player.bbox()
        except Exception:
            return False
        x1 = float(tx) * TILE_SIZE
        y1 = float(ty) * TILE_SIZE
        x2 = x1 + TILE_SIZE
        y2 = y1 + TILE_SIZE
        eps = 0.5
        return (
            px2 > x1 + eps
            and px1 < x2 - eps
            and py2 > y1 + eps
            and py1 < y2 - eps
        )

    def _deepest_stackable_water_cell(self, tx, surface_y):
        """Return the deepest contiguous water cell below a visible surface.

        Repeated iceball casts therefore build an ice stack from the bottom of
        the water column upward, which better matches the requested "freeze ->
        buoyant stack rises toward the surface" feel than instantly placing all
        new ice only at the top skin.
        """
        tx = int(tx)
        surface_y = int(surface_y)
        if not (0 <= tx < self.world.width_tiles and 0 <= surface_y < self.world.height_tiles):
            return None
        if self.environment.state.water_amount(tx, surface_y) < ICE_WATER_CHAIN_MIN_AMOUNT:
            return None
        deepest = surface_y
        y = surface_y
        while y + 1 < self.world.height_tiles and self.world.get_tile(tx, y + 1) == AIR:
            next_amount = float(self.environment.state.water_amount(tx, y + 1))
            if next_amount < ICE_WATER_CHAIN_MIN_AMOUNT:
                break
            deepest = y + 1
            y += 1
        return (tx, deepest)

    def _surface_water_chain_targets(self, hit_tx, hit_ty, level):
        """Contiguous surface-water cells inside a strict local width.

        L1/L2 use widths 1/3. L3 uses width 7 on water only.  The scan is
        centered on the struck water column, never crosses a dry gap, never
        exceeds +/-3 cells for L3, and never walks an entire pond graph.
        """
        hit_tx = int(hit_tx)
        hit_ty = int(hit_ty)
        surface = self._water_surface_cell(hit_tx, hit_ty)
        if surface is None:
            return []
        cx, sy = surface
        width = max(1, int(self._water_ice_chain_limit(level)))
        half = width // 2

        def valid_water(tx):
            if not (0 <= tx < self.world.width_tiles):
                return False
            return (
                self.world.get_tile(tx, sy) == AIR
                and self.environment.state.water_amount(tx, sy)
                    >= ICE_WATER_CHAIN_MIN_AMOUNT
            )

        if not valid_water(cx):
            return []

        # First discover how far the same continuous surface extends on each
        # side, but never outside this spell's local width.
        left_reach = 0
        for d in range(1, half + 1):
            if not valid_water(cx - d):
                break
            left_reach = d

        right_reach = 0
        for d in range(1, half + 1):
            if not valid_water(cx + d):
                break
            right_reach = d

        targets = []
        for offset in range(-left_reach, right_reach + 1):
            tx = cx + offset
            target = self._deepest_stackable_water_cell(tx, sy)
            if target is None:
                continue
            ttx, tty = target
            # FIX36: freezing is allowed to complete even when the player is
            # standing in this water cell. GameApp's solid-formation resolver
            # moves an embedded player to the nearest safe position immediately
            # after the phase-change step.
            targets.append((ttx, tty))

        # Freeze deepest cells first so successive impacts visibly stack
        # upward through the same water body.
        targets.sort(key=lambda cell: (-cell[1], abs(cell[0] - cx)))
        return targets[:width]

    def _water_entry_below_ice_or_surface(self, tx, start_y):
        """Find the first liquid cell in a column under a surface/ice hit.

        Iceballs that clip the side of an existing floating ice stack must not
        create a new solid block sideways. We look through the local surface
        ice/air gap for the actual water column and freeze there instead.
        """
        tx = int(tx)
        if not (0 <= tx < self.world.width_tiles):
            return None
        y = max(0, min(self.world.height_tiles - 1, int(start_y)))
        gap = 0
        while y < self.world.height_tiles and gap <= 4:
            tile_id = self.world.get_tile(tx, y)
            amount = float(self.environment.state.water_amount(tx, y))
            if tile_id == AIR and amount >= ICE_WATER_CHAIN_MIN_AMOUNT:
                return (tx, y)
            if tile_id == ICE:
                y += 1
                continue
            if tile_id == AIR and amount < ICE_WATER_CHAIN_MIN_AMOUNT:
                gap += 1
                y += 1
                continue
            break
        return None

    def _ice_hit_water_chain_targets(self, hit_tx, hit_ty, impact_x, impact_y, level):
        """Redirect an iceball that struck existing floating ice into water.

        Side contact prefers the water column on the struck side, so a second
        adjacent column begins underwater and then rises instead of creating a
        horizontal ice tile attached to the old stack.
        """
        hit_tx = int(hit_tx); hit_ty = int(hit_ty)
        width = max(1, int(self._water_ice_chain_limit(level)))
        half = width // 2
        hit_center_x = (hit_tx + 0.5) * TILE_SIZE
        hit_center_y = (hit_ty + 0.5) * TILE_SIZE
        dx = float(impact_x) - hit_center_x
        dy = float(impact_y) - hit_center_y
        side = 1 if dx >= 0.0 else -1
        impact_tx = int(float(impact_x) // TILE_SIZE)
        if abs(dx) > abs(dy):
            # True side contact: begin in the neighboring water column.
            preferred = [hit_tx + side, impact_tx, hit_tx, hit_tx - side]
        else:
            # Top/bottom contact: keep the same water column first.
            preferred = [hit_tx, impact_tx, hit_tx + side, hit_tx - side]
        seen = set()
        centers = []
        for tx in preferred:
            tx = int(tx)
            if tx in seen or not (0 <= tx < self.world.width_tiles):
                continue
            seen.add(tx)
            entry = self._water_entry_below_ice_or_surface(tx, max(0, hit_ty - 1))
            if entry is not None:
                centers.append((tx, entry[1]))
        if not centers:
            return []

        # Keep the side/impact preference first; it is more important than raw
        # depth when the projectile visibly hit the side of an old ice column.
        cx, entry_y = centers[0]
        targets = []
        for offset in range(-half, half + 1):
            tx = cx + offset
            entry = self._water_entry_below_ice_or_surface(tx, max(0, entry_y - 2))
            if entry is None:
                continue
            target = self._deepest_stackable_water_cell(entry[0], entry[1])
            if target is None:
                continue
            targets.append(target)
        targets.sort(key=lambda cell: (-cell[1], abs(cell[0] - cx)))
        return targets[:width]

    def _queue_buoyant_ice(self, tx, ty):
        key = (int(tx), int(ty))
        if self.world.get_tile(*key) != ICE:
            return
        self._buoyant_ice[key] = 0.0

    def _move_buoyant_ice_one_cell(self, tx, ty):
        """Move one spell-created ICE tile upward through liquid conservatively.

        The water occupying the destination cell is displaced into the old ice
        cell.  Ice mass and liquid mass are therefore preserved; only their
        vertical positions swap.  Returns the new key when movement occurred,
        otherwise None when the block reached surface/another ice/solid.
        """
        tx = int(tx); ty = int(ty)
        key = (tx, ty)
        if self.world.get_tile(tx, ty) != ICE:
            return None
        if ty <= 0:
            return None

        env = self.environment.state
        target = (tx, ty - 1)
        target_tile = self.world.get_tile(*target)
        if target_tile == ICE:
            # Existing floating ice above: this block has reached the stack.
            return None
        if target_tile != AIR:
            return None

        target_water = max(0.0, float(env.water_amount(*target)))
        # No liquid above means current tile is already the top floating cell.
        if target_water <= 0.02:
            return None

        ice_mass = max(0.0, float(env.ice_mass.get(key, 1.0)))
        ice_temp = float(env.temperature_at(tx, ty, ICEBALL_TEMPERATURE_C))
        target_temp = float(env.water_temperature_at(
            target[0], target[1], env.temperature_at(target[0], target[1], 4.0)
        ))

        # Clear destination liquid before materializing the solid there.
        env.set_water(target[0], target[1], 0.0)
        env.water_temperature.pop(target, None)
        env.water_freeze_progress.pop(target, None)

        env.ice_mass.pop(key, None)
        env.ice_melt_progress.pop(key, None)
        self.world.set_tile(tx, ty, AIR, track_change=True)

        # Displaced liquid fills the vacated lower cell.
        env.set_water(tx, ty, min(1.0, target_water))
        env.set_water_temperature(tx, ty, target_temp)

        self.world.set_tile(target[0], target[1], ICE, track_change=True)
        env.ice_mass[target] = ice_mass
        env.set_temperature(target[0], target[1], min(0.0, ice_temp))
        env.ice_melt_progress.pop(target, None)
        return target

    def _update_buoyant_ice(self, dt):
        if not self._buoyant_ice:
            return
        dt = max(0.0, float(dt))
        next_state = {}
        moved_columns = set()

        # Top-to-bottom processing plus one move per column per update makes a
        # melted gap propagate visibly through the stack instead of teleporting
        # every lower block upward in the same frame.
        items = sorted(
            tuple(self._buoyant_ice.items()),
            key=lambda item: (int(item[0][0]), int(item[0][1])),
        )
        for key, timer in items:
            tx, ty = int(key[0]), int(key[1])
            if self.world.get_tile(tx, ty) != ICE:
                continue

            timer = float(timer) + dt
            current = (tx, ty)
            above_y = ty - 1
            if above_y < 0:
                continue
            above_tile = self.world.get_tile(tx, above_y)
            above_water = float(self.environment.state.water_amount(tx, above_y))

            if above_tile == ICE:
                # Keep blocked members registered. If the upper ice melts later,
                # this block will resume buoyancy automatically.
                next_state[current] = 0.0
                continue

            if above_tile != AIR:
                # Solid ceiling: physically trapped, but keep a slow watch in
                # case terrain is mined away later.
                next_state[current] = min(timer, self._buoyant_ice_step_seconds)
                continue

            if above_water <= 0.02:
                # Air without water above = current block is already at the
                # water surface. It can leave the active buoyancy queue; lower
                # blocks remain registered independently.
                continue

            if tx in moved_columns:
                # One visible rise step per water column at a time. A lower
                # block waits a full buoyancy interval after the block above
                # vacates its cell, producing an ordered upward cascade.
                next_state[current] = 0.0
                continue
            if timer < self._buoyant_ice_step_seconds:
                next_state[current] = timer
                continue

            timer -= self._buoyant_ice_step_seconds
            moved = self._move_buoyant_ice_one_cell(*current)
            if moved is None:
                # If movement failed only because another ice appeared above,
                # retain this member so melting can reopen the path later.
                if self.world.get_tile(tx, ty - 1) == ICE:
                    next_state[current] = 0.0
                continue

            moved_columns.add(tx)
            ntx, nty = moved
            # Keep moving only while still submerged. Surface ice can leave the
            # queue, but every lower member remains and will compact upward if
            # this one subsequently melts.
            if nty > 0:
                next_tile = self.world.get_tile(ntx, nty - 1)
                next_water = float(self.environment.state.water_amount(ntx, nty - 1))
                if next_tile == ICE:
                    next_state[moved] = 0.0
                elif next_tile == AIR and next_water > 0.02:
                    next_state[moved] = timer

        self._buoyant_ice = next_state

    def _update_airborne_ice_gravity(self, dt):
        """Drop spell-created ice through AIR after its support melts/mines away.

        Ice floating in liquid remains governed by buoyancy; this pass only
        resolves unsupported AIR gaps. Bottom-to-top order makes stacks settle
        together without leaving upper blocks suspended on old grid rows.
        """
        env = self.environment.state
        keys = [k for k in tuple(env.ice_mass.keys()) if self.world.get_tile(*k) == ICE]
        for tx, ty in sorted(keys, key=lambda k: (int(k[0]), -int(k[1]))):
            tx=int(tx); ty=int(ty)
            if ty + 1 >= self.world.height_tiles:
                continue
            if self.world.get_tile(tx, ty + 1) != AIR:
                continue
            # Do not make ice sink through water; buoyancy owns submerged ice.
            if float(env.water_amount(tx, ty + 1)) > 0.02:
                continue
            src=(tx,ty); dst=(tx,ty+1)
            mass=max(0.0,float(env.ice_mass.get(src,1.0)))
            temp=float(env.temperature_at(tx,ty,ICEBALL_TEMPERATURE_C))
            melt=float(env.ice_melt_progress.pop(src,0.0))
            env.ice_mass.pop(src,None)
            self.world.set_tile(tx,ty,AIR,track_change=True)
            self.world.set_tile(dst[0],dst[1],ICE,track_change=True)
            env.ice_mass[dst]=mass
            env.set_temperature(dst[0],dst[1],temp)
            if melt > 0.0:
                env.ice_melt_progress[dst]=melt
            self.world.mark_tile_visual_dirty(tx,ty)
            self.world.mark_tile_visual_dirty(dst[0],dst[1])
            # Keep submerged destination eligible for the existing buoyancy pass.
            if float(env.water_amount(dst[0],dst[1])) > 0.02:
                self._queue_buoyant_ice(*dst)

    def _freeze_water_targets(self, ib, targets, deposit_tx, deposit_ty):
        level = int(getattr(ib, "level", 1))
        if not targets:
            return 0

        spell_remaining = max(0.0, float(getattr(ib, "water_mass", 0.0)))
        world_water_frozen = 0.0
        placed = 0

        for tx, ty in targets:
            amount = max(0.0, float(self.environment.state.water_amount(tx, ty)))
            if amount < ICE_WATER_CHAIN_MIN_AMOUNT:
                continue

            add_magic = min(spell_remaining, max(0.0, 1.0 - amount))
            ice_mass = min(1.0, amount + add_magic)
            spell_remaining = max(0.0, spell_remaining - add_magic)

            self.environment.state.set_water(tx, ty, 0.0)
            self.environment.state.water_temperature.pop((tx, ty), None)
            self.environment.state.water_freeze_progress.pop((tx, ty), None)
            self.world.set_tile(tx, ty, ICE, track_change=True)
            self.environment.state.ice_mass[(tx, ty)] = ice_mass
            self.environment.state.set_temperature(
                tx, ty, min(float(getattr(ib, "temperature_c", ICEBALL_TEMPERATURE_C)), -2.0)
            )
            self._queue_buoyant_ice(tx, ty)
            world_water_frozen += min(amount, ice_mass)
            placed += 1

        if world_water_frozen > 0.0:
            self.ledger.record_water_to_ice(
                self.internal_water_to_units(world_water_frozen)
            )

        ib.water_mass = spell_remaining
        if spell_remaining > 1e-9:
            try:
                self.environment.water.deposit(
                    int(deposit_tx),
                    int(deposit_ty),
                    spell_remaining,
                    temperature_c=2.0,
                )
                self.ledger.record_water_to_free(
                    self.internal_water_to_units(spell_remaining)
                )
                ib.water_mass = 0.0
            except Exception:
                self._return_iceball_to_vapor(ib)
        else:
            ib.water_mass = 0.0

        if placed > 0:
            self.events.emit(
                'message',
                text=(
                    f'{level} 級冰球命中水體：'
                    f'結冰 {placed}/{self._water_ice_chain_limit(level)} 格，開始上浮'
                ),
            )
        return placed

    def _freeze_water_impact(self, ib, hit_tx, hit_ty):
        level = int(getattr(ib, "level", 1))
        targets = self._surface_water_chain_targets(hit_tx, hit_ty, level)
        return self._freeze_water_targets(ib, targets, hit_tx, hit_ty)

    def _ice_impact(
        self,
        hit_tx,
        hit_ty,
        impact_x,
        impact_y,
        water_mass,
        ice_temperature_c=ICEBALL_TEMPERATURE_C,
        level=1,
    ):
        self._extinguish_fire(
            hit_tx,
            hit_ty,
        )

        count = (
            self._ice_tile_count(
                level
            )
        )

        targets = (
            self._ice_line_targets(
                hit_tx,
                hit_ty,
                impact_x,
                impact_y,
                count,
            )
        )

        if not targets:
            return (
                False,
                water_mass,
            )

        remaining = max(
            0.0,
            float(
                water_mass
            ),
        )

        created_total = 0.0
        placed = 0

        for tx, ty in targets:
            if remaining <= 1e-9:
                break

            # One ICE tile consumes at most one internal water unit.
            requested = min(
                1.0,
                remaining,
            )

            success, leftover = (
                self.environment.thermal.create_ice_tile(
                    tx,
                    ty,
                    requested,
                    temperature_c=ice_temperature_c,
                )
            )

            if not success:
                continue

            used = max(
                0.0,
                requested
                - max(
                    0.0,
                    float(
                        leftover
                    ),
                )
            )

            if used <= 1e-9:
                continue

            remaining = max(
                0.0,
                remaining
                - used,
            )

            created_total += used
            placed += 1

            self.environment.state.set_temperature(
                tx,
                ty,
                min(
                    ice_temperature_c,
                    self.environment.state.temperature_at(
                        tx,
                        ty,
                        ICEBALL_TEMPERATURE_C,
                    ),
                ),
            )

        if created_total > 0.0:
            self.ledger.record_water_to_ice(
                self.internal_water_to_units(
                    created_total
                )
            )

        if placed > 0:
            self.events.emit(
                "message",
                text=(
                    f"{level} 級冰球："
                    f"生成 {placed}/{count} 格冰"
                ),
            )

        return (
            placed > 0,
            remaining,
        )

    def _return_waterball_to_vapor(self, wb):
        mass = max(0.0, float(getattr(wb, 'water_mass', 0.0)))
        if mass <= 0.0:
            return

        chunk_px = CHUNK_SIZE * TILE_SIZE
        max_cx = max(0, (self.world.width_tiles - 1) // CHUNK_SIZE)
        max_cy = max(0, (self.world.height_tiles - 1) // CHUNK_SIZE)
        cx = max(0, min(max_cx, int(max(0.0, wb.x) // chunk_px)))
        cy = max(0, min(max_cy, int(max(0.0, wb.y) // chunk_px)))
        self.environment.state.add_vapor(
            cx,
            cy,
            mass,
        )

        self.ledger.record_water_to_vapor(
            self.internal_water_to_units(
                mass
            )
        )

        wb.water_mass = 0.0

    def reclaim_waterballs_to_atmosphere(self):
        for wb in self.waterballs:
            if getattr(
                wb,
                'active',
                False,
            ):
                self._return_waterball_to_vapor(
                    wb
                )
                wb.active = False

        self.waterballs.clear()

    def reclaim_iceballs_to_atmosphere(self):
        for ib in self.iceballs:
            if getattr(
                ib,
                'active',
                False,
            ):
                self._return_iceball_to_vapor(
                    ib
                )
                ib.active = False

        self.iceballs.clear()

    def reclaim_condensed_projectiles_to_atmosphere(self):
        self.reclaim_waterballs_to_atmosphere()
        self.reclaim_iceballs_to_atmosphere()

    # --------------------------------------------------------
    # Projectile updates
    # --------------------------------------------------------

    def _update_fireballs(self, dt):
        alive = []

        for fb in self.fireballs:
            if not fb.active:
                continue

            fb.life -= dt
            if fb.life <= 0:
                tx, ty = self._cell_from_world(
                    fb.x,
                    fb.y,
                )
                self._release_fireball_energy(
                    fb,
                    tx,
                    ty,
                )
                fb.active = False
                continue

            travel_speed = max(
                abs(fb.vx),
                abs(fb.vy),
            )
            steps = max(
                1,
                int(
                    travel_speed
                    * dt
                    / (
                        TILE_SIZE
                        * 0.28
                    )
                )
                + 1,
            )
            step_dt = dt / steps
            finished = False

            for _ in range(steps):
                fb.vy = min(
                    FIREBALL_MAX_FALL_SPEED,
                    fb.vy
                    + FIREBALL_GRAVITY
                    * step_dt,
                )

                previous_x = fb.x
                previous_y = fb.y
                next_x = (
                    fb.x
                    + fb.vx
                    * step_dt
                )
                next_y = (
                    fb.y
                    + fb.vy
                    * step_dt
                )

                # Poison gas combusts directly when a fireball enters a
                # connected cloud. The cloud is sparse, so this checks only
                # cells along the projectile segment and bounded neighbors.
                gas_hit = self._poison_gas_cell_on_segment(
                    previous_x, previous_y, next_x, next_y
                )
                if gas_hit is not None:
                    hit_tx, hit_ty, hit_x, hit_y = gas_hit
                    fb.x=hit_x; fb.y=hit_y
                    burned_cells,burned_amount=self._burn_connected_poison_gas(hit_tx,hit_ty)
                    self._release_fireball_energy(fb,hit_tx,hit_ty)
                    if self.action_messages_enabled:
                        self.events.emit('message',text=f'火球燃燒毒氣：清除 {burned_cells} 格')
                    fb.active=False; finished=True; break

                actor_hit = self._swept_creature_hit(
                    previous_x, previous_y, next_x, next_y, fb.radius
                )
                if actor_hit is not None:
                    creature, hit_x, hit_y = actor_hit
                    fb.x = hit_x
                    fb.y = hit_y
                    self._damage_creature_with_magic(
                        creature, "fire", getattr(fb, "level", 1), fb.vx, fb.vy
                    )
                    hit_tx, hit_ty = self._cell_from_world(hit_x, hit_y)
                    self._release_fireball_energy(fb, hit_tx, hit_ty)
                    fb.active = False
                    finished = True
                    break

                # Continuous/swept collision: resolve the FIRST world object
                # along the travelled segment instead of testing only endpoint.
                hit = self._swept_fireball_hit(
                    previous_x,
                    previous_y,
                    next_x,
                    next_y,
                    fb.radius,
                )

                if hit is not None:
                    hit_kind, hit_tx, hit_ty, hit_x, hit_y = hit
                    fb.x = hit_x
                    fb.y = hit_y

                    self._pulse_fireball_lift(
                        fb.x,
                        fb.y,
                        getattr(
                            fb,
                            "level",
                            1,
                        ),
                        impact_scale=0.55,
                    )

                    if hit_kind == "water":
                        # Water always consumes the fireball as heat.  This is
                        # also true for L3: once ICE has become WATER, the next
                        # fire impact performs WATER -> STEAM rather than an
                        # ignition burst on top of liquid.
                        evaporated_units = (
                            self._evaporate_free_water_with_fireball(
                                hit_tx,
                                hit_ty,
                                fb,
                            )
                        )
                        remaining_units = (
                            self.internal_water_to_units(
                                self.environment.state.water_amount(
                                    hit_tx,
                                    hit_ty,
                                )
                            )
                        )

                        self._release_fireball_energy(
                            fb,
                            hit_tx,
                            hit_ty,
                        )
                        self.events.emit(
                            'message',
                            text=(
                                f'火球蒸發 {evaporated_units:.1f} 單位水；'
                                f'剩餘 {remaining_units:.1f} 單位'
                            ),
                        )
                        fb.active = False
                        finished = True
                        break

                    # Ice phase rule: L1/L2 remain ICE -> WATER. L3 is a
                    # deliberate high-energy exception and directly vaporizes
                    # up to three connected ice tiles using their real mass.
                    if (
                        hit_kind == "solid"
                        and self.world.get_tile(hit_tx, hit_ty) == ICE
                    ):
                        if (
                            int(getattr(fb, "level", 1)) == 3
                            and not bool(getattr(fb, "fragment", False))
                        ):
                            self._vaporize_level3_ice_cluster(fb, hit_tx, hit_ty)
                        else:
                            ice_internal_before = float(
                                self.environment.state.ice_mass.get((hit_tx, hit_ty), 1.0)
                            )
                            phase = self.environment.thermal.fireball_heat_ice(
                                hit_tx,
                                hit_ty,
                                rank=getattr(fb, "level", FIREBALL_RANK),
                                fireball_temperature_c=fb.temperature_c,
                            )
                            self._release_fireball_energy(fb, hit_tx, hit_ty)
                            if phase == "water":
                                self.ledger.record_ice_to_water(
                                    self.internal_water_to_units(ice_internal_before)
                                )
                                self.events.emit(
                                    'message',
                                    text='火球：冰 → 水；再次加熱才會形成蒸汽',
                                )
                            fb.active = False
                        finished = True
                        break

                    if (
                        getattr(fb, "level", 1) == 3
                        and not getattr(fb, "fragment", False)
                    ):
                        self._explode_level3_fireball(
                            fb, hit_tx, hit_ty, fb.x, fb.y
                        )
                        finished = True
                        break

                    # Plant hits are keyed by their base terrain tile, so the
                    # existing ignition system can burn the vegetation without
                    # inventing a second fire representation.
                    self._ignite_hit_tile(hit_tx, hit_ty, fb)

                    fb.active = False
                    finished = True
                    break

                # No collision anywhere on this sub-step: commit movement.
                fb.x = next_x
                fb.y = next_y

                self._pulse_fireball_lift(
                    fb.x,
                    fb.y,
                    getattr(
                        fb,
                        "level",
                        1,
                    ),
                    impact_scale=0.42,
                )

                env_tx, env_ty = self._cell_from_world(
                    fb.x,
                    fb.y,
                )

                if (
                    0 <= env_tx < self.world.width_tiles
                    and 0 <= env_ty < self.world.height_tiles
                ):
                    ambient_c = (
                        self.environment.state.temperature_at(
                            env_tx,
                            env_ty,
                            20.0,
                        )
                    )
                    fb.temperature_c += (
                        ambient_c
                        - fb.temperature_c
                    ) * min(
                        1.0,
                        0.10
                        * step_dt,
                    )

                if (
                    fb.x < 0
                    or fb.y < 0
                    or fb.x >= self.world.width_tiles * TILE_SIZE
                    or fb.y >= self.world.height_tiles * TILE_SIZE
                ):
                    self._release_fireball_energy(
                        fb
                    )
                    fb.active = False
                    finished = True
                    break

            if (
                not finished
                and fb.active
            ):
                alive.append(
                    fb
                )

        self.fireballs = alive

    def _update_waterballs(self, dt):
        alive = []

        for wb in self.waterballs:
            if not wb.active:
                continue

            wb.life -= dt
            if wb.life <= 0:
                self._return_waterball_to_vapor(wb)
                wb.active = False
                continue

            travel_speed = max(abs(wb.vx), abs(wb.vy))
            steps = max(1, int(travel_speed * dt / (TILE_SIZE * 0.26)) + 1)
            step_dt = dt / steps
            finished = False

            for _ in range(steps):
                wb.vy = min(
                    WATERBALL_MAX_FALL_SPEED,
                    wb.vy + WATERBALL_GRAVITY * step_dt,
                )
                previous_x = wb.x
                previous_y = wb.y
                next_x = wb.x + wb.vx * step_dt
                next_y = wb.y + wb.vy * step_dt

                actor_hit = self._swept_creature_hit(
                    previous_x, previous_y, next_x, next_y, wb.radius
                )
                if actor_hit is not None:
                    creature, hit_x, hit_y = actor_hit
                    wb.x = hit_x
                    wb.y = hit_y
                    self._damage_creature_with_magic(
                        creature, "water", getattr(wb, "level", 1), wb.vx, wb.vy
                    )
                    self._splash_water_at_creature(
                        creature, wb.water_mass, wb.temperature_c
                    )
                    wb.water_mass = 0.0
                    wb.active = False
                    finished = True
                    break

                wb.x = next_x
                wb.y = next_y

                env_tx, env_ty = self._cell_from_world(
                    wb.x,
                    wb.y,
                )

                if (
                    0 <= env_tx
                    < self.world.width_tiles
                    and 0 <= env_ty
                    < self.world.height_tiles
                ):
                    ambient_c = (
                        self.environment.state.temperature_at(
                            env_tx,
                            env_ty,
                            20.0,
                        )
                    )

                    wb.temperature_c += (
                        ambient_c
                        - wb.temperature_c
                    ) * min(
                        1.0,
                        0.28
                        * step_dt,
                    )

                if (
                    wb.x < 0 or wb.y < 0
                    or wb.x >= self.world.width_tiles * TILE_SIZE
                    or wb.y >= self.world.height_tiles * TILE_SIZE
                ):
                    self._return_waterball_to_vapor(wb)
                    wb.active = False
                    finished = True
                    break

                tx, ty = self._cell_from_world(wb.x, wb.y)

                # FIX35: waterball quenches magma. Water is first returned
                # as real free liquid; LavaSystem then consumes it gradually,
                # darkens the magma and commits igneous rock + steam.
                if self.environment.state.lava_amount(tx,ty) > 0.02:
                    self.environment.water.deposit(tx,ty,wb.water_mass,temperature_c=wb.temperature_c)
                    wb.water_mass=0.0; wb.active=False; finished=True
                    self.events.emit('message',text='水球正在冷卻岩漿')
                    break

                # Existing water absorbs/merges the waterball immediately.
                if self.environment.state.water_amount(tx, ty) > 0.02:
                    if (
                        getattr(
                            wb,
                            "level",
                            1,
                        )
                        == 3
                        and not getattr(
                            wb,
                            "fragment",
                            False,
                        )
                    ):
                        self._split_level3_waterball(
                            wb,
                            tx,
                            ty,
                            wb.x,
                            wb.y,
                            force_horizontal=True,
                        )

                        finished = True
                        break

                    self._water_impact_water(
                        tx,
                        ty,
                        wb.water_mass,
                        wb.temperature_c,
                    )
                    wb.water_mass = 0.0
                    wb.active = False
                    finished = True
                    break

                hit_cell = self._solid_hit_cell(
                    wb.x,
                    wb.y,
                    wb.radius,
                )

                if hit_cell is not None:
                    hit_tx, hit_ty = hit_cell

                    if (
                        getattr(
                            wb,
                            "level",
                            1,
                        )
                        == 3
                        and not getattr(
                            wb,
                            "fragment",
                            False,
                        )
                    ):
                        self._split_level3_waterball(
                            wb,
                            hit_tx,
                            hit_ty,
                            wb.x,
                            wb.y,
                        )

                        finished = True
                        break

                    self._water_impact_solid(
                        hit_tx,
                        hit_ty,
                        wb.x,
                        wb.y,
                        wb.water_mass,
                        wb.temperature_c,
                    )
                    wb.water_mass = 0.0
                    wb.active = False
                    finished = True
                    break

            if not finished and wb.active:
                alive.append(wb)

        self.waterballs = alive


    def _update_iceballs(
        self,
        dt,
    ):
        alive = []

        for ib in self.iceballs:
            if not ib.active:
                continue

            ib.life -= dt

            if ib.life <= 0:
                self._return_iceball_to_vapor(
                    ib
                )
                ib.active = False
                continue

            travel_speed = max(
                abs(
                    ib.vx
                ),
                abs(
                    ib.vy
                ),
            )

            steps = max(
                1,
                int(
                    travel_speed
                    * dt
                    / (
                        TILE_SIZE
                        * 0.26
                    )
                )
                + 1,
            )

            step_dt = (
                dt
                / steps
            )

            finished = False

            for _ in range(
                steps
            ):
                ib.vy = min(
                    ICEBALL_MAX_FALL_SPEED,
                    ib.vy
                    + ICEBALL_GRAVITY
                    * step_dt,
                )

                previous_x = ib.x
                previous_y = ib.y
                next_x = ib.x + ib.vx * step_dt
                next_y = ib.y + ib.vy * step_dt

                actor_hit = self._swept_creature_hit(
                    previous_x, previous_y, next_x, next_y, ib.radius
                )
                if actor_hit is not None:
                    creature, hit_x, hit_y = actor_hit
                    ib.x = hit_x
                    ib.y = hit_y
                    self._damage_creature_with_magic(
                        creature, "ice", getattr(ib, "level", 1), ib.vx, ib.vy
                    )
                    # The condensed magic matter is returned to atmosphere on
                    # an actor hit instead of silently disappearing.
                    self._return_iceball_to_vapor(ib)
                    ib.active = False
                    finished = True
                    break

                ib.x = next_x
                ib.y = next_y

                env_tx, env_ty = self._cell_from_world(
                    ib.x,
                    ib.y,
                )

                if (
                    0 <= env_tx
                    < self.world.width_tiles
                    and 0 <= env_ty
                    < self.world.height_tiles
                ):
                    ambient_c = (
                        self.environment.state.temperature_at(
                            env_tx,
                            env_ty,
                            20.0,
                        )
                    )

                    ib.temperature_c += (
                        ambient_c
                        - ib.temperature_c
                    ) * min(
                        1.0,
                        0.20
                        * step_dt,
                    )

                if (
                    ib.x < 0
                    or ib.y < 0
                    or ib.x
                    >= self.world.width_tiles
                    * TILE_SIZE
                    or ib.y
                    >= self.world.height_tiles
                    * TILE_SIZE
                ):
                    self._return_iceball_to_vapor(
                        ib
                    )
                    ib.active = False
                    finished = True
                    break

                tx, ty = self._cell_from_world(
                    ib.x,
                    ib.y,
                )

                # FIX35: ice does not directly create rock on magma. It first
                # melts into ordinary water; the shared lava/water cooling
                # system performs the quench and steam reaction afterwards.
                if self.environment.state.lava_amount(tx,ty) > 0.02:
                    melt_mass=max(0.0,float(getattr(ib,'water_mass',0.0)))
                    if melt_mass>0.0:
                        self.environment.water.deposit(tx,ty,melt_mass,temperature_c=2.0)
                    ib.water_mass=0.0; ib.active=False; finished=True
                    self.events.emit('message',text='冰球遇岩漿先融成水，再冷卻岩漿')
                    break

                # Direct water impact uses a bounded surface-freeze budget.
                # L3 may freeze at most seven contiguous surface cells; no
                # generic WATER+ICE reaction is allowed to continue the chain.
                if (
                    self.environment.state.water_amount(tx, ty) > 0.02
                    and self.world.get_tile(tx, ty) == AIR
                ):
                    placed = self._freeze_water_impact(ib, tx, ty)
                    if placed > 0:
                        ib.active = False
                        finished = True
                        break

                hit_cell = self._solid_hit_cell(
                    ib.x,
                    ib.y,
                    ib.radius,
                )

                if hit_cell is not None:
                    hit_tx, hit_ty = hit_cell

                    # Existing floating ice is not a legal sideways growth
                    # surface while water exists below/alongside it. Redirect
                    # the projectile into that water column, freeze there, then
                    # let buoyancy perform the visible rise/stack process.
                    if self.world.get_tile(hit_tx, hit_ty) == ICE:
                        redirected = self._ice_hit_water_chain_targets(
                            hit_tx,
                            hit_ty,
                            ib.x,
                            ib.y,
                            getattr(ib, "level", 1),
                        )
                        if redirected:
                            placed = self._freeze_water_targets(
                                ib,
                                redirected,
                                redirected[0][0],
                                redirected[0][1],
                            )
                            if placed > 0:
                                ib.active = False
                                finished = True
                                break

                    success, leftover = (
                        self._ice_impact(
                            hit_tx,
                            hit_ty,
                            ib.x,
                            ib.y,
                            ib.water_mass,
                            ib.temperature_c,
                            level=getattr(
                                ib,
                                "level",
                                1,
                            ),
                        )
                    )

                    if success:
                        ib.water_mass = 0.0

                        if leftover > 1e-9:
                            target = self._surface_water_cell(
                                hit_tx,
                                hit_ty,
                                ib.x,
                                ib.y,
                            )

                            if target is not None:
                                self.environment.water.deposit(
                                    target[0],
                                    target[1],
                                    leftover,
                                    temperature_c=ICEBALL_TEMPERATURE_C,
                                )
                            else:
                                self.environment.state.add_vapor(
                                    hit_tx
                                    // CHUNK_SIZE,
                                    hit_ty
                                    // CHUNK_SIZE,
                                    leftover,
                                )

                        self.events.emit(
                            'message',
                            text='冰球生成結冰圖塊',
                        )
                    else:
                        self._return_iceball_to_vapor(
                            ib
                        )

                    ib.active = False
                    finished = True
                    break

            if (
                not finished
                and ib.active
            ):
                alive.append(
                    ib
                )

        self.iceballs = alive

    def _water_surface_cell(self, tx, ty):
        tx=int(tx); ty=int(ty)
        if self.environment.state.water_amount(tx,ty) <= 0.02:
            return None
        surface=ty
        while surface > 0 and self.environment.state.water_amount(tx,surface-1) > 0.02:
            surface -= 1
        return (tx,surface)

    def _level3_electrolyze_water(self, eb, tx, ty):
        if int(getattr(eb,"level",1)) != 3:
            return 0.0
        surface = self._water_surface_cell(tx,ty)
        if surface is None:
            return 0.0
        sx,sy=surface
        gas_y=sy-1
        if gas_y < 0 or tile_def(self.world.get_tile(sx,gas_y)).solid:
            return 0.0
        available=max(0.0,float(self.environment.state.water_amount(tx,ty)))
        used=min(available,float(ELECTRICBALL_L3_ELECTROLYSIS_WATER))
        if used <= 1e-6:
            return 0.0
        self.environment.state.set_water(tx,ty,max(0.0,available-used))
        gas_amount=used*float(ELECTRICBALL_L3_POISON_GAS_YIELD)
        self.environment.state.add_reactive("poison_gas",sx,gas_y,gas_amount)
        self.environment.state.poison_gas_ttl[(sx,gas_y)] = float(POISON_GAS_LIFETIME_SECONDS)
        try:
            self.environment.reactions.mark_dirty(sx,gas_y,True)
            self.environment.reactions.mark_dirty(tx,ty,True)
        except Exception:
            pass
        return used

    def _poison_gas_cell_on_segment(self,x0,y0,x1,y1):
        dx=float(x1)-float(x0); dy=float(y1)-float(y0)
        dist=math.hypot(dx,dy)
        count=max(1,int(math.ceil(dist/max(2.0,TILE_SIZE*0.18))))
        for i in range(1,count+1):
            f=i/count; x=x0+dx*f; y=y0+dy*f
            tx,ty=self._cell_from_world(x,y)
            if self.environment.state.reactive_amount("poison_gas",tx,ty) > 0.02:
                return tx,ty,x,y
        return None

    def _burn_connected_poison_gas(self, start_tx, start_ty):
        env=self.environment.state
        start=(int(start_tx),int(start_ty))
        if env.reactive_amount("poison_gas",*start) <= 0.02:
            return 0,0.0
        q=deque([start]); seen={start}; removed=0.0; cells=0
        cap=max(1,int(POISON_GAS_FIREBALL_CHAIN_MAX_CELLS))
        while q and cells < cap:
            tx,ty=q.popleft()
            amount=env.reactive_amount("poison_gas",tx,ty)
            if amount <= 0.02:
                continue
            removed += amount; cells += 1
            env.set_reactive("poison_gas",tx,ty,0.0)
            env.poison_gas_ttl.pop((tx,ty),None)
            for nx,ny in ((tx-1,ty),(tx+1,ty),(tx,ty-1),(tx,ty+1)):
                key=(nx,ny)
                if key in seen or not (0<=nx<self.world.width_tiles and 0<=ny<self.world.height_tiles):
                    continue
                seen.add(key)
                if env.reactive_amount("poison_gas",nx,ny) > 0.02:
                    q.append(key)
        if removed > 0.0:
            env.add_smoke(start[0]//CHUNK_SIZE,start[1]//CHUNK_SIZE,removed)
            env.add_updraft(start[0]//CHUNK_SIZE,start[1]//CHUNK_SIZE,removed*18.0)
        return cells,removed

    def _swept_electric_hit(self, x0, y0, x1, y1, radius):
        """Electric projectile collision that deliberately ignores water.

        Entering a lake/ocean is not an impact.  The ball keeps its ballistic
        path through water and only discharges when it hits a creature/solid/
        plant, reaches its lifetime, or leaves the world.
        """
        dx=float(x1)-float(x0); dy=float(y1)-float(y0)
        distance=math.hypot(dx,dy)
        max_step=max(1.5,min(4.0,float(radius)*0.45))
        count=max(1,int(math.ceil(distance/max_step)))
        for i in range(1,count+1):
            f=i/float(count)
            x=float(x0)+dx*f; y=float(y0)+dy*f
            tx,ty=self._cell_from_world(x,y)
            if not (0<=tx<self.world.width_tiles and 0<=ty<self.world.height_tiles):
                continue
            plant_hit=self._plant_hit_cell(x,y,radius)
            if plant_hit is not None:
                return ("plant",plant_hit[0],plant_hit[1],x,y)
            solid_hit=self._solid_hit_cell(x,y,radius)
            if solid_hit is not None:
                return ("solid",solid_hit[0],solid_hit[1],x,y)
        return None

    def _electric_impact(self, eb, hit_kind, tx, ty):
        tx=int(tx); ty=int(ty)
        charge=max(0.0,float(getattr(eb,"charge",0.0)))
        if charge <= 0.0:
            return 0
        level=max(1,min(3,int(getattr(eb,"level",1))))
        water=self.environment.state.water_amount(tx,ty)

        if water>0.02:
            # V0.7.7.5: water conduction is a LOCAL gameplay radius, never a
            # whole connected lake/ocean flood-fill.  Level 1/2/3 => exactly
            # one/two/three tiles around the impact cell.
            touched=self.environment.electrical.apply_charge_bounded(
                tx,ty,charge,radius=level,water_only=True
            )
            cells=len(touched)
            for sx,sy in touched:
                dist=max(abs(int(sx)-tx),abs(int(sy)-ty))
                gain=max(0.32,1.0-0.18*float(dist))
                self.environment.state.set_reactive(
                    "electric_shock",int(sx),int(sy),
                    max(0.18,min(2.4,charge*ELECTRICBALL_WATER_SHOCK_SCALE*gain))
                )
                try:
                    self.environment.reactions.mark_dirty(int(sx),int(sy),True)
                except Exception:
                    pass
        else:
            # Dry impact keeps the existing property-driven conductor logic.
            self.environment.state.set_reactive(
                "electric_shock",tx,ty,max(0.22,min(2.4,charge*ELECTRICBALL_DRY_SHOCK_SCALE))
            )
            cells=self.environment.electrical.apply_charge(tx,ty,charge)
            try:
                self.environment.reactions.mark_dirty(tx,ty,True)
            except Exception:
                pass

        electrolyzed=0.0
        if water>0.02 and level == 3:
            electrolyzed=self._level3_electrolyze_water(eb,tx,ty)

        if self.action_messages_enabled:
            if water>0.02:
                text=(
                    f'3級電球電解水 {electrolyzed:.2f}；水中電擊半徑 {level} 格'
                    if electrolyzed>0 else
                    f'{level}級電球水中放電：半徑 {level} 格 / {cells} 格導通'
                )
            elif cells>1:
                text=f'電球沿乾燥導體傳遞 {cells} 格'
            elif hit_kind == "timeout":
                text='電球飛行結束：局部放電'
            else:
                text='電球命中：局部放電'
            self.events.emit('message',text=text)
        return cells

    def _update_electricballs(self, dt):
        alive=[]
        for eb in self.electricballs:
            if not eb.active:
                continue

            # Lifetime expires into a discharge at the ball's CURRENT cell.
            # Crucially, merely entering water no longer ends the projectile.
            eb.life -= dt
            if eb.life <= 0:
                tx,ty=self._cell_from_world(eb.x,eb.y)
                if 0<=tx<self.world.width_tiles and 0<=ty<self.world.height_tiles:
                    self._electric_impact(eb,"timeout",tx,ty)
                eb.active=False
                continue

            travel_speed=max(abs(eb.vx),abs(eb.vy))
            steps=max(1,int(travel_speed*dt/(TILE_SIZE*0.25))+1)
            step_dt=dt/steps
            finished=False
            for _ in range(steps):
                eb.vy=min(ELECTRICBALL_MAX_FALL_SPEED,eb.vy+ELECTRICBALL_GRAVITY*step_dt)
                x0,y0=eb.x,eb.y
                x1=x0+eb.vx*step_dt; y1=y0+eb.vy*step_dt

                actor_hit=self._swept_creature_hit(x0,y0,x1,y1,eb.radius)
                if actor_hit is not None:
                    creature,hx,hy=actor_hit
                    eb.x=hx; eb.y=hy
                    self._damage_creature_with_magic(
                        creature,"electric",getattr(eb,"level",1),eb.vx,eb.vy
                    )
                    tx,ty=self._cell_from_world(hx,hy)
                    self._electric_impact(eb,"creature",tx,ty)
                    eb.active=False; finished=True; break

                # Electric balls have their own sweep: WATER IS NOT A HIT.
                hit=self._swept_electric_hit(x0,y0,x1,y1,eb.radius)
                if hit is not None:
                    hit_kind,tx,ty,hx,hy=hit
                    eb.x=hx; eb.y=hy
                    self._electric_impact(eb,hit_kind,tx,ty)
                    eb.active=False; finished=True; break

                eb.x=x1; eb.y=y1
                if (eb.x<0 or eb.y<0 or eb.x>=self.world.width_tiles*TILE_SIZE or eb.y>=self.world.height_tiles*TILE_SIZE):
                    eb.active=False; finished=True; break

            if not finished and eb.active:
                alive.append(eb)
        self.electricballs=alive

    def update(self, dt):
        self._update_airborne_ice_gravity(dt)
        self._update_buoyant_ice(dt)
        self._update_fireballs(dt)
        self._update_waterballs(dt)
        self._update_iceballs(dt)
        self._update_electricballs(dt)
