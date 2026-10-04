"""A quiet, predictable timer for tiny rituals; it never calls the LLM."""
from __future__ import annotations

from dataclasses import dataclass
import random
import time


def quiet_hour(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


@dataclass
class Ambient:
    minimum: float = 600
    maximum: float = 1200
    quiet_start: int = 22
    quiet_end: int = 8
    next_at: float = 0

    def __post_init__(self):
        if not 30 <= self.minimum <= self.maximum <= 86400:
            raise ValueError("Ritual interval must be 30..86400 seconds, minimum <= maximum")
        if not 0 <= self.quiet_start <= 23 or not 0 <= self.quiet_end <= 23:
            raise ValueError("Quiet hours must be 0..23")
        self.schedule(time.monotonic())

    def schedule(self, now: float):
        self.next_at = now + random.uniform(self.minimum, self.maximum)

    def due(self, now: float, hour: int, last_interaction: float, busy: bool, enabled: bool) -> bool:
        if now < self.next_at:
            return False
        # Skip a missed ritual instead of accumulating a backlog.
        self.schedule(now)
        return (enabled and not busy and now - last_interaction >= 120
                and not quiet_hour(hour, self.quiet_start, self.quiet_end))
