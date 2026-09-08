# -*- coding: utf-8 -*-
from dataclasses import dataclass


@dataclass
class Entity:
    entity_id: str
    x: float
    y: float
    active: bool = True
