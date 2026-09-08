# -*- coding: utf-8 -*-
"""FIX97: settings presets; order of counts is multiplication, arithmetic, Zhuyin."""
PROFILES = {
    0: {"label": "預設", "required": (2, 2, 2), "interval": 180.0},
    1: {"label": "1 階", "required": (5, 5, 5), "interval": 900.0},
    2: {"label": "2 階", "required": (10, 10, 10), "interval": 1800.0},
}


def validate_profile(value):
    # bool is an int subclass; don't accidentally turn a toggle into a preset.
    if type(value) is not int or value not in PROFILES:
        raise ValueError("學習強度必須為預設、1 階或 2 階")
    return value


def profile_summary(value):
    p = PROFILES[validate_profile(value)]
    return "%s｜每類 %d 題・%d 分鐘" % (p["label"], p["required"][0], p["interval"] / 60)
