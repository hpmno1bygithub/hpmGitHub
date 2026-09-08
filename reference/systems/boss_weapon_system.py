# -*- coding: utf-8 -*-
"""FIX88 bounded boss-weapon simulation; no UIKit, threads or wall-clock timers.

Attack clips call fire() at their authored projectile/action marker. Durability
is committed there (not on press, charge or a cancelled windup). Beams and a
maximum of eight bees outlive the final weapon copy, but are deliberately
transient on map change/load, like the existing melee projectiles.
"""
import math
from config import TILE_SIZE
from entities.creature import Creature
from systems.boss_weapon_defs import BOSS_WEAPON_DEFS
from systems.weapon_catalog import WEAPON_DEFS


def ray_box(ox, oy, ux, uy, length, box):
    """Distance to a rectangle along a finite normalized ray, or None."""
    near, far = 0.0, float(length)
    for origin, direction, low, high in (
        (ox, ux, box[0], box[2]), (oy, uy, box[1], box[3])
    ):
        if abs(direction) < 1.e-9:
            if origin < low or origin > high:
                return None
        else:
            a, b = (low-origin)/direction, (high-origin)/direction
            if a > b:
                a, b = b, a
            near, far = max(near, a), min(far, b)
            if near > far:
                return None
    return near if far >= 0.0 else None


def swept_box_hit(ox, oy, ux, uy, length, width, box):
    half = max(0.0, float(width)) * .5
    ex, ey = abs(uy)*half, abs(ux)*half
    return ray_box(ox, oy, ux, uy, length,
                   (box[0]-ex, box[1]-ey, box[2]+ex, box[3]+ey))


class BossWeaponSystem:
    MAX_BEAMS = 8                 # player beams + bounded hostile boss echoes
    MAX_IMPACTS = 12
    BEE_COUNT = 8

    def __init__(self, game):
        self.game = game
        from systems.fauna99_combat import ExpansionCombat
        self.expansion = ExpansionCombat(game)
        from systems.python102_combat import PythonBossCombat
        self.python = PythonBossCombat(game)
        from systems.fix103_combat import Fix103Combat
        self.fix103 = Fix103Combat(game)
        self.beams = []
        self.impacts = []
        self.bees = [self._empty_bee(i) for i in range(self.BEE_COUNT)]
        self.hostile_projectiles = []
        self.hostile_bees = []
        self._boss_attack_counts = {}
        self.phase = 0.0
        self.committed = False
        self.swarm_elapsed = 0.0
        self.scan_clock = 0.0
        self._candidates = ()
        self.last_fire = None
        self.last_hammer = None
        self.fire_serial = 0
        self.sync_equipment()

    @staticmethod
    def _empty_bee(index):
        return dict(index=index, active=False, state='inactive', x=0., y=0.,
                    prev_x=0., prev_y=0., age=0., cooldown=index*.12,
                    target_id='', delay=0., trail=[], heavy=False)

    def rebind(self, game):
        self.__init__(game)
        game.melee.boss_weapon_system = self

    def _player_center(self):
        p = self.game.player
        return float(p.x), float(p.y)-float(p.height())*.55

    @property
    def acquire_radius(self):
        # Read the CURRENT drone presentation envelope, rather than comparing
        # to its unboosted catalog radius (which would be less than +50%).
        drone = self.game.drone
        return (float(drone.row.get('drone_acquire_radius', 280.0)) *
                float(drone._profile_scale('acquire_radius_scale')) * 1.5)

    def _entities(self):
        # A bounded-radius scan is throttled for autonomous acquisition. Damage
        # scans use the live scene so a newly spawned target is not missed.
        return (e for e in self.game.scene.entities if isinstance(e, Creature)
                and not e.background_only and bool(e.active) and float(e.hp) > 0.0)

    def _targets(self):
        px, py = self._player_center()
        rr = self.acquire_radius ** 2
        return tuple(e for e in self._entities() if bool(e.hostile) and
                     (float(e.x)-px)**2 + (float(e.y)-e.height()*.5-py)**2 <= rr)

    def _choose(self, heavy=False, candidates=None):
        px, py = self._player_center()
        choices = candidates if candidates is not None else self._targets()
        eligible = [e for e in choices if e.active and e.hp > 0.0]
        if not eligible:
            return None
        return min(eligible, key=lambda e: (
            0 if heavy and bool(e.boss) else 1,
            (e.x-px)**2+(e.y-e.height()*.5-py)**2,
            str(e.entity_id)))

    def _notice(self, text):
        self.game.events.emit('message', text=text)

    def can_start(self, weapon, heavy, announce=False):
        row = BOSS_WEAPON_DEFS.get(weapon)
        reason = ''
        if row is None or self.game.inventory.count_item(row['item_id']) <= 0:
            reason = '目前沒有這件武器'
        elif weapon in ('magma_core_hammer','nine_tail_fan','abyss_claw','thunder_longbow','earth_drill_lance','world_tree_staff') and not self.fix103.can_start(weapon,heavy):
            reason = '此武器的重攻擊特效仍在作用中或特效佇列已滿'
        elif weapon == 'python_whip' and not self.python.can_start(heavy):
            reason = '旋風仍在作用中、前方被阻擋，或特效數量已達上限'
        elif weapon in ('kong_gauntlets','triple_staff') and not self.expansion.can_start(weapon):
            reason = '連擊／雷雲尚在作用中，或特效佇列已滿'
        elif weapon in ('pressure_cannon', 'alien_cannon') and any(
                b['weapon'] == weapon for b in self.beams):
            reason = '光柱／水柱尚在作用中'
        elif weapon == 'bee_swarm':
            if self.committed:
                reason = '工蜂正在逐一衝撞，請等待本組結束'
            elif heavy and self._choose(True) is None:
                reason = '範圍內沒有追擊目標，不消耗工蜂'
        if reason and announce:
            self._notice(reason)
        return not bool(reason)

    def fire(self, weapon, heavy=False):
        """Authoritative animation-event commit. A final heavy still finishes."""
        row = BOSS_WEAPON_DEFS.get(str(weapon))
        if row is None or not self.can_start(weapon, heavy, announce=True):
            return False
        # De-duplicate a marker delivered twice within the same attack serial.
        serial = int(self.game.melee._attack_serial)
        key = (weapon, serial)
        if self.last_fire == key:
            return False
        if heavy:
            commit = self.game._commit_weapon_heavy_use(row['item_id'], weapon)
            if not commit.get('accepted'):
                return False
            self.game.melee.last_durability_result = dict(commit)
        self.last_fire = key
        self.fire_serial += 1
        if weapon in ('magma_core_hammer','nine_tail_fan','abyss_claw','thunder_longbow','earth_drill_lance','world_tree_staff'):
            self.fix103.fire(weapon,heavy)
        elif weapon == 'python_whip':
            self.python.fire(heavy)
        elif weapon in ('kong_gauntlets','triple_staff'):
            self.expansion.fire(weapon,heavy)
        elif weapon == 'quake_hammer':
            self._hammer(heavy)
        elif weapon == 'master_sword':
            melee = self.game.melee
            melee._damage_creatures(row, melee._hitbox(row, heavy=heavy), heavy=heavy)
            if heavy:
                # Existing greatsword code owns flight, collision and lifetime.
                # A copied row changes radius ONLY so visuals are exactly 2x.
                moon = dict(WEAPON_DEFS['greatsword'])
                moon.update({k: v for k, v in row.items() if k.startswith('heavy_projectile')})
                melee._spawn_greatsword_crescent(moon)
        elif weapon == 'pressure_cannon':
            self._water(heavy)
        elif weapon == 'alien_cannon':
            self._orbital(heavy)
        elif weapon == 'bee_swarm':
            self._command_bees(heavy)
        self.game.events.emit('boss_weapon_fired', weapon=weapon, heavy=bool(heavy),
                              serial=self.fire_serial)
        return True

    def _impact(self, weapon, x, y, heavy=False, targets=(), world_hit=False):
        self.impacts.append(dict(weapon=weapon, x=float(x), y=float(y),
                                 heavy=bool(heavy), age=0., life=.42))
        del self.impacts[:-self.MAX_IMPACTS]
        self.game.melee.emit_external_impact(
            weapon, heavy=heavy, x=x, y=y, targets=targets,
            delivery='boss_'+weapon, world_hit=world_hit)

    @staticmethod
    def _hurt(target, damage, dx=0., knockback=0.):
        if getattr(target,"background_only",False) or damage <= 0.0 or not target.active or target.hp <= 0.0:
            return False
        try:
            target.provoke_by_player()
        except AttributeError:
            target.provoked_by_player = True
        target.hp = max(0., float(target.hp)-float(damage))
        target.hurt_timer = max(.12, float(target.hurt_timer))
        if knockback:
            target.vx = (1. if dx >= 0. else -1.) * knockback
        return True

    def _hammer(self, heavy):
        p, tools = self.game.player, self.game.tools
        cx, cy = self._player_center()
        ux, uy = self.game.melee._aim_direction()
        direction = 1 if p.facing >= 0 else -1
        tx, ty = int(cx//TILE_SIZE), int(cy//TILE_SIZE)
        vertical = abs(uy) > .65 and abs(uy) > abs(ux)
        if heavy:
            if vertical:
                first_y = int(p.y//TILE_SIZE) if uy > 0 else int((p.y-p.height())//TILE_SIZE)-1
                step = 1 if uy > 0 else -1
                cells = [(tx-1+x, first_y+step*y) for y in range(4) for x in range(4)]
            else:
                # Four columns forward and four rows spanning the torso/ground.
                cells = [(tx+direction*(x+1), ty-1+y) for y in range(4) for x in range(4)]
        elif vertical:
            first_y = int(p.y//TILE_SIZE) if uy > 0 else int((p.y-p.height())//TILE_SIZE)-1
            cells = [(tx, first_y+(1 if uy > 0 else -1)*i) for i in range(3)]
        else:
            cells = [(tx+direction*(i+1), ty) for i in range(3)]
        removed = []
        for bx, by in cells:
            # World boundaries remain intact, like a non-traversable map edge.
            if 0 < bx < self.game.world.width_tiles-1 and 0 < by < self.game.world.height_tiles-1:
                result = tools.blast_remove_tiles(bx, by, width=1)
                removed.extend(result.get('removed_cells', ()))
        boxes = [(x*TILE_SIZE, y*TILE_SIZE, (x+1)*TILE_SIZE, (y+1)*TILE_SIZE) for x,y in cells]
        victims = []
        row = BOSS_WEAPON_DEFS['quake_hammer']
        damage = row['damage']*(row['heavy_damage_mult'] if heavy else 1.)
        for c in self._entities():
            box = c.combat_bbox() if hasattr(c, 'combat_bbox') else c.bbox()
            if any(self.game.melee._overlap(box, b) for b in boxes):
                self._hurt(c, damage, direction, row['knockback'])
                victims.append(c)
        mx = sum(x+.5 for x,y in cells)/len(cells)*TILE_SIZE
        my = sum(y+.5 for x,y in cells)/len(cells)*TILE_SIZE
        self.last_hammer = dict(cells=tuple(cells), removed_cells=tuple(removed),
                                heavy=heavy, hits=len(victims))
        self._impact('quake_hammer', mx, my, heavy, victims, bool(removed))
        self.impacts[-1]['cells'] = tuple(cells)
        # FIX89: a heavy slam visibly blows removed tiles apart instead of
        # making the 4x4 block disappear in one silent frame. Reuse the same
        # authored heavy-effect frames at bounded cell centres.
        if heavy and removed:
            for removed_row in tuple(removed)[:8]:
                rx, ry = int(removed_row[0]), int(removed_row[1])
                self.impacts.append(dict(weapon='quake_hammer', x=(rx+.5)*TILE_SIZE,
                                         y=(ry+.5)*TILE_SIZE, heavy=True,
                                         age=0., life=.52, tile_burst=True))
            self.impacts=self.impacts[-self.MAX_IMPACTS:]
        # A fluid/solid conversion or huge slam must not leave a player embedded.
        resolver = getattr(self.game.player, 'resolve_tile_overlap', None)
        if callable(resolver):
            resolver(self.game.world)

    def _clip(self, ox, oy, ux, uy, length, width):
        """Clip a thick ray at exact visible tile rectangles (LOW/MID included)."""
        world = self.game.world
        length = max(0., float(length))
        half = width*.5
        ex, ey = abs(uy)*half, abs(ux)*half
        probe = (min(ox,ox+ux*length)-ex, min(oy,oy+uy*length)-ey,
                 max(ox,ox+ux*length)+ex, max(oy,oy+uy*length)+ey)
        # Clip at map edges, with no out-of-bounds tile probes.
        for origin, direction, upper in ((ox,ux,world.width_tiles*TILE_SIZE),
                                         (oy,uy,world.height_tiles*TILE_SIZE)):
            if direction > 1.e-9:
                length = min(length, max(0., (upper-origin)/direction))
            elif direction < -1.e-9:
                length = min(length, max(0., -origin/direction))
        for _tx, _ty, _tile, rect in world.solid_cells_in_rect(probe, padding=0):
            distance = swept_box_hit(ox,oy,ux,uy,length,width,rect)
            if distance is not None:
                length = min(length, max(0., distance-.5))
        return length

    def _new_beam(self, weapon, heavy, **values):
        row = BOSS_WEAPON_DEFS[weapon]
        duration = row['heavy_seconds' if heavy else 'normal_seconds']
        beam = dict(weapon=weapon, heavy=bool(heavy), life=duration,
                    duration=duration, age=0., dps=row['damage']*(row['heavy_damage_mult'] if heavy else 1.),
                    feedback_clock=0.)
        beam.update(values)
        self.beams.append(beam)
        if len(self.beams) > self.MAX_BEAMS:
            raise RuntimeError('boss beam budget invariant violated')
        return beam

    def _water(self, heavy):
        row = BOSS_WEAPON_DEFS['pressure_cannon']
        # FIX89: the cannon is a forward weapon, not a steerable hose. The left
        # stick remains pure movement and can move the player while the 3 s
        # heavy stream is active.
        facing = 1.0 if int(getattr(self.game.player, 'facing', 1)) >= 0 else -1.0
        if heavy:
            ux, uy = facing, 0.0
        else:
            ux, uy = self.game.melee._aim_direction()
        beam = self._new_beam('pressure_cannon', heavy, ux=ux, uy=uy,
                             width=row['heavy_width' if heavy else 'normal_width'],
                             max_length=row['range_tiles']*TILE_SIZE,
                             follow=True, ox=0., oy=0., length=0.)
        self._position_water(beam)

    def _position_water(self, beam):
        cx, cy = self._player_center()
        ux, uy = beam['ux'], beam['uy']
        # Ray begins at the character, so a muzzle never teleports through a
        # thin nearby wall. Only the visible strip begins outside the hand.
        beam['ox'], beam['oy'] = cx, cy
        beam['length'] = self._clip(cx, cy, ux, uy, beam['max_length'], beam['width'])

    def _orbital(self, heavy):
        p = self.game.player
        row = BOSS_WEAPON_DEFS['alien_cannon']
        cx, cy = self._player_center()
        facing = 1 if p.facing >= 0 else -1
        width = (2 if heavy else 1)*TILE_SIZE
        duration = row['heavy_seconds' if heavy else 'normal_seconds']
        starts = []
        if heavy:
            starts = [
                (cx-facing*row['heavy_start_tiles']*TILE_SIZE, -facing*row['heavy_travel_tiles']*TILE_SIZE),
                (cx+facing*row['heavy_start_tiles']*TILE_SIZE,  facing*row['heavy_travel_tiles']*TILE_SIZE),
            ]
        else:
            starts = [(cx+facing*row['normal_start_tiles']*TILE_SIZE,
                       facing*row['normal_travel_tiles']*TILE_SIZE)]
        for start_x, travel in starts:
            beam = self._new_beam('alien_cannon', heavy, ux=0., uy=1., width=width,
                                  max_length=0., ox=start_x, oy=cy, length=0., follow=False)
            beam.update(sweep=True, anchor_y=cy, sweep_start_x=float(start_x),
                        sweep_dx=float(travel), sweep_duration=float(duration),
                        column_height_tiles=float(row['column_height_tiles']))
            self._position_orbital_sweep(beam)

    def _position_orbital_sweep(self, beam):
        duration=max(1e-6,float(beam.get('sweep_duration',beam.get('duration',1.0))))
        t=max(0.0,min(1.0,float(beam.get('age',0.0))/duration))
        x=float(beam.get('sweep_start_x',beam.get('ox',0.0)))+float(beam.get('sweep_dx',0.0))*t
        world_w=self.game.world.width_tiles*TILE_SIZE
        half=float(beam['width'])*.5
        x=max(half+1.,min(world_w-half-1.,x))
        cy=float(beam.get('anchor_y',self._player_center()[1]))
        up=self._clip(x,cy,0.,-1.,float(beam.get('column_height_tiles',8.0))*TILE_SIZE,float(beam['width']))
        top=max(1.,cy-up+1.)
        floor_target=min(self.game.world.height_tiles*TILE_SIZE-1., cy+2*TILE_SIZE)
        length=self._clip(x,top,0.,1.,max(0.,floor_target-top),float(beam['width']))
        beam['ox'],beam['oy'],beam['length'],beam['max_length']=x,top,length,length

    def _update_beams(self, dt):
        if not self.beams:
            return
        live = tuple(self._entities())
        keep = []
        for b in self.beams:
            step = min(dt, max(0., b['life']))
            # Advance animation/sweep time before sampling this fixed step so
            # moving columns visibly move on the first simulation tick.
            b['age'] += step
            if b.get('hostile'):
                self._position_hostile_beam(b)
            elif b.get('sweep'):
                self._position_orbital_sweep(b)
            elif b['follow']:
                self._position_water(b)
            else:
                b['length'] = self._clip(b['ox'],b['oy'],b['ux'],b['uy'],b['max_length'],b['width'])
            hits = []
            if b['length'] > 0.:
                if b.get('hostile'):
                    pb=self.game.player.combat_bbox() if hasattr(self.game.player,'combat_bbox') else self.game.player.bbox()
                    distance=swept_box_hit(b['ox'],b['oy'],b['ux'],b['uy'],b['length'],b['width'],pb)
                    if distance is not None and self._hurt_player_from_boss(b,b['dps']*step):
                        hits.append(self.game.player)
                else:
                    for c in live:
                        box = c.combat_bbox() if hasattr(c, 'combat_bbox') else c.bbox()
                        distance = swept_box_hit(b['ox'],b['oy'],b['ux'],b['uy'],b['length'],b['width'],box)
                        if distance is not None and self._hurt(c,b['dps']*step):
                            hits.append(c)
            b['feedback_clock'] -= step
            if hits and b['feedback_clock'] <= 0.:
                # Feedback is throttled; DPS is integrated every fixed step,
                # independent of device FPS and normal hurt flashing.
                c = hits[0]
                if b.get('hostile'):
                    self._impact(b['weapon'],float(c.x),float(c.y)-float(c.height())*.5,b['heavy'],(),False)
                else:
                    self.game.melee.emit_external_impact(
                        b['weapon'],heavy=b['heavy'],x=c.x,y=c.y-c.height()*.5,
                        targets=tuple(hits[:8]),delivery='continuous_beam',magnitude_scale=.30)
                b['feedback_clock'] = .25
            b['life'] = max(0., b['life']-step)
            if b['life'] > 1.e-8:
                keep.append(b)
        self.beams = keep

    def _hurt_player_from_boss(self, source, damage):
        cs=getattr(self.game,'creature_system',None)
        if cs is None or damage<=0.0:
            return False
        try:
            if not cs._player_can_take_damage():
                return False
            resolved=cs._resolve_player_damage(float(source.get('ox',self.game.player.x)),
                                               float(source.get('oy',self.game.player.y)),
                                               float(damage),'boss_weapon_echo')
            blocked=bool(resolved.get('blocked',False))
            if not blocked:
                self.game.player.hurt_timer=max(.16,float(getattr(self.game.player,'hurt_timer',0.0)))
            return not blocked
        except Exception:
            return False

    def _boss_by_id(self, entity_id):
        key=str(entity_id or '')
        for e in self.game.scene.entities:
            if isinstance(e,Creature) and str(getattr(e,'entity_id',''))==key and e.active and e.hp>0:
                return e
        return None

    def _position_hostile_beam(self, beam):
        owner=self._boss_by_id(beam.get('owner_id'))
        if beam.get('sweep'):
            duration=max(1e-6,float(beam.get('sweep_duration',beam.get('duration',1.0))))
            t=max(0.0,min(1.0,float(beam.get('age',0.0))/duration))
            x=float(beam['sweep_start_x'])+float(beam['sweep_dx'])*t
            cy=float(beam.get('anchor_y',beam.get('source_y',0.0)))
            if owner is not None and beam.get('follow_owner_y'):
                cy=float(owner.y)-float(owner.height())*.55
            half=float(beam['width'])*.5
            x=max(half+1.,min(self.game.world.width_tiles*TILE_SIZE-half-1.,x))
            up=self._clip(x,cy,0.,-1.,float(beam.get('column_height_tiles',8.0))*TILE_SIZE,float(beam['width']))
            top=max(1.,cy-up+1.)
            floor_target=min(self.game.world.height_tiles*TILE_SIZE-1.,cy+2*TILE_SIZE)
            length=self._clip(x,top,0.,1.,max(0.,floor_target-top),float(beam['width']))
            beam.update(ox=x,oy=top,ux=0.,uy=1.,length=length,max_length=length)
            return
        if owner is None:
            beam['life']=0.;beam['length']=0.;return
        ox=float(owner.x);oy=float(owner.y)-float(owner.height())*.55
        beam['ox'],beam['oy']=ox,oy
        beam['length']=self._clip(ox,oy,float(beam['ux']),float(beam['uy']),beam['max_length'],beam['width'])

    def fire_boss_echo(self, creature, action):
        """Mirror the corresponding reward weapon on its source boss.
        Returns True only for authored actions intentionally replaced by FIX89.
        """
        species=str(getattr(creature,'species',''))
        action_id=str(getattr(creature,'ai_action_id','') or action.get('animation_state',''))
        owner_id=str(getattr(creature,'entity_id',''))
        if species=='abyss_colossus' and action_id=='slam':
            self._boss_hammer_echo(creature,action);return True
        if species=='loch_ness_monster':
            count=self._boss_attack_counts.get(owner_id,0)+1;self._boss_attack_counts[owner_id]=count
            self._boss_water_echo(creature,action,heavy=(count%3==0));return True
        if species=='queen_bee':
            self._boss_swarm_echo(creature,action);return True
        if species=='wuxia_sword_tomb_master' and action_id=='flying_sword':
            self._boss_crescent_echo(creature,action);return True
        if species=='biolume_heartwarden' and action_id=='heart_slam':
            self._boss_alien_echo(creature,action,heavy=False);return True
        if species=='biolume_heartwarden' and action_id=='pulse_cast':
            self._boss_alien_echo(creature,action,heavy=True);return True
        return False

    def _boss_hammer_echo(self,c,action):
        facing=1 if int(getattr(c,'facing',1))>=0 else -1
        tx=int(float(c.x)//TILE_SIZE);ty=int(float(c.y)//TILE_SIZE)
        cells=[(tx+facing*(x+1),ty-1+y) for y in range(4) for x in range(4)]
        pb=self.game.player.combat_bbox() if hasattr(self.game.player,'combat_bbox') else self.game.player.bbox()
        hit=any(self.game.melee._overlap(pb,(x*TILE_SIZE,y*TILE_SIZE,(x+1)*TILE_SIZE,(y+1)*TILE_SIZE)) for x,y in cells)
        if hit:self._hurt_player_from_boss({'ox':c.x,'oy':c.y},float(action.get('damage',42.0)))
        for x,y in cells[::2]:
            self._impact('quake_hammer',(x+.5)*TILE_SIZE,(y+.5)*TILE_SIZE,True,(),False)

    def _boss_water_echo(self,c,action,heavy=False):
        row=BOSS_WEAPON_DEFS['pressure_cannon'];facing=1. if float(self.game.player.x)>=float(c.x) else -1.
        duration=row['heavy_seconds' if heavy else 'normal_seconds']
        beam=self._new_beam('pressure_cannon',heavy,ux=facing,uy=0.,
                            width=row['heavy_width' if heavy else 'normal_width'],
                            max_length=row['range_tiles']*TILE_SIZE,follow=False,ox=c.x,oy=c.y,length=0.)
        beam.update(hostile=True,owner_id=str(c.entity_id),dps=max(8.,float(action.get('damage',34.0))/max(.5,duration)))
        self._position_hostile_beam(beam)

    def _boss_crescent_echo(self,c,action):
        facing=1. if float(self.game.player.x)>=float(c.x) else -1.
        row=BOSS_WEAPON_DEFS['master_sword']
        self.hostile_projectiles.append(dict(weapon='master_sword',heavy=True,owner_id=str(c.entity_id),
            x=float(c.x)+facing*float(c.width())*.55,y=float(c.y)-float(c.height())*.55,
            vx=facing*float(row['heavy_projectile_speed']),vy=0.,radius=float(row['heavy_projectile_radius']),
            damage=float(action.get('damage',31.0)),knockback=float(action.get('knockback',260.0)),
            life=1.25,age=0.,facing=1 if facing>0 else -1))
        del self.hostile_projectiles[:-12]

    def _boss_alien_echo(self,c,action,heavy=True):
        row=BOSS_WEAPON_DEFS['alien_cannon'];cx=float(c.x);cy=float(c.y)-float(c.height())*.5
        facing=1 if float(self.game.player.x)>=cx else -1;width=(2 if heavy else 1)*TILE_SIZE
        duration=row['heavy_seconds' if heavy else 'normal_seconds']
        if heavy:
            starts=[(cx-facing*TILE_SIZE,-facing*row['heavy_travel_tiles']*TILE_SIZE),
                    (cx+facing*TILE_SIZE, facing*row['heavy_travel_tiles']*TILE_SIZE)]
        else:
            starts=[(cx+facing*TILE_SIZE,facing*row['normal_travel_tiles']*TILE_SIZE)]
        for sx,travel in starts:
            b=self._new_beam('alien_cannon',heavy,ux=0.,uy=1.,width=width,max_length=0.,follow=False,ox=sx,oy=cy,length=0.)
            b.update(hostile=True,owner_id=str(c.entity_id),sweep=True,sweep_start_x=sx,sweep_dx=travel,
                     sweep_duration=duration,column_height_tiles=row['column_height_tiles'],anchor_y=cy,
                     source_y=cy,dps=max(9.,float(action.get('damage',31.0))/max(.75,duration)))
            self._position_hostile_beam(b)

    def _boss_swarm_echo(self,c,action):
        # Eight staggered homing bees reuse the player's swarm art, but damage only the player.
        base=float(c.y)-float(c.height())*.45
        for i in range(8):
            self.hostile_bees.append(dict(weapon='bee_swarm',heavy=True,owner_id=str(c.entity_id),
                x=float(c.x)+(i-3.5)*5.,y=base+((i%2)*8.-4.),prev_x=float(c.x),prev_y=base,
                delay=i*.18,age=0.,life=3.6,damage=max(5.,float(action.get('damage',34.0))*.34),active=True))
        self.hostile_bees=self.hostile_bees[-16:]

    def _update_hostile_projectiles(self,dt):
        keep=[];pb=self.game.player.combat_bbox() if hasattr(self.game.player,'combat_bbox') else self.game.player.bbox()
        for p in self.hostile_projectiles:
            p['age']+=dt;p['life']-=dt
            if p['life']<=0:continue
            ox,oy=p['x'],p['y'];nx=ox+p['vx']*dt;ny=oy+p['vy']*dt
            length=math.hypot(nx-ox,ny-oy);blocked=False
            if length>0:
                allowed=self._clip(ox,oy,(nx-ox)/length,(ny-oy)/length,length,p['radius']*2.)
                if allowed+.01<length:
                    nx=ox+(nx-ox)/length*allowed;ny=oy+(ny-oy)/length*allowed;blocked=True
            p['x'],p['y']=nx,ny
            box=(nx-p['radius'],ny-p['radius'],nx+p['radius'],ny+p['radius'])
            if self.game.melee._overlap(box,pb):
                self._hurt_player_from_boss({'ox':ox,'oy':oy},p['damage']);self._impact('master_sword',nx,ny,True,(),False);continue
            if blocked:self._impact('master_sword',nx,ny,True,(),True);continue
            keep.append(p)
        self.hostile_projectiles=keep[-12:]

    def _update_hostile_bees(self,dt):
        keep=[];p=self.game.player;pb=p.combat_bbox() if hasattr(p,'combat_bbox') else p.bbox()
        for b in self.hostile_bees:
            b['age']+=dt;b['life']-=dt
            if b['life']<=0:continue
            b['prev_x'],b['prev_y']=b['x'],b['y']
            if b['age']<b['delay']:
                keep.append(b);continue
            tx=float(p.x);ty=float(p.y)-float(p.height())*.5
            dx,dy=tx-b['x'],ty-b['y'];mag=max(.001,math.hypot(dx,dy));step=min(mag,420.*dt)
            allowed=self._clip(b['x'],b['y'],dx/mag,dy/mag,step,8.)
            b['x']+=dx/mag*allowed;b['y']+=dy/mag*allowed
            box=(b['x']-5,b['y']-5,b['x']+5,b['y']+5)
            if self.game.melee._overlap(box,pb):
                self._hurt_player_from_boss({'ox':b['x'],'oy':b['y']},b['damage']);self._impact('bee_swarm',b['x'],b['y'],True,(),False);continue
            if allowed+.01<step:
                self._impact('bee_swarm',b['x'],b['y'],True,(),True);continue
            keep.append(b)
        self.hostile_bees=keep[-16:]

    def _orbit(self, index):
        cx, cy = self._player_center()
        angle = self.phase*1.8+index*math.tau/self.BEE_COUNT
        return cx+math.cos(angle)*(39.+(index%2)*9.), cy-24.+math.sin(angle)*19.

    def _deploy(self):
        for b in self.bees:
            fresh = self._empty_bee(b['index'])
            b.clear(); b.update(fresh)
            b['active'], b['state'] = True, 'orbit'
            b['x'], b['y'] = self._orbit(b['index'])
            b['prev_x'], b['prev_y'] = b['x'], b['y']

    def sync_equipment(self):
        if self.committed:
            return
        equipped = str(self.game.melee.selected_weapon) == 'bee_swarm'
        owned = self.game.inventory.count_item('weapon_bee_swarm') > 0
        if equipped and owned:
            if not any(b['active'] for b in self.bees):
                self._deploy()
        else:
            for b in self.bees:
                b['active'] = False
                b['state'] = 'inactive'

    def _command_bees(self, heavy):
        if not any(b['active'] for b in self.bees):
            self._deploy()
        target = self._choose(heavy)
        if heavy:
            self.committed = True
            self.swarm_elapsed = 0.
        for b in self.bees:
            if not b['active']:
                continue
            if heavy:
                b.update(state='waiting', heavy=True,
                         delay=b['index']*BOSS_WEAPON_DEFS['bee_swarm']['heavy_launch_interval'],
                         age=0., target_id=str(target.entity_id) if target else '')
            elif target is not None:
                b.update(state='dive', age=0., target_id=str(target.entity_id))

    def _move_bee(self, b, tx, ty, speed, dt, collide):
        dx, dy = tx-b['x'], ty-b['y']
        distance = math.hypot(dx,dy)
        if distance < .01:
            return True, False
        ux, uy = dx/distance, dy/distance
        step = min(distance, speed*dt)
        if collide:
            allowed = self._clip(b['x'],b['y'],ux,uy,step,6.)
        else:
            allowed = step
        b['x'] += ux*allowed; b['y'] += uy*allowed
        return distance <= step+.01, allowed+.01 < step

    def _finish_bee(self, b, target=None, world_hit=False):
        row = BOSS_WEAPON_DEFS['bee_swarm']
        heavy = b['heavy']
        if target is not None:
            self._hurt(target,row['damage']*(row['heavy_damage_mult'] if heavy else 1.),
                       target.x-b['x'],row['knockback'])
        self._impact('bee_swarm',b['x'],b['y'],heavy,
                     (target,) if target is not None else (),world_hit)
        self.game.events.emit('bee_impact',index=b['index'],heavy=heavy,
                              target_id=str(target.entity_id) if target else '')
        if heavy:
            b.update(active=False,state='inactive',target_id='')
        else:
            b.update(state='return',cooldown=.85,age=0.,target_id='')

    def _update_bees(self, dt):
        self.sync_equipment()
        if not any(b['active'] for b in self.bees):
            return
        self.phase += dt
        self.scan_clock -= dt
        if self.scan_clock <= 0.:
            self._candidates = self._targets()
            self.scan_clock = .18
        candidates = self._candidates
        by_id = {str(c.entity_id):c for c in candidates if c.active and c.hp > 0.}
        self.swarm_elapsed += dt if self.committed else 0.
        for b in self.bees:
            if not b['active']:
                continue
            b['prev_x'],b['prev_y'] = b['x'],b['y']
            b['age'] += dt
            b['cooldown'] = max(0.,b['cooldown']-dt)
            state = b['state']
            if state == 'waiting':
                self._move_bee(b,*self._orbit(b['index']),190.,dt,False)
                if self.swarm_elapsed+1.e-8 >= b['delay']:
                    target = self._choose(True,candidates)
                    b.update(state='heavy_dive',age=0.,target_id=str(target.entity_id) if target else '')
                    self.game.events.emit('bee_launch',index=b['index'],heavy=True,
                                          target_id=b['target_id'],at=self.swarm_elapsed)
                continue
            if state in ('dive','heavy_dive'):
                target = by_id.get(b['target_id'])
                if target is None:
                    target = self._choose(b['heavy'],candidates)
                    b['target_id'] = str(target.entity_id) if target else ''
                if target is None:
                    if b['heavy'] and b['age'] > .55:
                        self._finish_bee(b)
                    elif not b['heavy']:
                        b.update(state='return',age=0.,cooldown=.8)
                    continue
                tx,ty = float(target.x),float(target.y)-float(target.height())*.5
                arrived,wall = self._move_bee(b,tx,ty,570. if b['heavy'] else 330.,dt,True)
                box = target.combat_bbox() if hasattr(target,'combat_bbox') else target.bbox()
                dx,dy = b['x']-b['prev_x'],b['y']-b['prev_y']
                length = math.hypot(dx,dy)
                hit = self.game.melee._circle_aabb(b['x'],b['y'],5.,box)
                if length > 0.:
                    hit = hit or swept_box_hit(b['prev_x'],b['prev_y'],dx/length,dy/length,length,10.,box) is not None
                # Walls win over a target behind the wall; clipping above limits
                # the swept test to only the actually travelled segment.
                if hit:
                    self._finish_bee(b,target)
                elif wall or b['age'] > 2.0:
                    if b['heavy']:
                        self._finish_bee(b,world_hit=wall)
                    else:
                        b.update(state='return',cooldown=1.,age=0.)
                b['trail'].append((b['x'],b['y']))
                del b['trail'][:-5]
            else:
                arrived,_ = self._move_bee(b,*self._orbit(b['index']),230.,dt,False)
                if arrived or b['age'] > 1.5:
                    b['state'] = 'orbit'
                if not self.committed and b['cooldown'] <= 0. and b['state'] == 'orbit':
                    target = self._choose(False,candidates)
                    if target:
                        b.update(state='dive',age=0.,target_id=str(target.entity_id),heavy=False)
        if self.committed and (not any(b['active'] for b in self.bees) or self.swarm_elapsed > 5.):
            for b in self.bees:
                if b['active']:
                    self._finish_bee(b)
            self.committed = False
            self._candidates = ()
            self.scan_clock = 0.
            # Spare bundles may deploy on the next tick, never replenish this
            # already-authorized eight-bee kamikaze sequence.

    def update(self, dt):
        dt = max(0., min(.25,float(dt)))
        if dt <= 0.:
            return
        self.expansion.update(dt)
        self.python.update(dt)
        self.fix103.update(dt)
        self._update_beams(dt)
        self._update_bees(dt)
        self._update_hostile_projectiles(dt)
        self._update_hostile_bees(dt)
        keep = []
        for f in self.impacts:
            f['age'] += dt
            if f['age'] < f['life']:
                keep.append(f)
        self.impacts = keep[-self.MAX_IMPACTS:]
