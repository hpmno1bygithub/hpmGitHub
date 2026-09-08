# -*- coding: utf-8 -*-
"""Nearby interaction, pickup and treasure-chest opening (FIX39)."""
from engine.math2d import distance
from entities.npc import NPC
from entities.item import Item
from entities.chest import Chest
from config import ITEM_PICKUP_RADIUS_PX, NPC_INTERACT_RADIUS_PX, CHEST_INTERACT_RADIUS_PX


class InteractionSystem:
    def __init__(
        self, scene, events, radius=None, pickup_callback=None,
        pickup_radius=None, npc_radius=None, chest_callback=None,
        chest_radius=None,
    ):
        self.scene = scene
        self.events = events
        fallback = 95.0 if radius is None else float(radius)
        self.pickup_radius = float(ITEM_PICKUP_RADIUS_PX if pickup_radius is None else pickup_radius)
        self.npc_radius = float(NPC_INTERACT_RADIUS_PX if npc_radius is None else npc_radius)
        self.chest_radius = float(CHEST_INTERACT_RADIUS_PX if chest_radius is None else chest_radius)
        self.radius = fallback
        self.pickup_callback = pickup_callback
        self.chest_callback = chest_callback

    def nearest(self, player):
        best = None
        best_dist = None
        best_priority = None
        for entity in self.scene.entities:
            if not entity.active:
                continue
            if not isinstance(entity, (NPC, Item, Chest)):
                continue
            d = distance(player.x, player.y, entity.x, entity.y)
            if isinstance(entity, Chest):
                if bool(getattr(entity, "opened", False)):
                    continue
                limit = self.chest_radius
                priority = 0
            elif isinstance(entity, Item):
                limit = self.pickup_radius
                priority = 1
            else:
                limit = self.npc_radius
                priority = 2
            if d > limit:
                continue
            # Chests take precedence when distances are effectively equal so the
            # interaction button does not accidentally pick a drop behind them.
            rank = (float(d), int(priority))
            if best is None or rank < (float(best_dist), int(best_priority)):
                best = entity
                best_dist = d
                best_priority = priority
        return best

    def interact(self, player):
        entity = self.nearest(player)
        if entity is None:
            self.events.emit("message", text="附近沒有可互動物件")
            return False

        if isinstance(entity, Chest):
            if entity.opened:
                self.events.emit("message", text="寶箱已經打開")
                return True
            if self.chest_callback is None:
                return False
            return bool(self.chest_callback(entity))

        if isinstance(entity, NPC):
            self.events.emit("npc_talk", entity=entity)
            self.events.emit("message", text=f"{entity.name}：{entity.dialogue}")
            return True

        if isinstance(entity, Item):
            if entity.picked:
                return False
            accepted = int(entity.count)
            if self.pickup_callback is not None:
                accepted = int(self.pickup_callback(entity))
            if accepted <= 0:
                self.events.emit("message", text="背包已滿")
                return False
            entity.count -= accepted
            self.events.emit("item_picked", entity=entity, count=accepted)
            # FIX50: system emoji/SF-symbol weapon artwork was removed.
            # Pickup toast stays textual; inventory and world-mode icons are
            # custom-authored pixel geometry from WeaponIconWidget.
            self.events.emit("message", text=f"撿起：{entity.name} ×{accepted}")
            if entity.count <= 0:
                entity.picked = True
                entity.active = False
            return True
        return False
