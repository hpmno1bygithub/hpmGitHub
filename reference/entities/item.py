# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class Item(Entity):
    item_id: str = "item"
    name: str = "Item"
    count: int = 1
    max_stack: int = 99
    # Dynamic weapon drops preserve the remaining heavy-attack budget of
    # every physical copy.  Ordinary items keep ``None`` so authored maps and
    # old saves do not gain an unnecessary field.
    heavy_uses: object = None
    picked: bool = False
    temperature_c: float = 20.0
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = False
    width_px: float = 15.0
    height_px: float = 15.0

    def width(self, state=None):
        return self.width_px

    def height(self, state=None):
        return self.height_px

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else float(x)
        y = self.y if y is None else float(y)
        return (
            x - self.width_px * 0.5,
            y - self.height_px,
            x + self.width_px * 0.5,
            y,
        )
