# -*- coding: utf-8 -*-
import json
import os
import re
import time
import shutil

from config import (
    VERSION,
    SAVE_SLOT_NAME,
    CHUNK_SIZE,
    ATMOSPHERIC_WATER_INITIAL_PER_CHUNK,
    ORGANIC_MATTER_INITIAL_PER_CHUNK,
    TILE_SIZE,
)
from player.states import STAND
from systems.item_drop_system import (
    MAX_DYNAMIC_WORLD_DROPS,
    MAX_DROP_STACK_COUNT,
    DROP_WORLD_MARGIN_PX,
    MAX_DROP_ABS_POSITION_PX,
    MAX_DROP_ABS_SAVE_SPEED,
    clean_drop_heavy_uses,
)
from systems.weapon_catalog import WEAPON_DEFS, weapon_from_item


class SaveManager:
    """Simple JSON save format.

    V0.3 saves:
      - player position/resources/run mode
      - runtime tile edits only
      - item active/picked state

    It intentionally does not serialize every initial tile because the
    base world can be regenerated from build_test_world().
    """

    # FIX69 persists the bounded per-map snapshots used by portal travel.  The
    # current map remains authoritative in the ordinary world/entities fields;
    # ``map_sessions`` contains only other visited authored maps.
    FORMAT_VERSION = 19
    MAP_SESSION_FORMAT_VERSION = 1
    MAX_MAP_SESSIONS = 24
    MAX_MAP_SESSION_BYTES = 2 * 1024 * 1024
    MAX_MAP_SESSIONS_BYTES = 8 * 1024 * 1024
    # Compatibility alias used by FIX69 validators. The authoritative number
    # lives with the runtime budget so Scene, top-level save and map sessions
    # cannot drift apart.
    MAX_SESSION_WORLD_ITEMS = MAX_DYNAMIC_WORLD_DROPS
    MAX_SESSION_CREATURES = 512
    _MAP_SESSION_STATE_TYPES = {
        "world_base": (dict,),
        "world_changes": (list, tuple),
        "environment": (dict, type(None)),
        "energy": (dict, type(None)),
        "topsoil_wet_clock": (list, tuple),
        "world_items": (list, tuple),
        "drop_serial": (int, float),
        "creatures": (list, tuple),
        "creature_respawn": (dict,),
    }

    def __init__(self):
        # V0.7.1.1: saves must survive restarting Pyto and replacing the
        # downloaded project folder.  The old implementation wrote beside
        # main.py, which is not a reliable persistent location when a project
        # is opened from an external / temporary folder.  Pyto's own Documents
        # container is stable across app launches.
        project_root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "").strip()
        if not project_root:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.project_root = os.path.abspath(project_root)

        persistent_root = os.environ.get("PYTO_RPG_SAVE_ROOT", "").strip()
        if not persistent_root:
            persistent_root = os.path.join(
                os.path.expanduser("~/Documents"),
                "PytoRPG",
            )
        self.persistent_root = os.path.abspath(os.path.expanduser(persistent_root))
        self.save_dir = os.path.join(self.persistent_root, "saves")
        self.save_path = os.path.join(self.save_dir, SAVE_SLOT_NAME)

        # Old locations are migration sources only.  Do not keep writing there.
        self.legacy_save_paths = []
        for candidate in (
            os.path.join(self.project_root, "saves", SAVE_SLOT_NAME),
            os.path.expanduser("~/Documents/PytoRPG_V0_3_saves/" + SAVE_SLOT_NAME),
        ):
            candidate = os.path.abspath(os.path.expanduser(candidate))
            if candidate != self.save_path and candidate not in self.legacy_save_paths:
                self.legacy_save_paths.append(candidate)

        self._ensure_dir()
        self._migrate_legacy_save_if_needed()


    @staticmethod
    def _world_base_signature(game):
        """Small identity for the deterministic base world behind save deltas.

        Runtime tile/environment state is meaningful only when this identity
        matches.  V0.7.3 changed the authored base map from the old generator
        to the biome generator while keeping the same dimensions, so blindly
        replaying an old save produced floating plants, holes and unsafe player
        positions.
        """
        out = {
            "kind": "custom_map" if bool(getattr(game, "custom_map_loaded", False)) else "test_world",
            "width": int(getattr(game.world, "width_tiles", 0)),
            "height": int(getattr(game.world, "height_tiles", 0)),
        }
        loader = getattr(game, "map_loader", None)
        if loader is not None:
            try:
                meta = dict((loader.payload or {}).get("metadata", {}) or {})
            except Exception:
                meta = {}
            for key in ("generator", "seed", "biome_version", "map_id"):
                if key in meta:
                    out[key] = meta.get(key)
        return out

    @staticmethod
    def _world_base_compatible(saved, current):
        if not isinstance(saved, dict) or not isinstance(current, dict):
            return False
        keys = ("kind", "width", "height", "generator", "seed", "biome_version", "map_id")
        for key in keys:
            if saved.get(key) != current.get(key):
                return False
        return True

    @staticmethod
    def _snap_player_to_surface(game, x_hint=None):
        """Place player safely on the current map while preserving X when possible."""
        p = game.player
        x = float(p.x if x_hint is None else x_hint)
        x = max(p.width() * 0.5 + 1.0, min(
            float(game.world.width_tiles * TILE_SIZE) - p.width() * 0.5 - 1.0, x
        ))
        tx = max(1, min(game.world.width_tiles - 2, int(x // TILE_SIZE)))
        row = game.world.first_solid_row(tx)
        if row is None:
            p.x = float(getattr(game, "respawn_x", p.x))
            p.y = float(getattr(game, "respawn_y", p.y))
        else:
            p.x = (tx + 0.5) * TILE_SIZE
            p.y = float(row) * TILE_SIZE - 1.0
        p.vx = 0.0
        p.vy = 0.0
        p.state = STAND
        p.grounded = True
        p.climb = None
        p.reset_climb_regrab()  # FIX84: a load is a fresh movement intent.
        return tx, row

    @staticmethod
    def _cleanup_legacy_orphan_surface_water(game,saved_format,saved_version):
        """FIX48 remove the old long, shallow dry-biome water sheet once.

        FIX42 only migrated saves older than format 15 and ignored cells above
        amount .36.  A FIX45-47 session could therefore save the same artifact
        at modern format and relaunching/pressing Load restored it forever.
        We now migrate any pre-FIX48 save, but only remove a very specific
        signature: non-authored, one-cell-thick surface water in a contiguous
        dry-biome run of at least six columns.  Authored lake/ocean/swamp water
        and deeper player-made pools are preserved.
        """
        try:
            # Migration is only for saves created before FIX48. Once a save has
            # been written by FIX48 or any later numbered build, never re-run
            # the cleanup on player-authored water.
            ver_text = str(saved_version or "").upper()
            match = re.search(r"FIX(\d+)", ver_text)
            if match is not None and int(match.group(1)) >= 48:
                return 0
            payload=game.map_loader.payload if game.map_loader else {}
            layers=dict((payload or {}).get("layers",{}) or {})
            authored={(int(r[0]),int(r[1])) for r in layers.get("water",[]) if isinstance(r,(list,tuple)) and len(r)>=3}
            from world.biomes import normalize_biome_spans,biome_at_tile_x
            meta=dict((payload or {}).get("metadata",{}) or {})
            spans=normalize_biome_spans(meta.get("biome_spans"),game.world.width_tiles)
            by_x={}
            water=game.environment.state.water
            for (tx,ty),amount in list(water.items()):
                tx=int(tx);ty=int(ty);amount=float(amount)
                if amount<=0.02 or (tx,ty) in authored:
                    continue
                surface=game.world.first_solid_row(tx)
                if surface is None:
                    continue
                if biome_at_tile_x(tx,spans,game.world.width_tiles) in {"ocean","lake","swamp"}:
                    continue
                # The orphan is a shallow skin immediately above/on the local
                # terrain surface.  Real pools have free water in deeper nearby
                # cells and are deliberately excluded.
                if not (int(surface)-2 <= ty <= int(surface)):
                    continue
                deeper=False
                for yy in range(int(surface)+1,min(int(game.world.height_tiles),int(surface)+4)):
                    if (tx,yy) not in authored and float(water.get((tx,yy),0.0))>0.02:
                        deeper=True;break
                if deeper:
                    continue
                by_x[tx]=ty

            removed=[];run=[];prev=None;prev_y=None
            def flush(rows):
                if len(rows)>=6:
                    removed.extend((x,by_x[x]) for x in rows)
            for tx in sorted(by_x):
                ty=by_x[tx]
                if prev is None or (tx==prev+1 and abs(int(ty)-int(prev_y))<=1):
                    run.append(tx)
                else:
                    flush(run);run=[tx]
                prev=tx;prev_y=ty
            flush(run)
            for tx,ty in removed:
                game.environment.state.set_water(tx,ty,0.0)
            game.legacy_surface_water_removed=len(removed)
            if removed:
                print("FIX48 removed orphan surface-water sheet cells:",len(removed))
            return len(removed)
        except Exception as exc:
            print("FIX48 water migration skipped",repr(exc));return 0

    def _migrate_legacy_save_if_needed(self):
        if os.path.isfile(self.save_path):
            return False
        for old_path in self.legacy_save_paths:
            if not os.path.isfile(old_path):
                continue
            try:
                tmp = self.save_path + ".migrate.tmp"
                shutil.copy2(old_path, tmp)
                os.replace(tmp, self.save_path)
                print("SAVE MIGRATED:", old_path, "->", self.save_path)
                return True
            except Exception as exc:
                print("Save migration skipped:", repr(exc))
        return False

    def _ensure_dir(self):
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            return
        except Exception as primary_exc:
            print("Persistent save dir error:", repr(primary_exc))

        # Last-resort fallback remains inside Pyto Documents.  This avoids
        # silently falling back to the project/runtime folder.
        fallback = os.path.expanduser("~/Documents/PytoRPG_saves")
        os.makedirs(fallback, exist_ok=True)
        self.save_dir = fallback
        self.save_path = os.path.join(fallback, SAVE_SLOT_NAME)

    @classmethod
    def preferred_saved_map_path(cls, project_root):
        """Return an existing map path recorded in the persistent save slot."""
        project_root=os.path.abspath(project_root or os.getcwd())
        persistent_root=os.environ.get("PYTO_RPG_SAVE_ROOT","").strip()
        if not persistent_root:
            persistent_root=os.path.expanduser("~/Documents/PytoRPG")
        save_path=os.path.join(os.path.abspath(os.path.expanduser(persistent_root)),"saves",SAVE_SLOT_NAME)
        try:
            with open(save_path,"r",encoding="utf-8") as f:data=json.load(f)
            return cls._validated_saved_map_path(project_root, data)
        except Exception:
            return None

    @staticmethod
    def _validated_saved_map_path(project_root, data):
        """Resolve ``map_file`` only when it stays inside this project/maps."""
        if not isinstance(data, dict):
            return None
        rel=str(data.get("map_file","") or "").replace("\\","/").lstrip("/")
        if not rel:
            return None
        project_root=os.path.abspath(project_root or os.getcwd())
        candidate=os.path.realpath(os.path.join(project_root,*rel.split("/")))
        maps_root=os.path.realpath(os.path.join(project_root,"maps"))
        try:
            inside=os.path.commonpath((candidate,maps_root))==maps_root
        except Exception:
            inside=False
        if not inside:
            return None
        return candidate if os.path.isfile(candidate) else None

    def saved_map_path(self):
        """Return the valid authored map referenced by this save slot."""
        try:
            with open(self.save_path,"r",encoding="utf-8") as f:
                data=json.load(f)
            return self._validated_saved_map_path(self.project_root,data)
        except Exception:
            return None

    def _validated_session_map(self, raw_path):
        """Return ``(canonical_path, maps-relative path)`` for a real map.

        Runtime session keys are absolute real paths because portal switching
        needs an unambiguous dictionary key.  Absolute paths must never enter
        a portable save, however: they disclose a device container path and
        stop working when the project folder is replaced.  This gate accepts
        either representation, resolves symlinks, and permits files only below
        this project's real ``maps`` directory.
        """
        text = str(raw_path or "").strip().replace("\\", "/")
        if not text or "\x00" in text or len(text) > 1024:
            return None
        project_root = os.path.realpath(os.path.abspath(self.project_root))
        maps_root = os.path.realpath(os.path.join(project_root, "maps"))
        if os.path.isabs(text):
            candidate = os.path.realpath(os.path.abspath(text))
        else:
            candidate = os.path.realpath(os.path.join(project_root, *text.split("/")))
        try:
            inside = os.path.commonpath((candidate, maps_root)) == maps_root
        except Exception:
            inside = False
        if (
            not inside
            or not candidate.lower().endswith(".json")
            or not os.path.isfile(candidate)
        ):
            return None
        relative = os.path.relpath(candidate, project_root).replace(os.sep, "/")
        if not relative.startswith("maps/") or len(relative) > 512:
            return None
        return candidate, relative

    @staticmethod
    def _finite_number(value, default):
        try:
            value = float(value)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(value)
            return value
        except Exception:
            return float(default)

    @classmethod
    def _clean_world_item_rows(
        cls, rows, *, strict=True, require_entity_id=True,
        world_width_px=None, world_height_px=None,
    ):
        """Return finite, cap-bounded dynamic-drop rows or ``None``.

        Map sessions use strict mode so one malformed spatial snapshot cannot
        be partially restored. Top-level load uses tolerant mode for legacy
        saves: invalid/non-finite rows are rejected individually and scanning
        is still bounded. Missing IDs are accepted only for those old saves.
        """
        if not isinstance(rows, (list, tuple)):
            return None
        if bool(strict) and len(rows) > MAX_DYNAMIC_WORLD_DROPS:
            return None
        output = []
        used_ids = set()
        # One extra row detects an oversized hostile list without traversing it.
        for row in rows[: MAX_DYNAMIC_WORLD_DROPS + 1]:
            valid = isinstance(row, dict)
            try:
                x = float(row.get("x", 0.0)) if valid else 0.0
                y = float(row.get("y", 0.0)) if valid else 0.0
                vy = float(row.get("vy", 0.0)) if valid else 0.0
                count = int(row.get("count", 0) or 0) if valid else 0
                valid = valid and all(
                    value == value
                    and value not in (float("inf"), float("-inf"))
                    for value in (x, y, vy)
                )
                valid = valid and 0 < count <= MAX_DROP_STACK_COUNT
                valid = valid and abs(vy) <= MAX_DROP_ABS_SAVE_SPEED
                valid = valid and (
                    abs(x) <= MAX_DROP_ABS_POSITION_PX
                    and abs(y) <= MAX_DROP_ABS_POSITION_PX
                )
                if world_width_px is not None and world_height_px is not None:
                    width = max(0.0, float(world_width_px))
                    height = max(0.0, float(world_height_px))
                    margin = float(DROP_WORLD_MARGIN_PX)
                    valid = valid and (
                        -margin <= x <= width + margin
                        and -margin <= y <= height + margin
                    )
            except (TypeError, ValueError, OverflowError):
                valid = False
            entity_id = str(row.get("entity_id", "") or "")[:64] if isinstance(row, dict) else ""
            suffix = entity_id[5:] if entity_id.startswith("drop_") else ""
            serial = int(suffix) if suffix.isdigit() else -1
            if require_entity_id and not (0 < serial <= 1000000000):
                valid = False
            if entity_id and (
                not (0 < serial <= 1000000000) or entity_id in used_ids
            ):
                valid = False
            if not valid:
                if strict:
                    return None
                continue
            if len(output) >= MAX_DYNAMIC_WORLD_DROPS:
                if strict:
                    return None
                break
            if entity_id:
                used_ids.add(entity_id)
            cleaned_row = {
                "entity_id": entity_id,
                "item_id": str(row.get("item_id", "item") or "item")[:128],
                "name": str(row.get("name", "物品") or "物品")[:128],
                "count": count,
                "x": x,
                "y": y,
                "vy": vy,
            }
            durability = clean_drop_heavy_uses(
                cleaned_row["item_id"], count, row.get("heavy_uses"),
            )
            if durability is not None:
                cleaned_row["heavy_uses"] = durability
            output.append(cleaned_row)
        return output

    @classmethod
    def _session_wall_time(cls, state, now_wall=None, now_monotonic=None):
        """Convert old in-memory monotonic captures to a portable wall time."""
        now_wall = cls._finite_number(time.time() if now_wall is None else now_wall, time.time())
        captured = state.get("captured_at_unix") if isinstance(state, dict) else None
        if captured is not None:
            captured = cls._finite_number(captured, now_wall)
        elif isinstance(state, dict) and state.get("captured_monotonic") is not None:
            now_mono = cls._finite_number(
                time.monotonic() if now_monotonic is None else now_monotonic,
                time.monotonic(),
            )
            old_mono = cls._finite_number(state.get("captured_monotonic"), now_mono)
            captured = now_wall - max(0.0, now_mono - old_mono)
        else:
            captured = now_wall
        # A clock corrected into the future must not freeze an unloaded map's
        # timers. Old past timestamps remain valid and simply allow short
        # respawn timers to expire while Pyto was closed.
        return max(0.0, min(float(captured), now_wall))

    @classmethod
    def _clean_map_session_state(cls, state, now_wall=None, now_monotonic=None):
        """Whitelist and JSON-roundtrip one session, returning ``(copy, bytes)``.

        The roundtrip rejects NaN, custom Python objects, tuple dictionary keys
        and any future runtime-only object accidentally added to the cache.
        A malformed field rejects the whole map instead of restoring a partial
        terrain/environment combination.
        """
        if not isinstance(state, dict):
            return None, 0
        clean = {}
        for key, accepted in cls._MAP_SESSION_STATE_TYPES.items():
            if key not in state or not isinstance(state.get(key), accepted):
                return None, 0
            clean[key] = state.get(key)

        # Normalise the small entity-facing collections before the generic
        # JSON check. This makes restore atomic for malformed/hand-edited rows
        # instead of applying half a terrain or clearing drops before a late
        # conversion error.
        try:
            changes = []
            for row in clean["world_changes"]:
                if not isinstance(row, (list, tuple)) or len(row) != 3:
                    return None, 0
                changes.append([int(row[0]), int(row[1]), int(row[2])])
            clean["world_changes"] = changes

            wet_rows = []
            for row in clean["topsoil_wet_clock"]:
                if not isinstance(row, (list, tuple)) or len(row) < 3:
                    return None, 0
                value = float(row[2])
                if value != value or value in (float("inf"), float("-inf")):
                    return None, 0
                wet_rows.append([int(row[0]), int(row[1]), value])
            clean["topsoil_wet_clock"] = wet_rows

            base_width = max(0, int(clean["world_base"].get("width", 0) or 0))
            base_height = max(0, int(clean["world_base"].get("height", 0) or 0))
            item_rows = cls._clean_world_item_rows(
                clean["world_items"], strict=True, require_entity_id=True,
                world_width_px=(base_width * TILE_SIZE if base_width else None),
                world_height_px=(base_height * TILE_SIZE if base_height else None),
            )
            if item_rows is None:
                return None, 0
            clean["world_items"] = item_rows

            if len(clean["creatures"]) > cls.MAX_SESSION_CREATURES:
                return None, 0
            creature_rows = []
            for row in clean["creatures"]:
                if not isinstance(row, dict):
                    return None, 0
                entity_id = str(row.get("entity_id", "") or "")[:128]
                if not entity_id:
                    return None, 0
                x = float(row.get("x", 0.0))
                y = float(row.get("y", 0.0))
                hp = float(row.get("hp", 0.0))
                if any(value != value or value in (float("inf"), float("-inf")) for value in (x, y, hp)):
                    return None, 0
                creature_rows.append({
                    "entity_id": entity_id,
                    "x": x, "y": y, "hp": hp,
                    "active": bool(row.get("active", False)),
                    "death_processed": bool(row.get("death_processed", False)),
                    "behavior_state": str(row.get("behavior_state", "idle") or "idle")[:64],
                    "player_detected": bool(row.get("player_detected", False)),
                    "provoked_by_player": bool(row.get("provoked_by_player", False)),
                })
            clean["creatures"] = creature_rows

            respawn = clean["creature_respawn"]
            pending = respawn.get("pending", ())
            defeated = respawn.get("defeated", ())
            if not isinstance(pending, (list, tuple)) or not isinstance(defeated, (list, tuple)):
                return None, 0
            if len(pending) > cls.MAX_SESSION_CREATURES or len(defeated) > cls.MAX_SESSION_CREATURES:
                return None, 0
            pending_rows = []
            for row in pending:
                if not isinstance(row, dict):
                    return None, 0
                remaining = float(row.get("remaining_seconds", 0.0))
                if remaining != remaining or remaining in (float("inf"), float("-inf")):
                    return None, 0
                pending_rows.append({
                    "entity_id": str(row.get("entity_id", "") or "")[:128],
                    "remaining_seconds": max(0.0, remaining),
                    "retries": max(0, min(1000000, int(row.get("retries", 0) or 0))),
                    "blocked_reason": str(row.get("blocked_reason", "") or "")[:128],
                })
            clean["creature_respawn"] = {
                "format": max(0, int(respawn.get("format", 0) or 0)),
                "map_token": str(respawn.get("map_token", "") or "")[:512],
                "pending": pending_rows,
                "defeated": [str(value or "")[:128] for value in defeated],
            }
        except (TypeError, ValueError, OverflowError):
            return None, 0
        try:
            drop_serial_value = float(clean.get("drop_serial", 0))
            if drop_serial_value != drop_serial_value or drop_serial_value in (
                float("inf"), float("-inf"),
            ):
                return None, 0
            clean["drop_serial"] = max(
                0, min(1000000000, int(drop_serial_value))
            )
        except (TypeError, ValueError, OverflowError):
            return None, 0
        clean["captured_at_unix"] = cls._session_wall_time(
            state, now_wall=now_wall, now_monotonic=now_monotonic
        )
        try:
            encoded = json.dumps(
                clean,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > cls.MAX_MAP_SESSION_BYTES:
                return None, len(encoded)
            return json.loads(encoded.decode("utf-8")), len(encoded)
        except (TypeError, ValueError, OverflowError):
            return None, 0

    def _encoded_map_sessions(self, game):
        """Build the bounded, portable format-18 session section."""
        states = getattr(game, "_map_session_states", {}) or {}
        if not isinstance(states, dict):
            states = {}
        current_raw = str(
            getattr(game, "current_map_path", "")
            or getattr(getattr(game, "map_runtime", None), "source_path", "")
            or getattr(getattr(game, "map_loader", None), "path", "")
            or ""
        )
        current_valid = self._validated_session_map(current_raw)
        current_path = current_valid[0] if current_valid else ""
        candidate_by_path = {}
        skipped = 0
        now_wall = time.time()
        now_mono = time.monotonic()
        for raw_path, raw_state in states.items():
            valid = self._validated_session_map(raw_path)
            if valid is None or (current_path and valid[0] == current_path):
                skipped += 1
                continue
            clean, state_bytes = self._clean_map_session_state(
                raw_state, now_wall=now_wall, now_monotonic=now_mono
            )
            if clean is None:
                skipped += 1
                continue
            row = {"map_file": valid[1], "state": clean}
            try:
                row_bytes = len(json.dumps(
                    row, ensure_ascii=False, separators=(",", ":"),
                    sort_keys=True, allow_nan=False,
                ).encode("utf-8"))
            except (TypeError, ValueError, OverflowError):
                skipped += 1
                continue
            if row_bytes > self.MAX_MAP_SESSION_BYTES + 1024:
                skipped += 1
                continue
            candidate = (float(clean["captured_at_unix"]), valid[1], row, row_bytes)
            previous = candidate_by_path.get(valid[0])
            if previous is None or candidate[0] > previous[0]:
                if previous is not None:
                    skipped += 1
                candidate_by_path[valid[0]] = candidate
            else:
                skipped += 1

        # If a future world contains more than the cap, retain the maps visited
        # most recently.  Final path sorting keeps save diffs deterministic.
        candidates = list(candidate_by_path.values())
        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected = []
        used_bytes = 64
        for _captured, _relative, row, row_bytes in candidates:
            if len(selected) >= self.MAX_MAP_SESSIONS:
                skipped += 1
                continue
            if used_bytes + row_bytes > self.MAX_MAP_SESSIONS_BYTES:
                skipped += 1
                continue
            selected.append(row)
            used_bytes += row_bytes
        selected.sort(key=lambda row: str(row.get("map_file", "")))
        if skipped:
            print("SAVE map sessions skipped:", skipped)
        return {
            "format": self.MAP_SESSION_FORMAT_VERSION,
            "sessions": selected,
        }

    def _decoded_map_sessions(self, game, data):
        """Validate format-18 disk rows and rebuild canonical runtime keys."""
        try:
            saved_format = int(data.get("format", 0) or 0)
        except Exception:
            saved_format = 0
        payload = data.get("map_sessions") if saved_format >= 18 else None
        if not isinstance(payload, dict):
            return {}
        try:
            if int(payload.get("format", 0) or 0) != self.MAP_SESSION_FORMAT_VERSION:
                return {}
        except Exception:
            return {}
        rows = payload.get("sessions", ())
        if not isinstance(rows, list):
            return {}

        current_raw = str(
            getattr(game, "current_map_path", "")
            or getattr(getattr(game, "map_runtime", None), "source_path", "")
            or getattr(getattr(game, "map_loader", None), "path", "")
            or ""
        )
        current_valid = self._validated_session_map(current_raw)
        current_path = current_valid[0] if current_valid else ""
        decoded = {}
        used_bytes = 64
        # A valid writer emits at most MAX_MAP_SESSIONS.  The small scan bound
        # also prevents a hand-edited save with millions of tiny rows from
        # monopolising the simulation thread during Load.
        for row in rows[: self.MAX_MAP_SESSIONS * 4]:
            if len(decoded) >= self.MAX_MAP_SESSIONS or not isinstance(row, dict):
                continue
            valid = self._validated_session_map(row.get("map_file"))
            if valid is None or (current_path and valid[0] == current_path):
                continue
            clean, state_bytes = self._clean_map_session_state(row.get("state"))
            if clean is None:
                continue
            try:
                row_bytes = len(json.dumps(
                    {"map_file": valid[1], "state": clean},
                    ensure_ascii=False, separators=(",", ":"),
                    sort_keys=True, allow_nan=False,
                ).encode("utf-8"))
            except (TypeError, ValueError, OverflowError):
                continue
            if row_bytes > self.MAX_MAP_SESSION_BYTES + 1024:
                continue
            if used_bytes + row_bytes > self.MAX_MAP_SESSIONS_BYTES:
                continue
            try:
                key = game._map_session_key(valid[0])
            except Exception:
                key = valid[0]
            if not key:
                continue
            # A valid writer emits each canonical map once. Treat duplicate
            # rows in a hand-edited save as malformed instead of charging the
            # same map against the aggregate byte budget more than once.
            if key in decoded:
                continue
            decoded[key] = clean
            used_bytes += row_bytes
        return decoded

    def exists(self):
        return os.path.isfile(self.save_path)

    @staticmethod
    def _remove_exact_files(paths, strict=True):
        """Remove only explicitly enumerated save files.

        New Game must never recursively remove a directory (especially the
        user's Documents folder).  This helper deliberately refuses ordinary
        directories and removes symlinks as links rather than following them.
        It is also useful in source-level regression tests where the save root
        is redirected to a temporary directory.
        """
        removed = []
        failures = []
        seen = set()
        for raw_path in tuple(paths or ()):
            raw_text = str(raw_path or "").strip()
            if not raw_text:
                continue
            path = os.path.abspath(os.path.expanduser(raw_text))
            if path in seen:
                continue
            seen.add(path)
            try:
                if not os.path.lexists(path):
                    continue
                if os.path.isdir(path) and not os.path.islink(path):
                    raise IsADirectoryError(path)
                os.remove(path)
                removed.append(path)
            except Exception as exc:
                failures.append((path, repr(exc)))
        if failures and strict:
            raise OSError("無法清除存檔：" + "; ".join(
                "%s (%s)" % (path, error) for path, error in failures
            ))
        return tuple(removed), tuple(failures)

    def clear_slot(self, include_legacy=True):
        """Delete the current slot and its exact temporary/migration sources.

        This is intentionally separate from ``load`` and is never called by a
        normal load failure.  Removing the legacy migration sources prevents a
        deleted pre-FIX save from silently reappearing on the next launch.
        """
        targets = [
            self.save_path,
            self.save_path + ".tmp",
            self.save_path + ".migrate.tmp",
        ]
        if include_legacy:
            for legacy in self.legacy_save_paths:
                targets.extend((legacy, legacy + ".tmp", legacy + ".migrate.tmp"))
        removed, _failures = self._remove_exact_files(targets, strict=True)
        return removed

    def replace_with_clean_game(self, game):
        """Atomically replace save_01 with a freshly constructed game state.

        ``save`` writes a temporary JSON and uses ``os.replace`` so an I/O
        failure cannot leave a half-written slot.  Only after the clean slot is
        durable do we remove obsolete migration sources.  Failure to remove a
        legacy source is reported for diagnostics but cannot resurrect it
        while the new primary slot exists.
        """
        path = self.save(game)
        cleanup = []
        for legacy in self.legacy_save_paths:
            cleanup.extend((legacy, legacy + ".tmp", legacy + ".migrate.tmp"))
        _removed, failures = self._remove_exact_files(cleanup, strict=False)
        for old_path, error in failures:
            print("NEW GAME legacy cleanup warning:", old_path, error)
        return path

    def save(self, game):
        self._ensure_dir()

        # Transient waterballs are not serialized; return their conserved
        # water mass to atmosphere before saving.
        game.magic.reclaim_condensed_projectiles_to_atmosphere()

        p = game.player
        try:
            creature_respawn_state = game.creature_system.respawn.export_state()
        except Exception:
            creature_respawn_state = {}
        try:
            world_item_rows = game.item_drop_system.export_rows()
        except Exception:
            world_item_rows = []
        try:
            drop_serial = max(0, min(
                1000000000, int(getattr(game, "_drop_serial", 0) or 0)
            ))
        except (TypeError, ValueError, OverflowError):
            drop_serial = 0

        data = {
            "format": self.FORMAT_VERSION,
            "game_version": VERSION,
            "saved_at": time.time(),
            "map_file": os.path.relpath(
                str(getattr(getattr(game,"map_runtime",None),"source_path","") or os.path.join(self.project_root,"maps","editor_map.json")),
                self.project_root,
            ).replace(os.sep,"/"),
            "world_base": self._world_base_signature(game),
            "sky_layout_version": 83,
            "map_sessions": self._encoded_map_sessions(game),

            "player": {
                "x": p.x,
                "y": p.y,
                "hp": p.hp,
                "stamina": p.stamina,
                "mana": p.mana,
                # FIX65 compatibility field; movement is always run-speed.
                "run_mode": True,
                "facing": p.facing,
            },

            "magic": {
                "selected": game.magic.selected_magic,
                "ledger": game.magic.ledger.export_state(),
            },

            "inventory": game.inventory.export_state(),
            "equipment": {
                "weapon": str(getattr(game.melee, "selected_weapon", "sword")),
                "gear": game.equipment.export_state(),
            },
            "opened_chests": sorted(str(v) for v in getattr(game, "opened_chest_ids", set())),
            "recovery_chests": game._export_recovery_chests(),

            "game_time": game.time.export_state(),

            "energy": game.environment.energy.export_state(),

            "ecology": {
                "topsoil_wet_clock": [
                    [tx, ty, value]
                    for (tx, ty), value in sorted(
                        game.environment.plants.topsoil_wet_clock.items()
                    )
                ],
            },

            "world": {
                "changes": game.world.export_changes(),
                "environment": game.environment.state.export_state(),
            },

            "entities": {
                "item_test": {
                    "active": game.item.active,
                    "picked": game.item.picked,
                    "temperature_c": getattr(
                        game.item,
                        "temperature_c",
                        20.0,
                    ),
                },
                "npc_test": {
                    "x": game.npc.x,
                    "y": game.npc.y,
                    "ai_state": game.npc.ai_state,
                    "target_x": game.npc.target_x,
                    "temperature_c": getattr(
                        game.npc,
                        "temperature_c",
                        37.0,
                    ),
                },
                "world_items": world_item_rows,
                "drop_serial": drop_serial,
                "creatures": [
                    {
                        "entity_id": c.entity_id,
                        "x": c.x, "y": c.y,
                        "hp": c.hp,
                        "active": c.active,
                        "death_processed": c.death_processed,
                        "behavior_state": c.behavior_state,
                        "player_detected": bool(getattr(c, "player_detected", False)),
                        "provoked_by_player": bool(getattr(c, "provoked_by_player", False)),
                    }
                    for c in game.creatures
                ],
                # FIX68 stores only the small pending-timer/defeated-ID set.
                # Spawn blueprints remain deterministic map data and are not
                # duplicated into the save file.
                "creature_respawn": creature_respawn_state,
                "physics_objects": [
                    {
                        "entity_id": obj.entity_id,
                        "x": obj.x,
                        "y": obj.y,
                        "vx": obj.vx,
                        "vy": obj.vy,
                        "active": obj.active,
                        "temperature_c": getattr(
                            obj,
                            "temperature_c",
                            20.0,
                        ),
                    }
                    for obj in game.physics_objects
                ],
            },
        }

        tmp = self.save_path + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            # Close is normally enough, but fsync makes a user pressing Save
            # and immediately force-quitting Pyto much safer.
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass

        os.replace(
            tmp,
            self.save_path,
        )

        return self.save_path


    @staticmethod
    def _repair_player_if_embedded(game):
        """Keep old saves usable after the authored base map changes.

        V0.7.3 introduces real biome terrain. A player position saved against
        the older test map can therefore land inside a new solid column. Only
        repair genuinely embedded/out-of-bounds positions; valid cave/house
        positions are preserved exactly.
        """
        p = game.player
        max_x = max(TILE_SIZE, float(game.world.width_tiles) * TILE_SIZE)
        max_y = max(TILE_SIZE, float(game.world.height_tiles) * TILE_SIZE)
        p.x = max(p.width() * 0.5 + 1.0, min(max_x - p.width() * 0.5 - 1.0, float(p.x)))
        p.y = max(p.height() + 1.0, min(max_y - 1.0, float(p.y)))
        try:
            from engine.math2d import rects_overlap
            from world.tile_registry import tile_def
            box = p.bbox(state=STAND)
            embedded = any(not tile_def(tid).one_way_platform and rects_overlap(box, rect)
                           for _tx, _ty, tid, rect in game.world.solid_cells_in_rect(box))
        except Exception:
            embedded = False
        if not embedded:
            return False
        tx = max(1, min(game.world.width_tiles - 2, int(p.x // TILE_SIZE)))
        row = game.world.first_solid_row(tx)
        if row is None:
            return False
        p.y = float(row) * TILE_SIZE - 1.0
        p.vx = 0.0
        p.vy = 0.0
        p.state = STAND
        p.grounded = True
        print("SAVE LOAD: player snapped to safe biome surface", tx, row)
        return True

    def load(self, game):
        if not os.path.exists(self.save_path):
            return False, "尚未找到存檔"

        with open(
            self.save_path,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        # FIX82 translates only the known FIX81 main-world coordinate space.
        # Never discard excavations/creatures merely because sky was padded.
        from systems.sky_save_migration import migrate as migrate_sky
        converted, sky_migrated = migrate_sky(data, self.project_root)
        if sky_migrated:
            backup=self.save_path+".pre_FIX82.bak"
            if not os.path.exists(backup):
                # A failed backup aborts this load instead of destroying the
                # sole recovery copy. The original save is not rewritten here.
                shutil.copy2(self.save_path,backup)
            data=converted
            game.sky_save_migrated=True
            print("FIX82 main-world save translated +96 rows; backup:",backup)

        from systems.sky_layout_migration import migrate as migrate_cloud_layout
        lowered, layout_migrated = migrate_cloud_layout(data,self.project_root)
        if layout_migrated:
            backup=self.save_path+".pre_FIX83.bak"
            if not os.path.exists(backup): shutil.copy2(self.save_path,backup)
            data=lowered
            game.sky_layout_migrated=True

        # Decode to a local first.  It is committed to GameApp only after the
        # authoritative current-map fields below have loaded successfully.
        # Format 17 has no section and intentionally yields an empty cache.
        loaded_map_sessions = self._decoded_map_sessions(game, data)

        current_world_base = self._world_base_signature(game)
        saved_world_base = data.get("world_base")
        world_compatible = self._world_base_compatible(saved_world_base, current_world_base)
        # V0.7.2.x saves have no world_base signature.  When loading them into
        # the new v0.7.3 biome map, preserve player resources/inventory but do
        # NOT replay old-map spatial deltas/environment/entity coordinates.
        migrated_world = not world_compatible
        if migrated_world:
            print(
                "SAVE WORLD MIGRATION:",
                "saved=", saved_world_base or data.get("game_version", "legacy"),
                "current=", current_world_base,
            )

        # Rebuild the correct deterministic base world, then apply save delta.
        # A custom/editor map must never be replaced by build_test_world() when
        # the user presses the Load button.
        if getattr(game, "custom_map_loaded", False) and getattr(game, "map_loader", None) is not None:
            game.map_loader.apply_terrain(game.world)
        else:
            game.world.reset()
            game.world.build_test_world()

        world_data = data.get("world", {})
        if world_compatible:
            game.world.apply_changes(
                world_data.get("changes", [])
            )

        game.time.import_state(
            data.get("game_time", {})
        )

        if getattr(game, "custom_map_loaded", False) and getattr(game, "map_loader", None) is not None:
            game.environment.initialize_from_world()
            game.map_loader.apply_environment(game)
        else:
            game.environment.reset_test_environment()
        env_data = world_data.get("environment") if world_compatible else None
        if isinstance(env_data, dict):
            game.environment.state.import_state(env_data)
            self._cleanup_legacy_orphan_surface_water(game,data.get("format",0),data.get("game_version",""))

            # V0.7.1: never materialize reservoirs for every chunk of a
            # Terraria-scale world while loading an old save. Missing vapor /
            # organic entries are lazily seeded only when chunks become active
            # by EnvironmentManager._ensure_active_reservoirs().
            if "vapor" not in env_data:
                game.environment.state.vapor.clear()
            if "organic" not in env_data:
                game.environment.state.organic.clear()

            # Backward compatibility: old saves had no thermal state.
            game.environment.thermal.initialize_world()

        if world_compatible:
            game.environment.energy.import_state(
                data.get("energy", {})
            )
        else:
            game.environment.energy.capture_baseline()

        game.environment.plants.topsoil_wet_clock.clear()
        ecology_data = data.get("ecology", {}) if world_compatible else {}
        for item in ecology_data.get("topsoil_wet_clock", []):
            if len(item) >= 3:
                game.environment.plants.topsoil_wet_clock[(
                    int(item[0]),
                    int(item[1]),
                )] = float(item[2])

        p_data = data.get("player", {})
        p = game.player

        p.x = float(
            p_data.get("x", p.x)
        )
        p.y = float(
            p_data.get("y", p.y)
        )
        p.hp = float(
            p_data.get("hp", p.hp)
        )
        p.stamina = float(
            p_data.get(
                "stamina",
                p.stamina,
            )
        )
        p.mana = float(
            p_data.get("mana", p.mana)
        )
        # FIX65: an older save may contain run_mode=false.  Loading it must
        # not bring back the removed walk/run toggle.
        p.run_mode = True
        p.facing = int(
            p_data.get(
                "facing",
                p.facing,
            )
        )

        p.vx = 0.0
        p.vy = 0.0
        p.state = STAND
        p.grounded = True
        p.climb = None
        p.reset_climb_regrab()  # FIX84: a load is a fresh movement intent.

        if world_compatible:
            self._repair_player_if_embedded(game)
        else:
            tx, row = self._snap_player_to_surface(game, p.x)
            print("SAVE LOAD: legacy position moved to current biome surface", tx, row)
        # Rendering interpolates prev -> current.  Keeping the pre-load map's
        # previous coordinate here can place the sprite outside the camera for
        # one or more frames after a manual load from a secondary map.
        p.prev_x = float(p.x)
        p.prev_y = float(p.y)

        magic_data = data.get("magic", {})
        game.magic.set_magic(
            magic_data.get(
                "selected",
                game.magic.MAGIC_FIRE,
            )
        )

        if world_compatible:
            game.magic.ledger.import_state(
                magic_data.get(
                    "ledger",
                    {},
                )
            )
        inventory_payload = data.get("inventory", {})
        has_authoritative_inventory = (
            isinstance(inventory_payload, dict)
            and isinstance(inventory_payload.get("slots"), list)
        )
        game.inventory.import_state(inventory_payload)
        try:
            equipment = data.get("equipment", {}) or {}
            saved_version_text=str(data.get("game_version","") or "").upper()
            saved_fix_match=re.search(r"FIX(\d+)",saved_version_text)
            saved_before_fix73=(
                saved_fix_match is None or int(saved_fix_match.group(1)) < 73
            )
            # Weapon migration remains limited to saves without authoritative
            # slots. The FIX73 hook is a new starter grant, so every older
            # release receives it exactly once. After the next save records
            # FIX73, intentionally dropping/removing it remains authoritative.
            if not has_authoritative_inventory:
                game.ensure_weapon_inventory()
            if (not has_authoritative_inventory) or saved_before_fix73:
                game.ensure_starter_tool_inventory()
            has_wearable_state = isinstance(equipment.get("gear"), dict)
            if not has_wearable_state:
                game.ensure_equipment_inventory()
            saved_weapon = str(equipment.get("weapon", "sword") or "sword")
            saved_row = WEAPON_DEFS.get(saved_weapon)
            saved_item = str((saved_row or {}).get("item_id", "") or "")
            if saved_row is not None and game.inventory.count_item(saved_item) > 0:
                game.melee.equip_weapon(saved_weapon)
            else:
                owned_item = game.inventory.first_owned_weapon()
                owned_weapon = weapon_from_item(owned_item)
                game.melee.equip_weapon(owned_weapon or "sword")
                if not owned_weapon:
                    game.action_mode = game.ACTION_TOOL
                    game.tools.set_action_mode_active(True)
            game.equipment.import_state(equipment.get("gear", equipment))
            game.equipment.sync_player(game.player)
        except Exception:
            pass
        try:
            game.opened_chest_ids = set(str(v) for v in (data.get("opened_chests", []) or []))
            game._apply_opened_chest_state()
            game._import_recovery_chests(data.get("recovery_chests", {}))
        except Exception:
            pass
        game.magic.fireballs.clear()
        game.magic.waterballs.clear()
        game.magic.iceballs.clear()
        game.magic.electricballs.clear()

        entity_data = data.get(
            "entities",
            {},
        ).get(
            "item_test",
            {},
        )

        if world_compatible:
            game.item.active = bool(
                entity_data.get(
                    "active",
                    game.item.active,
                )
            )
            game.item.picked = bool(
                entity_data.get(
                    "picked",
                    game.item.picked,
                )
            )

            game.item.temperature_c = float(
                entity_data.get(
                    "temperature_c",
                    getattr(
                        game.item,
                        "temperature_c",
                        20.0,
                    ),
                )
            )

        entities = data.get("entities", {})
        npc_data = entities.get("npc_test", {})
        if world_compatible:
            game.npc.x = float(npc_data.get("x", game.npc.x))
            game.npc.y = float(npc_data.get("y", game.npc.y))
            game.npc.ai_state = str(npc_data.get("ai_state", game.npc.ai_state))
            game.npc.target_x = float(
                npc_data.get(
                    "target_x",
                    game.npc.target_x,
                )
            )
            game.npc.temperature_c = float(
                npc_data.get(
                    "temperature_c",
                    getattr(
                        game.npc,
                        "temperature_c",
                        37.0,
                    ),
                )
            )

        # Replace runtime-generated ground drops with the saved set.
        raw_world_items = entities.get("world_items", []) if world_compatible else []
        item_rows = self._clean_world_item_rows(
            raw_world_items,
            strict=False,
            # FIX69 top-level rows had no entity_id; retain migration support.
            require_entity_id=False,
            world_width_px=float(game.world.width_tiles) * TILE_SIZE,
            world_height_px=float(game.world.height_tiles) * TILE_SIZE,
        )
        if item_rows is None:
            item_rows = []
        game.item_drop_system.clear_dynamic()
        highest_serial = 0
        for item_data in item_rows:
            candidate = str(item_data.get("entity_id", "") or "")
            suffix = candidate[5:] if candidate.startswith("drop_") else ""
            if suffix.isdigit():
                highest_serial = max(highest_serial, int(suffix))
        try:
            saved_serial = max(0, min(1000000000, int(self._finite_number(
                entities.get("drop_serial", 0), 0.0,
            ))))
        except (TypeError, ValueError, OverflowError):
            saved_serial = 0
        game._drop_serial = max(
            int(getattr(game, "_drop_serial", 0) or 0),
            highest_serial,
            max(0, saved_serial),
        )
        for item_data in item_rows:
            result = game.spawn_world_item_result(
                item_data.get("item_id", "item"),
                item_data.get("name", "物品"),
                item_data.get("count", 1),
                item_data.get("x", p.x),
                item_data.get("y", p.y),
                heavy_uses=item_data.get("heavy_uses"),
                preferred_entity_id=item_data.get("entity_id") or None,
                allow_merge=False,
                announce_rejection=False,
            )
            item = result.item
            if item is not None:
                item.vy = float(item_data.get("vy", 0.0))
        game._drop_serial = max(
            int(getattr(game, "_drop_serial", 0) or 0), highest_serial,
        )

        creature_by_id = {c.entity_id: c for c in game.creatures}
        for c_data in (entities.get("creatures", []) if world_compatible else []):
            c = creature_by_id.get(c_data.get("entity_id"))
            if c is None:
                continue
            c.x = float(c_data.get("x", c.x))
            c.y = float(c_data.get("y", c.y))
            c.hp = float(c_data.get("hp", c.hp))
            c.active = bool(c_data.get("active", c.active))
            c.death_processed = bool(c_data.get("death_processed", c.death_processed))
            c.behavior_state = str(c_data.get("behavior_state", c.behavior_state))
            c.provoked_by_player = bool(c_data.get("provoked_by_player", False))
            can_attack = bool(getattr(c, "hostile", False) or c.provoked_by_player)
            old_awareness = can_attack and c.behavior_state in ("chase", "attack", "recover", "recoil")
            c.player_detected = bool(c_data.get("player_detected", old_awareness)) if can_attack else False
            c.vx = 0.0
            c.vy = 0.0

        # Creature active/HP values must be restored before pending timers.
        # Older saves omit this field; their dead actors are discovered by the
        # scheduler's bounded one-second population sync and receive a fresh
        # full delay instead of spawning immediately.
        respawn_state = entities.get("creature_respawn")
        if world_compatible and isinstance(respawn_state, dict):
            try:
                saved_at = self._finite_number(data.get("saved_at"), time.time())
                offline_seconds = max(0.0, time.time() - saved_at)
                game.creature_system.respawn.import_state(
                    respawn_state,
                    elapsed_seconds=offline_seconds,
                )
            except Exception:
                pass

        by_id = {obj.entity_id: obj for obj in game.physics_objects}
        for obj_data in (entities.get("physics_objects", []) if world_compatible else []):
            obj = by_id.get(obj_data.get("entity_id"))
            if obj is None:
                continue
            obj.x = float(obj_data.get("x", obj.x))
            obj.y = float(obj_data.get("y", obj.y))
            obj.vx = float(obj_data.get("vx", obj.vx))
            obj.vy = float(obj_data.get("vy", obj.vy))
            obj.active = bool(
                obj_data.get(
                    "active",
                    obj.active,
                )
            )
            obj.temperature_c = float(
                obj_data.get(
                    "temperature_c",
                    getattr(
                        obj,
                        "temperature_c",
                        20.0,
                    ),
                )
            )

        game.chunk_streamer.update(
            p.x,
            p.y,
        )
        game.environment.matter.capture_baseline(game)
        if not isinstance(data.get("energy"), dict):
            game.environment.energy.capture_baseline()

        # Current-map world/entities above are authoritative and were never
        # inserted into this dictionary by the format-18 decoder.
        game._map_session_states = loaded_map_sessions

        if migrated_world:
            return True, self.save_path + " (舊地圖存檔已安全遷移：保留角色/背包，重置世界空間狀態)"
        return True, self.save_path
