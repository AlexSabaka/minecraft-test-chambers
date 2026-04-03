"""
Recorder CLI — orchestrate test-chamber recordings.

Subcommands
-----------
start       Load a chamber then delegate recording to RecorderPlugin.
validate    Validate JSONL episode files against the action schema.
dump-tools  Print the MCP tools/list JSON to stdout.

Usage::

    python -m minecraft_recorder start --chamber plains_day
    python -m minecraft_recorder start --chamber hostile_night --seed 42

    python -m minecraft_recorder validate episodes/*.jsonl

    python -m minecraft_recorder dump-tools
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

# Allow running as `python -m minecraft_recorder` from repo root.
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from minecraft_recorder.episode_writer import validate_episode
from minecraft_recorder.screenshot_capture import ScreenshotSyncer, merge_visual, _visual_path_for
from minecraft_recorder.tool_definitions import tools_list_response

# ─── Constants ────────────────────────────────────────────────────────────────

RCON_HOST     = "127.0.0.1"
RCON_PORT     = 25575
RCON_PASSWORD = "minecraft_dev"


# ─── Subcommand: start ────────────────────────────────────────────────────────

def cmd_start(args: argparse.Namespace) -> int:
    """
    Load a chamber then delegate all action recording to the RecorderPlugin
    running inside the Paper server.  Python's role here is pure orchestration:

        1. Load the chamber world via RCON  (mobs / structures / weather).
        2. Send  ``/recorder start <chamber> <seed>``  via RCON  →  plugin opens
           the JSONL file and begins capturing all server-side events.
        3. (Optional) Start ScreenshotSyncer to tail the JSONL and grab a
           screenshot after each action record (--screenshots flag).
        4. Wait for Ctrl-C (or --duration seconds).
        5. Send  ``/recorder stop``  via RCON  →  plugin flushes & closes the
           file.  The path is printed by the plugin to the server log.
        6. (Optional) Auto-merge episode + visual sidecar into a single JSONL
           with ``image_b64`` field per record (unless --no-merge).

    No Mineflayer bridge is needed: the plugin has authoritative access to
    inventory, held item, combat, chest interactions, position, and craft
    events via Bukkit listeners.
    """
    chamber = args.chamber
    seed    = args.seed if args.seed is not None else 0
    dry_run = args.dry_run
    verbose = args.verbose

    # Import RconClient from minecraft_server.py.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "minecraft_server", str(_REPO_ROOT / "minecraft_server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    screenshots   = getattr(args, "screenshots", False)
    no_merge      = getattr(args, "no_merge", False)
    interval_ms   = getattr(args, "interval_ms", 500)
    window_search = getattr(args, "window", "Minecraft")
    rec_format    = getattr(args, "format", "semantic")

    if dry_run:
        if not args.skip_load:
            print(f"Loading chamber '{chamber}'… (dry-run)", file=sys.stderr)
            result = mod.load_chamber(
                chamber,
                rcon_fn=lambda _: "(dry-run)",
                seed_override=seed,
                dry_run=True,
                verbose=verbose,
            )
            if not result.success:
                print(f"Chamber load failed: {result.errors}", file=sys.stderr)
                return 1
        print(f"[dry-run] Would send: /recorder start {chamber} {seed} {rec_format}",
              file=sys.stderr)
        if screenshots:
            print(f"[dry-run] Would start ScreenshotSyncer  interval={interval_ms}ms  "
                  f"window={window_search!r}", file=sys.stderr)
        return 0

    # ── Open persistent RCON connection ──────────────────────────────────────
    episode_path: Path | None = None
    syncer: ScreenshotSyncer | None = None

    with mod.RconClient(host=RCON_HOST, port=RCON_PORT, password=RCON_PASSWORD) as rcon_client:
        try:
            # ── (Optional) load the chamber ──────────────────────────────────────
            if not args.skip_load:
                print(f"Loading chamber '{chamber}'…", file=sys.stderr)
                result = mod.load_chamber(
                    chamber,
                    rcon_fn=rcon_client.send,
                    seed_override=seed,
                    dry_run=False,
                    verbose=verbose,
                )
                if not result.success:
                    print(f"Chamber load failed: {result.errors}", file=sys.stderr)
                    return 1

            # ── Tell the plugin to start recording ───────────────────────────────
            plugin_resp = rcon_client.send(f"recorder start {chamber} {seed} {rec_format}")
            if plugin_resp:
                print(f"[plugin] {plugin_resp}", file=sys.stderr)
            print(f"Recording started  chamber={chamber}  seed={seed}  format={rec_format}",
                  file=sys.stderr)

            # ── Derive episode file path from plugin response ─────────────────────
            # Plugin sends: "Recorder started: {filename}.jsonl [format]"
            if plugin_resp and "Recorder started:" in plugin_resp:
                raw_name = plugin_resp.split("Recorder started:", 1)[1].strip()
                # Strip any trailing annotation like " [minerl]"
                filename = raw_name.split(".jsonl")[0] + ".jsonl"
                episode_path = (_REPO_ROOT / "episodes" / filename)

            # ── (Optional) start screenshot syncer ───────────────────────────────
            if screenshots:
                if episode_path is None:
                    print(
                        "[screenshot] Warning: could not determine episode path from "
                        "plugin response; screenshots disabled.",
                        file=sys.stderr,
                    )
                else:
                    syncer = ScreenshotSyncer(
                        _visual_path_for(episode_path),
                        interval_ms=interval_ms,
                        window_search=window_search,
                        verbose=verbose,
                    )
                    syncer.start()
                    print(
                        f"[screenshot] Syncer started  interval={interval_ms}ms  "
                        f"window={window_search!r}  → {syncer.visual_path.name}",
                        file=sys.stderr,
                    )

            print("Press Ctrl-C to stop recording.", file=sys.stderr)

            # ── Wait for Ctrl-C (or optional --duration) ──────────────────────
            stop_event = threading.Event()

            def _shutdown(signum, _frame):
                print("\nStopping…", file=sys.stderr)
                stop_event.set()

            signal.signal(signal.SIGINT,  _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)

            deadline = time.time() + args.duration if args.duration else None

            while not stop_event.is_set():
                if deadline and time.time() >= deadline:
                    break
                time.sleep(0.25)

        finally:
            # ── Stop screenshot syncer before telling plugin to stop ──────────
            if syncer is not None:
                syncer.stop()
                print("[screenshot] Syncer stopped.", file=sys.stderr)

            # ── Tell the plugin to stop & flush ──────────────────────────────
            try:
                stop_resp = rcon_client.send("recorder stop")
                if stop_resp:
                    print(f"[plugin] {stop_resp}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[recorder] Warning: could not send /recorder stop: {exc}",
                      file=sys.stderr)
            print("Episode saved (see server log for filename).", file=sys.stderr)

    # ── Auto-merge screenshots into episode JSONL (RCON no longer needed) ────
    if syncer is not None and episode_path is not None and not no_merge:
        try:
            merged = merge_visual(episode_path, overwrite=True)
            print(f"[screenshot] Merged → {merged.name}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[screenshot] Warning: merge failed: {exc}", file=sys.stderr)

    # ── Aggregate consecutive navigate records (optional) ─────────────────────
    if args.aggregate and episode_path is not None:
        from minecraft_recorder.episode_writer import aggregate_episode
        try:
            agg_path = aggregate_episode(episode_path)
            print(f"[aggregate] Aggregated → {agg_path.name}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[aggregate] Warning: aggregation failed: {exc}", file=sys.stderr)

    return 0


# ─── Subcommand: validate ─────────────────────────────────────────────────────

def _resolve_paths(patterns: list[str]) -> list[Path]:
    """Expand glob patterns to concrete paths, handling absolute paths."""
    result: list[Path] = []
    for pattern in patterns:
        p = Path(pattern)
        if p.is_absolute():
            result.append(p)
        else:
            expanded = list(Path(".").glob(pattern))
            result.extend(expanded if expanded else [p])
    return result


def cmd_validate(args: argparse.Namespace) -> int:
    rc = 0
    for path in _resolve_paths(args.files):
        errors = validate_episode(path)
        n = _count_lines(path)
        if errors:
            print(f"FAIL  {path}  ({len(errors)} errors):")
            for e in errors:
                print(f"  {e}")
            rc = 1
        else:
            print(f"OK    {path}  ({n} records)")
    return rc


# ─── Subcommand: merge-visual ─────────────────────────────────────────────────

def cmd_merge_visual(args: argparse.Namespace) -> int:
    """
    Merge a ``_visual.jsonl`` sidecar into its episode JSONL file, adding an
    ``"image_b64"`` field to each record.

    Example::

        python -m minecraft_recorder merge-visual episodes/desert_tomb_0_...jsonl
    """
    episode_path = Path(args.episode)
    visual_path  = Path(args.visual) if args.visual else None
    out_path     = Path(args.out)    if args.out    else None

    if not episode_path.exists():
        print(f"Error: episode file not found: {episode_path}", file=sys.stderr)
        return 1

    resolved_visual = visual_path or _visual_path_for(episode_path)
    if not resolved_visual.exists():
        print(f"Error: visual sidecar not found: {resolved_visual}", file=sys.stderr)
        return 1

    try:
        merged = merge_visual(
            episode_path,
            visual_path=visual_path,
            out_path=out_path,
            overwrite=args.overwrite,
        )
        print(f"Merged → {merged}")
    except FileExistsError as exc:
        print(f"Error: {exc}  (use --overwrite to replace)", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.open() if line.strip())
    except OSError:
        return 0


# ─── Subcommand: dump-tools ───────────────────────────────────────────────────

def cmd_dump_tools(_args: argparse.Namespace) -> int:
    print(json.dumps(tools_list_response(), indent=2, ensure_ascii=False))
    return 0


# ─── Subcommand: aggregate ────────────────────────────────────────────────────

def cmd_aggregate(args: argparse.Namespace) -> int:
    from minecraft_recorder.episode_writer import aggregate_episode
    out = aggregate_episode(args.episode)
    print(f"Aggregated episode written to: {out}")
    return 0


# ─── Argument parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m minecraft_recorder",
        description="Minecraft playthrough recorder → JSONL training data",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # start
    p_start = sub.add_parser("start", help="Start a recording session")
    p_start.add_argument("--chamber", required=True, help="Chamber name")
    p_start.add_argument("--seed",    type=int, default=None, help="Override RNG seed")
    p_start.add_argument("--dry-run", action="store_true",
                         help="Print sample records; don't connect to server")
    p_start.add_argument("--skip-load", action="store_true",
                         help="Don't call load-chamber; assume it's already loaded")
    p_start.add_argument("--screenshots", action="store_true",
                         help="Capture game-window screenshots on a fixed timer "
                              "and save alongside the episode")
    p_start.add_argument("--interval", dest="interval_ms", type=int, default=500,
                         metavar="MS",
                         help="Screenshot interval in milliseconds (default 500)")
    p_start.add_argument("--window", default="Minecraft", metavar="TITLE",
                         help="Window title/owner substring to capture on macOS "
                              "(default 'Minecraft'; pass '' for full-screen)")
    p_start.add_argument("--no-merge", action="store_true",
                         help="With --screenshots: keep sidecar _visual.jsonl "
                              "but skip auto-merging into _merged.jsonl")
    p_start.add_argument("--format", dest="format", default="semantic",
                         choices=["semantic", "minerl"],
                         help="Recording schema: 'semantic' (default, Holy-7 action primitives) "
                              "or 'minerl' (raw MineRL control-space per 500 ms tick)")
    p_start.add_argument("--duration", type=float, default=None, metavar="SECONDS",
                         help="Auto-stop after N seconds (default: wait for Ctrl-C)")
    p_start.add_argument("--aggregate", action="store_true",
                         help="Post-process episode to merge consecutive navigate records")
    p_start.add_argument("--verbose", action="store_true")

    # validate
    p_val = sub.add_parser("validate", help="Validate JSONL episode files")
    p_val.add_argument("files", nargs="+", help="JSONL file paths or glob patterns")

    # merge-visual
    p_merge = sub.add_parser(
        "merge-visual",
        help="Merge a _visual.jsonl sidecar into its episode file (adds image_b64)",
    )
    p_merge.add_argument("episode", help="Episode JSONL file")
    p_merge.add_argument("--visual",    default=None,
                         help="Visual sidecar JSONL (default: {episode_stem}_visual.jsonl)")
    p_merge.add_argument("--out",       default=None,
                         help="Output path (default: {episode_stem}_merged.jsonl)")
    p_merge.add_argument("--overwrite", action="store_true",
                         help="Overwrite existing output file")

    # dump-tools
    sub.add_parser("dump-tools", help="Print MCP tools/list JSON to stdout")

    # aggregate
    p_agg = sub.add_parser("aggregate",
                            help="Merge consecutive navigate records in a JSONL episode")
    p_agg.add_argument("episode", type=Path, help="Path to episode JSONL file")

    return parser


# ─── __main__ ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    dispatch = {
        "start":        cmd_start,
        "validate":     cmd_validate,
        "merge-visual": cmd_merge_visual,
        "dump-tools":   cmd_dump_tools,
        "aggregate":    cmd_aggregate,
    }
    return dispatch[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
