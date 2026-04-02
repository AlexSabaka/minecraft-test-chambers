# Copilot Instructions

## Project Purpose

Pipeline that procedurally generates Minecraft test chambers and records human playthroughs as flat JSONL training data for LLM fine-tuning.

## Architecture

```
minecraft_recorder/       Python CLI — RCON orchestration, recording session lifecycle
minecraft_test_chambers/  Chamber generation — YAML → RCON command lists
  generators/             One class per feature type; all inherit FeatureGenerator
recorder_plugin/          Paper/Bukkit plugin (Java) — authoritative server-side recorder
test_chambers/            YAML definitions for each scenario
episodes/                 Output JSONL files (git-ignored)
server/                   Paper 1.20.6 server files (git-ignored)
```

Data flow: `test_chambers/*.yaml` → `chamber_loader.load_chamber()` → RCON commands → Paper server → `episodes/*.jsonl`

## Build & Test

```bash
# Python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,recorder]"
pytest

# Java plugin
cd recorder_plugin && mvn package -q
cp target/RecorderPlugin.jar ../server/plugins/

# Server
cd server && java -Xmx2G -jar paper-1.20.6-151.jar --nogui
# Or via python helper:
python minecraft_server.py start

# Recording session
python -m minecraft_recorder start --chamber desert_tomb
python -m minecraft_recorder validate episodes/*.jsonl
```

Linter: `ruff check .` (line-length 100, target py310).

## Generator Pattern

All feature generators extend `FeatureGenerator` ([minecraft_test_chambers/generators/base.py](../minecraft_test_chambers/generators/base.py)):

```python
class FooGenerator(FeatureGenerator):
    def generate(self) -> list[str]:
        # Return raw RCON command strings, no leading '/'
        ...
```

- Constructor always takes `(config: dict[str, Any], rng: random.Random)` — never generate randomness directly; use `self.rng`.
- `_rand_pos(area)` — area is always 6 ints `[x1,y1,z1,x2,y2,z2]`.
- `_resolve_count(count)` — accepts `int` or `[min, max]` list.
- Determinism is tested: same seed must produce identical command lists.
- See [minecraft_test_chambers/generators/mob.py](../minecraft_test_chambers/generators/mob.py) for NBT generation; use 1.20.6-style lowercase `attributes:` / `minecraft:generic.max_health` (not legacy `Attributes:`).

Chamber loading order ([chamber_loader.py](../minecraft_test_chambers/chamber_loader.py)): reset → spawn point → clear → environment → chamber room → structures → `raw_cmds` → trees → **caves** → ores → mobs → inventory.

**Pipeline rationale:** `raw_cmds` runs before generators so terrain fills are in place when ores/mobs are placed. Caves carve into that terrain, then ores go into remaining stone (use `replace: stone`), then mobs spawn on solid ground.

## YAML Chamber Schema

```yaml
name: snake_case_name
description: "..."
spawn_point: [x, y, z]  # optional, default [0, -56, 0]

features:
  biome: minecraft:<id>
  time: day|night|noon|sunrise|sunset|midnight|dawn|dusk
  weather: clear|rain|thunder
  difficulty: peaceful|easy|normal|hard
  gamerule_overrides:
    doMobSpawning: "false"    # always string values

  mobs:
    - type: <entity_id>
      count: [min, max]       # or fixed int
      area: [x1,y1,z1,x2,y2,z2]
      nbt: "{...}"            # optional raw NBT
      # Note: passive mobs (cow, sheep, etc.) spawn even on peaceful;
      # hostile mobs are skipped on peaceful.

  caves:                      # procedural cave carver (worm algorithm)
    area: [x1,y1,z1,x2,y2,z2]
    tunnels: [min, max]       # number of worms (default [2,4])
    tunnel_length: [min, max] # steps per worm (default [20,40])
    branch_chance: 0.25       # fork probability per step
    room_chance: 0.15         # room expansion probability per step
    room_radius: [min, max]   # room sphere radius (default [3,4])
    min_radius: 1             # minimum tunnel radius
    max_radius: 2             # maximum tunnel radius
    entrances:                # optional vertical shafts
      - [x, y_surface, z]

  ores:
    - type: <block_id>
      count: [min, max]
      area: [x1,y1,z1,x2,y2,z2]
      vein_size: 3            # optional BFS cluster

  inventory:                  # optional — clears then equips player
    mainhand: <item_id>       # equip main hand
    offhand: <item_id>        # equip off hand
    armor: [helmet, chest, legs, boots]
    items:                    # item_id: count map
      diamond_sword: 1
      torch: 16

  raw_cmds: ["<cmd>"]         # escape hatch; no leading slash
```

Seed is random by default (override via `--seed` CLI flag or `seed_override` param).
See [test_chambers/desert_tomb.yaml](../test_chambers/desert_tomb.yaml) and [test_chambers/spider_cave.yaml](../test_chambers/spider_cave.yaml) as canonical examples.

## Episode / Action Schema

Seven action primitives ("Holy-7") defined in [minecraft_recorder/tool_definitions.py](../minecraft_recorder/tool_definitions.py): `navigate`, `gather`, `craft`, `interact`, `combat`, `transfer`, `say`.

Each JSONL record:
```json
{"action": "combat", "args": {"target_entity": "husk", "strategy": "melee+shield"},
 "result": "Killed husk.", "obs": {"pos": [3.5,-57.0,-2.1], "facing": "North",
 "health": 16.5, "hunger": 19, "held": "diamond_sword", "inv": {"diamond_sword": 1}, "xp": 4},
 "ts_start": 1772440100.123, "ts_end": 1772440107.456, "chamber": "desert_tomb", "seed": 0}
```

Use `iter_records()` / `validate_episode()` from [minecraft_recorder/episode_writer.py](../minecraft_recorder/episode_writer.py) to read/validate episodes.

## RCON

- **One-shot** (`_rcon_send` in [minecraft_server.py](../minecraft_server.py)): lifetime commands (start/stop/status).
- **Persistent** (`RconClient` context manager): bulk chamber loading — reuses a single TCP connection to avoid per-command auth overhead. Password: `"minecraft_dev"`.
- Global defaults applied at server start come from [world_config.yaml](../world_config.yaml); chamber `gamerule_overrides` take precedence.

## Test Conventions

- Fixed seed `FIXED_SEED = 42` everywhere — never use `random.random()` directly in tests.
- Generator tests: instantiate with config dict + `make_rng()` → call `.generate()` → assert on `list[str]`.
- Loader tests: use `_noop_rcon` (returns `""`) for dry runs; `MagicMock` for call counts.
- RCON socket reuse: patch `socket.socket`, verify `connect` called exactly once per session.
- See [tests/test_generators.py](../tests/test_generators.py) and [tests/test_chamber_loader.py](../tests/test_chamber_loader.py).
