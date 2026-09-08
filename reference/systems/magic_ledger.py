# -*- coding: utf-8 -*-


class MagicLedger:
    """Tracks only matter/energy created by magic.

    This is intentionally NOT a global environment conservation ledger.

    Boundary:
        mana -> spell-created matter/energy

    After creation:
        water -> soil water -> vapor -> ice -> water ...
        fireball energy -> evaporation / heat / ignition ...

    The ledger is primarily an audit/debug layer; gameplay state remains in
    the normal Water/Soil/Thermal systems.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.mana_spent = 0.0

        self.water_created_units = 0.0
        self.ice_created_units = 0.0

        self.water_to_soil_units = 0.0
        self.water_to_free_units = 0.0
        self.water_to_vapor_units = 0.0
        self.water_to_ice_units = 0.0
        self.ice_to_water_units = 0.0

        self.fire_energy_created = 0.0
        self.electric_energy_created = 0.0
        self.fire_energy_evap_used = 0.0
        self.fire_energy_heat_used = 0.0
        self.fire_water_evaporated_units = 0.0

    def cast_water(self, mana, water_units):
        self.mana_spent += float(mana)
        self.water_created_units += float(water_units)

    def cast_ice(self, mana, water_units):
        self.mana_spent += float(mana)
        self.ice_created_units += float(water_units)

    def cast_fire(self, mana, energy_units):
        self.mana_spent += float(mana)
        self.fire_energy_created += float(energy_units)

    def cast_electric(self, mana, energy_units):
        self.mana_spent += float(mana)
        self.electric_energy_created += float(energy_units)

    def record_water_to_soil(self, units):
        self.water_to_soil_units += max(0.0, float(units))

    def record_water_to_free(self, units):
        self.water_to_free_units += max(0.0, float(units))

    def record_water_to_vapor(self, units):
        self.water_to_vapor_units += max(0.0, float(units))

    def record_water_to_ice(self, units):
        self.water_to_ice_units += max(0.0, float(units))

    def record_ice_to_water(self, units):
        self.ice_to_water_units += max(0.0, float(units))

    def record_fire_evaporation(self, water_units, energy_units):
        self.fire_water_evaporated_units += max(0.0, float(water_units))
        self.fire_energy_evap_used += max(0.0, float(energy_units))

    def record_fire_heat(self, energy_units):
        self.fire_energy_heat_used += max(0.0, float(energy_units))

    def snapshot(self):
        return {
            "mana_spent": self.mana_spent,
            "water_created_units": self.water_created_units,
            "ice_created_units": self.ice_created_units,
            "fire_energy_created": self.fire_energy_created,
            "electric_energy_created": self.electric_energy_created,
            "fire_energy_evap_used": self.fire_energy_evap_used,
            "fire_energy_heat_used": self.fire_energy_heat_used,
            "fire_water_evaporated_units": self.fire_water_evaporated_units,
            "water_to_soil_units": self.water_to_soil_units,
            "water_to_free_units": self.water_to_free_units,
            "water_to_vapor_units": self.water_to_vapor_units,
            "water_to_ice_units": self.water_to_ice_units,
            "ice_to_water_units": self.ice_to_water_units,
        }

    def export_state(self):
        return self.snapshot()

    def import_state(self, data):
        if not isinstance(data, dict):
            return

        for key in self.snapshot().keys():
            if key in data:
                setattr(self, key, float(data[key]))
