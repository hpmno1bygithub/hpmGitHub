# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class Iceball(Entity):
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 8.0
    life: float = 1.0
    power: float = 1.0
    water_mass: float = 0.0
    temperature_c: float = -28.0
    level: int = 1
    fragment: bool = False
    active: bool = True
