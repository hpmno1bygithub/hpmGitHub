# -*- coding: utf-8 -*-
"""FIX62 cached pixel weapon icons.

The artwork follows the accepted pixel design, pre-rasterised as nine
small PNG assets. Each on-screen icon is exactly ONE retained UIImageView.
No per-pixel UIView children are created, and changing a slot only swaps a
cached UIImage reference.
"""
import os
import pyto_ui as ui
from UIKit import UIImageView, UIImage
from ios_host.objc_view import WrapperView
from systems.weapon_catalog import weapon_from_item

_ICON_FILES = {
    "sword": "sword.png",
    "dagger": "dagger.png",
    "spear": "spear.png",
    "battle_axe": "battle_axe.png",
    "war_hammer": "war_hammer.png",
    "greatsword": "greatsword.png",
    "energy_bow": "energy_bow.png",
    "whip": "whip.png",
    "laser_gun": "laser_gun.png",
    "flying_drone": "flying_drone.png",
    "yoyo": "yoyo.png",
    "battle_top": "battle_top.png",
    "rpg_launcher": "rpg_launcher.png",
    "tnt": "tnt.png",
}
from systems.boss_weapon_defs import BOSS_WEAPON_DEFS
_ICON_FILES.update({key: key+".png" for key in BOSS_WEAPON_DEFS})
_IMAGE_CACHE = {}


def weapon_id_for(value):
    key = str(value or "")
    if key in _ICON_FILES:
        return key
    return weapon_from_item(key)


def _icon_path(weapon_id):
    root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "").strip()
    if not root:
        return ""
    return os.path.join(root, "assets", "weapon_icons", _ICON_FILES.get(str(weapon_id), ""))


def _load_image(weapon_id):
    wid = str(weapon_id or "")
    if not wid:
        return None
    if wid in _IMAGE_CACHE:
        return _IMAGE_CACHE[wid]
    path = _icon_path(wid)
    image = None
    if path and os.path.isfile(path):
        try:
            image = UIImage.imageWithContentsOfFile(path)
        except Exception:
            try:
                image = UIImage.imageWithContentsOfFile_(path)
            except Exception:
                image = None
    _IMAGE_CACHE[wid] = image
    return image


def preload_weapon_images():
    """Load all tiny icons once before the first inventory presentation."""
    for wid in _ICON_FILES:
        _load_image(wid)


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
        # Keep nearest-neighbour pixel edges where QuartzCore accepts the
        # standard filter strings. Failure is harmless on older Pyto builds.
        try:
            view.layer.magnificationFilter = "nearest"
            view.layer.minificationFilter = "nearest"
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


class WeaponIconWidget:
    """One retained UIImageView. No allocation occurs in set_weapon()."""
    def __init__(self, parent, max_parts=None):
        self.view = _PixelUIImageView()
        self.view.background_color = ui.Color.rgb(0, 0, 0, 0)
        self.view.user_interaction_enabled = False
        self.view.hidden = True
        parent.add_subview(self.view)
        self.weapon_id = ""
        self._frame = (0.0, 0.0, 1.0, 1.0)

    def set_weapon(self, value):
        wid = weapon_id_for(value)
        if wid == self.weapon_id:
            return
        self.weapon_id = wid
        self.view.set_image(_load_image(wid))

    def set_hidden(self, hidden):
        self.view.hidden = bool(hidden)

    def layout(self, frame):
        self._frame = tuple(float(v) for v in frame)
        self.view.frame = self._frame

    @property
    def native_element_count(self):
        return 1
