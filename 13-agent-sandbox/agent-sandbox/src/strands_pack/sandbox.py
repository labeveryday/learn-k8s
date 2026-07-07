"""Sandbox tool for Strands Agents.

Self-hosted code interpreter. Each session is an isolated container
(Docker) or pod (Kubernetes) with a persistent Python kernel, a shell,
a private /workspace, and an embedded worker agent that can take whole
delegated tasks (deep research, long jobs).

Env vars:
    SANDBOX_MANAGER_URL     Manager base URL, e.g. http://localhost:8700
    SANDBOX_MANAGER_TOKEN   Bearer token matching the manager's MANAGER_TOKEN
"""

import os
import time

from strands import tool

_TIMEOUT_PAD = 60


def _client():
    try:
        import httpx
    except ImportError as exc:
        raise ImportError("sandbox tool requires httpx: pip install httpx") from exc
    base = os.environ.get("SANDBOX_MANAGER_URL", "http://localhost:8700").rstrip("/")
    headers = {}
    token = os.environ.get("SANDBOX_MANAGER_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx, base, headers


def _call(method: str, path: str, json=None, params=None, timeout: int = 60) -> dict:
    httpx, base, headers = _client()
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.request(method, base + path, json=json, params=params, headers=headers)
    except httpx.HTTPError as exc:
        return {"status": "error", "message": f"sandbox manager unreachable at {base}: {exc}"}
    if resp.status_code >= 400:
        return {"status": "error", "http_status": resp.status_code, "message": resp.text[:2000]}
    data = resp.json()
    if isinstance(data, dict) and "status" not in data:
        data["status"] = "success"
    return data


@tool
def sandbox(
    action: str,
    session_id: str = None,
    code: str = None,
    command: str = None,
    path: str = None,
    content: str = None,
    task: str = None,
    job_id: str = None,
    network: bool = False,
    ttl_minutes: int = 30,
    timeout_s: int = 120,
    cpus: float = 1.0,
    memory_mb: int = 1024,
    system_prompt: str = None,
    wait_seconds: int = 0,
) -> dict:
    """Isolated code interpreter sandbox. Run Python and shell commands in a
    private container with a persistent kernel and workspace, or delegate a
    whole task to a worker agent inside the sandbox.

    Typical flow: start a session once, reuse its session_id for every call,
    then stop it when finished.

    Actions:
        start: Create a new sandbox session. Optional: network (True to allow
            internet and model API access, required for 'delegate'),
            ttl_minutes, cpus, memory_mb. Returns session_id.
        execute: Run Python code in the session's persistent kernel. Requires
            session_id, code. Optional timeout_s. Variables persist between
            calls. Returns stdout, result, stderr, new_files.
        shell: Run a bash command in the workspace. Requires session_id,
            command. Use 'pip install --user <pkg>' to add packages.
        write_file: Write a text file into /workspace. Requires session_id,
            path, content.
        read_file: Read a file from /workspace. Requires session_id, path.
        list_files: List workspace files. Requires session_id.
        delegate: Hand a whole task to the worker agent inside the sandbox
            (e.g. research and write a report, build and test a script).
            Requires session_id, task. Optional system_prompt. Returns job_id
            immediately; set wait_seconds > 0 to block and poll until done.
        job_status: Check a delegated job. Requires session_id, job_id.
            Returns status, result, error, new_files.
        restart_kernel: Reset the Python kernel state. Requires session_id.
        stop: Destroy the session and its container. Requires session_id.
        list_sessions: List active sessions on the manager.

    Args:
        action: One of start, execute, shell, write_file, read_file,
            list_files, delegate, job_status, restart_kernel, stop,
            list_sessions.
        session_id: Session ID returned by 'start'.
        code: Python source for 'execute'.
        command: Bash command for 'shell'.
        path: Workspace-relative file path for file actions.
        content: File contents for 'write_file'.
        task: Task description for 'delegate'.
        job_id: Job ID for 'job_status'.
        network: For 'start', allow internet egress from the sandbox.
        ttl_minutes: For 'start', idle lifetime; activity extends it.
        timeout_s: Execution timeout for 'execute' and 'shell'.
        cpus: For 'start', CPU limit.
        memory_mb: For 'start', memory limit in MB.
        system_prompt: For 'delegate', override the worker agent's prompt.
        wait_seconds: For 'delegate', poll internally up to this long and
            return the finished result instead of just a job_id.
    """
    action = (action or "").strip().lower()

    if action == "start":
        return _call(
            "POST",
            "/v1/sessions",
            json={
                "network": network,
                "ttl_seconds": max(60, ttl_minutes * 60),
                "cpus": cpus,
                "memory_mb": memory_mb,
            },
            timeout=180,
        )

    if action == "list_sessions":
        return _call("GET", "/v1/sessions")

    if not session_id:
        return {"status": "error", "message": f"action '{action}' requires session_id (use action='start' first)"}

    if action == "execute":
        if not code:
            return {"status": "error", "message": "execute requires code"}
        return _call(
            "POST",
            f"/v1/sessions/{session_id}/execute",
            json={"code": code, "timeout_s": timeout_s},
            timeout=timeout_s + _TIMEOUT_PAD,
        )

    if action == "shell":
        if not command:
            return {"status": "error", "message": "shell requires command"}
        return _call(
            "POST",
            f"/v1/sessions/{session_id}/shell",
            json={"command": command, "timeout_s": timeout_s},
            timeout=timeout_s + _TIMEOUT_PAD,
        )

    if action == "write_file":
        if not path or content is None:
            return {"status": "error", "message": "write_file requires path and content"}
        return _call(
            "PUT",
            f"/v1/sessions/{session_id}/files/content",
            json={"path": path, "content": content, "encoding": "utf-8"},
        )

    if action == "read_file":
        if not path:
            return {"status": "error", "message": "read_file requires path"}
        return _call("GET", f"/v1/sessions/{session_id}/files/content", params={"path": path})

    if action == "list_files":
        return _call("GET", f"/v1/sessions/{session_id}/files")

    if action == "restart_kernel":
        return _call("POST", f"/v1/sessions/{session_id}/restart_kernel", timeout=120)

    if action == "delegate":
        if not task:
            return {"status": "error", "message": "delegate requires task"}
        job = _call(
            "POST",
            f"/v1/sessions/{session_id}/jobs",
            json={"task": task, "system_prompt": system_prompt},
        )
        if job.get("status") == "error" or wait_seconds <= 0:
            return job
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(5)
            status = _call("GET", f"/v1/sessions/{session_id}/jobs/{job['job_id']}")
            if status.get("status") in ("completed", "failed", "error"):
                return status
        status = _call("GET", f"/v1/sessions/{session_id}/jobs/{job['job_id']}")
        status["note"] = "job still running; poll again with action='job_status'"
        return status

    if action == "job_status":
        if not job_id:
            return {"status": "error", "message": "job_status requires job_id"}
        return _call("GET", f"/v1/sessions/{session_id}/jobs/{job_id}")

    if action == "stop":
        return _call("DELETE", f"/v1/sessions/{session_id}")

    return {
        "status": "error",
        "message": f"unknown action '{action}'. Valid: start, execute, shell, write_file, "
        "read_file, list_files, delegate, job_status, restart_kernel, stop, list_sessions",
    }
