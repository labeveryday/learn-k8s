# Strands Agents Template

**A production-ready template for building AI agents with [Strands Agents](https://strandsagents.com/latest/).**

Created by [Du'An Lightfoot](https://duanlightfoot.com) | [@labeveryday](https://github.com/labeveryday)

---

## Features

- **Multi-Model Support** - Anthropic, Amazon Bedrock, OpenAI, Gemini, Writer, and Ollama (local)
- **Gemini Media Tools** - Image generation, video generation (Veo 3.1), and music generation (Lyria)
- **Agent Hub** - Centralized S3-backed session, metrics, and prompt management
- **MCP Server Integration** - Built-in AgentCore and Strands documentation servers
- **AgentCore Ready** - Deploy to AWS Bedrock AgentCore with included examples
- **Auto-Tool Loading** - Tools automatically discovered from `src/tools/` directory
- **Custom Hooks** - Extensible logging and monitoring system
- **Model Selection Tools** - Smart model recommendations based on task requirements

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/labeveryday/strands-agents-template.git
cd strands-agents-template
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your API key (you only need ONE provider):

```bash
# Model Provider (choose one)
ANTHROPIC_API_KEY=your_key_here
# OR
OPENAI_API_KEY=your_key_here
# OR use AWS credentials for Bedrock (no key needed, uses IAM)

# Gemini Media Tools (optional - for image/video/music generation)
GOOGLE_API_KEY=your_google_api_key

# Agent Hub (optional - enables S3 storage for sessions/metrics/prompts)
USE_S3=false
# AGENT_HUB_BUCKET=your-bucket-name
# AGENT_HUB_REGION=us-east-1
```

### 3. Run the Agent

```bash
python src/agent.py
```

Type `tools` to see available tools, `exit` to quit and export metrics.

## Project Structure

```
.
├── src/
│   ├── agent.py              # Boilerplate agent (customize this)
│   ├── config/               # Agent configuration
│   │   └── prompts.py        # System prompts
│   ├── hooks/                # Custom hook providers
│   │   └── logging_hook.py   # Tool invocation logging
│   ├── hub/                  # Centralized session/metrics/prompt management
│   │   ├── config.py         # Hub configuration
│   │   ├── metrics.py        # Run metrics export
│   │   ├── prompts.py        # Versioned prompt management
│   │   ├── registry.py       # Agent registry
│   │   └── session.py        # Session manager factory
│   ├── models/
│   │   └── models.py         # Model configurations (Anthropic, Bedrock, OpenAI, Gemini, etc.)
│   └── tools/                # Agent tools (auto-loaded)
│       ├── model_selector.py # Model selection tools
│       ├── gemini_image.py   # Image generation/editing (Gemini 3 Pro)
│       ├── gemini_video.py   # Video generation (Veo 3.1)
│       └── gemini_music.py   # Music generation (Lyria RealTime)
├── examples/
│   ├── mcp_docs_agent.py         # MCP server integration example
│   ├── gemini_image_example.py   # Image generation with hub tracking
│   ├── gemini_video_example.py   # Video generation with hub tracking
│   └── gemini_music_example.py   # Music generation with hub tracking
├── .agent_hub/               # Local hub storage (auto-created)
├── .env                      # API keys and hub config (you create this)
└── requirements.txt          # Dependencies
```

## Examples

### MCP Documentation Agent

The `examples/mcp_docs_agent.py` demonstrates MCP server integration with AgentCore and Strands documentation:

```bash
python examples/mcp_docs_agent.py
```

### Gemini Media Examples

Generate images, videos, and music with hub-integrated tracking:

```bash
# Image generation (Gemini 3 Pro)
python examples/gemini_image_example.py --prompt "A mountain landscape at sunset"

# Video generation (Veo 3.1) - takes 1-5 minutes
python examples/gemini_video_example.py --prompt "A drone shot over a coastal city"

# Music generation (Lyria RealTime)
python examples/gemini_music_example.py --prompt "Upbeat electronic dance track"
```

All examples support `--interactive` mode and `--no-hub` flag. See [examples/README.md](examples/README.md) for full documentation.

## Model Support

### Switching Models

Edit `src/agent.py`:

```python
# Anthropic (direct API)
from models import anthropic_model
MODEL = anthropic_model(model_id="claude-sonnet-4-5-20250929")

# Amazon Bedrock (multiple providers)
from models import bedrock_model
MODEL = bedrock_model(model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# Bedrock with extended thinking
MODEL = bedrock_model(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    thinking=True,
    budget_tokens=10000,
    max_tokens=16000,
)

# Bedrock with 1M context (beta)
MODEL = bedrock_model(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    extended_context=True,
)

# OpenAI
from models import openai_model
MODEL = openai_model(model_id="gpt-4o")

# Local Ollama
from models import ollama_model
MODEL = ollama_model(model_id="llama3.1:latest")

# Gemini (for agents using Gemini models)
from models import gemini_model
MODEL = gemini_model(model_id="gemini-2.0-flash")
```

### Available Bedrock Models

| Provider | Model ID | Context |
|----------|----------|---------|
| Anthropic | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | 200k (1M beta) |
| Anthropic | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | 200k |
| Meta | `us.meta.llama4-scout-17b-instruct-v1:0` | 512k |
| Amazon | `amazon.nova-pro-v1:0` | 300k |
| Amazon | `amazon.nova-lite-v1:0` | 300k |
| Mistral | `mistral.mistral-large-2407-v1:0` | 128k |

### Gemini Media Models

| Model | Tool | Description |
|-------|------|-------------|
| `gemini-3-pro-image-preview` | `generate_image`, `edit_image` | Image generation and editing |
| `veo-3.1-generate-preview` | `generate_video`, `generate_video_from_image` | Video generation (4-6 seconds) |
| `lyria-realtime-exp` | `generate_music`, `generate_music_weighted` | Music generation (5-120 seconds) |

See `src/models/models.py` for complete model listings with pricing.

## Agent Hub

The hub provides centralized management for all your agents:

- **Sessions** - Conversation history persisted to S3 or local storage
- **Metrics** - Run performance data with offline sync
- **Prompts** - Versioned system prompts with caching
- **Registry** - Track all your agents in one place

### Local Mode (default)

```
.agent_hub/
├── sessions/           # Session data
├── metrics/            # Run metrics by date
│   └── 2024-12-16/
├── prompts/            # Cached prompts per agent
└── registry.json       # Agent registry
```

### S3 Mode

Enable S3 storage by setting `USE_S3=true` in `.env`:

```bash
USE_S3=true
AGENT_HUB_BUCKET=your-bucket-name
AGENT_HUB_REGION=us-east-1
```

See [src/hub/README.md](src/hub/README.md) for detailed hub documentation.

## Adding Tools

### Step 1: Create Tool File

```python
# src/tools/my_tool.py
from strands import tool

@tool
def my_tool(param: str) -> str:
    """Description of what your tool does.

    Args:
        param: Parameter description

    Returns:
        Result description
    """
    return f"Result: {param}"
```

### Step 2: Export Tool

```python
# src/tools/__init__.py
from .my_tool import my_tool

__all__ = ["my_tool"]
```

The tool auto-loads when you run the agent.

### Built-in: Grab Code From a File

This template includes a `grab_code` tool so you can reference local code without pasting it:

```python
grab_code("src/agent.py")
grab_code("src/models/models.py", start_line=120, end_line=220)
```

## Custom Hooks

Hooks are located in `src/hooks/`:

```python
from hooks import LoggingHook

agent = Agent(hooks=[LoggingHook(verbose=True)])
```

Create your own:

```python
# src/hooks/my_hook.py
from strands.hooks import HookProvider, HookRegistry
from strands.experimental.hooks import BeforeToolInvocationEvent

class MyHook(HookProvider):
    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolInvocationEvent, self.on_tool_call)

    def on_tool_call(self, event: BeforeToolInvocationEvent) -> None:
        print(f"Calling tool: {event.tool_use['name']}")
```

## Tips

- **Metrics export** - Metrics are automatically exported when you type `exit`
- **Local first** - Hub works offline and syncs to S3 when available
- **Prompt versioning** - Use `prompt_manager.set(content, version="v2")` to update prompts
- **Extended thinking** - Anthropic/Bedrock models support extended thinking mode
- **Local models** - Ollama models run locally with zero API costs

## Requirements

- Python 3.10+
- API key for at least one provider OR AWS credentials for Bedrock
- AWS credentials (optional, for S3 hub storage and AgentCore deployment)

## License

MIT License - Use freely, build amazing things.

## About

Built by **Du'An Lightfoot** ([@labeveryday](https://github.com/labeveryday))
- Website: [duanlightfoot.com](https://duanlightfoot.com)
- YouTube: [LabEveryday](https://youtube.com/@labeveryday)

---

**Ready to build? Run `python src/agent.py` and start creating.**
