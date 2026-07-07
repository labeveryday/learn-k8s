"""Demo: Strands agent using the self-hosted sandbox.

Prereqs:
    cd server && docker compose build && docker compose up -d manager
    export SANDBOX_MANAGER_URL=http://localhost:8700
    export SANDBOX_MANAGER_TOKEN=change-me
    Model creds for both your agent and the worker (AWS/Bedrock by default).
"""

from strands import Agent

from strands_pack.sandbox import sandbox

SYSTEM = (
    "You have a sandbox tool: an isolated code interpreter. "
    "Start one session with sandbox(action='start'), reuse its session_id for "
    "every execute/shell/file call, and stop it when the task is done. "
    "For big self-contained tasks, start a session with network=True and use "
    "action='delegate' with wait_seconds to hand the whole job to the worker "
    "agent inside the sandbox."
)

agent = Agent(tools=[sandbox], system_prompt=SYSTEM)


def code_interpreter_demo():
    agent(
        "Start a sandbox. Using python, load the classic iris dataset from "
        "sklearn if available or generate synthetic data, compute summary "
        "stats, save them to stats.csv, show me the output, then read "
        "stats.csv back and stop the sandbox."
    )


def delegated_research_demo():
    agent(
        "Start a sandbox with network access, then delegate this task and "
        "wait up to 10 minutes for it: 'Research the current state of "
        "Python agent frameworks, compare the top three, and write your "
        "findings to report.md.' When it finishes, read report.md and give "
        "me the highlights, then stop the sandbox."
    )


if __name__ == "__main__":
    code_interpreter_demo()
    # delegated_research_demo()
