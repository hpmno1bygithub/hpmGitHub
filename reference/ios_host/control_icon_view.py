# -*- coding: utf-8 -*-
"""Cached, original pixel icons shared by every gameplay control.

Every control owns one retained UIImageView.  Selection changes only swap a
cached UIImage reference, so the pixel symbols never create per-pixel UIKit
views and do not add work to the 60 Hz gameplay loop.  FIX71 also centralises
the square pixel-frame styling and native pressed opacity here so the primary
UIControl backend and the Gesture fallback use the same visual vocabulary.
"""
import os

import pyto_ui as ui
from UIKit import UIImageView, UIImage

from ios_host.objc_view import WrapperView


_ICON_FILES = {
    "control_move": "control_move.png",
    "control_up": "control_up.png",
    "control_left": "control_left.png",
    "control_right": "control_right.png",
    "control_down": "control_down.png",
    "control_jump": "control_jump.png",
    "control_crouch": "control_crouch.png",
    "control_action": "control_action.png",
    "control_roll": "control_roll.png",
    "control_interact": "control_interact.png",
    "control_aim": "control_aim.png",
    "control_save": "control_save.png",
    "control_load": "control_load.png",
    "control_new_game": "control_new_game.png",
    "control_close": "control_close.png",
    "control_backpack": "control_backpack.png",
    "tool_pickaxe": "tool_pickaxe.png",
    "tool_axe": "tool_axe.png",
    "tool_grapple": "tool_grapple.png",
    "magic_fireball": "magic_fireball.png",
    "magic_waterball": "magic_waterball.png",
    "magic_iceball": "magic_iceball.png",
    "magic_electricball": "magic_electricball.png",
    "action_unknown": "action_unknown.png",
}
_IMAGE_CACHE = {}

_TOOL_ICON_ALIASES = {
    "shovel": "tool_pickaxe",
    "pickaxe": "tool_pickaxe",
    "鏟子": "tool_pickaxe",
    "十字鎬": "tool_pickaxe",
    "鎬": "tool_pickaxe",
    "axe": "tool_axe",
    "斧": "tool_axe",
    "斧頭": "tool_axe",
    "grapple": "tool_grapple",
    "grappling_hook": "tool_grapple",
    "hook": "tool_grapple",
    "鉤爪": "tool_grapple",
}

_MAGIC_ICON_ALIASES = {
    "fire": "magic_fireball",
    "fireball": "magic_fireball",
    "火球": "magic_fireball",
    "water": "magic_waterball",
    "waterball": "magic_waterball",
    "水球": "magic_waterball",
    "ice": "magic_iceball",
    "iceball": "magic_iceball",
    "冰球": "magic_iceball",
    "electric": "magic_electricball",
    "electricball": "magic_electricball",
    "lightning": "magic_electricball",
    "電球": "magic_electricball",
}


def tool_icon_id(value):
    return _TOOL_ICON_ALIASES.get(str(value or "").strip().lower(), "tool_pickaxe")


def magic_icon_id(value):
    return _MAGIC_ICON_ALIASES.get(str(value or "").strip().lower(), "magic_fireball")


def _icon_path(icon_id):
    root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "").strip()
    if not root:
        return ""
    filename = _ICON_FILES.get(str(icon_id), _ICON_FILES["action_unknown"])
    return os.path.join(root, "assets", "control_icons", filename)


def _load_image(icon_id):
    key = str(icon_id or "action_unknown")
    if key not in _ICON_FILES:
        key = "action_unknown"
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]

    path = _icon_path(key)
    image = None
    if path and os.path.isfile(path):
        try:
            image = UIImage.imageWithContentsOfFile(path)
        except Exception:
            try:
                image = UIImage.imageWithContentsOfFile_(path)
            except Exception:
                image = None
    _IMAGE_CACHE[key] = image
    return image


def preload_control_images():
    """Load the fixed icon atlas once when explicitly requested."""
    for icon_id in _ICON_FILES:
        _load_image(icon_id)


class _PixelUIImageView(WrapperView):
    objc_class = UIImageView

    def configure_view(self, view):
        try:
            view.contentMode = 1  # UIViewContentModeScaleAspectFit
        except Exception:
            pass
        try:
            view.clipsToBounds = True
        except Exception:
            pass
        try:
            view.userInteractionEnabled = False
        except Exception:
            pass
        try:
            view.layer.magnificationFilter = "nearest"
            # FIX72 sources use a finer 48x48 authored grid.  Controls usually
            # display below the 96px file size, so linear minification keeps
            # the icon readable while nearest magnification preserves pixels.
            view.layer.minificationFilter = "linear"
        except Exception:
            pass

    def set_image(self, image):
        try:
            self.objc_view.setImage(image)
        except Exception:
            try:
                self.objc_view.image = image
            except Exception:
                pass


class ControlIconWidget:
    """One retained UIImageView for a selected magic or tool symbol."""

    def __init__(self, parent):
        self.view = _PixelUIImageView()
        self.view.background_color = ui.Color.rgb(0, 0, 0, 0)
        self.view.user_interaction_enabled = False
        self.view.hidden = True
        parent.add_subview(self.view)
        self.icon_id = ""
        self._frame = (0.0, 0.0, 1.0, 1.0)

    def set_icon(self, value):
        icon_id = str(value or "action_unknown")
        if icon_id not in _ICON_FILES:
            icon_id = "action_unknown"
        if icon_id == self.icon_id:
            return
        self.icon_id = icon_id
        self.view.set_image(_load_image(icon_id))

    def set_tool(self, value):
        self.set_icon(tool_icon_id(value))

    def set_magic(self, value):
        self.set_icon(magic_icon_id(value))

    def set_hidden(self, hidden):
        self.view.hidden = bool(hidden)

    def layout(self, frame):
        self._frame = tuple(float(v) for v in frame)
        self.view.frame = self._frame

    @property
    def native_element_count(self):
        return 1


_PIXEL_TONES = {
    "neutral": (0.075, 0.105, 0.14, 0.88),
    "move": (0.075, 0.105, 0.14, 0.88),
    "jump": (0.055, 0.25, 0.47, 0.90),
    "stance": (0.25, 0.105, 0.36, 0.90),
    "action": (0.47, 0.075, 0.075, 0.90),
    "interact": (0.43, 0.285, 0.035, 0.90),
    "utility": (0.055, 0.085, 0.115, 0.92),
    "close": (0.30, 0.055, 0.065, 0.94),
    "backpack": (0.18, 0.105, 0.035, 0.94),
}


def apply_pixel_control_style(view, tone="neutral", transparent=False):
    """Apply the common hard-edged control frame without allocating children."""
    rgba = _PIXEL_TONES.get(str(tone or "neutral"), _PIXEL_TONES["neutral"])
    try:
        view.background_color = (
            ui.Color.rgb(0.0, 0.0, 0.0, 0.001)
            if transparent else ui.Color.rgb(*rgba)
        )
    except Exception:
        pass
    try:
        view.corner_radius = 3
        view.border_width = 2
        view.border_color = ui.Color.rgb(0.62, 0.75, 0.82, 0.98)
    except Exception:
        pass
    return view


def set_pixel_pressed(view, pressed):
    """Set one retained native layer's opacity; no PytoUI redraw is enqueued.

    Native UIControl callbacks already run on UIKit's main thread.  Writing a
    scalar opacity there is substantially cheaper than changing several PytoUI
    colors and still gives an immediate, consistent pressed state.
    """
    alpha = 0.56 if bool(pressed) else 1.0
    candidates = (
        getattr(view, "_native_control", None),
        getattr(view, "objc_view", None),
        getattr(getattr(view, "__py_view__", None), "managed", None),
    )
    for native in candidates:
        if native is None:
            continue
        try:
            native.layer.opacity = alpha
            return True
        except Exception:
            try:
                native.alpha = alpha
                return True
            except Exception:
                pass
    return False
