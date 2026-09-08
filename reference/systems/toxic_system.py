# -*- coding: utf-8 -*-
"""Entity poison exposure from sparse poison-gas cells."""
from config import (
    TILE_SIZE, TOXIC_SYSTEM_HZ, POISON_GAS_EXPOSURE_THRESHOLD,
    POISON_GAS_DOSE_RISE_PER_SEC, POISON_GAS_DOSE_DECAY_PER_SEC,
    POISON_GAS_DAMAGE_MIN_PER_SEC, POISON_GAS_DAMAGE_MAX_PER_SEC,
)
from systems.fixed_scheduler import FixedRateTask


class ToxicSystem:
    def __init__(self, env, player, scene):
        self.env=env
        self.player=player
        self.scene=scene
        self.task=FixedRateTask(TOXIC_SYSTEM_HZ,max_steps=2)

    def _sample_entity_gas(self,obj):
        try:
            h=float(obj.height())
        except Exception:
            h=float(getattr(obj,"height_px",40.0))
        x=float(obj.x)
        points=(
            (x,float(obj.y)-h*0.15),
            (x,float(obj.y)-h*0.50),
            (x,float(obj.y)-h*0.82),
        )
        best=0.0
        for px,py in points:
            tx=int(px//TILE_SIZE); ty=int(py//TILE_SIZE)
            best=max(best,float(self.env.poison_gas.get((tx,ty),0.0)))
        return best

    def _apply(self,obj,dt):
        if bool(getattr(obj,"background_only",False)): return
        if not bool(getattr(obj,"active",True)):
            return
        if not hasattr(obj,"hp"):
            return
        # FIX31: the first visible seconds after load are damage-safe. Clear
        # any transient poison dose on the player so a stale exposure cannot
        # immediately cash out as HP loss after startup.
        if obj is self.player and float(getattr(obj, "startup_damage_grace", 0.0)) > 0.0:
            obj.poison_dose = 0.0
            obj.poisoned = False
            return
        if bool(getattr(obj,"poison_immune",False)):
            obj.poison_dose=0.0;obj.poisoned=False
            return
        gas=self._sample_entity_gas(obj)
        dose=max(0.0,min(1.0,float(getattr(obj,"poison_dose",0.0))))
        if gas >= POISON_GAS_EXPOSURE_THRESHOLD:
            dose=min(1.0,dose+float(dt)*POISON_GAS_DOSE_RISE_PER_SEC*min(1.0,0.45+gas))
        else:
            dose=max(0.0,dose-float(dt)*POISON_GAS_DOSE_DECAY_PER_SEC)
        obj.poison_dose=dose
        obj.poisoned=dose>0.02
        if dose <= 0.0:
            return
        # Continuous exposure builds dose toward the max rate. Short/intermittent
        # contact loses dose between puffs and therefore causes much less damage.
        dps=POISON_GAS_DAMAGE_MIN_PER_SEC+(POISON_GAS_DAMAGE_MAX_PER_SEC-POISON_GAS_DAMAGE_MIN_PER_SEC)*(dose*dose)
        damage=dps*float(dt)
        taker=getattr(obj,"take_damage",None) if obj is self.player else None
        if callable(taker):taker(damage,source_x=obj.x,kind="poison_gas")
        else:obj.hp=max(0.0,float(obj.hp)-damage)
        if obj is not self.player and obj.hp <= 0.0:
            obj.active=False

    def update(self,dt):
        for step in self.task.consume(dt):
            self._apply(self.player,step)
            for obj in tuple(self.scene.entities):
                if obj is self.player:
                    continue
                self._apply(obj,step)
