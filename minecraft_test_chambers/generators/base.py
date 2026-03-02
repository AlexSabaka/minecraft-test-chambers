"""base.py — Abstract base class for all feature generators."""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Any


class FeatureGenerator(ABC):
    """Abstract base for all chamber feature generators.

    Each subclass accepts a config dict (from the YAML `features` section) and a
    seeded ``random.Random`` instance, then produces a list of RCON command strings.
    """

    def __init__(self, config: dict[str, Any], rng: random.Random) -> None:
        self.config = config
        self.rng = rng

    @abstractmethod
    def generate(self) -> list[str]:
        """Return a list of raw Minecraft RCON command strings (without leading '/')."""

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _rand_pos(
        self,
        area: list[int],  # [x1, y1, z1, x2, y2, z2]
    ) -> tuple[int, int, int]:
        """Pick a random (x, y, z) inside *area*.

        *area* must be a list of exactly 6 integers: ``[x1, y1, z1, x2, y2, z2]``.
        Both endpoints are inclusive.
        """
        if len(area) != 6:
            raise ValueError(f"area must have 6 elements [x1,y1,z1,x2,y2,z2], got {area}")
        x1, y1, z1, x2, y2, z2 = area
        x = self.rng.randint(min(x1, x2), max(x1, x2))
        y = self.rng.randint(min(y1, y2), max(y1, y2))
        z = self.rng.randint(min(z1, z2), max(z1, z2))
        return x, y, z

    def _resolve_count(self, count: int | list[int]) -> int:
        """Resolve a count that may be a fixed int or a [min, max] range."""
        if isinstance(count, list):
            lo, hi = count[0], count[1]
            return self.rng.randint(lo, hi)
        return int(count)
