# -*- coding: utf-8 -*-
"""FIX83 stack-backed, persisted flight durability. No UI/wall-clock timers."""
import math
from config import TILE_SIZE

ITEM_WIND_WINGS = 'equipment.wind_god_wings'
WING_SECONDS = 300.0
FLIGHT_SPEED = 320.0
FLIGHT_ACCEL = 480.0
FLIGHT_BRAKE = 640.0
FLIGHT_TURN = 780.0

class WindWingsMixin:
    def _init_wings(self):
        self._wing_remaining = []

    def _sync_wing_copies(self):
        count = self._owned_count(ITEM_WIND_WINGS)
        with self._lock:
            pool = self._wing_remaining
            if len(pool) > count:
                del pool[count:]
            elif len(pool) < count:
                pool.extend([WING_SECONDS] * (count-len(pool)))

    def wing_state(self):
        self._sync_wing_copies()
        with self._lock:
            return {'remaining_seconds': self._wing_remaining[0] if self._wing_remaining else 0.0,
                    'copies_seconds': list(self._wing_remaining),
                    'maximum_seconds': WING_SECONDS,
                    'equipped': self._slots.get('body') == ITEM_WIND_WINGS}

    def _import_wings(self, data):
        if not isinstance(data, dict): data = {}
        data = data.get('equipment', data)
        for key in ('gear', 'wearables'):
            if isinstance(data.get(key), dict): data = data[key]; break
        row = data.get('wind_wings', {})
        raw = row.get('copies_seconds', []) if isinstance(row, dict) else []
        pool = []
        for n in raw[:99] if isinstance(raw, (list,tuple)) else ():
            try:
                f = float(n)
                if not math.isfinite(f): f = WING_SECONDS
                pool.append(max(0.0, min(WING_SECONDS, f)))
            except (TypeError, ValueError): pool.append(WING_SECONDS)
        self._wing_remaining = pool
        self._sync_wing_copies()

    def _wear_wings(self, seconds, player):
        """Consume exactly one copy. Extra drops stay in the backpack, unworn."""
        self._sync_wing_copies()
        if not self._wing_remaining: return False
        with self._lock:
            self._wing_remaining[0] = max(0.0, self._wing_remaining[0]-max(0.0,float(seconds)))
            left = self._wing_remaining[0]
            player.wing_time_remaining = left
            broke = left <= 1e-7
            if broke:
                self._wing_remaining.pop(0)
                self._slots['body'] = ''
                self._revision += 1
        if broke:
            self.inventory.consume_item(ITEM_WIND_WINGS, 1)
            self.sync_player(player)
            player.wing_flying = False
            player.wing_time_remaining = 0.0
            self._emit('equipment_destroyed', item_id=ITEM_WIND_WINGS, slot='body', consumed_count=1)
            self._message('風神翅膀累積飛翔已滿 5 分鐘，這一件已消失。')
            return False
        return True

    def update_wing_flight(self, player, dt, physics, inputs, world):
        """Eight-direction analog flight in any map; solid terrain still collides.

        Airborne hovering consumes time, floor-supported idling does not. Pause
        never reaches this hook. A dropped/stored/unequipped copy does not age.
        """
        player.wing_flying = False
        if self.equipped_item('body') != ITEM_WIND_WINGS or player.hp <= 0:
            return False
        self._sync_wing_copies()
        if not self._wing_remaining:
            return False
        dt = max(0.0, min(0.1, float(dt)))
        ax, ay = float(inputs.axis_x()), float(inputs.axis_y())
        mag = math.hypot(ax,ay)
        if mag > 1.0: ax /= mag; ay /= mag
        # Preserve compact hitboxes in low ceilings; don't stand inside rock.
        if player._can_use_state('stand', world): player.state = 'stand'
        player.climb = None
        player.hover_active = False
        player.air_dash_active = False
        player.jump_preparing = False
        player.jump_buffer_remaining = 0.0
        player.roll_timer = 0.0  # An interrupted air roll must not grant perpetual invulnerability.
        tx,ty = ax*FLIGHT_SPEED, ay*FLIGHT_SPEED
        vx,vy = float(player.vx),float(player.vy)
        diffx,diffy = tx-vx,ty-vy
        diff = math.hypot(diffx,diffy)
        reverse = vx*tx+vy*ty < 0.0
        rate = FLIGHT_TURN if reverse else (FLIGHT_BRAKE if math.hypot(tx,ty)<math.hypot(vx,vy) else FLIGHT_ACCEL)
        scale = min(1.0,rate*dt/diff) if diff>1e-8 else 1.0
        player.vx,player.vy = vx+diffx*scale,vy+diffy*scale
        if physics.move_horizontal(player, player.vx*dt): player.vx=0.0
        hit,grounded=physics.move_vertical(player, player.vy*dt)
        if hit: player.vy=0.0
        player.grounded = bool(grounded or (player.vy>=0.0 and physics.grounded(player)))
        minimum_y=player.height()+1.0; maximum_y=world.height_tiles*TILE_SIZE-1.0
        if player.y<minimum_y: player.y=minimum_y;player.vy=max(0.0,player.vy)
        if player.y>maximum_y: player.y=maximum_y;player.vy=min(0.0,player.vy)
        # Same cylinder seam as normal Player.update, including camera signal.
        max_x=world.width_tiles*TILE_SIZE; margin=12.0; wrapped=0
        if getattr(world,'horizontal_wrap_enabled',False):
            if player.x<=margin and player.vx<0: player.x=max_x-margin-2;wrapped=-1
            elif player.x>=max_x-margin and player.vx>0: player.x=margin+2;wrapped=1
        player.x=max(margin,min(max_x-margin,player.x))
        if wrapped:
            player.prev_x=player.x;player.world_wrap_direction=wrapped
            player.world_wrap_serial=int(getattr(player,'world_wrap_serial',0))+1
        if abs(ax)>.01: player.facing=1 if ax>0 else -1
        player.wing_flying=not player.grounded
        player.wing_time_remaining=self._wing_remaining[0]
        if player.grounded:
            player._reset_landed_equipment_motion()
        else:
            player.state='jump' if player.vy<0 else 'fall'
            self._wear_wings(dt,player)
        # Keep animation-clock input monotone without adding scene entities.
        player.wing_animation_time=float(getattr(player,'wing_animation_time',0.0))+dt
        return True
