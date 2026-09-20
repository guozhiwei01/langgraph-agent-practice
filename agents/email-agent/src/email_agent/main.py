"""Entrypoint demonstrating stream execution, pause, and resumption."""

from langgraph.types import Command
from email_agent.graph import app


def run_pipeline():
    # Thread ID ensures all state for this conversation is preserved together
    config = {"configurable": {"thread_id": "cust-session-2026"}}

    sample_email = {
        "email_content": "I was charged twice for my subscription! Please fix this immediately!",
        "sender_email": "customer@siliconvalley.com",
        "email_id": "email-9988",
    }

    print(">>> Stage 1: Running workflow until human review interrupt...")
    for event in app.stream(sample_email, config):
        print(f"Step executed: {list(event.keys())}")

    # Inspect the checkpoint
    snapshot = app.get_state(config)
    print(f"\nWorkflow safely paused at: {snapshot.next}")

    print("\n>>> Stage 2: Human supervisor approves and resumes execution...")
    resume_command = Command(
        resume={
            "approved": True,
            "edited_response": "We verified the duplicate charge and have credited your billing profile immediately.",
        }
    )

    for event in app.stream(resume_command, config):
        print(f"Post-resume step executed: {list(event.keys())}")


def main() -> None:
    """Run the Email Agent from the command-line entrypoint."""
    run_pipeline()


if __name__ == "__main__":
    main()
