# -*- coding: utf-8 -*-
import math

from config import (
    GAME_TIME_SCALE,
    GAME_START_DAY,
    GAME_START_HOUR,
    GAME_START_MINUTE,
    GAME_DAY_SECONDS,
    SUNRISE_HOUR,
    SUNSET_HOUR,
)


class GameTimeSystem:
    """Deterministic absolute world clock.

    The physics loop still receives real/fixed dt. This system maps that dt
    into game-time seconds so ecology/weather/daylight can use an absolute
    timeline instead of anonymous frame counts.
    """

    def __init__(self):
        self.time_scale = float(GAME_TIME_SCALE)
        self.start_day = int(GAME_START_DAY)
        self.start_hour = int(GAME_START_HOUR)
        self.start_minute = int(GAME_START_MINUTE)

        self.elapsed_real_seconds = 0.0
        self.elapsed_game_seconds = 0.0
        self.last_game_dt = 0.0

        self.start_of_day_seconds = (
            self.start_hour * 3600.0
            + self.start_minute * 60.0
        )

    def update(self, real_dt):
        real_dt = max(0.0, float(real_dt))
        self.elapsed_real_seconds += real_dt
        self.last_game_dt = real_dt * self.time_scale
        self.elapsed_game_seconds += self.last_game_dt
        return self.last_game_dt

    @property
    def absolute_game_seconds(self):
        return (
            (self.start_day - 1) * GAME_DAY_SECONDS
            + self.start_of_day_seconds
            + self.elapsed_game_seconds
        )

    @property
    def day_index(self):
        return 1 + int(
            self.absolute_game_seconds
            // GAME_DAY_SECONDS
        )

    @property
    def seconds_of_day(self):
        return self.absolute_game_seconds % GAME_DAY_SECONDS

    @property
    def hour_float(self):
        return self.seconds_of_day / 3600.0

    @property
    def hour(self):
        return int(self.hour_float) % 24

    @property
    def minute(self):
        return int(
            self.seconds_of_day // 60.0
        ) % 60

    @property
    def second(self):
        return int(self.seconds_of_day) % 60

    @property
    def formatted_time(self):
        return f"D{self.day_index} {self.hour:02d}:{self.minute:02d}"

    def daylight_factor(self):
        """0..1 smooth sun elevation proxy between sunrise/sunset."""
        hour = self.hour_float
        if hour <= SUNRISE_HOUR or hour >= SUNSET_HOUR:
            return 0.0

        phase = (
            (hour - SUNRISE_HOUR)
            / max(0.01, SUNSET_HOUR - SUNRISE_HOUR)
        )
        return max(0.0, math.sin(phase * math.pi))

    def export_state(self):
        return {
            "time_scale": self.time_scale,
            "start_day": self.start_day,
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "elapsed_real_seconds": self.elapsed_real_seconds,
            "elapsed_game_seconds": self.elapsed_game_seconds,
        }

    def import_state(self, data):
        if not isinstance(data, dict):
            return

        self.time_scale = float(
            data.get(
                "time_scale",
                self.time_scale,
            )
        )
        self.start_day = int(
            data.get(
                "start_day",
                self.start_day,
            )
        )
        self.start_hour = int(
            data.get(
                "start_hour",
                self.start_hour,
            )
        )
        self.start_minute = int(
            data.get(
                "start_minute",
                self.start_minute,
            )
        )
        self.elapsed_real_seconds = float(
            data.get(
                "elapsed_real_seconds",
                0.0,
            )
        )
        self.elapsed_game_seconds = float(
            data.get(
                "elapsed_game_seconds",
                0.0,
            )
        )
        self.last_game_dt = 0.0
        self.start_of_day_seconds = (
            self.start_hour * 3600.0
            + self.start_minute * 60.0
        )
