# -*- coding: utf-8 -*-
import os
import threading
import time

import pyto_ui as ui
import mainthread

from config import (
    TARGET_FPS, MAX_FRAME_DT, VERSION,
    PHYSICS_FIXED_HZ, PHYSICS_MAX_CATCHUP_STEPS,
    PHYSICS_MAX_ACCUMULATOR, PERF_RUNTIME_LOG_SECONDS,
    DISPLAY_SYNC_GAME_LOOP, DISPLAY_SYNC_WAIT_TIMEOUT,
    DISPLAY_SYNC_STEP_THRESHOLD,
)
from engine.math2d import clamp
from engine.fixed_step import FixedStepClock
from engine.display_pacing import DisplayPacer
from ios_host.colors import SKY
from ios_host.touch_controls import TouchControls
from ios_host.metal_renderer import MetalKitRenderer
from ios_host.backpack_ui import BackpackUI
from ios_host.orientation import lock_landscape, reassert_landscape, is_ipad, device_family
from ios_host.startup_trace import trace as startup_trace
from ios_host.audio_manager import AudioManager
from ios_host.game_menu import GameMenuOverlay
from ios_host.study_ui import StudyOverlay
from ios_host.app_activity import ForegroundProbe
from systems.study_challenge import ActivePlayClock
from systems.study_diagnostics import StudyDiagnostics, pump_learning_modal


try:
    PRESENT_FULLSCREEN = ui.PresentationMode.FULLSCREEN
except Exception:
    PRESENT_FULLSCREEN = ui.PRESENTATION_MODE_FULLSCREEN


class PytoHost:
    """Pyto/iOS platform host.

    Owns:
      - root View
      - touch controls
      - debug renderer
      - loop thread

    Does NOT own gameplay rules.
    """

    def __init__(self, game):
        self.game = game
        startup_trace("host.__init__.begin", device_family())
        self.running = True
        self._closing = False
        self._close_completed = threading.Event()
        # V0.7.3.2 startup gate: the simulation/render thread must not advance
        # before the fullscreen landscape layout is real.  V0.7.3.0/1 started
        # the fallback clock while Pyto was still presenting the view, so the
        # player, weather and AI could advance against the old camera/viewport.
        self._layout_ready = threading.Event()
        self._startup_ready = threading.Event()
        self._death_menu_shown = False

        self.root = ui.View()
        self.root.title = f"Pyto RPG V{VERSION}"
        self.root.background_color = SKY

        startup_trace("renderer.init.begin")
        print("STARTUP CHECKPOINT: renderer init begin")
        self.renderer = MetalKitRenderer(
            self.root,
            self.game,
        )
        # FIX71: engineering/build proof belongs in the console, not over the
        # world.  Gameplay bars, boss health and EventBus messages stay owned by
        # the renderer and remain visible.
        self._hide_background_system_hud()
        print("STARTUP CHECKPOINT: renderer init OK (geometry-only)")
        startup_trace("renderer.init.ok")

        startup_trace("controls.init.begin")
        print("STARTUP CHECKPOINT: controls init begin")
        self.controls = TouchControls(
            self.root,
            self.game.input,
            self.close,
            on_save=lambda: self._queue_ui_command("save"),
            on_load=lambda: self._queue_ui_command("load"),
            on_new_game=lambda: self._queue_ui_command("new_game"),
            on_debug_water=lambda: self._queue_ui_command("debug_water"),
            on_debug_fire=lambda: self._queue_ui_command("debug_fire"),
            on_toggle_magic=lambda: self._queue_ui_command("toggle_magic"),
            on_toggle_tool=lambda: self._queue_ui_command("toggle_tool"),
            on_toggle_action_mode=lambda: self._queue_ui_command("toggle_action_mode"),
            on_get_action_mode_state=self.game.action_mode_ui_state,
            on_aim_begin=lambda: self._queue_ui_command("aim_begin"),
            on_aim_axis=lambda x, y: self._queue_ui_command("aim_axis", x=x, y=y),
            on_aim_release=lambda held, fire=False: self._queue_ui_command(
                "aim_release", held_seconds=held, fire=fire
            ),
        )
        print("STARTUP CHECKPOINT: controls init OK")
        startup_trace("controls.init.ok")

        # Normal PytoUI overlay shared by both input backends. Keeping backpack
        # outside the gesture/UIControl implementation avoids duplicating modal
        # inventory logic and leaves the proven close path untouched.
        self.backpack_ui = BackpackUI(
            self.root,
            self.game,
            self.renderer,
            on_equipment_changed=None,
            on_modal_changed=self._set_inventory_modal,
        )
        startup_trace("backpack.init.ok")

        self.game_menu = GameMenuOverlay(
            self.root,
            self.game,
            on_start=self._finish_title_menu,
            on_close=self.close,
        )
        self.controls.set_hidden(True)
        self.backpack_ui.button.hidden = True

        # FIX78: one retained full-screen learning view, above all game menus.
        self.study_diagnostics = StudyDiagnostics(self.game.save_manager.save_dir)
        self.study_ui = StudyOverlay(
            self.root, self._queue_ui_command, self._set_study_modal,
            diagnostic_sink=self.study_diagnostics.report,
        )
        self.study_clock = ActivePlayClock()
        self._foreground_probe = ForegroundProbe()
        self._study_pause_active = False
        self._study_snapshot_revision = None

        # FIX38 audio is platform-owned: gameplay stays independent from Pyto's
        # `sound` module while Host manages the retained AVAudioPlayer objects.
        self.audio = AudioManager(
            self.game,
            project_root=os.environ.get("PYTO_RPG_PROJECT_ROOT", ""),
        )
        startup_trace("audio.init.ok")

        # Exposed to renderer/HUD for real-device verification.
        self.game.input_backend_name = (
            self.controls.backend_name
        )
        self._last_controls_signature = None
        self._last_platform_controls_reset_serial = int(
            getattr(self.game,"_platform_controls_reset_serial",0)
        )
        # FIX66 selector artwork reads authoritative state directly inside the
        # Metal snapshot.  No initial UIKit state transaction is required.
        self._last_controls_signature = None

        self.landscape_w = 852.0
        self.landscape_h = 393.0
        self.layout_locked = False

        # Renderer cadence and physics cadence are intentionally separated.
        self.physics_clock = FixedStepClock(
            hz=PHYSICS_FIXED_HZ,
            max_steps=PHYSICS_MAX_CATCHUP_STEPS,
            max_accumulator=PHYSICS_MAX_ACCUMULATOR,
        )
        self.display_pacer = DisplayPacer(
            fixed_hz=PHYSICS_FIXED_HZ,
            max_steps=PHYSICS_MAX_CATCHUP_STEPS,
            max_accumulator=PHYSICS_MAX_ACCUMULATOR,
            step_threshold=DISPLAY_SYNC_STEP_THRESHOLD,
            max_frame_dt=MAX_FRAME_DT,
        )
        self.game.physics_debug = {
            "hz": PHYSICS_FIXED_HZ,
            "steps": 0,
            "alpha": 0.0,
            "dropped_ms": 0.0,
            "sim_ms": 0.0,
            "render_submit_ms": 0.0,
        }

        # V0.6.0 real-device profiler. Values are accumulated without creating
        # per-frame diagnostic lists and printed only every few seconds.
        self._perf_window_start = time.monotonic()
        self._perf_frames = 0
        self._perf_sim_total = 0.0
        self._perf_render_total = 0.0
        self._perf_work_max = 0.0
        self._perf_catchup_frames = 0

        # V0.6.1 frame pacing state. The renderer's MTKView display callback
        # is the master clock; this background thread only advances after a
        # new VSync pulse has arrived.
        self._display_last_seq = 0
        self._display_last_time = None
        self._display_skipped_total = 0
        # FIX51: inventory is a true modal pause. While open, UIKit gets the
        # main-thread budget and the Python game loop does not keep producing
        # simulation/render snapshots behind the overlay.
        self._inventory_pause_active = False

        self.root.layout = self._layout

    def _queue_ui_command(self, name, **payload):
        """UIKit callback: publish intent and return immediately."""
        try:
            return self.game.enqueue_ui_command(name, **payload)
        except Exception as exc:
            print("CONTROL UI queue warning:", name, repr(exc))
        return False

    def _hide_background_system_hud(self):
        """Hide only non-gameplay diagnostic labels, without destroying them."""
        renderer = getattr(self, "renderer", None)
        if renderer is None:
            return
        for name in ("runtime_diag", "status", "movement_status"):
            view = getattr(renderer, name, None)
            if view is None:
                continue
            try:
                view.hidden = True
            except Exception:
                pass

    def _set_inventory_modal(self, opened):
        """One-time visibility switch; inventory content never becomes UIKit."""
        if bool(opened):
            # Hiding an active native joystick does not guarantee UIKit emits
            # ENDED/CANCELLED.  Force-release every hold/aim state first so a
            # stale finger cannot survive the modal transition.
            try:
                self.controls.reset()
            except Exception:
                pass
        try:
            self.controls.set_hidden(bool(opened))
        except Exception:
            pass
        try:
            self.renderer.set_hud_hidden(bool(opened))
        except Exception:
            pass
        if not bool(opened):
            # MetalKitRenderer restores its whole legacy HUD group on modal
            # close. Re-apply the gameplay-only policy once at that boundary.
            self._hide_background_system_hud()
        # FIX64: pause MTKView's continuous display link while the backpack is
        # modal. Inventory changes request a single frame; closing restores the
        # normal VSync-driven gameplay cadence.
        try:
            self.renderer.set_inventory_render_mode(bool(opened))
        except Exception:
            pass

    def _set_study_modal(self, opened):
        """MAIN THREAD only; world locking is already enforced on the worker."""
        # A renderer/bridge warning must never prevent the answer panel from
        # opening. The simulation and input-command gate are independent.
        blocked = bool(opened or getattr(self.game, "death_pending", False)
                       or getattr(getattr(self.game, "playtime_guard", None), "locked", False)
                       or self._death_menu_shown)
        operations = (
            self.controls.reset,
            lambda: self.controls.set_hidden(blocked),
            lambda: setattr(self.backpack_ui.button, "hidden", blocked),
            lambda: self.renderer.set_hud_hidden(blocked),
            lambda: self.renderer.set_inventory_render_mode(bool(opened)),
        )
        for operation in operations:
            try:
                operation()
            except Exception as exc:
                print("STUDY modal bridge warning:", repr(exc))
        if not blocked:
            self._hide_background_system_hud()

    def _reset_after_study_pause(self, state=None):
        self.physics_clock.accumulator = 0.0
        self.display_pacer.reset()
        self.study_clock.reset()
        self._display_last_time = None
        if state is not None:
            self._display_last_seq = int(getattr(state, "display_seq", self._display_last_seq))

    def _finish_title_menu(self):
        """Release the simulation only after New or Load was selected."""
        try:
            self.controls.set_hidden(False)
        except Exception:
            pass
        try:
            self.backpack_ui.button.hidden = False
        except Exception:
            pass
        self._startup_ready.set()

    def _show_death_menu(self):
        # FIX87: an already queued main-thread callback may outlive a policy
        # change or load. Never resurrect a stale death overlay.
        if (self.game.study.active
                or getattr(getattr(self.game, "playtime_guard", None), "locked", False)
                or not bool(getattr(self.game, "death_pending", False))
                or not self.game.game_settings.death_handling_enabled):
            self._death_menu_shown = False
            return
        self._death_menu_shown = True
        try:
            self.controls.reset()
            self.controls.set_hidden(True)
            self.backpack_ui.button.hidden = True
            self.renderer.set_hud_hidden(True)
        except Exception:
            pass
        self.game_menu.show_death()

    def _hide_death_menu(self):
        self._death_menu_shown = False
        self.game_menu.hide()
        blocked = self.game.learning_blocked()
        try:
            self.controls.set_hidden(blocked)
            self.backpack_ui.button.hidden = blocked
            self.renderer.set_hud_hidden(blocked)
            self._hide_background_system_hud()
        except Exception:
            pass
        # FIX66 has no root-level selector UIImageViews to restore on close.

    @staticmethod
    def _control_state_signature(state):
        state = dict(state or {})
        action = dict(state.get("action_mode", {}) or {})
        return (
            str(action.get("mode", "")),
            str(action.get("title", "")),
            str(action.get("weapon", "")),
            str(action.get("tool", "")),
            str(action.get("magic", "")),
            str(state.get("tool_id", "")),
            str(state.get("tool_name", "")),
            str(state.get("magic_id", "")),
            str(state.get("magic_name", "")),
        )

    def _sync_controls_from_game(self, force=False):
        """Compatibility probe with zero main-thread work.

        FIX66 renders selector state in Metal and therefore never schedules a
        UIKit transaction after a tool, magic or weapon change.
        """
        try:
            state = self.game.control_ui_state()
            signature = self._control_state_signature(state)
            changed = bool(force) or signature != self._last_controls_signature
            self._last_controls_signature = signature
            return changed
        except Exception as exc:
            print("CONTROL state probe warning:", repr(exc))
            return False

    def _service_platform_controls_reset(self):
        """Release native holds on UIKit's main thread when gameplay asks."""
        serial=int(getattr(self.game,"_platform_controls_reset_serial",0))
        if serial==int(self._last_platform_controls_reset_serial):return False
        self._last_platform_controls_reset_serial=serial

        def apply():
            if self.running and not self._closing:
                try:self.controls.reset()
                except Exception as exc:print("CONTROL platform reset warning:",repr(exc))
        mainthread.run_async(apply)
        return True

    def _layout(self, _sender=None):
        actual_w = max(1.0, float(self.root.width))
        actual_h = max(1.0, float(self.root.height))

        # FIX31.1 Universal iOS:
        # - iPhone keeps the proven fixed landscape viewport + strict lock.
        # - iPad/iPad Pro never mutates Pyto's controller class and follows the
        #   ACTUAL fullscreen/Stage-Manager viewport whenever it changes.
        ipad = is_ipad()
        if actual_h > actual_w:
            if self.layout_locked:
                try:
                    reassert_landscape(self.root)
                except Exception:
                    pass
            return

        first_landscape = not self.layout_locked
        if first_landscape or ipad:
            changed = (
                abs(float(self.landscape_w) - actual_w) > 1.0
                or abs(float(self.landscape_h) - actual_h) > 1.0
            )
            self.landscape_w = actual_w
            self.landscape_h = actual_h
            self.layout_locked = True
            if first_landscape:
                print("Landscape UI ready:", int(actual_w), "x", int(actual_h), "device=", device_family())
                startup_trace("layout.landscape.ready", "%dx%d %s" % (int(actual_w), int(actual_h), device_family()))
            elif ipad and changed:
                print("iPad viewport resized:", int(actual_w), "x", int(actual_h))
                startup_trace("layout.ipad.resize", "%dx%d" % (int(actual_w), int(actual_h)))

        # Apply the device-specific landscape policy. On iPad this is a safe,
        # one-shot UIWindowScene geometry request; on iPhone this retains the
        # existing controller lock.
        try:
            lock_landscape(self.root)
        except Exception as exc:
            if first_landscape:
                print("LANDSCAPE LOCK setup warning:", repr(exc))

        w = self.landscape_w
        h = self.landscape_h

        self.game.set_viewport(w, h)

        self.renderer.layout(w, h)
        self._hide_background_system_hud()
        self.controls.layout(w, h)
        self.backpack_ui.layout(w, h)
        self.game_menu.layout(w, h)
        self.study_ui.layout(w, h)

        if not self._layout_ready.is_set():
            # set_viewport() above already snapped the camera to the final
            # auto-loaded player position.  Only now may the first Metal
            # snapshot and physics step be published.
            self._layout_ready.set()
            print(
                "STARTUP CHECKPOINT: landscape layout ready",
                int(w), "x", int(h),
                "player=(%.1f, %.1f)" % (self.game.player.x, self.game.player.y),
            )

    def _loop(self):
        # V0.7.3.2: do not bootstrap or simulate while ui.show_view() is still
        # constructing/laying out the fullscreen hierarchy.  This removes the
        # visible "vegetation first -> terrain later -> player falls" startup
        # race and prevents hostile/environment damage before controls exist.
        wait_started = time.monotonic()
        while self.running and not self._layout_ready.wait(0.05):
            if time.monotonic() - wait_started > 8.0:
                # Diagnostic fallback only.  A valid landscape presentation
                # should always run _layout before this point.
                print("STARTUP WARNING: landscape layout wait timed out")
                break
        if not self.running:
            return
        while self.running and not self._startup_ready.wait(0.05):
            pass
        if not self.running:
            return
        # Allow the layout transaction to finish on UIKit before the first
        # MTKView request. No gameplay time is advanced during this delay.
        time.sleep(0.02)

        # V0.6.2.8 bootstrap rule:
        # NEVER wait for the first MTKView display pulse before publishing the
        # first game snapshot.  Some Pyto/MTKView combinations do not start the
        # delegate cadence until there is drawable work, which previously made
        # a circular wait: game waits VSync -> MTKView waits work -> snapshot
        # stays None forever.
        state = getattr(self.renderer, "state", None)
        prev_fallback = time.monotonic()
        fallback_active = False
        no_pulse_count = 0

        try:
            self.game.render_alpha = 1.0
            self.game.physics_debug["steps"] = 0
            self.game.physics_debug["alpha"] = 1.0
            self.game.physics_debug["display_seq"] = 0
            self.game.physics_debug["display_skipped"] = 0
            self.game.loop_stage = "BOOTSTRAP SNAPSHOT"
            print("STARTUP CHECKPOINT: bootstrap render begin")
            self.renderer.render(self.game)
            self.game.loop_stage = "BOOTSTRAP SNAPSHOT OK"
            try:
                self.renderer.request_draw(force=True)
            except Exception:
                pass
            print("STARTUP CHECKPOINT: bootstrap requestDraw OK")
            print("V0.6.3 bootstrap snapshot published")
        except Exception as exc:
            self.game.last_render_error = repr(exc)
            self.game.loop_stage = "BOOTSTRAP ERROR: " + repr(exc)
            print("V0.6.3 BOOTSTRAP ERROR:", repr(exc))

        while self.running:
            inventory_open = bool(getattr(getattr(self, "backpack_ui", None), "opened", False))
            # FIX85: the modal command pump must not execute a queued shot
            # while inventory pauses the world. Written on the worker thread.
            self.game._input_modal_blocked = inventory_open
            # Use real foreground playtime once per loop, never the fixed-step
            # dt (slow devices must still receive a challenge after 3 minutes).
            study = self.game.study
            guard = self.game.playtime_guard
            if study.entry_handled:
                guard.ensure_restored()
            clock_now = time.monotonic()
            foreground = self._foreground_probe.poll(clock_now)
            was_playing = self.study_clock.was_playing
            play_dt = self.study_clock.sample(
                clock_now,
                foreground and not inventory_open and not self._study_pause_active
                and self.game.study_countable(),
            )
            if play_dt > 0.0:
                self.game.advance_study_playtime(play_dt)
            elif was_playing and not self.study_clock.was_playing:
                # Commit exact elapsed at background/menu boundaries. An
                # abrupt OS kill is covered by the five-second reservation.
                guard.checkpoint(force=True)
                study.checkpoint(force=True)

            if self.game.learning_blocked() or self._study_pause_active:
                if not self._study_pause_active:
                    self._study_pause_active = True
                    guard.checkpoint(force=True)
                    study.checkpoint(force=True)
                    self._reset_after_study_pause(state)
                    self.game._cancel_study_input(clear_queue=True)
                    self.game._request_platform_controls_reset()
                    # Freeze the last world snapshot and stop existing sounds.
                    # AudioManager lazily restarts BGM on normal gameplay update.
                    try:
                        self.audio.close()
                    except Exception as exc:
                        self.study_diagnostics.report('modal_entry_error', {'error_type':type(exc).__name__})
                self.game.loop_stage = "PLAYTIME LOCKED" if guard.locked else "STUDY PAUSED"
                # FIX101: an exception in a modal command/snapshot must not
                # silently terminate the worker and strand all answer keys.
                pump_learning_modal(self.game, self.study_ui, self.study_diagnostics)
                self._study_snapshot_revision = study.revision
                # UI hide acknowledgment is a barrier. The worker cannot
                # resume behind a still-visible modal or replay paused time.
                if not self.game.learning_blocked() and self.study_ui.settled_hidden:
                    self._study_pause_active = False
                    self.game._cancel_study_input(clear_queue=True)
                    self._reset_after_study_pause(state)
                    self.game.loop_stage = "STUDY RESUME"
                prev_fallback = time.monotonic()
                time.sleep(0.025)
                continue

            if inventory_open:
                if not self._inventory_pause_active:
                    self._inventory_pause_active = True
                    try:
                        self.physics_clock.accumulator = 0.0
                        self.display_pacer.reset()
                    except Exception:
                        pass
                    self._display_last_time = None
                    prev_fallback = time.monotonic()
                    try:
                        self.game.input.reset()
                    except Exception:
                        pass
                    self.game._cancel_aim_release()
                    self.game.aim_active = False
                    self.game.loop_stage = "BACKPACK PAUSED"
                # FIX62 modal-only pump: world simulation remains frozen, but
                # inventory intents are consumed on this authoritative thread
                # and republished as one immutable Metal batch.
                try:
                    processed = self.game._process_ui_commands()
                    if processed:
                        self.backpack_ui.refresh_from_game()
                    self._service_platform_controls_reset()
                except Exception as exc:
                    print("BACKPACK modal command warning:", repr(exc))
                time.sleep(0.025)
                continue
            elif self._inventory_pause_active:
                # Resume without replaying the time spent in the inventory.
                self._inventory_pause_active = False
                try:
                    self.physics_clock.accumulator = 0.0
                    self.display_pacer.reset()
                except Exception:
                    pass
                # Discard display pulses produced by manual backpack redraws;
                # the first gameplay step must wait for a fresh VSync after the
                # MTKView continuous cadence is restored.
                if state is not None:
                    try:
                        self._display_last_seq = int(state.display_seq)
                    except Exception:
                        pass
                self._display_last_time = None
                prev_fallback = time.monotonic()
                self.game.loop_stage = "BACKPACK RESUME"

            used_display_pulse = False
            if DISPLAY_SYNC_GAME_LOOP and state is not None:
                # Keep this wait short. If MTKView is not emitting callbacks,
                # immediately fall back to the fixed-step clock instead of
                # freezing the entire game at 'waiting snapshot'.
                pulse_wait = min(
                    max(0.004, 1.35 / max(1.0, float(TARGET_FPS))),
                    max(0.004, float(DISPLAY_SYNC_WAIT_TIMEOUT)),
                )
                seq, pulse_time = state.wait_for_display_pulse(
                    self._display_last_seq,
                    timeout=pulse_wait,
                )
                if not self.running:
                    break

                if int(seq) > int(self._display_last_seq):
                    used_display_pulse = True
                    fallback_active = False
                    no_pulse_count = 0
                    skipped = max(0, int(seq) - int(self._display_last_seq) - 1)
                    self._display_skipped_total += skipped
                    self._display_last_seq = int(seq)

                    if self._display_last_time is None:
                        real_dt = 1.0 / max(1.0, float(TARGET_FPS))
                    else:
                        real_dt = max(
                            0.0,
                            min(float(pulse_time) - self._display_last_time, MAX_FRAME_DT),
                        )
                    self._display_last_time = float(pulse_time)
                    prev_fallback = time.monotonic()
                    steps = self.display_pacer.advance(real_dt)
                    self.game.loop_stage = "VSYNC"
                else:
                    # No MTKView callback: run a normal fixed-step fallback.
                    # This is intentionally a compatibility path, not catch-up
                    # replay.  It keeps input/physics/render alive and requests
                    # an MTKView draw on the main thread after publishing.
                    no_pulse_count += 1
                    fallback_active = True
                    frame_now = time.monotonic()
                    real_dt = max(0.0, min(frame_now - prev_fallback, MAX_FRAME_DT))
                    prev_fallback = frame_now
                    steps = self.physics_clock.advance(real_dt)
                    self.game.loop_stage = "FALLBACK NO-VSYNC x%d" % no_pulse_count
            else:
                # Compatibility fallback for a renderer without display pulses.
                fallback_active = True
                frame_now = time.monotonic()
                real_dt = max(0.0, min(frame_now - prev_fallback, MAX_FRAME_DT))
                prev_fallback = frame_now
                steps = self.physics_clock.advance(real_dt)
                self.game.loop_stage = "FIXED CLOCK"

            frame_start = time.monotonic()

            try:
                # Python simulation remains authoritative. In display-sync mode
                # we render the newest complete state (alpha=1) instead of using
                # a residual accumulator from an unrelated clock.
                sim_start = time.monotonic()
                for _ in range(steps):
                    self.game.update(self.physics_clock.fixed_dt)
                    if self.game.learning_blocked():
                        break
                    self.game.respawn_if_needed()
                    death_pending = bool(getattr(self.game, "death_pending", False))
                    if death_pending and not self._death_menu_shown:
                        self._death_menu_shown = True
                        mainthread.run_async(self._show_death_menu)
                    elif (not death_pending) and self._death_menu_shown:
                        self._death_menu_shown = False
                        mainthread.run_async(self._hide_death_menu)
                sim_ms = (time.monotonic() - sim_start) * 1000.0
                if self.game.learning_blocked():
                    continue

                pd = self.game.physics_debug
                pd["hz"] = self.physics_clock.hz
                pd["steps"] = steps
                pd["alpha"] = (
                    1.0
                    if used_display_pulse
                    else self.physics_clock.alpha
                )
                pd["dropped_ms"] = (
                    max(0.0, self.display_pacer.accumulator) * 1000.0
                    if used_display_pulse
                    else self.physics_clock.dropped_time * 1000.0
                )
                pd["clock_mode"] = "vsync" if used_display_pulse else "fallback"
                pd["sim_ms"] = sim_ms
                pd["display_seq"] = int(self._display_last_seq)
                pd["display_skipped"] = int(self._display_skipped_total)
                self.game.render_alpha = pd["alpha"]

                self._service_platform_controls_reset()

                # FIX66: selector visuals are part of this frame's Metal
                # snapshot; no UIKit state synchronization runs here.

                try:
                    self.audio.update(real_dt)
                except Exception as exc:
                    print("AUDIO LOOP warning:", repr(exc))

                render_start = time.monotonic()
                self.renderer.render(self.game)
                render_ms = (time.monotonic() - render_start) * 1000.0
                pd["render_submit_ms"] = render_ms

                # If the MTKView automatic cadence is missing, request one
                # draw explicitly on the main thread. Once normal VSync pulses
                # resume this becomes unnecessary and is skipped.
                if fallback_active:
                    try:
                        self.renderer.request_draw(force=True)
                    except Exception:
                        pass

            except Exception as exc:
                self.game.last_render_error = repr(exc)
                print("GAME LOOP ERROR:", repr(exc))
                try:
                    self.game.input.end_frame()
                except Exception:
                    pass
                self.game.events.emit(
                    "message",
                    text="Game loop error: " + repr(exc),
                )
                try:
                    self.renderer.render(self.game)
                except Exception:
                    pass

            elapsed = time.monotonic() - frame_start

            try:
                self._perf_frames += 1
                self._perf_sim_total += float(self.game.physics_debug.get("sim_ms", 0.0))
                self._perf_render_total += float(self.game.physics_debug.get("render_submit_ms", 0.0))
                self._perf_work_max = max(self._perf_work_max, elapsed * 1000.0)
                if int(self.game.physics_debug.get("steps", 0)) > 1:
                    self._perf_catchup_frames += 1

                perf_now = time.monotonic()
                perf_elapsed = perf_now - self._perf_window_start
                if perf_elapsed >= max(1.0, float(PERF_RUNTIME_LOG_SECONDS)):
                    n = max(1, self._perf_frames)
                    rs = getattr(self.renderer, "state", None)
                    metal_fps = float(getattr(rs, "fps", 0.0)) if rs is not None else 0.0
                    terrain_q = int(getattr(rs, "last_terrain_quads", 0)) if rs is not None else 0
                    water_q = int(getattr(rs, "last_water_quads", 0)) if rs is not None else 0
                    env_q = int(getattr(rs, "last_environment_quads", 0)) if rs is not None else 0
                    dyn_q = int(getattr(rs, "last_dynamic_quads", 0)) if rs is not None else 0
                    print(
                        "PERF V0.6.3 | sim %.2fms | submit %.2fms | workMax %.2fms | "
                        "catchup %d/%d | skippedPulse %d | Metal %.1ffps | Q T/W/E/D=%d/%d/%d/%d" % (
                            self._perf_sim_total / n,
                            self._perf_render_total / n,
                            self._perf_work_max,
                            self._perf_catchup_frames,
                            n,
                            self._display_skipped_total,
                            metal_fps,
                            terrain_q, water_q, env_q, dyn_q,
                        )
                    )
                    self._perf_window_start = perf_now
                    self._perf_frames = 0
                    self._perf_sim_total = 0.0
                    self._perf_render_total = 0.0
                    self._perf_work_max = 0.0
                    self._perf_catchup_frames = 0
            except Exception:
                pass

            # Normal VSync mode blocks on the next display condition. In
            # fallback mode pace the loop to TARGET_FPS so it does not spin.
            if (not DISPLAY_SYNC_GAME_LOOP) or fallback_active:
                target_render_dt = 1.0 / max(1.0, float(TARGET_FPS))
                sleep_for = target_render_dt - elapsed
                if sleep_for > 0:
                    time.sleep(sleep_for)

        # FIX78 persist a clean exit on the authoritative thread. Periodic
        # checkpoints also protect against iOS force termination.
        self.game.study.checkpoint(force=True)
        self.game.playtime_guard.checkpoint(force=True)

    def _finalize_after_view_closed(self):
        """Release runtime resources only after UIKit actually disappears."""
        # ui.show_view() returns only after Pyto's internal ViewController runs
        # viewWillDisappear and clears PyView.isPresented.  Therefore cleanup
        # remains here and never runs from the X button callback itself.
        self._close_completed.set()
        self.running = False
        try:
            self.study_ui.close()
            self.study_diagnostics.close()
        except Exception:
            pass

        try:
            self.controls.reset()
        except Exception as exc:
            print("Close controls reset error:", repr(exc))
        try:
            self.game.input.reset()
        except Exception as exc:
            print("Close input reset error:", repr(exc))
        try:
            self.audio.close()
        except Exception as exc:
            print("Close audio error:", repr(exc))
        try:
            self.renderer.close()
        except Exception as exc:
            print("Close renderer error:", repr(exc))

    def run(self):
        thread = threading.Thread(
            target=self._loop,
            name="PytoRPGLoop",
            daemon=True,
        )
        thread.start()

        try:
            # Keep Pyto's normal fullscreen presentation because it already
            # handles orientation, safe-area sizing and the Metal child view.
            # V0.7.1.6 changes only the dismissal path.
            startup_trace("show_view.begin")
            print("STARTUP CHECKPOINT: show_view begin")
            ui.show_view(
                self.root,
                PRESENT_FULLSCREEN,
            )
            startup_trace("show_view.returned")
        finally:
            self._finalize_after_view_closed()

    def _root_owner_view_controller(self):
        """Return the exact UIViewController that owns the Pyto root UIView.

        Pyto's ConsoleViewController.viewController(...) creates a container
        UIViewController, adds PyView.managed as its first subview, and presents
        that controller.  On the MAIN build PyView itself does not retain a
        public viewController reference, so walking the native responder chain
        is more reliable than guessing the app's top controller.

        Must be called on the UIKit main thread.
        """
        try:
            responder = self.root.__py_view__.managed
        except Exception as exc:
            print("GAME CLOSE: native root lookup error:", repr(exc))
            return None

        for _ in range(16):
            try:
                responder = responder.nextResponder
            except Exception:
                responder = None

            if responder is None:
                return None

            try:
                # UIViewController exposes both of these selectors/properties;
                # ordinary UIViews in the responder chain do not.
                getattr(responder, "presentingViewController")
                getattr(responder, "dismissViewControllerAnimated")
                getattr(responder, "view")
                return responder
            except Exception:
                pass

        return None

    def _top_presented_view_controller(self):
        """Fallback only: find the window's currently visible controller."""
        try:
            native = self.root.__py_view__.managed
            window = native.window
            if window is None:
                return None
            controller = window.rootViewController
            if controller is None:
                return None

            for _ in range(16):
                try:
                    presented = controller.presentedViewController
                except Exception:
                    presented = None
                if presented is None:
                    break
                controller = presented
            return controller
        except Exception as exc:
            print("GAME CLOSE: top-controller lookup error:", repr(exc))
            return None

    def _dismiss_exact_controller_on_main(self, allow_top_fallback=False):
        """Issue a fully non-blocking UIKit dismiss on the main thread.

        There is deliberately NO PyView.close(), semaphore wait, renderer stop,
        input reset, or manual isPresented write here.  Pyto's own internal
        ViewController.viewWillDisappear is responsible for setting
        PyView.isPresented=False; that naturally releases ui.show_view().
        """
        try:
            owner = self._root_owner_view_controller()
            if owner is not None:
                try:
                    presenter = owner.presentingViewController
                except Exception:
                    presenter = None

                if presenter is not None:
                    # Dismiss from the presenter.  With animated=False this is
                    # a direct UIKit hierarchy operation and does not wait on a
                    # Python semaphore or completion callback.
                    presenter.dismissViewControllerAnimated(
                        False,
                        completion=None,
                    )
                    print("GAME CLOSE: exact Pyto owner dismiss sent")
                    return True

                # If the controller is already in transition, asking the exact
                # owner to dismiss itself is a safe second form of the same
                # request.
                owner.dismissViewControllerAnimated(
                    False,
                    completion=None,
                )
                print("GAME CLOSE: exact owner self-dismiss sent")
                return True

            print("GAME CLOSE: exact owner not found")

            if not allow_top_fallback:
                return False

            controller = self._top_presented_view_controller()
            if controller is None:
                print("GAME CLOSE FALLBACK: no visible controller")
                return False

            try:
                presenter = controller.presentingViewController
            except Exception:
                presenter = None

            if presenter is None:
                print("GAME CLOSE FALLBACK: visible controller has no presenter")
                return False

            presenter.dismissViewControllerAnimated(
                False,
                completion=None,
            )
            print("GAME CLOSE FALLBACK: top presented controller dismiss sent")
            return True
        except Exception as exc:
            print("GAME CLOSE DISMISS ERROR:", repr(exc))
            return False

    def _close_retry_worker(self):
        """Retry dismissal without ever blocking Python or stopping the game."""
        # Event.wait releases the GIL.  Unlike V0.7.1.5, this thread NEVER calls
        # PyView.close() and never waits inside a UIKit/Python semaphore bridge.
        if self._close_completed.wait(0.35):
            return

        print("GAME CLOSE RETRY: exact controller still presented")
        try:
            mainthread.run_async(
                lambda: self._dismiss_exact_controller_on_main(False)
            )
        except Exception as exc:
            print("GAME CLOSE RETRY SCHEDULE ERROR:", repr(exc))

        if self._close_completed.wait(0.55):
            return

        print("GAME CLOSE RETRY: using top-controller fallback")
        try:
            mainthread.run_async(
                lambda: self._dismiss_exact_controller_on_main(True)
            )
        except Exception as exc:
            print("GAME CLOSE FALLBACK SCHEDULE ERROR:", repr(exc))

        if self._close_completed.wait(0.80):
            return

        # Never convert a failed dismissal into a frozen half-closed game.
        # Re-arm X so the user can try again; simulation and Metal remain alive.
        self._closing = False
        print("GAME CLOSE: not confirmed; runtime kept alive and X re-armed")

    def close(self, sender=None):
        """Dismiss the exact Pyto fullscreen owner, fully non-blocking.

        V0.7.1.6 removes the V0.7.1.5 worker that called raw PyView.close().
        The X action now only queues one UIKit dismiss to the next main-loop
        turn.  Pyto's own ViewController then sets isPresented=False from
        viewWillDisappear, so show_view() returns through its normal path.
        """
        if self._closing:
            print("GAME CLOSE REQUEST: close already in progress")
            return True

        self._closing = True
        self._close_completed.clear()
        print("GAME CLOSE REQUEST: V0.7.1.7 inherited exact-controller nonblocking path")

        try:
            # The Pyto Button callback normally arrives on the main thread.
            # Deferring one queue turn avoids dismissing in the middle of the
            # current UIControl/gesture dispatch.
            mainthread.run_async(
                lambda: self._dismiss_exact_controller_on_main(False)
            )
        except Exception as exc:
            self._closing = False
            print("GAME CLOSE SCHEDULE ERROR:", repr(exc))
            return False

        try:
            retry = threading.Thread(
                target=self._close_retry_worker,
                name="PytoRPGCloseRetry",
                daemon=True,
            )
            retry.start()
        except Exception as exc:
            print("GAME CLOSE RETRY START ERROR:", repr(exc))

        return True
