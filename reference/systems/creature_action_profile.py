# -*- coding: utf-8 -*-
"""Data-driven creature actions authored outside rpg_runtime.zip.

Each animation state can be exposed to CreatureSystem as one AI-callable action.
The action profile intentionally contains only deterministic gameplay metadata;
art remains in assets/pixel_sources/*.json and frame timing/events are read from
AssetRegistry at runtime.
"""
import copy
import json
import os

FORMAT = 1

MOTION_TYPES = (
    "stationary",   # play in place
    "charge",       # horizontal rush
    "leap",         # ground ballistic jump toward target
    "dive",         # direct 2-D approach toward captured target
)

MOTION_LABELS = {
    "stationary": "原地",
    "charge": "衝刺",
    "leap": "跳躍突進",
    "dive": "俯衝／直線突進",
}

EFFECT_TYPES = (
    "damage_knockback",
    "damage",
    "knockback",
    "magic_bolt",
    "lightning_bolt",
    "fire_column",
    "explosion",
    "none",
    "kong_combo", "triple_breath", "python_tail", "python_tornado",
    "poison_puff", "vine_whip", "seed_volley", "ceiling_bite", "wind_volley",
)

EFFECT_LABELS = {
    "python_tail": "蟒蛇長距離甩尾", "python_tornado": "蟒蛇移動旋風柱",
    "kong_combo": "雙拳／連續拳擊", "triple_breath": "三頭元素廣域吐息",
    "damage_knockback": "傷害＋擊退",
    "damage": "傷害",
    "knockback": "擊退",
    "magic_bolt": "魔法彈",
    "lightning_bolt": "閃電彈",
    "fire_column": "火焰柱",
    "explosion": "爆炸",
    "none": "無直接效果",
    "wind_volley": "風刃扇射", "poison_puff": "孢子毒氣", "vine_whip": "藤鞭",
    "seed_volley": "種子連射", "ceiling_bite": "倒吊咬擊",
}

DEFAULT_ACTION = {
    "enabled": True,
    "animation_state": "",
    "motion": "stationary",
    "effect": "damage_knockback",
    "range_min": 0.0,
    "range_max": 72.0,
    "vertical_range": 72.0,
    "cooldown": 1.25,
    # 0 = derive from authored animation frame_count/fps; fallback 0.55s.
    "duration": 0.0,
    "speed": 220.0,
    "lift_speed": 280.0,
    "damage": 8.0,
    "knockback": 180.0,
    # Radius around the actor/body used at the gameplay event frame.
    "hit_radius": 34.0,
    "requires_ground": False,
    # Higher values are preferred when several actions are eligible.
    "priority": 10,
    # Deterministic probability check after priority filtering.
    "chance": 1.0,
}

DEFAULT_CREATURE_ENTRY = {
    # When False the old contact-only bite remains available as a fallback.
    "override_legacy_contact": False,
    "actions": {},
}

_NUM_LIMITS = {
    "range_min": (0.0, 800.0),
    "range_max": (1.0, 1200.0),
    "vertical_range": (1.0, 800.0),
    "cooldown": (0.05, 20.0),
    "duration": (0.0, 5.0),
    "speed": (0.0, 1200.0),
    "lift_speed": (0.0, 1200.0),
    "damage": (0.0, 999.0),
    "knockback": (0.0, 1200.0),
    "hit_radius": (1.0, 300.0),
    "priority": (0.0, 100.0),
    "chance": (0.0, 1.0),
}


def settings_path(project_root=None):
    root = project_root or os.environ.get("PYTO_RPG_PROJECT_ROOT", "")
    if not root:
        return ""
    return os.path.join(root, "assets", "creature_actions.json")


def default_action(animation_state=""):
    out = copy.deepcopy(DEFAULT_ACTION)
    out["animation_state"] = str(animation_state or "")
    return out


def normalize_action(raw, state_name=""):
    out = default_action(state_name)
    if isinstance(raw, dict):
        for key in out:
            if key in raw:
                out[key] = raw[key]
    out["animation_state"] = str(out.get("animation_state") or state_name or "")
    out["enabled"] = bool(out.get("enabled", True))
    out["requires_ground"] = bool(out.get("requires_ground", False))
    motion = str(out.get("motion", "stationary"))
    out["motion"] = motion if motion in MOTION_TYPES else "stationary"
    effect = str(out.get("effect", "damage_knockback"))
    out["effect"] = effect if effect in EFFECT_TYPES else "damage_knockback"
    for key, (lo, hi) in _NUM_LIMITS.items():
        try:
            value = float(out.get(key, DEFAULT_ACTION[key]))
        except Exception:
            value = float(DEFAULT_ACTION[key])
        out[key] = max(lo, min(hi, value))
    if out["range_max"] < out["range_min"]:
        out["range_max"] = out["range_min"]
    out["priority"] = int(round(out["priority"]))
    # FIX31: preserve optional projectile / fire-column presentation fields.
    # They are intentionally not required by the action editor, but authored
    # boss attacks may use them to make a projectile or pillar visibly larger.
    optional_limits = {
        "radius": (2.0, 60.0),
        "life": (0.10, 8.0),
        "column_distance": (18.0, 220.0),
        "column_height": (28.0, 240.0),
        "column_width": (10.0, 120.0),
    }
    if isinstance(raw, dict):
        for key, (lo, hi) in optional_limits.items():
            if key not in raw:
                continue
            try:
                out[key] = max(lo, min(hi, float(raw[key])))
            except Exception:
                pass
        if "element" in raw:
            out["element"] = str(raw.get("element") or "arcane")
    return out


def normalize_data(raw=None):
    out = {"format": FORMAT, "creatures": {}}
    creatures = raw.get("creatures", {}) if isinstance(raw, dict) else {}
    if not isinstance(creatures, dict):
        creatures = {}
    for asset_id, entry in creatures.items():
        if not isinstance(entry, dict):
            continue
        clean = {
            "override_legacy_contact": bool(entry.get("override_legacy_contact", False)),
            "actions": {},
        }
        actions = entry.get("actions", {})
        if isinstance(actions, dict):
            for action_id, action in actions.items():
                key = str(action_id).strip()
                if not key:
                    continue
                clean["actions"][key] = normalize_action(action, key)
        out["creatures"][str(asset_id)] = clean
    return out


def load_creature_actions(project_root=None):
    path = settings_path(project_root)
    if not path or not os.path.isfile(path):
        return normalize_data()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return normalize_data(json.load(f))
    except Exception as exc:
        print("CREATURE ACTION SETTINGS LOAD warning:", repr(exc))
        return normalize_data()


def save_creature_actions(data, project_root=None):
    path = settings_path(project_root)
    if not path:
        raise RuntimeError("找不到 PYTO_RPG_PROJECT_ROOT")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = normalize_data(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path, clean
