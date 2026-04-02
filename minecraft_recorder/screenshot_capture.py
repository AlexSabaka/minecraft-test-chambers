"""
Screenshot capture synced to episode action records.

Architecture
------------
``ScreenshotSyncer`` runs a fixed-interval background timer (default 500 ms)
while recording.  Every tick it grabs a PNG of the Minecraft game window and
appends one entry to a *visual sidecar* file::

    episodes/{episode_stem}_visual.jsonl

Each sidecar entry::

    {"ts": 1772442749.872, "image_b64": "<base64-encoded PNG>"}

After recording, ``merge_visual`` pairs each episode action record with the
sidecar entry whose ``ts`` is nearest to the action's ``ts_end``.

Window capture (macOS)
----------------------
The Minecraft game window is located via ``Quartz`` (pyobjc-framework-Quartz)
and captured with ``screencapture -l <windowID> -x`` so only the game view is
saved — no desktop, dock, or other windows bleed through.  Falls back to
full-screen ``mss`` / ``PIL.ImageGrab`` when the window cannot be found or on
non-macOS platforms.
"""
from __future__ import annotations

import base64
import bisect
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

# ─── macOS window lookup ──────────────────────────────────────────────────────

if sys.platform == "darwin":
    try:
        import Quartz as _Quartz    # pyobjc-framework-Quartz
        _QUARTZ_OK = True
    except ImportError:
        _QUARTZ_OK = False
else:
    _QUARTZ_OK = False


def _find_window_id(search: str = "Minecraft") -> int | None:
    """
    Return the CGWindowNumber of the first on-screen window whose owner name
    or title contains *search* (case-insensitive).

    Requires macOS + ``pyobjc-framework-Quartz``.  Returns ``None`` otherwise.
    """
    if not _QUARTZ_OK:
        return None
    needle  = search.lower()
    windows = _Quartz.CGWindowListCopyWindowInfo(
        _Quartz.kCGWindowListOptionOnScreenOnly
        | _Quartz.kCGWindowListExcludeDesktopElements,
        _Quartz.kCGNullWindowID,
    )
    for win in windows:
        owner = (win.get("kCGWindowOwnerName") or "").lower()
        title = (win.get("kCGWindowName")      or "").lower()
        if needle in owner or needle in title:
            wid = win.get("kCGWindowNumber")
            return int(wid) if wid else None
    return None


def _screencapture_window(window_id: int) -> bytes:
    """
    Capture a single macOS window by its CGWindowNumber using
    ``screencapture -l <id> -x``.  Returns raw PNG bytes.
    Raises ``RuntimeError`` on non-zero exit.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        result = subprocess.run(
            ["screencapture", "-l", str(window_id), "-x", "-t", "png", tmp],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"screencapture exited {result.returncode}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ─── Screenshot helpers ───────────────────────────────────────────────────────

def _grab_full_screen_png_bytes() -> bytes:
    """Return raw PNG bytes for the primary monitor (full-screen fallback)."""
    try:
        import mss        # type: ignore[import]
        import mss.tools  # type: ignore[import]
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            return mss.tools.to_png(shot.rgb, shot.size)
    except ImportError:
        pass

    try:
        from PIL import ImageGrab  # type: ignore[import]
        buf = io.BytesIO()
        ImageGrab.grab().save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        pass

    raise RuntimeError(
        "No screenshot backend found.  "
        "Install one with:  pip install mss   or   pip install Pillow"
    )


def _grab_png_bytes(window_search: str = "Minecraft") -> bytes:
    """
    Grab a PNG of the game window if discoverable, otherwise fall back to the
    primary monitor.

    Parameters
    ----------
    window_search:
        Substring to search for in window owner / title (case-insensitive).
        Pass ``""`` to always use full-screen capture.
    """
    if window_search and sys.platform == "darwin":
        win_id = _find_window_id(window_search)
        if win_id is not None:
            try:
                return _screencapture_window(win_id)
            except Exception:   # noqa: BLE001
                pass            # fall through to full-screen
    return _grab_full_screen_png_bytes()


def grab_b64_png(window_search: str = "Minecraft") -> str:
    """Capture the game window and return a base64-encoded PNG string."""
    return base64.b64encode(_grab_png_bytes(window_search)).decode()


# ─── EpisodeTailer ────────────────────────────────────────────────────────────

class EpisodeTailer:
    """
    Background thread that tails a live JSONL episode file and fires a
    callback whenever a new complete line is appended.

    Parameters
    ----------
    episode_path:
        Path to the JSONL file being written by the Java plugin.  The file is
        polled every ``poll_interval`` seconds.
    on_record:
        Called with ``(seq: int, record: dict)`` on the main thread of the
        tailing loop whenever a new record is detected.
    poll_interval:
        Seconds between file-size polls (default 0.1 s).
    """

    def __init__(
        self,
        episode_path: Path,
        on_record: Callable[[int, dict], None],
        *,
        poll_interval: float = 0.1,
    ) -> None:
        self._path          = episode_path
        self._on_record     = on_record
        self._poll_interval = poll_interval
        self._stop_event    = threading.Event()
        self._thread        = threading.Thread(target=self._run, daemon=True,
                                               name="episode-tailer")
        self._seq           = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Wait for the file to appear (plugin may not have created it yet).
        deadline = time.monotonic() + 30.0
        while not self._path.exists():
            if self._stop_event.is_set() or time.monotonic() > deadline:
                return
            time.sleep(self._poll_interval)

        with self._path.open("r", encoding="utf-8") as fh:
            # Seek to end so we only see NEW records added after tailer starts.
            fh.seek(0, 2)  # SEEK_END
            leftover = ""

            while not self._stop_event.is_set():
                chunk = fh.read(65536)
                if chunk:
                    leftover += chunk
                    while "\n" in leftover:
                        line, leftover = leftover.split("\n", 1)
                        line = line.strip()
                        if line:
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            self._on_record(self._seq, record)
                            self._seq += 1
                else:
                    time.sleep(self._poll_interval)


# ─── ScreenshotSyncer ─────────────────────────────────────────────────────────

class ScreenshotSyncer:
    """
    Fixed-interval screenshot recorder.

    Captures a PNG of the Minecraft game window every ``interval_ms``
    milliseconds (default 500 ms), completely independent of server-side
    action events.  Screenshots are written to a *visual sidecar* file whose
    path is supplied at construction time.

    Each sidecar entry::

        {"ts": 1772442749.872, "image_b64": "<base64 PNG>"}

    ``merge_visual`` later pairs each action record to the nearest sidecar
    entry by timestamp (binary search).

    The timer corrects for capture latency by advancing ``next_tick`` by
    ``interval`` from the *scheduled* time rather than from ``now()``, so
    slow frames don't cause long-term drift.

    Parameters
    ----------
    visual_path:
        Where to write the visual sidecar JSONL.
    interval_ms:
        Screenshot cadence in milliseconds (default 500).
    window_search:
        Substring used to locate the game window (case-insensitive match
        against CGWindow owner / title on macOS).  Pass ``""`` to always
        capture the full screen.
    verbose:
        Print a status line to stderr for each captured frame.
    """

    def __init__(
        self,
        visual_path: Path,
        *,
        interval_ms: int   = 500,
        window_search: str = "Minecraft",
        verbose: bool      = False,
    ) -> None:
        self._visual_path   = visual_path
        self._interval      = interval_ms / 1000.0
        self._window_search = window_search
        self._verbose       = verbose
        self._stop_event    = threading.Event()
        self._thread        = threading.Thread(target=self._run, daemon=True,
                                               name="screenshot-syncer")
        self._lock          = threading.Lock()
        self._visual_fh     = None
        self._count         = 0
        # Cache window ID; re-check every ~10 s when still None
        self._window_id: int | None = None
        self._win_checked           = False

    @property
    def visual_path(self) -> Path:
        return self._visual_path

    @property
    def count(self) -> int:
        """Number of screenshots successfully written so far."""
        return self._count

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._visual_path.parent.mkdir(parents=True, exist_ok=True)
        self._visual_fh = self._visual_path.open("w", encoding="utf-8")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        with self._lock:
            if self._visual_fh:
                self._visual_fh.flush()
                self._visual_fh.close()
                self._visual_fh = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _get_window_id(self) -> int | None:
        """Return cached window ID, re-querying every ~10 s while still None."""
        if not self._win_checked or (self._window_id is None and self._count % 20 == 0):
            self._window_id   = (
                _find_window_id(self._window_search) if self._window_search else None
            )
            self._win_checked = True
        return self._window_id

    def _capture(self) -> str:
        """Grab one frame and return base64 PNG.  Never raises."""
        try:
            win_id = self._get_window_id()
            png    = (_screencapture_window(win_id) if win_id is not None
                      else _grab_full_screen_png_bytes())
            return base64.b64encode(png).decode()
        except Exception as exc:    # noqa: BLE001
            print(f"[screenshot] Capture failed: {exc}", file=sys.stderr)
            return ""

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_tick:
                # Sleep in 50 ms slices so stop_event is checked frequently
                time.sleep(min(next_tick - now, 0.05))
                continue

            ts        = time.time()
            image_b64 = self._capture()
            entry     = {"ts": round(ts, 3), "image_b64": image_b64}

            with self._lock:
                if self._visual_fh:
                    self._visual_fh.write(json.dumps(entry, ensure_ascii=False))
                    self._visual_fh.write("\n")
                    self._visual_fh.flush()

            self._count += 1
            if self._verbose:
                print(
                    f"[screenshot] #{self._count}  ts={ts:.3f}  "
                    f"win_id={self._window_id}  size={len(image_b64)} chars",
                    file=sys.stderr,
                )

            # Advance from scheduled tick (not from now) to avoid drift
            next_tick += self._interval


# ─── merge_visual ─────────────────────────────────────────────────────────────

def merge_visual(
    episode_path: Path,
    visual_path: Path | None = None,
    *,
    out_path: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """
    Merge a visual sidecar into the episode JSONL to produce one record per
    screenshot frame (uniform cadence), each annotated with the most recently
    completed action.

    Matching strategy
    -----------------
    **Timestamp-based** (current format): sidecar entries have a ``"ts"``
    field produced by the fixed-interval ``ScreenshotSyncer``.  The merged
    output has **one entry per screenshot** (uniform cadence, e.g. 500 ms),
    each enriched with fields from the latest action record whose ``ts_end``
    is ≤ the screenshot timestamp.  This preserves the fixed interval in the
    merged output instead of collapsing to the (irregular) action cadence.

    **Sequence-based** (legacy format): sidecar entries have a ``"seq"``
    field (produced by the old event-driven syncer).  Matched by line index
    for backward compatibility — one record per action.

    Parameters
    ----------
    episode_path:
        Source episode JSONL (written by the Java plugin).
    visual_path:
        Visual sidecar JSONL.  Defaults to ``{episode_stem}_visual.jsonl``
        next to *episode_path*.
    out_path:
        Destination for the merged file.  Defaults to
        ``{episode_stem}_merged.jsonl`` next to *episode_path*.
    overwrite:
        If *True*, silently overwrite *out_path*; otherwise raise
        ``FileExistsError``.

    Returns
    -------
    Path
        Path to the written merged file.
    """
    if visual_path is None:
        visual_path = _visual_path_for(episode_path)

    if out_path is None:
        out_path = episode_path.with_name(episode_path.stem + "_merged.jsonl")

    if out_path.exists() and not overwrite:
        raise FileExistsError(f"Merged file already exists: {out_path}")

    # ── load sidecar ──────────────────────────────────────────────────────────
    vis_entries: list[dict] = []
    if visual_path.exists():
        with visual_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        vis_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    # Auto-detect format from the first entry
    use_seq = bool(vis_entries and "seq" in vis_entries[0])

    if use_seq:
        # ── Legacy path: one merged record per action, matched by seq index ──
        by_seq: dict[int, str] = {
            e["seq"]: e.get("image_b64", "") for e in vis_entries if "seq" in e
        }
        with episode_path.open(encoding="utf-8") as src, \
             out_path.open("w", encoding="utf-8") as dst:
            for seq, raw in enumerate(src):
                raw = raw.strip()
                if not raw:
                    continue
                record = json.loads(raw)
                b64 = by_seq.get(seq, "")
                if b64:
                    record["image_b64"] = b64
                dst.write(json.dumps(record, ensure_ascii=False))
                dst.write("\n")
        return out_path

    # ── Current path: one merged record per screenshot (uniform cadence) ──────
    # Load and sort action records by ts_end
    act_records: list[dict] = []
    with episode_path.open(encoding="utf-8") as src:
        for raw in src:
            raw = raw.strip()
            if raw:
                try:
                    act_records.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
    act_records.sort(key=lambda r: r.get("ts_end", 0.0))
    act_ts_ends = [r.get("ts_end", 0.0) for r in act_records]

    # Sort sidecar entries by ts
    vis_entries.sort(key=lambda e: e.get("ts", 0.0))

    with out_path.open("w", encoding="utf-8") as dst:
        for vis in vis_entries:
            ts    = vis.get("ts", 0.0)
            b64   = vis.get("image_b64", "")

            # Most recent action completed before (or at) this screenshot
            idx = bisect.bisect_right(act_ts_ends, ts) - 1

            if idx >= 0:
                # Copy action fields into the merged record, then overwrite
                # ts and image_b64 with the screenshot values
                record = dict(act_records[idx])
            else:
                record = {}

            record["ts"]        = ts
            record["image_b64"] = b64
            dst.write(json.dumps(record, ensure_ascii=False))
            dst.write("\n")

    return out_path


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _visual_path_for(episode_path: Path) -> Path:
    """Return the default visual sidecar path for an episode file."""
    return episode_path.with_name(episode_path.stem + "_visual.jsonl")
