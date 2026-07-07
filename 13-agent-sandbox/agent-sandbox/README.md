# strands-sandbox

Self-hosted code interpreter for [Strands Agents](https://github.com/strands-agents/sdk-python). Same idea as Bedrock AgentCore Code Interpreter, but running on your own Docker host or Kubernetes cluster. Includes a worker agent inside each sandbox so your main agent can delegate whole tasks (deep research, long jobs) into isolation.

Built to slot into [strands-pack](https://github.com/labeveryday/strands-pack): one action-based `sandbox` tool.

## How it works

```
Your Strands agent
      |  sandbox(action=..., ...)
      v
Manager (FastAPI, port 8700)          <- control plane, auth, TTL reaper
      |  creates + proxies
      v
One isolated runtime per session      <- Docker container or K8s pod
  - persistent Jupyter kernel (stateful python, like a notebook)
  - bash shell + pip install --user
  - private /workspace (tmpfs / emptyDir, size-limited)
  - worker agent (Strands) for delegated jobs
  - no internet unless the session was started with network=True
```

Isolation per session: dropped capabilities, no-new-privileges, read-only rootfs, non-root user, CPU/memory/pids limits, seccomp default, and a no-egress network by default. `network=True` attaches an egress path (Docker bridge network, or the `egress=allowed` NetworkPolicy on K8s).

## Quickstart (Docker)

```bash
cd server
docker compose --profile build build     # builds manager + runtime images
docker compose up -d manager

export SANDBOX_MANAGER_URL=http://localhost:8700
export SANDBOX_MANAGER_TOKEN=change-me   # match MANAGER_TOKEN in compose
```

Put model credentials for the worker agent in `server/.env` (Bedrock creds by default, or `ANTHROPIC_API_KEY` etc). They get injected into sandboxes via `SANDBOX_PASSTHROUGH_ENV`.

Give the tool to an agent:

```python
from strands import Agent
from strands_pack.sandbox import sandbox

agent = Agent(tools=[sandbox])
agent("Start a sandbox, plot y=x^2 to plot.png with matplotlib, list the files, then stop the sandbox.")
```

Copy `src/strands_pack/sandbox.py` into strands-pack's `src/strands_pack/` and export it in `__init__.py`. Only extra dependency is `httpx`.

## Delegating tasks to the worker agent

The worker runs inside the sandbox with its own tools (`run_python`, `run_shell`, `write_file`, `read_file`, plus `http_request` when the session has network). It shares the session's kernel and workspace, so your main agent can inspect everything it produced.

```python
s = sandbox(action="start", network=True, ttl_minutes=60)
job = sandbox(
    action="delegate",
    session_id=s["session_id"],
    task="Research X, analyze the data with pandas, write findings to report.md",
    wait_seconds=600,
)
report = sandbox(action="read_file", session_id=s["session_id"], path="report.md")
sandbox(action="stop", session_id=s["session_id"])
```

Delegated jobs need model API access, so start those sessions with `network=True` (or point `WORKER_MODEL_PROVIDER`/`WORKER_MODEL_ID` at an endpoint reachable through an egress proxy you control).

## Tool actions

| Action | Required args | Notes |
|---|---|---|
| `start` | | `network`, `ttl_minutes`, `cpus`, `memory_mb` optional. Returns `session_id` |
| `execute` | `session_id`, `code` | Stateful python kernel. Returns stdout, result, stderr, new_files |
| `shell` | `session_id`, `command` | Bash in /workspace. `pip install --user <pkg>` works |
| `write_file` | `session_id`, `path`, `content` | |
| `read_file` | `session_id`, `path` | Binary files come back base64 |
| `list_files` | `session_id` | |
| `delegate` | `session_id`, `task` | Returns `job_id`; `wait_seconds > 0` blocks until done |
| `job_status` | `session_id`, `job_id` | |
| `restart_kernel` | `session_id` | Clears python state, keeps files |
| `stop` | `session_id` | Destroys the container/pod |
| `list_sessions` | | |

## Kubernetes

```bash
# Build and push both images
docker build -t YOUR_REGISTRY/strands-sandbox-runtime:latest server/runtime
docker build -t YOUR_REGISTRY/strands-sandbox-manager:latest server/manager
docker push YOUR_REGISTRY/strands-sandbox-runtime:latest
docker push YOUR_REGISTRY/strands-sandbox-manager:latest

# Edit image refs in k8s/manager.yaml, then:
kubectl apply -f k8s/manager.yaml
kubectl apply -f k8s/network-policies.yaml
kubectl -n strands-sandboxes create secret generic sandbox-manager-secrets \
  --from-literal=MANAGER_TOKEN=<token>
```

The manager creates one pod per session in its own namespace. Network policies deny sandbox egress by default, allow DNS, and open the internet only for `network=True` sessions while always blocking RFC1918 and the cloud metadata endpoint. Requires a CNI that enforces NetworkPolicy. For Bedrock creds prefer IRSA or EKS Pod Identity on the sandbox pods over passing keys.

Expose the manager to your agents via the ClusterIP service, an internal LB, or `kubectl port-forward svc/sandbox-manager 8700:8700`.

## Configuration

Manager env:

| Var | Default | Purpose |
|---|---|---|
| `SANDBOX_BACKEND` | `docker` | `docker` or `k8s` |
| `SANDBOX_IMAGE` | `strands-sandbox-runtime:latest` | Runtime image |
| `MANAGER_TOKEN` | empty (open) | Bearer token clients must send |
| `SANDBOX_PASSTHROUGH_ENV` | empty | Comma list of env vars copied into every sandbox |
| `SANDBOX_DEFAULT_TTL_SECONDS` | `1800` | Idle TTL; any activity extends it |
| `SANDBOX_MAX_SESSIONS` | `20` | Concurrency cap |
| `SANDBOX_WORKSPACE_MB` | 512 / 1024 | Workspace size (docker / k8s) |
| `SANDBOX_NAMESPACE` | `strands-sandboxes` | K8s only |

Worker agent env (set via passthrough or per-session `env`):

| Var | Default | Purpose |
|---|---|---|
| `WORKER_MODEL_PROVIDER` | `bedrock` | `bedrock`, `anthropic`, `openai`, `litellm` |
| `WORKER_MODEL_ID` | provider default | Model for the in-sandbox worker |

Client env: `SANDBOX_MANAGER_URL`, `SANDBOX_MANAGER_TOKEN`.

## Notes and limits

- Session registry is in-memory. On manager restart, orphaned sandboxes are cleaned up and clients must start new sessions.
- The manager mounts `docker.sock` in Docker mode, which is root-equivalent on that host. Run it on a dedicated VM, or use the K8s backend for real multi-tenant isolation. gVisor or Kata as the container runtime hardens it further.
- Containers share the host kernel. For truly hostile code, prefer K8s with a sandboxed runtime class.
- Workspaces are tmpfs/emptyDir: fast, size-capped, gone when the session stops. Pull anything you need out with `read_file` first.
- If the manager runs directly on the host (not compose) in Docker mode, it must be able to reach container IPs; that works on Linux, not Docker Desktop for Mac. Compose handles this by putting the manager on the internal network.

## License

MIT
