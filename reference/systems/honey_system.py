# -*- coding: utf-8 -*-
"""Sparse viscous honey liquid with no thermal or atmospheric phase changes."""
from config import CHUNK_SIZE, TILE_SIZE
from systems.fixed_scheduler import FixedRateTask
from world.tile_registry import AIR, tile_def


class HoneySystem:
    def __init__(self, world, env, chunk_streamer):
        self.world = world
        self.env = env
        self.chunk_streamer = chunk_streamer
        self.task = FixedRateTask(5.0, max_steps=2, phase=0.27)

    def _in_world(self, tx, ty):
        return 0 <= int(tx) < self.world.width_tiles and 0 <= int(ty) < self.world.height_tiles

    def _active(self, tx, ty):
        try:
            return self.chunk_streamer.is_active(int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE)
        except Exception:
            return True

    def _capacity(self, tx, ty):
        if not self._in_world(tx, ty):
            return 0.0
        tile_id = int(self.world.get_tile(int(tx), int(ty)))
        if tile_id == AIR or bool(getattr(tile_def(tile_id), "one_way_platform", False)):
            return 1.0
        try:
            return max(0.0, min(1.0, float(self.world.liquid_capacity_at(tx, ty))))
        except Exception:
            return 0.0 if tile_def(tile_id).solid else 1.0

    def surface_y(self, tx, ty):
        cap = self._capacity(tx, ty)
        amount = max(0.0, min(cap, self.env.honey_amount(tx, ty)))
        return (float(ty) + cap - amount) * TILE_SIZE

    def depth_at_world(self, x, y):
        tx=int(float(x)//TILE_SIZE); ty=int(float(y)//TILE_SIZE)
        if not self._in_world(tx,ty):return 0.0
        amount=self.env.honey_amount(tx,ty)
        if amount<=1e-9:return 0.0
        return max(0.0,min(TILE_SIZE,float(y)-self.surface_y(tx,ty)))

    def deposit(self, tx, ty, amount):
        remaining = max(0.0, float(amount))
        tx, ty = int(tx), int(ty)
        # The mined cell receives honey first; compact lateral/upper fallback
        # prevents clipping when multiple adjacent combs are opened together.
        candidates = [(tx, ty)]
        for distance in range(1, 7):
            candidates.extend(((tx-distance, ty), (tx+distance, ty)))
        candidates.extend((tx, ty-offset) for offset in range(1, 5))
        for cx, cy in candidates:
            if remaining <= 1e-9:
                break
            cap = self._capacity(cx, cy)
            if cap <= 0.0:
                continue
            current = self.env.honey_amount(cx, cy)
            moved = min(remaining, max(0.0, cap-current))
            if moved > 0.0:
                self.env.set_honey(cx, cy, current+moved)
                remaining -= moved
        return max(0.0, float(amount)-remaining)

    def update(self, dt):
        for step in self.task.consume(dt):
            self._step(step)

    def _move(self, source, target, amount):
        amount = max(0.0, float(amount))
        if amount <= 1e-9:
            return 0.0
        sx, sy = source; tx, ty = target
        available = self.env.honey_amount(sx, sy)
        capacity = self._capacity(tx, ty)
        present = self.env.honey_amount(tx, ty)
        moved = min(amount, available, max(0.0, capacity-present))
        if moved <= 1e-9:
            return 0.0
        self.env.set_honey(sx, sy, available-moved)
        self.env.set_honey(tx, ty, present+moved)
        return moved

    def _step(self, dt):
        # Honey is deliberately viscous: gravity is modest and sideways
        # equalisation is much slower than water.  No temperature is queried.
        rows = sorted(
            (key, float(value)) for key, value in tuple(self.env.honey.items())
            if float(value) > 1e-8 and self._active(*key)
        )
        for (tx, ty), _initial in reversed(rows):
            amount = self.env.honey_amount(tx, ty)
            if amount <= 1e-8:
                continue
            fall = min(amount, 0.42 * max(0.0, float(dt)) * 5.0)
            moved = self._move((tx, ty), (tx, ty+1), fall)
            if moved > 1e-7:
                continue
            current = self.env.honey_amount(tx, ty)
            directions = (-1, 1) if ((tx+ty) & 1) == 0 else (1, -1)
            for direction in directions:
                nx = tx + direction
                neighbor = self.env.honey_amount(nx, ty)
                difference = current-neighbor
                if difference <= 0.04:
                    continue
                moved = self._move(
                    (tx, ty), (nx, ty),
                    min(difference*0.22, 0.10*max(0.0, float(dt))*5.0),
                )
                current -= moved
