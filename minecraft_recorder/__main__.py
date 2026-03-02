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
import time
from pathlib import Path

# Allow running as `python -m minecraft_recorder` from repo root.
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from minecraft_recorder.episode_writer import validate_episode
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
        3. Wait for Ctrl-C (or --duration seconds).
        4. Send  ``/recorder stop``  via RCON  →  plugin flushes & closes the
           file.  The path is printed by the plugin to the server log.

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
        print("[dry-run] Would send: /recorder start", chamber, seed, file=sys.stderr)
        return 0

    # ── Open persistent RCON connection ──────────────────────────────────────
    rcon_client = mod.RconClient(
        host=RCON_HOST, port=RCON_PORT, password=RCON_PASSWORD
    )
    rcon_client.__enter__()

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
        plugin_resp = rcon_client.send(f"recorder start {chamber} {seed}")
        if plugin_resp:
            print(f"[plugin] {plugin_resp}", file=sys.stderr)
        print(f"Recording started  chamber={chamber}  seed={seed}", file=sys.stderr)
        print("Press Ctrl-C to stop recording.", file=sys.stderr)

        # ── Wait for Ctrl-C (or optional --duration) ─────────────────────────
        stop_event = threading.Event()

        def _shutdown(signum, _frame):
            print("\nStopping…", file=sys.stderr)
            stop_event.set()

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        duration = getattr(args, "duration", None)
        deadline = time.time() + duration if duration else None

        while not stop_event.is_set():
            if deadline and time.time() >= deadline:
                break
            time.sleep(0.25)

    finally:
        # ── Tell the plugin to stop & flush ──────────────────────────────────
        try:
            stop_resp = rcon_client.send("recorder stop")
            if stop_resp:
                print(f"[plugin] {stop_resp}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[recorder] Warning: could not send /recorder stop: {exc}",
                  file=sys.stderr)
        rcon_client.__exit__(None, None, None)
        print("Episode saved (see server log for filename).", file=sys.stderr)

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


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.open() if line.strip())
    except OSError:
        return 0


# ─── Subcommand: dump-tools ───────────────────────────────────────────────────

def cmd_dump_tools(_args: argparse.Namespace) -> int:
    print(json.dumps(tools_list_response(), indent=2, ensure_ascii=False))
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
    p_start.add_argument("--verbose", action="store_true")

    # validate
    p_val = sub.add_parser("validate", help="Validate JSONL episode files")
    p_val.add_argument("files", nargs="+", help="JSONL file paths or glob patterns")

    # dump-tools
    sub.add_parser("dump-tools", help="Print MCP tools/list JSON to stdout")

    return parser


# ─── __main__ ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    dispatch = {
        "start":      cmd_start,
        "validate":   cmd_validate,
        "dump-tools": cmd_dump_tools,
    }
    return dispatch[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
