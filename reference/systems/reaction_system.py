# -*- coding: utf-8 -*-
"""V0.7.0 event-driven systemic element reactions.

The world may be millions of tiles wide, but this system never scans it.
Only cells dirtied by water/fire/electric/tile/reactive changes enter the queue.
Rules are data-driven; handlers conserve/transfer existing gameplay state where
practical and produce sparse transient fields for new elements.
"""
from collections import deque
from dataclasses import dataclass
import math

from config import (
    CHUNK_SIZE,
    REACTION_HZ,
    REACTION_MAX_CELLS_PER_TICK,
    REACTION_EPSILON,
    REACTION_WATER_ELECTRIC_LONG_SECONDS,
    REACTION_TRANSIENT_DECAY_PER_SEC,
    REACTION_GAS_DECAY_PER_SEC,
    REACTION_LIQUID_DECAY_PER_SEC,
    REACTION_STEAM_SPREAD_PER_SEC,
    REACTION_GAS_SPREAD_PER_SEC,
    REACTION_WATER_FIRE_RATE,
    REACTION_WATER_POISON_RATE,
    REACTION_POISON_STEAM_RATE,
    REACTION_ACID_ELECTRIC_RATE,
    POISON_GAS_LIFETIME_SECONDS,
    MAGNET_DEFAULT_RADIUS_TILES,
    MAGNET_MAX_SCAN_CELLS,
    MAGNET_FORCE_SCALE,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import AIR, ICE, tile_def

WATER = "water"
FIRE = "fire"
ICE_E = "ice"
ELECTRIC = "electric"
POISON_GAS = "poison_gas"
ACID = "acid"
WIND = "wind"
STEAM = "steam"
POISON_LIQUID = "poison_liquid"
MAGNETIC = "magnetic"

@dataclass(frozen=True)
class ReactionRule:
    a: str
    b: str
    result: str
    handler: str
    min_seconds: float = 0.0
    note: str = ""

# V0.7.2.3: WATER + ICE is deliberately NOT a generic reaction.  Adjacency
# alone must never cause an unbounded pond-wide freeze.  Ice expansion is now
# owned by the ice spell impact and has a strict per-cast tile budget.
REACTION_RULES = (
    ReactionRule(WATER, FIRE, STEAM, "water_fire", note="water+fire -> rising steam"),
    ReactionRule(WATER, ELECTRIC, "electric_shock", "water_electric", note="water+electric -> conductive shock"),
    ReactionRule(WATER, POISON_GAS, ACID, "water_poison", note="water+poison gas -> acid"),
    ReactionRule(FIRE, ICE_E, WATER, "fire_ice", note="fire+ice -> water"),
    ReactionRule(FIRE, POISON_GAS, WIND, "fire_poison", note="fire+poison gas -> wind/updraft"),
    ReactionRule(FIRE, ACID, POISON_GAS, "fire_acid", note="fire+acid -> poison gas"),
    ReactionRule(FIRE, WIND, "updraft", "fire_wind", note="fire+wind -> rising air"),
    ReactionRule(FIRE, STEAM, "updraft", "fire_steam", note="fire+steam -> rising air"),
    ReactionRule(ELECTRIC, ACID, POISON_GAS, "electric_acid", note="electric+acid -> poison gas"),
    ReactionRule(POISON_GAS, STEAM, POISON_LIQUID, "poison_steam", note="poison gas+steam -> poison liquid"),
)

class ReactionSystem:
    def __init__(self, world, env, chunk_streamer, thermal=None, wind_system=None, energy=None):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.thermal = thermal
        self.wind_system = wind_system
        self.energy = energy
        self.task = FixedRateTask(REACTION_HZ, max_steps=2)
        self.queue = deque()
        self.queued = set()
        self.contact_time = {}
        self.tick_index = 0
        self.last_debug = {"processed": 0, "queued": 0, "reactions": 0}
        self._born_tick = {}
        self._magnet_cache = {}
        self._known_active_chunks = set()

        self.env._reactive_change_callback = self.on_reactive_changed
        self.world.add_tile_change_listener(self.on_tile_changed)

    # ---------- dirty/event path ----------
    def mark_dirty(self, tx, ty, neighbors=True):
        tx = int(tx); ty = int(ty)
        points = ((tx, ty),)
        if neighbors:
            points = ((tx, ty),(tx-1,ty),(tx+1,ty),(tx,ty-1),(tx,ty+1))
        for x,y in points:
            if not (0 <= x < self.world.width_tiles and 0 <= y < self.world.height_tiles):
                continue
            key=(x,y)
            if key not in self.queued:
                self.queued.add(key); self.queue.append(key)

    def on_reactive_changed(self, tx, ty, _element=None):
        tx=int(tx);ty=int(ty)
        element=str(_element or "")
        if not self.chunk_streamer.is_active(tx//CHUNK_SIZE,ty//CHUNK_SIZE):
            return
        # FIX40: dynamic water moves constantly, but water by itself has no
        # reaction. Do not feed every ocean flow write into the 20 Hz reaction
        # queue. Only wake the local cell if one of water's actual co-reactants
        # is already in the 5-cell cross. A newly-created fire/electric/gas
        # source independently marks itself dirty, so reactions remain prompt.
        if element == WATER:
            for x,y in ((tx,ty),(tx-1,ty),(tx+1,ty),(tx,ty-1),(tx,ty+1)):
                if not (0 <= x < self.world.width_tiles and 0 <= y < self.world.height_tiles):
                    continue
                key=(x,y)
                if key in self.env.fire:
                    self.mark_dirty(tx,ty,True); return
                if abs(float(self.env.electric_charge.get(key,0.0))) > REACTION_EPSILON:
                    self.mark_dirty(tx,ty,True); return
                if float(self.env.reactive_amount(POISON_GAS,x,y)) > REACTION_EPSILON:
                    self.mark_dirty(tx,ty,True); return
            return
        # Lava and magnetic-source mutations are handled by their own systems;
        # neither appears in REACTION_RULES. Avoid useless queue churn.
        if element in ("lava", MAGNETIC):
            return
        self.mark_dirty(tx, ty, True)

    def on_tile_changed(self, tx, ty, _old, _new):
        self._magnet_cache.clear()
        self.mark_dirty(tx, ty, True)

    def reset(self):
        self.queue.clear(); self.queued.clear(); self.contact_time.clear()
        self._born_tick.clear(); self._magnet_cache.clear()
        self._known_active_chunks.clear()

    def _activate_chunks(self, chunks):
        # At most 256 cells per newly active chunk. This is the only discovery
        # pass required when walking into sleeping world regions.
        for cx,cy in tuple(chunks):
            sx=int(cx)*CHUNK_SIZE; sy=int(cy)*CHUNK_SIZE
            ex=min(self.world.width_tiles,sx+CHUNK_SIZE)
            ey=min(self.world.height_tiles,sy+CHUNK_SIZE)
            for ty in range(sy,ey):
                for tx in range(sx,ex):
                    key=(tx,ty)
                    # FIX40: water/ice are passive reactants. Queueing every
                    # stable ocean/ice cell when a chunk wakes created a large
                    # burst despite there being nothing to react with. Active
                    # sources/transients are sufficient discovery seeds because
                    # each rule searches the neighbouring cross for its partner.
                    if (key in self.env.fire or key in self.env.electric_charge or
                        key in self.env.steam or key in self.env.poison_gas or
                        key in self.env.acid or key in self.env.poison_liquid):
                        self.mark_dirty(tx,ty,True)

    def _sync_active_chunks(self):
        current=set(self.chunk_streamer.active_chunks)
        new=current-self._known_active_chunks
        if new:self._activate_chunks(new)
        self._known_active_chunks=current

    def seed_existing(self):
        self._sync_active_chunks()

    # ---------- material/element query ----------
    def _amount(self, element, tx, ty):
        key=(int(tx),int(ty))
        if element == WATER: return self.env.water_amount(*key)
        if element == FIRE:
            cell=self.env.fire.get(key)
            return 0.0 if cell is None else max(0.0,float(cell.intensity))
        if element == ICE_E: return 1.0 if self.world.get_tile(*key)==ICE else 0.0
        if element == ELECTRIC: return abs(float(self.env.electric_charge.get(key,0.0)))
        if element in (POISON_GAS,ACID,STEAM,POISON_LIQUID):
            return self.env.reactive_amount(element,*key)
        if element == WIND:
            # Ambient horizontal weather wind is not present underground.
            # Only explicitly reaction-created local wind counts there.
            local=abs(self.env.reactive_amount("reaction_wind",*key))
            surface=self.world.first_solid_row(key[0])
            if surface is not None and key[1] > int(surface):
                return local
            cx=key[0]//CHUNK_SIZE; cy=key[1]//CHUNK_SIZE
            try:
                vx,vy=self.env.wind.get((cx,cy),(0.0,0.0))
                local=max(local, math.hypot(float(vx),float(vy))/120.0)
            except Exception: pass
            return local
        return 0.0

    def _near_points(self, tx, ty, element):
        out=[]
        for x,y in ((tx,ty),(tx-1,ty),(tx+1,ty),(tx,ty-1),(tx,ty+1)):
            if 0 <= x < self.world.width_tiles and 0 <= y < self.world.height_tiles:
                a=self._amount(element,x,y)
                if a > REACTION_EPSILON:
                    # Ignore fields born earlier in this same reaction tick so
                    # chains advance one tick at a time instead of exploding in one frame.
                    if self._born_tick.get((element,x,y),-1) == self.tick_index:
                        continue
                    out.append((x,y,a))
        return out

    def _pairs(self, tx, ty, a, b):
        aa=self._near_points(tx,ty,a); bb=self._near_points(tx,ty,b)
        for ax,ay,av in aa:
            for bx,by,bv in bb:
                if abs(ax-bx)+abs(ay-by) <= 1:
                    yield (ax,ay,av),(bx,by,bv)

    def _born_add(self, field, tx, ty, amount):
        self.env.add_reactive(field,tx,ty,amount)
        if field == POISON_GAS and amount > 0.0:
            self.env.poison_gas_ttl[(int(tx), int(ty))] = float(POISON_GAS_LIFETIME_SECONDS)
        self._born_tick[(field,int(tx),int(ty))]=self.tick_index

    def _consume_reactive(self, field, tx, ty, amount):
        old=self.env.reactive_amount(field,tx,ty)
        self.env.set_reactive(field,tx,ty,max(0.0,old-float(amount)))

    # ---------- rule handlers ----------
    def _water_fire(self, pa, pb, dt):
        w = pa if self._amount(WATER,*pa[:2]) > 0 else pb
        f = pb if w is pa else pa
        tx,ty,wa=w; fx,fy,fa=f
        moved=min(wa, max(0.015, REACTION_WATER_FIRE_RATE*dt*max(0.25,fa)))
        if moved <= 0: return False
        self.env.set_water(tx,ty,max(0.0,wa-moved))
        self._born_add(STEAM,tx,ty,moved)
        self.env.add_updraft(tx//CHUNK_SIZE,ty//CHUNK_SIZE,moved*22.0)
        fire=self.env.fire.get((fx,fy))
        if fire is not None: fire.intensity=max(0.0,fire.intensity-moved*0.30)
        return True

    def _water_ice(self, pa, pb, dt):
        # Compatibility stub only.  Since V0.7.2.3, ordinary liquid touching
        # ordinary ice does not self-propagate freezing.  Explicit ice magic
        # and genuine sub-zero thermal conditions are the only freeze sources.
        return False

    def _water_electric(self, pa, pb, dt):
        """Water + electricity -> a transient conductive shock field.

        V0.7.2 deliberately does NOT manufacture poison gas here.  Water is
        simply a good path for charge; poison chemistry belongs to the later
        poison/acid element stage.
        """
        w=pa if self._amount(WATER,*pa[:2]) > 0 else pb
        e=pb if w is pa else pa
        tx,ty,wa=w; ex,ey,ea=e
        shock=max(0.25,min(2.0,abs(float(ea))*(0.75+min(1.0,float(wa))*0.55)))
        self.env.set_reactive("electric_shock",tx,ty,shock)
        # Water does not disappear from being electrified.  Charge loses a
        # little potential each reaction tick; ElectricalSystem continues the
        # bounded network propagation through neighboring conductive cells.
        self.env.electric_charge[(ex,ey)] = float(self.env.electric_charge.get((ex,ey),0.0))*0.985
        self.mark_dirty(tx,ty,False)
        return True

    def _water_poison(self, pa, pb, dt):
        w=pa if self._amount(WATER,*pa[:2]) > 0 else pb
        g=pb if w is pa else pa
        tx,ty,wa=w; gx,gy,ga=g
        # Surface poison gas created by electrolysis intentionally occupies the
        # AIR cell one tile above water. It must not instantly destroy itself
        # by becoming acid simply because the cells are adjacent. Acid is made
        # only after poison gas is actually dissolved into the same water cell.
        if (tx,ty) != (gx,gy):
            return False
        moved=min(wa,ga,max(0.02,REACTION_WATER_POISON_RATE*dt))
        if moved<=0:return False
        self.env.set_water(tx,ty,max(0.0,wa-moved))
        self._consume_reactive(POISON_GAS,gx,gy,moved)
        self._born_add(ACID,tx,ty,moved*1.35)
        return True

    def _fire_ice(self, pa, pb, dt):
        ice=pa if self._amount(ICE_E,*pa[:2]) > 0 else pb
        tx,ty,_=ice
        if self.thermal is None:return False
        self.thermal._melt_ice_cell(tx,ty,temperature_c=8.0)
        return True

    def _fire_poison(self, pa, pb, dt):
        f=pa if self._amount(FIRE,*pa[:2]) > 0 else pb
        g=pb if f is pa else pa
        fx,fy,fa=f; gx,gy,ga=g
        used=min(ga,max(0.02,0.16*dt*max(0.3,fa)))
        if used<=0:return False
        self._consume_reactive(POISON_GAS,gx,gy,used)
        # Underground horizontal airflow is prohibited; create upward lift only.
        surface=self.world.first_solid_row(fx)
        if surface is not None and fy > int(surface):
            self.env.add_updraft(fx//CHUNK_SIZE,fy//CHUNK_SIZE,used*38.0)
        else:
            self._born_add("reaction_wind",fx,fy,used*2.2)
        return True

    def _fire_acid(self, pa, pb, dt):
        f=pa if self._amount(FIRE,*pa[:2]) > 0 else pb
        a=pb if f is pa else pa
        ax,ay,aa=a
        used=min(aa,max(0.015,0.12*dt))
        if used<=0:return False
        self._consume_reactive(ACID,ax,ay,used)
        self._born_add(POISON_GAS,ax,ay,used*1.15)
        return True

    def _fire_wind(self, pa, pb, dt):
        f=pa if self._amount(FIRE,*pa[:2]) > 0 else pb
        fx,fy,fa=f
        self.env.add_updraft(fx//CHUNK_SIZE,fy//CHUNK_SIZE,max(0.25,fa)*4.0*dt)
        return True

    def _fire_steam(self, pa, pb, dt):
        f=pa if self._amount(FIRE,*pa[:2]) > 0 else pb
        s=pb if f is pa else pa
        fx,fy,fa=f; sx,sy,sa=s
        used=min(sa,max(0.01,0.10*dt))
        self.env.add_updraft(fx//CHUNK_SIZE,fy//CHUNK_SIZE,used*30.0+fa*0.3)
        return used>0

    def _electric_acid(self, pa, pb, dt):
        a=pa if self._amount(ACID,*pa[:2]) > 0 else pb
        ax,ay,aa=a
        used=min(aa,max(0.01,REACTION_ACID_ELECTRIC_RATE*dt))
        if used<=0:return False
        self._consume_reactive(ACID,ax,ay,used)
        self._born_add(POISON_GAS,ax,ay,used*1.25)
        return True

    def _poison_steam(self, pa, pb, dt):
        g=pa if self._amount(POISON_GAS,*pa[:2]) > 0 else pb
        s=pb if g is pa else pa
        gx,gy,ga=g; sx,sy,sa=s
        used=min(ga,sa,max(0.01,REACTION_POISON_STEAM_RATE*dt))
        if used<=0:return False
        self._consume_reactive(POISON_GAS,gx,gy,used)
        self._consume_reactive(STEAM,sx,sy,used)
        self._born_add(POISON_LIQUID,gx,gy,used*1.15)
        return True

    _HANDLERS = {
        "water_fire": _water_fire, "water_ice": _water_ice,
        "water_electric": _water_electric, "water_poison": _water_poison,
        "fire_ice": _fire_ice, "fire_poison": _fire_poison,
        "fire_acid": _fire_acid, "fire_wind": _fire_wind,
        "fire_steam": _fire_steam, "electric_acid": _electric_acid,
        "poison_steam": _poison_steam,
    }

    # ---------- sparse transport/decay ----------
    def _decay_table(self, field, rate, dt):
        table=getattr(self.env,field)
        for key,value in tuple(table.items()):
            # only active chunks are time-stepped; distant chemistry sleeps.
            if not self.chunk_streamer.is_active(key[0]//CHUNK_SIZE,key[1]//CHUNK_SIZE):
                continue
            nv=max(0.0,float(value)-float(rate)*float(dt)*max(0.2,float(value)))
            if nv <= REACTION_EPSILON*0.2: table.pop(key,None)
            else: table[key]=nv

    def _steam_to_atmospheric_vapor(self, dt):
        """Visible tile steam rises, then becomes chunk-scale atmospheric vapor.

        Older builds simply decayed STEAM and deleted that water-equivalent
        amount.  V0.7.4.0 makes the fade a real phase transfer so desert
        evaporation can be seen rising without violating the water ledger.
        """
        table = self.env.steam
        rate = float(REACTION_GAS_DECAY_PER_SEC)
        for key, value in tuple(table.items()):
            tx, ty = key
            if not self.chunk_streamer.is_active(tx//CHUNK_SIZE, ty//CHUNK_SIZE):
                continue
            value = max(0.0, float(value))
            if value <= 0.0:
                table.pop(key, None)
                continue
            lost = min(value, rate * float(dt) * max(0.2, value))
            remain = value - lost
            if remain <= REACTION_EPSILON * 0.2:
                lost += max(0.0, remain)
                table.pop(key, None)
            else:
                table[key] = remain
            if lost > 0.0:
                self.env.add_vapor(tx//CHUNK_SIZE, ty//CHUNK_SIZE, lost)

    def _decay_poison_gas_lifetime(self, dt):
        # Exact finite lifetime with linear opacity fade. Amount remains the
        # conserved gas quantity; multiplying by remaining/previous TTL makes
        # its rendered concentration fade smoothly to zero at the deadline.
        table = self.env.poison_gas
        ttl_table = self.env.poison_gas_ttl
        for key, amount in tuple(table.items()):
            if not self.chunk_streamer.is_active(key[0]//CHUNK_SIZE,key[1]//CHUNK_SIZE):
                continue
            old_ttl = max(1e-6, float(ttl_table.get(key, POISON_GAS_LIFETIME_SECONDS)))
            new_ttl = max(0.0, old_ttl - float(dt))
            if new_ttl <= 0.0:
                table.pop(key, None); ttl_table.pop(key, None)
                continue
            table[key] = max(0.0, float(amount) * (new_ttl / old_ttl))
            ttl_table[key] = new_ttl
            if table[key] <= REACTION_EPSILON*0.2:
                table.pop(key, None); ttl_table.pop(key, None)

    def _maintain_transients(self, dt):
        self._decay_table("electric_shock", REACTION_TRANSIENT_DECAY_PER_SEC, dt)
        self._steam_to_atmospheric_vapor(dt)
        self._decay_poison_gas_lifetime(dt)
        self._decay_table(ACID, REACTION_LIQUID_DECAY_PER_SEC, dt)
        self._decay_table(POISON_LIQUID, REACTION_LIQUID_DECAY_PER_SEC*0.5, dt)
        self._decay_table("reaction_wind", REACTION_TRANSIENT_DECAY_PER_SEC*0.65, dt)

    def _transport_reactive_gases(self, dt):
        # Steam rises. Poison gas is deliberately heavier/low-lying in this
        # stage: it stays at its current height and only creeps horizontally.
        # Electrolysis therefore leaves the cloud one tile above the water
        # surface instead of letting it float up like steam.
        steam = self.env.steam
        changes=[]
        for (tx,ty),amount in tuple(steam.items()):
            if not self.chunk_streamer.is_active(tx//CHUNK_SIZE,ty//CHUNK_SIZE):
                continue
            if amount <= REACTION_EPSILON or ty <= 0:
                continue
            nx,ny=tx,ty-1
            if tile_def(self.world.get_tile(nx,ny)).solid:
                continue
            moved=min(float(amount)*0.22,max(0.0,float(REACTION_STEAM_SPREAD_PER_SEC)*float(dt)))
            if moved > 1e-6:
                changes.append((STEAM,tx,ty,nx,ny,moved,None))

        gas = self.env.poison_gas
        for (tx,ty),amount in tuple(gas.items()):
            if not self.chunk_streamer.is_active(tx//CHUNK_SIZE,ty//CHUNK_SIZE):
                continue
            if amount <= REACTION_EPSILON:
                continue
            direction=-1 if ((tx+ty+self.tick_index)&1)==0 else 1
            candidates=((tx+direction,ty),(tx-direction,ty))
            for nx,ny in candidates:
                if not (0<=nx<self.world.width_tiles and 0<=ny<self.world.height_tiles):
                    continue
                if tile_def(self.world.get_tile(nx,ny)).solid:
                    continue
                # Poison gas stays exactly one tile above standing water. It
                # may spread along a continuous water surface, but not drift
                # away into arbitrary cave/sky cells.
                if ny + 1 >= self.world.height_tiles:
                    continue
                if self.env.water_amount(nx,ny) > REACTION_EPSILON:
                    continue
                if self.env.water_amount(nx,ny+1) <= REACTION_EPSILON:
                    continue
                moved=min(float(amount)*0.12,max(0.0,float(REACTION_GAS_SPREAD_PER_SEC)*0.55*float(dt)))
                if moved > 1e-6:
                    ttl=float(self.env.poison_gas_ttl.get((tx,ty),POISON_GAS_LIFETIME_SECONDS))
                    changes.append((POISON_GAS,tx,ty,nx,ny,moved,ttl))
                break

        for field,tx,ty,nx,ny,moved,ttl in changes:
            self._consume_reactive(field,tx,ty,moved)
            self._born_add(field,nx,ny,moved)
            if field == POISON_GAS and ttl is not None:
                # Moving gas keeps the source cloud's remaining lifetime;
                # horizontal diffusion must not refresh it indefinitely.
                self.env.poison_gas_ttl[(nx,ny)] = min(
                    float(self.env.poison_gas_ttl.get((nx,ny),ttl)), float(ttl)
                )
            self.mark_dirty(nx,ny,True)

    def update(self, dt):
        self._sync_active_chunks()
        for step in self.task.consume(dt):
            self._step(step)

    def _step(self, dt):
        self.tick_index += 1
        self._maintain_transients(dt)
        self._transport_reactive_gases(dt)
        count=min(int(REACTION_MAX_CELLS_PER_TICK),len(self.queue))
        processed=reactions=0
        for _ in range(count):
            tx,ty=self.queue.popleft(); self.queued.discard((tx,ty))
            if not self.chunk_streamer.is_active(tx//CHUNK_SIZE,ty//CHUNK_SIZE):
                continue
            processed+=1
            # FIX40: one dirty liquid cell used to rescan the same 5-neighbour
            # cross twice for every reaction rule. A stable water-only ocean
            # therefore paid ~100 sparse/tile queries per cell even though no
            # second reactant existed. Cache each element neighbourhood once
            # for this cell and skip a rule immediately when either side is
            # absent. Reaction ordering/handlers are unchanged.
            near_cache = {}
            def near(element):
                points = near_cache.get(element)
                if points is None:
                    points = self._near_points(tx, ty, element)
                    near_cache[element] = points
                return points

            for rule in REACTION_RULES:
                aa = near(rule.a)
                if not aa:
                    continue
                bb = near(rule.b)
                if not bb:
                    continue
                handler=getattr(self,"_"+rule.handler)
                fired=False
                for pa in aa:
                    ax, ay = pa[0], pa[1]
                    for pb in bb:
                        if abs(ax-pb[0])+abs(ay-pb[1]) > 1:
                            continue
                        if handler(pa,pb,dt):
                            fired=True; reactions+=1; break
                    if fired:
                        break
                if fired:
                    # Outputs are queued for next tick; don't cascade infinitely.
                    self.mark_dirty(tx,ty,True)
            # Fire is a persistent energy source. Keep only fire-source cells
            # warm in the reaction queue so future wind/steam can interact;
            # stable water alone never causes permanent polling.
            if (tx,ty) in self.env.fire:
                self.mark_dirty(tx,ty,False)
        self.last_debug={"processed":processed,"queued":len(self.queue),"reactions":reactions}

    # ---------- future spell/machine API ----------
    def inject_element(self, tx, ty, element, amount=1.0):
        tx=int(tx);ty=int(ty);amount=max(0.0,float(amount))
        if element==WATER:
            self.env.set_water(tx,ty,self.env.water_amount(tx,ty)+amount)
        elif element==FIRE:
            self.env.ignite(tx,ty,intensity=min(1.0,max(0.1,amount)),fuel=max(0.1,amount),source="reactive")
        elif element==ELECTRIC:
            self.env.electric_charge[(tx,ty)]=self.env.electric_charge.get((tx,ty),0.0)+amount
            self.mark_dirty(tx,ty,True)
        elif element==ICE_E:
            if self.thermal is not None and self.world.get_tile(tx,ty)==AIR:
                self.thermal.create_ice_tile(tx,ty,min(1.0,amount),temperature_c=-8.0)
        elif element==MAGNETIC:
            self.env.set_magnetic_source(tx,ty,amount,MAGNET_DEFAULT_RADIUS_TILES)
        elif element in (STEAM,POISON_GAS,ACID,POISON_LIQUID):
            self.env.add_reactive(element,tx,ty,amount)
        elif element==WIND:
            self.env.add_reactive("reaction_wind",tx,ty,amount)
        else:
            raise KeyError("unknown element: %s" % element)
        self.mark_dirty(tx,ty,True)

    # ---------- magnetic source-driven query ----------
    def magnetic_force_at_tile(self, tx, ty):
        tx=int(tx);ty=int(ty)
        td=tile_def(self.world.get_tile(tx,ty))
        if not bool(getattr(td,"metal",False)):
            return (0.0,0.0)
        fx=fy=0.0
        for (sx,sy),(strength,radius) in tuple(self.env.magnetic_sources.items()):
            dx=float(sx-tx);dy=float(sy-ty);d2=dx*dx+dy*dy
            if d2<=1e-9 or d2>float(radius*radius):continue
            inv=1.0/math.sqrt(d2)
            mag=float(strength)*float(getattr(td,"magnetic_susceptibility",0.0))*MAGNET_FORCE_SCALE/max(1.0,d2)
            fx+=dx*inv*mag;fy+=dy*inv*mag
        return (fx,fy)

    def magnetic_affected_tiles(self, source_tx, source_ty):
        key=(int(source_tx),int(source_ty))
        src=self.env.magnetic_sources.get(key)
        if src is None:return tuple()
        strength,radius=src
        cache_key=(key,float(strength),int(radius),int(getattr(self.world,"revision",0)))
        if cache_key in self._magnet_cache:return self._magnet_cache[cache_key]
        out=[];r=int(radius);scanned=0
        for ty in range(max(0,key[1]-r),min(self.world.height_tiles,key[1]+r+1)):
            for tx in range(max(0,key[0]-r),min(self.world.width_tiles,key[0]+r+1)):
                scanned+=1
                if scanned>MAGNET_MAX_SCAN_CELLS:break
                td=tile_def(self.world.get_tile(tx,ty))
                if getattr(td,"metal",False):
                    force=self.magnetic_force_at_tile(tx,ty)
                    if abs(force[0])+abs(force[1])>1e-9:out.append((tx,ty,force))
            if scanned>MAGNET_MAX_SCAN_CELLS:break
        result=tuple(out);self._magnet_cache.clear();self._magnet_cache[cache_key]=result
        return result
