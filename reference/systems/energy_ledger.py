# -*- coding: utf-8 -*-
from config import (
    ENERGY_REFERENCE_KELVIN,
    ENERGY_AUDIT_TOLERANCE,
)
from world.tile_registry import tile_def


class EnergyLedger:
    """Stage-1 gameplay energy accounting.

    Matter is audited as a closed ledger. Energy is intentionally treated as
    an OPEN system because sunlight and climate boundaries can inject/remove
    heat. The ledger therefore reports current stored energy plus cumulative
    named fluxes instead of pretending the world is energetically closed.
    """

    def __init__(self, world, env):
        self.world = world
        self.env = env
        self.cumulative = {
            "solar_in": 0.0,
            "fire_heat_in": 0.0,
            "climate_exchange": 0.0,
            "wind_work": 0.0,
            "phase_change": 0.0,
            "electric_in": 0.0,
        }
        self.baseline_stored = None

    def reset(self):
        for key in list(self.cumulative.keys()):
            self.cumulative[key] = 0.0
        self.baseline_stored = None

    def record(self, key, amount):
        self.cumulative[key] = (
            float(self.cumulative.get(key, 0.0))
            + float(amount)
        )

    def thermal_stored(self):
        """Sparse diagnostic energy proxy.

        V0.7.0 intentionally avoids width*height scans. Only material tiles
        and explicitly materialized thermal cells contribute to this debug
        ledger; gameplay physics never depends on this audit value.
        """
        total=0.0
        seen=set()
        for tx,ty,tile_id in self.world.iter_non_air_tiles():
            seen.add((tx,ty)); td=tile_def(tile_id)
            temp_k=max(0.0,self.env.temperature_at(tx,ty,20.0)+ENERGY_REFERENCE_KELVIN)
            total += max(0.05,float(td.heat_capacity))*temp_k
        for (tx,ty),temp in self.env.temperature.items():
            if (tx,ty) in seen: continue
            td=tile_def(self.world.get_tile(tx,ty))
            total += max(0.05,float(td.heat_capacity))*max(0.0,float(temp)+ENERGY_REFERENCE_KELVIN)
        for (tx,ty),amount in self.env.water.items():
            temp_k=max(0.0,self.env.water_temperature_at(tx,ty,20.0)+ENERGY_REFERENCE_KELVIN)
            total += max(0.0,float(amount))*4.18*temp_k
        for fire in self.env.fire.values():
            total += max(0.0,float(getattr(fire,"temperature_c",620.0))+ENERGY_REFERENCE_KELVIN)*max(0.0,float(fire.intensity))*0.12
        return total

    def wind_kinetic(self):
        total = 0.0
        for vector in getattr(self.env, "wind", {}).values():
            try:
                vx, vy = vector
            except Exception:
                continue
            total += 0.5 * (float(vx) ** 2 + float(vy) ** 2) * 0.001
        return total

    def capture_baseline(self):
        self.baseline_stored = self.thermal_stored() + self.wind_kinetic()

    def snapshot(self):
        thermal = self.thermal_stored()
        wind = self.wind_kinetic()
        stored = thermal + wind
        delta = (
            0.0
            if self.baseline_stored is None
            else stored - self.baseline_stored
        )
        return {
            "thermal": thermal,
            "wind": wind,
            "stored": stored,
            "stored_delta": delta,
            "flux": dict(self.cumulative),
            "near_baseline": abs(delta) <= ENERGY_AUDIT_TOLERANCE,
        }
    def export_state(self):
        return {
            "cumulative": dict(self.cumulative),
            "baseline_stored": self.baseline_stored,
        }

    def import_state(self, data):
        if not isinstance(data, dict):
            return
        cumulative = data.get("cumulative", {})
        if isinstance(cumulative, dict):
            for key, value in cumulative.items():
                self.cumulative[str(key)] = float(value)
        baseline = data.get("baseline_stored")
        self.baseline_stored = (
            None if baseline is None else float(baseline)
        )

