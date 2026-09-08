# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class EnemyMagicBolt(Entity):
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 7.0
    life: float = 2.4
    damage: float = 10.0
    knockback: float = 140.0
    owner_id: str = ""
    active: bool = True
    element: str = "arcane"
    width: float = 0.0
    height: float = 0.0
    stationary: bool = False
