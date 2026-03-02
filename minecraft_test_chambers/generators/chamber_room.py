"""chamber_room.py — Portal-style /fill-based test chamber room generator."""
from __future__ import annotations

import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator

# Default chamber dimensions if no `chamber` key in yaml
_DEFAULT_SIZE = [24, 12, 24]      # x, y, z interior dimensions
_DEFAULT_MATERIAL = "iron_block"
_DEFAULT_GLASS = "glass"          # used for one-way observation panels


class ChamberRoomGenerator(FeatureGenerator):
    """Generates a Portal-style enclosed test chamber using /fill.

    YAML schema (under ``features.chamber``):
        material: <block_id>            # wall/ceiling/floor block  (default: iron_block)
        glass: <block_id>               # observation panel block   (default: glass)
        size: [x, y, z]                 # interior size             (default: [24,12,24])
        origin: [x, y, z]              # NW corner base, Y=floor   (default: computed from tp_y)
        panels: true|false              # add glass panels on walls  (default: true)
        skip: true|false               # if true, emit nothing      (default: false)
    """

    def __init__(self, config: dict[str, Any], rng: random.Random, tp_y: int = -57) -> None:
        super().__init__(config, rng)
        self.tp_y = tp_y

    def generate(self) -> list[str]:
        cfg = self.config.get("chamber", {})
        if not cfg or cfg.get("skip"):
            return []

        material = cfg.get("material", _DEFAULT_MATERIAL)
        glass    = cfg.get("glass", _DEFAULT_GLASS)
        sx, sy, sz = cfg.get("size", _DEFAULT_SIZE)
        panels   = cfg.get("panels", True)

        # Origin: where the floor NW corner sits
        if "origin" in cfg:
            ox, oy, oz = cfg["origin"]
        else:
            half_x, half_z = sx // 2, sz // 2
            ox = -half_x
            oy = self.tp_y
            oz = -half_z

        # Bounding box (outer shell): origin to origin+size+1 (walls are 1-thick)
        x1, y1, z1 = ox - 1,      oy - 1,      oz - 1
        x2, y2, z2 = ox + sx,     oy + sy,     oz + sz

        cmds: list[str] = []

        # 1. Clear interior + shell volume with air first
        cmds.append(f"fill {x1} {y1} {z1} {x2} {y2} {z2} air")

        # 2. Outer shell (walls, floor, ceiling) — hollow keeps interior air
        cmds.append(f"fill {x1} {y1} {z1} {x2} {y2} {z2} {material} hollow")

        # 3. Observation glass panels: a 3×3 window centred on each wall
        if panels and sx >= 6 and sz >= 6 and sy >= 5:
            panel_y1 = oy + 2
            panel_y2 = oy + sy - 2

            # North wall (z = z1)
            cx = ox + sx // 2
            cmds.append(f"fill {cx-1} {panel_y1} {z1} {cx+1} {panel_y2} {z1} {glass}")
            # South wall (z = z2)
            cmds.append(f"fill {cx-1} {panel_y1} {z2} {cx+1} {panel_y2} {z2} {glass}")
            # West wall (x = x1)
            cz = oz + sz // 2
            cmds.append(f"fill {x1} {panel_y1} {cz-1} {x1} {panel_y2} {cz+1} {glass}")
            # East wall (x = x2)
            cmds.append(f"fill {x2} {panel_y1} {cz-1} {x2} {panel_y2} {cz+1} {glass}")

        # 4. Ceiling grate variant: place glowstone every 4 blocks on the ceiling
        grate_y = y2
        grate_block = cfg.get("ceiling_light", "glowstone")
        step = 4
        x, z = ox + step, oz + step
        while x < ox + sx:
            z = oz + step
            while z < oz + sz:
                cmds.append(f"setblock {x} {grate_y} {z} {grate_block}")
                z += step
            x += step

        # 5. Entry portal marker — gold block + glowing frame hint
        entry_x = ox + sx // 2
        entry_z = oz
        cmds.append(f"setblock {entry_x} {oy} {entry_z} gold_block")
        # Exit marker
        exit_z = oz + sz - 1
        cmds.append(f"setblock {entry_x} {oy} {exit_z} emerald_block")

        # 6. Teleport spawn platform — raise it one block above floor
        cmds.append(f"setblock {entry_x} {oy + 1} {oz + sz // 2} smooth_stone")

        return cmds
