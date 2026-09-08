# -*- coding: utf-8 -*-
"""Bounded Terraria-style grappling hook runtime.

The system owns exactly one permanently allocated hook slot.  It deliberately
does not create projectile objects, particles, rope segments, or callbacks in
the 60 Hz path; Pyto/iOS can therefore leave a hook attached indefinitely
without growing Python or UIKit state.

Coordinates use the game's normal convention (positive Y points down).  The
hook is fired toward a world-space target, ray-swept against real fractional
tile geometry, then shortens its rope while preserving tangential player
velocity.  That combination gives a useful pull plus a natural pendulum swing
without replacing the authoritative TilePhysics collision resolver.
"""
import math

from config import TILE_SIZE
from world.tile_registry import AIR, ICE, STONE, tile_def


class GrappleSystem:
    """One-slot hook state machine with a bounded, allocation-stable update."""

    IDLE = "idle"
    EXTENDING = "extending"
    LATCHED = "latched"
    RETRACTING = "retracting"

    # Public budget contract used by validation/debug UI.
    MAX_ACTIVE_HOOKS = 1
    POOL_CAPACITY = 1

    DEFAULT_MAX_RANGE_TILES = 9.0
    DEFAULT_HOOK_SPEED = 920.0
    DEFAULT_RETRACT_SPEED = 1180.0
    DEFAULT_REEL_SPEED = 235.0
    DEFAULT_PULL_ACCEL = 2350.0
    DEFAULT_PULL_SPEED = 520.0
    DEFAULT_BREAK_RANGE_SCALE = 1.20

    def __init__(
        self,
        player,
        world,
        events=None,
        grappleable_predicate=None,
        max_range_tiles=DEFAULT_MAX_RANGE_TILES,
    ):
        self.player = player
        self.world = world
        self.events = events
        self.grappleable_predicate = grappleable_predicate

        self.max_range = max(
            float(TILE_SIZE) * 2.0,
            float(TILE_SIZE) * float(max_range_tiles),
        )
        self.break_range = self.max_range * self.DEFAULT_BREAK_RANGE_SCALE
        self.hook_speed = float(self.DEFAULT_HOOK_SPEED)
        self.retract_speed = float(self.DEFAULT_RETRACT_SPEED)
        self.reel_speed = float(self.DEFAULT_REEL_SPEED)
        self.pull_accel = float(self.DEFAULT_PULL_ACCEL)
        self.max_pull_speed = float(self.DEFAULT_PULL_SPEED)
        self.min_rope_length = float(TILE_SIZE) * 1.10
        self.collision_step = max(3.0, float(TILE_SIZE) * 0.125)

        # The slot is reset in-place for the lifetime of this object.
        self.state = self.IDLE
        self.hook_x = 0.0
        self.hook_y = 0.0
        self.prev_hook_x = 0.0
        self.prev_hook_y = 0.0
        self.hook_vx = 0.0
        self.hook_vy = 0.0
        self.travel_distance = 0.0
        self.rope_length = 0.0
        self.anchor_tx = -1
        self.anchor_ty = -1
        self.anchor_tile_id = AIR
        # Retained collision scratch; the flight loop never returns/allocates
        # a per-sample hit tuple.
        self._hit_tx = -1
        self._hit_ty = -1
        self._hit_tile_id = AIR
        self.enabled = True
        self.serial = 0
        self.last_detach_reason = ""
        self._fire_world_wrap_serial = int(
            getattr(self.player, "world_wrap_serial", 0)
        )

        # Mutated in place.  Render integration may call render_state() and
        # copy these scalars into its already-existing frame snapshot.
        self._render_state = {
            "visible": False,
            "state": self.IDLE,
            "serial": 0,
            "rope_x1": 0.0,
            "rope_y1": 0.0,
            "rope_x2": 0.0,
            "rope_y2": 0.0,
            "hook_x": 0.0,
            "hook_y": 0.0,
            "prev_hook_x": 0.0,
            "prev_hook_y": 0.0,
            "anchor_tx": -1,
            "anchor_ty": -1,
            "rope_length": 0.0,
            "max_range": float(self.max_range),
        }
        self._sync_render_state()

    # ------------------------------------------------------------------
    # Public state / lifecycle
    # ------------------------------------------------------------------

    @property
    def active(self):
        return self.state != self.IDLE

    @property
    def attached(self):
        return self.state == self.LATCHED

    @property
    def active_count(self):
        return 0 if self.state == self.IDLE else 1

    @property
    def pool_capacity(self):
        return self.POOL_CAPACITY

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled and self.active:
            self.detach("mode_switch", retract=False)
        return self.enabled

    def rebind(self, player=None, world=None, events=None):
        """Rebind after a map adoption and discard the old-world anchor."""
        self.detach("world_rebind", retract=False)
        if player is not None:
            self.player = player
        if world is not None:
            self.world = world
        if events is not None:
            self.events = events
        self._sync_render_state()

    def reset(self):
        """Hard reset used by load/new-game/portal transient cleanup."""
        self._set_idle("reset")

    # Compatibility names for a standalone GameApp-owned system.
    reset_transient = reset

    def trigger(self, target_x, target_y):
        """Fire an idle hook; a second trigger retracts the current hook."""
        if not self.enabled:
            return False
        if self.active:
            self.detach("action_pressed_again", retract=True)
            return False
        return self.fire_at(target_x, target_y)

    def fire_at(self, target_x, target_y):
        if not self.enabled or self.active:
            return False

        origin_x = float(self.player.x)
        origin_y = float(self.player.y) - float(self.player.height()) * 0.45
        try:
            target_x = float(target_x)
            target_y = float(target_y)
        except Exception:
            return False
        if not (
            math.isfinite(origin_x)
            and math.isfinite(origin_y)
            and math.isfinite(target_x)
            and math.isfinite(target_y)
        ):
            return False
        dx = target_x - origin_x
        dy = target_y - origin_y
        length = math.hypot(dx, dy)
        if length < 1e-7:
            dx = float(getattr(self.player, "facing", 1) or 1)
            dy = 0.0
            length = 1.0
        nx = dx / length
        ny = dy / length

        # Spawn just outside the torso so a downward shot may reach the floor,
        # while a sideways shot cannot immediately collide with it.
        muzzle = max(7.0, min(float(TILE_SIZE) * 0.42, float(self.player.width()) * 0.48))
        self.hook_x = origin_x + nx * muzzle
        self.hook_y = origin_y + ny * muzzle
        self.prev_hook_x = self.hook_x
        self.prev_hook_y = self.hook_y
        self.hook_vx = nx * self.hook_speed
        self.hook_vy = ny * self.hook_speed
        self.travel_distance = muzzle
        self.rope_length = 0.0
        self.anchor_tx = -1
        self.anchor_ty = -1
        self.anchor_tile_id = AIR
        self.state = self.EXTENDING
        self.last_detach_reason = ""
        self._fire_world_wrap_serial = int(
            getattr(self.player, "world_wrap_serial", 0)
        )
        self.serial += 1
        self._emit("grapple_fired")
        self._sync_render_state()
        return True

    # A concise alias for integrations that name the operation ``fire``.
    fire = fire_at

    def release_action(self):
        """Release hook on action-button up (integration hook for GameApp)."""
        if not self.active:
            return False
        self.detach("action_released", retract=True)
        return True

    def detach(self, reason="manual", retract=True):
        if not self.active:
            return False
        self.last_detach_reason = str(reason or "manual")
        self.anchor_tx = -1
        self.anchor_ty = -1
        self.anchor_tile_id = AIR
        self.rope_length = 0.0
        if bool(retract) and self.state != self.IDLE:
            self.state = self.RETRACTING
            self.serial += 1
            self._emit("grapple_detached", reason=self.last_detach_reason)
            self._sync_render_state()
        else:
            self._set_idle(self.last_detach_reason, emit=True)
        return True

    # ------------------------------------------------------------------
    # Fixed update
    # ------------------------------------------------------------------

    def update(self, dt):
        """Advance the single slot with a clamped stall-safe timestep."""
        try:
            step_dt = float(dt)
        except Exception:
            step_dt = 0.0
        if not math.isfinite(step_dt):
            step_dt = 0.0
        step_dt = max(0.0, min(0.05, step_dt))

        if not self.enabled:
            if self.active:
                self.detach("disabled", retract=False)
            return
        current_wrap_serial = int(getattr(self.player, "world_wrap_serial", 0))
        if current_wrap_serial != int(self._fire_world_wrap_serial):
            if self.active:
                self.detach("world_wrap", retract=False)
            self._fire_world_wrap_serial = current_wrap_serial
            return
        if self.state == self.IDLE or step_dt <= 0.0:
            self._sync_render_state()
            return
        self.prev_hook_x = float(self.hook_x)
        self.prev_hook_y = float(self.hook_y)
        if self.state == self.EXTENDING:
            self._update_extending(step_dt)
        elif self.state == self.LATCHED:
            self._update_latched(step_dt)
        elif self.state == self.RETRACTING:
            self._update_retracting(step_dt)
        else:
            self._set_idle("invalid_state")
        self._sync_render_state()

    def _update_extending(self, dt):
        old_x = self.hook_x
        old_y = self.hook_y
        dx = self.hook_vx * dt
        dy = self.hook_vy * dt
        segment_length = math.hypot(dx, dy)
        if segment_length <= 1e-9:
            self.detach("stalled", retract=True)
            return

        steps = max(1, int(math.ceil(segment_length / self.collision_step)))
        for index in range(1, steps + 1):
            ratio = index / float(steps)
            sample_x = old_x + dx * ratio
            sample_y = old_y + dy * ratio
            if self._probe_grappleable_hit(sample_x, sample_y):
                self._latch(
                    self._hit_tx,
                    self._hit_ty,
                    self._hit_tile_id,
                    sample_x,
                    sample_y,
                )
                return

        self.hook_x = old_x + dx
        self.hook_y = old_y + dy
        self.travel_distance += segment_length
        if self.travel_distance >= self.max_range - 1e-6:
            # Clamp presentation to the finite range before starting return.
            origin_x = float(self.player.x)
            origin_y = float(self.player.y) - float(self.player.height()) * 0.45
            ray_x = self.hook_x - origin_x
            ray_y = self.hook_y - origin_y
            ray_len = math.hypot(ray_x, ray_y)
            if ray_len > self.max_range and ray_len > 1e-7:
                scale = self.max_range / ray_len
                self.hook_x = origin_x + ray_x * scale
                self.hook_y = origin_y + ray_y * scale
            self.detach("max_range", retract=True)

    def _update_latched(self, dt):
        if not self._anchor_is_valid():
            self.detach("anchor_lost", retract=True)
            return

        origin_x = float(self.player.x)
        origin_y = float(self.player.y) - float(self.player.height()) * 0.45
        dx = self.hook_x - origin_x
        dy = self.hook_y - origin_y
        distance = math.hypot(dx, dy)
        if distance > self.break_range:
            self.detach("break_range", retract=True)
            return
        if distance < 1e-7:
            return

        self.rope_length = max(
            self.min_rope_length,
            float(self.rope_length) - self.reel_speed * dt,
        )
        nx = dx / distance
        ny = dy / distance

        player_vx = float(getattr(self.player, "vx", 0.0))
        player_vy = float(getattr(self.player, "vy", 0.0))
        inward_speed = player_vx * nx + player_vy * ny
        stretch = max(0.0, distance - self.rope_length)

        # A shrinking rope supplies a baseline reel velocity; extra stretch is
        # spring-like.  Only radial velocity is corrected, leaving tangential
        # movement intact so left/right input produces a real swing arc.
        if distance > self.min_rope_length * 0.96:
            target_inward = min(
                self.max_pull_speed,
                92.0 + self.reel_speed * 0.54 + stretch * 8.5,
            )
        else:
            # Do not keep accelerating through the anchor after the reel has
            # reached its useful close distance.
            target_inward = 0.0
        if inward_speed < target_inward:
            impulse = min(target_inward - inward_speed, self.pull_accel * dt)
            player_vx += nx * impulse
            player_vy += ny * impulse

        speed = math.hypot(player_vx, player_vy)
        speed_limit = self.max_pull_speed * 1.35
        if speed > speed_limit and speed > 1e-7:
            velocity_scale = speed_limit / speed
            player_vx *= velocity_scale
            player_vy *= velocity_scale

        self.player.vx = player_vx
        self.player.vy = player_vy
        if player_vy < -1.0:
            self.player.grounded = False
        if str(getattr(self.player, "state", "")) == "climb":
            self.player.state = "fall"
            self.player.climb = None
        if hasattr(self.player, "hover_active"):
            self.player.hover_active = False

    def _update_retracting(self, dt):
        origin_x = float(self.player.x)
        origin_y = float(self.player.y) - float(self.player.height()) * 0.45
        dx = origin_x - self.hook_x
        dy = origin_y - self.hook_y
        distance = math.hypot(dx, dy)
        travel = self.retract_speed * dt
        if distance <= max(5.0, travel):
            self._set_idle(self.last_detach_reason)
            return
        self.hook_x += dx / distance * travel
        self.hook_y += dy / distance * travel

    # ------------------------------------------------------------------
    # Collision and material policy
    # ------------------------------------------------------------------

    def is_tile_grappleable(self, tx, ty, tile_id=None):
        tx = int(tx)
        ty = int(ty)
        if not (0 <= tx < int(self.world.width_tiles) and 0 <= ty < int(self.world.height_tiles)):
            return False

        custom_solid = (tx, ty) in getattr(self.world, "custom_solid_cells", ())
        if tile_id is None:
            tile_id = int(self.world.get_tile(tx, ty))
        tile_id = int(tile_id)
        if custom_solid and tile_id == AIR:
            tile_id = STONE

        if callable(self.grappleable_predicate):
            try:
                decision = self.grappleable_predicate(tx, ty, tile_id)
            except TypeError:
                decision = self.grappleable_predicate(tile_id)
            if decision is not None:
                return bool(decision)

        td = tile_def(tile_id)
        explicit = getattr(td, "grappleable", None)
        if explicit is not None:
            return bool(explicit)
        if custom_solid:
            return True
        # Stable solid blocks are the normal Terraria target.  Wooden ladders
        # and authored vines may opt in through their existing ladder/material
        # metadata even though they are not full collision blocks.
        return bool(
            td.solid
            or (
                bool(getattr(td, "ladder", False))
                and str(getattr(td, "material_group", "")) == "wood"
            )
        )

    def _probe_grappleable_hit(self, x, y):
        self._hit_tx = -1
        self._hit_ty = -1
        self._hit_tile_id = AIR
        tx = int(math.floor(float(x) / float(TILE_SIZE)))
        ty = int(math.floor(float(y) / float(TILE_SIZE)))
        if not (0 <= tx < int(self.world.width_tiles) and 0 <= ty < int(self.world.height_tiles)):
            return False

        tile_id = int(self.world.get_tile(tx, ty))
        custom_solid = (tx, ty) in getattr(self.world, "custom_solid_cells", ())
        physical_id = STONE if custom_solid and tile_id == AIR else tile_id
        if not self.is_tile_grappleable(tx, ty, physical_id):
            return False

        td = tile_def(physical_id)
        left = tx * float(TILE_SIZE)
        top = ty * float(TILE_SIZE)
        right = left + float(TILE_SIZE)
        bottom = top + float(TILE_SIZE)

        if custom_solid:
            pass
        elif physical_id == ICE and hasattr(self.world, "ice_rect_at"):
            rect = self.world.ice_rect_at(tx, ty)
            if rect is None:
                return False
            left, top, right, bottom = (
                float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
            )
        elif bool(getattr(td, "one_way_platform", False)):
            bottom = top + float(TILE_SIZE) * 0.30
        elif bool(getattr(td, "solid", False)) and hasattr(self.world, "solid_top_y"):
            real_top = self.world.solid_top_y(tx, ty)
            if real_top is None:
                return False
            top = float(real_top)
        # Non-solid ladder/vine fallback deliberately uses the visible cell.

        if left <= float(x) <= right and top <= float(y) <= bottom:
            self._hit_tx = tx
            self._hit_ty = ty
            self._hit_tile_id = physical_id
            return True
        return False

    def _anchor_is_valid(self):
        tx = int(self.anchor_tx)
        ty = int(self.anchor_ty)
        if tx < 0 or ty < 0:
            return False
        # Re-test the exact impact point.  If a LOW/MID layer is mined away,
        # the tile ID can remain solid while the rope's actual attachment
        # pixel has become empty space.
        return bool(
            self._probe_grappleable_hit(self.hook_x, self.hook_y)
            and int(self._hit_tx) == tx
            and int(self._hit_ty) == ty
        )

    def _latch(self, tx, ty, tile_id, impact_x, impact_y):
        self.anchor_tx = int(tx)
        self.anchor_ty = int(ty)
        self.anchor_tile_id = int(tile_id)
        self.hook_x = float(impact_x)
        self.hook_y = float(impact_y)
        self.hook_vx = 0.0
        self.hook_vy = 0.0
        origin_x = float(self.player.x)
        origin_y = float(self.player.y) - float(self.player.height()) * 0.45
        self.rope_length = max(
            self.min_rope_length,
            min(self.max_range, math.hypot(self.hook_x - origin_x, self.hook_y - origin_y)),
        )
        self.state = self.LATCHED
        self.serial += 1
        self._emit("grapple_latched", tx=self.anchor_tx, ty=self.anchor_ty, tile_id=self.anchor_tile_id)

    # ------------------------------------------------------------------
    # Render/event helpers
    # ------------------------------------------------------------------

    def render_state(self, out=None):
        """Return/fill the bounded rope packet without constructing segments.

        With no argument the same retained dictionary is returned every time.
        A renderer that already owns a frame dictionary can pass it as ``out``
        and avoid even a shallow-copy allocation.
        """
        self._sync_render_state()
        if out is None:
            return self._render_state
        out.update(self._render_state)
        return out

    render_snapshot = render_state

    def _sync_render_state(self):
        origin_x = float(self.player.x)
        origin_y = float(self.player.y) - float(self.player.height()) * 0.45
        packet = self._render_state
        packet["visible"] = bool(self.state != self.IDLE)
        packet["state"] = str(self.state)
        packet["serial"] = int(self.serial)
        packet["rope_x1"] = origin_x
        packet["rope_y1"] = origin_y
        packet["rope_x2"] = float(self.hook_x)
        packet["rope_y2"] = float(self.hook_y)
        packet["hook_x"] = float(self.hook_x)
        packet["hook_y"] = float(self.hook_y)
        packet["prev_hook_x"] = float(self.prev_hook_x)
        packet["prev_hook_y"] = float(self.prev_hook_y)
        packet["anchor_tx"] = int(self.anchor_tx)
        packet["anchor_ty"] = int(self.anchor_ty)
        packet["rope_length"] = float(self.rope_length)
        packet["max_range"] = float(self.max_range)

    def _set_idle(self, reason="", emit=False):
        was_active = self.state != self.IDLE
        self.state = self.IDLE
        self.hook_vx = 0.0
        self.hook_vy = 0.0
        self.travel_distance = 0.0
        self.rope_length = 0.0
        self.anchor_tx = -1
        self.anchor_ty = -1
        self.anchor_tile_id = AIR
        if reason:
            self.last_detach_reason = str(reason)
        if was_active:
            self.serial += 1
            if emit:
                self._emit("grapple_detached", reason=self.last_detach_reason)
        self._sync_render_state()

    def _emit(self, name, **payload):
        if self.events is None:
            return
        try:
            self.events.emit(str(name), **payload)
        except Exception:
            pass
