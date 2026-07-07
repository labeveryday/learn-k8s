"""Persistent Python execution kernel for the sandbox runtime.

Wraps a single Jupyter kernel so code execution is stateful across calls
(variables, imports, and dataframes persist), like a notebook or the
AgentCore Code Interpreter. Thread-safe: one execution at a time.
"""

import os
import queue
import re
import subprocess
import threading
import time

from jupyter_client.manager import KernelManager

WORKSPACE = os.environ.get("SANDBOX_WORKSPACE", "/workspace")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SKIP_DIRS = (".local", ".ipython", ".cache", ".config", ".jupyter")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def snapshot() -> dict:
    """Map of workspace relpath -> mtime, skipping dot/cache dirs."""
    files = {}
    root = os.path.realpath(WORKSPACE)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                files[os.path.relpath(path, root)] = os.path.getmtime(path)
            except OSError:
                pass
    return files


def diff_files(before: dict, after: dict) -> tuple[list, list]:
    new = sorted(k for k in after if k not in before)
    modified = sorted(k for k in after if k in before and after[k] != before[k])
    return new, modified


class Kernel:
    """Singleton wrapper around one Jupyter python3 kernel."""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "Kernel":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        os.makedirs(WORKSPACE, exist_ok=True)
        self._lock = threading.Lock()
        self._start()

    def _start(self):
        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel(cwd=WORKSPACE)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)

    def restart(self):
        with self._lock:
            try:
                self.kc.stop_channels()
                self.km.shutdown_kernel(now=True)
            except Exception:
                pass
            self._start()

    def execute(self, code: str, timeout: int = 120) -> dict:
        """Run code in the persistent kernel. Interrupts on timeout."""
        with self._lock:
            before = snapshot()
            msg_id = self.kc.execute(code)
            stdout, stderr, results = [], [], []
            status = "ok"
            timed_out = False
            deadline = time.time() + max(1, timeout)

            while True:
                if time.time() > deadline:
                    if not timed_out:
                        timed_out = True
                        status = "timeout"
                        try:
                            self.km.interrupt_kernel()
                        except Exception:
                            break
                        deadline = time.time() + 10  # grace to drain output
                    else:
                        break
                try:
                    msg = self.kc.get_iopub_msg(timeout=1)
                except queue.Empty:
                    continue
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                mtype = msg["msg_type"]
                content = msg["content"]
                if mtype == "stream":
                    target = stdout if content.get("name") == "stdout" else stderr
                    target.append(content.get("text", ""))
                elif mtype in ("execute_result", "display_data"):
                    text = content.get("data", {}).get("text/plain")
                    if text:
                        results.append(text)
                elif mtype == "error":
                    if status == "ok":
                        status = "error"
                    stderr.append(_strip_ansi("\n".join(content.get("traceback", []))))
                elif mtype == "status" and content.get("execution_state") == "idle":
                    break

            after = snapshot()
            new_files, modified_files = diff_files(before, after)
            return {
                "status": status,
                "stdout": "".join(stdout),
                "stderr": "".join(stderr),
                "result": "\n".join(results),
                "new_files": new_files,
                "modified_files": modified_files,
            }


def run_shell(command: str, timeout: int = 120) -> dict:
    """Run a bash command in the workspace."""
    before = snapshot()
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
        )
        status = "ok" if proc.returncode == 0 else "error"
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        status, code = "timeout", -1
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    after = snapshot()
    new_files, modified_files = diff_files(before, after)
    return {
        "status": status,
        "returncode": code,
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
        "new_files": new_files,
        "modified_files": modified_files,
    }
