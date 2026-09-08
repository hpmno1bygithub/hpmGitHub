# -*- coding: utf-8 -*-
from dataclasses import dataclass
from engine.entity import Entity


@dataclass
class NPC(Entity):
    name: str = "NPC"
    dialogue: str = "你好，互動接口正常。"
    ai_state: str = "idle"
    home_x: float = 0.0
    patrol_radius: float = 180.0
    speed: float = 58.0
    target_x: float = 0.0
    facing: int = 1
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = True
    width_px: float = 30.0
    height_px: float = 50.0
    ai_wait: float = 0.0
    temperature_c: float = 37.0
    hp: float = 100.0
    poison_dose: float = 0.0
    poisoned: bool = False

    def width(self, state=None):
        return self.width_px

    def height(self, state=None):
        return self.height_px

    def bbox(self, x=None, y=None, state=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return (x - self.width_px / 2, y - self.height_px,
                x + self.width_px / 2, y)
