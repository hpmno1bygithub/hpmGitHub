# -*- coding: utf-8 -*-
"""Bounded, presentation-only creature and monster attack effects.

Creature combat remains authoritative in :mod:`systems.creature_system`.  This
module accepts an event only *after* an attack/action or collision has been
confirmed, resolves a data-driven visual profile, and keeps a tiny TTL queue for
the Metal renderer.  It never reads or writes HP, damage, hit boxes, cooldowns,
physics, time scale, UIKit state, or the gameplay camera.

Renderer contract
-----------------
``snapshot()`` returns detached dictionaries with world-space coordinates,
style/palette, phase/outcome, progress, radius and a per-event ``quad_budget``.
The renderer must stop at ``max_total_quads`` and must not feed presentation
results back into gameplay.
"""
import json
import math
import os


FORMAT = 1
DEFAULT_BUDGETS = {
    "max_events": 24,
    "max_quads_per_event": 18,
    "max_total_quads": 192,
    "default_ttl": 0.34,
    "coalesce_seconds": 0.045,
    "coalesce_radius_px": 14.0,
}

_FALLBACK_PROFILES = {
    "passive": {
        "style": "none", "palette": ("#00000000",),
        "ttl": 0.10, "quad_cost": 0, "scale": 0.0, "radius": 0.0,
    },
    "claw": {
        "style": "claw_slash",
        "palette": ("#FFF2D0FF", "#FF8B5CFF", "#9E2836CC"),
        "ttl": 0.30, "quad_cost": 12, "scale": 1.0, "radius": 29.0,
    },
    "arcane": {
        "style": "arcane_runes",
        "palette": ("#FFF1FFFF", "#BE70FFFF", "#5936BADD"),
        "ttl": 0.42, "quad_cost": 16, "scale": 1.12, "radius": 37.0,
    },
    "lightning": {
        "style": "lightning_burst",
        "palette": ("#FFFFFFFF", "#FFE765FF", "#6BAEFFFF"),
        "ttl": 0.34, "quad_cost": 18, "scale": 1.12, "radius": 38.0,
    },
    "fire": {
        "style": "fire_burst",
        "palette": ("#FFF08AFF", "#FF8A19FF", "#D92B12DD"),
        "ttl": 0.44, "quad_cost": 18, "scale": 1.18, "radius": 40.0,
    },
    "explosion": {
        "style": "explosion_ring",
        "palette": ("#FFFFFFFF", "#FFB52CFF", "#EE3818FF"),
        "ttl": 0.48, "quad_cost": 18, "scale": 1.35, "radius": 58.0,
    },
}


def _bounded_float(value, default, low, high):
    try:
        value = float(value)
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    return max(float(low), min(float(high), value))


def _bounded_int(value, default, low, high):
    try:
        value = int(value)
    except Exception:
        value = int(default)
    return max(int(low), min(int(high), value))


def _hex_rgba(value):
    """Convert #RRGGBB[AA] into a Metal-friendly immutable float tuple."""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return (1.0, 1.0, 1.0, 1.0)
    try:
        return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4, 6))
    except Exception:
        return (1.0, 1.0, 1.0, 1.0)


def catalog_path(project_root=None):
    root = str(project_root or os.environ.get("PYTO_RPG_PROJECT_ROOT", "") or "")
    if root:
        return os.path.join(os.path.abspath(root), "assets", "creature_vfx_catalog.json")
    local = os.path.join(os.getcwd(), "assets", "creature_vfx_catalog.json")
    return local if os.path.isfile(local) else ""


def _normalize_catalog(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    source_budgets = raw.get("budgets", {}) if isinstance(raw.get("budgets"), dict) else {}
    budgets = {
        "max_events": _bounded_int(source_budgets.get("max_events"), 24, 4, 32),
        "max_quads_per_event": _bounded_int(source_budgets.get("max_quads_per_event"), 18, 0, 24),
        "max_total_quads": _bounded_int(source_budgets.get("max_total_quads"), 192, 24, 256),
        "default_ttl": _bounded_float(source_budgets.get("default_ttl"), 0.34, 0.05, 1.25),
        "coalesce_seconds": _bounded_float(source_budgets.get("coalesce_seconds"), 0.045, 0.0, 0.20),
        "coalesce_radius_px": _bounded_float(source_budgets.get("coalesce_radius_px"), 14.0, 0.0, 80.0),
    }

    profiles = {}
    source_profiles = raw.get("profiles", {}) if isinstance(raw.get("profiles"), dict) else {}
    merged_profiles = dict(_FALLBACK_PROFILES)
    merged_profiles.update(source_profiles)
    for profile_id, row in merged_profiles.items():
        if not isinstance(row, dict):
            continue
        palette = row.get("palette", ("#FFFFFFFF",))
        if not isinstance(palette, (list, tuple)):
            palette = ("#FFFFFFFF",)
        palette = tuple(str(color) for color in tuple(palette)[:4]) or ("#FFFFFFFF",)
        key = str(profile_id)
        profiles[key] = {
            "style": str(row.get("style", "claw_slash") or "claw_slash")[:40],
            "palette": palette,
            "ttl": _bounded_float(row.get("ttl"), budgets["default_ttl"], 0.05, 1.25),
            "quad_cost": _bounded_int(row.get("quad_cost"), 12, 0, budgets["max_quads_per_event"]),
            "scale": _bounded_float(row.get("scale"), 1.0, 0.0, 2.5),
            "radius": _bounded_float(row.get("radius"), 30.0, 0.0, 160.0),
        }

    species = {}
    for asset_id, profile_id in (raw.get("species", {}) if isinstance(raw.get("species"), dict) else {}).items():
        asset_key = str(asset_id)
        profile_key = str(profile_id)
        if profile_key in profiles:
            species[asset_key] = profile_key

    action_overrides = {}
    source_overrides = raw.get("action_overrides", {}) if isinstance(raw.get("action_overrides"), dict) else {}
    for asset_id, actions in source_overrides.items():
        if not isinstance(actions, dict):
            continue
        clean = {}
        for action_id, profile_id in actions.items():
            profile_key = str(profile_id)
            if profile_key in profiles:
                clean[str(action_id)] = profile_key
        if clean:
            action_overrides[str(asset_id)] = clean

    effect_fallbacks = {}
    source_fallbacks = raw.get("effect_fallbacks", {}) if isinstance(raw.get("effect_fallbacks"), dict) else {}
    for effect, profile_id in source_fallbacks.items():
        profile_key = str(profile_id)
        if profile_key in profiles:
            effect_fallbacks[str(effect)] = profile_key
    defaults = {
        "magic_bolt": "arcane", "lightning_bolt": "lightning",
        "fire_column": "fire", "explosion": "explosion", "none": "passive",
    }
    for effect, profile_id in defaults.items():
        if effect not in effect_fallbacks and profile_id in profiles:
            effect_fallbacks[effect] = profile_id
    return {
        "format": FORMAT, "budgets": budgets, "profiles": profiles,
        "species": species, "action_overrides": action_overrides,
        "effect_fallbacks": effect_fallbacks,
    }


def load_creature_vfx_catalog(project_root=None):
    path = catalog_path(project_root)
    raw = None
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except Exception as exc:
            print("CREATURE VFX CATALOG warning:", repr(exc))
    return _normalize_catalog(raw)


class CreatureAttackVFXSystem:
    """Small TTL/coalescing queue consumed by the world-space renderer."""

    def __init__(self, event_bus=None, project_root=None, catalog=None):
        self.event_bus = event_bus
        self.catalog = _normalize_catalog(catalog) if isinstance(catalog, dict) else load_creature_vfx_catalog(project_root)
        self.budgets = dict(self.catalog["budgets"])
        self.feedback = []
        # Aliases make discovery simple for renderer/tests without duplicating state.
        self.active_vfx = self.feedback
        self.last_feedback = None
        self.serial = 0

    @staticmethod
    def asset_id_for(creature):
        if creature is None:
            return "creature.unknown"
        asset_id = str(getattr(creature, "asset_id", "") or "")
        if asset_id:
            return asset_id
        return "creature." + str(getattr(creature, "species", "unknown") or "unknown")

    def profile_id_for(self, asset_id, action_id="", effect="", hostile=True):
        asset_id = str(asset_id or "creature.unknown")
        action_id = str(action_id or "")
        effect = str(effect or "")
        exact = self.catalog["action_overrides"].get(asset_id, {})
        if action_id in exact:
            return exact[action_id]
        # Explicit non-melee effects are safer fallbacks for editor-created
        # creatures than a species' ordinary contact style.
        if effect in ("magic_bolt", "lightning_bolt", "fire_column", "explosion", "none"):
            profile_id = self.catalog["effect_fallbacks"].get(effect)
            if profile_id:
                return profile_id
        profile_id = self.catalog["species"].get(asset_id)
        if profile_id:
            return profile_id
        profile_id = self.catalog["effect_fallbacks"].get(effect)
        if profile_id:
            return profile_id
        return "claw" if bool(hostile) else "passive"

    def profile_for(self, asset_id, action_id="", effect="", hostile=True):
        profile_id = self.profile_id_for(asset_id, action_id, effect, hostile)
        row = self.catalog["profiles"].get(profile_id)
        if row is None:
            profile_id = "claw" if bool(hostile) else "passive"
            row = self.catalog["profiles"].get(profile_id, _FALLBACK_PROFILES[profile_id])
        return profile_id, dict(row)

    @staticmethod
    def _world_center(creature):
        if creature is None:
            return 0.0, 0.0
        height = getattr(creature, "height", None)
        try:
            height = float(height()) if callable(height) else float(height or 0.0)
        except Exception:
            height = 0.0
        return float(getattr(creature, "x", 0.0)), float(getattr(creature, "y", 0.0)) - height * 0.5

    def emit_attack(
        self, creature=None, action_id="", effect="", attack_kind="attack",
        phase="impact", outcome="released", x=None, y=None,
        target_x=None, target_y=None, radius=None, scale=1.0,
        authoritative="attack", element="", source_id="", asset_id="",
        species="", facing=None,
    ):
        """Queue one visual packet after an authoritative gameplay decision.

        ``authoritative`` must be ``attack`` or ``collision``.  This explicit
        guard makes accidental speculative/per-frame calls no-ops.
        """
        authoritative = str(authoritative or "")
        if authoritative not in ("attack", "collision"):
            return None
        resolved_asset = str(asset_id or self.asset_id_for(creature))
        resolved_species = str(species or getattr(creature, "species", "") or resolved_asset.rsplit(".", 1)[-1])
        hostile = bool(getattr(creature, "hostile", True))
        profile_id, profile = self.profile_for(resolved_asset, action_id, effect, hostile)
        if profile.get("style") == "none" or int(profile.get("quad_cost", 0)) <= 0:
            return None

        center_x, center_y = self._world_center(creature)
        px = _bounded_float(center_x if x is None else x, center_x, -10000000.0, 10000000.0)
        py = _bounded_float(center_y if y is None else y, center_y, -10000000.0, 10000000.0)
        if target_x is None:
            if facing is None:
                facing = getattr(creature, "facing", 1)
            target_x = px + (1.0 if int(facing or 1) >= 0 else -1.0) * max(12.0, float(profile["radius"]))
        if target_y is None:
            target_y = py
        tx = _bounded_float(target_x, px, -10000000.0, 10000000.0)
        ty = _bounded_float(target_y, py, -10000000.0, 10000000.0)
        dx, dy = tx - px, ty - py
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            dx, dy, length = (1.0 if int(facing or 1) >= 0 else -1.0), 0.0, 1.0
        dx /= length
        dy /= length
        ttl = _bounded_float(profile.get("ttl"), self.budgets["default_ttl"], 0.05, 1.25)
        resolved_radius = _bounded_float(
            profile.get("radius") if radius is None else radius,
            profile.get("radius", 30.0), 0.0, 160.0,
        )
        resolved_scale = _bounded_float(float(profile.get("scale", 1.0)) * float(scale or 0.0), 1.0, 0.0, 2.5)
        quad_budget = _bounded_int(
            profile.get("quad_cost"), 12, 0, self.budgets["max_quads_per_event"],
        )
        actor_id = str(source_id or getattr(creature, "entity_id", "") or resolved_asset)
        merge_key = "|".join((actor_id, str(action_id or attack_kind), str(phase), profile_id, str(outcome)))

        # Dense contacts/projectile clusters share one packet.  This bounds both
        # Python allocation and Metal quad count without skipping gameplay hits.
        window = float(self.budgets["coalesce_seconds"])
        merge_radius_sq = float(self.budgets["coalesce_radius_px"]) ** 2
        if window > 0.0:
            for prior in reversed(self.feedback):
                if float(prior.get("age", 9.0)) > window:
                    break
                if str(prior.get("merge_key", "")) != merge_key:
                    continue
                ddx = float(prior.get("x", 0.0)) - px
                ddy = float(prior.get("y", 0.0)) - py
                if ddx * ddx + ddy * ddy > merge_radius_sq:
                    continue
                prior["merged_hits"] = min(8, int(prior.get("merged_hits", 1)) + 1)
                prior["ttl"] = max(float(prior.get("ttl", 0.0)), ttl)
                prior["duration"] = max(float(prior.get("duration", ttl)), ttl)
                prior["scale"] = min(2.5, max(float(prior.get("scale", 1.0)), resolved_scale) + 0.04)
                prior["target_x"], prior["target_y"] = tx, ty
                self.last_feedback = dict(prior)
                return dict(prior)

        self.serial += 1
        packet = {
            "serial": int(self.serial),
            "source_id": actor_id,
            "asset_id": resolved_asset,
            "species": resolved_species,
            "action_id": str(action_id or ""),
            "attack_kind": str(attack_kind or "attack"),
            "effect": str(effect or ""),
            "element": str(element or getattr(creature, "element_type", "neutral") or "neutral"),
            "authoritative": authoritative,
            "phase": str(phase or "impact"),
            "outcome": str(outcome or "released"),
            "profile_id": profile_id,
            "style": str(profile["style"]),
            "colors": tuple(_hex_rgba(color) for color in profile["palette"]),
            "x": px, "y": py, "target_x": tx, "target_y": ty,
            "direction_x": dx, "direction_y": dy,
            "radius": resolved_radius,
            "scale": resolved_scale,
            "quad_budget": quad_budget,
            "ttl": ttl, "duration": ttl, "age": 0.0,
            "merged_hits": 1,
            "merge_key": merge_key,
        }
        self.feedback.append(packet)
        limit = int(self.budgets["max_events"])
        if len(self.feedback) > limit:
            del self.feedback[:-limit]
        self.last_feedback = dict(packet)
        if self.event_bus is not None:
            try:
                self.event_bus.emit("creature_attack_vfx", **dict(packet))
            except Exception:
                pass
        return dict(packet)

    def update(self, dt):
        dt = _bounded_float(dt, 0.0, 0.0, 0.25)
        if not self.feedback:
            return
        keep = []
        for packet in self.feedback:
            packet["age"] = float(packet.get("age", 0.0)) + dt
            packet["ttl"] = max(0.0, float(packet.get("ttl", 0.0)) - dt)
            if packet["ttl"] > 0.0:
                keep.append(packet)
        self.feedback[:] = keep[-int(self.budgets["max_events"]):]

    def clear(self):
        self.feedback[:] = []
        self.last_feedback = None

    def snapshot(self, max_events=None, max_quads=None):
        """Return a detached renderer view obeying both event and quad caps."""
        event_limit = _bounded_int(
            self.budgets["max_events"] if max_events is None else max_events,
            self.budgets["max_events"], 0, self.budgets["max_events"],
        )
        quad_limit = _bounded_int(
            self.budgets["max_total_quads"] if max_quads is None else max_quads,
            self.budgets["max_total_quads"], 0, self.budgets["max_total_quads"],
        )
        # Select from newest to oldest so a saturated combat burst keeps the
        # impact that just happened instead of spending the whole budget on
        # effects that are already several fixed steps old.  Reverse the
        # selected rows before returning so Metal still blends them in normal
        # chronological order (oldest first, newest last).
        rows = []
        used = 0
        candidates = self.feedback[-event_limit:] if event_limit else ()
        for packet in reversed(candidates):
            cost = min(int(packet.get("quad_budget", 0)), quad_limit - used)
            if cost <= 0:
                continue
            row = dict(packet)
            row.pop("merge_key", None)
            row["quad_budget"] = cost
            duration = max(1e-6, float(row.get("duration", 0.01)))
            row["progress"] = max(0.0, min(1.0, float(row.get("age", 0.0)) / duration))
            rows.append(row)
            used += cost
            if used >= quad_limit:
                break
        rows.reverse()
        return tuple(rows)

    def coverage_report(self, archetypes=None, action_profiles=None, custom_creatures=None):
        """Audit built-ins, runtime custom types and actual authored attacks."""
        archetypes = archetypes if isinstance(archetypes, dict) else None
        custom_creatures = custom_creatures if isinstance(custom_creatures, dict) else {}
        if archetypes is None:
            requested = set(self.catalog["species"])
            hostile_ids = set()
        else:
            requested = {"creature." + str(species) for species in archetypes}
            hostile_ids = {
                "creature." + str(species) for species, row in archetypes.items()
                if isinstance(row, dict) and bool(row.get("hostile", False))
            }
        for species, row in custom_creatures.items():
            asset_id = str(row.get("asset_id", "") if isinstance(row, dict) else "")
            requested.add(asset_id or "creature." + str(species))
            if isinstance(row, dict) and bool(row.get("hostile", False)):
                hostile_ids.add(asset_id or "creature." + str(species))

        missing_types = []
        for asset_id in sorted(requested):
            profile_id = self.catalog["species"].get(asset_id)
            if profile_id not in self.catalog["profiles"]:
                # Editor-created types are deliberately covered by a bounded
                # hostile/passive fallback even before an artist authors data.
                if asset_id not in self.catalog["species"] and asset_id.startswith("creature."):
                    continue
                missing_types.append(asset_id)

        action_rows = action_profiles.get("creatures", {}) if isinstance(action_profiles, dict) else {}
        active_action_count = 0
        missing_actions = []
        legacy_overridden_assets = set()
        for asset_id, entry in action_rows.items():
            if not isinstance(entry, dict):
                continue
            actions = entry.get("actions", {}) if isinstance(entry.get("actions"), dict) else {}
            has_active_attack = False
            for action_id, action in actions.items():
                if not isinstance(action, dict) or not bool(action.get("enabled", True)):
                    continue
                effect = str(action.get("effect", "damage_knockback") or "damage_knockback")
                if effect == "none":
                    continue
                active_action_count += 1
                has_active_attack = True
                profile_id = self.profile_id_for(asset_id, action_id, effect, hostile=True)
                profile = self.catalog["profiles"].get(profile_id, {})
                if not profile or profile.get("style") == "none" or int(profile.get("quad_cost", 0)) <= 0:
                    missing_actions.append(str(asset_id) + ":" + str(action_id))
            if has_active_attack and bool(entry.get("override_legacy_contact", False)):
                legacy_overridden_assets.add(str(asset_id))

        legacy_attackers = sorted(hostile_ids - legacy_overridden_assets)
        for asset_id in legacy_attackers:
            profile_id = self.profile_id_for(asset_id, effect="damage_knockback", hostile=True)
            profile = self.catalog["profiles"].get(profile_id, {})
            if not profile or profile.get("style") == "none" or int(profile.get("quad_cost", 0)) <= 0:
                missing_actions.append(asset_id + ":legacy_contact")
        return {
            "catalog_species_count": len(self.catalog["species"]),
            "total_types": len(requested),
            "covered_types": len(requested) - len(missing_types),
            "hostile_types": len(hostile_ids),
            "authored_attack_actions": active_action_count,
            "legacy_attack_types": len(legacy_attackers),
            "total_attack_paths": active_action_count + len(legacy_attackers),
            "missing_types": tuple(missing_types),
            "missing_attack_profiles": tuple(missing_actions),
            "complete": not missing_types and not missing_actions,
            "budgets": dict(self.budgets),
        }
