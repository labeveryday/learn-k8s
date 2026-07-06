# Lab 01: The sandbox on Docker, and the ceiling of the socket

**Goal:** run the sandbox on plain Docker, drive one session through its whole lifecycle
with curl, and inspect the hardening on the runtime container. The lab ends at the line
in `docker-compose.yml` that mounts `/var/run/docker.sock`, because that line is the
argument for everything the rest of the phase builds.

**Time:** ~40 min · **Cost:** free (no model needed; `execute` and `shell` are code paths)

## The problem (why this exists)

An agent with a code-execution tool types whatever the model decides into an
interpreter. On your laptop that interpreter shares your filesystem, your credentials,
and your network position. The sandbox moves execution into a disposable container with
none of the three. Before deploying it as a platform (lab 02), you drive it by hand so
every later YAML field maps to a behavior you have watched.

## 1. Bring it up

```bash
git clone https://github.com/labeveryday/agent-sandbox.git
cd agent-sandbox/server
docker compose --profile build build     # builds manager and runtime images; takes a while
docker compose up -d manager
curl -s localhost:8700/health
```

Two images came out of that build, and the split matters: the **manager** is the control
plane (FastAPI on 8700; auth, session registry, TTL reaper), and the **runtime** is the
cell (Jupyter kernel, shell, file API, worker agent on port 8000). The manager creates
one runtime container per session and proxies your requests into it. Nothing but the
manager can reach a runtime: compose puts them on an `internal: true` network.

## 2. One session, full lifecycle

Every call carries the bearer token from the compose file (`change-me` unless you set
`MANAGER_TOKEN`). Start a session:

```bash
TOK="Authorization: Bearer change-me"
SID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
echo $SID
docker ps --filter name=sbx    # the cell exists
```

Now the property that separates this from `docker run python`: the kernel is
**stateful**. Two calls, one variable:

```bash
curl -s -X POST localhost:8700/v1/sessions/$SID/execute -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"code": "x = 41"}'
curl -s -X POST localhost:8700/v1/sessions/$SID/execute -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"code": "x + 1"}'
```

**What you should see:** the second call returns `42`. The session is a notebook, and an
agent can build up state across many tool calls the way you build up state in a REPL.
Round out the surface: a shell command, a file write, a listing:

```bash
curl -s -X POST localhost:8700/v1/sessions/$SID/shell -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"command": "pip install --user cowsay && python -c \"import cowsay; cowsay.cow(str(42))\""}'
curl -s -X PUT localhost:8700/v1/sessions/$SID/files/content -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"path": "notes.md", "content": "the cell held"}'
curl -s "localhost:8700/v1/sessions/$SID/files" -H "$TOK"
```

`pip install --user` works because `/workspace` and the user's home are writable; the
rest of the filesystem is not, which you verify next.

## 3. Inspect the walls

Ask Docker what the manager asked for when it created the cell:

```bash
C=$(docker ps -q --filter name=sbx)
docker inspect $C --format '{{json .HostConfig.CapDrop}} {{.HostConfig.ReadonlyRootfs}} {{json .HostConfig.SecurityOpt}} {{.HostConfig.PidsLimit}} {{.HostConfig.Memory}}'
docker exec $C id
```

**What you should see:** all capabilities dropped, a read-only root filesystem,
`no-new-privileges`, a pids limit, a memory limit, and a non-root uid. Confirm two of
them from inside:

```bash
docker exec $C sh -c 'touch /etc/x || echo "rootfs: read-only holds"'
docker exec $C sh -c 'touch /workspace/x && echo "workspace: writable"'
```

Also try the network from a default session:

```bash
curl -s -X POST localhost:8700/v1/sessions/$SID/shell -H "$TOK" \
  -H 'Content-Type: application/json' --data @- <<'EOF'
{"command": "python -c 'import urllib.request; urllib.request.urlopen(\"https://example.com\", timeout=3); print(\"EGRESS OPEN\")' || echo NO EGRESS"}
EOF
```

Default sessions sit on the internal network with no route out. `network=True` at
session creation attaches an egress path; hold that thought for lab 03, where the
Kubernetes version of the same promise needs an enforcer.

## 4. The reaper

Sessions die of neglect on purpose. Start one with the minimum TTL and watch:

```bash
curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"ttl_seconds": 60}'
docker compose logs -f manager     # within ~90s: "reaping expired session ..."
```

Any activity extends the clock (the manager refreshes `expires_at` on every proxied
call), so an agent mid-task keeps its cell and an abandoned cell gets collected. This is
the same shape as Phase 12's heartbeat thinking: liveness of *interest*, not liveness of
process. Clean up your first session too:

```bash
curl -s -X DELETE localhost:8700/v1/sessions/$SID -H "$TOK"
```

## 5. Find the ceiling

Open `docker-compose.yml` and read the manager's volume mount:

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

The manager creates containers by talking to that socket, and the socket is
root-equivalent: any process that holds it can start a privileged container, mount the
host filesystem into it, and own the machine. Every wall you inspected in step 3
constrains the *sandboxes*; none of them constrains the *manager*, which holds the most
dangerous handle on the host. The README's own advice is a dedicated VM, and the real
fix is the next lab: on Kubernetes the manager holds a scoped API credential instead of
a socket, and the badge says pods, five verbs, one namespace.

```bash
docker compose down    # labs 02-04 use the k8s backend
```

## Checkpoint: you can now explain…

1. **What a session is.** A container with a persistent kernel, a writable workspace, a
   TTL that activity extends, and walls: caps dropped, read-only rootfs, non-root, pids
   and memory limits, no egress by default.
2. **Why the manager/runtime split exists.** Control plane and cell have different jobs,
   different images, and different exposure; only the manager is reachable, and only it
   can reach the cells.
3. **Why Docker mode has a ceiling.** The socket the manager holds is root on the host.
   Isolation for the tenants means nothing if the landlord's key is under the mat.

You can now:
- [ ] Drive start, execute, shell, write, read, list, delete against the manager with curl.
- [ ] Demonstrate kernel statefulness across two `execute` calls.
- [ ] Point at the compose line that motivates the Kubernetes backend.

## Next

→ `lab-02-a-pod-factory-with-rbac.md`: the same manager, but its create-a-cell call goes
to the API server with a ServiceAccount badge you can read, test, and revoke.
