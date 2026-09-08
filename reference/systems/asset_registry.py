# -*- coding: utf-8 -*-
"""Project-local external asset registry used by gameplay and Metal rendering.

Bindings live outside rpg_runtime.zip so artwork can be replaced without
rewriting gameplay code. Missing/invalid assets are always allowed: callers
fall back to the built-in geometric renderer.

V0.7.7.5 FIX4 also exposes the pixel-editor JSON as renderer-ready rectangles.
This intentionally avoids a second Metal texture pipeline on Pyto: edited pixel
art is converted once to compact same-colour rectangles, cached, then submitted
through the already-stable instanced quad renderer.
"""
import json
import os


class AssetRegistry:
    FORMAT = 3

    def __init__(self, assets_root):
        self.assets_root = os.path.abspath(os.path.expanduser(str(assets_root)))
        self.registry_path = os.path.join(self.assets_root, "registry.json")
        self.bindings = {}
        self._resolved = {}
        self.signature = (0, 0, 0)
        self._pixel_cache = {}
        self._pixel_payload_cache = {}
        self.reload()

    @classmethod
    def from_project_root(cls, project_root):
        root = os.path.abspath(os.path.expanduser(project_root or os.getcwd()))
        return cls(os.path.join(root, "assets"))

    def reload(self):
        self.bindings = {}
        self._resolved = {}
        self._pixel_cache = {}
        self._pixel_payload_cache = {}
        self._editor_defaults = None
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("bindings", {}) if isinstance(data, dict) else {}
            if isinstance(rows, dict):
                self.bindings = rows
        except Exception:
            self.bindings = {}

        for asset_id, row in tuple(self.bindings.items()):
            if not isinstance(row, dict):
                continue
            rel = str(row.get("file", "")).replace("\\", "/").lstrip("/")
            if not rel:
                continue
            path = os.path.join(self.assets_root, *rel.split("/"))
            if os.path.isfile(path):
                self._resolved[str(asset_id)] = path

        try:
            st = os.stat(self.registry_path)
            self.signature = (
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                int(getattr(st, "st_size", 0)),
                len(self.bindings),
            )
        except Exception:
            self.signature = (0, 0, len(self.bindings))
        return self

    def refresh_if_changed(self):
        """Reload registry when the editor changed it without restarting Pyto."""
        try:
            st = os.stat(self.registry_path)
            current = (
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                int(getattr(st, "st_size", 0)),
                len(self.bindings),
            )
        except Exception:
            current = (0, 0, len(self.bindings))
        if current[:2] != self.signature[:2]:
            self.reload()
            return True
        return False

    def get(self, asset_id, default=None):
        row = self.bindings.get(str(asset_id))
        return row if isinstance(row, dict) else default

    def resolve(self, asset_id):
        return self._resolved.get(str(asset_id))

    def has(self, asset_id):
        return str(asset_id) in self._resolved

    def category_bindings(self, category):
        category = str(category)
        return {
            key: value for key, value in self.bindings.items()
            if isinstance(value, dict)
            and str(value.get("category", "")) == category
            and key in self._resolved
        }

    def render_meta(self, asset_id):
        """Return normalized texture/sprite metadata for the renderer."""
        row = self.get(asset_id)
        if not row or not self.resolve(asset_id):
            return None
        category = str(row.get("category", ""))
        anchor = str(row.get("anchor", "center") or "center")
        try:
            fw = max(0, int(row.get("frame_width", 0) or 0))
        except Exception:
            fw = 0
        try:
            fh = max(0, int(row.get("frame_height", 0) or 0))
        except Exception:
            fh = 0
        try:
            frame_index = max(0, int(row.get("frame_index", 0) or 0))
        except Exception:
            frame_index = 0
        preserve = row.get("preserve_aspect", category != "tile")
        flip = row.get("flip_with_facing", category in ("player", "creature"))
        return {
            "asset_id": str(asset_id),
            "category": category,
            "path": self.resolve(asset_id),
            "frame_width": fw,
            "frame_height": fh,
            "frame_index": frame_index,
            "anchor": anchor,
            "preserve_aspect": bool(preserve),
            "flip_with_facing": bool(flip),
        }

    @staticmethod
    def _parse_rgba(value):
        s = str(value or "#00000000").strip().lstrip("#")
        if len(s) == 6:
            s += "FF"
        if len(s) != 8:
            return None
        try:
            rgba = tuple(int(s[i:i+2], 16) / 255.0 for i in (0, 2, 4, 6))
        except Exception:
            return None
        if rgba[3] <= 0.001:
            return None
        return rgba

    @staticmethod
    def _merge_pixel_rects(pixels, size):
        """Merge same-colour pixel runs vertically into compact rectangles.

        Return rows as (x0, y0, x1, y1, rgba), coordinates in source pixels.
        The algorithm is lossless; it only merges identical horizontal runs on
        consecutive scanlines, which greatly lowers instance count for normal
        pixel art while keeping exact hard pixel edges.
        """
        active = {}
        rects = []
        for y in range(size):
            row = pixels[y] if y < len(pixels) and isinstance(pixels[y], list) else []
            runs = []
            x = 0
            while x < size:
                raw = row[x] if x < len(row) else "#00000000"
                rgba = AssetRegistry._parse_rgba(raw)
                if rgba is None:
                    x += 1
                    continue
                key_color = tuple(round(float(v), 6) for v in rgba)
                x0 = x
                x += 1
                while x < size:
                    raw2 = row[x] if x < len(row) else "#00000000"
                    rgba2 = AssetRegistry._parse_rgba(raw2)
                    if rgba2 is None or tuple(round(float(v), 6) for v in rgba2) != key_color:
                        break
                    x += 1
                runs.append((x0, x, key_color))

            next_active = {}
            seen = set()
            for x0, x1, color in runs:
                key = (x0, x1, color)
                seen.add(key)
                if key in active:
                    rx0, ry0, rx1, _ry1, rcolor = active[key]
                    next_active[key] = (rx0, ry0, rx1, y + 1, rcolor)
                else:
                    next_active[key] = (x0, y, x1, y + 1, color)
            for key, rect in active.items():
                if key not in seen:
                    rects.append(rect)
            active = next_active
        rects.extend(active.values())
        return tuple(rects)

    def _pixel_source_payload(self, asset_id):
        """Load one editor JSON and cache it until registry reload.

        The editor writes the JSON before registry.json, so a hot reload sees a
        complete Base + animation payload atomically.
        """
        aid = str(asset_id)
        cached = self._pixel_payload_cache.get(aid)
        if cached is not None:
            return cached
        real_aid=aid.split("editor.visual:",1)[1] if aid.startswith("editor.visual:") else aid
        row = self.get(real_aid)
        if aid.startswith("editor.visual:") and (not isinstance(row,dict) or not row.get("pixel_source")):
            # Explicit map stamps may use an immutable captured default, but
            # this never overrides ordinary game's procedural defaults.
            from systems.editor_catalog import read_json
            if getattr(self,"_editor_defaults",None) is None:
                self._editor_defaults=read_json(os.path.join(self.assets_root,"editor_asset_manifest.json")).get("assets",{})
            meta=self._editor_defaults.get(real_aid,{})
            row={"category":real_aid.split(".",1)[0],"pixel_source":meta.get("default_source","")}
        if not isinstance(row, dict):
            return None
        # FIX104 linked FX aliases point at their actual weapon animation.
        # Map stamps and the effect editor therefore see the same edited pixels.
        parent_id = str(row.get("linked_effect_asset", "") or "")
        parent_state = str(row.get("linked_effect_state", "") or "")
        if parent_id and parent_state and parent_id != aid:
            parent = self._pixel_source_payload(parent_id)
            raw = (parent or {}).get("animations", {}).get(parent_state, {})
            if isinstance(raw, dict) and raw.get("frames"):
                linked = dict(parent)
                linked.update(asset_id=aid, category="effect",
                              size=int(raw.get("canvas_size", parent.get("size", 64))),
                              pixels=max(raw["frames"], key=lambda grid: sum(not str(color).upper().endswith("00") for pixel_row in grid for color in pixel_row)), animations={"idle":raw,"effect":raw},
                              combat_bindings={})
                self._pixel_payload_cache[aid] = linked
                return linked
        rel = str(row.get("pixel_source", "")).replace("\\", "/").lstrip("/")
        if not rel:
            return None
        path = os.path.join(self.assets_root, *rel.split("/"))
        if not os.path.isfile(path):
            return None
        try:
            st = os.stat(path)
            sig = (
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                int(getattr(st, "st_size", 0)),
            )
        except Exception:
            sig = (0, 0)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            size = max(1, int(data.get("size", row.get("pixel_size", 0)) or 0))
            # FIX104 long 96px animations reuse a small palette. Share strings
            # within this payload instead of keeping hundreds of thousands of
            # duplicate RGBA strings alive. No process-global intern table.
            if str(data.get("authoring", {}).get("pack", "")).startswith("FIX1"):
                palette = {}
                grids = [data.get("pixels", [])]
                for animation in data.get("animations", {}).values():
                    if isinstance(animation, dict):
                        grids.extend(animation.get("frames", []))
                for grid in grids:
                    if not isinstance(grid, list):continue
                    for pixel_row in grid:
                        if not isinstance(pixel_row, list):continue
                        for pixel_index, color in enumerate(pixel_row):
                            if isinstance(color, str):
                                pixel_row[pixel_index] = palette.setdefault(color, color)
            out = {
                "asset_id": aid,
                "category": str(row.get("category", "")),
                "size": size,
                "pixels": data.get("pixels", []),
                "animations": data.get("animations", {}) if isinstance(data.get("animations", {}), dict) else {},
                "combat_bindings": data.get("combat_bindings", {}) if isinstance(data.get("combat_bindings", {}), dict) else {},
                # FIX20 renderer mapping metadata. Missing fields mean a legacy
                # FIX19 normalized-square source and retain the old path until
                # the AssetEditor migrates/saves it.
                "pixel_mapping": str(data.get("pixel_mapping", row.get("pixel_mapping", "")) or ""),
                "pixel_world_scale": data.get("pixel_world_scale", row.get("pixel_world_scale", None)),
                "logical_size": int(data.get("logical_size", size) or size),
                "export_size": int(data.get("export_size", row.get("export_size", size)) or size),
                "export_scale": float(data.get("export_scale", row.get("export_scale", 1.0)) or 1.0),
                "world_fit": str(data.get("world_fit", row.get("world_fit", "")) or ""),
                "signature": sig,
                "path": path,
            }
            self._pixel_payload_cache[aid] = out
            return out
        except Exception:
            return None

    def pixel_rects(self, asset_id):
        """Return cached Base-frame pixel rectangles for an asset, or None."""
        aid = str(asset_id)
        cached = self._pixel_cache.get((aid, "base"))
        if cached is not None:
            return cached
        payload = self._pixel_source_payload(aid)
        if payload is None:
            return None
        size = int(payload.get("size", 1) or 1)
        pixels = payload.get("pixels", [])
        if not isinstance(pixels, list) or not pixels:
            return None
        out = {
            "asset_id": aid,
            "category": payload.get("category", ""),
            "size": size,
            "pixel_mapping": payload.get("pixel_mapping", ""),
            "pixel_world_scale": payload.get("pixel_world_scale", None),
            "logical_size": payload.get("logical_size", size),
            "export_size": payload.get("export_size", size),
            "export_scale": payload.get("export_scale", 1.0),
            "world_fit": payload.get("world_fit", ""),
            "rects": self._merge_pixel_rects(pixels, size),
            "signature": payload.get("signature", (0, 0)),
            "path": payload.get("path", ""),
            "animation": None,
            "frame_index": 0,
        }
        self._pixel_cache[(aid, "base")] = out
        return out

    @staticmethod
    def _animation_state_candidates(state):
        """Return compatible authored animation-state names.

        FIX11 keeps old projects and editor wording interoperable: player motion
        may be authored as either ``walk`` or ``move``.  ``run`` can also fall
        back to the ordinary movement clip until a dedicated run clip exists.
        """
        state = str(state or "")
        aliases = {
            "walk": ("walk", "move"),
            "move": ("move", "walk"),
            "run": ("run", "walk", "move"),
            # FIX15 posture-aware player animation fallbacks. Dedicated clips
            # are preferred; when only one crouch clip has been authored, it is
            # reused for crouch movement instead of reverting immediately to a
            # full-height walk cycle.
            # ``climb`` is kept as the LAST fallback because FIX14's editor
            # exposed only that English word; existing projects may therefore
            # contain crawl artwork saved under climb. New dedicated states
            # always win and remove this compatibility ambiguity.
            "crouch": ("crouch", "crouch_idle", "climb"),
            "crouch_walk": ("crouch_walk", "crouch_move", "crouch", "crawl", "climb", "walk", "move"),
            "crouch_run": ("crouch_run", "crouch_walk", "crouch_move", "crouch", "crawl", "climb", "run", "walk", "move"),
            "prone": ("prone", "crawl_idle", "climb"),
            "crawl": ("crawl", "prone_walk", "prone_move", "prone", "climb"),
            # FIX16: dedicated one-shot roll. Older custom characters that do
            # not have it yet retain authored art through compact/idle fallbacks
            # instead of dropping to the geometric debug body.
            "roll": ("roll", "crouch", "prone", "idle"),
        }
        return aliases.get(state, (state,))

    def animation_info(self, asset_id, state):
        payload = self._pixel_source_payload(str(asset_id))
        if payload is None:
            return None
        raw = None
        resolved_state = str(state)
        animations = payload.get("animations", {})
        for candidate in self._animation_state_candidates(state):
            candidate_raw = animations.get(candidate) if isinstance(animations, dict) else None
            if isinstance(candidate_raw, dict) and isinstance(candidate_raw.get("frames"), list) and candidate_raw.get("frames"):
                raw = candidate_raw
                resolved_state = candidate
                break
        if not isinstance(raw, dict):
            return None
        frames = raw.get("frames", [])
        if not isinstance(frames, list) or not frames:
            return None
        try:
            fps = max(1.0, min(30.0, float(raw.get("fps", 6.0) or 6.0)))
        except Exception:
            fps = 6.0
        events = {}
        raw_events = raw.get("events", {})
        if isinstance(raw_events, dict):
            for name, value in raw_events.items():
                try:
                    events[str(name)] = max(0, min(len(frames)-1, int(value)))
                except Exception:
                    pass
        frame_events={}
        raw_frame_events=raw.get("frame_events",{})
        if isinstance(raw_frame_events,dict):
            for key,meta in raw_frame_events.items():
                try:index=max(0,min(len(frames)-1,int(key)))
                except Exception:continue
                if isinstance(meta,dict):frame_events[index]=dict(meta)
        damage_frames=tuple(sorted(index for index,meta in frame_events.items() if bool(meta.get("damage",False))))
        count=len(frames)
        link_frame=events.get("link")
        # FIX17: a link marker is both a preferred transition entry point and,
        # unless an explicit loop_start exists, the beginning of the stable
        # repeating range.  This lets an authored intro (e.g. stand->crouch)
        # play once without replaying on every idle loop.
        loop_start=events.get("loop_start", link_frame if link_frame is not None else 0)
        loop_end=events.get("loop_end", count-1)
        try:loop_start=max(0,min(count-1,int(loop_start)))
        except Exception:loop_start=0
        try:loop_end=max(0,min(count-1,int(loop_end)))
        except Exception:loop_end=count-1
        if loop_end < loop_start:
            loop_end=loop_start
        try:canvas_size=max(1,int(raw.get("canvas_size",payload.get("size",1)) or payload.get("size",1)))
        except Exception:canvas_size=max(1,int(payload.get("size",1) or 1))
        return {
            "fps": fps,
            "loop": bool(raw.get("loop", resolved_state not in ("jump", "roll"))),
            "frame_count": count,
            # Effect flipbooks may use a larger local canvas than the weapon
            # body. Keep that bound explicit for editor/runtime parity.
            "canvas_size": canvas_size,
            "state": resolved_state,
            "events": events,
            "frame_events": frame_events,
            "damage_frames": damage_frames,
            "link_frame": int(link_frame) if link_frame is not None else None,
            "loop_start": int(loop_start),
            "loop_end": int(loop_end),
            "action_frame": int(events["action"]) if "action" in events else None,
        }

    def combat_binding(self, asset_id, attack_state):
        """Resolve an attack clip to an authored VFX asset/clip.

        FIX112 generalises the old weapon-only same-asset binding.  Creatures,
        bosses and weapons may now point at one independent ``effect.*`` asset;
        this lets a boss and its dropped weapon consume the exact same VFX
        flipbook without copying it into their body canvases.
        """
        aid=str(asset_id); state=str(attack_state)
        payload=self._pixel_source_payload(aid)
        if payload is None:return None
        rows=payload.get("combat_bindings",{})
        raw=rows.get(state) if isinstance(rows,dict) else None
        explicit=isinstance(raw,dict)
        if not explicit: raw={}
        default_effect="heavy_effect" if state in ("heavy_attack","special") else "normal_effect"
        effect_asset=str(raw.get("effect_asset",aid) or aid)
        effect_state=str(raw.get("effect_state",("effect" if effect_asset!=aid else default_effect)) or ("effect" if effect_asset!=aid else default_effect))
        effect_payload=self._pixel_source_payload(effect_asset)
        if effect_payload is None:return None
        animations=effect_payload.get("animations",{})
        effect_raw=animations.get(effect_state) if isinstance(animations,dict) else None
        if not isinstance(effect_raw,dict) or not effect_raw.get("frames"):
            return None
        anchor=str(raw.get("anchor","actor" if aid.startswith("creature.") else "weapon") or "actor")
        allowed=("hand","weapon","player","actor","creature","ground","ground_forward","target","projectile","action_box")
        if anchor not in allowed:anchor="actor" if aid.startswith("creature.") else "weapon"
        def number(key,default,lo,hi):
            try:return max(lo,min(hi,float(raw.get(key,default) or default)))
            except Exception:return float(default)
        ox=number("offset_x",0.0,-320.0,320.0); oy=number("offset_y",0.0,-320.0,320.0)
        scale=number("scale",1.0,.1,8.0)
        world_width_tiles=number("world_width_tiles",0.0,0.0,32.0)
        world_height_tiles=number("world_height_tiles",0.0,0.0,32.0)
        forward_tiles=number("forward_tiles",0.0,-16.0,16.0)
        render_mode=str(raw.get("render_mode","authored") or "authored").lower()
        if render_mode not in ("fix54","runtime","authored"):render_mode="authored"
        motion=str(raw.get("motion","follow") or "follow").lower()
        if motion not in ("follow","projectile","world"):motion="follow"
        pixel_density_mode=str(raw.get("pixel_density_mode","") or "").lower()
        if pixel_density_mode not in ("","match_owner"):pixel_density_mode=""
        pixel_density_scale=number("pixel_density_scale",1.0,.25,2.5)
        segments=[]
        source_segments=raw.get("segments",())
        if isinstance(source_segments,list):
            for seg in source_segments[:16]:
                if not isinstance(seg,dict):continue
                clean={}
                for key in ("delay","phase","frame","hold","dx_tiles","dy_tiles","scale","start_frame","clip_frames"):
                    if key in seg:clean[key]=seg[key]
                if clean:segments.append(clean)
        world_motion_mode=str(raw.get("world_motion_mode","") or "").lower()
        if world_motion_mode not in ("stationary","segments","distance"):
            world_motion_mode="segments" if segments else "stationary"
        motion_repeat=str(raw.get("motion_repeat","once") or "once").lower()
        if motion_repeat not in ("once","loop"):motion_repeat="once"
        return {
            "effect_asset":effect_asset,"effect_state":effect_state,
            "trigger_event":str(raw.get("trigger_event","effect") or "effect"),
            "anchor":anchor,"offset_x":ox,"offset_y":oy,"scale":scale,
            "render_mode":render_mode,"motion":motion,
            "world_width_tiles":world_width_tiles,"world_height_tiles":world_height_tiles,
            "forward_tiles":forward_tiles,
            "follow_owner":bool(raw.get("follow_owner",motion=="follow")),
            "pixel_density_mode":pixel_density_mode,"pixel_density_scale":pixel_density_scale,
            "segments":segments,
            "segment_playback":(str(raw.get("segment_playback","phase") or "phase").lower() if str(raw.get("segment_playback","phase") or "phase").lower() in ("phase","clip","full_clip","animate") else "phase"),
            "segment_start_frame":int(number("segment_start_frame",0.0,0.0,192.0)),
            "segment_clip_frames":int(number("segment_clip_frames",0.0,0.0,192.0)),
            "segment_source_fps":number("segment_source_fps",0.0,0.0,60.0),
            "world_motion_mode":world_motion_mode,"motion_repeat":motion_repeat,
            "travel_start_delay":number("travel_start_delay",0.0,0.0,4.0),
            "travel_start_x_tiles":number("travel_start_x_tiles",0.0,-16.0,16.0),
            "travel_start_y_tiles":number("travel_start_y_tiles",0.0,-16.0,16.0),
            "travel_distance_tiles":number("travel_distance_tiles",4.0,-32.0,32.0),
            "travel_y_tiles":number("travel_y_tiles",0.0,-16.0,16.0),
            "travel_duration":number("travel_duration",1.0,.05,8.0),
        }

    def animation_event_frame(self, asset_id, state, event_name, fallback=None):
        """Return the authored frame index for a gameplay animation event."""
        info = self.animation_info(asset_id, state)
        if not info:
            return fallback
        events = info.get("events", {})
        if str(event_name) in events:
            return int(events[str(event_name)])
        return fallback

    def animation_event_time(self, asset_id, state, event_name, fallback_frame=None):
        """Return seconds from animation start to an authored event frame."""
        info = self.animation_info(asset_id, state)
        if not info:
            return None
        frame = self.animation_event_frame(asset_id, state, event_name, fallback_frame)
        if frame is None:
            return None
        fps = max(1.0, float(info.get("fps", 6.0) or 6.0))
        return max(0.0, float(frame) / fps)

    def pixel_animation_rects(self, asset_id, state, time_seconds=0.0, frame_index=None):
        """Resolve one animation frame to the same compact quad format as Base.

        If the requested state has no frames, return None so callers can fall
        back to the Base custom art. `time_seconds` is visual-only and never
        feeds gameplay physics.
        """
        aid = str(asset_id); state = str(state)
        payload = self._pixel_source_payload(aid)
        if payload is None:
            return None
        animations = payload.get("animations", {})
        raw = None
        resolved_state = state
        for candidate in self._animation_state_candidates(state):
            candidate_raw = animations.get(candidate) if isinstance(animations, dict) else None
            if isinstance(candidate_raw, dict) and isinstance(candidate_raw.get("frames"), list) and candidate_raw.get("frames"):
                raw = candidate_raw
                resolved_state = candidate
                break
        if not isinstance(raw, dict):
            return None
        frames = raw.get("frames", [])
        if not isinstance(frames, list) or not frames:
            return None
        try:
            fps = max(1.0, min(30.0, float(raw.get("fps", 6.0) or 6.0)))
        except Exception:
            fps = 6.0
        loop = bool(raw.get("loop", resolved_state not in ("jump", "roll")))
        raw_events=raw.get("events",{}) if isinstance(raw.get("events",{}),dict) else {}
        link_frame=raw_events.get("link")
        try:loop_start=int(raw_events.get("loop_start",link_frame if link_frame is not None else 0))
        except Exception:loop_start=0
        try:loop_end=int(raw_events.get("loop_end",len(frames)-1))
        except Exception:loop_end=len(frames)-1
        loop_start=max(0,min(len(frames)-1,loop_start)); loop_end=max(loop_start,min(len(frames)-1,loop_end))
        if frame_index is None:
            raw_idx=max(0,int(max(0.0,float(time_seconds))*fps))
            if loop and raw_idx>loop_end:
                span=max(1,loop_end-loop_start+1)
                idx=loop_start+((raw_idx-(loop_end+1))%span)
            else:
                idx=min(raw_idx,len(frames)-1)
        else:
            idx=max(0,min(len(frames)-1,int(frame_index)))
        key = (aid, resolved_state, idx)
        cached = self._pixel_cache.get(key)
        if cached is not None:
            return cached
        try:size=max(1,int(raw.get("canvas_size",payload.get("size",1)) or payload.get("size",1)))
        except Exception:size=max(1,int(payload.get("size",1) or 1))
        pixels = frames[idx]
        if not isinstance(pixels, list):
            return None
        out = {
            "asset_id": aid,
            "category": payload.get("category", ""),
            "size": size,
            "pixel_mapping": payload.get("pixel_mapping", ""),
            "pixel_world_scale": payload.get("pixel_world_scale", None),
            "logical_size": payload.get("logical_size", size),
            "export_size": payload.get("export_size", size),
            "export_scale": payload.get("export_scale", 1.0),
            "world_fit": payload.get("world_fit", ""),
            "rects": self._merge_pixel_rects(pixels, size),
            "signature": payload.get("signature", (0, 0)),
            "path": payload.get("path", ""),
            "animation": resolved_state,
            "frame_index": idx,
            "fps": fps,
            "loop": loop,
        }
        self._pixel_cache[key] = out
        return out
