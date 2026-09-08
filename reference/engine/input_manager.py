# -*- coding: utf-8 -*-
import time
import threading
import math
from dataclasses import dataclass


@dataclass
class LiveActionState:
    down: bool = False
    down_since: float = 0.0
    released_duration: float = 0.0
    press_seq: int = 0
    release_seq: int = 0


@dataclass
class FrameActionState:
    down: bool = False
    pressed: bool = False
    released: bool = False
    held_seconds: float = 0.0
    released_duration: float = 0.0


class InputManager:
    """Thread-safe action input.

    iOS UIKit touch callbacks run on the main thread while the game loop
    runs on a background thread. V0.3 therefore does not share mutable
    "just pressed" booleans directly.

    UI thread:
        press(action)
        release(action)

    Game thread, once per frame:
        begin_frame()
        query actions...

    Each press/release has a sequence number, so a fast tap cannot be
    lost merely because it occurred between two game-loop reads.
    """

    ACTIONS = (
        "move_left",
        "move_right",
        "move_up",
        "move_down",
        "jump",
        "crouch",
        "run",
        "roll",
        "attack",
        "interact",
    )

    def __init__(self):
        self._lock = threading.RLock()
        # FIX83: one analog owner; fixed-step snapshots and a bounded edge queue.
        self._move_live = (0.0, 0.0, False)
        self._move_frame = (0.0, 0.0, False)
        self._move_events = []
        self._move_frame_events = ()
        self._move_started = 0.0
        self._move_last_vector = (0.0, 0.0)

        self._live = {
            name: LiveActionState()
            for name in self.ACTIONS
        }

        self._frame = {
            name: FrameActionState()
            for name in self.ACTIONS
        }

        self._seen_press = {
            name: 0
            for name in self.ACTIONS
        }

        self._seen_release = {
            name: 0
            for name in self.ACTIONS
        }

        # Finite input pulses are used for very quick direction taps.
        # They expire by monotonic time inside begin_frame(), so a tap can
        # never remain latched even if a GestureRecognizer misses an END.
        self._pulse_until = {
            name: 0.0
            for name in self.ACTIONS
        }

        self._platform_debug = {
            name: {
                "last_event": "",
                "last_event_time": 0.0,
                "touchDown": 0,
                "dragEnter": 0,
                "dragExit": 0,
                "upInside": 0,
                "upOutside": 0,
                "cancel": 0,
                "forceRelease": 0,
            }
            for name in self.ACTIONS
        }

    # --------------------------------------------------------
    # UI / platform thread
    # --------------------------------------------------------

    def _release_locked(
        self,
        action,
        now,
    ):
        state = self._live.get(
            action
        )

        if (
            state is None
            or not state.down
        ):
            return

        state.down = False
        state.released_duration = max(
            0.0,
            now
            - state.down_since,
        )
        state.release_seq += 1

        self._pulse_until[
            action
        ] = 0.0

    def _post_move_event(self, kind, **data):
        event = dict(kind=kind, **data)
        if kind == "axis" and self._move_events and self._move_events[-1]["kind"] == "axis":
            self._move_events[-1] = event
        else:
            self._move_events.append(event)
        # UI may outpace a paused world. Never allow unbounded touch allocation.
        if len(self._move_events) > 64:
            self._move_events = self._move_events[-64:]

    def begin_move_stick(self):
        with self._lock:
            if self._move_live[2]:
                return False
            self._move_started = time.monotonic()
            self._move_live = (0.0, 0.0, True)
            self._move_last_vector = (0.0, 0.0)
            self._post_move_event("begin")
            return True

    def set_move_stick(self, x, y):
        """Radial deadzone + analog amplitude; digital edges retain climb/up."""
        try:
            x, y = float(x), float(y)
            if not math.isfinite(x) or not math.isfinite(y):
                return
        except (TypeError, ValueError):
            return
        m = math.hypot(x, y)
        deadzone = 0.14
        if m <= deadzone:
            x = y = 0.0
        else:
            scale = min(1.0, (m - deadzone) / (1.0 - deadzone)) / m
            x, y = x * scale, y * scale
        with self._lock:
            if not self._move_live[2]:
                return
            self._move_live = (x, y, True)
            if x or y:
                self._move_last_vector = (x, y)
            for action, active in (("move_left", x < -0.05), ("move_right", x > 0.05),
                                   ("move_up", y < -0.12), ("move_down", y > 0.12)):
                if active:
                    self.press(action)
                else:
                    self.release(action)
            self._post_move_event("axis", x=x, y=y)

    def end_move_stick(self, held_seconds=None, fire=False):
        """Release movement only; legacy ``fire`` arguments cannot issue attacks."""
        with self._lock:
            if not self._move_live[2]:
                return False
            x, y = self._move_last_vector
            held = max(0.0, time.monotonic() - self._move_started)
            if held_seconds is not None:
                held = max(0.0, min(10.0, float(held_seconds)))
            self._post_move_event("release", x=x, y=y, held_seconds=held,
                                  fire=False)
            self._move_live = (0.0, 0.0, False)
            for action in ("move_left", "move_right", "move_up", "move_down"):
                self.release(action)
            return True

    def move_stick_events(self):
        return self._move_frame_events

    def note_platform_event(
        self,
        action,
        event_name,
    ):
        if action not in self._platform_debug:
            return

        now = time.monotonic()

        with self._lock:
            debug = self._platform_debug[
                action
            ]

            event_name = str(
                event_name
            )

            debug[
                "last_event"
            ] = event_name

            debug[
                "last_event_time"
            ] = now

            if event_name not in debug:
                debug[
                    event_name
                ] = 0

            debug[
                event_name
            ] += 1

    def press(self, action):
        if action not in self._live:
            return

        now = time.monotonic()

        with self._lock:
            # Opposite directions are mutually exclusive.
            #
            # This is important for the Pyto gesture fallback: if one
            # recognizer ever misses its ENDED/CANCELLED callback, the stale
            # direction must not cancel a new physical press on the opposite
            # direction.
            opposite = {
                "move_left": "move_right",
                "move_right": "move_left",
                "move_up": "move_down",
                "move_down": "move_up",
            }.get(
                action
            )

            if opposite is not None:
                self._release_locked(
                    opposite,
                    now,
                )

            state = self._live[action]

            if state.down:
                # Hard-reset a stale down state, but DO NOT synthesize a
                # normal release event from it.
                #
                # V0.5.4 converted the stale duration into released_duration
                # and release_seq. If that stale duration was already >0.55s,
                # Player interpreted the SAME new tap as a full-length hold,
                # which caused the "light tap -> huge jump" seen in video.
                state.down = False
                state.released_duration = 0.0

            # Every real platform press starts a brand-new timing epoch.
            self._pulse_until[
                action
            ] = 0.0

            state.down = True
            state.down_since = now
            state.press_seq += 1

    def pulse(
        self,
        action,
        duration=0.10,
    ):
        """Generate a finite action pulse.

        Used by quick direction taps. The caller never needs to deliver a
        matching release event; begin_frame() will end the pulse automatically.
        """
        if action not in self._live:
            return

        now = time.monotonic()
        duration = max(
            0.01,
            float(
                duration
            ),
        )

        with self._lock:
            opposite = {
                "move_left": "move_right",
                "move_right": "move_left",
                "move_up": "move_down",
                "move_down": "move_up",
            }.get(
                action
            )

            if opposite is not None:
                self._release_locked(
                    opposite,
                    now,
                )

            state = self._live[
                action
            ]

            # A second quick tap extends/restarts the finite pulse. It never
            # converts the input into an unbounded held state.
            state.down = True
            state.down_since = now
            state.released_duration = 0.0
            state.press_seq += 1

            self._pulse_until[
                action
            ] = (
                now
                + duration
            )

    def cancel_pulse(
        self,
        action,
    ):
        if action not in self._live:
            return

        with self._lock:
            self._pulse_until[
                action
            ] = 0.0

    def release(self, action):
        if action not in self._live:
            return

        now = time.monotonic()

        with self._lock:
            self._release_locked(
                action,
                now,
            )

    # --------------------------------------------------------
    # Game thread
    # --------------------------------------------------------

    def begin_frame(self):
        now = time.monotonic()

        with self._lock:
            self._move_frame = self._move_live
            self._move_frame_events = tuple(self._move_events)
            self._move_events.clear()
            for name, live in self._live.items():
                pulse_until = self._pulse_until[
                    name
                ]

                if (
                    pulse_until > 0.0
                    and now >= pulse_until
                ):
                    self._release_locked(
                        name,
                        now,
                    )

                press_seen = self._seen_press[name]
                release_seen = self._seen_release[name]

                frame = self._frame[name]
                frame.down = live.down
                frame.pressed = live.press_seq != press_seen
                frame.released = live.release_seq != release_seen

                if live.down:
                    frame.held_seconds = max(
                        0.0,
                        now - live.down_since,
                    )
                else:
                    frame.held_seconds = 0.0

                frame.released_duration = live.released_duration

                self._seen_press[name] = live.press_seq
                self._seen_release[name] = live.release_seq

    def down(self, action):
        return self._frame[action].down

    def just_pressed(self, action):
        return self._frame[action].pressed

    def just_released(self, action):
        return self._frame[action].released

    def held_seconds(self, action):
        return self._frame[action].held_seconds

    def released_duration(self, action):
        return self._frame[action].released_duration

    def axis_x(self):
        if self._move_frame[2]:
            return self._move_frame[0]
        right = self.down(
            "move_right"
        )

        left = self.down(
            "move_left"
        )

        if right and left:
            # Last physical press wins. This is a second line of defense
            # against a stale gesture state.
            with self._lock:
                right_time = (
                    self._live[
                        "move_right"
                    ].down_since
                )

                left_time = (
                    self._live[
                        "move_left"
                    ].down_since
                )

            return (
                1
                if right_time
                >= left_time
                else -1
            )

        return (
            int(
                right
            )
            - int(
                left
            )
        )

    def axis_y(self):
        if self._move_frame[2]:
            return self._move_frame[1]
        down = self.down(
            "move_down"
        )

        up = self.down(
            "move_up"
        )

        if down and up:
            with self._lock:
                down_time = (
                    self._live[
                        "move_down"
                    ].down_since
                )

                up_time = (
                    self._live[
                        "move_up"
                    ].down_since
                )

            return (
                1
                if down_time
                >= up_time
                else -1
            )

        return (
            int(
                down
            )
            - int(
                up
            )
        )

    def any_direction(self):
        return any(
            self.down(action)
            for action in (
                "move_left",
                "move_right",
                "move_up",
                "move_down",
            )
        )

    def debug_action(
        self,
        action,
    ):
        if action not in self._live:
            return {}

        with self._lock:
            live = self._live[
                action
            ]

            frame = self._frame[
                action
            ]

            now = time.monotonic()

            pulse_remaining = max(
                0.0,
                float(
                    self._pulse_until.get(
                        action,
                        0.0,
                    )
                )
                - now,
            )

            live_held_seconds = (
                max(
                    0.0,
                    now
                    - float(
                        live.down_since
                    ),
                )
                if live.down
                else 0.0
            )

            if live.down:
                mode = (
                    "TAP"
                    if pulse_remaining > 0.0
                    else "HOLD"
                )
            else:
                mode = "OFF"

            platform = dict(
                self._platform_debug.get(
                    action,
                    {},
                )
            )

            last_event_time = float(
                platform.get(
                    "last_event_time",
                    0.0,
                )
            )

            platform_age = (
                max(
                    0.0,
                    now
                    - last_event_time,
                )
                if last_event_time > 0.0
                else 0.0
            )

            return {
                "live_down": bool(
                    live.down
                ),
                "frame_down": bool(
                    frame.down
                ),
                "pressed": bool(
                    frame.pressed
                ),
                "released": bool(
                    frame.released
                ),
                "held_seconds": float(
                    frame.held_seconds
                ),
                "live_held_seconds": float(
                    live_held_seconds
                ),
                "released_duration": float(
                    frame.released_duration
                ),
                "pulse_remaining": float(
                    pulse_remaining
                ),
                "mode": mode,
                "press_seq": int(
                    live.press_seq
                ),
                "release_seq": int(
                    live.release_seq
                ),
                "platform_last_event": str(
                    platform.get(
                        "last_event",
                        "",
                    )
                ),
                "platform_event_age": float(
                    platform_age
                ),
                "platform_touch_down": int(
                    platform.get(
                        "touchDown",
                        0,
                    )
                ),
                "platform_drag_enter": int(
                    platform.get(
                        "dragEnter",
                        0,
                    )
                ),
                "platform_drag_exit": int(
                    platform.get(
                        "dragExit",
                        0,
                    )
                ),
                "platform_up_inside": int(
                    platform.get(
                        "upInside",
                        0,
                    )
                ),
                "platform_up_outside": int(
                    platform.get(
                        "upOutside",
                        0,
                    )
                ),
                "platform_cancel": int(
                    platform.get(
                        "cancel",
                        0,
                    )
                ),
                "platform_force_release": int(
                    platform.get(
                        "forceRelease",
                        0,
                    )
                ),
            }

    def end_frame(self):
        # V0.3 edge states are snapshots, not booleans that need clearing.
        pass

    def reset(self):
        with self._lock:
            self._move_live = self._move_frame = (0.0, 0.0, False)
            self._move_events.clear()
            self._move_frame_events = ()
            self._move_last_vector = (0.0, 0.0)
            for name, state in self._live.items():
                state.down = False
                state.down_since = 0.0
                state.released_duration = 0.0
                state.press_seq += 1
                state.release_seq += 1

                self._seen_press[name] = state.press_seq
                self._seen_release[name] = state.release_seq
                self._pulse_until[
                    name
                ] = 0.0

                self._frame[name] = FrameActionState()
