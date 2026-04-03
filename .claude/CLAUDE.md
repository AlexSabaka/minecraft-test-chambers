# minecraft-test-chambers — Claude Code Context

Procedurally-generated Minecraft test chambers → flat JSONL training data for LLM fine-tuning.

## Commands

```bash
# Install
pip install minecraft-test-chambers            # from PyPI
pip install -e ".[dev]"                        # editable dev install (tests + linting)
pip install -e ".[recorder]"                   # screenshot capture (macOS/Windows/Linux)
uvx minecraft-test-chambers --help            # run without installing

# Tests & linting
python -m pytest tests/ -x -q
ruff check .                                   # line-length 100, target py310

# Java plugin (must be built separately — not distributed via PyPI)
cd recorder_plugin && mvn package -q
cp target/RecorderPlugin.jar ../server/plugins/

# Server lifecycle (server/ dir created in CWD)
minecraft-server setup          # download Paper JAR + verify config
minecraft-server start          # start server; applies world_config.yaml defaults via RCON
minecraft-server stop
minecraft-server status

# Record a session
minecraft-recorder start --chamber desert_tomb
minecraft-recorder start --chamber hostile_night --seed 42 --duration 120
minecraft-recorder start --chamber desert_tomb --screenshots --aggregate

# Post-processing
minecraft-recorder validate episodes/*.jsonl
minecraft-recorder aggregate episodes/foo.jsonl
minecraft-recorder merge-visual episodes/foo.jsonl
minecraft-recorder viewer       # open bundled episode_viewer.html in browser
minecraft-recorder dump-tools
minecraft-recorder --version
```

## Architecture

```
minecraft_recorder/           Python CLI — session lifecycle, RCON orchestration
  __main__.py                 Entry: start / validate / aggregate / merge-visual / viewer / dump-tools
  server.py                   Server lifecycle + RconClient (was minecraft_server.py)
  episode_writer.py           iter_records(), validate_episode(), aggregate_episode()
  screenshot_capture.py       ScreenshotSyncer + merge_visual() — macOS/Windows/Linux
  tool_definitions.py         JSON schemas for the 7 Holy action primitives
  world_config.yaml           Default gamerules + worldborder applied on server start
  episode_viewer.html         Bundled browser-based episode viewer

recorder_plugin/              Paper 1.20.6 Bukkit plugin — authoritative recorder (Java)
  PlayerRecorderListener.java All event handlers + GatherPool accumulator
  EpisodeWriter.java          Thread-safe JSONL writer (flushes each record)
  ObsSnapshot.java            Player state snapshot (must call on main thread)
  RecorderPlugin.java         /recorder start|stop|status + lifecycle

minecraft_test_chambers/      Chamber generation (Python)
  generators/                 One class per feature type; all extend FeatureGenerator
  chamber_loader.py           YAML → RCON command list; calls generators in fixed order
  chambers/                   22 YAML chamber scenarios (bundled package data)

episodes/                     Output JSONL written to CWD/episodes/ (git-ignored)
server/                       Paper server files in CWD/server/ (git-ignored)
```

**Data flow:** `minecraft_test_chambers/chambers/*.yaml` → `chamber_loader.load_chamber()` → RCON → Paper server → `CWD/episodes/*.jsonl`

**Python is orchestration only.** All action recording happens server-side in the Java plugin.

## Key Constants

Defined in both `__main__.py` and `minecraft_recorder/server.py`:

```python
RCON_HOST     = "127.0.0.1"
RCON_PORT     = 25575
RCON_PASSWORD = "minecraft_dev"
```

`RconClient` lives in `minecraft_recorder/server.py` and is imported by `__main__.py` — do not duplicate it.

## Episode / Action Schema

Seven action primitives: `navigate` · `gather` · `craft` · `interact` · `combat` · `transfer` · `say`

```json
{"action": "gather", "args": {"block_type": "iron_ore", "count": 3},
 "result": "Mined 3× iron_ore.", "obs": {"pos": [-3.4,-59.0,7.1], "facing": "North",
 "health": 18.5, "hunger": 17, "held": "iron_pickaxe", "inv": {"cobblestone": 14}, "xp": 3},
 "ts_start": 1772438426.54, "ts_end": 1772438432.10, "chamber": "desert_tomb", "seed": 0}
```

`gather.count` reflects the pooled total (multiple consecutive block breaks of the same type → one record). MineRL format uses `"controls"` key instead of `"action"`.

## Generator Pattern

```python
class FooGenerator(FeatureGenerator):
    def generate(self) -> list[str]:
        # Return raw RCON command strings, no leading '/'
```

- Constructor: `(config: dict[str, Any], rng: random.Random)` — always use `self.rng`, never `random.random()`.
- `_rand_pos(area)` — area always `[x1,y1,z1,x2,y2,z2]`.
- `_resolve_count(count)` — accepts `int` or `[min, max]`.
- **Chamber loading order:** reset → spawn → clear → environment → chamber_room → structures → `raw_cmds` → trees → caves → ores → mobs → inventory. `raw_cmds` intentionally runs before generators so terrain fills exist when ores/mobs are placed.

## YAML Chamber Schema

```yaml
name: snake_case_name          # MUST match filename stem exactly
description: "..."
features:
  biome: minecraft:<id>
  time: day|night|noon|...
  weather: clear|rain|thunder
  difficulty: peaceful|easy|normal|hard
  gamerule_overrides:
    doMobSpawning: "false"     # always strings
  mobs:
    - type: <entity_id>        # MC 1.16+ names (e.g. zombified_piglin not zombie_pigman)
      count: [min, max]
      area: [x1,y1,z1,x2,y2,z2]
  ores:
    - type: <block_id>
      count: [min, max]
      area: [x1,y1,z1,x2,y2,z2]
      vein_size: 3
      replace: stone           # optional — defaults to any replaceable block
  inventory:
    mainhand: <item_id>
    offhand: <item_id>
    items:
      iron_sword: 1
  raw_cmds: ["<cmd>"]          # escape hatch, no leading slash
```

World is a **superflat**: bedrock Y=-64, ground Y=-63/-62. All coords use negative Y.

## Action Pooling (GatherPool)

`PlayerRecorderListener` accumulates consecutive `BlockBreakEvent`s of the same block type into a `GatherPool`. The pool is flushed (written as one record) when:
- A different block type is broken.
- Any non-gather action fires (`maybeWriteNavigate` flushes the pool first).
- The 500 ms tick fires (`writeTick` flushes before writing the tick record).
- Player disconnects (`onPlayerQuit`) or recording stops (`flushAll`).

Navigate injection threshold: **1.0 block** (event-driven) = tick task threshold.

MineRL `attack` field is an **integer count** (not binary) — reflects actual click frequency within each 500 ms tick window.

## Gotchas

- **YAML filename must match `name:` field** — `list_chambers()` uses `Path.stem`. A mismatch loads under the wrong name silently.
- **Chamber YAMLs are in `minecraft_test_chambers/chambers/`** — not the repo root. `chamber_loader._CHAMBERS_DIR` uses `Path(__file__).parent / "chambers"`.
- **`ObsSnapshot.capture()` is main-thread-only** — never call from async event handlers; schedule via `runTask()`.
- **Episodes write to `Path.cwd() / "episodes"`** — the plugin writes there; Python derives the path from the plugin's RCON response. Running `minecraft-recorder` from different directories gives different episode locations.
- **`server/` is also CWD-relative** — `minecraft-server setup/start/stop` create and manage `./server/` in the working directory. Run from the project root.
- `episodes/` and `server/` are **git-ignored** — don't reference local server files in tests or assertions.
- Screenshot capture (`--screenshots`) uses game-window isolation on macOS (Quartz), Windows (pywin32), and Linux/X11 (xdotool). Falls back to full-screen on Wayland and when the window can't be found. Linux requires system package: `apt install xdotool` / `pacman -S xdotool`.
- **No batching in EpisodeWriter** — each record is flushed immediately to disk (`writer.flush()` after every write).
- Passive mobs (cow, sheep) spawn even on `peaceful`; hostile mobs are skipped.
- NBT in YAML: use 1.20.6-style lowercase `attributes:` / `minecraft:generic.max_health` (not legacy `Attributes:`).
- `validate_episode()` checks for **temporal gaps > 2 s** between consecutive records and reports them as errors.

## Testing

```bash
python -m pytest tests/ -x -q   # 84 tests; always pass before committing
```

- Fixed seed `FIXED_SEED = 42` everywhere — never use `random.random()` directly in tests.
- Generator tests: config dict + `make_rng()` → `.generate()` → assert on `list[str]`.
- Loader tests: `_noop_rcon` for dry runs, `MagicMock` for call-count assertions.
- `RconClient` imported from `minecraft_recorder.server` — not from `minecraft_server` (file moved).
- No tests for episode recording pipeline (EpisodeWriter, PlayerRecorderListener) — Java-side only.
