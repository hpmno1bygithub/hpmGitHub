# -*- coding: utf-8 -*-
"""Native hold-repeat button for map-editor viewport panning."""

import threading
import time

import pyto_ui as ui
from Foundation import NSObject
from UIKit import UIControl
from rubicon.objc import objc_method, SEL

from config import MAP_EDITOR_PAN_TILES_PER_SECOND
from ios_host.colors import color
from ios_host.haptics import light as haptic_light


CONTROL_TOUCH_DOWN = 1
CONTROL_TOUCH_UP_INSIDE = 64
CONTROL_TOUCH_UP_OUTSIDE = 128
CONTROL_TOUCH_CANCEL = 256
CONTROL_TOUCH_DRAG_EXIT = 32

WHITE = color("#FFFFFF")


class _PanTarget(NSObject):
    owner = None

    @objc_method
    def touchDown(self):
        if self.owner is not None:
            self.owner._start_hold()

    @objc_method
    def touchUpInside(self):
        if self.owner is not None:
            self.owner._stop_hold()

    @objc_method
    def touchUpOutside(self):
        if self.owner is not None:
            self.owner._stop_hold()

    @objc_method
    def touchCancel(self):
        if self.owner is not None:
            self.owner._stop_hold()

    @objc_method
    def touchDragExit(self):
        if self.owner is not None:
            self.owner._stop_hold()


class EditorPanControl(ui.UIKitView):
    """A native UIControl that repeats viewport movement while held."""

    def __init__(
        self,
        title,
        dx,
        dy,
        callback,
        bg,
    ):
        self.title_text = str(title)
        self.dx = int(dx)
        self.dy = int(dy)
        self.callback = callback
        self.bg = bg
        self._held = False
        self._target = None
        self._native_control = None
        self._thread = None

        super().__init__()

        self.background_color = bg
        self.corner_radius = 7
        self.border_width = 1
        self.border_color = WHITE

        self.label = ui.Label(self.title_text)
        self.label.text_color = WHITE
        self.label.font = ui.Font.bold_system_font_of_size(14)
        try:
            self.label.text_alignment = ui.TextAlignment.CENTER
        except Exception:
            self.label.text_alignment = ui.TEXT_ALIGNMENT_CENTER
        self.label.user_interaction_enabled = False
        self.add_subview(self.label)

    def make_view(self):
        control = UIControl.alloc().init()
        target = _PanTarget.alloc().init()
        target.owner = self

        self._native_control = control
        self._target = target

        for selector, event in (
            ("touchDown", CONTROL_TOUCH_DOWN),
            ("touchUpInside", CONTROL_TOUCH_UP_INSIDE),
            ("touchUpOutside", CONTROL_TOUCH_UP_OUTSIDE),
            ("touchCancel", CONTROL_TOUCH_CANCEL),
            ("touchDragExit", CONTROL_TOUCH_DRAG_EXIT),
        ):
            control.addTarget(
                target,
                action=SEL(selector),
                forControlEvents=event,
            )

        return control

    def set_control_frame(self, frame):
        self.frame = frame
        self.label.frame = (0, 0, frame[2], frame[3])

    def _start_hold(self):
        if self._held:
            return

        self._held = True
        self.background_color = WHITE
        self.label.text_color = self.bg
        haptic_light()

        # Immediate one-tile response makes the control useful as both tap and
        # hold; the repeated movement then follows real hold duration.
        try:
            self.callback(self.dx, self.dy)
        except Exception:
            pass

        interval = 1.0 / max(1.0, MAP_EDITOR_PAN_TILES_PER_SECOND)

        def loop():
            # Small initial delay prevents a single tap from jumping 2 cells.
            time.sleep(max(0.10, interval * 1.25))
            while self._held:
                try:
                    self.callback(self.dx, self.dy)
                except Exception:
                    pass
                time.sleep(interval)

        self._thread = threading.Thread(
            target=loop,
            daemon=True,
        )
        self._thread.start()

    def _stop_hold(self):
        self._held = False
        self.background_color = self.bg
        self.label.text_color = WHITE

    def force_stop(self):
        self._stop_hold()
