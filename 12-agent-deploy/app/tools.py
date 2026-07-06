"""Tools the review agent can call.

Read access to one git repository, plus a single write path: appending
feedback entries to a ledger file. The repo lives wherever REPO_DIR points.
On your laptop that is a directory you cloned yourself; in the cluster an
initContainer clones it into an emptyDir before this process starts.
"""

import datetime
import os
import pathlib
import re
import subprocess

from strands import tool

REPO_DIR = pathlib.Path(os.getenv("REPO_DIR", "/work/repo"))
FEEDBACK_FILE = pathlib.Path(os.getenv("FEEDBACK_FILE", str(REPO_DIR / "NEW_FEEDBACK.md")))

# Never let the agent read credentials out of the repo it reviews.
BLOCKED = re.compile(r"(^|/)(\.env[^/]*|secrets|\.git|node_modules|\.venv|__pycache__)(/|$)")

FEEDBACK_HEADER = """# New Feedback

Entries are appended here by the review agent. Each entry keeps its status
line until a human or a build session resolves it.
"""


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def repo_ready() -> bool:
    """True when REPO_DIR holds a git repository. The readiness probe calls this."""
    return (REPO_DIR / ".git").is_dir()


def feedback_entries() -> int:
    if not FEEDBACK_FILE.exists():
        return 0
    return len(re.findall(r"^## \[F-\d+\]", FEEDBACK_FILE.read_text(), re.M))


@tool
def list_repo() -> str:
    """List all git-tracked files in the repository.

    Returns:
        One file path per line
    """
    return _git("ls-files")


@tool
def read_repo_file(path: str) -> str:
    """Read a file from the repository. Env files, secrets, and .git are blocked.

    Args:
        path: Path relative to the repository root

    Returns:
        File contents (truncated to 30k characters)
    """
    p = (REPO_DIR / path).resolve()
    if not str(p).startswith(str(REPO_DIR)):
        return f"ERROR: access to {path} is not allowed"
    rel = str(p.relative_to(REPO_DIR))
    if BLOCKED.search(rel):
        return f"ERROR: access to {path} is not allowed"
    if not p.is_file():
        return f"ERROR: {path} is not a file"
    return p.read_text(errors="replace")[:30000]


@tool
def recent_commits() -> str:
    """Show the last 10 commits and the file-level stat of the most recent one.

    Returns:
        git log --oneline output followed by git show --stat HEAD
    """
    return _git("log", "--oneline", "-10") + "\n\n" + _git("show", "--stat", "HEAD")


@tool
def file_feedback(suggestion: str) -> str:
    """File one concrete, actionable suggestion about the repository.

    Args:
        suggestion: The suggestion. Name the file, the problem, and the proposed fix.

    Returns:
        The id the entry was filed under
    """
    count = feedback_entries()
    if not FEEDBACK_FILE.exists():
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        FEEDBACK_FILE.write_text(FEEDBACK_HEADER)
    fid = f"F-{count + 1:03d}"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with FEEDBACK_FILE.open("a") as f:
        f.write(f"\n## [{fid}] {stamp} - review agent\n**Status: PENDING**\n\n{suggestion.strip()}\n")
    return f"Filed as {fid}"
