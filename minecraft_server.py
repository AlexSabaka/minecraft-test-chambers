#!/usr/bin/env python3
"""minecraft_server.py — Download, configure, start and stop a Paper 1.20.6 server.

Usage:
    venv/bin/python3 scripts/minecraft_server.py setup    # download JAR + write config
    venv/bin/python3 scripts/minecraft_server.py start    # start server in background
    venv/bin/python3 scripts/minecraft_server.py stop     # gracefully stop server
    venv/bin/python3 scripts/minecraft_server.py status   # check if running
    venv/bin/python3 scripts/minecraft_server.py reset-world  # delete world/ for clean start

The server process is tracked via a PID file at server/.server.pid.

Server directory: server/
RCON password: minecraft_dev (port 25575, localhost only)
Game port: 25565
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Chamber generator (lazy import so missing pyyaml only fails on chamber commands)
try:
    from minecraft_test_chambers.chamber_loader import load_chamber, list_chambers
    _CHAMBERS_AVAILABLE = True
except ImportError:
    _CHAMBERS_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
SERVER_DIR = ROOT / "server"
PID_FILE = SERVER_DIR / ".server.pid"
LOG_FILE = SERVER_DIR / "server.log"

MINECRAFT_VERSION = "1.20.6"
PAPER_API = "https://api.papermc.io/v2/projects/paper/versions/{version}/builds"
PAPER_DOWNLOAD = "https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{build}/downloads/{filename}"

RCON_HOST = "127.0.0.1"
RCON_PORT = 25575
RCON_PASSWORD = "minecraft_dev"
GAME_PORT = 25565

JAVA_FLAGS = [
    "-Xmx2G",
    "-Xms512M",
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
    "-Dusing.aikars.flags=https://mcflags.emc.gs",
    "-Daikars.new.flags=true",
]


# ═══════════════════════════════════════════════════════════════════════════════
# JAR management
# ═══════════════════════════════════════════════════════════════════════════════

def get_latest_paper_build(version: str = MINECRAFT_VERSION) -> tuple[int, str]:
    """Return (build_number, jar_filename) for the latest Paper build."""
    url = PAPER_API.format(version=version)
    print(f"  Querying Paper API: {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())

    builds = data.get("builds", [])
    if not builds:
        raise RuntimeError(f"No Paper builds found for Minecraft {version}")

    latest = builds[-1]
    build_num = latest["build"]
    filename = latest["downloads"]["application"]["name"]
    return build_num, filename


def find_existing_jar() -> Path | None:
    """Return path to any existing paper-1.20.6-*.jar in SERVER_DIR."""
    for p in SERVER_DIR.glob("paper-1.20.6-*.jar"):
        return p
    return None


def download_paper_jar(dest_dir: Path = SERVER_DIR) -> Path:
    """Download latest Paper 1.20.6 JAR if not already present. Returns JAR path."""
    existing = find_existing_jar()
    if existing:
        print(f"  Paper JAR already present: {existing.name}")
        return existing

    build_num, filename = get_latest_paper_build()
    url = PAPER_DOWNLOAD.format(
        version=MINECRAFT_VERSION, build=build_num, filename=filename
    )
    dest = dest_dir / filename
    print(f"  Downloading Paper {MINECRAFT_VERSION} build {build_num}…")
    print(f"  URL: {url}")

    def _progress(count, block_size, total_size):
        pct = count * block_size / total_size * 100
        mb = count * block_size / 1_048_576
        print(f"\r  {mb:.1f} MB  ({pct:.0f}%)", end="", flush=True)

    urllib.request.urlretrieve(url, dest, _progress)
    print(f"\n  Downloaded: {dest.name} ({dest.stat().st_size / 1_048_576:.1f} MB)")
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
# Server control
# ═══════════════════════════════════════════════════════════════════════════════

def _rcon_send(command: str, password: str = RCON_PASSWORD,
               host: str = RCON_HOST, port: int = RCON_PORT) -> str | None:
    """Send a single RCON command over a fresh connection. Returns response or None.

    Use this for one-shot commands (setup, start, stop, status).
    For bulk chamber loading use :class:`RconClient` to reuse a single connection.
    """
    import struct
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))

        def _packet(req_id: int, ptype: int, payload: str) -> bytes:
            body = payload.encode("utf-8") + b"\x00\x00"
            length = 4 + 4 + len(body)
            return struct.pack("<iii", length, req_id, ptype) + body

        # Auth
        sock.send(_packet(1, 3, password))
        sock.recv(4096)  # auth response

        # Command
        sock.send(_packet(2, 2, command))
        resp = sock.recv(4096)
        if len(resp) >= 12:
            payload_bytes = resp[12:-2]
            return payload_bytes.decode("utf-8", errors="replace")
        return ""
    except (OSError, struct.error):
        return None
    finally:
        sock.close()


class RconClient:
    """Persistent RCON connection for bulk command execution.

    Opens a single TCP connection and authenticates once; subsequent
    :meth:`send` calls reuse the same socket.  Use as a context manager::

        with RconClient() as rcon:
            rcon.send("time set 6000")
            rcon.send("weather clear")

    This eliminates the 3-way TCP handshake + auth round-trip that
    :func:`_rcon_send` pays *per command*, which matters when a chamber
    load issues 500-700 commands.
    """

    _REQ_AUTH    = 1
    _REQ_COMMAND = 2
    _TYPE_AUTH   = 3
    _TYPE_CMD    = 2

    def __init__(
        self,
        password: str = RCON_PASSWORD,
        host: str = RCON_HOST,
        port: int = RCON_PORT,
        timeout: float = 10.0,
    ) -> None:
        import struct as _struct
        self._struct = _struct
        self.password = password
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._req_id = 10  # start above the auth id to avoid collision

    def __enter__(self) -> "RconClient":
        import struct as _struct
        self._struct = _struct
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # Authenticate once
        self._sock.send(self._packet(self._REQ_AUTH, self._TYPE_AUTH, self.password))
        self._sock.recv(4096)  # discard auth response
        return self

    def __exit__(self, *_: object) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def send(self, command: str) -> str | None:
        """Send *command* over the persistent connection. Returns response or None."""
        if not self._sock:
            return None
        try:
            req_id = self._req_id
            self._req_id += 1
            self._sock.send(self._packet(req_id, self._TYPE_CMD, command))
            resp = self._sock.recv(4096)
            if len(resp) >= 12:
                return resp[12:-2].decode("utf-8", errors="replace")
            return ""
        except (OSError, self._struct.error):
            return None

    def _packet(self, req_id: int, ptype: int, payload: str) -> bytes:
        body = payload.encode("utf-8") + b"\x00\x00"
        length = 4 + 4 + len(body)
        return self._struct.pack("<iii", length, req_id, ptype) + body


def wait_for_server(timeout: int = 120, quiet: bool = False) -> bool:
    """Poll RCON until the server accepts connections. Returns True on success."""
    deadline = time.time() + timeout
    last_print = 0.0
    while time.time() < deadline:
        resp = _rcon_send("list")
        if resp is not None:
            return True
        now = time.time()
        if not quiet and now - last_print > 5:
            remaining = int(deadline - now)
            print(f"  Waiting for server… ({remaining}s remaining)", end="\r", flush=True)
            last_print = now
        time.sleep(1)
    print()
    return False


def is_server_running() -> bool:
    """Return True if the tracked server process is still alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        return False


def _require_jar() -> Path:
    jar = find_existing_jar()
    if not jar:
        sys.exit(
            "  ERROR: Paper JAR not found. Run:\n"
            "    venv/bin/python3 scripts/minecraft_server.py setup"
        )
    return jar


# ═══════════════════════════════════════════════════════════════════════════════
# CLI commands
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_setup() -> None:
    """Download Paper JAR and verify config files are in place."""
    print("=== minecraft_server.py setup ===")
    SERVER_DIR.mkdir(parents=True, exist_ok=True)

    # Download JAR
    jar = download_paper_jar(SERVER_DIR)

    # Verify config files exist (they're checked into the repo)
    for fname in ("server.properties", "eula.txt"):
        fpath = SERVER_DIR / fname
        if not fpath.exists():
            sys.exit(f"  ERROR: {fpath} is missing — check git checkout")
        print(f"  Config OK: {fname}")

    print(f"\n  Setup complete. JAR: {jar.name}")

def cmd_start() -> None:
    """Start the Paper server in the background."""
    if is_server_running():
        print("  Server already running.")
        return

    jar = _require_jar()
    log_fd = LOG_FILE.open("a")
    cmd = ["java"] + JAVA_FLAGS + ["-jar", str(jar.name), "--nogui"]

    print(f"  Starting Paper {MINECRAFT_VERSION} server…")
    print(f"  Log: {LOG_FILE}")
    proc = subprocess.Popen(
        cmd,
        cwd=SERVER_DIR,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"  PID: {proc.pid}")

    print("  Waiting for server to become ready (RCON)…")
    if wait_for_server(timeout=120):
        print(f"\n  ✓ Server is ready on port {GAME_PORT}")
    else:
        print(f"\n  ✗ Server did not become ready within 120s — check {LOG_FILE}")
        sys.exit(1)

    # Set eval-friendly gamerules (reset to defaults on every fresh world)
    _eval_gamerules = [
        ("doMobSpawning",   "false"),   # no random mob spawns
        ("keepInventory",   "true"),    # no item loss on death
        ("doDaylightCycle", "false"),   # freeze time (scenarios set it via RCON)
        ("doWeatherCycle",  "false"),   # freeze weather
        ("doFireTick",      "false"),   # no fire spread
        ("mobGriefing",     "false"),   # no creeper/enderman griefing
    ]
    print("  Setting eval gamerules…")
    for rule, value in _eval_gamerules:
        resp = _rcon_send(f"gamerule {rule} {value}")
        status = "✓" if resp is not None else "✗"
        print(f"    {status} gamerule {rule} {value}")
    # Constrain agent to 64-block worldborder (≈ 1 chunk radius)
    resp = _rcon_send("worldborder set 64")
    print(f"    {'✓' if resp is not None else '✗'} worldborder set 64")


def cmd_stop() -> None:
    """Gracefully stop the server via RCON, then kill if needed."""
    if not is_server_running():
        print("  Server not running.")
        return

    pid = int(PID_FILE.read_text().strip())
    print(f"  Sending RCON stop (PID {pid})…")
    _rcon_send("stop")

    # Wait up to 30s for process to exit
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(1)
    else:
        print("  Server did not stop gracefully — sending SIGTERM")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    PID_FILE.unlink(missing_ok=True)
    print("  Server stopped.")


def cmd_status() -> None:
    """Print server running status."""
    if is_server_running():
        pid = PID_FILE.read_text().strip()
        print(f"  ✓ Running (PID {pid})")
        resp = _rcon_send("list")
        if resp:
            print(f"  RCON response: {resp.strip()}")
    else:
        print("  ✗ Not running")


def cmd_list_chambers() -> None:
    """Print all available test chamber names."""
    _require_chambers()
    chambers = list_chambers()
    if not chambers:
        print("  No chambers found in test_chambers/")
        return
    print(f"  {len(chambers)} chamber(s) available:")
    for name in chambers:
        print(f"    • {name}")


def cmd_load_chamber(name: str, seed: int | None, dry_run: bool) -> None:
    """Generate procedural RCON commands from a YAML chamber config and apply them."""
    _require_chambers()
    if not dry_run and not is_server_running():
        sys.exit(
            "  ERROR: Server is not running. Start it first:\n"
            "    python minecraft_server.py start"
        )

    if dry_run:
        # No server needed — pass a no-op callable
        rcon_fn = lambda cmd: "(dry-run)"  # noqa: E731
        result = load_chamber(name, rcon_fn=rcon_fn, seed_override=seed,
                               dry_run=True, verbose=True)
    else:
        # Open one persistent TCP connection for the entire chamber load
        with RconClient() as client:
            result = load_chamber(name, rcon_fn=client.send, seed_override=seed,
                                   dry_run=False, verbose=True)

    if not result.success:
        sys.exit(f"  Chamber load finished with {len(result.errors)} error(s).")


def _require_chambers() -> None:
    if not _CHAMBERS_AVAILABLE:
        sys.exit(
            "  ERROR: minecraft_test_chambers package not importable.\n"
            "  Install dependencies:  pip install pyyaml"
        )


def cmd_reset_world() -> None:
    """Delete the world/ directories for a clean server start."""
    was_running = is_server_running()
    if was_running:
        print("  Stopping server first…")
        cmd_stop()

    for world_dir in ("world", "world_nether", "world_the_end"):
        path = SERVER_DIR / world_dir
        if path.exists():
            shutil.rmtree(path)
            print(f"  Deleted {world_dir}/")

    print("  World reset. Restart with:")
    print("    venv/bin/python3 scripts/minecraft_server.py start")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    p = argparse.ArgumentParser(
        description="Manage the Paper 1.20.6 Minecraft server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "command",
        choices=["setup", "start", "stop", "status", "reset-world",
                 "list-chambers", "load-chamber"],
        help="Command to run",
    )
    p.add_argument(
        "chamber",
        nargs="?",
        metavar="CHAMBER",
        help="Chamber name for load-chamber (e.g. forest_day)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="SEED",
        help="RNG seed override for load-chamber",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated RCON commands without sending them (load-chamber)",
    )
    args = p.parse_args()

    if args.command == "load-chamber":
        if not args.chamber:
            p.error("load-chamber requires a CHAMBER name argument")
        cmd_load_chamber(args.chamber, args.seed, args.dry_run)
        return

    dispatch = {
        "setup":         cmd_setup,
        "start":         cmd_start,
        "stop":          cmd_stop,
        "status":        cmd_status,
        "reset-world":   cmd_reset_world,
        "list-chambers": cmd_list_chambers,
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
