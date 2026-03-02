"""
Episode utilities — read and validate JSONL episode files produced by
RecorderPlugin (the Paper server-side plugin).

Each record in an episode file is a flat JSON object::

    {
      "action":   "gather",
      "args":     {"block_type": "gold_ore", "count": 1},
      "result":   "Mined 1x gold_ore.",
      "obs": {
        "pos":    [-3.4, -59.0, 7.1],
        "facing": "North",
        "health": 18.5,
        "hunger": 17,
        "held":   "iron_pickaxe",
        "inv":    {"cobblestone": 14, "iron_ore": 3},
        "xp":     3
      },
      "ts_start": 1772438426.54,
      "ts_end":   1772438432.10,
      "chamber":  "desert_tomb",
      "seed":     0
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


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
