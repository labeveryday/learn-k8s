# Lab 04: Delegation, and a hole exactly one workload wide

**Goal:** use the worker agent that lives inside every cell. Hand it a whole task, watch
it work in isolation, and read back the files it produced. Then solve the tension the
worker creates: it needs a model, your egress wall denies everything, and the right fix
is a NetworkPolicy that names one workload (your vLLM) rather than widening an IP range.

**Time:** ~40 min · **Cost:** free with in-cluster vLLM

## The problem (why this exists)

`execute` and `shell` are fine for one step at a time, with your main agent thinking
between each. Some work is not one step: research this, analyze the data, write the
report. Shipping every intermediate result back to the main agent wastes its context and
its turns. The sandbox's answer is a second agent that lives inside the cell, takes the
whole task, and runs it to completion in isolation, and your main agent collects only the
files at the end. That worker needs to call a model, which reopens the egress question you
closed in lab 03, on purpose and precisely.

## 1. Meet the worker

Read `server/runtime/app/worker.py`. It is the Phase 07 agent-with-tools pattern, now
running inside the cell: an `Agent` with `run_python`, `run_shell`, `write_file`, and
`read_file`, plus `http_request` when the session has network. Its tools share the same
kernel and `/workspace` your `execute` calls use, so anything it makes, you can read. The
model comes from `WORKER_MODEL_PROVIDER` / `WORKER_MODEL_ID`, and jobs run in background
threads you poll.

The tension is already visible: the worker calls a model over the network, and lab 03's
default policy denies all egress. A delegated job on a no-network session fails at the
model call. You have three ways to give it a path, and only the last is good:

| Option | What it does | Verdict |
|---|---|---|
| `network=True` | opens the whole public internet to the cell | works, but hands hostile code an exfil path for a model call |
| widen `sandbox-allow-internet` `except` list | pokes IP ranges into the egress wall | brittle: Service IPs change, and you are hand-maintaining CIDRs |
| a policy that names the vLLM workload | one selector-based hole to one Service | precise, and it tracks the workload as pods move |

You build the third.

## 2. Open one hole, by name

`13-agent-sandbox/manifests/egress-to-vllm.yaml` adds egress from every sandbox to the
vLLM pods in the `default` namespace, on port 8000, and nothing else:

```yaml
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: default
          podSelector:
            matchLabels:
              app: vllm
      ports:
        - protocol: TCP
          port: 8000
```

The difference from lab 03's ipBlock approach is the difference between an address and a
name. An ipBlock exception is a number that goes stale when the Service reschedules; this
selector is a description the CNI re-resolves as vLLM's pods come and go. The hole is
defined by *what it reaches*, not *where that thing currently sits*. Apply it, and point
the worker's model at vLLM through the cluster DNS name:

```bash
kubectl apply -f 13-agent-sandbox/manifests/egress-to-vllm.yaml

# Tell new sessions' workers to use your in-cluster vLLM over the OpenAI protocol
# (Phase 07 base_url bridge). Set these on the manager so they pass through to cells.
kubectl -n strands-sandboxes set env deploy/sandbox-manager \
  WORKER_MODEL_PROVIDER=openai \
  WORKER_MODEL_ID=Qwen/Qwen2.5-0.5B-Instruct \
  OPENAI_API_KEY=EMPTY \
  OPENAI_BASE_URL=http://vllm.default.svc.cluster.local:8000/v1
kubectl -n strands-sandboxes rollout status deploy/sandbox-manager
```

Confirm `OPENAI_BASE_URL` and `OPENAI_API_KEY` are in the manager's
`SANDBOX_PASSTHROUGH_ENV` list (they are, in `manager.yaml`); that is what copies them
into each cell. Note what you did *not* do: you never set `network=True`. The cell still
cannot reach the internet, the metadata endpoint, or your VPC. It can reach one Service.

## 3. Delegate a task

Start a session (no network flag needed now) and hand the worker a small end-to-end job:

```bash
TOK="Authorization: Bearer lab-token"
SID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')

JOB=$(curl -s -X POST localhost:8700/v1/sessions/$SID/jobs -H "$TOK" \
  -H 'Content-Type: application/json' \
  -d '{"task": "Compute the first 20 Fibonacci numbers with Python, save them one per line to fib.txt, then write a one-paragraph explanation to about.md."}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')

# Poll until done
watch -n 3 "curl -s localhost:8700/v1/sessions/$SID/jobs/$JOB -H '$TOK' | python3 -m json.tool"
```

**What you should see:** the job moves from running to done, and its summary names the
files it wrote. Collect them the way your main agent would:

```bash
curl -s "localhost:8700/v1/sessions/$SID/files" -H "$TOK"
curl -s "localhost:8700/v1/sessions/$SID/files/content?path=fib.txt" -H "$TOK"
curl -s "localhost:8700/v1/sessions/$SID/files/content?path=about.md" -H "$TOK"
```

The worker did multi-step work in isolation and left deliverables in the shared
workspace. Your main agent spent one delegate call and one read, not twenty execute
calls, and no untrusted output ever entered its context until you chose to read a file.

## 4. Prove the hole is exactly one workload wide

The worker reached vLLM. Confirm it still cannot reach anything else, from the same cell:

```bash
# vLLM: allowed
kubectl -n strands-sandboxes exec sbx-$SID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('http://vllm.default.svc.cluster.local:8000/v1/models', timeout=5); print('VLLM REACHABLE')"

# the public internet: still denied
kubectl -n strands-sandboxes exec sbx-$SID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)" \
  || echo "INTERNET STILL BLOCKED"

# the metadata endpoint: still denied
kubectl -n strands-sandboxes exec sbx-$SID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('http://169.254.169.254/', timeout=4)" \
  || echo "METADATA STILL BLOCKED"
```

**What you should see:** one reach, two refusals. You gave the worker exactly the access
its job requires and not one address more. That is the principle the whole phase has been
building toward: isolation by default, and every hole cut to the shape of a specific need,
named by workload so it survives the workload moving. Clean up:

```bash
curl -s -X DELETE localhost:8700/v1/sessions/$SID -H "$TOK"
```

(Keep the cluster; section 5 uses it. Teardown is at the end.)

## 5. Multi-agent, which you already built

Look at what sections 3 and 4 add up to: one agent (yours, outside) planned and routed a
task, another agent (the worker, inside a cell) executed it in isolation, and the results
crossed back over a controlled boundary. That is a two-agent system. Multi-agent is not a new
technology you now need to install; it is a topology decision about agents you already have,
and the pattern you built has a name: **supervisor/worker**. The supervisor treats each worker
as a tool. Its "tool call" is `POST /v1/sessions/$SID/jobs`, and the tool's implementation
happens to be another agent.

The mechanism, on your stack, with nothing new: a supervisor that fans three subtasks out to
three cells, then synthesizes. Save as `supervisor.py` (the manager port-forward from lab 02
and the vLLM port-forward from `07-kagent/lab-00` both running):

```python
import requests, time

MGR = "http://localhost:8700/v1"
HDR = {"Authorization": "Bearer lab-token"}
VLLM = "http://localhost:8000/v1"

SUBTASKS = [
    "Write a Python one-liner that sums 1..100 and save it with its output to result.md.",
    "List three properties of prime numbers in result.md.",
    "Compute 2**32 in Python and explain the number's role in computing in result.md.",
]

def delegate(task):
    sid = requests.post(f"{MGR}/sessions", headers=HDR, json={}).json()["session_id"]
    job = requests.post(f"{MGR}/sessions/{sid}/jobs", headers=HDR,
                        json={"task": task}).json()["job_id"]
    return sid, job

def collect(sid, job):
    while requests.get(f"{MGR}/sessions/{sid}/jobs/{job}", headers=HDR).json()["status"] != "done":
        time.sleep(3)
    text = requests.get(f"{MGR}/sessions/{sid}/files/content",
                        headers=HDR, params={"path": "result.md"}).text
    requests.delete(f"{MGR}/sessions/{sid}", headers=HDR)
    return text

handles = [delegate(t) for t in SUBTASKS]          # all three cells start in parallel
results = [collect(s, j) for s, j in handles]

merged = "\n\n---\n\n".join(results)
answer = requests.post(f"{VLLM}/chat/completions", json={
    "model": "Qwen/Qwen2.5-0.5B-Instruct", "max_tokens": 300,
    "messages": [{"role": "user",
                  "content": "Merge these three worker reports into one short summary:\n" + merged}],
}).json()["choices"][0]["message"]["content"]
print(answer)
```

While it runs, watch the topology exist:

```bash
kubectl -n strands-sandboxes get pods
```

**What you should see:** three `sbx-*` pods running at once, one per subtask, then gone as
each `collect()` deletes its session. The supervisor's context only ever holds the three
`result.md` files and the final summary; the workers' intermediate steps (their tool calls,
their failed attempts) died with their cells.

Now the honest ledger. What the topology buys you:

- **Isolation per subtask.** A worker that gets confused, or runs hostile code, cannot touch
  its siblings' workspaces or your supervisor's context. This is the entire architecture of the
  phase, reused: the walls you built in lab 03 are what make parallel delegation safe at all.
- **Parallelism and small contexts.** Three cells work simultaneously, and each worker starts
  with a context containing only its own subtask, not the other two plus your conversation
  history (the memory-horizon problem from `07-kagent/lab-05`, dodged by construction).

What it costs you:

- **Tokens, multiplied.** Every worker runs its own loop against vLLM, and the supervisor pays
  again to synthesize. The Phase 06 token budget on the route is now metering four agents.
- **Serialization through the supervisor.** Workers cannot talk to each other; everything
  merges through one context, and detail is lost at the merge exactly like the compaction
  trade in `07-kagent/lab-05`.
- **Debugging.** "Which of four agents went wrong, in which cell that no longer exists?" is
  answered by traces (Phase 11), not by scrollback. Delete sessions after collecting evidence,
  not before, when a run misbehaves.

**Break it, gently:** give two workers contradictory instructions (one "argue the number 7 is
prime", one "argue the number 7 is composite") and read the supervisor's merged summary. The
synthesis model has to reconcile them, and with a 0.5B model it may pick one without
flagging the conflict. Reconciliation policy (flag disagreements? majority vote? re-ask?) is
supervisor harness, your code, not something the topology solves for you.

Done with the phase? Tear the cluster down:

```bash
kind delete cluster --name sandbox-net
```

## 6. Where this goes

Step back and place the manager next to Phase 07's kagent. Both create pods a controller
supervises; both reconcile desired state (sessions, agents) against running pods; both
reap. The manager is a purpose-built controller with an HTTP API instead of a CRD. On
LKE (Phase 09) this deploys unchanged and the policies enforce for free, because LKE
ships Calico. The honest next question, the one Phase 12 lab-04 also left you with: at
what point does a hand-written pod factory want to become a `Sandbox` CRD with a real
controller? You now have the two reference points to answer it.

## Checkpoint: you can now explain…

1. **Why delegation exists as a pattern.** Multi-step or long or dangerous work runs to
   completion inside one isolated cell; the main agent spends one call and reads files,
   keeping untrusted output out of its context.
2. **Why a selector-based egress hole beats widening an ipBlock.** A selector names a
   workload and the CNI re-resolves it as pods move; an ipBlock names addresses that go
   stale. Same access, opposite maintainability.
3. **What "least privilege" looks like for a network.** The worker reaches exactly one
   Service and still cannot touch the internet, the VPC, or the metadata endpoint.
4. **What supervisor/worker is and what it trades.** The supervisor treats each worker as a
   tool whose implementation is another agent; you gain per-subtask isolation, parallelism,
   and small worker contexts, and you pay in multiplied tokens, loss at the merge, and
   debugging that needs traces.

You can now:
- [ ] Delegate a whole task to the in-cell worker and collect its files.
- [ ] Write a NetworkPolicy that opens one workload-wide egress hole by label.
- [ ] Verify least-privilege egress with one allowed reach and two blocked ones.
- [ ] Fan subtasks out to parallel cells from a plain-Python supervisor and synthesize the
      results through one model call.

## What you proved across Phase 13

You ran a self-hosted code interpreter, took it from a Docker socket to a scoped pod
factory, hardened its cells and tested every wall from inside, caught your CNI ignoring a
policy and fixed it with Calico, and gave an in-cell worker agent exactly one
workload-wide path to your own model. The sandbox was your first workload that is also a
platform, and every mechanism it uses (RBAC, pod IPs, securityContext, NetworkPolicy, the
base_url bridge) was already in your hands from Phases 03, 07, and 12. You did not learn
new primitives here; you learned what it takes to run software that runs other people's
code.
