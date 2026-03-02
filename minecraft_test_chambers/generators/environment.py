"""environment.py — Sets time, weather, biome, difficulty, and per-chamber gamerules."""
from __future__ import annotations

import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator

# Maps human-friendly strings → Minecraft time values (ticks)
_TIME_MAP: dict[str, int] = {
    "day":      6000,
    "noon":     6000,
    "sunrise":  0,
    "dawn":     0,      # alias for sunrise — first light
    "sunset":   12000,
    "dusk":     12000,  # alias for sunset
    "night":    18000,
    "midnight": 18000,
}

# Maps weather → Minecraft weather command arg
_WEATHER_MAP: dict[str, str] = {
    "clear":   "clear",
    "rain":    "rain",
    "thunder": "thunder",
}

# Maps difficulty labels
_DIFFICULTY_MAP: dict[str, str] = {
    "peaceful": "peaceful",
    "easy":     "easy",
    "normal":   "normal",
    "hard":     "hard",
}


class EnvironmentGenerator(FeatureGenerator):
    """Emits commands that establish the environmental context of a chamber.

    Supported top-level YAML keys (all optional):
        time: day|night|noon|sunrise|sunset|midnight   (default: day)
        weather: clear|rain|thunder                    (default: clear)
        biome: minecraft:<id>                          (default: no fillbiome call)
        difficulty: peaceful|easy|normal|hard          (default: normal)
        gamerule_overrides:
          <rule>: <value>
    """

    # Play-area radius; fillbiome is applied in a generous window around origin
    _BIOME_RADIUS = 32

    def __init__(self, config: dict[str, Any], rng: random.Random, tp_y: int = -57) -> None:
        super().__init__(config, rng)
        self.tp_y = tp_y

    def generate(self) -> list[str]:
        cmds: list[str] = []

        # Time
        time_key = str(self.config.get("time", "day")).lower()
        ticks = _TIME_MAP.get(time_key, 6000)
        cmds.append(f"time set {ticks}")

        # Weather
        weather_key = str(self.config.get("weather", "clear")).lower()
        weather_cmd = _WEATHER_MAP.get(weather_key, "clear")
        cmds.append(f"weather {weather_cmd} 1000000")   # 1 M ticks ≈ freeze

        # Biome — paint a square region centred on 0,0 at play-floor level
        biome = self.config.get("biome")
        if biome:
            r = self._BIOME_RADIUS
            y_min = self.tp_y - 2
            y_max = self.tp_y + 10
            cmds.append(f"fillbiome -{r} {y_min} -{r} {r} {y_max} {r} {biome}")

        # Difficulty
        diff_key = str(self.config.get("difficulty", "normal")).lower()
        diff_val = _DIFFICULTY_MAP.get(diff_key, "normal")
        cmds.append(f"difficulty {diff_val}")

        # Per-chamber gamerule overrides
        for rule, value in self.config.get("gamerule_overrides", {}).items():
            cmds.append(f"gamerule {rule} {value}")

        return cmds
