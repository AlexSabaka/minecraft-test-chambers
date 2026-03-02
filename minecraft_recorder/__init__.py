"""
minecraft_recorder — gameplay recording pipeline.

Public surface:
    from minecraft_recorder.tool_definitions import TOOLS, tools_list_response
    from minecraft_recorder.state_tracker import StateTracker, PlayerState
    from minecraft_recorder.action_classifier import ActionClassifier, ActionEvent
    from minecraft_recorder.episode_writer import EpisodeWriter
    from minecraft_recorder.reasoning_injector import inject_reasoning
"""
from __future__ import annotations

__version__ = "0.1.0"
