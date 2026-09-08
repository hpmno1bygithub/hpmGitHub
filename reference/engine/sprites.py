# -*- coding: utf-8 -*-
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpriteDef:
    """Logical sprite resource.

    asset_path remains optional. V0.7.3 external artwork is resolved through
    systems.asset_registry using stable IDs; this class remains a renderer-neutral
    animation resource for later multi-frame animation work.
    """

    sprite_id: str
    asset_path: str = ""
    frame_width: int = 0
    frame_height: int = 0
    anchor_x: float = 0.5
    anchor_y: float = 1.0


@dataclass(frozen=True)
class AnimationClip:
    animation_id: str
    frames: tuple
    fps: float = 8.0
    loop: bool = True


class SpriteRegistry:
    def __init__(self):
        self.sprites = {}
        self.animations = {}

    def register_sprite(self, sprite):
        self.sprites[sprite.sprite_id] = sprite
        return sprite

    def register_animation(self, clip):
        self.animations[clip.animation_id] = clip
        return clip

    def get_sprite(self, sprite_id):
        return self.sprites.get(sprite_id)

    def get_animation(self, animation_id):
        return self.animations.get(animation_id)


class AnimationPlayer:
    """Renderer-neutral animation clock with authored frame-range support.

    The original prototype clock only knew the two dummy frames registered in
    ``SpriteRegistry``.  FIX17 lets an external pixel animation provide its real
    frame count/FPS and a loop sub-range.  This is what allows an intro such as
    ``stand -> crouch`` to play once and then loop only the stable crouched
    frames instead of replaying the standing pose every time the clip loops.

    ``time`` remains elapsed seconds since the most recent animation switch for
    backwards compatibility.  ``frame_index`` is now the authoritative visual
    frame and should be preferred by renderers.
    """

    def __init__(self, registry):
        self.registry = registry
        self.animation_id = None
        self.time = 0.0
        self.frame_index = 0
        self.frame_cursor = 0.0
        self._fps_override = None
        self._frame_count_override = None
        self._loop_override = None
        self._loop_start = 0
        self._loop_end = None

    def play(
        self, animation_id, restart=False, start_frame=0,
        fps=None, frame_count=None, loop=None,
        loop_start=None, loop_end=None,
    ):
        changed = (self.animation_id != animation_id) or bool(restart)
        self.animation_id = animation_id

        if fps is None:
            self._fps_override = None
        else:
            try:self._fps_override = max(0.001, float(fps))
            except Exception:self._fps_override = None

        if frame_count is None:
            self._frame_count_override = None
        else:
            try:self._frame_count_override = max(1, int(frame_count))
            except Exception:self._frame_count_override = None

        self._loop_override = None if loop is None else bool(loop)

        count = self._effective_frame_count()
        try:ls = int(loop_start) if loop_start is not None else 0
        except Exception:ls = 0
        try:le = int(loop_end) if loop_end is not None else count - 1
        except Exception:le = count - 1
        ls=max(0,min(count-1,ls)); le=max(0,min(count-1,le))
        if le < ls:
            le = ls
        self._loop_start = ls
        self._loop_end = le

        if not changed:
            return False

        try:start = int(start_frame)
        except Exception:start = 0
        start=max(0,min(count-1,start))
        self.time = 0.0
        self.frame_cursor = float(start)
        self.frame_index = start
        return True

    def _effective_clip(self):
        return self.registry.get_animation(self.animation_id)

    def _effective_frame_count(self):
        if self._frame_count_override is not None:
            return max(1,int(self._frame_count_override))
        clip=self._effective_clip()
        return max(1,len(clip.frames)) if clip is not None and clip.frames else 1

    def _effective_fps(self):
        if self._fps_override is not None:
            return max(0.001,float(self._fps_override))
        clip=self._effective_clip()
        return max(0.001,float(getattr(clip,'fps',8.0) or 8.0)) if clip is not None else 8.0

    def _effective_loop(self):
        if self._loop_override is not None:
            return bool(self._loop_override)
        clip=self._effective_clip()
        return bool(getattr(clip,'loop',True)) if clip is not None else True

    def update(self, dt):
        clip = self._effective_clip()
        count = self._effective_frame_count()
        if count <= 0:
            self.frame_index = 0
            return None

        try:delta=max(0.0,float(dt))
        except Exception:delta=0.0
        fps=self._effective_fps()
        self.time += delta
        self.frame_cursor += delta * fps

        if self._effective_loop():
            ls=max(0,min(count-1,int(self._loop_start)))
            le=self._loop_end if self._loop_end is not None else count-1
            le=max(ls,min(count-1,int(le)))
            # Frames before loop_start are an optional one-shot intro.  Once the
            # cursor passes loop_end, only [loop_start, loop_end] repeats.
            boundary=float(le+1)
            if self.frame_cursor >= boundary:
                span=max(1,le-ls+1)
                self.frame_cursor=float(ls)+((self.frame_cursor-boundary)%float(span))
            idx=max(0,min(count-1,int(self.frame_cursor)))
        else:
            self.frame_cursor=min(float(count-1),self.frame_cursor)
            idx=max(0,min(count-1,int(self.frame_cursor)))

        self.frame_index=idx
        if clip is None or not clip.frames:
            return None
        # Logical clips may still have fewer dummy frames than the authored
        # external animation.  Clamp only for the optional logical return value;
        # frame_index itself remains the authored frame index.
        logical_index=max(0,min(len(clip.frames)-1,idx))
        return clip.frames[logical_index]
