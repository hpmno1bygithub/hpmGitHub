# -*- coding: utf-8 -*-
import math

from config import (
    WEATHER_HZ,
    WEATHER_BACKGROUND_SNOW_HZ,
    WEATHER_PROFILE_SECONDS,
    ATMOSPHERE_CLOUD_FULL_MASS,
    ATMOSPHERE_CLOUD_RAIN_THRESHOLD,
    ATMOSPHERE_PRECIP_EFFICIENCY,
    ATMOSPHERE_PRECIP_EXCLUDED_PROFILES,
    ATMOSPHERE_PRECIP_TARGET_SHARE,
    CHUNK_SIZE,
)
from systems.fixed_scheduler import FixedRateTask
from world.environment_state import WeatherCell
from world.tile_registry import tile_def, AIR, ICE


class WeatherSystem:
    """Weather boundary driven by real cloud water + astronomical daylight.

    V0.7.4.0 removes the old timer-only rainforest rain source.  A climate now
    controls condensation/precipitation efficiency, while actual rain requires
    conserved cloud water produced by AtmosphereSystem.
    """

    def __init__(self, env, chunk_streamer, time_system=None, astronomy_system=None, world=None):
        self.env = env
        self.world = world
        self.chunk_streamer = chunk_streamer
        self.time_system = time_system
        self.astronomy_system = astronomy_system
        self.clock = 0.0
        self.task = FixedRateTask(WEATHER_HZ, max_steps=2)
        self.background_snow_task = FixedRateTask(
            WEATHER_BACKGROUND_SNOW_HZ, max_steps=1, phase=0.5
        )
        self._precip_density_cache = None

    def update(self, dt):
        self.clock += dt
        for step in self.task.consume(dt):
            self._step(step)
        for step in self.background_snow_task.consume(dt):
            self._background_snow_cover_step(step)

    def _profile(self, cx, cy=0):
        return self.env.climate_at_chunk(cx, cy, "temperate")

    def _daylight(self):
        if self.time_system is None:
            return 1.0
        return self.time_system.daylight_factor()

    def _precip_group(self, cx, profile=None):
        profile = str(profile or self._profile(cx, 0))
        if profile in ATMOSPHERE_PRECIP_EXCLUDED_PROFILES:
            return "excluded"
        try:
            biome = self.env.biome_at_chunk(cx, 0, "")
        except Exception:
            biome = str(getattr(self.env, "biome", {}).get((int(cx), 0), ""))
        if biome == "rainforest":
            return "rainforest"
        if biome == "desert":
            return "desert"
        return "temperate"

    def _precip_density(self):
        """Area-normalized capture rates for the requested 60/10/30 split."""
        if self._precip_density_cache is not None:
            return self._precip_density_cache
        groups = {"rainforest": 0, "desert": 0, "temperate": 0}
        biome_map = getattr(self.env, "biome", {})
        xs = sorted({int(cx) for (cx, cy) in biome_map.keys() if int(cy) == 0})
        if not xs:
            self._precip_density_cache = {}
            return self._precip_density_cache
        for cx in xs:
            group = self._precip_group(cx)
            if group in groups:
                groups[group] += 1
        raw = {}
        for group, count in groups.items():
            share = float(ATMOSPHERE_PRECIP_TARGET_SHARE.get(group, 0.0))
            if count > 0 and share > 0.0:
                raw[group] = share / float(count)
        peak = max(raw.values()) if raw else 1.0
        self._precip_density_cache = {
            group: max(0.0, min(1.0, value / max(1e-9, peak)))
            for group, value in raw.items()
        }
        return self._precip_density_cache

    def _precip_efficiency(self, cx, profile):
        group = self._precip_group(cx, profile)
        if group == "excluded":
            return 0.0
        density = self._precip_density()
        if density:
            return float(density.get(group, 0.0))
        return float(ATMOSPHERE_PRECIP_EFFICIENCY.get(profile, 0.70))

    def _base_conditions(self, profile, wobble):
        if profile == "rainforest":
            return 24.0 + 2.0*wobble, 0.18, 0.18 + 0.20*wobble
        if profile == "desert":
            return 32.0 + 6.0*wobble, 0.03, 0.30 + 0.20*wobble
        if profile == "alpine":
            return -7.0 + 4.0*wobble, 0.10, 0.24 + 0.24*wobble
        if profile == "polar":
            return -16.0 + 3.0*wobble, 0.12, 0.26 + 0.22*wobble
        return 20.0 + 5.0*wobble, 0.08, 0.14 + 0.16*wobble

    def _weather_values(self, cx, cy):
        profile = self._profile(cx, cy)
        phase = (
            self.clock / max(1.0, WEATHER_PROFILE_SECONDS)
            + int(cx) * 0.71
            + int(cy) * 0.33
        )
        wobble = math.sin(phase * math.tau) * 0.5 + 0.5
        temp, base_cloud, base_wind = self._base_conditions(profile, wobble)

        cloud_mass = max(0.0, float(self.env.cloud_amount(cx, 0)))
        cloud_fill = min(1.0, cloud_mass / max(0.01, float(ATMOSPHERE_CLOUD_FULL_MASS)))
        cloud = max(0.0, min(1.0, base_cloud + cloud_fill * 0.88))

        # V0.7.5.5: cloud condensed from desert evaporation is visible but not
        # precipitable while it is still over desert. It must first advect into
        # a non-desert column, where AtmosphereSystem removes the fresh tag.
        fresh_desert = 0.0
        try:
            fresh_desert = float(self.env.cloud_desert_fresh_amount(cx, 0))
        except Exception:
            pass
        precipitable_cloud = max(0.0, cloud_mass - fresh_desert)
        threshold = float(ATMOSPHERE_CLOUD_RAIN_THRESHOLD)
        rain_pool = max(0.0, precipitable_cloud - threshold)
        efficiency = self._precip_efficiency(cx, profile)
        rain = max(0.0, min(
            1.0,
            (rain_pool / max(0.25, float(ATMOSPHERE_CLOUD_FULL_MASS) - threshold))
            * efficiency
            * (0.75 + 0.25*wobble),
        ))

        # V0.7.5.3 rainfall routing excludes alpine/polar completely. Their
        # visible snow/frost is maintained by the cold-surface deposition
        # system; cloud water is kept on / returned to eligible climates.
        if profile in ATMOSPHERE_PRECIP_EXCLUDED_PROFILES:
            rain = 0.0

        if self.astronomy_system is not None:
            sunlight = self.astronomy_system.surface_solar_flux(profile, cloud)
        else:
            daylight = self._daylight()
            sunlight = max(0.0, min(1.0, daylight * (1.0 - cloud * 0.78)))
        return profile, rain, cloud, sunlight, temp, base_wind


    def _surface_snow_step(self, cx, cy, profile, rain, cloud, temp, dt):
        """Grow a thin visual snow rim on newly exposed cold terrain.

        The original authored SNOW_DIRT already carries a white top color.
        This sparse cover is for *newly exposed* dirt/stone after mining.
        It accumulates gradually in alpine/polar air and is removed when that
        tile is excavated or is no longer the sky-facing surface.
        """
        if self.world is None:
            return

        cx = int(cx)
        profile = str(profile)
        cold = profile in ("alpine", "polar") and float(temp) <= 0.5
        start_tx = max(0, cx * CHUNK_SIZE)
        end_tx = min(self.world.width_tiles, start_tx + CHUNK_SIZE)

        # First discard stale caps in this horizontal chunk. This is what
        # makes the old cap disappear immediately after the tile is mined.
        for key in tuple(self.env.snow_cover.keys()):
            tx, ty = int(key[0]), int(key[1])
            if not (start_tx <= tx < end_tx):
                continue
            if self.world.get_tile(tx, ty) == AIR:
                self.env.snow_cover.pop(key, None)
                continue
            surface_ty = self.world.first_solid_row(tx)
            if surface_ty is None or int(surface_ty) != ty:
                self.env.snow_cover.pop(key, None)

        if not cold:
            # Warm air melts only the cosmetic rim. Real ICE remains governed
            # by ThermalSystem and the user's fire-only snow-region rule.
            for key in tuple(self.env.snow_cover.keys()):
                tx, _ty = key
                if start_tx <= int(tx) < end_tx:
                    left = max(0.0, float(self.env.snow_cover.get(key, 0.0)) - float(dt) * 0.16)
                    if left <= 0.01:
                        self.env.snow_cover.pop(key, None)
                    else:
                        self.env.snow_cover[key] = left
            return

        # Cold deposition is deliberately visible after a short delay rather
        # than instantly. Cloud/rain (snow-equivalent precipitation) speeds it
        # up, while clear cold air still allows slow frost/snow deposition.
        rate = 0.055 + 0.095 * max(0.0, min(1.0, float(cloud))) + 0.22 * max(0.0, min(1.0, float(rain)))
        for tx in range(start_tx, end_tx):
            ty = self.world.first_solid_row(tx)
            if ty is None:
                continue
            ty = int(ty)
            tile_id = self.world.get_tile(tx, ty)
            if tile_id in (AIR, ICE):
                continue
            # Fire on/just above the surface prevents accumulation locally.
            hot = False
            for fy in (ty, ty - 1):
                if (tx, fy) in self.env.fire:
                    hot = True
                    break
            key = (tx, ty)
            if hot:
                left = max(0.0, float(self.env.snow_cover.get(key, 0.0)) - float(dt) * 0.55)
                if left <= 0.01:
                    self.env.snow_cover.pop(key, None)
                else:
                    self.env.snow_cover[key] = left
                continue
            current = max(0.0, min(1.0, float(self.env.snow_cover.get(key, 0.0))))
            self.env.snow_cover[key] = min(1.0, current + rate * float(dt))

    def _background_snow_cover_step(self, dt):
        """Advance existing snow/frost caps outside active chunks sparsely.

        We intentionally do not scan the whole world. Only tiles that already
        have a snow_cover state are touched, so a cap that began while visible
        keeps accumulating/melting after the player walks away.
        """
        if self.world is None or not self.env.snow_cover:
            return
        active_cx = {int(cx) for cx, _cy in self.chunk_streamer.active_chunks}
        for key in tuple(self.env.snow_cover.keys()):
            tx, ty = int(key[0]), int(key[1])
            cx = tx // CHUNK_SIZE
            if cx in active_cx:
                continue
            if not (0 <= tx < self.world.width_tiles and 0 <= ty < self.world.height_tiles):
                self.env.snow_cover.pop(key, None)
                continue
            if self.world.get_tile(tx, ty) == AIR:
                self.env.snow_cover.pop(key, None)
                continue
            surface_ty = self.world.first_solid_row(tx)
            if surface_ty is None or int(surface_ty) != ty:
                self.env.snow_cover.pop(key, None)
                continue

            profile, rain, cloud, _sunlight, temp, _wind = self._weather_values(cx, 0)
            current = max(0.0, min(1.0, float(self.env.snow_cover.get(key, 0.0))))
            hot = any((tx, fy) in self.env.fire for fy in (ty, ty - 1))
            cold = profile in ("alpine", "polar") and float(temp) <= 0.5
            if hot:
                current = max(0.0, current - float(dt) * 0.55)
            elif cold:
                rate = (
                    0.055
                    + 0.095 * max(0.0, min(1.0, float(cloud)))
                    + 0.22 * max(0.0, min(1.0, float(rain)))
                )
                current = min(1.0, current + rate * float(dt))
            else:
                current = max(0.0, current - float(dt) * 0.16)

            if current <= 0.01:
                self.env.snow_cover.pop(key, None)
            else:
                self.env.snow_cover[key] = current

    def _step(self, dt):
        for cx, cy in self.chunk_streamer.active_chunks:
            profile, rain, cloud, sunlight, temp, base_wind = self._weather_values(cx, cy)
            existing = self.env.weather.get((cx, cy))
            wind_x = float(getattr(existing, "wind_x", 0.0)) if existing else 0.0
            wind_y = float(getattr(existing, "wind_y", 0.0)) if existing else 0.0
            self.env.weather[(cx, cy)] = WeatherCell(
                profile=profile,
                rain=rain,
                cloud=cloud,
                sunlight=sunlight,
                temperature=temp,
                wind=base_wind,
                wind_x=wind_x,
                wind_y=wind_y,
            )
            self._surface_snow_step(cx, cy, profile, rain, cloud, temp, dt)

    def weather_at_chunk(self, cx, cy):
        cell = self.env.weather.get((cx, cy))
        if cell is None:
            profile, rain, cloud, sunlight, temp, base_wind = self._weather_values(cx, cy)
            cell = WeatherCell(
                profile=profile,
                rain=rain,
                cloud=cloud,
                sunlight=sunlight,
                temperature=temp,
                wind=base_wind,
            )
            self.env.weather[(cx, cy)] = cell
        return cell
