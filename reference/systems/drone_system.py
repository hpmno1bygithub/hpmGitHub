# -*- coding: utf-8 -*-
"""Bounded three-drone companion formation and synchronized kamikaze attack.

FIX71 keeps exactly three reusable runtime units.  Orbit phase, approach lane
and a final separation pass prevent their paths from collapsing into one
sprite.  Weapon durability is *not* mutated here: MeleeSystem/InventorySystem
commits a successful heavy use before ``queue_attack`` and this system only
finishes the already-authorized attack.
"""
import math

from config import TILE_SIZE, CHUNK_SIZE
from entities.creature import Creature
from systems.weapon_catalog import WEAPON_DEFS
from world.tile_registry import tile_def


class DroneUnit:
    """Small reusable state record; never added to Scene or serialized."""

    __slots__ = (
        "index", "phase_offset", "active", "state", "x", "y", "prev_x",
        "prev_y", "target_id", "cooldown", "state_elapsed", "trail",
        "last_hit_id", "attack_animation_offset", "attack_vfx_elapsed",
        "explosion_timer", "explosion_duration", "explosion_x",
        "explosion_y", "heavy_ready",
    )

    def __init__(self, index):
        self.index = int(index)
        self.phase_offset = float(index) * (math.pi * 2.0 / 3.0)
        self.active = False
        self.state = "inactive"
        self.x = 0.0
        self.y = 0.0
        self.prev_x = 0.0
        self.prev_y = 0.0
        self.target_id = ""
        self.cooldown = 0.0
        self.state_elapsed = 0.0
        self.trail = []
        self.last_hit_id = ""
        self.attack_animation_offset = 0.0
        self.attack_vfx_elapsed = 0.0
        self.explosion_timer = 0.0
        self.explosion_duration = 0.52
        self.explosion_x = 0.0
        self.explosion_y = 0.0
        self.heavy_ready = False

    def reset(self):
        index = self.index
        self.__init__(index)


class DroneSystem:
    WEAPON_ID = "flying_drone"
    ITEM_ID = "weapon_flying_drone"
    THREAT_STATES = {"chase", "attack", "recover", "recoil", "charge", "dive", "cast"}
    DEFAULT_EXPLOSION_DURATION = 0.52
    DRONE_COUNT = 3
    MIN_FORMATION_SEPARATION = 14.0
    HEAVY_SYNC_TIMEOUT = 1.35

    def __init__(self, game):
        self.game = game
        self.units = [DroneUnit(index) for index in range(self.DRONE_COUNT)]
        self.active = False
        self.state = "inactive"
        self.x = 0.0
        self.y = 0.0
        self.prev_x = 0.0
        self.prev_y = 0.0
        self.target_id = ""
        self.scan_timer = 0.0
        self.cooldown = 0.0
        self.state_elapsed = 0.0
        self.phase = 0.0
        self.heavy_committed = False
        self.explosion_timer = 0.0
        self.explosion_duration = self.DEFAULT_EXPLOSION_DURATION
        self.explosion_x = 0.0
        self.explosion_y = 0.0
        self.trail = []
        self.last_hit_id = ""
        self.pending_attack = None
        self.heavy_durability_result = None
        self.heavy_sync_elapsed = 0.0
        self.attack_animation_offset = 0.0
        self.attack_vfx_elapsed = 0.0
        self.sync_equipment(force=True)

    def rebind(self, game):
        self.game = game
        self.reset_transient()

    def reset_transient(self):
        """Discard runtime-only combat state after map adoption or manual load."""
        for unit in self.units:
            unit.reset()
        self.active = False
        self.state = "inactive"
        self.target_id = ""
        self.trail = []
        self.heavy_committed = False
        self.cooldown = 0.0
        self.scan_timer = 0.0
        self.state_elapsed = 0.0
        self.attack_animation_offset = 0.0
        self.attack_vfx_elapsed = 0.0
        self.explosion_timer = 0.0
        self.explosion_duration = self.DEFAULT_EXPLOSION_DURATION
        self.last_hit_id = ""
        self.pending_attack = None
        self.heavy_durability_result = None
        self.heavy_sync_elapsed = 0.0
        self.sync_equipment(force=True)

    @property
    def row(self):
        return WEAPON_DEFS[self.WEAPON_ID]

    def _profile(self):
        melee = getattr(self.game, "melee", None)
        if melee is not None and hasattr(melee, "presentation_profile"):
            try:
                return melee.presentation_profile(self.WEAPON_ID)
            except Exception:
                pass
        return {}

    def _profile_scale(self, key, default=1.0):
        try:
            value = float(self._profile().get(str(key), default) or default)
        except Exception:
            value = float(default)
        return max(0.50, min(1.50, value))

    def _sync_legacy_view(self):
        """Mirror unit zero for FIX62 renderers while exposing all three."""
        primary = self.units[0]
        self.active = any(unit.active for unit in self.units)
        self.state = str(primary.state if primary.active else (
            "explosion" if primary.explosion_timer > 0.0 else "inactive"
        ))
        self.x = float(primary.x)
        self.y = float(primary.y)
        self.prev_x = float(primary.prev_x)
        self.prev_y = float(primary.prev_y)
        self.target_id = str(primary.target_id)
        self.cooldown = float(primary.cooldown)
        self.state_elapsed = float(primary.state_elapsed)
        self.trail = primary.trail
        self.last_hit_id = str(primary.last_hit_id)
        self.attack_animation_offset = float(primary.attack_animation_offset)
        self.attack_vfx_elapsed = float(primary.attack_vfx_elapsed)
        self.explosion_timer = max(float(unit.explosion_timer) for unit in self.units)
        self.explosion_duration = max(float(unit.explosion_duration) for unit in self.units)
        self.explosion_x = float(primary.explosion_x)
        self.explosion_y = float(primary.explosion_y)

    @property
    def drone_instances(self):
        """Renderer bridge returning the three stable unit objects."""
        return tuple(self.units)

    def render_instances(self):
        """Return at most three detached interpolation/VFX packets."""
        rows = []
        for unit in self.units:
            if not unit.active and unit.explosion_timer <= 0.0:
                continue
            rows.append({
                "instance_id": "flying_drone_%d" % (unit.index + 1),
                "index": int(unit.index),
                "active": bool(unit.active),
                "state": str(unit.state),
                "x": float(unit.x), "y": float(unit.y),
                "prev_x": float(unit.prev_x), "prev_y": float(unit.prev_y),
                "target_id": str(unit.target_id),
                "state_elapsed": float(unit.state_elapsed),
                "attack_animation_offset": float(unit.attack_animation_offset),
                "attack_vfx_elapsed": float(unit.attack_vfx_elapsed),
                "trail": tuple((float(x), float(y)) for x, y in unit.trail[-8:]),
                "explosion_timer": float(unit.explosion_timer),
                "explosion_duration": float(unit.explosion_duration),
                "explosion_x": float(unit.explosion_x),
                "explosion_y": float(unit.explosion_y),
                "phase_offset": float(unit.phase_offset),
                "pending_attack": dict(self.pending_attack) if isinstance(self.pending_attack, dict) else None,
            })
        return tuple(rows[:self.DRONE_COUNT])

    def snapshot(self):
        return {
            "active": bool(self.active),
            "count": sum(1 for unit in self.units if unit.active),
            "heavy_committed": bool(self.heavy_committed),
            "instances": self.render_instances(),
        }

    def _impact_feedback(self, heavy, x, y, targets=(), world_hit=False, metadata=None, unit=None):
        melee = getattr(self.game, "melee", None)
        previous_x = float(getattr(unit, "prev_x", self.prev_x))
        previous_y = float(getattr(unit, "prev_y", self.prev_y))
        direction = (float(x) - previous_x, float(y) - previous_y)
        if melee is not None and hasattr(melee, "emit_external_impact"):
            return melee.emit_external_impact(
                self.WEAPON_ID,
                heavy=bool(heavy),
                x=float(x),
                y=float(y),
                direction=direction,
                targets=targets,
                delivery="drone_explosion" if heavy else "drone_dive",
                world_hit=bool(world_hit),
                metadata=dict(metadata or {}),
            )
        # Compatibility for hosts that inject an older MeleeSystem.  Keep the
        # fallback semantic and bounded by one event per actual drone impact.
        target_list = tuple(targets or ())
        payload = {
            "weapon": self.WEAPON_ID,
            "heavy": bool(heavy),
            "x": float(x), "y": float(y),
            "target_ids": [str(getattr(target, "entity_id", target)) for target in target_list][:8],
            "target_count": min(8, len(target_list)),
            "world_hit": bool(world_hit),
            "delivery": "drone_explosion" if heavy else "drone_dive",
        }
        payload.update(dict(metadata or {}))
        try:
            self.game.events.emit("weapon_impact", **payload)
        except Exception:
            pass
        return payload

    def _orbit_point(self, unit_or_index=0):
        p = self.game.player
        index = int(getattr(unit_or_index, "index", unit_or_index or 0))
        index = max(0, min(self.DRONE_COUNT - 1, index))
        offset = self.units[index].phase_offset
        side = 1.0 if int(getattr(p, "facing", 1)) >= 0 else -1.0
        angle = self.phase * 1.7 + offset
        cx = float(p.x) + math.cos(angle) * 32.0
        cy = float(p.y) - float(p.height()) - 22.0 + math.sin(angle) * 9.0
        if self._point_hits_world(cx, cy, 8.0):
            # Three deterministic shoulder lanes remain separated even under a
            # low ceiling; facing mirrors the formation without merging it.
            cx = float(p.x) + side * float((index - 1) * 24)
            cy = float(p.y) - float(p.height()) * 0.58 - float(index % 2) * 10.0
        return cx, cy

    @staticmethod
    def _circle_aabb(cx, cy, radius, box):
        qx=max(float(box[0]),min(float(cx),float(box[2])))
        qy=max(float(box[1]),min(float(cy),float(box[3])))
        dx=float(cx)-qx;dy=float(cy)-qy
        return dx*dx+dy*dy<=float(radius)*float(radius)

    def _point_hits_world(self, cx, cy, radius):
        world=getattr(self.game,"world",None)
        if world is None:return False
        r=max(1.0,float(radius))
        min_tx=int(math.floor((float(cx)-r)/TILE_SIZE));max_tx=int(math.floor((float(cx)+r)/TILE_SIZE))
        min_ty=int(math.floor((float(cy)-r)/TILE_SIZE));max_ty=int(math.floor((float(cy)+r)/TILE_SIZE))
        if min_tx<0 or min_ty<0 or max_tx>=world.width_tiles or max_ty>=world.height_tiles:return True
        for tx in range(min_tx,max_tx+1):
            for ty in range(min_ty,max_ty+1):
                try:
                    if not tile_def(world.get_tile(tx,ty)).solid:continue
                except Exception:continue
                if self._circle_aabb(cx,cy,r,(tx*TILE_SIZE,ty*TILE_SIZE,(tx+1)*TILE_SIZE,(ty+1)*TILE_SIZE)):
                    return True
        return False

    def _is_active_chunk(self, creature):
        streamer=getattr(self.game,"chunk_streamer",None)
        if streamer is None:return True
        size=float(TILE_SIZE*CHUNK_SIZE)
        try:return bool(streamer.is_active(int(math.floor(creature.x/size)),int(math.floor(creature.y/size))))
        except Exception:return True

    def _is_threat(self, creature):
        if not isinstance(creature,Creature) or creature.background_only or not creature.active or creature.hp<=0.0:return False
        if not bool(getattr(creature,"hostile",False)) or not self._is_active_chunk(creature):return False
        state=str(getattr(creature,"behavior_state","") or "").lower()
        action=str(getattr(creature,"ai_action_id","") or "")
        return bool(action) or state in self.THREAT_STATES or float(getattr(creature,"attack_anim_timer",0.0))>0.0

    def _nearest_threat(self):
        p=self.game.player
        limit=float(self.row.get("drone_acquire_radius",280.0))*self._profile_scale("acquire_radius_scale")
        limit2=limit*limit
        best=None;best_d2=limit2
        for entity in tuple(getattr(self.game.scene,"entities",())):
            if not self._is_threat(entity):continue
            dx=float(entity.x)-float(p.x);dy=(float(entity.y)-float(entity.height())*.5)-(float(p.y)-float(p.height())*.5)
            d2=dx*dx+dy*dy
            if d2<best_d2:
                best=entity;best_d2=d2
        return best

    def _target(self, unit=None):
        wanted=str(getattr(unit, "target_id", self.target_id) or "")
        if not wanted:return None
        for entity in tuple(getattr(self.game.scene,"entities",())):
            if isinstance(entity,Creature) and not entity.background_only and str(entity.entity_id)==wanted and entity.active and entity.hp>0.0:
                return entity
        return None

    def _deploy_group(self, emit=True):
        for unit in self.units:
            unit.active = True
            unit.state = "orbit"
            unit.target_id = ""
            unit.cooldown = 0.0
            unit.state_elapsed = 0.0
            unit.trail[:] = []
            unit.heavy_ready = False
            unit.x, unit.y = self._orbit_point(unit)
            unit.prev_x, unit.prev_y = unit.x, unit.y
        self.heavy_sync_elapsed = 0.0
        self._separate_units()
        self._sync_legacy_view()
        if emit:
            try:
                self.game.events.emit(
                    "drone_deployed",
                    count=self.DRONE_COUNT,
                    instance_ids=["flying_drone_%d" % (i + 1) for i in range(self.DRONE_COUNT)],
                )
            except Exception:
                pass

    def sync_equipment(self, force=False):
        if self.heavy_committed or (
            isinstance(self.pending_attack, dict) and bool(self.pending_attack.get("heavy", False))
        ):
            return
        owned=int(self.game.inventory.count_item(self.ITEM_ID))>0
        equipped=str(getattr(self.game.melee,"selected_weapon",""))==self.WEAPON_ID
        should=owned and equipped
        if should:
            if force or not any(unit.active for unit in self.units):
                self._deploy_group(emit=True)
        else:
            for unit in self.units:
                unit.active=False;unit.state="inactive";unit.target_id="";unit.trail[:]=[]
            self._sync_legacy_view()

    def _start_dive(self, target, heavy=False, animation_offset=0.0, units=None):
        if target is None:return False
        selected = tuple(units) if units is not None else tuple(self.units)
        started = 0
        for unit in selected:
            if not unit.active:
                continue
            unit.state="heavy_dive" if heavy else "dive"
            unit.target_id=str(target.entity_id);unit.state_elapsed=0.0;unit.last_hit_id=""
            unit.attack_animation_offset=max(0.0,float(animation_offset or 0.0))
            unit.attack_vfx_elapsed=0.0
            unit.heavy_ready=False
            started += 1
            try:
                self.game.events.emit(
                    "drone_dive_started",
                    instance_id="flying_drone_%d" % (unit.index + 1),
                    drone_index=int(unit.index), group_count=self.DRONE_COUNT,
                    target_id=unit.target_id,
                    heavy=bool(heavy),
                    dive_speed=float(self.row.get("drone_dive_speed",430.0))*self._profile_scale("dive_speed_scale"),
                    vfx_scale=self._profile_scale("heavy_vfx_scale" if heavy else "vfx_scale"),
                )
            except Exception:pass
        self._sync_legacy_view()
        return started > 0

    def _fallback_after_consumption(self):
        """Deprecated compatibility hook; inventory durability owns fallback."""
        return False

    def _latest_heavy_commit(self):
        row = getattr(getattr(self.game, "melee", None), "last_durability_result", None)
        if not isinstance(row, dict):
            return None
        if str(row.get("item_id", "")) != self.ITEM_ID:
            return None
        return dict(row)

    def command_attack(self, heavy=False, animation_offset=0.0, durability_result=None):
        selected_is_drone = str(getattr(self.game.melee,"selected_weapon",""))==self.WEAPON_ID
        commit = dict(durability_result) if isinstance(durability_result, dict) else self._latest_heavy_commit()
        if bool(heavy):
            if commit is not None and not bool(commit.get("accepted", False)):
                return False
            # The final one-use drone may already have triggered weapon fallback
            # before this authored action frame.  A matching accepted commit is
            # sufficient authority to finish the kamikaze animation.
            if not selected_is_drone and not (commit and bool(commit.get("accepted", False))):
                return False
        elif not selected_is_drone:
            return False
        allowed_units = [
            unit for unit in self.units
            if unit.active and (
                unit.state in ("orbit","return") or (bool(heavy) and unit.state=="dive")
            )
        ]
        if not allowed_units and bool(heavy):
            # Durability fallback can call sync_equipment before GameApp queues
            # the drone action. Recreate only these transient kamikaze actors.
            self._deploy_group(emit=False)
            allowed_units = list(self.units)
        if not allowed_units:return False
        target=self._nearest_threat()
        if target is None and not bool(heavy):
            self.game.events.emit("message",text="附近沒有正在攻擊角色的目標")
            return False
        if heavy:
            self.heavy_committed=True
            self.heavy_durability_result = commit
            self.heavy_sync_elapsed = 0.0
            try:self.game.events.emit(
                "drone_consumed", count=1,
                durability_committed=True,
                broken=bool((commit or {}).get("broken", True)),
                remaining=int((commit or {}).get("remaining", 0) or 0),
            )
            except Exception:pass
            if target is None:
                for unit in self.units:
                    unit.heavy_ready=True
                    unit.state="heavy_hold"
                self._explode_group()
                return True
            return self._start_dive(target,heavy=True,animation_offset=animation_offset,units=self.units)
        return self._start_dive(target,heavy=False,animation_offset=animation_offset,units=allowed_units)

    def queue_attack(self, heavy=False, durability_result=None):
        """Commit on the AssetEditor action event, not on button release."""
        selected_is_drone = str(getattr(self.game.melee,"selected_weapon",""))==self.WEAPON_ID
        commit = dict(durability_result) if isinstance(durability_result, dict) else self._latest_heavy_commit()
        if bool(heavy):
            if not selected_is_drone and not (commit and bool(commit.get("accepted", False))):
                return False
        elif not selected_is_drone:
            return False
        allowed=any(
            unit.active and (unit.state in ("orbit","return") or (bool(heavy) and unit.state=="dive"))
            for unit in self.units
        )
        if not allowed and not bool(heavy):return False
        state="heavy_attack" if bool(heavy) else "normal_attack"
        try:trigger=float(self.game.melee._authored_event_progress(state,"action",0.0))
        except Exception:trigger=0.0
        duration=max(.01,float(getattr(self.game.melee,"swing_duration",.01)))
        delay=max(0.0,min(duration,duration*max(0.0,min(1.0,trigger))))
        self.pending_attack={
            "heavy":bool(heavy),"delay":delay,"duration":duration,
            "trigger_time":delay,"durability_result":commit,
        }
        if delay<=0.0:
            row=self.pending_attack;self.pending_attack=None
            return self.command_attack(
                heavy=bool(row["heavy"]),
                animation_offset=float(row.get("trigger_time",0.0)),
                durability_result=row.get("durability_result"),
            )
        return True

    def _move_unit_toward(self, drone, tx, ty, speed, dt, collide=True):
        dx=float(tx)-drone.x;dy=float(ty)-drone.y;dist=math.hypot(dx,dy)
        if dist<=1e-5:return True,False
        step=min(dist,max(0.0,float(speed))*float(dt));ux=dx/dist;uy=dy/dist
        steps=max(1,min(18,int(math.ceil(step/4.0))));step_unit=step/steps
        collided=False
        for _ in range(steps):
            nx=drone.x+ux*step_unit;ny=drone.y+uy*step_unit
            if collide and self._point_hits_world(nx,ny,7.0):collided=True;break
            drone.x=nx;drone.y=ny
        return dist<=max(3.0,step+1.0),collided

    def _move_toward(self, tx, ty, speed, dt, collide=True):
        """Legacy unit-zero adapter retained for older diagnostics."""
        result = self._move_unit_toward(self.units[0], tx, ty, speed, dt, collide=collide)
        self._sync_legacy_view()
        return result

    def _separate_units(self):
        """Enforce a hard non-overlap invariant after orbit/flight movement."""
        live = [u for u in self.units if u.active and u.state != "explosion"]
        minimum = float(self.MIN_FORMATION_SEPARATION)
        for left_index in range(len(live)):
            for right_index in range(left_index + 1, len(live)):
                left, right = live[left_index], live[right_index]
                dx = float(right.x) - float(left.x)
                dy = float(right.y) - float(left.y)
                distance = math.hypot(dx, dy)
                if distance >= minimum:
                    continue
                if distance <= 1e-6:
                    angle = (left.index + right.index + 1) * 1.0471975512
                    ux, uy = math.cos(angle), math.sin(angle)
                else:
                    ux, uy = dx / distance, dy / distance
                correction = (minimum - distance) * 0.5 + 0.01
                left_candidate = (left.x - ux * correction, left.y - uy * correction)
                right_candidate = (right.x + ux * correction, right.y + uy * correction)
                left_clear = not self._point_hits_world(left_candidate[0], left_candidate[1], 7.0)
                right_clear = not self._point_hits_world(right_candidate[0], right_candidate[1], 7.0)
                if left_clear:
                    left.x, left.y = left_candidate
                if right_clear:
                    right.x, right.y = right_candidate
                if not left_clear and right_clear:
                    right.x += ux * correction
                    right.y += uy * correction
                elif left_clear and not right_clear:
                    left.x -= ux * correction
                    left.y -= uy * correction

    @staticmethod
    def _target_point(drone, target):
        """Distinct triangular approach points keep all three flight lanes visible."""
        angle = float(drone.phase_offset) - math.pi * 0.5
        radius_x = min(10.0, max(5.0, float(target.width()) * 0.26))
        radius_y = min(8.0, max(4.0, float(target.height()) * 0.20))
        return (
            float(target.x) + math.cos(angle) * radius_x,
            float(target.y) - float(target.height()) * 0.52 + math.sin(angle) * radius_y,
        )

    def _impact_normal(self, drone, target):
        if target is None or getattr(target,"background_only",False):return
        damage=max(1.0,float(self.row.get("damage",28.0)))
        try:target.provoke_by_player()
        except Exception:
            target.provoked_by_player=True;target.player_detected=True
        target.hp=max(0.0,float(target.hp)-damage);target.hurt_timer=max(.18,float(getattr(target,"hurt_timer",0.0)))
        direction=1.0 if float(target.x)>=drone.x else -1.0
        knockback=max(0.0,float(self.row.get("knockback",155.0)))*self._profile_scale("knockback_scale")
        target.vx=direction*knockback
        drone.last_hit_id=str(target.entity_id)
        self.game.events.emit(
            "audio_melee_hit",weapon=self.WEAPON_ID,
            sound_key=str(self.row.get("hit_sound","hammer_hit")),
            target_id=drone.last_hit_id,heavy=False,
            impact_tier="medium",sound_gain=1.06,
        )
        self.game.events.emit("message",text="無人機俯衝命中 %s  傷害 %.0f"%(target.name,damage))
        feedback=self._impact_feedback(
            False,drone.x,drone.y,targets=(target,),unit=drone,
            metadata={"damage":damage,"knockback":knockback,"drone_index":drone.index},
        )
        self.game.events.emit(
            "drone_impact",target_id=drone.last_hit_id,
            instance_id="flying_drone_%d" % (drone.index + 1),
            drone_index=int(drone.index),
            impact_serial=int((feedback or {}).get("serial",0) or 0),
        )

    def _authored_explosion_duration(self):
        duration = float(self.DEFAULT_EXPLOSION_DURATION)
        try:
            assets=getattr(self.game,"assets",None)
            binding=assets.combat_binding("weapon.flying_drone","heavy_attack") if assets is not None else None
            if isinstance(binding,dict) and str(binding.get("render_mode","runtime") or "runtime").lower()=="authored":
                info=assets.animation_info("weapon.flying_drone",str(binding.get("effect_state","heavy_effect") or "heavy_effect"))
                if isinstance(info,dict):
                    fps=max(1.0,float(info.get("fps",12.0) or 12.0));count=max(1,int(info.get("frame_count",1) or 1))
                    duration=max(duration,float(count)/fps)
        except Exception:
            pass
        return duration

    def _explosion_targets(self, drone, radius):
        victims=[]
        for entity in tuple(getattr(self.game.scene,"entities",())):
            if not isinstance(entity,Creature) or entity.background_only or not entity.active or entity.hp<=0.0:continue
            box=entity.combat_bbox() if hasattr(entity,"combat_bbox") else entity.bbox()
            if not self._circle_aabb(drone.x,drone.y,radius,box):continue
            d2=(float(entity.x)-drone.x)**2+(float(entity.y)-drone.y)**2
            victims.append((d2,entity))
        victims.sort(key=lambda row:row[0])
        return victims[:max(1,int(self.row.get("heavy_max_targets",8)))]

    def _explode_group(self):
        """Commit all three explosion states and damage in the same update."""
        radius=max(12.0,float(self.row.get("heavy_explosion_radius",84.0)))*self._profile_scale("explosion_radius_scale")
        damage=max(1.0,float(self.row.get("heavy_explosion_damage",76.0)))
        knockback=220.0*self._profile_scale("heavy_knockback_scale")
        duration=self._authored_explosion_duration()
        total_hits=0
        unique_ids=set()
        event_rows=[]
        for drone in self.units:
            if not drone.active:
                continue
            drone.explosion_x=float(drone.x);drone.explosion_y=float(drone.y)
            targets=[]
            for _distance,target in self._explosion_targets(drone,radius):
                try:
                    target.provoke_by_player()
                except Exception:
                    target.provoked_by_player=True
                    target.player_detected=True
                target.hp=max(0.0,float(target.hp)-damage)
                target.hurt_timer=max(.28,float(getattr(target,"hurt_timer",0.0)))
                target.vx=(1.0 if target.x>=drone.x else -1.0)*knockback
                targets.append(target)
                unique_ids.add(str(target.entity_id))
            total_hits += len(targets)
            drone.state="explosion";drone.state_elapsed=0.0
            drone.explosion_duration=duration;drone.explosion_timer=duration
            drone.target_id="";drone.trail[:]=[];drone.heavy_ready=True
            feedback=self._impact_feedback(
                True,drone.x,drone.y,targets=targets,world_hit=(not bool(targets)),unit=drone,
                metadata={
                    "damage":damage,"radius":radius,"knockback":knockback,
                    "consumed":True,"drone_index":drone.index,"group_count":self.DRONE_COUNT,
                },
            )
            payload={
                "instance_id":"flying_drone_%d" % (drone.index + 1),
                "drone_index":int(drone.index),"group_count":self.DRONE_COUNT,
                "x":drone.x,"y":drone.y,"hits":len(targets),
                "radius":radius,"damage":damage,
                "impact_serial":int((feedback or {}).get("serial",0) or 0),
            }
            event_rows.append(payload)
            self.game.events.emit("drone_exploded",**payload)
        if event_rows:
            self.game.events.emit(
                "audio_melee_hit",weapon=self.WEAPON_ID,
                sound_key=str(self.row.get("hit_sound","hammer_hit")),
                target_id="",heavy=True,impact_tier="heavy",sound_gain=1.20,
            )
            self.game.events.emit(
                "drone_group_exploded",count=len(event_rows),hits=total_hits,
                unique_hits=len(unique_ids),radius=radius,damage_per_drone=damage,
                synchronized=True,
            )
            self.game.events.emit(
                "message",text="3 台無人機同步爆炸：命中 %d 個目標"%len(unique_ids)
            )
        self._sync_legacy_view()
        return bool(event_rows)

    def _explode(self):
        """Legacy name now resolves the authoritative synchronized group."""
        return self._explode_group()

    def update(self, dt):
        dt=max(0.0,float(dt));self.phase+=dt
        for unit in self.units:
            unit.prev_x=float(unit.x);unit.prev_y=float(unit.y)
            unit.cooldown=max(0.0,float(unit.cooldown)-dt)
            unit.state_elapsed+=dt
            if unit.state in ("dive","heavy_dive","return"):
                unit.attack_vfx_elapsed+=dt

        if isinstance(self.pending_attack,dict):
            is_heavy=bool(self.pending_attack.get("heavy",False))
            selected=str(getattr(self.game.melee,"selected_weapon",""))==self.WEAPON_ID
            committed=isinstance(self.pending_attack.get("durability_result"),dict) and bool(
                self.pending_attack["durability_result"].get("accepted",False)
            )
            if not selected and not (is_heavy and committed):
                self.pending_attack=None
            else:
                self.pending_attack["delay"]=max(0.0,float(self.pending_attack.get("delay",0.0))-dt)
                if self.pending_attack["delay"]<=0.0:
                    row=self.pending_attack;self.pending_attack=None
                    self.command_attack(
                        heavy=bool(row.get("heavy",False)),
                        animation_offset=float(row.get("trigger_time",0.0) or 0.0),
                        durability_result=row.get("durability_result"),
                    )

        self.sync_equipment()
        live=[unit for unit in self.units if unit.active]
        if not live:
            self._sync_legacy_view()
            return

        # All explosion timers begin together and remain three independent
        # renderer packets until the longest authored flipbook completes.
        if all(unit.state=="explosion" for unit in live):
            for unit in live:
                unit.explosion_timer=max(0.0,float(unit.explosion_timer)-dt)
                if unit.explosion_timer<=0.0:
                    unit.active=False;unit.state="inactive";unit.heavy_ready=False
            if not any(unit.active for unit in self.units):
                self.heavy_committed=False
                self.heavy_durability_result=None
                self.heavy_sync_elapsed=0.0
                self._sync_legacy_view()
                # A stacked replacement may still be selected; deploy its own
                # new trio only after the previous explosion fully disappeared.
                self.sync_equipment()
            else:
                self._sync_legacy_view()
            return

        if self.heavy_committed:
            self.heavy_sync_elapsed+=dt

        for unit in live:
            orbit_x,orbit_y=self._orbit_point(unit)
            if math.hypot(unit.x-float(self.game.player.x),unit.y-float(self.game.player.y))>520.0:
                unit.x,unit.y=orbit_x,orbit_y;unit.prev_x=unit.x;unit.prev_y=unit.y
                if self.heavy_committed:
                    unit.state="heavy_hold";unit.heavy_ready=True
                else:
                    unit.state="orbit";unit.target_id=""

            if unit.state=="orbit":
                self._move_unit_toward(unit,orbit_x,orbit_y,210.0,dt,collide=False)

            elif unit.state in ("dive","heavy_dive"):
                target=self._target(unit)
                if target is None:
                    if unit.state=="heavy_dive":
                        unit.state="heavy_hold";unit.heavy_ready=True;unit.state_elapsed=0.0
                    else:
                        unit.state="return";unit.target_id="";unit.state_elapsed=0.0
                    continue
                target_x,target_y=self._target_point(unit,target)
                speed=(
                    float(self.row.get("drone_dive_speed",430.0))
                    *self._profile_scale("dive_speed_scale")
                    *(1.18 if unit.state=="heavy_dive" else 1.0)
                )
                arrived,collided=self._move_unit_toward(unit,target_x,target_y,speed,dt,collide=True)
                box=target.combat_bbox() if hasattr(target,"combat_bbox") else target.bbox()
                hit=self._circle_aabb(
                    unit.x,unit.y,
                    float(self.row.get("drone_hit_radius",12.0))*self._profile_scale("hit_radius_scale"),
                    box,
                )
                unit.trail.append((unit.x,unit.y));del unit.trail[:-8]
                if unit.state=="heavy_dive" and (hit or collided or arrived):
                    unit.state="heavy_hold";unit.heavy_ready=True;unit.state_elapsed=0.0
                elif collided:
                    unit.state="return";unit.target_id="";unit.state_elapsed=0.0;unit.cooldown=.35
                elif hit or arrived:
                    self._impact_normal(unit,target)
                    unit.state="return";unit.target_id="";unit.state_elapsed=0.0
                    unit.cooldown=max(.25,float(self.row.get("cooldown",.85)))

            elif unit.state=="return":
                arrived,_collided=self._move_unit_toward(
                    unit,orbit_x,orbit_y,float(self.row.get("drone_return_speed",350.0)),dt,collide=True
                )
                unit.trail.append((unit.x,unit.y));del unit.trail[:-8]
                if arrived or unit.state_elapsed>1.10:
                    if unit.state_elapsed>1.10:unit.x,unit.y=orbit_x,orbit_y
                    unit.state="orbit";unit.state_elapsed=0.0;unit.trail[:]=[]
                    unit.attack_animation_offset=0.0;unit.attack_vfx_elapsed=0.0

        self._separate_units()

        if self.heavy_committed:
            active_heavy=[unit for unit in self.units if unit.active]
            if active_heavy and (
                all(bool(unit.heavy_ready) for unit in active_heavy)
                or self.heavy_sync_elapsed>=float(self.HEAVY_SYNC_TIMEOUT)
            ):
                self._explode_group()
                return
        else:
            self.scan_timer=max(-1.0,float(self.scan_timer)-dt)
            ready=[unit for unit in self.units if unit.active and unit.state=="orbit" and unit.cooldown<=0.0]
            if self.pending_attack is None and ready and self.scan_timer<=0.0:
                self.scan_timer=max(.05,float(self.row.get("drone_scan_interval",.15)))
                target=self._nearest_threat()
                if target is not None:self._start_dive(target,heavy=False,animation_offset=0.0,units=ready)

        self._sync_legacy_view()
