"""cave.py — Procedural cave carver using a random-walk 'worm' algorithm.

Produces Minecraft-like cave systems by steering one or more worms through
a 3D bounding volume.  Each worm step carves a sphere (approximated by a
fill-box) whose radius varies smoothly, yielding natural-looking tunnels.
Branch tunnels and room expansions are triggered stochastically.

The generator outputs ``fill … air`` commands clamped to the configured area.
Run it **after** terrain setup and **before** ore / mob placement so that
ores appear on cave walls and mobs spawn on cave floors.
"""
from __future__ import annotations

import math
import random
from typing import Any

from minecraft_test_chambers.generators.base import FeatureGenerator


class CaveGenerator(FeatureGenerator):
    """Carve procedural cave networks inside a bounding volume.

    YAML schema (under ``features.caves``):

    .. code-block:: yaml

        caves:
          area: [x1, y1, z1, x2, y2, z2]
          tunnels: <int> | [min, max]         # worm count (default [2, 4])
          tunnel_length: <int> | [min, max]   # steps per worm (default [20, 40])
          branch_chance: <float>              # per-step fork probability (0.25)
          room_chance: <float>                # per-step room probability (0.15)
          room_radius: <int> | [min, max]     # room sphere radius (default [3, 4])
          min_radius: <int>                   # minimum tunnel radius (1)
          max_radius: <int>                   # maximum tunnel radius (2)
          entrances:                          # optional vertical shafts
            - [x, y_surface, z]

    All positions are world coordinates; the generator clamps output to *area*.
    """

    # Maximum recursive branch depth to prevent runaway generation
    _MAX_BRANCH_DEPTH = 2

    def generate(self) -> list[str]:
        cfg = self.config.get("caves")
        if not cfg:
            return []

        area: list[int] = cfg["area"]
        num_tunnels = self._resolve_count(cfg.get("tunnels", [2, 4]))
        tunnel_length = cfg.get("tunnel_length", [20, 40])
        branch_chance = float(cfg.get("branch_chance", 0.25))
        room_chance = float(cfg.get("room_chance", 0.15))
        room_radius_cfg = cfg.get("room_radius", [3, 4])
        min_r = int(cfg.get("min_radius", 1))
        max_r = int(cfg.get("max_radius", 2))

        # Collect carved spheres as (cx, cy, cz, radius) tuples
        regions: list[tuple[int, int, int, int]] = []

        for _ in range(num_tunnels):
            sx, sy, sz = self._rand_pos(area)
            self._carve_worm(
                sx, sy, sz,
                tunnel_length, min_r, max_r,
                branch_chance, room_chance, room_radius_cfg,
                area, regions, depth=0,
            )

        cmds = self._regions_to_cmds(regions, area)

        # Optional vertical entrance shafts from surface → cave area.
        # Entrances carve ABOVE the cave area, so they are emitted directly
        # without area-clamping.
        entrances: list[list[int]] = cfg.get("entrances", [])
        y_top = max(area[1], area[4])
        for ent in entrances:
            ex, ey_surface, ez = int(ent[0]), int(ent[1]), int(ent[2])
            if ey_surface <= y_top:
                import warnings
                warnings.warn(
                    f"Cave entrance at ({ex}, {ey_surface}, {ez}) is at or below "
                    f"cave top y={y_top}; no shaft will be generated.",
                    stacklevel=2,
                )
                continue
            for ey in range(y_top, ey_surface + 1):
                cmds.append(
                    f"fill {ex - 1} {ey} {ez - 1} {ex + 1} {ey} {ez + 1} air"
                )

        return cmds

    # ── Worm carver ───────────────────────────────────────────────────────────

    def _carve_worm(
        self,
        x: float, y: float, z: float,
        length_cfg: int | list[int],
        min_r: int, max_r: int,
        branch_chance: float,
        room_chance: float,
        room_radius_cfg: int | list[int],
        area: list[int],
        regions: list[tuple[int, int, int, int]],
        depth: int,
    ) -> None:
        x1, y1, z1, x2, y2, z2 = area
        length = self._resolve_count(length_cfg)

        # Random initial heading — favor horizontal travel
        yaw = self.rng.uniform(0, 2 * math.pi)
        pitch = self.rng.uniform(-0.3, 0.3)

        for _step in range(length):
            r = self.rng.randint(min_r, max_r)
            ix, iy, iz = int(round(x)), int(round(y)), int(round(z))

            # Bail out when center leaves the bounding area.
            # The fill commands are clamped to *area* later, so we only
            # need the center itself to be inside.
            if not (x1 <= ix <= x2 and y1 <= iy <= y2 and z1 <= iz <= z2):
                break

            regions.append((ix, iy, iz, r))

            # Probabilistic room expansion
            if self.rng.random() < room_chance:
                rr = self._resolve_count(room_radius_cfg)
                regions.append((ix, iy, iz, rr))

            # Probabilistic branch (limited depth)
            if depth < self._MAX_BRANCH_DEPTH and self.rng.random() < branch_chance:
                bl = [max(5, length // 4), max(8, length // 2)]
                self._carve_worm(
                    x, y, z,
                    bl, min_r, max_r,
                    branch_chance * 0.5, room_chance, room_radius_cfg,
                    area, regions, depth + 1,
                )

            # Advance position along current heading
            dx = math.cos(yaw) * math.cos(pitch)
            dy = math.sin(pitch)
            dz = math.sin(yaw) * math.cos(pitch)
            x += dx
            y += dy
            z += dz

            # Perturb heading for natural curvature
            yaw += self.rng.gauss(0, 0.3)
            pitch += self.rng.gauss(0, 0.1)
            pitch = max(-0.5, min(0.5, pitch))  # keep mostly horizontal

    # ── Command output ────────────────────────────────────────────────────────

    @staticmethod
    def _regions_to_cmds(
        regions: list[tuple[int, int, int, int]],
        area: list[int],
    ) -> list[str]:
        """Convert carved spheres into ``fill … air`` commands.

        Each sphere is approximated by its axis-aligned bounding box, clamped
        to *area*.  Overlapping fills are harmless (air → air is a no-op).
        """
        x1b, y1b, z1b, x2b, y2b, z2b = area
        cmds: list[str] = []
        for cx, cy, cz, r in regions:
            fx1 = max(x1b, cx - r)
            fy1 = max(y1b, cy - r)
            fz1 = max(z1b, cz - r)
            fx2 = min(x2b, cx + r)
            fy2 = min(y2b, cy + r)
            fz2 = min(z2b, cz + r)
            cmds.append(f"fill {fx1} {fy1} {fz1} {fx2} {fy2} {fz2} air")
        return cmds
