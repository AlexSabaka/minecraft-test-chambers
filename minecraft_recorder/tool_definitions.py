"""
Tool definitions for the 7 Minecraft action primitives.

Each ToolDef is expressed in two compatible formats:
 - ``mcp``  → MCP tools/list JSON (inputSchema camelCase)
 - ``hf``   → HuggingFace / OpenAI function-calling dict (parameters snake/camelCase, same schema)

Usage::

    from minecraft_recorder.tool_definitions import TOOLS, tools_list_response, hf_tools

    # Dump MCP tools/list response
    import json; print(json.dumps(tools_list_response(), indent=2))

    # Use in an Anthropic Messages API call
    tools_param = [t.anthropic() for t in TOOLS]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDef:
    """Single action-primitive tool definition."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]   # JSON Schema object

    # ── Format converters ─────────────────────────────────────────────────────

    def mcp(self) -> dict[str, Any]:
        """MCP tools/list tool object (inputSchema camelCase)."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    def anthropic(self) -> dict[str, Any]:
        """Anthropic Messages API tools array entry (input_schema snake_case)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def hf(self) -> dict[str, Any]:
        """HuggingFace / OpenAI function definition for tool_calls JSONL."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


# ─── 7 action-primitive tool definitions ──────────────────────────────────────

TOOLS: list[ToolDef] = [
    ToolDef(
        name="navigate",
        title="Navigate",
        description=(
            "Move the player to a target location, entity, or direction. "
            "Use for any intentional positioning: walking to a resource node, "
            "retreating from danger, or exploring. "
            "Prefer 'sprint' speed when covering > 10 blocks. "
            "Prefer 'walk' when approaching a fragile structure or a mob."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "oneOf": [
                        {
                            "type": "object",
                            "title": "Coordinate",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "z": {"type": "number"},
                            },
                            "required": ["x", "z"],
                        },
                        {
                            "type": "string",
                            "title": "Entity or direction",
                            "description": (
                                "Either an entity type ('zombie', 'chest'), "
                                "a cardinal direction ('north', 'south', 'east', 'west', 'up', 'down'), "
                                "or a special keyword ('spawn', 'away_from_danger')."
                            ),
                        },
                    ],
                    "description": "Destination — coordinate object, entity type, or direction keyword.",
                },
                "speed": {
                    "type": "string",
                    "enum": ["walk", "sprint", "sneak"],
                    "description": "Movement speed tier. Defaults to 'walk'.",
                    "default": "walk",
                },
            },
            "required": ["target"],
        },
    ),

    ToolDef(
        name="gather",
        title="Gather",
        description=(
            "Mine and collect a block type. Handles the full loop: "
            "equip correct tool, break the block, wait for item to land, "
            "pick up the drop. "
            "Use craft if you need to process the raw material further."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "block_type": {
                    "type": "string",
                    "description": (
                        "Minecraft block id without namespace prefix if unambiguous, "
                        "e.g. 'oak_log', 'stone', 'iron_ore', 'grass_block'."
                    ),
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of blocks to mine. Defaults to 1.",
                    "default": 1,
                },
            },
            "required": ["block_type"],
        },
    ),

    ToolDef(
        name="craft",
        title="Craft",
        description=(
            "Craft an item, using a crafting table if the recipe requires one. "
            "Assumes required ingredients are already in inventory. "
            "Do not call this if materials are missing — gather first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": (
                        "Output item id, e.g. 'wooden_pickaxe', 'chest', 'torch'."
                    ),
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number to craft. Defaults to 1.",
                    "default": 1,
                },
            },
            "required": ["item"],
        },
    ),

    ToolDef(
        name="interact",
        title="Interact",
        description=(
            "Perform a targeted interaction with a block or entity: "
            "eat food, place a block, equip gear, open a container, or activate a device. "
            "Choose mode carefully — 'place' is for block placement, "
            "'equip' is for armour/tools, 'consume' is for food/potions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Block id, entity type, or item id, e.g. "
                        "'chest', 'villager', 'bread', 'iron_door'."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["consume", "place", "equip", "open", "activate", "use"],
                    "description": (
                        "consume — eat/drink; "
                        "place — place held block; "
                        "equip — put on armour or switch held tool; "
                        "open — open inventory/container; "
                        "activate — right-click lever/button/door; "
                        "use — generic right-click."
                    ),
                },
            },
            "required": ["target", "mode"],
        },
    ),

    ToolDef(
        name="combat",
        title="Combat",
        description=(
            "Engage or evade a hostile entity. "
            "Strategy determines posture: "
            "'melee' = close and attack; "
            "'ranged' = maintain distance, use bow/crossbow; "
            "'flee' = disengage and create distance without attacking. "
            "After calling this tool, follow up with navigate if flee was chosen."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_entity": {
                    "type": "string",
                    "description": (
                        "Entity type to engage, e.g. 'zombie', 'skeleton', 'creeper'."
                    ),
                },
                "strategy": {
                    "type": "string",
                    "enum": ["melee", "ranged", "flee"],
                    "description": "Combat posture.",
                },
            },
            "required": ["target_entity", "strategy"],
        },
    ),

    ToolDef(
        name="transfer",
        title="Transfer Item",
        description=(
            "Move an item from the player's inventory to a container or device slot. "
            "Destinations: 'chest', 'furnace_input', 'furnace_fuel', 'drop' (throw on ground). "
            "Use 'drop' when discarding unwanted items. "
            "Must be standing adjacent to the target container (within 4 blocks)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": "Item id to transfer, e.g. 'cobblestone', 'raw_iron'.",
                },
                "destination": {
                    "type": "string",
                    "enum": ["chest", "furnace_input", "furnace_fuel", "drop"],
                    "description": "Where to send the item.",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of items to transfer. Defaults to all of that type.",
                },
            },
            "required": ["item", "destination"],
        },
    ),

    ToolDef(
        name="say",
        title="Say",
        description=(
            "Send a visible chat message. "
            "Use to narrate intent, report observations, or ask for help. "
            "Keep messages short (< 256 chars). "
            "Do not use for in-game commands — those are handled by other tools."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "maxLength": 256,
                    "description": "Chat message text.",
                },
            },
            "required": ["message"],
        },
    ),
]

# Convenience lookups.
TOOLS_BY_NAME: dict[str, ToolDef] = {t.name: t for t in TOOLS}


def tools_list_response() -> dict[str, Any]:
    """Return a complete MCP tools/list JSON-RPC 2.0 result body."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [t.mcp() for t in TOOLS],
        },
    }


def hf_tools() -> list[dict[str, Any]]:
    """Return the tools list in HuggingFace / OpenAI function-calling format."""
    return [t.hf() for t in TOOLS]
