# -*- coding: utf-8 -*-
"""FIX61 bounded normal/heavy weapon combat with melee and projectiles."""
import math

from config import (
    TILE_SIZE,
    MELEE_FRONT_REACH_TILES,
    WEAPON_HIT_STOP_MAX_SECONDS,
    WEAPON_CAMERA_IMPULSE_MAX_PX,
    WEAPON_IMPACT_FEEDBACK_LIMIT,
    WEAPON_IMPACT_FEEDBACK_TTL,
    WEAPON_IMPACT_COALESCE_SECONDS,
)
from entities.creature import Creature
from systems.weapon_catalog import WEAPON_DEFS, weapon_from_item
from world.tile_registry import tile_def


class MeleeSystem:
    WEAPON_SWORD = "sword"
    MAX_WEAPON_PROJECTILES = 40

    # FIX67 keeps damage values and AssetEditor event timing authoritative, but
    # gives every weapon a distinct presentation envelope.  Reach/knockback
    # multipliers affect collision results; VFX values are presentation-only.
    # The renderer can query ``presentation_profile`` without knowing combat
    # internals, and authored/FIX54 render modes remain separate.
    PRESENTATION_PROFILES = {
        "sword": {
            "reach_scale": 1.12, "heavy_reach_scale": 1.18,
            "knockback_scale": 1.08, "heavy_knockback_scale": 1.13,
            "vfx_scale": 1.10, "heavy_vfx_scale": 1.20,
            "hit_stop": 0.030, "heavy_hit_stop": 0.052,
            "camera": 1.2, "heavy_camera": 2.8,
            "magnitude": 0.55, "heavy_magnitude": 0.78,
        },
        "dagger": {
            "reach_scale": 1.10, "heavy_reach_scale": 1.18,
            "knockback_scale": 1.04, "heavy_knockback_scale": 1.10,
            "vfx_scale": 1.08, "heavy_vfx_scale": 1.20,
            "hit_stop": 0.018, "heavy_hit_stop": 0.038,
            "camera": 0.6, "heavy_camera": 1.7,
            "magnitude": 0.34, "heavy_magnitude": 0.62,
        },
        "spear": {
            "reach_scale": 1.10, "heavy_reach_scale": 1.15,
            "knockback_scale": 1.12, "heavy_knockback_scale": 1.22,
            "vfx_scale": 1.10, "heavy_vfx_scale": 1.24,
            "hit_stop": 0.030, "heavy_hit_stop": 0.060,
            "camera": 1.4, "heavy_camera": 3.4,
            "magnitude": 0.55, "heavy_magnitude": 0.85,
        },
        "battle_axe": {
            "reach_scale": 1.12, "heavy_reach_scale": 1.16,
            "knockback_scale": 1.13, "heavy_knockback_scale": 1.20,
            "vfx_scale": 1.12, "heavy_vfx_scale": 1.25,
            "hit_stop": 0.044, "heavy_hit_stop": 0.074,
            "camera": 1.8, "heavy_camera": 4.5,
            "magnitude": 0.70, "heavy_magnitude": 0.94,
        },
        "war_hammer": {
            "reach_scale": 1.10, "heavy_reach_scale": 1.22,
            "knockback_scale": 1.16, "heavy_knockback_scale": 1.25,
            "vfx_scale": 1.14, "heavy_vfx_scale": 1.32,
            "hit_stop": 0.055, "heavy_hit_stop": 0.085,
            "camera": 2.0, "heavy_camera": 6.0,
            "magnitude": 0.82, "heavy_magnitude": 1.00,
        },
        "greatsword": {
            "reach_scale": 1.12, "heavy_reach_scale": 1.20,
            "knockback_scale": 1.14, "heavy_knockback_scale": 1.22,
            "vfx_scale": 1.14, "heavy_vfx_scale": 1.28,
            "hit_stop": 0.050, "heavy_hit_stop": 0.080,
            "camera": 1.9, "heavy_camera": 5.4,
            "magnitude": 0.78, "heavy_magnitude": 0.98,
            "projectile_speed_scale": 1.06,
            "projectile_life_scale": 1.12,
            "projectile_radius_scale": 1.12,
        },
        "energy_bow": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.08, "heavy_knockback_scale": 1.12,
            "vfx_scale": 1.12, "heavy_vfx_scale": 1.22,
            "hit_stop": 0.020, "heavy_hit_stop": 0.040,
            "camera": 0.8, "heavy_camera": 2.2,
            "magnitude": 0.40, "heavy_magnitude": 0.70,
            "projectile_speed_scale": 1.08,
            "projectile_life_scale": 1.12,
            "projectile_radius_scale": 1.14,
            "heavy_projectile_speed_scale": 1.10,
            "heavy_projectile_life_scale": 1.12,
            "heavy_projectile_radius_scale": 1.15,
        },
        "whip": {
            "reach_scale": 1.08, "heavy_reach_scale": 1.14,
            "knockback_scale": 1.08, "heavy_knockback_scale": 1.16,
            "vfx_scale": 1.12, "heavy_vfx_scale": 1.24,
            "hit_stop": 0.025, "heavy_hit_stop": 0.055,
            "camera": 1.1, "heavy_camera": 3.6,
            "magnitude": 0.48, "heavy_magnitude": 0.82,
        },
        "laser_gun": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.06, "heavy_knockback_scale": 1.12,
            "vfx_scale": 1.14, "heavy_vfx_scale": 1.24,
            "hit_stop": 0.018, "heavy_hit_stop": 0.042,
            "camera": 0.9, "heavy_camera": 2.5,
            "magnitude": 0.44, "heavy_magnitude": 0.76,
            "projectile_speed_scale": 1.06,
            "projectile_life_scale": 1.12,
            "projectile_radius_scale": 1.20,
            "heavy_projectile_speed_scale": 1.06,
            "heavy_projectile_life_scale": 1.00,
            "heavy_projectile_radius_scale": 1.15,
        },
        "yoyo": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.06, "heavy_knockback_scale": 1.14,
            "vfx_scale": 1.12, "heavy_vfx_scale": 1.30,
            "hit_stop": 0.020, "heavy_hit_stop": 0.050,
            "camera": 0.7, "heavy_camera": 2.6,
            "magnitude": 0.44, "heavy_magnitude": 0.82,
            "projectile_speed_scale": 1.06,
            "projectile_life_scale": 1.08,
            "projectile_radius_scale": 1.08,
        },
        "battle_top": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.12, "heavy_knockback_scale": 1.24,
            "vfx_scale": 1.14, "heavy_vfx_scale": 1.34,
            "hit_stop": 0.032, "heavy_hit_stop": 0.068,
            "camera": 1.2, "heavy_camera": 4.1,
            "magnitude": 0.58, "heavy_magnitude": 0.94,
            "projectile_speed_scale": 1.04,
            "projectile_life_scale": 1.10,
            "projectile_radius_scale": 1.12,
        },
        "rpg_launcher": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.18, "heavy_knockback_scale": 1.28,
            "vfx_scale": 1.18, "heavy_vfx_scale": 1.38,
            "hit_stop": 0.050, "heavy_hit_stop": 0.085,
            "camera": 3.2, "heavy_camera": 6.0,
            "magnitude": 0.84, "heavy_magnitude": 1.00,
            "projectile_speed_scale": 1.04,
            "projectile_life_scale": 1.04,
            "projectile_radius_scale": 1.05,
        },
        "tnt": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.14, "heavy_knockback_scale": 1.26,
            "vfx_scale": 1.16, "heavy_vfx_scale": 1.38,
            "hit_stop": 0.046, "heavy_hit_stop": 0.085,
            "camera": 2.6, "heavy_camera": 6.0,
            "magnitude": 0.78, "heavy_magnitude": 1.00,
            "projectile_speed_scale": 1.00,
            "projectile_life_scale": 1.00,
            "projectile_radius_scale": 1.00,
        },
        "flying_drone": {
            "reach_scale": 1.00, "heavy_reach_scale": 1.00,
            "knockback_scale": 1.14, "heavy_knockback_scale": 1.12,
            "vfx_scale": 1.14, "heavy_vfx_scale": 1.32,
            "hit_stop": 0.035, "heavy_hit_stop": 0.085,
            "camera": 1.5, "heavy_camera": 5.8,
            "magnitude": 0.62, "heavy_magnitude": 1.00,
            "acquire_radius_scale": 1.18,
            "dive_speed_scale": 1.08,
            "hit_radius_scale": 1.17,
            "explosion_radius_scale": 1.22,
        },
    }

    def __init__(
        self, player, scene, events, world=None, tool_system=None,
        asset_registry=None, aim_provider=None, heavy_use_callback=None,
    ):
        self.player = player
        self.scene = scene
        self.events = events
        self.world = world
        self.tool_system = tool_system
        self.asset_registry = asset_registry
        self.aim_provider = aim_provider
        # GameApp owns inventory and supplies the authoritative per-copy
        # durability transaction.  Keeping the callback outside this combat
        # module avoids coupling projectiles to backpack/UI implementation.
        self.heavy_use_callback = heavy_use_callback
        self.last_durability_result = None
        self.boss_weapon_system = None
        self.selected_weapon = self.WEAPON_SWORD
        self.cooldown = 0.0
        self.swing_timer = 0.0
        self.swing_duration = 0.0
        self.last_hit_id = None
        self.current_attack_kind = ""
        self.last_charge_seconds = 0.0
        # All weapon projectiles share one bounded gameplay-authoritative list.
        # The historical name stays public for renderer/save compatibility.
        self.slash_projectiles = []
        self._slash_serial = 0
        self._pending_world_effect = None
        self._pending_attack_hit = None
        self._pending_projectile = None
        # FIX55 VFX instances are visual-only and may outlive the attack clip.
        # Keeping them here avoids truncating a 5-frame effect just because the
        # melee swing itself ended earlier.
        self._pending_vfx = None
        self.active_vfx = []
        self._attack_serial = 0
        self.impact_serial = 0
        self.impact_feedback = []
        self.last_impact_feedback = None
        # Presentation snapshots only.  MeleeSystem never sleeps, scales dt,
        # pauses Metal/UIKit, or mutates the authoritative game camera.
        self.camera_impulse_x = 0.0
        self.camera_impulse_y = 0.0
        self._impact_coalesce_remaining = 0.0

    def weapon_def(self, weapon_id=None):
        key = str(self.selected_weapon if weapon_id is None else weapon_id)
        return WEAPON_DEFS.get(key, WEAPON_DEFS[self.WEAPON_SWORD])

    def presentation_profile(self, weapon_id=None):
        """Return a detached FIX67 presentation/combat envelope.

        Asset files remain the source of animation frames and event markers.
        This runtime profile only scales their presentation and the matching
        authoritative collision envelope; callers cannot mutate the defaults.
        """
        key = str(self.selected_weapon if weapon_id is None else weapon_id)
        return dict(self.PRESENTATION_PROFILES.get(key, {}))

    def effective_reach_tiles(self, weapon_id=None, heavy=False):
        row = self.weapon_def(weapon_id)
        key = "heavy_reach_tiles" if bool(heavy) else "reach_tiles"
        base = max(0.0, float(row.get(key, row.get("reach_tiles", MELEE_FRONT_REACH_TILES))))
        profile = self.presentation_profile(weapon_id)
        scale_key = "heavy_reach_scale" if bool(heavy) else "reach_scale"
        return base * max(0.25, min(2.0, float(profile.get(scale_key, 1.0) or 1.0)))

    def _effective_knockback(self, row, heavy=False, weapon_id=None):
        key = str(self.selected_weapon if weapon_id is None else weapon_id)
        profile = self.presentation_profile(key)
        knockback = max(0.0, float(row.get("knockback", 120.0)))
        if heavy:
            knockback *= max(1.0, float(row.get("heavy_knockback_mult", 1.2)))
        scale_key = "heavy_knockback_scale" if heavy else "knockback_scale"
        return knockback * max(0.25, min(2.0, float(profile.get(scale_key, 1.0) or 1.0)))

    def _presentation_scale(self, weapon_id=None, heavy=False):
        profile = self.presentation_profile(weapon_id)
        key = "heavy_vfx_scale" if bool(heavy) else "vfx_scale"
        return max(0.75, min(2.0, float(profile.get(key, 1.0) or 1.0)))

    def _projectile_profile_scale(self, weapon_id, metric, heavy=False):
        profile = self.presentation_profile(weapon_id)
        base_key = "projectile_" + str(metric) + "_scale"
        heavy_key = "heavy_" + base_key
        key = heavy_key if bool(heavy) and heavy_key in profile else base_key
        return max(0.50, min(1.50, float(profile.get(key, 1.0) or 1.0)))

    def _boost_authored_binding(self, binding, weapon_id=None, heavy=False):
        if not isinstance(binding, dict):
            return None
        boosted = dict(binding)
        try:
            authored_scale = float(boosted.get("scale", 1.0) or 1.0)
        except Exception:
            authored_scale = 1.0
        presentation_scale = self._presentation_scale(weapon_id, heavy=heavy)
        boosted["authored_scale"] = authored_scale
        boosted["presentation_scale"] = presentation_scale
        boosted["scale"] = max(0.25, min(3.0, authored_scale * presentation_scale))
        return boosted

    @staticmethod
    def _target_feedback_id(target):
        if hasattr(target, "entity_id"):
            return str(getattr(target, "entity_id", ""))
        return str(target or "")

    def emit_external_impact(
        self,
        weapon_id,
        heavy=False,
        x=None,
        y=None,
        direction=None,
        targets=(),
        delivery="external",
        world_hit=False,
        magnitude_scale=1.0,
        allow_hit_stop=True,
        metadata=None,
    ):
        """Publish one bounded, visual-only impact packet after a real hit.

        Damage/collision must already have been resolved by the caller.  The
        packet is deliberately downstream-only, preventing a larger sprite or
        camera effect from silently changing gameplay damage.
        """
        weapon_id = str(weapon_id or self.selected_weapon)
        profile = self.presentation_profile(weapon_id)
        heavy = bool(heavy)
        magnitude_scale = max(0.0, min(1.5, float(magnitude_scale or 0.0)))
        stop_key = "heavy_hit_stop" if heavy else "hit_stop"
        camera_key = "heavy_camera" if heavy else "camera"
        magnitude_key = "heavy_magnitude" if heavy else "magnitude"
        hit_stop = max(0.0, min(
            float(WEAPON_HIT_STOP_MAX_SECONDS),
            float(profile.get(stop_key, 0.025) or 0.0) * magnitude_scale,
        ))
        camera = max(0.0, min(
            float(WEAPON_CAMERA_IMPULSE_MAX_PX),
            float(profile.get(camera_key, 3.0) or 0.0) * magnitude_scale,
        ))
        magnitude = max(0.0, min(
            1.0,
            float(profile.get(magnitude_key, 0.5) or 0.0) * magnitude_scale,
        ))
        coalesced = self._impact_coalesce_remaining > 0.0
        if coalesced:
            # A twelve-arrow volley may report every collision, but only its
            # leading edge can request a pause.  Later hits retain lightweight
            # particles/audio metadata and a reduced camera impulse.
            hit_stop = 0.0
            camera *= 0.22
        elif not bool(allow_hit_stop):
            hit_stop = 0.0
        else:
            self._impact_coalesce_remaining = max(
                float(self._impact_coalesce_remaining),
                float(WEAPON_IMPACT_COALESCE_SECONDS),
            )

        try:
            dx, dy = direction if direction is not None else (1.0, -0.12)
            dx = float(dx); dy = float(dy)
        except Exception:
            dx, dy = 1.0, -0.12
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            dx, dy, length = 0.0, -1.0, 1.0
        dx /= length; dy /= length
        impulse_x = max(-float(WEAPON_CAMERA_IMPULSE_MAX_PX), min(
            float(WEAPON_CAMERA_IMPULSE_MAX_PX), dx * camera,
        ))
        impulse_y = max(-float(WEAPON_CAMERA_IMPULSE_MAX_PX), min(
            float(WEAPON_CAMERA_IMPULSE_MAX_PX), (dy - 0.16) * camera,
        ))
        # The budget is radial, not per-axis.  Without this final normalization
        # a diagonal direction could remain below 6 px on each axis yet exceed
        # the intended total screen displacement.
        impulse_length = math.hypot(impulse_x, impulse_y)
        impulse_limit = max(0.0, float(WEAPON_CAMERA_IMPULSE_MAX_PX))
        if impulse_limit > 0.0 and impulse_length > impulse_limit:
            impulse_scale = impulse_limit / impulse_length
            impulse_x *= impulse_scale
            impulse_y *= impulse_scale
        px = float(self.player.x if x is None else x)
        py = float((self.player.y - self.player.height() * 0.45) if y is None else y)
        target_ids = [
            target_id for target_id in (
                self._target_feedback_id(target) for target in tuple(targets or ())
            ) if target_id
        ]
        self.impact_serial += 1
        tier = "heavy" if magnitude >= 0.82 else ("medium" if magnitude >= 0.52 else "light")
        payload = {
            "serial": int(self.impact_serial),
            "weapon": weapon_id,
            "delivery": str(delivery or "external"),
            "heavy": heavy,
            "x": px,
            "y": py,
            "world_hit": bool(world_hit),
            "target_ids": target_ids[:12],
            "target_count": min(12, len(target_ids)),
            "hit_stop_seconds": hit_stop,
            "camera_impulse_x": impulse_x,
            "camera_impulse_y": impulse_y,
            "magnitude": magnitude,
            "impact_tier": tier,
            "vfx_scale": self._presentation_scale(weapon_id, heavy=heavy),
            "sound_gain": min(1.25, 0.82 + magnitude * 0.38),
            "coalesced": bool(coalesced),
        }
        if isinstance(metadata, dict):
            # Only scalar/list presentation metadata is expected; the event is
            # synchronous and never retains entity or renderer objects.
            for meta_key, meta_value in dict(metadata).items():
                if str(meta_key) not in payload:
                    payload[str(meta_key)] = meta_value
        feedback = dict(payload)
        feedback["ttl"] = float(WEAPON_IMPACT_FEEDBACK_TTL)
        self.impact_feedback.append(feedback)
        self.impact_feedback = self.impact_feedback[-max(1, int(WEAPON_IMPACT_FEEDBACK_LIMIT)):]
        self.last_impact_feedback = dict(payload)
        if abs(impulse_x) + abs(impulse_y) > abs(self.camera_impulse_x) + abs(self.camera_impulse_y):
            self.camera_impulse_x = impulse_x
            self.camera_impulse_y = impulse_y
        try:
            self.events.emit("weapon_impact", **payload)
        except Exception:
            pass
        return payload

    @property
    def selected_name(self):
        return str(self.weapon_def().get("name", "鐵劍"))

    @property
    def selected_item_id(self):
        return str(self.weapon_def().get("item_id", "weapon_sword"))

    @property
    def charge_threshold(self):
        return max(0.10, float(self.weapon_def().get("heavy_charge_seconds", 0.55)))

    @property
    def attack_progress(self):
        duration = max(1e-6, float(getattr(self, "swing_duration", 0.0)))
        timer = max(0.0, min(duration, float(getattr(self, "swing_timer", 0.0))))
        if timer <= 0.0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - timer / duration))

    @property
    def heavy_swinging(self):
        return self.current_attack_kind == "heavy" and self.swing_timer > 0.0

    def equip_weapon(self, weapon_id):
        weapon_id = str(weapon_id or "")
        if weapon_id not in WEAPON_DEFS:
            return False
        self.selected_weapon = weapon_id
        self.cooldown = 0.0
        self.swing_timer = 0.0
        self.swing_duration = 0.0
        self.current_attack_kind = ""
        self.last_charge_seconds = 0.0
        self._pending_world_effect = None
        self._pending_attack_hit = None
        self._pending_projectile = None
        self._pending_vfx = None
        self.active_vfx = []
        self.impact_feedback = []
        self.last_impact_feedback = None
        self.camera_impulse_x = 0.0
        self.camera_impulse_y = 0.0
        self._impact_coalesce_remaining = 0.0
        self.last_durability_result = None
        return True

    def equip_item(self, item_id):
        wid = weapon_from_item(item_id)
        return self.equip_weapon(wid) if wid else False

    def _weapon_asset_id(self):
        return "weapon." + str(self.selected_weapon)

    def _authored_event_progress(self, state, event_name, fallback):
        """Map an AssetEditor keyframe to normalized attack progress.

        Missing custom assets/markers deliberately keep the historical code
        timing, so existing projects do not change until the artist saves a
        weapon asset and places a marker.
        """
        assets = self.asset_registry
        if assets is None or not hasattr(assets, "animation_info"):
            return float(fallback)
        try:
            info = assets.animation_info(self._weapon_asset_id(), str(state))
        except Exception:
            info = None
        if not isinstance(info, dict):
            return float(fallback)
        events = info.get("events", {}) if isinstance(info.get("events", {}), dict) else {}
        if str(event_name) not in events:
            return float(fallback)
        try:
            frame = int(events[str(event_name)])
            count = max(1, int(info.get("frame_count", 1) or 1))
            return max(0.0, min(1.0, float(frame) / float(max(1, count - 1))))
        except Exception:
            return float(fallback)

    def _authored_vfx_spec(self, state):
        assets=self.asset_registry
        if assets is None or not hasattr(assets,"combat_binding"):
            return None
        try:
            binding=assets.combat_binding(self._weapon_asset_id(),str(state))
            ainfo=assets.animation_info(self._weapon_asset_id(),str(state))
        except Exception:
            return None
        if not isinstance(binding,dict) or not isinstance(ainfo,dict):return None
        # FIX58 packaged weapons retain the exact FIX54 procedural VFX. Once
        # AssetEditor marks a pair as authored, this independent VFX track takes
        # over and can still outlive the hand/weapon attack clip.
        if str(binding.get("render_mode","authored") or "authored").lower()!="authored":
            return None
        # Projectile visuals are rendered at the authoritative projectile
        # entity's collision position. Do not also spawn a hand-anchored VFX
        # instance, which would duplicate the moon blade and drift from hits.
        if str(binding.get("motion","follow") or "follow").lower()=="projectile":
            return None
        effect_state=str(binding.get("effect_state","normal_effect"))
        try:einfo=assets.animation_info(self._weapon_asset_id(),effect_state)
        except Exception:einfo=None
        if not isinstance(einfo,dict):return None
        events=ainfo.get("events",{}) if isinstance(ainfo.get("events",{}),dict) else {}
        trigger_name=str(binding.get("trigger_event","effect") or "effect")
        frame=events.get(trigger_name,events.get("effect",events.get("action",0)))
        count=max(1,int(ainfo.get("frame_count",1) or 1))
        try:frame=max(0,min(count-1,int(frame)))
        except Exception:frame=0
        progress=0.0 if count<=1 else float(frame)/float(count-1)
        ecount=max(1,int(einfo.get("frame_count",1) or 1)); efps=max(1.0,float(einfo.get("fps",12.0) or 12.0))
        duration=max(1.0/efps,float(ecount)/efps)
        heavy = str(state) == "heavy_attack"
        boosted_binding = self._boost_authored_binding(
            binding, self.selected_weapon, heavy=heavy,
        ) or dict(binding)
        return {
            "weapon":str(self.selected_weapon),"effect_state":effect_state,
            "trigger_progress":max(0.0,min(1.0,progress)),
            "duration":duration,"elapsed":0.0,"binding":boosted_binding,
            "presentation_scale":self._presentation_scale(self.selected_weapon, heavy=heavy),
            "facing":1 if int(getattr(self.player,"facing",1))>=0 else -1,
        }

    def _spawn_authored_vfx(self, spec):
        if not isinstance(spec,dict):return
        item=dict(spec); item["elapsed"]=0.0
        self.active_vfx.append(item)
        if len(self.active_vfx)>6:self.active_vfx=self.active_vfx[-6:]

    def _update_pending_vfx(self, prev_progress, new_progress):
        pending=self._pending_vfx
        if not isinstance(pending,dict):return
        trigger=max(0.0,min(1.0,float(pending.get("trigger_progress",0.0))))
        if self._crossed_progress(prev_progress,new_progress,trigger):
            self._spawn_authored_vfx(pending); self._pending_vfx=None

    def _update_active_vfx(self, dt):
        if not self.active_vfx:return
        keep=[]
        for item in self.active_vfx:
            row=dict(item); row["elapsed"]=max(0.0,float(row.get("elapsed",0.0)))+float(dt)
            if row["elapsed"] < max(0.01,float(row.get("duration",0.01))):keep.append(row)
        self.active_vfx=keep[-6:]

    @staticmethod
    def _crossed_progress(prev_progress, new_progress, trigger):
        trigger = max(0.0, min(1.0, float(trigger)))
        return float(prev_progress) < trigger <= float(new_progress) or (trigger <= 0.0 and float(new_progress) >= 0.0)

    def _authored_animation_duration(self, state, fallback):
        assets = self.asset_registry
        if assets is None or not hasattr(assets, "animation_info"):
            return float(fallback)
        try:
            info = assets.animation_info(self._weapon_asset_id(), str(state))
        except Exception:
            info = None
        if not isinstance(info, dict):
            return float(fallback)
        try:
            count = max(1, int(info.get("frame_count", 1) or 1))
            fps = max(1.0, float(info.get("fps", 10.0) or 10.0))
            if count <= 1:
                return float(fallback)
            return max(0.08, min(2.0, float(count) / fps))
        except Exception:
            return float(fallback)

    @staticmethod
    def _overlap(a, b):
        return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

    @staticmethod
    def _circle_aabb(cx, cy, radius, box):
        qx = max(float(box[0]), min(float(cx), float(box[2])))
        qy = max(float(box[1]), min(float(cy), float(box[3])))
        dx = float(cx) - qx
        dy = float(cy) - qy
        return dx * dx + dy * dy <= float(radius) * float(radius)

    def _hitbox(self, row=None, heavy=False):
        p = self.player
        facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
        row = self.weapon_def() if row is None else row
        weapon_id = str(self.selected_weapon)
        # The same profile is available to presentation code, so the wider
        # slash never becomes an invisible hit volume.
        reach = TILE_SIZE * self.effective_reach_tiles(weapon_id, heavy=heavy)
        body_front = p.x + facing * (p.width() * 0.5)
        far_edge = body_front + facing * reach
        top_scale = float(row.get("hitbox_top_scale", 0.88))
        bottom_scale = float(row.get("hitbox_bottom_scale", 0.05))
        # Heavy horizontal attacks need enough low-body coverage to cut grass;
        # hammer/sword still retain their authored high arc.
        if heavy and str(row.get("heavy_style", "")) == "horizontal_cleave":
            top_scale = max(top_scale, 0.92)
            bottom_scale = min(bottom_scale, -0.02)
        y1 = p.y - p.height() * top_scale
        y2 = p.y - p.height() * bottom_scale
        return min(body_front, far_edge), min(y1, y2), max(body_front, far_edge), max(y1, y2)

    @staticmethod
    def _provoke_creature(target):
        """Latch retaliation before applying a player-owned damage event."""
        try:
            return bool(target.provoke_by_player())
        except Exception:
            try:
                target.provoked_by_player = True
                target.player_detected = True
                return True
            except Exception:
                return False

    def _damage_creatures(self, row, hitbox, heavy=False):
        p = self.player
        facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
        candidates = []
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            if (entity.x - p.x) * facing <= 0.0:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if not self._overlap(hitbox, cb):
                continue
            candidates.append((abs(entity.x - p.x), entity))

        if not candidates:
            self.last_hit_id = None
            return []

        candidates.sort(key=lambda pair: pair[0])
        damage = max(1.0, float(row.get("damage", 34.0)))
        knockback = self._effective_knockback(row, heavy=False)
        max_targets = max(1, int(row.get("max_targets", 1) or 1))
        if heavy:
            damage *= max(1.0, float(row.get("heavy_damage_mult", 1.5)))
            knockback = self._effective_knockback(row, heavy=True)
            max_targets = max(max_targets, int(row.get("heavy_max_targets", max_targets) or max_targets))
        hit_names = []
        last_target = None
        for _distance, target in candidates[:max_targets]:
            self._provoke_creature(target)
            target.hp = max(0.0, float(target.hp) - damage)
            target.hurt_timer = 0.16 if not heavy else 0.24
            target.vx = facing * knockback
            last_target = target
            hit_names.append(str(target.name))
            try:
                self.events.emit(
                    "audio_melee_hit",
                    weapon=str(self.selected_weapon),
                    sound_key=str(row.get("hit_sound", "sword_hit")),
                    target_id=str(target.entity_id),
                    heavy=bool(heavy),
                    impact_tier=("heavy" if heavy else "normal"),
                    sound_gain=min(1.25, 0.94 + self._presentation_scale(
                        self.selected_weapon, heavy=heavy,
                    ) * 0.12),
                )
            except Exception:
                pass
        self.last_hit_id = None if last_target is None else last_target.entity_id
        if last_target is not None:
            suffix = "、".join(hit_names)
            label = "重擊" if heavy else "命中"
            self.events.emit("message", text=f"{self.selected_name}{label} {suffix}  傷害 {damage:.0f}")
        targets = [pair[1] for pair in candidates[:max_targets]]
        if targets:
            avg_x = sum(float(target.x) for target in targets) / float(len(targets))
            avg_y = sum(
                float(target.y) - float(target.height()) * 0.5 for target in targets
            ) / float(len(targets))
            self.emit_external_impact(
                self.selected_weapon,
                heavy=heavy,
                x=avg_x,
                y=avg_y,
                direction=(facing, -0.10 if heavy else -0.04),
                targets=targets,
                delivery="melee",
                metadata={"damage": damage, "knockback": knockback},
            )
        return targets

    def _damage_creatures_radial(self, row, heavy=True):
        """Apply a true 360-degree attack around the player.

        Unlike the ordinary facing hitbox this intentionally has no front-side
        filter.  It is used by the whip heavy and remains a single authoritative
        damage event even though the visual lash completes a full circle.
        """
        p = self.player
        radius = TILE_SIZE * max(0.25, self.effective_reach_tiles(self.selected_weapon, heavy=True))
        cx = float(p.x)
        cy = float(p.y) - float(p.height()) * 0.46
        candidates = []
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if not self._circle_aabb(cx, cy, radius, cb):
                continue
            distance = math.hypot(float(entity.x) - cx, float(entity.y) - cy)
            candidates.append((distance, entity))
        candidates.sort(key=lambda pair: pair[0])
        damage = max(1.0, float(row.get("damage", 27.0))) * max(1.0, float(row.get("heavy_damage_mult", 1.5)))
        knockback = self._effective_knockback(row, heavy=True)
        max_targets = max(1, int(row.get("heavy_max_targets", 8) or 8))
        names = []
        last_target = None
        for _distance, target in candidates[:max_targets]:
            self._provoke_creature(target)
            target.hp = max(0.0, float(target.hp) - damage)
            target.hurt_timer = 0.24
            direction = 1.0 if float(target.x) >= cx else -1.0
            target.vx = direction * knockback
            last_target = target
            names.append(str(target.name))
            try:
                self.events.emit(
                    "audio_melee_hit", weapon=str(self.selected_weapon),
                    sound_key=str(row.get("hit_sound", "dagger_hit")),
                    target_id=str(target.entity_id), heavy=True,
                    impact_tier="heavy",
                    sound_gain=min(1.25, 0.94 + self._presentation_scale(
                        self.selected_weapon, heavy=True,
                    ) * 0.12),
                )
            except Exception:
                pass
        self.last_hit_id = None if last_target is None else last_target.entity_id
        if names:
            self.events.emit("message", text="甩鞭旋風重擊 %s  傷害 %.0f" % ("、".join(names), damage))
        targets = [pair[1] for pair in candidates[:max_targets]]
        if targets:
            self.emit_external_impact(
                self.selected_weapon,
                heavy=True,
                x=cx,
                y=cy,
                direction=(0.0, -1.0),
                targets=targets,
                delivery="radial",
                metadata={"damage": damage, "knockback": knockback, "radius": radius},
            )
        return targets

    def _aim_direction(self):
        facing = 1 if int(getattr(self.player, "facing", 1)) >= 0 else -1
        try:
            dx, dy = self.aim_provider() if callable(self.aim_provider) else (facing, 0.0)
            dx = float(dx); dy = float(dy)
        except Exception:
            dx, dy = float(facing), 0.0
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return float(facing), 0.0
        return dx / length, dy / length

    def _projectile_visual_binding(self, state):
        """Return an edited projectile flipbook binding, never a runtime default."""
        try:
            candidate = self.asset_registry.combat_binding(self._weapon_asset_id(), str(state)) if self.asset_registry is not None else None
        except Exception:
            candidate = None
        if not isinstance(candidate, dict):
            return None
        if str(candidate.get("render_mode", "runtime") or "runtime").lower() != "authored":
            return None
        if str(candidate.get("motion", "follow") or "follow").lower() != "projectile":
            return None
        return self._boost_authored_binding(
            candidate,
            self.selected_weapon,
            heavy=(str(state) == "heavy_attack"),
        )

    def _append_projectile(self, payload):
        self._slash_serial += 1
        row = dict(payload)
        row["id"] = self._slash_serial
        row.setdefault("weapon", str(self.selected_weapon))
        row.setdefault("hit_ids", set())
        row.setdefault("trail", [])
        row.setdefault("delay", 0.0)
        row.setdefault("bounces", 0)
        row.setdefault("max_hits", 1 if bool(row.get("consume_on_hit", True)) else 12)
        self.slash_projectiles.append(row)
        if len(self.slash_projectiles) > self.MAX_WEAPON_PROJECTILES:
            self.slash_projectiles = self.slash_projectiles[-self.MAX_WEAPON_PROJECTILES:]
        return row

    def _spawn_greatsword_crescent(self, row):
        p = self.player
        facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
        speed = max(80.0, float(row.get("heavy_projectile_speed", 310.0))) * self._projectile_profile_scale(
            "greatsword", "speed", heavy=True,
        )
        life = max(0.12, float(row.get("heavy_projectile_life", 0.62))) * self._projectile_profile_scale(
            "greatsword", "life", heavy=True,
        )
        radius = max(5.0, float(row.get("heavy_projectile_radius", 12.0))) * self._projectile_profile_scale(
            "greatsword", "radius", heavy=True,
        )
        spawn_offset=max(10.0,float(row.get("heavy_projectile_spawn_offset",30.0) or 30.0))
        authored_binding=None
        try:
            candidate=self.asset_registry.combat_binding(self._weapon_asset_id(),"heavy_attack") if self.asset_registry is not None else None
            if (
                isinstance(candidate,dict) and
                str(candidate.get("render_mode","fix54") or "fix54").lower()=="authored" and
                str(candidate.get("motion","follow") or "follow").lower()=="projectile"
            ):
                authored_binding=self._boost_authored_binding(
                    candidate, "greatsword", heavy=True,
                )
        except Exception:
            authored_binding=None
        if authored_binding and self.selected_weapon == "master_sword":
            authored_binding["scale"] = float(authored_binding.get("scale", 1.0))*2.0
        self._append_projectile({
            "kind": "greatsword_crescent",
            "weapon":str(self.selected_weapon),
            "x": float(p.x + facing * (p.width() * 0.65 + spawn_offset)),
            "y": float(p.y - p.height() * 0.56),
            "vx": float(facing * speed), "vy": 0.0,
            "life": life, "max_life": life, "radius": radius,
            "damage": max(1.0, float(row.get("heavy_projectile_damage", 42.0))),
            "knockback": max(80.0, self._effective_knockback(
                row, heavy=True, weapon_id="greatsword",
            ) * 0.78),
            # FIX48 visual size is independent from gameplay collision radius.
            # This lets the moon blade read 50% larger without silently
            # extending its damage reach.
            "visual_scale": max(1.0, float(row.get("heavy_projectile_visual_scale", 1.0) or 1.0)) * self._presentation_scale(
                "greatsword", heavy=True,
            ),
            "impact_vfx_scale": self._presentation_scale("greatsword", heavy=True),
            # A custom heavy_effect can replace only the projectile's visuals
            # while movement, collision, lifetime and damage stay authoritative.
            "authored_visual":bool(authored_binding),
            "effect_state":str((authored_binding or {}).get("effect_state","heavy_effect")),
            "binding":authored_binding or {},
            "facing": facing, "hit_ids": set(), "consume_on_hit": True,
            "heavy": True,
        })

    def _spawn_energy_arrows(self, row, heavy=False):
        p = self.player
        aim_x, aim_y = self._aim_direction()
        facing = 1 if aim_x >= 0.0 else -1
        state = "heavy_attack" if heavy else "normal_attack"
        authored = self._projectile_visual_binding(state)
        spawn_x = float(p.x + facing * (p.width() * 0.62 + 12.0))
        spawn_y = float(p.y - p.height() * 0.58)
        if not heavy:
            speed = max(120.0, float(row.get("projectile_speed", 520.0))) * self._projectile_profile_scale(
                "energy_bow", "speed", heavy=False,
            )
            life = max(0.20, float(row.get("projectile_life", 1.35))) * self._projectile_profile_scale(
                "energy_bow", "life", heavy=False,
            )
            self._append_projectile({
                "kind": "energy_arrow", "weapon": "energy_bow",
                "x": spawn_x, "y": spawn_y,
                "vx": aim_x * speed, "vy": aim_y * speed,
                "gravity": 0.0, "life": life, "max_life": life,
                "radius": max(2.0, float(row.get("projectile_radius", 4.5))) * self._projectile_profile_scale(
                    "energy_bow", "radius", heavy=False,
                ),
                "damage": max(1.0, float(row.get("projectile_damage", row.get("damage", 30.0)))),
                "knockback": self._effective_knockback(
                    row, heavy=False, weapon_id="energy_bow",
                ),
                "facing": facing, "consume_on_hit": True,
                "visual_scale": self._presentation_scale("energy_bow", heavy=False),
                "impact_vfx_scale": self._presentation_scale("energy_bow", heavy=False),
                "authored_visual": bool(authored),
                "effect_state": str((authored or {}).get("effect_state", "normal_effect")),
                "binding": authored or {},
            })
            return

        # Dense arrow rain: every arrow starts by travelling upward, then
        # gravity turns the set into staggered parabolas that fall over a broad
        # forward area. A tiny launch delay keeps the volley readable.
        count = max(6, min(16, int(row.get("heavy_arrow_count", 12) or 12)))
        base_speed = max(110.0, float(row.get("heavy_projectile_speed", 230.0))) * self._projectile_profile_scale(
            "energy_bow", "speed", heavy=True,
        )
        gravity = max(240.0, float(row.get("heavy_projectile_gravity", 720.0)))
        life = max(0.80, float(row.get("heavy_projectile_life", 1.75))) * self._projectile_profile_scale(
            "energy_bow", "life", heavy=True,
        )
        for index in range(count):
            t = float(index) / float(max(1, count - 1))
            vx = facing * base_speed * (0.52 + 0.86 * t)
            vy = -(430.0 + (index % 4) * 24.0 + 36.0 * math.sin(t * math.pi))
            self._append_projectile({
                "kind": "energy_arrow", "weapon": "energy_bow", "heavy": True,
                "x": spawn_x + facing * float(index % 3) * 2.0,
                "y": spawn_y - float(index % 2) * 2.0,
                "vx": vx, "vy": vy, "gravity": gravity,
                "life": life, "max_life": life,
                "radius": max(2.0, float(row.get("heavy_projectile_radius", 4.0))) * self._projectile_profile_scale(
                    "energy_bow", "radius", heavy=True,
                ),
                "damage": max(1.0, float(row.get("heavy_projectile_damage", 18.0))),
                "knockback": self._effective_knockback(
                    row, heavy=True, weapon_id="energy_bow",
                ) * 0.72,
                "facing": facing, "delay": (index % 6) * 0.032,
                "consume_on_hit": True,
                "visual_scale": self._presentation_scale("energy_bow", heavy=True),
                "impact_vfx_scale": self._presentation_scale("energy_bow", heavy=True),
                "authored_visual": bool(authored),
                "effect_state": str((authored or {}).get("effect_state", "heavy_effect")),
                "binding": authored or {},
            })

    def _spawn_laser(self, row, heavy=False):
        p = self.player
        aim_x, aim_y = self._aim_direction()
        prefix = "heavy_" if heavy else ""
        speed = max(180.0, float(row.get(prefix + "projectile_speed", row.get("projectile_speed", 760.0)))) * self._projectile_profile_scale(
            "laser_gun", "speed", heavy=heavy,
        )
        life = max(0.35, float(row.get(prefix + "projectile_life", row.get("projectile_life", 1.65)))) * self._projectile_profile_scale(
            "laser_gun", "life", heavy=heavy,
        )
        reflections = max(1, int(row.get(prefix + "projectile_reflections", row.get("projectile_reflections", 3)) or 3))
        authored = self._projectile_visual_binding("heavy_attack" if heavy else "normal_attack")
        count = 1 if not heavy else max(1, min(
            5, int(row.get("heavy_projectile_count", 5) or 5),
        ))
        spread = 0.0 if count <= 1 else math.radians(max(
            0.0, min(36.0, float(row.get("heavy_projectile_spread_degrees", 18.0) or 18.0)),
        ))
        base_angle = math.atan2(aim_y, aim_x)
        radius = max(2.0, float(row.get(prefix + "projectile_radius", row.get("projectile_radius", 4.0)))) * self._projectile_profile_scale(
            "laser_gun", "radius", heavy=heavy,
        )
        beam_length = max(10.0, min(36.0, float(row.get(
            prefix + "projectile_visual_length",
            row.get("projectile_visual_length", 18.0),
        ) or 18.0)))
        for index in range(count):
            offset = 0.0 if count <= 1 else (-0.5 + float(index) / float(count - 1)) * spread
            angle = base_angle + offset
            dx = math.cos(angle); dy = math.sin(angle)
            facing = 1 if dx >= 0.0 else -1
            self._append_projectile({
                "kind": "laser", "weapon": "laser_gun", "heavy": bool(heavy),
                "x": float(p.x + dx * (p.width() * 0.65 + 14.0)),
                "y": float(p.y - p.height() * 0.57 + dy * 8.0),
                "vx": dx * speed, "vy": dy * speed,
                "gravity": 0.0, "life": life, "max_life": life,
                "radius": radius,
                "beam_length": beam_length,
                "damage": max(1.0, float(row.get(prefix + "projectile_damage", row.get("projectile_damage", row.get("damage", 25.0))))),
                "knockback": self._effective_knockback(
                    row, heavy=heavy, weapon_id="laser_gun",
                ),
                "facing": facing, "max_bounces": reflections, "bounces": 0,
                "max_hits": max(1, int(row.get(
                    "heavy_max_targets" if heavy else "max_targets", 6 if heavy else 4,
                ) or (6 if heavy else 4))),
                "consume_on_hit": False,
                "visual_scale": self._presentation_scale("laser_gun", heavy=heavy),
                "impact_vfx_scale": self._presentation_scale("laser_gun", heavy=heavy),
                "authored_visual": bool(authored),
                "effect_state": str((authored or {}).get("effect_state", "heavy_effect" if heavy else "normal_effect")),
                "binding": authored or {},
            })

    def _spawn_yoyo(self, row, heavy=False):
        p = self.player
        aim_x, aim_y = self._aim_direction()
        facing = 1 if aim_x >= 0.0 else -1
        prefix = "heavy_" if heavy else ""
        life = max(0.45, float(row.get(
            prefix + "projectile_life", row.get("projectile_life", 1.10),
        ))) * self._projectile_profile_scale("yoyo", "life", heavy=heavy)
        radius = max(5.0, float(row.get(
            prefix + "projectile_radius", row.get("projectile_radius", 8.0),
        ))) * self._projectile_profile_scale("yoyo", "radius", heavy=heavy)
        speed = max(180.0, float(row.get("projectile_speed", 520.0))) * self._projectile_profile_scale(
            "yoyo", "speed", heavy=heavy,
        )
        authored = self._projectile_visual_binding("heavy_attack" if heavy else "normal_attack")
        spawn_x = float(p.x + aim_x * (p.width() * 0.62 + 10.0))
        spawn_y = float(p.y - p.height() * 0.56 + aim_y * 8.0)
        self._append_projectile({
            "kind": "yoyo", "weapon": "yoyo", "heavy": bool(heavy),
            "phase": "orbit" if heavy else "outbound", "elapsed": 0.0,
            "x": spawn_x, "y": spawn_y,
            "vx": aim_x * speed, "vy": aim_y * speed,
            "aim_x": aim_x, "aim_y": aim_y,
            "life": life, "max_life": life, "gravity": 0.0,
            "radius": radius,
            "range": max(64.0, min(300.0, float(row.get("projectile_range", 168.0) or 168.0))),
            "return_speed": max(220.0, float(row.get("projectile_return_speed", 620.0) or 620.0)),
            "orbit_radius": max(48.0, min(180.0, float(row.get("heavy_orbit_radius", 104.0) or 104.0))),
            "orbit_turns": max(1.0, min(5.0, float(row.get("heavy_orbit_turns", 2.6) or 2.6))),
            "damage": max(1.0, float(row.get(
                prefix + "projectile_damage", row.get("projectile_damage", row.get("damage", 24.0)),
            ))),
            "knockback": self._effective_knockback(row, heavy=heavy, weapon_id="yoyo"),
            "facing": facing, "consume_on_hit": False,
            "max_hits": max(1, int(row.get("heavy_max_targets" if heavy else "max_targets", 8 if heavy else 3))),
            "visual_scale": self._presentation_scale("yoyo", heavy=heavy),
            "impact_vfx_scale": self._presentation_scale("yoyo", heavy=heavy),
            "authored_visual": bool(authored),
            "effect_state": str((authored or {}).get("effect_state", "heavy_effect" if heavy else "normal_effect")),
            "binding": authored or {},
        })

    def _spawn_battle_top(self, row, heavy=False):
        p = self.player
        aim_x, aim_y = self._aim_direction()
        facing = 1 if aim_x >= 0.0 else -1
        prefix = "heavy_" if heavy else ""
        speed = max(180.0, float(row.get(
            prefix + "projectile_speed", row.get("projectile_speed", 410.0),
        ))) * self._projectile_profile_scale("battle_top", "speed", heavy=heavy)
        life = max(0.60, float(row.get(
            prefix + "projectile_life", row.get("projectile_life", 1.45),
        ))) * self._projectile_profile_scale("battle_top", "life", heavy=heavy)
        radius = max(6.0, float(row.get(
            prefix + "projectile_radius", row.get("projectile_radius", 10.0),
        ))) * self._projectile_profile_scale("battle_top", "radius", heavy=heavy)
        authored = self._projectile_visual_binding("heavy_attack" if heavy else "normal_attack")
        self._append_projectile({
            "kind": "battle_top", "weapon": "battle_top", "heavy": bool(heavy),
            "x": float(p.x + facing * (p.width() * 0.62 + 12.0)),
            "y": float(p.y - p.height() * 0.38),
            "vx": aim_x * speed, "vy": min(-75.0, aim_y * speed),
            "gravity": max(180.0, float(row.get(
                prefix + "projectile_gravity", row.get("projectile_gravity", 760.0),
            ))),
            "life": life, "max_life": life, "radius": radius,
            "damage": max(1.0, float(row.get(
                prefix + "projectile_damage", row.get("projectile_damage", row.get("damage", 32.0)),
            ))),
            "knockback": self._effective_knockback(row, heavy=heavy, weapon_id="battle_top"),
            "facing": facing, "consume_on_hit": False,
            "max_hits": max(1, int(row.get("heavy_max_targets" if heavy else "max_targets", 10 if heavy else 4))),
            "max_bounces": max(1, int(row.get(
                prefix + "projectile_bounces", row.get("projectile_bounces", 4),
            ))),
            "bounces": 0, "spin": 0.0,
            "visual_scale": self._presentation_scale("battle_top", heavy=heavy),
            "impact_vfx_scale": self._presentation_scale("battle_top", heavy=heavy),
            "authored_visual": bool(authored),
            "effect_state": str((authored or {}).get("effect_state", "heavy_effect" if heavy else "normal_effect")),
            "binding": authored or {},
        })

    def _spawn_rockets(self, row, heavy=False):
        p = self.player
        aim_x, aim_y = self._aim_direction()
        prefix = "heavy_" if heavy else ""
        count = 1 if not heavy else max(1, min(3, int(row.get("heavy_projectile_count", 3) or 3)))
        spread = 0.0 if count <= 1 else math.radians(max(
            0.0, min(28.0, float(row.get("heavy_projectile_spread_degrees", 13.0) or 13.0)),
        ))
        base_angle = math.atan2(aim_y, aim_x)
        speed = max(160.0, float(row.get(
            prefix + "projectile_speed", row.get("projectile_speed", 455.0),
        ))) * self._projectile_profile_scale("rpg_launcher", "speed", heavy=heavy)
        life = max(0.60, float(row.get(
            prefix + "projectile_life", row.get("projectile_life", 2.0),
        ))) * self._projectile_profile_scale("rpg_launcher", "life", heavy=heavy)
        radius = max(3.0, float(row.get(
            prefix + "projectile_radius", row.get("projectile_radius", 6.0),
        ))) * self._projectile_profile_scale("rpg_launcher", "radius", heavy=heavy)
        authored = self._projectile_visual_binding("heavy_attack" if heavy else "normal_attack")
        for index in range(count):
            offset = 0.0 if count <= 1 else (-0.5 + float(index) / float(count - 1)) * spread
            angle = base_angle + offset
            dx = math.cos(angle); dy = math.sin(angle)
            facing = 1 if dx >= 0.0 else -1
            self._append_projectile({
                "kind": "rocket", "weapon": "rpg_launcher", "heavy": bool(heavy),
                "x": float(p.x + dx * (p.width() * 0.65 + 18.0)),
                "y": float(p.y - p.height() * 0.57 + dy * 10.0),
                "vx": dx * speed, "vy": dy * speed,
                "gravity": 0.0, "life": life, "max_life": life, "radius": radius,
                "damage": max(1.0, float(row.get(
                    prefix + "projectile_damage", row.get("projectile_damage", row.get("damage", 64.0)),
                ))),
                "explosion_damage": max(1.0, float(row.get(
                    prefix + "explosion_damage", row.get("explosion_damage", 74.0),
                ))),
                "explosion_radius": max(20.0, min(180.0, float(row.get(
                    prefix + "explosion_radius", row.get("explosion_radius", 72.0),
                )))),
                "knockback": self._effective_knockback(row, heavy=heavy, weapon_id="rpg_launcher"),
                "facing": facing, "consume_on_hit": True,
                "max_hits": max(1, int(row.get("heavy_max_targets" if heavy else "max_targets", 12 if heavy else 8))),
                "visual_scale": self._presentation_scale("rpg_launcher", heavy=heavy),
                "impact_vfx_scale": self._presentation_scale("rpg_launcher", heavy=heavy),
                "authored_visual": bool(authored),
                "effect_state": str((authored or {}).get("effect_state", "heavy_effect" if heavy else "normal_effect")),
                "binding": authored or {},
            })

    def _spawn_tnt(self, row, heavy=False):
        """Lob one player-owned TNT bundle along a bounded parabola."""
        p = self.player
        aim_x, aim_y = self._aim_direction()
        prefix = "heavy_" if heavy else ""
        speed = max(140.0, float(row.get(
            prefix + "projectile_speed", row.get("projectile_speed", 330.0),
        ))) * self._projectile_profile_scale("tnt", "speed", heavy=heavy)
        life = max(0.45, float(row.get(
            prefix + "projectile_life", row.get("projectile_life", 1.30),
        ))) * self._projectile_profile_scale("tnt", "life", heavy=heavy)
        radius = max(5.0, float(row.get(
            prefix + "projectile_radius", row.get("projectile_radius", 8.0),
        ))) * self._projectile_profile_scale("tnt", "radius", heavy=heavy)
        gravity = max(180.0, float(row.get(
            prefix + "projectile_gravity", row.get("projectile_gravity", 720.0),
        )))
        facing = 1 if aim_x >= 0.0 else -1
        # Preserve vertical aiming, but guarantee enough initial lift for a
        # clearly readable throw even while the reticle is horizontal/downward.
        launch_vy = aim_y * speed - (175.0 if heavy else 150.0)
        launch_vy = min(-78.0, launch_vy)
        authored = self._projectile_visual_binding("heavy_attack" if heavy else "normal_attack")
        blast_width = int(row.get(
            "heavy_blast_width" if heavy else "blast_width", 3 if heavy else 1,
        ) or (3 if heavy else 1))
        self._append_projectile({
            "kind": "tnt", "weapon": "tnt", "heavy": bool(heavy),
            "x": float(p.x + facing * (p.width() * 0.58 + 10.0)),
            "y": float(p.y - p.height() * 0.67),
            "vx": aim_x * speed, "vy": launch_vy,
            "gravity": gravity, "life": life, "max_life": life,
            "radius": radius,
            # TNT applies explosion damage only; keeping the field here makes
            # diagnostics/catalog tooling compatible with other projectiles.
            "damage": max(1.0, float(row.get(
                prefix + "projectile_damage", row.get("projectile_damage", row.get("damage", 42.0)),
            ))),
            "explosion_damage": max(1.0, float(row.get(
                prefix + "explosion_damage", row.get("explosion_damage", 58.0),
            ))),
            "explosion_radius": max(20.0, min(200.0, float(row.get(
                prefix + "explosion_radius", row.get("explosion_radius", 62.0),
            )))),
            "blast_width": 3 if blast_width >= 3 else 1,
            "knockback": self._effective_knockback(row, heavy=heavy, weapon_id="tnt"),
            "facing": facing, "consume_on_hit": True,
            "max_hits": max(1, int(row.get(
                "heavy_max_targets" if heavy else "max_targets", 12 if heavy else 6,
            ))),
            "visual_scale": self._presentation_scale("tnt", heavy=heavy),
            "impact_vfx_scale": self._presentation_scale("tnt", heavy=heavy),
            "authored_visual": bool(authored),
            "effect_state": str((authored or {}).get(
                "effect_state", "heavy_effect" if heavy else "normal_effect",
            )),
            "binding": authored or {},
        })

    def _spawn_attack_projectiles(self, row, heavy=False):
        delivery = str(row.get("delivery", "melee") or "melee")
        style = str(row.get("heavy_style", "") or "") if heavy else ""
        if delivery.startswith("boss_"):
            controller = self.boss_weapon_system
            if controller is not None:
                controller.fire(str(self.selected_weapon), bool(heavy))
        elif delivery == "energy_arrow":
            self._spawn_energy_arrows(row, heavy=heavy)
        elif delivery == "laser":
            self._spawn_laser(row, heavy=heavy)
        elif delivery == "yoyo":
            self._spawn_yoyo(row, heavy=heavy)
        elif delivery == "battle_top":
            self._spawn_battle_top(row, heavy=heavy)
        elif delivery == "rocket":
            self._spawn_rockets(row, heavy=heavy)
        elif delivery == "tnt":
            self._spawn_tnt(row, heavy=heavy)
        elif heavy and style == "flying_crescent":
            self._spawn_greatsword_crescent(row)

    def _apply_heavy_world_effect(self, row, hitbox):
        style = str(row.get("heavy_style", ""))
        tools = self.tool_system
        if tools is None:
            return
        state = "heavy_attack"
        if style == "ground_slam":
            p = self.player
            facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
            tx = int((float(p.x) + facing * TILE_SIZE * 0.72) // TILE_SIZE)
            approx_ty = int((float(p.y) + 2.0) // TILE_SIZE)
            trigger = self._authored_event_progress(state, "impact", 0.62)
            self._pending_world_effect = {
                "style": "ground_slam", "tx": tx, "approx_ty": approx_ty,
                "trigger_progress": trigger, "fired": False,
            }
        elif style == "horizontal_cleave":
            # An authored impact marker delays vegetation removal until the axe
            # visually reaches it. Without a marker the old immediate behaviour
            # remains unchanged.
            action_fallback = self._authored_event_progress(state, "action", 0.0)
            trigger = self._authored_event_progress(state, "impact", action_fallback)
            if trigger <= 0.001:
                try:
                    removed = int(tools.heavy_axe_clear_plants(hitbox) or 0)
                    if removed > 0:
                        p = self.player
                        facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
                        self.emit_external_impact(
                            self.selected_weapon,
                            heavy=True,
                            x=float(p.x) + facing * TILE_SIZE * 0.95,
                            y=float(p.y) - float(p.height()) * 0.45,
                            direction=(facing, -0.18),
                            targets=(),
                            delivery="world_cleave",
                            world_hit=True,
                            magnitude_scale=0.72,
                            metadata={"removed_plants": removed},
                        )
                except Exception:
                    pass
            else:
                self._pending_world_effect = {
                    "style": "horizontal_cleave",
                    "trigger_progress": trigger, "fired": False,
                }

    def _update_pending_attack_hit(self, prev_progress, new_progress):
        pending = self._pending_attack_hit
        if not pending:
            return
        trigger = float(pending.get("trigger_progress", 0.0))
        if not self._crossed_progress(prev_progress, new_progress, trigger) and not (self.swing_timer <= 0.0):
            return
        row = self.weapon_def()
        heavy = bool(pending.get("heavy", False))
        if bool(pending.get("radial", False)):
            self._damage_creatures_radial(row, heavy=True)
        else:
            self._damage_creatures(row, self._hitbox(row, heavy=heavy), heavy=heavy)
        self._pending_attack_hit = None

    def _update_pending_world_effect(self, prev_progress, new_progress):
        pending = self._pending_world_effect
        if not pending:
            return
        trigger = float(pending.get("trigger_progress", 0.62))
        should_fire = (not bool(pending.get("fired", False))) and (
            self._crossed_progress(prev_progress, new_progress, trigger) or self.swing_timer <= 0.0
        )
        if not should_fire:
            return
        tools = self.tool_system
        style = str(pending.get("style", ""))
        if tools is not None:
            if style == "ground_slam":
                removed = False
                try:
                    removed = bool(tools.heavy_hammer_remove_ground_layer(
                        int(pending.get("tx", 0)), int(pending.get("approx_ty", 0)),
                    ))
                except Exception:
                    pass
                self.emit_external_impact(
                    self.selected_weapon,
                    heavy=True,
                    x=(int(pending.get("tx", 0)) + 0.5) * TILE_SIZE,
                    y=float(int(pending.get("approx_ty", 0))) * TILE_SIZE,
                    direction=(0.0, -1.0),
                    targets=(),
                    delivery="ground_slam",
                    world_hit=True,
                    magnitude_scale=1.0,
                    metadata={"terrain_removed": bool(removed)},
                )
            elif style == "horizontal_cleave":
                removed = 0
                try:
                    row = self.weapon_def()
                    removed = int(tools.heavy_axe_clear_plants(
                        self._hitbox(row, heavy=True),
                    ) or 0)
                except Exception:
                    pass
                if removed > 0:
                    p = self.player
                    facing = 1 if int(getattr(p, "facing", 1)) >= 0 else -1
                    self.emit_external_impact(
                        self.selected_weapon,
                        heavy=True,
                        x=float(p.x) + facing * TILE_SIZE * 0.95,
                        y=float(p.y) - float(p.height()) * 0.45,
                        direction=(facing, -0.18),
                        targets=(),
                        delivery="world_cleave",
                        world_hit=True,
                        magnitude_scale=0.72,
                        metadata={"removed_plants": removed},
                    )
        pending["fired"] = True
        self._pending_world_effect = None

    def _update_pending_projectile(self, prev_progress, new_progress):
        pending = self._pending_projectile
        if not pending:
            return
        trigger = float(pending.get("trigger_progress", 0.0))
        if not self._crossed_progress(prev_progress, new_progress, trigger) and not (self.swing_timer <= 0.0):
            return
        self._spawn_attack_projectiles(
            self.weapon_def(), heavy=bool(pending.get("heavy", False))
        )
        self._pending_projectile = None

    def attack(self, charge_seconds=0.0, heavy=None):
        """Release-triggered attack. ``heavy`` defaults from held duration."""
        if self.cooldown > 0.0:
            return False
        row = self.weapon_def()
        charge_seconds = max(0.0, float(charge_seconds or 0.0))
        if heavy is None:
            heavy = charge_seconds >= max(0.10, float(row.get("heavy_charge_seconds", 0.55)))
        heavy = bool(heavy)
        self.last_durability_result = None
        if row.get("boss_exclusive"):
            if self.boss_weapon_system is None or not self.boss_weapon_system.can_start(
                str(self.selected_weapon), heavy, announce=True
            ):
                return False
        if heavy and not row.get("defer_heavy_commit") and callable(self.heavy_use_callback):
            try:
                durability = self.heavy_use_callback(
                    str(row.get("item_id", self.selected_item_id)),
                    str(self.selected_weapon),
                )
            except Exception as exc:
                try:
                    self.events.emit("message", text="武器耐久度更新失敗：" + str(exc))
                except Exception:
                    pass
                return False
            accepted = bool(
                durability.get("accepted", False)
                if isinstance(durability, dict) else durability
            )
            if not accepted:
                try:
                    self.events.emit("message", text="武器已不存在，無法發動重攻擊")
                except Exception:
                    pass
                return False
            self.last_durability_result = (
                dict(durability) if isinstance(durability, dict)
                else {"accepted": True}
            )
        self.last_charge_seconds = charge_seconds
        self.current_attack_kind = "heavy" if heavy else "normal"
        self._pending_world_effect = None
        self._pending_attack_hit = None
        self._pending_projectile = None
        self._pending_vfx = None

        if heavy:
            self.cooldown = max(0.12, float(row.get("heavy_cooldown", row.get("cooldown", 0.30) * 1.65)))
            self.swing_duration = max(0.10, min(self.cooldown, float(row.get("heavy_attack_duration", self.cooldown * 0.76))))
            self.swing_duration = self._authored_animation_duration("heavy_attack", self.swing_duration)
        else:
            self.cooldown = max(0.08, float(row.get("cooldown", 0.30)))
            self.swing_duration = max(0.08, min(self.cooldown, float(row.get("attack_duration", self.cooldown * 0.70))))
            self.swing_duration = self._authored_animation_duration("normal_attack", self.swing_duration)
        # Authored clips may be longer than the legacy cooldown. Never allow a
        # second attack to start before its edited animation finishes.
        self.cooldown = max(self.cooldown, self.swing_duration)
        self.swing_timer = self.swing_duration
        self.player.attack_timer = max(float(getattr(self.player, "attack_timer", 0.0)), self.swing_duration)
        self._attack_serial += 1
        presentation_scale = self._presentation_scale(self.selected_weapon, heavy=heavy)
        try:
            self.events.emit(
                "audio_melee_swing",
                weapon=str(self.selected_weapon),
                sound_key=str(row.get("swing_sound", "sword_swing")),
                heavy=bool(heavy),
                attack_serial=int(self._attack_serial),
                impact_tier=("heavy" if heavy else "normal"),
                sound_gain=min(1.20, 0.90 + presentation_scale * 0.12),
            )
        except Exception:
            pass

        hitbox = self._hitbox(row, heavy=heavy)
        state = "heavy_attack" if heavy else "normal_attack"
        hit_trigger = self._authored_event_progress(state, "action", 0.0)
        delivery = str(row.get("delivery", "melee") or "melee")
        try:
            self.events.emit(
                "weapon_attack_started",
                attack_serial=int(self._attack_serial),
                weapon=str(self.selected_weapon),
                delivery=delivery,
                heavy=bool(heavy),
                duration=float(self.swing_duration),
                cooldown=float(self.cooldown),
                reach_tiles=float(self.effective_reach_tiles(self.selected_weapon, heavy=heavy)),
                action_progress=float(hit_trigger),
                vfx_scale=float(presentation_scale),
            )
        except Exception:
            pass
        radial = bool(heavy and str(row.get("heavy_style", "") or "") == "whip_spin")
        if delivery == "melee":
            if hit_trigger <= 0.001:
                if radial:
                    self._damage_creatures_radial(row, heavy=True)
                else:
                    self._damage_creatures(row, hitbox, heavy=heavy)
            else:
                self._pending_attack_hit = {
                    "heavy": bool(heavy), "radial": radial,
                    "trigger_progress": hit_trigger,
                }
        _vfx=self._authored_vfx_spec(state)
        if isinstance(_vfx,dict):
            if float(_vfx.get("trigger_progress",0.0))<=0.001:self._spawn_authored_vfx(_vfx)
            else:self._pending_vfx=_vfx
        if heavy:
            self._apply_heavy_world_effect(row, hitbox)
        needs_projectile = delivery.startswith("boss_") or delivery in (
            "energy_arrow", "laser", "yoyo", "battle_top", "rocket", "tnt",
        ) or (
            heavy and str(row.get("heavy_style", "")) == "flying_crescent"
        )
        if needs_projectile:
            projectile_trigger = self._authored_event_progress(state, "projectile", hit_trigger)
            if projectile_trigger <= 0.001:
                self._spawn_attack_projectiles(row, heavy=heavy)
            else:
                self._pending_projectile = {
                    "trigger_progress": projectile_trigger, "heavy": bool(heavy),
                }
        return True

    def _projectile_hits_world(self, cx, cy, radius):
        """Test a shot against the world's authoritative visible geometry.

        Layer-mined terrain, melting ice and one-way rainforest limbs occupy
        only part of their storage cell.  The old weapon path expanded every
        solid tile back to a full 40x40 box, so an arrow or laser could strike
        empty pixels above a remaining LOW/MID layer (the "virtual wall" seen
        beside the reticle).  TileWorld already exposes the exact rectangles
        used by player physics; every weapon projectile now shares those.
        """
        world = self.world
        if world is None:
            return False
        cx = float(cx); cy = float(cy); radius = max(1.0, float(radius))
        min_tx = int(math.floor((cx - radius) / TILE_SIZE))
        max_tx = int(math.floor((cx + radius) / TILE_SIZE))
        min_ty = int(math.floor((cy - radius) / TILE_SIZE))
        max_ty = int(math.floor((cy + radius) / TILE_SIZE))
        if min_tx < 0 or min_ty < 0 or max_tx >= world.width_tiles or max_ty >= world.height_tiles:
            return True

        probe = (cx - radius, cy - radius, cx + radius, cy + radius)
        exact_cells = getattr(world, "solid_cells_in_rect", None)
        if callable(exact_cells):
            try:
                for _tx, _ty, _tile_id, solid_rect in exact_cells(probe, padding=1):
                    if self._circle_aabb(cx, cy, radius, solid_rect):
                        return True
                return False
            except (AttributeError, TypeError, ValueError):
                # Lightweight legacy/test worlds may expose an incompatible
                # helper.  Keep the historical full-cell fallback for them;
                # production TileWorld always takes the exact path above.
                pass

        for tx in range(min_tx, max_tx + 1):
            for ty in range(min_ty, max_ty + 1):
                try:
                    if not tile_def(world.get_tile(tx, ty)).solid:
                        continue
                except Exception:
                    continue
                box = (tx * TILE_SIZE, ty * TILE_SIZE, (tx + 1) * TILE_SIZE, (ty + 1) * TILE_SIZE)
                if self._circle_aabb(cx, cy, radius, box):
                    return True
        return False

    def _hit_projectile_creatures(self, shot):
        radius = max(2.0, float(shot.get("radius", 4.0)))
        hit_ids = shot.setdefault("hit_ids", set())
        max_hits = max(1, min(16, int(shot.get("max_hits", 1) or 1)))
        # A laser may continue reflecting after reaching its damage budget, but
        # it no longer scans/damages an unbounded number of scene creatures.
        if len(hit_ids) >= max_hits:
            return False
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            eid = str(entity.entity_id)
            if eid in hit_ids:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if not self._circle_aabb(float(shot.get("x", 0.0)), float(shot.get("y", 0.0)), radius, cb):
                continue
            damage = max(1.0, float(shot.get("damage", 25.0)))
            self._provoke_creature(entity)
            entity.hp = max(0.0, float(entity.hp) - damage)
            entity.hurt_timer = 0.22 if bool(shot.get("heavy", False)) else 0.18
            direction = 1.0 if float(shot.get("vx", shot.get("facing", 1.0))) >= 0.0 else -1.0
            entity.vx = direction * max(0.0, float(shot.get("knockback", 80.0)))
            hit_ids.add(eid)
            weapon = str(shot.get("weapon", self.selected_weapon))
            row = self.weapon_def(weapon)
            kind = str(shot.get("kind", ""))
            label = {
                "greatsword_crescent": "巨劍月牙",
                "energy_arrow": "能量箭雨" if bool(shot.get("heavy", False)) else "能量箭",
                "laser": "五重反射雷射" if bool(shot.get("heavy", False)) else "雷射",
                "yoyo": "溜溜球旋風" if bool(shot.get("heavy", False)) else "溜溜球",
                "battle_top": "陀螺龍捲" if bool(shot.get("heavy", False)) else "戰鬥陀螺",
            }.get(kind, str(row.get("name", "武器")))
            try:
                self.events.emit(
                    "audio_melee_hit", weapon=weapon,
                    sound_key=str(row.get("hit_sound", "sword_hit")),
                    target_id=eid, heavy=bool(shot.get("heavy", False)),
                    projectile_kind=kind,
                    sound_gain=min(1.25, 0.94 + self._presentation_scale(
                        weapon, heavy=bool(shot.get("heavy", False)),
                    ) * 0.12),
                )
                self.events.emit("message", text=f"{label}命中 {entity.name}  傷害 {damage:.0f}")
            except Exception:
                pass
            velocity = (
                float(shot.get("vx", shot.get("facing", 1.0))),
                float(shot.get("vy", 0.0)),
            )
            self.emit_external_impact(
                weapon,
                heavy=bool(shot.get("heavy", False)),
                x=float(shot.get("x", entity.x)),
                y=float(shot.get("y", entity.y - entity.height() * 0.5)),
                direction=velocity,
                targets=(entity,),
                delivery=kind or "projectile",
                metadata={
                    "damage": damage,
                    "knockback": float(shot.get("knockback", 0.0)),
                    "projectile_id": int(shot.get("id", 0) or 0),
                    "bounce_count": int(shot.get("bounces", 0) or 0),
                },
            )
            if bool(shot.get("consume_on_hit", True)):
                return True
            if len(hit_ids) >= max_hits:
                return False
        return False

    def _emit_projectile_world_impact(self, shot, x, y, ricochet=False, final=False):
        weapon = str(shot.get("weapon", self.selected_weapon))
        heavy = bool(shot.get("heavy", False))
        kind = str(shot.get("kind", "projectile") or "projectile")
        scale = 0.24 if ricochet else 0.42
        if final:
            scale = 0.48
        payload = self.emit_external_impact(
            weapon,
            heavy=heavy,
            x=float(x),
            y=float(y),
            direction=(float(shot.get("vx", 0.0)), float(shot.get("vy", 0.0))),
            targets=(),
            delivery=kind,
            world_hit=True,
            magnitude_scale=scale,
            allow_hit_stop=(not ricochet or final),
            metadata={
                "impact_kind": "ricochet" if ricochet else "surface",
                "projectile_id": int(shot.get("id", 0) or 0),
                "bounce_count": int(shot.get("bounces", 0) or 0),
                "max_bounces": int(shot.get("max_bounces", 0) or 0),
                "final": bool(final),
            },
        )
        if ricochet:
            try:
                self.events.emit("weapon_ricochet", **payload)
            except Exception:
                pass

    @staticmethod
    def _record_projectile_trail(shot, limit):
        trail = list(shot.get("trail", ()) or ())
        trail.append((
            float(shot.get("x", 0.0)), float(shot.get("y", 0.0)),
        ))
        shot["trail"] = trail[-max(2, min(24, int(limit))):]

    def _advance_yoyo(self, shot, dt):
        """Advance a player-tethered projectile without allocating rope bodies."""
        p = self.player
        shot["elapsed"] = max(0.0, float(shot.get("elapsed", 0.0))) + dt
        heavy = bool(shot.get("heavy", False))
        if heavy or str(shot.get("phase", "")) == "orbit":
            duration = max(0.10, float(shot.get("max_life", 1.0) or 1.0))
            phase = max(0.0, min(1.0, float(shot["elapsed"]) / duration))
            turns = max(1.0, min(5.0, float(shot.get("orbit_turns", 2.6) or 2.6)))
            base_angle = math.atan2(
                float(shot.get("aim_y", 0.0)),
                float(shot.get("aim_x", 1.0)),
            )
            angle = base_angle + phase * turns * math.pi * 2.0
            full_radius = max(32.0, float(shot.get("orbit_radius", 104.0) or 104.0))
            envelope = min(1.0, phase * 5.0, max(0.0, (1.0 - phase) * 6.0))
            orbit_radius = full_radius * (0.30 + 0.70 * envelope)
            shot["x"] = float(p.x) + math.cos(angle) * orbit_radius
            shot["y"] = float(p.y) - float(p.height()) * 0.52 + math.sin(angle) * orbit_radius * 0.72
            shot["vx"] = -math.sin(angle) * full_radius * turns * math.pi * 2.0 / duration
            shot["vy"] = math.cos(angle) * full_radius * 0.72 * turns * math.pi * 2.0 / duration
            shot["spin"] = angle
            self._record_projectile_trail(shot, 14)
            self._hit_projectile_creatures(shot)
            return True

        phase = str(shot.get("phase", "outbound") or "outbound")
        px = float(p.x)
        py = float(p.y) - float(p.height()) * 0.52
        x = float(shot.get("x", px)); y = float(shot.get("y", py))
        if phase == "outbound":
            vx = float(shot.get("vx", 0.0)); vy = float(shot.get("vy", 0.0))
            nx = x + vx * dt; ny = y + vy * dt
            if (
                math.hypot(nx - px, ny - py) >= max(48.0, float(shot.get("range", 168.0)))
                or self._projectile_hits_world(nx, ny, max(2.0, float(shot.get("radius", 8.0))))
            ):
                shot["phase"] = "return"
            else:
                shot["x"] = nx; shot["y"] = ny
        else:
            dx = px - x; dy = py - y
            distance = math.hypot(dx, dy)
            if distance <= max(12.0, float(shot.get("radius", 8.0)) + 5.0):
                return False
            speed = max(220.0, float(shot.get("return_speed", 620.0) or 620.0))
            step = min(distance, speed * dt)
            shot["vx"] = dx / max(1e-6, distance) * speed
            shot["vy"] = dy / max(1e-6, distance) * speed
            shot["x"] = x + dx / max(1e-6, distance) * step
            shot["y"] = y + dy / max(1e-6, distance) * step
        self._record_projectile_trail(shot, 12)
        self._hit_projectile_creatures(shot)
        return True

    def _advance_battle_top(self, shot, dt):
        radius = max(3.0, float(shot.get("radius", 10.0)))
        shot["spin"] = float(shot.get("spin", 0.0)) + dt * (
            17.0 if bool(shot.get("heavy", False)) else 13.0
        )
        shot["vy"] = float(shot.get("vy", 0.0)) + float(shot.get("gravity", 0.0)) * dt
        vx = float(shot.get("vx", 0.0)); vy = float(shot.get("vy", 0.0))
        travel = math.hypot(vx, vy) * dt
        steps = max(1, min(24, int(math.ceil(travel / max(3.0, radius * 0.65)))))
        step_dt = dt / float(steps)
        for _step in range(steps):
            x = float(shot.get("x", 0.0)); y = float(shot.get("y", 0.0))
            vx = float(shot.get("vx", 0.0)); vy = float(shot.get("vy", 0.0))
            nx = x + vx * step_dt; ny = y + vy * step_dt
            if self._projectile_hits_world(nx, ny, radius):
                hit_x = self._projectile_hits_world(nx, y, radius)
                hit_y = self._projectile_hits_world(x, ny, radius)
                if hit_x and not hit_y:
                    vx = -vx * 0.78
                elif hit_y and not hit_x:
                    vy = -abs(vy) * 0.56
                    vx *= 0.92
                else:
                    vx = -vx * 0.72; vy = -abs(vy) * 0.52
                shot["vx"] = vx; shot["vy"] = vy
                shot["bounces"] = int(shot.get("bounces", 0) or 0) + 1
                final = int(shot["bounces"]) >= max(1, int(shot.get("max_bounces", 4) or 4))
                self._emit_projectile_world_impact(
                    shot, x, y, ricochet=True, final=final,
                )
                if final:
                    return False
            else:
                shot["x"] = nx; shot["y"] = ny
            self._record_projectile_trail(shot, 10)
            self._hit_projectile_creatures(shot)
        return True

    def _rocket_touches_creature(self, shot):
        radius = max(2.0, float(shot.get("radius", 6.0)))
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if self._circle_aabb(
                float(shot.get("x", 0.0)), float(shot.get("y", 0.0)), radius, cb,
            ):
                return True
        return False

    def _explode_rocket(self, shot, x, y):
        if bool(shot.get("exploded", False)):
            return []
        shot["exploded"] = True
        x = float(x); y = float(y)
        radius = max(20.0, min(180.0, float(shot.get("explosion_radius", 72.0) or 72.0)))
        base_damage = max(1.0, float(shot.get("explosion_damage", 74.0) or 74.0))
        max_hits = max(1, min(16, int(shot.get("max_hits", 8) or 8)))
        candidates = []
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if not self._circle_aabb(x, y, radius, cb):
                continue
            candidates.append((math.hypot(float(entity.x) - x, float(entity.y) - y), entity))
        candidates.sort(key=lambda pair: pair[0])
        targets = []
        for distance, entity in candidates[:max_hits]:
            falloff = max(0.55, 1.0 - 0.45 * distance / max(1.0, radius))
            damage = base_damage * falloff
            self._provoke_creature(entity)
            entity.hp = max(0.0, float(entity.hp) - damage)
            entity.hurt_timer = 0.28
            dx = float(entity.x) - x
            direction = 1.0 if dx >= 0.0 else -1.0
            entity.vx = direction * max(80.0, float(shot.get("knockback", 188.0)))
            targets.append(entity)
        try:
            self.events.emit(
                "audio_melee_hit", weapon="rpg_launcher", sound_key="hammer_hit",
                target_id="", heavy=bool(shot.get("heavy", False)),
                projectile_kind="rocket", sound_gain=1.25,
            )
            self.events.emit(
                "message",
                text="RPG爆炸%s  傷害 %.0f" % (
                    " 命中%d個目標" % len(targets) if targets else "", base_damage,
                ),
            )
        except Exception:
            pass
        self.emit_external_impact(
            "rpg_launcher", heavy=bool(shot.get("heavy", False)),
            x=x, y=y, direction=(float(shot.get("vx", 1.0)), float(shot.get("vy", 0.0))),
            targets=targets, delivery="rocket_explosion", world_hit=True,
            metadata={"damage": base_damage, "radius": radius},
        )
        return targets

    def _nearest_solid_tile(self, x, y, search_radius=2):
        """Return the closest solid storage cell to an impact point."""
        world = self.world
        if world is None:
            return None
        base_tx = int(math.floor(float(x) / TILE_SIZE))
        base_ty = int(math.floor(float(y) / TILE_SIZE))
        search_radius = max(0, min(4, int(search_radius)))
        candidates = []
        for ty in range(base_ty - search_radius, base_ty + search_radius + 1):
            for tx in range(base_tx - search_radius, base_tx + search_radius + 1):
                if not (0 <= tx < world.width_tiles and 0 <= ty < world.height_tiles):
                    continue
                try:
                    tile_id = int(world.get_tile(tx, ty))
                    if tile_id == 0 or not bool(tile_def(tile_id).solid):
                        continue
                except Exception:
                    continue
                cx = (tx + 0.5) * TILE_SIZE
                cy = (ty + 0.5) * TILE_SIZE
                candidates.append(((cx - float(x)) ** 2 + (cy - float(y)) ** 2, tx, ty))
        if not candidates:
            return None
        _distance, tx, ty = min(candidates, key=lambda row: (row[0], row[2], row[1]))
        return int(tx), int(ty)

    def _explode_tnt(self, shot, x, y):
        """Resolve creature damage and the exact terrain footprint once."""
        if bool(shot.get("exploded", False)):
            return []
        shot["exploded"] = True
        x = float(x); y = float(y)
        heavy = bool(shot.get("heavy", False))
        radius = max(20.0, min(200.0, float(shot.get("explosion_radius", 62.0) or 62.0)))
        base_damage = max(1.0, float(shot.get("explosion_damage", 58.0) or 58.0))
        max_hits = max(1, min(16, int(shot.get("max_hits", 6) or 6)))
        candidates = []
        for entity in tuple(self.scene.entities):
            if not isinstance(entity, Creature) or entity.background_only or not entity.active or entity.hp <= 0.0:
                continue
            cb = entity.combat_bbox() if hasattr(entity, "combat_bbox") else entity.bbox()
            if not self._circle_aabb(x, y, radius, cb):
                continue
            candidates.append((math.hypot(float(entity.x) - x, float(entity.y) - y), entity))
        candidates.sort(key=lambda pair: pair[0])
        targets = []
        for distance, entity in candidates[:max_hits]:
            falloff = max(0.58, 1.0 - 0.42 * distance / max(1.0, radius))
            damage = base_damage * falloff
            self._provoke_creature(entity)
            entity.hp = max(0.0, float(entity.hp) - damage)
            entity.hurt_timer = 0.30 if heavy else 0.24
            dx = float(entity.x) - x
            direction = 1.0 if dx >= 0.0 else -1.0
            entity.vx = direction * max(90.0, float(shot.get("knockback", 168.0)))
            targets.append(entity)

        blast_width = 3 if int(shot.get("blast_width", 1) or 1) >= 3 else 1
        terrain = {
            "width": blast_width,
            "pattern_cells": (),
            "removed_tiles": 0,
            "removed_plants": 0,
            "removed_cells": (),
        }
        tools = self.tool_system
        center = self._nearest_solid_tile(x, y, search_radius=2)
        if tools is not None and center is not None and hasattr(tools, "blast_remove_tiles"):
            try:
                terrain = tools.blast_remove_tiles(center[0], center[1], width=blast_width)
            except Exception:
                terrain = dict(terrain)

        try:
            self.events.emit(
                "audio_melee_hit", weapon="tnt", sound_key="hammer_hit",
                target_id="", heavy=heavy, projectile_kind="tnt",
                sound_gain=1.30 if heavy else 1.16,
            )
            self.events.emit(
                "message",
                text="TNT%s爆破：%d格圖塊%s  傷害 %.0f" % (
                    "重型" if heavy else "",
                    int(terrain.get("removed_tiles", 0) or 0),
                    "，命中%d個目標" % len(targets) if targets else "",
                    base_damage,
                ),
            )
        except Exception:
            pass
        self.emit_external_impact(
            "tnt", heavy=heavy,
            x=x, y=y,
            direction=(float(shot.get("vx", 1.0)), float(shot.get("vy", 0.0))),
            targets=targets, delivery="tnt_explosion", world_hit=True,
            metadata={
                "damage": base_damage,
                "radius": radius,
                "blast_width": blast_width,
                "removed_tiles": int(terrain.get("removed_tiles", 0) or 0),
                "removed_plants": int(terrain.get("removed_plants", 0) or 0),
                "pattern_cells": tuple(terrain.get("pattern_cells", ()) or ()),
            },
        )
        return targets

    def _advance_projectile(self, shot, dt):
        kind = str(shot.get("kind", "greatsword_crescent"))
        if kind == "yoyo":
            return self._advance_yoyo(shot, dt)
        if kind == "battle_top":
            return self._advance_battle_top(shot, dt)
        radius = max(2.0, float(shot.get("radius", 4.0)))
        gravity = float(shot.get("gravity", 0.0) or 0.0)
        shot["vy"] = float(shot.get("vy", 0.0)) + gravity * dt
        vx = float(shot.get("vx", 0.0)); vy = float(shot.get("vy", 0.0))
        travel = math.hypot(vx, vy) * dt
        steps = max(1, min(28, int(math.ceil(travel / max(3.0, radius * 0.80)))))
        step_dt = dt / float(steps)
        for _step in range(steps):
            x = float(shot.get("x", 0.0)); y = float(shot.get("y", 0.0))
            vx = float(shot.get("vx", 0.0)); vy = float(shot.get("vy", 0.0))
            nx = x + vx * step_dt; ny = y + vy * step_dt
            if self._projectile_hits_world(nx, ny, radius):
                if kind == "rocket":
                    self._explode_rocket(shot, nx, ny)
                    return False
                if kind == "tnt":
                    self._explode_tnt(shot, nx, ny)
                    return False
                if kind != "laser":
                    self._emit_projectile_world_impact(shot, nx, ny)
                    return False
                bounce_limit = max(1, int(shot.get("max_bounces", 1) or 1))
                if int(shot.get("bounces", 0) or 0) >= bounce_limit:
                    # All authored reflections already completed and travelled
                    # a visible segment.  The next wall contact terminates the
                    # beam without creating an extra ricochet.
                    self._emit_projectile_world_impact(shot, nx, ny, final=True)
                    return False
                hit_x = self._projectile_hits_world(nx, y, radius)
                hit_y = self._projectile_hits_world(x, ny, radius)
                if hit_x and not hit_y:
                    vx = -vx
                elif hit_y and not hit_x:
                    vy = -vy
                else:
                    vx = -vx; vy = -vy
                shot["vx"] = vx; shot["vy"] = vy
                shot["bounces"] = int(shot.get("bounces", 0) or 0) + 1
                # The final authorized reflection still travels afterward;
                # otherwise the fifth "bounce" would exist only as a one-frame
                # event at the wall.  A subsequent collision removes the beam.
                final_bounce = int(shot["bounces"]) >= bounce_limit
                self._emit_projectile_world_impact(
                    shot, nx, ny, ricochet=True, final=final_bounce,
                )
                magnitude = max(1.0, math.hypot(vx, vy))
                escape = radius + 1.5
                ex = x + vx / magnitude * escape; ey = y + vy / magnitude * escape
                if not self._projectile_hits_world(ex, ey, radius):
                    shot["x"] = ex; shot["y"] = ey
            else:
                shot["x"] = nx; shot["y"] = ny
            self._record_projectile_trail(
                shot, 14 if kind == "laser" else (10 if kind in ("rocket", "tnt") else 7),
            )
            if kind in ("rocket", "tnt") and self._rocket_touches_creature(shot):
                if kind == "tnt":
                    self._explode_tnt(
                        shot, float(shot.get("x", x)), float(shot.get("y", y)),
                    )
                else:
                    self._explode_rocket(
                        shot, float(shot.get("x", x)), float(shot.get("y", y)),
                    )
                return False
            if self._hit_projectile_creatures(shot):
                return False
        return True

    def _update_slash_projectiles(self, dt):
        if not self.slash_projectiles:
            return
        keep = []
        for shot in self.slash_projectiles:
            delay = max(0.0, float(shot.get("delay", 0.0) or 0.0))
            if delay > 0.0:
                shot["delay"] = max(0.0, delay - dt)
                keep.append(shot)
                continue
            shot["life"] = float(shot.get("life", 0.0)) - dt
            if shot["life"] <= 0.0:
                if str(shot.get("kind", "")) == "tnt":
                    self._explode_tnt(
                        shot, float(shot.get("x", 0.0)), float(shot.get("y", 0.0)),
                    )
                continue
            if self._advance_projectile(shot, dt):
                keep.append(shot)
        self.slash_projectiles = keep[-self.MAX_WEAPON_PROJECTILES:]

    def _update_impact_feedback(self, dt):
        self._impact_coalesce_remaining = max(
            0.0, float(self._impact_coalesce_remaining) - dt,
        )
        damping = max(0.0, 1.0 - dt * 14.0)
        self.camera_impulse_x *= damping
        self.camera_impulse_y *= damping
        if abs(self.camera_impulse_x) < 0.01:
            self.camera_impulse_x = 0.0
        if abs(self.camera_impulse_y) < 0.01:
            self.camera_impulse_y = 0.0
        if self.impact_feedback:
            keep = []
            for event in self.impact_feedback:
                row = dict(event)
                row["ttl"] = max(0.0, float(row.get("ttl", 0.0)) - dt)
                if row["ttl"] > 0.0:
                    keep.append(row)
            self.impact_feedback = keep[-max(1, int(WEAPON_IMPACT_FEEDBACK_LIMIT)):]

    def update(self, dt):
        dt = max(0.0, float(dt))
        self._update_impact_feedback(dt)
        self.cooldown = max(0.0, self.cooldown - dt)
        prev_progress = self.attack_progress if self.swing_timer > 0.0 else 0.0
        was_swinging = self.swing_timer > 0.0
        self.swing_timer = max(0.0, self.swing_timer - dt)
        new_progress = self.attack_progress if (was_swinging or self.swing_timer > 0.0) else 1.0
        self._update_pending_attack_hit(prev_progress, new_progress)
        self._update_pending_world_effect(prev_progress, new_progress)
        self._update_pending_projectile(prev_progress, new_progress)
        self._update_pending_vfx(prev_progress, new_progress)
        self._update_active_vfx(dt)
        if was_swinging and self.swing_timer <= 0.0:
            self.current_attack_kind = ""
        self._update_slash_projectiles(dt)
