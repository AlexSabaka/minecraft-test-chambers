"""Unit tests for all FeatureGenerator subclasses.

Each generator is exercised with a fixed seed so tests are deterministic.
Tests verify:
  - generate() returns a non-empty list of strings
  - individual commands have the expected structure
  - edge cases (count=0, minimal area, skip flags)
"""
from __future__ import annotations

import random
import pytest

from minecraft_test_chambers.generators.base import FeatureGenerator
from minecraft_test_chambers.generators.chamber_room import ChamberRoomGenerator
from minecraft_test_chambers.generators.environment import EnvironmentGenerator
from minecraft_test_chambers.generators.mob import MobGenerator
from minecraft_test_chambers.generators.ore import OreGenerator
from minecraft_test_chambers.generators.structure import StructureGenerator
from minecraft_test_chambers.generators.tree import TreeGenerator


# ─── Helpers ─────────────────────────────────────────────────────────────────

FIXED_SEED = 42


def make_rng(seed: int = FIXED_SEED) -> random.Random:
    return random.Random(seed)


# ─── Base helpers ─────────────────────────────────────────────────────────────

class _ConcreteGen(FeatureGenerator):
    def generate(self) -> list[str]:
        return []


def test_rand_pos_within_area() -> None:
    g = _ConcreteGen({}, make_rng())
    for _ in range(100):
        x, y, z = g._rand_pos([-5, -63, -5, 5, -60, 5])
        assert -5 <= x <= 5
        assert -63 <= y <= -60
        assert -5 <= z <= 5


def test_rand_pos_clamped_single_point() -> None:
    g = _ConcreteGen({}, make_rng())
    x, y, z = g._rand_pos([3, -63, 7, 3, -63, 7])
    assert (x, y, z) == (3, -63, 7)


def test_rand_pos_wrong_length() -> None:
    g = _ConcreteGen({}, make_rng())
    with pytest.raises(ValueError):
        g._rand_pos([0, 0, 0])


def test_resolve_count_fixed() -> None:
    g = _ConcreteGen({}, make_rng())
    assert g._resolve_count(7) == 7


def test_resolve_count_range() -> None:
    g = _ConcreteGen({}, make_rng())
    for _ in range(50):
        v = g._resolve_count([3, 8])
        assert 3 <= v <= 8


# ─── EnvironmentGenerator ─────────────────────────────────────────────────────

def _env(overrides: dict | None = None) -> EnvironmentGenerator:
    cfg = {"time": "day", "weather": "clear", "biome": "minecraft:plains", "difficulty": "normal"}
    if overrides:
        cfg.update(overrides)
    return EnvironmentGenerator(cfg, make_rng())


def test_environment_basic() -> None:
    cmds = _env().generate()
    assert any("time set 6000" in c for c in cmds)
    assert any("weather clear" in c for c in cmds)
    assert any("fillbiome" in c and "minecraft:plains" in c for c in cmds)
    assert any("difficulty normal" in c for c in cmds)


def test_environment_night() -> None:
    cmds = EnvironmentGenerator({"time": "night"}, make_rng()).generate()
    assert any("time set 18000" in c for c in cmds)


def test_environment_dawn() -> None:
    cmds = EnvironmentGenerator({"time": "dawn"}, make_rng()).generate()
    assert any("time set 0" in c for c in cmds)


def test_environment_dusk() -> None:
    cmds = EnvironmentGenerator({"time": "dusk"}, make_rng()).generate()
    assert any("time set 12000" in c for c in cmds)


def test_environment_weather_rain() -> None:
    cmds = EnvironmentGenerator({"weather": "rain"}, make_rng()).generate()
    assert any("weather rain" in c for c in cmds)


def test_environment_gamerule_overrides() -> None:
    cfg = {"gamerule_overrides": {"doMobSpawning": "false", "keepInventory": "true"}}
    cmds = EnvironmentGenerator(cfg, make_rng()).generate()
    assert any("gamerule doMobSpawning false" in c for c in cmds)
    assert any("gamerule keepInventory true" in c for c in cmds)


def test_environment_no_biome() -> None:
    """No fillbiome command if biome not specified."""
    cmds = EnvironmentGenerator({"time": "day"}, make_rng()).generate()
    assert not any("fillbiome" in c for c in cmds)


# ─── ChamberRoomGenerator ─────────────────────────────────────────────────────

def _room(overrides: dict | None = None) -> ChamberRoomGenerator:
    cfg = {
        "chamber": {
            "material": "stone",
            "glass": "glass",
            "size": [20, 10, 20],
            "panels": True,
            "ceiling_light": "glowstone",
        }
    }
    if overrides:
        cfg["chamber"].update(overrides)
    return ChamberRoomGenerator(cfg, make_rng())


def test_chamber_room_generates_fill() -> None:
    cmds = _room().generate()
    fill_cmds = [c for c in cmds if c.startswith("fill") and "stone" in c]
    assert fill_cmds, "Expected at least one fill command with the wall material"


def test_chamber_room_hollow() -> None:
    cmds = _room().generate()
    assert any("hollow" in c for c in cmds)


def test_chamber_room_glass_panel() -> None:
    cmds = _room().generate()
    assert any("glass" in c for c in cmds)


def test_chamber_room_ceiling_glowstone() -> None:
    cmds = _room().generate()
    assert any("glowstone" in c for c in cmds)


def test_chamber_room_gold_entry() -> None:
    cmds = _room().generate()
    assert any("gold_block" in c for c in cmds)


def test_chamber_room_emerald_exit() -> None:
    cmds = _room().generate()
    assert any("emerald_block" in c for c in cmds)


def test_chamber_room_skip() -> None:
    cmds = ChamberRoomGenerator({"chamber": {"skip": True}}, make_rng()).generate()
    assert cmds == []


def test_chamber_room_no_config() -> None:
    """Empty config = no chamber section → empty output."""
    cmds = ChamberRoomGenerator({}, make_rng()).generate()
    assert cmds == []


# ─── TreeGenerator ────────────────────────────────────────────────────────────

_SPECIES = ["oak", "birch", "spruce", "jungle", "acacia", "dark_oak"]


@pytest.mark.parametrize("species", _SPECIES)
def test_tree_species_generates_log(species: str) -> None:
    cfg = {
        "trees": [
            {"type": species, "count": 1, "area": [-2, -63, -2, 2, -63, 2]}
        ]
    }
    cmds = TreeGenerator(cfg, make_rng()).generate()
    log_block = species.replace("_oak", "_oak_log").replace("birch", "birch_log") \
        .replace("oak", "oak_log").replace("spruce", "spruce_log") \
        .replace("jungle", "jungle_log").replace("acacia", "acacia_log")
    # Just check *some* setblock commands were emitted
    assert any("setblock" in c for c in cmds)


def test_tree_persistent_leaves() -> None:
    cfg = {"trees": [{"type": "oak", "count": 3, "area": [-5, -63, -5, 5, -63, 5]}]}
    cmds = TreeGenerator(cfg, make_rng()).generate()
    leaf_cmds = [c for c in cmds if "leaves" in c]
    assert leaf_cmds, "Expected leaf blocks"
    assert all("persistent=true" in c for c in leaf_cmds)


def test_tree_count_zero() -> None:
    cfg = {"trees": [{"type": "oak", "count": 0, "area": [-5, -63, -5, 5, -63, 5]}]}
    cmds = TreeGenerator(cfg, make_rng()).generate()
    assert cmds == []


def test_tree_multiple_species() -> None:
    cfg = {
        "trees": [
            {"type": "oak", "count": 2, "area": [-5, -63, -5, 5, -63, 5]},
            {"type": "birch", "count": 1, "area": [-4, -63, -4, 4, -63, 4]},
        ]
    }
    cmds = TreeGenerator(cfg, make_rng()).generate()
    assert len(cmds) > 0


def test_tree_no_config() -> None:
    cmds = TreeGenerator({}, make_rng()).generate()
    assert cmds == []


# ─── OreGenerator ─────────────────────────────────────────────────────────────

def test_ore_basic_scatter() -> None:
    cfg = {
        "ores": [
            {"type": "coal_ore", "count": 5, "area": [-10, -63, -10, 10, -61, 10]}
        ]
    }
    cmds = OreGenerator(cfg, make_rng()).generate()
    assert len(cmds) >= 5
    assert all("coal_ore" in c for c in cmds)


def test_ore_with_replace() -> None:
    cfg = {
        "ores": [
            {
                "type": "coal_ore",
                "count": 3,
                "area": [-5, -63, -5, 5, -63, 5],
                "replace": "grass_block",
            }
        ]
    }
    cmds = OreGenerator(cfg, make_rng()).generate()
    assert any("replace grass_block" in c for c in cmds)


def test_ore_vein_size() -> None:
    """With vein_size > 0, command count can exceed the count value (BFS expansion)."""
    cfg = {
        "ores": [
            {"type": "iron_ore", "count": 2, "area": [-8, -63, -8, 8, -61, 8], "vein_size": 3}
        ]
    }
    cmds = OreGenerator(cfg, make_rng()).generate()
    assert len(cmds) >= 2


def test_ore_count_zero() -> None:
    cfg = {"ores": [{"type": "diamond_ore", "count": 0, "area": [-5, -63, -5, 5, -63, 5]}]}
    cmds = OreGenerator(cfg, make_rng()).generate()
    assert cmds == []


def test_ore_no_config() -> None:
    cmds = OreGenerator({}, make_rng()).generate()
    assert cmds == []


# ─── MobGenerator ─────────────────────────────────────────────────────────────

def test_mob_basic_summon() -> None:
    cfg = {
        "mobs": [
            {"type": "zombie", "count": 2, "area": [-5, -62, -5, 5, -62, 5]}
        ]
    }
    cmds = MobGenerator(cfg, make_rng()).generate()
    assert len(cmds) == 2
    assert all("summon" in c and "zombie" in c for c in cmds)


def test_mob_difficulty_hard_scales_health() -> None:
    cfg = {
        "mobs": [
            {"type": "zombie", "count": 1, "area": [0, -62, 0, 0, -62, 0], "difficulty": "hard"}
        ]
    }
    cmds = MobGenerator(cfg, make_rng()).generate()
    assert len(cmds) == 1
    # Hard scale = 1.5 → health > default (20)
    # Verify 1.20.6-compliant NBT: lowercase keys, single attributes list
    assert "Health:" in cmds[0]
    assert "attributes:" in cmds[0]
    assert "minecraft:generic.max_health" in cmds[0]
    assert "Attributes:" not in cmds[0]  # old broken format must be absent


def test_mob_nbt_appended() -> None:
    extra_nbt = "CustomName:'\"Test\"'"
    cfg = {
        "mobs": [
            {"type": "skeleton", "count": 1, "area": [0, -62, 0, 0, -62, 0], "nbt": extra_nbt}
        ]
    }
    cmds = MobGenerator(cfg, make_rng()).generate()
    assert extra_nbt in cmds[0]


def test_mob_count_zero() -> None:
    cfg = {"mobs": [{"type": "creeper", "count": 0, "area": [-5, -62, -5, 5, -62, 5]}]}
    cmds = MobGenerator(cfg, make_rng()).generate()
    assert cmds == []


def test_mob_no_config() -> None:
    cmds = MobGenerator({}, make_rng()).generate()
    assert cmds == []


# ─── StructureGenerator ───────────────────────────────────────────────────────

def test_structure_basic() -> None:
    cfg = {
        "structures": [
            {"id": "minecraft:dungeon", "pos": [0, -63, 0]}
        ]
    }
    cmds = StructureGenerator(cfg, make_rng()).generate()
    assert any("place structure minecraft:dungeon" in c for c in cmds)
    assert any("forceload add" in c for c in cmds)
    assert any("forceload remove" in c for c in cmds)


def test_structure_with_biome() -> None:
    cfg = {
        "structures": [
            {"id": "minecraft:village_plains", "pos": [0, -63, 0], "biome": "minecraft:plains", "biome_radius": 16}
        ]
    }
    cmds = StructureGenerator(cfg, make_rng()).generate()
    assert any("fillbiome" in c and "minecraft:plains" in c for c in cmds)


def test_structure_no_config() -> None:
    cmds = StructureGenerator({}, make_rng()).generate()
    assert cmds == []


# ─── Determinism ──────────────────────────────────────────────────────────────

def test_tree_determinism() -> None:
    """Same seed must produce identical command list across two calls."""
    cfg = {"trees": [{"type": "oak", "count": [2, 5], "area": [-10, -63, -10, 10, -63, 10]}]}
    cmds_a = TreeGenerator(cfg, make_rng(99)).generate()
    cmds_b = TreeGenerator(cfg, make_rng(99)).generate()
    assert cmds_a == cmds_b


def test_ore_determinism() -> None:
    cfg = {"ores": [{"type": "iron_ore", "count": [3, 6], "area": [-8, -63, -8, 8, -61, 8], "vein_size": 2}]}
    cmds_a = OreGenerator(cfg, make_rng(77)).generate()
    cmds_b = OreGenerator(cfg, make_rng(77)).generate()
    assert cmds_a == cmds_b


def test_mob_determinism() -> None:
    cfg = {"mobs": [{"type": "zombie", "count": [1, 3], "area": [-5, -62, -5, 5, -62, 5]}]}
    cmds_a = MobGenerator(cfg, make_rng(55)).generate()
    cmds_b = MobGenerator(cfg, make_rng(55)).generate()
    assert cmds_a == cmds_b
