"""ore.py — Scatter ore blocks and vein clusters via setblock."""
from __future__ import annotations

import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator

# All valid ore block IDs accepted in YAML type fields
_VALID_ORES = {
    "coal_ore", "iron_ore", "gold_ore", "diamond_ore",
    "emerald_ore", "lapis_ore", "redstone_ore", "copper_ore",
    "deepslate_coal_ore", "deepslate_iron_ore", "deepslate_gold_ore",
    "deepslate_diamond_ore", "deepslate_emerald_ore", "deepslate_lapis_ore",
    "deepslate_redstone_ore", "deepslate_copper_ore",
    "nether_quartz_ore", "nether_gold_ore", "ancient_debris",
}


class OreGenerator(FeatureGenerator):
    """Places ore blocks (scattered or vein-clustered) inside a chamber.

    YAML schema (under ``features.ores`` — a list):
        - type: <ore_block_id>
          count: <int>  OR  [min, max]   # number of ore blocks / vein seeds
          area: [x1, y1, z1, x2, y2, z2]
          vein_size: <int>               # optional extra adjacent ores per seed (default 0)
          replace: <block_id>            # only replace this block (default: any)
    """

    def generate(self) -> list[str]:
        entries = self.config.get("ores", [])
        cmds: list[str] = []
        for entry in entries:
            ore_type   = str(entry.get("type", "iron_ore"))
            count      = self._resolve_count(entry.get("count", 1))
            area       = entry["area"]
            vein_size  = int(entry.get("vein_size", 0))
            replace    = entry.get("replace")

            for _ in range(count):
                x, y, z = self._rand_pos(area)
                cmds.extend(_place_ore(ore_type, x, y, z, vein_size, replace, self.rng))
        return cmds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _place_ore(
    ore: str,
    x: int, y: int, z: int,
    vein_size: int,
    replace: str | None,
    rng: random.Random,
) -> list[str]:
    cmds: list[str] = []

    def _cmd(bx: int, by: int, bz: int) -> str:
        if replace:
            return f"fill {bx} {by} {bz} {bx} {by} {bz} {ore} replace {replace}"
        return f"setblock {bx} {by} {bz} {ore}"

    cmds.append(_cmd(x, y, z))

    if vein_size > 0:
        placed = {(x, y, z)}
        frontier = [(x, y, z)]
        extra = min(vein_size, 7)  # cap to avoid enormous veins
        for _ in range(extra):
            if not frontier:
                break
            bx, by, bz = rng.choice(frontier)
            neighbours = [
                (bx + 1, by, bz), (bx - 1, by, bz),
                (bx, by + 1, bz), (bx, by - 1, bz),
                (bx, by, bz + 1), (bx, by, bz - 1),
            ]
            rng.shuffle(neighbours)
            for nx, ny, nz in neighbours:
                if (nx, ny, nz) not in placed:
                    placed.add((nx, ny, nz))
                    frontier.append((nx, ny, nz))
                    cmds.append(_cmd(nx, ny, nz))
                    break

    return cmds
