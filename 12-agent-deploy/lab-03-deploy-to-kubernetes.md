# Lab 03: Deploy: secrets, an initContainer, and probes that gate traffic

**Goal:** run the agent on kind with its configuration split correctly, its repo cloned
before it starts, and its probes wired to the endpoints you built in lab 01. Then break
it twice and read both failures from the cluster's side: one crash at startup, one
readiness failure at runtime, each visible in a different place.

**Time:** ~45 min · **Cost:** free

## The problem (why this exists)

In lab 02 you ran one `docker run` with a mount and three `-e` flags. That command line
is state in your shell history: undeclared, unrepeatable, invisible to the cluster. This
lab moves every one of those flags into an object the API server stores, so the workload
can be rebuilt from declarations alone. Four manifests in `manifests/` carry the whole
thing; you read them before you apply them.

## 1. Split the configuration by sensitivity

The rule from Phase 03 lab-05: facts go in a ConfigMap, credentials go in a Secret. Read
`manifests/configmap.yaml`: model endpoint, model id, repo URL, clone path. All four are
safe to read aloud. Now create the Secret; every key in it is optional for this workload:

```bash
cd 12-agent-deploy

# Keyless path (vLLM in-cluster): an empty Secret satisfies the reference
kubectl create secret generic review-agent-secrets

# Anthropic instead of vLLM:
#   kubectl create secret generic review-agent-secrets \
#     --from-literal=ANTHROPIC_API_KEY=sk-ant-...
# Discord (optional, add both or neither):
#     --from-literal=DISCORD_BOT_TOKEN=... --from-literal=DISCORD_CHANNEL_ID=...
```

`manifests/secret.example.yaml` documents the shape for reference. The real Secret exists
only as the object you created; nothing with a credential in it touches this repo. If
you use the Anthropic path, also remove `OPENAI_BASE_URL` from the ConfigMap: `agent.py`
prefers it when both are present, and pointing at a vLLM you never deployed produces
failed reviews, not a failed startup.

The Deployment consumes both with `envFrom`, and marks the Secret `optional: true`. Read
that line in `manifests/deployment.yaml` and say what it trades away: the Pod starts with
no Secret at all, which is exactly right for the keyless path, and also means a typo in
the Secret's name fails silent instead of failing the Pod. `kubectl describe pod` would
show the env either way; you accept the trade with your eyes open.

## 2. The initContainer: sequencing as an API

The agent needs `/work/repo` to exist before `server.py` imports. On your laptop you ran
`git clone` first because you knew to. The Pod spec encodes that knowledge:

```yaml
initContainers:
  - name: clone
    image: alpine/git:2.45.2
    command: ["sh", "-c", "git clone --depth 1 $(REPO_URL) $(REPO_DIR) || true"]
```

initContainers run to completion, in order, before any main container starts. The main
container therefore never contains clone logic, retry logic, or git credentials; by the
time Python runs, the repo is there or the Pod never got that far. The `|| true` handles
one specific case: the kubelet restarts the *containers* of a Pod in place (a liveness
failure, an OOM kill), the emptyDir survives that restart, and the second clone into a
populated directory would otherwise fail the init and wedge the Pod.

An emptyDir is Pod-scoped scratch space: born with the Pod, shared by its containers,
destroyed with the Pod. Delete the Pod and the next one re-clones fresh. Hold that
thought; it becomes the ledger problem in lab 04.

## 3. Apply, and watch the sequence

```bash
kubectl apply -f manifests/configmap.yaml -f manifests/deployment.yaml -f manifests/service.yaml
kubectl get pods -l app=review-agent -w
```

**What you should see**, in order: `Init:0/1`, then `PodInitializing`, then `Running` with
`READY 0/1`, then `READY 1/1` once the readiness probe passes. Each transition is one of
the mechanisms you configured. Read the clone's work:

```bash
kubectl logs -l app=review-agent -c clone
kubectl exec deploy/review-agent -- ls /work/repo | head
```

Now use it through the Service:

```bash
kubectl port-forward svc/review-agent 8080:8080 &
curl -s localhost:8080/status | python3 -m json.tool
curl -s -X POST localhost:8080/review | python3 -m json.tool
```

The review runs inside the cluster, against the model Service, over the repo the init
cloned. If you configured Discord, the report also lands in your channel, and `/readyz`
held the Pod out of the Service until the gateway connected.

## 4. Break it at startup: the crash you designed

Remove the model configuration and roll the Pod:

```bash
kubectl patch configmap review-agent-config --type=json \
  -p='[{"op":"remove","path":"/data/OPENAI_BASE_URL"}]'
kubectl rollout restart deploy/review-agent
kubectl get pods -l app=review-agent -w        # CrashLoopBackOff arrives within a minute
```

Read it the only correct way:

```bash
kubectl logs -l app=review-agent --previous | tail -3
# RuntimeError: set OPENAI_BASE_URL (vLLM) or ANTHROPIC_API_KEY
```

`--previous` is the habit this exercise installs: the crashed container's logs, not the
current attempt's. The `RuntimeError` you wrote in `agent.py` surfaces as a one-line
diagnosis with backoff doing no harm. Compare the alternative design honestly: a lazy
model check would have produced a `Running 1/1` Pod that fails every review, and you
would be reading application logs at 2am wondering why the cluster says everything is
fine. Restore it:

```bash
kubectl patch configmap review-agent-config --type=merge \
  -p='{"data":{"OPENAI_BASE_URL":"http://vllm.default.svc.cluster.local:8000/v1"}}'
kubectl rollout restart deploy/review-agent
```

## 5. Break it at runtime: readiness gates the Service

Watch endpoints in one terminal:

```bash
kubectl get endpoints review-agent -w
```

In another, take the repo out from under the running agent:

```bash
kubectl exec deploy/review-agent -- rm -rf /work/repo/.git
```

Within one probe period (five seconds) the readiness probe starts returning 503, the Pod
flips to `READY 0/1`, and the endpoints list empties. No restart happened: `kubectl get
pods` shows `RESTARTS 0`, because liveness kept passing the whole time. The cluster's
response to "alive but not able" is to stop sending traffic, the exact behavior you
argued for in lab 01 when you kept the repo check out of `/healthz`.

Recovery, and one more lesson with it: a container restart would not re-clone (init
already ran for this Pod), so delete the Pod and let the ReplicaSet build a fresh one,
init and all:

```bash
kubectl delete pod -l app=review-agent
kubectl get endpoints review-agent -w      # empty, then repopulated at READY 1/1
```

## Checkpoint: you can now explain…

1. **Where each piece of configuration lives, and why.** Facts in the ConfigMap,
   credentials in the Secret you created imperatively, and what `optional: true` on the
   Secret reference trades for the keyless path.
2. **What an initContainer guarantees, and what an emptyDir does not.** Ordering: the
   main container starts only after the clone completes. Persistence: none beyond the
   Pod's life.
3. **The two failure planes, and where to read each.** Startup failure:
   `CrashLoopBackOff`, read `kubectl logs --previous`. Runtime capability failure:
   `READY 0/1` with empty endpoints, read `/readyz` and the events. Restarts fix the
   first plane and are useless on the second.

You can now:
- [ ] Recreate the whole deployment from the manifests directory plus one `kubectl create secret`.
- [ ] Trace `Init:0/1 → PodInitializing → 0/1 → 1/1` to the specific mechanism behind each transition.
- [ ] Diagnose a CrashLoopBackOff without guessing, on the first `logs --previous`.

## Next

→ `lab-04-identity-state-and-rollouts.md`: it runs. Now scale it to two, watch the
identity split, and fix the rollout and the ledger so operating it stops being luck.
