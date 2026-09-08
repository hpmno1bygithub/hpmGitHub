# -*- coding: utf-8 -*-
"""Bounded, map-local creature respawn scheduling.

The gameplay population is authored once by :mod:`systems.biome_system`.
Respawning deliberately reuses those same Creature objects instead of
allocating replacements.  This keeps ``GameApp.creatures``, the scene list,
the environment object tuple and renderer references stable on Pyto.

State flow for one authored spawn::

    alive -> waiting -> blocked -> waiting -> alive
                  -> defeated (respawn disabled / ordinary boss policy)

Only a small due-time heap is touched every update.  There is no whole-world
terrain scan and no per-frame Creature allocation.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math

from config import TILE_SIZE
from engine.math2d import rects_overlap


_FLYING_LOCOMOTION = frozenset((
    "bird", "insect_low", "fly_low", "cave_fly", "ghost_fly", "sky_fly",
))
_AQUATIC_LOCOMOTION = frozenset(("fish", "swim"))


@dataclass
class _SpawnRecord:
    entity: object
    entity_id: str
    species: str
    x: float
    y: float
    home_x: float
    home_y: float
    width: float
    height: float
    max_hp: float
    temperature_c: float
    region: str
    locomotion: str
    boss: bool
    enabled: bool
    delay_seconds: float
    state: str = "alive"


@dataclass
class _PendingRespawn:
    entity_id: str
    due_at: float
    generation: int
    epoch: int
    retries: int = 0
    blocked_reason: str = ""


class CreatureRespawnSystem:
    """Low-cost death -> delayed safe respawn state machine.

    ``species_policies`` accepts either a delay number, ``None`` (disabled),
    or a dictionary such as ``{"enabled": True, "delay_seconds": 30}``.
    ``region_delay_multipliers`` uses ``Creature.respawn_region``, otherwise
    ``underground`` for underground actors and finally ``Creature.biome``.

    Bosses never auto-respawn by default.  Set ``boss_mode="timed"`` or give
    that boss species an explicit enabled policy to opt in.
    """

    FORMAT_VERSION = 1

    def __init__(
        self,
        scene,
        physics,
        player,
        *,
        map_token="",
        visibility_callback=None,
        region_callback=None,
        species_policies=None,
        region_delay_multipliers=None,
        hostile_delay_seconds=45.0,
        passive_delay_seconds=75.0,
        boss_delay_seconds=600.0,
        boss_mode="never",
        min_player_distance_px=None,
        camera_half_width_px=470.0,
        camera_half_height_px=280.0,
        retry_delay_seconds=8.0,
        poll_interval_seconds=0.25,
        population_sync_interval_seconds=1.0,
        max_due_checks_per_tick=4,
        max_respawns_per_tick=2,
    ):
        self.scene = scene
        self.physics = physics
        self.player = player
        self.map_token = str(map_token or "")
        self.visibility_callback = visibility_callback
        self.region_callback = region_callback
        self.species_policies = dict(species_policies or {})
        self.region_delay_multipliers = dict(region_delay_multipliers or {})
        self.hostile_delay_seconds = max(0.25, float(hostile_delay_seconds))
        self.passive_delay_seconds = max(0.25, float(passive_delay_seconds))
        self.boss_delay_seconds = max(0.25, float(boss_delay_seconds))
        self.boss_mode = str(boss_mode or "never").lower()
        self.min_player_distance_px = max(
            0.0,
            float(
                min_player_distance_px
                if min_player_distance_px is not None
                else TILE_SIZE * 12.0
            ),
        )
        self.camera_half_width_px = max(0.0, float(camera_half_width_px))
        self.camera_half_height_px = max(0.0, float(camera_half_height_px))
        self.retry_delay_seconds = max(0.25, float(retry_delay_seconds))
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.population_sync_interval_seconds = max(
            self.poll_interval_seconds,
            float(population_sync_interval_seconds),
        )
        self.max_due_checks_per_tick = max(1, int(max_due_checks_per_tick))
        self.max_respawns_per_tick = max(1, int(max_respawns_per_tick))

        self.clock = 0.0
        self._poll_accumulator = 0.0
        self._population_sync_accumulator = 0.0
        self._population_epoch = 1
        self._generation = 0
        self._records = {}
        self._pending = {}
        self._heap = []
        self.last_blocked_reason = ""

    # ------------------------------------------------------------------
    # Population / policy registration
    # ------------------------------------------------------------------
    @property
    def world(self):
        return getattr(self.physics, "world", None)

    @staticmethod
    def _number(value, fallback):
        try:
            value = float(value)
            if math.isfinite(value):
                return value
        except Exception:
            pass
        return float(fallback)

    def _region_for(self, creature):
        callback = self.region_callback
        if callable(callback):
            try:
                value = callback(creature)
                if value:
                    return str(value)
            except Exception:
                pass
        explicit = str(getattr(creature, "respawn_region", "") or "")
        if explicit:
            return explicit
        if bool(getattr(creature, "underground", False)):
            return "underground"
        return str(getattr(creature, "biome", "default") or "default")

    def _policy_for(self, creature):
        species = str(getattr(creature, "species", "") or "")
        raw = self.species_policies.get(species, {})
        explicit_enabled = False
        enabled = True
        delay = None
        if raw is None:
            enabled = False
            explicit_enabled = True
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            delay = float(raw)
            enabled = delay >= 0.0
            explicit_enabled = True
        elif isinstance(raw, dict):
            if "enabled" in raw:
                enabled = bool(raw.get("enabled"))
                explicit_enabled = True
            if "delay_seconds" in raw or "delay" in raw:
                raw_delay = raw.get("delay_seconds", raw.get("delay"))
                if raw_delay is not None:
                    delay = self._number(raw_delay, self.hostile_delay_seconds)

        if hasattr(creature, "respawn_enabled"):
            enabled = bool(getattr(creature, "respawn_enabled"))
            explicit_enabled = True
        if hasattr(creature, "respawn_delay_seconds"):
            delay = self._number(
                getattr(creature, "respawn_delay_seconds"),
                self.hostile_delay_seconds,
            )

        boss = bool(getattr(creature, "boss", False))
        if boss and not explicit_enabled and self.boss_mode != "timed":
            enabled = False
        if delay is None:
            if boss:
                delay = self.boss_delay_seconds
            elif bool(getattr(creature, "hostile", False)):
                delay = self.hostile_delay_seconds
            else:
                delay = self.passive_delay_seconds

        region = self._region_for(creature)
        multiplier = self._number(
            self.region_delay_multipliers.get(region, 1.0),
            1.0,
        )
        return bool(enabled), max(0.25, float(delay) * max(0.05, multiplier)), region

    def register(self, creature):
        """Register one authored spawn; duplicate entity IDs are rejected."""
        entity_id = str(getattr(creature, "entity_id", "") or "")
        if not entity_id:
            return False
        existing = self._records.get(entity_id)
        if existing is not None:
            return existing.entity is creature

        enabled, delay, region = self._policy_for(creature)
        width = max(1.0, self._number(getattr(creature, "width_px", 1.0), 1.0))
        height = max(1.0, self._number(getattr(creature, "height_px", 1.0), 1.0))
        max_hp = max(1.0, self._number(getattr(creature, "max_hp", 1.0), 1.0))
        x = self._number(getattr(creature, "home_x", 0.0), getattr(creature, "x", 0.0))
        y = self._number(getattr(creature, "home_y", 0.0), getattr(creature, "y", 0.0))
        # Old actors may leave home_x/home_y at their dataclass zero default.
        if abs(x) <= 1e-9 and abs(float(getattr(creature, "x", 0.0))) > 1e-9:
            x = float(creature.x)
        if abs(y) <= 1e-9 and abs(float(getattr(creature, "y", 0.0))) > 1e-9:
            y = float(creature.y)
        record = _SpawnRecord(
            entity=creature,
            entity_id=entity_id,
            species=str(getattr(creature, "species", "") or ""),
            x=float(x),
            y=float(y),
            home_x=float(x),
            home_y=float(y),
            width=width,
            height=height,
            max_hp=max_hp,
            temperature_c=self._number(getattr(creature, "temperature_c", 37.0), 37.0),
            region=region,
            locomotion=str(getattr(creature, "locomotion", "ground") or "ground"),
            boss=bool(getattr(creature, "boss", False)),
            enabled=enabled,
            delay_seconds=delay,
            state="alive" if bool(getattr(creature, "active", True)) else "waiting",
        )
        self._records[entity_id] = record
        if not bool(getattr(creature, "active", True)) or float(getattr(creature, "hp", 0.0)) <= 0.0:
            self.on_death(creature)
        return True

    def register_population(self, creatures):
        added = 0
        for creature in tuple(creatures or ()):
            if self.register(creature):
                added += 1
            # A save loader can restore death_processed=True after initial
            # registration. Detect it at the low-frequency population sync.
            if (
                not bool(getattr(creature, "active", True))
                or float(getattr(creature, "hp", 0.0)) <= 0.0
            ):
                self.on_death(creature)
        return added

    def replace_population(self, creatures, map_token=None):
        """Clear old-map schedules and bind a newly authored population."""
        self._population_epoch += 1
        self._records.clear()
        self._pending.clear()
        self._heap[:] = []
        if map_token is not None:
            self.map_token = str(map_token or "")
        self.register_population(creatures)

    def set_visibility_callback(self, callback):
        """Install ``callback(creature, spawn_x, spawn_y) -> bool``."""
        self.visibility_callback = callback

    # ------------------------------------------------------------------
    # Death scheduling / save bridge
    # ------------------------------------------------------------------
    def _push_pending(self, record, remaining, retries=0, reason=""):
        self._generation += 1
        pending = _PendingRespawn(
            entity_id=record.entity_id,
            due_at=self.clock + max(0.0, float(remaining)),
            generation=self._generation,
            epoch=self._population_epoch,
            retries=max(0, int(retries)),
            blocked_reason=str(reason or ""),
        )
        self._pending[record.entity_id] = pending
        heapq.heappush(
            self._heap,
            (pending.due_at, pending.generation, pending.epoch, record.entity_id),
        )
        return pending

    def on_death(self, creature):
        """Schedule exactly one respawn for a newly dead Creature."""
        entity_id = str(getattr(creature, "entity_id", "") or "")
        record = self._records.get(entity_id)
        if record is None:
            if not self.register(creature):
                return False
            record = self._records.get(entity_id)
        if record is None:
            return False
        if record.entity is not creature:
            return False
        if not record.enabled:
            record.state = "defeated"
            self._pending.pop(entity_id, None)
            return False
        if entity_id in self._pending:
            return False
        record.state = "waiting"
        self._push_pending(record, record.delay_seconds)
        return True

    def cancel_all(self):
        self._population_epoch += 1
        self._pending.clear()
        self._heap[:] = []
        for record in self._records.values():
            entity = record.entity
            if bool(getattr(entity, "active", False)) and float(getattr(entity, "hp", 0.0)) > 0.0:
                record.state = "alive"
            else:
                record.state = "defeated"

    def export_state(self):
        """Return only pending timers; authored spawn blueprints stay in map data."""
        rows = []
        for entity_id in sorted(self._pending):
            pending = self._pending[entity_id]
            rows.append({
                "entity_id": entity_id,
                "remaining_seconds": max(0.0, pending.due_at - self.clock),
                "retries": int(pending.retries),
                "blocked_reason": str(pending.blocked_reason or ""),
            })
        return {
            "format": self.FORMAT_VERSION,
            "map_token": self.map_token,
            "pending": rows,
            "defeated": sorted(
                record.entity_id
                for record in self._records.values()
                if record.state == "defeated"
            ),
        }

    def import_state(
        self,
        state,
        *,
        strict_map_token=True,
        restore_dead=False,
        elapsed_seconds=0.0,
    ):
        """Restore remaining timers after Creature active/HP state is loaded.

        A mismatched non-empty map token is rejected so a portal save cannot
        respawn an actor into another map with the same entity ID.  Portal map
        caches may pass ``restore_dead=True`` because they rebuild fresh actors;
        ordinary SaveManager load should first restore Creature HP/active fields
        and can keep the default. ``elapsed_seconds`` lets unloaded-map time
        count down without running that map's AI.
        """
        if not isinstance(state, dict):
            return False
        saved_token = str(state.get("map_token", "") or "")
        if strict_map_token and saved_token and self.map_token and saved_token != self.map_token:
            return False
        self._population_epoch += 1
        self._pending.clear()
        self._heap[:] = []
        elapsed_seconds = max(0.0, self._number(elapsed_seconds, 0.0))
        defeated = state.get("defeated", ())
        if isinstance(defeated, (list, tuple)):
            for entity_id in defeated:
                record = self._records.get(str(entity_id or ""))
                if record is None:
                    continue
                if restore_dead:
                    record.entity.active = False
                    record.entity.hp = 0.0
                    record.entity.death_processed = True
                if (
                    not bool(getattr(record.entity, "active", False))
                    or float(getattr(record.entity, "hp", 0.0)) <= 0.0
                ):
                    record.state = "defeated"
        for row in state.get("pending", ()) if isinstance(state.get("pending", ()), (list, tuple)) else ():
            if not isinstance(row, dict):
                continue
            record = self._records.get(str(row.get("entity_id", "") or ""))
            if record is None or not record.enabled:
                continue
            entity = record.entity
            if restore_dead:
                entity.active = False
                entity.hp = 0.0
                entity.death_processed = True
            if bool(getattr(entity, "active", False)) and float(getattr(entity, "hp", 0.0)) > 0.0:
                record.state = "alive"
                continue
            record.state = "waiting"
            remaining = max(
                0.0,
                self._number(row.get("remaining_seconds", record.delay_seconds), record.delay_seconds)
                - elapsed_seconds,
            )
            self._push_pending(
                record,
                remaining,
                retries=int(row.get("retries", 0) or 0),
                reason=str(row.get("blocked_reason", "") or ""),
            )
        return True

    # ------------------------------------------------------------------
    # Safety gates
    # ------------------------------------------------------------------
    def _wrapped_dx(self, x1, x2):
        dx = abs(float(x1) - float(x2))
        world = self.world
        if world is not None and bool(getattr(world, "horizontal_wrap_enabled", False)):
            circumference = max(0.0, float(getattr(world, "width_tiles", 0)) * TILE_SIZE)
            if circumference > 0.0:
                dx = min(dx, max(0.0, circumference - dx))
        return dx

    def _player_too_close(self, record):
        player = self.player
        dx = self._wrapped_dx(record.x, getattr(player, "x", 0.0))
        dy = abs(float(record.y) - float(getattr(player, "y", 0.0)))
        return math.hypot(dx, dy) < self.min_player_distance_px

    def _spawn_visible(self, record):
        callback = self.visibility_callback
        if callable(callback):
            try:
                return bool(callback(record.entity, record.x, record.y))
            except Exception:
                # Visibility uncertainty must never create a pop-in.
                return True
        # Renderer-neutral fallback: the player normally sits near camera
        # centre. GameApp may replace this with exact camera bounds via the API.
        dx = self._wrapped_dx(record.x, getattr(self.player, "x", 0.0))
        dy = abs(float(record.y) - float(getattr(self.player, "y", 0.0)))
        return (
            dx <= self.camera_half_width_px + record.width
            and dy <= self.camera_half_height_px + record.height
        )

    @staticmethod
    def _spawn_bbox(record):
        return (
            record.x - record.width * 0.5,
            record.y - record.height,
            record.x + record.width * 0.5,
            record.y,
        )

    def _terrain_safe(self, record):
        world = self.world
        if world is None:
            return False
        bbox = self._spawn_bbox(record)
        try:
            for _tx, _ty, _tile_id, solid_rect in world.solid_cells_in_rect(bbox, padding=1):
                if rects_overlap(bbox, solid_rect):
                    return False
        except Exception:
            return False

        tx = int(record.x // TILE_SIZE)
        center_y = record.y - record.height * 0.5
        ty = int(center_y // TILE_SIZE)
        state = getattr(world, "environment_state", None)
        if record.locomotion in _AQUATIC_LOCOMOTION:
            if state is None:
                return False
            try:
                return float(state.water_amount(tx, ty)) > 0.025
            except Exception:
                return False

        if record.locomotion in ('ceiling','ceiling_drop'):
            from world.tile_registry import tile_def
            ty=int((record.y-record.height-1.)//TILE_SIZE)
            return bool(tile_def(world.get_tile(tx,ty)).solid)
        if record.locomotion in _FLYING_LOCOMOTION:
            return True

        # Ordinary ground actors (and waterbirds) require an actual platform.
        probe = (bbox[0] + 2.0, bbox[3], bbox[2] - 2.0, bbox[3] + 3.25)
        try:
            for _tx, _ty, _tile_id, solid_rect in world.solid_cells_in_rect(probe, padding=1):
                horizontal = not (probe[2] <= solid_rect[0] or probe[0] >= solid_rect[2])
                if horizontal and float(solid_rect[1]) <= bbox[3] + 3.25:
                    return True
        except Exception:
            return False

        # A waterbird may also return to a real water surface.
        if record.locomotion == "waterbird" and state is not None:
            try:
                foot_ty = int((record.y + 2.0) // TILE_SIZE)
                return float(state.water_amount(tx, foot_ty)) > 0.025
            except Exception:
                pass
        return False

    def _occupied(self, record):
        bbox = self._spawn_bbox(record)
        try:
            player_box = self.player.bbox()
            if rects_overlap(bbox, player_box):
                return True
        except Exception:
            pass
        for entity in tuple(getattr(self.scene, "entities", ()) or ()):
            if getattr(entity,"background_only",False): continue
            if entity is record.entity:
                continue
            if not bool(getattr(entity, "active", False)):
                continue
            try:
                if rects_overlap(bbox, entity.bbox()):
                    return True
            except Exception:
                continue
        return False

    def _safe_reason(self, record):
        if self._player_too_close(record):
            return "player_near"
        if self._spawn_visible(record):
            return "camera_visible"
        if not self._terrain_safe(record):
            return "spawn_terrain_unsafe"
        if self._occupied(record):
            return "spawn_occupied"
        return ""

    # ------------------------------------------------------------------
    # Respawn execution
    # ------------------------------------------------------------------
    @staticmethod
    def _reset_runtime_fields(record):
        c = record.entity
        c.x = float(record.x)
        c.y = float(record.y)
        c.home_x = float(record.home_x)
        c.home_y = float(record.home_y)
        c.hp = float(record.max_hp)
        c.max_hp = float(record.max_hp)
        c.active = True
        c.death_processed = False
        c.vx = 0.0
        c.vy = 0.0
        c.grounded = record.locomotion not in (_FLYING_LOCOMOTION | _AQUATIC_LOCOMOTION | {"ceiling","ceiling_drop"})
        c.facing = 1
        c.behavior_state = "idle"
        c.player_detected = False
        c.provoked_by_player = False
        c.poison_dose = 0.0
        c.poisoned = False
        c.temperature_c = float(record.temperature_c)
        c.attack_rearm_required = False
        c.ai_action_id = ""
        c.ai_action_hit_done = False
        c.ai_action_cooldowns = {}
        c.animation_state_override = ""
        c._f99_drop = "hanging"
        c._f99_drop_age = 0.0
        c._f99_hop_slot = -1
        c.flight_mode = ""
        c.flight_target_x = float(record.x)
        c.flight_target_y = float(record.y)
        for name in (
            "attack_cooldown", "hurt_timer", "slow_timer", "stun_timer",
            "attack_anim_timer", "attack_recoil_timer", "motion_time",
            "visual_animation_time", "ai_action_elapsed", "ai_action_duration",
            "ai_action_effect_time", "out_of_water_time", "habitat_damage_clock",
            "flight_timer", "rest_timer",
            "lava_burn_timer",
        ):
            if hasattr(c, name):
                setattr(c, name, 0.0)
        c.ai_action_target_x = float(record.x)
        c.ai_action_target_y = float(record.y)

    def _respawn(self, record):
        # The ordinary engine keeps dead Creature objects in the scene.  Be
        # defensive if a custom system removed it, but never append duplicates.
        entities = getattr(self.scene, "entities", None)
        if isinstance(entities, list) and not any(obj is record.entity for obj in entities):
            if any(str(getattr(obj, "entity_id", "") or "") == record.entity_id for obj in entities):
                return False
            entities.append(record.entity)
        self._reset_runtime_fields(record)
        record.state = "alive"
        self._pending.pop(record.entity_id, None)
        return True

    def update(self, dt, population=None):
        dt = max(0.0, float(dt))
        self.clock += dt
        self._poll_accumulator += dt
        self._population_sync_accumulator += dt

        if population is not None and self._population_sync_accumulator >= self.population_sync_interval_seconds:
            self._population_sync_accumulator = 0.0
            self.register_population(population)

        if self._poll_accumulator < self.poll_interval_seconds:
            return 0
        self._poll_accumulator = min(self._poll_accumulator, self.poll_interval_seconds)
        self._poll_accumulator = 0.0

        checked = 0
        respawned = 0
        while (
            self._heap
            and self._heap[0][0] <= self.clock
            and checked < self.max_due_checks_per_tick
            and respawned < self.max_respawns_per_tick
        ):
            _due, generation, epoch, entity_id = heapq.heappop(self._heap)
            pending = self._pending.get(entity_id)
            if (
                pending is None
                or pending.generation != generation
                or pending.epoch != epoch
                or epoch != self._population_epoch
            ):
                continue
            checked += 1
            record = self._records.get(entity_id)
            if record is None or record.entity is None:
                self._pending.pop(entity_id, None)
                continue
            entity = record.entity
            if bool(getattr(entity, "active", False)) and float(getattr(entity, "hp", 0.0)) > 0.0:
                record.state = "alive"
                self._pending.pop(entity_id, None)
                continue

            reason = self._safe_reason(record)
            if reason:
                record.state = "blocked"
                self.last_blocked_reason = reason
                retry = pending.retries + 1
                # Tiny deterministic spreading prevents a whole biome from
                # retrying on one frame while keeping the delay predictable.
                spread = (sum(ord(ch) for ch in entity_id) % 7) * 0.11
                self._push_pending(
                    record,
                    self.retry_delay_seconds + spread,
                    retries=retry,
                    reason=reason,
                )
                continue

            if self._respawn(record):
                respawned += 1
        return respawned

    def reset_for_new_game(self, population=None, *, restore_population=True, map_token=None):
        """Clear all timers and optionally restore the authored population.

        A freshly constructed GameApp already supplies a fresh population, but
        this API also supports an in-place New Game implementation.
        """
        if population is not None:
            self.replace_population(population, map_token=map_token)
        else:
            self._population_epoch += 1
            if map_token is not None:
                self.map_token = str(map_token or "")
        # replace_population() intentionally observes dead actors for ordinary
        # map/load usage. New Game is different: no old death timer survives.
        self._population_epoch += 1
        self._pending.clear()
        self._heap[:] = []
        self.clock = 0.0
        self._poll_accumulator = 0.0
        self._population_sync_accumulator = 0.0
        if restore_population:
            for record in self._records.values():
                self._reset_runtime_fields(record)
                record.state = "alive"
        else:
            for record in self._records.values():
                entity = record.entity
                record.state = (
                    "alive"
                    if bool(getattr(entity, "active", False))
                    and float(getattr(entity, "hp", 0.0)) > 0.0
                    else "defeated"
                )

    def debug_snapshot(self):
        states = {}
        for record in self._records.values():
            states[record.state] = states.get(record.state, 0) + 1
        return {
            "map_token": self.map_token,
            "clock": float(self.clock),
            "population": len(self._records),
            "pending": len(self._pending),
            "states": states,
            "last_blocked_reason": self.last_blocked_reason,
        }
