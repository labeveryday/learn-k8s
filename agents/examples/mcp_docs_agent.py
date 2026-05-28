"""
MCP Documentation Agent

An agent that integrates with MCP servers for accessing AgentCore and Strands
documentation. Demonstrates MCP client integration with hub features.

Usage:
    cd examples
    python mcp_docs_agent.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

# Load environment variables FIRST (before hub imports)
load_dotenv()

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from mcp import stdio_client, StdioServerParameters
from strands.tools.mcp import MCPClient
from strands_tools import shell, editor, current_time
from pprint import pprint

from models import anthropic_model
from hooks import LoggingHook
from hub import (
    create_session_manager,
    MetricsExporter,
    S3PromptManager,
    AgentRegistry,
)
from hub.session import generate_run_id

# =============================================================================
# AGENT CONFIGURATION
# =============================================================================

AGENT_ID = "mcp-docs-agent"
AGENT_NAME = "MCP Documentation Agent"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """
You are a helpful assistant that specializes in building and optimizing AI agents.

You have access to documentation servers:
- AgentCore MCP: Search and fetch AWS Bedrock AgentCore documentation
- Strands MCP: Search and fetch Strands Agents framework documentation

You also have access to system tools:
- shell: Execute shell commands
- editor: Edit files
- current_time: Get the current time

When asked about AgentCore or Strands, use the appropriate documentation tools
to find accurate, up-to-date information.
""".strip()

# =============================================================================
# MODEL SELECTION
# =============================================================================

MODEL = anthropic_model(model_id="claude-sonnet-4-5-20250929")

# =============================================================================
# HUB INTEGRATION
# =============================================================================

run_id = generate_run_id(AGENT_ID)

# Register agent
registry = AgentRegistry()
registry.register(
    agent_id=AGENT_ID,
    description="Documentation agent with AgentCore and Strands MCP servers",
    tags=["mcp", "documentation", "agentcore", "strands"],
    repo_url="https://github.com/labeveryday/strands-agents-template",
    owner="labeveryday",
    environment="dev",
    model_id="claude-sonnet-4-5-20250929",
)

# Setup system prompt
prompt_manager = S3PromptManager(agent_id=AGENT_ID)
prompt_manager.ensure_exists(content=SYSTEM_PROMPT, version=PROMPT_VERSION)
system_prompt = prompt_manager.get_current()

# Session and conversation managers
session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)
conversation_manager = SlidingWindowConversationManager(
    window_size=20,
    should_truncate_results=True,
)

# Metrics exporter
metrics = MetricsExporter(
    agent_id=AGENT_ID,
    run_id=run_id,
    prompt_version=PROMPT_VERSION,
)

# =============================================================================
# MCP CLIENTS
# =============================================================================

agentcore_mcp_client = MCPClient(lambda: stdio_client(
    StdioServerParameters(
        command="uvx",
        args=["awslabs.amazon-bedrock-agentcore-mcp-server@latest"],
        autoApprove=[
            "search_agentcore_docs",
            "fetch_agentcore_doc"
        ]
    )
))

strands_mcp_client = MCPClient(lambda: stdio_client(
    StdioServerParameters(
        command="uvx",
        args=["strands-agents-mcp-server"],
        autoApprove=[
            "search_docs",
            "fetch_doc"
        ]
    )
))

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    with agentcore_mcp_client, strands_mcp_client:
        # Get tools from MCP servers
        mcp_tools = (
            agentcore_mcp_client.list_tools_sync() +
            strands_mcp_client.list_tools_sync()
        )

        agent = Agent(
            model=MODEL,
            system_prompt=system_prompt,
            tools=[shell, current_time, editor] + mcp_tools,
            session_manager=session_manager,
            conversation_manager=conversation_manager,
            hooks=[LoggingHook()],
            name=AGENT_NAME,
        )

        print(f"\n{AGENT_NAME}")
        print(f"Run ID: {run_id}")
        print("-" * 60)
        print("MCP Servers: AgentCore, Strands")
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

            last_result = agent(prompt)
            print(f"\n{last_result}\n")

        # Export metrics
        print("Exporting metrics...")
        if last_result:
            metrics.set_from_agent_result(last_result)
        metrics_path = metrics.export()
        print(f"Saved to: {metrics_path}")

        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=True)
