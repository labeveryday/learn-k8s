"""Embedded Strands worker agent.

The main agent can delegate a whole task ("deep research X and write a
report") to this worker. It runs inside the same isolated container,
shares the session's persistent kernel and /workspace, and uses its own
tools: python, shell, and file access, plus http_request when the
session was started with network access.

Jobs run in background threads. Poll status via the /jobs API.
"""

import os
import threading
import time
import uuid

from . import kernel as K

JOBS: dict = {}
_jobs_lock = threading.Lock()

DEFAULT_SYSTEM_PROMPT = (
    "You are a worker agent running inside an isolated sandbox container. "
    "Your working directory is /workspace. Complete the task you are given "
    "end to end. Use run_python for computation and data work (state persists "
    "between calls), run_shell for system commands and pip installs "
    "(use pip install --user), and write_file to save deliverables. "
    "Save all final outputs (reports, data, code) as files in /workspace. "
    "Finish with a concise summary of what you did and the file paths you produced."
)


def _build_model():
    """Pick the worker's model from env. Defaults to Strands' default (Bedrock)."""
    provider = os.environ.get("WORKER_MODEL_PROVIDER", "").strip().lower()
    model_id = os.environ.get("WORKER_MODEL_ID", "").strip() or None

    if provider in ("", "bedrock"):
        if not model_id:
            return None  # Agent() default: Bedrock Claude
        from strands.models import BedrockModel
        return BedrockModel(model_id=model_id)
    if provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(model_id=model_id or "claude-sonnet-4-5-20250929", max_tokens=8192)
    if provider == "openai":
        from strands.models.openai import OpenAIModel
        return OpenAIModel(model_id=model_id or "gpt-4o")
    if provider == "litellm":
        from strands.models.litellm import LiteLLMModel
        return LiteLLMModel(model_id=model_id)
    raise ValueError(f"Unknown WORKER_MODEL_PROVIDER: {provider}")


def _build_agent(system_prompt: str | None):
    from strands import Agent, tool

    @tool
    def run_python(code: str) -> str:
        """Execute Python in the sandbox's persistent kernel. Variables and
        imports persist between calls. Returns stdout, result, and errors.

        Args:
            code: Python source to execute.
        """
        r = K.Kernel.get().execute(code, timeout=300)
        parts = []
        if r["stdout"]:
            parts.append(r["stdout"])
        if r["result"]:
            parts.append(r["result"])
        if r["stderr"]:
            parts.append("STDERR:\n" + r["stderr"])
        if r["new_files"]:
            parts.append("New files: " + ", ".join(r["new_files"]))
        if r["status"] != "ok":
            parts.append(f"[status: {r['status']}]")
        return "\n".join(parts) or "(no output)"

    @tool
    def run_shell(command: str) -> str:
        """Run a bash command in /workspace. Use 'pip install --user <pkg>'
        to add Python packages.

        Args:
            command: The shell command to run.
        """
        r = K.run_shell(command, timeout=300)
        out = (r["stdout"] + ("\nSTDERR:\n" + r["stderr"] if r["stderr"] else "")).strip()
        return f"exit={r['returncode']}\n{out}" if out else f"exit={r['returncode']}"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write a text file inside /workspace.

        Args:
            path: Relative path within /workspace.
            content: File contents.
        """
        full = os.path.realpath(os.path.join(K.WORKSPACE, path.lstrip("/")))
        root = os.path.realpath(K.WORKSPACE)
        if full != root and not full.startswith(root + os.sep):
            return "Error: path escapes workspace"
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {os.path.relpath(full, root)} ({len(content)} chars)"

    @tool
    def read_file(path: str) -> str:
        """Read a text file from /workspace.

        Args:
            path: Relative path within /workspace.
        """
        full = os.path.realpath(os.path.join(K.WORKSPACE, path.lstrip("/")))
        root = os.path.realpath(K.WORKSPACE)
        if full != root and not full.startswith(root + os.sep):
            return "Error: path escapes workspace"
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:100000]
        except OSError as exc:
            return f"Error: {exc}"

    tools = [run_python, run_shell, write_file, read_file]

    if os.environ.get("SANDBOX_NETWORK") == "1":
        try:
            from strands_tools import http_request
            tools.append(http_request)
        except ImportError:
            pass

    kwargs = {"tools": tools, "system_prompt": system_prompt or DEFAULT_SYSTEM_PROMPT}
    model = _build_model()
    if model is not None:
        kwargs["model"] = model
    return Agent(**kwargs)


def submit(task: str, system_prompt: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "task": task,
            "result": None,
            "error": None,
            "new_files": [],
            "started_at": time.time(),
            "finished_at": None,
        }
    thread = threading.Thread(target=_run, args=(job_id, task, system_prompt), daemon=True)
    thread.start()
    return job_id


def _run(job_id: str, task: str, system_prompt: str | None):
    before = K.snapshot()
    try:
        agent = _build_agent(system_prompt)
        result = agent(task)
        text, status, error = str(result), "completed", None
    except Exception as exc:  # noqa: BLE001 - surface anything to the caller
        text, status, error = None, "failed", f"{type(exc).__name__}: {exc}"
    after = K.snapshot()
    new_files, _ = K.diff_files(before, after)
    with _jobs_lock:
        JOBS[job_id].update(
            status=status,
            result=text,
            error=error,
            new_files=new_files,
            finished_at=time.time(),
        )


def get(job_id: str) -> dict | None:
    with _jobs_lock:
        job = JOBS.get(job_id)
        return dict(job) if job else None
