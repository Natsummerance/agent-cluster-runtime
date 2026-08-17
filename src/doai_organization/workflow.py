"""Ephemeral LangGraph driver; durable state remains in SessionEvent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .catalog import MEETINGS, MeetingDefinition


class MeetingGraphState(TypedDict):
    index: int


async def run_meeting_workflow(
    callback: Callable[[MeetingDefinition], Awaitable[None]],
) -> None:
    """Drive all seven meetings through LangGraph without treating its state as durable."""

    async def run_current(state: MeetingGraphState) -> MeetingGraphState:
        await callback(MEETINGS[state["index"]])
        return {"index": state["index"] + 1}

    def route(state: MeetingGraphState) -> Literal["continue", "done"]:
        return "done" if state["index"] >= len(MEETINGS) else "continue"

    graph = StateGraph(MeetingGraphState)
    graph.add_node("meeting", run_current)
    graph.add_edge(START, "meeting")
    graph.add_conditional_edges("meeting", route, {"continue": "meeting", "done": END})
    await graph.compile().ainvoke({"index": 0})
