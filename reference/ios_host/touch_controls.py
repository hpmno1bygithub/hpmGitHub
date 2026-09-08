# -*- coding: utf-8 -*-
"""Universal iPhone / iPad gameplay input backend selector.

V0.5.6.7 priority:
    1. Per-button native UIKit UIControl target/action
    2. Gesture Safe fallback

The old full-screen Native Finger-ID overlay remains disabled.
"""

from config import USE_UIKIT_UICONTROL_INPUT


class TouchControls:
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
        self.backend = None
        self.backend_name = "unknown"
        self.native_error = ""
        self._hidden = False
        self._runtime_state = None

        if USE_UIKIT_UICONTROL_INPUT:
            try:
                from ios_host.touch_controls_uicontrol import (
                    UIKitUIControlTouchControls,
                )

                self.backend = (
                    UIKitUIControlTouchControls(
                        root,
                        input_manager,
                        on_close,
                        on_save=on_save,
                        on_load=on_load,
                        on_new_game=on_new_game,
                        on_debug_water=on_debug_water,
                        on_debug_fire=on_debug_fire,
                        on_toggle_magic=on_toggle_magic,
                        on_toggle_tool=on_toggle_tool,
                        on_toggle_action_mode=on_toggle_action_mode,
                        on_get_action_mode_state=on_get_action_mode_state,
                        on_aim_begin=on_aim_begin,
                        on_aim_axis=on_aim_axis,
                        on_aim_release=on_aim_release,
                    )
                )

                self.backend_name = (
                    self.backend.backend_name
                )

                print(
                    "Input backend:",
                    self.backend_name,
                )

                return

            except Exception as exc:
                self.native_error = repr(
                    exc
                )

                print("")
                print(
                    "UIKit UIControl backend unavailable."
                )
                print(
                    "Falling back to Gesture Safe."
                )
                print(
                    "Reason:",
                    self.native_error,
                )

        from ios_host.touch_controls_gesture import (
            GestureTouchControls,
        )

        self.backend = GestureTouchControls(
            root,
            input_manager,
            on_close,
            on_save=on_save,
            on_load=on_load,
            on_new_game=on_new_game,
            on_toggle_magic=on_toggle_magic,
            on_toggle_tool=on_toggle_tool,
            on_toggle_action_mode=on_toggle_action_mode,
            on_get_action_mode_state=on_get_action_mode_state,
            on_aim_begin=on_aim_begin,
            on_aim_axis=on_aim_axis,
            on_aim_release=on_aim_release,
        )

        self.backend_name = "Gesture Safe"

        print(
            "Input backend:",
            self.backend_name,
        )

    def layout(
        self,
        w,
        h,
    ):
        self.backend.layout(
            w,
            h,
        )

    def reset(
        self,
    ):
        self.backend.reset()

    def set_hidden(self, hidden):
        """Hide the fixed gameplay controls while the Metal backpack is modal."""
        value = bool(hidden)
        self._hidden = value
        seen = set()
        for obj in getattr(self.backend, "controls", {}).values():
            view = obj.get("view") if isinstance(obj, dict) else None
            if view is None or id(view) in seen:
                continue
            seen.add(id(view))
            try:
                view.hidden = value
            except Exception:
                pass
        # Some backends keep the aim joystick outside ``controls``.
        aim = getattr(self.backend, "aim_joystick", None)
        if aim is not None and id(aim) not in seen:
            try:
                aim.hidden = value
            except Exception:
                pass

    def sync_action_mode(self, state=None):
        """Compatibility hook with zero UIKit work (FIX66)."""
        if isinstance(state, dict):
            self._runtime_state = dict(state)
        return state

    def sync_runtime_state(self, state):
        """Record a snapshot only; Metal reads live authoritative state."""
        if isinstance(state, dict):
            self._runtime_state = dict(state)
            return state
        return None

    @property
    def last_native_error(
        self,
    ):
        return getattr(
            self.backend,
            "last_native_error",
            self.native_error,
        )
