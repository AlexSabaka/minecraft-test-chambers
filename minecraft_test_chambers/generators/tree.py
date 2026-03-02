"""tree.py — Procedural tree generator using setblock commands.

Supports: oak, birch, spruce, jungle, acacia, dark_oak.
All leaves are placed with ``persistent=true`` to prevent decay.
"""
from __future__ import annotations

import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator


class TreeGenerator(FeatureGenerator):
    """Places procedurally generated trees inside a chamber.

    YAML schema (under ``features.trees`` — a list):
        - type: oak|birch|spruce|jungle|acacia|dark_oak
          count: <int>  OR  [min, max]
          area: [x1, y1, z1, x2, y2, z2]  # y1==y2 typically (ground level)
    """

    def generate(self) -> list[str]:
        entries = self.config.get("trees", [])
        cmds: list[str] = []
        for entry in entries:
            species = str(entry.get("type", "oak")).lower().replace(" ", "_")
            count   = self._resolve_count(entry.get("count", 1))
            area    = entry["area"]
            for _ in range(count):
                x, y, z = self._rand_pos(area)
                cmds.extend(_build_tree(species, x, y, z, self.rng))
        return cmds


# ── Per-species builders ──────────────────────────────────────────────────────

def _log(species: str, x: int, y: int, z: int) -> str:
    return f"setblock {x} {y} {z} {species}_log"


def _leaf(species: str, x: int, y: int, z: int) -> str:
    block = f"{species}_leaves[persistent=true]"
    return f"setblock {x} {y} {z} {block}"


def _build_tree(species: str, bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    builders = {
        "oak":      _oak,
        "birch":    _birch,
        "spruce":   _spruce,
        "jungle":   _jungle,
        "acacia":   _acacia,
        "dark_oak": _dark_oak,
    }
    builder = builders.get(species, _oak)
    return builder(bx, by, bz, rng)


def _oak(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(4, 6)
    cmds = []
    for i in range(height):
        cmds.append(_log("oak", bx, by + i, bz))
    top = by + height
    # Canopy: layers at top-1 and top
    for dy, radius in [(top - 1, 2), (top, 1)]:
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if abs(dx) == radius and abs(dz) == radius:
                    continue  # skip corners at outer ring
                cmds.append(_leaf("oak", bx + dx, dy, bz + dz))
    return cmds


def _birch(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(5, 7)
    cmds = []
    for i in range(height):
        cmds.append(_log("birch", bx, by + i, bz))
    top = by + height
    # Birch: tighter, taller canopy — 3 layers
    for dy, radius in [(top - 2, 2), (top - 1, 1), (top, 1)]:
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                cmds.append(_leaf("birch", bx + dx, dy, bz + dz))
    return cmds


def _spruce(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(6, 9)
    cmds = []
    for i in range(height):
        cmds.append(_log("spruce", bx, by + i, bz))
    top = by + height
    # Spruce: pyramid layers decreasing radius from base
    layers = [(top - 4, 3), (top - 3, 2), (top - 2, 2), (top - 1, 1), (top, 1), (top + 1, 0)]
    for dy, radius in layers:
        if radius == 0:
            cmds.append(_leaf("spruce", bx, dy, bz))
        else:
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if abs(dx) == radius and abs(dz) == radius:
                        continue
                    cmds.append(_leaf("spruce", bx + dx, dy, bz + dz))
    return cmds


def _jungle(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(10, 14)
    cmds = []
    for i in range(height):
        cmds.append(_log("jungle", bx, by + i, bz))
    top = by + height
    # Wide base canopy 5×5 then 3×3 near top
    for dy, radius in [(top - 2, 3), (top - 1, 2), (top, 1)]:
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                cmds.append(_leaf("jungle", bx + dx, dy, bz + dz))
    # Vines on lower trunk occasionally
    vine_faces = ["north", "south", "east", "west"]
    for i in range(2, height - 2):
        if rng.random() < 0.3:
            face = rng.choice(vine_faces)
            offsets = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
            dx, dz = offsets[face]
            cmds.append(f"setblock {bx + dx} {by + i} {bz + dz} vine[{face}=true]")
    return cmds


def _acacia(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(4, 6)
    cmds = []
    # Forked trunk: main trunk then branch
    for i in range(height - 1):
        cmds.append(_log("acacia", bx, by + i, bz))
    # Fork offset
    fork_dx, fork_dz = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
    fork_top_y = by + height
    cmds.append(_log("acacia", bx + fork_dx, fork_top_y - 1, bz + fork_dz))
    # Two offset canopy clusters (acacia is wide and flat)
    for cx, cz in [(bx, bz), (bx + fork_dx * 2, bz + fork_dz * 2)]:
        for dy, radius in [(fork_top_y, 2), (fork_top_y + 1, 1)]:
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    cmds.append(_leaf("acacia", cx + dx, dy, cz + dz))
    return cmds


def _dark_oak(bx: int, by: int, bz: int, rng: random.Random) -> list[str]:
    height = rng.randint(6, 8)
    cmds = []
    # 2×2 trunk
    for dx in range(2):
        for dz in range(2):
            for i in range(height):
                cmds.append(_log("dark_oak", bx + dx, by + i, bz + dz))
    top = by + height
    cx, cz = bx + 1, bz + 1   # trunk centre
    # Wide spreading canopy
    for dy, radius in [(top - 2, 3), (top - 1, 3), (top, 2), (top + 1, 1)]:
        for dx in range(-radius, radius + 1):
            for dz_ in range(-radius, radius + 1):
                if abs(dx) == radius and abs(dz_) == radius and rng.random() < 0.5:
                    continue  # randomly skip corners for natural look
                cmds.append(_leaf("dark_oak", cx + dx, dy, cz + dz_))
    return cmds
