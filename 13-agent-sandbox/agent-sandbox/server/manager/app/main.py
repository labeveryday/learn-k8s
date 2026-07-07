"""Sandbox manager: control plane for isolated code-interpreter sessions.

Creates one sandbox (Docker container or K8s pod) per session, proxies
execution/file/job requests into it, extends the session TTL on activity,
and reaps expired sessions.
"""

import logging
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .backends import get_backend

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sandbox.manager")

BACKEND_NAME = os.environ.get("SANDBOX_BACKEND", "docker")
DEFAULT_IMAGE = os.environ.get("SANDBOX_IMAGE", "strands-sandbox-runtime:latest")
MANAGER_TOKEN = os.environ.get("MANAGER_TOKEN", "")
DEFAULT_TTL = int(os.environ.get("SANDBOX_DEFAULT_TTL_SECONDS", "1800"))
MAX_SESSIONS = int(os.environ.get("SANDBOX_MAX_SESSIONS", "20"))
PASSTHROUGH_ENV = [
    v.strip()
    for v in os.environ.get("SANDBOX_PASSTHROUGH_ENV", "").split(",")
    if v.strip()
]

backend = get_backend(BACKEND_NAME)
SESSIONS: dict = {}
_lock = threading.Lock()


def _reaper():
    while True:
        time.sleep(15)
        now = time.time()
        with _lock:
            expired = [sid for sid, s in SESSIONS.items() if s["expires_at"] < now]
        for sid in expired:
            log.info("reaping expired session %s", sid)
            _destroy(sid)


def _destroy(session_id: str):
    with _lock:
        session = SESSIONS.pop(session_id, None)
    if session:
        try:
            backend.destroy(session["ref"])
        except Exception as exc:  # noqa: BLE001
            log.warning("destroy failed for %s: %s", session_id, exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    removed = backend.cleanup_orphans()
    if removed:
        log.info("removed %d orphaned sandboxes on startup", removed)
    threading.Thread(target=_reaper, daemon=True).start()
    yield
    for sid in list(SESSIONS):
        _destroy(sid)


app = FastAPI(title="strands-sandbox-manager", lifespan=lifespan)


def auth(authorization: str | None = Header(default=None)):
    if MANAGER_TOKEN and authorization != f"Bearer {MANAGER_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


class SessionCreate(BaseModel):
    network: bool = False
    ttl_seconds: int = Field(default=DEFAULT_TTL, ge=60, le=86400)
    cpus: float = Field(default=1.0, gt=0, le=8)
    memory_mb: int = Field(default=1024, ge=256, le=16384)
    image: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    code: str
    timeout_s: int = Field(default=120, ge=1, le=1800)


class ShellRequest(BaseModel):
    command: str
    timeout_s: int = Field(default=120, ge=1, le=1800)


class WriteFileRequest(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


class JobRequest(BaseModel):
    task: str
    system_prompt: str | None = None


def _get_session(session_id: str) -> dict:
    with _lock:
        session = SESSIONS.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="session not found or expired")
        session["expires_at"] = time.time() + session["ttl_seconds"]
        return dict(session)


def _proxy(session_id, method, path, json=None, params=None, timeout=60):
    session = _get_session(session_id)
    headers = {"X-Sandbox-Token": session["token"]}
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.request(
                method, session["endpoint"] + path, json=json, params=params, headers=headers
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"sandbox unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _wait_healthy(endpoint: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(timeout=3) as http:
        while time.time() < deadline:
            try:
                if http.get(endpoint + "/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(1)
    return False


@app.get("/health")
def health():
    with _lock:
        return {"status": "ok", "backend": BACKEND_NAME, "sessions": len(SESSIONS)}


@app.post("/v1/sessions", dependencies=[Depends(auth)])
def create_session(req: SessionCreate):
    with _lock:
        if len(SESSIONS) >= MAX_SESSIONS:
            raise HTTPException(status_code=429, detail="max sessions reached")

    session_id = uuid.uuid4().hex[:12]
    token = secrets.token_urlsafe(24)

    env = {k: os.environ[k] for k in PASSTHROUGH_ENV if os.environ.get(k)}
    env.update(req.env)
    env["SANDBOX_TOKEN"] = token
    env["SANDBOX_NETWORK"] = "1" if req.network else "0"

    info = backend.create(
        session_id=session_id,
        image=req.image or DEFAULT_IMAGE,
        env=env,
        cpus=req.cpus,
        memory_mb=req.memory_mb,
        network=req.network,
        ttl_seconds=req.ttl_seconds,
    )
    if not _wait_healthy(info["endpoint"]):
        backend.destroy(info["ref"])
        raise HTTPException(status_code=502, detail="sandbox failed to become healthy")

    with _lock:
        SESSIONS[session_id] = {
            "session_id": session_id,
            "endpoint": info["endpoint"],
            "ref": info["ref"],
            "token": token,
            "network": req.network,
            "ttl_seconds": req.ttl_seconds,
            "created_at": time.time(),
            "expires_at": time.time() + req.ttl_seconds,
        }
    log.info("session %s ready (network=%s)", session_id, req.network)
    return {"session_id": session_id, "network": req.network, "ttl_seconds": req.ttl_seconds}


@app.get("/v1/sessions", dependencies=[Depends(auth)])
def list_sessions():
    with _lock:
        return {
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "network": s["network"],
                    "created_at": s["created_at"],
                    "expires_at": s["expires_at"],
                }
                for s in SESSIONS.values()
            ]
        }


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(auth)])
def delete_session(session_id: str):
    _destroy(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.post("/v1/sessions/{session_id}/execute", dependencies=[Depends(auth)])
def execute(session_id: str, req: ExecuteRequest):
    return _proxy(session_id, "POST", "/execute", json=req.model_dump(), timeout=req.timeout_s + 60)


@app.post("/v1/sessions/{session_id}/shell", dependencies=[Depends(auth)])
def shell(session_id: str, req: ShellRequest):
    return _proxy(session_id, "POST", "/shell", json=req.model_dump(), timeout=req.timeout_s + 60)


@app.post("/v1/sessions/{session_id}/restart_kernel", dependencies=[Depends(auth)])
def restart_kernel(session_id: str):
    return _proxy(session_id, "POST", "/restart_kernel", timeout=90)


@app.get("/v1/sessions/{session_id}/files", dependencies=[Depends(auth)])
def list_files(session_id: str):
    return _proxy(session_id, "GET", "/files")


@app.get("/v1/sessions/{session_id}/files/content", dependencies=[Depends(auth)])
def read_file(session_id: str, path: str):
    return _proxy(session_id, "GET", "/files/content", params={"path": path})


@app.put("/v1/sessions/{session_id}/files/content", dependencies=[Depends(auth)])
def write_file(session_id: str, req: WriteFileRequest):
    return _proxy(session_id, "PUT", "/files/content", json=req.model_dump())


@app.post("/v1/sessions/{session_id}/jobs", dependencies=[Depends(auth)])
def create_job(session_id: str, req: JobRequest):
    return _proxy(session_id, "POST", "/jobs", json=req.model_dump())


@app.get("/v1/sessions/{session_id}/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(session_id: str, job_id: str):
    return _proxy(session_id, "GET", f"/jobs/{job_id}")
