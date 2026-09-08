# -*- coding: utf-8 -*-


class FixedStepClock:
    """Stable fixed-timestep accumulator independent of renderer cadence."""

    def __init__(self, hz=120.0, max_steps=6, max_accumulator=0.10):
        self.hz = max(1.0, float(hz))
        self.fixed_dt = 1.0 / self.hz
        self.max_steps = max(1, int(max_steps))
        self.max_accumulator = max(self.fixed_dt, float(max_accumulator))
        self.accumulator = 0.0
        self.dropped_time = 0.0
        self.total_steps = 0
        self.last_steps = 0

    def advance(self, real_dt):
        dt = max(0.0, min(float(real_dt), self.max_accumulator))
        self.accumulator = min(self.max_accumulator, self.accumulator + dt)

        available = int(self.accumulator / self.fixed_dt)
        steps = min(available, self.max_steps)
        self.accumulator -= steps * self.fixed_dt

        if available > self.max_steps:
            kept = min(self.accumulator, self.fixed_dt * 0.999)
            self.dropped_time += max(0.0, self.accumulator - kept)
            self.accumulator = kept

        self.last_steps = steps
        self.total_steps += steps
        return steps

    @property
    def alpha(self):
        return max(0.0, min(1.0, self.accumulator / self.fixed_dt))
