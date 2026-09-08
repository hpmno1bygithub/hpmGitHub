# -*- coding: utf-8 -*-
"""Bounded, indexed ground-item runtime for FIX70.

Only runtime-created ``drop_*`` items are governed by this module. Authored
items such as ``item_test`` remain ordinary scene entities and are never
merged, retired or charged against the dynamic budget.

The normal spawn path searches one small spatial bucket neighbourhood rather
than the whole Scene. A bounded O(MAX_DYNAMIC_WORLD_DROPS) consolidation pass
is used only when the hard cap is already full. The per-frame physics pass also
iterates the bounded index, so historical picked-up drops cannot make it slower
over time.
"""
from dataclasses import dataclass
import math

from config import GRAVITY, TILE_SIZE, CHUNK_SIZE
from entities.item import Item
from systems.inventory_system import InventorySystem


# Single source of truth for runtime, top-level save and map-session budgets.
MAX_DYNAMIC_WORLD_DROPS = 256
MAX_DROP_STACK_COUNT = 1_000_000
DROP_MERGE_RADIUS_PX = TILE_SIZE * 2.0
DROP_BUCKET_SIZE_PX = DROP_MERGE_RADIUS_PX
# Covers the widest current iPhone viewport half-width plus sprite margin.
DROP_PLAYER_PROTECT_RADIUS_PX = max(TILE_SIZE * 20.0, 800.0)
DROP_RECENT_SERIAL_WINDOW = 12
DROP_WORLD_MARGIN_PX = TILE_SIZE * 8.0
MAX_DROP_ABS_POSITION_PX = 100_000_000.0
MAX_DROP_ABS_SAVE_SPEED = 2048.0


def clean_drop_heavy_uses(item_id, count, values=None):
    """Return ordered per-copy durability for a weapon ground stack.

    World stacks can use the existing ground-item quantity ceiling, which is
    intentionally independent from the backpack's visual weapon stack size.
    Missing data is an old-save migration and therefore means full durability.
    Non-weapons always return ``None`` and never serialize this field.
    """
    key = str(item_id or "")
    limit = InventorySystem.weapon_heavy_use_limit(key)
    if limit <= 0:
        return None
    try:
        amount = int(count or 0)
    except (TypeError, ValueError, OverflowError):
        amount = 0
    amount = max(0, min(MAX_DROP_STACK_COUNT, amount))
    source = values if isinstance(values, (list, tuple)) else ()
    cleaned = []
    for raw in source[:amount]:
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            value = limit
        cleaned.append(max(1, min(limit, value)))
    if len(cleaned) < amount:
        cleaned.extend([limit] * (amount - len(cleaned)))
    return cleaned


def _normalize_item_heavy_uses(item):
    values = clean_drop_heavy_uses(
        getattr(item, "item_id", ""),
        getattr(item, "count", 0),
        getattr(item, "heavy_uses", None),
    )
    item.heavy_uses = values
    return values


def _append_item_heavy_uses(target, source_values):
    """Append copies after the target's existing order during a merge."""
    if InventorySystem.weapon_heavy_use_limit(getattr(target, "item_id", "")) <= 0:
        target.heavy_uses = None
        return
    incoming = list(source_values or ())
    # Callers have already increased ``target.count``.  Normalize only the
    # pre-merge prefix, otherwise missing old-save data would create one full
    # durability entry for each incoming copy before we append it again.
    old_count = max(0, int(getattr(target, "count", 0) or 0) - len(incoming))
    current = clean_drop_heavy_uses(
        getattr(target, "item_id", ""), old_count,
        getattr(target, "heavy_uses", None),
    ) or []
    current.extend(incoming)
    target.heavy_uses = current


@dataclass(frozen=True)
class DropSpawnResult:
    """Explicit accounting result for one requested world drop.

    ``accepted_count + rejected_count`` equals ``requested_count`` for every
    valid non-negative request. ``item`` is the new entity or the existing
    entity that received a local merge.
    """

    item: object = None
    requested_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    merged_count: int = 0
    consolidated_entities: int = 0
    reason: str = ""

    @property
    def fully_accepted(self):
        return int(self.rejected_count) == 0 and int(self.accepted_count) > 0

    def __bool__(self):
        return int(self.accepted_count) > 0


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _drop_serial(entity):
    try:
        value = int(getattr(entity, "drop_created_serial", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        value = 0
    if value > 0:
        return value
    entity_id = str(getattr(entity, "entity_id", "") or "")
    suffix = entity_id[5:] if entity_id.startswith("drop_") else ""
    return int(suffix) if suffix.isdigit() else 0


def dynamic_drop(entity):
    return isinstance(entity, Item) and str(
        getattr(entity, "entity_id", "") or ""
    ).startswith("drop_")


def item_value_tier(item_id):
    """Small deterministic protection heuristic; no inventory/catalog scan."""
    key = str(item_id or "").strip().lower()
    if key.startswith(("weapon_", "equipment.", "equipment_", "armor_")):
        return 4
    if any(token in key for token in (
        "boss", "legend", "artifact", "relic", "diamond", "mythic",
    )):
        return 4
    if any(token in key for token in (
        "rare", "crystal", "gem", "gold", "jade", "heart", "core",
    )):
        return 3
    if any(token in key for token in (
        "iron", "copper", "silver", "hide", "meat", "ore",
    )):
        return 2
    if any(token in key for token in (
        "soil", "dirt", "mud", "sand", "wood", "leaf", "stone",
        "moss", "reed", "gel", "ash",
    )):
        return 0
    return 1


class ItemDropSystem:
    MAX_DYNAMIC_DROPS = MAX_DYNAMIC_WORLD_DROPS
    MAX_STACK_COUNT = MAX_DROP_STACK_COUNT
    MERGE_RADIUS_PX = DROP_MERGE_RADIUS_PX

    def __init__(self, scene, physics, chunk_streamer, player=None):
        self.scene = scene
        self.physics = physics
        self.chunk_streamer = chunk_streamer
        self.player = player
        self._dynamic_by_id = {}
        self._bucket_by_id = {}
        self._buckets = {}
        self.last_export_rejected = 0
        self.startup_migration_merged_count = 0
        self.startup_migration_rejected_count = 0
        self.runtime_offworld_rejected_count = 0
        self._static_items = tuple(
            entity for entity in getattr(scene, "entities", ())
            if isinstance(entity, Item) and not dynamic_drop(entity)
        )
        # Migration safety for a Scene assembled before this system exists.
        # Retain near/high-value/newer rows first, then losslessly fold excess
        # duplicates into a retained same-item stack. Any impossible excess is
        # explicitly counted and removed instead of living outside the index.
        existing = [
            entity for entity in tuple(getattr(scene, "entities", ()))
            if dynamic_drop(entity) and entity.active and not entity.picked
        ]
        existing.sort(key=lambda entity: (
            self._initial_retention_distance(entity),
            -item_value_tier(getattr(entity, "item_id", "")),
            -_drop_serial(entity),
            str(getattr(entity, "entity_id", "")),
        ))
        overflow = []
        for entity in existing:
            _normalize_item_heavy_uses(entity)
            if not self._runtime_item_valid(entity):
                if not self._recover_offworld(entity):
                    overflow.append(entity)
                    continue
            if not self._register(entity):
                overflow.append(entity)
        for entity in overflow:
            count = self._safe_positive_count(entity)
            targets = [
                target for target in self._dynamic_by_id.values()
                if str(getattr(target, "item_id", ""))
                == str(getattr(entity, "item_id", ""))
                and self._safe_positive_count(target) + count
                <= MAX_DROP_STACK_COUNT
            ]
            targets.sort(key=lambda target: (
                self._initial_retention_distance(target),
                -_drop_serial(target),
                str(target.entity_id),
            ))
            if count > 0 and targets:
                target = targets[0]
                source_values = _normalize_item_heavy_uses(entity)
                # Normalize before changing count; then append the source in
                # its original copy order.
                _normalize_item_heavy_uses(target)
                target.count = int(target.count) + count
                _append_item_heavy_uses(target, source_values)
                self.startup_migration_merged_count += count
            else:
                self.startup_migration_rejected_count += count
            entity.active = False
            entity.picked = False
            try:
                self.scene.entities.remove(entity)
            except (ValueError, AttributeError):
                pass
        if self.startup_migration_rejected_count:
            print(
                "DROP MIGRATION overflow rejected:",
                int(self.startup_migration_rejected_count),
            )

    def _initial_retention_distance(self, item):
        """Sort key used before indexes are fully constructed."""
        player = self.player
        if player is None:
            return float("inf")
        try:
            dx = float(item.x) - float(getattr(player, "x", 0.0))
            dy = float(item.y) - float(getattr(player, "y", 0.0))
            distance2 = dx * dx + dy * dy
            return distance2 if math.isfinite(distance2) else float("inf")
        except (TypeError, ValueError, OverflowError):
            return float("inf")

    @staticmethod
    def _safe_positive_count(item):
        try:
            return max(0, min(
                MAX_DROP_STACK_COUNT,
                int(getattr(item, "count", 0) or 0),
            ))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _runtime_item_valid(self, item):
        x = _finite(getattr(item, "x", None))
        y = _finite(getattr(item, "y", None))
        vy = _finite(getattr(item, "vy", 0.0))
        count = self._safe_positive_count(item)
        return (
            x is not None and y is not None and vy is not None
            and count > 0 and count == int(getattr(item, "count", 0) or 0)
            and abs(vy) <= MAX_DROP_ABS_SAVE_SPEED
            and self._inside_reasonable_world_bounds(x, y)
        )

    def _recover_offworld(self, item):
        """Move anomalous loot to a nearby valid surface when one exists."""
        try:
            count = int(getattr(item, "count", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return False
        if not 0 < count <= MAX_DROP_STACK_COUNT:
            return False
        world = getattr(self.physics, "world", None)
        first_solid = getattr(world, "first_solid_row", None)
        width_tiles = max(0, int(getattr(world, "width_tiles", 0) or 0))
        if not callable(first_solid) or width_tiles < 3:
            return False
        x = _finite(getattr(item, "x", None))
        if x is None:
            x = _finite(getattr(self.player, "x", None))
        if x is None:
            x = width_tiles * TILE_SIZE * 0.5
        base_tx = max(1, min(width_tiles - 2, int(float(x) // TILE_SIZE)))
        offsets = [0]
        for radius in range(1, 9):
            offsets.extend((-radius, radius))
        for offset in offsets:
            tx = max(1, min(width_tiles - 2, base_tx + offset))
            try:
                row = first_solid(tx)
            except Exception:
                row = None
            if row is None:
                continue
            try:
                row = int(row)
            except (TypeError, ValueError, OverflowError):
                continue
            if row <= 0:
                continue
            item.x = (tx + 0.5) * TILE_SIZE
            item.y = float(row) * TILE_SIZE - 1.0
            item.vx = 0.0
            item.vy = 0.0
            item.grounded = True
            return True
        return False

    @staticmethod
    def is_dynamic(entity):
        return dynamic_drop(entity)

    def _bucket_key(self, item_id, x, y):
        size = float(DROP_BUCKET_SIZE_PX)
        return (
            str(item_id),
            int(math.floor(float(x) / size)),
            int(math.floor(float(y) / size)),
        )

    def _register(self, item):
        entity_id = str(getattr(item, "entity_id", "") or "")
        if not entity_id or entity_id in self._dynamic_by_id:
            return False
        if len(self._dynamic_by_id) >= MAX_DYNAMIC_WORLD_DROPS:
            return False
        _normalize_item_heavy_uses(item)
        key = self._bucket_key(item.item_id, item.x, item.y)
        self._dynamic_by_id[entity_id] = item
        self._bucket_by_id[entity_id] = key
        self._buckets.setdefault(key, {})[entity_id] = item
        return True

    def _unregister(self, item):
        entity_id = str(getattr(item, "entity_id", "") or "")
        self._dynamic_by_id.pop(entity_id, None)
        key = self._bucket_by_id.pop(entity_id, None)
        bucket = self._buckets.get(key)
        if bucket is not None:
            bucket.pop(entity_id, None)
            if not bucket:
                self._buckets.pop(key, None)

    def _move_bucket_if_needed(self, item):
        entity_id = str(getattr(item, "entity_id", "") or "")
        old_key = self._bucket_by_id.get(entity_id)
        new_key = self._bucket_key(item.item_id, item.x, item.y)
        if old_key == new_key:
            return
        old_bucket = self._buckets.get(old_key)
        if old_bucket is not None:
            old_bucket.pop(entity_id, None)
            if not old_bucket:
                self._buckets.pop(old_key, None)
        self._bucket_by_id[entity_id] = new_key
        self._buckets.setdefault(new_key, {})[entity_id] = item

    def contains_entity_id(self, entity_id):
        return str(entity_id or "") in self._dynamic_by_id

    def active_count(self):
        return len(self._dynamic_by_id)

    def dynamic_items(self):
        return tuple(sorted(
            self._dynamic_by_id.values(),
            key=lambda item: (_drop_serial(item), str(item.entity_id)),
        ))

    def total_quantity(self):
        return sum(
            max(0, int(getattr(item, "count", 0) or 0))
            for item in self._dynamic_by_id.values()
        )

    def _remove_from_scene(self, item):
        # Removal, rather than merely active=False, keeps resident count
        # bounded across repeated drop/pickup cycles.
        try:
            self.scene.entities.remove(item)
        except (ValueError, AttributeError):
            pass

    def retire(self, item, picked=True):
        """Immediately remove a consumed or consolidated dynamic item."""
        if not dynamic_drop(item):
            return False
        self._unregister(item)
        item.active = False
        item.picked = bool(picked)
        self._remove_from_scene(item)
        return True

    def clear_dynamic(self):
        """Remove only ``drop_*`` entities; authored/static items survive."""
        for item in tuple(self._dynamic_by_id.values()):
            item.active = False
            item.picked = True
        self._dynamic_by_id.clear()
        self._bucket_by_id.clear()
        self._buckets.clear()
        self.scene.entities[:] = [
            entity for entity in self.scene.entities if not dynamic_drop(entity)
        ]

    def _nearby_candidates(self, item_id, x, y):
        base = self._bucket_key(item_id, x, y)
        radius2 = float(DROP_MERGE_RADIUS_PX) ** 2
        rows = []
        for by in range(base[2] - 1, base[2] + 2):
            for bx in range(base[1] - 1, base[1] + 2):
                bucket = self._buckets.get((str(item_id), bx, by), {})
                for item in bucket.values():
                    if not item.active or item.picked:
                        continue
                    dx = float(item.x) - float(x)
                    dy = float(item.y) - float(y)
                    distance2 = dx * dx + dy * dy
                    if distance2 <= radius2:
                        rows.append((
                            distance2, -_drop_serial(item),
                            str(item.entity_id), item,
                        ))
        rows.sort(key=lambda row: row[:3])
        return [row[3] for row in rows]

    def _player_distance2(self, item):
        player = self.player
        if player is None:
            return float("inf")
        dx = float(item.x) - float(getattr(player, "x", 0.0))
        dy = float(item.y) - float(getattr(player, "y", 0.0))
        return dx * dx + dy * dy

    def _inside_reasonable_world_bounds(self, x, y):
        if (
            abs(float(x)) > MAX_DROP_ABS_POSITION_PX
            or abs(float(y)) > MAX_DROP_ABS_POSITION_PX
        ):
            return False
        world = getattr(self.physics, "world", None)
        width = float(getattr(world, "width_tiles", 0) or 0) * TILE_SIZE
        height = float(getattr(world, "height_tiles", 0) or 0) * TILE_SIZE
        if width <= 0.0 or height <= 0.0:
            return True
        margin = float(DROP_WORLD_MARGIN_PX)
        return (
            -margin <= float(x) <= width + margin
            and -margin <= float(y) <= height + margin
        )

    def _merge_same_item_at_capacity(
        self, item_id, remaining, incoming_heavy_uses=None,
        incoming_offset=0,
    ):
        """Losslessly absorb incoming common loot when all slots are occupied.

        This bounded global lookup occurs only at the hard cap. It prevents a
        mined block or creature death from losing ordinary loot merely because
        the nearest same-item stack is in another part of the current map.
        """
        targets = [
            item for item in self._dynamic_by_id.values()
            if str(getattr(item, "item_id", "")) == str(item_id)
            and item.active and not item.picked
            and int(getattr(item, "count", 0) or 0) < MAX_DROP_STACK_COUNT
        ]
        targets.sort(key=lambda target: (
            self._player_distance2(target),
            -_drop_serial(target),
            str(target.entity_id),
        ))
        moved_total = 0
        for target in targets:
            room = max(0, MAX_DROP_STACK_COUNT - int(target.count))
            moved = min(room, int(remaining) - moved_total)
            if moved <= 0:
                continue
            if incoming_heavy_uses is not None:
                values = incoming_heavy_uses[
                    int(incoming_offset) + moved_total:
                    int(incoming_offset) + moved_total + moved
                ]
                _normalize_item_heavy_uses(target)
            target.count = int(target.count) + moved
            if incoming_heavy_uses is not None:
                _append_item_heavy_uses(target, values)
            moved_total += moved
            if moved_total >= int(remaining):
                break
        return moved_total, (targets[0] if moved_total and targets else None)

    def _retire_stale_at_capacity(self):
        """Bounded fallback for custom callers that bypass pickup callback."""
        retired = 0
        for item in tuple(self._dynamic_by_id.values()):
            count = self._safe_positive_count(item)
            if not item.active or item.picked or count <= 0:
                retired += int(self.retire(
                    item, picked=bool(getattr(item, "picked", False)),
                ))
            elif not self._runtime_item_valid(item):
                if self._recover_offworld(item):
                    self._move_bucket_if_needed(item)
                else:
                    self.runtime_offworld_rejected_count += count
                    print(
                        "DROP off-world item rejected:",
                        str(item.entity_id), count,
                    )
                    retired += int(self.retire(item, picked=False))
        return retired

    def _consolidate_one_safe_entity(self):
        """Free one slot without destroying a single unit of loot.

        Only an old, distant, low-value duplicate may be retired, and its full
        quantity is first transferred to another entity with enough capacity.
        Near-player, high-value and recently-created entities are never chosen
        as the source. If no such lossless move exists the incoming request is
        rejected explicitly.
        """
        if len(self._dynamic_by_id) < MAX_DYNAMIC_WORLD_DROPS:
            return 0
        rows = tuple(self._dynamic_by_id.values())
        if not rows:
            return 0
        newest_serial = max(_drop_serial(item) for item in rows)
        protect2 = float(DROP_PLAYER_PROTECT_RADIUS_PX) ** 2
        by_item = {}
        for item in rows:
            by_item.setdefault(str(item.item_id), []).append(item)

        candidates = []
        for group in by_item.values():
            if len(group) < 2:
                continue
            for source in group:
                count = max(0, int(getattr(source, "count", 0) or 0))
                serial = _drop_serial(source)
                distance2 = self._player_distance2(source)
                tier = item_value_tier(source.item_id)
                if (
                    count <= 0
                    or distance2 <= protect2
                    or tier >= 3
                    or serial > newest_serial - DROP_RECENT_SERIAL_WINDOW
                ):
                    continue
                destinations = [
                    target for target in group
                    if target is not source
                    and max(0, int(getattr(target, "count", 0) or 0)) + count
                    <= MAX_DROP_STACK_COUNT
                ]
                if not destinations:
                    continue
                # Preserve the entity most useful to the player: nearest first,
                # then newest. This never changes total quantity.
                destinations.sort(key=lambda target: (
                    self._player_distance2(target),
                    -_drop_serial(target),
                    str(target.entity_id),
                ))
                candidates.append((
                    tier, serial, -distance2, str(source.entity_id),
                    source, destinations[0],
                ))
        if not candidates:
            return 0
        candidates.sort(key=lambda row: row[:4])
        source, target = candidates[0][4], candidates[0][5]
        moved = max(0, int(source.count))
        source_values = _normalize_item_heavy_uses(source)
        _normalize_item_heavy_uses(target)
        target.count = int(target.count) + moved
        if source_values is not None:
            _append_item_heavy_uses(target, source_values)
        self.retire(source, picked=False)
        return 1

    def spawn(
        self, item_id, name, count, x, y, item_factory,
        allow_merge=True, heavy_uses=None,
    ):
        """Merge or create one bounded dynamic drop and report accounting."""
        try:
            requested = int(count)
        except (TypeError, ValueError, OverflowError):
            return DropSpawnResult(reason="invalid_count")
        requested = max(0, requested)
        if requested <= 0:
            return DropSpawnResult(requested_count=requested, reason="empty")
        px = _finite(x)
        py = _finite(y)
        if px is None or py is None:
            return DropSpawnResult(
                requested_count=requested,
                rejected_count=requested,
                reason="non_finite_position",
            )
        if not self._inside_reasonable_world_bounds(px, py):
            return DropSpawnResult(
                requested_count=requested,
                rejected_count=requested,
                reason="outside_world_bounds",
            )
        key = str(item_id or "item")[:128]
        label = str(name or key or "物品")[:128]
        incoming_heavy_uses = clean_drop_heavy_uses(
            key, requested, heavy_uses,
        )
        incoming_offset = 0
        remaining = requested
        merged = 0
        receiving_item = None

        if allow_merge:
            for target in self._nearby_candidates(key, px, py):
                room = max(
                    0, MAX_DROP_STACK_COUNT - max(0, int(target.count))
                )
                if room <= 0:
                    continue
                moved = min(room, remaining)
                if incoming_heavy_uses is not None:
                    values = incoming_heavy_uses[
                        incoming_offset:incoming_offset + moved
                    ]
                    _normalize_item_heavy_uses(target)
                target.count = int(target.count) + moved
                if incoming_heavy_uses is not None:
                    _append_item_heavy_uses(target, values)
                    incoming_offset += moved
                remaining -= moved
                merged += moved
                receiving_item = target
                if remaining <= 0:
                    return DropSpawnResult(
                        item=receiving_item,
                        requested_count=requested,
                        accepted_count=requested,
                        merged_count=merged,
                        reason="merged",
                    )

        consolidated = 0
        if remaining > 0 and len(self._dynamic_by_id) >= MAX_DYNAMIC_WORLD_DROPS:
            self._retire_stale_at_capacity()
        # At capacity, a same-item transfer is always preferable to rejecting
        # tool/creature loot or rearranging an unrelated entity. This is the
        # only normal spawn path that performs a bounded whole-index lookup.
        if remaining > 0 and len(self._dynamic_by_id) >= MAX_DYNAMIC_WORLD_DROPS:
            moved, target = self._merge_same_item_at_capacity(
                key, remaining,
                incoming_heavy_uses=incoming_heavy_uses,
                incoming_offset=incoming_offset,
            )
            if moved:
                remaining -= moved
                merged += moved
                if incoming_heavy_uses is not None:
                    incoming_offset += moved
                receiving_item = target
                if remaining <= 0:
                    return DropSpawnResult(
                        item=receiving_item,
                        requested_count=requested,
                        accepted_count=requested,
                        merged_count=merged,
                        reason="capacity_merge",
                    )
        if remaining > 0 and len(self._dynamic_by_id) >= MAX_DYNAMIC_WORLD_DROPS:
            consolidated = self._consolidate_one_safe_entity()
        if remaining > 0 and len(self._dynamic_by_id) >= MAX_DYNAMIC_WORLD_DROPS:
            return DropSpawnResult(
                item=receiving_item,
                requested_count=requested,
                accepted_count=requested - remaining,
                rejected_count=remaining,
                merged_count=merged,
                consolidated_entities=consolidated,
                reason="dynamic_drop_cap",
            )

        created_count = min(remaining, MAX_DROP_STACK_COUNT)
        if created_count > 0:
            try:
                item = item_factory(key, label, created_count, px, py)
            except Exception:
                item = None
            if item is not None:
                item.heavy_uses = (
                    None if incoming_heavy_uses is None else list(
                        incoming_heavy_uses[
                            incoming_offset:incoming_offset + created_count
                        ]
                    )
                )
            if not dynamic_drop(item) or not self._register(item):
                return DropSpawnResult(
                    item=receiving_item,
                    requested_count=requested,
                    accepted_count=merged,
                    rejected_count=requested - merged,
                    merged_count=merged,
                    consolidated_entities=consolidated,
                    reason="entity_factory_failed",
                )
            self.scene.add_entity(item)
            receiving_item = item
            remaining -= created_count
            if incoming_heavy_uses is not None:
                incoming_offset += created_count

        accepted = requested - remaining
        return DropSpawnResult(
            item=receiving_item,
            requested_count=requested,
            accepted_count=accepted,
            rejected_count=remaining,
            merged_count=merged,
            consolidated_entities=consolidated,
            reason="spawned" if remaining <= 0 else "stack_limit",
        )

    def export_rows(self):
        """Finite, deterministic, cap-bounded save rows."""
        rows = []
        rejected = 0
        for item in self.dynamic_items():
            if not self._runtime_item_valid(item):
                if self._recover_offworld(item):
                    self._move_bucket_if_needed(item)
                else:
                    count = self._safe_positive_count(item)
                    self.runtime_offworld_rejected_count += count
                    rejected += 1
                    print(
                        "DROP invalid save item rejected:",
                        str(item.entity_id), count,
                    )
                    self.retire(item, picked=False)
                    continue
            x = _finite(getattr(item, "x", None))
            y = _finite(getattr(item, "y", None))
            vy = _finite(getattr(item, "vy", 0.0))
            try:
                count = int(getattr(item, "count", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                x is None or y is None or vy is None or count <= 0
                or count > MAX_DROP_STACK_COUNT
                or abs(vy) > MAX_DROP_ABS_SAVE_SPEED
                or not self._inside_reasonable_world_bounds(x, y)
            ):
                rejected += 1
                continue
            row = {
                "entity_id": str(item.entity_id)[:64],
                "item_id": str(item.item_id or "item")[:128],
                "name": str(item.name or "物品")[:128],
                "count": min(MAX_DROP_STACK_COUNT, count),
                "x": x,
                "y": y,
                "vy": vy,
            }
            values = _normalize_item_heavy_uses(item)
            if values is not None:
                row["heavy_uses"] = list(values)
            rows.append(row)
            if len(rows) >= MAX_DYNAMIC_WORLD_DROPS:
                break
        self.last_export_rejected = int(rejected)
        return rows

    def update(self, dt):
        dt = max(0.0, float(dt))
        # Index size is hard-bounded at 256. Consumed items normally retire
        # synchronously in GameApp._pickup_world_item; this stale check covers
        # older/custom callers without scanning the whole Scene.
        for item in tuple(self._dynamic_by_id.values()):
            count = self._safe_positive_count(item)
            if not item.active or item.picked or count <= 0:
                self.retire(item, picked=bool(getattr(item, "picked", False)))
                continue
            if not self._runtime_item_valid(item):
                if self._recover_offworld(item):
                    self._move_bucket_if_needed(item)
                    continue
                self.runtime_offworld_rejected_count += count
                print("DROP off-world item rejected:", str(item.entity_id), count)
                self.retire(item, picked=False)
                continue
            cx = int(item.x // (TILE_SIZE * CHUNK_SIZE))
            cy = int(item.y // (TILE_SIZE * CHUNK_SIZE))
            if self.chunk_streamer.is_active(cx, cy):
                item.vy = min(
                    520.0,
                    float(getattr(item, "vy", 0.0)) + GRAVITY * dt,
                )
                hit, grounded = self.physics.move_vertical(item, item.vy * dt)
                if hit:
                    item.vy = 0.0
                item.grounded = bool(grounded)
            self._move_bucket_if_needed(item)
        # Preserve FIX69 physics for the small authored/static item set without
        # registering or charging it against the dynamic drop budget.
        for item in self._static_items:
            if not item.active or item.picked:
                continue
            cx = int(item.x // (TILE_SIZE * CHUNK_SIZE))
            cy = int(item.y // (TILE_SIZE * CHUNK_SIZE))
            if not self.chunk_streamer.is_active(cx, cy):
                continue
            item.vy = min(
                520.0,
                float(getattr(item, "vy", 0.0)) + GRAVITY * dt,
            )
            hit, grounded = self.physics.move_vertical(item, item.vy * dt)
            if hit:
                item.vy = 0.0
            item.grounded = bool(grounded)
