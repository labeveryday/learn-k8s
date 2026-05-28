# Lab 01 — Install kagent: an agent stops being a process, becomes an object

**Goal:** install the kagent controller and *read* the kinds it adds — `ModelConfig`,
`Agent`, `RemoteMCPServer` — before you create a single one. By the end you'll be able
to say what changes when an agent is a reconciled resource instead of a script, and why
an `Agent` you create today won't run until something the controller needs exists.

**Time:** ~20 min · **Cost:** free (local kind)

## The problem (why this exists)

You already have an agent framework in this repo: `agents/`. It works. But an agent there
is a **Python process you babysit**. You run `python agent.py`; if it dies, it's gone; if
the box reboots, you restart it by hand. You can't ask the cluster "is my agent healthy,"
you can't roll it back, you can't scale it, and the only way to see what it's doing is the
`print()` statements you remembered to add. None of the operational machinery you spent
Phases 03–06 learning — Deployments, restarts, status, RBAC, observability — applies to it,
because it isn't a cluster object. It's a script on a laptop.

That's the gap. The *idea* of the agent is fine (an LLM + a loop + tools). The *operational
model* is the problem.

## What it replaces, and the mental model

kagent doesn't replace your agent's logic — it replaces **how the agent runs**. Same idea,
different operational model:

| | `agents/` (this repo) | kagent |
|---|---|---|
| What an agent *is* | a Python process | a Kubernetes object (`Agent` CR) |
| Who keeps it alive | you, by hand | the kagent controller, by reconcile loop |
| How you observe it | `print()` / stdout | `kubectl describe` status + Pod logs + events |
| Restart / scale / roll back | rewrite the script / babysit | it's a workload — same as a Deployment |
| Config (model, prompt, tools) | code | declarative YAML you `kubectl apply` |

This is the exact same move Kubernetes made for a binary: a binary you `./run` becomes a
`Deployment` the cluster reconciles. kagent makes that move for an *agent*. The thing you
declare (`Agent`) is reconciled into a thing that runs and is observable.

```
   agents/agent.py            kagent Agent CR
   (imperative script)   ──►  (declarative object)
        │                          │ controller reconciles ↓
   you run + babysit          a running workload the cluster keeps alive,
                              restarts, exposes, and reports status on
```

## 0. Prereqs

A running kind cluster from Phases 05–06, with vLLM (Phase 06) reachable in-cluster — your
agents will call **your** model, not a hosted API.

```bash
kubectl config current-context        # kind-kind
kubectl get nodes
kubectl get svc vllm                  # the model endpoint kagent will call in lab-02
```

If vLLM isn't running, redo `06-ai-gateway/lab-01`.

## 1. Install kagent via Helm (CRDs first, then the controller)

kagent ships in two charts — the CRDs, then the controller — the **same split you saw with
kgateway in Phase 05**. The reason is the same: a controller that watches custom resources
can't start cleanly unless those resource *types* already exist in the API server. CRDs are
the vocabulary; the controller is the thing that acts on it. Install the vocabulary first.

```bash
helm upgrade -i kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --namespace kagent --create-namespace

helm upgrade -i kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent
```

Verify the controller is up:

```bash
kubectl -n kagent get pods
```

**What to look for:** a controller Pod (the kagent deployment) in `Running`, `1/1` ready.
The second chart also created something you'll use in lab-03 without authoring it — a
built-in MCP tool server. You'll meet it in step 3.

## 2. Read the new kinds — the vocabulary, not behavior

```bash
kubectl get crd | grep kagent
```

The three that carry the whole phase:

| Kind | Role | Analogy from earlier phases |
|---|---|---|
| `ModelConfig` | *which* LLM + how to reach it (your vLLM, or a hosted API) | like a connection string, as an object |
| `Agent` | the agent itself — system prompt, which model, which tools | the "Deployment" of the agent world |
| `RemoteMCPServer` | an external MCP server whose tools the agent may call | a registered tool catalog |

> `MCPServer` (from the kmcp subproject) also exists: that's an MCP server kagent *runs for
> you* in-cluster, rather than connecting out to a remote one. You won't need it — the Helm
> install already gave you a `RemoteMCPServer` named `kagent-tool-server` (the built-in
> Kubernetes tools). Confirm it landed:

```bash
kubectl get remotemcpserver -n kagent     # expect: kagent-tool-server
```

Now read the `Agent` schema from the *live CRD* — never from memory or a blog, because
kagent moves fast and `explain` is always in sync with what you actually installed
(Kelsey's rule from Phase 05):

```bash
kubectl explain agent.spec
```

**What to look for:** `spec.type` (this is `Declarative` for a controller-run agent),
`spec.declarative` (where `modelConfig`, `systemMessage`, and `tools` live), and
`spec.description` at the top level. That nesting is the shape every manifest in this phase
uses — get it from `explain`, not from this lab's prose.

## 3. Break it: create an Agent with no model, then read why it won't run

Spec is not behavior. A `CustomResourceDefinition` will happily *store* any `Agent` whose
YAML is structurally valid — that doesn't mean the controller can run it. Prove it by
declaring an `Agent` that points at a `ModelConfig` that doesn't exist yet:

```bash
kubectl apply -n kagent -f - <<'EOF'
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: ghost
  namespace: kagent
spec:
  description: "An agent with no model — should never become ready."
  type: Declarative
  declarative:
    modelConfig: does-not-exist
    systemMessage: "I will never run."
EOF

kubectl get agent ghost -n kagent
kubectl describe agent ghost -n kagent
```

**Read the status, don't skim it.** Under `status.conditions` the Agent reports itself
**not ready**, with a reason pointing at the missing/unresolved `ModelConfig`. The object
exists; the API server accepted it; but the controller cannot reconcile it into a running
agent because a dependency it references is absent.

> This is the most important habit in the phase: **you can't `print()` into a reconciled
> object — you read its conditions.** The same lesson as the Gateway stuck `Accepted=False`
> in Phase 05, and a Pod stuck `Pending` with no node in Phase 03. The YAML is valid; the
> thing that *acts* on it is missing or unsatisfied, and it tells you so in `status`.

Clean up the dead agent:

```bash
kubectl delete agent ghost -n kagent
```

## Under the hood (MIT hat): what does the controller turn an Agent into?

The kagent controller **watches** `Agent` objects in the API server. When you `apply` one
whose dependencies resolve (a real `ModelConfig`, valid tools), the reconcile loop turns
that declarative spec into a **running workload**: a Pod running the kagent agent runtime
(`kagent-adk`, the engine that actually executes the LLM loop — read prompt, call model,
maybe call a tool, repeat). The controller also proxies invocation traffic to that Pod, so
you talk to the agent through kagent rather than reaching the Pod directly.

```
Agent CR (declarative: model + prompt + tools)
   │  kagent controller reconciles ↓
Agent runtime Pod (kagent-adk runs the LLM loop)
   │  the loop calls ↓
ModelConfig.baseUrl  ─►  your Phase 06 vLLM  (OpenAI-compatible /v1)
        ▲ new top floor                ▲ the model server you already run
```

Two things to notice, because they're the entire point of the phase:

1. **It's a workload, not a script.** That Pod is restarted, schedulable, and observable
   like any Deployment Pod. Delete it and the controller rebuilds it. *That's* the upgrade
   over `python agent.py`.
2. **It rides the machinery you already own.** The model call is just HTTP to a Service
   ClusterIP — resolved by CoreDNS, DNAT'd by `kube-proxy`, exactly the Phase 03 stack.
   kagent didn't invent a new network; it put an agent on top of the one you built.

kagent isn't magic — it's a new top floor on the stack you already understand.

## Checkpoint — you can now explain…

Answer these from what you just did, not as homework:

1. **What problem does kagent solve that `agents/` couldn't?** It makes an agent a
   first-class cluster object: reconciled, restarted, scaled, observable, and RBAC-scoped —
   none of which a laptop Python process gets.
2. **What does it replace, and what was insufficient about it?** It replaces the imperative
   "run and babysit a process" model. That model has no status, no restart, no rollback,
   and no observability beyond `print()`.
3. **What does the controller turn an `Agent` into?** A Pod running the agent runtime
   (`kagent-adk`) that executes the LLM loop and calls the model named by its `ModelConfig`
   over plain HTTP.

You can now:
- [ ] Name `ModelConfig` / `Agent` / `RemoteMCPServer` and what each is for.
- [ ] Explain why an `Agent` referencing a missing `ModelConfig` never becomes ready, and
      where that's reported.
- [ ] Describe the process→object shift and why it's the same move K8s made for binaries.

## Next

→ `lab-02-modelconfig-agent.md`: create a real `ModelConfig` pointing at your vLLM, define
an `Agent` that actually reconciles to `Ready`, and invoke it — watching the `ghost`
problem resolve the instant its model dependency exists.
