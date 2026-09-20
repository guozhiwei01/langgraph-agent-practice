"""LangGraph assembly. Keep graph wiring separate from node implementation."""

from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from .config import get_settings
from .nodes import create_llm_node, create_tool_node, should_continue
from .state import ArithmeticAgentState
from .tools import ARITHMETIC_TOOLS


def build_graph(checkpointer: Any = None) -> Any:
    """Build the arithmetic workflow, optionally with a LangGraph checkpointer."""
    settings = get_settings()
    model = ChatOpenAI(
        model=settings.model_name,
        base_url=settings.base_url,
        temperature=0,
        api_key=settings.deepseek_api_key,
    )
    tools_by_name = {
        registered_tool.name: registered_tool for registered_tool in ARITHMETIC_TOOLS
    }
    model_with_tools = model.bind_tools(ARITHMETIC_TOOLS)

    builder = StateGraph(ArithmeticAgentState)
    builder.add_node("llm_call", create_llm_node(model_with_tools))
    builder.add_node("tool_node", create_tool_node(tools_by_name))
    builder.add_edge(START, "llm_call")
    builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    builder.add_edge("tool_node", "llm_call")
    return builder.compile(checkpointer=checkpointer)
