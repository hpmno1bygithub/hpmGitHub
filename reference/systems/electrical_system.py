# -*- coding: utf-8 -*-
"""Sparse event-driven electricity for the reactive world.

A voltage event flood-fills only a bounded conductive network in ACTIVE chunks.
No per-frame world scan is performed. Stored charge then decays sparsely and can
feed ReactionSystem (water shock -> long-contact poison gas).
"""
from collections import deque
from config import (
    ELECTRIC_CHARGE_DECAY_PER_SEC, LIGHTNING_ENERGY_DEFAULT, CHUNK_SIZE,
    ELECTRIC_NETWORK_MAX_CELLS, ELECTRIC_NETWORK_MIN_CHARGE,
    ELECTRIC_WATER_CONDUCTIVITY, ELECTRIC_ACID_CONDUCTIVITY,
    ELECTRIC_POISON_LIQUID_CONDUCTIVITY, ELECTRIC_JOULE_HEAT_SCALE,
)
from world.tile_registry import tile_def

class ElectricalSystem:
    def __init__(self, env, energy=None, world=None, chunk_streamer=None):
        self.env=env;self.energy=energy;self.world=world;self.chunk_streamer=chunk_streamer
        self.last_debug={"network_cells":0,"source_charge":0.0}

    def _active(self,tx,ty):
        return self.chunk_streamer is None or self.chunk_streamer.is_active(int(tx)//CHUNK_SIZE,int(ty)//CHUNK_SIZE)

    def _conductivity(self,tx,ty):
        tx=int(tx);ty=int(ty)
        c=0.0
        if self.env.water_amount(tx,ty)>0.05:
            c=max(c,ELECTRIC_WATER_CONDUCTIVITY)
        # V0.7.2: soil moisture is a weak conductor.  This is intentionally
        # weaker than a free-water cell so rain/wet ground matters without
        # turning every dirt block into a copper wire.
        soil = self.env.soil.get((tx,ty))
        if soil is not None:
            moisture=max(0.0,min(1.0,float(getattr(soil,"moisture",0.0))))
            if moisture > 0.18:
                c=max(c, min(0.48, 0.10 + moisture*0.38))
        if self.env.reactive_amount("acid",tx,ty)>0.02:c=max(c,ELECTRIC_ACID_CONDUCTIVITY)
        if self.env.reactive_amount("poison_liquid",tx,ty)>0.02:c=max(c,ELECTRIC_POISON_LIQUID_CONDUCTIVITY)
        if self.world is not None:
            td=tile_def(self.world.get_tile(tx,ty))
            c=max(c,float(getattr(td,"electrical_conductivity",0.0)))
        return max(0.0,min(1.0,c))

    def update(self,dt):
        decay=max(0.0,1.0-ELECTRIC_CHARGE_DECAY_PER_SEC*max(0.0,float(dt)))
        for key,value in tuple(self.env.electric_charge.items()):
            if not self._active(*key):
                continue
            value=float(value)*decay
            if abs(value)<1e-5:self.env.electric_charge.pop(key,None)
            else:
                self.env.electric_charge[key]=value
                # Joule heating is local and property-driven. Good conductors
                # heat less; resistive metal warms more for the same charge.
                if self.world is not None:
                    td=tile_def(self.world.get_tile(*key))
                    cond=float(getattr(td,"electrical_conductivity",0.0))
                    if getattr(td,"metal",False) and cond>0.0:
                        heat=(value*value)*max(0.03,1.0-cond)*ELECTRIC_JOULE_HEAT_SCALE*max(0.0,float(dt))
                        if heat>1e-6:
                            self.env.set_temperature(key[0],key[1],self.env.temperature_at(key[0],key[1],20.0)+heat)

    def _write_charge(self,tx,ty,charge):
        key=(int(tx),int(ty))
        old=float(self.env.electric_charge.get(key,0.0))
        # Network propagation represents one impulse; keep the stronger local
        # potential instead of adding every alternate path repeatedly.
        if abs(float(charge))>abs(old):self.env.electric_charge[key]=float(charge)
        try:self.env._notify_reactive_change(key[0],key[1],"electric")
        except Exception:pass

    def apply_charge(self,tx,ty,charge):
        tx=int(tx);ty=int(ty);charge=float(charge)
        self._write_charge(tx,ty,charge)
        if self.world is None or abs(charge)<ELECTRIC_NETWORK_MIN_CHARGE:
            self.last_debug={"network_cells":1,"source_charge":charge};return 1
        start_cond=self._conductivity(tx,ty)
        if start_cond<=0.0:
            self.last_debug={"network_cells":1,"source_charge":charge};return 1
        q=deque([(tx,ty,charge)])
        best={(tx,ty):abs(charge)};processed=0
        while q and processed<int(ELECTRIC_NETWORK_MAX_CELLS):
            x,y,v=q.popleft();processed+=1
            if not self._active(x,y):continue
            cond_here=self._conductivity(x,y)
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if not (0<=nx<self.world.width_tiles and 0<=ny<self.world.height_tiles):continue
                if not self._active(nx,ny):continue
                cond=self._conductivity(nx,ny)
                if cond<=0.0:continue
                # Attenuate by the weaker boundary conductivity.
                boundary=min(max(0.05,cond_here),max(0.05,cond))
                nv=float(v)*(0.78+0.18*boundary)
                if abs(nv)<ELECTRIC_NETWORK_MIN_CHARGE:continue
                if abs(nv)<=best.get((nx,ny),0.0)+1e-9:continue
                best[(nx,ny)]=abs(nv);self._write_charge(nx,ny,nv);q.append((nx,ny,nv))
        self.last_debug={"network_cells":processed,"source_charge":charge}
        return processed


    def apply_charge_bounded(self, tx, ty, charge, radius, water_only=False):
        """Apply one conductive impulse inside a hard tile-radius boundary.

        Electric-ball water shocks use this path so one projectile can never
        energise an entire lake/ocean network.  The search is event-driven and
        bounded to at most (2r+1)^2 cells; no world scan is performed.

        ``water_only`` intentionally ignores wet soil/metal side branches.  A
        water impact therefore remains a local body-water discharge, while a
        dry impact can still use the ordinary conductor network via
        :meth:`apply_charge`.
        """
        tx=int(tx); ty=int(ty); charge=float(charge)
        radius=max(0,int(radius))
        if self.world is None or radius <= 0:
            self._write_charge(tx,ty,charge)
            self.last_debug={"network_cells":1,"source_charge":charge,"radius":radius}
            return ((tx,ty),)

        def eligible(x,y):
            if not (0 <= x < self.world.width_tiles and 0 <= y < self.world.height_tiles):
                return False
            if not self._active(x,y):
                return False
            if water_only and self.env.water_amount(x,y) <= 0.02:
                return False
            return self._conductivity(x,y) > 0.0

        if not eligible(tx,ty):
            self._write_charge(tx,ty,charge)
            self.last_debug={"network_cells":1,"source_charge":charge,"radius":radius}
            return ((tx,ty),)

        q=deque([(tx,ty,charge)])
        best={(tx,ty):abs(charge)}
        ordered=[]
        while q:
            x,y,v=q.popleft()
            ordered.append((x,y))
            cond_here=max(0.05,self._conductivity(x,y))
            for ox,oy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
                nx=x+ox; ny=y+oy
                if max(abs(nx-tx),abs(ny-ty)) > radius:
                    continue
                if not eligible(nx,ny):
                    continue
                cond=max(0.05,self._conductivity(nx,ny))
                # A small per-hop falloff keeps the edge visibly weaker while
                # the hard radius remains the authoritative gameplay limit.
                nv=float(v)*(0.82+0.14*min(cond_here,cond))
                if abs(nv)<ELECTRIC_NETWORK_MIN_CHARGE:
                    continue
                if abs(nv)<=best.get((nx,ny),0.0)+1e-9:
                    continue
                best[(nx,ny)]=abs(nv)
                self._write_charge(nx,ny,nv)
                q.append((nx,ny,nv))

        self.last_debug={"network_cells":len(ordered),"source_charge":charge,"radius":radius}
        return tuple(ordered)

    def lightning_strike(self,tx,ty,energy=LIGHTNING_ENERGY_DEFAULT):
        charge=float(energy)*0.01
        cells=self.apply_charge(tx,ty,charge)
        if self.energy is not None:self.energy.record("electric_in",float(energy))
        return {"tx":int(tx),"ty":int(ty),"energy":float(energy),"network_cells":int(cells),
                "chunk":(int(tx)//CHUNK_SIZE,int(ty)//CHUNK_SIZE)}
