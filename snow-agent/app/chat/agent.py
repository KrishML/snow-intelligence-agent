"""LangGraph conversational agent with a fixed, booking-free tool registry."""

from __future__ import annotations

import uuid

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.chat.store import SessionStore
from app.chat.tools import create_chat_tools
from app.prefs.service import Preferences, default_preferences
from app.settings import settings

_REQUIRED_TOOL_NAMES = {
    "filter_results",
    "fetch_live_snow",
    "fetch_webcam",
    "search_flights_readonly",
    "search_hotels_readonly",
    "get_historical_reliability",
    "create_plan_artifact",
}


class AgentState(MessagesState):
    session_id: str
    user_id: str
    memory_context: str
    live_calls_remaining: int
    plan_artifact_created: bool


def build_chat_graph(
    session_id: str,
    user_id: uuid.UUID,
    memory_context: str,
    *,
    model=None,
    prefs: Preferences | None = None,
    store: SessionStore | None = None,
):
    """Compile a per-session StateGraph, rejecting any tool registry drift."""
    prefs = prefs or default_preferences(user_id)
    state: dict = {"live_calls": 0, "plan_created": False}
    tools = create_chat_tools(session_id, user_id, state, prefs, store)
    actual_names = {item.name for item in tools}
    if actual_names != _REQUIRED_TOOL_NAMES or len(tools) != 7:
        raise RuntimeError(f"Unexpected chat tool registry: {sorted(actual_names)}")

    if model is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured for the chat agent")
        model = ChatOpenAI(
            model=settings.openai_model_chat,
            api_key=settings.openai_api_key,
            streaming=True,
        )
    model_with_tools = model.bind_tools(tools)
    system_prompt = _system_prompt(memory_context, prefs)

    async def agent_node(agent_state: AgentState) -> dict:
        messages = [SystemMessage(content=system_prompt), *agent_state["messages"]]
        response = None
        async for chunk in model_with_tools.astream(messages):
            response = chunk if response is None else response + chunk
        return {"messages": [response]} if response is not None else {}

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "agent")
    return workflow.compile(checkpointer=MemorySaver())


def _system_prompt(memory_context: str, prefs: Preferences) -> str:
    budget = (
        f"GBP {prefs.budget_total_gbp}" if prefs.budget_total_gbp is not None else "not set"
    )
    prompt = f"""You are the Snow Intelligence Agent, a winter-trip information assistant.
Use the current operational mode ({prefs.mode}) and prefer the latest
weekly cache. The traveller lives in {prefs.residence_city}; flights depart from
{', '.join(prefs.preferred_airports)} (default {prefs.origin_airport}). Trip budget: {budget}.
Use live snow or flight tools only when relevant and clearly label
live results. Never invent conditions, availability, or prices. Only present
flight and hotel prices returned by a live search tool result in this session.

You are strictly read-only with respect to bookings. You cannot book, modify,
cancel, or check a booking. A draft plan is not a booking; never imply that a
booking has been made, confirmed, or is in progress. Never ask for payment data.
"""
    if memory_context:
        prompt += f"\n\n{memory_context}"
    return prompt
