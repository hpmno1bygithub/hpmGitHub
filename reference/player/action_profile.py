# -*- coding: utf-8 -*-
"""Project-local player action tuning loaded from assets/action_settings.json.

The file is intentionally outside rpg_runtime.zip.  This lets the iPhone action
editor change movement/action behaviour without editing Python or rebuilding the
runtime.  A new GameApp/Player instance reads the settings on launch.
"""
import json
import os


DEFAULT_ACTION_SETTINGS = {
    "format": 2,
    "direct_jump_from_compact": True,
    "restore_posture_after_jump": True,
    "up_restores_stand": True,
    "walk_speed": 145.0,
    "run_speed": 235.0,
    "crouch_speed_scale": 0.55,
    "prone_speed_scale": 0.35,
    "jump_speed": 455.0,
    "jump_height_px": 48.0,
    "roll_speed": 355.0,
    "roll_time": 0.38,
}

_LIMITS = {
    "walk_speed": (40.0, 420.0),
    "run_speed": (60.0, 620.0),
    "crouch_speed_scale": (0.10, 1.00),
    "prone_speed_scale": (0.05, 1.00),
    "jump_speed": (180.0, 760.0),
    "jump_height_px": (24.0, 120.0),
    "roll_speed": (120.0, 760.0),
    "roll_time": (0.10, 1.20),
}


def settings_path(project_root=None):
    root = project_root or os.environ.get("PYTO_RPG_PROJECT_ROOT", "")
    if not root:
        return ""
    return os.path.join(root, "assets", "action_settings.json")


def normalize_action_settings(raw=None):
    out = dict(DEFAULT_ACTION_SETTINGS)
    if isinstance(raw, dict):
        for key in out:
            if key in raw:
                out[key] = raw[key]

    for key in ("direct_jump_from_compact", "restore_posture_after_jump", "up_restores_stand"):
        out[key] = bool(out.get(key, DEFAULT_ACTION_SETTINGS[key]))

    for key, (lo, hi) in _LIMITS.items():
        try:
            value = float(out.get(key, DEFAULT_ACTION_SETTINGS[key]))
        except Exception:
            value = float(DEFAULT_ACTION_SETTINGS[key])
        out[key] = max(lo, min(hi, value))

    out["format"] = 2
    return out


def load_action_settings(project_root=None):
    path = settings_path(project_root)
    if not path or not os.path.isfile(path):
        return normalize_action_settings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return normalize_action_settings(json.load(f))
    except Exception as exc:
        print("ACTION SETTINGS LOAD warning:", repr(exc))
        return normalize_action_settings()


def save_action_settings(data, project_root=None):
    path = settings_path(project_root)
    if not path:
        raise RuntimeError("找不到 PYTO_RPG_PROJECT_ROOT")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = normalize_action_settings(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path, clean
