# -*- coding: utf-8 -*-
import math
import itertools

_WORLD_CACHE_SERIAL = itertools.count(1)

from config import (
    TILE_SIZE,
    CHUNK_SIZE,
    WORLD_WIDTH_TILES,
    WORLD_HEIGHT_TILES,
    GROUND_ROW,
)
from engine.math2d import rects_overlap
from world.chunk import Chunk
from world.tile_registry import (
    AIR,
    DIRT,
    GRASS_DIRT,
    LAYERED_GROUND_TILES,
    LAYERED_SOLID_TILES,
    WOOD,
    STONE,
    LADDER,
    ICE,
    RAINFOREST_TREE_TILES,
    RAINFOREST_BRANCH,
    tile_def,
)
from world.ice_layers import ice_height_ratio_for_mass
from world.soil_layers import SOIL_LAYER_FULL, normalize_soil_mask, soil_vertical_bounds, soil_top_ratio

_LAYERED_SOLID_TILE_SET = frozenset(int(v) for v in LAYERED_SOLID_TILES)


class TileWorld:
    """Chunked tile world.

    The world stores tile IDs only. Rendering and gameplay properties
    are looked up through TileDef.
    """

    def __init__(self):
        self.chunks = {}
        self.width_tiles = WORLD_WIDTH_TILES
        self.height_tiles = WORLD_HEIGHT_TILES

        # Only runtime edits are stored here. Initial test-world
        # generation does not enter the save delta.
        self.modified_tiles = {}
        self._modified_by_chunk = {}

        # V0.7.1: optional indexed binary base terrain. Only chunks touched by
        # rendering/physics/active simulation are materialized in Python RAM.
        self.backing_store = None
        self._backing_code_to_tile_id = None

        # Global revision is kept for save/debug compatibility, but V0.5.9
        # renderers use per-chunk revisions so a single mined tile does not
        # invalidate the whole visible terrain cache.
        self.revision = 0
        # Render-cache epoch must also be unique across *different* TileWorld
        # instances. Portal travel constructs a fresh GameApp but keeps the
        # same Metal renderer; a local 0/1/2 counter could therefore match the
        # previous map and reuse stale terrain quads (visible as an invisible
        # wall when physics was already on the new map). Start every world in a
        # disjoint numeric epoch range, then ordinary resets still increment it.
        self.cache_epoch = next(_WORLD_CACHE_SERIAL) * 1000000
        # Runtime listeners let dense C/NumPy mirrors patch exactly one cell
        # when mining/placing changes terrain.
        self._tile_change_listeners = []

        # Column surface cache. -2 = unknown, -1 = no solid tile, >=0 = first
        # solid row. Mining only dirties the edited X column.
        self._surface_unknown = -2
        self._surface_none = -1
        self._surface_rows = [
            self._surface_unknown
            for _ in range(self.width_tiles)
        ]

        # V0.7.1.7 visual back-layer baseline.  The foreground terrain may be
        # mined away, but a 2D world still needs an earth/rock backdrop below
        # the ORIGINAL surface so tunnels do not reveal the sky colour.
        #
        # This is intentionally sparse: a column is remembered only immediately
        # before its first runtime edit. Unedited columns can use the current
        # first-solid cache because current == original there.
        self._backdrop_surface_rows = {}
        # Fractional top edge of the original layered surface tile. Without
        # this, a 1/3 or 2/3 surface tile would reveal the underground backdrop
        # through its intentionally empty upper portion before the player mined it.
        self._backdrop_surface_top_ratios = {}

        # EnvironmentState is attached after EnvironmentManager construction.
        # It lets physics use exact ice_mass for partial-height ice collision
        # without duplicating ice into three unrelated tile IDs.
        self.environment_state = None
        # V0.7.6.0 authored underground free-water conduit. Porous terrain may
        # recharge this row vertically; only free water in this open row is
        # allowed to move horizontally underground.
        self.underground_waterway_row = None
        # V0.7.6.8: excavated dry underground air pockets resist liquid entry
        # until the player actually opens a cell directly into free water.
        self.pressurized_air_cells = set()
        # FIX35 author-created map overlays may optionally be solid without
        # consuming a built-in terrain tile ID. Renderer reads the same cells
        # from MapRuntimeState so custom collision can never be invisible.
        self.custom_solid_cells = set()
        self.custom_platform_cells = set()
        self.custom_ladder_cells = set()
        self.custom_climb_platform_cells = set()
        # FIX45 authored landmarks can protect their real terrain cells from
        # mining and can opt out of wall climbing.  Both sets refer to the
        # ordinary foreground tiles used by rendering and collision; there is
        # no second invisible/background hull.
        self.indestructible_cells = set()
        self.no_climb_cells = set()
        # The main map is a horizontal cylinder. Child/dungeon maps leave this
        # disabled and retain their ordinary hard boundaries.
        self.horizontal_wrap_enabled = False

        # FIX68 rainforest megatrees are real saved terrain segments, but keep
        # a sparse authored support graph so removing a trunk/branch cannot
        # leave invisible floating collision. The immutable natural surface is
        # stored separately because canopy platforms must not reclassify the
        # open-air rainforest as underground backdrop.
        self.rainforest_tree_segments = {}
        self.rainforest_tree_children = {}
        self.rainforest_tree_specs = {}
        self._rainforest_tree_mutating = False
        self._authored_natural_surface_rows = {}
        self._authored_natural_surface_top_ratios = {}

    def attach_environment_state(self, state):
        self.environment_state = state

    def configure_rainforest_tree_system(self, payload=None):
        """Attach FIX68's sparse tree support/render metadata.

        ``payload`` is deliberately plain JSON data from map metadata. Tree
        tiles remain authoritative for collision and saves; this index only
        records parent/child support and restores the original terrain surface
        hidden behind tall tree platforms.
        """
        data = payload if isinstance(payload, dict) else {}
        self.rainforest_tree_segments.clear()
        self.rainforest_tree_children.clear()
        self.rainforest_tree_specs.clear()
        self._authored_natural_surface_rows.clear()
        self._authored_natural_surface_top_ratios.clear()

        for row in data.get("natural_surface", ()) or ():
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                tx, ty = int(row[0]), int(row[1])
                ratio = float(row[2]) if len(row) >= 3 else 0.0
            except Exception:
                continue
            if 0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles:
                self._authored_natural_surface_rows[tx] = ty
                self._authored_natural_surface_top_ratios[tx] = max(0.0, min(1.0, ratio))

        for raw in data.get("trees", ()) or ():
            if not isinstance(raw, dict):
                continue
            tree_id = str(raw.get("id", "")).strip()
            if tree_id:
                self.rainforest_tree_specs[tree_id] = dict(raw)

        for row in data.get("segments", ()) or ():
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                tx, ty = int(row[0]), int(row[1])
                role, tree_id = str(row[2]), str(row[3])
                parent_raw = row[4]
                parent = None
                if isinstance(parent_raw, (list, tuple)) and len(parent_raw) >= 2:
                    parent = (int(parent_raw[0]), int(parent_raw[1]))
                wood_yield = max(0, int(row[5])) if len(row) >= 6 else 0
                leaf_yield = max(0, int(row[6])) if len(row) >= 7 else 0
            except Exception:
                continue
            key = (tx, ty)
            if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
                continue
            meta = {
                "x": tx,
                "y": ty,
                "role": role,
                "tree_id": tree_id,
                "parent": parent,
                "wood_yield": wood_yield,
                "leaf_yield": leaf_yield,
            }
            self.rainforest_tree_segments[key] = meta
            if parent is not None:
                self.rainforest_tree_children.setdefault(parent, set()).add(key)

        # Binary terrain surface rows count every non-AIR palette entry. Vines
        # and leaves are passable, so replace just the rainforest columns with
        # the authored first *solid* row. This remains O(tree cells), never a
        # full-world scan on iPhone.
        self._ensure_surface_cache()
        solid_rows = dict(self._authored_natural_surface_rows)
        for (tx, ty), _meta in self.rainforest_tree_segments.items():
            if tile_def(self.get_tile(tx, ty)).solid:
                current = solid_rows.get(tx)
                if current is None or ty < current:
                    solid_rows[tx] = ty
        for tx in self._authored_natural_surface_rows:
            row = solid_rows.get(tx)
            self._surface_rows[tx] = self._surface_none if row is None else int(row)

        self.cache_epoch += 1
        return len(self.rainforest_tree_specs)

    def configure_cloud_islands(self, metadata=None):
        """Keep natural ground distinct from high-altitude collision surfaces."""
        data=metadata if isinstance(metadata,dict) else {}
        self.fauna_surface_rows = tuple(data.get("fauna_surface_rows", ()) or ())
        self.cloud_ladder_landings={}
        for route in data.get('sky_access_routes',()):
            tx=int(route['x'])
            self.cloud_ladder_landings[tx]=tuple(int(r['bounds'][1]) for r in data.get('sky_islands',())
                if r.get('cloud_bank') and r['bounds'][0]<=tx<=r['bounds'][2])
        if not data.get("sky_extension"):
            return
        initial_masks={(int(r[0]),int(r[1])):int(r[2])
                       for r in data.get("initial_ground_layers",()) if len(r)>=3}
        for row in data.get("natural_surface", ()):
            if len(row)<2: continue
            tx,ty=int(row[0]),int(row[1])
            if 0<=tx<self.width_tiles and 0<=ty<self.height_tiles:
                self._authored_natural_surface_rows[tx]=ty
                ratio=float(row[2]) if len(row)>2 else 0.
                if (tx,ty) in initial_masks:
                    ratio=float(soil_top_ratio(initial_masks[(tx,ty)]))
                self._authored_natural_surface_top_ratios[tx]=ratio
        columns=set()
        for island in data.get("sky_islands", ()):
            b=island.get("bounds", ())
            if len(b)==4: columns.update(range(max(0,int(b[0])),min(self.width_tiles,int(b[2])+1)))
        for route in data.get("sky_access_routes", ()):
            columns.add(int(route['x']))
        self._ensure_surface_cache()
        for tx in columns:
            if 0<=tx<self.width_tiles:self._surface_rows[tx]=self._surface_unknown
        self.cache_epoch+=1

    def fauna_surface_row(self, tx):
        """Original ground/perch index, used only for non-cloud populations."""
        tx=int(tx)
        rows=getattr(self,'fauna_surface_rows',())
        if 0<=tx<len(rows):return rows[tx]
        return self.first_solid_row(tx)

    def rainforest_tree_segment_at(self, tx, ty):
        return self.rainforest_tree_segments.get((int(tx), int(ty)))

    def iter_rainforest_tree_segments(self):
        """Yield live tree cells for renderer/debug integrations."""
        for key, meta in tuple(self.rainforest_tree_segments.items()):
            tx, ty = key
            tile_id = int(self.get_tile(tx, ty))
            if tile_id in RAINFOREST_TREE_TILES:
                yield tx, ty, tile_id, dict(meta)

    def _rainforest_descendants(self, root_key, include_root=True):
        root_key = (int(root_key[0]), int(root_key[1]))
        stack = [root_key]
        seen = set()
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            stack.extend(self.rainforest_tree_children.get(key, ()))
        if not include_root:
            seen.discard(root_key)
        return seen

    def remove_rainforest_tree_segment(self, tx, ty, track_change=True):
        """Remove one chopped segment and every child that loses support.

        The returned compact records let ToolSystem aggregate wood/leaves into
        at most two physical drops, rather than spawning one object per tile.
        """
        root = (int(tx), int(ty))
        if root not in self.rainforest_tree_segments:
            return tuple()
        keys = self._rainforest_descendants(root, include_root=True)
        removed = []
        self._rainforest_tree_mutating = True
        try:
            # Leaves first makes the result deterministic and prevents a
            # transient unsupported platform frame during large base chops.
            for key in sorted(keys, key=lambda item: (item[1], item[0])):
                meta = self.rainforest_tree_segments.get(key)
                tile_id = int(self.get_tile(*key))
                if meta is not None and tile_id in RAINFOREST_TREE_TILES:
                    record = dict(meta)
                    record["tile_id"] = tile_id
                    removed.append(record)
                    self.set_tile(key[0], key[1], AIR, track_change=bool(track_change))
        finally:
            self._rainforest_tree_mutating = False
        for key in keys:
            self.rainforest_tree_segments.pop(key, None)
            self.rainforest_tree_children.pop(key, None)
        for children in self.rainforest_tree_children.values():
            children.difference_update(keys)
        return tuple(removed)

    def _prune_rainforest_children_after_support_loss(self, tx, ty, track_change):
        """Environmental fallback for burn/reaction-driven support removal."""
        root = (int(tx), int(ty))
        keys = self._rainforest_descendants(root, include_root=False)
        if not keys:
            self.rainforest_tree_segments.pop(root, None)
            return
        self._rainforest_tree_mutating = True
        try:
            for key in sorted(keys, key=lambda item: (item[1], item[0])):
                if int(self.get_tile(*key)) in RAINFOREST_TREE_TILES:
                    self.set_tile(key[0], key[1], AIR, track_change=bool(track_change))
        finally:
            self._rainforest_tree_mutating = False
        keys.add(root)
        for key in keys:
            self.rainforest_tree_segments.pop(key, None)
            self.rainforest_tree_children.pop(key, None)
        for children in self.rainforest_tree_children.values():
            children.difference_update(keys)

    def tile_layer_mask_at(self, tx, ty):
        """Shared LOW/MID/HIGH geometry mask for ordinary solid blocks.

        The sparse backing table is intentionally kept as ``soil_layers`` for
        save compatibility. Hydrology still interprets masks only for porous
        LAYERED_GROUND_TILES; stone/ore/wood use the mask purely as geometry.
        """
        tile_id = self.get_tile(int(tx), int(ty))
        if tile_id not in LAYERED_SOLID_TILES:
            return SOIL_LAYER_FULL
        env = self.environment_state
        if env is None:
            return SOIL_LAYER_FULL
        return normalize_soil_mask(env.soil_layers.get((int(tx), int(ty)), SOIL_LAYER_FULL))

    def soil_layer_mask_at(self, tx, ty):
        # Compatibility alias used by older soil/sand code.
        return self.tile_layer_mask_at(tx, ty)

    def solid_top_y(self, tx, ty):
        """Return the real top surface Y of one solid cell in world pixels.

        V0.7.6.6 makes fractional terrain geometry authoritative for every
        surface-attached system, not just terrain drawing/collision.  A LOW
        block therefore has its support surface at 2/3 of the tile height and
        a MID block at 1/3. ICE uses its own mass-driven rectangle.
        """
        tx = int(tx); ty = int(ty)
        if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
            return None
        tile_id = self.get_tile(tx, ty)
        if (not tile_def(tile_id).solid and ((tx, ty) in self.custom_solid_cells
                or (tx, ty) in self.custom_platform_cells)):
            return float(ty * TILE_SIZE)
        if not tile_def(tile_id).solid:
            return None
        if tile_id == ICE:
            rect = self.ice_rect_at(tx, ty)
            return None if rect is None else float(rect[1])
        if tile_id in LAYERED_SOLID_TILES:
            mask = self.tile_layer_mask_at(tx, ty)
            if mask <= 0:
                return None
            top_ratio, _bottom_ratio = soil_vertical_bounds(mask)
            return ty * TILE_SIZE + TILE_SIZE * float(top_ratio)
        return ty * TILE_SIZE

    def surface_anchor_y(self, tx, ty):
        """Shared ground anchor for plants/decorations stored by tile key.

        Map vegetation is normally keyed to the supporting terrain cell.  If a
        legacy/custom map instead stores it in the air cell immediately above,
        fall back to the real top surface of the block below.
        """
        tx = int(tx); ty = int(ty)
        top = self.solid_top_y(tx, ty)
        if top is not None:
            return float(top)
        # Legacy/generated vegetation can be keyed more than one air cell above
        # a LOW/MID surface. Search a short vertical support column so the
        # renderer, hitboxes and tools all share the same non-floating anchor.
        for sy in range(ty + 1, min(self.height_tiles, ty + 7)):
            top = self.solid_top_y(tx, sy)
            if top is not None:
                return float(top)
        return float(ty * TILE_SIZE)


    def plant_anchor_y(self, tx, ty, species=""):
        """Return a stable vegetation root anchor.

        Trees are rooted into the immutable soil/rock backdrop, not a loose
        rigid body sitting on the currently visible foreground tile. Mining
        foreground soil below a trunk therefore reveals the earth back-wall
        without making the whole tree fall downward. Short vegetation keeps
        using the live fractional support surface.
        """
        tx = int(tx); ty = int(ty)
        species = str(species or "")
        tree_like = species in ("map_tree", "map_jungle_tree", "map_pine")
        if tree_like:
            try:
                row = self.backdrop_surface_row(tx)
                if row is not None:
                    # Map vegetation is keyed at/near its original surface.
                    # Use the preserved fractional backdrop only when it is
                    # reasonably close to the stored root key; this avoids
                    # snapping custom underground trees to the overworld.
                    if abs(int(row) - ty) <= 2:
                        ratio = float(self.backdrop_surface_top_ratio(tx))
                        return float(row * TILE_SIZE + TILE_SIZE * ratio)
            except Exception:
                pass
        return float(self.surface_anchor_y(tx, ty))

    def liquid_capacity_at(self, tx, ty):
        """Free-liquid capacity of one tile in full-tile water units.

        V0.7.7.0 makes LOW/MID layered geometry visible to hydrology itself,
        instead of treating every non-empty terrain tile as a full 40px wall.
        A bottom-third block therefore leaves 2/3 of the cell available to
        free water, while a full block leaves no free-liquid volume.
        """
        tx = int(tx); ty = int(ty)
        if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
            return 0.0
        tile_id = self.get_tile(tx, ty)
        if tile_id == AIR:
            if self.liquid_blocked_by_air_pressure(tx, ty):
                return 0.0
            return 1.0
        if bool(getattr(tile_def(tile_id), "one_way_platform", False)):
            # A branch platform supports actors only. Water and gases may pass
            # around/through its open pixel gaps instead of treating the whole
            # tile cell as an impermeable dam.
            return 1.0
        if tile_id in LAYERED_SOLID_TILES:
            mask = self.tile_layer_mask_at(tx, ty)
            top_ratio, _bottom_ratio = soil_vertical_bounds(mask)
            return max(0.0, min(1.0, float(top_ratio)))
        # ICE and ordinary/full solids remain true liquid boundaries.
        if tile_def(tile_id).solid:
            return 0.0
        return 1.0

    def liquid_has_internal_floor(self, tx, ty):
        """True when water in this cell rests on fractional solid geometry."""
        cap = float(self.liquid_capacity_at(tx, ty))
        return 1e-9 < cap < 1.0 - 1e-9

    def release_pressurized_air_component(self, tx, ty):
        """Release every connected pressure-locked AIR cell near (tx, ty).

        A dry cavity may stay pressure-locked while sealed. Once seawater,
        lake water, or seepage actually reaches an opening, the whole connected
        cavity must become one ordinary air/water region; otherwise left and
        right water bodies can remain logically separated forever.
        """
        starts = []
        tx = int(tx); ty = int(ty)
        for key in ((tx, ty), (tx-1, ty), (tx+1, ty), (tx, ty-1), (tx, ty+1)):
            if key in self.pressurized_air_cells:
                starts.append(key)
        if not starts:
            return 0
        stack = list(starts)
        seen = set()
        while stack:
            key = stack.pop()
            if key in seen or key not in self.pressurized_air_cells:
                continue
            seen.add(key)
            x, y = key
            for nk in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if nk in self.pressurized_air_cells and nk not in seen:
                    stack.append(nk)
        if not seen:
            return 0
        self.pressurized_air_cells.difference_update(seen)
        self.cache_epoch += 1
        return len(seen)

    def water_floor_y(self, tx, ty):
        """Bottom Y for liquid in a cell, including its own missing layers.

        If (tx,ty) itself is LOW/MID terrain, free water occupies the removed
        upper layers and rests directly on that tile's real top surface.
        Otherwise retain the older support-below behavior for water stored in
        an AIR cell above fractional terrain.
        """
        tx = int(tx); ty = int(ty)
        grid_bottom = float((ty + 1) * TILE_SIZE)
        cap = float(self.liquid_capacity_at(tx, ty))
        if 1e-9 < cap < 1.0 - 1e-9:
            top = self.solid_top_y(tx, ty)
            if top is not None:
                return float(top)
        sy = ty + 1
        if sy >= self.height_tiles:
            return grid_bottom
        top = self.solid_top_y(tx, sy)
        if top is None:
            return grid_bottom
        return max(grid_bottom, float(top))

    def water_surface_keeps_grid_level(self, tx, ty, water_amount_getter=None):
        """Keep a connected liquid body's free surface level over partial support.

        LOW/MID terrain changes the water body's bottom, but connected ocean or
        lake water should not inherit that terrain step at its free surface.
        Isolated puddles still settle down onto the real fractional support.
        """
        tx = int(tx); ty = int(ty)
        if water_amount_getter is None:
            env = self.environment_state
            if env is None:
                return False
            water_amount_getter = env.water_amount
        eps = 0.02
        if ty > 0 and float(water_amount_getter(tx, ty - 1)) > eps:
            return True
        if ty + 1 < self.height_tiles and self.get_tile(tx, ty + 1) == AIR:
            if float(water_amount_getter(tx, ty + 1)) > eps:
                return True
        if tx > 0 and self.get_tile(tx - 1, ty) == AIR:
            if float(water_amount_getter(tx - 1, ty)) > eps:
                return True
        if tx + 1 < self.width_tiles and self.get_tile(tx + 1, ty) == AIR:
            if float(water_amount_getter(tx + 1, ty)) > eps:
                return True
        return False

    def ice_rect_at(self, tx, ty):
        """Collision/render rect for ice compacted onto the true support below.

        This also handles ICE-on-ICE stacks: every upper ice block rests on the
        actual top of the ice below, which may itself have been lowered onto a
        LOW/MID support.
        """
        tx = int(tx); ty = int(ty)
        ratio = float(ice_height_ratio_for_mass(self.ice_mass_at(tx, ty)))
        if ratio <= 0.0:
            return None
        left = tx * TILE_SIZE
        right = left + TILE_SIZE
        bottom = float((ty + 1) * TILE_SIZE)
        sy = ty + 1
        if sy < self.height_tiles:
            support_top = self.solid_top_y(tx, sy)
            if support_top is not None:
                bottom = max(bottom, float(support_top))
        top = bottom - TILE_SIZE * ratio
        return (left, top, right, bottom)

    def mark_pressurized_air(self, tx, ty, enabled=True):
        key = (int(tx), int(ty))
        before = key in self.pressurized_air_cells
        if enabled:
            self.pressurized_air_cells.add(key)
        else:
            self.pressurized_air_cells.discard(key)
        # V0.7.6.9: the NumPy water solver caches a local solid mask. Air
        # pressure is a liquid boundary even though the terrain tile is AIR,
        # so changing this state must invalidate that mask immediately.
        after = key in self.pressurized_air_cells
        if before != after:
            self.cache_epoch += 1

    def liquid_blocked_by_air_pressure(self, tx, ty):
        return (int(tx), int(ty)) in self.pressurized_air_cells

    def mark_tile_geometry_dirty(self, tx, ty):
        """Invalidate geometry plus horizontal transition neighbours.

        FIX72 terrain edge patches depend on the material immediately beside a
        tile.  A mined layer therefore invalidates its own fixed GPU slots and
        the two slots that may blend into it.
        """
        tx = int(tx); ty = int(ty)
        self._mark_tile_render_slot_dirty(tx, ty)
        self._mark_tile_render_slot_dirty(tx - 1, ty)
        self._mark_tile_render_slot_dirty(tx + 1, ty)
        self.revision += 1

    def _mark_tile_render_slot_dirty(self, tx, ty):
        tx = int(tx); ty = int(ty)
        if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
            return
        cx = self._chunk_coord(tx); cy = self._chunk_coord(ty)
        chunk = self._get_chunk(cx, cy, False)
        if chunk is None:
            return
        index = self._local_coord(ty) * CHUNK_SIZE + self._local_coord(tx)
        chunk.dirty_indices.add(int(index))
        chunk.revision += 1

    def ice_mass_at(self, tx, ty):
        if self.get_tile(int(tx), int(ty)) != ICE:
            return 0.0
        env = self.environment_state
        if env is None:
            return float(tile_def(ICE).water_mass)
        return float(env.ice_mass.get((int(tx), int(ty)), tile_def(ICE).water_mass))

    def mark_tile_visual_dirty(self, tx, ty):
        """Invalidate one terrain slot when dynamic geometry changes.

        ice_mass can change while tile_id remains ICE. set_tile() therefore
        cannot observe the visual/collision stage transition itself.
        """
        self.mark_tile_geometry_dirty(int(tx), int(ty))

    def add_tile_change_listener(self, callback):
        if callback is None:
            return
        if callback not in self._tile_change_listeners:
            self._tile_change_listeners.append(callback)

    def remove_tile_change_listener(self, callback):
        try:
            self._tile_change_listeners.remove(callback)
        except ValueError:
            pass

    def _notify_tile_changed(self, tx, ty, old_tile_id, new_tile_id):
        for callback in tuple(self._tile_change_listeners):
            try:
                callback(int(tx), int(ty), int(old_tile_id), int(new_tile_id))
            except Exception:
                pass

    # --------------------------------------------------------
    # Chunk access
    # --------------------------------------------------------

    @staticmethod
    def _chunk_coord(t):
        return t // CHUNK_SIZE

    @staticmethod
    def _local_coord(t):
        return t % CHUNK_SIZE

    def _load_backing_chunk(self, cx, cy):
        key = (int(cx), int(cy))
        reader = self.backing_store
        codes = None
        if reader is not None and reader.has_chunk(*key):
            codes = reader.read_chunk_codes(*key)

        edits = self._modified_by_chunk.get(key)
        if codes is None and not edits:
            return None

        chunk = Chunk(*key)
        if codes is not None:
            remap = self._backing_code_to_tile_id or ()
            non_air = 0
            for i, raw in enumerate(codes):
                code = int(raw)
                tile_id = int(remap[code]) if 0 <= code < len(remap) else AIR
                chunk._tiles[i] = tile_id
                if tile_id != AIR:
                    non_air += 1
            chunk.non_air_count = non_air

        if edits:
            for index, tile_id in edits.items():
                old = int(chunk._tiles[int(index)])
                new = int(tile_id)
                if old == new:
                    continue
                chunk._tiles[int(index)] = new
                if old == AIR and new != AIR:
                    chunk.non_air_count += 1
                elif old != AIR and new == AIR:
                    chunk.non_air_count = max(0, chunk.non_air_count - 1)

        if chunk.non_air_count <= 0 and not edits:
            return None
        self.chunks[key] = chunk
        return chunk

    def _get_chunk(self, cx, cy, create=False):
        key = (int(cx), int(cy))
        chunk = self.chunks.get(key)
        if chunk is None and (
            self.backing_store is not None
            or key in self._modified_by_chunk
        ):
            chunk = self._load_backing_chunk(*key)
        if chunk is None and create:
            chunk = Chunk(*key)
            self.chunks[key] = chunk
        return chunk

    def attach_binary_backing(self, reader, code_to_tile_id):
        """Attach a random-access base map without materializing all chunks."""
        self.chunks.clear()
        self.modified_tiles.clear()
        self._modified_by_chunk.clear()
        self.backing_store = reader
        self._backing_code_to_tile_id = tuple(int(v) for v in code_to_tile_id)
        self._backdrop_surface_rows.clear()
        self._backdrop_surface_top_ratios.clear()
        self.width_tiles = int(reader.width)
        self.height_tiles = int(reader.height)
        self.revision += 1
        self.cache_epoch += 1
        rows = list(getattr(reader, "surface_rows", ()))
        if len(rows) == self.width_tiles:
            self._surface_rows = [
                self._surface_none if int(v) < 0 else int(v)
                for v in rows
            ]
        else:
            self._surface_rows = [self._surface_unknown for _ in range(self.width_tiles)]

    def unload_chunk(self, cx, cy):
        """Release one materialized binary chunk; save deltas remain sparse."""
        if self.backing_store is None:
            return False
        key = (int(cx), int(cy))
        return self.chunks.pop(key, None) is not None

    def preload_chunk(self, cx, cy):
        return self._get_chunk(int(cx), int(cy), False)

    def get_tile(self, tx, ty):
        if tx < 0 or ty < 0:
            return AIR
        if tx >= self.width_tiles or ty >= self.height_tiles:
            return AIR

        cx = self._chunk_coord(tx)
        cy = self._chunk_coord(ty)
        chunk = self._get_chunk(cx, cy, False)
        if chunk is None:
            return AIR

        return chunk.get(
            self._local_coord(tx),
            self._local_coord(ty),
        )

    def set_tile(self, tx, ty, tile_id, track_change=True):
        if tx < 0 or ty < 0:
            return
        if tx >= self.width_tiles or ty >= self.height_tiles:
            return

        old_tile_id = self.get_tile(tx, ty)
        if int(old_tile_id) == int(tile_id):
            return

        # Capture the original surface BEFORE the first persistent/runtime edit
        # in this X column.  Save replay also comes through track_change=True
        # after the deterministic base map is restored, so relaunching keeps the
        # same backdrop even when the surface tile itself was mined previously.
        if track_change:
            self._remember_backdrop_surface(tx)

        cx = self._chunk_coord(tx)
        cy = self._chunk_coord(ty)
        chunk = self._get_chunk(cx, cy, True)
        changed = chunk.set(
            self._local_coord(tx),
            self._local_coord(ty),
            tile_id,
        )

        if changed:
            self.revision += 1
            # The changed tile was dirtied by Chunk.set().  Also refresh the
            # adjacent material-transition slots, including across chunks.
            self._mark_tile_render_slot_dirty(int(tx) - 1, int(ty))
            self._mark_tile_render_slot_dirty(int(tx) + 1, int(ty))
            self._ensure_surface_cache()
            # V0.7.0: world generation/map loading can build the first-solid
            # cache incrementally. This avoids the first rain/wind query later
            # scanning a 2400-tile-deep column from y=0.
            cached = self._surface_rows[int(tx)]
            old_solid = bool(tile_def(old_tile_id).solid)
            new_solid = bool(tile_def(tile_id).solid)
            if new_solid:
                if cached in (self._surface_unknown, self._surface_none) or int(ty) < int(cached):
                    self._surface_rows[int(tx)] = int(ty)
            elif old_solid and cached == int(ty):
                self._surface_rows[int(tx)] = self._surface_unknown
            self._notify_tile_changed(tx, ty, old_tile_id, tile_id)

            # Direct fire/reaction/save edits can remove a structural segment
            # without going through ToolSystem. Prune only its dependent
            # subtree so no floating branch collision survives. ToolSystem's
            # explicit chop path sets the guard and receives drop accounting.
            if (
                not self._rainforest_tree_mutating
                and int(old_tile_id) in RAINFOREST_TREE_TILES
                and int(tile_id) not in RAINFOREST_TREE_TILES
                and (int(tx), int(ty)) in self.rainforest_tree_segments
            ):
                self._prune_rainforest_children_after_support_loss(
                    int(tx), int(ty), bool(track_change)
                )

            # Fully excavated chunks need no 256-entry tile array in memory.
            # Save deltas remain in modified_tiles, so removing the empty
            # runtime chunk is safe and makes large excavated worlds lighter.
            if chunk.non_air_count <= 0:
                self.chunks.pop((cx, cy), None)

        if track_change:
            tx_i = int(tx)
            ty_i = int(ty)
            tile_i = int(tile_id)
            self.modified_tiles[(tx_i, ty_i)] = tile_i
            cx_i = self._chunk_coord(tx_i)
            cy_i = self._chunk_coord(ty_i)
            index = self._local_coord(ty_i) * CHUNK_SIZE + self._local_coord(tx_i)
            self._modified_by_chunk.setdefault((cx_i, cy_i), {})[int(index)] = tile_i

    def reset(self):
        self.chunks.clear()
        self.modified_tiles.clear()
        self._modified_by_chunk.clear()
        self.backing_store = None
        self._backing_code_to_tile_id = None
        self._backdrop_surface_rows.clear()
        self._backdrop_surface_top_ratios.clear()
        self.custom_solid_cells.clear()
        self.custom_platform_cells.clear()
        self.custom_ladder_cells.clear()
        self.custom_climb_platform_cells.clear()
        self.indestructible_cells.clear()
        self.no_climb_cells.clear()
        self.horizontal_wrap_enabled = False
        self.rainforest_tree_segments.clear()
        self.rainforest_tree_children.clear()
        self.rainforest_tree_specs.clear()
        self._rainforest_tree_mutating = False
        self._authored_natural_surface_rows.clear()
        self._authored_natural_surface_top_ratios.clear()
        self.revision += 1
        self.cache_epoch += 1
        self._surface_rows = [
            self._surface_unknown
            for _ in range(self.width_tiles)
        ]

    def export_changes(self):
        return [
            [tx, ty, tile_id]
            for (tx, ty), tile_id
            in sorted(self.modified_tiles.items())
        ]

    def apply_changes(self, changes):
        for item in changes:
            if len(item) != 3:
                continue

            tx, ty, tile_id = item

            self.set_tile(
                int(tx),
                int(ty),
                int(tile_id),
                track_change=True,
            )



    def iter_non_air_tiles(self):
        """Yield (tx, ty, tile_id) without scanning empty world coordinates.

        Runtime worlds are chunk-sparse. Large-world initialization and audits
        must scale with stored material, not width*height.
        """
        for (cx, cy), chunk in tuple(self.chunks.items()):
            if chunk is None or chunk.non_air_count <= 0:
                continue
            start_x = int(cx) * CHUNK_SIZE
            start_y = int(cy) * CHUNK_SIZE
            for index, raw in enumerate(chunk._tiles):
                tile_id = int(raw)
                if tile_id == AIR:
                    continue
                lx = index % CHUNK_SIZE
                ly = index // CHUNK_SIZE
                tx = start_x + lx
                ty = start_y + ly
                if tx < self.width_tiles and ty < self.height_tiles:
                    yield tx, ty, tile_id

    def stored_tile_count(self):
        if self.backing_store is not None:
            base = int(getattr(self.backing_store, "non_air_tile_count", 0))
            # Exact edited total would require inspecting each base edited cell.
            # This function is diagnostics-only, so report base + newly-created
            # AIR->solid edits conservatively from materialized chunks.
            return base
        return sum(int(c.non_air_count) for c in self.chunks.values())

    @property
    def backing_chunk_count(self):
        if self.backing_store is None:
            return 0
        return int(getattr(self.backing_store, "non_empty_chunk_count", 0))

    # --------------------------------------------------------
    # Chunk / column cache helpers
    # --------------------------------------------------------

    def _ensure_surface_cache(self):
        if len(self._surface_rows) != self.width_tiles:
            self._surface_rows = [
                self._surface_unknown
                for _ in range(self.width_tiles)
            ]

    def _remember_backdrop_surface(self, tx):
        """Remember the pre-edit surface row *and fractional top* once."""
        tx = int(tx)
        if tx < 0 or tx >= self.width_tiles:
            return
        if tx in self._backdrop_surface_rows:
            return
        row = self.first_solid_row(tx)
        self._backdrop_surface_rows[tx] = (
            self._surface_none
            if row is None
            else int(row)
        )
        ratio = 0.0
        if row is not None:
            try:
                tile_id = self.get_tile(tx, int(row))
                if tile_id in LAYERED_SOLID_TILES:
                    ratio = float(soil_top_ratio(self.soil_layer_mask_at(tx, int(row))))
            except Exception:
                ratio = 0.0
        self._backdrop_surface_top_ratios[tx] = max(0.0, min(1.0, ratio))

    def remember_backdrop_surface(self, tx):
        """Public hook for geometry-only layer edits before the mask changes."""
        self._remember_backdrop_surface(tx)

    def backdrop_surface_row(self, tx):
        """Return the immutable visual surface used by the background layer.

        Unedited columns are free to use the live first-solid row.  Edited
        columns return the row captured immediately before their first edit.
        This gives mining a Terraria-like back wall without allocating a full
        second tilemap.
        """
        tx = int(tx)
        if tx < 0 or tx >= self.width_tiles:
            return None
        if tx in self._authored_natural_surface_rows:
            return int(self._authored_natural_surface_rows[tx])
        cached = self._backdrop_surface_rows.get(tx, self._surface_unknown)
        if cached != self._surface_unknown:
            return None if cached == self._surface_none else int(cached)
        return self.first_solid_row(tx)

    def backdrop_surface_top_ratio(self, tx):
        """Original fractional top edge within backdrop_surface_row()."""
        tx = int(tx)
        if tx < 0 or tx >= self.width_tiles:
            return 0.0
        if tx in self._authored_natural_surface_top_ratios:
            return float(self._authored_natural_surface_top_ratios[tx])
        if tx in self._backdrop_surface_rows:
            return float(self._backdrop_surface_top_ratios.get(tx, 0.0))
        row = self.first_solid_row(tx)
        if row is None:
            return 0.0
        try:
            tile_id = self.get_tile(tx, int(row))
            if tile_id in LAYERED_SOLID_TILES:
                return float(soil_top_ratio(self.soil_layer_mask_at(tx, int(row))))
        except Exception:
            pass
        return 0.0

    def is_underground(self, tx, ty):
        """True below the immutable pre-mining terrain surface.

        This deliberately does not use the live mined surface.  Opening a cave
        or vertical shaft does not reclassify deep terrain as outdoor topsoil.
        """
        surface = self.backdrop_surface_row(int(tx))
        return surface is not None and int(ty) > int(surface)

    def underground_depth(self, tx, ty):
        """Depth in tiles below the immutable surface, or 0 above/on surface."""
        surface = self.backdrop_surface_row(int(tx))
        if surface is None:
            return 0
        return max(0, int(ty) - int(surface))

    def can_support_grass_skin(self, tx, ty):
        """Whether a soil tile may visually become GRASS_DIRT.

        Grass skin is an outdoor surface state, not simply a moisture state.
        Underground cells never qualify, and a buried soil cell with another
        solid tile above it never qualifies even when fully wet.
        """
        tx = int(tx); ty = int(ty)
        if self.is_underground(tx, ty):
            return False
        if ty <= 0:
            return True
        if tile_def(self.get_tile(tx, ty - 1)).solid:
            return False
        return self.is_sky_exposed(tx, ty - 1)

    def chunk_revision(self, cx, cy):
        chunk = self._get_chunk(int(cx), int(cy), False)
        if chunk is None:
            return -1
        return int(chunk.revision)

    def chunk_dirty_indices(self, cx, cy):
        chunk = self._get_chunk(int(cx), int(cy), False)
        if chunk is None:
            return tuple()
        return tuple(chunk.dirty_indices)

    def clear_chunk_dirty_indices(self, cx, cy):
        chunk = self._get_chunk(int(cx), int(cy), False)
        if chunk is not None:
            chunk.dirty_indices.clear()

    def first_solid_row(self, tx):
        """Return the first solid row in one X column using a dirty cache.

        Rain, wind and thermal code call this frequently. Before V0.5.9 each
        call scanned from the top of the world again, so deep mining made the
        cost grow with hole depth.
        """
        tx = int(tx)
        if tx < 0 or tx >= self.width_tiles:
            return None

        self._ensure_surface_cache()
        cached = self._surface_rows[tx]
        if cached != self._surface_unknown:
            return None if cached == self._surface_none else cached

        found = self._surface_none
        for ty in range(self.height_tiles):
            if tile_def(self.get_tile(tx, ty)).solid:
                found = int(ty)
                break

        self._surface_rows[tx] = found
        return None if found == self._surface_none else found

    def is_sky_exposed(self, tx, ty):
        """True when there is no solid tile above (tx, ty)."""
        surface = self.first_solid_row(tx)
        return surface is None or int(surface) >= int(ty)

    # --------------------------------------------------------
    # Geometry
    # --------------------------------------------------------

    @staticmethod
    def tile_rect(tx, ty):
        x1 = tx * TILE_SIZE
        y1 = ty * TILE_SIZE
        return (
            x1,
            y1,
            x1 + TILE_SIZE,
            y1 + TILE_SIZE,
        )

    def _tile_query_bounds(self, rect, padding=0):
        """Return raw and world-clipped tile bounds for a pixel rectangle."""
        x1, y1, x2, y2 = rect
        pad = int(padding)
        raw_min_tx = math.floor(x1 / TILE_SIZE) - pad
        raw_max_tx = math.floor((x2 - 1e-6) / TILE_SIZE) + pad
        raw_min_ty = math.floor(y1 / TILE_SIZE) - pad
        raw_max_ty = math.floor((y2 - 1e-6) / TILE_SIZE) + pad
        min_tx = max(0, raw_min_tx)
        max_tx = min(self.width_tiles - 1, raw_max_tx)
        min_ty = max(0, raw_min_ty)
        max_ty = min(self.height_tiles - 1, raw_max_ty)
        return (
            raw_min_tx, raw_max_tx, raw_min_ty, raw_max_ty,
            min_tx, max_tx, min_ty, max_ty,
        )

    def cells_in_rect(self, rect, padding=0):
        """Yield row-major cells while resolving each touched chunk only once.

        FIX76 called ``get_tile`` for every cell. Physics asks for thousands of
        small rectangles per second, so the repeated chunk division, dictionary
        lookup and ``Chunk.get`` calls dominated the Python frame. This direct
        scan preserves the same AIR/out-of-bounds behavior and lazy binary
        backing, but reads each chunk's compact tile array in contiguous runs.
        """
        (
            _raw_min_tx, _raw_max_tx, _raw_min_ty, _raw_max_ty,
            min_tx, max_tx, min_ty, max_ty,
        ) = self._tile_query_bounds(rect, padding)
        if min_tx > max_tx or min_ty > max_ty:
            return

        chunk_size = int(CHUNK_SIZE)
        get_chunk = self._get_chunk
        for ty in range(min_ty, max_ty + 1):
            cy = ty // chunk_size
            ly = ty - cy * chunk_size
            tx = min_tx
            while tx <= max_tx:
                cx = tx // chunk_size
                chunk_start_x = cx * chunk_size
                segment_end = min(max_tx, chunk_start_x + chunk_size - 1)
                chunk = get_chunk(cx, cy, False)
                if chunk is None:
                    for cell_x in range(tx, segment_end + 1):
                        yield cell_x, ty, AIR
                else:
                    tiles = chunk._tiles
                    index = ly * chunk_size + (tx - chunk_start_x)
                    for cell_x in range(tx, segment_end + 1):
                        yield cell_x, ty, int(tiles[index])
                        index += 1
                tx = segment_end + 1

    def solid_cells_in_rect(self, rect, padding=0):
        """Yield exact solid geometry with a chunk-batched foreground scan."""
        (
            raw_min_tx, raw_max_tx, raw_min_ty, raw_max_ty,
            min_tx, max_tx, min_ty, max_ty,
        ) = self._tile_query_bounds(rect, padding)

        if min_tx <= max_tx and min_ty <= max_ty:
            chunk_size = int(CHUNK_SIZE)
            tile_size = float(TILE_SIZE)
            get_chunk = self._get_chunk
            get_tile_def = tile_def
            layered_ids = _LAYERED_SOLID_TILE_SET
            environment = self.environment_state
            soil_layers = getattr(environment, "soil_layers", None) if environment is not None else None

            for ty in range(min_ty, max_ty + 1):
                cy = ty // chunk_size
                ly = ty - cy * chunk_size
                tx = min_tx
                while tx <= max_tx:
                    cx = tx // chunk_size
                    chunk_start_x = cx * chunk_size
                    segment_end = min(max_tx, chunk_start_x + chunk_size - 1)
                    chunk = get_chunk(cx, cy, False)
                    if chunk is None:
                        tx = segment_end + 1
                        continue

                    tiles = chunk._tiles
                    index = ly * chunk_size + (tx - chunk_start_x)
                    for cell_x in range(tx, segment_end + 1):
                        tile_id = int(tiles[index])
                        index += 1
                        if tile_id == AIR:
                            continue
                        td = get_tile_def(tile_id)
                        if not td.solid:
                            continue
                        if tile_id == ICE:
                            ice_rect = self.ice_rect_at(cell_x, ty)
                            if ice_rect is not None:
                                yield cell_x, ty, tile_id, ice_rect
                            continue
                        if bool(getattr(td, "one_way_platform", False)):
                            left = cell_x * tile_size
                            top = ty * tile_size
                            yield cell_x, ty, tile_id, (
                                left,
                                top,
                                left + tile_size,
                                top + tile_size * 0.30,
                            )
                            continue
                        if tile_id in layered_ids:
                            if soil_layers is None:
                                mask = SOIL_LAYER_FULL
                            else:
                                mask = normalize_soil_mask(
                                    soil_layers.get((int(cell_x), int(ty)), SOIL_LAYER_FULL)
                                )
                            if mask <= 0:
                                continue
                            top_ratio, bottom_ratio = soil_vertical_bounds(mask)
                            left = cell_x * tile_size
                            right = left + tile_size
                            top = ty * tile_size + tile_size * top_ratio
                            bottom = ty * tile_size + tile_size * bottom_ratio
                            if bottom > top + 1e-6:
                                yield cell_x, ty, tile_id, (left, top, right, bottom)
                            continue
                        left = cell_x * tile_size
                        top = ty * tile_size
                        yield cell_x, ty, tile_id, (
                            left,
                            top,
                            left + tile_size,
                            top + tile_size,
                        )
                    tx = segment_end + 1

        # FIX96: custom platforms reuse the established one-way branch
        # geometry as a *collision proxy only*. No terrain ID/art/map data is
        # replaced. Full underlying stone is never made passable by an overlay.
        if self.custom_solid_cells or self.custom_platform_cells:
            for ty in range(min_ty, max_ty + 1):
                for tx in range(min_tx, max_tx + 1):
                    key = (tx, ty)
                    if key not in self.custom_solid_cells and key not in self.custom_platform_cells:
                        continue
                    underlying = tile_def(self.get_tile(tx, ty))
                    if underlying.solid:
                        continue
                    if key in self.custom_platform_cells:
                        left = float(tx * TILE_SIZE); top = float(ty * TILE_SIZE)
                        yield tx, ty, RAINFOREST_BRANCH, (left, top, left + TILE_SIZE, top + TILE_SIZE * 0.30)
                    else:
                        yield tx, ty, STONE, self.tile_rect(tx, ty)

    def configure_custom_map_cells(self, cells, metadata):
        """Rebuild sparse collision/grip sets from saved IDs, not save snapshots."""
        from systems.custom_tile_rules import role_from_meta, PLATFORM_ROLES, LADDER_ROLES
        self.custom_solid_cells.clear()
        self.custom_platform_cells.clear()
        self.custom_ladder_cells.clear()
        self.custom_climb_platform_cells.clear()
        for key, aid in cells.items():
            tx, ty = int(key[0]), int(key[1])
            if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
                continue
            key = (tx, ty)
            role = role_from_meta(metadata.get(aid, {}))
            if role == 'solid': self.custom_solid_cells.add(key)
            if role in PLATFORM_ROLES: self.custom_platform_cells.add(key)
            if role in LADDER_ROLES: self.custom_ladder_cells.add(key)
            if role == 'climb_platform': self.custom_climb_platform_cells.add(key)
        self.revision = int(getattr(self, 'revision', 0)) + 1

    def is_ladder_at(self, tx, ty):
        tx, ty = int(tx), int(ty)
        if not (0 <= tx < self.width_tiles and 0 <= ty < self.height_tiles):
            return False
        td = tile_def(self.get_tile(tx, ty))
        key = (tx, ty)
        if key in self.custom_solid_cells:
            return False
        if td.solid and not td.one_way_platform:
            return False
        return bool(td.ladder or key in self.custom_ladder_cells)

    def ladder_cell_in_rect(self, rect):
        for tx, ty, tile_id in self.cells_in_rect(rect, 0):
            if self.is_ladder_at(tx, ty) and rects_overlap(rect, self.tile_rect(tx, ty)):
                return tx, ty
        return None

    def ladder_entry_from_platform(self, rect):
        """A standing AABB does not overlap its supporting tile. Probe its feet.

        Only explicit climb-platforms with a continuous ladder directly below
        may accept a down command from above. A plain platform or a disconnected
        painted decoration must never act as an accidental drop-through hole.
        """
        feet = float(rect[3]); center = (float(rect[0]) + float(rect[2])) * 0.5
        ty = int(round(feet / TILE_SIZE)); tx = int(center // TILE_SIZE)
        key = (tx, ty)
        if abs(feet - ty * TILE_SIZE) > 3.25:
            return None
        if key not in self.custom_climb_platform_cells:
            return None
        if not self.is_ladder_at(tx, ty) or not self.is_ladder_at(tx, ty + 1):
            return None
        return key

    def wall_contact(self, bbox, direction):
        """Return a *real* climbable wall face touching the player's side.

        FIX33: layered stone/ore used to be tested against the tile's full
        40 px rectangle.  A LOW (1/3-height) rock therefore looked like a
        vertical wall to the climb detector even though physics correctly
        treated it as a walkable step.  That put the player into CLIMB and
        drained stamina while visually walking on a flat/terraced floor.

        Use the same authoritative fractional solid rectangles as collision,
        and deliberately ignore one-layer faces.  MID (2 layers) and FULL
        faces remain valid climbing surfaces, matching the movement rule that
        only a one-layer rise is auto-walkable.
        """
        if direction == 0:
            return None

        if direction > 0:
            probe = (
                bbox[2] - 1,
                bbox[1] + 5,
                bbox[2] + 7,
                bbox[3] - 5,
            )
        else:
            probe = (
                bbox[0] - 7,
                bbox[1] + 5,
                bbox[0] + 1,
                bbox[3] - 5,
            )

        one_layer_max = float(TILE_SIZE) / 3.0 + 1.0
        for tx, ty, tile_id, solid_rect in self.solid_cells_in_rect(probe, padding=1):
            if (int(tx), int(ty)) in self.no_climb_cells:
                continue
            td = tile_def(tile_id)
            if not (td.solid and td.wall_climbable):
                continue
            face_h = max(0.0, float(solid_rect[3]) - float(solid_rect[1]))
            if tile_id in LAYERED_SOLID_TILES and face_h <= one_layer_max:
                continue
            if rects_overlap(probe, solid_rect):
                return tx, ty
        return None

    # --------------------------------------------------------
    # Climb geometry
    # --------------------------------------------------------

    def ladder_info(self, tx, ty):
        """Find one contiguous vertical ladder segment."""
        top = ty
        bottom = ty

        while top - 1 >= 0:
            if not self.is_ladder_at(tx, top - 1):
                break
            top -= 1

        while bottom + 1 < self.height_tiles:
            if not self.is_ladder_at(tx, bottom + 1):
                break
            bottom += 1

        above_id = self.get_tile(tx, top - 1)
        above_solid = tile_def(above_id).solid

        if above_solid:
            exit_y = (top - 1) * TILE_SIZE
        else:
            exit_y = top * TILE_SIZE

        bottom_y=(bottom+1)*TILE_SIZE
        if tx in getattr(self,'cloud_ladder_landings',{}):
            # Cloud routes meet LOW/MID/HIGH soil. A standing player's feet
            # may be 26.7px below the nominal cell top; allow that bottom grab
            # rather than immediately ejecting them on the first upward step.
            surface=self.solid_top_y(tx,bottom+1)
            if surface is not None:bottom_y=max(bottom_y,float(surface))
        return {
            "type": "ladder",
            "tx": tx,
            "top_row": top,
            "bottom_row": bottom,
            "custom_revision": self.revision,
            "custom_route": any((int(tx), row) in self.custom_ladder_cells
                                for row in range(top, bottom + 1)),
            "top_climb_platform": (int(tx), top) in self.custom_climb_platform_cells,
            "center_x": (tx + 0.5) * TILE_SIZE,
            "top_y": top * TILE_SIZE,
            "bottom_y": bottom_y,
            "exit_y": exit_y,
        }

    def wall_info(self, tx, ty, side):
        """Find top of a contiguous climbable wall column."""
        top = ty
        bottom = ty

        while top - 1 >= 0:
            if (int(tx), int(top - 1)) in self.no_climb_cells:
                break
            tile_id = self.get_tile(tx, top - 1)
            td = tile_def(tile_id)
            if not (td.solid and td.wall_climbable):
                break
            top -= 1

        while bottom + 1 < self.height_tiles:
            if (int(tx), int(bottom + 1)) in self.no_climb_cells:
                break
            tile_id = self.get_tile(tx, bottom + 1)
            td = tile_def(tile_id)
            if not (td.solid and td.wall_climbable):
                break
            bottom += 1

        return {
            "type": "wall",
            "tx": tx,
            "top_row": top,
            "bottom_row": bottom,
            "top_y": top * TILE_SIZE,
            "bottom_y": (bottom + 1) * TILE_SIZE,
            "side": side,
            "mantle_x": (tx + 0.5) * TILE_SIZE,
        }

    # --------------------------------------------------------
    # Rendering query
    # --------------------------------------------------------

    def iter_visible(self, camera_x, camera_y, width, height, margin_tiles=1):
        x1 = camera_x - margin_tiles * TILE_SIZE
        y1 = camera_y - margin_tiles * TILE_SIZE
        x2 = camera_x + width + margin_tiles * TILE_SIZE
        y2 = camera_y + height + margin_tiles * TILE_SIZE

        for tx, ty, tile_id in self.cells_in_rect((x1, y1, x2, y2), 0):
            if tile_id != AIR:
                yield tx, ty, tile_id

    # --------------------------------------------------------
    # Test world
    # --------------------------------------------------------

    def build_test_world(self):
        # Ground: only top row uses grass top.
        for tx in range(self.width_tiles):
            self.set_tile(tx, GROUND_ROW, GRASS_DIRT, track_change=False)
            for ty in range(GROUND_ROW + 1, self.height_tiles):
                self.set_tile(tx, ty, DIRT, track_change=False)

        # Low wooden platform
        for tx in range(8, 14):
            self.set_tile(tx, 10, WOOD, track_change=False)

        # Ladder + high wooden platform.
        for tx in range(15, 24):
            self.set_tile(tx, 7, WOOD, track_change=False)

        for ty in range(8, GROUND_ROW):
            self.set_tile(18, ty, LADDER, track_change=False)

        # Climbable stone wall + top platform.
        for ty in range(7, GROUND_ROW):
            self.set_tile(32, ty, STONE, track_change=False)

        for tx in range(32, 39):
            self.set_tile(tx, 6, STONE, track_change=False)

        # Jump test platforms.
        for tx in range(53, 56):
            self.set_tile(tx, 11, STONE, track_change=False)

        for tx in range(57, 60):
            self.set_tile(tx, 9, STONE, track_change=False)
