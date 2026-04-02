"""chamber_loader.py — Orchestrates YAML parsing and RCON command generation.

Public API:
    load_chamber(name, rcon_fn, *, seed_override=None, dry_run=False) -> LoadResult
    list_chambers() -> list[str]
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from minecraft_test_chambers.generators.cave import CaveGenerator
from minecraft_test_chambers.generators.chamber_room import ChamberRoomGenerator
from minecraft_test_chambers.generators.environment import EnvironmentGenerator
from minecraft_test_chambers.generators.mob import MobGenerator
from minecraft_test_chambers.generators.ore import OreGenerator
from minecraft_test_chambers.generators.structure import StructureGenerator
from minecraft_test_chambers.generators.tree import TreeGenerator

# Resolve test_chambers/ relative to this package's parent (repo root)
_REPO_ROOT = Path(__file__).parent.parent
_CHAMBERS_DIR = _REPO_ROOT / "test_chambers"

# Ground-floor Y coordinate when no tp_y is specified in YAML
_DEFAULT_TP_Y = -57

# Default spawn point when not specified in YAML (origin, above ground)
_DEFAULT_SPAWN_POINT = [0, -56, 0]

# Area used for the initial world-reset fill operations
# Superflat bedrock sits at Y=-64, ground at Y=-63/-62
_RESET_RADIUS = 32


@dataclass
class CommandResult:
    command: str
    response: str | None
    ok: bool


@dataclass
class LoadResult:
    chamber: str
    seed: int
    commands_run: int
    errors: list[str] = field(default_factory=list)
    results: list[CommandResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ─── Public API ───────────────────────────────────────────────────────────────

def list_chambers() -> list[str]:
    """Return sorted list of chamber names available in test_chambers/."""
    return sorted(p.stem for p in _CHAMBERS_DIR.glob("*.yaml"))


def load_chamber(
    name: str,
    rcon_fn: Callable[[str], str | None],
    *,
    seed_override: int | None = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> LoadResult:
    """Load and apply a test chamber to the running Minecraft server.

    Args:
        name:           Chamber name (filename without .yaml extension).
        rcon_fn:        Callable that sends an RCON command and returns the server
                        response string, or ``None`` on failure.
        seed_override:  Override the YAML seed for this run.
        dry_run:        If True, generate commands but do not send them.
        verbose:        Print progress to stdout.

    Returns:
        A :class:`LoadResult` with execution details.
    """
    chamber_path = _CHAMBERS_DIR / f"{name}.yaml"
    if not chamber_path.exists():
        available = list_chambers()
        raise FileNotFoundError(
            f"Chamber '{name}' not found. Available: {', '.join(available)}"
        )

    with chamber_path.open() as fh:
        doc: dict[str, Any] = yaml.safe_load(fh) or {}

    # ── Seed resolution ───────────────────────────────────────────────────────
    if seed_override is not None:
        seed = seed_override
    elif "seed" in doc:
        seed = int(doc["seed"])
    else:
        seed = random.randint(0, 2**31 - 1)

    rng = random.Random(seed)
    tp_y: int = int(doc.get("tp_y", _DEFAULT_TP_Y))
    spawn_point: list[int] = doc.get("spawn_point", _DEFAULT_SPAWN_POINT)

    if verbose:
        print(f"\n  Loading chamber '{name}'  (seed={seed})")

    # ── Build command list ────────────────────────────────────────────────────
    all_cmds: list[str] = []

    # 1. Area reset — wipe the play zone before building
    all_cmds.extend(_reset_commands(tp_y))

    # 2. Spawn point — set world spawn, per-player spawn, and teleport
    sx, sy, sz = spawn_point
    all_cmds.append(f"setworldspawn {sx} {sy} {sz}")
    all_cmds.append(f"spawnpoint @a {sx} {sy} {sz}")
    all_cmds.append(f"tp @a {sx} {sy} {sz}")

    # 2b. Always clear player inventory so only YAML-defined items remain
    all_cmds.append("clear @a")

    features: dict[str, Any] = doc.get("features", {})
    raw_cmds: list[str] = doc.get("rcon_cmds", [])   # backward-compat

    if features:
        # 3. Environment (time, weather, biome, difficulty, gamerules)
        env_cfg = {
            "time":              features.get("time", doc.get("time", "day")),
            "weather":           features.get("weather", doc.get("weather", "clear")),
            "biome":             features.get("biome"),
            "difficulty":        features.get("difficulty", "normal"),
            "gamerule_overrides": features.get("gamerule_overrides", {}),
        }
        env_gen = EnvironmentGenerator(env_cfg, rng, tp_y=tp_y)
        all_cmds.extend(env_gen.generate())

        # 4. Chamber room (Portal-style walls / ceiling / floor)
        if "chamber" in features:
            room_gen = ChamberRoomGenerator(features, rng, tp_y=tp_y)
            all_cmds.extend(room_gen.generate())

        # 5. Structures (before trees/ores so ground is clear)
        if "structures" in features:
            struct_gen = StructureGenerator(features, rng)
            all_cmds.extend(struct_gen.generate())

        # 6. Escape-hatch raw cmds — terrain / structure fills run BEFORE
        #    procedural generators so that ores, mobs, and caves are placed
        #    *into* the correct terrain.
        if "raw_cmds" in features:
            all_cmds.extend(features["raw_cmds"])

        # 7. Trees
        if "trees" in features:
            tree_gen = TreeGenerator(features, rng)
            all_cmds.extend(tree_gen.generate())

        # 8. Caves (carve into terrain before ore/mob placement)
        if "caves" in features:
            cave_gen = CaveGenerator(features, rng)
            all_cmds.extend(cave_gen.generate())

        # 9. Ores (placed after terrain + caves so they sit in solid blocks)
        if "ores" in features:
            ore_gen = OreGenerator(features, rng)
            all_cmds.extend(ore_gen.generate())

        # 10. Mobs (last procedural step so terrain is settled)
        if "mobs" in features:
            difficulty = features.get("difficulty", "normal")
            mob_gen = MobGenerator(features, rng, difficulty=difficulty)
            all_cmds.extend(mob_gen.generate())

        # 11. Inventory — clear and equip player after everything else
        inventory = features.get("inventory")
        if inventory:
            all_cmds.extend(_inventory_commands(inventory))

    elif raw_cmds:
        # Legacy YAML: only rcon_cmds present, no features section
        # Still emit environment setup so time/weather are applied
        env_cfg = {
            "time":    doc.get("time", "day"),
            "weather": doc.get("weather", "clear"),
        }
        env_gen = EnvironmentGenerator(env_cfg, rng, tp_y=tp_y)
        all_cmds.extend(env_gen.generate())
        all_cmds.extend(raw_cmds)

    # ── Execute ───────────────────────────────────────────────────────────────
    result = LoadResult(chamber=name, seed=seed, commands_run=0)

    if verbose:
        print(f"  {len(all_cmds)} commands to execute …")

    for cmd in all_cmds:
        if not cmd or not cmd.strip():
            continue
        result.commands_run += 1

        if dry_run:
            if verbose:
                print(f"    [DRY-RUN] {cmd}")
            result.results.append(CommandResult(command=cmd, response="(dry-run)", ok=True))
            continue

        resp = rcon_fn(cmd)
        ok   = resp is not None
        if not ok:
            result.errors.append(f"RCON failure: {cmd!r}")

        if verbose:
            status = "✓" if ok else "✗"
            resp_preview = (resp or "").strip()[:60]
            print(f"    {status} {cmd[:80]}{' …' if len(cmd) > 80 else ''}"
                  f"{'  →  ' + resp_preview if resp_preview else ''}")

        result.results.append(CommandResult(command=cmd, response=resp, ok=ok))

    if verbose:
        err_count = len(result.errors)
        if err_count:
            print(f"\n  ⚠ {err_count} RCON error(s) during load — check server log")
        else:
            print(f"\n  ✓ Chamber '{name}' loaded successfully (seed={seed})")

    return result


# ─── Internals ────────────────────────────────────────────────────────────────

def _reset_commands(tp_y: int) -> list[str]:
    """Wipe the play area above bedrock and restore a clean flat surface.

    Minecraft's ``fill`` command is limited to 32,768 blocks per call.
    With a 64-block worldborder the horizontal footprint is 65×65 = 4,225 blocks
    per Y slice, so we can safely clear at most 7 rows at a time
    (7 × 4,225 = 29,575 ≤ 32,768).
    """
    r = _RESET_RADIUS
    clear_y_min = -63          # first block above bedrock
    clear_y_max = tp_y + 30   # headroom above tallest possible feature

    # Max rows per fill call: floor(32_768 / ((2r+1)^2))
    side = 2 * r + 1
    _FILL_SLICE = max(1, 32_768 // (side * side))

    cmds: list[str] = []

    # Kill leftover entities (mobs, items, projectiles) in play zone
    cmds.append(f"kill @e[type=!player,x=0,y={tp_y},z=0,distance=..{r + 8}]")

    # Clear air in vertical slices to stay within 32,768-block fill limit
    y = clear_y_min
    while y <= clear_y_max:
        y_top = min(y + _FILL_SLICE - 1, clear_y_max)
        cmds.append(f"fill -{r} {y} -{r} {r} {y_top} {r} air")
        y = y_top + 1

    # Restore grass surface and bedrock floor
    cmds.append(f"fill -{r} -63 -{r} {r} -63 {r} grass_block")
    cmds.append(f"fill -{r} -64 -{r} {r} -64 {r} bedrock")

    return cmds


# Armor slot order matches the YAML list: [head, chest, legs, feet]
_ARMOR_SLOTS = ["armor.head", "armor.chest", "armor.legs", "armor.feet"]


def _inventory_commands(inventory: dict[str, Any]) -> list[str]:
    """Generate give + equip commands from an ``inventory`` config block.

    The player is already cleared by the main pipeline; this function only
    adds the requested items and equipment.

    Schema::

        inventory:
          mainhand: diamond_sword
          offhand: shield
          armor: [iron_helmet, iron_chestplate, iron_leggings, iron_boots]
          items:
            diamond_sword: 1
            torch: 16
    """
    cmds: list[str] = []

    # Give items into general inventory
    items: dict[str, int] = inventory.get("items", {})
    for item, count in items.items():
        cmds.append(f"give @a minecraft:{item} {count}")

    # Equip mainhand / offhand
    mainhand = inventory.get("mainhand")
    if mainhand:
        cmds.append(
            f"item replace entity @a weapon.mainhand with minecraft:{mainhand}"
        )

    offhand = inventory.get("offhand")
    if offhand:
        cmds.append(
            f"item replace entity @a weapon.offhand with minecraft:{offhand}"
        )

    # Equip armor (list of up to 4 items: head, chest, legs, feet)
    armor: list[str] = inventory.get("armor", [])
    for slot_name, piece in zip(_ARMOR_SLOTS, armor):
        cmds.append(
            f"item replace entity @a {slot_name} with minecraft:{piece}"
        )

    return cmds
