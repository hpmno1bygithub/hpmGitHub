# -*- coding: utf-8 -*-
"""FIX68 data-driven underground background planner.

The module deliberately owns *data selection and cheap quad planning only*.
It does not import UIKit, Metal, the game loop, or collision systems.  A Metal
renderer can consume a plan without creating textures or per-decoration UIKit
views::

    plan = build_background_plan(
        project_root, map_metadata, current_map_path,
        camera_x, camera_y, viewport_w, viewport_h, visual_time,
    )
    quads = tuple(renderer._quad(q.x, q.y, q.width, q.height, q.rgba)
                  for q in plan.quads)

Every emitted record is background-only and non-collidable by contract.  The
catalog contribution is capped at 128 instanced quads so the other 128 entries
in the existing 256-quad Metal background buffer remain available for the
terrain/backdrop pass.
"""
from __future__ import division

import json
import math
import os
import threading
import zlib
from collections import namedtuple


CATALOG_RELATIVE_PATH = "assets/underground_background_catalog.json"
METADATA_FORMAT_VERSION = 1
LAYER_ORDER = ("far", "mid", "near")
SUPPORTED_PROFILE_IDS = (
    "underground.glow_moss",
    "underground.crystal_mine",
    "underground.magma_hell",
    "underground.underground_castle",
    "underground.boss_dungeon",
    "underground.egyptian_pyramid",
)

_PROFILE_BY_THEME = {
    "glow_moss": "underground.glow_moss",
    "glow_moss_cavern": "underground.glow_moss",
    "crystal": "underground.crystal_mine",
    "crystal_mine": "underground.crystal_mine",
    "magma": "underground.magma_hell",
    "magma_hell": "underground.magma_hell",
    "underground_castle": "underground.underground_castle",
    "castle": "underground.underground_castle",
    "boss_dungeon": "underground.boss_dungeon",
    "boss": "underground.boss_dungeon",
    "egyptian_pyramid": "underground.egyptian_pyramid",
    "pyramid": "underground.egyptian_pyramid",
}

BackgroundQuad = namedtuple(
    "BackgroundQuad",
    "x y width height rgba layer motif collision",
)
BackgroundPlan = namedtuple(
    "BackgroundPlan",
    "profile_id display_name base_color quads budget layer_counts contract",
)


class BackgroundCatalogError(ValueError):
    """Raised only when strict catalog loading or validation is requested."""


_CACHE_LOCK = threading.RLock()
_CATALOG_CACHE = {}


def background_metadata(theme):
    """Return the canonical map-metadata binding for one underground theme.

    This helper is intentionally independent of the external JSON file so the
    map generator can author stable bindings even when it is run from a zipped
    runtime before an assets folder is selected.
    """
    key = str(theme or "").strip().lower()
    profile_id = _PROFILE_BY_THEME.get(key, "")
    if not profile_id:
        return {}
    return {
        "background_profile": profile_id,
        "background_profile_version": METADATA_FORMAT_VERSION,
        "background_catalog": CATALOG_RELATIVE_PATH,
        "background_layer_order": list(LAYER_ORDER),
        "background_draw_phase": "background",
        "background_collision": False,
        "background_quad_budget": 128,
        "background_backdrop_reserve": 128,
        "background_generator": "fix68.data_driven_parallax.v1",
    }


def dynamic_background_metadata():
    """Return the contract used by the main map's bounded underground zones."""
    return {
        "background_profile_version": METADATA_FORMAT_VERSION,
        "background_catalog": CATALOG_RELATIVE_PATH,
        "background_profile_mode": "underground_biome_bounds",
        "background_layer_order": list(LAYER_ORDER),
        "background_draw_phase": "background",
        "background_collision": False,
        "background_quad_budget": 128,
        "background_backdrop_reserve": 128,
        "background_generator": "fix68.data_driven_parallax.v1",
    }


def _catalog_path(project_root, metadata):
    root = os.path.abspath(os.path.expanduser(str(project_root or os.getcwd())))
    rel = CATALOG_RELATIVE_PATH
    if isinstance(metadata, dict):
        candidate = str(metadata.get("background_catalog", "") or "").strip()
        if candidate:
            rel = candidate
    rel = rel.replace("\\", "/").lstrip("/")
    path = os.path.abspath(os.path.join(root, *rel.split("/")))
    try:
        if os.path.commonpath((root, path)) != root:
            path = os.path.join(root, *CATALOG_RELATIVE_PATH.split("/"))
    except Exception:
        path = os.path.join(root, *CATALOG_RELATIVE_PATH.split("/"))
    return path


def _read_signature(path):
    try:
        stat = os.stat(path)
        return (
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000))),
            int(getattr(stat, "st_size", 0)),
        )
    except Exception:
        return (0, 0)


def clear_catalog_cache():
    with _CACHE_LOCK:
        _CATALOG_CACHE.clear()


def load_background_catalog(project_root, metadata=None, strict=False):
    """Load and cache the external catalog; return ``None`` on safe fallback.

    The cache is invalidated by mtime/size, allowing editor-side catalog
    changes to appear without restarting Pyto.  Runtime callers normally keep
    ``strict=False`` so a missing custom asset can never stop gameplay.
    """
    path = _catalog_path(project_root, metadata)
    signature = _read_signature(path)
    with _CACHE_LOCK:
        cached = _CATALOG_CACHE.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        errors = validate_background_catalog(data)
        if errors:
            raise BackgroundCatalogError("; ".join(errors))
    except Exception as exc:
        if strict:
            if isinstance(exc, BackgroundCatalogError):
                raise
            raise BackgroundCatalogError("無法載入地下背景目錄：%s" % exc)
        return None
    with _CACHE_LOCK:
        _CATALOG_CACHE[path] = (signature, data)
    return data


def _number(value, default=0.0):
    try:
        value = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return value


def _integer(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def validate_background_catalog(data):
    """Return deterministic schema/Metal-budget errors for a catalog object."""
    errors = []
    if not isinstance(data, dict):
        return ("catalog 必須是 JSON object",)
    if data.get("format") != "pyto_rpg_background_catalog":
        errors.append("format 必須是 pyto_rpg_background_catalog")
    if _integer(data.get("format_version"), 0) != 1:
        errors.append("format_version 必須是 1")
    contract = data.get("renderer_contract")
    if not isinstance(contract, dict):
        errors.append("缺少 renderer_contract")
        contract = {}
    if contract.get("draw_phase") != "background":
        errors.append("renderer_contract.draw_phase 必須是 background")
    if contract.get("collision") is not False:
        errors.append("renderer_contract.collision 必須是 false")
    if contract.get("diggable") is not False:
        errors.append("renderer_contract.diggable 必須是 false")
    metal_max = _integer(contract.get("metal_background_max_quads"), 0)
    catalog_max = _integer(contract.get("max_catalog_quads"), 0)
    backdrop = _integer(contract.get("backdrop_reserved_quads"), 0)
    if metal_max <= 0 or catalog_max <= 0 or backdrop < 0:
        errors.append("背景 quad 預算必須為正值")
    if catalog_max > 128:
        errors.append("max_catalog_quads 不可超過 128")
    if catalog_max + backdrop > metal_max:
        errors.append("catalog + backdrop 預算超過 Metal background buffer")

    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        errors.append("缺少 profiles")
        profiles = {}
    for profile_id in SUPPORTED_PROFILE_IDS:
        if profile_id not in profiles:
            errors.append("缺少背景 profile：%s" % profile_id)
    for profile_id, profile in sorted(profiles.items()):
        prefix = str(profile_id)
        if not isinstance(profile, dict):
            errors.append("%s 必須是 object" % prefix)
            continue
        budget = _integer(profile.get("quad_budget"), 0)
        if budget <= 0 or budget > catalog_max:
            errors.append("%s.quad_budget 超出目錄預算" % prefix)
        if _parse_rgba(profile.get("base_color")) is None:
            errors.append("%s.base_color 無效" % prefix)
        layers = profile.get("layers")
        if not isinstance(layers, list):
            errors.append("%s.layers 必須是陣列" % prefix)
            continue
        layer_ids = tuple(str(row.get("id", "")) for row in layers if isinstance(row, dict))
        if layer_ids != LAYER_ORDER:
            errors.append("%s.layers 必須依序為 far/mid/near" % prefix)
        layer_total = 1
        for layer in layers:
            if not isinstance(layer, dict):
                errors.append("%s 包含無效 layer" % prefix)
                continue
            layer_id = str(layer.get("id", "?"))
            parallax = _number(layer.get("parallax"), -1.0)
            if not 0.0 <= parallax <= 0.35:
                errors.append("%s.%s.parallax 必須介於 0 和 0.35" % (prefix, layer_id))
            spacing = _number(layer.get("spacing_px"), 0.0)
            if spacing < 64.0:
                errors.append("%s.%s.spacing_px 過小" % (prefix, layer_id))
            layer_budget = _integer(layer.get("max_quads"), 0)
            if layer_budget <= 0:
                errors.append("%s.%s.max_quads 必須為正值" % (prefix, layer_id))
            layer_total += max(0, layer_budget)
            motifs = layer.get("motifs")
            if not isinstance(motifs, list) or not motifs:
                errors.append("%s.%s 缺少 motifs" % (prefix, layer_id))
        if layer_total > budget:
            errors.append("%s 分層預算總和超過 profile.quad_budget" % prefix)

    bindings = data.get("map_bindings")
    if not isinstance(bindings, dict):
        errors.append("缺少 map_bindings")
    else:
        for map_id, profile_id in sorted(bindings.items()):
            if profile_id not in profiles:
                errors.append("map_bindings.%s 指向不存在的 profile" % map_id)
    return tuple(errors)


def validate_map_background_metadata(metadata, catalog, map_path=""):
    """Return consistency errors for one map metadata/catalog binding."""
    errors = []
    if not isinstance(metadata, dict):
        return ("map metadata 必須是 object",)
    dynamic_mode = str(metadata.get("background_profile_mode", "")) == "underground_biome_bounds"
    profile_id = resolve_background_profile(metadata, map_path, catalog)
    profiles = catalog.get("profiles", {}) if isinstance(catalog, dict) else {}
    if dynamic_mode:
        errors.extend(validate_underground_biome_bindings(metadata, catalog))
    elif not profile_id:
        errors.append("找不到 background_profile")
    elif profile_id not in profiles:
        errors.append("background_profile 不存在：%s" % profile_id)
    if metadata.get("background_collision") is not False:
        errors.append("background_collision 必須是 false")
    if str(metadata.get("background_draw_phase", "")) != "background":
        errors.append("background_draw_phase 必須是 background")
    if tuple(metadata.get("background_layer_order", ())) != LAYER_ORDER:
        errors.append("background_layer_order 必須是 far/mid/near")
    catalog_budget = _integer(
        (catalog.get("renderer_contract", {}) if isinstance(catalog, dict) else {}).get("max_catalog_quads"),
        0,
    )
    map_budget = _integer(metadata.get("background_quad_budget"), 0)
    if map_budget <= 0 or map_budget > catalog_budget:
        errors.append("background_quad_budget 超出 catalog 預算")
    if bool(metadata.get("background_collision", True)):
        errors.append("背景不得加入碰撞")
    return tuple(errors)


def validate_underground_biome_bindings(metadata, catalog):
    """Validate the main-map ``underground_biomes`` bounds and theme links."""
    errors = []
    if not isinstance(metadata, dict):
        return ("map metadata 必須是 object",)
    rows = metadata.get("underground_biomes")
    if not isinstance(rows, list) or not rows:
        return ("缺少 underground_biomes",)
    for index, row in enumerate(rows):
        prefix = "underground_biomes[%d]" % index
        if not isinstance(row, dict):
            errors.append("%s 必須是 object" % prefix)
            continue
        bounds = row.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
            errors.append("%s.bounds 必須是 [x0,y0,x1,y1]" % prefix)
            continue
        x0, y0, x1, y1 = (_number(value, -1.0) for value in bounds[:4])
        if x1 < x0 or y1 < y0:
            errors.append("%s.bounds 順序無效" % prefix)
        theme = str(row.get("background_theme", row.get("type", "")) or "").lower()
        profile_id = _catalog_profile_for_theme(theme, catalog)
        if not profile_id:
            errors.append("%s.type 沒有背景 profile：%s" % (prefix, theme))
    return tuple(errors)


def _catalog_profile_for_theme(theme, catalog):
    normalized = str(theme or "").strip().lower()
    profiles = catalog.get("profiles", {}) if isinstance(catalog, dict) else {}
    aliases = catalog.get("theme_aliases", {}) if isinstance(catalog, dict) else {}
    candidate = str(aliases.get(normalized, "") or "") if isinstance(aliases, dict) else ""
    if not candidate:
        candidate = _PROFILE_BY_THEME.get(normalized, "")
    if candidate and (not profiles or candidate in profiles):
        return candidate
    return ""


def _profile_at_underground_tile(metadata, catalog, tile_x, tile_y):
    if tile_x is None or tile_y is None:
        return ""
    tx = _number(tile_x, -1000000.0)
    ty = _number(tile_y, -1000000.0)
    rows = metadata.get("underground_biomes", ()) if isinstance(metadata, dict) else ()
    matches = []
    for index, row in enumerate(rows if isinstance(rows, list) else ()):
        if not isinstance(row, dict):
            continue
        bounds = row.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
            continue
        x0, y0, x1, y1 = (_number(value, -1000000.0) for value in bounds[:4])
        if not (x0 <= tx <= x1 and y0 <= ty <= y1):
            continue
        theme = row.get("background_theme", row.get("type", ""))
        profile_id = _catalog_profile_for_theme(theme, catalog)
        if not profile_id:
            continue
        # Smaller authored zones beat broad bands (for example crystal/boss
        # rooms that overlap the main world's bottom magma band).  An explicit
        # priority can override that rule without relying on JSON list order.
        area = max(1.0, (x1 - x0 + 1.0) * (y1 - y0 + 1.0))
        priority = _integer(row.get("background_priority"), 0)
        matches.append((-priority, area, index, profile_id))
    if not matches:
        return ""
    matches.sort(key=lambda value: (value[0], value[1], value[2]))
    return matches[0][3]


def resolve_background_profile(metadata=None, map_path="", catalog=None,
                               tile_x=None, tile_y=None):
    """Resolve bounded biome, explicit metadata, map id/path, then aliases."""
    metadata = metadata if isinstance(metadata, dict) else {}
    profiles = catalog.get("profiles", {}) if isinstance(catalog, dict) else {}
    bounded = _profile_at_underground_tile(metadata, catalog, tile_x, tile_y)
    if bounded:
        return bounded
    explicit = str(metadata.get("background_profile", "") or "").strip()
    if explicit and (not profiles or explicit in profiles):
        return explicit

    map_id = str(metadata.get("map_id", "") or "").strip().lower()
    if not map_id:
        path = str(map_path or "").replace("\\", "/")
        map_id = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    bindings = catalog.get("map_bindings", {}) if isinstance(catalog, dict) else {}
    candidate = str(bindings.get(map_id, "") or "") if isinstance(bindings, dict) else ""
    if candidate and (not profiles or candidate in profiles):
        return candidate

    for key in (metadata.get("background_theme"), metadata.get("theme"), map_id):
        candidate = _catalog_profile_for_theme(key, catalog)
        if candidate:
            return candidate
    return ""


def _parse_rgba(value):
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return None
    try:
        return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4, 6))
    except Exception:
        return None


def _rgba_alpha(color, multiplier, maximum=0.86):
    return (
        float(color[0]), float(color[1]), float(color[2]),
        max(0.0, min(float(maximum), float(color[3]) * float(multiplier))),
    )


def _stable_unit(*parts):
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return float(zlib.crc32(payload) & 0xFFFFFFFF) / 4294967295.0


def _weighted_motif(motifs, seed_parts):
    rows = [row for row in motifs if isinstance(row, dict) and row.get("primitive")]
    if not rows:
        return {"primitive": "cavern_mass", "weight": 1}
    total = sum(max(1, _integer(row.get("weight"), 1)) for row in rows)
    ticket = _stable_unit(*seed_parts) * float(total)
    cursor = 0.0
    for row in rows:
        cursor += float(max(1, _integer(row.get("weight"), 1)))
        if ticket <= cursor:
            return row
    return rows[-1]


def _part(dx, dy, width, height, color):
    return (float(dx), float(dy), max(1.0, float(width)), max(1.0, float(height)), color)


def _primitive_parts(name, scale, base, accent):
    """Return low-quad pixel silhouettes centered around a baseline anchor."""
    s = max(0.25, float(scale))
    name = str(name or "cavern_mass")
    if name == "cavern_mass":
        return (
            _part(-28*s, -35*s, 42*s, 70*s, base),
            _part(8*s, -27*s, 34*s, 54*s, base),
            _part(31*s, -17*s, 20*s, 34*s, accent),
        )
    if name == "root_arch":
        return (
            _part(-29*s, -32*s, 10*s, 64*s, base),
            _part(29*s, -36*s, 9*s, 72*s, base),
            _part(0, -66*s, 67*s, 10*s, base),
            _part(-19*s, -58*s, 7*s, 25*s, accent),
        )
    if name == "giant_mushroom":
        return (
            _part(0, -21*s, 9*s, 42*s, base),
            _part(0, -46*s, 52*s, 13*s, accent),
            _part(-14*s, -49*s, 8*s, 4*s, base),
            _part(12*s, -44*s, 7*s, 4*s, base),
        )
    if name == "hanging_vine":
        return (
            _part(0, -35*s, 5*s, 70*s, base),
            _part(-8*s, -49*s, 14*s, 4*s, accent),
            _part(8*s, -28*s, 14*s, 4*s, accent),
            _part(-4*s, -6*s, 7*s, 7*s, accent),
        )
    if name == "spore_cluster":
        return tuple(
            _part(dx*s, dy*s, size*s, size*s, accent)
            for dx, dy, size in ((-18, -27, 6), (-4, -43, 4), (12, -20, 5), (23, -50, 3), (1, -8, 4))
        )
    if name == "crystal_cluster":
        return (
            _part(-18*s, -24*s, 10*s, 48*s, base),
            _part(0, -34*s, 12*s, 68*s, accent),
            _part(18*s, -20*s, 9*s, 40*s, base),
            _part(7*s, -14*s, 5*s, 27*s, accent),
            _part(-3*s, -56*s, 5*s, 12*s, _rgba_alpha(accent, 1.12)),
        )
    if name == "mine_brace":
        return (
            _part(-27*s, -32*s, 8*s, 64*s, base),
            _part(27*s, -32*s, 8*s, 64*s, base),
            _part(0, -62*s, 62*s, 8*s, accent),
            _part(0, -31*s, 5*s, 52*s, accent),
        )
    if name == "hoist_chain":
        return tuple(_part(0, (-10-index*11)*s, 7*s, 7*s, accent) for index in range(5))
    if name == "basalt_teeth":
        return (
            _part(-28*s, -26*s, 18*s, 52*s, base),
            _part(-7*s, -38*s, 15*s, 76*s, base),
            _part(13*s, -29*s, 17*s, 58*s, base),
            _part(31*s, -18*s, 13*s, 36*s, accent),
        )
    if name == "lava_fall":
        return (
            _part(0, -38*s, 16*s, 76*s, base),
            _part(0, -38*s, 7*s, 73*s, accent),
            _part(0, 1*s, 39*s, 7*s, accent),
        )
    if name == "furnace_arch":
        return (
            _part(-28*s, -29*s, 11*s, 58*s, base),
            _part(28*s, -29*s, 11*s, 58*s, base),
            _part(0, -58*s, 67*s, 12*s, base),
            _part(0, -13*s, 28*s, 24*s, accent),
        )
    if name == "broken_chain":
        return tuple(
            _part((-24+index*12)*s, (-45+abs(index-2)*8)*s, 8*s, 8*s, accent)
            for index in range(5)
        )
    if name == "ember_cluster":
        return tuple(
            _part(dx*s, dy*s, size*s, size*s, accent)
            for dx, dy, size in ((-22, -12, 4), (-11, -39, 5), (2, -23, 3), (16, -51, 4), (25, -29, 3))
        )
    if name == "castle_arch":
        return (
            _part(-30*s, -35*s, 11*s, 70*s, base),
            _part(30*s, -35*s, 11*s, 70*s, base),
            _part(0, -69*s, 72*s, 11*s, base),
            _part(0, -55*s, 38*s, 8*s, accent),
        )
    if name == "barred_window":
        return (
            _part(0, -38*s, 44*s, 50*s, base),
            _part(-11*s, -38*s, 5*s, 43*s, accent),
            _part(0, -38*s, 5*s, 43*s, accent),
            _part(11*s, -38*s, 5*s, 43*s, accent),
            _part(0, -38*s, 37*s, 5*s, accent),
        )
    if name == "torn_banner":
        return (
            _part(0, -42*s, 8*s, 72*s, base),
            _part(17*s, -43*s, 30*s, 40*s, accent),
            _part(10*s, -20*s, 12*s, 13*s, accent),
            _part(24*s, -17*s, 9*s, 9*s, accent),
        )
    if name == "abyss_ribs":
        return (
            _part(-32*s, -31*s, 9*s, 62*s, base),
            _part(-16*s, -48*s, 8*s, 73*s, base),
            _part(0, -56*s, 8*s, 82*s, accent),
            _part(16*s, -48*s, 8*s, 73*s, base),
            _part(32*s, -31*s, 9*s, 62*s, base),
        )
    if name == "ritual_monolith":
        return (
            _part(0, -36*s, 27*s, 72*s, base),
            _part(0, -39*s, 12*s, 58*s, accent),
            _part(0, -71*s, 17*s, 9*s, accent),
        )
    if name == "void_sigil":
        return (
            _part(0, -36*s, 44*s, 5*s, accent),
            _part(0, -36*s, 5*s, 44*s, accent),
            _part(-17*s, -53*s, 7*s, 7*s, base),
            _part(17*s, -53*s, 7*s, 7*s, base),
            _part(0, -36*s, 12*s, 12*s, accent),
        )
    if name == "pyramid_column":
        return (
            _part(0, -35*s, 16*s, 70*s, base),
            _part(0, -70*s, 31*s, 10*s, accent),
            _part(0, 0, 33*s, 10*s, accent),
            _part(0, -36*s, 7*s, 48*s, accent),
        )
    if name == "sealed_tomb_door":
        return (
            _part(0, -34*s, 53*s, 68*s, base),
            _part(0, -39*s, 31*s, 51*s, accent),
            _part(-10*s, -40*s, 5*s, 31*s, base),
            _part(10*s, -40*s, 5*s, 31*s, base),
        )
    if name == "hieroglyph_band":
        return (
            _part(0, -36*s, 66*s, 18*s, base),
            _part(-24*s, -36*s, 7*s, 7*s, accent),
            _part(-8*s, -36*s, 5*s, 12*s, accent),
            _part(9*s, -36*s, 11*s, 5*s, accent),
            _part(25*s, -36*s, 6*s, 10*s, accent),
        )
    if name == "sarcophagus":
        return (
            _part(0, -28*s, 27*s, 56*s, base),
            _part(0, -58*s, 23*s, 12*s, accent),
            _part(0, -31*s, 13*s, 36*s, accent),
            _part(0, 0, 34*s, 8*s, base),
        )
    if name == "torch_niche":
        return (
            _part(0, -38*s, 32*s, 48*s, base),
            _part(0, -32*s, 5*s, 28*s, accent),
            _part(0, -51*s, 13*s, 17*s, accent),
            _part(0, -51*s, 5*s, 24*s, _rgba_alpha(accent, 1.12)),
        )
    return (_part(0, -28*s, 48*s, 56*s, base),)


def _pixel_snap(value):
    return float(round(float(value)))


_FALLBACK_PALETTES = {
    "underground.glow_moss": ("#091C1B", "#12302D", "#1B4A3D", "#173B31"),
    "underground.crystal_mine": ("#0B1426", "#15243D", "#223E62", "#1D3454"),
    "underground.magma_hell": ("#1D090D", "#2C1012", "#421516", "#311012"),
    "underground.underground_castle": ("#111119", "#242330", "#302E3A", "#24222D"),
    "underground.boss_dungeon": ("#09070F", "#20152A", "#35213A", "#29182F"),
    "underground.egyptian_pyramid": ("#251A12", "#4B3523", "#755334", "#5A3E28"),
    "fallback.underground": ("#16191D", "#252A2F", "#353B40", "#1D2024"),
}


def _needs_underground_plan(metadata, map_path, tile_y, profile_id):
    if profile_id:
        return True
    if bool(metadata.get("underground_only", False)):
        return True
    if tile_y is not None:
        start = _number(metadata.get("underground_start_row"), 1000000.0)
        if _number(tile_y, -1000000.0) >= start:
            return True
    path_id = str(map_path or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return bool(_PROFILE_BY_THEME.get(path_id.lower(), ""))


def _fallback_background_plan(profile_id, metadata, camera_x, camera_y,
                              viewport_w, viewport_h, quad_budget):
    """Create a texture-free, three-depth safety background in <=24 quads."""
    selected = profile_id if profile_id in _FALLBACK_PALETTES else "fallback.underground"
    raw_palette = _FALLBACK_PALETTES[selected]
    palette = tuple(_parse_rgba(value) for value in raw_palette)
    map_limit = max(1, min(128, _integer(metadata.get("background_quad_budget"), 128)))
    requested = 24 if quad_budget is None else max(1, _integer(quad_budget, 24))
    budget = min(24, 128, map_limit, requested)
    width = max(1.0, _number(viewport_w, 1.0))
    height = max(1.0, _number(viewport_h, 1.0))
    quads = [BackgroundQuad(
        _pixel_snap(float(camera_x) + width * 0.5),
        _pixel_snap(float(camera_y) + height * 0.5),
        max(1.0, _pixel_snap(width + 4.0)),
        max(1.0, _pixel_snap(height + 4.0)),
        palette[0], "far", "fallback_fill", False,
    )]
    layer_counts = {"far": 1}
    layer_rows = (
        ("far", 0.035, 250.0, 0.64, 7, 0.38, palette[1]),
        ("mid", 0.11, 182.0, 0.72, 8, 0.52, palette[2]),
        ("near", 0.23, 142.0, 0.8, 8, 0.64, palette[3]),
    )
    for layer_id, parallax, spacing, baseline, layer_limit, alpha, color in layer_rows:
        if len(quads) >= budget:
            break
        phase = float(camera_x) * parallax
        first = int(math.floor((phase - spacing) / spacing))
        last = int(math.ceil((phase + width + spacing) / spacing))
        count = 0
        for slot in range(first, last + 1):
            if count >= layer_limit or len(quads) >= budget:
                break
            local_x = slot * spacing - phase + spacing * 0.5
            seed = (selected, "fallback", layer_id, slot)
            block_w = spacing * (0.34 + _stable_unit(*(seed + ("w",))) * 0.2)
            block_h = height * (0.16 + _stable_unit(*(seed + ("h",))) * 0.2)
            y_jitter = (_stable_unit(*(seed + ("y",))) - 0.5) * height * 0.08
            quads.append(BackgroundQuad(
                _pixel_snap(float(camera_x) + local_x),
                _pixel_snap(float(camera_y) + height * baseline - block_h * 0.5 + y_jitter),
                max(1.0, _pixel_snap(block_w)),
                max(1.0, _pixel_snap(block_h)),
                _rgba_alpha(color, alpha, 0.72),
                layer_id, "fallback_cavern_mass", False,
            ))
            count += 1
        layer_counts[layer_id] = layer_counts.get(layer_id, 0) + count
    return BackgroundPlan(
        selected,
        "地下安全背景（素材目錄缺失 fallback）",
        palette[0], tuple(quads[:budget]), budget, layer_counts,
        {
            "draw_phase": "background", "collision": False,
            "max_catalog_quads": 128, "backdrop_reserved_quads": 128,
            "fallback": True, "batch": "metal_instanced_quads",
        },
    )


def _build_layer_quads(profile_id, layer, camera_x, camera_y, viewport_w, viewport_h,
                       visual_time, remaining_budget, max_alpha):
    layer_id = str(layer.get("id", "far"))
    spacing = max(64.0, _number(layer.get("spacing_px"), 180.0))
    parallax = max(0.0, min(0.35, _number(layer.get("parallax"), 0.1)))
    baseline = max(0.15, min(0.95, _number(layer.get("baseline_ratio"), 0.7)))
    jitter = max(0.0, min(96.0, _number(layer.get("jitter_px"), 0.0)))
    repetitions = max(1, min(3, _integer(layer.get("motifs_per_anchor"), 1)))
    layer_limit = min(max(0, _integer(layer.get("max_quads"), 0)), max(0, int(remaining_budget)))
    alpha = max(0.02, min(max_alpha, _number(layer.get("alpha"), 0.6)))
    scale_range = layer.get("scale_range", (0.8, 1.2))
    if not isinstance(scale_range, (list, tuple)) or len(scale_range) < 2:
        scale_range = (0.8, 1.2)
    scale_low = max(0.25, _number(scale_range[0], 0.8))
    scale_high = max(scale_low, _number(scale_range[1], 1.2))
    colors = tuple(_parse_rgba(value) for value in layer.get("colors", ()))
    colors = tuple(color for color in colors if color is not None) or ((0.2, 0.2, 0.24, 1.0),)
    accent_raw = _parse_rgba(layer.get("accent")) or colors[-1]
    motifs = layer.get("motifs", ())

    phase = float(camera_x) * parallax
    first_slot = int(math.floor((phase - spacing) / spacing))
    last_slot = int(math.ceil((phase + float(viewport_w) + spacing) / spacing))
    out = []
    for slot in range(first_slot, last_slot + 1):
        if len(out) >= layer_limit:
            break
        local_x = float(slot) * spacing - phase + spacing * 0.5
        for ordinal in range(repetitions):
            if len(out) >= layer_limit:
                break
            seed = (profile_id, layer_id, slot, ordinal)
            motif = _weighted_motif(motifs, seed + ("motif",))
            primitive = str(motif.get("primitive", "cavern_mass"))
            spread = (float(ordinal) - (repetitions - 1) * 0.5) * spacing * 0.33
            spread += (_stable_unit(*(seed + ("x",))) - 0.5) * spacing * 0.22
            anchor_x = float(camera_x) + local_x + spread
            anchor_y = float(camera_y) + float(viewport_h) * baseline
            anchor_y += (_stable_unit(*(seed + ("y",))) - 0.5) * 2.0 * jitter
            scale = scale_low + (scale_high - scale_low) * _stable_unit(*(seed + ("scale",)))
            scale *= max(0.25, _number(motif.get("scale"), 1.0))
            pulse = max(0.0, min(0.35, _number(motif.get("pulse"), 0.0)))
            pulse_alpha = 1.0
            if pulse > 0.0:
                phase_offset = _stable_unit(*(seed + ("pulse",))) * math.pi * 2.0
                pulse_alpha += pulse * math.sin(float(visual_time) * 1.7 + phase_offset)
            color = colors[int(_stable_unit(*(seed + ("color",))) * len(colors)) % len(colors)]
            base = _rgba_alpha(color, alpha * pulse_alpha, max_alpha)
            accent = _rgba_alpha(accent_raw, alpha * min(1.18, pulse_alpha + 0.08), max_alpha)
            for dx, dy, width, height, rgba in _primitive_parts(primitive, scale, base, accent):
                if len(out) >= layer_limit:
                    break
                out.append(BackgroundQuad(
                    _pixel_snap(anchor_x + dx),
                    _pixel_snap(anchor_y + dy),
                    max(1.0, _pixel_snap(width)),
                    max(1.0, _pixel_snap(height)),
                    rgba,
                    layer_id,
                    primitive,
                    False,
                ))
    return tuple(out)


def build_background_plan(project_root, metadata=None, map_path="", camera_x=0.0,
                          camera_y=0.0, viewport_w=0.0, viewport_h=0.0,
                          visual_time=0.0, quad_budget=None, catalog=None,
                          include_base=True, strict=False, tile_x=None,
                          tile_y=None):
    """Build a stable, bounded three-depth background plan for one frame.

    ``visual_time`` may come from ``time.monotonic()`` but is used only for
    decorative alpha pulsing.  It never advances simulation or changes map
    state.  Returning an empty plan is the normal runtime fallback when the
    external catalog is unavailable.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    if catalog is None:
        catalog = load_background_catalog(project_root, metadata, strict=strict)
    empty_contract = {
        "draw_phase": "background", "collision": False,
        "max_catalog_quads": 128, "backdrop_reserved_quads": 128,
    }
    if not isinstance(catalog, dict):
        profile_id = resolve_background_profile(
            metadata, map_path, None, tile_x=tile_x, tile_y=tile_y,
        )
        if _needs_underground_plan(metadata, map_path, tile_y, profile_id):
            return _fallback_background_plan(
                profile_id, metadata, camera_x, camera_y,
                viewport_w, viewport_h, quad_budget,
            )
        return BackgroundPlan("", "", None, (), 0, {}, empty_contract)
    profile_id = resolve_background_profile(
        metadata, map_path, catalog, tile_x=tile_x, tile_y=tile_y,
    )
    profile = (catalog.get("profiles", {}) or {}).get(profile_id)
    if not isinstance(profile, dict):
        if strict:
            raise BackgroundCatalogError("找不到地下背景 profile：%s" % profile_id)
        if _needs_underground_plan(metadata, map_path, tile_y, profile_id):
            return _fallback_background_plan(
                profile_id, metadata, camera_x, camera_y,
                viewport_w, viewport_h, quad_budget,
            )
        return BackgroundPlan("", "", None, (), 0, {}, empty_contract)

    contract = dict(catalog.get("renderer_contract", {}) or {})
    catalog_limit = max(1, min(128, _integer(contract.get("max_catalog_quads"), 128)))
    profile_limit = max(1, min(catalog_limit, _integer(profile.get("quad_budget"), catalog_limit)))
    map_limit = _integer(metadata.get("background_quad_budget"), catalog_limit)
    requested = profile_limit if quad_budget is None else _integer(quad_budget, profile_limit)
    budget = max(1, min(catalog_limit, profile_limit, map_limit, requested))
    width = max(1.0, _number(viewport_w, 1.0))
    height = max(1.0, _number(viewport_h, 1.0))
    max_alpha = _number(
        (contract.get("foreground_separation", {}) or {}).get("max_decorative_alpha"),
        0.86,
    )
    max_alpha = max(0.1, min(0.9, max_alpha))

    base = _parse_rgba(profile.get("base_color")) or (0.05, 0.05, 0.07, 1.0)
    quads = []
    layer_counts = {}
    if include_base and budget > 0:
        quads.append(BackgroundQuad(
            _pixel_snap(float(camera_x) + width * 0.5),
            _pixel_snap(float(camera_y) + height * 0.5),
            max(1.0, _pixel_snap(width + 4.0)),
            max(1.0, _pixel_snap(height + 4.0)),
            base,
            "far",
            "base_fill",
            False,
        ))
        layer_counts["far"] = 1
    for layer in profile.get("layers", ()):
        if not isinstance(layer, dict) or len(quads) >= budget:
            continue
        rows = _build_layer_quads(
            profile_id, layer, camera_x, camera_y, width, height,
            visual_time, budget - len(quads), max_alpha,
        )
        quads.extend(rows[:max(0, budget - len(quads))])
        layer_id = str(layer.get("id", "far"))
        layer_counts[layer_id] = layer_counts.get(layer_id, 0) + len(rows)
    return BackgroundPlan(
        profile_id,
        str(profile.get("display_name", profile_id)),
        base,
        tuple(quads[:budget]),
        budget,
        layer_counts,
        contract,
    )


def renderer_quad_rows(plan):
    """Return the renderer-neutral ``(x,y,w,h,rgba)`` integration rows."""
    if not isinstance(plan, BackgroundPlan):
        return ()
    return tuple((q.x, q.y, q.width, q.height, q.rgba) for q in plan.quads)


__all__ = (
    "BackgroundCatalogError", "BackgroundPlan", "BackgroundQuad",
    "CATALOG_RELATIVE_PATH", "LAYER_ORDER", "SUPPORTED_PROFILE_IDS",
    "background_metadata", "dynamic_background_metadata",
    "build_background_plan", "clear_catalog_cache",
    "load_background_catalog", "renderer_quad_rows",
    "resolve_background_profile", "validate_background_catalog",
    "validate_map_background_metadata", "validate_underground_biome_bindings",
)
