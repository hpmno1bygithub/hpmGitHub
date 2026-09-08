# -*- coding: utf-8 -*-


class Scene:
    """Minimal scene container reserved for future scene switching."""

    def __init__(self, name="world"):
        self.name = name
        self.entities = []
        self.systems = []

    def add_entity(self, entity):
        self.entities.append(entity)
        return entity

    def add_system(self, system):
        self.systems.append(system)
        return system

    def update(self, dt):
        for system in self.systems:
            update = getattr(system, "update", None)
            if update is not None:
                update(dt)
