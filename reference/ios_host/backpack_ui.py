# -*- coding: utf-8 -*-
"""FIX62 constant-cost Metal backpack controller.

Inventory data stays in :class:`InventorySystem` and all mutations are queued
to the simulation thread. UIKit owns only four fixed objects regardless of
item count: the open button, its retained pixel icon, one transparent gesture
surface, and one cosmetic detail label. Slots, selection, counts, pages,
buttons, and item icons are a single Metal batch produced by
``MetalKitRenderer``.
"""
import threading

import mainthread
import pyto_ui as ui
from Foundation import NSObject
from UIKit import UIControl, UIPanGestureRecognizer, UITapGestureRecognizer
from rubicon.objc import SEL, objc_method

from ios_host.colors import WHITE
from ios_host.orientation import is_ipad
from ios_host.control_overlay import top_control_frames
from ios_host.control_icon_view import ControlIconWidget, apply_pixel_control_style


class _InventoryGestureTarget(NSObject):
    owner = None

    @objc_method
    def tapChanged(self):
        if self.owner is not None:
            self.owner._emit_tap()

    @objc_method
    def panChanged(self):
        if self.owner is not None:
            self.owner._emit_pan()


class _InventoryTouchSurface(ui.UIKitView):
    """One native control for every inventory hit target and page swipe."""

    def __init__(self, callback):
        self.callback = callback
        self._active = False
        self._target = None
        self._native_control = None
        self._tap = None
        self._pan = None
        super().__init__()
        try:
            self.background_color = ui.Color.rgb(0.0, 0.0, 0.0, 0.001)
        except Exception:
            pass

    def make_view(self):
        control = UIControl.alloc().init()
        # A hidden Pyto wrapper has historically not been sufficient on every
        # iOS/Pyto combination: its transparent native UIView could remain in
        # hit-testing for a frame.  Keep the native gate disabled explicitly
        # until the backpack is modal.
        try:
            control.userInteractionEnabled = False
            control.multipleTouchEnabled = False
            control.exclusiveTouch = True
        except Exception:
            pass
        target = _InventoryGestureTarget.alloc().init()
        target.owner = self
        tap = UITapGestureRecognizer.alloc().initWithTarget_action_(target, SEL("tapChanged"))
        pan = UIPanGestureRecognizer.alloc().initWithTarget_action_(target, SEL("panChanged"))
        try:
            pan.maximumNumberOfTouches = 1
        except Exception:
            pass
        # A completed page swipe must never also select the slot under the
        # release point.  A normal tap still resolves as soon as pan fails.
        try:
            tap.requireGestureRecognizerToFail_(pan)
        except Exception:
            pass
        control.addGestureRecognizer_(tap)
        control.addGestureRecognizer_(pan)
        self._target = target
        self._native_control = control
        self._tap = tap
        self._pan = pan
        return control

    def set_active(self, active):
        """Atomically gate both the Pyto wrapper and native hit testing."""
        value = bool(active)
        self._active = value
        try:
            self.user_interaction_enabled = value
        except Exception:
            pass
        control = self._native_control
        if control is not None:
            try:
                control.userInteractionEnabled = value
            except Exception:
                pass
        # Disabling an in-flight recognizer forces UIKit to cancel it.  This
        # prevents a swipe ending after close from changing the next page.
        for recognizer in (self._tap, self._pan):
            if recognizer is None:
                continue
            try:
                recognizer.enabled = value
            except Exception:
                try:
                    recognizer.setEnabled_(value)
                except Exception:
                    pass
        self.hidden = not value

    def _emit_tap(self):
        if self.callback is None or self._tap is None or self._native_control is None:
            return
        try:
            if int(self._tap.state) != 3:
                return
            point = self._tap.locationInView_(self._native_control)
            self.callback("tap", float(point.x), float(point.y), 0.0, 0.0)
        except Exception:
            pass

    def _emit_pan(self):
        if self.callback is None or self._pan is None or self._native_control is None:
            return
        try:
            if int(self._pan.state) != 3:
                return
            point = self._pan.locationInView_(self._native_control)
            delta = self._pan.translationInView_(self._native_control)
            self.callback("pan", float(point.x), float(point.y), float(delta.x), float(delta.y))
        except Exception:
            pass


class BackpackUI:
    PAGE_SIZE = 24
    COLS = 6
    ROWS = 4

    def __init__(self, root, game, renderer, on_equipment_changed=None, on_modal_changed=None):
        self.root = root
        self.game = game
        self.renderer = renderer
        self.on_equipment_changed = on_equipment_changed
        self.on_modal_changed = on_modal_changed
        self.opened = False
        self.page = 0
        self._lock = threading.RLock()
        self._layout_values = (852.0, 393.0)
        self._last_detail_signature = None
        self._last_overlay_signature = None
        self._pending_detail_text = None
        self._detail_apply_scheduled = False

        self.button = ui.Button(title="")
        apply_pixel_control_style(self.button, "backpack")
        self.button_icon = ControlIconWidget(self.button)
        self.button_icon.set_icon("control_backpack")
        self.button_icon.set_hidden(False)
        self.button.action = lambda _sender: self.show()
        self.root.add_subview(self.button)

        self.touch_surface = _InventoryTouchSurface(self._gesture)
        self.touch_surface.set_active(False)
        self.root.add_subview(self.touch_surface)

        # Cosmetic only: selected Chinese text stays legible while every
        # scalable slot/control is rendered and hit-tested without UIKit.
        self.detail_label = ui.Label(text="")
        self.detail_label.text_color = WHITE
        self.detail_label.background_color = ui.Color.rgb(0.02, 0.03, 0.05, 0.72)
        self.detail_label.font = ui.Font.bold_system_font_of_size(11)
        self.detail_label.text_alignment = ui.TextAlignment.CENTER
        self.detail_label.corner_radius = 6
        self.detail_label.user_interaction_enabled = False
        self.detail_label.hidden = True
        self.root.add_subview(self.detail_label)

    @property
    def native_element_count(self):
        """Stable proof used by diagnostics/tests; never scales with slots."""
        return 4

    def _top_safe_insets(self):
        """Mirror gameplay controls' conservative landscape safe area."""
        safe_top = 10.0
        safe_right = 16.0 if is_ipad() else 72.0
        try:
            native = self.root.__py_view__.managed
            insets = native.safeAreaInsets
            safe_top = max(safe_top, float(insets.top) + 6.0)
            safe_right = max(safe_right, float(insets.right) + 10.0)
        except Exception:
            pass
        return safe_top, safe_right

    def _geometry(self):
        w, h = self._layout_values
        pw = min(680.0, w - 36.0)
        ph = min(330.0, h - 28.0)
        px = (w - pw) * 0.5
        py = (h - ph) * 0.5
        grid_x, grid_y = px + 14.0, py + 47.0
        grid_w = pw * 0.68
        gap = 5.0
        sw = (grid_w - gap * (self.COLS - 1)) / self.COLS
        sh = (ph - 63.0 - gap * (self.ROWS - 1)) / self.ROWS
        rx = grid_x + grid_w + 14.0
        rw = pw - (rx - px) - 12.0
        rh = ph - 61.0
        detail_h = max(32.0, min(64.0, rh * 0.24))
        action_start = detail_h + 18.0
        button_h = max(14.0, min(32.0, (rh - action_start - 20.0) / 5.0))
        equip_y = action_start + button_h + 8.0
        step = button_h + 4.0
        return {
            "panel": (px, py, pw, ph), "grid": (grid_x, grid_y, grid_w, ph - 63.0),
            "cell": (sw, sh, gap), "right": (rx, py + 47.0, rw, rh),
            "detail_h": detail_h, "button_h": button_h,
            "actions": {
                "qty": action_start, "inventory_equip": equip_y,
                "inventory_move": equip_y + step,
                "inventory_drop": equip_y + step * 2.0,
                "inventory_remove": equip_y + step * 3.0,
            },
            "close": (px + pw - 48.0, py + 7.0, 38.0, 32.0),
            "prev": (px + 118.0, py + 9.0, 34.0, 28.0),
            "next": (px + 222.0, py + 9.0, 34.0, 28.0),
        }

    @staticmethod
    def _inside(x, y, rect):
        rx, ry, rw, rh = rect
        return rx <= x <= rx + rw and ry <= y <= ry + rh

    def _post(self, name, **payload):
        try:
            self.game.enqueue_ui_command(name, **payload)
        except Exception as exc:
            print("INVENTORY command warning:", name, repr(exc))

    def _gesture(self, kind, x, y, dx, dy):
        if not self.opened:
            return
        if kind == "pan":
            if abs(dx) >= 52.0 and abs(dx) > abs(dy) * 1.25:
                self._change_page(-1 if dx > 0.0 else 1)
            return

        g = self._geometry()
        if self._inside(x, y, g["close"]):
            self.hide()
            return
        if self._inside(x, y, g["prev"]):
            self._change_page(-1)
            return
        if self._inside(x, y, g["next"]):
            self._change_page(1)
            return

        gx, gy, _gw, _gh = g["grid"]
        sw, sh, gap = g["cell"]
        if gx <= x <= gx + self.COLS * sw + (self.COLS - 1) * gap and gy <= y:
            col = int((x - gx) // (sw + gap))
            row = int((y - gy) // (sh + gap))
            if 0 <= col < self.COLS and 0 <= row < self.ROWS:
                bx = gx + col * (sw + gap)
                by = gy + row * (sh + gap)
                if bx <= x <= bx + sw and by <= y <= by + sh:
                    index = self.page * self.PAGE_SIZE + row * self.COLS + col
                    if index < int(self.game.inventory.SLOT_COUNT):
                        self._post("inventory_select", index=index)
                    return

        rx, ry, rw, _rh = g["right"]
        third = (rw - 8.0) / 3.0
        qty_y = float(g["actions"]["qty"])
        button_h = float(g["button_h"])
        if ry + qty_y <= y <= ry + qty_y + button_h:
            if rx <= x <= rx + third:
                self._post("inventory_adjust", delta=-1)
            elif rx + third + 4.0 <= x <= rx + third * 2.0 + 4.0:
                self._post("inventory_adjust", delta=1)
            elif rx + (third + 4.0) * 2.0 <= x <= rx + rw:
                self._post("inventory_max")
            return
        # EQUIP and UNEQUIP share the existing action row.  They remain Metal
        # geometry hit regions on this one fixed touch surface; no per-item or
        # per-action UIKit views are allocated.
        equip_offset = float(g["actions"]["inventory_equip"])
        equip_gap = 4.0
        equip_half = (rw - equip_gap) * 0.5
        if self._inside(x, y, (rx, ry + equip_offset, equip_half, button_h)):
            self._post("inventory_equip")
            return
        if self._inside(
            x, y,
            (rx + equip_half + equip_gap, ry + equip_offset, equip_half, button_h),
        ):
            self._post("inventory_unequip")
            return
        for name in ("inventory_move", "inventory_drop", "inventory_remove"):
            offset = float(g["actions"][name])
            if self._inside(x, y, (rx, ry + offset, rw, button_h)):
                self._post(name)
                return

    def _page_count(self):
        return max(1, (int(self.game.inventory.SLOT_COUNT) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _change_page(self, delta):
        with self._lock:
            self.page = max(0, min(self._page_count() - 1, int(self.page) + int(delta)))
        self.refresh_from_game(force=True, follow_selection=False)

    def _detail_text(self, snap):
        slots = list(snap.get("slots", ()) or ())
        index = int(snap.get("selected_index", 0))
        row = slots[index] if 0 <= index < len(slots) else None
        if not row:
            return "空欄位"
        item_id=str(row.get("item_id", "") or "")
        equipped = item_id == str(getattr(self.game.melee, "selected_item_id", ""))
        slot_label=""
        try:
            gear=self.game.equipment.snapshot().get("slots", {})
            slot=next((key for key,value in gear.items() if str(value)==item_id), "")
            slot_label={"foot":"腳部","body":"身體","head":"頭部"}.get(slot, "")
            equipped=equipped or bool(slot_label)
        except Exception:
            pass
        suffix = ("｜%s裝備中" % slot_label) if slot_label else ("｜裝備中" if equipped else "")
        if item_id == "equipment.wind_god_wings":
            import math
            seconds = int(math.ceil(self.game.equipment.wing_state()["remaining_seconds"]))
            suffix += "｜本件剩餘 %d／300 秒" % seconds
        from systems.boss_weapon_defs import BOSS_ITEM_LIMITS
        if item_id in BOSS_ITEM_LIMITS:
            limit = BOSS_ITEM_LIMITS[item_id]
            values = row.get("heavy_uses") or []
            remaining = int(values[0]) if values else limit
            if item_id == "weapon_bee_swarm":
                suffix += "｜每組8隻・重擊耗用一組"
            elif limit:
                suffix += "｜重擊剩餘 %d／%d 次" % (remaining, limit)
            else:
                suffix += "｜不限制重擊次數"
        return "%s ×%d%s" % (str(row.get("name", "物品")), int(row.get("count", 0)), suffix)

    @staticmethod
    def _stack_signature(row):
        if not row:
            return None
        return (
            str(row.get("item_id", "")),
            int(row.get("count", 0)),
            int(row.get("max_stack", 0)),
            tuple((row.get("heavy_uses") or ())[:1]),
        )

    def _overlay_signature(self, snap, page):
        # Only the visible 24 slots affect this Metal overlay.  Keeping the
        # signature page-local means future inventories can grow to hundreds of
        # slots without making a selection scan/rebuild proportional to total
        # capacity.
        slots = list(snap.get("slots", ()) or ())
        start = int(page) * self.PAGE_SIZE
        visible = tuple(
            self._stack_signature(slots[i]) if 0 <= i < len(slots) else None
            for i in range(start, start + self.PAGE_SIZE)
        )
        try:
            equipped_gear=tuple(self.game.equipment.player_slot_ids())
        except Exception:
            equipped_gear=tuple()
        return (
            int(page),
            int(snap.get("selected_index", 0)),
            int(snap.get("selected_quantity", 1)),
            snap.get("move_source"),
            str(getattr(self.game.melee, "selected_item_id", "")),
            equipped_gear,
            visible,
            tuple(round(float(v), 2) for v in self._layout_values),
        )

    def _schedule_detail_text(self, text):
        """Keep at most one pending UIKit label transaction.

        Slot taps can arrive faster than Pyto's main thread applies Label.text.
        FIX66 stores only the latest value and never queues one run_async task
        per selection.
        """
        with self._lock:
            self._pending_detail_text = str(text or "")
            if self._detail_apply_scheduled:
                return False
            self._detail_apply_scheduled = True

        def apply_latest():
            with self._lock:
                value = self._pending_detail_text
                self._pending_detail_text = None
                self._detail_apply_scheduled = False
                opened = bool(self.opened)
            if opened and value is not None:
                try:
                    self.detail_label.text = value
                except Exception:
                    pass

        try:
            mainthread.run_async(apply_latest)
            return True
        except Exception:
            with self._lock:
                self._detail_apply_scheduled = False
            return False

    def refresh_from_game(self, force=False, follow_selection=True, snapshot=None):
        if not self.opened:
            return False
        snap = snapshot if isinstance(snapshot, dict) else self.game.inventory.snapshot()
        selected = int(snap.get("selected_index", 0))
        with self._lock:
            if follow_selection and not (self.page * self.PAGE_SIZE <= selected < (self.page + 1) * self.PAGE_SIZE):
                self.page = max(0, min(self._page_count() - 1, selected // self.PAGE_SIZE))
            page = int(self.page)

        overlay_sig = self._overlay_signature(snap, page)
        overlay_changed = bool(force) or overlay_sig != self._last_overlay_signature
        if overlay_changed:
            self._last_overlay_signature = overlay_sig
            self.renderer.publish_inventory_overlay(snap, page)

        text = self._detail_text(snap)
        sig = (text, page)
        if force or sig != self._last_detail_signature:
            self._last_detail_signature = sig
            self._schedule_detail_text(text)
        return overlay_changed

    def show(self):
        # Do not let a queued backpack tap cover the mandatory learning modal.
        if getattr(getattr(self.game, "study", None), "active", False):
            return
        try:
            self.game.input.reset()
        except Exception:
            pass
        snap = self.game.inventory.snapshot()
        selected = int(snap.get("selected_index", 0))
        with self._lock:
            self.page = max(0, min(self._page_count() - 1, selected // self.PAGE_SIZE))
            self.opened = True
        self.button.hidden = True
        self.touch_surface.set_active(True)
        self.detail_label.hidden = False
        if self.on_modal_changed is not None:
            self.on_modal_changed(True)
        # Reuse the snapshot already obtained above; FIX63 copied all 72 slots
        # twice on every open before drawing the first frame.
        self._last_overlay_signature = None
        self.refresh_from_game(force=True, snapshot=snap)

    def hide(self):
        with self._lock:
            self.opened = False
        self._post("inventory_cancel_move")
        try:
            self.game.input.reset()
        except Exception:
            pass
        self.renderer.clear_inventory_overlay()
        self._last_overlay_signature = None
        self.button.hidden = False
        self.touch_surface.set_active(False)
        self.detail_label.hidden = True
        if self.on_modal_changed is not None:
            self.on_modal_changed(False)

    def refresh(self):
        return self.refresh_from_game(force=True)

    def layout(self, w, h):
        with self._lock:
            self._layout_values = (max(1.0, float(w)), max(1.0, float(h)))
        safe_top, safe_right = self._top_safe_insets()
        frame = top_control_frames(
            w, safe_top, safe_right, ipad=is_ipad(),
        )["backpack"]
        self.button.frame = frame
        inset = max(5.0, min(float(frame[2]), float(frame[3])) * 0.16)
        self.button_icon.layout((
            inset,
            inset,
            max(1.0, float(frame[2]) - inset * 2.0),
            max(1.0, float(frame[3]) - inset * 2.0),
        ))
        self.touch_surface.frame = (0.0, 0.0, w, h)
        g = self._geometry()
        rx, ry, rw, _rh = g["right"]
        self.detail_label.frame = (rx, ry + 2.0, rw, float(g["detail_h"]))
        if self.opened:
            self.refresh_from_game(force=True)
