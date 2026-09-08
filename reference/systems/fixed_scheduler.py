# -*- coding: utf-8 -*-
class FixedRateTask:
    def __init__(self, hz, max_steps=4, phase=0.0):
        self.step = 1.0 / max(0.001, float(hz))
        self.max_steps = max(1, int(max_steps))
        # Optional phase delay lets expensive low-frequency systems be spread
        # across different 60 Hz frames instead of synchronizing into one
        # periodic CPU spike. phase=0 keeps legacy timing.
        phase = max(0.0, min(0.999, float(phase)))
        self.accumulator = -self.step * phase

    def consume(self, dt):
        self.accumulator += max(0.0, float(dt))
        count = 0
        while self.accumulator >= self.step and count < self.max_steps:
            self.accumulator -= self.step
            count += 1
            yield self.step
        if count >= self.max_steps:
            self.accumulator = min(self.accumulator, self.step)
