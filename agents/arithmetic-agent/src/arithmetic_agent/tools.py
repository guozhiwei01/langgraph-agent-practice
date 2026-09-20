"""Tools the model may select for arithmetic requests."""

from langchain.tools import BaseTool, tool


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool
def divide(a: int, b: int) -> float:
    """Divide a by b."""
    if b == 0:
        raise ValueError("Cannot divide by zero.")
    return a / b


ARITHMETIC_TOOLS: list[BaseTool] = [add, multiply, divide]
