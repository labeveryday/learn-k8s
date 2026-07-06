# Lab 01: Install kagent, where an agent stops being a process and becomes an object

**Goal:** install the kagent controller and read the kinds it adds (`ModelConfig`,
`Agent`, `RemoteMCPServer`) before you create a single one. By the end you'll be able
to say what changes when an agent is a reconciled resource instead of a script, and why
an `Agent` you create today won't run until something the controller needs exists.

**Time:** ~20 min · **Cost:** free (local kind)

## The problem (why this exists)

You already have an agent framework in this repo: `agents/`. It works. But an agent there
is a Python process you babysit. You run `python agent.py`; if it dies, it's gone; if
the box reboots, you restart it by hand. You can't ask the cluster "is my agent healthy,"
you can't roll it back, you can't scale it, and the only way to see what it's doing is the
`print()` statements you remembered to add. None of the operational machinery from
Phases 03–06 (Deployments, restarts, status, RBAC, observability) applies to it,
because it isn't a cluster object. It's a script on a laptop.

That's the gap. The idea of the agent is fine (an LLM + a loop + tools). The operational
model is the problem.

## What it replaces, and the mental model

kagent doesn't replace your agent's logic; it replaces how the agent runs. Same idea,
different operational model:

| | `agents/` (this repo) | kagent |
|---|---|---|
| What an agent *is* | a Python process | a Kubernetes object (`Agent` CR) |
| Who keeps it alive | you, by hand | the kagent controller, by reconcile loop |
| How you observe it | `print()` / stdout | `kubectl describe` status + Pod logs + events |
| Restart / scale / roll back | rewrite the script / babysit | it's a workload, same as a Deployment |
| Config (model, prompt, tools) | code | declarative YAML you `kubectl apply` |

This is the same move Kubernetes made for a binary: a binary you `./run` becomes a
`Deployment` the cluster reconciles. kagent makes that move for an agent. The thing you
declare (`Agent`) is reconciled into a thing that runs and is observable.

```
   agents/agent.py            kagent Agent CR
   (imperative script)   ──►  (declarative object)
        │                          │ controller reconciles ↓
   you run + babysit          a running workload the cluster keeps alive,
                              restarts, exposes, and reports status on
```

## 0. Prereqs

A running kind cluster from Phases 05–06, with vLLM (Phase 06) reachable in-cluster, so
your agents call your model instead of a hosted API.

```bash
kubectl config current-context        # kind-kind
kubectl get nodes
kubectl get svc vllm                  # the model endpoint kagent will call in lab-02
```

If vLLM isn't running, redo `06-ai-gateway/lab-01`.

## 1. Install kagent via Helm (CRDs first, then the controller)

kagent ships in two charts, the CRDs and then the controller: the same split you saw with
kgateway in Phase 05. The reason is the same: a controller that watches custom resources
can't start cleanly unless those resource *types* already exist in the API server. CRDs are
the vocabulary; the controller is the thing that acts on it. Install the vocabulary first.

```bash
helm upgrade -i kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --namespace kagent --create-namespace   # CHART 1: just the CRDs (the API vocabulary)

helm upgrade -i kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent                       # CHART 2: the controller that acts on them
```

- `upgrade -i` (= `--install`) is the idempotent verb: install if absent, upgrade if
  present. Re-running these is safe, the same declarative spirit as `kubectl apply`.
- `oci://ghcr.io/...` pulls the chart straight from a container registry (GitHub Container
  Registry) instead of a classic `helm repo add` URL. kagent ships its charts as OCI
  artifacts, so there's no repo to add first.
- `--create-namespace` makes the `kagent` namespace on the *first* chart; the second reuses it.
  Order is load-bearing: install CRDs before the controller, or the controller crash-loops
  looking for `Agent`/`ModelConfig` types that don't exist yet.

Verify the controller is up:

```bash
kubectl -n kagent get pods
```

**What you should see:** a controller Pod (the kagent deployment) in `Running`, `1/1` ready.
That `1/1` means its single container passed its readiness probe, so the reconcile loop is
live and watching. The second chart also created something you'll use in lab-03 without
authoring it: a built-in MCP tool server. You'll meet it in step 3.

### Install the `kagent` CLI (you'll need it to talk to agents in lab-02/03)

Helm installed the controller; it did not give you a command-line client. Install the CLI
now; it's how you'll invoke agents in labs 02–03. Use the official installer (read
kagent.dev rather than blogs, since kagent moves fast):

```bash
curl -sfL https://kagent.dev/install.sh | bash    # installs the `kagent` CLI
kagent version                                     # confirm it's on your PATH
```

- `curl -sfL`: `-s` silent (no progress bar), `-f` fail on HTTP errors (so a 404 doesn't pipe
  an error page into `bash`), `-L` follow redirects. Piping a remote script to `bash` runs
  unreviewed code; that's fine here because it's the project's own published installer. Read
  it first (`curl -sfL https://kagent.dev/install.sh | less`) if you're cautious.

**What you should see:** `kagent version` prints a version string. If it's "command not found,"
the installer dropped the binary somewhere not on your `PATH`. Its output tells you where.

Don't have it / can't install it? Every `kagent invoke` in this phase has a `curl`
fallback against the port-forwarded API; see lab-02 step 3. The CLI is the convenient
path, not the only one.

## 2. Read the new kinds: the vocabulary, not behavior

```bash
kubectl get crd | grep kagent
```

The three that carry the whole phase:

| Kind | Role | Analogy from earlier phases |
|---|---|---|
| `ModelConfig` | *which* LLM + how to reach it (your vLLM, or a hosted API) | like a connection string, as an object |
| `Agent` | the agent itself: system prompt, which model, which tools | the "Deployment" of the agent world |
| `RemoteMCPServer` | an external MCP server whose tools the agent may call | a registered tool catalog |

> `MCPServer` (from the kmcp subproject) also exists: that's an MCP server kagent *runs for
> you* in-cluster, rather than connecting out to a remote one. You won't need it; the Helm
> install already gave you a `RemoteMCPServer` named `kagent-tool-server` (the built-in
> Kubernetes tools). Confirm it landed:

```bash
kubectl get remotemcpserver -n kagent     # expect: kagent-tool-server
```

Now read the `Agent` schema from the live CRD, never from memory or a blog: kagent moves
fast, and `explain` is always in sync with what you installed (the ask-the-tool-first
habit from Phase 05):

```bash
kubectl explain agent.spec        # the live schema, generated from the CRD you just installed
```

- `explain` reads the OpenAPI schema embedded in the installed CRD, so it can never drift from
  your cluster the way a blog post can. Append `.declarative` to drill in
  (`kubectl explain agent.spec.declarative`) and see exactly which sub-fields the controller honors.

**What to look for:** `spec.type` (this is `Declarative` for a controller-run agent),
`spec.declarative` (where `modelConfig`, `systemMessage`, and `tools` live), and
`spec.description` at the top level. That nesting is the shape every manifest in this phase
uses; get it from `explain`, not from this lab's prose.

## 3. Break it: create an Agent with no model, then read why it won't run

Spec is not behavior. A `CustomResourceDefinition` will store any `Agent` whose
YAML is structurally valid; that doesn't mean the controller can run it. Prove it by
declaring an `Agent` that points at a `ModelConfig` that doesn't exist yet:

```bash
kubectl apply -n kagent -f - <<'EOF'
apiVersion: kagent.dev/v1alpha2     # the kind kagent's CRD chart added - NOT a built-in K8s type
kind: Agent
metadata:
  name: ghost
  namespace: kagent                 # same namespace as the controller - and where its `vllm` ModelConfig will live (lab-02)
spec:
  description: "An agent with no model - should never become ready."  # human label; surfaces in the kagent UI/CLI
  type: Declarative                 # controller-RUN agent (vs. a BYO/remote agent); selects the `declarative:` block below
  declarative:                       # everything the runtime needs lives here for a Declarative agent
    modelConfig: does-not-exist     # by NAME, a ModelConfig in this namespace - and it doesn't exist (the whole point)
    systemMessage: "I will never run."  # the agent's system prompt - fine on its own; useless without a model to send it to
EOF

kubectl get agent ghost -n kagent
kubectl describe agent ghost -n kagent
```

Dissecting the two fields that decide this Agent's fate:

- **`type: Declarative`** is the switch. It tells the controller to run this agent itself,
  and that's what makes `modelConfig` mandatory: a controller-run agent needs a model to
  run with. (`explain agent.spec.type` lists the other values; Declarative is what every
  agent in this phase uses.)
- **`modelConfig: does-not-exist`** is a reference by name, not an inline config: the same
  late binding you saw with Services selecting Pods by label. The reference is structurally
  valid YAML, so the API server stores it. Whether the named `ModelConfig` exists is
  not checked at admission; the controller checks it later, in the reconcile loop,
  which is why a valid-looking Agent can still never run.

Beginner gotcha: nothing here is malformed, so `apply` succeeds and prints `agent.kagent.dev/ghost created`.
"It applied" tells you the shape was accepted, never that the thing will run. The next two
commands tell you whether it will.

Read the status, don't skim it. Under `status.conditions` the Agent reports itself
not ready, with a reason pointing at the missing/unresolved `ModelConfig`. The object
exists; the API server accepted it; but the controller cannot reconcile it into a running
agent because a dependency it references is absent.

> This is the most important habit in the phase: **you can't `print()` from a reconciled
> object; you read its conditions.** The same lesson as the Gateway stuck `Accepted=False`
> in Phase 05, and a Pod stuck `Pending` with no node in Phase 03. The YAML is valid; the
> thing that acts on it is missing or unsatisfied, and it tells you so in `status`.

Clean up the dead agent:

```bash
kubectl delete agent ghost -n kagent
```

## What does the controller turn an Agent into?

The kagent controller watches `Agent` objects in the API server. When you `apply` one
whose dependencies resolve (a real `ModelConfig`, valid tools), the reconcile loop turns
that declarative spec into a running workload: a Pod running the kagent agent runtime
(`kagent-adk`; ADK is the Agent Development Kit, the engine that executes the LLM loop: read prompt, call model,
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

Two things to notice, because they're the point of the phase:

1. **It's a workload, not a script.** That Pod is restarted, schedulable, and observable
   like any Deployment Pod. Delete it and the controller rebuilds it. That's the upgrade
   over `python agent.py`.
2. **It rides the machinery you already own.** The model call is plain HTTP to a Service
   ClusterIP, resolved by CoreDNS and DNAT'd by `kube-proxy`: the Phase 03 stack.
   kagent didn't invent a new network; it put an agent on top of the one you built.

kagent is a new top floor on the stack you already understand.

## Checkpoint: you can now explain…

Answer these from what you just did, not as homework:

1. **What problem does kagent solve that `agents/` couldn't?** It makes the agent a
   cluster object: reconciled, restarted, scaled, observable, and RBAC-scoped, none of
   which a laptop Python process gets.
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
an `Agent` that reconciles to `Ready`, and invoke it, watching the `ghost`
problem resolve the instant its model dependency exists.
