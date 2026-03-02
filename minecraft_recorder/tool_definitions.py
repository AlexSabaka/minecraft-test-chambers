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
                "from": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Starting position [x, y, z].",
                },
                "to": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Destination position [x, y, z].",
                },
            },
            "required": ["from", "to"],
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
            "Open or inspect a container or interactable block. "
            "Recorded when the player opens a chest, barrel, furnace, etc. "
            "A 'transfer' record follows if items were moved."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Container type opened, e.g. 'chest', 'ender_chest', "
                        "'furnace', 'hopper', 'dispenser'."
                    ),
                },
            },
            "required": ["target"],
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
                    "description": (
                        "Combat posture. Core values: 'melee', 'ranged', 'flee'. "
                        "Plugin may append '+shield' when a shield block was used, "
                        "e.g. 'melee+shield'."
                    ),
                },
            },
            "required": ["target_entity", "strategy"],
        },
    ),

    ToolDef(
        name="transfer",
        title="Transfer Item",
        description=(
            "Move items between the player's inventory and a container. "
            "Recorded when the player closes a chest/furnace/etc after moving items. "
            "direction: 'take' = player took items from container; "
            "'put' = player put items into container; "
            "'both' = items moved in both directions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["take", "put", "both"],
                    "description": "Direction items were moved.",
                },
                "container": {
                    "type": "string",
                    "description": "Container type, e.g. 'chest', 'furnace', 'hopper'.",
                },
                "items": {
                    "type": "object",
                    "description": "Map of item id to quantity transferred.",
                    "additionalProperties": {"type": "integer"},
                },
            },
            "required": ["direction", "container", "items"],
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
