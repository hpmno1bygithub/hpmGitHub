# -*- coding: utf-8 -*-
"""FIX35 sparse tile lava simulation.

Lava intentionally shares the useful volume-per-tile idea with WaterSystem but
runs much slower.  It has no wave physics: gravity, depression filling and
slow lateral equalisation are authoritative.  Water contact progressively
cools magma into solid igneous rock and emits conserved visual steam.
"""
from config import (
    LAVA_HZ, LAVA_FALL_FLOW, LAVA_SIDE_FLOW, LAVA_FLOW_EPSILON,
    LAVA_SOURCE_RATE_PER_SECOND, LAVA_COOL_RATE_PER_WATER,
    LAVA_WATER_CONSUME_RATE, LAVA_STEAM_YIELD, LAVA_BOTTOM_RESERVOIR_ROWS,
    CHUNK_SIZE, TILE_SIZE,
)
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import AIR, ICE, IGNEOUS_ROCK, LAYERED_SOLID_TILES, tile_def
from world.soil_layers import soil_vertical_bounds


class LavaSystem:
    def __init__(self, world, env, chunk_streamer):
        self.world=world
        self.env=env
        self.chunk_streamer=chunk_streamer
        self.task=FixedRateTask(LAVA_HZ,max_steps=3,phase=0.42)
        self.sources=[]  # (tx,ty,rate_per_second)
        self.tick_index=0

    def set_sources(self, rows):
        out=[]
        for row in rows or ():
            if not isinstance(row,(list,tuple)) or len(row)<2: continue
            try:
                tx=int(row[0]); ty=int(row[1])
                rate=float(row[2]) if len(row)>=3 else float(LAVA_SOURCE_RATE_PER_SECOND)
            except Exception: continue
            if 0<=tx<self.world.width_tiles and 0<=ty<self.world.height_tiles:
                out.append((tx,ty,max(0.0,rate)))
        self.sources=out

    def update(self,dt):
        for step in self.task.consume(dt):
            self._step(step)

    def _in_world(self,tx,ty):
        return 0<=int(tx)<self.world.width_tiles and 0<=int(ty)<self.world.height_tiles

    def _active(self,tx,ty):
        try:return self.chunk_streamer.is_active(int(tx)//CHUNK_SIZE,int(ty)//CHUNK_SIZE)
        except Exception:return True

    def _capacity(self,tx,ty):
        tx=int(tx); ty=int(ty)
        if not self._in_world(tx,ty): return 0.0
        tile_id=int(self.world.get_tile(tx,ty))
        # Unlike water, magma can enter excavated air regardless of the old
        # sealed-cave air-pressure helper. The rock itself is the boundary.
        if tile_id==AIR: return 1.0
        if bool(getattr(tile_def(tile_id), "one_way_platform", False)):
            return 1.0
        if tile_id in LAYERED_SOLID_TILES:
            try:
                mask=int(self.world.tile_layer_mask_at(tx,ty))
                top,_bottom=soil_vertical_bounds(mask)
                return max(0.0,min(1.0,float(top)))
            except Exception:return 0.0
        if tile_def(tile_id).solid:return 0.0
        return 1.0

    def surface_y(self,tx,ty):
        """World-Y of the actual magma surface in one occupied cell.

        FIX37 makes gameplay, damage and rendering query the same fractional
        geometry used by flow.  Lava remains flat (no wave displacement).
        """
        tx=int(tx); ty=int(ty)
        cap=max(0.0,min(1.0,float(self._capacity(tx,ty))))
        amount=max(0.0,min(cap,float(self.env.lava_amount(tx,ty))))
        return (float(ty)+cap-amount)*TILE_SIZE

    def surface_at_world(self,x,y,max_scan_cells=12):
        """Return the top free surface of the contiguous lava column at x/y.

        ``None`` means the sample point is not inside magma.  This is needed
        for stacked full cells: the physical surface may be several cells above
        the rock floor on which old builds incorrectly let the player walk.
        """
        tx=int(float(x)//TILE_SIZE)
        sample_ty=int(float(y)//TILE_SIZE)
        hit_ty=None
        for ty in (sample_ty,sample_ty-1):
            if not self._in_world(tx,ty):
                continue
            cap=max(0.0,min(1.0,float(self._capacity(tx,ty))))
            amount=max(0.0,min(cap,float(self.env.lava_amount(tx,ty))))
            if amount<=LAVA_FLOW_EPSILON:
                continue
            top=(float(ty)+cap-amount)*TILE_SIZE
            bottom=(float(ty)+cap)*TILE_SIZE
            if top-1e-6<=float(y)<=bottom+1e-6:
                hit_ty=ty
                break
        if hit_ty is None:
            return None
        top_ty=int(hit_ty)
        for _ in range(max(1,int(max_scan_cells))):
            above=top_ty-1
            if not self._in_world(tx,above):
                break
            cap=max(0.0,min(1.0,float(self._capacity(tx,above))))
            amount=max(0.0,min(cap,float(self.env.lava_amount(tx,above))))
            if amount<=LAVA_FLOW_EPSILON:
                break
            top_ty=above
        return float(self.surface_y(tx,top_ty))

    def depth_at_world(self,x,y):
        """Immersion depth below the authoritative contiguous lava surface."""
        surface=self.surface_at_world(x,y)
        if surface is None:
            return 0.0
        return max(0.0,float(y)-float(surface))

    def deposit(self,tx,ty,amount):
        """Place magma and preserve overflow by searching the local basin."""
        tx=int(tx); ty=int(ty); remain=max(0.0,float(amount))
        if remain<=0.0:return 0.0
        placed=0.0
        # Prefer target/below, then a bounded same-row depression search.
        candidates=[(tx,ty)]
        for yy in range(ty+1,min(self.world.height_tiles,ty+5)):
            candidates.append((tx,yy))
        for d in range(1,9):
            candidates.extend(((tx-d,ty),(tx+d,ty)))
        for cx,cy in candidates:
            if remain<=1e-12:break
            if not self._in_world(cx,cy):continue
            if cy>=self.world.height_tiles-max(1,int(LAVA_BOTTOM_RESERVOIR_ROWS)):
                self.env.lava_bottom_sink_mass += remain
                # Keep the bottom visible as a bottomless reservoir.
                self.env.set_lava(cx,cy,1.0)
                placed += remain; remain=0.0; break
            cap=self._capacity(cx,cy)
            room=max(0.0,cap-self.env.lava_amount(cx,cy)-self.env.water_amount(cx,cy))
            if room<=1e-9:continue
            moved=min(remain,room)
            self.env.set_lava(cx,cy,self.env.lava_amount(cx,cy)+moved)
            remain-=moved; placed+=moved
        return placed

    def _cool_cell(self,tx,ty,dt):
        lava=self.env.lava_amount(tx,ty)
        if lava<=1e-9:return False
        # Same-cell water is strongest; orthogonal contact also cools.
        contacts=[]
        for nx,ny,factor in ((tx,ty,1.0),(tx-1,ty,0.62),(tx+1,ty,0.62),(tx,ty-1,0.72),(tx,ty+1,0.55)):
            if not self._in_world(nx,ny):continue
            w=self.env.water_amount(nx,ny)
            if w>1e-7:contacts.append((nx,ny,w,factor))
        if not contacts:return False
        available=sum(w*f for _x,_y,w,f in contacts)
        consume_budget=min(available, max(0.01,float(LAVA_WATER_CONSUME_RATE)*dt))
        if consume_budget<=1e-9:return False
        remaining=consume_budget
        consumed=0.0
        for nx,ny,w,factor in contacts:
            if remaining<=1e-9:break
            effective=max(0.0,w*factor)
            take_eff=min(remaining,effective)
            actual=min(w,take_eff/max(0.05,factor))
            self.env.set_water(nx,ny,w-actual)
            consumed += actual
            remaining -= take_eff
        progress=self.env.lava_cooling_amount(tx,ty)
        # Cooling is intentionally gradual; larger exposed water mass cools faster.
        progress += consumed*float(LAVA_COOL_RATE_PER_WATER)/max(0.20,lava)
        self.env.set_lava_cooling(tx,ty,progress)
        steam=max(0.02,consumed*float(LAVA_STEAM_YIELD))
        sy=max(0,ty-1)
        try:self.env.add_reactive('steam',tx,sy,steam)
        except Exception:self.env.steam[(tx,sy)]=min(4.0,float(self.env.steam.get((tx,sy),0.0))+steam)
        if progress>=0.999:
            self.env.set_lava(tx,ty,0.0)
            self.env.lava_cooling.pop((tx,ty),None)
            # Cooling creates a real solid terrain tile; flow stops permanently.
            self.world.set_tile(tx,ty,IGNEOUS_ROCK,track_change=True)
            try:self.world.mark_tile_geometry_dirty(tx,ty)
            except Exception:pass
            return True
        return False

    def _step(self,dt):
        self.tick_index+=1
        # Continuous vents are finite-rate sources, not infinite cells. Their
        # authored drain shafts carry overflow to the bottom reservoir.
        for tx,ty,rate in tuple(self.sources):
            self.deposit(tx,ty,rate*dt)

        # Bottom row is an infinite sink/reservoir. Never lets the map overflow
        # just because long-running vents have emitted a lot of magma.
        bottom0=max(0,self.world.height_tiles-max(1,int(LAVA_BOTTOM_RESERVOIR_ROWS)))
        for key,amount in list(self.env.lava.items()):
            tx,ty=key
            if ty>=bottom0:
                if amount<1.0:self.env.set_lava(tx,ty,1.0)

        # Cooling before motion lets a water dam freeze the contact front.
        for (tx,ty),amount in list(self.env.lava.items()):
            if amount<=LAVA_FLOW_EPSILON:continue
            if not self._active(tx,ty):continue
            self._cool_cell(tx,ty,dt)

        keys=sorted(tuple(self.env.lava.keys()), key=lambda k:(-k[1],k[0]))
        delta={}
        def add(k,v):delta[k]=delta.get(k,0.0)+v
        for tx,ty in keys:
            amount=max(0.0,self.env.lava_amount(tx,ty)+delta.get((tx,ty),0.0))
            if amount<=LAVA_FLOW_EPSILON or not self._active(tx,ty):continue
            if ty>=bottom0:
                # Any additional volume entering the bottom is swallowed.
                overflow=max(0.0,amount-1.0)
                if overflow>0:self.env.lava_bottom_sink_mass+=overflow
                continue
            # Gravity first.
            ny=ty+1
            if ny>=bottom0:
                moved=min(amount,float(LAVA_FALL_FLOW))
                add((tx,ty),-moved)
                self.env.lava_bottom_sink_mass+=moved
                self.env.set_lava(tx,ny,1.0)
                amount-=moved
            else:
                cap=self._capacity(tx,ny)
                dest=max(0.0,self.env.lava_amount(tx,ny)+delta.get((tx,ny),0.0))
                water=self.env.water_amount(tx,ny)
                room=max(0.0,cap-dest-water)
                if room>1e-9:
                    moved=min(amount,room,float(LAVA_FALL_FLOW))
                    add((tx,ty),-moved); add((tx,ny),moved); amount-=moved
            if amount<=LAVA_FLOW_EPSILON:continue
            # Slow horizontal depression filling. Alternate bias avoids drift.
            dirs=(-1,1) if self.tick_index%2==0 else (1,-1)
            for dx in dirs:
                nx=tx+dx
                cap=self._capacity(nx,ty)
                if cap<=1e-9:continue
                here=max(0.0,self.env.lava_amount(tx,ty)+delta.get((tx,ty),0.0))
                dest=max(0.0,self.env.lava_amount(nx,ty)+delta.get((nx,ty),0.0))
                water=self.env.water_amount(nx,ty)
                room=max(0.0,cap-dest-water)
                diff=max(0.0,(here-dest)*0.5)
                moved=min(diff,room,float(LAVA_SIDE_FLOW))
                if moved>LAVA_FLOW_EPSILON:
                    add((tx,ty),-moved); add((nx,ty),moved)

        for (tx,ty),dv in delta.items():
            if ty>=bottom0:
                self.env.set_lava(tx,ty,1.0)
                continue
            self.env.set_lava(tx,ty,self.env.lava_amount(tx,ty)+dv)
