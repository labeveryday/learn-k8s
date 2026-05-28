# Agent Hub

Centralized session, metrics, and prompt management for Strands agents.

## Overview

Agent Hub provides S3-backed storage with local fallback for:

- **Sessions** - Conversation history persisted to S3
- **Metrics** - Run performance data with offline sync
- **Prompts** - Versioned system prompts with caching
- **Registry** - Track all your agents in one place

## Quick Start

### 1. Install Dependencies

```bash
pip install boto3 python-dotenv
```

### 2. Create `.env` File

Create a `.env` file in your **project root** (same directory where you run your agent):

```bash
# examples/agents/linkedin_enricher/.env

# ─────────────────────────────────────────────────────────────────────────────
# Agent Hub Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Enable S3 storage (set to "false" for local-only mode)
USE_S3=true

# Your S3 bucket name
AGENT_HUB_BUCKET=your-bucket-name

# AWS region
AGENT_HUB_REGION=us-east-1

# Local fallback directory (optional, defaults to ./.agent_hub)
# AGENT_HUB_LOCAL_DIR=./.agent_hub

# ─────────────────────────────────────────────────────────────────────────────
# AWS Credentials (if not using AWS CLI profile or IAM role)
# ─────────────────────────────────────────────────────────────────────────────

# AWS_ACCESS_KEY_ID=your_access_key
# AWS_SECRET_ACCESS_KEY=your_secret_key
# AWS_DEFAULT_REGION=us-east-1
```

### 3. Load Environment Variables

At the **top of your main script**, before importing hub:

```python
# agent.py or run.py

# Load environment variables FIRST
from dotenv import load_dotenv
load_dotenv()  # Loads from .env in current directory

# Now import hub components
from hub import (
    create_session_manager,
    MetricsExporter,
    S3PromptManager,
    AgentRegistry,
)
```

### 4. Use Hub Components

```python
from hub import create_session_manager, MetricsExporter, S3PromptManager, AgentRegistry
from hub.session import generate_run_id

AGENT_ID = "my-agent"
run_id = generate_run_id(AGENT_ID)

# Register agent (auto-creates if new)
registry = AgentRegistry()
registry.register(agent_id=AGENT_ID, description="My awesome agent")

# Setup prompt (uploads to S3 if new, uses cache after)
prompt_manager = S3PromptManager(agent_id=AGENT_ID)
prompt_manager.ensure_exists(content="You are a helpful assistant.", version="v1")
system_prompt = prompt_manager.get_current()

# Create session manager (S3 or local based on USE_S3)
session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

# Create metrics exporter
metrics = MetricsExporter(agent_id=AGENT_ID, run_id=run_id, prompt_version="v1")

# ... run your agent ...

# Export metrics when done
metrics.export()
```

---

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `USE_S3` | No | `false` | Enable S3 storage. Set to `true` for cloud mode. |
| `AGENT_HUB_BUCKET` | If USE_S3=true | - | S3 bucket name for storage |
| `AGENT_HUB_REGION` | No | `us-east-1` | AWS region |
| `AGENT_HUB_LOCAL_DIR` | No | `./.agent_hub` | Local fallback directory |

### AWS Credentials

Hub uses boto3, which checks for credentials in this order:

1. **Environment variables**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
2. **AWS credentials file**: `~/.aws/credentials`
3. **AWS config file**: `~/.aws/config`
4. **IAM role** (if running on AWS)

**Recommended**: Use `aws configure` to set up credentials once:

```bash
aws configure
# Enter your Access Key, Secret Key, and Region
```

---

## Directory Structure

### Local Storage (when USE_S3=false or offline)

```
.agent_hub/                    # Local fallback directory
├── sessions/                  # Session data
│   └── my-agent_20241215_143022/
├── metrics/                   # Metrics JSON files
│   └── 2024-12-15/
│       └── my-agent_20241215_143022.json
├── prompts/                   # Cached prompts
│   └── my-agent/
│       ├── v1.txt
│       ├── current.txt
│       └── cache_meta.json
├── registry.json              # Local agent registry
├── sync_queue.txt             # Pending S3 syncs (metrics)
└── prompt_sync_queue.txt      # Pending S3 syncs (prompts)
```

### S3 Storage

```
s3://your-bucket-name/
├── sessions/                  # S3SessionManager data
│   └── my-agent_20241215_143022/
│       └── agents/...
├── metrics/                   # Metrics organized by date
│   └── 2024-12-15/
│       └── my-agent_20241215_143022.json
├── system_prompts/            # Versioned prompts per agent
│   └── my-agent/
│       ├── v1.txt
│       ├── v2.txt
│       ├── current.txt
│       └── versions.json
└── registry.json              # Global agent registry
```

---

## Component Reference

### HubConfig

Global configuration loaded from environment variables.

```python
from hub import HubConfig
from hub.config import get_config, set_config

# Get current config
config = get_config()
print(config.use_s3)        # True/False
print(config.bucket)        # "your-bucket-name"
print(config.region)        # "us-east-1"

# Override config (for testing)
custom_config = HubConfig()
custom_config.use_s3 = False
set_config(custom_config)
```

### create_session_manager()

Factory function that returns the appropriate session manager.

```python
from hub import create_session_manager

# Automatic (uses USE_S3 env var)
session_manager = create_session_manager(
    agent_id="my-agent",
    run_id="my-agent_20241215_143022",  # Optional, auto-generated if omitted
)

# Force local (ignore USE_S3)
session_manager = create_session_manager(
    agent_id="my-agent",
    use_s3=False,
)

# Use with Strands Agent
agent = Agent(session_manager=session_manager, ...)
```

### MetricsExporter

Export run metrics to S3 or local storage.

```python
from hub import MetricsExporter

metrics = MetricsExporter(
    agent_id="my-agent",
    run_id="my-agent_20241215_143022",
    prompt_version="v1",
)

# Set custom metrics
metrics.set_stats("total_jobs", 100)
metrics.set_stats("success_rate", 0.95)
metrics.set_timing("avg_per_job_seconds", 15.3)

# Extract from Strands AgentResult
result = agent("Do something")
metrics.set_from_agent_result(result)  # Gets tokens, latency, tool usage

# Export (to S3 if available, local otherwise)
path = metrics.export()
print(f"Metrics saved to: {path}")

# Sync any pending local metrics to S3
from hub.metrics import MetricsExporter
synced = MetricsExporter.sync_pending()
print(f"Synced {synced} files to S3")
```

### S3PromptManager

Versioned system prompts with caching.

```python
from hub import S3PromptManager

manager = S3PromptManager(agent_id="my-agent")

# First time: define and upload
PROMPT = "You are a helpful assistant..."
manager.ensure_exists(content=PROMPT, version="v1")

# Get current prompt (cached, doesn't fetch from S3 every time)
prompt = manager.get_current()

# Force refresh from S3
prompt = manager.get_current(force_refresh=True)

# Upload new version
manager.set(
    content="You are an AMAZING assistant...",
    version="v2",
    make_current=True,
    note="Added enthusiasm",
)

# Get specific version
v1_prompt = manager.get_version("v1")

# List all versions
versions = manager.list_versions()
for v in versions:
    print(f"{v['version']}: {v.get('note', 'No note')}")
```

### AgentRegistry

Track all your agents in one place.

```python
from hub import AgentRegistry

registry = AgentRegistry()

# Register agent (safe to call multiple times)
registry.register(
    agent_id="my-agent",
    description="Does amazing things",
    tags=["production", "nlp"],
)

# Get agent info
info = registry.get_agent("my-agent")
print(info)

# List all agents
agents = registry.list_agents()
for agent in agents:
    print(f"{agent['agent_id']}: {agent.get('description', 'No description')}")

# Filter by tag
nlp_agents = registry.list_agents(tag="nlp")

# Record a run
registry.record_run(
    agent_id="my-agent",
    run_id="my-agent_20241215_143022",
    success=True,
)

# Sync pending changes to S3
from hub.registry import AgentRegistry
AgentRegistry.sync_if_pending()
```

---

## Offline-First Workflow

Hub is designed to work offline and sync when online:

1. **Offline**: All data saved locally in `.agent_hub/`
2. **Online**: Automatically syncs to S3
3. **Pending syncs**: Queued in `sync_queue.txt` files
4. **Manual sync**: Call `MetricsExporter.sync_pending()` or `AgentRegistry.sync_if_pending()`

### Example: Sync on Startup

```python
from dotenv import load_dotenv
load_dotenv()

from hub.metrics import MetricsExporter
from hub.registry import AgentRegistry

# Sync any pending data from previous offline runs
print("Syncing pending data...")
metrics_synced = MetricsExporter.sync_pending()
registry_synced = AgentRegistry.sync_if_pending()
print(f"  Metrics synced: {metrics_synced}")
print(f"  Registry synced: {registry_synced}")

# Continue with your agent...
```

---

## Multi-Machine Setup

To access the same data from multiple machines:

1. **Same AWS credentials** on each machine (`aws configure`)
2. **Same `AGENT_HUB_BUCKET`** in `.env`
3. **Cross-account access**: Add bucket policy if using different AWS accounts

### Bucket Policy for Cross-Account Access

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "AWS": "arn:aws:iam::OTHER_ACCOUNT_ID:user/username"
    },
    "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"],
    "Resource": [
      "arn:aws:s3:::your-bucket-name",
      "arn:aws:s3:::your-bucket-name/*"
    ]
  }]
}
```

---

## Best Practices

1. **Always load `.env` first**: Before importing hub modules
2. **Use `ensure_exists()` for prompts**: Safe to call every run, only uploads if new
3. **Call `export()` at the end**: Don't forget to save your metrics
4. **Sync on startup**: Clear any pending offline data
5. **Version your prompts**: Use "v1", "v2", etc. for tracking changes
6. **Add notes to prompt versions**: Helps remember what changed

---

## Troubleshooting

### "AGENT_HUB_BUCKET must be set when USE_S3=true"

Make sure you're loading `.env` before importing hub:

```python
from dotenv import load_dotenv
load_dotenv()  # Must be before hub imports!

from hub import create_session_manager
```

### "Could not fetch prompt from S3"

Check:
1. AWS credentials are configured (`aws configure`)
2. Bucket exists and is accessible
3. IAM user has required permissions

### "Warning: S3 unavailable, falling back to local"

This is normal when offline. Data will sync when S3 is reachable.

---

## License

MIT

