# -*- coding: utf-8 -*-
import time
import pyto_ui as ui
from ios_host.orientation import is_ipad
from ios_host.control_overlay import selector_frames, top_control_frames
from ios_host.control_icon_view import (
    ControlIconWidget,
    apply_pixel_control_style,
    set_pixel_pressed,
)

from config import (
    DIRECTION_TAP_PULSE_SECONDS,
    DIRECTION_HOLD_THRESHOLD_SECONDS,
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


def _enum_or_old(enum_name, member_name, old_name):
    enum_cls = getattr(ui, enum_name, None)
    if enum_cls is not None and hasattr(enum_cls, member_name):
        return getattr(enum_cls, member_name)
    return getattr(ui, old_name)


GESTURE_LONG_PRESS = _enum_or_old(
    "GestureType",
    "LONG_PRESS",
    "GESTURE_TYPE_LONG_PRESS",
)

STATE_BEGAN = _enum_or_old(
    "GestureState",
    "BEGAN",
    "GESTURE_STATE_BEGAN",
)
STATE_ENDED = _enum_or_old(
    "GestureState",
    "ENDED",
    "GESTURE_STATE_ENDED",
)
STATE_CANCELLED = _enum_or_old(
    "GestureState",
    "CANCELLED",
    "GESTURE_STATE_CANCELLED",
)

try:
    STATE_FAILED = _enum_or_old(
        "GestureState",
        "FAILED",
        "GESTURE_STATE_FAILED",
    )
except Exception:
    STATE_FAILED = None


class GestureTouchControls:
    """iOS touch -> InputManager adapter.

    Every control is a plain View + LongPress recognizer.
    We avoid mixing UIButton state with movement gestures.
    """

    def __init__(
        self,
        root,
        input_manager,
        on_close,
        on_save=None,
        on_load=None,
        on_new_game=None,
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
        self.backend_name = "Gesture fallback"

        self.controls = {}
        self.gestures = []
        self._selector_last_tap = {}
        self._last_runtime_state = None
        self.aim_joystick = None
        self.aim_fallback_error = ""

        self._build()

        # Best-effort native UIKit multi-touch flag.
        # Pyto's own objc_view example exposes the wrapped UIView through
        # __py_view__.managed. Failure is harmless.
        self._enable_native_multitouch(root)

    @staticmethod
    def _enable_native_multitouch(view):
        try:
            view.__py_view__.managed.multipleTouchEnabled = True
        except Exception:
            pass

    def _restore_control(
        self,
        key,
    ):
        obj = self.controls.get(
            key
        )

        if obj is None:
            return

        holder = obj[
            "view"
        ]

        # Restore one scalar on the already-retained native layer.  This does
        # not enqueue PytoUI color/border work and allocates no UIKit objects.
        set_pixel_pressed(holder, False)

    def _add_direction(
        self,
        key,
        icon_id,
        action,
        bg,
        font_size=16,
        low_latency=False,
    ):
        """Direction control with mutually exclusive tap / hold paths.

        QUICK TAP:
            UIButton action -> finite InputManager.pulse()
            No release callback is required.

        LONG HOLD:
            LongPress BEGAN -> InputManager.press()
            LongPress END/CANCEL/FAIL -> InputManager.release()

        The recognizer cancels UIButton delivery once the hold threshold is
        crossed, so one physical touch cannot run both paths.
        """
        holder = ui.Button(title="")
        apply_pixel_control_style(holder, "move")
        icon = ControlIconWidget(holder)
        icon.set_icon(icon_id)
        icon.set_hidden(False)

        gesture = ui.GestureRecognizer(
            GESTURE_LONG_PRESS
        )
        gesture.minimum_press_duration = (
            DIRECTION_HOLD_THRESHOLD_SECONDS
        )

        # Critical V0.5.6.5 rule:
        # once a long hold is recognized, cancel the UIButton tap path.
        gesture.cancels_touches_in_view = True

        def hold_handler(
            sender,
        ):
            state = sender.state

            if state == STATE_BEGAN:
                set_pixel_pressed(holder, True)
                opposite = {
                    "move_left": (
                        "right",
                        "move_right",
                    ),
                    "move_right": (
                        "left",
                        "move_left",
                    ),
                    "move_up": (
                        "down",
                        "move_down",
                    ),
                    "move_down": (
                        "up",
                        "move_up",
                    ),
                }.get(
                    action
                )

                if opposite is not None:
                    opposite_key, opposite_action = (
                        opposite
                    )

                    self.input.release(
                        opposite_action
                    )

                    self._restore_control(
                        opposite_key
                    )

                self.input.cancel_pulse(
                    action
                )

                if action in ("move_left", "move_right", "move_up", "move_down"):
                    haptic_light()
                elif not low_latency:
                    haptic_for_action(action)

                self.input.press(
                    action
                )

            elif (
                state == STATE_ENDED
                or state == STATE_CANCELLED
                or (
                    STATE_FAILED is not None
                    and state == STATE_FAILED
                )
            ):
                self.input.release(
                    action
                )
                set_pixel_pressed(holder, False)

        def quick_tap(
            _sender,
        ):
            # No persistent "down" state is created here. The pulse expires
            # automatically from InputManager.begin_frame().
            if action in ("move_left", "move_right", "move_up", "move_down"):
                haptic_light()
            else:
                haptic_for_action(action)

            self.input.pulse(
                action,
                DIRECTION_TAP_PULSE_SECONDS,
            )

        holder.action = quick_tap
        gesture.action = hold_handler

        holder.add_gesture_recognizer(
            gesture
        )

        self._enable_native_multitouch(
            holder
        )

        self.gestures.append(
            gesture
        )
        self.root.add_subview(
            holder
        )

        self.controls[
            key
        ] = {
            "view": holder,
            "label": None,
            "icon": icon,
            "bg": bg,
            "gesture": gesture,
            "kind": "direction",
        }

    def _add_hold(
        self,
        key,
        icon_id,
        action,
        bg,
        release_action=True,
        font_size=16,
        low_latency=False,
    ):
        """Single-path hold control for non-direction gameplay buttons."""
        holder = ui.View()
        tone = {
            "jump": "jump",
            "crouch": "stance",
            "attack": "action",
            "interact": "interact",
        }.get(key, "neutral")
        apply_pixel_control_style(holder, tone)
        icon = ControlIconWidget(holder)
        icon.set_icon(icon_id)
        icon.set_hidden(False)

        gesture = ui.GestureRecognizer(
            GESTURE_LONG_PRESS
        )
        gesture.minimum_press_duration = 0.01
        gesture.cancels_touches_in_view = True

        def handler(
            sender,
        ):
            state = sender.state

            if state == STATE_BEGAN:
                set_pixel_pressed(holder, True)
                if action == "attack":
                    haptic_light()
                elif not low_latency:
                    haptic_for_action(action)

                self.input.press(
                    action
                )

            elif (
                state == STATE_ENDED
                or state == STATE_CANCELLED
                or (
                    STATE_FAILED is not None
                    and state == STATE_FAILED
                )
            ):
                self.input.release(action)
                set_pixel_pressed(holder, False)

        gesture.action = handler
        holder.add_gesture_recognizer(
            gesture
        )

        self._enable_native_multitouch(
            holder
        )

        self.gestures.append(
            gesture
        )
        self.root.add_subview(
            holder
        )

        self.controls[
            key
        ] = {
            "view": holder,
            "label": None,
            "icon": icon,
            "bg": bg,
            "gesture": gesture,
            "kind": "hold",
        }


    def _add_selector(self, key, callback):
        """Static fallback hit target; selector artwork is rendered by Metal."""
        button = ui.Button(title="")
        button.background_color = ui.Color.rgb(0.0, 0.0, 0.0, 0.001)
        button.title_color = ui.Color.rgb(1.0, 1.0, 1.0, 0.001)
        button.corner_radius = 0

        def action(_sender):
            now = time.monotonic()
            previous = float(self._selector_last_tap.get(key, -1.0e9))
            if now - previous < 0.040:
                return
            self._selector_last_tap[key] = now
            if callback is not None:
                callback()

        button.action = action
        self.root.add_subview(button)
        self.controls[key] = {
            "view": button,
            "label": None,
            "kind": "tap",
        }
        return button

    def _add_icon_button(self, key, icon_id, callback, tone="utility"):
        """Create one retained low-frequency icon button."""
        button = ui.Button(title="")
        apply_pixel_control_style(button, tone)
        icon = ControlIconWidget(button)
        icon.set_icon(icon_id)
        icon.set_hidden(False)
        button.action = callback
        self.root.add_subview(button)
        self.controls[key] = {
            "view": button,
            "label": None,
            "icon": icon,
            "kind": "tap",
        }
        return button

    def sync_action_mode(self, state=None):
        """Compatibility hook; FIX66 performs no UIKit artwork mutation."""
        if state is None and self.on_get_action_mode_state is not None:
            try:
                state = self.on_get_action_mode_state()
            except Exception:
                state = None
        if isinstance(state, dict):
            self._last_runtime_state = dict(state)
        return state

    def sync_runtime_state(self, state):
        """Record-only compatibility hook; visuals come from the Metal batch."""
        if isinstance(state, dict):
            self._last_runtime_state = dict(state)
            return state
        return None

    def _build(self):
        # The fallback keeps gesture-based action buttons, but may safely reuse
        # the one retained native pan surface for analog aim.  This restores the
        # mushroom-stick path when UIControl gameplay buttons are disabled.  If
        # a very old Pyto build cannot create UIPanGestureRecognizer, all other
        # fallback controls still load normally.
        try:
            from ios_host.touch_controls_uicontrol import UIKitAimJoystick
            self.aim_joystick = UIKitAimJoystick(
                self.on_aim_begin,
                self.on_aim_axis,
                self.on_aim_release,
            )
            self.root.add_subview(self.aim_joystick)
            self.controls["aim"] = {
                "view": self.aim_joystick,
                "label": None,
                "icon": self.aim_joystick.icon,
                "kind": "aim",
            }
        except Exception as exc:
            self.aim_joystick = None
            self.aim_fallback_error = repr(exc)

        self.move_joystick = None
        try:
            from ios_host.touch_controls_uicontrol import UIKitMovementJoystick
            self.move_joystick = UIKitMovementJoystick(self.input)
            self.root.add_subview(self.move_joystick)
            self.controls["move"] = {"view": self.move_joystick, "label": None,
                                     "icon": self.move_joystick.icon, "kind": "aim"}
        except Exception:
            self._add_direction(
                "up", "control_up", "move_up", DARK2
            )
            self._add_direction(
                "left", "control_left", "move_left", DARK2,
                low_latency=True,
            )
            self._add_direction(
                "right", "control_right", "move_right", DARK2,
                low_latency=True,
            )
            self._add_direction(
                "down", "control_down", "move_down", DARK2
            )

        self._add_hold(
            "jump", "control_jump", "jump", BLUE,
            low_latency=True,
        )
        self._add_hold(
            "crouch", "control_crouch", "crouch", PURPLE,
            font_size=13,
        )
        self._add_hold(
            "attack", "control_action", "attack", RED,
            font_size=14,
        )
        self._add_hold(
            "roll", "control_roll", "roll", DARK2,
            font_size=13,
        )
        self._add_hold(
            "interact", "control_interact", "interact", GOLD,
            font_size=13,
        )

        # FIX66: static transparent hit targets only.  No UIImageView and no
        # UIButton color/title writes occur while the user cycles selections.
        self._add_selector("tool", self.on_toggle_tool)
        self._add_selector("magic", self.on_toggle_magic)
        self._add_selector("action_mode", self.on_toggle_action_mode)

        def save_action(_sender):
            haptic_selection()

            if self.on_save is not None:
                self.on_save()

        self._add_icon_button("save", "control_save", save_action)

        def load_action(_sender):
            haptic_selection()

            if self.on_load is not None:
                self.on_load()

        self._add_icon_button("load", "control_load", load_action)

        # FIX68: one additional fixed top-row control. It never creates views
        # per item/entity; confirmation and reset run through GameCommandQueue.
        def new_game_action(_sender):
            haptic_selection()
            if self.on_new_game is not None:
                self.on_new_game()

        self._add_icon_button("new_game", "control_new_game", new_game_action)

        # Close is intentionally a normal button so it cannot become
        # stuck as a game action.
        def close_action(_sender):
            print("GAME CLOSE TAP: V0.7.1.7 inherited exact-controller button")
            # Same Host-owned UIKit + PytoUI close path as the UIControl
            # backend.  Do not close sender.superview inside Button.action.
            self.on_close(_sender)

        self._add_icon_button(
            "close", "control_close", close_action, tone="close"
        )

    def _set_frame(self, key, frame):
        obj = self.controls.get(key)
        if obj is None:
            return  # Four legacy keys are absent when the analog stick exists.
        view = obj["view"]
        view.frame = frame

        label = obj.get("label")
        if label is not None:
            label.frame = (0, 0, frame[2], frame[3])
        icon = obj.get("icon")
        if icon is not None:
            inset = max(5.0, min(float(frame[2]), float(frame[3])) * 0.16)
            icon.layout((inset, inset, max(1.0, frame[2] - inset * 2.0),
                         max(1.0, frame[3] - inset * 2.0)))


    def _top_safe_insets(self):
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

    def layout(self, w, h):
        ipad = is_ipad()
        size = min(78.0 if ipad else 68.0, h * (0.145 if ipad else 0.17))
        gap = 6.0 if ipad else 5.0
        left_x = 30.0 if ipad else 24.0
        base_y = h - size * 2.05 - 18

        self._set_frame(
            "up",
            (left_x + size + gap, base_y, size, size),
        )
        self._set_frame(
            "left",
            (left_x, base_y + size + gap, size, size),
        )
        self._set_frame(
            "right",
            (
                left_x + 2 * (size + gap),
                base_y + size + gap,
                size,
                size,
            ),
        )
        self._set_frame(
            "down",
            (
                left_x + size + gap,
                base_y + size + gap,
                size,
                size,
            ),
        )

        if self.move_joystick is not None:
            diameter = min(178.0 if ipad else 152.0, h * 0.40)
            self.move_joystick.set_control_frame((left_x, h-diameter-22.0, diameter, diameter))
        b = min(84.0 if ipad else 72.0, h * (0.15 if ipad else 0.18))
        right = w - (28.0 if ipad else 18.0)

        self._set_frame(
            "jump",
            (
                right - b * 2.0 - 10,
                h - b * 2.15 - 10,
                b,
                b,
            ),
        )
        self._set_frame(
            "attack",
            (
                right - b,
                h - b * 2.15 - 10,
                b,
                b,
            ),
        )
        self._set_frame(
            "crouch",
            (
                right - b,
                h - b - 10,
                b,
                b,
            ),
        )

        small_w = b * 0.92
        small_h = b * 0.62

        self._set_frame(
            "roll",
            (
                right - b * 3.1 - 24,
                h - b - 8,
                small_w,
                small_h,
            ),
        )
        self._set_frame(
            "interact",
            (
                right - b * 3.1 - 24,
                h - b * 1.75 - 12,
                small_w,
                small_h,
            ),
        )

        selector_layout = selector_frames(w, h, ipad=ipad)
        self._set_frame("magic", selector_layout["magic"])
        self._set_frame("tool", selector_layout["tool"])
        self._set_frame("action_mode", selector_layout["action_mode"])

        if self.aim_joystick is not None:
            aim_size = min(
                104.0 if ipad else 88.0,
                max(78.0 if ipad else 70.0, h * (0.18 if ipad else 0.21)),
            )
            self.aim_joystick.set_control_frame((
                right - aim_size - 6.0,
                max(62.0, h - b * 3.45 - aim_size * 0.20),
                aim_size,
                aim_size,
            ))

        safe_top, safe_right = self._top_safe_insets()
        top = top_control_frames(w, safe_top, safe_right, ipad=ipad)
        self._set_frame("new_game", top["new_game"])
        self._set_frame("save", top["save"])
        self._set_frame("load", top["load"])
        self._set_frame("close", top["close"])

    def reset(self):
        if getattr(self, "move_joystick", None) is not None:
            self.move_joystick.force_release()
        for key in tuple(self.controls):
            self._restore_control(key)
        if self.aim_joystick is not None:
            try:
                self.aim_joystick.force_release()
            except Exception:
                pass
        self.input.reset()
