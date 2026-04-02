"""Integration tests for chamber_loader.

Uses a mock RCON function; does NOT require a Minecraft server.
Tests verify:
  - list_chambers() discovers all YAML files
  - load_chamber() produces a LoadResult with expected metadata
  - seed override produces different command counts but deterministic results
  - dry_run=True marks results ok without calling rcon_fn
  - RCON errors are captured in LoadResult.errors
  - Backward-compat: legacy rcon_cmds-only YAML still loads
  - FileNotFoundError for unknown chamber
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from minecraft_test_chambers.chamber_loader import (
    LoadResult,
    list_chambers,
    load_chamber,
)


# ─── list_chambers ────────────────────────────────────────────────────────────

def test_list_chambers_returns_expected() -> None:
    names = list_chambers()
    # Core chambers that must always be present (subset check — new chambers may be added)
    expected_core = {
        "deep_mine", "forest_day", "hostile_night", "open_plains", "plains_day",
        "rain_forest", "desert_day", "jungle_ruins", "swamp_night", "ice_tundra",
        "spider_cave", "village_assault", "nether_remnant", "end_island",
    }
    assert expected_core <= set(names), (
        f"Missing chambers: {expected_core - set(names)}"
    )


def test_list_chambers_sorted() -> None:
    names = list_chambers()
    assert names == sorted(names)


# ─── load_chamber — dry_run ───────────────────────────────────────────────────

def _noop_rcon(cmd: str) -> str:
    return ""


def test_load_plains_day_dry_run() -> None:
    result = load_chamber(
        "plains_day", _noop_rcon, seed_override=505, dry_run=True, verbose=False,
    )
    assert isinstance(result, LoadResult)
    assert result.chamber == "plains_day"
    assert result.seed == 505
    assert result.commands_run > 0
    assert result.success
    assert all(r.ok for r in result.results)


def test_dry_run_never_calls_rcon() -> None:
    rcon = MagicMock(return_value="")
    load_chamber("plains_day", rcon, dry_run=True, verbose=False)
    rcon.assert_not_called()


def test_load_forest_day_dry_run() -> None:
    result = load_chamber(
        "forest_day", _noop_rcon, seed_override=101, dry_run=True, verbose=False,
    )
    assert result.seed == 101
    assert result.commands_run > 100   # trees + caves + ores + mobs = many commands
    # Cave generator should produce fill ... air commands
    cmds = [r.command for r in result.results]
    cave_cmds = [c for c in cmds if c.startswith("fill") and c.endswith("air")]
    assert cave_cmds, "Expected cave carving commands"
    # Passive mobs should spawn even on peaceful difficulty
    summon_cmds = [c for c in cmds if c.startswith("summon")]
    assert summon_cmds, "Expected passive mob summons on peaceful"


def test_load_hostile_night_dry_run() -> None:
    result = load_chamber("hostile_night", _noop_rcon, dry_run=True, verbose=False)
    cmds = [r.command for r in result.results]
    assert any("summon" in c for c in cmds)


def test_load_deep_mine_dry_run() -> None:
    result = load_chamber("deep_mine", _noop_rcon, dry_run=True, verbose=False)
    cmds = [r.command for r in result.results]
    assert any("deepslate" in c for c in cmds)


def test_load_rain_forest_dry_run() -> None:
    result = load_chamber("rain_forest", _noop_rcon, dry_run=True, verbose=False)
    cmds = [r.command for r in result.results]
    assert any("weather" in c and "rain" in c for c in cmds)
    assert any("instant_damage" in c for c in cmds)


# ─── seed override ────────────────────────────────────────────────────────────

def test_seed_override_changes_command_content() -> None:
    r1 = load_chamber("forest_day", _noop_rcon, seed_override=1, dry_run=True, verbose=False)
    r2 = load_chamber("forest_day", _noop_rcon, seed_override=2, dry_run=True, verbose=False)
    cmds1 = [r.command for r in r1.results]
    cmds2 = [r.command for r in r2.results]
    # Different seeds may produce different per-position commands for trees/ores
    assert cmds1 != cmds2


def test_seed_override_deterministic() -> None:
    r1 = load_chamber("forest_day", _noop_rcon, seed_override=99, dry_run=True, verbose=False)
    r2 = load_chamber("forest_day", _noop_rcon, seed_override=99, dry_run=True, verbose=False)
    assert [r.command for r in r1.results] == [r.command for r in r2.results]


# ─── RCON success / failure tracking ─────────────────────────────────────────

def test_rcon_success_marks_ok() -> None:
    rcon = MagicMock(return_value="OK")
    result = load_chamber("plains_day", rcon, dry_run=False, verbose=False)
    assert result.success
    assert all(r.ok for r in result.results)


def test_rcon_failure_captured() -> None:
    rcon = MagicMock(return_value=None)  # None = failure
    result = load_chamber("plains_day", rcon, dry_run=False, verbose=False)
    assert not result.success
    assert len(result.errors) > 0
    assert any("RCON failure" in e for e in result.errors)


# ─── Backward-compatibility with legacy rcon_cmds YAML ───────────────────────

def test_legacy_rcon_cmds_loads(tmp_path: Path) -> None:
    """A YAML with only rcon_cmds (no features:) should still load."""
    legacy_yaml = {
        "name": "legacy_test",
        "description": "legacy",
        "time": "day",
        "weather": "clear",
        "tp_y": -57,
        "rcon_cmds": [
            "setblock 0 -63 0 gold_block",
            "say legacy chamber loaded",
        ],
    }
    yaml_path = tmp_path / "legacy_test.yaml"
    yaml_path.write_text(yaml.dump(legacy_yaml))

    # Patch the chambers directory to point to tmp_path
    with patch("minecraft_test_chambers.chamber_loader._CHAMBERS_DIR", tmp_path):
        result = load_chamber("legacy_test", _noop_rcon, dry_run=True, verbose=False)

    cmds = [r.command for r in result.results]
    assert any("setblock 0 -63 0 gold_block" in c for c in cmds)
    assert any("say legacy chamber loaded" in c for c in cmds)
    assert result.success


def test_legacy_rcon_cmds_includes_time_weather(tmp_path: Path) -> None:
    """Legacy path still emits time/weather setup commands."""
    legacy_yaml = {
        "name": "legacy_env",
        "time": "night",
        "weather": "rain",
        "tp_y": -57,
        "rcon_cmds": ["setblock 0 -63 0 stone"],
    }
    yaml_path = tmp_path / "legacy_env.yaml"
    yaml_path.write_text(yaml.dump(legacy_yaml))

    with patch("minecraft_test_chambers.chamber_loader._CHAMBERS_DIR", tmp_path):
        result = load_chamber("legacy_env", _noop_rcon, dry_run=True, verbose=False)

    cmds = [r.command for r in result.results]
    assert any("time set 18000" in c for c in cmds)
    assert any("weather rain" in c for c in cmds)


# ─── Error handling ───────────────────────────────────────────────────────────

def test_unknown_chamber_raises() -> None:
    with pytest.raises(FileNotFoundError, match="does_not_exist"):
        load_chamber("does_not_exist", _noop_rcon, verbose=False)


def test_reset_commands_always_present() -> None:
    """Every chamber must start with kill + fill air + restore grass."""
    result = load_chamber("plains_day", _noop_rcon, dry_run=True, verbose=False)
    cmds = [r.command for r in result.results]
    assert any(c.startswith("kill @e[type=!player") for c in cmds)
    assert any("air" in c and c.startswith("fill") for c in cmds)
    assert any("grass_block" in c for c in cmds)
    assert any("bedrock" in c for c in cmds)


# ─── LoadResult properties ────────────────────────────────────────────────────

def test_load_result_success_property() -> None:
    r = load_chamber("plains_day", _noop_rcon, dry_run=True, verbose=False)
    assert r.success is True
    assert r.errors == []


# ─── Spawn point ──────────────────────────────────────────────────────────────

def test_default_spawn_point_commands() -> None:
    """Default spawn point [0, -56, 0] emitted when YAML has no spawn_point."""
    result = load_chamber("plains_day", _noop_rcon, dry_run=True, verbose=False)
    cmds = [r.command for r in result.results]
    assert "setworldspawn 0 -56 0" in cmds
    assert "spawnpoint @a 0 -56 0" in cmds
    assert "tp @a 0 -56 0" in cmds


def test_custom_spawn_point(tmp_path: Path) -> None:
    """Custom spawn_point in YAML produces matching commands."""
    chamber_yaml = {
        "name": "spawn_test",
        "description": "test",
        "spawn_point": [10, -50, 5],
        "features": {
            "time": "day",
            "weather": "clear",
            "difficulty": "peaceful",
        },
    }
    yaml_path = tmp_path / "spawn_test.yaml"
    yaml_path.write_text(yaml.dump(chamber_yaml))

    with patch("minecraft_test_chambers.chamber_loader._CHAMBERS_DIR", tmp_path):
        result = load_chamber("spawn_test", _noop_rcon, dry_run=True, verbose=False)

    cmds = [r.command for r in result.results]
    assert "setworldspawn 10 -50 5" in cmds
    assert "spawnpoint @a 10 -50 5" in cmds
    assert "tp @a 10 -50 5" in cmds


# ─── Inventory ────────────────────────────────────────────────────────────────

def test_inventory_generates_clear_and_give() -> None:
    """Inventory section produces clear + give + equip commands."""
    result = load_chamber(
        "village_assault", _noop_rcon, seed_override=1, dry_run=True, verbose=False,
    )
    cmds = [r.command for r in result.results]
    assert "clear @a" in cmds
    assert "give @a minecraft:iron_sword 1" in cmds
    assert "give @a minecraft:shield 1" in cmds
    assert "item replace entity @a weapon.mainhand with minecraft:iron_sword" in cmds
    assert "item replace entity @a weapon.offhand with minecraft:shield" in cmds


def test_inventory_armor_equip(tmp_path: Path) -> None:
    """Armor list equips head/chest/legs/feet slots."""
    chamber_yaml = {
        "name": "armor_test",
        "description": "test",
        "features": {
            "time": "day",
            "weather": "clear",
            "inventory": {
                "armor": [
                    "iron_helmet", "iron_chestplate",
                    "iron_leggings", "iron_boots",
                ],
                "items": {"iron_sword": 1},
            },
        },
    }
    yaml_path = tmp_path / "armor_test.yaml"
    yaml_path.write_text(yaml.dump(chamber_yaml))

    with patch("minecraft_test_chambers.chamber_loader._CHAMBERS_DIR", tmp_path):
        result = load_chamber("armor_test", _noop_rcon, dry_run=True, verbose=False)

    cmds = [r.command for r in result.results]
    assert "item replace entity @a armor.head with minecraft:iron_helmet" in cmds
    assert "item replace entity @a armor.chest with minecraft:iron_chestplate" in cmds
    assert "item replace entity @a armor.legs with minecraft:iron_leggings" in cmds
    assert "item replace entity @a armor.feet with minecraft:iron_boots" in cmds


def test_clear_always_runs_even_without_inventory() -> None:
    """Every chamber clears player inventory, even without an inventory section."""
    result = load_chamber(
        "plains_day", _noop_rcon, seed_override=1, dry_run=True, verbose=False,
    )
    cmds = [r.command for r in result.results]
    assert "clear @a" in cmds
    assert not any(c.startswith("give @a") for c in cmds)


def test_random_seed_when_no_seed_in_yaml() -> None:
    """With no seed in YAML and no override, a random seed is generated."""
    r1 = load_chamber("plains_day", _noop_rcon, dry_run=True, verbose=False)
    r2 = load_chamber("plains_day", _noop_rcon, dry_run=True, verbose=False)
    assert isinstance(r1.seed, int)
    assert isinstance(r2.seed, int)
    # Extremely unlikely but technically possible for two random seeds to match.
    # We just verify they are valid ints; determinism is tested with seed_override.


def test_load_result_chamber_name() -> None:
    r = load_chamber("open_plains", _noop_rcon, dry_run=True, verbose=False)
    assert r.chamber == "open_plains"


# ─── Pipeline ordering ───────────────────────────────────────────────────────

def test_raw_cmds_run_before_ores_and_mobs() -> None:
    """raw_cmds (terrain) must execute before ore/mob generators so that
    ores are placed into solid terrain and mobs spawn on real ground."""
    result = load_chamber(
        "forest_day", _noop_rcon, seed_override=42, dry_run=True, verbose=False,
    )
    cmds = [r.command for r in result.results]

    # Find indices of key command types
    terrain_fill = next(
        i for i, c in enumerate(cmds) if "fill" in c and "stone" in c and "-32" in c
    )
    first_ore = next(
        (i for i, c in enumerate(cmds) if "coal_ore" in c or "iron_ore" in c),
        None,
    )
    first_summon = next(
        (i for i, c in enumerate(cmds) if c.startswith("summon")),
        None,
    )

    if first_ore is not None:
        assert terrain_fill < first_ore, "Terrain fill must precede ore placement"
    if first_summon is not None:
        assert terrain_fill < first_summon, "Terrain fill must precede mob summons"


def test_caves_run_before_ores() -> None:
    """Cave carving must happen before ore placement so ores appear on walls."""
    result = load_chamber(
        "forest_day", _noop_rcon, seed_override=42, dry_run=True, verbose=False,
    )
    cmds = [r.command for r in result.results]

    cave_cmds_idx = [i for i, c in enumerate(cmds) if c.endswith("air") and "fill" in c]
    ore_cmds_idx = [
        i for i, c in enumerate(cmds)
        if any(o in c for o in ("coal_ore", "iron_ore", "copper_ore", "gold_ore", "diamond_ore"))
    ]

    if cave_cmds_idx and ore_cmds_idx:
        assert max(cave_cmds_idx) < min(ore_cmds_idx), (
            "All cave carving must finish before first ore placement"
        )


# ─── RconClient ───────────────────────────────────────────────────────────────

def test_rcon_client_reuses_socket() -> None:
    """RconClient must open exactly one TCP connection regardless of command count."""
    import sys
    import os
    # Ensure the repo root (where minecraft_server.py lives) is importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from minecraft_server import RconClient  # noqa: PLC0415

    auth_response = b"\x0a\x00\x00\x00" + b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00"
    cmd_response  = b"\x0a\x00\x00\x00" + b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x00\x00"

    with patch("socket.socket") as mock_cls:
        mock_sock = MagicMock()
        mock_sock.recv.return_value = auth_response + cmd_response * 3
        mock_cls.return_value = mock_sock

        with RconClient(password="test") as client:
            client.send("time set 0")
            client.send("weather clear")
            client.send("difficulty normal")

        # TCP connect must happen exactly once for all three commands
        mock_sock.connect.assert_called_once()
        # Socket must be closed on context-manager exit
        mock_sock.close.assert_called_once()
