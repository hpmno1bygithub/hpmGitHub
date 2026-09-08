# -*- coding: utf-8 -*-
"""FIX39 bounded lava-surface eruption particles.

These are gameplay projectiles (can burn actors) but are intentionally tiny in
count. They never mutate lava mass and therefore cannot destabilize the liquid
solver. Visuals are rendered as ordinary Metal quads.
"""
from dataclasses import dataclass
import math
import random

from config import (
    TILE_SIZE, CHUNK_SIZE,
    LAVA_SPIT_MAX_ACTIVE, LAVA_SPIT_SPAWN_MIN_SECONDS,
    LAVA_SPIT_SPAWN_MAX_SECONDS, LAVA_SPIT_SPEED_MIN,
    LAVA_SPIT_SPEED_MAX, LAVA_SPIT_GRAVITY,
    LAVA_SPIT_DIRECT_DAMAGE, LAVA_BURN_SECONDS,
)


@dataclass
class LavaSpit:
    x: float
    y: float
    vx: float
    vy: float
    life: float = 2.2
    radius: float = 5.0
    active: bool = True
    serial: int = 0


class LavaSpitSystem:
    def __init__(self, game):
        self.game = game
        self.spits = []
        self.rng = random.Random(39039)
        self.spawn_clock = 0.38
        self.serial = 0

    def _visible_surface_candidates(self):
        g = self.game
        env = g.environment.state
        lava = g.environment.lava
        px = float(g.player.x)
        py = float(g.player.y)
        radius_x = max(float(g.viewport_w) * 0.80, TILE_SIZE * 8.0)
        radius_y = max(float(g.viewport_h) * 0.90, TILE_SIZE * 6.0)
        out = []
        # Sparse lava is normally small; filter by active chunks and camera/player
        # before asking for an authoritative surface.
        for (tx, ty), raw in tuple(env.lava.items()):
            amount = float(raw)
            if amount < 0.18:
                continue
            wx = (int(tx) + 0.5) * TILE_SIZE
            wy = (int(ty) + 0.5) * TILE_SIZE
            if abs(wx - px) > radius_x or abs(wy - py) > radius_y:
                continue
            try:
                if not g.chunk_streamer.is_active(int(tx)//CHUNK_SIZE, int(ty)//CHUNK_SIZE):
                    continue
            except Exception:
                pass
            # Only exposed top cells erupt; a full lava cell above hides this one.
            if float(env.lava_amount(int(tx), int(ty)-1)) > 0.04:
                continue
            try:
                sy = float(lava.surface_y(int(tx), int(ty)))
            except Exception:
                sy = int(ty) * TILE_SIZE
            out.append((int(tx), int(ty), wx, sy))
        return out

    @staticmethod
    def _overlap_circle_bbox(x, y, radius, actor):
        try:
            x1, y1, x2, y2 = actor.bbox()
        except Exception:
            return False
        nx = min(max(float(x), float(x1)), float(x2))
        ny = min(max(float(y), float(y1)), float(y2))
        dx = float(x) - nx
        dy = float(y) - ny
        return dx*dx + dy*dy <= float(radius)*float(radius)

    def _burn_actor(self, actor, scale=1.0):
        if not bool(getattr(actor, "active", True)):
            return False
        try:
            damage = float(LAVA_SPIT_DIRECT_DAMAGE) * float(scale)
            taker = getattr(actor, "take_damage", None) if actor is self.game.player else None
            if callable(taker):
                taker(damage, source_x=getattr(actor, "x", 0.0), kind="lava_spit")
            else:
                actor.hp = max(0.0, float(actor.hp) - damage)
            actor.lava_burn_timer = max(float(getattr(actor, "lava_burn_timer", 0.0)), float(LAVA_BURN_SECONDS))
            actor.hurt_timer = max(float(getattr(actor, "hurt_timer", 0.0)), 0.16)
            return True
        except Exception:
            return False

    def _spawn_one(self):
        if len(self.spits) >= int(LAVA_SPIT_MAX_ACTIVE):
            return False
        candidates = self._visible_surface_candidates()
        if not candidates:
            return False
        _tx, _ty, x, sy = self.rng.choice(candidates)
        self.serial += 1
        speed = self.rng.uniform(float(LAVA_SPIT_SPEED_MIN), float(LAVA_SPIT_SPEED_MAX))
        self.spits.append(LavaSpit(
            x=float(x) + self.rng.uniform(-TILE_SIZE*0.22, TILE_SIZE*0.22),
            y=float(sy) - 3.0,
            vx=self.rng.uniform(-38.0, 38.0),
            vy=-speed,
            life=self.rng.uniform(1.45, 2.25),
            radius=self.rng.uniform(4.0, 6.5),
            serial=self.serial,
        ))
        try:
            self.game.events.emit("audio_lava_spit")
        except Exception:
            pass
        return True

    def update(self, dt):
        dt = max(0.0, min(0.10, float(dt)))
        self.spawn_clock -= dt
        if self.spawn_clock <= 0.0:
            self._spawn_one()
            self.spawn_clock = self.rng.uniform(
                float(LAVA_SPIT_SPAWN_MIN_SECONDS), float(LAVA_SPIT_SPAWN_MAX_SECONDS)
            )

        g = self.game
        alive = []
        for s in self.spits:
            if not s.active:
                continue
            s.life -= dt
            s.vy += float(LAVA_SPIT_GRAVITY) * dt
            s.x += s.vx * dt
            s.y += s.vy * dt
            if s.life <= 0.0:
                continue

            hit = False
            if self._overlap_circle_bbox(s.x, s.y, s.radius, g.player):
                if float(getattr(g.player, "startup_damage_grace", 0.0)) <= 0.0:
                    self._burn_actor(g.player, 1.0)
                hit = True
            if not hit:
                for c in tuple(getattr(g, "creatures", ()) or ()):
                    if getattr(c,"background_only",False): continue
                    if not bool(getattr(c, "active", False)) or float(getattr(c, "hp", 0.0)) <= 0.0:
                        continue
                    if abs(float(c.x)-s.x) > TILE_SIZE*1.5 or abs(float(c.y)-s.y) > TILE_SIZE*1.5:
                        continue
                    if self._overlap_circle_bbox(s.x, s.y, s.radius, c):
                        self._burn_actor(c, 0.72)
                        hit = True
                        break
            if hit:
                continue

            # Re-entering lava consumes the projectile. World terrain only kills
            # the spit once it is descending, so launch is not blocked by its source.
            if s.vy > 0.0:
                try:
                    if float(g.environment.lava.depth_at_world(s.x, s.y)) > 1.5:
                        continue
                    tx = int(s.x // TILE_SIZE); ty = int(s.y // TILE_SIZE)
                    if g.world.get_tile(tx, ty) != 0 and g.world.liquid_capacity_at(tx, ty) <= 1e-7:
                        continue
                except Exception:
                    pass
            alive.append(s)
        self.spits = alive[-int(LAVA_SPIT_MAX_ACTIVE):]
