"""
This is a sample script that creates a daily dev ops brief.
"""

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands_tools import shell, editor, current_time, http_request


model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name="us-east-1",
    max_tokens=4096,
    temperature=1,
    additional_request_fields={
        "thinking": {
            "type": "enabled",
            "budget_tokens": 1024 
        }
    },
    additional_headers={
        "anthropic-beta": "extended-context-1m-2025-04-14"
    }
)

agent = Agent(model=model, tools=[shell, editor, current_time, http_request])
result = agent("""Create a “Daily Dev Ops Brief” for me. 
Use current_time() for the timestamp, 
use http_request to fetch https://www.githubstatus.com/api/v2/status.json and 
summarize the current GitHub status in 1–2 lines, 
use shell to run git status --porcelain && git log -5 --oneline, 
then use editor to write a markdown file OPS_BRIEF.md with sections: 
Timestamp, GitHub Status, Repo Status, Recent Commits
""")