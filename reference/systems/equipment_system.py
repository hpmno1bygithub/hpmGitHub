# -*- coding: utf-8 -*-
"""Inventory-backed wearable equipment and FIX71 ability controller.

Equipment is intentionally a small gameplay data layer.  The backpack owns
the item stacks; this system only records which owned item is worn in each
slot.  Equipping never consumes a stack, matching the existing weapon swap
rules and keeping UIKit/Metal presentation out of authoritative state.

The canonical slots are ``foot``, ``body`` and ``head``.  ``feet`` is accepted
as an input alias because the Player bridge names its first argument
``feet_item_id``.

FIX71 keeps persistent shield durability here (beside the inventory/equipment
authority) and exposes narrow movement hooks for :class:`player.player.Player`.
The hooks intentionally do not import Player, physics or UIKit.  This avoids a
systems -> player import cycle and lets save/load tests exercise the rules with
small stand-ins.
"""

from __future__ import annotations

import threading
from systems.wind_wings import WindWingsMixin, ITEM_WIND_WINGS, WING_SECONDS


SLOT_FOOT = "foot"
SLOT_BODY = "body"
SLOT_HEAD = "head"
EQUIPMENT_SLOTS = (SLOT_FOOT, SLOT_BODY, SLOT_HEAD)

ITEM_DOUBLE_JUMP_SHOES = "equipment.double_jump_shoes"
ITEM_AIR_DASH_SHOES = "equipment.air_dash_shoes"
ITEM_HOVER_SHOES = "equipment.hover_shoes"
ITEM_SHIELD_ARMOR = "equipment.shield_armor"
ITEM_ELECTRIC_MOUSE_HELMET = "equipment.electric_mouse_helmet"
ITEM_SKY_PUPPY_HELMET = "equipment.sky_puppy_helmet"

# Public gameplay constants.  Player/CreatureSystem should consume these APIs
# instead of duplicating timers and durability arithmetic.
HOVER_DURATION_SECONDS = 5.0
HOVER_HORIZONTAL_SPEED = 235.0
HOVER_VERTICAL_SPEED = 180.0
AIR_EVADE_DURATION_SECONDS = 0.18
AIR_EVADE_SPEED = 445.0
SHIELD_HP_MULTIPLIER = 2.0
DEFAULT_PLAYER_MAX_HP = 100.0


# Definitions are data-only so the same catalog can drive backpack labels,
# renderer overlays and player abilities without importing UIKit or Metal.
EQUIPMENT_DEFS = {
    ITEM_WIND_WINGS: {
        "item_id": ITEM_WIND_WINGS, "name": "風神翅膀", "slot": SLOT_BODY,
        "description": "左側蘑菇頭自由飛翔、加減速；每件累積飛翔 300 秒後消失，停在地面不耗耐久。",
        "abilities": {"free_flight": True, "flight_duration_seconds": WING_SECONDS},
        "visual_id": ITEM_WIND_WINGS, "visual_layer": "body_overlay",
    },
    ITEM_DOUBLE_JUMP_SHOES: {
        "item_id": ITEM_DOUBLE_JUMP_SHOES,
        "name": "二段跳鞋",
        "slot": SLOT_FOOT,
        "description": "在空中可額外跳躍一次。",
        "abilities": {
            "extra_air_jumps": 1,
            "double_jump": True,
        },
        "visual_id": ITEM_DOUBLE_JUMP_SHOES,
        "visual_layer": "feet_overlay",
    },
    ITEM_AIR_DASH_SHOES: {
        "item_id": ITEM_AIR_DASH_SHOES,
        "name": "空中衝刺鞋",
        "slot": SLOT_FOOT,
        "description": "離地後可額外發動一次空中翻滾衝刺。",
        "abilities": {
            "extra_air_rolls": 1,
            "air_dash": True,
        },
        "visual_id": ITEM_AIR_DASH_SHOES,
        "visual_layer": "feet_overlay",
    },
    ITEM_HOVER_SHOES: {
        "item_id": ITEM_HOVER_SHOES,
        "name": "浮空鞋",
        "slot": SLOT_FOOT,
        "description": "每次離地可浮空 5 秒，並可自由上下左右移動。",
        "abilities": {
            "air_hover": True,
            "hover_duration_seconds": HOVER_DURATION_SECONDS,
            "hover_horizontal_speed": HOVER_HORIZONTAL_SPEED,
            "hover_vertical_speed": HOVER_VERTICAL_SPEED,
        },
        "visual_id": ITEM_HOVER_SHOES,
        "visual_layer": "feet_overlay",
    },
    ITEM_SHIELD_ARMOR: {
        "item_id": ITEM_SHIELD_ARMOR,
        "name": "護盾盔甲",
        "slot": SLOT_BODY,
        "description": "隱藏護盾值為角色最大血量 2 倍；耗盡時盔甲破壞消失。",
        "abilities": {
            "front_shield": True,
            "rear_shield": True,
            "blocks_creature_attacks": True,
            "shield_hp_multiplier": SHIELD_HP_MULTIPLIER,
        },
        "visual_id": ITEM_SHIELD_ARMOR,
        "visual_layer": "body_overlay",
    },
    ITEM_ELECTRIC_MOUSE_HELMET: {
        "item_id": ITEM_ELECTRIC_MOUSE_HELMET,
        "name": "皮卡丘風格頭盔",
        "slot": SLOT_HEAD,
        "description": "帶有長耳與電氣鼠配色的造型頭盔。",
        "abilities": {},
        "visual_id": ITEM_ELECTRIC_MOUSE_HELMET,
        "visual_layer": "head_overlay",
        "cosmetic_only": True,
    },
    ITEM_SKY_PUPPY_HELMET: {
        "item_id": ITEM_SKY_PUPPY_HELMET,
        "name": "大耳狗風格頭盔",
        "slot": SLOT_HEAD,
        "description": "帶有垂落長耳的天空色造型頭盔。",
        "abilities": {},
        "visual_id": ITEM_SKY_PUPPY_HELMET,
        "visual_layer": "head_overlay",
        "cosmetic_only": True,
    },
}


_SLOT_ALIASES = {
    "foot": SLOT_FOOT,
    "feet": SLOT_FOOT,
    "shoe": SLOT_FOOT,
    "shoes": SLOT_FOOT,
    "boot": SLOT_FOOT,
    "boots": SLOT_FOOT,
    "body": SLOT_BODY,
    "chest": SLOT_BODY,
    "torso": SLOT_BODY,
    "armor": SLOT_BODY,
    "head": SLOT_HEAD,
    "helmet": SLOT_HEAD,
    "hat": SLOT_HEAD,
}


# These aliases are import-only migrations.  Export always writes the fixed
# dotted IDs above, so new saves remain deterministic.
_ITEM_ID_ALIASES = {
    "equipment_double_jump_shoes": ITEM_DOUBLE_JUMP_SHOES,
    "equipment_double_jump_boots": ITEM_DOUBLE_JUMP_SHOES,
    "double_jump_shoes": ITEM_DOUBLE_JUMP_SHOES,
    "equipment_air_dash_shoes": ITEM_AIR_DASH_SHOES,
    "equipment_air_dash_boots": ITEM_AIR_DASH_SHOES,
    "air_dash_shoes": ITEM_AIR_DASH_SHOES,
    "equipment_hover_shoes": ITEM_HOVER_SHOES,
    "equipment_hover_boots": ITEM_HOVER_SHOES,
    "hover_shoes": ITEM_HOVER_SHOES,
    "equipment_shield_armor": ITEM_SHIELD_ARMOR,
    "shield_armor": ITEM_SHIELD_ARMOR,
    "equipment_electric_mouse_helmet": ITEM_ELECTRIC_MOUSE_HELMET,
    "electric_mouse_helmet": ITEM_ELECTRIC_MOUSE_HELMET,
    "equipment_sky_puppy_helmet": ITEM_SKY_PUPPY_HELMET,
    "sky_puppy_helmet": ITEM_SKY_PUPPY_HELMET,
}


def normalize_slot(slot):
    """Return a canonical equipment slot, or an empty string if unknown."""
    return _SLOT_ALIASES.get(str(slot or "").strip().lower(), "")


def normalize_equipment_item_id(item_id):
    """Return a fixed catalog ID, accepting a few pre-release aliases."""
    key = str(item_id or "").strip()
    if key in EQUIPMENT_DEFS:
        return key
    return _ITEM_ID_ALIASES.get(key, "")


def is_equipment_item(item_id):
    return bool(normalize_equipment_item_id(item_id))


def equipment_slot_for_item(item_id):
    resolved = normalize_equipment_item_id(item_id)
    return str(EQUIPMENT_DEFS.get(resolved, {}).get("slot", ""))


def _copy_definition(row):
    if not row:
        return None
    copied = dict(row)
    copied["abilities"] = dict(row.get("abilities", {}))
    return copied


def equipment_definition(item_id):
    """Return a caller-safe copy of one equipment definition."""
    return _copy_definition(EQUIPMENT_DEFS.get(normalize_equipment_item_id(item_id)))


def equipment_catalog():
    """Return the wearable definitions in stable UI order."""
    return tuple(_copy_definition(EQUIPMENT_DEFS[item_id]) for item_id in EQUIPMENT_DEFS)


class EquipmentSystem(WindWingsMixin):
    """Authoritative equipped-slot state backed by an ``InventorySystem``."""

    FORMAT_VERSION = 3
    EQUIPMENT_MAX_STACK = 99

    def __init__(self, inventory, events=None):
        self.inventory = inventory
        self.events = events
        self._lock = threading.RLock()
        self._slots = {slot: "" for slot in EQUIPMENT_SLOTS}
        self._revision = 0
        # Shield durability is persistent.  ``_shield_initialized`` separates
        # an old save (which should receive a full shield on first player sync)
        # from a legitimately depleted shield with 0 HP (which must break).
        self._shield_hp = 0.0
        self._shield_max_hp = 0.0
        self._shield_player_max_hp = 0.0
        self._shield_initialized = False
        self._shield_break_serial = 0
        self._init_wings()

    def _emit(self, name, **payload):
        if self.events is None:
            return
        try:
            self.events.emit(str(name), **payload)
        except Exception:
            # Equipment state must never be rolled back because a presentation
            # subscriber is unavailable during startup, load or shutdown.
            pass

    def _message(self, text):
        self._emit("message", text=str(text))

    def _owned_count(self, item_id):
        inventory = self.inventory
        if inventory is None:
            return 0
        try:
            return max(0, int(inventory.count_item(str(item_id))))
        except Exception:
            pass
        try:
            snapshot = inventory.snapshot()
            return sum(
                max(0, int(row.get("count", 0)))
                for row in snapshot.get("slots", ())
                if isinstance(row, dict) and str(row.get("item_id", "")) == str(item_id)
            )
        except Exception:
            return 0

    def owned_count(self, item_id):
        resolved = normalize_equipment_item_id(item_id)
        return self._owned_count(resolved) if resolved else 0

    def owns(self, item_id):
        return self.owned_count(item_id) > 0

    def acquire(self, item_id, count=1):
        """Add catalog equipment to the backpack and return accepted count."""
        resolved = normalize_equipment_item_id(item_id)
        row = EQUIPMENT_DEFS.get(resolved)
        requested = max(0, int(count))
        if not row or requested <= 0 or self.inventory is None:
            return 0
        try:
            accepted = int(self.inventory.add(
                resolved,
                str(row["name"]),
                requested,
                max_stack=self.EQUIPMENT_MAX_STACK,
            ))
        except Exception:
            return 0
        if accepted > 0:
            if resolved == ITEM_WIND_WINGS: self._sync_wing_copies()
            self._emit(
                "equipment_acquired",
                item_id=resolved,
                slot=str(row["slot"]),
                count=int(accepted),
            )
        return max(0, accepted)

    def selected_item_id(self):
        """Return the selected backpack item's fixed equipment ID, if any."""
        try:
            snapshot = self.inventory.snapshot()
            index = int(snapshot.get("selected_index", 0))
            slots = snapshot.get("slots", ())
            row = slots[index] if 0 <= index < len(slots) else None
            if not isinstance(row, dict):
                return ""
            return normalize_equipment_item_id(row.get("item_id", ""))
        except Exception:
            return ""

    def equip_selected(self):
        item_id = self.selected_item_id()
        if not item_id:
            self._message("請先在背包選擇可裝備物品")
            return False
        return self.equip(item_id)

    def equip(self, item_id):
        """Equip one owned item in its declared slot without consuming it."""
        resolved = normalize_equipment_item_id(item_id)
        row = EQUIPMENT_DEFS.get(resolved)
        if not row:
            self._message("這個物品不是裝備")
            return False
        if self._owned_count(resolved) <= 0:
            self._message("背包中沒有這件裝備")
            return False

        slot = str(row["slot"])
        with self._lock:
            previous = str(self._slots.get(slot, ""))
            if previous == resolved:
                return True
            self._slots[slot] = resolved
            self._revision += 1
            revision = int(self._revision)

        self._emit(
            "equipment_changed",
            slot=slot,
            item_id=resolved,
            previous_item_id=previous,
            revision=revision,
        )
        self._emit("audio_equipment_equip", slot=slot, item_id=resolved)
        self._message("已裝備：" + str(row["name"]))
        return True

    def unequip(self, slot_or_item_id):
        """Clear one slot.  The backpack item remains untouched."""
        slot = normalize_slot(slot_or_item_id)
        if not slot:
            resolved = normalize_equipment_item_id(slot_or_item_id)
            slot = equipment_slot_for_item(resolved)
        if not slot:
            return False

        with self._lock:
            previous = str(self._slots.get(slot, ""))
            if not previous:
                return False
            self._slots[slot] = ""
            self._revision += 1
            revision = int(self._revision)

        self._emit(
            "equipment_changed",
            slot=slot,
            item_id="",
            previous_item_id=previous,
            revision=revision,
        )
        old_row = EQUIPMENT_DEFS.get(previous, {})
        self._message("已卸下：" + str(old_row.get("name", "裝備")))
        return True

    def equipped_item(self, slot):
        canonical = normalize_slot(slot)
        if not canonical:
            return ""
        with self._lock:
            return str(self._slots.get(canonical, ""))

    # Read-only alias used by renderers/tools that prefer a getter name.
    def get_equipped(self, slot):
        return self.equipped_item(slot)

    def equipped_definition(self, slot):
        return equipment_definition(self.equipped_item(slot))

    def is_equipped(self, item_id):
        resolved = normalize_equipment_item_id(item_id)
        if not resolved:
            return False
        with self._lock:
            return resolved in self._slots.values()

    def can_remove_inventory_item(self, item_id, count=1):
        """Tell drop/remove UI whether removing a stack would break equipment."""
        resolved = normalize_equipment_item_id(item_id)
        if not resolved or not self.is_equipped(resolved):
            return True
        return self._owned_count(resolved) > max(0, int(count))

    def reconcile_inventory(self):
        """Unequip entries no longer owned; return the cleared slot names."""
        with self._lock:
            current = dict(self._slots)
        # Query InventorySystem outside our lock.  This establishes one lock
        # order and avoids a future inventory callback deadlocking with an
        # equip/unequip operation.
        owned = {
            item_id: self._owned_count(item_id)
            for item_id in set(current.values())
            if item_id
        }
        missing = {
            slot for slot, item_id in current.items()
            if item_id and owned.get(item_id, 0) <= 0
        }
        if not missing:
            return ()

        changed = []
        with self._lock:
            for slot in EQUIPMENT_SLOTS:
                item_id = str(self._slots.get(slot, ""))
                if slot not in missing or item_id != current.get(slot, ""):
                    continue
                self._slots[slot] = ""
                changed.append(slot)
                if slot == SLOT_BODY and item_id == ITEM_SHIELD_ARMOR:
                    self._shield_hp = 0.0
                    self._shield_max_hp = 0.0
                    self._shield_player_max_hp = 0.0
                    self._shield_initialized = False
            if changed:
                self._revision += 1
                revision = int(self._revision)
            else:
                revision = int(self._revision)

        for slot in changed:
            self._emit(
                "equipment_changed",
                slot=slot,
                item_id="",
                previous_item_id=current.get(slot, ""),
                revision=revision,
            )
        return tuple(changed)

    def abilities(self):
        """Return merged abilities for the currently worn equipment."""
        with self._lock:
            item_ids = tuple(self._slots[slot] for slot in EQUIPMENT_SLOTS)
        merged = {
            "extra_air_jumps": 0,
            "extra_air_rolls": 0,
            "double_jump": False,
            "air_dash": False,
            "air_hover": False,
            "hover_duration_seconds": 0.0,
            "hover_horizontal_speed": 0.0,
            "hover_vertical_speed": 0.0,
            "front_shield": False,
            "rear_shield": False,
            "blocks_creature_attacks": False,
            "shield_hp_multiplier": 0.0,
        }
        for item_id in item_ids:
            for key, value in EQUIPMENT_DEFS.get(item_id, {}).get("abilities", {}).items():
                if isinstance(value, bool):
                    merged[key] = bool(merged.get(key, False)) or value
                elif isinstance(value, (int, float)):
                    merged[key] = merged.get(key, 0) + value
                else:
                    merged[key] = value
        return merged

    def has_ability(self, ability):
        return bool(self.abilities().get(str(ability), False))

    def ability_value(self, ability, default=0):
        return self.abilities().get(str(ability), default)

    @property
    def extra_air_jumps(self):
        return int(self.ability_value("extra_air_jumps", 0))

    @property
    def extra_air_rolls(self):
        return int(self.ability_value("extra_air_rolls", 0))

    def shield_blocks(self, side="front"):
        side = str(side or "front").strip().lower()
        with self._lock:
            shield_ready = (
                self._slots.get(SLOT_BODY, "") == ITEM_SHIELD_ARMOR
                and (not self._shield_initialized or self._shield_hp > 0.0)
            )
        if not shield_ready:
            return False
        if side in ("back", "behind", "rear"):
            return self.has_ability("rear_shield")
        return self.has_ability("front_shield")

    # --------------------------------------------------------
    # FIX71 shield durability
    # --------------------------------------------------------

    @staticmethod
    def _resolved_player_max_hp(player=None, player_max_hp=None):
        """Resolve a stable max-HP basis without treating current HP as max HP."""
        candidates = [player_max_hp]
        if player is not None:
            candidates.extend((
                getattr(player, "max_hp", None),
                getattr(player, "maximum_hp", None),
                getattr(player, "MAX_HP", None),
            ))
        for value in candidates:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                return value
        return float(DEFAULT_PLAYER_MAX_HP)

    def _publish_shield_state_to_player(self, player):
        if player is None:
            return
        state = self.shield_state()
        # These fields are diagnostic/gameplay bridges only.  The shield bar is
        # deliberately hidden; renderers need only the existing impact pulse.
        try:
            player.shield_armor_hp = float(state["hp"])
            player.shield_armor_max_hp = float(state["max_hp"])
            player.shield_armor_active = bool(state["active"])
            player.equipment_damage_resolver = self.resolve_incoming_damage
        except Exception:
            pass

    def ensure_shield_capacity(self, player=None, player_max_hp=None):
        """Initialize/resize the equipped armor to exactly 2x player max HP.

        A max-HP upgrade preserves the remaining shield percentage rather than
        repairing it for free.  Old saves have no durability block and receive
        one full pool on the first call, which is the intended migration.
        """
        basis = self._resolved_player_max_hp(player, player_max_hp)
        wanted = max(1.0, basis * float(SHIELD_HP_MULTIPLIER))
        with self._lock:
            equipped = self._slots.get(SLOT_BODY, "") == ITEM_SHIELD_ARMOR
            if equipped:
                if not self._shield_initialized or self._shield_max_hp <= 0.0:
                    self._shield_hp = wanted
                    self._shield_max_hp = wanted
                    self._shield_player_max_hp = basis
                    self._shield_initialized = True
                elif abs(self._shield_max_hp - wanted) > 1e-6:
                    ratio = max(0.0, min(1.0, self._shield_hp / self._shield_max_hp))
                    self._shield_hp = wanted * ratio
                    self._shield_max_hp = wanted
                    self._shield_player_max_hp = basis
            state = {
                "active": bool(equipped and self._shield_initialized and self._shield_hp > 0.0),
                "hp": float(self._shield_hp if equipped else 0.0),
                "max_hp": float(self._shield_max_hp if equipped else 0.0),
                "player_max_hp": float(self._shield_player_max_hp if equipped else basis),
                "ratio": (
                    max(0.0, min(1.0, self._shield_hp / self._shield_max_hp))
                    if equipped and self._shield_max_hp > 0.0 else 0.0
                ),
                "hidden": True,
                "break_serial": int(self._shield_break_serial),
            }
        self._publish_shield_state_to_player(player)
        return state

    def shield_state(self):
        """Return detached durability data for saves, diagnostics and tests."""
        with self._lock:
            equipped = self._slots.get(SLOT_BODY, "") == ITEM_SHIELD_ARMOR
            maximum = float(self._shield_max_hp if equipped else 0.0)
            current = float(self._shield_hp if equipped else 0.0)
            return {
                "active": bool(equipped and self._shield_initialized and current > 0.0),
                "equipped": bool(equipped),
                "initialized": bool(self._shield_initialized if equipped else False),
                "hp": current,
                "max_hp": maximum,
                "player_max_hp": float(self._shield_player_max_hp if equipped else 0.0),
                "ratio": max(0.0, min(1.0, current / maximum)) if maximum > 0.0 else 0.0,
                "hidden": True,
                "break_serial": int(self._shield_break_serial),
            }

    @staticmethod
    def _shield_hit_pulse(player, source_x, damage):
        if player is None:
            return 0
        try:
            dx = float(source_x) - float(player.x)
        except Exception:
            dx = float(getattr(player, "facing", 1) or 1)
        side = 1 if dx > 0.5 else (-1 if dx < -0.5 else int(getattr(player, "facing", 1) or 1))
        try:
            player.shield_block_side = side
            player.shield_block_strength = max(0.35, min(1.0, float(damage) / 36.0))
            player.shield_block_timer = max(float(getattr(player, "shield_block_timer", 0.0)), 0.22)
            player.shield_block_serial = int(getattr(player, "shield_block_serial", 0)) + 1
        except Exception:
            pass
        return side

    def resolve_incoming_damage(
        self,
        damage,
        player=None,
        player_max_hp=None,
        source_x=None,
        source_y=None,
        kind="damage",
        bypass_shield=False,
    ):
        """Absorb damage before HP and return the exact unabsorbed remainder.

        Integration contract: the caller applies only ``remaining_damage`` to
        player HP.  Knockback/invulnerability remain owned by the originating
        combat or environment system.  When the pool reaches zero exactly one
        armor item is consumed, the body slot is cleared, and ``sync_player``
        is performed immediately when a player object was supplied.
        """
        try:
            incoming = max(0.0, float(damage))
        except (TypeError, ValueError):
            incoming = 0.0
        result = {
            "incoming_damage": incoming,
            "absorbed_damage": 0.0,
            "remaining_damage": incoming,
            "blocked": False,
            "shield_broken": False,
            "shield_hp": 0.0,
            "shield_max_hp": 0.0,
            "kind": str(kind or "damage"),
        }
        if incoming <= 0.0 or bool(bypass_shield):
            return result

        self.ensure_shield_capacity(player=player, player_max_hp=player_max_hp)
        with self._lock:
            if (
                self._slots.get(SLOT_BODY, "") != ITEM_SHIELD_ARMOR
                or not self._shield_initialized
                or self._shield_hp <= 0.0
            ):
                return result
            absorbed = min(incoming, float(self._shield_hp))
            self._shield_hp = max(0.0, float(self._shield_hp) - absorbed)
            remaining = max(0.0, incoming - absorbed)
            broke = self._shield_hp <= 1e-9
            maximum = float(self._shield_max_hp)
            hp_after = float(self._shield_hp)
            previous_item = ""
            if broke:
                previous_item = str(self._slots.get(SLOT_BODY, ""))
                self._slots[SLOT_BODY] = ""
                self._shield_hp = 0.0
                self._shield_max_hp = 0.0
                self._shield_player_max_hp = 0.0
                self._shield_initialized = False
                self._shield_break_serial += 1
            self._revision += 1
            revision = int(self._revision)

        side = self._shield_hit_pulse(player, source_x, absorbed)
        consumed = 0
        if broke:
            try:
                consumed = int(self.inventory.consume_item(ITEM_SHIELD_ARMOR, 1))
            except Exception:
                consumed = 0
            self._emit(
                "equipment_changed",
                slot=SLOT_BODY,
                item_id="",
                previous_item_id=previous_item or ITEM_SHIELD_ARMOR,
                revision=revision,
                reason="durability_depleted",
            )
            self._emit(
                "equipment_destroyed",
                item_id=ITEM_SHIELD_ARMOR,
                slot=SLOT_BODY,
                consumed_count=consumed,
                kind=str(kind or "damage"),
            )
            self._message("護盾盔甲能量耗盡，裝備已破壞")

        result.update({
            "absorbed_damage": float(absorbed),
            "remaining_damage": float(remaining),
            "blocked": bool(absorbed > 0.0 and remaining <= 1e-9),
            "shield_broken": bool(broke),
            "shield_hp": float(hp_after),
            "shield_max_hp": float(maximum),
            "consumed_count": int(consumed),
            "side": int(side),
            "source_y": source_y,
            "revision": revision,
        })
        self._emit("shield_damaged", **dict(result))
        if player is not None:
            if broke:
                self.sync_player(player, player_max_hp=player_max_hp)
            else:
                self._publish_shield_state_to_player(player)
        return result

    # Descriptive alias used by damage pipelines.
    absorb_damage = resolve_incoming_damage

    # --------------------------------------------------------
    # FIX71 shoe movement hooks
    # --------------------------------------------------------

    @staticmethod
    def _ensure_mobility_fields(player):
        defaults = {
            "hover_active": False,
            "hover_time_remaining": 0.0,
            "hover_used": False,
            "air_dash_used": False,
            "air_dash_active": False,
            "equipment_jump_latched": False,
        }
        for name, value in defaults.items():
            if not hasattr(player, name):
                try:
                    setattr(player, name, value)
                except Exception:
                    pass

    def reset_air_mobility(self, player, reason="reset", rearm=True, clear_jump_latch=True):
        """Cancel transient shoe motion after landing/load/portal/unequip."""
        if player is None:
            return False
        self._ensure_mobility_fields(player)
        was_active = bool(getattr(player, "hover_active", False) or getattr(player, "air_dash_active", False))
        try:
            player.hover_active = False
            player.hover_time_remaining = 0.0
            player.air_dash_active = False
            if clear_jump_latch:
                player.equipment_jump_latched = False
            if rearm:
                player.hover_used = False
                player.air_dash_used = False
        except Exception:
            return False
        if was_active:
            self._emit("equipment_air_motion_ended", reason=str(reason or "reset"))
        return True

    def handle_airborne_jump(
        self,
        player,
        move_x=0.0,
        move_y=0.0,
        fully_submerged=False,
        climbing=False,
    ):
        """Consume one *edge-triggered* airborne Jump press when appropriate.

        Return value contains ``consumed`` and ``action``.  Player should set
        its local ``jump_edge`` false when consumed, preventing the same press
        from entering jump buffering or wall-climb logic later in the frame.
        """
        outcome = {"consumed": False, "action": "none"}
        if player is None:
            return outcome
        self._ensure_mobility_fields(player)
        if bool(getattr(player, "grounded", False)) or bool(fully_submerged) or bool(climbing):
            return outcome
        foot = self.equipped_item(SLOT_FOOT)

        if foot == ITEM_HOVER_SHOES:
            if bool(getattr(player, "hover_active", False)):
                player.hover_active = False
                player.hover_time_remaining = 0.0
                player.hover_toggle_serial = int(getattr(player, "hover_toggle_serial", 0)) + 1
                self._emit("equipment_hover_ended", reason="jump_cancel")
                return {"consumed": True, "action": "hover_cancelled", "remaining": 0.0}
            if bool(getattr(player, "hover_used", False)):
                return outcome
            player.hover_active = True
            player.hover_used = True
            player.hover_time_remaining = float(HOVER_DURATION_SECONDS)
            player.vy = 0.0
            player.hover_toggle_serial = int(getattr(player, "hover_toggle_serial", 0)) + 1
            self._emit("equipment_hover_started", duration=HOVER_DURATION_SECONDS)
            return {
                "consumed": True,
                "action": "hover_started",
                "remaining": float(HOVER_DURATION_SECONDS),
            }

        if foot == ITEM_AIR_DASH_SHOES and not bool(getattr(player, "air_dash_used", False)):
            try:
                axis = float(move_x)
            except (TypeError, ValueError):
                axis = 0.0
            direction = 1 if axis > 0.05 else (-1 if axis < -0.05 else (1 if int(getattr(player, "facing", 1) or 1) >= 0 else -1))
            settings = getattr(player, "action_settings", {}) or {}
            try:
                duration = max(0.08, min(0.35, float(settings.get("air_dash_time", AIR_EVADE_DURATION_SECONDS))))
            except Exception:
                duration = float(AIR_EVADE_DURATION_SECONDS)
            try:
                speed = max(float(AIR_EVADE_SPEED), float(settings.get("air_dash_speed", AIR_EVADE_SPEED)))
            except Exception:
                speed = float(AIR_EVADE_SPEED)
            player.facing = direction
            player.roll_return_posture = "stand"
            player.state = "roll"
            player.roll_timer = duration
            player.vy = 0.0
            player.vx = speed * direction
            player.air_dash_used = True
            player.air_dash_active = True
            player.air_dash_serial = int(getattr(player, "air_dash_serial", 0)) + 1
            player.roll_audio_serial = int(getattr(player, "roll_audio_serial", 0)) + 1
            self._emit("equipment_air_evade_started", direction=direction, duration=duration, speed=speed)
            return {
                "consumed": True,
                "action": "air_evade_started",
                "direction": direction,
                "duration": duration,
                "speed": speed,
            }
        return outcome

    def handle_jump_input(
        self,
        player,
        jump_down,
        move_x=0.0,
        move_y=0.0,
        fully_submerged=False,
        climbing=False,
    ):
        """Level-input adapter with a latch, safe to call every fixed frame."""
        if player is None:
            return {"consumed": False, "action": "none"}
        self._ensure_mobility_fields(player)
        down = bool(jump_down)
        latched = bool(getattr(player, "equipment_jump_latched", False))
        player.equipment_jump_latched = down
        if not down or latched:
            return {"consumed": False, "action": "none"}
        return self.handle_airborne_jump(
            player,
            move_x=move_x,
            move_y=move_y,
            fully_submerged=fully_submerged,
            climbing=climbing,
        )

    def update_hover_motion(
        self,
        player,
        dt,
        move_x=0.0,
        move_y=0.0,
        fully_submerged=False,
        climbing=False,
    ):
        """Advance the 5-second hover and write collision-ready vx/vy.

        Call after ordinary input acceleration and before vertical collision.
        Physics remains authoritative: this method only supplies velocity.
        """
        if player is None:
            return {"active": False, "remaining": 0.0}
        self._ensure_mobility_fields(player)
        if bool(getattr(player, "grounded", False)):
            self.reset_air_mobility(
                player, reason="landed", rearm=True, clear_jump_latch=False,
            )
            return {"active": False, "remaining": 0.0, "reason": "landed"}
        if bool(fully_submerged) or bool(climbing) or self.equipped_item(SLOT_FOOT) != ITEM_HOVER_SHOES:
            self.reset_air_mobility(
                player, reason="invalid_medium", rearm=False,
                clear_jump_latch=False,
            )
            return {"active": False, "remaining": 0.0, "reason": "invalid_medium"}
        if not bool(getattr(player, "hover_active", False)):
            return {"active": False, "remaining": max(0.0, float(getattr(player, "hover_time_remaining", 0.0)))}

        remaining = max(0.0, float(getattr(player, "hover_time_remaining", 0.0)) - max(0.0, float(dt)))
        player.hover_time_remaining = remaining
        if remaining <= 0.0:
            player.hover_active = False
            player.vy = 0.0
            self._emit("equipment_hover_ended", reason="duration_complete")
            return {"active": False, "remaining": 0.0, "reason": "duration_complete"}

        def axis(value):
            try:
                return max(-1.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return 0.0
        ax = axis(move_x)
        ay = axis(move_y)
        magnitude = (ax * ax + ay * ay) ** 0.5
        if magnitude > 1.0:
            ax /= magnitude
            ay /= magnitude
        player.vx = ax * float(HOVER_HORIZONTAL_SPEED)
        player.vy = ay * float(HOVER_VERTICAL_SPEED)
        if abs(ax) > 0.05:
            player.facing = 1 if ax > 0.0 else -1
        return {
            "active": True,
            "remaining": remaining,
            "vx": float(player.vx),
            "vy": float(player.vy),
        }

    def mobility_state(self, player):
        self._ensure_mobility_fields(player)
        return {
            "hover_active": bool(getattr(player, "hover_active", False)),
            "hover_used": bool(getattr(player, "hover_used", False)),
            "hover_time_remaining": max(0.0, float(getattr(player, "hover_time_remaining", 0.0))),
            "air_evade_active": bool(getattr(player, "air_dash_active", False)),
            "air_evade_used": bool(getattr(player, "air_dash_used", False)),
        }

    def player_slot_ids(self):
        """Return IDs in Player.set_equipment_abilities argument order."""
        with self._lock:
            return (
                str(self._slots[SLOT_FOOT]),
                str(self._slots[SLOT_BODY]),
                str(self._slots[SLOT_HEAD]),
            )

    def sync_player(self, player, player_max_hp=None):
        """Apply IDs and bind the narrow movement/damage integration hooks."""
        setter = getattr(player, "set_equipment_abilities", None)
        if not callable(setter):
            return False
        try:
            player.equipment_system = self
            player.equipment_damage_resolver = self.resolve_incoming_damage
        except Exception:
            pass
        slot_ids = self.player_slot_ids()
        previous_foot = str(getattr(player, "feet_equipment_id", "") or "")
        setter(*slot_ids)
        self._ensure_mobility_fields(player)
        wing = self.wing_state()
        player.wing_time_remaining = wing['remaining_seconds'] if wing['equipped'] else 0.0
        if not wing['equipped']: player.wing_flying = False
        if previous_foot != str(slot_ids[0]):
            airborne = not bool(getattr(player, "grounded", False))
            self.reset_air_mobility(
                player, reason="foot_equipment_changed", rearm=(not airborne),
            )
            if airborne:
                # Swapping shoes in mid-air cannot refresh either one-use move.
                player.hover_used = True
                player.air_dash_used = True
        if self.equipped_item(SLOT_BODY) == ITEM_SHIELD_ARMOR:
            self.ensure_shield_capacity(player=player, player_max_hp=player_max_hp)
        else:
            self._publish_shield_state_to_player(player)
        return True

    def visual_layers(self):
        """Return renderer-ready definition copies keyed by canonical slot."""
        with self._lock:
            current = dict(self._slots)
        return {
            slot: equipment_definition(current.get(slot, ""))
            for slot in EQUIPMENT_SLOTS
            if current.get(slot, "")
        }

    def snapshot(self):
        with self._lock:
            slots = dict(self._slots)
            revision = int(self._revision)
        return {
            "format": int(self.FORMAT_VERSION),
            "slots": slots,
            "foot": slots[SLOT_FOOT],
            "body": slots[SLOT_BODY],
            "head": slots[SLOT_HEAD],
            "revision": revision,
            "abilities": self.abilities(),
            "shield": self.shield_state(),
            "wind_wings": self.wing_state(),
        }

    def export_state(self):
        """Serialize slot IDs and shield durability; air-motion stays transient."""
        wing_state = self.wing_state()
        with self._lock:
            return {
                "format": int(self.FORMAT_VERSION),
                "wind_wings": wing_state,
                "slots": {slot: str(self._slots[slot]) for slot in EQUIPMENT_SLOTS},
                "durability": {
                    "shield_armor": {
                        "initialized": bool(self._shield_initialized),
                        "hp": float(self._shield_hp),
                        "max_hp": float(self._shield_max_hp),
                        "player_max_hp": float(self._shield_player_max_hp),
                        "break_serial": int(self._shield_break_serial),
                    },
                },
            }

    @staticmethod
    def _state_slots(data):
        if not isinstance(data, dict):
            return {}

        # Accept either the system payload itself, a full save's equipment
        # object, or a future nested gear/wearables object.
        source = data
        if isinstance(source.get("equipment"), dict):
            source = source["equipment"]
        for key in ("gear", "wearables"):
            if isinstance(source.get(key), dict):
                source = source[key]
                break
        if isinstance(source.get("slots"), dict):
            source = source["slots"]

        return {
            SLOT_FOOT: source.get("foot", source.get("feet", "")),
            SLOT_BODY: source.get("body", ""),
            SLOT_HEAD: source.get("head", ""),
        }

    @staticmethod
    def _state_durability(data):
        if not isinstance(data, dict):
            return {}
        source = data
        if isinstance(source.get("equipment"), dict):
            source = source["equipment"]
        for key in ("gear", "wearables"):
            if isinstance(source.get(key), dict):
                source = source[key]
                break
        durability = source.get("durability", source.get("equipment_durability", {}))
        if not isinstance(durability, dict):
            return {}
        row = durability.get("shield_armor", durability.get(ITEM_SHIELD_ARMOR, {}))
        return dict(row) if isinstance(row, dict) else {}

    def import_state(self, data, require_owned=True):
        """Load slots atomically and safely migrate saves without wearables.

        Existing saves only contain ``equipment.weapon``.  Such a payload has
        no recognized wearable slots and therefore imports as three empty
        slots without touching weapon state.  Invalid IDs, wrong-slot IDs and
        (by default) items missing from the backpack are also cleared.
        """
        raw_slots = self._state_slots(data)
        raw_shield = self._state_durability(data)
        rebuilt = {slot: "" for slot in EQUIPMENT_SLOTS}
        for slot in EQUIPMENT_SLOTS:
            raw = raw_slots.get(slot, "")
            if isinstance(raw, dict):
                raw = raw.get("item_id", raw.get("id", ""))
            item_id = normalize_equipment_item_id(raw)
            if not item_id or equipment_slot_for_item(item_id) != slot:
                continue
            if require_owned and self._owned_count(item_id) <= 0:
                continue
            rebuilt[slot] = item_id

        def number(key, default=0.0):
            try:
                return max(0.0, float(raw_shield.get(key, default)))
            except (TypeError, ValueError):
                return max(0.0, float(default))

        shield_max = number("max_hp")
        shield_hp = min(shield_max, number("hp")) if shield_max > 0.0 else 0.0
        shield_basis = number("player_max_hp")
        shield_initialized = bool(raw_shield.get("initialized", bool(shield_max > 0.0)))
        # A valid runtime can never save an equipped initialized shield at zero:
        # depletion consumes it synchronously. Treat that malformed/partial
        # state as an old-save migration and initialize it on player sync.
        if shield_max <= 0.0 or shield_hp <= 0.0:
            shield_initialized = False
            shield_hp = 0.0
            shield_max = 0.0
            shield_basis = 0.0
        try:
            shield_break_serial = max(0, int(raw_shield.get("break_serial", 0)))
        except (TypeError, ValueError):
            shield_break_serial = 0
        shield_owned = (not require_owned) or self._owned_count(ITEM_SHIELD_ARMOR) > 0

        with self._lock:
            if rebuilt != self._slots:
                self._slots = rebuilt
                self._revision += 1
            else:
                self._slots = rebuilt
            # Preserve durability while voluntarily unequipped, but never
            # resurrect a pool when the corresponding inventory item is gone.
            if shield_owned and shield_initialized:
                self._shield_hp = shield_hp
                self._shield_max_hp = shield_max
                self._shield_player_max_hp = shield_basis
                self._shield_initialized = True
            else:
                self._shield_hp = 0.0
                self._shield_max_hp = 0.0
                self._shield_player_max_hp = 0.0
                self._shield_initialized = False
            self._shield_break_serial = shield_break_serial
        self._import_wings(data)
        return self.snapshot()


__all__ = (
    "SLOT_FOOT",
    "SLOT_BODY",
    "SLOT_HEAD",
    "EQUIPMENT_SLOTS",
    "ITEM_WIND_WINGS",
    "WING_SECONDS",
    "ITEM_DOUBLE_JUMP_SHOES",
    "ITEM_AIR_DASH_SHOES",
    "ITEM_HOVER_SHOES",
    "ITEM_SHIELD_ARMOR",
    "ITEM_ELECTRIC_MOUSE_HELMET",
    "ITEM_SKY_PUPPY_HELMET",
    "HOVER_DURATION_SECONDS",
    "HOVER_HORIZONTAL_SPEED",
    "HOVER_VERTICAL_SPEED",
    "AIR_EVADE_DURATION_SECONDS",
    "AIR_EVADE_SPEED",
    "SHIELD_HP_MULTIPLIER",
    "EQUIPMENT_DEFS",
    "EquipmentSystem",
    "normalize_slot",
    "normalize_equipment_item_id",
    "is_equipment_item",
    "equipment_slot_for_item",
    "equipment_definition",
    "equipment_catalog",
)
