# -*- coding: utf-8 -*-
"""Small persistent settings shared by the title menu and every world.

Settings intentionally live beside the save directory rather than inside one
save slot. A user may therefore change death handling before loading a save,
and starter-item policy remains available when a clean New Game is built.
FIX87: death_handling=True permits death + the learning/death-menu sequence;
False protects the last 1 HP (not just hiding the death menu). Format and saved
boolean keys remain compatible; save slots cannot overwrite this preference.
"""
import json
import os
from systems.study_profiles import validate_profile


class GameSettings:
    FORMAT = 2
    DEFAULTS = {
        "death_handling": True,
        "starter_items": True,
        "study_intensity": 0,
    }

    def __init__(self, persistent_root, values=None):
        self.persistent_root = os.path.abspath(os.path.expanduser(str(persistent_root)))
        self.path = os.path.join(self.persistent_root, "settings.json")
        self.values = dict(self.DEFAULTS)
        if isinstance(values, dict):
            for key in self.DEFAULTS:
                if key in values:
                    self.values[key] = self._normalize(key, values[key])

    @classmethod
    def from_project_root(cls, project_root=""):
        persistent_root = os.environ.get("PYTO_RPG_SAVE_ROOT", "").strip()
        if not persistent_root:
            persistent_root = os.path.join(os.path.expanduser("~/Documents"), "PytoRPG")
        obj = cls(persistent_root)
        obj.load()
        return obj

    @staticmethod
    def _normalize(key, value):
        if key == "study_intensity":
            try:
                return validate_profile(value)
            except ValueError:
                return 0
        return bool(value)

    @property
    def study_intensity(self):
        return self._normalize("study_intensity", self.values.get("study_intensity", 0))

    @property
    def death_handling_enabled(self):
        return bool(self.values.get("death_handling", True))

    @property
    def starter_items_enabled(self):
        return bool(self.values.get("starter_items", True))

    def snapshot(self):
        return {
            "format": self.FORMAT,
            "death_handling": self.death_handling_enabled,
            "starter_items": self.starter_items_enabled,
            "study_intensity": self.study_intensity,
        }

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                for key in self.DEFAULTS:
                    if key in payload:
                        self.values[key] = self._normalize(key, payload[key])
        except (OSError, ValueError, TypeError):
            pass
        return self.snapshot()

    def save(self):
        os.makedirs(self.persistent_root, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        os.replace(tmp, self.path)
        return self.path

    def set(self, key, enabled, persist=True):
        if key not in self.DEFAULTS:
            raise KeyError("unknown setting: " + str(key))
        value = validate_profile(enabled) if key == "study_intensity" else bool(enabled)
        previous = self.values[str(key)]
        self.values[str(key)] = value
        if persist:
            try:
                self.save()
            except Exception:
                self.values[str(key)] = previous
                raise
        return value

    def toggle(self, key, persist=True):
        if key == "study_intensity":
            return self.set(key, (self.study_intensity + 1) % 3, persist=persist)
        return self.set(key, not bool(self.values.get(key, self.DEFAULTS[key])), persist=persist)
