# -*- coding: utf-8 -*-
"""V0.7.4 atmospheric astronomy boundary.

This layer deliberately stays chunk/clock scale.  It does not own water; it
provides solar forcing and day/night information to Weather/Thermal/Hydrology.
Water mass remains conserved by EnvironmentState reservoirs.
"""
from config import CLIMATE_SOLAR_FLUX_MULTIPLIER


class AtmosphericAstronomySystem:
    def __init__(self, time_system=None):
        self.time_system = time_system
        self.last_debug = {
            "daylight": 1.0,
            "hour": 12.0,
        }

    def daylight_factor(self):
        if self.time_system is None:
            return 1.0
        value = max(0.0, min(1.0, float(self.time_system.daylight_factor())))
        self.last_debug["daylight"] = value
        self.last_debug["hour"] = float(getattr(self.time_system, "hour_float", 12.0))
        return value

    def surface_solar_flux(self, profile_id, cloud_cover=0.0):
        """Return normalized 0..1 usable short-wave flux at the surface.

        Desert receives stronger clear-sky forcing; rainforest cloud/humidity
        attenuates it more.  Cloud cover is supplied by WeatherSystem, so
        evaporation and ice melt naturally slow under clouds/night rather than
        being fixed biome timers.
        """
        daylight = self.daylight_factor()
        climate = float(CLIMATE_SOLAR_FLUX_MULTIPLIER.get(str(profile_id), 1.0))
        cloud = max(0.0, min(1.0, float(cloud_cover)))
        attenuation = max(0.08, 1.0 - cloud * 0.78)
        return max(0.0, min(1.0, daylight * climate * attenuation))
