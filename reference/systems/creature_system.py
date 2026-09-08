# -*- coding: utf-8 -*-
"""Targetable creature AI, hostile contact combat and death/drop bridge."""
import random
import math
from config import (
    GRAVITY, TILE_SIZE, CHUNK_SIZE,
    CREATURE_AGGRO_RADIUS_PX, CREATURE_AGGRO_VERTICAL_PX,
    CREATURE_PRONE_DETECTION_RADIUS_PX,
    CREATURE_PRONE_DETECTION_VERTICAL_PX,
    CREATURE_ATTACK_CONTACT_MARGIN_PX, CREATURE_ATTACK_COOLDOWN_SECONDS,
    PLAYER_HURT_INVULNERABILITY_SECONDS, CREATURE_HIT_KNOCKBACK_SPEED,
    CREATURE_ATTACK_RECOIL_SECONDS, CREATURE_ATTACK_REARM_GAP_PX,
)
from entities.creature import Creature
from entities.enemy_magic import EnemyMagicBolt
from engine.math2d import rects_overlap
from systems.creature_action_profile import load_creature_actions
from systems.creature_vfx_system import CreatureAttackVFXSystem, load_creature_vfx_catalog
from systems.creature_bound_vfx import CreatureBoundVFXSystem
from systems.creature_respawn_system import CreatureRespawnSystem
from world.tile_registry import LAYERED_SOLID_TILES
from world.soil_layers import soil_top_ratio
from player.states import ROLL, PRONE
from systems.fauna99_combat import update_special_actor, agile_kong
from systems.plant_monsters import hold_plant, plant_hit, release_spores, update_sky_flyer, clear_ray


class CreatureSystem:
    def __init__(self, scene, physics, chunk_streamer, player, drop_callback, events=None, environment=None, biome_system=None, asset_registry=None, secondary_world=None):
        self.scene = scene
        self.physics = physics
        self.chunk_streamer = chunk_streamer
        self.player = player
        self.drop_callback = drop_callback
        self.last_death_drop_results = ()
        self.events = events
        if events is not None:
            try: events.subscribe("tile_fully_mined", self._on_tile_fully_mined)
            except Exception: pass
        self.environment = environment
        self.env = getattr(environment, "state", None)
        self.water_system = getattr(environment, "water", None)
        self.biome_system = biome_system
        self.asset_registry = asset_registry
        self.secondary_world = secondary_world
        self.boss_weapon_system = None
        # FIX19: external, data-driven action definitions.  This is loaded once
        # per game launch just like other project-local authoring data.
        self.action_profiles = load_creature_actions()
        # FIX90 a placed secondary-world species retains its original attack
        # profiles even when authored into the main world (or another map).
        from systems.editor_catalog import placed_secondary_runtimes
        import os
        _editor_secondary=placed_secondary_runtimes(
            os.path.dirname(getattr(asset_registry,"assets_root","")),
            getattr(biome_system,"metadata",{}) or {})
        for _sec in _editor_secondary:self.action_profiles=_sec.overlay_action_profiles(self.action_profiles)
        if bool(getattr(secondary_world, "active", False)):
            self.action_profiles = secondary_world.overlay_action_profiles(
                self.action_profiles
            )
        self.rng = random.Random(724)
        self._water_column_cache = {}
        self._water_cache_clock = 0.0
        # FIX29 hostile ranged projectiles are owned by CreatureSystem rather
        # than the player's MagicSystem so they can damage the player and still
        # use the same world collision rules.
        self.enemy_magic_bolts = []
        self.explosion_vfx = []
        self._enemy_magic_seq = 0
        # FIX68: combat owns all hit/action decisions; this downstream system
        # only queues bounded world-space presentation packets for Metal.
        _vfx_catalog = load_creature_vfx_catalog()
        for _sec in _editor_secondary:_vfx_catalog=_sec.merged_vfx_catalog(_vfx_catalog)
        if bool(getattr(secondary_world, "active", False)):
            _vfx_catalog = secondary_world.merged_vfx_catalog(_vfx_catalog)
        self.creature_vfx_system = CreatureAttackVFXSystem(
            event_bus=events, catalog=_vfx_catalog
        )
        self.attack_vfx = self.creature_vfx_system.feedback
        # FIX112: authored body animation and attack VFX are separate tracks.
        self.bound_vfx_system = CreatureBoundVFXSystem(self, asset_registry)
        # Short presentation grace: world/UI must become visible before nearby
        # hostile actors are allowed to chase or damage the player.
        self.startup_grace = 1.0
        # FIX68: deaths feed a bounded, map-local respawn heap. The scheduler
        # reuses the authored Creature objects so renderer/environment tuples
        # keep stable references and Pyto does not accumulate objects.
        respawn_meta = {}
        try:
            respawn_meta = dict(
                (getattr(biome_system, "metadata", {}) or {}).get(
                    "creature_respawn", {}
                ) or {}
            )
        except Exception:
            respawn_meta = {}
        raw_species_policies = respawn_meta.get("species", {})
        species_policies = (
            dict(raw_species_policies)
            if isinstance(raw_species_policies, dict)
            else {}
        )
        # Asset-editor custom profiles may opt into a distinct delay without
        # requiring a new Creature dataclass field or spawn-code branch.
        try:
            from systems.biome_system import CREATURE_ARCHETYPES
            for species, profile in CREATURE_ARCHETYPES.items():
                if not isinstance(profile, dict):
                    continue
                if "respawn_seconds" not in profile and "respawn_enabled" not in profile:
                    continue
                row = dict(species_policies.get(species, {}) or {})
                if profile.get("respawn_seconds") is not None:
                    row["delay_seconds"] = profile.get("respawn_seconds")
                if "respawn_enabled" in profile:
                    row["enabled"] = bool(profile.get("respawn_enabled"))
                species_policies[str(species)] = row
        except Exception:
            pass
        meta_name = str(
            (getattr(biome_system, "metadata", {}) or {}).get("name", "")
            or getattr(scene, "name", "world")
        )
        if bool(getattr(secondary_world, "active", False)):
            meta_name = str(getattr(secondary_world, "world_id", "") or meta_name)
        def _respawn_number(key, fallback):
            try:
                value = float(respawn_meta.get(key, fallback))
                return value if math.isfinite(value) else float(fallback)
            except Exception:
                return float(fallback)
        # FIX77 streaming observability. ``active_population`` is reused by
        # downstream hazards so they do not repeat a global creature scan and
        # chunk test during the same frame. A monotonic simulation clock lets
        # cooldowns catch up once when an actor wakes from a sleeping chunk.
        self._simulation_clock = 0.0
        self.active_population = []
        self.respawn = CreatureRespawnSystem(
            scene,
            physics,
            player,
            map_token=meta_name,
            species_policies=species_policies,
            region_delay_multipliers=(
                dict(respawn_meta.get("regions", {}))
                if isinstance(respawn_meta.get("regions", {}), dict)
                else {}
            ),
            hostile_delay_seconds=_respawn_number("hostile_seconds", 45.0),
            passive_delay_seconds=_respawn_number("passive_seconds", 75.0),
            boss_delay_seconds=_respawn_number("boss_seconds", 600.0),
            boss_mode=str(respawn_meta.get("boss_mode", "never") or "never"),
            min_player_distance_px=_respawn_number("min_player_distance_tiles", 12.0) * TILE_SIZE,
            retry_delay_seconds=_respawn_number("retry_seconds", 8.0),
            max_respawns_per_tick=max(1, int(_respawn_number("max_per_tick", 2))),
        )
        initial_population = self.creatures()
        for creature in initial_population:
            creature._fix77_timer_clock = 0.0
        self.respawn.register_population(initial_population)

    def _on_tile_fully_mined(self, payload):
        """Reveal MapEditor burrow actors only after their exact cell is AIR."""
        try: tx=int(payload.get("tx")); ty=int(payload.get("ty"))
        except Exception: return
        for c in self.creatures():
            if not bool(getattr(c,"editor_hidden_until_mined",False)):
                continue
            if bool(getattr(c,"editor_revealed",False)):
                continue
            try: key=tuple(getattr(c,"editor_hidden_tile",()) or ())
            except Exception: key=()
            if key!=(tx,ty):
                continue
            c.editor_revealed=True
            c.active=True
            c.x=(float(tx)+0.5)*TILE_SIZE
            c.y=(float(ty)+1.0)*TILE_SIZE
            c.home_x=float(c.x); c.home_y=float(c.y)
            c.vx=0.0; c.vy=-min(110.0,max(42.0,float(c.speed)*0.65))
            c.grounded=False
            c.behavior_state="emerge"
            c.animation_state_override="move"
            c.hurt_timer=max(float(getattr(c,"hurt_timer",0.0)),0.12)
            if self.events is not None:
                try:self.events.emit("message",text=str(getattr(c,"name","生物"))+"從岩土中現身！")
                except Exception:pass

    def creatures(self):
        return [e for e in self.scene.entities if isinstance(e, Creature)]

    def _death_drops(self, c):
        if c.species in ("corrosive_slime", "butterfly_backdrop"): return ()
        authored = tuple(getattr(c, "loot_table", ()) or ())
        from systems.boss_weapon_defs import with_boss_reward
        authored = with_boss_reward(c.species, authored)
        if authored:
            return authored
        if c.species == "slime":
            return (("slime_gel", "史萊姆凝膠", 2),)
        if c.species == "boar":
            return (("raw_meat", "生肉", 2), ("hide", "獸皮", 1))
        return (("monster_drop", "魔物素材", 1),)

    def _process_death(self, c):
        if getattr(c,"background_only",False): return
        if c.death_processed or float(c.hp) > 0.0:
            return
        c.death_processed = True
        if c.species == "giant_python" and self.boss_weapon_system is not None:
            self.boss_weapon_system.python.death(c)
        if self.boss_weapon_system is not None and c.species in ('lava_beetle_emperor','crystal_nine_tail','abyss_crab_king','thunder_roc','drill_worm','ancient_tree_demon'):
            self.boss_weapon_system.fix103.death(c)
        c.active = False
        self.respawn.on_death(c)
        drop_results = []
        for item_id, name, count in self._death_drops(c):
            drop_results.append(
                self.drop_callback(item_id, name, count, c.x, c.y - 3.0)
            )
        # Explicit accounting remains inspectable by tests/engineering HUD;
        # callbacks predating FIX70 may still return Item/None and are valid.
        self.last_death_drop_results = tuple(drop_results)

    @staticmethod
    def _axis_gap(a1, a2, b1, b2):
        if a2 < b1:
            return b1 - a2
        if b2 < a1:
            return a1 - b2
        return 0.0

    def _contacting_player(self, c):
        # Damage is allowed only after the currently visible body outlines
        # overlap. Aggro distance is NOT attack distance. This deliberately
        # avoids the old 8 px invisible bite margin.
        cb = c.combat_bbox() if hasattr(c, "combat_bbox") else c.bbox()
        pb = self.player.combat_bbox() if hasattr(self.player, "combat_bbox") else self.player.bbox()
        return rects_overlap(cb, pb)

    def _player_is_rolling(self):
        p = self.player
        return (
            str(getattr(p, "state", "")) == str(ROLL)
            or float(getattr(p, "roll_timer", 0.0)) > 0.0
        )

    def _player_is_prone(self):
        """True only for the actual prone/crawl posture, not crouch or roll."""
        return str(getattr(self.player, "state", "")) == str(PRONE)

    @staticmethod
    def _can_attack_player(c):
        # FIX130: a MapEditor placement may override hostility without changing
        # the species definition globally.  This is instance-local and survives
        # respawn because the authored spawn row recreates the same policy.
        mode=str(getattr(c,"editor_behavior_mode","default") or "default")
        attack_mode=str(getattr(c,"editor_attack_mode","default") or "default")
        if attack_mode=="none" or mode=="passive":
            return False
        if mode=="retaliate":
            return bool(getattr(c,"provoked_by_player",False))
        if mode=="hostile":
            return not bool(getattr(c,"background_only",False))
        try:
            return bool(c.can_attack_player())
        except Exception:
            return bool(
                getattr(c, "hostile", False)
                or getattr(c, "provoked_by_player", False)
            )

    @staticmethod
    def _editor_move_mode(c):
        mode=str(getattr(c,"editor_move_mode","default") or "default")
        return mode if mode in ("default","hold","patrol","chase","patrol_chase") else "default"

    @classmethod
    def _editor_chase_allowed(cls,c):
        return cls._editor_move_mode(c) in ("default","chase","patrol_chase")

    @classmethod
    def _editor_patrol_allowed(cls,c):
        return cls._editor_move_mode(c) in ("default","patrol","patrol_chase")

    @staticmethod
    def _awareness_memory(c):
        """Recover awareness from old saves and active attack states."""
        if not CreatureSystem._can_attack_player(c):
            return False
        if bool(getattr(c, "player_detected", False)):
            return True
        if str(getattr(c, "ai_action_id", "") or ""):
            c.player_detected = True
            return True
        if str(getattr(c, "behavior_state", "") or "") in (
            "chase", "attack", "recover", "recoil",
        ):
            c.player_detected = True
            return True
        return False

    @staticmethod
    def _mark_player_detected(c):
        if not CreatureSystem._can_attack_player(c):
            return False
        c.player_detected = True
        return True

    def _update_player_detection(
        self,
        c,
        horizontal_distance,
        vertical_distance,
        normal_radius,
        normal_vertical,
        allow_new=True,
    ):
        """Return this creature's awareness after applying FIX65 stealth.

        Standing uses the caller's ordinary aggro range.  Prone uses the much
        smaller close-range limits only while the creature has never detected
        the player.  Awareness is a latch, so a sudden prone transition cannot
        cancel chase, attacks, or projectiles already in progress.
        """
        if not self._can_attack_player(c):
            return False
        if self._awareness_memory(c):
            return True
        if not bool(allow_new):
            return False

        radius = max(0.0, float(normal_radius))
        vertical = max(0.0, float(normal_vertical))
        custom_radius=float(getattr(c,"editor_detection_radius_px",0.0) or 0.0)
        custom_vertical=float(getattr(c,"editor_detection_vertical_px",0.0) or 0.0)
        if custom_radius>0.0:radius=custom_radius
        if custom_vertical>0.0:vertical=custom_vertical
        if self._player_is_prone():
            radius = min(radius, float(CREATURE_PRONE_DETECTION_RADIUS_PX))
            vertical = min(vertical, float(CREATURE_PRONE_DETECTION_VERTICAL_PX))

        if (
            float(horizontal_distance) <= radius
            and float(vertical_distance) <= vertical
        ):
            return self._mark_player_detected(c)
        return False

    def _hostile_action_visible(
        self,
        c,
        horizontal_distance,
        vertical_distance,
        allow_new=True,
    ):
        """Gate authored attacks without reducing their normal standing range."""
        # Passive fauna has authored attack clips too, but those clips must be
        # completely unavailable until this individual is provoked by a
        # player-owned hit.
        if not self._can_attack_player(c):
            return False
        if self._awareness_memory(c):
            return True
        if not bool(allow_new):
            return False
        if not self._player_is_prone():
            # A placement-authored detection radius is authoritative.  Default
            # species retain FIX65's long-range action behaviour unchanged.
            if float(getattr(c,"editor_detection_radius_px",0.0) or 0.0)>0.0:
                return self._update_player_detection(
                    c,horizontal_distance,vertical_distance,
                    float(getattr(c,"editor_detection_radius_px",0.0)),
                    float(getattr(c,"editor_detection_vertical_px",0.0) or CREATURE_AGGRO_VERTICAL_PX),
                    allow_new=True,
                )
            # Do not mark awareness yet: an authored action must actually be
            # eligible before a long-range actor is considered to have spotted.
            return True
        return self._update_player_detection(
            c,
            horizontal_distance,
            vertical_distance,
            CREATURE_PRONE_DETECTION_RADIUS_PX,
            CREATURE_PRONE_DETECTION_VERTICAL_PX,
            allow_new=True,
        )

    def _player_can_take_damage(self):
        p = self.player
        # FIX46: roll is a genuine dodge state. Enemy contact, authored melee
        # actions, explosions and enemy magic all consult this common gate.
        if self._player_is_rolling():
            return False
        if float(getattr(p, "startup_damage_grace", 0.0)) > 0.0:
            return False
        if float(getattr(p, "hurt_invulnerability", 0.0)) > 0.0:
            return False
        return float(getattr(p, "hp", 0.0)) > 0.0

    def _resolve_player_damage(self, source_x, source_y, damage, kind):
        """Route monster damage through hidden shield durability, then HP."""
        try:incoming=max(0.0,float(damage))
        except Exception:incoming=0.0
        taker=getattr(self.player,"take_damage",None)
        if callable(taker):
            try:result=taker(incoming,source_x=source_x,source_y=source_y,kind=kind)
            except Exception:result=None
        else:
            result=None
        if not isinstance(result,dict):
            self.player.hp=max(0.0,float(self.player.hp)-incoming)
            result={"incoming_damage":incoming,"absorbed_damage":0.0,"remaining_damage":incoming,"blocked":False}
        absorbed=max(0.0,float(result.get("absorbed_damage",0.0) or 0.0))
        remaining=max(0.0,float(result.get("remaining_damage",incoming) or 0.0))
        blocked=absorbed>0.0 and remaining<=1e-9
        result["blocked"]=bool(blocked)
        if absorbed>0.0 and self.events is not None:
            self.events.emit(
                "audio_shield_block", kind=str(kind or "creature"),
                damage=float(absorbed),
                side=int(getattr(self.player,"shield_block_side",0) or 0),
            )
            if blocked:
                self.events.emit("message",text="護盾盔甲吸收了攻擊")
        return result

    def _emit_attack_vfx(
        self, c=None, action=None, phase="impact", outcome="released",
        authoritative="attack", x=None, y=None, target_x=None, target_y=None,
        radius=None, attack_kind="attack", effect=None,
    ):
        """Publish presentation only after CreatureSystem confirms the event."""
        system = getattr(self, "creature_vfx_system", None)
        if system is None:
            return None
        action = action if isinstance(action, dict) else {}
        # FIX123: authored attack FX is the presentation authority.  Previously
        # CreatureSystem also emitted the older procedural CreatureAttackVFX on
        # impact, so gameplay showed extra shapes that could never appear in the
        # AssetEditor.  When the current action has an authored effect binding,
        # suppress that generic overlay; editor and game now consume the same
        # exact effect pixel source. Contact/fallback attacks without authored
        # bindings retain the legacy feedback path.
        if c is not None and self.asset_registry is not None:
            try:
                _state=str(getattr(c,"animation_state_override","") or "")
                _aid=str(getattr(c,"asset_id","") or ("creature."+str(getattr(c,"species",""))))
                _bind=self.asset_registry.combat_binding(_aid,_state) if _state else None
                if isinstance(_bind,dict) and str(_bind.get("render_mode","authored") or "authored")=="authored" and str(_bind.get("effect_asset","") or ""):
                    return None
            except Exception:
                pass
        action_id = str(getattr(c, "ai_action_id", "") or "")
        resolved_effect = str(action.get("effect", "") if effect is None else effect)
        if target_x is None:
            target_x = float(getattr(self.player, "x", 0.0))
        if target_y is None:
            target_y = float(getattr(self.player, "y", 0.0)) - float(self.player.height()) * 0.5
        try:
            return system.emit_attack(
                creature=c, action_id=action_id, effect=resolved_effect,
                attack_kind=str(attack_kind or "attack"), phase=phase,
                outcome=outcome, authoritative=authoritative,
                x=x, y=y, target_x=target_x, target_y=target_y,
                radius=radius,
                element=str(action.get("element", getattr(c, "element_type", "neutral")) or "neutral"),
            )
        except Exception:
            return None

    def _emit_bolt_vfx(self, bolt, phase, outcome, authoritative="collision", x=None, y=None):
        system = getattr(self, "creature_vfx_system", None)
        if system is None or bolt is None:
            return None
        element = str(getattr(bolt, "element", "arcane") or "arcane")
        if element == "lightning":
            effect = "lightning_bolt"
        elif element == "fire_column":
            effect = "fire_column"
        else:
            effect = "magic_bolt"
        px = float(getattr(bolt, "x", 0.0) if x is None else x)
        py = float(getattr(bolt, "y", 0.0) if y is None else y)
        vx = float(getattr(bolt, "vx", 0.0)); vy = float(getattr(bolt, "vy", 0.0))
        return system.emit_attack(
            creature=None,
            action_id=str(getattr(bolt, "source_action_id", "") or ""),
            effect=effect, attack_kind="projectile", phase=phase,
            outcome=outcome, authoritative=authoritative,
            x=px, y=py, target_x=px + vx * 0.08, target_y=py + vy * 0.08,
            radius=max(
                float(getattr(bolt, "radius", 7.0)) * 2.2,
                float(getattr(bolt, "width", 0.0)) * 0.5,
                float(getattr(bolt, "height", 0.0)) * 0.5,
            ),
            element=element,
            source_id=str(getattr(bolt, "owner_id", "") or "enemy_magic"),
            asset_id=str(getattr(bolt, "source_asset_id", "") or "creature.unknown"),
            species=str(getattr(bolt, "source_species", "") or "unknown"),
            facing=1 if vx >= 0.0 else -1,
        )

    def creature_vfx_snapshot(self, max_events=None, max_quads=None):
        """Stable public API for the Metal renderer; returns detached packets."""
        return self.creature_vfx_system.snapshot(max_events=max_events, max_quads=max_quads)

    def authored_creature_vfx_snapshot(self):
        return self.bound_vfx_system.snapshot()

    def _damage_player(self, c, direction):
        if not self._can_attack_player(c):
            return False
        self._mark_player_detected(c)
        p = self.player
        if not self._player_can_take_damage():
            return False

        resolved = self._resolve_player_damage(
            float(c.x),float(c.y)-float(c.height())*.5,
            float(c.attack_damage),"contact",
        )
        hp_damage=max(0.0,float(resolved.get("remaining_damage",c.attack_damage)))
        if bool(resolved.get("blocked",False)):
            # A blocked body hit still completes the creature's attack cycle;
            # otherwise continuous overlap would attempt a new block every frame.
            c.attack_cooldown = float(CREATURE_ATTACK_COOLDOWN_SECONDS) * max(.25,min(4.0,float(getattr(c,"editor_attack_cooldown_scale",1.0) or 1.0)))
            c.attack_anim_timer = 0.18
            c.attack_recoil_timer = float(CREATURE_ATTACK_RECOIL_SECONDS)
            c.attack_rearm_required = True
            self._emit_attack_vfx(
                c, phase="impact", outcome="blocked", authoritative="collision",
                attack_kind="contact", effect="damage_knockback",
            )
            return True

        p.hurt_timer = 0.20
        p.hurt_invulnerability = float(PLAYER_HURT_INVULNERABILITY_SECONDS)
        p.vx = float(direction) * float(CREATURE_HIT_KNOCKBACK_SPEED)
        c.attack_cooldown = float(CREATURE_ATTACK_COOLDOWN_SECONDS) * max(.25,min(4.0,float(getattr(c,"editor_attack_cooldown_scale",1.0) or 1.0)))
        c.attack_anim_timer = 0.18
        c.attack_recoil_timer = float(CREATURE_ATTACK_RECOIL_SECONDS)
        c.attack_rearm_required = True
        if self.events is not None:
            self.events.emit(
                "message",
                text=f"{c.name} 咬傷/擊中：-{hp_damage:.0f} HP（直接傷害）"
            )
        self._emit_attack_vfx(
            c, phase="impact", outcome="hit", authoritative="collision",
            attack_kind="contact", effect="damage_knockback",
        )
        return True

    # ------------------------------------------------------------------
    # FIX19 data-driven creature action bridge
    # ------------------------------------------------------------------
    def _action_entry(self, c):
        creatures=self.action_profiles.get("creatures",{}) if isinstance(self.action_profiles,dict) else {}
        aid=str(getattr(c,"asset_id","") or ("creature."+str(getattr(c,"species",""))))
        entry=creatures.get(aid)
        return entry if isinstance(entry,dict) else None

    def _action_def(self, c, action_id):
        entry=self._action_entry(c)
        actions=entry.get("actions",{}) if isinstance(entry,dict) else {}
        raw=actions.get(str(action_id)) if isinstance(actions,dict) else None
        return raw if isinstance(raw,dict) else None

    def _legacy_contact_overridden(self, c):
        mode=str(getattr(c,"editor_attack_mode","default") or "default")
        if mode=="contact":return False
        if mode in ("none","normal","special","mixed"):return True
        entry=self._action_entry(c)
        return bool(entry and entry.get("override_legacy_contact",False))

    def _tick_action_cooldowns(self, c, dt):
        cds=getattr(c,"ai_action_cooldowns",None)
        if not isinstance(cds,dict):
            c.ai_action_cooldowns={}; return
        # Most creatures have no authored action on cooldown. Avoid allocating
        # list(cds.items()) for every sleeping/idle creature every frame.
        if not cds:
            return
        for key,value in list(cds.items()):
            try:value=max(0.0,float(value)-float(dt))
            except Exception:value=0.0
            if value<=0.0:cds.pop(key,None)
            else:cds[key]=value

    def _authored_action_timing(self, c, action):
        """Return (duration, gameplay-event-time) for an authored action."""
        state=str(action.get("animation_state",getattr(c,"ai_action_id","")) or getattr(c,"ai_action_id",""))
        try:duration=max(0.0,float(action.get("duration",0.0)))
        except Exception:duration=0.0
        info=None
        if self.asset_registry is not None:
            try:info=self.asset_registry.animation_info(str(getattr(c,"asset_id","")),state)
            except Exception:info=None
        if duration<=0.0:
            if info:
                try:duration=max(0.05,float(info.get("frame_count",1))/max(1.0,float(info.get("fps",6.0))))
                except Exception:duration=0.55
            else:duration=0.55
        effect_time=None
        if info:
            try:
                frame=info.get("action_frame")
                if frame is not None:effect_time=float(frame)/max(1.0,float(info.get("fps",6.0)))
            except Exception:effect_time=None
        if effect_time is None:effect_time=duration*0.50
        return max(0.05,duration),max(0.0,min(duration,float(effect_time)))

    def _authored_vfx_time(self,c,action,state,fallback):
        if self.asset_registry is None:return float(fallback)
        aid=str(getattr(c,'asset_id','') or ('creature.'+str(getattr(c,'species',''))))
        try:
            info=self.asset_registry.animation_info(aid,state)
            if info:
                events=info.get('events',{}) if isinstance(info.get('events',{}),dict) else {}
                frame=events.get('effect',events.get('action'))
                if frame is not None:return max(0.,float(frame)/max(1.,float(info.get('fps',6.))))
        except Exception:pass
        return float(fallback)

    def _has_authored_bound_vfx(self,c,state):
        if self.asset_registry is None:return False
        aid=str(getattr(c,'asset_id','') or ('creature.'+str(getattr(c,'species',''))))
        try:return bool(self.asset_registry.combat_binding(aid,state))
        except Exception:return False

    def _eligible_action(self, c, action_id, action, horizontal_distance, vertical_distance):
        if c.locomotion in ("rooted","ceiling"):
            sx,sy=float(c.x),float(c.y)-float(c.height())*(.3 if c.locomotion=="ceiling" else .62)
            if not clear_ray(self.physics.world,sx,sy,self.player.x,self.player.y-self.player.height()*.5):
                return False
        if not bool(action.get("enabled",True)):return False
        if float(getattr(c,"ai_action_cooldowns",{}).get(str(action_id),0.0))>0.0:return False
        if bool(action.get("requires_ground",False)) and not bool(getattr(c,"grounded",False)):return False
        try:rmin=float(action.get("range_min",0.0)); rmax=float(action.get("range_max",72.0)); vr=float(action.get("vertical_range",72.0))
        except Exception:return False
        if horizontal_distance<rmin or horizontal_distance>rmax or vertical_distance>vr:return False
        try:chance=max(0.0,min(1.0,float(action.get("chance",1.0))))
        except Exception:chance=1.0
        return chance>=1.0 or self.rng.random()<=chance

    def _choose_ai_action(self, c, horizontal_distance, vertical_distance):
        entry=self._action_entry(c)
        actions=entry.get("actions",{}) if isinstance(entry,dict) else {}
        if not isinstance(actions,dict) or not actions:return None
        eligible=[]
        attack_mode=str(getattr(c,"editor_attack_mode","default") or "default")
        for action_id,action in actions.items():
            if not isinstance(action,dict):continue
            state=str(action.get("animation_state",action_id) or action_id)
            is_special=(str(action_id)=="special" or state=="special")
            is_normal=(str(action_id)=="attack" or state=="attack")
            if attack_mode in ("none","contact"):continue
            if attack_mode=="normal" and not is_normal:continue
            if attack_mode=="special" and not is_special:continue
            # mixed explicitly means the common attack/special pair. Custom
            # nonstandard actions remain available only under species default.
            if attack_mode=="mixed" and not (is_normal or is_special):continue
            if self._eligible_action(c,action_id,action,horizontal_distance,vertical_distance):
                try:priority=int(action.get("priority",10))
                except Exception:priority=10
                eligible.append((priority,str(action_id),action))
        if not eligible:return None
        eligible.sort(key=lambda row:(-row[0],row[1]))
        # Deterministic order at equal priority keeps behavior repeatable for
        # debugging and later behavior-tree/LLM controllers.
        return eligible[0][1],eligible[0][2]

    def _start_ai_action(self, c, action_id, action):
        p=self.player
        c.ai_action_id=str(action_id)
        c.ai_action_elapsed=0.0
        c.ai_action_hit_done=False
        c.ai_action_target_x=float(p.x)
        c.ai_action_target_y=float(p.y-p.height()*0.5)
        duration,effect_time=self._authored_action_timing(c,action)
        c.ai_action_duration=duration; c.ai_action_effect_time=effect_time
        state=str(action.get("animation_state",action_id) or action_id)
        c.ai_action_vfx_time=self._authored_vfx_time(c,action,state,effect_time)
        c.ai_action_vfx_done=False
        c.animation_state_override=state
        c.behavior_state=state
        c.visual_animation_time=0.0
        direction=1 if p.x>=c.x else -1; c.facing=direction
        motion=str(action.get("motion","stationary"))
        try:speed=max(0.0,float(action.get("speed",220.0)))
        except Exception:speed=220.0
        if motion=="charge":
            c.vx=direction*speed
        elif motion=="leap":
            c.vx=direction*speed
            try:c.vy=-max(0.0,float(action.get("lift_speed",280.0)))
            except Exception:c.vy=-280.0
            c.grounded=False
        elif motion=="dive":
            dx=c.ai_action_target_x-c.x; dy=c.ai_action_target_y-(c.y-c.height()*0.5)
            mag=max(1e-6,(dx*dx+dy*dy)**0.5)
            c.vx=dx/mag*speed; c.vy=dy/mag*speed
        else:
            c.vx=0.0
        # Starting an authored action is itself an authoritative AI decision.
        # Emit wind-up/travel presentation now; the later event frame publishes
        # its release or collision outcome separately.
        if str(action.get("effect", "damage_knockback")) != "none" and not self._has_authored_bound_vfx(c,state):
            self._emit_attack_vfx(
                c, action=action, phase="start", outcome="started",
                authoritative="attack", attack_kind=str(motion or "attack"),
                target_x=c.ai_action_target_x, target_y=c.ai_action_target_y,
                radius=action.get("hit_radius"),
            )
        return True

    def _spawn_enemy_magic_bolt(self, c, action):
        p=self.player
        self._enemy_magic_seq += 1
        sx=float(c.x)+(8.0 if int(getattr(c,"facing",1))>=0 else -8.0)
        sy=float(c.y)-float(c.height())*0.62
        tx=float(p.x); ty=float(p.y)-float(p.height())*0.52
        dx=tx-sx;dy=ty-sy;mag=max(1e-6,(dx*dx+dy*dy)**0.5)
        try:speed=max(90.0,float(action.get("speed",280.0)))
        except Exception:speed=280.0
        try:damage=max(0.0,float(action.get("damage",10.0)))
        except Exception:damage=10.0
        try:knock=max(0.0,float(action.get("knockback",140.0)))
        except Exception:knock=140.0
        bolt=EnemyMagicBolt(
            entity_id="enemy_magic_%04d"%self._enemy_magic_seq,
            x=sx,y=sy,vx=dx/mag*speed,vy=dy/mag*speed,
            radius=float(action.get("radius",7.0) or 7.0),life=float(action.get("life",2.4) or 2.4),damage=damage,knockback=knock,
            owner_id=str(getattr(c,"entity_id","")),active=True,
            element=str(action.get("element","arcane") or "arcane"),
        )
        bolt.source_asset_id=str(getattr(c,"asset_id","") or ("creature."+str(getattr(c,"species","unknown"))))
        bolt.source_species=str(getattr(c,"species","") or "unknown")
        bolt.source_action_id=str(getattr(c,"ai_action_id","") or "")
        # FIX82 sustained volleys have a hard active-projectile budget.
        if len(self.enemy_magic_bolts) >= 64:
            oldest=self.enemy_magic_bolts.pop(0);oldest.active=False
        self.enemy_magic_bolts.append(bolt)
        try:self.scene.add_entity(bolt)
        except Exception:pass
        return bolt

    def _spawn_enemy_fire_column(self, c, action):
        """Create a short-lived vertical fire pillar in front of the caster."""
        self._enemy_magic_seq += 1
        direction=1 if int(getattr(c,"facing",1))>=0 else -1
        try:distance=max(18.0,float(action.get("column_distance",34.0)))
        except Exception:distance=34.0
        try:height=max(28.0,float(action.get("column_height",64.0)))
        except Exception:height=64.0
        try:width=max(10.0,float(action.get("column_width",22.0)))
        except Exception:width=22.0
        try:damage=max(0.0,float(action.get("damage",15.0)))
        except Exception:damage=15.0
        try:knock=max(0.0,float(action.get("knockback",120.0)))
        except Exception:knock=120.0
        sx=float(c.x)+direction*distance
        sy=float(c.y)-height*0.5
        hazard=EnemyMagicBolt(
            entity_id="enemy_fire_column_%04d"%self._enemy_magic_seq,
            x=sx,y=sy,vx=0.0,vy=0.0,radius=width*0.5,life=0.72,
            damage=damage,knockback=knock,owner_id=str(getattr(c,"entity_id","")),active=True,
            element="fire_column",width=width,height=height,stationary=True,
        )
        hazard.source_asset_id=str(getattr(c,"asset_id","") or ("creature."+str(getattr(c,"species","unknown"))))
        hazard.source_species=str(getattr(c,"species","") or "unknown")
        hazard.source_action_id=str(getattr(c,"ai_action_id","") or "")
        self.enemy_magic_bolts.append(hazard)
        try:self.scene.add_entity(hazard)
        except Exception:pass
        return hazard

    def _update_enemy_magic_bolts(self, dt):
        if not self.enemy_magic_bolts:return
        p=self.player
        pb=p.bbox()
        keep=[]
        for bolt in self.enemy_magic_bolts:
            if not bool(getattr(bolt,"active",False)):continue
            bolt.life=float(getattr(bolt,"life",0.0))-float(dt)
            if bolt.life<=0.0:
                bolt.active=False;continue
            old_x,old_y=float(bolt.x),float(bolt.y)
            if not bool(getattr(bolt,"stationary",False)):
                bolt.x=old_x+float(bolt.vx)*float(dt)
                bolt.y=old_y+float(bolt.vy)*float(dt)
            r=float(getattr(bolt,"radius",7.0))
            bw=float(getattr(bolt,"width",0.0) or r*2.0); bh=float(getattr(bolt,"height",0.0) or r*2.0)
            box=(bolt.x-bw*0.5,bolt.y-bh*0.5,bolt.x+bw*0.5,bolt.y+bh*0.5)
            wall_hit=False
            # Stationary fire columns are allowed to rise from a floor tile and
            # therefore do not self-delete merely because the bottom overlaps it.
            if not bool(getattr(bolt,"stationary",False)):
                try:
                    for _tx,_ty,_tile,rect in self.physics.world.solid_cells_in_rect(box,padding=1):
                        if rects_overlap(box,rect):wall_hit=True;break
                except Exception:
                    wall_hit=False
            if wall_hit:
                self._emit_bolt_vfx(bolt,"impact","wall",authoritative="collision")
                bolt.active=False;continue
            if rects_overlap(box,pb):
                # During a roll the projectile is not consumed: it continues
                # through the player so the dodge also behaves as a true phase
                # through moving magic instead of merely granting 0 damage.
                if self._player_is_rolling():
                    keep.append(bolt);continue
                if self._player_can_take_damage():
                    resolved=self._resolve_player_damage(
                        old_x,old_y,float(bolt.damage),"monster_projectile"
                    )
                    blocked=bool(resolved.get("blocked",False))
                    hp_damage=max(0.0,float(resolved.get("remaining_damage",bolt.damage)))
                    if not blocked:
                        p.hurt_timer=0.20
                        p.hurt_invulnerability=float(PLAYER_HURT_INVULNERABILITY_SECONDS)
                        direction=1.0 if float(bolt.vx)>=0.0 else -1.0
                        p.vx=direction*float(bolt.knockback)
                        p.vy=min(float(getattr(p,"vy",0.0)),-float(bolt.knockback)*0.16)
                        if self.events is not None:
                            label={"lightning":"閃電","fire_column":"火焰柱","fire":"火焰彈","crystal":"水晶彈","arcane":"魔法彈"}.get(str(getattr(bolt,"element","arcane")),"魔法")
                            self.events.emit("message",text=f"{label}命中：-{hp_damage:.0f} HP")
                    self._emit_bolt_vfx(
                        bolt,"impact","blocked" if blocked else "hit",
                        authoritative="collision",
                    )
                else:
                    self._emit_bolt_vfx(bolt,"impact","invulnerable",authoritative="collision")
                bolt.active=False;continue
            keep.append(bolt)
        self.enemy_magic_bolts=keep
        # Keep Scene bounded; inactive hostile bolts are otherwise invisible but
        # would remain in the generic entity list after long dungeon sessions.
        try:
            self.scene.entities=[e for e in self.scene.entities if not isinstance(e,EnemyMagicBolt) or bool(getattr(e,"active",False))]
        except Exception:
            pass

    def _custom_action_hit_test(self, c, action):
        if self._player_is_rolling():
            return False
        shaped=plant_hit(self,c,action)
        if shaped is not None:return shaped
        p=self.player
        if c.species == "wind_god_pterosaur" and not clear_ray(self.physics.world,c.x,c.y-c.height()*.5,p.x,p.y-p.height()*.5):
            return False
        try:radius=max(1.0,float(action.get("hit_radius",34.0)))
        except Exception:radius=34.0
        if self._contacting_player(c):return True
        dx=float(p.x)-float(c.x)
        dy=float(p.y-p.height()*0.5)-float(c.y-c.height()*0.5)
        # Radius is intentionally measured from visible-body centers. Terrain
        # collision remains unchanged; this only controls authored action reach.
        return dx*dx+dy*dy <= radius*radius

    def _apply_custom_action_effect(self, c, action):
        if bool(getattr(c,"ai_action_hit_done",False)):return False
        c.ai_action_hit_done=True
        # FIX89: selected boss action event frames invoke the exact reward-
        # weapon presentation/gameplay system, so their signature attack and
        # the weapon earned from them read as the same ability family.
        boss_system=getattr(self,'boss_weapon_system',None)
        if boss_system is not None:
            try:
                if boss_system.fire_boss_echo(c,action):
                    return True
            except Exception:
                pass
        effect=str(action.get("effect","damage_knockback"))
        if effect.startswith("fix103_"):
            return bool(boss_system and boss_system.fix103.creature_action(c,action))
        if effect in ("python_tail", "python_tornado"):
            return bool(boss_system and boss_system.python.creature_action(c,action))
        if effect in ("kong_combo","triple_breath"):
            return bool(boss_system and boss_system.expansion.creature_action(c,action))
        if effect=="none":return False
        if effect=="poison_puff":return release_spores(self,c,action)
        if effect in ("seed_volley", "wind_volley"):
            for angle in (-.12,0.,.12):
                bolt=self._spawn_enemy_magic_bolt(c,dict(action,element="wind" if effect=="wind_volley" else "seed",radius=8.0 if effect=="wind_volley" else 4.5,life=2.5 if effect=="wind_volley" else 2.2))
                vx,vy=bolt.vx,bolt.vy
                bolt.vx=vx*math.cos(angle)-vy*math.sin(angle)
                bolt.vy=vx*math.sin(angle)+vy*math.cos(angle)
                self._emit_bolt_vfx(bolt,"release","spawned",authoritative="attack")
            return True
        if effect in ("vine_whip","ceiling_bite"):
            effect="damage_knockback"  # Same shield/invulnerability/damage path.
        if effect in ("magic_bolt","lightning_bolt"):
            payload=dict(action)
            if effect=="lightning_bolt":payload["element"]="lightning"
            bolt=self._spawn_enemy_magic_bolt(c,payload)
            self._emit_bolt_vfx(bolt,"release","spawned",authoritative="attack")
            if self.events is not None:
                self.events.emit("message",text=f"{c.name} 施放{'閃電' if effect=='lightning_bolt' else '魔法'}")
            return True
        if effect=="fire_column":
            hazard=self._spawn_enemy_fire_column(c,action)
            self._emit_bolt_vfx(hazard,"release","spawned",authoritative="attack")
            if self.events is not None:self.events.emit("message",text=f"{c.name} 噴出火焰柱")
            return True
        if effect=="explosion":
            p=self.player
            try:radius=max(18.0,float(action.get("hit_radius",58.0)))
            except Exception:radius=58.0
            dx=float(p.x)-float(c.x);dy=float(p.y-p.height()*0.5)-float(c.y-c.height()*0.5)
            hit=(dx*dx+dy*dy)<=radius*radius
            outcome="miss"
            if hit and self._player_can_take_damage():
                try:damage=max(0.0,float(action.get("damage",22.0)))
                except Exception:damage=22.0
                try:knock=max(0.0,float(action.get("knockback",240.0)))
                except Exception:knock=240.0
                resolved=self._resolve_player_damage(c.x,c.y,damage,"monster_explosion")
                blocked=bool(resolved.get("blocked",False))
                if not blocked:
                    p.hurt_timer=.20;p.hurt_invulnerability=float(PLAYER_HURT_INVULNERABILITY_SECONDS)
                    p.vx=(1.0 if dx>=0 else -1.0)*knock;p.vy=min(float(getattr(p,"vy",0.0)),-knock*.28)
                outcome="blocked" if blocked else "hit"
            elif hit:
                outcome="evaded" if self._player_is_rolling() else "invulnerable"
            self.explosion_vfx.append({
                "x":float(c.x), "y":float(c.y-c.height()*.5),
                "life":.38, "duration":.38,
                "style":"wisp" if str(getattr(c,"species",""))=="exploding_wisp" else "fire",
            })
            self.explosion_vfx=self.explosion_vfx[-8:]
            c.hp=0.0
            if self.events is not None:self.events.emit("message",text=f"{c.name} 爆炸")
            self._emit_attack_vfx(
                c, action=action, phase="impact", outcome=outcome,
                authoritative="collision" if hit else "attack",
                radius=radius, attack_kind="explosion",
            )
            return True
        if self._player_is_rolling():
            self._emit_attack_vfx(
                c,action=action,phase="impact",outcome="evaded",
                authoritative="attack",radius=action.get("hit_radius"),
                attack_kind=str(action.get("motion","melee")),
            )
            return False
        if not self._custom_action_hit_test(c,action):
            self._emit_attack_vfx(
                c,action=action,phase="impact",outcome="miss",
                authoritative="attack",radius=action.get("hit_radius"),
                attack_kind=str(action.get("motion","melee")),
            )
            return False
        p=self.player
        if not self._player_can_take_damage():
            self._emit_attack_vfx(
                c,action=action,phase="impact",outcome="invulnerable",
                authoritative="collision",radius=action.get("hit_radius"),
                attack_kind=str(action.get("motion","melee")),
            )
            return False
        direction=1 if p.x>=c.x else -1
        try:damage=max(0.0,float(action.get("damage",c.attack_damage)))
        except Exception:damage=max(0.0,float(c.attack_damage))
        try:knock=max(0.0,float(action.get("knockback",180.0)))
        except Exception:knock=180.0
        damage_applies = effect in ("damage", "damage_knockback")
        incoming_damage = damage if damage_applies else 0.0
        resolved=self._resolve_player_damage(c.x,c.y-c.height()*.5,incoming_damage,"monster_action")
        hp_damage=max(0.0,float(resolved.get("remaining_damage",incoming_damage)))
        if bool(resolved.get("blocked",False)):
            c.attack_anim_timer=max(float(getattr(c,"attack_anim_timer",0.0)),0.16)
            self._emit_attack_vfx(
                c,action=action,phase="impact",outcome="blocked",
                authoritative="collision",radius=action.get("hit_radius"),
                attack_kind=str(action.get("motion","melee")),
            )
            return True
        if effect in ("damage","damage_knockback") and hp_damage>0.0:
            p.hurt_timer=0.20; p.hurt_invulnerability=float(PLAYER_HURT_INVULNERABILITY_SECONDS)
        if effect in ("knockback","damage_knockback") and knock>0.0:
            p.vx=float(direction)*knock
            # A small upward component makes leap/dive impacts visually legible
            # without changing ordinary contact attacks.
            if str(action.get("motion","")) in ("leap","dive"):
                p.vy=min(float(getattr(p,"vy",0.0)),-knock*0.22)
        c.attack_anim_timer=max(float(getattr(c,"attack_anim_timer",0.0)),0.16)
        if self.events is not None:
            self.events.emit("message",text=f"{c.name} 使用 {c.ai_action_id}：-{hp_damage:.0f} HP" if hp_damage>0 else f"{c.name} 使用 {c.ai_action_id}")
        self._emit_attack_vfx(
            c,action=action,phase="impact",outcome="hit",
            authoritative="collision",radius=action.get("hit_radius"),
            attack_kind=str(action.get("effect","melee")),
        )
        return True

    def _finish_ai_action(self, c, action):
        aid=str(getattr(c,"ai_action_id",""))
        try:cooldown=max(0.05,float(action.get("cooldown",1.25)))
        except Exception:cooldown=1.25
        try:cooldown*=max(.25,min(4.0,float(getattr(c,"editor_attack_cooldown_scale",1.0) or 1.0)))
        except Exception:pass
        if not isinstance(getattr(c,"ai_action_cooldowns",None),dict):c.ai_action_cooldowns={}
        if aid:c.ai_action_cooldowns[aid]=cooldown
        c.ai_action_id=""; c.ai_action_elapsed=0.0; c.ai_action_duration=0.0; c.ai_action_effect_time=0.0; c.ai_action_hit_done=False; c.ai_action_vfx_time=0.0; c.ai_action_vfx_done=False; c.animation_state_override=""
        c.behavior_state="idle"

    def _update_active_ai_action(self, c, action, dt):
        if not isinstance(action,dict):
            c.ai_action_id=""; c.animation_state_override=""; return False
        previous=float(getattr(c,"ai_action_elapsed",0.0)); now=previous+float(dt); c.ai_action_elapsed=now
        old_x, old_y = float(c.x), float(c.y)
        state=str(action.get("animation_state",getattr(c,"ai_action_id","")) or getattr(c,"ai_action_id",""))
        c.animation_state_override=state; c.behavior_state=state
        motion=str(action.get("motion","stationary")); locomotion=str(getattr(c,"locomotion","ground"))
        if motion=="stationary":
            c.vx=0.0
        elif motion=="charge":
            direction=1 if c.ai_action_target_x>=c.x else -1; c.facing=direction
            try:c.vx=direction*max(0.0,float(action.get("speed",220.0)))
            except Exception:pass
        elif motion=="dive":
            # Flying/swimming actors can travel in 2-D. Ground actors still use
            # tile physics below so custom data cannot tunnel through walls.
            if locomotion in ("fish","swim","bird","waterbird","insect_low","fly_low","cave_fly","ghost_fly","sky_fly"):
                if locomotion in ("cave_fly", "sky_fly"):
                    step_x,step_y=c.vx*dt,c.vy*dt
                    if c.species == "wind_god_pterosaur":
                        dx=c.ai_action_target_x-c.x
                        dy=c.ai_action_target_y-(c.y-c.height()*.5)
                        distance=math.hypot(dx,dy)
                        travel=math.hypot(step_x,step_y)
                        if travel>distance and travel>0:
                            step_x,step_y=dx,dy
                            c.vx=c.vy=0.0
                    self.physics.move_horizontal(c,step_x)
                    self.physics.move_vertical(c,step_y)
                else:
                    c.x+=c.vx*dt; c.y+=c.vy*dt
        # Ground/terrain-aware movement for stationary/charge/leap and ground dive.
        if locomotion not in ("fish","swim","bird","waterbird","insect_low","fly_low","cave_fly","ghost_fly","sky_fly","rooted","ceiling"):
            if motion in ("charge","leap","dive"):
                self.physics.move_horizontal(c,c.vx*dt)
            c.vy=min(700.0,c.vy+GRAVITY*dt)
            hit,grounded=self.physics.move_vertical(c,c.vy*dt)
            if hit:c.vy=0.0
            c.grounded=bool(grounded)
        if locomotion in ("rooted","ceiling"):
            hold_plant(self,c,dt)
        # Habitat constraints still outrank authored action motion. A future fish
        # dive/charge cannot escape the water, and low flyers cannot action-dive
        # into a liquid cell.
        if locomotion == "fish":
            cy=float(c.y)-float(c.height())*0.5
            if not self._point_in_water(c.x,cy):
                c.x,c.y=old_x,old_y; c.vx=c.vy=0.0
        elif locomotion in ("bird","insect_low","fly_low"):
            if self._point_in_water(c.x,c.y):
                c.x,c.y=old_x,old_y; c.vy=-max(30.0,abs(float(c.vy))*0.5)
        vfx_time=float(getattr(c,"ai_action_vfx_time",getattr(c,"ai_action_effect_time",0.0)))
        if (not bool(getattr(c,"ai_action_vfx_done",False))) and previous<=vfx_time<=now+1e-9:
            try:self.bound_vfx_system.emit(c,action,state)
            except Exception:pass
            c.ai_action_vfx_done=True
        effect_time=float(getattr(c,"ai_action_effect_time",0.0))
        if (not bool(getattr(c,"ai_action_hit_done",False))) and previous<=effect_time<=now+1e-9:
            self._apply_custom_action_effect(c,action)
        if now>=max(0.05,float(getattr(c,"ai_action_duration",0.05))):
            self._finish_ai_action(c,action)
            return False
        return True

    @staticmethod
    def _approach(value, target, delta):
        value = float(value); target = float(target); delta = max(0.0, float(delta))
        if value < target:
            return min(target, value + delta)
        return max(target, value - delta)

    def _home_biome_ok(self, c, x=None):
        if c.biome == 'sky_island':
            return abs(float(c.x if x is None else x)-c.home_x)<=c.patrol_radius
        if self.biome_system is None:
            return True
        try:
            return str(self.biome_system.biome_at_world_x(c.x if x is None else x)) == str(c.biome)
        except Exception:
            return True

    def _water_column(self, x):
        if self.env is None:
            return None
        tx = int(float(x) // TILE_SIZE)
        tx = max(0, min(self.physics.world.width_tiles - 1, tx))
        key = tx
        cached = self._water_column_cache.get(key)
        if cached is not None:
            return cached
        # Habitat queries use only free surface water, not saturated soil or
        # the underground waterway. This prevents fish from treating deep
        # groundwater beneath intact terrain as a swimmable open column.
        solid_row = self.physics.world.fauna_surface_row(tx)
        rows = [ty for ty in range(self.physics.world.height_tiles)
                if float(self.env.water_amount(tx, ty)) > 0.025
                and (solid_row is None or int(ty) < int(solid_row))]
        if not rows:
            result = None
        else:
            rows.sort()
            group = [rows[0]]
            for ty in rows[1:]:
                if ty == group[-1] + 1:
                    group.append(ty)
                else:
                    break
            top = group[0]; bottom = group[-1]
            amount = max(0.0, min(1.0, float(self.env.water_amount(tx, top))))
            if self.water_system is not None:
                try:
                    surface = float(self.water_system.surface_y(tx, top))
                except Exception:
                    surface = (float(top) + 1.0 - amount) * TILE_SIZE
            else:
                surface = (float(top) + 1.0 - amount) * TILE_SIZE
            floor = float((bottom + 1) * TILE_SIZE)
            try:
                floor = max(floor, float(self.physics.world.water_floor_y(tx, bottom)))
            except Exception:
                pass
            result = (tx, top, bottom, surface, floor)
        self._water_column_cache[key] = result
        return result

    def _point_in_water(self, x, y):
        if self.env is None:
            return False
        tx = int(float(x) // TILE_SIZE); ty = int(float(y) // TILE_SIZE)
        if not (0 <= tx < self.physics.world.width_tiles and 0 <= ty < self.physics.world.height_tiles):
            return False
        # Prefer WaterSystem's fractional geometry. It understands LOW/MID/HIGH
        # terrain capacity, so a point above a partial solid surface can still be
        # correctly submerged even when it shares the same tile row.
        if self.water_system is not None:
            try:
                if float(self.water_system.depth_at_world(float(x), float(y))) > 2.0:
                    return True
            except Exception:
                pass
        # Fallback for tests/older environments without WaterSystem: only free
        # cells above the first solid row count, excluding saturated soil and
        # the deep groundwater channel from ordinary fish habitat.
        solid_row = self.physics.world.fauna_surface_row(tx)
        if solid_row is not None and int(ty) >= int(solid_row):
            return False
        return float(self.env.water_amount(tx, ty)) > 0.025

    def _ground_surface_y(self, x):
        """Visible top of the first solid terrain column at world X."""
        tx = int(float(x) // TILE_SIZE)
        tx = max(0, min(self.physics.world.width_tiles - 1, tx))
        row = self.physics.world.fauna_surface_row(tx)
        if row is None:
            return float(self.physics.world.height_tiles * TILE_SIZE)
        top_ratio = 0.0
        try:
            if self.physics.world.get_tile(tx, int(row)) in LAYERED_SOLID_TILES:
                top_ratio = float(soil_top_ratio(self.physics.world.soil_layer_mask_at(tx, int(row))))
        except Exception:
            top_ratio = 0.0
        return (float(row) + top_ratio) * TILE_SIZE

    def _support_surface_y(self, x):
        """Top surface a low flyer must stay above (terrain or water)."""
        ground_y = self._ground_surface_y(x)
        col = self._water_column(x)
        if col is not None:
            return min(float(ground_y), float(col[3]))
        return float(ground_y)

    def _dry_standable(self, x):
        """Bird landing test: solid terrain exists and open water is absent."""
        if not self._home_biome_ok_dummy_x(x):
            return False
        tx = int(float(x) // TILE_SIZE)
        tx = max(0, min(self.physics.world.width_tiles - 1, tx))
        if self.physics.world.fauna_surface_row(tx) is None:
            return False
        return self._water_column(x) is None

    def _home_biome_ok_dummy_x(self, x):
        # Helper used by generic surface routines where no creature instance is
        # available. Biome ownership is validated separately by callers.
        return 0.0 <= float(x) < float(self.physics.world.width_tiles * TILE_SIZE)

    def _find_bird_landing_x(self, c, desired_x):
        """Find a dry landing point inside the bird's authored biome/patrol."""
        world_max = float(self.physics.world.width_tiles * TILE_SIZE - 1)
        desired_x = max(1.0, min(world_max, float(desired_x)))
        radius_tiles = max(2, int(math.ceil(float(c.patrol_radius) / TILE_SIZE)))
        base_tx = int(desired_x // TILE_SIZE)
        for r in range(radius_tiles + 1):
            choices = (base_tx,) if r == 0 else (base_tx - r, base_tx + r)
            for tx in choices:
                x = (float(tx) + 0.5) * TILE_SIZE
                if abs(x - float(c.home_x)) > float(c.patrol_radius) * 1.15:
                    continue
                if not self._home_biome_ok(c, x):
                    continue
                if self._water_column(x) is not None:
                    continue
                try:
                    if self.physics.world.fauna_surface_row(tx) is None:
                        continue
                except Exception:
                    continue
                return x
        # Home was authored as dry during spawn; retain it as last resort.
        if self._home_biome_ok(c, c.home_x) and self._water_column(c.home_x) is None:
            return float(c.home_x)
        return None

    def _apply_out_of_water(self, c, dt, damage=False):
        """Expose an aquatic actor to gravity; fish begin suffocating after 3 s."""
        c.vx = 0.0
        c.animation_state_override = "idle"
        c.behavior_state = "stranded"
        c.out_of_water_time = float(getattr(c, "out_of_water_time", 0.0)) + float(dt)
        c.vy = min(700.0, float(c.vy) + GRAVITY * float(dt))
        hit, grounded = self.physics.move_vertical(c, c.vy * float(dt))
        if hit:
            c.vy = 0.0
        c.grounded = bool(grounded)
        if damage and c.out_of_water_time >= 3.0:
            # Continuous HP loss after a grace window. At 12% max HP/s a healthy
            # fish still has several seconds for newly supplied water to save it.
            dps = max(2.0, float(c.max_hp) * 0.12)
            c.hp = max(0.0, float(c.hp) - dps * float(dt))

    def _update_fish(self, c, dt):
        """Strict aquatic AI: fish may swim only while physically inside water."""
        center_y = float(c.y) - float(c.height()) * 0.5
        in_water = self._point_in_water(c.x, center_y)
        if not in_water:
            self._apply_out_of_water(c, dt, damage=True)
            return
        c.out_of_water_time = 0.0
        c.habitat_damage_clock = 0.0
        c.animation_state_override = ""
        self._update_swimmer(c, dt)

    def _update_bird(self, c, dt):
        """Perch -> vertical takeoff -> low flight -> landing state machine.

        A bird never walks horizontally on land. Any horizontal relocation is
        performed only after it has visibly left the ground, so the standing
        sprite is reserved for perched/landed states and the move sprite means
        flapping flight.
        """
        c.motion_time = float(getattr(c, "motion_time", 0.0)) + float(dt)
        c.flight_timer = float(getattr(c, "flight_timer", 0.0)) + float(dt)
        mode = str(getattr(c, "flight_mode", "") or "")
        ground_y = self._ground_surface_y(c.x)
        altitude = max(0.0, float(ground_y) - float(c.y))

        if not mode:
            mode = "perched" if altitude <= 3.0 else "flight"
            c.flight_mode = mode
            stable = sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id)))
            c.rest_timer = 1.4 + (stable % 7) * 0.28
            if mode == "flight":
                c.facing = 1 if c.facing >= 0 else -1
                c.flight_target_x = float(c.home_x) + float(c.facing) * float(c.patrol_radius) * 0.65
                c.flight_target_y = float(self._support_surface_y(c.x)) - 32.0

        if mode == "perched":
            c.animation_state_override = "idle"
            c.behavior_state = "idle"
            c.vx = 0.0
            # Keep the feet anchored on terrain, settling through physics if a
            # save/load or terrain edit leaves the bird a few pixels above it.
            if abs(float(c.y) - float(ground_y)) > 1.0:
                c.vy = min(420.0, float(c.vy) + GRAVITY * float(dt))
                hit, grounded = self.physics.move_vertical(c, c.vy * float(dt))
                if hit:
                    c.vy = 0.0
                c.grounded = bool(grounded)
            else:
                c.y = float(ground_y)
                c.vy = 0.0
                c.grounded = True
            c.rest_timer = max(0.0, float(getattr(c, "rest_timer", 0.0)) - float(dt))
            if c.rest_timer > 0.0:
                return

            # Alternate sides of the patrol area; landing point must be dry.
            if c.facing == 0:
                c.facing = 1
            desired = float(c.home_x) + float(c.facing) * float(c.patrol_radius) * 0.72
            target_x = self._find_bird_landing_x(c, desired)
            if target_x is None or abs(target_x - c.x) < TILE_SIZE * 0.7:
                c.facing *= -1
                desired = float(c.home_x) + float(c.facing) * float(c.patrol_radius) * 0.72
                target_x = self._find_bird_landing_x(c, desired)
            if target_x is None or abs(target_x - c.x) < TILE_SIZE * 0.7:
                c.rest_timer = 1.8
                return
            c.flight_target_x = float(target_x)
            support = self._support_surface_y(c.x)
            c.flight_target_y = float(support) - min(44.0, max(24.0, c.height() * 1.20))
            c.flight_mode = "takeoff"
            c.flight_timer = 0.0
            c.grounded = False
            c.vx = 0.0  # strictly no ground sliding before lift-off
            c.vy = -max(54.0, float(c.speed) * 0.78)
            c.animation_state_override = ""
            c.behavior_state = "takeoff"
            return

        if mode == "takeoff":
            c.animation_state_override = ""
            c.behavior_state = "takeoff"
            # Vertical first. Horizontal flight is unlocked only after a clearly
            # visible gap exists under the feet.
            desired_vy = -max(48.0, float(c.speed) * 0.66)
            c.vy = self._approach(c.vy, desired_vy, float(c.speed) * 2.2 * float(dt))
            hit, _grounded = self.physics.move_vertical(c, c.vy * float(dt))
            if hit and c.vy < 0.0:
                c.vy = 0.0
                c.flight_mode = "landing"
                return
            ground_y = self._ground_surface_y(c.x)
            altitude = max(0.0, float(ground_y) - float(c.y))
            if altitude >= 10.0:
                direction = 1 if float(c.flight_target_x) >= float(c.x) else -1
                c.facing = direction
                c.vx = direction * float(c.speed) * 0.45
            else:
                c.vx = 0.0
            if altitude >= 16.0:
                c.flight_mode = "flight"
                c.flight_timer = 0.0
            return

        if mode == "flight":
            c.animation_state_override = ""
            c.behavior_state = "flight"
            target_x = float(getattr(c, "flight_target_x", c.home_x))
            direction = 1 if target_x >= c.x else -1
            c.facing = direction
            desired_vx = direction * float(c.speed) * 0.72
            c.vx = self._approach(c.vx, desired_vx, float(c.speed) * 2.5 * float(dt))
            # Do not allow flight to drift into a foreign biome. Turn back toward
            # the known dry home if the next horizontal sample crosses a border.
            look_x = c.x + c.vx * float(dt) * 2.0
            if not self._home_biome_ok(c, look_x):
                c.flight_target_x = float(c.home_x)
                c.vx *= -0.35
            self.physics.move_horizontal(c, c.vx * float(dt))

            support_y = self._support_surface_y(c.x)
            stable = sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id)))
            cruise_alt = 28.0 + float(stable % 4) * 5.0
            cruise_alt = max(22.0, min(48.0, cruise_alt))
            bob = 2.5 * math.sin(c.motion_time * 2.1 + (stable % 11))
            desired_y = float(support_y) - cruise_alt + bob
            # Hard low-altitude band: birds in this controller never disappear
            # into high sky, and never touch the water/ground while cruising.
            desired_y = max(float(support_y) - 52.0, min(float(support_y) - 18.0, desired_y))
            desired_vy = max(-float(c.speed) * 0.60, min(float(c.speed) * 0.60, (desired_y - c.y) * 2.0))
            c.vy = self._approach(c.vy, desired_vy, float(c.speed) * 2.4 * float(dt))
            hit, _ = self.physics.move_vertical(c, c.vy * float(dt))
            if hit:
                c.vy *= -0.25
            c.grounded = False

            if abs(float(c.x) - target_x) <= TILE_SIZE * 0.75:
                if self._water_column(target_x) is None:
                    c.flight_mode = "landing"
                    c.flight_timer = 0.0
                else:
                    # Never land a non-waterbird on water: choose a dry perch.
                    alt_target = self._find_bird_landing_x(c, c.home_x)
                    if alt_target is not None:
                        c.flight_target_x = float(alt_target)
            return

        # landing
        c.animation_state_override = ""
        c.behavior_state = "landing"
        target_x = float(getattr(c, "flight_target_x", c.home_x))
        if self._water_column(target_x) is not None:
            replacement = self._find_bird_landing_x(c, c.home_x)
            if replacement is None:
                c.flight_mode = "flight"
                return
            target_x = c.flight_target_x = float(replacement)
        direction = 1 if target_x >= c.x else -1
        c.facing = direction
        c.vx = self._approach(c.vx, direction * float(c.speed) * 0.28, float(c.speed) * 2.2 * float(dt))
        if abs(c.x - target_x) < 5.0:
            c.vx = 0.0
        self.physics.move_horizontal(c, c.vx * float(dt))
        ground_y = self._ground_surface_y(c.x)
        desired_vy = max(30.0, min(float(c.speed) * 0.55, (ground_y - c.y) * 2.0))
        c.vy = self._approach(c.vy, desired_vy, float(c.speed) * 2.4 * float(dt))
        hit, grounded = self.physics.move_vertical(c, c.vy * float(dt))
        if grounded or hit or c.y >= ground_y - 1.0:
            c.y = float(ground_y)
            c.vx = c.vy = 0.0
            c.grounded = True
            c.flight_mode = "perched"
            c.flight_timer = 0.0
            stable = sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id)))
            c.rest_timer = 1.6 + float(stable % 6) * 0.32
            c.behavior_state = "idle"
            c.animation_state_override = "idle"
            c.facing *= -1

    def _update_insect_low(self, c, dt):
        """Low-altitude insect flight: above surfaces, never underwater/high sky."""
        c.motion_time = float(getattr(c, "motion_time", 0.0)) + float(dt)
        if c.facing == 0:
            c.facing = 1
        left = float(c.home_x) - float(c.patrol_radius)
        right = float(c.home_x) + float(c.patrol_radius)
        if c.x <= left + 5.0:
            c.facing = 1
        elif c.x >= right - 5.0:
            c.facing = -1
        look_x = c.x + c.facing * max(18.0, c.speed * 0.30)
        if not self._home_biome_ok(c, look_x):
            c.facing *= -1

        stable = sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id)))
        phase = (stable % 997) / 997.0 * 6.2831853
        desired_vx = c.facing * c.speed * (0.48 + 0.10 * math.sin(c.motion_time * 1.7 + phase))
        c.vx = self._approach(c.vx, desired_vx, c.speed * 2.8 * dt)
        next_x = c.x + c.vx * dt
        if self._home_biome_ok(c, next_x):
            c.x = next_x
        else:
            c.facing *= -1
            c.vx *= -0.35

        support_y = self._support_surface_y(c.x)
        lift = 20.0 + float(stable % 4) * 5.0
        # Insects use a tighter 18..42 px band than birds.
        target_y = support_y - lift + 3.2 * math.sin(c.motion_time * 3.1 + phase)
        target_y = max(support_y - 42.0, min(support_y - 18.0, target_y))
        c.y = self._approach(c.y, target_y, c.speed * 1.15 * dt)

        # Water can rise underneath an insect after terrain edits. Clamp it back
        # above the liquid surface immediately instead of allowing a submerged
        # flying state.
        col = self._water_column(c.x)
        if col is not None:
            surface_y = float(col[3])
            if c.y >= surface_y - 6.0 or self._point_in_water(c.x, c.y):
                c.y = surface_y - 12.0
        c.vy = 0.0
        c.behavior_state = "insect_fly"
        c.animation_state_override = ""
        c.grounded = False

    def _update_swimmer(self, c, dt):
        """Continuous fish-like swimming: forward cruise, smooth turns and depth control."""
        c.motion_time = float(getattr(c, "motion_time", 0.0)) + dt
        if float(getattr(c, "home_y", 0.0)) <= 0.0:
            c.home_y = float(c.y)
        if c.facing == 0:
            c.facing = 1

        center_y = float(c.y) - float(c.height()) * 0.5
        col = self._water_column(c.x)
        if col is None:
            # Aquatic non-fish (currently sea turtle) may become stranded after
            # drainage; they fall with terrain physics but do not suffocate like
            # obligate fish.
            self._apply_out_of_water(c, dt, damage=False)
            return
        c.out_of_water_time = 0.0
        c.animation_state_override = ""
        _tx, _top, _bottom, surface_y, floor_y = col
        min_y = surface_y + max(5.0, c.height() * 0.65)
        max_y = floor_y - max(5.0, c.height() * 0.65)
        if max_y <= min_y + 3.0:
            target_depth_y = (min_y + max_y) * 0.5
        else:
            phase = (sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id))) % 997) / 997.0
            band = 0.42 + 0.16 * __import__('math').sin(c.motion_time * 0.65 + phase * 6.2831853)
            target_depth_y = min_y + (max_y - min_y) * band

        # Patrol remains inside the species' home biome and liquid column.
        left = float(c.home_x) - float(c.patrol_radius)
        right = float(c.home_x) + float(c.patrol_radius)
        if c.x <= left + 5.0:
            c.facing = 1
        elif c.x >= right - 5.0:
            c.facing = -1

        look_x = c.x + c.facing * max(14.0, c.speed * 0.35)
        look_center_y = center_y
        if (not self._home_biome_ok(c, look_x)) or (not self._point_in_water(look_x, look_center_y)):
            c.facing *= -1

        # Fish flee smoothly from a nearby player instead of instantly teleporting/turning.
        dxp = c.x - self.player.x
        dyp = center_y - (self.player.y - self.player.height() * 0.5)
        flee = (dxp * dxp + dyp * dyp) < (TILE_SIZE * 1.55) ** 2
        if flee and abs(dxp) > 2.0:
            c.facing = 1 if dxp > 0.0 else -1
        speed_scale = 1.28 if flee else 0.72
        desired_vx = c.facing * c.speed * speed_scale
        desired_vy = max(-c.speed * 0.32, min(c.speed * 0.32, (target_depth_y - center_y) * 1.8))
        c.vx = self._approach(c.vx, desired_vx, c.speed * 2.1 * dt)
        c.vy = self._approach(c.vy, desired_vy, c.speed * 1.4 * dt)

        old_x, old_y = c.x, c.y
        c.x += c.vx * dt
        c.y += c.vy * dt
        new_center_y = c.y - c.height() * 0.5
        if (not self._home_biome_ok(c)) or (not self._point_in_water(c.x, new_center_y)):
            c.x, c.y = old_x, old_y
            c.facing *= -1
            c.vx *= -0.35
            c.vy *= -0.25
        c.behavior_state = "swim"
        c.grounded = False

    def _update_cave_flyer(self, c, dt, allow_hostile=True):
        """Hostile cave flight constrained around its underground home band."""
        c.motion_time=float(getattr(c,"motion_time",0.0))+float(dt)
        if c.facing==0:c.facing=1
        p=self.player
        dx=float(p.x)-float(c.x)
        dy=(float(p.y)-float(p.height())*0.5)-(float(c.y)-float(c.height())*0.5)
        radius=float(CREATURE_AGGRO_RADIUS_PX)*1.05
        vertical=max(120.0,float(CREATURE_AGGRO_VERTICAL_PX)*1.35)
        near=(abs(dx)<=radius and abs(dy)<=vertical)
        detected=self._update_player_detection(
            c,abs(dx),abs(dy),radius,vertical,allow_new=bool(allow_hostile)
        )
        if bool(allow_hostile) and self._can_attack_player(c) and detected and near:
            c.facing=1 if dx>=0.0 else -1
            desired_vx=c.facing*float(c.speed)*0.92
            desired_center_y=float(p.y)-float(p.height())*0.5
            desired_vy=max(-float(c.speed)*0.58,min(float(c.speed)*0.58,(desired_center_y-(float(c.y)-float(c.height())*0.5))*1.25))
            c.behavior_state="chase"
        else:
            if float(getattr(c,"home_x",0.0))==0.0:c.home_x=float(c.x)
            if float(c.x)<=float(c.home_x)-float(c.patrol_radius):c.facing=1
            elif float(c.x)>=float(c.home_x)+float(c.patrol_radius):c.facing=-1
            desired_vx=c.facing*float(c.speed)*0.48
            stable=sum((i+1)*ord(ch) for i,ch in enumerate(str(c.entity_id))); target_y=float(getattr(c,"home_y",c.y))+math.sin(c.motion_time*1.5+(stable%13))*18.0
            desired_vy=max(-float(c.speed)*0.35,min(float(c.speed)*0.35,(target_y-float(c.y))*1.2))
            c.behavior_state="fly"
        c.vx=self._approach(float(c.vx),desired_vx,float(c.speed)*2.6*float(dt))
        c.vy=self._approach(float(c.vy),desired_vy,float(c.speed)*2.3*float(dt))
        hit_x=self.physics.move_horizontal(c,float(c.vx)*float(dt))
        hit_y,_grounded=self.physics.move_vertical(c,float(c.vy)*float(dt))
        if hit_x:
            c.vx*=-0.35;c.facing*=-1
        if hit_y:c.vy*=-0.25
        c.grounded=False
        c.animation_state_override="move" if abs(c.vx)+abs(c.vy)>6.0 else "idle"

    def _update_ghost(self, c, dt, allow_hostile=True):
        """Spectral underground patrol. Ghosts float through terrain but stay near home."""
        c.motion_time=float(getattr(c,"motion_time",0.0))+float(dt)
        p=self.player; dx=float(p.x)-float(c.x); dy=(float(p.y)-float(p.height())*.5)-(float(c.y)-float(c.height())*.5)
        radius=float(CREATURE_AGGRO_RADIUS_PX)*.95;vertical=140.0
        near=abs(dx)<=radius and abs(dy)<=vertical
        detected=self._update_player_detection(
            c,abs(dx),abs(dy),radius,vertical,allow_new=bool(allow_hostile)
        )
        if bool(allow_hostile) and self._can_attack_player(c) and detected and near:
            c.facing=1 if dx>=0 else -1; desired_vx=c.facing*float(c.speed)*.70; desired_vy=max(-float(c.speed)*.48,min(float(c.speed)*.48,dy*1.05));c.behavior_state="chase"
        else:
            if float(getattr(c,"home_x",0.0))==0.0:c.home_x=float(c.x)
            if float(getattr(c,"home_y",0.0))==0.0:c.home_y=float(c.y)
            phase=(sum((i+1)*ord(ch) for i,ch in enumerate(str(c.entity_id)))%31)*.31
            target_x=float(c.home_x)+math.sin(c.motion_time*.45+phase)*float(c.patrol_radius)*.72
            target_y=float(c.home_y)+math.sin(c.motion_time*.72+phase*.7)*34.0
            c.facing=1 if target_x>=c.x else -1
            desired_vx=max(-float(c.speed)*.45,min(float(c.speed)*.45,(target_x-c.x)*.8))
            desired_vy=max(-float(c.speed)*.35,min(float(c.speed)*.35,(target_y-c.y)*.8));c.behavior_state="float"
        c.vx=self._approach(c.vx,desired_vx,float(c.speed)*1.8*dt);c.vy=self._approach(c.vy,desired_vy,float(c.speed)*1.5*dt)
        c.x+=c.vx*dt;c.y+=c.vy*dt
        # Keep ghosts inside a broad underground band around their authored spawn.
        c.x=max(float(c.home_x)-float(c.patrol_radius),min(float(c.home_x)+float(c.patrol_radius),float(c.x)))
        c.y=max(float(c.home_y)-70.0,min(float(c.home_y)+70.0,float(c.y)))
        c.grounded=False;c.animation_state_override="move" if abs(c.vx)+abs(c.vy)>5.0 else "idle"

    def _update_waterbird(self, c, dt):
        """Waterfowl paddle on the surface using the standing/idle pose."""
        c.motion_time = float(getattr(c, "motion_time", 0.0)) + dt
        if c.facing == 0:
            c.facing = 1
        col = self._water_column(c.x)
        if col is None:
            # If a lake is drained underneath it, a waterbird settles to terrain
            # and stands still rather than hovering or "walking" through air.
            c.animation_state_override = "idle"
            c.behavior_state = "idle"
            c.vx = 0.0
            c.vy = min(700.0, c.vy + GRAVITY * dt)
            hit, grounded = self.physics.move_vertical(c, c.vy * dt)
            if hit:
                c.vy = 0.0
            c.grounded = bool(grounded)
            return
        if c.x <= c.home_x - c.patrol_radius + 6.0:
            c.facing = 1
        elif c.x >= c.home_x + c.patrol_radius - 6.0:
            c.facing = -1
        look_x = c.x + c.facing * 18.0
        if not self._home_biome_ok(c, look_x) or self._water_column(look_x) is None:
            c.facing *= -1
        desired_vx = c.facing * c.speed * 0.36
        c.vx = self._approach(c.vx, desired_vx, c.speed * 1.7 * dt)
        old_x = c.x
        c.x += c.vx * dt
        col = self._water_column(c.x)
        if col is None:
            c.x = old_x
            c.facing *= -1
            c.vx *= -0.25
            col = self._water_column(c.x)
        if col is not None:
            surface_y = float(col[3])
            bob = 0.8 * math.sin(c.motion_time * 2.0)
            # Feet anchor is 2 px under the surface: visually the standing legs
            # disappear into water and read as hidden paddling feet.
            c.y = surface_y + 2.0 + bob
        c.vy = 0.0
        c.behavior_state = "water_paddle"
        c.animation_state_override = "idle"
        c.grounded = False

    def _update_low_flyer(self, c, dt):
        """Compatibility for old saves: legacy fly_low now uses FIX26 low flight."""
        self._update_insect_low(c, dt)

    def update(self, dt):
        dt = max(0.0, float(dt))
        # Presentation TTL advances independently, but is bounded to the same
        # fixed update step and never feeds back into AI/combat.
        self.creature_vfx_system.update(dt)
        self.bound_vfx_system.update(dt)
        if self.explosion_vfx:
            keep=[]
            for row in self.explosion_vfx:
                item=dict(row);item["life"]=max(0.0,float(item.get("life",0.0))-dt)
                if item["life"]>0.0:keep.append(item)
            self.explosion_vfx=keep[-8:]
        self._update_enemy_magic_bolts(dt)
        self._water_cache_clock += dt
        if self._water_cache_clock >= 0.20:
            self._water_cache_clock = 0.0
            self._water_column_cache.clear()
        self.startup_grace = max(0.0, float(self.startup_grace) - dt)
        startup_safe = self.startup_grace > 0.0
        p = self.player
        population = self.creatures()
        self.respawn.update(dt, population=population)
        self._simulation_clock = float(self._simulation_clock) + dt
        simulation_clock = float(self._simulation_clock)
        chunk_span = float(TILE_SIZE * CHUNK_SIZE)
        chunk_is_active = self.chunk_streamer.is_active
        active_population = []
        self.active_population = active_population
        for c in population:
            self._process_death(c)
            if not c.active:
                continue

            # FIX77: reject sleeping chunks before touching twelve per-creature
            # timers and cooldown dictionaries. Timers retain FIX76 semantics by
            # catching up once when an actor re-enters the active chunk window;
            # movement/AI itself still advances only by this fixed frame's dt.
            cx = int(c.x // chunk_span)
            cy = int(c.y // chunk_span)
            if not chunk_is_active(cx, cy):
                c.vx = 0.0
                continue
            if not getattr(c,"background_only",False): active_population.append(c)

            last_timer_clock = getattr(c, "_fix77_timer_clock", None)
            if last_timer_clock is None:
                timer_dt = dt
            else:
                try:
                    timer_dt = max(dt, simulation_clock - float(last_timer_clock))
                except Exception:
                    timer_dt = dt
            c._fix77_timer_clock = simulation_clock

            # FIX11: all creatures need an animation clock, not only swimmers /
            # waterbirds / flyers.  Ground animals previously kept motion_time at
            # zero, so an authored move clip appeared frozen on frame 0.
            c.visual_animation_time = float(getattr(c, "visual_animation_time", 0.0)) + timer_dt

            c.attack_cooldown = max(0.0, c.attack_cooldown - timer_dt)
            c.hurt_timer = max(0.0, c.hurt_timer - timer_dt)
            c.attack_anim_timer = max(0.0, float(getattr(c, "attack_anim_timer", 0.0)) - timer_dt)
            c.attack_recoil_timer = max(0.0, float(getattr(c, "attack_recoil_timer", 0.0)) - timer_dt)
            c.slow_timer = max(0.0, float(getattr(c, "slow_timer", 0.0)) - timer_dt)
            c.stun_timer = max(0.0, float(getattr(c, "stun_timer", 0.0)) - timer_dt)
            self._tick_action_cooldowns(c, timer_dt)

            if update_special_actor(self,c,dt,startup_safe):
                self._process_death(c)
                continue

            # FIX19: an active authored action owns locomotion/animation until
            # its authored duration ends.  This branch runs before species
            # locomotion so swimmers/flyers can use dive actions too.
            if str(getattr(c, "ai_action_id", "")):
                action = self._action_def(c, c.ai_action_id)
                if not self._can_attack_player(c):
                    # A legacy save or data edit may leave an attack active on
                    # peaceful fauna. Cancel it before any hit event is emitted.
                    if isinstance(action, dict):
                        self._finish_ai_action(c, action)
                    else:
                        c.ai_action_id = ""; c.animation_state_override = ""
                    c.ai_action_hit_done = False
                elif c.stun_timer > 0.0:
                    self._mark_player_detected(c)
                    # Stun interrupts an authored attack immediately.  Keep a
                    # short cooldown so the same action cannot restart on the
                    # very first frame after stun expires.
                    if isinstance(action, dict):
                        self._finish_ai_action(c, action)
                    else:
                        c.ai_action_id = ""; c.animation_state_override = ""
                else:
                    self._mark_player_detected(c)
                    self._update_active_ai_action(c, action, dt)
                    self._process_death(c)
                    continue

            pdx = p.x - c.x
            pdy = (p.y - p.height() * 0.5) - (c.y - c.height() * 0.5)
            pdx_abs = abs(pdx)
            pdy_abs = abs(pdy)
            self._update_player_detection(
                c,
                pdx_abs,
                pdy_abs,
                CREATURE_AGGRO_RADIUS_PX,
                CREATURE_AGGRO_VERTICAL_PX,
                allow_new=(not startup_safe),
            )
            if (
                (not startup_safe)
                and c.stun_timer <= 0.0
                and c.attack_recoil_timer <= 0.0
                and not bool(getattr(c, "attack_rearm_required", False))
                and self._hostile_action_visible(c, pdx_abs, pdy_abs, allow_new=True)
            ):
                chosen = self._choose_ai_action(c, pdx_abs, pdy_abs)
                if chosen is not None:
                    action_id, action = chosen
                    self._mark_player_detected(c)
                    self._start_ai_action(c, action_id, action)
                    self._update_active_ai_action(c, action, dt)
                    self._process_death(c)
                    continue

            if agile_kong(self,c,dt,startup_safe):
                self._process_death(c)
                continue
            locomotion = str(getattr(c, "locomotion", "ground"))
            # FIX130 fixed placement: keep biological habitat/physics intact,
            # but suppress voluntary locomotion between authored attacks.
            if self._editor_move_mode(c)=="hold" and locomotion in (
                "fish","swim","bird","waterbird","insect_low","fly_low","cave_fly","ghost_fly","sky_fly","fly"
            ):
                c.vx=0.0;c.vy=0.0;c.behavior_state="idle";c.animation_state_override="idle"
                self._process_death(c)
                continue
            # FIX31 electric paralysis applies to every locomotion class, not
            # only ground walkers.  Aquatic/flying actors stop their AI motion
            # for the full 3-second stun instead of continuing to swim/fly.
            if c.stun_timer > 0.0 and locomotion in (
                "fish","swim","bird","waterbird","insect_low","fly_low","cave_fly","ghost_fly","sky_fly"
            ):
                c.behavior_state="stunned"
                c.animation_state_override="idle"
                c.vx=0.0;c.vy=0.0
                self._process_death(c)
                continue
            if locomotion in ("rooted", "ceiling"):
                hold_plant(self,c,dt)
                c.behavior_state="stunned" if c.stun_timer>0 else "idle"
                c.animation_state_override="idle"
                self._process_death(c)
                continue
            if locomotion == "sky_fly":
                update_sky_flyer(self,c,dt,allow_hostile=((not startup_safe) and self._editor_chase_allowed(c)))
                self._process_death(c)
                continue
            if locomotion == "fish":
                self._update_fish(c, dt)
                self._process_death(c)
                continue
            if locomotion == "swim":
                self._update_swimmer(c, dt)
                self._process_death(c)
                continue
            if locomotion == "bird":
                self._update_bird(c, dt)
                self._process_death(c)
                continue
            if locomotion in ("cave_fly", "sky_fly"):
                self._update_cave_flyer(c, dt, allow_hostile=((not startup_safe) and self._editor_chase_allowed(c)))
                self._process_death(c)
                continue
            if locomotion == "ghost_fly":
                self._update_ghost(c, dt, allow_hostile=((not startup_safe) and self._editor_chase_allowed(c)))
                self._process_death(c)
                continue
            if locomotion == "waterbird":
                self._update_waterbird(c, dt)
                self._process_death(c)
                continue
            if locomotion in ("insect_low", "fly_low"):
                self._update_insect_low(c, dt)
                self._process_death(c)
                continue

            dx = p.x - c.x
            dy = (p.y - p.height() * 0.5) - (c.y - c.height() * 0.5)
            horizontal_distance = abs(dx)
            vertical_distance = abs(dy)
            direction = 1 if dx >= 0.0 else -1
            c.facing = direction

            contacting = (not self._player_is_rolling()) and self._contacting_player(c)
            if bool(getattr(c, "attack_rearm_required", False)):
                cb = c.bbox(); pb = p.bbox()
                hgap = self._axis_gap(cb[0], cb[2], pb[0], pb[2])
                # A bite/strike is one discrete event. The creature must create
                # a visible gap before the next attack can arm, preventing an
                # overlap from looking like poison/bleed damage.
                if (not contacting) and hgap >= float(CREATURE_ATTACK_REARM_GAP_PX):
                    c.attack_rearm_required = False

            if startup_safe:
                # Let gravity settle actors onto the authored terrain, but do
                # not chase/attack while the fullscreen view is appearing.
                c.behavior_state = "idle"
                c.vx = 0.0
                c.vy = min(700.0, c.vy + GRAVITY * dt)
                hit, grounded = self.physics.move_vertical(c, c.vy * dt)
                if hit:
                    c.vy = 0.0
                c.grounded = bool(grounded)
                continue

            # Electric magic may briefly stun an actor.  Gravity still runs,
            # but hostile motion/attacks pause during the stun.
            if c.stun_timer > 0.0:
                c.behavior_state = "stunned"
                c.vx = 0.0
            elif c.attack_recoil_timer > 0.0:
                c.behavior_state = "recoil"
                c.vx = -direction * c.speed * 0.85
            elif bool(getattr(c, "attack_rearm_required", False)):
                c.behavior_state = "recover"
                c.vx = -direction * c.speed * 0.55
            elif self._can_attack_player(c) and bool(getattr(c, "player_detected", False)) and contacting:
                c.behavior_state = "attack"
                c.vx = 0.0
                if (not self._legacy_contact_overridden(c)) and c.attack_cooldown <= 0.0 and not bool(getattr(c, "attack_rearm_required", False)):
                    self._damage_player(c, direction)
            elif (
                self._can_attack_player(c)
                and self._editor_chase_allowed(c)
                and bool(getattr(c, "player_detected", False))
                and horizontal_distance <= float(getattr(c,"editor_detection_radius_px",0.0) or CREATURE_AGGRO_RADIUS_PX)
                and vertical_distance <= float(getattr(c,"editor_detection_vertical_px",0.0) or CREATURE_AGGRO_VERTICAL_PX)
            ):
                c.behavior_state = "chase"
                speed_scale = 0.52 if c.slow_timer > 0.0 else 1.0
                c.vx = direction * c.speed * speed_scale
            else:
                if not self._editor_patrol_allowed(c):
                    c.vx=0.0
                    c.behavior_state="idle"
                    target=c.x
                else:
                    if c.home_x == 0.0:
                        c.home_x = c.x
                    stable = sum((i + 1) * ord(ch) for i, ch in enumerate(str(c.entity_id)))
                    phase = (stable % 1024) / 1023.0
                    target = c.home_x + (phase * 2.0 - 1.0) * c.patrol_radius
                    if not self._home_biome_ok(c, target):
                        target = c.home_x
                    if not self._home_biome_ok(c):
                        target = c.home_x
                    if abs(c.x - target) < 8.0:
                        c.vx = 0.0
                        c.behavior_state = "idle"
                    else:
                        c.facing = 1 if target > c.x else -1
                        c.vx = c.facing * c.speed * 0.35
                        c.behavior_state = "wander"

            # Terrestrial animals never cross into a different authored biome.
            # This is especially important at lake/ocean edges: a wolf/slime
            # may see the player across the water, but it does not become an
            # aquatic creature merely because aggro is active.
            next_x = c.x + c.vx * dt
            if not self._home_biome_ok(c, next_x):
                if abs(c.home_x - c.x) > 2.0:
                    c.vx = (1.0 if c.home_x > c.x else -1.0) * c.speed * 0.45
                else:
                    c.vx = 0.0

            if c.biome == 'sky_island':
                # Ground cloud fauna must not chase a falling player off an
                # island or cross the ladder shaft. Knockback/mining still work.
                nx=c.x+c.vx*dt
                direction=1 if c.vx>=0 else -1
                probe_x=nx+direction*(c.width()*.5+3.)
                row=int((c.y+4.)//TILE_SIZE)
                tx=int(probe_x//TILE_SIZE)
                top=self.physics.world.solid_top_y(tx,row)
                if abs(nx-c.home_x)>c.patrol_radius or top is None or abs(top-c.y)>TILE_SIZE*.6:
                    c.vx=0.;c.facing=-direction
            self.physics.move_horizontal(c, c.vx * dt)
            c.vy = min(700.0, c.vy + GRAVITY * dt)
            hit, grounded = self.physics.move_vertical(c, c.vy * dt)
            if hit:
                c.vy = 0.0
            c.grounded = bool(grounded)

            # Re-test after movement so a charging creature can attack on the
            # exact frame it reaches the player instead of waiting one update.
            if (
                self._can_attack_player(c)
                and c.stun_timer <= 0.0
                and c.attack_cooldown <= 0.0
                and not bool(getattr(c, "attack_rearm_required", False))
                and (not self._player_is_rolling())
                and self._contacting_player(c)
                and (not self._legacy_contact_overridden(c))
            ):
                self._mark_player_detected(c)
                direction = 1 if p.x >= c.x else -1
                c.facing = direction
                c.behavior_state = "attack"
                c.vx = 0.0
                self._damage_player(c, direction)

            self._process_death(c)
