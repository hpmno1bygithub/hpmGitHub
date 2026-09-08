# -*- coding: utf-8 -*-
"""Low-rate main-thread UIApplication probe; never call UIKit on the game thread."""
import time
import mainthread


class ForegroundProbe:
    def __init__(self):
        self.active = True
        self.sampled_at = time.monotonic()
        self.requested_at = 0.0
        self.pending = False
        self._warned = False

    def poll(self, now=None):
        now = time.monotonic() if now is None else float(now)
        if not self.pending and now-self.requested_at >= .5:
            self.pending = True
            self.requested_at = now
            try:
                mainthread.run_async(self._sample)
            except Exception:
                self.pending = False
        # A suspended/unresponsive main thread is not active playtime.
        return bool(self.active and now-self.sampled_at < 1.5)

    def _sample(self):
        try:
            from UIKit import UIApplication
            app = UIApplication.sharedApplication
            if callable(app):
                app = app()
            state = app.applicationState
            if callable(state):
                state = state()
            self.active = int(state) == 0  # UIApplicationStateActive
        except Exception as exc:
            # Pyto normally exposes UIApplication as a property. Keep a
            # compatibility fallback for bridges missing the optional probe;
            # ActivePlayClock still ignores long suspend gaps.
            self.active = True
            if not self._warned:
                print('STUDY foreground probe fallback:',repr(exc))
                self._warned = True
        finally:
            self.sampled_at = time.monotonic()
            self.pending = False
