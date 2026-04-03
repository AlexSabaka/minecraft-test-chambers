# minecraft-test-chambers

Procedurally-generated Minecraft test chambers for recording human playthroughs as **flat JSONL training data** for LLM fine-tuning.

## How it works

```bash
minecraft-recorder start --chamber desert_tomb
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

```text
minecraft_recorder/           Python CLI + server management
  __main__.py                 Entry: start / validate / aggregate / merge-visual / viewer / dump-tools
  server.py                   Server lifecycle + RconClient (minecraft-server entry point)
  episode_writer.py           iter_records(), validate_episode(), aggregate_episode()
  screenshot_capture.py       ScreenshotSyncer + merge_visual()
  tool_definitions.py         JSON schemas for the 7 action primitives
  world_config.yaml           Default gamerules applied on server start
  episode_viewer.html         Bundled browser-based episode viewer

recorder_plugin/              Paper plugin (Java) — the authoritative recorder
  src/…/RecorderPlugin.java       Plugin main + /recorder command
  src/…/PlayerRecorderListener.java  All Bukkit event handlers
  src/…/EpisodeWriter.java    Thread-safe JSONL writer
  src/…/ObsSnapshot.java      Player state snapshot

minecraft_test_chambers/      Chamber definitions (Python generators)
  generators/                 One class per feature type
  chamber_loader.py           YAML → RCON command list
  chambers/                   22 YAML chamber scenarios (bundled package data)

episodes/                     Recorded JSONL files — written to CWD/episodes/ (git-ignored)
server/                       Paper 1.20.6 server files — managed in CWD/server/ (git-ignored)
```

## Setup

### Prerequisites

- Java 21+, Python 3.10+, Maven 3.9+
- A Paper 1.20.6 server set up via `minecraft-server setup` (see below)

### Install

```bash
# From PyPI (recommended)
pip install minecraft-test-chambers
pip install "minecraft-test-chambers[recorder]"  # + screenshot capture

# Or run directly with uvx (no install needed)
uvx minecraft-test-chambers --help

# Editable dev install from source
pip install -e ".[dev,recorder]"
```

### Build & install the plugin

The server-side recorder plugin must be built from source and dropped into the Paper plugins folder — it is not distributed via PyPI.

```bash
cd recorder_plugin
mvn package -q
cp target/RecorderPlugin.jar ../server/plugins/
```

### Set up and run the server

```bash
# Download Paper JAR, verify config, apply world_config.yaml defaults
minecraft-server setup

# Start server in the background (blocks until RCON is ready)
minecraft-server start

# Check status / stop
minecraft-server status
minecraft-server stop
```

### Record a session

```bash
# In a second terminal (server must be running):
minecraft-recorder start --chamber desert_tomb
# Play in Minecraft, then Ctrl-C to stop.
```

Optional flags:

```bash
minecraft-recorder start --chamber desert_tomb \
  --screenshots        \  # capture game-window frames alongside the episode
  --format minerl      \  # record raw MineRL control space instead of semantic actions
  --duration 120       \  # auto-stop after 120 seconds
  --aggregate             # post-process: merge consecutive navigate records
```

Screenshot capture isolates the game window on all platforms:

| Platform        | Method                   | Prerequisite                                  |
|-----------------|--------------------------|-----------------------------------------------|
| macOS           | Quartz + `screencapture` | included in `.[recorder]`                     |
| Windows         | `win32gui` + mss region  | included in `.[recorder]`                     |
| Linux (X11)     | `xdotool` + mss region   | `apt install xdotool` / `pacman -S xdotool`   |
| Linux (Wayland) | mss full-screen fallback | none                                          |

Pass `--window ""` to force full-screen capture on any platform.

### Validate recordings

```bash
minecraft-recorder validate episodes/*.jsonl
```

### Aggregate navigate records

```bash
minecraft-recorder aggregate episodes/episode.jsonl
```

Merges chains of consecutive tick-generated `navigate` records into single path records, writing a `_aggregated.jsonl` output. Useful for reducing noise in training data. Also available as `--aggregate` on `start`.

### Merge screenshot sidecar

```bash
minecraft-recorder merge-visual episodes/episode.jsonl
```

Combines the `_visual.jsonl` frames captured by `--screenshots` into the episode file as `image_b64` fields, writing a `_merged.jsonl` output.

### Open the episode viewer

```bash
minecraft-recorder viewer
```

Opens the bundled `episode_viewer.html` in the default browser. Load any episode JSONL file directly — no server needed.

### Add a chamber

Add a YAML file to `minecraft_test_chambers/chambers/` and a generator class under `minecraft_test_chambers/generators/`. See existing chambers for the pattern.

## Rebuilding the plugin after changes

```bash
cd recorder_plugin && mvn package -q && cp target/RecorderPlugin.jar ../server/plugins/
```

Then `/reload confirm` or restart the server.
