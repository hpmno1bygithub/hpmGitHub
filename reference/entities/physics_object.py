# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class PhysicsObject(Entity):
    width_px: float = 30.0
    height_px: float = 30.0
    vx: float = 0.0
    vy: float = 0.0
    mass: float = 1.0
    restitution: float = 0.05
    friction: float = 0.88
    grounded: bool = False
    temperature_c: float = 20.0

    def width(self, state=None):
        return self.width_px

    def height(self, state=None):
        return self.height_px

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return (x - self.width_px / 2, y - self.height_px,
                x + self.width_px / 2, y)
