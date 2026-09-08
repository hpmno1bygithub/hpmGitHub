# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class ElectricBall(Entity):
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 8.0
    life: float = 1.0
    power: float = 1.0
    charge: float = 1.0
    energy_units: float = 0.0
    level: int = 1
    active: bool = True
