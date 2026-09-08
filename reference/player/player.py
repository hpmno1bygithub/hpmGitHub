# -*- coding: utf-8 -*-
import math
from config import (
    TILE_SIZE,
    GRAVITY,
    RUN_SPEED,
    AIR_CONTROL,
    PLAYER_MOVE_ACCEL_GROUND,
    PLAYER_MOVE_DECEL_GROUND,
    PLAYER_MOVE_TURN_ACCEL,
    PLAYER_MOVE_ACCEL_AIR,
    PLAYER_MOVE_DECEL_AIR,
    JUMP_SPEED,
    PLAYER_SWIM_KICK_SPEED,
    PLAYER_SWIM_GRAVITY_SCALE,
    PLAYER_SWIM_VERTICAL_DRAG_PER_SECOND,
    PLAYER_SWIM_MAX_SINK_SPEED,
    PLAYER_SWIM_MAX_RISE_SPEED,
    PLAYER_LAVA_MOVE_SPEED_SCALE,
    PLAYER_LAVA_SUPPORT_MIN_DEPTH_PX,
    PLAYER_LAVA_SURFACE_IMMERSION_PX,
    PLAYER_LAVA_BUOYANCY_RISE_SPEED,
    CLIMB_SPEED,
    WALL_STAMINA_COST,
    STAMINA_RECOVERY,
    ROLL_SPEED,
    ROLL_TIME,
    MAX_HP,
    PLAYER_HP_REGEN_DELAY_SECONDS,
    MAX_STAMINA,
    MAX_MANA,
    WIND_MAX_SPEED,
    PLAYER_TAILWIND_SPEED_BONUS,
    PLAYER_HEADWIND_SPEED_PENALTY,
    PLAYER_GROUND_WIND_DRIFT,
    PLAYER_AIR_WIND_DRIFT,
    PLAYER_WIND_VERTICAL_ACCEL,
    PLAYER_TAILWIND_JUMP_BONUS,
    PLAYER_FIRE_LIFT_JUMP_BONUS,
    MAGIC_CHARGE_LEVEL1_MAX,
    MAGIC_CHARGE_LEVEL2_MAX,
    JUMP_MIN_INITIAL_SCALE,
    JUMP_HOLD_MAX_SECONDS,
    JUMP_HOLD_BASE_ACCEL,
    JUMP_HOLD_RAMP_ACCEL,
    JUMP_INPUT_BUFFER_SECONDS,
)
from engine.math2d import clamp, rects_overlap
from world.tile_registry import ICE
from player.action_profile import load_action_settings
from player.states import (
    STAND,
    CROUCH,
    PRONE,
    JUMP,
    FALL,
    ROLL,
    CLIMB,
)


class Player:
    HEIGHTS = {
        STAND: 54.0,
        CROUCH: 36.0,
        PRONE: 22.0,
        JUMP: 54.0,
        FALL: 54.0,
        ROLL: 24.0,
        CLIMB: 50.0,
    }

    WIDTHS = {
        STAND: 28.0,
        CROUCH: 30.0,
        PRONE: 46.0,
        JUMP: 28.0,
        FALL: 28.0,
        ROLL: 42.0,
        CLIMB: 28.0,
    }

    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.prev_x = float(x)
        self.prev_y = float(y)

        # V0.5.6.6 diagnostic-only movement snapshot.
        # This does not alter movement behavior.
        self.movement_debug = {
            "move": 0,
            "base_speed": 0.0,
            "effective_speed": 0.0,
            "wind_x": 0.0,
            "wind_drift": 0.0,
            "requested_vx": 0.0,
            "hit_horizontal": False,
        }

        self.state = STAND
        self.grounded = True
        self.facing = 1

        # FIX87: this flag is supplied by GameSettings, never by a save slot.
        # Keep HP writes authoritative here: DOT, old damage fallbacks and
        # loaded zero-HP saves must obey the same non-fatal policy.
        self.death_handling_enabled = True
        self.hp = MAX_HP
        self.stamina = MAX_STAMINA
        self.mana = MAX_MANA
        self.poison_dose = 0.0
        self.poisoned = False
        # FIX31: game startup may restore a save into an already-active world.
        # Damage systems respect this short grace timer; GameApp arms it after
        # map/save initialization so HP is not lost before control is visible.
        self.startup_damage_grace = 0.0

        # FIX65: movement has one default pace.  Keep the legacy field for
        # save/renderer compatibility, but it is always True and no longer has
        # an on-screen or input-driven toggle.
        self.run_mode = True

        # FIX18: editable gameplay-action profile.  The project-local JSON is
        # intentionally outside rpg_runtime.zip so the new action editor can
        # tune controls/physics without changing Python source.
        self.action_settings = load_action_settings()

        # Remember the normal posture that intentionally started a jump.
        # CROUCH/PRONE jumps now enter JUMP directly, and landing restores this
        # posture instead of forcing STAND.
        self.jump_return_posture = STAND

        self.roll_timer = 0.0
        # FIX16: roll is a temporary action layered on top of a posture.
        # Remember the posture that initiated the roll so standing, crouching
        # and prone/crawl all return to themselves after the roll finishes.
        self.roll_return_posture = STAND
        self.attack_timer = 0.0
        self.hurt_timer = 0.0
        self.hurt_invulnerability = 0.0
        self.health_regen_delay_remaining = 0.0
        self.map_speed_multiplier = 1.0
        self.attack_charge_seconds = 0.0
        self.attack_charge_level = 1

        # Continuous variable-jump state.
        self.jump_hold_elapsed = 0.0
        self.jump_hold_target = 0.0
        self.jump_environment_scale = 1.0
        self.jump_buffer_remaining = 0.0
        self.shallow_water_jump_boost_time = 0.0

        # FIX14 authored jump-event synchronization. A grounded jump may enter
        # the jump animation while still planted; the vertical impulse is only
        # applied when the authored ``takeoff`` frame is reached.
        self.jump_preparing = False
        self.jump_prepare_elapsed = 0.0
        self.jump_takeoff_delay_seconds = 0.0
        self.jump_hold_input_offset = 0.0

        self.climb = None
        # FIX84: detached ladders must not immediately recapture a held
        # diagonal stick. These are transient input guards, never save data.
        self.reset_climb_regrab()
        self.lava_supported = False
        # FIX39 semantic motion-audio counters. AudioManager polls these instead
        # of doing expensive duplicate input/physics work on the UIKit thread.
        self.fully_submerged = False
        self.jump_audio_serial = 0
        self.roll_audio_serial = 0
        self.swim_audio_serial = 0
        self.landing_audio_serial = 0
        self.last_landing_speed = 0.0
        # FIX45 horizontal world-cylinder handoff. GameApp observes the serial
        # to snap camera/interpolation without mistaking the wrap for a
        # thirty-thousand-pixel movement frame.
        self.world_wrap_serial = 0
        self.world_wrap_direction = 0

        # FIX67 equipment movement contract.  The equipment system owns the
        # slot selection and calls ``set_equipment_abilities`` only when its
        # equipped IDs change.  Player owns the short-lived physics state so a
        # UIKit inventory refresh can never create or consume an air action.
        self.feet_equipment_id = ""
        self.body_equipment_id = ""
        self.head_equipment_id = ""
        self.double_jump_used = False
        self.air_dash_used = False
        self.air_dash_active = False
        self.hover_active = False
        self.hover_used = False
        self.hover_time_remaining = 0.0
        # EquipmentSystem binds these lightweight gameplay hooks.  Keeping the
        # references on Player lets every damage source and the fixed-step
        # movement loop share one authoritative shield/footwear path without
        # importing inventory code into the physics entity.
        self.equipment_system = None
        self.equipment_damage_resolver = None
        self.shield_armor_hp = 0.0
        self.shield_armor_max_hp = 0.0
        self.shield_armor_active = False
        self.double_jump_serial = 0
        self.air_dash_serial = 0
        self.hover_toggle_serial = 0
        self.shield_block_timer = 0.0
        self.shield_block_serial = 0
        self.shield_block_side = 0
        self.shield_block_strength = 0.0

    # --------------------------------------------------------
    # Equipment-driven movement
    # --------------------------------------------------------

    def set_equipment_abilities(
        self,
        feet_item_id="",
        body_item_id="",
        head_item_id="",
    ):
        """Synchronize equipped slot IDs without coupling Player to inventory.

        The three stable feet IDs are:
        ``equipment.double_jump_shoes``, ``equipment.air_dash_shoes`` and
        ``equipment.hover_shoes``.  Body/head are retained here for renderer
        and combat-system synchronization, but do not alter movement.
        """
        feet_item_id = str(feet_item_id or "")
        body_item_id = str(body_item_id or "")
        head_item_id = str(head_item_id or "")
        feet_changed = feet_item_id != str(getattr(self, "feet_equipment_id", ""))
        body_changed = body_item_id != str(getattr(self, "body_equipment_id", ""))
        head_changed = head_item_id != str(getattr(self, "head_equipment_id", ""))

        self.feet_equipment_id = feet_item_id
        self.body_equipment_id = body_item_id
        self.head_equipment_id = head_item_id

        if feet_changed:
            # Never carry a previous shoe's one-use state or suspended gravity
            # into another shoe.  A newly equipped shoe becomes usable after a
            # real grounded frame, preventing mid-air inventory swap exploits.
            airborne = not bool(getattr(self, "grounded", False))
            was_air_dash = bool(getattr(self, "air_dash_active", False))
            self.double_jump_used = airborne
            self.air_dash_used = airborne
            self.air_dash_active = False
            self.hover_active = False
            if was_air_dash and self.state == ROLL:
                self.roll_timer = 0.0
                self.vx = 0.0
                self.state = FALL if airborne else STAND
        if body_changed:
            self.shield_block_timer = 0.0
            self.shield_block_side = 0
            self.shield_block_strength = 0.0
        return bool(feet_changed or body_changed or head_changed)

    def reset_equipment_motion_state(self):
        """Clear transient shoe state after load/teleport or hard recovery."""
        was_air_dash = bool(getattr(self, "air_dash_active", False))
        self.double_jump_used = False
        self.air_dash_used = False
        self.air_dash_active = False
        self.hover_active = False
        self.hover_used = False
        self.hover_time_remaining = 0.0
        self.shield_block_timer = 0.0
        self.shield_block_side = 0
        self.shield_block_strength = 0.0
        if was_air_dash and self.state == ROLL:
            self.roll_timer = 0.0
            self.vx = 0.0
            self.state = STAND if self.grounded else FALL

    @property
    def hp(self):
        return self._hp

    @hp.setter
    def hp(self, value):
        value = float(value)
        # Preserve legacy HP semantics when death is enabled. When disabled,
        # even direct HP writes cannot expose zero HP to another subsystem.
        self._hp = value if getattr(self, "death_handling_enabled", True) else max(1.0, value)

    def configure_death_handling(self, enabled):
        """Apply a session setting, including recovery of legacy zero-HP saves.

        This is not full healing, resurrection or invulnerability: armor still
        wears and normal hits still reduce HP, only the final 1 HP is protected.
        """
        self.death_handling_enabled = bool(enabled)
        self.hp = self.hp

    def take_damage(self, damage, source_x=None, source_y=None, kind="damage", bypass_shield=False):
        """Apply one hit through the hidden shield-armor durability pool.

        The returned mapping always reports absorbed and remaining damage so
        callers can retain their own hit-stun, knockback and VFX policy.  This
        method is intentionally tiny: EquipmentSystem owns armor inventory and
        persistence, while Player remains authoritative for HP.
        """
        try:
            incoming = max(0.0, float(damage))
        except (TypeError, ValueError):
            incoming = 0.0
        result = {
            "incoming_damage": incoming,
            "absorbed_damage": 0.0,
            "remaining_damage": incoming,
            "blocked": False,
            "shield_broken": False,
        }
        resolver = getattr(self, "equipment_damage_resolver", None)
        if callable(resolver) and incoming > 0.0:
            try:
                resolved = resolver(
                    incoming,
                    player=self,
                    player_max_hp=MAX_HP,
                    source_x=source_x,
                    source_y=source_y,
                    kind=kind,
                    bypass_shield=bool(bypass_shield),
                )
                if isinstance(resolved, dict):
                    result.update(resolved)
            except Exception:
                pass
        try:
            remaining = max(0.0, float(result.get("remaining_damage", incoming)))
        except (TypeError, ValueError):
            remaining = incoming
        attempted_hp = float(self.hp) - remaining
        self.hp = max(0.0, attempted_hp)
        prevented = bool(not self.death_handling_enabled and remaining > 0.0 and attempted_hp < 1.0)
        if prevented:
            # Repeated hazards at 1 HP are still hits; they must not count as
            # a quiet period for natural recovery. Keep armor/VFX results intact.
            self.health_regen_delay_remaining = max(
                float(getattr(self, "health_regen_delay_remaining", 0.0)),
                float(PLAYER_HP_REGEN_DELAY_SECONDS),
            )
        result["fatal_damage_prevented"] = prevented
        result["remaining_damage"] = remaining
        result["hp_after"] = float(self.hp)
        return result

    def try_block_creature_attack(
        self,
        source_x,
        source_y,
        damage=0.0,
        kind="contact",
    ):
        """Resolve one creature hit against the two-sided shield armor.

        CreatureSystem remains authoritative about whether a hit reached the
        player. Once it calls this helper, equipped shield armor blocks the hit
        completely and publishes a compact renderer pulse on the impact side.
        """
        if self.body_equipment_id != "equipment.shield_armor":
            return False
        try:
            source_x = float(source_x)
        except Exception:
            source_x = float(self.x)
        try:
            damage = max(0.0, float(damage))
        except Exception:
            damage = 0.0

        delta_x = source_x - float(self.x)
        self.shield_block_side = (
            1 if delta_x > 0.5 else (-1 if delta_x < -0.5 else int(self.facing or 1))
        )
        self.shield_block_strength = max(0.35, min(1.0, damage / 36.0))
        self.shield_block_timer = max(float(self.shield_block_timer), 0.22)
        self.shield_block_serial = int(getattr(self, "shield_block_serial", 0)) + 1
        return True

    def _reset_landed_equipment_motion(self):
        """Re-arm one-use air actions and cancel hover on physical support."""
        self.double_jump_used = False
        self.air_dash_used = False
        self.air_dash_active = False
        self.hover_active = False

    def _air_jump_impulse(self):
        """Apply a clean second jump without replaying grounded anticipation."""
        jump_speed = float(self.action_settings.get("jump_speed", JUMP_SPEED))
        jump_height = max(24.0, float(self.action_settings.get("jump_height_px", 48.0)))
        height_speed = math.sqrt(max(0.0, 2.0 * float(GRAVITY) * jump_height))
        launch_speed = max(jump_speed * JUMP_MIN_INITIAL_SCALE, height_speed)
        scale = max(0.65, min(1.35, float(self.jump_environment_scale)))
        self.vy = -launch_speed * scale
        self.state = JUMP
        self.grounded = False
        self.jump_preparing = False
        self.jump_prepare_elapsed = 0.0
        self.jump_hold_input_offset = 0.0
        self.jump_hold_elapsed = 0.0
        self.jump_hold_target = 0.0
        self.jump_buffer_remaining = 0.0
        self.double_jump_used = True
        self.jump_audio_serial = int(getattr(self, "jump_audio_serial", 0)) + 1

    # --------------------------------------------------------
    # Body geometry
    # --------------------------------------------------------

    def width(self, state=None):
        return self.WIDTHS.get(state or self.state, 28.0)

    def height(self, state=None):
        return self.HEIGHTS.get(state or self.state, 54.0)

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        w = self.width(state)
        h = self.height(state)

        return (
            x - w / 2,
            y - h,
            x + w / 2,
            y,
        )

    def combat_bbox(self):
        """Current visible body outline used for contact damage.

        Kept separate from physics bbox so imported sprite assets can later
        supply a dedicated hurtbox without changing terrain collision.
        """
        return self.bbox()

    # --------------------------------------------------------
    # Posture
    # --------------------------------------------------------

    def _can_use_state(self, state, world):
        """Return True only when the requested body AABB is actually clear.

        FIX16.1 hotfix:
        ``solid_cells_in_rect`` is a broad-phase tile query.  With layered
        dirt/sand/rock, a player's feet can sit on a solid surface that starts
        part-way down the SAME tile cell.  Merely seeing that solid cell is not
        a collision; the candidate body must overlap the cell's *real* solid
        rectangle.  FIX16 forgot this narrow-phase test, which rejected every
        crouch/stand/prone transition on many layered surfaces and also forced
        completed rolls into PRONE.
        """
        box = self.bbox(state=state)

        for _tx, _ty, _tile_id, solid_rect in world.solid_cells_in_rect(
            box,
            padding=1,
        ):
            if rects_overlap(box, solid_rect):
                return False

        return True

    def set_posture(self, state, world, events):
        # FIX16 validates every normal posture transition, not only STAND.
        # PRONE -> CROUCH increases height, while CROUCH -> PRONE increases
        # width, so either direction can collide in a tight tunnel.
        if state in (STAND, CROUCH, PRONE) and not self._can_use_state(state, world):
            text = {
                STAND: "上方空間不足，不能站起",
                CROUCH: "空間不足，不能切換蹲姿",
                PRONE: "空間不足，不能切換趴姿",
            }.get(state, "空間不足，不能切換姿態")
            events.emit("message", text=text)
            return False

        self.state = state
        return True

    def _cycle_crouch_prone(self, world, events):
        """Cycle the dedicated crouch/prone button without standing up.

        FIX16 control contract:
            STAND  -> CROUCH
            CROUCH -> PRONE
            PRONE  -> CROUCH -> PRONE ...

        Standing up is intentionally NOT part of this button. FIX18 reserves
        move-up as the explicit stand-up command; Jump may launch directly from
        CROUCH/PRONE and can restore that posture on landing.
        """
        if self.state in (JUMP, FALL, ROLL, CLIMB):
            return False
        target = CROUCH if self.state in (STAND, PRONE) else PRONE
        return bool(self.set_posture(target, world, events))

    def _try_stand_from_posture(self, world, events):
        """Try to leave CROUCH/PRONE. Returns True only when standing fits."""
        if self.state not in (CROUCH, PRONE):
            return False
        return bool(self.set_posture(STAND, world, events))

    def _finish_roll_posture(self, world, events):
        """Restore the posture that existed immediately before a roll.

        A roll may travel under geometry where the original posture no longer
        fits. Prefer the exact saved posture, then degrade only to a safe compact
        posture instead of clipping the player into terrain.
        """
        target = self.roll_return_posture
        if target not in (STAND, CROUCH, PRONE):
            target = STAND

        if target == STAND:
            candidates = (STAND, CROUCH, PRONE)
        elif target == CROUCH:
            candidates = (CROUCH, PRONE, STAND)
        else:
            # PRONE is wider than CROUCH, so a narrow exit may accept crouch
            # even when the exact prone box no longer fits.
            candidates = (PRONE, CROUCH, STAND)

        for candidate in candidates:
            if self._can_use_state(candidate, world):
                self.state = candidate
                return candidate

        # The current roll box was collision-valid this frame. Keep the most
        # compact normal posture as a last-resort recovery rather than forcing
        # STAND and producing an overlap.
        self.state = PRONE
        return PRONE

    # --------------------------------------------------------
    # Climbing
    # --------------------------------------------------------

    def reset_climb_regrab(self):
        """Clear only temporary ladder input guards (new/load/changed world)."""
        self.ladder_regrab_cooldown = 0.0
        self._ladder_detach_world_id = None
        self._ladder_detach_tx = None
        self._ladder_vertical_released = False
        self._ladder_top_platform = None

    def _block_ladder_regrab(self, world, info):
        self._ladder_top_platform = None
        if world is None or not info or info.get("type") != "ladder":
            return
        self.ladder_regrab_cooldown = 0.30
        self._ladder_detach_world_id = id(world)
        self._ladder_detach_tx = int(info["tx"])
        self._ladder_vertical_released = False

    def _update_ladder_regrab(self, dt, world, inputs):
        """Cooldown plus intent latch, not just a timer that regrabs at apex.

        A sustained up/down input cannot re-grab overlapping ladders until
        the player has cleared the ladder area horizontally or deliberately
        recentred the vertical axis. Adjacent ladder columns must not capture
        the same exit gesture. The short grace also covers slow analog exits.
        """
        if (self._ladder_detach_world_id is not None
                and self._ladder_detach_world_id != id(world)):
            self.reset_climb_regrab()
            return
        self.ladder_regrab_cooldown = max(
            0.0, self.ladder_regrab_cooldown - max(0.0, float(dt)))
        tx = self._ladder_detach_tx
        if tx is None:
            return
        if abs(float(inputs.axis_y())) <= 0.12:
            self._ladder_vertical_released = True
        box = self.bbox(state=CLIMB)
        clear_of_column = (
            (box[2] < tx * TILE_SIZE - 1.0
             or box[0] > (tx + 1) * TILE_SIZE + 1.0)
            and world.ladder_cell_in_rect(box) is None)
        if clear_of_column or (self._ladder_vertical_released
                               and self.ladder_regrab_cooldown <= 1e-9):
            self._ladder_detach_tx = None

    def _try_start_climb(self, world, inputs, events, ladder_only=False,
                         allow_ladder=True):
        # FIX84: Jump is an exit/launch command, not implicit ladder entry.
        # Auto entry is requested by deliberate vertical input in update().
        ladder_cell = (world.ladder_cell_in_rect(self.bbox(state=CLIMB))
                       if allow_ladder else None)
        platform_entry = None
        if allow_ladder and float(inputs.axis_y()) > 0.12:
            platform_entry = world.ladder_entry_from_platform(self.bbox(state=CLIMB))
            if platform_entry is not None:
                ladder_cell = platform_entry
        ladder_allowed = (
            self.ladder_regrab_cooldown <= 1e-9
            and (self._ladder_detach_world_id != id(world)
                 or self._ladder_detach_tx is None))

        # Reversing Up -> Down after *walking onto this platform* is a fresh
        # descent intent. Jump exits retain FIX84's cooldown + neutral latch.
        if (platform_entry is not None and self.grounded
                and self._ladder_top_platform == (id(world),) + platform_entry):
            ladder_allowed = True
        if ladder_cell is not None and ladder_allowed and inputs.any_direction():
            tx, ty = ladder_cell
            self.climb = world.ladder_info(tx, ty)
            # A custom shaft must not snap the body sideways through a wall.
            if self.climb.get('custom_route'):
                candidate = self.bbox(x=self.climb['center_x'], state=CLIMB)
                if not self._custom_ladder_space_clear(world, candidate):
                    self.climb = None
                    return False
            self._ladder_top_platform = None
            self.jump_return_posture = STAND
            self.state = CLIMB
            self.grounded = False
            self.vx = 0.0
            self.vy = 0.0
            self.x = self.climb["center_x"]

            events.emit(
                "message",
                text="梯子攀爬：不消耗耐力",
            )
            return True

        if ladder_only:
            return False

        # Wall: jump while direction is pressed toward the wall.
        direction = inputs.axis_x()
        if direction != 0:
            wall_cell = world.wall_contact(
                self.bbox(state=STAND),
                direction,
            )

            if wall_cell is not None and self.stamina > 0:
                tx, ty = wall_cell
                self.climb = world.wall_info(
                    tx,
                    ty,
                    side=-1 if direction > 0 else 1,
                )
                self.jump_return_posture = STAND
                self.state = CLIMB
                self.grounded = False
                self.vx = 0.0
                self.vy = 0.0

                if direction > 0:
                    # Player is left of wall.
                    self.x = tx * TILE_SIZE - self.width(CLIMB) / 2 - 1
                else:
                    # Player is right of wall.
                    self.x = (tx + 1) * TILE_SIZE + self.width(CLIMB) / 2 + 1

                events.emit(
                    "message",
                    text="牆面攀爬：持續消耗耐力",
                )
                return True

        return False

    def _finish_climb_top(self, world, events):
        info = self.climb
        if info is None:
            return

        if info["type"] == "ladder":
            from systems.cloud_islands import ladder_landing
            landing=ladder_landing(self,world,top=True)
            target_x, target_y = landing if landing is not None else (info["center_x"], info["exit_y"])
            if info.get('custom_route'):
                candidate = self.bbox(x=target_x, y=target_y, state=STAND)
                if not self._custom_ladder_space_clear(world, candidate):
                    self.vy = 0.0
                    return
            self.x, self.y = target_x, target_y
            events.emit(
                "message",
                text="梯子登頂：已站上平台",
            )

        elif info["type"] == "wall":
            # FIX45: the old mantle blindly centered the player on the top
            # tile. On stepped structures an adjacent higher block could then
            # overlap the standing body, producing the pyramid-wall clipping
            # seen in the supplied recording. Test the complete destination
            # body first and fall back to the approach side; if neither fits,
            # leave climbing without ever writing an embedded position.
            target_y = float(info["top_y"])
            side = int(info.get("side", 0) or 0)
            tx = int(info.get("tx", 0) or 0)
            side_x = (
                tx * TILE_SIZE - self.width(STAND) * 0.5 - 1.0
                if side < 0
                else (tx + 1) * TILE_SIZE + self.width(STAND) * 0.5 + 1.0
            )
            candidates = (float(info["mantle_x"]), float(side_x))
            chosen = None
            for candidate_x in candidates:
                box = self.bbox(x=candidate_x, y=target_y, state=STAND)
                blocked = any(
                    rects_overlap(box, solid_rect)
                    for _tx, _ty, _tile_id, solid_rect
                    in world.solid_cells_in_rect(box, padding=1)
                )
                if not blocked:
                    chosen = candidate_x
                    break
            if chosen is None:
                events.emit("message", text="上方空間不足：已停止攀爬")
                self._leave_climb(False, world=world)
                return
            self.x = chosen
            self.y = target_y
            events.emit(
                "message",
                text="牆面登頂：已翻上平台",
            )

        self._block_ladder_regrab(world, info)
        if info.get('top_climb_platform'):
            self._ladder_top_platform = (id(world), int(info['tx']), int(info['top_row']))
        self.climb = None
        self.jump_return_posture = STAND
        self.state = STAND
        self.grounded = True
        self.vx = 0.0
        self.vy = 0.0

    def _leave_climb(self, jump=False, move_x=0.0, world=None):
        info = self.climb
        self._block_ladder_regrab(world, info)
        self.climb = None
        self.jump_return_posture = STAND
        self.state = JUMP if jump else FALL
        self.grounded = False
        # Do not reuse an old ground-jump anticipation/hold credit, and never
        # consume a footwear air action with this same physical Jump edge.
        self.jump_preparing = False
        self.jump_prepare_elapsed = 0.0
        self.jump_buffer_remaining = 0.0
        self.jump_hold_elapsed = 0.0
        self.jump_hold_target = 0.0
        self.jump_hold_input_offset = 0.0
        self.jump_environment_scale = 1.0

        if info and info.get("type") == "ladder":
            horizontal = clamp(float(move_x), -1.0, 1.0)
            speed = float(self.action_settings.get("run_speed", RUN_SPEED))
            speed *= max(0.10, float(self.map_speed_multiplier)) * AIR_CONTROL
            self.vx = horizontal * speed
            if abs(horizontal) > 0.05:
                self.facing = 1 if horizontal > 0.0 else -1

        if jump:
            self.vy = -JUMP_SPEED * 0.82
            self.jump_audio_serial = int(getattr(self, "jump_audio_serial", 0)) + 1

            if info and info["type"] == "wall":
                side = info.get("side", 0)

                if side == -1:
                    self.vx = -180.0
                elif side == 1:
                    self.vx = 180.0

    @staticmethod
    def _custom_ladder_space_clear(world, box):
        from world.tile_registry import tile_def
        if (box[0] < 0 or box[1] < 0 or box[2] > world.width_tiles * TILE_SIZE
                or box[3] > world.height_tiles * TILE_SIZE):
            return False
        for _tx, _ty, tile_id, rect in world.solid_cells_in_rect(box, padding=1):
            if tile_def(tile_id).one_way_platform:
                continue
            if rects_overlap(box, rect):
                return False
        return True

    def _update_climb(self, dt, world, inputs, events):
        info = self.climb
        if info is None:
            self.state = FALL
            return

        horizontal = float(inputs.axis_x())
        vertical = float(inputs.axis_y())
        if inputs.just_pressed("jump"):
            self._leave_climb(jump=True, move_x=horizontal, world=world)
            return

        if info.get('type') == 'ladder' and abs(horizontal) >= 0.30:
            from systems.cloud_islands import ladder_landing
            landing = ladder_landing(self, world, horizontal)
            if landing is not None:
                self._leave_climb(False, move_x=horizontal, world=world)
                self.x, self.y = landing
                self.state = STAND
                self.grounded = True
                self.vx = self.vy = 0.0
                events.emit('message', text='抵達雲端島嶼')
                return
            # Without a supported landing, retain the grip until Jump. The
            # two fingers do not arrive in the same physics frame: auto-drop
            # here would discard the ladder jump before the jump finger lands.
        vertical = inputs.axis_y()
        self.vy = vertical * CLIMB_SPEED
        if info.get('type') == 'ladder' and info.get('custom_route'):
            # Never keep an invisible grip after a vine/transition is removed.
            cell = world.ladder_cell_in_rect(self.bbox(state=CLIMB))
            if cell is None:
                top = (int(info['tx']), int(info['top_row']))
                if abs(self.y - info['top_y']) <= 4.0 and world.is_ladder_at(*top):
                    cell = top
                else:
                    self._leave_climb(False, move_x=horizontal, world=world)
                    return
            if info.get('custom_revision') != world.revision:
                info = world.ladder_info(int(info['tx']), int(cell[1]))
                # Even if the transition has just been removed, keep the safe
                # sweep for this grip until the player has left the shaft.
                info['custom_route'] = True
                self.climb = info
            # Substep against actual hard terrain, but not the authored
            # one-way support. A climb-platform is not a noclip switch.
            delta = self.vy * dt
            steps = max(1, int(abs(delta) / 5.0) + 1)
            for _ in range(steps):
                target_y = self.y + delta / steps
                box = self.bbox(x=info['center_x'], y=target_y, state=CLIMB)
                if not self._custom_ladder_space_clear(world, box):
                    self.vy = 0.0
                    if delta > 0.0:
                        from world.tile_registry import tile_def
                        floors = [float(rect[1]) for _tx, _ty, tid, rect
                                  in world.solid_cells_in_rect(box, padding=1)
                                  if not tile_def(tid).one_way_platform
                                  and rects_overlap(box, rect)
                                  and self.y <= float(rect[1]) + 0.75]
                        if floors:
                            self.y = min(floors)
                            self._leave_climb(False, move_x=horizontal, world=world)
                            self.state = STAND; self.grounded = True
                            self.vx = self.vy = 0.0
                            return
                    break
                self.y = target_y
        else:
            self.y += self.vy * dt

        if info["type"] == "ladder":
            # Keep player centered on ladder.
            self.x = info["center_x"]

            # Upward direction is negative axis_y.
            if vertical < 0 and self.y <= info["top_y"] + 4:
                self._finish_climb_top(world, events)
                return

            # Drop below ladder bottom.
            if self.y > info["bottom_y"] + 12:
                self._leave_climb(False, world=world)
                return

        elif info["type"] == "wall":
            self.stamina -= WALL_STAMINA_COST * dt

            if self.stamina <= 0:
                self.stamina = 0
                events.emit(
                    "message",
                    text="耐力耗盡：從牆上墜落",
                )
                self._leave_climb(False, world=world)
                return

            if vertical < 0 and self.y <= info["top_y"] + 5:
                self._finish_climb_top(world, events)
                return

            # Wall has finite bottom.
            if self.y > info["bottom_y"] + 10:
                self._leave_climb(False, world=world)
                return

    def _charge_level(
        self,
        seconds,
    ):
        seconds = max(
            0.0,
            float(
                seconds
            ),
        )

        if seconds < MAGIC_CHARGE_LEVEL1_MAX:
            return 1

        if seconds < MAGIC_CHARGE_LEVEL2_MAX:
            return 2

        return 3

    def _local_wind(
        self,
        environment,
    ):
        if environment is None:
            return (
                0.0,
                0.0,
            )

        try:
            return environment.wind_at_world(
                self.x,
                self.y,
            )
        except Exception:
            return (
                0.0,
                0.0,
            )

    def _fully_submerged(self, environment):
        """True only when the player's head and torso are inside free water."""
        if environment is None:
            return False
        water = getattr(environment, "water", None)
        honey = getattr(environment, "honey", None)
        if water is None and honey is None:
            return False
        try:
            box = self.bbox()
            head_y = float(box[1]) + 4.0
            chest_y = float(box[1]) + max(8.0, self.height() * 0.42)
            for liquid in (water,honey):
                if liquid is None:continue
                if (float(liquid.depth_at_world(self.x, head_y)) > 0.75
                        and float(liquid.depth_at_world(self.x, chest_y)) > 0.75):
                    return True
            return False
        except Exception:
            return False

    def _lava_foot_depth(self, environment):
        """Maximum real magma immersion around the lower body."""
        if environment is None:
            return 0.0
        lava=getattr(environment,"lava",None)
        if lava is None:
            return 0.0
        try:
            box=self.bbox()
            foot_y=float(box[3])-0.5
            xs=(
                self.x,
                float(box[0])+max(3.0,self.width()*0.22),
                float(box[2])-max(3.0,self.width()*0.22),
            )
            return max(float(lava.depth_at_world(px,foot_y)) for px in xs)
        except Exception:
            return 0.0

    def _apply_lava_surface_support(self, physics, environment, dt):
        """Dense-lava buoyancy: do not walk on submerged rock as dry ground.

        Lava remains passable and lethal.  When the feet are deeply immersed
        and the actor is not actively jumping upward, buoyancy raises the body
        toward the actual free surface through the normal collision resolver.
        This preserves ceilings/walls and avoids turning magma into an invisible
        solid tile.
        """
        if environment is None or self.state==CLIMB or float(self.vy)<-5.0:
            self.lava_supported=False
            return False
        lava=getattr(environment,"lava",None)
        if lava is None:
            self.lava_supported=False
            return False
        try:
            box=self.bbox()
            foot_y=float(box[3])-0.5
            candidates=[]
            support_threshold=(
                1.0
                if bool(getattr(self,"lava_supported",False))
                else float(PLAYER_LAVA_SUPPORT_MIN_DEPTH_PX)
            )
            for px in (self.x,float(box[0])+4.0,float(box[2])-4.0):
                depth=float(lava.depth_at_world(px,foot_y))
                if depth<support_threshold:
                    continue
                surface=lava.surface_at_world(px,foot_y)
                if surface is not None:
                    candidates.append((depth,float(surface)))
            if not candidates:
                self.lava_supported=False
                return False
            # Use the deepest supporting sample so a cell edge cannot make the
            # actor suddenly fall to the submerged rock floor.
            _depth,surface=max(candidates,key=lambda row:row[0])
            target_foot=surface+float(PLAYER_LAVA_SURFACE_IMMERSION_PX)
            lift=max(0.0,float(self.y)-target_foot)
            if lift>0.05:
                step=min(lift,max(0.5,float(PLAYER_LAVA_BUOYANCY_RISE_SPEED)*max(0.0,float(dt))))
                physics.move_vertical(self,-step)
            remaining=max(0.0,float(self.y)-target_foot)
            # Keep the liquid-support latch while rising so the threshold does
            # not oscillate between 5 px target immersion and the 8 px initial
            # pickup depth on consecutive frames.
            self.lava_supported=True
            if remaining<=0.85:
                self.y=min(float(self.y),target_foot)
                self.vy=0.0
                self.grounded=True
            else:
                # The old rock support is no longer authoritative while a deep
                # magma column is actively lifting the body.
                self.grounded=False
            return True
        except Exception:
            self.lava_supported=False
            return False

    def _ground_support_is_ice(self, world):
        try:
            box = self.bbox()
            probe = (box[0] + 2, box[3], box[2] - 2, box[3] + 4)
            for _tx, _ty, tile_id, rect in world.solid_cells_in_rect(probe, padding=1):
                if int(tile_id) != ICE:
                    continue
                if abs(float(box[3]) - float(rect[1])) <= 4.0:
                    return True
        except Exception:
            return False
        return False

    # --------------------------------------------------------
    # Authored jump event synchronization (FIX14)
    # --------------------------------------------------------

    def configure_jump_takeoff_delay(self, seconds):
        try:seconds=float(seconds)
        except Exception:seconds=0.0
        self.jump_takeoff_delay_seconds=max(0.0,min(0.75,seconds))

    def _commit_jump_takeoff(self):
        """Apply the real ground-jump impulse at the animation event frame."""
        if not self.jump_preparing:
            return False
        self.jump_hold_input_offset=max(0.0,float(self.jump_prepare_elapsed))
        self.jump_preparing=False
        self.state=JUMP
        jump_speed = float(self.action_settings.get("jump_speed", JUMP_SPEED))
        jump_height = max(24.0, float(self.action_settings.get("jump_height_px", 48.0)))
        # FIX29: the editor exposes the intended minimum jump apex directly.
        # Keep jump_speed for backward-compatible tuning, but never let the
        # launch impulse fall below the ballistic speed needed for jump_height.
        height_speed = math.sqrt(max(0.0, 2.0 * float(GRAVITY) * jump_height))
        launch_speed = max(jump_speed * JUMP_MIN_INITIAL_SCALE, height_speed)
        self.vy=(
            -launch_speed
            * self.jump_environment_scale
        )
        self.grounded=False
        self.jump_audio_serial = int(getattr(self, "jump_audio_serial", 0)) + 1
        return True

    def _effective_jump_hold_seconds(self, raw_seconds):
        # The crouch / anticipation frames before TAKEOFF should not consume
        # the variable-height hold window. A quick tap during anticipation still
        # produces the minimum jump; holding after launch adds lift as before.
        try:raw=max(0.0,float(raw_seconds))
        except Exception:raw=0.0
        return max(0.0,raw-float(self.jump_hold_input_offset))

    # --------------------------------------------------------
    # Main update
    # --------------------------------------------------------

    def update(
        self,
        dt,
        world,
        physics,
        inputs,
        events,
        environment=None,
    ):
        # Render interpolation snapshot. Physics remains authoritative at
        # 60 Hz; Metal can interpolate between these two complete states.
        self.prev_x = float(self.x)
        self.prev_y = float(self.y)
        self._update_ladder_regrab(dt, world, inputs)

        if self.attack_timer > 0:
            self.attack_timer = max(0.0, self.attack_timer - dt)
        if self.hurt_timer > 0:
            self.hurt_timer = max(0.0, self.hurt_timer - dt)
        if self.hurt_invulnerability > 0:
            self.hurt_invulnerability = max(0.0, self.hurt_invulnerability - dt)
        if self.shield_block_timer > 0.0:
            self.shield_block_timer = max(0.0, self.shield_block_timer - dt)
            if self.shield_block_timer <= 0.0:
                self.shield_block_side = 0
                self.shield_block_strength = 0.0

        # FIX83: body wings supply collision-aware acceleration before ordinary
        # gravity/climbing. Shoe abilities remain unchanged when wings are off.
        equipment = getattr(self, "equipment_system", None)
        if callable(getattr(equipment, "update_wing_flight", None)) and equipment.update_wing_flight(self, dt, physics, inputs, world):
            return

        # FIX16: ``蹲 / 趴`` is an edge-triggered posture cycle. It no longer
        # depends on touch duration, so the first tap crouches and the next tap
        # goes prone immediately. From prone, further taps alternate prone and
        # crouch until the player explicitly stands with Up.
        if inputs.just_pressed("crouch"):
            self._cycle_crouch_prone(world, events)

        # FIX65: old saves/input managers may still contain a run_mode value,
        # but gameplay always uses the authored run speed.
        self.run_mode = True

        # Up is the non-jumping stand-up command. Consume nothing else: after
        # standing, ordinary horizontal input continues normally.
        if (
            bool(self.action_settings.get("up_restores_stand", True))
            and self.grounded
            and self.state in (CROUCH, PRONE)
            and inputs.just_pressed("move_up")
        ):
            self._try_stand_from_posture(world, events)

        # V0.6.5: action routing moved to GameApp and is edge-triggered.
        # Player no longer emits a repeated attack event while the button is
        # held; this also cleanly separates aim direction from action choice.

        # FIX84: a climb exit integrates ordinary movement in this same
        # frame, before the next vertical input can attempt another grab.
        # Preserve the edge-consumed flag so the same Jump cannot also trigger
        # double-jump/hover shoes, a wall grab, or a second jump impulse.
        climb_exit_this_frame = False
        if self.state == CLIMB:
            self._update_climb(dt, world, inputs, events)
            if self.state == CLIMB or self.grounded:
                return
            climb_exit_this_frame = True

        # Stamina recovers outside wall climb.
        if self.stamina < MAX_STAMINA:
            self.stamina = min(
                MAX_STAMINA,
                self.stamina + STAMINA_RECOVERY * dt,
            )

        wind_x, wind_y = (
            self._local_wind(
                environment
            )
        )
        fully_submerged = self._fully_submerged(environment)
        self.fully_submerged = bool(fully_submerged)

        # Physical support is the only ordinary re-arm point for one-use air
        # actions. Water keeps its existing repeatable swim-kick behavior and
        # cannot be combined with hover gravity suppression.
        if self.grounded:
            self._reset_landed_equipment_motion()
        elif fully_submerged:
            self.hover_active = False

        # Advance an already-started anticipation phase before reading a new
        # input edge. GameApp's animation clock advances by the same fixed dt,
        # so the impulse lands on the authored TAKEOFF frame rather than when
        # the button was first pressed.
        if self.jump_preparing:
            self.jump_prepare_elapsed += max(0.0,float(dt))
            if self.jump_prepare_elapsed + 1e-9 >= self.jump_takeoff_delay_seconds:
                self._commit_jump_takeoff()

        # Jump request / buffer.
        #
        # A visible press should never be lost simply because the grounded
        # flag settles one or two physics frames later. The input edge is
        # buffered briefly, then consumed exactly once.
        self.jump_buffer_remaining = max(
            0.0,
            self.jump_buffer_remaining
            - dt,
        )

        jump_edge = (inputs.just_pressed("jump")
                     and not climb_exit_this_frame)
        swim_kick = False

        # Feet equipment consumes an airborne Jump edge before the historical
        # jump buffer/climb routing.  The initial grounded jump can therefore
        # never accidentally spend the shoe ability in the same physics tick.
        if (
            jump_edge
            and not self.grounded
            and not fully_submerged
            and self.state != CLIMB
        ):
            equipment = getattr(self, "equipment_system", None)
            outcome = {"consumed": False}
            if equipment is not None and hasattr(equipment, "handle_airborne_jump"):
                try:
                    outcome = equipment.handle_airborne_jump(
                        self,
                        move_x=inputs.axis_x(),
                        move_y=inputs.axis_y(),
                        fully_submerged=fully_submerged,
                        climbing=False,
                    )
                except Exception:
                    outcome = {"consumed": False}
            if bool(outcome.get("consumed", False)):
                self.jump_buffer_remaining = 0.0
                self.jump_preparing = False
                self.jump_prepare_elapsed = 0.0
                self.jump_hold_elapsed = 0.0
                self.jump_hold_target = 0.0
                if bool(getattr(self, "hover_active", False)) and self.state not in (CROUCH, PRONE):
                    self.state = FALL
                jump_edge = False
            elif (
                self.feet_equipment_id == "equipment.double_jump_shoes"
                and not self.double_jump_used
                and not self.air_dash_active
            ):
                self._air_jump_impulse()
                self.double_jump_serial = int(getattr(self, "double_jump_serial", 0)) + 1
                jump_edge = False

        # FIX18 posture-jump contract.  By default Jump no longer means
        # "stand up" while crouched/prone.  It records the current posture and
        # continues into the ordinary authored jump immediately.  The action
        # editor can switch back to the legacy stand-first behaviour if desired.
        if (
            jump_edge
            and self.grounded
            and self.state in (CROUCH, PRONE)
            and not fully_submerged
        ):
            if bool(self.action_settings.get("direct_jump_from_compact", True)):
                self.jump_return_posture = self.state
            else:
                self._try_stand_from_posture(world, events)
                jump_edge = False
                self.jump_buffer_remaining = 0.0

        # FIX27 underground UX: ladders can be grabbed directly with vertical
        # input. Wall climbing still requires the jump edge + direction below.
        if (
            self.state != CLIMB
            and not jump_edge
            and not climb_exit_this_frame
            and abs(float(inputs.axis_y())) > 0.12
            and not fully_submerged
            and not self.hover_active
            and not self.air_dash_active
        ):
            if self._try_start_climb(
                world,
                inputs,
                events,
                ladder_only=True,
            ):
                self.jump_buffer_remaining = 0.0
                return

        if jump_edge and fully_submerged:
            # Underwater jump is a repeatable swimming kick. FIX29 distinguishes
            # a bottom-supported shallow-water jump from an ordinary mid-water
            # kick.  When the feet are on the seabed, the jump-height parameter
            # supplies enough impulse to clear a one-tile ledge despite water
            # drag; free-swimming kicks keep the historical gentler strength.
            was_grounded = bool(self.grounded)
            self.state = JUMP
            self.grounded = False
            if was_grounded:
                jump_height = max(24.0, float(self.action_settings.get("jump_height_px", 48.0)))
                shallow_kick = max(
                    float(PLAYER_SWIM_KICK_SPEED),
                    math.sqrt(max(0.0, 2.0 * float(GRAVITY) * jump_height)) * 1.08,
                )
                self.vy = min(float(self.vy), -shallow_kick)
                self.shallow_water_jump_boost_time = 0.28
            else:
                self.vy = max(
                    -float(PLAYER_SWIM_MAX_RISE_SPEED),
                    min(float(self.vy), -float(PLAYER_SWIM_KICK_SPEED)),
                )
                self.shallow_water_jump_boost_time = 0.0
            self.jump_buffer_remaining = 0.0
            self.jump_hold_elapsed = 0.0
            self.jump_hold_target = 0.0
            self.jump_preparing = False
            self.jump_prepare_elapsed = 0.0
            self.jump_hold_input_offset = 0.0
            swim_kick = True
            self.swim_audio_serial = int(getattr(self, "swim_audio_serial", 0)) + 1

        elif jump_edge:
            # A ground/air Jump beside a ladder also expresses "jump", not
            # "grab on the following frame". Keep the same gesture guard used
            # by ladder exits, including any authored takeoff anticipation.
            ladder_cell = world.ladder_cell_in_rect(self.bbox(state=CLIMB))
            if ladder_cell is not None:
                self._block_ladder_regrab(world, world.ladder_info(*ladder_cell))
            if self.grounded and self.state == STAND:
                self.jump_return_posture = STAND
            self.jump_buffer_remaining = (
                JUMP_INPUT_BUFFER_SECONDS
            )

            if self._try_start_climb(
                world,
                inputs,
                events,
                allow_ladder=False,
            ):
                self.jump_buffer_remaining = 0.0
                return

        # A buffered press can become eligible one frame after landing.  Re-read
        # the *current* grounded posture here so crouch/prone chaining still
        # restores correctly even when the original press happened in the air.
        if (
            self.jump_buffer_remaining > 0.0
            and self.grounded
            and self.state in (CROUCH, PRONE)
            and not fully_submerged
            and not bool(self.action_settings.get("direct_jump_from_compact", True))
        ):
            self._try_stand_from_posture(world, events)
            self.jump_buffer_remaining = 0.0

        if (
            self.jump_buffer_remaining > 0.0
            and self.grounded
            and not fully_submerged
            and not self.jump_preparing
        ):
            if self.state in (CROUCH, PRONE):
                self.jump_return_posture = self.state
            else:
                self.jump_return_posture = STAND

            # Enter the authored jump clip immediately, but keep the feet on the
            # ground until its TAKEOFF marker. This removes the old visual where
            # the body was already rising while the anticipation frame played.
            self.state = JUMP

            move_dir = inputs.axis_x()
            if move_dir == 0:
                move_dir = self.facing

            tailwind = max(
                0.0,
                (wind_x * move_dir) / max(1.0,WIND_MAX_SPEED),
            )
            fire_lift = max(
                0.0,
                -wind_y / max(1.0,WIND_MAX_SPEED),
            )
            jump_scale = (
                1.0
                + tailwind * PLAYER_TAILWIND_JUMP_BONUS
                + fire_lift * PLAYER_FIRE_LIFT_JUMP_BONUS
            )

            self.jump_hold_elapsed = 0.0
            self.jump_hold_target = 0.0
            self.jump_environment_scale = max(0.65,min(1.55,float(jump_scale)))
            self.jump_prepare_elapsed = 0.0
            self.jump_hold_input_offset = 0.0
            self.jump_preparing = True
            self.vy = 0.0
            self.jump_buffer_remaining = 0.0

            # Assets without a multi-frame authored jump remain instant and keep
            # historical gameplay behaviour.
            if self.jump_takeoff_delay_seconds <= 1e-9:
                self._commit_jump_takeoff()

        # Roll. FIX16 treats roll as a temporary overlay over the current
        # grounded posture. STAND covers idle/walk/run, CROUCH covers
        # crouch/crouch-walk/crouch-run, and PRONE covers prone/crawl.
        roll_edge = inputs.just_pressed("roll")
        if roll_edge:
            if self.grounded and self.state in (STAND, CROUCH, PRONE):
                self.roll_return_posture = self.state
                self.state = ROLL
                self.roll_timer = float(self.action_settings.get("roll_time", ROLL_TIME))
                self.vy = 0.0
                self.vx = float(self.action_settings.get("roll_speed", ROLL_SPEED)) * self.facing
                self.air_dash_active = False
                self.roll_audio_serial = int(getattr(self, "roll_audio_serial", 0)) + 1

        if self.state == ROLL:
            self.roll_timer -= dt

            if self.air_dash_active:
                # Air dash is a short horizontal burst with frozen vertical
                # velocity. It ends on wall contact, nearby floor support, or
                # its timer; gravity resumes in this same fixed update.
                hit = physics.move_horizontal(self, self.vx * dt)
                self.grounded = physics.grounded(self)
                if hit or self.grounded or self.roll_timer <= 0.0:
                    self.air_dash_active = False
                    self.roll_timer = 0.0
                    self.vx = 0.0
                    self.state = STAND if self.grounded else FALL
                    if self.grounded:
                        self._reset_landed_equipment_motion()
                else:
                    return

            # Remember the last roll position at which the PRE-ROLL posture
            # still fitted.  A standing roll therefore cannot slip under a
            # ceiling and then be forced into prone merely because the compact
            # roll box fitted there.  This enforces the control contract:
            # roll ends in the same posture it started from.
            old_x = float(self.x)
            hit = physics.move_horizontal(
                self,
                self.vx * dt,
            )

            if (
                not hit
                and not self._can_use_state(
                    self.roll_return_posture,
                    world,
                )
            ):
                self.x = old_x
                hit = True

            if hit:
                self.roll_timer = 0.0

            self.grounded = physics.grounded(self)

            if self.roll_timer <= 0:
                self.vx = 0.0
                restored = self._finish_roll_posture(world, events)

                # Under ordinary terrain the exact saved posture must now fit
                # because roll travel was guarded above.  If the world changed
                # underneath the player during the roll, never leave a stale
                # ROLL state that ignores crouch/up/jump recovery inputs.
                if restored not in (STAND, CROUCH, PRONE):
                    self.state = self.roll_return_posture

                # Do not return here. If the original movement direction is
                # still held, process it in this same physics tick so walk/run/
                # crouch-walk/crouch-run/crawl resumes without a one-frame idle
                # flash after the roll. run_mode and facing were never cleared.
            else:
                return

        # Horizontal motion.
        move = inputs.axis_x()

        if move != 0:
            self.facing = 1 if move > 0 else -1

        base_speed = float(self.action_settings.get("run_speed", RUN_SPEED))

        speed = base_speed

        speed *= max(
            0.10,
            float(
                getattr(
                    self,
                    "map_speed_multiplier",
                    1.0,
                )
            ),
        )

        # FIX37: magma immersion is a movement medium, not just damage. Even
        # shallow contact is viscous; deep contact is additionally lifted to
        # the free surface after vertical collision below.
        if self._lava_foot_depth(environment) > 1.0:
            speed *= float(PLAYER_LAVA_MOVE_SPEED_SCALE)

        if self.state == CROUCH:
            speed *= float(self.action_settings.get("crouch_speed_scale", 0.55))
        elif self.state == PRONE:
            speed *= float(self.action_settings.get("prone_speed_scale", 0.35))

        if not self.grounded:
            # Hover shoes provide deliberate horizontal floating movement;
            # ordinary jumps retain the historical reduced air control.
            speed *= 1.0 if self.hover_active else AIR_CONTROL

        if move != 0:
            alignment = (
                wind_x
                * move
            ) / max(
                1.0,
                WIND_MAX_SPEED,
            )

            if alignment > 0.0:
                speed *= (
                    1.0
                    + min(
                        1.0,
                        alignment,
                    )
                    * PLAYER_TAILWIND_SPEED_BONUS
                )
            elif alignment < 0.0:
                speed *= max(
                    0.42,
                    1.0
                    - min(
                        1.0,
                        -alignment,
                    )
                    * PLAYER_HEADWIND_SPEED_PENALTY,
                )

        on_ice_support = (
            self._ground_support_is_ice(world)
            if self.grounded
            else False
        )

        drift_factor = (
            PLAYER_GROUND_WIND_DRIFT
            if (self.grounded and on_ice_support)
            else (
                PLAYER_AIR_WIND_DRIFT
                if not self.grounded
                else 0.0
            )
        )

        wind_drift = (
            wind_x
            * drift_factor
        )

        requested_vx = (
            move
            * speed
            + wind_drift
        )

        # V0.6.5.5: immediate input, continuous velocity.
        #
        # The old path assigned self.vx = requested_vx, so a stationary
        # character jumped from 0 directly to WALK_SPEED/RUN_SPEED in one
        # physics step. Combined with UIKit press feedback this looked like a
        # small freeze followed by a sudden lurch. Keep the input edge
        # immediate, but move velocity toward the target over ~0.08-0.13 s.
        current_vx = float(self.vx)
        target_vx = float(requested_vx)

        if (
            abs(current_vx) > 1e-6
            and abs(target_vx) > 1e-6
            and current_vx * target_vx < 0.0
        ):
            accel = PLAYER_MOVE_TURN_ACCEL
        elif abs(target_vx) < abs(current_vx):
            accel = (
                PLAYER_MOVE_DECEL_GROUND
                if self.grounded
                else PLAYER_MOVE_DECEL_AIR
            )
        else:
            accel = (
                PLAYER_MOVE_ACCEL_GROUND
                if self.grounded
                else PLAYER_MOVE_ACCEL_AIR
            )

        max_change = max(0.0, float(accel) * float(dt))
        delta_v = target_vx - current_vx
        if delta_v > max_change:
            self.vx = current_vx + max_change
        elif delta_v < -max_change:
            self.vx = current_vx - max_change
        else:
            self.vx = target_vx

        # Hover shoes replace ordinary air velocity for at most five seconds.
        # Both joystick axes are respected and the subsequent collision calls
        # remain authoritative, so floating cannot phase through terrain.
        equipment = getattr(self, "equipment_system", None)
        if equipment is not None and hasattr(equipment, "update_hover_motion"):
            try:
                equipment.update_hover_motion(
                    self,
                    dt,
                    move_x=inputs.axis_x(),
                    move_y=inputs.axis_y(),
                    fully_submerged=fully_submerged,
                    climbing=False,
                )
            except Exception:
                pass

        hit_horizontal = physics.move_horizontal(
            self,
            self.vx * dt,
        )

        # Diagnostic-only snapshot used by the large MOVE DEBUG panel.
        self.movement_debug = {
            "move": int(
                move
            ),
            "base_speed": float(
                base_speed
            ),
            "effective_speed": float(
                speed
            ),
            "wind_x": float(
                wind_x
            ),
            "wind_drift": float(
                wind_drift
            ),
            "requested_vx": float(
                requested_vx
            ),
            "hit_horizontal": bool(
                hit_horizontal
            ),
        }

        # Continuous variable-height jump with deferred hold-credit.
        #
        # V0.5.4 could receive a release after a UI/physics timing gap and then
        # apply the ENTIRE measured hold duration as one instantaneous velocity
        # impulse. That made some short taps launch far too high.
        #
        # V0.5.5 still uses the physical touch duration, but any unconsumed
        # duration becomes a TARGET. The corresponding lift is then consumed
        # gradually at physics-step rate, exactly like a real held button.
        if (
            self.state == JUMP
            and not self.grounded
            and self.vy < 0.0
            and not fully_submerged
            and not self.hover_active
        ):
            if inputs.down(
                "jump"
            ):
                self.jump_hold_target = max(
                    self.jump_hold_target,
                    min(
                        JUMP_HOLD_MAX_SECONDS,
                        float(
                            self._effective_jump_hold_seconds(
                                inputs.held_seconds("jump")
                            )
                        ),
                    ),
                )

            if inputs.just_released(
                "jump"
            ):
                self.jump_hold_target = max(
                    self.jump_hold_target,
                    min(
                        JUMP_HOLD_MAX_SECONDS,
                        float(
                            self._effective_jump_hold_seconds(
                                inputs.released_duration("jump")
                            )
                        ),
                    ),
                )

            # If a press+release happened between two physics snapshots,
            # jump_hold_target may jump ahead. Do NOT apply that difference in
            # one frame; consume at most one physics dt of hold-lift per step.
            if (
                self.jump_hold_target
                > self.jump_hold_elapsed
            ):
                active_dt = min(
                    dt,
                    self.jump_hold_target
                    - self.jump_hold_elapsed,
                )

                progress = min(
                    1.0,
                    self.jump_hold_elapsed
                    / max(
                        1e-6,
                        JUMP_HOLD_MAX_SECONDS,
                    ),
                )

                hold_accel = (
                    JUMP_HOLD_BASE_ACCEL
                    + JUMP_HOLD_RAMP_ACCEL
                    * progress
                )

                self.vy -= (
                    hold_accel
                    * self.jump_environment_scale
                    * active_dt
                )

                self.jump_hold_elapsed += active_dt

        # Vertical fire-lift wind affects airborne characters.
        if not self.grounded and not self.hover_active:
            self.vy += (
                wind_y
                * PLAYER_WIND_VERTICAL_ACCEL
                * dt
            )

        # Gravity / swimming buoyancy-drag approximation. Fully submerged
        # characters sink slowly and can apply repeated jump-kicks; the moment
        # the head leaves the water this returns to normal air gravity.
        if fully_submerged:
            self.vy += GRAVITY * float(PLAYER_SWIM_GRAVITY_SCALE) * dt
            boosted = float(getattr(self, "shallow_water_jump_boost_time", 0.0)) > 0.0
            if boosted:
                self.shallow_water_jump_boost_time = max(0.0, self.shallow_water_jump_boost_time - dt)
                # A short reduced-drag window represents pushing off the bottom.
                drag_rate = float(PLAYER_SWIM_VERTICAL_DRAG_PER_SECOND) * 0.30
                jump_height = max(24.0, float(self.action_settings.get("jump_height_px", 48.0)))
                rise_cap = max(
                    float(PLAYER_SWIM_MAX_RISE_SPEED),
                    math.sqrt(max(0.0, 2.0 * float(GRAVITY) * jump_height)) * 1.12,
                )
            else:
                drag_rate = float(PLAYER_SWIM_VERTICAL_DRAG_PER_SECOND)
                rise_cap = float(PLAYER_SWIM_MAX_RISE_SPEED)
            drag = max(0.0, 1.0 - drag_rate * dt)
            self.vy *= drag
            self.vy = max(
                -rise_cap,
                min(float(self.vy), float(PLAYER_SWIM_MAX_SINK_SPEED)),
            )
        elif self.hover_active:
            # EquipmentSystem already supplied collision-ready vertical input.
            self.shallow_water_jump_boost_time = 0.0
        else:
            self.shallow_water_jump_boost_time = 0.0
            self.vy += GRAVITY * dt
            self.vy = min(self.vy, 900.0)

        _pre_vertical_grounded = bool(self.grounded)
        _pre_vertical_vy = float(self.vy)
        hit_y, grounded = physics.move_vertical(
            self,
            self.vy * dt,
        )

        if hit_y:
            if grounded:
                if (not _pre_vertical_grounded) and _pre_vertical_vy > 45.0:
                    self.last_landing_speed = float(_pre_vertical_vy)
                    self.landing_audio_serial = int(getattr(self, "landing_audio_serial", 0)) + 1
                self.vy = 0.0
                self.grounded = True
            elif self.vy < 0:
                self.vy = 0.0
        else:
            self.grounded = False

        if not self.grounded and abs(self.vy) < 5:
            self.grounded = physics.grounded(self)

        # FIX37: resolve dense-lava support after ordinary terrain collision.
        # This is intentionally later than rock collision: a rock floor may stop
        # downward motion, but deep lava above it then becomes the authoritative
        # support surface and lifts the character out of the submerged floor.
        self._apply_lava_surface_support(physics, environment, dt)

        if self.grounded:
            self._reset_landed_equipment_motion()

        # If support disappears during anticipation (for example the player
        # walks off an edge), do not leave the character stuck in a grounded
        # wind-up. Commit the requested jump immediately and continue airborne.
        if self.jump_preparing and not self.grounded:
            self._commit_jump_takeoff()

        if not self.grounded:
            # Important low-clearance rule:
            # walking off a ledge while CROUCH/PRONE must keep the compact
            # hitbox.  The old code immediately changed it to FALL (54 px),
            # which made a 36 px crouched player suddenly too tall and the
            # horizontal collision resolver pushed the player backward out of
            # a one-tile-high recess.  An intentional jump already changes the
            # state to JUMP above, so jump behavior is unchanged.
            if self.state not in (CROUCH, PRONE):
                self.state = JUMP if self.vy < 0 else FALL
        else:
            if self.jump_preparing:
                # Anticipation is visually JUMP but physically grounded until
                # TAKEOFF. Keep that state so GameApp does not switch back to
                # idle and restart the animation clock.
                self.state = JUMP
                self.vy = 0.0
            else:
                # FIX18: an intentional crouch/prone jump lands in the posture
                # it started from.  This avoids an unwanted standing transition
                # and lets FIX17's destination link keyframe enter the stable
                # crouch/prone pose directly.
                if self.state in (JUMP, FALL):
                    target = STAND
                    if bool(self.action_settings.get("restore_posture_after_jump", True)):
                        if self.jump_return_posture in (CROUCH, PRONE):
                            target = self.jump_return_posture
                    if target in (CROUCH, PRONE) and self._can_use_state(target, world):
                        self.state = target
                    else:
                        self.state = STAND
                    self.jump_return_posture = STAND

                if self.state not in (STAND, CROUCH, PRONE):
                    self.state = STAND

        # Keep inside horizontal world bounds. The authored main map is a
        # horizontal cylinder; child maps keep the traditional hard clamp.
        max_x = world.width_tiles * TILE_SIZE
        margin = 12.0
        if bool(getattr(world, "horizontal_wrap_enabled", False)) and max_x > margin * 4.0:
            wrapped = 0
            if self.x <= margin and self.vx < 0.0:
                self.x = max_x - margin - 2.0
                wrapped = -1
            elif self.x >= max_x - margin and self.vx > 0.0:
                self.x = margin + 2.0
                wrapped = 1
            else:
                self.x = clamp(self.x, margin, max_x - margin)
            if wrapped:
                self.prev_x = float(self.x)
                self.world_wrap_direction = int(wrapped)
                self.world_wrap_serial = int(getattr(self, "world_wrap_serial", 0)) + 1
        else:
            self.x = clamp(self.x, margin, max_x - margin)
