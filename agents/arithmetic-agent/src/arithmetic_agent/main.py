"""Local CLI entrypoint for the arithmetic Agent."""

from langchain.messages import HumanMessage

from .graph import build_graph


def main() -> None:
    agent = build_graph()
    result = agent.invoke({"messages": [HumanMessage(content="Add 3 and 4.")]})
    for message in result["messages"]:
        message.pretty_print()


if __name__ == "__main__":
    main()
