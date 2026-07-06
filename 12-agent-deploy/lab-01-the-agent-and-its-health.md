# Lab 01: The agent, and the health surface it owes the cluster

**Goal:** understand the review agent's three source files, then run it as a plain local
process and probe it the way the kubelet will. The lesson lives in the gap between
`/healthz` and `/readyz`: two endpoints, two different questions, and a workload that can
answer yes to one and no to the other.

**Time:** ~40 min · **Cost:** free (local vLLM) or a few cents (Anthropic)

## The problem (why this exists)

Every server you have deployed exposes its health for free: if `curl :8000/v1/models`
answers, vLLM is up. An agent gives you nothing to curl. It is a loop that dials out, and
between calls it is indistinguishable from a hung process. Kubernetes cannot manage what
it cannot ask, so before this agent can become a workload, you owe the cluster an HTTP
surface that answers on its behalf. That surface is `server.py`, and it is less than 150
lines.

## 1. Read the code (twenty minutes, no shortcuts)

Three files in `12-agent-deploy/app/` split the responsibilities:

| File | Responsibility | Read for |
|---|---|---|
| `tools.py` | what the agent may do: read one repo, append one ledger | the `BLOCKED` pattern: the agent cannot read `.env` files out of the repo it reviews |
| `agent.py` | which model, and the review loop | `build_model()` raises at import when no model is configured. A Pod that cannot work should die where the kubelet can see it, at startup, in the log. |
| `server.py` | the HTTP adapter and the optional Discord thread | `STATE`, and which endpoint reads which part of it |

The endpoints, and who they serve:

| Endpoint | Question it answers | Caller |
|---|---|---|
| `GET /healthz` | is the process alive and responding? | liveness probe |
| `GET /readyz` | can it do its job right now? (repo cloned; Discord connected, if enabled) | readiness probe |
| `GET /status` | what has it done? (reviews run, last report, ledger count) | you |
| `POST /review` | run a review now, return the report | you, or anything in the cluster |

Note what `/readyz` checks and what it refuses to check. The repo directory: cheap, local,
load-bearing. The Discord gateway: a flag the connection callback sets. The model endpoint:
absent, on purpose. A readiness probe that calls an LLM burns tokens every five seconds
and turns a slow model into a cascade of endpoint flaps. Readiness checks must be cheap,
local, and honest.

What does `server.py` replace? On your laptop, you. You were the health check: you watched
the terminal, noticed the hang, pressed Ctrl-C. The Flask surface encodes that judgment so
a kubelet can exercise it 24 hours a day.

## 2. Run it as a process

Set up the environment and give the agent a repo to review:

```bash
cd 12-agent-deploy/app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

mkdir -p /tmp/agent-work
git clone --depth 1 https://github.com/labeveryday/claude-put-in-work.git /tmp/agent-work/repo
```

Point it at a model. In-cluster vLLM through a port-forward (the Phase 07 lab-04 bridge):

```bash
kubectl port-forward svc/vllm 8000:8000 &

export REPO_DIR=/tmp/agent-work/repo
export OPENAI_BASE_URL=http://localhost:8000/v1
python server.py
```

No vLLM? `export ANTHROPIC_API_KEY=...` instead and skip the port-forward. The precedence
lives in `agent.py`: `OPENAI_BASE_URL` wins when both are set.

## 3. Probe it like a kubelet

Second terminal:

```bash
curl -s localhost:8080/healthz          # {"ok": true}
curl -s localhost:8080/readyz           # {"ready": true}
curl -s localhost:8080/status | python3 -m json.tool
```

Then trigger a review and watch the first terminal while it runs:

```bash
curl -s -X POST localhost:8080/review | python3 -m json.tool
```

**What you should see:** the report names real files from the repo, and `status` now shows
`reviews_run: 1` with a timestamp. Check the ledger the tool wrote:

```bash
cat /tmp/agent-work/repo/NEW_FEEDBACK.md
```

Each entry has an id, a timestamp, and a `Status: PENDING` line. The agent's one write
path in the world is this file. Everything else it does is read.

Fire a second review while the first is still running and you get `409`: a
`threading.Lock` serializes reviews, because two concurrent passes over one repo produce
interleaved ledger entries. Concurrency control at the application layer, one replica at
the cluster layer (lab 04); the same invariant, enforced twice.

## 4. Break it, then read the two answers

Take the repo away while the process runs:

```bash
mv /tmp/agent-work/repo /tmp/agent-work/repo.gone

curl -s localhost:8080/healthz    # {"ok": true}          <- still alive
curl -s -w '%{http_code}\n' localhost:8080/readyz
                                  # {"ready": false, "problems": [...]} 503
```

Read those two answers together; they are the whole lab. The process is healthy. The
process is useless. Liveness and readiness diverge, and the actions they trigger differ to
match: a failed liveness probe gets the container restarted, a failed readiness probe gets
the Pod removed from Service endpoints until it recovers. Restarting this process would
not bring the repo back, so a liveness failure here would be a lie that causes a restart
loop. The 503 tells the truth: stop routing to me, the fix is elsewhere.

Put it back and watch readiness recover without a restart:

```bash
mv /tmp/agent-work/repo.gone /tmp/agent-work/repo
curl -s localhost:8080/readyz     # {"ready": true}
```

## 5. Optional: the Discord face

With a bot token (Developer Portal: New Application, then Bot, enable Message Content
Intent, invite with the bot scope):

```bash
export DISCORD_BOT_TOKEN=... DISCORD_CHANNEL_ID=...
python server.py
```

The bot greets the channel, replies to messages about the repo, and posts each review.
Watch `/readyz` during the first seconds after startup: it returns 503 until the gateway
connects, because `discord_enabled` adds a condition to readiness. An optional feature,
once enabled, becomes part of the definition of ready.

## Checkpoint: you can now explain…

1. **Why the Flask server exists.** The kubelet speaks HTTP; the agent does not. The
   adapter answers the cluster's two questions on the agent's behalf.
2. **The difference between the two probes, in this workload's terms.** Liveness: the
   process responds. Readiness: the repo is present and the gateway (if enabled) is
   connected. One triggers restarts, the other gates traffic, and confusing them turns a
   missing repo into a restart loop.
3. **What belongs in a readiness check.** Cheap, local, honest signals. A model ping fails
   all three tests.

You can now:
- [ ] Trace each endpoint in `server.py` to the state it reads.
- [ ] Run the agent against vLLM or Anthropic and produce a ledger entry.
- [ ] Predict, before running it, what `mv repo repo.gone` does to each probe.

## Next

→ `lab-02-containerize.md`: the process works; put it in an image whose layers cache in
the right order, and hand it to kind.
