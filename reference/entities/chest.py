# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class Chest(Entity):
    loot_item_id: str = "weapon_dagger"
    loot_name: str = "獵刀"
    count: int = 1
    opened: bool = False
    width_px: float = 30.0
    height_px: float = 22.0

    def width(self, state=None):
        return float(self.width_px)

    def height(self, state=None):
        return float(self.height_px)

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else float(x)
        y = self.y if y is None else float(y)
        return (
            x - self.width_px * 0.5,
            y - self.height_px,
            x + self.width_px * 0.5,
            y,
        )
