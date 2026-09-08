# -*- coding: utf-8 -*-
"""Stacked backpack inventory for V0.7.2.4.

The inventory is gameplay-authoritative and independent from UIKit.  The UI
only calls these small methods and redraws a snapshot.  Slots are fixed-order
so move/swap operations are deterministic and serialize cleanly.
"""
from dataclasses import dataclass
import threading
from systems.boss_weapon_defs import BOSS_ITEM_LIMITS


@dataclass
class InventoryStack:
    item_id: str
    name: str
    count: int = 1
    max_stack: int = 99
    # Weapons still occupy one visual stack, but every physical copy keeps its
    # own remaining heavy-use budget.  ``None`` is used by ordinary items so
    # old saves and non-weapon stacks stay compact.
    heavy_uses: object = None

    def export(self):
        out = {
            "item_id": str(self.item_id),
            "name": str(self.name),
            "count": int(self.count),
            "max_stack": int(self.max_stack),
        }
        if str(self.item_id).startswith("weapon_"):
            out["heavy_uses"] = [int(value) for value in (self.heavy_uses or ())]
        return out


class InventorySystem:
    # Three virtualized pages. UIKit/Metal object count stays constant because
    # only one 24-slot page is drawn and hit-tested at a time.
    SLOT_COUNT = 72
    DEFAULT_MAX_STACK = 99
    WEAPON_MAX_STACK = 999
    WEAPON_HEAVY_USES_DEFAULT = 10
    WEAPON_HEAVY_USES_BY_ITEM = {
        # The kamikaze heavy consumes the active drone item immediately.
        "weapon_flying_drone": 1,
        # One TNT bundle may perform five 3x3 demolition throws.
        "weapon_tnt": 5,
        **BOSS_ITEM_LIMITS,
    }

    def __init__(self, events=None):
        self.events = events
        self._lock = threading.RLock()
        self.slots = [None for _ in range(self.SLOT_COUNT)]
        self.selected_index = 0
        self.selected_quantity = 1
        self.move_source = None
        self.move_quantity = 0

    @classmethod
    def weapon_heavy_use_limit(cls, item_id):
        item_id = str(item_id or "")
        if not item_id.startswith("weapon_"):
            return 0
        return max(0, min(
            999,
            int(cls.WEAPON_HEAVY_USES_BY_ITEM.get(
                item_id, cls.WEAPON_HEAVY_USES_DEFAULT,
            )),
        ))

    @classmethod
    def _clean_heavy_uses(cls, item_id, count, values=None):
        """Return one bounded durability entry per weapon in a stack.

        FIX70 and older saves have no ``heavy_uses`` field.  Missing entries are
        migrated to full durability, while malformed/oversized input is clipped
        before it can inflate a save or create an unusable zero-life weapon.
        """
        count = max(0, min(int(cls.WEAPON_MAX_STACK), int(count or 0)))
        limit = cls.weapon_heavy_use_limit(item_id)
        if limit <= 0:
            return None
        source = values if isinstance(values, (list, tuple)) else ()
        cleaned = []
        for raw in source[:count]:
            try:
                value = int(raw)
            except Exception:
                value = limit
            cleaned.append(max(1, min(limit, value)))
        if len(cleaned) < count:
            cleaned.extend([limit] * (count - len(cleaned)))
        return cleaned

    @classmethod
    def _normalize_stack_heavy_uses(cls, stack):
        if stack is None:
            return None
        stack.heavy_uses = cls._clean_heavy_uses(
            stack.item_id, stack.count, getattr(stack, "heavy_uses", None),
        )
        return stack.heavy_uses

    def _emit(self, text):
        if self.events is not None:
            self.events.emit("message", text=str(text))

    def snapshot(self):
        with self._lock:
            return {
                "slots": [None if s is None else s.export() for s in self.slots],
                "selected_index": int(self.selected_index),
                "selected_quantity": int(self.selected_quantity),
                "move_source": self.move_source,
                "move_quantity": int(self.move_quantity),
            }

    def select(self, index):
        with self._lock:
            index = max(0, min(self.SLOT_COUNT - 1, int(index)))
            if self.move_source is not None and index != self.move_source:
                self._finish_move_locked(index)
                return self.snapshot()
            self.selected_index = index
            stack = self.slots[index]
            if stack is None:
                self.selected_quantity = 1
            else:
                self.selected_quantity = max(1, min(int(stack.count), int(self.selected_quantity)))
            return self.snapshot()

    def set_quantity(self, value):
        with self._lock:
            stack = self.slots[self.selected_index]
            maximum = int(stack.count) if stack is not None else 1
            self.selected_quantity = max(1, min(maximum, int(value)))
            return self.selected_quantity

    def adjust_quantity(self, delta):
        return self.set_quantity(int(self.selected_quantity) + int(delta))

    def set_max_quantity(self):
        with self._lock:
            stack = self.slots[self.selected_index]
            self.selected_quantity = int(stack.count) if stack is not None else 1
            return self.selected_quantity

    def add(self, item_id, name, count=1, max_stack=None, heavy_uses=None):
        item_id = str(item_id)
        name = str(name)
        remaining = max(0, int(count))
        if remaining <= 0:
            return 0
        max_stack = max(1, int(max_stack or self.DEFAULT_MAX_STACK))
        if item_id.startswith("weapon_"):
            max_stack = max(max_stack, int(self.WEAPON_MAX_STACK))
        original = remaining
        incoming_heavy_uses = self._clean_heavy_uses(
            item_id, remaining, heavy_uses,
        )
        incoming_offset = 0
        with self._lock:
            # Weapons are stackable equipment entries. Older saves may still
            # carry max_stack=1 from FIX53 and earlier, so upgrade the existing
            # stack before calculating room.
            if item_id.startswith("weapon_"):
                for stack in self.slots:
                    if stack is not None and stack.item_id == item_id:
                        stack.max_stack = max(int(stack.max_stack), int(self.WEAPON_MAX_STACK))
                        self._normalize_stack_heavy_uses(stack)
            # Fill existing compatible stacks first.
            for stack in self.slots:
                if remaining <= 0:
                    break
                if stack is None or stack.item_id != item_id:
                    continue
                room = max(0, int(stack.max_stack) - int(stack.count))
                if room <= 0:
                    continue
                moved = min(room, remaining)
                stack.count += moved
                if incoming_heavy_uses is not None:
                    stack.heavy_uses.extend(
                        incoming_heavy_uses[incoming_offset:incoming_offset + moved]
                    )
                    incoming_offset += moved
                remaining -= moved

            # Then allocate empty slots.
            for i, stack in enumerate(self.slots):
                if remaining <= 0:
                    break
                if stack is not None:
                    continue
                moved = min(max_stack, remaining)
                moved_heavy_uses = None
                if incoming_heavy_uses is not None:
                    moved_heavy_uses = list(
                        incoming_heavy_uses[incoming_offset:incoming_offset + moved]
                    )
                    incoming_offset += moved
                self.slots[i] = InventoryStack(
                    item_id, name, moved, max_stack, moved_heavy_uses,
                )
                remaining -= moved

            if self.slots[self.selected_index] is not None:
                self.selected_quantity = min(
                    max(1, int(self.selected_quantity)),
                    int(self.slots[self.selected_index].count),
                )
        return original - remaining

    def remove_selected(self, quantity=None):
        with self._lock:
            index = int(self.selected_index)
            stack = self.slots[index]
            if stack is None:
                return None
            qty = int(self.selected_quantity if quantity is None else quantity)
            qty = max(1, min(int(stack.count), qty))
            self._normalize_stack_heavy_uses(stack)
            removed_heavy_uses = None
            if stack.heavy_uses is not None:
                removed_heavy_uses = list(stack.heavy_uses[:qty])
                del stack.heavy_uses[:qty]
            removed = InventoryStack(
                stack.item_id, stack.name, qty, stack.max_stack,
                removed_heavy_uses,
            )
            stack.count -= qty
            if stack.count <= 0:
                self.slots[index] = None
                self.selected_quantity = 1
            else:
                self.selected_quantity = min(self.selected_quantity, stack.count)
            return removed

    def count_item(self, item_id):
        item_id = str(item_id)
        with self._lock:
            return sum(
                int(stack.count)
                for stack in self.slots
                if stack is not None and stack.item_id == item_id
            )

    def consume_item(self, item_id, count=1):
        """Atomically consume an item ID even when its slot is not selected."""
        item_id = str(item_id)
        requested = max(0, int(count))
        if requested <= 0:
            return 0
        with self._lock:
            available = sum(
                int(stack.count)
                for stack in self.slots
                if stack is not None and stack.item_id == item_id
            )
            if available < requested:
                return 0
            remaining = requested
            for index, stack in enumerate(self.slots):
                if remaining <= 0:
                    break
                if stack is None or stack.item_id != item_id:
                    continue
                self._normalize_stack_heavy_uses(stack)
                moved = min(int(stack.count), remaining)
                stack.count -= moved
                if stack.heavy_uses is not None:
                    del stack.heavy_uses[:moved]
                remaining -= moved
                if stack.count <= 0:
                    self.slots[index] = None
            selected = self.slots[int(self.selected_index)]
            self.selected_quantity = (
                1 if selected is None
                else max(1, min(int(self.selected_quantity), int(selected.count)))
            )
            return requested

    def first_owned_weapon(self, exclude=()):
        excluded = {str(value) for value in (exclude or ())}
        with self._lock:
            for stack in self.slots:
                if stack is None or int(stack.count) <= 0:
                    continue
                item_id = str(stack.item_id)
                if item_id.startswith("weapon_") and item_id not in excluded:
                    return item_id
        return ""

    def weapon_durability(self, item_id):
        """Return the active copy's heavy-use budget without exposing internals."""
        item_id = str(item_id or "")
        limit = self.weapon_heavy_use_limit(item_id)
        if limit <= 0:
            unlimited = item_id in BOSS_ITEM_LIMITS and BOSS_ITEM_LIMITS[item_id] == 0
            count = self.count_item(item_id) if unlimited else 0
            return {"owned": count > 0, "unlimited": unlimited,
                    "remaining": 0, "maximum": 0, "count": count}
        with self._lock:
            for stack in self.slots:
                if stack is None or stack.item_id != item_id or int(stack.count) <= 0:
                    continue
                values = self._normalize_stack_heavy_uses(stack) or []
                return {
                    "owned": True,
                    "remaining": int(values[0]) if values else limit,
                    "maximum": int(limit),
                    "count": int(stack.count),
                }
        return {"owned": False, "remaining": 0, "maximum": int(limit), "count": 0}

    def commit_weapon_heavy_use(self, item_id):
        """Spend one successful heavy attack and retire a broken weapon copy.

        The first entry is the copy currently represented by the equipped stack.
        A normal attack never calls this method.  The final permitted heavy hit
        is accepted, then that copy disappears; a stacked replacement begins at
        full durability without occupying another backpack slot.
        """
        item_id = str(item_id or "")
        limit = self.weapon_heavy_use_limit(item_id)
        if limit <= 0:
            count = self.count_item(item_id)
            unlimited = item_id in BOSS_ITEM_LIMITS and BOSS_ITEM_LIMITS[item_id] == 0
            return {
                "accepted": bool(unlimited and count > 0), "unlimited": unlimited,
                "broken": False, "remaining": 0, "maximum": 0,
                "count": count, "item_id": item_id,
            }
        with self._lock:
            for index, stack in enumerate(self.slots):
                if stack is None or stack.item_id != item_id or int(stack.count) <= 0:
                    continue
                values = self._normalize_stack_heavy_uses(stack) or []
                if not values:
                    return {
                        "accepted": False, "broken": False, "remaining": 0,
                        "maximum": int(limit), "count": 0, "item_id": item_id,
                    }
                values[0] = max(0, int(values[0]) - 1)
                broken = values[0] <= 0
                if broken:
                    del values[0]
                    stack.count -= 1
                    if stack.count <= 0:
                        self.slots[index] = None
                    if int(self.selected_index) == index:
                        chosen = self.slots[index]
                        self.selected_quantity = (
                            1 if chosen is None else max(
                                1, min(int(self.selected_quantity), int(chosen.count)),
                            )
                        )
                remaining = int(values[0]) if values else 0
                return {
                    "accepted": True,
                    "broken": bool(broken),
                    "remaining": remaining,
                    "maximum": int(limit),
                    "count": max(0, int(stack.count)),
                    "item_id": item_id,
                    "slot": int(index),
                }
        return {
            "accepted": False, "broken": False, "remaining": 0,
            "maximum": int(limit), "count": 0, "item_id": item_id,
        }

    def begin_move(self):
        with self._lock:
            if self.slots[self.selected_index] is None:
                self.move_source = None
                return False
            self.move_source = int(self.selected_index)
            stack = self.slots[self.move_source]
            self.move_quantity = max(1, min(int(stack.count), int(self.selected_quantity)))
            return True

    def cancel_move(self):
        with self._lock:
            self.move_source = None
            self.move_quantity = 0

    def _finish_move_locked(self, destination):
        src = self.move_source
        qty = int(self.move_quantity)
        self.move_source = None
        self.move_quantity = 0
        if src is None:
            return False
        src = int(src)
        destination = max(0, min(self.SLOT_COUNT - 1, int(destination)))
        if src == destination:
            self.selected_index = destination
            return True

        source = self.slots[src]
        target = self.slots[destination]
        if source is None:
            return False
        qty = max(1, min(int(source.count), qty if qty > 0 else int(source.count)))

        if target is None:
            self._normalize_stack_heavy_uses(source)
            moved_heavy_uses = None
            if source.heavy_uses is not None:
                moved_heavy_uses = list(source.heavy_uses[:qty])
                del source.heavy_uses[:qty]
            self.slots[destination] = InventoryStack(
                source.item_id, source.name, qty, source.max_stack,
                moved_heavy_uses,
            )
            source.count -= qty
            if source.count <= 0:
                self.slots[src] = None
        elif target.item_id == source.item_id:
            room = max(0, int(target.max_stack) - int(target.count))
            moved = min(room, qty)
            if moved <= 0:
                self._emit("目標堆疊已滿")
                return False
            self._normalize_stack_heavy_uses(source)
            self._normalize_stack_heavy_uses(target)
            if source.heavy_uses is not None and target.heavy_uses is not None:
                target.heavy_uses.extend(source.heavy_uses[:moved])
                del source.heavy_uses[:moved]
            target.count += moved
            source.count -= moved
            if source.count <= 0:
                self.slots[src] = None
        else:
            # Swapping a different item is only unambiguous for a whole stack.
            if qty != int(source.count):
                self._emit("部分搬移請選空欄或同類物品")
                self.selected_index = src
                return False
            self.slots[src], self.slots[destination] = target, source

        self.selected_index = destination
        chosen = self.slots[destination]
        self.selected_quantity = 1 if chosen is None else min(max(1, qty), chosen.count)
        return True

    def _consolidate_weapon_stacks_locked(self):
        """Merge duplicate weapon stacks into the first slot for each weapon.

        This is both the normal invariant for new gameplay and a migration for
        pre-FIX54 saves where every duplicate weapon used a separate slot with
        max_stack=1. The selected slot follows the merged destination.
        """
        first_by_id = {}
        selected_redirect = None
        for i, stack in enumerate(self.slots):
            if stack is None or not str(stack.item_id).startswith("weapon_"):
                continue
            stack.max_stack = max(int(stack.max_stack), int(self.WEAPON_MAX_STACK))
            key = str(stack.item_id)
            if key not in first_by_id:
                self._normalize_stack_heavy_uses(stack)
                first_by_id[key] = i
                continue
            dst_i = first_by_id[key]
            dst = self.slots[dst_i]
            if dst is None:
                first_by_id[key] = i
                continue
            self._normalize_stack_heavy_uses(dst)
            self._normalize_stack_heavy_uses(stack)
            dst_count = int(dst.count)
            stack_count = int(stack.count)
            dst.count = dst_count + stack_count
            if dst.heavy_uses is not None and stack.heavy_uses is not None:
                dst.heavy_uses.extend(stack.heavy_uses[:stack_count])
            dst.max_stack = max(int(dst.max_stack), int(self.WEAPON_MAX_STACK), int(dst.count))
            if int(self.selected_index) == i:
                selected_redirect = dst_i
            self.slots[i] = None
        if selected_redirect is not None:
            self.selected_index = int(selected_redirect)
        chosen = self.slots[int(self.selected_index)] if 0 <= int(self.selected_index) < len(self.slots) else None
        self.selected_quantity = 1 if chosen is None else max(1, min(int(self.selected_quantity), int(chosen.count)))

    def export_state(self):
        with self._lock:
            return {
                "slots": [None if s is None else s.export() for s in self.slots],
                "selected_index": int(self.selected_index),
            }

    def import_state(self, data):
        with self._lock:
            slots = list((data or {}).get("slots", []))
            rebuilt = []
            for raw in slots[:self.SLOT_COUNT]:
                if not isinstance(raw, dict):
                    rebuilt.append(None)
                    continue
                count = max(0, int(raw.get("count", 0)))
                if count <= 0:
                    rebuilt.append(None)
                    continue
                rebuilt.append(InventoryStack(
                    str(raw.get("item_id", "item")),
                    str(raw.get("name", raw.get("item_id", "物品"))),
                    count,
                    max(1, int(raw.get("max_stack", self.DEFAULT_MAX_STACK))),
                    raw.get("heavy_uses"),
                ))
            while len(rebuilt) < self.SLOT_COUNT:
                rebuilt.append(None)
            self.slots = rebuilt
            self.selected_index = max(0, min(self.SLOT_COUNT - 1, int((data or {}).get("selected_index", 0))))
            self.selected_quantity = 1
            self._consolidate_weapon_stacks_locked()
            self.move_source = None
            self.move_quantity = 0
