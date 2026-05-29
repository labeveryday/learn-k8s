# Lab 02 — ModelConfig → Agent → invoke: debugging a thing you can't print into

**Goal:** point kagent at the vLLM *you* serve with a `ModelConfig`, define an `Agent` that
reconciles all the way to `Ready`, and invoke it — then learn the core skill of this phase:
debugging an agent by reading its **status conditions** instead of `print()`.

**Time:** ~30 min · **Cost:** free (local kind)

## The problem (why this exists)

In lab-01 you proved an `Agent` won't run without a model it can resolve — the `ghost`
sat not-ready forever. So an `Agent` is only half the picture. It needs to know *which*
model and *how to reach it*, and that pointer has to be a cluster object too (otherwise
you're back to hardcoding endpoints in a script). That object is `ModelConfig`.

You also have a constraint the hosted-API tutorials don't: you don't want to send your
agent's traffic to someone else's API. You already serve a model in-cluster from Phase 06.
The whole sovereignty story collapses if kagent can only talk to OpenAI's cloud.

## What it replaces, and why the old way was insufficient

In `agents/`, the model connection is **code** — a base URL and key baked into the Python.
Change models and you edit and redeploy the script. There's no shared, inspectable record
of "this is the model my agents use." `ModelConfig` makes that connection a **declarative
object** instead:

- One `ModelConfig`, many agents reference it. Repoint it once, every agent follows.
- It's inspectable (`kubectl get/describe`) and RBAC-scoped, not buried in source.
- Because vLLM speaks the **OpenAI API** (Phase 06), kagent treats it as just another
  `provider: OpenAI` endpoint — you only swap the `baseUrl`. No special "self-hosted" mode.

That last point is the unlock: `ModelConfig` can aim at **any OpenAI-compatible endpoint**.
Here it's your in-cluster vLLM at `http://vllm.default.svc.cluster.local:8000/v1`.

## 1. Tell kagent about your model

`manifests/modelconfig-vllm.yaml` is **two objects in one file** (`---` splits them) — a
`Secret` holding the API key and the `ModelConfig` that references it. Here's the whole
thing, then the fields that carry the weight:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: vllm-api-key            # the ModelConfig will reference this by name (apiKeySecret)
  namespace: kagent             # MUST be the same namespace as the ModelConfig — Secrets don't cross namespaces
type: Opaque
stringData:
  # Local vLLM ignores the key, but the ModelConfig schema requires the field to exist.
  api-key: "not-needed-for-local-vllm"   # stringData = plain text in; the apiserver base64-encodes it for you
---
apiVersion: kagent.dev/v1alpha2  # same API group/version as the Agent — kagent's own CRDs
kind: ModelConfig
metadata:
  name: vllm                     # the Agent names THIS in declarative.modelConfig (section 2)
  namespace: kagent
spec:
  provider: OpenAI               # "speak the OpenAI wire protocol" — NOT "use api.openai.com"
  model: "Qwen/Qwen2.5-0.5B-Instruct"   # must EXACTLY match what your vLLM serves, or the call 404s
  apiKeySecret: vllm-api-key     # which Secret holds the key (the one above)
  apiKeySecretKey: api-key       # which KEY inside that Secret's data — i.e. Secret.stringData.api-key
  openAI:
    baseUrl: "http://vllm.default.svc.cluster.local:8000/v1"  # the endpoint — your in-cluster vLLM, not the cloud
```

The load-bearing idea is the split between `provider` and `baseUrl`. `provider: OpenAI`
selects the **protocol** (kagent will format requests the OpenAI way); `baseUrl` selects the
**destination**. Together they say "talk OpenAI-style to *this* host." That's why there's no
special "self-hosted" mode — your vLLM is just another OpenAI endpoint at a different URL.

Two beginner gotchas, both silent:

- **`apiKeySecret` / `apiKeySecretKey` are two different things.** The first names the Secret
  *object* (`vllm-api-key`); the second names the *key inside it* (`api-key`). Mismatch either
  and the controller can't read the key — even though, for local vLLM, the value is a dummy.
  The field is still mandatory: this is the "config that must be present even when unused"
  pattern from Phase 03.
- **The Secret must live in `namespace: kagent`,** the same namespace as the `ModelConfig`.
  A `ModelConfig` can't reference a Secret in another namespace; put it in `default` and the
  controller will report it can't find the key.

```bash
kubectl apply -f manifests/modelconfig-vllm.yaml   # applies BOTH objects (Secret + ModelConfig) in one call
```

> The `model:` string must **exactly** match what your Phase 06 vLLM actually serves, or
> the call 404s. Confirm with `kubectl exec` or `curl http://localhost:8000/v1/models`
> (after a `port-forward svc/vllm 8000:8000`) and edit the manifest if yours differs.

```bash
kubectl get modelconfig -n kagent
kubectl describe modelconfig vllm -n kagent
```

**What to look for:** the `baseUrl` resolves to your vLLM Service and the referenced Secret
exists. `ModelConfig` is config, not a workload — there's no Pod behind it. It's a pointer
the controller hands to any `Agent` that names it.

> **Platform tie-in:** point `baseUrl` at your Phase 06 *gateway* host instead of vLLM
> directly, and every agent call inherits your token limits and prompt guards for free —
> the agent doesn't even know it's behind a gateway.

## 2. Define an Agent that can actually resolve

`manifests/agent-helper.yaml` is a complete agent expressed as one Kubernetes object — no
Python, no Dockerfile. Read it before applying:

```yaml
apiVersion: kagent.dev/v1alpha2  # v1alpha2: agents are typed (the type field below picks the flavor)
kind: Agent
metadata:
  name: k8s-helper               # the name you'll invoke in section 3
  namespace: kagent
spec:
  description: "A concise Kubernetes helper backed by local vLLM."  # stays on spec, NOT under declarative
  type: Declarative              # controller-run agent — kagent builds + runs the runtime Pod for you
  declarative:                   # everything the managed runtime needs lives in this block
    modelConfig: vllm            # names the ModelConfig from section 1 — THE link that makes this resolve
    systemMessage: |             # the prompt baked into the agent; '|' keeps the newlines
      You are a concise Kubernetes assistant. Answer in one or two sentences.
      Prefer correctness over completeness. If unsure, say so.
```

This is the same `Agent` shape as the `ghost` from lab-01 — `type: Declarative`, with
`modelConfig` and `systemMessage` nested under `declarative:`, and `description` at the
top of `spec` — but this time `modelConfig: vllm` names a `ModelConfig` that **exists**.
That single difference is what lets the controller reconcile it.

Gotchas worth internalizing here:

- **`modelConfig: vllm` is a name reference, not a URL.** It must match a `ModelConfig` named
  `vllm` in the same namespace (the one you applied in section 1). Reference a name that
  doesn't exist and you've recreated the `ghost` — the Agent sits not-ready forever.
- **`type: Declarative` is what makes this a managed workload.** It tells the controller to
  stand up and run the agent runtime for you; the `declarative:` block is *only* read for that
  type. `description` deliberately sits on `spec`, one level up — a common place to misnest.

```bash
kubectl apply -f manifests/agent-helper.yaml      # create the Agent object; the controller does the rest
kubectl get agent -n kagent
kubectl describe agent k8s-helper -n kagent      # watch status go Ready
```

**What to look for — and this is the lab's core skill:** in `status.conditions`, the Agent
moves to **ready/accepted**. Contrast it directly with the `ghost` you described in lab-01:
*same kind, same controller* — the only thing that changed is whether the model dependency
resolved. The conditions are the controller narrating its reconcile out loud. When an agent
misbehaves later, this is the first place you look, because:

> You can't `print()` into a reconciled object. You read its conditions. The controller
> already wrote down why it could or couldn't run your agent — your job is to read it,
> exactly like the Gateway `Programmed` status in Phase 05.

## 3. Talk to it — no Python process anywhere

kagent exposes agents through its API/UI; the controller proxies your request to the agent
runtime Pod. The Helm install created a Service named `kagent` listening on port 80 that
fronts that invocation API — see it with `kubectl -n kagent get svc kagent`. Forward it to
local 8081 (8080 is taken by the Phase 06 gateway) and invoke:

```bash
kubectl -n kagent port-forward svc/kagent 8081:80 &   # local 8081 → Service port 80; '&' backgrounds it so you can type the next command

# Primary path — the kagent CLI you installed in lab-01:
kagent invoke --agent k8s-helper --task "In one sentence, what is a Kubernetes Service?"

# No CLI? Fallback: hit the same port-forwarded API with curl:
# curl -s http://localhost:8081/api/agents/k8s-helper/invoke \
#   -H 'Content-Type: application/json' -d '{"task":"In one sentence, what is a Kubernetes Service?"}'

kill %1 2>/dev/null                                   # %1 = the backgrounded port-forward — stop it when done
```

**What to look for:** a one-sentence answer that matches the `systemMessage`'s "concise"
instruction. Trace what just happened: your request hit the controller, which routed it to
the agent runtime Pod, whose LLM loop called your vLLM over the `ModelConfig.baseUrl`, and
the answer came back up the same path. You never ran a Python process. The reasoning
happened **in the cluster, on your model**. That's the entire point of Phase 07.

## 4. Observe it like any workload

```bash
kubectl -n kagent logs deploy/kagent --tail=50      # logs from the controller Pod (deploy/ picks its current Pod); last 50 lines
kubectl get events --sort-by=.lastTimestamp | tail  # cluster events oldest→newest, so the freshest are at the bottom
```

**What to look for:** lines mentioning your agent name (`k8s-helper`) and an inbound
invocation/request around the timestamp you ran the call — the controller logging that it
routed your task and got a response back. If nothing references the invocation, the call
never reached the controller: re-check the port-forward is still alive and you hit 8081.
This is the practical payoff of "agent as object": when something goes wrong, there's a
paper trail in the cluster, not just a terminal you closed.

## Break it: point the model at the wrong port, then read the failure

The agent reconciled fine, so where does a *runtime* failure surface — and how is it
different from the lab-01 *reconcile* failure? Find out. Edit the `ModelConfig` `baseUrl`
to a wrong port and re-apply, then invoke again:

```bash
kubectl describe agent k8s-helper -n kagent         # conditions: likely STILL clean — the spec is valid
kubectl -n kagent logs deploy/kagent --tail=50      # the logs: THIS is where the runtime connection error shows up
```

**Read the error, that's the lesson.** The `Agent` still looks structurally fine — its spec
is valid and the `ModelConfig` it references still exists, so the conditions may not even
complain. But the *invocation* fails: the agent runtime can't open a connection to the
model, and you'll see an upstream connection error in the logs.

This teaches the architecture: there are **two distinct failure planes**.

| Plane | Failure looks like | Where you read it | lab |
|---|---|---|---|
| Reconcile | object can't be turned into a workload | `kubectl describe` conditions | 01 (missing `ModelConfig`) |
| Runtime | workload runs but a call fails | Pod / controller **logs** | 02 (wrong `baseUrl` port) |

This is the same "gateway healthy, model unreachable" lesson from Phase 06 — the control
plane is satisfied (the config is valid) while the data plane fails (the model won't
answer). Restore the correct `baseUrl` and re-apply before moving on.

## Checkpoint — you can now explain…

1. **What does `ModelConfig` give you that a hardcoded URL didn't?** A shared, inspectable,
   RBAC-scoped, swap-once model connection that any agent can reference — and it can point
   at *any* OpenAI-compatible endpoint, including your own vLLM.
2. **How do you debug an agent you can't `print()` into?** Read `status.conditions` for
   reconcile problems; read Pod/controller **logs** for runtime problems. They're different
   planes and they fail differently.
3. **What actually happened on `invoke`?** controller → agent runtime Pod → LLM loop →
   `ModelConfig.baseUrl` (your vLLM) → back. No local process at any point.

You can now:
- [ ] Write a `ModelConfig` aimed at an OpenAI-compatible endpoint and reference it from an
      `Agent`.
- [ ] Explain why the `ghost` from lab-01 failed and `k8s-helper` succeeds.
- [ ] Tell a reconcile failure (conditions) apart from a runtime failure (logs).

## Next

→ `lab-03-agent-with-tools.md`: this agent can only *talk*. Give it a real **tool** via the
built-in `RemoteMCPServer` so it can *act* — and watch the agent's loop decide to call a
tool, route through MCP, and reason over the result.
