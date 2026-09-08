# -*- coding: utf-8 -*-
from config import (
    ATMOSPHERE_HZ,
    ATMOSPHERE_VAPOR_DIFFUSION_RATE,
    ATMOSPHERE_VAPOR_ADVECTION_RATE,
    ATMOSPHERE_MIN_TRANSFER,
    ATMOSPHERE_CLOUD_ADVECTION_RATE,
    ATMOSPHERE_CLOUD_DIFFUSION_RATE,
    ATMOSPHERE_CLOUD_MIN_TRANSFER,
    ATMOSPHERE_PREVAILING_EAST_SPEED,
    ATMOSPHERE_PRECIP_EXCLUDED_PROFILES,
    ATMOSPHERE_CONDENSE_THRESHOLD,
    ATMOSPHERE_CONDENSE_RATE,
    CHUNK_SIZE,
    WIND_MAX_SPEED,
)
from systems.fixed_scheduler import FixedRateTask


class AtmosphereSystem:
    """Conservative chunk-scale vapor -> cloud transport.

    V0.7.4.0 adds a real condensed cloud-water reservoir.  Water can now move:

        liquid/soil -> visible steam -> vapor -> cloud -> rain -> liquid/soil

    Vapor remains keyed by full (cx, cy), because caves and local evaporation
    may feed it at different heights.  Cloud water is keyed as (cx, 0): a
    horizontal atmospheric column that can keep drifting even when terrain
    chunks below it are sleeping.
    """

    def __init__(self, world, env, chunk_streamer, wind_system):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.wind_system = wind_system
        self.task = FixedRateTask(ATMOSPHERE_HZ, max_steps=2)
        self.max_cx = max(0, (self.world.width_tiles - 1) // CHUNK_SIZE)
        self.max_cy = max(0, (self.world.height_tiles - 1) // CHUNK_SIZE)
        self.last_debug = {
            "diffused": 0.0,
            "advected": 0.0,
            "condensed": 0.0,
            "cloud_advected": 0.0,
            "cloud_mass": 0.0,
        }

    def _valid_chunk(self, cx, cy):
        return 0 <= int(cx) <= self.max_cx and 0 <= int(cy) <= self.max_cy

    def update(self, dt):
        for step in self.task.consume(dt):
            self._step(step)

    # ------------------------------------------------------------------
    # Vapor transport (active neighborhood only)
    # ------------------------------------------------------------------
    def _diffusion_pass(self, working, dt, chunks):
        total_moved = 0.0
        for cx, cy in sorted(chunks):
            for nx, ny in ((cx + 1, cy), (cx, cy + 1)):
                if not self._valid_chunk(nx, ny):
                    continue
                a_key, b_key = (cx, cy), (nx, ny)
                a = float(working.get(a_key, 0.0))
                b = float(working.get(b_key, 0.0))
                difference = a - b
                if abs(difference) <= ATMOSPHERE_MIN_TRANSFER:
                    continue
                requested = (
                    abs(difference)
                    * 0.5
                    * float(ATMOSPHERE_VAPOR_DIFFUSION_RATE)
                    * float(dt)
                )
                if requested <= ATMOSPHERE_MIN_TRANSFER:
                    continue
                source, target = (a_key, b_key) if difference > 0 else (b_key, a_key)
                available = float(working.get(source, 0.0))
                moved = min(available, requested)
                if moved <= ATMOSPHERE_MIN_TRANSFER:
                    continue
                working[source] = available - moved
                working[target] = float(working.get(target, 0.0)) + moved
                total_moved += moved
        return total_moved

    def _advection_pass(self, working, dt, chunks):
        total_moved = 0.0
        for cx, cy in sorted(chunks):
            source = (cx, cy)
            available = float(working.get(source, 0.0))
            if available <= ATMOSPHERE_MIN_TRANSFER:
                continue
            vx, vy = self.wind_system.wind_at_chunk(cx, cy)
            abs_x, abs_y = abs(vx), abs(vy)
            if abs_x + abs_y <= 1e-9:
                continue
            if abs_x >= abs_y:
                dx, dy = (1 if vx > 0 else -1), 0
                directional_speed = abs_x
            else:
                dx, dy = 0, (1 if vy > 0 else -1)
                directional_speed = abs_y
            target = (cx + dx, cy + dy)
            if not self._valid_chunk(*target):
                continue
            speed_factor = min(1.0, directional_speed / max(1.0, WIND_MAX_SPEED))
            moved = min(
                available,
                available
                * float(ATMOSPHERE_VAPOR_ADVECTION_RATE)
                * speed_factor
                * float(dt),
            )
            if moved <= ATMOSPHERE_MIN_TRANSFER:
                continue
            working[source] = available - moved
            working[target] = float(working.get(target, 0.0)) + moved
            total_moved += moved
        return total_moved

    def _transport_vapor(self, dt, active):
        if not active:
            return 0.0, 0.0
        working_keys = set(active)
        for cx, cy in tuple(active):
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if self._valid_chunk(nx, ny):
                    working_keys.add((nx, ny))
        working = {key: float(self.env.vapor.get(key, 0.0)) for key in working_keys}
        before = sum(working.values())
        diffused = self._diffusion_pass(working, dt, active)
        advected = self._advection_pass(working, dt, active)
        for key in working_keys:
            amount = float(working.get(key, 0.0))
            if amount > ATMOSPHERE_MIN_TRANSFER:
                self.env.vapor[key] = amount
            else:
                self.env.vapor.pop(key, None)
        after = sum(float(self.env.vapor.get(k, 0.0)) for k in working_keys)
        drift = before - after
        if abs(drift) > 1e-9 and working_keys:
            key = next(iter(working_keys))
            self.env.vapor[key] = max(0.0, float(self.env.vapor.get(key, 0.0)) + drift)
        return diffused, advected

    # ------------------------------------------------------------------
    # Vapor -> cloud condensation
    # ------------------------------------------------------------------
    def _take_vapor_column(self, cx, amount):
        remaining = max(0.0, float(amount))
        if remaining <= 0.0:
            return 0.0
        # Prefer high atmospheric keys first, then cave vapor.  Total transfer
        # remains conservative regardless of ordering.
        keys = sorted(
            [k for k in tuple(self.env.vapor.keys()) if int(k[0]) == int(cx)],
            key=lambda k: int(k[1]),
        )
        moved = 0.0
        for key in keys:
            if remaining <= 1e-12:
                break
            have = max(0.0, float(self.env.vapor.get(key, 0.0)))
            take = min(have, remaining)
            left = have - take
            if left <= ATMOSPHERE_MIN_TRANSFER:
                self.env.vapor.pop(key, None)
            else:
                self.env.vapor[key] = left
            moved += take
            remaining -= take
        return moved

    def _condense_clouds(self, dt, active):
        xs = {int(cx) for cx, _cy in active}
        xs.update(int(cx) for cx, _cy in self.env.vapor.keys())
        xs.update(int(cx) for cx, _cy in getattr(self.env, "cloud_water", {}).keys())
        condensed = 0.0
        for cx in sorted(x for x in xs if 0 <= x <= self.max_cx):
            profile = self.env.climate_at_chunk(cx, 0, "temperate")
            threshold = float(ATMOSPHERE_CONDENSE_THRESHOLD.get(profile, 10.5))
            rate = float(ATMOSPHERE_CONDENSE_RATE.get(profile, 0.20))
            column_vapor = sum(
                max(0.0, float(v))
                for (vcx, _vcy), v in self.env.vapor.items()
                if int(vcx) == cx
            )
            excess = max(0.0, column_vapor - threshold)
            if excess <= ATMOSPHERE_MIN_TRANSFER:
                continue
            requested = min(excess, excess * rate * float(dt))
            moved = self._take_vapor_column(cx, requested)
            if moved > 0.0:
                # Desert-condensed cloud is visible immediately but is tagged
                # fresh. It must advect out of the desert before precipitation
                # can consume it, preventing evaporation -> same-place rain.
                self.env.add_cloud(
                    cx, 0, moved,
                    desert_fresh=(str(profile) == "desert"),
                )
                condensed += moved
        return condensed

    # ------------------------------------------------------------------
    # Cloud transport (sparse and world-persistent)
    # ------------------------------------------------------------------
    def _is_desert_column(self, cx):
        try:
            biome = str(self.env.biome_at_chunk(int(cx), 0, ""))
        except Exception:
            biome = str(getattr(self.env, "biome", {}).get((int(cx), 0), ""))
        if biome:
            return biome == "desert"
        return self.env.climate_at_chunk(int(cx), 0, "temperate") == "desert"

    def _prevailing_cloud_direction(self):
        centers = {}
        counts = {}
        for (cx, cy), biome in getattr(self.env, "biome", {}).items():
            if int(cy) != 0:
                continue
            biome = str(biome)
            centers[biome] = centers.get(biome, 0.0) + float(cx)
            counts[biome] = counts.get(biome, 0) + 1
        for biome, total in tuple(centers.items()):
            centers[biome] = total / max(1, int(counts.get(biome, 1)))
        desert_x = centers.get("desert")
        if desert_x is not None:
            for target_name in ("rainforest", "swamp", "ocean", "lake", "plains"):
                target_x = centers.get(target_name)
                if target_x is None:
                    continue
                delta = float(target_x) - float(desert_x)
                if abs(delta) > 1e-6:
                    return 1 if delta > 0.0 else -1
        return -1

    def _transport_clouds(self, dt):
        keys = sorted(getattr(self.env, "cloud_water", {}).keys())
        if not keys:
            getattr(self.env, "cloud_desert_fresh", {}).clear()
            return 0.0

        working = {
            int(cx): max(0.0, float(amount))
            for (cx, _cy), amount in self.env.cloud_water.items()
        }
        fresh = {
            int(cx): max(0.0, min(
                float(working.get(int(cx), 0.0)),
                float(getattr(self.env, "cloud_desert_fresh", {}).get((int(cx), 0), 0.0)),
            ))
            for cx in working
        }
        before = sum(working.values())
        moved_total = 0.0

        def move_mass(src, dst, amount):
            """Move cloud and its fresh-desert tag conservatively.

            Fresh desert cloud becomes mature the first time that tagged mass
            crosses into a non-desert column. Mature cloud never becomes fresh
            merely by entering the desert later, so desert rainfall can happen
            only after prior transport through an eligible non-desert region.
            """
            amount = max(0.0, min(float(amount), float(working.get(src, 0.0))))
            if amount <= ATMOSPHERE_CLOUD_MIN_TRANSFER:
                return 0.0
            src_before = max(0.0, float(working.get(src, 0.0)))
            src_fresh = max(0.0, min(src_before, float(fresh.get(src, 0.0))))
            fresh_move = amount * (src_fresh / src_before) if src_before > 1e-12 else 0.0

            working[src] = src_before - amount
            working[dst] = float(working.get(dst, 0.0)) + amount
            fresh[src] = max(0.0, src_fresh - fresh_move)

            if self._is_desert_column(dst):
                fresh[dst] = float(fresh.get(dst, 0.0)) + fresh_move
            else:
                # Leaving desert is the maturation event. The moved tagged
                # fraction becomes normal precipitable cloud over this column.
                fresh.setdefault(dst, 0.0)
            return amount

        # Gentle diffusion prevents hard chunk-edge cloud blocks. It also
        # counts as genuine transport for fresh desert cloud.
        for cx in sorted(tuple(working.keys())):
            nx = cx + 1
            if nx > self.max_cx:
                continue
            a, b = float(working.get(cx, 0.0)), float(working.get(nx, 0.0))
            diff = a - b
            requested = abs(diff) * 0.5 * float(ATMOSPHERE_CLOUD_DIFFUSION_RATE) * float(dt)
            if requested <= ATMOSPHERE_CLOUD_MIN_TRANSFER:
                continue
            src, dst = (cx, nx) if diff > 0 else (nx, cx)
            moved_total += move_mass(src, dst, requested)

        # Upper-air cloud drift follows the same authored surface circulation:
        # desert -> rainforest/humid side. Local chunk wind can still provide
        # magnitude, but the default fallback direction is no longer hardcoded
        # eastward.
        snapshot = dict(working)
        excluded = set(str(v) for v in ATMOSPHERE_PRECIP_EXCLUDED_PROFILES)
        fallback_direction = self._prevailing_cloud_direction()
        for cx, available in sorted(snapshot.items()):
            if available <= ATMOSPHERE_CLOUD_MIN_TRANSFER:
                continue

            source_profile = self.env.climate_at_chunk(cx, 0, "temperate")
            vx, _vy = self.wind_system.wind_at_chunk(cx, 0)

            if source_profile in excluded:
                direction = -1 if cx > 0 else 1
                speed = max(abs(float(vx)), float(ATMOSPHERE_PREVAILING_EAST_SPEED))
            elif abs(float(vx)) > 1e-3:
                direction = -1 if float(vx) < 0.0 else 1
                speed = max(abs(float(vx)), float(ATMOSPHERE_PREVAILING_EAST_SPEED))
            else:
                direction = int(fallback_direction)
                speed = float(ATMOSPHERE_PREVAILING_EAST_SPEED)

            nx = cx + direction
            if not (0 <= nx <= self.max_cx):
                continue

            target_profile = self.env.climate_at_chunk(nx, 0, "temperate")
            if source_profile not in excluded and target_profile in excluded:
                # Keep warm-rain cloud out of alpine/polar columns.
                continue

            speed_factor = 0.35 + 0.65 * min(1.0, speed / max(1.0, WIND_MAX_SPEED))
            requested = (
                available
                * float(ATMOSPHERE_CLOUD_ADVECTION_RATE)
                * speed_factor
                * float(dt)
            )
            moved_total += move_mass(cx, nx, requested)

        self.env.cloud_water.clear()
        getattr(self.env, "cloud_desert_fresh", {}).clear()
        for cx, amount in working.items():
            if amount > ATMOSPHERE_CLOUD_MIN_TRANSFER:
                key = (int(cx), 0)
                self.env.cloud_water[key] = float(amount)
                f = max(0.0, min(float(amount), float(fresh.get(cx, 0.0))))
                if f > ATMOSPHERE_CLOUD_MIN_TRANSFER:
                    self.env.cloud_desert_fresh[key] = f

        after = sum(self.env.cloud_water.values())
        drift = before - after
        if abs(drift) > 1e-9 and self.env.cloud_water:
            # Correct only mature mass for floating-point drift so fresh tags
            # never grow spuriously.
            key = next(iter(self.env.cloud_water))
            self.env.cloud_water[key] = max(0.0, self.env.cloud_water[key] + drift)
            try:
                self.env.set_cloud_desert_fresh(key[0], self.env.cloud_desert_fresh.get(key, 0.0))
            except Exception:
                pass
        return moved_total

    def _update_evaporation_haze(self, dt):
        table = getattr(self.env, "evaporation_haze", None)
        if not table:
            return
        decay = max(0.0, 1.0 - 0.34 * float(dt))
        for key in tuple(table.keys()):
            value = max(0.0, float(table.get(key, 0.0))) * decay
            if value <= 0.015:
                table.pop(key, None)
            else:
                table[key] = value

    def _step(self, dt):
        self._update_evaporation_haze(dt)
        active = set(self.chunk_streamer.active_chunks)
        if not active and not self.env.vapor and not getattr(self.env, "cloud_water", {}):
            self.last_debug = {"diffused": 0.0, "advected": 0.0, "condensed": 0.0, "cloud_advected": 0.0, "cloud_mass": 0.0}
            return
        diffused, advected = self._transport_vapor(dt, active)
        condensed = self._condense_clouds(dt, active)
        cloud_advected = self._transport_clouds(dt)
        self.last_debug = {
            "diffused": float(diffused),
            "advected": float(advected),
            "condensed": float(condensed),
            "cloud_advected": float(cloud_advected),
            "cloud_mass": float(sum(getattr(self.env, "cloud_water", {}).values())),
            "active_chunks": len(active),
        }
