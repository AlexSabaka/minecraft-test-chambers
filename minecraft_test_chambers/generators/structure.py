"""structure.py — Places vanilla Minecraft structures via /place structure."""
from __future__ import annotations

import math
import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator


class StructureGenerator(FeatureGenerator):
    """Places vanilla or datapacked structures at specified positions.

    Since this runs on a superflat world, only structures that don't require a
    specific biome footprint will succeed reliably. Known-good flat-world
    structures: dungeon, stronghold, pillager_outpost, desert_pyramid (after
    fillbiome), igloo (after fillbiome).

    The generator emits forceload commands so chunks are loaded before placement.

    YAML schema (under ``features.structures`` — a list):
        - id: minecraft:<structure_id>
          pos: [x, y, z]
          biome: minecraft:<id>       # optional: fill biome before placing
          biome_radius: <int>         # radius of fillbiome patch (default: 24)
    """

    def generate(self) -> list[str]:
        entries = self.config.get("structures", [])
        cmds: list[str] = []
        deferred_unloads: list[str] = []  # released after all structures are placed
        for entry in entries:
            struct_id = str(entry["id"])
            px, py, pz = entry["pos"]
            biome = entry.get("biome")
            br    = int(entry.get("biome_radius", 24))

            # Ensure chunk is loaded
            cx, cz = math.floor(px / 16), math.floor(pz / 16)
            chunk_x1, chunk_z1 = cx * 16, cz * 16
            chunk_x2, chunk_z2 = chunk_x1 + 15, chunk_z1 + 15
            cmds.append(f"forceload add {chunk_x1} {chunk_z1} {chunk_x2} {chunk_z2}")
            deferred_unloads.append(
                f"forceload remove {chunk_x1} {chunk_z1} {chunk_x2} {chunk_z2}"
            )

            # Optional biome override for biome-sensitive structures
            if biome:
                cmds.append(
                    f"fillbiome {px - br} {py - 4} {pz - br} "
                    f"{px + br} {py + 32} {pz + br} {biome}"
                )

            # Place the structure
            cmds.append(f"place structure {struct_id} {px} {py} {pz}")

        # Release forceloads only after every structure has been placed so that
        # block-tick settling (water flow, redstone, etc.) is not interrupted.
        cmds.extend(deferred_unloads)
        return cmds
