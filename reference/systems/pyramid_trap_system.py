# -*- coding: utf-8 -*-
"""FIX61 bounded background-layer traps for all authored sub-dungeons.

Trap authoring lives in map metadata instead of the terrain layer.  Mining can
therefore change nearby limestone without deleting trigger plates, launchers,
or their schedules.  The system keeps a small fixed projectile list and does
not add Scene entities, physics objects, or draw calls.
"""
from dataclasses import dataclass
import math

from config import TILE_SIZE, PLAYER_HURT_INVULNERABILITY_SECONDS
from engine.math2d import rects_overlap


@dataclass
class PyramidTrap:
    trap_id: str
    kind: str
    tx: float
    ty: float
    floor: int = 0
    dx: float = 1.0
    dy: float = 0.0
    interval: float = 2.4
    respawn: float = 1.0
    speed: float = 320.0
    damage: float = 16.0
    lifetime: float = 4.8
    trigger_width: float = 1.1
    delay: float = 0.0
    state: str = "hidden"
    state_time: float = 0.0
    cooldown: float = 0.0
    extension: float = 0.0
    skin: str = "stone"


@dataclass
class PyramidTrapProjectile:
    kind: str
    owner_id: str
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    life: float
    radius: float
    active: bool = True
    bounces: int = 0
    skin: str = "stone"


class PyramidTrapSystem:
    MAX_PROJECTILES = 18

    def __init__(self, game):
        self.game = game
        self.traps = []
        self.projectiles = []
        self.damage_events = 0
        self.spawn_events = 0
        self.bounce_events = 0
        self._load_map_traps()

    def rebind(self, game):
        self.game = game
        self.traps = []
        self.projectiles = []
        self._load_map_traps()

    def _load_map_traps(self):
        payload = getattr(getattr(self.game, "map_loader", None), "payload", None)
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        rows = []
        if isinstance(metadata, dict):
            rows.extend(metadata.get("pyramid_traps", ()) or ())
            rows.extend(metadata.get("dungeon_traps", ()) or ())
        for index, row in enumerate(rows or ()):
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type", "") or "")
            if kind not in ("hidden_spike", "wall_arrow", "bouncing_fireball"):
                continue
            direction = row.get("direction", (1.0, 0.0))
            try:
                dx = float(direction[0]); dy = float(direction[1])
            except Exception:
                dx, dy = 1.0, 0.0
            length = math.hypot(dx, dy)
            if length <= 1e-6:
                dx, dy, length = 1.0, 0.0, 1.0
            trap = PyramidTrap(
                trap_id=str(row.get("id", "trap_%02d" % index)),
                kind=kind,
                tx=float(row.get("x", 0.0)),
                ty=float(row.get("y", row.get("floor", 0.0))),
                floor=int(row.get("floor", 0) or 0),
                dx=dx / length,
                dy=dy / length,
                interval=max(0.35, float(row.get("interval", 2.4) or 2.4)),
                respawn=max(0.25, float(row.get("respawn", 1.0) or 1.0)),
                speed=max(80.0, float(row.get("speed", 320.0) or 320.0)),
                damage=max(1.0, float(row.get("damage", 16.0) or 16.0)),
                lifetime=max(0.8, float(row.get("lifetime", 4.8) or 4.8)),
                trigger_width=max(0.55, float(row.get("trigger_width", 1.1) or 1.1)),
                delay=max(0.0, float(row.get("delay", index * 0.13) or 0.0)),
                skin=str(row.get("skin", metadata.get("theme", "stone")) or "stone"),
            )
            trap.cooldown = trap.delay
            self.traps.append(trap)

    def _near_player(self, trap, scale=1.0):
        p = self.game.player
        wx = (float(trap.tx) + 0.5) * TILE_SIZE
        wy = (float(trap.floor) if trap.kind == "hidden_spike" else float(trap.ty) + 0.5) * TILE_SIZE
        rx = max(float(getattr(self.game, "viewport_w", 852.0)) * 0.78, TILE_SIZE * 10.0) * scale
        ry = max(float(getattr(self.game, "viewport_h", 393.0)) * 0.92, TILE_SIZE * 7.0) * scale
        return abs(float(p.x) - wx) <= rx and abs(float(p.y) - wy) <= ry

    @staticmethod
    def _circle_hits_actor(projectile, actor):
        try:
            x1, y1, x2, y2 = actor.combat_bbox() if hasattr(actor, "combat_bbox") else actor.bbox()
        except Exception:
            return False
        nx = min(max(float(projectile.x), float(x1)), float(x2))
        ny = min(max(float(projectile.y), float(y1)), float(y2))
        dx = float(projectile.x) - nx
        dy = float(projectile.y) - ny
        return dx * dx + dy * dy <= float(projectile.radius) ** 2

    def _player_can_take_damage(self):
        p = self.game.player
        return (
            float(getattr(p, "hp", 0.0)) > 0.0
            and float(getattr(p, "startup_damage_grace", 0.0)) <= 0.0
            and float(getattr(p, "hurt_invulnerability", 0.0)) <= 0.0
        )

    def _damage_player(self, amount, source, direction=0.0, burn=False):
        if not self._player_can_take_damage():
            return False
        p = self.game.player
        damage = max(0.0, float(amount))
        taker = getattr(p, "take_damage", None)
        if callable(taker):
            resolved = taker(damage, source_x=float(p.x) - float(direction), kind="pyramid_trap")
            hp_damage = float(resolved.get("remaining_damage", damage)) if isinstance(resolved, dict) else damage
        else:
            p.hp = max(0.0, float(p.hp) - damage)
            hp_damage = damage
        p.hurt_timer = max(float(getattr(p, "hurt_timer", 0.0)), 0.22)
        p.hurt_invulnerability = float(PLAYER_HURT_INVULNERABILITY_SECONDS)
        if abs(float(direction)) > 0.01:
            p.vx = (1.0 if direction > 0.0 else -1.0) * 138.0
            p.vy = min(float(getattr(p, "vy", 0.0)), -38.0)
        if burn:
            p.lava_burn_timer = max(float(getattr(p, "lava_burn_timer", 0.0)), 2.2)
        self.damage_events += 1
        try:
            if hp_damage > 1e-6:
                self.game.events.emit("message", text="%s：-%d HP" % (str(source), int(round(hp_damage))))
        except Exception:
            pass
        return True

    def _box_hits_world(self, cx, cy, radius):
        rect = (float(cx) - radius, float(cy) - radius, float(cx) + radius, float(cy) + radius)
        try:
            for _tx, _ty, _tile, solid_rect in self.game.world.solid_cells_in_rect(rect):
                if rects_overlap(rect, solid_rect):
                    return True
        except Exception:
            return True
        return False

    def _update_spike(self, trap, dt):
        p = self.game.player
        floor_y = float(trap.floor) * TILE_SIZE
        wx = (float(trap.tx) + 0.5) * TILE_SIZE
        try:
            _x1, _y1, _x2, foot = p.bbox()
        except Exception:
            foot = float(p.y)
        on_plate = (
            abs(float(p.x) - wx) <= trap.trigger_width * TILE_SIZE * 0.5
            and floor_y - 10.0 <= float(foot) <= floor_y + 7.0
        )

        if trap.state == "hidden":
            trap.extension = 0.0
            if on_plate:
                trap.state = "warning"; trap.state_time = 0.12
        elif trap.state == "warning":
            trap.extension = 0.0
            trap.state_time -= dt
            if trap.state_time <= 0.0:
                trap.state = "rising"; trap.state_time = 0.16
        elif trap.state == "rising":
            trap.state_time -= dt
            trap.extension = min(1.0, max(0.0, 1.0 - trap.state_time / 0.16))
            if trap.state_time <= 0.0:
                trap.state = "extended"; trap.state_time = 0.68; trap.extension = 1.0
        elif trap.state == "extended":
            trap.extension = 1.0
            trap.state_time -= dt
            if trap.state_time <= 0.0:
                trap.state = "retracting"; trap.state_time = 0.24
        elif trap.state == "retracting":
            trap.state_time -= dt
            trap.extension = min(1.0, max(0.0, trap.state_time / 0.24))
            if trap.state_time <= 0.0:
                trap.state = "cooldown"; trap.state_time = max(0.45, trap.interval); trap.extension = 0.0
        else:
            trap.extension = 0.0
            trap.state_time -= dt
            if trap.state_time <= 0.0 and not on_plate:
                trap.state = "hidden"; trap.state_time = 0.0

        if trap.extension >= 0.28:
            height = TILE_SIZE * 0.84 * trap.extension
            spike_box = (wx - TILE_SIZE * 0.42, floor_y - height, wx + TILE_SIZE * 0.42, floor_y)
            try:
                player_box = p.combat_bbox() if hasattr(p, "combat_bbox") else p.bbox()
            except Exception:
                player_box = ()
            if player_box and rects_overlap(spike_box, player_box):
                source = {
                    "glow_moss": "毒藤地刺", "crystal": "水晶地刺",
                    "castle": "城堡鐵刺", "boss": "深淵尖刺",
                    "magma": "黑曜石地刺",
                }.get(str(trap.skin), "隱藏尖刺")
                self._damage_player(trap.damage, source, direction=float(p.x) - wx)

    def _owner_active(self, trap_id):
        return any(p.active and p.owner_id == trap_id for p in self.projectiles)

    def _spawn_projectile(self, trap):
        if len(self.projectiles) >= self.MAX_PROJECTILES:
            return False
        radius = 4.0 if trap.kind == "wall_arrow" else 7.0
        speed = float(trap.speed)
        self.projectiles.append(PyramidTrapProjectile(
            kind="arrow" if trap.kind == "wall_arrow" else "fireball",
            owner_id=trap.trap_id,
            x=(float(trap.tx) + 0.5) * TILE_SIZE,
            y=(float(trap.ty) + 0.5) * TILE_SIZE,
            vx=float(trap.dx) * speed,
            vy=float(trap.dy) * speed,
            damage=float(trap.damage),
            life=float(trap.lifetime),
            radius=radius,
            skin=str(trap.skin),
        ))
        self.spawn_events += 1
        return True

    def _update_launcher(self, trap, dt):
        trap.cooldown = max(0.0, float(trap.cooldown) - dt)
        if not self._near_player(trap):
            return
        if trap.kind == "bouncing_fireball" and self._owner_active(trap.trap_id):
            return
        if trap.cooldown <= 0.0 and self._spawn_projectile(trap):
            trap.cooldown = trap.interval if trap.kind == "wall_arrow" else 999.0

    def _expire_projectile(self, projectile):
        projectile.active = False
        if projectile.kind == "fireball":
            for trap in self.traps:
                if trap.trap_id == projectile.owner_id:
                    trap.cooldown = max(0.0, float(trap.respawn))
                    break

    def _update_projectile(self, projectile, dt):
        projectile.life -= dt
        if projectile.life <= 0.0:
            self._expire_projectile(projectile)
            return

        if projectile.kind == "arrow":
            nx = projectile.x + projectile.vx * dt
            ny = projectile.y + projectile.vy * dt
            if self._box_hits_world(nx, ny, projectile.radius):
                self._expire_projectile(projectile); return
            projectile.x, projectile.y = nx, ny
        else:
            projectile.vy += 105.0 * dt
            nx = projectile.x + projectile.vx * dt
            if self._box_hits_world(nx, projectile.y, projectile.radius):
                projectile.vx = -projectile.vx * 0.965
                projectile.bounces += 1; self.bounce_events += 1
            else:
                projectile.x = nx
            ny = projectile.y + projectile.vy * dt
            if self._box_hits_world(projectile.x, ny, projectile.radius):
                projectile.vy = -projectile.vy * 0.93
                if abs(projectile.vy) < 88.0:
                    projectile.vy = -88.0
                projectile.bounces += 1; self.bounce_events += 1
            else:
                projectile.y = ny

        if self._circle_hits_actor(projectile, self.game.player):
            self._damage_player(
                projectile.damage,
                (
                    "水晶飛刺" if projectile.skin == "crystal" else
                    "城堡弩箭" if projectile.skin == "castle" else
                    "牆壁飛箭"
                ) if projectile.kind == "arrow" else (
                    "孢子彈" if projectile.skin == "glow_moss" else
                    "深淵能量球" if projectile.skin == "boss" else
                    "熔岩彈" if projectile.skin == "magma" else
                    "彈跳火球"
                ),
                direction=projectile.vx,
                burn=projectile.kind == "fireball",
            )
            if projectile.kind == "arrow":
                self._expire_projectile(projectile)

    def update(self, dt):
        dt = max(0.0, min(0.05, float(dt)))
        if not self.traps:
            self.projectiles = []
            return
        for trap in self.traps:
            if trap.kind == "hidden_spike":
                if self._near_player(trap, 1.10) or trap.state != "hidden":
                    self._update_spike(trap, dt)
            else:
                self._update_launcher(trap, dt)
        for projectile in tuple(self.projectiles):
            if projectile.active:
                self._update_projectile(projectile, dt)
        self.projectiles = [p for p in self.projectiles if p.active][-self.MAX_PROJECTILES:]
