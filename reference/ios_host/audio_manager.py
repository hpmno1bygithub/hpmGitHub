# -*- coding: utf-8 -*-
"""Low-overhead Pyto/iOS audio bridge for gameplay SFX, creature calls and BGM.

FIX38 design rules:
- Gameplay systems emit semantic audio events; they never import Pyto's `sound`.
- Short sounds are bounded by a small retained AudioPlayer pool.
- Creature calls are proximity-gated and heavily rate-limited.
- Exactly one background-music player is alive at a time.
- Project-local WAV files live outside rpg_runtime.zip under assets/audio/.
"""
from collections import deque
import json
import math
import os
import random

from config import TILE_SIZE


class AudioManager:
    def __init__(self, game, project_root=None):
        self.game = game
        self.project_root = str(
            project_root
            or os.environ.get("PYTO_RPG_PROJECT_ROOT", "")
            or os.getcwd()
        )
        self.audio_root = os.path.join(self.project_root, "assets", "audio")
        self.manifest_path = os.path.join(self.audio_root, "audio_manifest.json")
        self.manifest = self._load_manifest()
        self.enabled = bool(self.manifest.get("enabled", True))
        self.master_volume = self._volume("master_volume", 0.86)
        self.sfx_volume = self._volume("sfx_volume", 0.76)
        self.creature_volume = self._volume("creature_volume", 0.38)
        self.music_volume = self._volume("music_volume", 0.24)
        self.max_sfx = max(1, min(10, int(self.manifest.get("max_simultaneous_sfx", 6) or 6)))
        self.hear_radius = max(80.0, float(self.manifest.get("creature_hear_radius_px", 520.0) or 520.0))
        interval = self.manifest.get("creature_global_interval", [2.8, 5.5])
        try:
            self.creature_interval_min = max(1.0, float(interval[0]))
            self.creature_interval_max = max(self.creature_interval_min, float(interval[1]))
        except Exception:
            self.creature_interval_min, self.creature_interval_max = 2.8, 5.5

        self._sound = None
        self._backend_error = ""
        try:
            import sound as pyto_sound
            self._sound = pyto_sound
        except Exception as exc:
            self.enabled = False
            self._backend_error = repr(exc)
            print("AUDIO disabled: Pyto sound module unavailable:", repr(exc))

        self._pending = deque(maxlen=24)
        self._active_sfx = []
        self._sfx_clock = 0.0
        self._last_sfx_time = {}
        self._event_bus = None

        self._rng = random.Random(38077)
        self._ambient_clock = 0.0
        self._next_creature_call = 2.0
        self._creature_last = {}

        self._bgm_player = None
        self._bgm_key = ""
        self._bgm_elapsed = 0.0
        self._bgm_duration = 0.0
        self._music_check_clock = 0.0

        p = getattr(self.game, "player", None)
        self._motion_serial = {
            "jump": int(getattr(p, "jump_audio_serial", 0) or 0),
            "roll": int(getattr(p, "roll_audio_serial", 0) or 0),
            "swim": int(getattr(p, "swim_audio_serial", 0) or 0),
            "land": int(getattr(p, "landing_audio_serial", 0) or 0),
        }
        self._swim_stroke_clock = 0.0
        self._last_submerged = bool(getattr(p, "fully_submerged", False)) if p is not None else False

        self._ensure_event_bindings()
        try:
            self.game.audio_debug = self.debug_snapshot()
        except Exception:
            pass
        if self.enabled:
            print("AUDIO FIX38 ready:", self.audio_root)

    def _load_manifest(self):
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            print("AUDIO manifest warning:", repr(exc), self.manifest_path)
            return {}

    def _volume(self, key, default):
        try:
            return max(0.0, min(1.0, float(self.manifest.get(key, default))))
        except Exception:
            return float(default)

    def _ensure_event_bindings(self):
        bus = getattr(self.game, "events", None)
        if bus is None or bus is self._event_bus:
            return
        self._event_bus = bus
        for name, callback in (
            ("audio_magic_cast", self._on_magic_cast),
            ("audio_tool_hit", self._on_tool_hit),
            ("audio_melee_swing", self._on_melee_swing),
            ("audio_melee_hit", self._on_melee_hit),
            ("audio_lava_spit", self._on_lava_spit),
            ("audio_chest_open", self._on_chest_open),
            ("audio_weapon_equip", self._on_weapon_equip),
            ("audio_shield_block", self._on_shield_block),
        ):
            try:
                bus.subscribe(name, callback)
            except Exception as exc:
                print("AUDIO event bind warning:", name, repr(exc))

    def _enqueue(self, key, volume_scale=1.0, cooldown=0.04, category="sfx"):
        if not self.enabled or not key:
            return
        self._pending.append((str(key), float(volume_scale), float(cooldown), str(category)))

    def _on_magic_cast(self, payload):
        magic = str(payload.get("magic", "fireball"))
        key = {
            "fireball": "magic_fire",
            "waterball": "magic_water",
            "iceball": "magic_ice",
            "electricball": "magic_electric",
        }.get(magic, "magic_fire")
        level = max(1, min(3, int(payload.get("level", 1) or 1)))
        self._enqueue(key, 0.82 + 0.09 * level, 0.07, "sfx")

    def _on_tool_hit(self, payload):
        material = str(payload.get("material", "rock"))
        key = {
            "soil": "dig_soil",
            "wood": "chop_wood",
            "rock": "dig_rock",
        }.get(material, "dig_rock")
        self._enqueue(key, 0.92, 0.08, "sfx")

    def _on_melee_swing(self, payload):
        weapon = str(payload.get("weapon", "sword") or "sword")
        key = str(payload.get("sound_key", "") or "")
        if not key:
            key = {
                "dagger": "dagger_swing",
                "spear": "spear_swing",
                "battle_axe": "axe_swing",
                "war_hammer": "hammer_swing",
                "greatsword": "greatsword_swing",
            }.get(weapon, "sword_swing")
        volume = 0.84 if weapon == "dagger" else (1.00 if weapon in ("war_hammer", "greatsword") else 0.92)
        # FIX67 consumes the combat system's bounded presentation gain.  It is
        # audio-only metadata: changing it never changes collision or damage.
        try:
            gain = max(0.75, min(1.25, float(payload.get("sound_gain", 1.0) or 1.0)))
        except Exception:
            gain = 1.0
        volume = min(1.0, volume * gain)
        self._enqueue(key, volume, 0.06, "sfx")

    def _on_melee_hit(self, payload):
        weapon = str(payload.get("weapon", "sword") or "sword")
        key = str(payload.get("sound_key", "") or "")
        if not key:
            key = {
                "dagger": "dagger_hit",
                "spear": "spear_hit",
                "battle_axe": "axe_hit",
                "war_hammer": "hammer_hit",
                "greatsword": "greatsword_hit",
            }.get(weapon, "sword_hit")
        volume = 0.88 if weapon == "dagger" else (1.00 if weapon in ("war_hammer", "greatsword") else 0.95)
        try:
            gain = max(0.75, min(1.25, float(payload.get("sound_gain", 1.0) or 1.0)))
        except Exception:
            gain = 1.0
        volume = min(1.0, volume * gain)
        self._enqueue(key, volume, 0.05, "sfx")

    def _on_lava_spit(self, payload):
        self._enqueue("lava_spit", 0.72, 0.18, "sfx")

    def _on_chest_open(self, payload):
        self._enqueue("chest_open", 0.95, 0.15, "sfx")

    def _on_weapon_equip(self, payload):
        self._enqueue("weapon_equip", 0.82, 0.12, "sfx")

    def _on_shield_block(self, payload):
        # Reuse the existing electric crack for a crisp barrier impact; this
        # adds no audio asset or decoder work to the iPhone package.
        strength=max(0.65,min(1.0,float(payload.get("damage",18.0) or 18.0)/36.0))
        self._enqueue("magic_electric", 0.78+0.20*strength, 0.045, "sfx")

    def _asset_path(self, table, key):
        row = self.manifest.get(table, {})
        rel = row.get(key, "") if isinstance(row, dict) else ""
        if isinstance(rel, dict):
            rel = rel.get("file", "")
        if not rel:
            return ""
        path = os.path.join(self.audio_root, str(rel))
        return path if os.path.isfile(path) else ""

    @staticmethod
    def _is_playing(player):
        if player is None:
            return False
        try:
            return bool(player.playing)
        except Exception:
            return False

    def _start_player(self, path, volume):
        if not self.enabled or self._sound is None or not path:
            return None
        try:
            player = self._sound.AudioPlayer(path)
            try:
                player.volume = max(0.0, min(1.0, float(volume)))
            except Exception:
                pass
            player.play()
            return player
        except Exception as exc:
            # The game must never fail because a user replaced one WAV with a
            # malformed/unsupported file.
            print("AUDIO play warning:", os.path.basename(path), repr(exc))
            return None

    def _cleanup_sfx(self):
        if not self._active_sfx:
            return
        keep = []
        for player in self._active_sfx:
            if self._is_playing(player):
                keep.append(player)
            else:
                try:
                    player.stop()
                except Exception:
                    pass
        self._active_sfx = keep[-self.max_sfx:]

    def _play_sfx(self, key, volume_scale=1.0, cooldown=0.04, category="sfx"):
        now = float(self._sfx_clock)
        last = float(self._last_sfx_time.get(key, -999.0))
        if now - last < max(0.0, float(cooldown)):
            return False
        self._cleanup_sfx()
        if len(self._active_sfx) >= self.max_sfx:
            # Protect the Pyto audio graph instead of spawning an unbounded
            # AVAudioPlayer count during rapid digging / chain reactions.
            return False
        path = self._asset_path("creature_calls" if category == "creature" else "sfx", key)
        if not path:
            return False
        base = self.creature_volume if category == "creature" else self.sfx_volume
        volume = self.master_volume * base * max(0.0, min(1.5, float(volume_scale)))
        player = self._start_player(path, volume)
        if player is None:
            return False
        self._active_sfx.append(player)
        self._last_sfx_time[key] = now
        return True

    def _current_music_key(self):
        current_path = str(getattr(self.game, "current_map_path", "") or "")
        map_key = os.path.splitext(os.path.basename(current_path))[0].lower()
        by_map = self.manifest.get("music_by_map", {})
        if isinstance(by_map, dict) and map_key in by_map:
            return str(by_map.get(map_key) or "")
        try:
            biome = str(self.game.biomes.biome_at_world_x(self.game.player.x))
        except Exception:
            biome = "plains"
        by_biome = self.manifest.get("music_by_biome", {})
        if isinstance(by_biome, dict):
            return str(by_biome.get(biome, "field") or "field")
        return "field"

    def _music_row(self, key):
        rows = self.manifest.get("music", {})
        row = rows.get(key, {}) if isinstance(rows, dict) else {}
        return row if isinstance(row, dict) else {}

    def _stop_bgm(self):
        player = self._bgm_player
        self._bgm_player = None
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
        self._bgm_elapsed = 0.0
        self._bgm_duration = 0.0

    def _start_bgm(self, key):
        key = str(key or "")
        if not key:
            self._stop_bgm()
            self._bgm_key = ""
            return False
        row = self._music_row(key)
        rel = str(row.get("file", "") or "")
        path = os.path.join(self.audio_root, rel) if rel else ""
        if not path or not os.path.isfile(path):
            return False
        self._stop_bgm()
        player = self._start_player(path, self.master_volume * self.music_volume)
        if player is None:
            return False
        self._bgm_player = player
        self._bgm_key = key
        try:
            self._bgm_duration = max(1.0, float(row.get("duration", 24.0)))
        except Exception:
            self._bgm_duration = 24.0
        self._bgm_elapsed = 0.0
        return True

    def _restart_bgm(self):
        player = self._bgm_player
        if player is None:
            return self._start_bgm(self._bgm_key or self._current_music_key())
        try:
            player.current_time = 0.0
            try:
                player.volume = self.master_volume * self.music_volume
            except Exception:
                pass
            player.play()
            self._bgm_elapsed = 0.0
            return True
        except Exception:
            key = self._bgm_key
            self._stop_bgm()
            return self._start_bgm(key)

    def _update_music(self, dt):
        self._music_check_clock += float(dt)
        self._bgm_elapsed += float(dt)
        if self._music_check_clock >= 0.50:
            self._music_check_clock = 0.0
            key = self._current_music_key()
            if key != self._bgm_key:
                self._start_bgm(key)
                return
        if self._bgm_player is None:
            self._start_bgm(self._current_music_key())
            return
        # Explicit duration makes looping independent from undocumented player
        # duration properties. `playing` is a secondary early-end recovery.
        ended_by_time = self._bgm_duration > 0.0 and self._bgm_elapsed >= self._bgm_duration - 0.06
        ended_early = self._bgm_elapsed > 0.65 and not self._is_playing(self._bgm_player)
        if ended_by_time or ended_early:
            self._restart_bgm()

    def _creature_group(self, species):
        mapping = self.manifest.get("creature_group_map", {})
        if isinstance(mapping, dict):
            return str(mapping.get(str(species), "") or "")
        return ""

    def _update_creature_ambience(self, dt):
        self._ambient_clock += float(dt)
        self._next_creature_call -= float(dt)
        if self._next_creature_call > 0.0:
            return
        self._next_creature_call = self._rng.uniform(self.creature_interval_min, self.creature_interval_max)
        p = getattr(self.game, "player", None)
        if p is None:
            return
        px, py = float(p.x), float(p.y)
        candidates = []
        radius2 = self.hear_radius * self.hear_radius
        for c in tuple(getattr(self.game, "creatures", ()) or ()):
            try:
                if not bool(c.active) or float(c.hp) <= 0.0:
                    continue
                group = self._creature_group(c.species)
                if not group:
                    continue
                dx = float(c.x) - px
                dy = float(c.y) - py
                d2 = dx * dx + dy * dy
                if d2 > radius2:
                    continue
                entity_key = str(getattr(c, "entity_id", "") or (str(c.species) + ":" + str(id(c))))
                last = float(self._creature_last.get(entity_key, -999.0))
                # Individual animals cannot call repeatedly even if selected by
                # the random nearby-candidate sampler.
                if self._ambient_clock - last < 7.0:
                    continue
                candidates.append((d2, entity_key, group))
            except Exception:
                continue
        if not candidates:
            return
        # Favor nearby animals without making the choice deterministic-nearest.
        candidates.sort(key=lambda row: row[0])
        pool = candidates[: min(6, len(candidates))]
        d2, entity_key, group = self._rng.choice(pool)
        dist = math.sqrt(max(0.0, d2))
        scale = max(0.18, 1.0 - dist / max(1.0, self.hear_radius))
        if self._play_sfx(group, scale, 0.30, "creature"):
            self._creature_last[entity_key] = float(self._ambient_clock)

    def _surface_audio_group(self):
        p = getattr(self.game, "player", None)
        if p is None:
            return "soil"
        try:
            ty = int((float(p.y) + 1.0) // float(TILE_SIZE))
            tx = int(float(p.x) // float(TILE_SIZE))
            from world.tile_registry import tile_def
            name = str(tile_def(self.game.world.get_tile(tx, ty)).name)
        except Exception:
            name = "dirt"
        if name in ("sand", "sea_sand"):
            return "sand"
        if name in ("snow_dirt", "ice"):
            return "snow"
        if name in ("wood", "village_path"):
            return "wood"
        if name in ("stone", "limestone", "marble", "copper_ore", "iron_ore", "gold_ore", "igneous_rock"):
            return "rock"
        if name in ("swamp_soil", "mud", "jungle_soil"):
            return "mud"
        return "soil"

    def _swim_audio_group(self):
        try:
            biome = str(self.game.biomes.biome_at_world_x(self.game.player.x))
        except Exception:
            biome = "lake"
        if biome == "ocean":
            return "swim_ocean"
        if biome == "swamp":
            return "swim_swamp"
        return "swim_fresh"

    def _update_player_motion_sfx(self, dt):
        p = getattr(self.game, "player", None)
        if p is None:
            return
        submerged = bool(getattr(p, "fully_submerged", False))
        if submerged != bool(self._last_submerged):
            self._enqueue("water_enter" if submerged else "water_exit", 0.72, 0.18, "sfx")
            self._last_submerged = submerged

        current = {
            "jump": int(getattr(p, "jump_audio_serial", 0) or 0),
            "roll": int(getattr(p, "roll_audio_serial", 0) or 0),
            "swim": int(getattr(p, "swim_audio_serial", 0) or 0),
            "land": int(getattr(p, "landing_audio_serial", 0) or 0),
        }
        jump_changed = current["jump"] != int(self._motion_serial.get("jump", current["jump"]))
        roll_changed = current["roll"] != int(self._motion_serial.get("roll", current["roll"]))
        land_changed = current["land"] != int(self._motion_serial.get("land", current["land"]))
        surface = self._surface_audio_group() if (jump_changed or roll_changed or land_changed) else None
        if jump_changed:
            self._enqueue("jump_" + surface, 0.72, 0.10, "sfx")
        if roll_changed:
            self._enqueue("roll_" + surface, 0.82, 0.12, "sfx")
        if land_changed:
            impact = max(45.0, float(getattr(p, "last_landing_speed", 100.0)))
            scale = min(1.15, 0.52 + impact / 720.0)
            self._enqueue("land_" + surface, scale, 0.10, "sfx")
        if current["swim"] != int(self._motion_serial.get("swim", current["swim"])):
            self._enqueue(self._swim_audio_group(), 0.82, 0.10, "sfx")
            self._swim_stroke_clock = 0.0
        self._motion_serial = current

        # Continuous swimming makes a quiet stroke only while actually moving.
        # This is clock-limited and does not allocate gameplay particles.
        if submerged and abs(float(getattr(p, "vx", 0.0))) > 24.0:
            self._swim_stroke_clock += max(0.0, float(dt))
            if self._swim_stroke_clock >= 0.72:
                self._swim_stroke_clock = 0.0
                self._enqueue(self._swim_audio_group(), 0.42, 0.30, "sfx")
        else:
            self._swim_stroke_clock = min(0.72, self._swim_stroke_clock + max(0.0, float(dt))*0.25)

    def update(self, dt):
        if not self.enabled:
            return
        dt = max(0.0, min(0.20, float(dt)))
        self._sfx_clock += dt
        self._ensure_event_bindings()
        self._update_player_motion_sfx(dt)

        # Consume only a few events per render iteration. Digging and combat can
        # emit multiple events in one physics catch-up frame; the bounded queue
        # intentionally sheds excess audio while gameplay remains authoritative.
        for _ in range(min(3, len(self._pending))):
            key, scale, cooldown, category = self._pending.popleft()
            self._play_sfx(key, scale, cooldown, category)

        self._update_creature_ambience(dt)
        self._update_music(dt)

        # Cleanup is cheap (<=6 retained players) and prevents finished player
        # objects from accumulating over long sessions.
        self._cleanup_sfx()
        try:
            self.game.audio_debug = self.debug_snapshot()
        except Exception:
            pass

    def debug_snapshot(self):
        return {
            "enabled": bool(self.enabled),
            "backend": "Pyto sound.AudioPlayer" if self._sound is not None else "unavailable",
            "music": str(self._bgm_key),
            "active_sfx": int(len(self._active_sfx)),
            "queued_sfx": int(len(self._pending)),
            "backend_error": str(self._backend_error),
        }

    def close(self):
        self._pending.clear()
        self._stop_bgm()
        for player in tuple(self._active_sfx):
            try:
                player.stop()
            except Exception:
                pass
        self._active_sfx = []
