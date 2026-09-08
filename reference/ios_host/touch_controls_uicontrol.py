# -*- coding: utf-8 -*-
"""Per-button native UIKit UIControl input for Pyto.

This follows Pyto's documented UIKitView + addTarget(...forControlEvents...)
pattern instead of using a LongPress GestureRecognizer.

Important:
    This is NOT a full-screen transparent native overlay.
    Every control owns only its own button rectangle.
"""

import time
import math
import pyto_ui as ui

from Foundation import NSObject
from UIKit import UIControl, UIPanGestureRecognizer
from rubicon.objc import objc_method, SEL

from config import (
    AIM_JOYSTICK_RADIUS,
    AIM_JOYSTICK_DEADZONE,
)

from ios_host.orientation import is_ipad
from ios_host.control_overlay import selector_frames, top_control_frames
from ios_host.control_icon_view import (
    ControlIconWidget,
    apply_pixel_control_style,
    set_pixel_pressed,
)

from ios_host.haptics import (
    for_action as haptic_for_action,
    selection as haptic_selection,
    light as haptic_light,
)

from ios_host.colors import (
    WHITE,
    DARK,
    DARK2,
    BLUE,
    PURPLE,
    RED,
    GOLD,
)


def _magic_button_color(value):
    key = str(value or "").strip()
    if key in ("water", "waterball", "水球"):
        return BLUE
    if key in ("ice", "iceball", "冰球"):
        return ui.Color.rgb(0.35, 0.75, 0.95, 1.0)
    if key in ("electric", "electricball", "lightning", "電球"):
        return ui.Color.rgb(0.94, 0.76, 0.16, 1.0)
    return RED


# UIControl.Event raw values documented by UIKit.
CONTROL_TOUCH_DOWN = 1
CONTROL_TOUCH_DRAG_ENTER = 16
CONTROL_TOUCH_DRAG_EXIT = 32
CONTROL_TOUCH_UP_INSIDE = 64
CONTROL_TOUCH_UP_OUTSIDE = 128
CONTROL_TOUCH_CANCEL = 256


class _HoldTarget(NSObject):
    """Objective-C target object retained by UIKitHoldControl."""

    owner = None

    @objc_method
    def touchDown(self):
        owner = self.owner
        if owner is not None:
            owner._platform_down(
                "touchDown"
            )

    @objc_method
    def touchDragEnter(self):
        owner = self.owner
        if owner is not None:
            owner._platform_down(
                "dragEnter"
            )

    @objc_method
    def touchDragExit(self):
        owner = self.owner
        if owner is not None:
            owner._platform_up(
                "dragExit"
            )

    @objc_method
    def touchUpInside(self):
        owner = self.owner
        if owner is not None:
            owner._platform_up(
                "upInside"
            )

    @objc_method
    def touchUpOutside(self):
        owner = self.owner
        if owner is not None:
            owner._platform_up(
                "upOutside"
            )

    @objc_method
    def touchCancel(self):
        owner = self.owner
        if owner is not None:
            owner._platform_up(
                "cancel"
            )


class UIKitHoldControl(ui.UIKitView):
    """One gameplay hold button backed by native UIControl events."""

    def __init__(
        self,
        icon_id,
        action,
        input_manager,
        bg,
        font_size=16,
        low_latency=False,
    ):
        # make_view() is called by UIKitView initialization, therefore these
        # fields must exist before super().__init__().
        self.action_name = str(
            action
        )
        self.input = input_manager
        self.bg = bg
        self.font_size = float(
            font_size
        )
        self.icon_id = str(icon_id or "action_unknown")
        self.low_latency = bool(low_latency)

        self._target = None
        self._native_control = None
        self._pressed_visual = False

        super().__init__()

        tone = {
            "jump": "jump",
            "crouch": "stance",
            "attack": "action",
            "interact": "interact",
        }.get(self.action_name, "move" if self.action_name.startswith("move_") else "neutral")
        apply_pixel_control_style(self, tone)
        self.icon = ControlIconWidget(self)
        self.icon.set_icon(self.icon_id)
        self.icon.set_hidden(False)
        # Kept as a compatibility attribute for TouchControls' existing control
        # dictionaries.  No text label is created.
        self.label = None

    def make_view(self):
        control = UIControl.alloc().init()

        # Each button handles one finger. Different UIControls can receive
        # simultaneous touches, which is exactly what jump + direction needs.
        try:
            control.multipleTouchEnabled = False
        except Exception:
            pass

        try:
            control.exclusiveTouch = False
        except Exception:
            pass

        target = _HoldTarget.alloc().init()
        target.owner = self

        # Retain both objects strongly from Python. UIControl target/action
        # should not be relied upon to retain the target.
        self._target = target
        self._native_control = control

        control.addTarget(
            target,
            action=SEL(
                "touchDown"
            ),
            forControlEvents=CONTROL_TOUCH_DOWN,
        )

        control.addTarget(
            target,
            action=SEL(
                "touchDragEnter"
            ),
            forControlEvents=CONTROL_TOUCH_DRAG_ENTER,
        )

        control.addTarget(
            target,
            action=SEL(
                "touchDragExit"
            ),
            forControlEvents=CONTROL_TOUCH_DRAG_EXIT,
        )

        control.addTarget(
            target,
            action=SEL(
                "touchUpInside"
            ),
            forControlEvents=CONTROL_TOUCH_UP_INSIDE,
        )

        control.addTarget(
            target,
            action=SEL(
                "touchUpOutside"
            ),
            forControlEvents=CONTROL_TOUCH_UP_OUTSIDE,
        )

        control.addTarget(
            target,
            action=SEL(
                "touchCancel"
            ),
            forControlEvents=CONTROL_TOUCH_CANCEL,
        )

        return control

    def _set_pressed_visual(
        self,
        pressed,
    ):
        self._pressed_visual = bool(pressed)
        set_pixel_pressed(self, pressed)

    def _platform_down(
        self,
        event_name,
    ):
        # The UIControl itself is the authoritative source of physical
        # down/up state.
        self.input.note_platform_event(
            self.action_name,
            event_name,
        )
        self._set_pressed_visual(True)

        # No pressed/highlight visual. Direction keys and 動作 use the
        # pre-warmed native haptic path; other non-low-latency controls keep
        # their existing haptic semantics.
        if self.action_name in (
            "move_left", "move_right", "move_up", "move_down", "attack"
        ):
            haptic_light()
        elif not self.low_latency:
            haptic_for_action(self.action_name)

        self.input.press(
            self.action_name
        )

    def _platform_up(
        self,
        event_name,
    ):
        self.input.note_platform_event(
            self.action_name,
            event_name,
        )
        self._set_pressed_visual(False)

        self.input.release(
            self.action_name
        )

    def force_release(self):
        self.input.note_platform_event(
            self.action_name,
            "forceRelease",
        )

        self.input.release(
            self.action_name
        )
        self._set_pressed_visual(False)

    def set_control_frame(
        self,
        frame,
    ):
        self.frame = frame
        inset = max(5.0, min(float(frame[2]), float(frame[3])) * 0.16)
        self.icon.layout((
            inset,
            inset,
            max(1.0, float(frame[2]) - inset * 2.0),
            max(1.0, float(frame[3]) - inset * 2.0),
        ))



class _TapTarget(NSObject):
    """Retained target for one-shot selector buttons."""

    owner = None

    @objc_method
    def touchDown(self):
        owner = self.owner
        if owner is not None:
            owner._platform_down()

    @objc_method
    def touchUpInside(self):
        owner = self.owner
        if owner is not None:
            owner._platform_tap()

    @objc_method
    def touchUpOutside(self):
        owner = self.owner
        if owner is not None:
            owner._platform_cancel()

    @objc_method
    def touchCancel(self):
        owner = self.owner
        if owner is not None:
            owner._platform_cancel()


class UIKitTapControl(ui.UIKitView):
    """Transparent native hit target with no mutable UIKit artwork.

    FIX66 draws the tool/magic/weapon symbols in Metal.  The native object only
    receives touchUpInside and publishes one command, so repeated switching can
    never build an UIImage/UIButton update backlog on the iOS main thread.
    """

    def __init__(
        self, callback, minimum_interval=0.040, icon_id=None, tone="utility",
    ):
        self.callback = callback
        self.minimum_interval = max(0.0, float(minimum_interval))
        self.icon_id = str(icon_id or "")
        self.tone = str(tone or "utility")
        self.visible_control = bool(self.icon_id)
        self._last_tap = -1.0e9
        self._target = None
        self._native_control = None
        super().__init__()
        apply_pixel_control_style(
            self, self.tone, transparent=not self.visible_control,
        )
        if not self.visible_control:
            self.border_width = 0
            self.icon = None
        else:
            self.icon = ControlIconWidget(self)
            self.icon.set_icon(self.icon_id)
            self.icon.set_hidden(False)

    def make_view(self):
        control = UIControl.alloc().init()
        try:
            control.multipleTouchEnabled = False
            control.exclusiveTouch = False
            control.userInteractionEnabled = True
        except Exception:
            pass
        target = _TapTarget.alloc().init()
        target.owner = self
        self._target = target
        self._native_control = control
        control.addTarget(
            target,
            action=SEL("touchDown"),
            forControlEvents=CONTROL_TOUCH_DOWN,
        )
        control.addTarget(
            target,
            action=SEL("touchUpInside"),
            forControlEvents=CONTROL_TOUCH_UP_INSIDE,
        )
        control.addTarget(
            target,
            action=SEL("touchUpOutside"),
            forControlEvents=CONTROL_TOUCH_UP_OUTSIDE,
        )
        control.addTarget(
            target,
            action=SEL("touchCancel"),
            forControlEvents=CONTROL_TOUCH_CANCEL,
        )
        return control

    def _platform_down(self):
        if self.visible_control:
            set_pixel_pressed(self, True)

    def _platform_cancel(self):
        if self.visible_control:
            set_pixel_pressed(self, False)

    def _platform_tap(self):
        if self.visible_control:
            set_pixel_pressed(self, False)
        now = time.monotonic()
        if now - float(self._last_tap) < self.minimum_interval:
            return
        self._last_tap = now
        callback = self.callback
        if callback is not None:
            if self.visible_control:
                callback(self)
            else:
                callback()

    def set_control_frame(self, frame):
        self.frame = frame
        if self.icon is not None:
            inset = max(5.0, min(float(frame[2]), float(frame[3])) * 0.16)
            self.icon.layout((
                inset,
                inset,
                max(1.0, float(frame[2]) - inset * 2.0),
                max(1.0, float(frame[3]) - inset * 2.0),
            ))

    def force_release(self):
        self._platform_cancel()


class _AimTarget(NSObject):
    owner = None

    @objc_method
    def touchDown(self):
        owner = self.owner
        if owner is not None:
            owner._aim_down()

    @objc_method
    def touchUpInside(self):
        owner = self.owner
        if owner is not None:
            owner._aim_up(
                fire=True
            )

    @objc_method
    def touchUpOutside(self):
        owner = self.owner
        if owner is not None:
            owner._aim_up(
                fire=True
            )

    @objc_method
    def touchCancel(self):
        owner = self.owner
        if owner is not None:
            owner._aim_up(
                fire=False
            )

    @objc_method
    def panChanged(self):
        owner = self.owner
        if owner is not None:
            owner._aim_pan_changed()


class UIKitAimJoystick(ui.UIKitView):
    """Bounded right-stick aiming control.

    UIControl handles reliable down/up/cancel events. A native
    UIPanGestureRecognizer only supplies analog displacement; it never owns
    persistent gameplay state and cannot leave the player walking/aiming.
    """

    def __init__(
        self,
        on_begin,
        on_axis,
        on_release,
    ):
        self.on_begin = on_begin
        self.on_axis = on_axis
        self.on_release = on_release

        self._target = None
        self._native_control = None
        self._pan = None
        self._active = False
        self._started = 0.0
        self._has_direction = False
        self._radius = float(
            AIM_JOYSTICK_RADIUS
        )
        # V0.6.5.2: knob remains a normal absolute-direction joystick. The
        # crosshair transition is smoothed on the game thread, not here.

        super().__init__()

        apply_pixel_control_style(self, "utility")

        self.knob = ui.View()
        self.knob.background_color = ui.Color.rgb(
            0.78,
            0.86,
            0.92,
            0.88,
        )
        self.knob.border_color = ui.Color.rgb(0.62, 0.75, 0.82, 0.98)
        self.knob.border_width = 2
        self.knob.user_interaction_enabled = False
        self.add_subview(
            self.knob
        )

        self.icon = ControlIconWidget(self)
        self.icon.set_icon("control_aim")
        self.icon.set_hidden(False)
        self.label = None

    def make_view(self):
        control = UIControl.alloc().init()
        try:
            control.exclusiveTouch = False
        except Exception:
            pass

        target = _AimTarget.alloc().init()
        target.owner = self
        self._target = target
        self._native_control = control

        control.addTarget(
            target,
            action=SEL("touchDown"),
            forControlEvents=CONTROL_TOUCH_DOWN,
        )
        control.addTarget(
            target,
            action=SEL("touchUpInside"),
            forControlEvents=CONTROL_TOUCH_UP_INSIDE,
        )
        control.addTarget(
            target,
            action=SEL("touchUpOutside"),
            forControlEvents=CONTROL_TOUCH_UP_OUTSIDE,
        )
        control.addTarget(
            target,
            action=SEL("touchCancel"),
            forControlEvents=CONTROL_TOUCH_CANCEL,
        )

        pan = UIPanGestureRecognizer.alloc().initWithTarget_action_(
            target,
            SEL("panChanged"),
        )
        try:
            pan.maximumNumberOfTouches = 1
        except Exception:
            pass
        try:
            pan.cancelsTouchesInView = False
        except Exception:
            pass

        control.addGestureRecognizer_(
            pan
        )
        self._pan = pan
        return control

    def set_control_frame(
        self,
        frame,
    ):
        self.frame = frame
        w = float(frame[2])
        h = float(frame[3])
        self.corner_radius = 3

        knob_size = min(w, h) * 0.38
        self.knob.frame = (
            (w - knob_size) * 0.5,
            (h - knob_size) * 0.5,
            knob_size,
            knob_size,
        )
        self.knob.corner_radius = 2
        icon_inset = min(w, h) * 0.12
        self.icon.layout((
            icon_inset,
            icon_inset,
            max(1.0, w - icon_inset * 2.0),
            max(1.0, h - icon_inset * 2.0),
        ))

    def _center_knob(self):
        w = float(self.width)
        h = float(self.height)
        self.knob.center = (
            w * 0.5,
            h * 0.5,
        )

    def _aim_down(self):
        if self._active:
            return
        self._active = True
        self._started = time.monotonic()
        self._has_direction = False
        # No aim-start haptic: on Pyto this main-thread bridge can create a
        # noticeable first-touch hitch. The right stick should feel continuous.
        if self.on_begin is not None:
            self.on_begin()

    def _aim_pan_changed(self):
        if self._pan is None:
            return

        try:
            pan_state = int(self._pan.state)
        except Exception:
            pan_state = 2
        if pan_state in (4, 5):
            self._aim_up(fire=False)
            return
        if not self._active and pan_state == 3:
            return
        if not self._active:
            # A pan can begin after the touch-down target/action on some iOS
            # versions, but this guard keeps the state well-defined.
            self._aim_down()

        try:
            point = self._pan.translationInView_(
                self._native_control
            )
            dx = float(point.x)
            dy = float(point.y)
        except Exception:
            return

        radius = max(
            1.0,
            min(
                self._radius,
                min(
                    float(self.width),
                    float(self.height),
                ) * 0.42,
            ),
        )

        length = math.hypot(
            dx,
            dy,
        )
        if length > radius:
            scale = radius / length
            dx *= scale
            dy *= scale
            length = radius

        # Visual knob follows the finger. Gameplay receives the ABSOLUTE
        # normalized knob direction; GameApp performs the soft angular catch-up.
        self.knob.center = (
            float(self.width) * 0.5 + dx,
            float(self.height) * 0.5 + dy,
        )

        if length / radius < AIM_JOYSTICK_DEADZONE:
            if getattr(self, "movement_stick", False) and self.on_axis is not None:
                self.on_axis(0.0, 0.0)
            if pan_state == 3:
                self._aim_up(fire=True)
            return

        abs_x = dx / radius
        abs_y = dy / radius
        self._has_direction = True

        if self.on_axis is not None:
            self.on_axis(
                abs_x,
                abs_y,
            )
        if pan_state == 3:
            self._aim_up(fire=True)

    def _aim_up(
        self,
        fire=True,
    ):
        if not self._active:
            return

        held = max(
            0.0,
            time.monotonic()
            - self._started,
        )

        # FIX85: left is movement-only; right may request one ranged shot.
        # Touch cancel, forced reset, or a center-only tap never requests fire.
        release_fire = bool(
            fire and not getattr(self, "movement_stick", False)
            and self._has_direction
        )
        self._active = False
        self._started = 0.0
        self._has_direction = False
        self._center_knob()

        # Keep the last selected aim when the right knob recenters. Movement
        # release independently clears its own axes in InputManager.
        if self.on_release is not None:
            self.on_release(held, release_fire)

    def force_release(self):
        self._aim_up(
            fire=False,
        )


class UIKitMovementJoystick(UIKitAimJoystick):
    """FIX83 circular mushroom stick, reusing retained target/action ownership."""
    def __init__(self, input_manager):
        self.movement_stick = True
        self.input_manager = input_manager
        super().__init__(input_manager.begin_move_stick,
                         input_manager.set_move_stick,
                         input_manager.end_move_stick)
        self.icon.set_icon("control_move")
        self._radius = 100.0

    def set_control_frame(self, frame):
        super().set_control_frame(frame)
        size = min(float(frame[2]), float(frame[3]))
        self.corner_radius = size * 0.5
        self.knob.corner_radius = size * 0.19


class UIKitUIControlTouchControls:
    """Full game control layout using per-button native UIControls."""

    def __init__(
        self,
        root,
        input_manager,
        on_close,
        on_save=None,
        on_load=None,
        on_new_game=None,
        on_debug_water=None,
        on_debug_fire=None,
        on_toggle_magic=None,
        on_toggle_tool=None,
        on_toggle_action_mode=None,
        on_get_action_mode_state=None,
        on_aim_begin=None,
        on_aim_axis=None,
        on_aim_release=None,
    ):
        self.root = root
        self.input = input_manager
        self.on_close = on_close
        self.on_save = on_save
        self.on_load = on_load
        self.on_new_game = on_new_game
        self.on_toggle_magic = on_toggle_magic
        self.on_toggle_tool = on_toggle_tool
        self.on_toggle_action_mode = on_toggle_action_mode
        self.on_get_action_mode_state = on_get_action_mode_state
        self.on_aim_begin = on_aim_begin
        self.on_aim_axis = on_aim_axis
        self.on_aim_release = on_aim_release

        self.backend_name = (
            "UIKit UIControl"
        )

        self.controls = {}
        self.hold_controls = []
        self.tap_controls = []
        self._last_runtime_state = None

        # Best-effort: allow simultaneous touches on the root container.
        try:
            self.root.__py_view__.managed.multipleTouchEnabled = True
        except Exception:
            pass

        self._build()

    def _add_hold(
        self,
        key,
        icon_id,
        action,
        bg,
        font_size=16,
        low_latency=False,
    ):
        control = UIKitHoldControl(
            icon_id,
            action,
            self.input,
            bg,
            font_size=font_size,
            low_latency=low_latency,
        )

        self.root.add_subview(
            control
        )

        self.controls[
            key
        ] = {
            "view": control,
            "label": None,
            "icon": control.icon,
            "kind": "uicontrol",
        }

        self.hold_controls.append(
            control
        )


    def _add_selector(self, key, callback):
        """Add a native touch target; visuals are emitted by MetalRenderer."""
        control = UIKitTapControl(callback)
        self.root.add_subview(control)
        self.controls[key] = {
            "view": control,
            "label": None,
            "kind": "uicontrol_tap",
        }
        self.tap_controls.append(control)
        return control

    def _add_icon_button(self, key, icon_id, callback, tone="utility"):
        """Visible low-frequency native button using the shared pixel assets."""
        control = UIKitTapControl(
            callback, minimum_interval=0.040, icon_id=icon_id, tone=tone,
        )
        self.root.add_subview(control)
        self.controls[key] = {
            "view": control,
            "label": None,
            "icon": control.icon,
            "kind": "uicontrol_tap",
        }
        self.tap_controls.append(control)
        return control

    def sync_action_mode(self, state=None):
        """Compatibility hook; FIX66 keeps selector visuals entirely in Metal."""
        if state is None and self.on_get_action_mode_state is not None:
            try:
                state = self.on_get_action_mode_state()
            except Exception:
                state = None
        if isinstance(state, dict):
            self._last_runtime_state = dict(state)
        return state

    def sync_runtime_state(self, state):
        """Record-only compatibility hook; performs zero UIKit mutations."""
        if isinstance(state, dict):
            self._last_runtime_state = dict(state)
            return state
        return None

    def _build(self):
        self.aim_joystick = UIKitAimJoystick(
            self.on_aim_begin,
            self.on_aim_axis,
            self.on_aim_release,
        )
        self.root.add_subview(
            self.aim_joystick
        )
        self.controls[
            "aim"
        ] = {
            "view": self.aim_joystick,
            "label": None,
            "icon": self.aim_joystick.icon,
            "kind": "aim",
        }

        self.move_joystick = UIKitMovementJoystick(self.input)
        self.root.add_subview(self.move_joystick)
        self.controls["move"] = {"view": self.move_joystick, "label": None,
                                 "icon": self.move_joystick.icon, "kind": "aim"}

        # V0.6.5.4+: jump is a rapid gameplay input, just like 動作.
        # V0.6.5.5: left/right movement use the same low-latency path.
        # Do not mutate several PytoUI properties or invoke haptics inside
        # touchDown/touchUp; those synchronous main-thread bridges were
        # visible as a 2-3 video-frame stall before every running jump.
        self._add_hold(
            "jump",
            "control_jump",
            "jump",
            BLUE,
            low_latency=True,
        )

        self._add_hold(
            "crouch",
            "control_crouch",
            "crouch",
            PURPLE,
            font_size=13,
        )

        self._add_hold(
            "attack",
            "control_action",
            "attack",
            RED,
            font_size=14,
            low_latency=True,
        )

        self._add_hold(
            "roll",
            "control_roll",
            "roll",
            DARK2,
            font_size=13,
        )

        self._add_hold(
            "interact",
            "control_interact",
            "interact",
            GOLD,
            font_size=13,
        )

        # FIX66: these three rapidly-used selectors are transparent native
        # UIControl hit targets.  Their panels and pixel symbols are rendered in
        # the existing Metal dynamic batch, never as UIButton/UIImageView state.
        self._add_selector("tool", self.on_toggle_tool)
        self._add_selector("magic", self.on_toggle_magic)
        self._add_selector("action_mode", self.on_toggle_action_mode)

        def save_action(
            _sender,
        ):
            haptic_selection()

            if self.on_save is not None:
                self.on_save()

        self._add_icon_button("save", "control_save", save_action)

        def load_action(
            _sender,
        ):
            haptic_selection()

            if self.on_load is not None:
                self.on_load()

        self._add_icon_button("load", "control_load", load_action)

        # FIX68: constant-size New Game entry. The first tap only arms a
        # four-second confirmation; the second posts the reset command.
        def new_game_action(_sender):
            haptic_selection()
            if self.on_new_game is not None:
                self.on_new_game()

        self._add_icon_button("new_game", "control_new_game", new_game_action)

        def close_action(
            _sender,
        ):
            print("GAME CLOSE TAP: V0.7.1.7 inherited exact-controller button")
            # V0.7.1.6: Host dismisses the exact owning controller on next main-loop turn.
            # The Host owns the UIKit + PytoUI two-stage close so the visible
            # fullscreen controller and show_view() state cannot diverge.
            self.on_close(_sender)

        self._add_icon_button(
            "close", "control_close", close_action, tone="close"
        )

    def _set_frame(
        self,
        key,
        frame,
    ):
        obj = self.controls[
            key
        ]

        view = obj[
            "view"
        ]

        if obj.get(
            "kind"
        ) in (
            "uicontrol",
            "uicontrol_tap",
            "aim",
        ):
            view.set_control_frame(frame)
        else:
            view.frame = frame


    def _top_safe_insets(self):
        """Conservative landscape safe area for top-right tap buttons.

        On iPhone a PytoUI button can be visible in the rounded/system-edge
        region while its touch never reaches Button.action. Keep the close
        control well inside that region and also use UIKit safeAreaInsets when
        Pyto exposes them.
        """
        safe_top = 10.0
        # iPhone keeps extra right clearance for rounded edge / Dynamic Island.
        # iPad/iPad Pro has a much smaller default edge exclusion and relies on
        # the actual safeAreaInsets below.
        safe_right = 16.0 if is_ipad() else 72.0
        try:
            native = self.root.__py_view__.managed
            insets = native.safeAreaInsets
            safe_top = max(safe_top, float(insets.top) + 6.0)
            safe_right = max(safe_right, float(insets.right) + 10.0)
        except Exception:
            pass
        return safe_top, safe_right

    def layout(
        self,
        w,
        h,
    ):
        ipad = is_ipad()
        size = min(
            78.0 if ipad else 68.0,
            h * (0.145 if ipad else 0.17),
        )
        gap = 6.0 if ipad else 5.0
        left_x = 30.0 if ipad else 24.0
        base_y = (
            h
            - size * 2.05
            - 18
        )

        diameter = min(178.0 if ipad else 152.0, h * 0.40)
        safe_left = left_x
        try:
            safe_left = max(safe_left, float(self.root.__py_view__.managed.safeAreaInsets.left) + 12.0)
        except Exception:
            pass
        self._set_frame("move", (safe_left, h - diameter - 22.0, diameter, diameter))

        b = min(
            84.0 if ipad else 72.0,
            h * (0.15 if ipad else 0.18),
        )

        right = (
            w
            - (28.0 if ipad else 18.0)
        )

        self._set_frame(
            "jump",
            (
                right
                - b * 2.0
                - 10,
                h
                - b * 2.15
                - 10,
                b,
                b,
            ),
        )

        self._set_frame(
            "attack",
            (
                right
                - b,
                h
                - b * 2.15
                - 10,
                b,
                b,
            ),
        )

        self._set_frame(
            "crouch",
            (
                right
                - b,
                h
                - b
                - 10,
                b,
                b,
            ),
        )

        small_w = (
            b * 0.92
        )
        small_h = (
            b * 0.62
        )

        self._set_frame(
            "roll",
            (
                right
                - b * 3.1
                - 24,
                h
                - b
                - 8,
                small_w,
                small_h,
            ),
        )

        self._set_frame(
            "interact",
            (
                right
                - b * 3.1
                - 24,
                h
                - b * 1.75
                - 12,
                small_w,
                small_h,
            ),
        )

        selector_layout = selector_frames(w, h, ipad=ipad)
        self._set_frame("magic", selector_layout["magic"])
        self._set_frame("tool", selector_layout["tool"])
        self._set_frame("action_mode", selector_layout["action_mode"])

        aim_size = min(
            104.0 if ipad else 88.0,
            max(
                78.0 if ipad else 70.0,
                h * (0.18 if ipad else 0.21),
            ),
        )
        self._set_frame(
            "aim",
            (
                right
                - aim_size
                - 6,
                max(
                    62.0,
                    h
                    - b * 3.45
                    - aim_size * 0.20,
                ),
                aim_size,
                aim_size,
            ),
        )

        safe_top, safe_right = self._top_safe_insets()
        top = top_control_frames(w, safe_top, safe_right, ipad=ipad)
        self._set_frame("new_game", top["new_game"])
        self._set_frame("save", top["save"])
        self._set_frame("load", top["load"])
        self._set_frame("close", top["close"])

    def reset(self):
        for control in self.hold_controls:
            try:
                control.force_release()
            except Exception:
                pass

        for control in self.tap_controls:
            try:
                control.force_release()
            except Exception:
                pass

        try:
            self.aim_joystick.force_release()
        except Exception:
            pass

        try:
            self.move_joystick.force_release()
        except Exception:
            pass
        self.input.reset()
