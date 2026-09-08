# -*- coding: utf-8 -*-
"""FIX69 bounded, data-driven support for linked secondary worlds.

The authored map selects one or more project-local catalogs through
``metadata.secondary_world``.  This module deliberately keeps four concerns
behind one small, renderer-neutral object:

* safe catalog loading below ``PYTO_RPG_PROJECT_ROOT``;
* an authored-only creature roster with stable IDs and unique bosses;
* overlay of authoritative creature actions and presentation-only VFX data;
* a three-depth background plan capped at 128 Metal instances.

Nothing in this module imports UIKit or mutates combat from presentation data.
Missing or invalid catalogs fail open: ordinary FIX68 maps continue to use the
existing biome population, respawn scheduler, VFX catalog and background path.
"""
from __future__ import annotations

import json
import math
import os
import zlib
from collections import namedtuple

from config import TILE_SIZE
from entities.creature import Creature
from engine.math2d import rects_overlap
from systems.creature_action_profile import normalize_action
from systems.creature_attributes import normalize_element


FORMAT_VERSION = 1
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_BACKGROUND_QUADS = 128
MAX_VFX_EVENTS = 24
MAX_VFX_QUADS_PER_EVENT = 18
MAX_VFX_TOTAL_QUADS = 192
LAYER_ORDER = ("far", "mid", "near")

# Roster fallback is a recovery path for an incomplete authored map, not a
# second population generator.  Keep every dimension explicitly bounded so a
# malformed (but size-valid) catalog cannot turn startup into a whole-map scan.
MAX_FALLBACK_ROSTER_ENTRIES = 64
MAX_FALLBACK_ACTORS = 96
MAX_FALLBACK_X_PROBES = 96
MAX_FALLBACK_Y_PROBES = 64
MAX_FALLBACK_PROBES_PER_ACTOR = 512
MAX_FALLBACK_TOTAL_GEOMETRY_PROBES = 8192
MAX_FALLBACK_FORBIDDEN_RECTS = 512
MAX_FALLBACK_FORBIDDEN_BUCKET_REFS = 8192
MAX_FALLBACK_LARGE_FORBIDDEN_RECTS = 64
MAX_FALLBACK_EXTERNAL_ROWS_SCANNED = 2048
MAX_FALLBACK_PORTAL_ROWS_SCANNED = 256
MAX_FALLBACK_HAZARD_ROWS_SCANNED = 1536
MAX_FALLBACK_EXPLICIT_ROWS_SCANNED = 256
FALLBACK_PLAYER_CLEARANCE_TILES = 5.0

_AQUATIC_LOCOMOTION = frozenset(("fish", "swim"))
_WATER_SURFACE_LOCOMOTION = frozenset(("waterbird",))
_PERCHED_LOCOMOTION = frozenset(("ground", "bird"))
_LOW_FLY_LOCOMOTION = frozenset(("fly_low", "insect_low"))
_FREE_FLY_LOCOMOTION = frozenset(("cave_fly", "ghost_fly"))

SecondaryBackgroundQuad = namedtuple(
    "SecondaryBackgroundQuad",
    "x y width height rgba layer motif collision",
)
SecondaryBackgroundPlan = namedtuple(
    "SecondaryBackgroundPlan",
    "profile_id display_name base_color quads budget layer_counts contract",
)


def _finite(value, fallback=0.0, low=None, high=None):
    try:
        value = float(value)
    except Exception:
        value = float(fallback)
    if not math.isfinite(value):
        value = float(fallback)
    if low is not None:
        value = max(float(low), value)
    if high is not None:
        value = min(float(high), value)
    return value


def _integer(value, fallback=0, low=None, high=None):
    try:
        value = int(value)
    except Exception:
        value = int(fallback)
    if low is not None:
        value = max(int(low), value)
    if high is not None:
        value = min(int(high), value)
    return value


def _rgba(value, fallback=(1.0, 1.0, 1.0, 1.0)):
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            values = [float(item) for item in value[:4]]
            if len(values) == 3:
                values.append(1.0)
            return tuple(max(0.0, min(1.0, item)) for item in values)
        except Exception:
            return tuple(fallback)
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return tuple(fallback)
    try:
        return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4, 6))
    except Exception:
        return tuple(fallback)


def _with_alpha(color, multiplier, maximum=0.90):
    return (
        float(color[0]), float(color[1]), float(color[2]),
        max(0.0, min(float(maximum), float(color[3]) * float(multiplier))),
    )


def _stable_unit(*parts):
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return float(zlib.crc32(payload) & 0xFFFFFFFF) / 4294967295.0


def _bounded_integer_fan(preferred, low, high, limit):
    """Return a stable centre,+radius,-radius integer probe order."""
    low = int(low); high = int(high); limit = max(0, int(limit))
    if high < low or limit <= 0:
        return ()
    preferred = max(low, min(high, int(preferred)))
    out = []
    radius = 0
    while len(out) < limit and radius <= high - low:
        candidates = (preferred,) if radius == 0 else (preferred + radius, preferred - radius)
        for value in candidates:
            if low <= value <= high and value not in out:
                out.append(value)
                if len(out) >= limit:
                    break
        radius += 1
    return tuple(out)


def _body_box(position, profile, inset=0.0):
    """Creature's complete feet-anchored AABB (optional legacy inset)."""
    x, y = float(position[0]), float(position[1])
    width = max(1.0, float(profile["w"])); height = max(1.0, float(profile["h"]))
    inset = max(0.0, min(float(inset), width * 0.25, height * 0.25))
    return (
        x - width * 0.5 + inset,
        y - height + inset,
        x + width * 0.5 - inset,
        y - inset,
    )


class _BoundedSpawnResolver:
    """Shared geometry/habitat validator for secondary-world spawn paths.

    Authored placement keeps its established candidate order and legacy water
    sampling, while fallback calls :meth:`first_valid` with the strict habitat
    contract.  Centralising AABB, terrain and actor checks prevents those two
    paths from drifting into separate collision implementations.
    """

    def __init__(self, biome_system, scene=None, forbidden_rects=(), avoid_world_x=None, budgeted=True):
        self.biome_system = biome_system
        self.world = getattr(biome_system, "world", None)
        self.env = getattr(biome_system, "env", None)
        self.scene = scene
        self.forbidden_rects = tuple(
            tuple(float(value) for value in rect[:4])
            for rect in forbidden_rects
            if isinstance(rect, (list, tuple)) and len(rect) >= 4
        )
        self.avoid_world_x = None if avoid_world_x is None else float(avoid_world_x)
        self.world_width_px = max(0.0, float(getattr(self.world, "width_tiles", 0) or 0) * TILE_SIZE)
        self.world_height_px = max(0.0, float(getattr(self.world, "height_tiles", 0) or 0) * TILE_SIZE)
        self.horizontal_wrap = bool(getattr(self.world, "horizontal_wrap_enabled", False))
        self.budgeted = bool(budgeted)
        self.actor_geometry_probes = 0
        self.total_geometry_probes = 0
        self._forbidden_buckets = {}
        self._large_forbidden = []
        self._forbidden_index_overflow = False
        self._build_forbidden_index()

    def begin_actor(self):
        self.actor_geometry_probes = 0

    def can_probe_geometry(self):
        if not self.budgeted:
            return True
        return (
            self.actor_geometry_probes < MAX_FALLBACK_PROBES_PER_ACTOR
            and self.total_geometry_probes < MAX_FALLBACK_TOTAL_GEOMETRY_PROBES
        )

    def claim_geometry_probe(self):
        if not self.budgeted:
            return True
        if not self.can_probe_geometry():
            return False
        self.actor_geometry_probes += 1
        self.total_geometry_probes += 1
        return True

    def _build_forbidden_index(self):
        """Bucket nearby exclusions; pathological indexes fail closed."""
        if self.world is None:
            return
        width = int(getattr(self.world, "width_tiles", 0) or 0)
        height = int(getattr(self.world, "height_tiles", 0) or 0)
        refs = 0
        for index, rect in enumerate(self.forbidden_rects):
            min_tx = max(0, int(math.floor(rect[0] / TILE_SIZE)))
            max_tx = min(width - 1, int(math.floor((rect[2] - 1e-6) / TILE_SIZE)))
            min_ty = max(0, int(math.floor(rect[1] / TILE_SIZE)))
            max_ty = min(height - 1, int(math.floor((rect[3] - 1e-6) / TILE_SIZE)))
            if max_tx < min_tx or max_ty < min_ty:
                continue
            cell_count = (max_tx - min_tx + 1) * (max_ty - min_ty + 1)
            if cell_count > 128:
                if len(self._large_forbidden) >= MAX_FALLBACK_LARGE_FORBIDDEN_RECTS:
                    self._forbidden_index_overflow = True
                    return
                self._large_forbidden.append(index)
                continue
            if refs + cell_count > MAX_FALLBACK_FORBIDDEN_BUCKET_REFS:
                self._forbidden_index_overflow = True
                return
            for ty in range(min_ty, max_ty + 1):
                for tx in range(min_tx, max_tx + 1):
                    self._forbidden_buckets.setdefault((tx, ty), []).append(index)
                    refs += 1

    def forbidden_overlaps(self, bbox):
        if self._forbidden_index_overflow:
            return True
        indices = set(self._large_forbidden)
        min_tx = max(0, int(math.floor(bbox[0] / TILE_SIZE)))
        max_tx = min(int(getattr(self.world, "width_tiles", 0) or 0) - 1, int(math.floor((bbox[2] - 1e-6) / TILE_SIZE)))
        min_ty = max(0, int(math.floor(bbox[1] / TILE_SIZE)))
        max_ty = min(int(getattr(self.world, "height_tiles", 0) or 0) - 1, int(math.floor((bbox[3] - 1e-6) / TILE_SIZE)))
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                indices.update(self._forbidden_buckets.get((tx, ty), ()))
        return any(rects_overlap(bbox, self.forbidden_rects[index]) for index in indices)

    def normalize_position(self, position):
        if position is None:
            return None
        x, y = float(position[0]), float(position[1])
        if self.horizontal_wrap and self.world_width_px > 0.0:
            x %= self.world_width_px
        return x, y

    def _horizontal_distance(self, left, right):
        distance = abs(float(left) - float(right))
        if self.horizontal_wrap and self.world_width_px > 0.0:
            distance %= self.world_width_px
            distance = min(distance, self.world_width_px - distance)
        return distance

    def _scene_actors(self):
        entities = getattr(self.scene, "entities", ()) if self.scene is not None else ()
        return tuple(entities) if isinstance(entities, (list, tuple)) else ()

    @staticmethod
    def _interval_covered(intervals, left, right, epsilon=0.05):
        cursor = float(left)
        for start, end in sorted(intervals):
            start = max(float(left), float(start)); end = min(float(right), float(end))
            if end <= start:
                continue
            if start > cursor + float(epsilon):
                return False
            cursor = max(cursor, end)
            if cursor >= float(right) - float(epsilon):
                return True
        return cursor >= float(right) - float(epsilon)

    def body_clear(self, position, profile, prior=(), include_scene=True, inset=0.0):
        """Check world bounds, the complete terrain AABB and prior actors."""
        position = self.normalize_position(position)
        if position is None or self.world is None:
            return False
        bbox = _body_box(position, profile, inset=inset)
        if (
            bbox[0] < 0.0 or bbox[1] < 0.0
            or bbox[2] > self.world_width_px or bbox[3] > self.world_height_px
        ):
            # Wrapped worlds still use one canonical spawn AABB.  A body that
            # straddles the seam is rejected and the bounded fan tries an
            # interior column; physics never receives an unnormalised home_x.
            return False
        if not self.claim_geometry_probe():
            return False
        try:
            for _tx, _ty, _tile, solid_rect in self.world.solid_cells_in_rect(bbox, padding=1):
                if rects_overlap(bbox, solid_rect):
                    return False
        except Exception:
            return False
        if self.forbidden_overlaps(bbox):
            return False
        if self.avoid_world_x is not None:
            body_half_width = max(0.5, float(profile["w"]) * 0.5)
            if self._horizontal_distance(position[0], self.avoid_world_x) < (
                TILE_SIZE * FALLBACK_PLAYER_CLEARANCE_TILES + body_half_width
            ):
                return False
        blockers = []
        if include_scene:
            blockers.extend(self._scene_actors())
        blockers.extend(tuple(prior or ()))
        seen = set()
        for actor in blockers:
            if id(actor) in seen or getattr(actor, "active", True) is False:
                continue
            seen.add(id(actor))
            try:
                other = actor.bbox()
            except Exception:
                continue
            if isinstance(other, (list, tuple)) and len(other) >= 4 and rects_overlap(bbox, other):
                return False
        return True

    def body_touches_water(self, position, profile, inset=0.0):
        if self.env is None:
            return False
        bbox = _body_box(self.normalize_position(position), profile, inset=inset)
        min_tx = int(math.floor(bbox[0] / TILE_SIZE))
        max_tx = int(math.floor((bbox[2] - 1e-6) / TILE_SIZE))
        min_ty = int(math.floor(bbox[1] / TILE_SIZE))
        max_ty = int(math.floor((bbox[3] - 1e-6) / TILE_SIZE))
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                if not self.claim_geometry_probe():
                    # This method is used as a negative predicate; exhausted
                    # certainty must reject the candidate, not call it dry.
                    return True
                try:
                    amount = max(0.0, min(1.0, float(self.env.water_amount(tx, ty))))
                except Exception:
                    amount = 0.0
                if amount <= 0.025:
                    continue
                water_rect = (
                    tx * TILE_SIZE,
                    (float(ty) + 1.0 - amount) * TILE_SIZE,
                    (tx + 1) * TILE_SIZE,
                    (ty + 1) * TILE_SIZE,
                )
                if rects_overlap(bbox, water_rect):
                    return True
        return False

    def body_fully_submerged(self, position, profile):
        """Require every piece of the full AABB to be occupied by free water."""
        if self.env is None:
            return False
        bbox = _body_box(self.normalize_position(position), profile)
        min_tx = int(math.floor(bbox[0] / TILE_SIZE))
        max_tx = int(math.floor((bbox[2] - 1e-6) / TILE_SIZE))
        min_ty = int(math.floor(bbox[1] / TILE_SIZE))
        max_ty = int(math.floor((bbox[3] - 1e-6) / TILE_SIZE))
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                ix1 = max(bbox[0], tx * TILE_SIZE)
                ix2 = min(bbox[2], (tx + 1) * TILE_SIZE)
                iy1 = max(bbox[1], ty * TILE_SIZE)
                iy2 = min(bbox[3], (ty + 1) * TILE_SIZE)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                if not self.claim_geometry_probe():
                    return False
                try:
                    amount = max(0.0, min(1.0, float(self.env.water_amount(tx, ty))))
                except Exception:
                    return False
                water_top = (float(ty) + 1.0 - amount) * TILE_SIZE
                water_bottom = (float(ty) + 1.0) * TILE_SIZE
                if amount <= 0.025 or water_top > iy1 + 0.05 or water_bottom < iy2 - 0.05:
                    return False
        return True

    def floor_supported(self, position, profile):
        """Require continuous solid support below the complete body width."""
        bbox = _body_box(self.normalize_position(position), profile)
        foot_y = float(position[1])
        intervals = []
        strip = (bbox[0], foot_y - 0.05, bbox[2], foot_y + 1.25)
        if not self.claim_geometry_probe():
            return False
        try:
            cells = self.world.solid_cells_in_rect(strip, padding=1)
            for _tx, _ty, _tile, solid_rect in cells:
                if abs(float(solid_rect[1]) - foot_y) > 0.75:
                    continue
                left = max(bbox[0], float(solid_rect[0]))
                right = min(bbox[2], float(solid_rect[2]))
                if right > left:
                    intervals.append((left, right))
        except Exception:
            return False
        return self._interval_covered(intervals, bbox[0], bbox[2])

    def water_surface_supported(self, position, profile):
        """Waterbirds need a continuous surface directly below both feet."""
        if self.env is None:
            return False
        bbox = _body_box(self.normalize_position(position), profile)
        foot_y = float(position[1])
        min_tx = int(math.floor(bbox[0] / TILE_SIZE))
        max_tx = int(math.floor((bbox[2] - 1e-6) / TILE_SIZE))
        intervals = []
        height_tiles = int(getattr(self.world, "height_tiles", 0) or 0)
        for tx in range(min_tx, max_tx + 1):
            for ty in range(max(0, int(foot_y // TILE_SIZE) - 1), min(height_tiles, int(foot_y // TILE_SIZE) + 2)):
                if not self.claim_geometry_probe():
                    return False
                try:
                    amount = max(0.0, min(1.0, float(self.env.water_amount(tx, ty))))
                except Exception:
                    amount = 0.0
                if amount <= 0.025:
                    continue
                surface_y = (float(ty) + 1.0 - amount) * TILE_SIZE
                if abs(surface_y - foot_y) <= 1.0:
                    intervals.append((tx * TILE_SIZE, (tx + 1) * TILE_SIZE))
                    break
        return self._interval_covered(intervals, bbox[0], bbox[2])

    def low_flight_supported(self, position, profile):
        """Require a nearby, continuous dry floor below a low flyer."""
        bbox = _body_box(self.normalize_position(position), profile)
        foot_y = float(position[1])
        max_drop = 72.0
        by_surface = {}
        probe = (bbox[0], foot_y + 0.05, bbox[2], min(self.world_height_px, foot_y + max_drop))
        if not self.claim_geometry_probe():
            return False
        try:
            for _tx, _ty, _tile, solid_rect in self.world.solid_cells_in_rect(probe, padding=1):
                surface = float(solid_rect[1])
                if surface < foot_y + 8.0 or surface > foot_y + max_drop:
                    continue
                key = round(surface, 2)
                by_surface.setdefault(key, []).append((float(solid_rect[0]), float(solid_rect[2])))
        except Exception:
            return False
        return any(
            self._interval_covered(intervals, bbox[0], bbox[2])
            for intervals in by_surface.values()
        )

    def legacy_aquatic_samples(self, position, profile):
        """Preserve FIX69 authored-map sampling exactly."""
        if position is None or self.env is None:
            return False
        bbox = _body_box(position, profile, inset=0.25)
        sample_y = (bbox[1] + bbox[3]) * 0.5
        for sample_x in (bbox[0] + 1.0, (bbox[0] + bbox[2]) * 0.5, bbox[2] - 1.0):
            tx = int(sample_x // TILE_SIZE); ty = int(sample_y // TILE_SIZE)
            try:
                if float(self.env.water_amount(tx, ty)) <= 0.025:
                    return False
            except Exception:
                return False
        return True

    def habitat_valid(self, position, profile, locomotion, prior=()):
        position = self.normalize_position(position)
        if not self.body_clear(position, profile, prior=prior, include_scene=True):
            return False
        locomotion = str(locomotion or "ground")
        if locomotion in _AQUATIC_LOCOMOTION:
            return self.body_fully_submerged(position, profile)
        if locomotion in _WATER_SURFACE_LOCOMOTION:
            return (
                not self.body_touches_water(position, profile)
                and self.water_surface_supported(position, profile)
            )
        if locomotion in _PERCHED_LOCOMOTION:
            return (
                not self.body_touches_water(position, profile)
                and self.floor_supported(position, profile)
            )
        if locomotion in _LOW_FLY_LOCOMOTION:
            return (
                not self.body_touches_water(position, profile)
                and self.low_flight_supported(position, profile)
            )
        # cave_fly / ghost_fly / fly_low / insect_low (and unknown future
        # airborne locomotion) require a fully dry, wall-free body.
        return not self.body_touches_water(position, profile)

    def first_valid(self, candidates, profile, locomotion, prior=()):
        seen = set()
        probes = 0
        self.begin_actor()
        for raw in candidates:
            if (
                probes >= MAX_FALLBACK_PROBES_PER_ACTOR
                or not self.can_probe_geometry()
            ):
                break
            position = self.normalize_position(raw)
            if position is None:
                continue
            key = (round(position[0], 4), round(position[1], 4))
            if key in seen:
                continue
            seen.add(key); probes += 1
            if self.habitat_valid(position, profile, locomotion, prior=prior):
                return position
        return None


def secondary_world_metadata(metadata):
    if not isinstance(metadata, dict):
        return {}
    row = metadata.get("secondary_world", {})
    return dict(row) if isinstance(row, dict) else {}


def is_secondary_world(metadata):
    row = secondary_world_metadata(metadata)
    return bool(str(row.get("world_id", "") or "").strip())


def _safe_project_path(project_root, relative_path):
    # Resolve symlinks on both sides before the containment check.  An authored
    # map may name only project-local catalogs; a symlink inside ``assets``
    # must not silently turn that into an arbitrary outside read.
    root = os.path.realpath(os.path.abspath(os.path.expanduser(str(project_root or "."))))
    rel = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return ""
    path = os.path.realpath(os.path.abspath(os.path.join(root, *rel.split("/"))))
    try:
        if os.path.commonpath((root, path)) != root:
            return ""
    except Exception:
        return ""
    return path


def _catalog_candidates(project_root, metadata):
    row = secondary_world_metadata(metadata)
    world_id = str(row.get("world_id", "") or "").strip()
    raw = row.get("catalogs", row.get("catalog", ()))
    if isinstance(raw, str):
        requested = [raw]
    elif isinstance(raw, (list, tuple)):
        requested = [str(item) for item in raw]
    else:
        requested = []
    simple_id = world_id.rsplit(".", 1)[-1].replace("-", "_")
    aliases = [world_id.replace(".", "_"), simple_id]
    if world_id.startswith("biolume"):
        aliases.extend(("biolume", "biolume_world"))
    if world_id.startswith("wuxia"):
        aliases.extend(("wuxia", "wuxia_world"))
    for alias in aliases:
        if not alias:
            continue
        requested.extend((
            "assets/secondary_worlds/%s.json" % alias,
            "assets/%s_content.json" % alias,
        ))
    out = []
    seen = set()
    for value in requested:
        path = _safe_project_path(project_root, value)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return tuple(out)


def _read_catalog(path):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_CATALOG_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _dictionary(raw, *names):
    for name in names:
        value = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(value, dict):
            return value
    return {}


def _merge_catalog(target, source):
    """Merge independent world catalogs without sharing an authored file."""
    for key, aliases in (
        ("background_profiles", ("background_profiles", "backgrounds")),
        ("creature_archetypes", ("creature_archetypes", "creatures")),
        ("rosters", ("rosters",)),
        ("vfx_profiles", ("vfx_profiles",)),
        ("decoration_styles", ("decoration_styles", "decorations")),
        ("action_vfx", ("action_vfx", "action_overrides")),
        ("action_profiles", ("action_profiles",)),
    ):
        target[key].update(_dictionary(source, *aliases))
    regions = source.get("regions", ()) if isinstance(source, dict) else ()
    if isinstance(regions, list):
        target["regions"].extend(row for row in regions if isinstance(row, dict))
    source_world = str(source.get("world_id", "") or "") if isinstance(source, dict) else ""
    if source_world:
        target["source_world_ids"].add(source_world)
    source_map = str(source.get("map_file", "") or "") if isinstance(source, dict) else ""
    if source_map:
        target["map_files"].add(source_map)


def _normalize_archetype(species, row):
    row = row if isinstance(row, dict) else {}
    loot = []
    for item in row.get("loot", ()) if isinstance(row.get("loot", ()), (list, tuple)) else ():
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            loot.append((str(item[0]), str(item[1]), _integer(item[2], 1, 1, 99)))
    return {
        "name": str(row.get("name", species) or species)[:48],
        "hp": _finite(row.get("hp"), 80.0, 1.0, 9999.0),
        "attack": _finite(row.get("attack"), 8.0, 0.0, 999.0),
        "speed": _finite(row.get("speed"), 48.0, 0.0, 600.0),
        "w": _finite(row.get("w", row.get("width")), 36.0, 8.0, 180.0),
        "h": _finite(row.get("h", row.get("height")), 32.0, 8.0, 220.0),
        "hostile": bool(row.get("hostile", True)),
        "locomotion": str(row.get("locomotion", "ground") or "ground")[:24],
        "element": normalize_element(row.get("element", "earth"), "earth"),
        "loot": tuple(loot),
        "boss": bool(row.get("boss", False)),
        "boss_title": str(row.get("boss_title", row.get("name", species)) or species)[:64],
        "unique_key": str(row.get("unique_key", species) or species)[:64],
        "vfx_profile": str(row.get("vfx_profile", "") or "")[:64],
        "respawn_enabled": bool(row.get("respawn_enabled", not bool(row.get("boss", False)))),
        "respawn_seconds": (
            None if row.get("respawn_seconds") is None
            else _finite(row.get("respawn_seconds"), 45.0, 0.25, 3600.0)
        ),
    }


def _normalize_vfx_profile(row):
    row = row if isinstance(row, dict) else {}
    palette = row.get("palette", ("#FFFFFFFF",))
    if not isinstance(palette, (list, tuple)):
        palette = ("#FFFFFFFF",)
    return {
        "style": str(row.get("style", "claw_slash") or "claw_slash")[:40],
        "palette": [str(value) for value in tuple(palette)[:4]] or ["#FFFFFFFF"],
        "ttl": _finite(row.get("ttl"), 0.34, 0.05, 1.25),
        "quad_cost": _integer(row.get("quad_cost"), 12, 0, MAX_VFX_QUADS_PER_EVENT),
        "scale": _finite(row.get("scale"), 1.0, 0.0, 2.5),
        "radius": _finite(row.get("radius"), 30.0, 0.0, 160.0),
    }


class SecondaryWorldRuntime:
    """Immutable-after-load runtime binding for one authored secondary map."""

    def __init__(self, project_root, metadata=None):
        self.project_root = os.path.abspath(str(project_root or "."))
        self.metadata = metadata if isinstance(metadata, dict) else {}
        self.binding = secondary_world_metadata(self.metadata)
        self.world_id = str(self.binding.get("world_id", "") or "").strip()
        self.profile_id = str(self.binding.get("profile_id", "") or "").strip()
        self.roster_id = str(self.binding.get("roster_id", "") or "").strip()
        self.population_mode = str(
            self.binding.get("population_mode", self.metadata.get("population_mode", "authored_only"))
            or "authored_only"
        ).strip().lower()
        self.boss_unique = bool(self.binding.get("boss_unique", True))
        self.catalog_paths = ()
        self.validation_errors = []
        merged = {
            "background_profiles": {}, "creature_archetypes": {},
            "rosters": {}, "vfx_profiles": {}, "action_vfx": {},
            "action_profiles": {}, "decoration_styles": {},
            "regions": [], "source_world_ids": set(), "map_files": set(),
        }
        loaded_paths = []
        if self.world_id:
            for path in _catalog_candidates(self.project_root, self.metadata):
                raw = _read_catalog(path)
                if not isinstance(raw, dict):
                    continue
                version = _integer(raw.get("format_version"), 0)
                fmt = str(raw.get("format", "") or "")
                if version != FORMAT_VERSION:
                    self.validation_errors.append("%s: format_version 必須是 1" % os.path.basename(path))
                    continue
                if fmt and not fmt.startswith("pyto_rpg_secondary_world"):
                    self.validation_errors.append("%s: format 無效" % os.path.basename(path))
                    continue
                _merge_catalog(merged, raw)
                loaded_paths.append(path)
        self.catalog_paths = tuple(loaded_paths)
        self.background_profiles = dict(merged["background_profiles"])
        self.archetypes = {
            str(species): _normalize_archetype(species, row)
            for species, row in merged["creature_archetypes"].items()
            if str(species).strip() and isinstance(row, dict)
        }
        self.rosters = dict(merged["rosters"])
        self.vfx_profiles = {
            str(profile_id): _normalize_vfx_profile(row)
            for profile_id, row in merged["vfx_profiles"].items()
            if str(profile_id).strip() and isinstance(row, dict)
        }
        self.decoration_styles = dict(merged["decoration_styles"])
        self.action_vfx = dict(merged["action_vfx"])
        raw_actions = merged["action_profiles"]
        if isinstance(raw_actions.get("creatures"), dict):
            raw_actions = raw_actions.get("creatures", {})
        self.action_profiles = dict(raw_actions)
        self.catalog_regions = tuple(merged["regions"])
        self.map_files = tuple(sorted(merged["map_files"]))
        self._fallback_scan_diagnostics = {
            "total": 0, "portal": 0, "hazard": 0, "explicit": 0,
            "truncated": False,
        }
        self.active = bool(self.world_id and self.catalog_paths)
        self._validate_contract()

    @classmethod
    def from_map_loader(cls, project_root, map_loader):
        try:
            metadata = dict((map_loader.payload or {}).get("metadata", {}) or {}) if map_loader else {}
        except Exception:
            metadata = {}
        return cls(project_root, metadata)

    def _validate_contract(self):
        if not self.world_id:
            return
        if not self.catalog_paths:
            self.validation_errors.append("secondary_world 找不到安全 catalog")
            return
        if not self.background_profiles:
            self.validation_errors.append("catalog 缺少 background_profiles")
        if not self.archetypes:
            self.validation_errors.append("catalog 缺少 creature_archetypes")
        for profile_id, profile in self.background_profiles.items():
            if not isinstance(profile, dict):
                self.validation_errors.append("background profile 無效：%s" % profile_id)
                continue
            if _integer(profile.get("quad_budget"), 0) > MAX_BACKGROUND_QUADS:
                self.validation_errors.append("背景 quad_budget 超過 128：%s" % profile_id)
        # Missing optional presentation data does not disable travel/gameplay.

    def install_archetypes(self, target):
        """Install namespaced types before BiomeSystem resolves authored rows."""
        if not self.active or not isinstance(target, dict):
            return 0
        for species, row in self.archetypes.items():
            target[str(species)] = dict(row)
        return len(self.archetypes)

    def overlay_action_profiles(self, base):
        """Overlay gameplay actions once; values are normalized and bounded."""
        if not isinstance(base, dict):
            base = {"format": 1, "creatures": {}}
        creatures = base.setdefault("creatures", {})
        if not isinstance(creatures, dict):
            creatures = {}
            base["creatures"] = creatures
        for asset_id, entry in self.action_profiles.items():
            if not isinstance(entry, dict):
                continue
            key = str(asset_id)
            if not key.startswith("creature."):
                key = "creature." + key
            actions = {}
            source_actions = entry.get("actions", {})
            if isinstance(source_actions, dict):
                for action_id, action in source_actions.items():
                    action_key = str(action_id).strip()
                    if action_key and isinstance(action, dict):
                        actions[action_key] = normalize_action(action, action_key)
            creatures[key] = {
                "override_legacy_contact": bool(entry.get("override_legacy_contact", False)),
                "actions": actions,
            }
        return base

    def merged_vfx_catalog(self, base):
        """Return a raw catalog overlay; CreatureVFX performs final clamping."""
        base = base if isinstance(base, dict) else {}
        out = {
            "format": 1,
            "budgets": dict(base.get("budgets", {}) or {}),
            "profiles": dict(base.get("profiles", {}) or {}),
            "species": dict(base.get("species", {}) or {}),
            "action_overrides": dict(base.get("action_overrides", {}) or {}),
            "effect_fallbacks": dict(base.get("effect_fallbacks", {}) or {}),
        }
        out["budgets"].update({
            "max_events": min(MAX_VFX_EVENTS, _integer(out["budgets"].get("max_events"), MAX_VFX_EVENTS, 1)),
            "max_quads_per_event": min(MAX_VFX_QUADS_PER_EVENT, _integer(out["budgets"].get("max_quads_per_event"), MAX_VFX_QUADS_PER_EVENT, 0)),
            "max_total_quads": min(MAX_VFX_TOTAL_QUADS, _integer(out["budgets"].get("max_total_quads"), MAX_VFX_TOTAL_QUADS, 1)),
        })
        out["profiles"].update(self.vfx_profiles)
        for species, row in self.archetypes.items():
            profile_id = str(row.get("vfx_profile", "") or "")
            if profile_id in out["profiles"]:
                out["species"]["creature." + str(species)] = profile_id
        for asset_id, actions in self.action_vfx.items():
            if not isinstance(actions, dict):
                continue
            key = str(asset_id)
            if not key.startswith("creature."):
                key = "creature." + key
            clean = {
                str(action_id): str(profile_id)
                for action_id, profile_id in actions.items()
                if str(profile_id) in out["profiles"]
            }
            if clean:
                existing = dict(out["action_overrides"].get(key, {}) or {})
                existing.update(clean)
                out["action_overrides"][key] = existing
        return out

    def _regions(self):
        rows = self.metadata.get("regions", ())
        if not isinstance(rows, list) or not rows:
            rows = self.binding.get("regions", ())
        if not isinstance(rows, list) or not rows:
            rows = self.catalog_regions
        return tuple(row for row in rows if isinstance(row, dict))

    @staticmethod
    def _bounds(row):
        bounds = row.get("bounds", ()) if isinstance(row, dict) else ()
        if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
            return None
        try:
            x0, y0, x1, y1 = (float(value) for value in bounds[:4])
        except Exception:
            return None
        if x1 < x0 or y1 < y0:
            return None
        return x0, y0, x1, y1

    def region_at(self, tile_x, tile_y):
        matches = []
        for index, row in enumerate(self._regions()):
            bounds = self._bounds(row)
            if bounds is None:
                continue
            x0, y0, x1, y1 = bounds
            if x0 <= float(tile_x) <= x1 and y0 <= float(tile_y) <= y1:
                area = max(1.0, (x1 - x0 + 1.0) * (y1 - y0 + 1.0))
                priority = _integer(row.get("background_priority", row.get("priority", 0)), 0)
                matches.append((-priority, area, index, row))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        return matches[0][3]

    def background_profile_at(self, tile_x, tile_y):
        row = self.region_at(tile_x, tile_y)
        if isinstance(row, dict):
            candidate = str(row.get("background_profile", "") or "")
            if candidate in self.background_profiles:
                return candidate
        return self.profile_id if self.profile_id in self.background_profiles else ""

    def _annotate_population(self, creatures):
        keep = []
        seen_bosses = set()
        for creature in creatures:
            species = str(getattr(creature, "species", "") or "")
            profile = self.archetypes.get(species, {})
            creature.secondary_world_id = self.world_id
            region = self.region_at(float(creature.x) / TILE_SIZE, float(creature.y) / TILE_SIZE)
            if isinstance(region, dict):
                creature.respawn_region = str(region.get("id", region.get("name", "")) or "")
            if bool(profile.get("boss", getattr(creature, "boss", False))):
                creature.boss = True
                creature.boss_title = str(profile.get("boss_title", getattr(creature, "boss_title", creature.name)) or creature.name)
                unique_key = str(profile.get("unique_key", species) or species)
                if self.boss_unique and unique_key in seen_bosses:
                    creature.active = False
                    continue
                seen_bosses.add(unique_key)
                creature.respawn_enabled = False
            else:
                creature.respawn_enabled = bool(profile.get("respawn_enabled", True))
                if profile.get("respawn_seconds") is not None:
                    creature.respawn_delay_seconds = float(profile["respawn_seconds"])
            keep.append(creature)
        return keep

    def _roster_species(self):
        roster = self.rosters.get(self.roster_id, {})
        if isinstance(roster, (list, tuple)):
            rows = roster
        elif isinstance(roster, dict):
            rows = roster.get("species", roster.get("entries", ()))
        else:
            rows = ()
        out = []
        for raw in rows if isinstance(rows, (list, tuple)) else ():
            if isinstance(raw, str):
                out.append({"species": raw, "count": 1})
            elif isinstance(raw, dict) and raw.get("species"):
                out.append(dict(raw))
        return tuple(out)

    def _spawn_authored(self, biome_system, scene, fallback_y):
        """Spawn secondary rows with habitat-correct outdoor placement.

        The legacy BiomeSystem authored helper marks every actor underground
        and only recognises cave flyers.  Linked worlds can contain sky fauna,
        perched birds and real aquatic actors, so this path resolves each
        locomotion family explicitly while retaining stable entity IDs.
        """
        authored = self.metadata.get("creature_spawns", ())
        if not isinstance(authored, list):
            return []
        out = []
        serial = 0
        world = getattr(biome_system, "world", None)
        world_width = int(getattr(world, "width_tiles", 1) or 1)
        resolver = _BoundedSpawnResolver(biome_system, scene, budgeted=False)
        for row in authored:
            if not isinstance(row, dict):
                continue
            species = str(row.get("species", "") or "")
            profile = self.archetypes.get(species)
            if not profile:
                continue
            count = _integer(row.get("count"), 1, 1, 24)
            if bool(profile.get("boss", False)):
                count = 1
            spacing = _integer(row.get("spacing"), 3, 0, 64)
            preferred_floor = _integer(row.get("floor"), int(float(fallback_y) / TILE_SIZE), 1)
            locomotion = str(profile.get("locomotion", "ground") or "ground")
            for ordinal in range(count):
                authored_tx = _integer(row.get("x"), 10) + ordinal * spacing
                authored_tx = max(1, min(world_width - 2, authored_tx))
                position = None
                # The whole AABB, not only the centre column, must be empty.
                # A bounded X/Y fan repairs wide actors near cliffs without a
                # whole-map search or changing the authored encounter region.
                x_offsets = (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 8, -8, 10, -10)
                y_offsets = (0, -2, 2, -4, 4, -6, 6) if locomotion in (
                    "cave_fly", "ghost_fly", "fly_low", "insect_low",
                ) else (0,)
                for x_offset in x_offsets:
                    tx = max(1, min(world_width - 2, authored_tx + x_offset))
                    search_lo = max(1, tx - 4)
                    search_hi = min(world_width - 1, tx + 5)
                    for y_offset in y_offsets:
                        candidate = None
                        if locomotion in ("fish", "swim"):
                            water_tx = biome_system._find_water_tx(tx, search_lo, search_hi, min_cells=2)
                            if water_tx is not None:
                                candidate = biome_system._aquatic_pos(
                                    water_tx, fallback_y, 0.52,
                                    body_h=float(profile["h"]),
                                )
                        elif locomotion == "waterbird":
                            water_tx = biome_system._find_water_tx(tx, search_lo, search_hi, min_cells=1)
                            if water_tx is not None:
                                candidate = biome_system._water_surface_pos(water_tx, fallback_y, lift_px=2.0)
                        elif locomotion in ("cave_fly", "ghost_fly", "fly_low", "insect_low"):
                            candidate = biome_system._underground_air_pos(
                                tx, max(2, preferred_floor - 4 + y_offset),
                                body_h=float(profile["h"]), search_radius=14,
                            )
                        elif locomotion == "bird":
                            dry_tx = biome_system._find_dry_tx(tx, search_lo, search_hi)
                            if dry_tx is not None:
                                candidate = biome_system._surface_pos(dry_tx, fallback_y)
                        else:
                            candidate = biome_system._underground_floor_pos(
                                tx, preferred_floor, body_h=float(profile["h"]),
                                y_min=1, y_max=int(getattr(world, "height_tiles", 2)) - 1,
                            )
                        # Preserve FIX69 authored semantics: existing authored
                        # actors are checked, while prototype/player proximity
                        # and fallback-only exclusion zones are not introduced.
                        if not resolver.body_clear(
                            candidate, profile, prior=out,
                            include_scene=False, inset=0.25,
                        ):
                            continue
                        if locomotion in ("fish", "swim") and not resolver.legacy_aquatic_samples(candidate, profile):
                            continue
                        position = candidate
                        break
                    if position is not None:
                        break
                if position is None:
                    continue
                x, y = float(position[0]), float(position[1])
                serial += 1
                creature = Creature(
                    entity_id="secondary_%s_%s_%03d" % (
                        self.world_id.replace(".", "_"), species, serial,
                    ),
                    x=x, y=y, species=species, name=profile["name"],
                    hp=profile["hp"], max_hp=profile["hp"],
                    attack_damage=profile["attack"], speed=profile["speed"],
                    width_px=profile["w"], height_px=profile["h"],
                    hostile=profile["hostile"], home_x=x, home_y=y,
                    patrol_radius=TILE_SIZE * _finite(row.get("patrol_tiles"), 2.8, 0.5, 14.0),
                    biome=self.world_id, loot_table=profile["loot"],
                    asset_id="creature." + species,
                    locomotion=locomotion, element_type=profile["element"],
                    boss=profile["boss"], boss_title=profile["boss_title"],
                )
                # This is a world-space classification, not a locomotion hint.
                creature.underground = bool(row.get("underground", False))
                try:
                    from systems.editor_objects import apply_editor_ai_overrides
                    apply_editor_ai_overrides(creature,row)
                except Exception:
                    pass
                scene.add_entity(creature)
                out.append(creature)
        return out

    def _fallback_map_payload(self):
        """Read only the small catalog-declared map used by fallback safety."""
        requested = list(self.map_files)
        # Some valid catalogs (including the biolume world) deliberately omit
        # map_file.  The map metadata is already trusted by MapLoader and its
        # namespaced map_id gives a deterministic project-local fallback.
        map_id = str(self.metadata.get("map_id", self.binding.get("map_id", "")) or "").strip()
        if map_id:
            requested.append(map_id if map_id.endswith(".json") else map_id + ".json")
        seen = set()
        maps_root = _safe_project_path(self.project_root, "maps")
        for raw in requested:
            rel = str(raw or "").strip().replace("\\", "/").lstrip("/")
            if not rel:
                continue
            if not rel.startswith("maps/"):
                rel = "maps/" + rel
            path = _safe_project_path(self.project_root, rel)
            try:
                if (
                    not path or path in seen or not maps_root
                    or os.path.commonpath((maps_root, path)) != maps_root
                    or not os.path.isfile(path)
                    or os.path.getsize(path) > 8 * 1024 * 1024
                ):
                    continue
                seen.add(path)
                with open(path, "r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        return {}

    def _fallback_forbidden_rects(self):
        """Collect portal, arrival, player-start and authored hazard cells."""
        payload = self._fallback_map_payload()
        map_metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        sources = [self.metadata]
        if isinstance(map_metadata, dict) and map_metadata is not self.metadata:
            sources.append(map_metadata)
        spawn_rects = []
        portal_rects = []
        explicit_rects = []
        hazard_rects = []
        scanned = {"total": 0, "portal": 0, "hazard": 0, "explicit": 0}
        scan_limits = {
            "portal": MAX_FALLBACK_PORTAL_ROWS_SCANNED,
            "hazard": MAX_FALLBACK_HAZARD_ROWS_SCANNED,
            "explicit": MAX_FALLBACK_EXPLICIT_ROWS_SCANNED,
        }
        scan_truncated = [False]

        def bounded_rows(rows, family):
            if not isinstance(rows, list) or not rows:
                return ()
            family_left = max(0, int(scan_limits[family]) - int(scanned[family]))
            total_left = max(0, MAX_FALLBACK_EXTERNAL_ROWS_SCANNED - int(scanned["total"]))
            take = min(len(rows), family_left, total_left)
            if take < len(rows):
                # Ignoring an uninspected external row could hide a hazard.
                # Record overflow and add a world-covering sentinel below so
                # the optional fallback safely produces no population.
                scan_truncated[0] = True
            scanned[family] += int(take)
            scanned["total"] += int(take)
            return rows[:take]

        def tile_rect(tx, ty, width=1.0, height=1.0):
            rect = (
                float(tx) * TILE_SIZE, float(ty) * TILE_SIZE,
                (float(tx) + float(width)) * TILE_SIZE,
                (float(ty) + float(height)) * TILE_SIZE,
            )
            if not all(math.isfinite(value) for value in rect) or rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise ValueError("invalid fallback exclusion")
            return rect

        def append_rect(target, value, limit):
            if len(target) >= int(limit):
                scan_truncated[0] = True
                return False
            try:
                rect = tuple(float(item) for item in value[:4])
                if len(rect) < 4 or not all(math.isfinite(item) for item in rect):
                    return True
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    return True
                target.append(rect)
            except Exception:
                pass
            return len(target) < int(limit)

        # Player start must survive even when a hostile file contains hundreds
        # of hazards or portals; it is placed first in the stable final order.
        spawn = payload.get("player_spawn", ()) if isinstance(payload, dict) else ()
        if isinstance(spawn, (list, tuple)) and len(spawn) >= 2:
            try:
                sx, sy = float(spawn[0]), float(spawn[1])
                margin = FALLBACK_PLAYER_CLEARANCE_TILES
                append_rect(spawn_rects, tile_rect(sx - margin, sy - 5.0, margin * 2.0, 6.0), 2)
            except Exception:
                pass

        for metadata in sources:
            portals = metadata.get("portals", ()) if isinstance(metadata, dict) else ()
            for portal in bounded_rows(portals, "portal"):
                if not isinstance(portal, dict):
                    continue
                raw_rect = portal.get("rect", ())
                if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
                    try:
                        append_rect(portal_rects, tile_rect(*[float(value) for value in raw_rect[:4]]), MAX_FALLBACK_FORBIDDEN_RECTS)
                    except Exception:
                        pass
                arrival = portal.get("arrival", ())
                if isinstance(arrival, (list, tuple)) and len(arrival) >= 2:
                    try:
                        ax, ay = float(arrival[0]), float(arrival[1])
                        # Arrival feet plus two tiles of approach room.
                        append_rect(portal_rects, tile_rect(ax - 2.0, ay - 4.0, 5.0, 5.0), MAX_FALLBACK_FORBIDDEN_RECTS)
                    except Exception:
                        pass
            for key in ("hazards", "hazard_cells"):
                hazards = metadata.get(key, ()) if isinstance(metadata, dict) else ()
                for row in bounded_rows(hazards, "hazard"):
                    if isinstance(row, dict):
                        raw_rect = row.get("rect", ())
                        if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
                            try:
                                append_rect(hazard_rects, tile_rect(*[float(value) for value in raw_rect[:4]]), MAX_FALLBACK_FORBIDDEN_RECTS)
                            except Exception:
                                pass
                        elif "x" in row and "y" in row:
                            try:
                                append_rect(hazard_rects, tile_rect(row.get("x"), row.get("y")), MAX_FALLBACK_FORBIDDEN_RECTS)
                            except Exception:
                                pass
                    elif isinstance(row, (list, tuple)) and len(row) >= 2:
                        try:
                            append_rect(hazard_rects, tile_rect(row[0], row[1]), MAX_FALLBACK_FORBIDDEN_RECTS)
                        except Exception:
                            pass

        layers = payload.get("layers", {}) if isinstance(payload, dict) else {}
        if isinstance(layers, dict):
            # Static hazard, active fire and non-zero lava are never valid
            # fallback homes.  Water is intentionally handled by locomotion.
            for layer_name in ("hazard", "fire", "lava"):
                rows = layers.get(layer_name, ())
                for row in bounded_rows(rows, "hazard"):
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    if layer_name == "lava" and len(row) >= 3:
                        try:
                            if float(row[2]) <= 0.0:
                                continue
                        except Exception:
                            pass
                    try:
                        append_rect(hazard_rects, tile_rect(row[0], row[1]), MAX_FALLBACK_FORBIDDEN_RECTS)
                    except Exception:
                        pass
        raw_exclusions = self.binding.get("spawn_exclusion_rects", ())
        for row in bounded_rows(raw_exclusions, "explicit"):
            if isinstance(row, (list, tuple)) and len(row) >= 4:
                try:
                    x0, y0, x1, y1 = [float(value) for value in row[:4]]
                    if x1 >= x0 and y1 >= y0:
                        append_rect(explicit_rects, tile_rect(x0, y0, x1 - x0 + 1.0, y1 - y0 + 1.0), MAX_FALLBACK_FORBIDDEN_RECTS)
                except Exception:
                    pass
        # Priority is safety-critical: player start, portal trigger/arrival and
        # explicit exclusions survive before a stable truncation of hazards.
        base_ordered = spawn_rects + portal_rects + explicit_rects + hazard_rects
        if len(dict.fromkeys(tuple(round(value, 4) for value in rect) for rect in base_ordered)) > MAX_FALLBACK_FORBIDDEN_RECTS:
            scan_truncated[0] = True
        fail_closed = []
        if scan_truncated[0]:
            fail_closed.append((-1.0e12, -1.0e12, 1.0e12, 1.0e12))
        self._fallback_scan_diagnostics = dict(scanned)
        self._fallback_scan_diagnostics["truncated"] = bool(scan_truncated[0])
        ordered = fail_closed + base_ordered
        return tuple(dict.fromkeys(
            tuple(round(value, 4) for value in rect) for rect in ordered
        ))[:MAX_FALLBACK_FORBIDDEN_RECTS]

    @staticmethod
    def _fallback_floor_positions(resolver, tx, y_low, y_high, preferred_y):
        world = resolver.world
        x = (float(tx) + 0.5) * TILE_SIZE
        surfaces = []
        seen = set()
        # Probe at most MAX_FALLBACK_Y_PROBES individual tiles.  Do not hand a
        # full-height column to TileWorld, whose iteration cost would otherwise
        # grow with a malformed map's height even though actor probes are capped.
        for ty in _bounded_integer_fan(preferred_y, y_low, y_high, MAX_FALLBACK_Y_PROBES):
            if not resolver.claim_geometry_probe():
                break
            try:
                rect = (x - 0.10, float(ty) * TILE_SIZE, x + 0.10, (float(ty) + 1.0) * TILE_SIZE)
                cells = world.solid_cells_in_rect(rect, padding=0)
                for _sx, _sy, _tile, solid_rect in cells:
                    surface = round(float(solid_rect[1]), 5)
                    if surface in seen:
                        continue
                    if float(y_low) * TILE_SIZE <= surface <= (float(y_high) + 1.0) * TILE_SIZE:
                        seen.add(surface); surfaces.append((x, surface))
            except Exception:
                continue
        return tuple(surfaces)

    def _fallback_candidates(self, resolver, profile, locomotion, bounds, target_tx, ordinal):
        """Yield one deterministic, locomotion-specific bounded candidate fan."""
        world = resolver.world
        width = int(getattr(world, "width_tiles", 0) or 0)
        height = int(getattr(world, "height_tiles", 0) or 0)
        x0, y0, x1, y1 = bounds
        x_low = max(0, int(math.ceil(x0)))
        x_high = min(width - 1, int(math.floor(x1)))
        y_low = max(1, int(math.ceil(y0)))
        y_high = min(height - 1, int(math.floor(y1)))
        if x_high < x_low or y_high < y_low:
            return
        x_order = _bounded_integer_fan(target_tx, x_low, x_high, MAX_FALLBACK_X_PROBES)
        preferred_y = max(y_low, min(y_high, int(round((y0 + y1) * 0.5))))
        locomotion = str(locomotion or "ground")

        if locomotion in _PERCHED_LOCOMOTION or locomotion not in (
            _AQUATIC_LOCOMOTION | _WATER_SURFACE_LOCOMOTION
            | _LOW_FLY_LOCOMOTION | _FREE_FLY_LOCOMOTION
        ):
            for tx in x_order:
                for position in self._fallback_floor_positions(resolver, tx, y_low, y_high, preferred_y):
                    yield position
            return

        if locomotion in _AQUATIC_LOCOMOTION:
            y_order = _bounded_integer_fan(
                preferred_y + (int(ordinal) % 3) - 1,
                y_low, y_high, MAX_FALLBACK_Y_PROBES,
            )
            for tx in x_order:
                for ty in y_order:
                    if not resolver.claim_geometry_probe():
                        return
                    try:
                        amount = max(0.0, min(1.0, float(resolver.env.water_amount(tx, ty))))
                    except Exception:
                        continue
                    if amount <= 0.025:
                        continue
                    water_top = (float(ty) + 1.0 - amount) * TILE_SIZE
                    water_bottom = (float(ty) + 1.0) * TILE_SIZE
                    center_y = (water_top + water_bottom) * 0.5
                    yield (float(tx) + 0.5) * TILE_SIZE, center_y + float(profile["h"]) * 0.5
            return

        if locomotion in _WATER_SURFACE_LOCOMOTION:
            y_order = _bounded_integer_fan(preferred_y, y_low, y_high, MAX_FALLBACK_Y_PROBES)
            for tx in x_order:
                for ty in y_order:
                    if not resolver.claim_geometry_probe():
                        return
                    try:
                        amount = max(0.0, min(1.0, float(resolver.env.water_amount(tx, ty))))
                    except Exception:
                        continue
                    if amount > 0.025:
                        yield (
                            (float(tx) + 0.5) * TILE_SIZE,
                            (float(ty) + 1.0 - amount) * TILE_SIZE,
                        )
            return

        if locomotion in _LOW_FLY_LOCOMOTION:
            height_px = float(profile["h"])
            lifts = (
                max(18.0, min(52.0, height_px * 0.50 + 18.0)),
                max(24.0, min(60.0, height_px * 0.65 + 25.0)),
                max(32.0, min(68.0, height_px * 0.80 + 34.0)),
            )
            for tx in x_order:
                floors = self._fallback_floor_positions(resolver, tx, y_low, y_high, preferred_y)
                for _x, surface_y in floors[:8]:
                    for lift in lifts:
                        yield (float(_x), float(surface_y) - float(lift))
            return

        # Cave and spectral flyers search open air around the region centre.
        # Full-AABB terrain/water validation rejects narrow centre-column gaps.
        y_order = _bounded_integer_fan(preferred_y, y_low, y_high, MAX_FALLBACK_Y_PROBES)
        half_height = float(profile["h"]) * 0.5
        for tx in x_order:
            x = (float(tx) + 0.5) * TILE_SIZE
            for ty in y_order:
                yield x, (float(ty) + 0.5) * TILE_SIZE + half_height

    @staticmethod
    def _fallback_entity_id(world_id, species, entry_index, ordinal):
        def token(value):
            text = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(value or ""))
            text = text.strip("_") or "unnamed"
            if len(text) > 40:
                checksum = zlib.crc32(str(value).encode("utf-8")) & 0xFFFFFFFF
                text = text[:31] + "_%08x" % checksum
            return text
        return "secondary_%s_fallback_%03d_%s_%02d" % (
            token(world_id), int(entry_index), token(species), int(ordinal),
        )

    def _spawn_roster_fallback(self, biome_system, scene, avoid_world_x=None):
        """Bounded deterministic recovery when a map omitted authored rows."""
        rows = self._roster_species()[:MAX_FALLBACK_ROSTER_ENTRIES]
        regions = self._regions()
        if not rows:
            return []
        world = getattr(biome_system, "world", None)
        if world is None:
            return []
        resolver = _BoundedSpawnResolver(
            biome_system, scene,
            forbidden_rects=self._fallback_forbidden_rects(),
            avoid_world_x=avoid_world_x,
        )
        out = []
        attempted = 0
        spawned_boss_keys = set()
        for entry_index, entry in enumerate(rows):
            species = str(entry.get("species", "") or "")
            profile = self.archetypes.get(species)
            if not profile:
                continue
            is_boss = bool(profile.get("boss", False))
            unique_key = str(profile.get("unique_key", species) or species)
            if is_boss and unique_key in spawned_boss_keys:
                continue
            count = _integer(entry.get("count"), 1, 1, 12)
            if is_boss:
                count = 1
            requested_region = str(entry.get("region", "") or "")
            matching = [row for row in regions if str(row.get("id", "") or "") == requested_region]
            region = matching[0] if matching else (regions[entry_index % len(regions)] if regions else None)
            bounds = self._bounds(region) if region else None
            if bounds is None:
                bounds = (0.0, 1.0, float(world.width_tiles - 1), float(world.height_tiles - 1))
            x0, _y0, x1, _y1 = bounds
            for ordinal in range(count):
                if attempted >= MAX_FALLBACK_ACTORS:
                    return out
                attempted += 1
                fraction = float(ordinal + 1) / float(count + 1)
                target_tx = int(round(x0 + (x1 - x0) * fraction))
                if bool(getattr(world, "horizontal_wrap_enabled", False)) and int(world.width_tiles) > 0:
                    target_tx %= int(world.width_tiles)
                else:
                    target_tx = max(0, min(int(world.width_tiles) - 1, target_tx))
                locomotion = str(profile.get("locomotion", "ground") or "ground")
                candidates = self._fallback_candidates(
                    resolver, profile, locomotion, bounds, target_tx, ordinal,
                )
                position = resolver.first_valid(candidates, profile, locomotion, prior=out)
                if position is None:
                    # Safety is authoritative: never force a roster member into
                    # a wall, waterless cell, hazard or occupied actor AABB.
                    continue
                x, y = float(position[0]), float(position[1])
                creature = Creature(
                    entity_id=self._fallback_entity_id(self.world_id, species, entry_index, ordinal),
                    x=x, y=y, species=species, name=profile["name"],
                    hp=profile["hp"], max_hp=profile["hp"],
                    attack_damage=profile["attack"], speed=profile["speed"],
                    width_px=profile["w"], height_px=profile["h"],
                    hostile=profile["hostile"], home_x=x, home_y=y,
                    patrol_radius=TILE_SIZE * _finite(entry.get("patrol_tiles"), 2.8, 0.5, 12.0),
                    biome=str(region.get("id", self.world_id) if isinstance(region, dict) else self.world_id),
                    loot_table=profile["loot"], asset_id="creature." + species,
                    locomotion=locomotion, element_type=profile["element"],
                    boss=is_boss, boss_title=profile["boss_title"],
                )
                creature.underground = bool(
                    entry.get("underground", region.get("underground", False) if isinstance(region, dict) else False)
                )
                creature.respawn_enabled = False if is_boss else bool(profile.get("respawn_enabled", True))
                if not is_boss and profile.get("respawn_seconds") is not None:
                    creature.respawn_delay_seconds = float(profile["respawn_seconds"])
                scene.add_entity(creature)
                out.append(creature)
                if is_boss:
                    spawned_boss_keys.add(unique_key)
        return out

    def build_creatures(self, biome_system, scene, fallback_y, avoid_world_x=None):
        """Build only secondary-world actors unless metadata requests augment."""
        if not self.active:
            return biome_system.build_creatures(scene, fallback_y, avoid_world_x=avoid_world_x)
        if self.population_mode in ("augment", "main_plus_authored"):
            population = list(biome_system.build_creatures(scene, fallback_y, avoid_world_x=avoid_world_x))
        else:
            population = self._spawn_authored(biome_system, scene, fallback_y)
        if not population:
            population = self._spawn_roster_fallback(
                biome_system, scene, avoid_world_x=avoid_world_x,
            )
        filtered = self._annotate_population(population)
        rejected_ids = {id(creature) for creature in population if creature not in filtered}
        if rejected_ids:
            scene.entities[:] = [entity for entity in scene.entities if id(entity) not in rejected_ids]
        return filtered

    @staticmethod
    def _motif_parts(motif):
        parts = motif.get("parts", ()) if isinstance(motif, dict) else ()
        if isinstance(parts, list) and parts:
            return tuple(parts[:16])
        key = str((motif or {}).get("id", (motif or {}).get("primitive", "mass")) or "mass").lower()
        if "bamboo" in key:
            return ((-18,-62,6,124,"base"),(0,-70,7,140,"accent"),(19,-57,6,114,"base"),(-12,-86,25,5,"color0"),(14,-45,27,5,"color1"))
        if "karst" in key or "mountain" in key:
            return ((0,-48,58,96,"base"),(-18,-77,25,58,"accent"),(17,-91,21,72,"color0"))
        if "stalact" in key or "spike" in key:
            return ((-23,-48,13,96,"base"),(0,-37,15,74,"accent"),(24,-60,11,120,"color0"))
        if "grave" in key or "tomb" in key:
            return ((0,-25,27,50,"base"),(0,-52,9,19,"accent"),(-12,-44,33,7,"accent"))
        if "floating" in key or "island" in key:
            return ((0,-20,72,24,"base"),(-15,2,34,40,"accent"),(17,0,27,34,"color0"))
        if "tree" in key or "flora" in key:
            return ((0,-48,10,96,"base"),(-20,-91,44,38,"accent"),(18,-78,40,34,"color0"))
        if "crystal" in key:
            return ((-18,-29,11,58,"base"),(0,-43,13,86,"accent"),(19,-24,10,48,"color0"))
        return ((-24,-34,38,68,"base"),(12,-26,31,52,"accent"))

    def decoration_parts(self, kind, size=2, visual_time=0.0):
        """Return <=8 local quads for a catalog or unknown authored decor.

        Coordinates are relative to the ground anchor.  The renderer uses this
        only after its built-in decoration cases, so old FIX68 art is unchanged
        while every new secondary-world ID has a visible bounded fallback.
        """
        key = str(kind or "unknown").strip().lower()
        scale = 0.72 + 0.22 * _integer(size, 2, 1, 3)
        authored = self.decoration_styles.get(key)
        if not isinstance(authored, dict):
            authored = self.decoration_styles.get(str(kind), {})
        palette = (
            ((0.16, 0.31, 0.23, 1.0), (0.35, 0.62, 0.38, 0.96), (0.72, 0.85, 0.61, 0.92))
            if self.world_id.startswith("wuxia") else
            ((0.09, 0.30, 0.36, 1.0), (0.22, 0.86, 0.75, 0.92), (0.61, 0.45, 1.0, 0.90))
        )
        if isinstance(authored, dict) and authored:
            colors = authored.get("colors", ())
            if isinstance(colors, (list, tuple)) and colors:
                palette = tuple(_rgba(value, palette[0]) for value in colors[:4])
            raw_parts = authored.get("parts", ())
        else:
            raw_parts = ()

        if not isinstance(raw_parts, (list, tuple)) or not raw_parts:
            if any(word in key for word in ("gate", "arch", "mouth")):
                raw_parts = ((-20,-26,7,52,0),(20,-26,7,52,0),(0,-52,47,8,1),(0,-28,28,39,2))
            elif any(word in key for word in ("pavilion", "temple")):
                raw_parts = ((-17,-23,6,46,0),(17,-23,6,46,0),(0,-48,50,7,1),(0,-56,65,8,2),(0,-21,30,27,0))
            elif any(word in key for word in ("lantern", "chime", "bell")):
                raw_parts = ((0,-23,5,46,0),(0,-50,17,17,1),(0,-50,7,8,2))
            elif "raft" in key:
                raw_parts = ((0,-4,55,8,0),(-15,-10,5,19,1),(0,-10,5,19,1),(15,-10,5,19,1))
            elif "bamboo" in key:
                raw_parts = ((-13,-38,5,76,0),(0,-47,6,94,1),(14,-35,5,70,0),(-8,-63,19,5,2),(10,-48,21,5,2))
            elif any(word in key for word in ("waterfall", "pool", "veil")):
                pulse = 0.68 + 0.20 * math.sin(float(visual_time) * 2.2)
                water = (0.36, 0.78, 0.91, pulse)
                palette = (palette[0], water, (0.72, 0.96, 1.0, pulse))
                raw_parts = ((0,-27,18,54,1),(-4,-29,5,49,2),(0,-2,47,6,1))
            elif any(word in key for word in ("stalact", "fang", "curtain")):
                raw_parts = ((-17,-30,10,60,0),(0,-42,12,84,1),(18,-25,9,50,0))
            elif any(word in key for word in ("tree", "willow")):
                raw_parts = ((0,-37,8,74,0),(-17,-70,34,19,1),(16,-60,30,18,1),(-4,-83,27,17,2))
            elif any(word in key for word in ("sword", "post", "stele", "mark", "grave", "tomb", "mound")):
                raw_parts = ((0,-28,12,56,0),(0,-58,8,18,1),(-12,-42,30,6,2),(0,-3,30,6,0))
            elif any(word in key for word in ("lotus", "flower")):
                raw_parts = ((0,-6,30,8,0),(-11,-15,16,13,1),(11,-15,16,13,1),(0,-20,16,17,2))
            else:
                # Deliberately recognisable fallback instead of silently
                # dropping an unknown map-editor decoration identifier.
                raw_parts = ((0,-19,24,38,0),(-9,-40,13,16,1),(10,-34,15,13,2))

        out = []
        for index, part in enumerate(tuple(raw_parts)[:8]):
            if isinstance(part, dict):
                dx = part.get("dx", 0); dy = part.get("dy", 0)
                width = part.get("w", part.get("width", 8))
                height = part.get("h", part.get("height", 8))
                role = part.get("color", part.get("role", index))
            elif isinstance(part, (list, tuple)) and len(part) >= 4:
                dx, dy, width, height = part[:4]
                role = part[4] if len(part) >= 5 else index
            else:
                continue
            if isinstance(role, str):
                role_index = {"base": 0, "accent": 1}.get(role, _integer(role.replace("color", ""), index))
            else:
                role_index = _integer(role, index)
            color = palette[role_index % len(palette)]
            out.append((
                _finite(dx) * scale, _finite(dy) * scale,
                max(1.0, _finite(width, 8.0, 1.0) * scale),
                max(1.0, _finite(height, 8.0, 1.0) * scale),
                color,
            ))
        return tuple(out[:8])

    def build_background_plan(self, camera_x, camera_y, viewport_w, viewport_h,
                              visual_time=0.0, tile_x=0.0, tile_y=0.0,
                              max_quads=MAX_BACKGROUND_QUADS):
        empty_contract = {
            "draw_phase": "background", "collision": False,
            "metal_background_max_quads": 256,
            "max_catalog_quads": MAX_BACKGROUND_QUADS,
            "overflow_policy": "stable_truncate",
        }
        if not self.active:
            return SecondaryBackgroundPlan("", "", None, (), 0, {}, empty_contract)
        profile_id = self.background_profile_at(tile_x, tile_y)
        profile = self.background_profiles.get(profile_id)
        if not isinstance(profile, dict):
            return SecondaryBackgroundPlan("", "", None, (), 0, {}, empty_contract)
        budget = min(
            MAX_BACKGROUND_QUADS,
            _integer(max_quads, MAX_BACKGROUND_QUADS, 1, MAX_BACKGROUND_QUADS),
            _integer(profile.get("quad_budget"), MAX_BACKGROUND_QUADS, 1, MAX_BACKGROUND_QUADS),
        )
        width = max(1.0, _finite(viewport_w, 1.0))
        height = max(1.0, _finite(viewport_h, 1.0))
        base = _rgba(profile.get("base_color"), (0.04, 0.06, 0.08, 1.0))
        quads = [SecondaryBackgroundQuad(
            float(round(float(camera_x) + width * 0.5)),
            float(round(float(camera_y) + height * 0.5)),
            float(round(width + 4.0)), float(round(height + 4.0)),
            base, "far", "base_fill", False,
        )]
        counts = {"far": 1}
        source_layers = profile.get("layers", ())
        by_id = {
            str(layer.get("id", "")): layer
            for layer in source_layers if isinstance(layer, dict)
        } if isinstance(source_layers, list) else {}
        for layer_id in LAYER_ORDER:
            layer = by_id.get(layer_id)
            if not isinstance(layer, dict) or len(quads) >= budget:
                continue
            spacing = _finite(layer.get("spacing_px"), 180.0, 64.0, 640.0)
            parallax = _finite(layer.get("parallax"), 0.1, 0.0, 0.35)
            baseline = _finite(layer.get("baseline_ratio"), 0.70, 0.08, 0.98)
            jitter = _finite(layer.get("jitter_px"), 18.0, 0.0, 120.0)
            layer_cap = min(
                _integer(layer.get("max_quads"), 32, 1, 96),
                budget - len(quads),
            )
            colors = tuple(_rgba(color, base) for color in layer.get("colors", ()))
            colors = colors or (base,)
            accent = _rgba(layer.get("accent"), colors[-1])
            alpha = _finite(layer.get("alpha"), 0.64, 0.02, 0.90)
            motifs = [row for row in layer.get("motifs", ()) if isinstance(row, dict)]
            if not motifs:
                motifs = [{"id": "mass", "weight": 1}]
            total_weight = sum(max(1, _integer(row.get("weight"), 1)) for row in motifs)
            phase = float(camera_x) * parallax
            first = int(math.floor((phase - spacing) / spacing))
            last = int(math.ceil((phase + width + spacing) / spacing))
            before = len(quads)
            for slot in range(first, last + 1):
                if len(quads) - before >= layer_cap or len(quads) >= budget:
                    break
                ticket = _stable_unit(self.world_id, profile_id, layer_id, slot) * total_weight
                chosen = motifs[-1]
                cursor = 0.0
                for motif in motifs:
                    cursor += max(1, _integer(motif.get("weight"), 1))
                    if ticket <= cursor:
                        chosen = motif
                        break
                scale = _finite(chosen.get("scale"), 1.0, 0.25, 3.0)
                anchor_x = float(camera_x) + slot * spacing - phase + spacing * 0.5
                anchor_x += (_stable_unit(profile_id, layer_id, slot, "x") - 0.5) * spacing * 0.18
                anchor_y = float(camera_y) + height * baseline
                anchor_y += (_stable_unit(profile_id, layer_id, slot, "y") - 0.5) * jitter * 2.0
                motif_id = str(chosen.get("id", chosen.get("primitive", "motif")) or "motif")
                for raw_part in self._motif_parts(chosen):
                    if len(quads) - before >= layer_cap or len(quads) >= budget:
                        break
                    if isinstance(raw_part, dict):
                        dx = raw_part.get("dx", 0); dy = raw_part.get("dy", 0)
                        part_w = raw_part.get("w", raw_part.get("width", 8))
                        part_h = raw_part.get("h", raw_part.get("height", 8))
                        role = str(raw_part.get("color", raw_part.get("role", "base")))
                    elif isinstance(raw_part, (list, tuple)) and len(raw_part) >= 4:
                        dx, dy, part_w, part_h = raw_part[:4]
                        role = str(raw_part[4] if len(raw_part) >= 5 else "base")
                    else:
                        continue
                    if role == "accent":
                        color = accent
                    elif role.startswith("color"):
                        index = _integer(role[5:] or 0, 0) % len(colors)
                        color = colors[index]
                    else:
                        color = colors[int(_stable_unit(profile_id, layer_id, slot, role) * len(colors)) % len(colors)]
                    pulse = _finite(chosen.get("pulse"), 0.0, 0.0, 0.35)
                    pulse_alpha = 1.0 + pulse * math.sin(float(visual_time) * 1.7 + slot)
                    quads.append(SecondaryBackgroundQuad(
                        float(round(anchor_x + _finite(dx) * scale)),
                        float(round(anchor_y + _finite(dy) * scale)),
                        max(1.0, float(round(_finite(part_w, 8.0, 1.0) * scale))),
                        max(1.0, float(round(_finite(part_h, 8.0, 1.0) * scale))),
                        _with_alpha(color, alpha * pulse_alpha),
                        layer_id, motif_id, False,
                    ))
            counts[layer_id] = len(quads) - before
        return SecondaryBackgroundPlan(
            profile_id, str(profile.get("display_name", profile_id) or profile_id),
            base, tuple(quads[:budget]), budget, counts, empty_contract,
        )


__all__ = (
    "FORMAT_VERSION", "LAYER_ORDER", "MAX_BACKGROUND_QUADS",
    "MAX_VFX_EVENTS", "MAX_VFX_QUADS_PER_EVENT", "MAX_VFX_TOTAL_QUADS",
    "SecondaryBackgroundPlan", "SecondaryBackgroundQuad",
    "SecondaryWorldRuntime", "is_secondary_world", "secondary_world_metadata",
)
