# -*- coding: utf-8 -*-
"""Display-synchronised fixed-step pacing.

V0.6.1 removes the independent Python time.sleep() render clock.  MTKView
presentation pulses wake the simulation thread, while this small pacer keeps
physics at its requested fixed rate even if iOS presents at 30/60/120 Hz.
"""


class DisplayPacer:
    def __init__(
        self,
        fixed_hz=60.0,
        max_steps=2,
        max_accumulator=0.05,
        step_threshold=0.70,
        max_frame_dt=0.05,
    ):
        self.fixed_hz = max(1.0, float(fixed_hz))
        self.fixed_dt = 1.0 / self.fixed_hz
        self.max_steps = max(1, int(max_steps))
        self.max_accumulator = max(self.fixed_dt, float(max_accumulator))
        self.step_threshold = max(0.50, min(0.95, float(step_threshold)))
        self.max_frame_dt = max(self.fixed_dt, float(max_frame_dt))
        self.accumulator = 0.0

    def reset(self):
        self.accumulator = 0.0

    def advance(self, real_dt):
        """Return fixed physics steps for one new display pulse.

        A 0.70-step phase threshold intentionally absorbs small VSync timestamp
        noise. At nominal 60 Hz, 16.4/16.9ms pulses still produce one step each;
        at 120 Hz, two 8.3ms pulses combine into one 60Hz step; at 30 Hz, one
        display pulse can produce two physics steps, bounded by max_steps.
        """
        dt = max(0.0, min(float(real_dt), self.max_frame_dt))
        self.accumulator += dt
        threshold = self.fixed_dt * self.step_threshold

        steps = 0
        while self.accumulator + 1e-12 >= threshold and steps < self.max_steps:
            self.accumulator -= self.fixed_dt
            steps += 1

        # VSync timestamps have small scheduling noise. Once a complete fixed
        # step lands within +/- 1/4 step of phase, snap that tiny residual to
        # zero. This prevents random 16.4/16.9ms noise from slowly integrating
        # into an artificial 0-step then 2-step visual hitch. It still allows
        # 120Hz pulses to accumulate in pairs and 30Hz pulses to produce 2.
        if steps > 0 and abs(self.accumulator) <= self.fixed_dt * 0.25:
            self.accumulator = 0.0

        # Do not replay a stale backlog after a long subsystem stall. Keep only
        # a small phase error so the next presentation returns to cadence.
        self.accumulator = max(
            -self.fixed_dt * 0.35,
            min(self.accumulator, self.max_accumulator),
        )
        return steps
