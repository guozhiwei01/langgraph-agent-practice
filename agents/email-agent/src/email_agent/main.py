"""Default entrypoint for the persistent local review application."""


def main() -> None:
    """Start the same Postgres-backed application as email-agent-review."""
    import uvicorn

    uvicorn.run("email_agent.web:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()
