"""
EpisodeWriter — serialises ActionEvent objects to flat JSONL.

Each record is a single self-contained JSON object::

    {
      "action":   "gather",
      "args":     {"block_type": "gold_ore", "count": 3},
      "result":   "Mined 3x gold_ore.",
      "obs": {
        "pos":    [-3.4, -59.0, 7.1],
        "facing": "North",
        "health": 18.5,
        "hunger": 17,
        "held":   "iron_pickaxe",
        "inv":    {"cobblestone": 14, "iron_ore": 3, "torch": 8},
        "xp":     3
      },
      "ts_start": 1772438426.54,
      "ts_end":   1772438432.10,
      "chamber":  "desert_tomb",
      "seed":     0
    }

Reasoning injection (optional post-processing step) adds a "think" key::

    {"think": "I need gold for a better pickaxe...", "action": "gather", ...}

After writing, a sidecar ``.manifest.json`` is created with session metadata.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .action_classifier import ActionEvent
from .state_tracker import PlayerState


# ─── Observation snapshot ─────────────────────────────────────────────────────

def _yaw_to_facing(yaw: float) -> str:
    y = yaw % 360
    if y < 0:
        y += 360
    if y < 45 or y >= 315:
        return "South"
    if y < 135:
        return "West"
    if y < 225:
        return "North"
    return "East"


def _obs_dict(state: PlayerState | None, nearby_entities: list[str] | None = None) -> dict:
    """Convert a PlayerState into a compact observation dict."""
    if state is None:
        return {}
    obs: dict = {
        "pos":    [round(state.x, 2), round(state.y, 2), round(state.z, 2)],
        "facing": _yaw_to_facing(state.yaw),
        "health": round(state.health, 1),
        "hunger": state.hunger,
        "held":   state.held_item,
        "inv":    dict(sorted(state.inventory.items(), key=lambda kv: -kv[1])[:12]),
        "xp":     state.xp_level,
    }
    if nearby_entities:
        counts: dict[str, int] = defaultdict(int)
        for e in nearby_entities:
            counts[e] += 1
        obs["nearby"] = dict(counts)
    return obs


# ─── EpisodeWriter ────────────────────────────────────────────────────────────

class EpisodeWriter:
    """
    Writes one training episode to a flat JSONL file.

    Parameters
    ----------
    chamber:    Chamber name (used in filename and metadata).
    seed:       RNG seed used for the chamber.
    output_dir: Directory where episode files are written.
    """

    def __init__(self, chamber: str, seed: int, output_dir: Path = Path("episodes")) -> None:
        self.chamber = chamber
        self.seed    = seed
        self._start_ts = time.time()
        self._n_actions = 0
        self._type_counts: dict[str, int] = defaultdict(int)

        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"{chamber}_{seed}_{date_str}"
        self._jsonl_path   = output_dir / f"{stem}.jsonl"
        self._manifest_path = output_dir / f"{stem}.manifest.json"
        self._fh = self._jsonl_path.open("a", encoding="utf-8")

    # ── Write ─────────────────────────────────────────────────────────────────

    def write_action(
        self,
        event: ActionEvent,
        nearby_entities: list[str] | None = None,
    ) -> None:
        """Serialise one ActionEvent as a flat JSONL record."""
        record = {
            "action":   event.tool_name,
            "args":     event.tool_input,
            "result":   event.tool_result,
            "obs":      _obs_dict(event.before, nearby_entities),
            "ts_start": round(event.ts_start, 3),
            "ts_end":   round(event.ts_end,   3),
            "chamber":  self.chamber,
            "seed":     self.seed,
        }
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        self._n_actions += 1
        self._type_counts[event.tool_name] += 1

    # ── Finalise ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush the JSONL file and write the sidecar manifest."""
        if self._fh and not self._fh.closed:
            self._fh.close()

        manifest = {
            "chamber":            self.chamber,
            "seed":               self.seed,
            "n_actions":          self._n_actions,
            "action_type_counts": dict(self._type_counts),
            "duration_s":         round(time.time() - self._start_ts, 1),
            "jsonl_file":         self._jsonl_path.name,
            "created_utc":        datetime.now(tz=timezone.utc).isoformat(),
        }
        self._manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def __enter__(self) -> "EpisodeWriter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._jsonl_path


# ─── Helpers ─────────────────────────────────────────────────────────────────

def iter_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSONL records from an episode file."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def validate_episode(path: Path) -> list[str]:
    """
    Validate all records in a JSONL episode file against the tool schemas.

    Returns a list of error strings (empty = all OK).
    """
    from .tool_definitions import TOOLS_BY_NAME

    errors: list[str] = []
    for i, record in enumerate(iter_records(path)):
        name = record.get("action", "")
        if name not in TOOLS_BY_NAME:
            errors.append(f"Record {i}: unknown action '{name}'")
            continue
        args   = record.get("args", {})
        schema = TOOLS_BY_NAME[name].input_schema
        # Lightweight required-field check without jsonschema dep.
        for req in schema.get("required", []):
            if req not in args:
                errors.append(f"Record {i} action={name}: missing required arg '{req}'")
    return errors
