# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class Fireball(Entity):
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 8.0
    life: float = 1.0
    power: float = 1.0
    temperature_c: float = 520.0
    energy_units: float = 0.0
    evap_capacity_units: float = 0.0
    level: int = 1
    fragment: bool = False
    active: bool = True
