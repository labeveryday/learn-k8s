# Lab 02: Containerize: an image that caches well and runs as nobody

**Goal:** build the agent image, prove the layer order earns its keep, run the container
the way the cluster will (non-root, env-configured, repo mounted in), and load it into
kind. Phase 02 taught these mechanics on a toy; this lab spends them on a workload you
are about to operate.

**Time:** ~30 min · **Cost:** free

## The problem (why this exists)

`python server.py` worked because your laptop happened to have Python 3.12, git, and a
virtualenv you built by hand in lab 01. None of that survives the trip to a cluster node.
The image is the contract that says: everything this process needs travels with it. The
craft is in what the contract costs you per rebuild, and in what the process is allowed
to be once it runs.

## 1. Read the Dockerfile before building it

Open `app/Dockerfile`. Four decisions, each visible in a line:

| Line | Decision | Cost of getting it wrong |
|---|---|---|
| `python:3.12-slim` | slim base, add only git and curl | the default `python:3.12` image carries ~700 MB of build toolchain the agent never calls |
| `COPY requirements.txt .` before `COPY agent.py ...` | dependencies layer before source layer | every one-line code edit reinstalls every dependency |
| `USER agent` | the process runs as a named non-root user | a container escape starts with root on the node's kernel |
| `CMD ["python", "server.py"]` | exec form, no shell wrapper | signals reach a shell instead of Python; SIGTERM dies in the middleman and every stop waits for SIGKILL |

The `COPY` split is the one to internalize. Docker caches each layer keyed on its inputs.
`requirements.txt` changes when you add a dependency, which is rare. Source changes every
edit. Order the rare above the frequent and the expensive `pip install` layer survives
almost every rebuild.

## 2. Build it, and read what you built

```bash
cd 12-agent-deploy/app
docker build -t review-agent:0.1 .
docker images review-agent          # note the size
docker history review-agent:0.1    # one line per layer, with its size
```

**What you should see** in the history: the `pip install` layer dominates (the Strands
SDK, Flask, discord.py), the apt layer is small because `--no-install-recommends` and the
`rm -rf /var/lib/apt/lists/*` ran in the same layer, and your source layer costs a few
kilobytes.

## 3. Prove the cache order (measure, don't trust)

Touch a source file and rebuild:

```bash
touch server.py
time docker build -t review-agent:0.1 .
```

Every layer through `pip install` reports `CACHED`; the build finishes in about a second.
Now break the order on purpose. Edit the Dockerfile so `COPY agent.py prompts.py
server.py tools.py .` sits above the `COPY requirements.txt` line, and:

```bash
touch server.py
time docker build -t review-agent:bad .
```

The cache dies at the source copy, and you sit through the full dependency install for a
one-byte change. Multiply that by every rebuild in a working day; that is the bill for
one swapped line. Restore the original order before moving on.

## 4. Run the container as the cluster will

The container has no repo inside it, by design: the image holds the agent, the repo
arrives at runtime (in lab 03, an initContainer delivers it). Simulate that with a mount,
and pass the model config as env:

```bash
docker run --rm -p 8080:8080 \
  -e OPENAI_BASE_URL=http://host.docker.internal:8000/v1 \
  -e REPO_DIR=/work/repo \
  -v /tmp/agent-work:/work \
  review-agent:0.1
```

(`host.docker.internal` reaches the vLLM port-forward running on your host. Anthropic
instead: swap in `-e ANTHROPIC_API_KEY=...` and drop the base URL.)

Probe from another terminal, same as lab 01:

```bash
curl -s localhost:8080/readyz
curl -s -X POST localhost:8080/review | python3 -m json.tool
```

Confirm the user the process runs as:

```bash
docker exec $(docker ps -q -f ancestor=review-agent:0.1) id
# uid=1000(agent) gid=1000(agent) ...
```

Not root. The Deployment in lab 03 sets `runAsNonRoot: true`, which turns this image
property into an enforced contract: a future image that drops the `USER` line fails
admission instead of failing an audit.

## 5. Hand the image to kind

kind nodes pull from their own containerd store, not from your Docker daemon. Load it:

```bash
kind load docker-image review-agent:0.1
docker exec -it $(kind get clusters | head -1 | xargs -I{} echo {}-control-plane) \
  crictl images | grep review-agent
```

The image now exists on the node. This, plus `imagePullPolicy: IfNotPresent` in the
Deployment, is why lab 03 never touches a registry. On a real cluster (Phase 09, LKE)
this step becomes `docker push` to a registry the nodes can reach; the manifests do not
change, only the image reference does.

## Break it, then read the error

Start the container with no model configuration at all:

```bash
docker run --rm review-agent:0.1
```

It exits in about two seconds with the `RuntimeError` from `agent.py`:
`set OPENAI_BASE_URL (vLLM) or ANTHROPIC_API_KEY`. That crash is a feature you built.
The alternative, a process that starts, looks healthy, and fails on the first review
twenty minutes later, wastes the kubelet's restart machinery on a problem no restart can
fix. Fail at import, log the reason, exit nonzero: in lab 03 this exact behavior becomes
a readable `CrashLoopBackOff` with the answer sitting in `kubectl logs --previous`.

## Checkpoint: you can now explain…

1. **Why requirements copy before source.** Layer cache is keyed on inputs; order layers
   by how often their inputs change and rebuilds cost seconds instead of minutes.
2. **What `USER agent` buys, and what enforces it.** The process holds no root on the
   shared kernel; `runAsNonRoot` in the Pod spec turns the convention into a gate.
3. **Why the image contains no repo.** The image is the code contract; the repo is
   runtime input. Coupling them means rebuilding the image to review a new commit.

You can now:
- [ ] Read `docker history` output and attribute the size.
- [ ] Demonstrate the cache penalty of a wrong COPY order with `time`.
- [ ] Load an image into kind and verify it from the node's side.

## Next

→ `lab-03-deploy-to-kubernetes.md`: the image is on the node. Write the Secret, the
ConfigMap, the initContainer, and the probes that turn it into a workload.
