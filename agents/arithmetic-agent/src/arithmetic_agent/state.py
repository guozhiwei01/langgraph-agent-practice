"""Internal LangGraph state; this is not a public API contract."""

import operator
from typing import Annotated, TypedDict

from langchain.messages import AnyMessage


class ArithmeticAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
