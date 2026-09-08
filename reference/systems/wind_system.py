# -*- coding: utf-8 -*-
import math

from config import (
    WIND_HZ,
    WIND_TEMP_GRADIENT_GAIN,
    WIND_BASE_WEATHER_GAIN,
    WIND_MAX_SPEED,
    WIND_SMOOTH_RATE,
    CHUNK_SIZE,
    EVAP_UPDRAFT_DECAY_PER_SECOND,
    EVAP_UPDRAFT_MAX_SPEED,
    WIND_FIRE_LIFT_PER_INTENSITY,
    WIND_FIRE_LIFT_TEMP_GAIN,
    WIND_FIRE_LIFT_MAX,
    FIREBALL_LIFT_DECAY_PER_SECOND,
    WIND_WEAK_TARGET_SPEED,
    WIND_MEDIUM_TARGET_SPEED,
    WIND_STRONG_TARGET_SPEED,
    FIRE_STACK_LIFT_L1,
    FIRE_STACK_LIFT_L2,
    FIRE_STACK_LIFT_L3,
)
from systems.fixed_scheduler import FixedRateTask


class WindSystem:
    """Chunk wind driven primarily by temperature gradients.

    A warmer neighboring chunk acts as a low-pressure proxy, so the vector
    points broadly toward warmer air. This is a gameplay approximation, but
    unlike the old scalar WeatherCell.wind it has direction and is derived
    from the thermal field.
    """

    def __init__(self, world, env, chunk_streamer, weather_system, energy=None):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.weather_system = weather_system
        self.energy = energy
        self.task = FixedRateTask(WIND_HZ, max_steps=2)

    def _chunk_mean_temp(self, cx, cy):
        sx = max(0, cx * CHUNK_SIZE)
        sy = max(0, cy * CHUNK_SIZE)
        ex = min(self.world.width_tiles, sx + CHUNK_SIZE)
        ey = min(self.world.height_tiles, sy + CHUNK_SIZE)

        total = 0.0
        count = 0
        for ty in range(sy, ey):
            for tx in range(sx, ex):
                total += self.env.temperature_at(tx, ty, 20.0)
                count += 1

        return total / max(1, count)

    def _static_fire_lift(
        self,
        cx,
        cy,
    ):
        lift = 0.0

        for (
            tx,
            ty
        ), fire in self.env.fire.items():
            if (
                tx // CHUNK_SIZE != cx
                or ty // CHUNK_SIZE != cy
            ):
                continue

            intensity = max(
                0.0,
                float(
                    getattr(
                        fire,
                        "intensity",
                        0.0,
                    )
                ),
            )

            temp_c = max(
                0.0,
                float(
                    getattr(
                        fire,
                        "temperature_c",
                        0.0,
                    )
                ),
            )

            temp_factor = (
                1.0
                + min(
                    1.5,
                    temp_c / 700.0
                )
                * WIND_FIRE_LIFT_TEMP_GAIN
            )

            stack_level = max(
                1,
                min(
                    3,
                    int(
                        getattr(
                            fire,
                            "stack_level",
                            1,
                        )
                    ),
                ),
            )

            if stack_level == 1:
                stack_lift = FIRE_STACK_LIFT_L1
            elif stack_level == 2:
                stack_lift = FIRE_STACK_LIFT_L2
            else:
                stack_lift = FIRE_STACK_LIFT_L3

            lift += (
                intensity
                * WIND_FIRE_LIFT_PER_INTENSITY
                * temp_factor
                * stack_lift
            )

        return min(
            WIND_FIRE_LIFT_MAX,
            lift,
        )

    def _chunk_fully_underground(self, cx, cy):
        """Return True when the whole chunk lies below the outdoor surface.

        Ambient weather wind is not simulated horizontally in deep cave
        chunks.  Fire/combustion lift is still allowed through target_y.
        """
        top_row = int(cy) * CHUNK_SIZE
        start_x = max(0, int(cx) * CHUNK_SIZE)
        end_x = min(self.world.width_tiles, start_x + CHUNK_SIZE)
        if start_x >= end_x:
            return False
        for tx in range(start_x, end_x):
            surface = self.world.first_solid_row(tx)
            if surface is None or int(surface) >= top_row:
                return False
        return True

    def _biome_chunk_centers(self):
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
        return centers

    def _prevailing_day_direction(self):
        """Stable daytime surface wind direction.

        User rule: daytime flow goes from desert toward rainforest. If a
        dedicated rainforest belt is unavailable, fall back to the next humid
        region on the map (swamp/ocean/lake/plains). This intentionally
        removes the old pseudo-random left/right flipping.
        """
        centers = self._biome_chunk_centers()
        desert_x = centers.get("desert")
        if desert_x is not None:
            for target_name in ("rainforest", "swamp", "ocean", "lake", "plains"):
                target_x = centers.get(target_name)
                if target_x is None:
                    continue
                delta = float(target_x) - float(desert_x)
                if abs(delta) > 1e-6:
                    return 1.0 if delta > 0.0 else -1.0
        return -1.0

    def _horizontal_target_speed(
        self,
        weather_strength,
        gradient_strength,
    ):
        strength = max(
            0.0,
            min(
                1.0,
                weather_strength
                + gradient_strength,
            ),
        )

        if strength < 0.34:
            # Weak wind.
            local = strength / 0.34
            return (
                WIND_WEAK_TARGET_SPEED
                * (
                    0.55
                    + 0.45
                    * local
                )
            )

        if strength < 0.68:
            local = (
                strength
                - 0.34
            ) / 0.34

            return (
                WIND_WEAK_TARGET_SPEED
                + (
                    WIND_MEDIUM_TARGET_SPEED
                    - WIND_WEAK_TARGET_SPEED
                )
                * local
            )

        local = (
            strength
            - 0.68
        ) / 0.32

        return (
            WIND_MEDIUM_TARGET_SPEED
            + (
                WIND_STRONG_TARGET_SPEED
                - WIND_MEDIUM_TARGET_SPEED
            )
            * min(
                1.0,
                local,
            )
        )

    def update(self, dt):
        for step in self.task.consume(dt):
            self._step(step)

    def _step(
        self,
        dt,
    ):
        if not self.chunk_streamer.active_chunks:
            return

        cache = {}

        def temp(
            cx,
            cy,
        ):
            key = (
                cx,
                cy,
            )

            if key not in cache:
                cache[
                    key
                ] = self._chunk_mean_temp(
                    cx,
                    cy,
                )

            return cache[
                key
            ]

        for cx, cy in self.chunk_streamer.active_chunks:
            center = temp(
                cx,
                cy,
            )

            left = temp(
                max(
                    0,
                    cx - 1,
                ),
                cy,
            )

            if (
                (
                    cx + 1
                )
                * CHUNK_SIZE
                < self.world.width_tiles
            ):
                right = temp(
                    cx + 1,
                    cy,
                )
            else:
                right = center

            # Ambient wind direction is HORIZONTAL.
            # Temperature only contributes a horizontal pressure proxy.
            gx = (
                right
                - left
            ) * 0.5

            weather = (
                self.weather_system.weather_at_chunk(
                    cx,
                    cy,
                )
            )

            background = max(
                0.0,
                min(
                    1.0,
                    float(
                        getattr(
                            weather,
                            "wind",
                            0.0,
                        )
                    ),
                ),
            )

            prevailing_direction = self._prevailing_day_direction()

            # Keep wind strength climate-responsive, but keep its horizontal
            # sign stable instead of letting it pseudo-randomly oscillate.
            gradient_strength = min(
                0.45,
                abs(
                    gx
                )
                * 0.035,
            )

            horizontal_speed = (
                self._horizontal_target_speed(
                    background,
                    gradient_strength,
                )
            )

            # Daytime ambient wind follows the authored biome chain: desert ->
            # rainforest / humid west side. Temperature gradient still affects
            # magnitude through gradient_strength, but no longer flips sign.
            target_x = (
                prevailing_direction
                * horizontal_speed
            )

            # V0.6.5: deep underground chunks have no ambient horizontal
            # weather wind.  Caves can still receive combustion lift below.
            if self._chunk_fully_underground(cx, cy):
                target_x = 0.0

            # Vertical wind is LOCAL convection only.
            # No general upward drift from gy / global temperature gradient.
            static_fire_lift = (
                self._static_fire_lift(
                    cx,
                    cy,
                )
            )

            transient_fireball_lift = min(
                WIND_FIRE_LIFT_MAX,
                self.env.fireball_lift_amount(
                    cx,
                    cy,
                ),
            )

            target_y = -min(
                WIND_FIRE_LIFT_MAX,
                static_fire_lift
                + transient_fireball_lift,
            )

            speed = math.hypot(
                target_x,
                target_y,
            )

            if (
                speed
                > WIND_MAX_SPEED
                and speed
                > 1e-9
            ):
                scale = (
                    WIND_MAX_SPEED
                    / speed
                )

                target_x *= scale
                target_y *= scale

            old_x, old_y = self.env.wind.get(
                (
                    cx,
                    cy,
                ),
                (
                    0.0,
                    0.0,
                ),
            )

            alpha = min(
                1.0,
                WIND_SMOOTH_RATE
                * max(
                    0.01,
                    dt,
                )
                * WIND_HZ,
            )

            vx = (
                old_x
                + (
                    target_x
                    - old_x
                )
                * alpha
            )

            vy = (
                old_y
                + (
                    target_y
                    - old_y
                )
                * alpha
            )

            # Without fire lift, vertical component decays rapidly to zero so
            # ambient wind reads as horizontal in both physics and renderer.
            if (
                static_fire_lift
                + transient_fireball_lift
                <= 0.01
            ):
                vy *= max(
                    0.0,
                    1.0
                    - 4.0
                    * dt,
                )

                if abs(
                    vy
                ) < 0.6:
                    vy = 0.0

            self.env.wind[
                (
                    cx,
                    cy,
                )
            ] = (
                vx,
                vy,
            )

            weather.wind = min(
                1.0,
                abs(
                    vx
                )
                / max(
                    1.0,
                    WIND_STRONG_TARGET_SPEED,
                ),
            )

            weather.wind_x = vx
            weather.wind_y = vy

            # Fireball lift is transient and decays when the projectile/explosion
            # is no longer refreshing it.
            if transient_fireball_lift > 0.0:
                remaining = max(
                    0.0,
                    self.env.fireball_lift_amount(
                        cx,
                        cy,
                    )
                    * max(
                        0.0,
                        1.0
                        - FIREBALL_LIFT_DECAY_PER_SECOND
                        * dt,
                    ),
                )

                if remaining <= 1e-5:
                    self.env.fireball_lift.pop(
                        (
                            cx,
                            cy,
                        ),
                        None,
                    )
                else:
                    self.env.fireball_lift[
                        (
                            cx,
                            cy,
                        )
                    ] = remaining

            # Evaporation plume state still decays for thermal/atmosphere use,
            # but it no longer tilts the ambient wind vector upward.
            plume = self.env.updraft_amount(
                cx,
                cy,
            )

            if plume > 0.0:
                plume_remaining = max(
                    0.0,
                    plume
                    * max(
                        0.0,
                        1.0
                        - EVAP_UPDRAFT_DECAY_PER_SECOND
                        * dt,
                    ),
                )

                if plume_remaining <= 1e-6:
                    self.env.updraft.pop(
                        (
                            cx,
                            cy,
                        ),
                        None,
                    )
                else:
                    self.env.updraft[
                        (
                            cx,
                            cy,
                        )
                    ] = plume_remaining

        if self.energy is not None:
            self.energy.record(
                "wind_work",
                self.energy.wind_kinetic()
                * dt
                * 0.002,
            )

    def wind_at_chunk(self, cx, cy):
        cx = int(cx)
        cy = int(cy)
        vx, vy = self.env.wind.get((cx, cy), (0.0, 0.0))
        if self._chunk_fully_underground(cx, cy):
            return (0.0, min(0.0, float(vy)))
        return (float(vx), float(vy))
