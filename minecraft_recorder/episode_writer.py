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

    For MineRL-format files (records contain a ``"controls"`` key instead of
    ``"action"``), validates that each record has both ``"controls"`` and
    ``"obs"`` keys and returns early without checking semantic action names.

    Also checks for temporal gaps > 2 s between consecutive records.

    Returns a list of error strings (empty = all OK).
    """
    from .tool_definitions import TOOLS_BY_NAME

    errors: list[str] = []
    prev_end: float | None = None

    for i, record in enumerate(iter_records(path)):
        # Temporal gap check
        ts_start = record.get("ts_start") or record.get("ts_end")
        ts_end   = record.get("ts_end")
        if prev_end is not None and ts_start is not None and ts_start - prev_end > 2.0:
            errors.append(
                f"Record {i}: gap of {ts_start - prev_end:.1f}s since previous record"
            )
        prev_end = ts_end

        if "controls" in record:
            # MineRL format — validate controls + obs presence per record
            _validate_minerl_record(i, record, errors)
            continue

        # Semantic format
        name = record.get("action", "")
        if name not in TOOLS_BY_NAME:
            errors.append(f"Record {i}: unknown action '{name}'")
            continue
        args   = record.get("args", {})
        schema = TOOLS_BY_NAME[name].input_schema
        for req in schema.get("required", []):
            if req not in args:
                errors.append(f"Record {i} action={name}: missing required arg '{req}'")
    return errors


def aggregate_episode(path: Path) -> Path:
    """
    Merge consecutive ``navigate`` records into single path records.

    Two navigate records are merged when the ``to`` position of record N
    equals the ``from`` position of record N+1 (i.e. the tick sequence is a
    continuous walk with no intervening action).  The merged record spans
    ``ts_start`` of the first through ``ts_end`` of the last.

    Writes to ``{stem}_aggregated.jsonl`` and returns that path.
    """
    records = list(iter_records(path))
    out: list[dict] = []
    i = 0
    while i < len(records):
        rec = records[i]
        if rec.get("action") != "navigate":
            out.append(rec)
            i += 1
            continue
        # Accumulate a chain of consecutive navigate records
        chain = [rec]
        while (
            i + len(chain) < len(records)
            and records[i + len(chain)].get("action") == "navigate"
            and records[i + len(chain)]["args"].get("from") == chain[-1]["args"].get("to")
        ):
            chain.append(records[i + len(chain)])
        if len(chain) == 1:
            out.append(rec)
        else:
            merged = dict(rec)
            f = chain[0]["args"]["from"]
            t = chain[-1]["args"]["to"]
            merged["args"] = {"from": f, "to": t}
            merged["result"] = (
                f"Moved from [{f[0]:.0f},{f[1]:.0f},{f[2]:.0f}] "
                f"to [{t[0]:.0f},{t[1]:.0f},{t[2]:.0f}]."
            )
            merged["ts_end"] = chain[-1]["ts_end"]
            out.append(merged)
        i += len(chain)

    out_path = path.with_name(path.stem + "_aggregated.jsonl")
    with out_path.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    return out_path


def _validate_minerl_record(i: int, record: dict, errors: list[str]) -> None:
    """Lightweight per-record check for MineRL-format episodes."""
    if "controls" not in record:
        errors.append(f"Record {i}: MineRL record missing 'controls' field")
    if "obs" not in record:
        errors.append(f"Record {i}: MineRL record missing 'obs' field")
    if "ts_end" not in record:
        errors.append(f"Record {i}: MineRL record missing 'ts_end' field")
