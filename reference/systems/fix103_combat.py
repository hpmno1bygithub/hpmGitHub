# -*- coding: utf-8 -*-
"""FIX116 native boss/weapon combat choreography.

FIX103 introduced the six boss reward weapons.  FIX116 keeps the same bounded,
simulation-dt-only architecture, but removes the old shared ``front box every
0.28s`` behaviour.  Each weapon now has its own normal-hit silhouette and a
fixed heavy pulse timeline that follows the authored 96/128px VFX.
"""
from config import TILE_SIZE as T
from engine.math2d import rects_overlap
from systems.fix103_defs import WEAPONS, REWARDS
from systems.creature_attributes import elemental_multiplier


class Fix103Combat:
    MAX = 18
    WEAPON_ELEMENTS = {
        'magma_core_hammer':'fire',
        'nine_tail_fan':'ice',
        'abyss_claw':'water',
        'thunder_longbow':'electric',
        'earth_drill_lance':'earth',
        'world_tree_staff':'earth',
    }
    HEAVY_LIFE = {
        'magma_core_hammer': 1.50,
        'nine_tail_fan': 3.00,
        'abyss_claw': 1.40,
        'thunder_longbow': 3.00,
        'earth_drill_lance': 1.40,
        'world_tree_staff': 3.00,
    }
    # Discrete authored beats.  A frame hitch can cross several beats; update()
    # consumes all missed beats exactly once, so gameplay stays deterministic.
    HEAVY_BEATS = {
        'magma_core_hammer': (.20, .48, .78, 1.12),
        'nine_tail_fan': (.26, .58, .90, 1.22, 1.54, 1.86, 2.18, 2.52),
        'abyss_claw': (.28, .54, .78, 1.05),
        'thunder_longbow': (.30, .72, 1.16, 1.60, 2.04, 2.48),
        'earth_drill_lance': (.18, .38, .60, .82, 1.06, 1.28),
        'world_tree_staff': (.28, .62, .96, 1.30, 1.64, 1.98, 2.32, 2.66),
    }

    HEAVY_EFFECT_ASSETS = {
        'magma_core_hammer':'effect.magma_core_hammer_heavy',
        'nine_tail_fan':'effect.nine_tail_fan_heavy',
        'abyss_claw':'effect.abyss_claw_heavy',
        'thunder_longbow':'effect.thunder_longbow_heavy',
        'earth_drill_lance':'effect.earth_drill_lance_heavy',
        'world_tree_staff':'effect.world_tree_staff_heavy',
    }

    def _heavy_beats(self, weapon):
        """Read editable FX damage markers before falling back to FIX116 beats.

        A heavy effect frame marked with ``frame_events[index].damage`` in the
        AssetEditor becomes a gameplay pulse at index/fps.  This keeps the
        authored visual impact and the authoritative hit test on the same beat.
        """
        fallback=tuple(self.HEAVY_BEATS.get(str(weapon),()))
        asset=self.HEAVY_EFFECT_ASSETS.get(str(weapon))
        assets=getattr(self.game,'assets',None)
        if not asset or assets is None:return fallback
        try:info=assets.animation_info(asset,'effect')
        except Exception:return fallback
        if not isinstance(info,dict):return fallback
        frames=tuple(info.get('damage_frames',()) or ())
        if not frames:return fallback
        try:fps=max(1.0,float(info.get('fps',8.0) or 8.0))
        except Exception:fps=8.0
        life=float(self.HEAVY_LIFE.get(str(weapon),3.0))
        beats=[]
        for frame in frames:
            try:t=max(0.0,min(life-1e-4,float(int(frame))/fps))
            except Exception:continue
            if not beats or abs(t-beats[-1])>1e-5:beats.append(t)
        return tuple(beats) if beats else fallback

    def __init__(self, game):
        self.game = game
        self.effects = []
        self.flashes = []
        self.deaths = []

    def can_start(self, weapon, heavy):
        return len(self.effects) < self.MAX and not (
            heavy and any(e.get('owner') is self.game.player and e.get('weapon') == weapon for e in self.effects)
        )

    def _targets(self, owner):
        return list(self.game.boss_weapons._entities()) if owner is self.game.player else [self.game.player]

    def _hurt(self, owner, target, damage, dx=1, element=''):
        if owner is self.game.player:
            if element:
                damage=float(damage)*float(elemental_multiplier(element,getattr(target,'element_type','earth')))
            return self.game.boss_weapons._hurt(target, damage, dx, 80.)
        cs = self.game.creature_system
        if not cs._can_attack_player(owner) or not cs._player_can_take_damage():
            return False
        r = cs._resolve_player_damage(owner.x, owner.y - owner.height() * .5, damage, 'fix103_boss')
        if not r.get('blocked'):
            self.game.player.vx = (1 if dx >= 0 else -1) * 110.
        return not r.get('blocked')

    @staticmethod
    def _front_span(owner, start, end, top, bottom=0.0):
        """World box expressed in tiles from the owner's front/baseline."""
        f = 1 if owner.facing >= 0 else -1
        front = owner.x + f * owner.width() * .5
        x1 = front + f * start * T
        x2 = front + f * end * T
        return (min(x1, x2), owner.y - top * T, max(x1, x2), owner.y - bottom * T), f

    def _normal_boxes(self, owner, weapon):
        """Distinct silhouettes for the six normal attacks.

        A target is de-duplicated across boxes, so multi-part silhouettes do not
        accidentally multiply damage.  The list itself is also used to compute
        a single authored-VFX placement envelope.
        """
        f = 1 if owner.facing >= 0 else -1
        if weapon == 'magma_core_hammer':
            # Heavy head + short ground splash.
            return [self._front_span(owner, 0.00, 2.20, 1.90)[0],
                    self._front_span(owner, .55, 2.65, .62)[0]], f
        if weapon == 'nine_tail_fan':
            # Three separated ice blades in a shallow fan.
            return [self._front_span(owner, .30, 4.30, 2.65, 2.05)[0],
                    self._front_span(owner, .20, 4.60, 1.82, 1.18)[0],
                    self._front_span(owner, .25, 4.20, 1.02, .38)[0]], f
        if weapon == 'abyss_claw':
            # Upper/lower pincers converge around the target's centre.
            return [self._front_span(owner, .15, 2.75, 2.35, 1.32)[0],
                    self._front_span(owner, .15, 2.75, 1.18, .18)[0]], f
        if weapon == 'thunder_longbow':
            # Long, narrow piercing lane.
            return [self._front_span(owner, .20, 7.00, 1.62, .82)[0]], f
        if weapon == 'earth_drill_lance':
            # Concentrated drill line plus a small tip impact.
            return [self._front_span(owner, .15, 4.35, 1.48, .62)[0],
                    self._front_span(owner, 3.80, 4.75, 1.72, .36)[0]], f
        # World-tree seed travels in the middle, with a low thorn wake.
        return [self._front_span(owner, .20, 4.40, 1.62, .88)[0],
                self._front_span(owner, 1.20, 4.80, .78, .20)[0]], f

    @staticmethod
    def _envelope(boxes):
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _normal(self, owner, weapon, damage, maxhits=24, visual=True):
        boxes, f = self._normal_boxes(owner, weapon)
        hit_ids = set()
        hits = 0
        for target in self._targets(owner):
            key = id(target)
            bbox = target.combat_bbox() if hasattr(target, 'combat_bbox') else target.bbox()
            if key not in hit_ids and any(rects_overlap(box, bbox) for box in boxes):
                hit_ids.add(key)
                hits += int(self._hurt(owner, target, damage, f, self.WEAPON_ELEMENTS.get(weapon,'')))
            if hits >= maxhits:
                break
        if visual:
            self.flashes.append(dict(
                box=self._envelope(boxes), age=0., life=.82,
                weapon=weapon, heavy=False, facing=f, owner=owner,
            ))
            self.flashes = self.flashes[-8:]
        return True

    def fire(self, weapon, heavy=False):
        p = self.game.player
        row = WEAPONS[weapon]
        if not heavy:
            return self._normal(p, weapon, row['damage'], 2 if weapon == 'thunder_longbow' else 24)
        f = 1 if p.facing >= 0 else -1
        self.effects.append(dict(
            owner=p, weapon=weapon, age=0., life=self.HEAVY_LIFE[weapon],
            facing=f, sx=p.x, base=p.y, pulse=0, damage=row['damage'] * .72,
        ))
        self.effects = self.effects[-self.MAX:]
        return True

    def creature_action(self, creature, action):
        eff = str(action.get('effect', ''))
        if not eff.startswith('fix103_'):
            return False
        weapon = REWARDS.get(creature.species)
        if not weapon:
            return False
        heavy = eff.endswith('_heavy')
        if not heavy:
            return self._normal(creature, weapon, float(action.get('damage', creature.attack)),
                                2 if weapon == 'thunder_longbow' else 24, visual=False)
        if len(self.effects) >= self.MAX:
            return False
        self.effects.append(dict(
            owner=creature, weapon=weapon, age=0., life=self.HEAVY_LIFE[weapon],
            facing=1 if creature.facing >= 0 else -1, sx=creature.x, base=creature.y,
            pulse=0, damage=float(action.get('damage', 15.)), visual=False,
        ))
        return True

    def _effect_box(self, e):
        """Stable authored-VFX envelope; no whole-animation jumping per pulse."""
        f = e['facing']; x = e['sx']; y = e['base']; w = e['weapon']
        if w == 'magma_core_hammer':
            x2 = x + f * 5.0 * T; return (min(x, x2), y - 4.2*T, max(x, x2), y)
        if w == 'nine_tail_fan':
            x2 = x + f * 5.4 * T; return (min(x, x2), y - 4.0*T, max(x, x2), y)
        if w == 'abyss_claw':
            cx = x + f * 2.25 * T; return (cx - 2.15*T, y - 3.0*T, cx + 2.15*T, y)
        if w == 'thunder_longbow':
            x2 = x + f * 7.2 * T; return (min(x, x2), y - 7.0*T, max(x, x2), y)
        if w == 'earth_drill_lance':
            x2 = x + f * 5.6 * T; return (min(x, x2), y - 2.4*T, max(x, x2), y)
        x2 = x + f * 5.4 * T; return (min(x, x2), y - 4.0*T, max(x, x2), y)

    # Kept as the renderer-facing name used by FIX104 visuals.
    def _box(self, e):
        return self._effect_box(e)

    @staticmethod
    def _world_box(e, start, end, top, bottom=0.0):
        f=e['facing']; x=e['sx']; y=e['base']
        x1=x+f*start*T; x2=x+f*end*T
        return (min(x1,x2), y-top*T, max(x1,x2), y-bottom*T)

    def _pulse_boxes(self, e, i):
        """Return [(box, damage_multiplier), ...] for one signature beat."""
        w = e['weapon']
        if w == 'magma_core_hammer':
            # Four pillars march away from the caster; the fourth is widest/tallest.
            pos = (1.15, 2.15, 3.20, 4.35)[min(i, 3)]
            wide = .48 if i < 3 else .78
            top = (2.1, 2.55, 3.05, 3.85)[min(i, 3)]
            return [(self._world_box(e, pos-wide, pos+wide, top), 1.38 if i == 3 else .96)]
        if w == 'nine_tail_fan':
            # Falling crystal lanes intentionally alternate instead of one full-zone tick.
            lanes = (1.0, 3.0, 4.7, 2.0, 4.0, 1.5, 3.5, 5.0)
            p = lanes[i % len(lanes)]
            boxes=[(self._world_box(e, p-.34, p+.34, 3.75, .05), .74)]
            # Mid/final beats add a low frost sweep without duplicating the same full box.
            if i in (3, 7): boxes.append((self._world_box(e, .35, 5.25, .72, .02), .44 if i == 3 else .62))
            return boxes
        if w == 'abyss_claw':
            if i == 0: return [(self._world_box(e, .25, 2.25, 2.75, 1.42), .72)]
            if i == 1: return [(self._world_box(e, 2.25, 4.25, 1.38, .10), .72)]
            if i == 2: return [(self._world_box(e, 1.15, 3.45, 2.55, .18), 1.28)]
            return [(self._world_box(e, .10, 4.55, 1.12, .02), .78)]
        if w == 'thunder_longbow':
            lanes=(.9, 2.25, 3.65, 5.10, 6.45, 4.25)
            p=lanes[i % len(lanes)]
            mult=1.18 if i in (2,5) else .90
            return [(self._world_box(e, p-.30, p+.30, 6.75, .02), mult)]
        if w == 'earth_drill_lance':
            # Moving drill head. Later beats leave a small ground fracture behind it.
            p=(.9, 1.75, 2.65, 3.55, 4.45, 5.20)[min(i,5)]
            rows=[(self._world_box(e, p-.72, p+.72, 1.82, .18), .78)]
            if i >= 3: rows.append((self._world_box(e, p-1.20, p-.30, .72, .02), .38))
            return rows
        # World tree: roots erupt in non-linear order, then the last beat sweeps the root bed.
        lanes=(1.0, 3.0, 4.65, 2.0, 4.0, 1.5, 3.45, 5.0)
        p=lanes[i % len(lanes)]
        rows=[(self._world_box(e, p-.42, p+.42, 3.45, .02), .72)]
        if i == 7: rows.append((self._world_box(e, .30, 5.30, .68, .02), .68))
        return rows

    def _resolve_pulse(self, e, pulse_index):
        owner=e['owner']; targets=self._targets(owner); hit_ids=set()
        # A target can be hit once by each sub-box only when boxes represent truly
        # separate layers (e.g. crystal + frost sweep).  Within the same box it is
        # naturally resolved once.
        for box, mult in self._pulse_boxes(e, pulse_index):
            hit_ids.clear()
            for target in targets:
                key=id(target)
                if key in hit_ids: continue
                bbox=target.combat_bbox() if hasattr(target,'combat_bbox') else target.bbox()
                if rects_overlap(box,bbox):
                    hit_ids.add(key)
                    self._hurt(owner,target,e['damage']*mult,e['facing'],self.WEAPON_ELEMENTS.get(e['weapon'],''))

    def update(self, dt):
        dt=max(0.,min(.25,float(dt))); keep=[]
        for e in self.effects:
            e['age']=min(e['life'],e['age']+dt)
            beats=self._heavy_beats(e['weapon'])
            i=int(e.get('pulse',0))
            while i < len(beats) and beats[i] <= e['age'] + 1e-8:
                self._resolve_pulse(e,i); i += 1
            e['pulse']=i
            if e['age'] < e['life']: keep.append(e)
        self.effects=keep
        for f in self.flashes: f['age'] += dt
        self.flashes=[f for f in self.flashes if f['age'] < f['life']]
        for f in self.deaths: f['age'] += dt
        self.deaths=[f for f in self.deaths if f['age'] < f['life']]

    def reserve(self):
        # Native VFX can contain more fine one-pixel rectangles than FIX115's
        # 32->96 exports, while the global boss-render cap still bounds the cost.
        return min(1200, len(self.effects)*520 + len(self.flashes)*300 + len(self.deaths)*520)

    def death(self, c):
        # Called after authoritative death deduplication; no second drop is issued.
        self.deaths.append(dict(death=True,species=c.species,x=c.x,y=c.y,
                                w=float(c.width()),h=float(c.height()),
                                facing=c.facing,age=0.,life=1.4))
        del self.deaths[:-4]

    def append_visuals(self, renderer, out, budget=1200):
        from systems.fix104_visuals import append_combat
        return append_combat(self,renderer,out,budget)
