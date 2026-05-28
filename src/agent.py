"""
Strands Agent - Boilerplate

A production-ready agent template integrating all framework features:
- Multi-model support (Anthropic, Bedrock, OpenAI, Ollama, Writer)
- Hub integration (sessions, metrics, prompts, registry)
- Custom hooks for logging and monitoring
- Auto-loaded tools from src/tools/
- Conversation management with sliding window

Customize the configuration section below and start building.
"""

from dotenv import load_dotenv

# Load environment variables FIRST (before hub imports)
load_dotenv()

from strands import Agent  # noqa: E402
from strands.agent.conversation_manager import SlidingWindowConversationManager  # noqa: E402
from strands_tools import shell, editor, current_time  # noqa: E402
from pprint import pprint  # noqa: E402

from models import anthropic_model  # noqa: E402
from hooks import LoggingHook  # noqa: E402
from hub import (  # noqa: E402
    create_session_manager,
    MetricsExporter,
    S3PromptManager,
    AgentRegistry,
)
from hub.session import generate_run_id  # noqa: E402
from config import DEMO_AGENT_PROMPT  # noqa: E402


# =============================================================================
# CONFIGURATION - Customize these for your agent
# =============================================================================

AGENT_ID = "my-agent"  # Unique identifier for this agent
AGENT_NAME = "My Agent"  # Display name
PROMPT_VERSION = "v1"  # Increment when you update the system prompt

# System prompt - define in src/config/prompts.py or inline here
SYSTEM_PROMPT = DEMO_AGENT_PROMPT

# Model selection - see src/models/models.py for all options
# Examples:
#   anthropic_model(model_id="claude-sonnet-4-5-20250929")
#   bedrock_model(model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
#   openai_model(model_id="gpt-4o")
#   ollama_model(model_id="llama3.1:latest")
MODEL = anthropic_model(model_id="claude-sonnet-4-5-20250929")


# =============================================================================
# HUB INTEGRATION - Sessions, metrics, prompts, and registry
# =============================================================================

# Generate unique run ID for this session
run_id = generate_run_id(AGENT_ID)

# Register agent in hub (safe to call multiple times)
registry = AgentRegistry()
registry.register(
    agent_id=AGENT_ID,
    description="My custom agent",  # Update this description
    tags=["custom"],  # Add relevant tags
    repo_url="https://github.com/your-org/your-repo",  # Link to source code
    owner="your-name",  # Agent maintainer
    environment="dev",  # dev, staging, or prod
    model_id="claude-sonnet-4-5-20250929",  # Model being used
)

# Setup versioned system prompt (syncs to S3 if enabled)
prompt_manager = S3PromptManager(agent_id=AGENT_ID)
prompt_manager.ensure_exists(content=SYSTEM_PROMPT, version=PROMPT_VERSION)
system_prompt = prompt_manager.get_current()

# Session manager (S3 or local based on USE_S3 env var)
session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

# Conversation manager with sliding window
conversation_manager = SlidingWindowConversationManager(
    window_size=20,  # Number of messages to keep in context
    should_truncate_results=True,
)

# Metrics exporter for tracking run performance
metrics = MetricsExporter(
    agent_id=AGENT_ID,
    run_id=run_id,
    prompt_version=PROMPT_VERSION,
)


# =============================================================================
# AGENT SETUP
# =============================================================================

agent = Agent(
    model=MODEL,
    system_prompt=system_prompt,
    tools=[shell, current_time, editor],  # Add your tools here
    session_manager=session_manager,
    conversation_manager=conversation_manager,
    hooks=[LoggingHook(verbose=True)],  # Set verbose=False to reduce output
    name=AGENT_NAME,
    load_tools_from_directory=True,  # Auto-loads tools from src/tools/
)


# =============================================================================
# MAIN LOOP
# =============================================================================

def main():
    """Run the interactive agent loop."""
    print(f"\n{AGENT_NAME}")
    print(f"Run ID: {run_id}")
    print("-" * 60)
    print("Commands: exit, model, metrics, tools, name")
    print("-" * 60 + "\n")

    last_result = None

    while True:
        try:
            prompt = input("> ")
        except (KeyboardInterrupt, EOFError):
            print("\n")
            break

        if not prompt.strip():
            continue

        if prompt in ("exit", "quit"):
            break

        # Built-in commands
        if prompt == "model":
            print(f"Model: {agent.model.config}")
            continue

        if prompt == "metrics":
            pprint(agent.event_loop_metrics)
            continue

        if prompt == "tools":
            print(f"Tools: {agent.tool_names}")
            continue

        if prompt == "name":
            print(f"Name: {agent.name}")
            continue

        # Run agent
        last_result = agent(prompt)
        print(f"\n{last_result}\n")

    # Export metrics on exit
    print("Exporting metrics...")
    if last_result:
        metrics.set_from_agent_result(last_result)
    metrics_path = metrics.export()
    print(f"Saved to: {metrics_path}")

    # Record run in registry
    registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=True)


if __name__ == "__main__":
    main()
