# -*- coding: utf-8 -*-
from config import (
    GRAVITY, TILE_SIZE, CHUNK_SIZE, RUN_SPEED,
    PHYSICS_BOX_PUSH_ACCEL,
    PHYSICS_OBJECT_MAX_SPEED_X,
    PHYSICS_OBJECT_MAX_SPEED_Y,
    WIND_OBJECT_DRAG_AREA,
    WIND_OBJECT_MAX_ACCEL,
)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class PhysicsObjectSystem:
    """AABB rigid-object prototype with dt-stable forces."""

    def __init__(
        self,
        objects,
        physics,
        chunk_streamer,
        player,
        environment=None,
    ):
        self.objects = objects
        self.physics = physics
        self.chunk_streamer = chunk_streamer
        self.player = player
        self.environment = environment

    def update(self, dt):
        p = self.player
        for obj in self.objects:
            if not obj.active:
                continue

            cx = int(obj.x // (TILE_SIZE * CHUNK_SIZE))
            cy = int(obj.y // (TILE_SIZE * CHUNK_SIZE))
            if not self.chunk_streamer.is_active(cx, cy):
                continue

            # Directional wind is a real force input, not a renderer effect.
            # Use quadratic relative-air drag so light objects respond more.
            if self.environment is not None:
                wind_x, wind_y = self.environment.wind_at_world(
                    obj.x,
                    obj.y - obj.height() * 0.5,
                )

                rel_x = float(wind_x) - float(obj.vx)
                rel_y = float(wind_y) - float(obj.vy)

                accel_x = (
                    rel_x
                    * abs(rel_x)
                    * WIND_OBJECT_DRAG_AREA
                    / max(0.20, obj.mass)
                )
                accel_y = (
                    rel_y
                    * abs(rel_y)
                    * WIND_OBJECT_DRAG_AREA
                    / max(0.20, obj.mass)
                )

                accel_x = _clamp(
                    accel_x,
                    -WIND_OBJECT_MAX_ACCEL,
                    WIND_OBJECT_MAX_ACCEL,
                )
                accel_y = _clamp(
                    accel_y,
                    -WIND_OBJECT_MAX_ACCEL * 0.45,
                    WIND_OBJECT_MAX_ACCEL * 0.45,
                )

                obj.vx += accel_x * dt
                obj.vy += accel_y * dt

            # Push is acceleration * dt; result remains stable across fixed-step rates.
            if (
                abs(p.x - obj.x) < (p.width() + obj.width()) * 0.70
                and abs(p.y - obj.y) < 48
                and abs(p.vx) > 5
            ):
                direction = 1.0 if p.vx > 0 else -1.0
                speed_factor = min(1.0, abs(p.vx) / max(1.0, RUN_SPEED))
                obj.vx += (
                    direction * PHYSICS_BOX_PUSH_ACCEL * speed_factor
                    / max(0.20, obj.mass)
                ) * dt

            obj.vx = _clamp(obj.vx, -PHYSICS_OBJECT_MAX_SPEED_X, PHYSICS_OBJECT_MAX_SPEED_X)
            obj.vy = _clamp(obj.vy + GRAVITY * dt, -PHYSICS_OBJECT_MAX_SPEED_Y, PHYSICS_OBJECT_MAX_SPEED_Y)

            if self.physics.move_horizontal(obj, obj.vx * dt):
                obj.vx *= -obj.restitution

            hit_y, grounded = self.physics.move_vertical(obj, obj.vy * dt)
            if hit_y:
                if grounded:
                    obj.vy = -obj.vy * obj.restitution
                    if abs(obj.vy) < 22:
                        obj.vy = 0.0
                    obj.grounded = True
                else:
                    obj.vy *= -obj.restitution
            else:
                obj.grounded = False

            if obj.grounded:
                obj.vx *= obj.friction ** (dt * 60.0)
                if abs(obj.vx) < 0.5:
                    obj.vx = 0.0
