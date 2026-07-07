"""Sandbox runtime API. One instance runs inside every session container."""

import base64
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import kernel as K
from . import worker

TOKEN = os.environ.get("SANDBOX_TOKEN", "")

app = FastAPI(title="strands-sandbox-runtime")


def auth(x_sandbox_token: str | None = Header(default=None)):
    if TOKEN and x_sandbox_token != TOKEN:
        raise HTTPException(status_code=401, detail="invalid sandbox token")


def _safe_path(path: str) -> str:
    root = os.path.realpath(K.WORKSPACE)
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    resolved = os.path.realpath(candidate)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise HTTPException(status_code=400, detail="path outside workspace")
    return resolved


class ExecuteRequest(BaseModel):
    code: str
    timeout_s: int = 120


class ShellRequest(BaseModel):
    command: str
    timeout_s: int = 120


class WriteFileRequest(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"  # "utf-8" or "base64"


class JobRequest(BaseModel):
    task: str
    system_prompt: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/execute", dependencies=[Depends(auth)])
def execute(req: ExecuteRequest):
    return K.Kernel.get().execute(req.code, timeout=req.timeout_s)


@app.post("/restart_kernel", dependencies=[Depends(auth)])
def restart_kernel():
    K.Kernel.get().restart()
    return {"status": "ok"}


@app.post("/shell", dependencies=[Depends(auth)])
def shell(req: ShellRequest):
    return K.run_shell(req.command, timeout=req.timeout_s)


@app.get("/files", dependencies=[Depends(auth)])
def list_files():
    root = os.path.realpath(K.WORKSPACE)
    entries = []
    for rel in sorted(K.snapshot()):
        try:
            entries.append({"path": rel, "size": os.path.getsize(os.path.join(root, rel))})
        except OSError:
            pass
    return {"workspace": root, "files": entries}


@app.get("/files/content", dependencies=[Depends(auth)])
def read_file(path: str):
    full = _safe_path(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="file not found")
    with open(full, "rb") as f:
        data = f.read()
    if len(data) > 5_000_000:
        raise HTTPException(status_code=413, detail="file too large (>5MB)")
    try:
        return {"path": path, "encoding": "utf-8", "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": path, "encoding": "base64", "content": base64.b64encode(data).decode()}


@app.put("/files/content", dependencies=[Depends(auth)])
def write_file(req: WriteFileRequest):
    full = _safe_path(req.path)
    os.makedirs(os.path.dirname(full) or K.WORKSPACE, exist_ok=True)
    if req.encoding == "base64":
        data = base64.b64decode(req.content)
    else:
        data = req.content.encode("utf-8")
    with open(full, "wb") as f:
        f.write(data)
    return {"status": "ok", "path": req.path, "bytes": len(data)}


@app.post("/jobs", dependencies=[Depends(auth)])
def create_job(req: JobRequest):
    job_id = worker.submit(req.task, req.system_prompt)
    return {"job_id": job_id, "status": "running"}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    job = worker.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
