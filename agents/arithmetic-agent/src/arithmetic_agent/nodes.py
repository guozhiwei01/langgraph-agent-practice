"""Graph nodes and routing decisions for the arithmetic Agent."""

from collections.abc import Callable
from typing import Literal

from langchain.messages import SystemMessage, ToolMessage

from .state import ArithmeticAgentState


def create_llm_node(model_with_tools: object) -> Callable[[ArithmeticAgentState], dict]:
    """Create the node that asks the model to answer or request a tool."""

    def llm_call(state: ArithmeticAgentState) -> dict:
        response = model_with_tools.invoke(  # type: ignore[attr-defined]
            [
                SystemMessage(
                    content=(
                        "You are a helpful assistant tasked with performing "
                        "arithmetic on a set of inputs."
                    )
                )
            ]
            + state["messages"]
        )
        return {
            "messages": [response],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return llm_call


def create_tool_node(tools_by_name: dict[str, object]) -> Callable[[ArithmeticAgentState], dict]:
    """Create the node that executes model-requested tools."""

    def tool_node(state: ArithmeticAgentState) -> dict:
        results = []
        for tool_call in state["messages"][-1].tool_calls:
            selected_tool = tools_by_name[tool_call["name"]]
            observation = selected_tool.invoke(tool_call["args"])  # type: ignore[attr-defined]
            results.append(
                ToolMessage(content=str(observation), tool_call_id=tool_call["id"])
            )
        return {"messages": results}

    return tool_node


def should_continue(
    state: ArithmeticAgentState,
) -> Literal["tool_node", "__end__"]:
    """Route to tool execution only when the last model message contains calls."""
    if state["messages"][-1].tool_calls:
        return "tool_node"
    return "__end__"
