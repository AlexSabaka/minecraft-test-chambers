# minecraft-test-chambers

Procedurally-generated Minecraft test chambers for recording human playthroughs as **flat JSONL training data** for LLM fine-tuning.

## How it works

```
python -m minecraft_recorder start --chamber desert_tomb
```

1. Python loads the chamber world via RCON (mobs, structures, weather, time).
2. Sends `/recorder start <chamber> <seed>` to the Paper server via RCON.
3. **RecorderPlugin** (server-side Bukkit plugin) captures all player actions with authoritative server data and writes a flat JSONL file to `episodes/`.
4. Press `Ctrl-C` → Python sends `/recorder stop` → plugin flushes and closes the file.

## Output format

Each line in an episode file is one player action:

```json
{"action": "combat", "args": {"target_entity": "husk", "strategy": "melee+shield"}, "result": "Killed husk.", "obs": {"pos": [3.5, -57.0, -2.1], "facing": "North", "health": 16.5, "hunger": 19, "held": "diamond_sword", "inv": {"diamond_sword": 1, "shield": 1, "torch": 8}, "xp": 4}, "ts_start": 1772440100.123, "ts_end": 1772440107.456, "chamber": "desert_tomb", "seed": 0}
```

**Action types:** `navigate` · `gather` · `craft` · `interact` · `combat` · `transfer` · `say`

## Project layout

```
minecraft_recorder/       Python CLI (chamber loader + RCON orchestration)
  __main__.py             Entry point: start / validate / dump-tools
  episode_writer.py       iter_records() + validate_episode() utilities
  tool_definitions.py     JSON schemas for the 7 action primitives

recorder_plugin/          Paper plugin (Java) — the authoritative recorder
  src/…/RecorderPlugin.java   Plugin main + /recorder command
  src/…/PlayerRecorderListener.java  All Bukkit event handlers
  src/…/EpisodeWriter.java    Thread-safe JSONL writer
  src/…/ObsSnapshot.java      Player state capture

minecraft_test_chambers/   Chamber definitions (Python generators)
test_chambers/             YAML config files for each chamber
episodes/                  Recorded JSONL files (git-ignored)
server/                    Paper 1.20.6 server files (git-ignored)
```

## Setup

### Prerequisites
- Java 21+, Python 3.12+, Maven 3.9+
- Paper 1.20.6 server (files in `server/`)

### Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Build & install the plugin

```bash
cd recorder_plugin
mvn package -q
cp target/RecorderPlugin.jar ../server/plugins/
```

Restart the server (or `/reload confirm`) to load the plugin.

### Run the server

```bash
cd server && java -Xmx2G -jar paper-1.20.6-151.jar --nogui
```

### Record a session

```bash
# In a second terminal:
python -m minecraft_recorder start --chamber desert_tomb
# Play in Minecraft, then Ctrl-C to stop.
```

### Validate recordings

```bash
python -m minecraft_recorder validate episodes/*.jsonl
```

### Available chambers

| Chamber | Description |
|---|---|
| `desert_tomb` | Underground tomb with husks, gold ore, and a trapped chest |
| `plains_day` | Open plains, daytime |
| `forest_day` | Dense forest, daytime |
| `hostile_night` | Surface at night with hostile mobs |
| `open_plains` | Flat plains, no mobs |
| `rain_forest` | Rainfall, forest biome |
| `deep_mine` | Deep mining scenario |

### Add a chamber

Add a YAML file to `test_chambers/` and a generator class under `minecraft_test_chambers/generators/`. See existing chambers for the pattern.

## Rebuilding the plugin after changes

```bash
cd recorder_plugin && mvn package -q && cp target/RecorderPlugin.jar ../server/plugins/
```

Then `/reload confirm` or restart the server.
