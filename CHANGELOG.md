# Changelog

All notable changes to this project are documented here.

---

## [0.2.0] — 2026-04-03 — Distribution & packaging

### Added

- **Action pooling — GatherPool accumulator** (`PlayerRecorderListener.java`)
  Consecutive `BlockBreakEvent`s of the same block type are now buffered into a per-player `GatherPool` instead of written immediately. The pool is flushed as a single `gather` record with the real accumulated `count` when the block type changes, any non-gather action fires, the 500 ms tick fires, the player disconnects, or recording stops. This removes the longstanding fragmentation where mining 20 iron ore produced 20 separate JSONL lines — the `count` field in the schema was always intended for batching but was never implemented.

- **Navigate injection threshold lowered: 4.0 → 1.0 blocks** (`PlayerRecorderListener.java`)
  The event-driven `maybeWriteNavigate()` threshold was inconsistently higher than the tick task threshold (already 1.0). Movements ≤ 4 blocks between actions were silently discarded. Aligning both to 1.0 ensures small positional changes are captured.

- **MineRL attack count** (`PlayerRecorderListener.java`)
  Replaced `Map<UUID, Boolean> mrlAttack` with `Map<UUID, Integer> mrlAttackCount`. Each attack event now increments a counter rather than setting a flag, so the MineRL `attack` field records the actual click frequency within each 500 ms tick window instead of collapsing it to 0 or 1.

- **`aggregate_episode(path) → Path`** (`episode_writer.py`)
  New post-processing function that merges chains of consecutive tick-generated `navigate` records (where the `to` position of record N equals the `from` of record N+1) into single path records spanning the full `ts_start`/`ts_end` range. Writes to `{stem}_aggregated.jsonl`.

- **Temporal gap detection in `validate_episode()`** (`episode_writer.py`)
  Validation now checks for gaps > 2 s between consecutive records and includes them in the error list. Helps surface recording sessions where the tick task was stalled.

- **`aggregate` subcommand** (`__main__.py`)
  `python -m minecraft_recorder aggregate episodes/foo.jsonl` — standalone post-processing for existing episodes.

- **`--aggregate` flag on `start`** (`__main__.py`)
  Auto-runs `aggregate_episode()` immediately after a recording session ends.

- **`.claude/CLAUDE.md`** — Claude Code project context file covering commands, architecture, YAML schema, action pooling semantics, and non-obvious gotchas.

- **Cross-platform screenshot capture** (`screenshot_capture.py`, `pyproject.toml`)
  Window-specific capture now works on all three platforms. macOS continues to use Quartz + `screencapture -l` (unchanged). Windows uses `win32gui.EnumWindows` to locate the game HWND and `mss` region capture; requires `pywin32` (now included in `.[recorder]`). Linux/X11 uses `xdotool search --name` + `xdotool getwindowgeometry` to get the window rect, then `mss` region capture; requires the `xdotool` system package (`apt install xdotool` / `pacman -S xdotool`). Wayland detects `XDG_SESSION_TYPE=wayland` and silently falls back to full-screen `mss` capture. All paths converge on `_grab_full_screen_png_bytes()` on any failure. `ScreenshotSyncer` keeps the macOS CGWindowNumber cache path unchanged and delegates to `_grab_png_bytes()` for Windows/Linux.

- **`CHANGELOG.md`**, **`README.md`** — documented all changes; README now includes a platform support table for `--screenshots`.

### Changed — distribution & packaging

- **Package restructured for PyPI / `uvx` distribution** (`pyproject.toml`, multiple files)
  Five blocking issues prevented the package from functioning after a pip install. All resolved:

  | File move | Reason |
  |-----------|--------|
  | `test_chambers/` → `minecraft_test_chambers/chambers/` | Must be inside a package to ship as package data |
  | `minecraft_server.py` → `minecraft_recorder/server.py` | Was dynamically loaded via `importlib.util`; now a proper importable module |
  | `world_config.yaml` → `minecraft_recorder/world_config.yaml` | Must be co-located with the module that reads it |
  | `episode_viewer.html` → `minecraft_recorder/episode_viewer.html` | Bundled via `importlib.resources` for the `viewer` subcommand |

- **`pyproject.toml` rewritten** — added `[project.scripts]` (`minecraft-recorder`, `minecraft-server`), `[tool.setuptools.package-data]`, full metadata (`readme`, `license`, `authors`, `keywords`, `classifiers`, `[project.urls]`), `[tool.pytest.ini_options]`.

- **`minecraft_recorder/__main__.py`**: removed `_REPO_ROOT` / `sys.path.insert` hack; replaced `importlib.util` dynamic load of `minecraft_server.py` with `from minecraft_recorder.server import RconClient`; episode output path changed from `_REPO_ROOT / "episodes"` to `Path.cwd() / "episodes"` so it always writes to the user's working directory; added `--version` flag; added `viewer` subcommand.

- **`minecraft_recorder/server.py`**: `SERVER_DIR` changed from `Path(__file__).parent / "server"` (inside site-packages) to `Path.cwd() / "server"` (user's working directory); implemented `_apply_world_config()` which reads the bundled `world_config.yaml` — the file was documented but never actually read by any code.

- **`minecraft_test_chambers/chamber_loader.py`**: `_CHAMBERS_DIR` changed from `_REPO_ROOT / "test_chambers"` to `Path(__file__).parent / "chambers"` so chambers are found in the installed package.

- **`tests/test_chamber_loader.py`**: removed `sys.path.insert` hack; import changed from `from minecraft_server import RconClient` to `from minecraft_recorder.server import RconClient`.

- **`LICENSE`** (MIT) created.

- **`.markdownlint.json`** created — enables `MD024: siblings_only` so repeated `### Added` / `### Fixed` section headings across changelog versions don't trigger linter warnings.

---

## [0.1.1] — 2026-04-02

### Fixed

- **`__main__.py`: RCON connection resource leak** — replaced manual `rcon_client.__enter__()` / `.__exit__()` calls with a proper `with` statement. Previously an exception between `__enter__` and the `try` block would leave the TCP connection open.

- **`__main__.py`: `--duration` argument was never registered** — `cmd_start` read `getattr(args, "duration", None)` and used it for the timed-stop loop, but `p_start` never added the argument to the parser. Passing `--duration` silently did nothing.

- **`episode_writer.py`: dead `first_record` tracking variable removed** — the variable was written but never read for its value; the outer `if "controls" in record:` already handled all records identically. Removing it simplifies the validation loop without changing behaviour.

- **`cave.py`: silent failure on invalid entrance configuration** — `for ey in range(y_top, ey_surface + 1)` produced an empty range when `ey_surface <= y_top`, silently generating no entrance shaft. Now emits a `warnings.warn` so the caller knows the shaft was skipped.

- **`test_chambers/mansion_ambush.yaml`: removed invalid `allium` mob entry** — `allium` is a flower (block), not an entity; it could never spawn (`count: [0, 0]`) but was invalid schema noise that would cause errors if count were non-zero.

- **`test_chambers/nether_remnant.yaml`: deprecated entity name** — `zombie_pigman` → `zombified_piglin` (renamed in MC 1.16+). Using the old name causes the mob to silently fail to spawn on Paper 1.20.6.

- **`test_chambers/volcano_escape.yaml`: invalid chest item** — `minecraft:fire_resistance` is a potion *effect*, not an item ID; it cannot be placed in a chest NBT slot. Replaced with `minecraft:golden_apple`.

- **`test_chambers/village_assault.yaml`: removed no-op raw command** — `fill -6 -63 -6 6 -63 6 air replace air` replaced air with air and had no effect.

- **`test_chambers/dark_heist.yaml` → `deep_dark_heist.yaml`** — the file's `name:` field, header comment, and description all referenced `deep_dark_heist`, but the filename was `dark_heist.yaml`. Since `list_chambers()` uses `Path.stem`, the chamber loaded under the wrong name (`--chamber dark_heist` instead of `--chamber deep_dark_heist`).

- **`PlayerRecorderListener.java`: memory leak on player disconnect** — all 15 UUID-keyed `ConcurrentHashMap`s (combat, inventory, tick baselines, MineRL flags) were never cleared when a player left the server. On long-running servers this leaked memory indefinitely. Added `onPlayerQuit` handler that removes all per-player entries.

### Changed

- **README.md**: corrected Python version (`3.12+` → `3.10+` to match `pyproject.toml`), documented `.[dev]` and `.[recorder]` install extras, added missing project layout entries (`screenshot_capture.py`, `tool_definitions.py`, `minecraft_server.py`, `episode_viewer.html`), documented `merge-visual` subcommand, documented `--screenshots`, `--format`, and `--duration` flags.

---

## [0.1.0] — 2026-03-31 (initial patches)

### Fixed

- **`episode_writer.py`**: removed imports of deleted modules that caused `ImportError` on startup.
- **`tool_definitions.py`**: updated JSON schemas to match actual plugin output field names.
- **`__main__.py`**: restored missing `import threading` that caused `NameError` when `--screenshots` was used.

---

## [0.0.1] — 2026-03-31 (initial commit)

Initial project creation:

- Python CLI (`minecraft_recorder/`) — RCON orchestration, chamber loading, episode validation, screenshot capture, tool definitions.
- Paper Bukkit plugin (`recorder_plugin/`) — server-side event recorder writing flat JSONL episodes.
- Chamber generators (`minecraft_test_chambers/generators/`) — procedural world setup: mobs, ores, trees, caves, structures, environments.
- 22 YAML test chamber scenarios covering biomes from plains to nether, difficulty easy through extreme.
- 84 unit tests covering generators and chamber loader.
- Browser-based episode viewer (`episode_viewer.html`).
