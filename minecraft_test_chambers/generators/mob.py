"""mob.py — Procedural mob summoning with optional difficulty scaling."""
from __future__ import annotations

import math
import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator

# Difficulty → health/speed multiplier
_DIFFICULTY_SCALE: dict[str, float] = {
    "peaceful": 0.0,
    "easy":     0.75,
    "normal":   1.0,
    "hard":     1.5,
    "extreme":  2.0,
}

# Per-mob base health (half-hearts)
_BASE_HEALTH: dict[str, float] = {
    "zombie":       20.0,
    "skeleton":     20.0,
    "spider":       16.0,
    "creeper":      20.0,
    "witch":        26.0,
    "blaze":        20.0,
    "cave_spider":  12.0,
    "pillager":     24.0,
    "vindicator":   24.0,
    "enderman":     40.0,
    "phantom":      20.0,
    "slime":        16.0,
    "drowned":      20.0,
    "husk":         20.0,
    "stray":        20.0,
    "wither_skeleton": 20.0,
    "silverfish":    8.0,
    "guardian":     30.0,
    "elder_guardian": 80.0,
    "evoker":       24.0,
    "ravager":      100.0,
    "chicken":      4.0,
    "cow":          10.0,
    "pig":          10.0,
    "sheep":        8.0,
    "wolf":         8.0,
    "villager":     20.0,
}

_DEFAULT_HEALTH = 20.0

# Passive / neutral mobs that survive peaceful difficulty.  When the
# difficulty scale is 0.0 (peaceful) hostile mobs are skipped, but these
# are still summoned with normal (scale=1.0) stats.
_PASSIVE_MOBS: set[str] = {
    # Farm / overworld passive
    "chicken", "cow", "pig", "sheep", "mooshroom",
    "horse", "donkey", "mule", "rabbit", "turtle",
    "fox", "panda", "bee", "axolotl", "goat", "frog",
    "allay", "sniffer", "camel", "armadillo", "cat",
    "parrot", "ocelot",
    # Aquatic passive
    "squid", "glow_squid", "cod", "salmon", "tropical_fish",
    "pufferfish", "dolphin",
    # Utility / NPC
    "villager", "wandering_trader", "iron_golem", "snow_golem",
    # Neutral (won't attack unprovoked — still useful in peaceful chambers)
    "wolf", "bat", "strider",
}


class MobGenerator(FeatureGenerator):
    """Spawns mob packs with optional difficulty scaling.

    YAML schema (under ``features.mobs`` — a list):
        - type: <entity_id>
          count: <int>  OR  [min, max]
          area: [x1, y1, z1, x2, y2, z2]
          difficulty: easy|normal|hard|extreme  # optional, overrides chamber difficulty
          nbt: <extra NBT string>               # merged with generated NBT
          equipment: true|false                 # add iron gear to humanoids (default false)
    """

    def __init__(self, config: dict[str, Any], rng: random.Random,
                 difficulty: str = "normal") -> None:
        super().__init__(config, rng)
        self.difficulty = difficulty

    def generate(self) -> list[str]:
        entries = self.config.get("mobs", [])
        cmds: list[str] = []
        for entry in entries:
            mob_type   = str(entry.get("type", "zombie"))
            count      = self._resolve_count(entry.get("count", 1))
            area       = entry["area"]
            diff_key   = str(entry.get("difficulty", self.difficulty)).lower()
            extra_nbt  = entry.get("nbt", "")
            equipment  = entry.get("equipment", False)

            scale = _DIFFICULTY_SCALE.get(diff_key, 1.0)
            if scale == 0.0:
                if mob_type not in _PASSIVE_MOBS:
                    continue  # peaceful — skip hostile mobs
                scale = 1.0  # passive mobs get normal stats on peaceful

            for _ in range(count):
                x, y, z = self._rand_pos(area)
                cmd = _summon(mob_type, x, y, z, scale, extra_nbt, equipment, self.rng)
                cmds.append(cmd)

        return cmds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summon(
    mob: str,
    x: int, y: int, z: int,
    scale: float,
    extra_nbt: str,
    equipment: bool,
    rng: random.Random,
) -> str:
    base_hp = _BASE_HEALTH.get(mob, _DEFAULT_HEALTH)
    hp = round(base_hp * scale, 1)

    # 1.20.6 attribute format: lowercase keys, id/base instead of Name/Base.
    # All attribute modifiers must live in a single `attributes` list —
    # duplicate top-level keys in NBT silently discard all but the last.
    attribute_entries: list[str] = [
        f'{{id:"minecraft:generic.max_health",base:{hp}}}',
    ]

    # Speed boost on hard / extreme
    if scale > 1.0:
        speed_boost = round(0.25 + 0.05 * scale, 3)
        attribute_entries.append(
            f'{{id:"minecraft:generic.movement_speed",base:{speed_boost}}}'
        )

    nbt_parts: list[str] = [
        f"Health:{hp}f",
        f"attributes:[{','.join(attribute_entries)}]",
    ]

    # Optional iron equipment for humanoid mobs (1.20.6: count not Count)
    if equipment and mob in {"zombie", "skeleton", "husk", "stray", "drowned", "pillager", "vindicator"}:
        nbt_parts.append(
            "equipment:{feet:{id:\"minecraft:iron_boots\",count:1},"
            "legs:{id:\"minecraft:iron_leggings\",count:1},"
            "chest:{id:\"minecraft:iron_chestplate\",count:1},"
            "head:{id:\"minecraft:iron_helmet\",count:1}}"
        )

    if extra_nbt:
        nbt_parts.append(extra_nbt.strip().strip("{}"))

    nbt = "{" + ",".join(nbt_parts) + "}"
    return f"summon {mob} {x} {y} {z} {nbt}"
