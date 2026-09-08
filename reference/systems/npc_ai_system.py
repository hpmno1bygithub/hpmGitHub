# -*- coding: utf-8 -*-
import random
from config import NPC_AI_HZ, GRAVITY, CHUNK_SIZE, TILE_SIZE
from systems.fixed_scheduler import FixedRateTask


class NPCAISystem:
    def __init__(self, scene, physics, chunk_streamer, player):
        self.scene = scene
        self.physics = physics
        self.chunk_streamer = chunk_streamer
        self.player = player
        self.task = FixedRateTask(NPC_AI_HZ, max_steps=2)
        self.rng = random.Random(1404)

    def _is_npc(self, obj):
        return hasattr(obj, "ai_state")

    def update(self, dt):
        for step in self.task.consume(dt):
            self._think(step)
        for npc in self.scene.entities:
            if not self._is_npc(npc) or not npc.active:
                continue
            cx = int(npc.x // (TILE_SIZE * CHUNK_SIZE)); cy = int(npc.y // (TILE_SIZE * CHUNK_SIZE))
            if not self.chunk_streamer.is_active(cx, cy):
                npc.vx = 0.0
                continue
            self.physics.move_horizontal(npc, npc.vx * dt)
            npc.vy = min(700.0, npc.vy + GRAVITY * dt)
            hit, grounded = self.physics.move_vertical(npc, npc.vy * dt)
            if hit and grounded:
                npc.vy = 0.0; npc.grounded = True
            elif hit and npc.vy < 0:
                npc.vy = 0.0
            else:
                npc.grounded = grounded

    def _think(self, dt):
        p = self.player
        for npc in self.scene.entities:
            if not self._is_npc(npc) or not npc.active:
                continue
            cx = int(npc.x // (TILE_SIZE * CHUNK_SIZE)); cy = int(npc.y // (TILE_SIZE * CHUNK_SIZE))
            if not self.chunk_streamer.is_active(cx, cy):
                npc.ai_state = "sleep"; npc.vx = 0.0
                continue
            distance = abs(p.x - npc.x)
            if distance < 170:
                direction = 1 if p.x > npc.x else -1
                npc.facing = direction
                if distance > 68:
                    npc.ai_state = "follow"; npc.vx = direction * npc.speed
                else:
                    npc.ai_state = "observe"; npc.vx = 0.0
                continue
            if npc.home_x == 0.0:
                npc.home_x = npc.x
            if npc.target_x == 0.0 or abs(npc.x - npc.target_x) < 10:
                npc.target_x = npc.home_x + self.rng.uniform(-npc.patrol_radius, npc.patrol_radius)
            npc.ai_state = "patrol"
            direction = 1 if npc.target_x > npc.x else -1
            npc.facing = direction
            npc.vx = direction * npc.speed * 0.65
