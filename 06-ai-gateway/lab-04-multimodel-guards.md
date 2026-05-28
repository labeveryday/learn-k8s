# Lab 04 — Multi-model routing & prompt guards

> Route one endpoint to *two* models by the name inside the request body, and reject bad prompts before they reach any model. The two AI-gateway features that plain routing can't express.

**Time:** ~25 min · **Cost:** free (local kind)

## The problem

A real AI platform exposes **one** stable URL but fans out to many models behind it — your
cheap local vLLM for easy questions, a bigger model for hard ones — and the client code
never changes which model it's talking to. And it must not let a PII-laden or malicious
prompt reach *any* of those models. Try to build either with what you know:

- **Routing.** Gateway API matches on **path**, **host**, and **headers**. The thing that
  says *which model the caller wants* is the `model` field — and that lives in the **JSON
  request body**. A route match can't read a body field. So body-based routing isn't a
  config knob you flip; you have to get the body's value somewhere the router can see it.
- **Guarding.** You could make every model defend itself against bad prompts, but that's N
  copies of the same filter, drifting out of sync. The prompt is in the body too, and the
  one place that sees every request *before* it forks to a model is the gateway.

Both are gateway-layer concerns, and both require the gateway to act on the **payload**,
not the envelope. That's the leap this lab teaches.

## What it replaces, and why path/host routing wasn't enough

Phase 05 routing keys off the request *envelope*: the path and the `Host` header are
visible the instant the request arrives, before the body is even read. That's cheap and it
covers most web traffic. It cannot cover LLM routing, because two requests to the *same*
URL (`POST /v1/chat/completions`, `Host: multimodel.example.com`) must go to *different*
backends depending on a string buried in their bodies. The envelope is identical; only the
payload differs.

So the gateway has to do something an HTTP router normally never does: **parse the body
before it picks a route.** That reordering — read payload, *then* route — is the whole
mechanism of Part A.

## Under the hood (MIT hat): turning a body field into a routable header

agentgateway can't make an `HTTPRoute` match on `json(request.body).model` directly —
route matching operates on headers. So it splits the job into two phases and bridges them
with a header it invents on the fly:

```
curl  body: {"model":"TinyLlama/...","messages":[...]}
  │
  ▼
Gateway "http"
  │   ┌─ PHASE 1: PreRouting AgentgatewayPolicy (targets the GATEWAY) ─┐
  │   │  transformation.request.set:                                    │
  │   │     x-llm-model = json(request.body).model                      │
  │   │  → injects header  x-llm-model: TinyLlama/...                   │
  │   └─────────────────────────────────────────────────────────────── ┘
  │
  │   ┌─ PHASE 2: HTTPRoute rules match the NEW header ─┐
  │   │  x-llm-model ~ ^Qwen/.*       → vllm-ai          │
  │   │  x-llm-model ~ ^TinyLlama/.*  → vllm-b-ai        │
  │   └───────────────────────────────────────────────── ┘
  ▼
the matched AgentgatewayBackend  →  the right vLLM
```

Two constraints make or break this, and they're the lesson:

1. **`spec.traffic.phase: PreRouting`.** The transformation must run *before* route
   selection. A default-phase transformation runs *after* the router already picked a rule
   — too late; the header it sets can't influence a decision that's already made.
2. **The policy targets the `Gateway`, not the route.** Route selection happens at the
   Gateway level, so the header has to exist before any route is chosen. A policy attached
   to a route runs only *after* that route was selected — same too-late problem.

Get either wrong and there's no error — every request just falls through to whichever rule
matches first, silently. That's why this is non-trivial: the failure mode is wrong routing,
not a crash.

For **Part B**, the mechanism is simpler but the location is the point: a prompt guard is a
**regex evaluated against the prompt at the gateway, before the request is forwarded.** One
policy in front of the fork protects every model behind it at once — no per-model filters
to keep in sync.

## Part A — Multi-model routing

### Step 1 — A second backend

```bash
kubectl apply -f manifests/vllm-second-model.yaml
kubectl rollout status deploy/vllm-b --timeout=900s
```

This deploys a second vLLM (`TinyLlama/TinyLlama-1.1B-Chat-v1.0`) as Service `vllm-b`.
Distinct from Qwen so you can *see* which one answered.

**What to look for:** the same slow Running→Ready flip from lab-01 — it's a second CPU
model loading weights. Don't move on until `rollout status` reports success; an unready
`vllm-b` would surface later as a `502` on the TinyLlama route only, which is a confusing
way to learn the model wasn't up.

### Step 2 — Route by the body's `model` value

```bash
kubectl apply -f manifests/kgateway-multimodel-route.yaml
```

That one file is the whole mechanism from the diagram above. It bundles three resources:

1. **A second `AgentgatewayBackend`** (`vllm-b-ai`) pointing at the `vllm-b` Service —
   same shape as lab-02's backend, `host`/`port` siblings of `openai`.
2. **An `AgentgatewayPolicy`** on the **Gateway** with `spec.traffic.phase: PreRouting`,
   whose `spec.traffic.transformation.request.set` lifts `json(request.body).model` into
   the header `x-llm-model` **before** route selection runs.
3. **An `HTTPRoute`** (`llm-multimodel`, host `multimodel.example.com`) with two rules that
   `RegularExpression`-match `x-llm-model` and send each to a different backend:

```bash
# model: "Qwen/Qwen2.5-0.5B-Instruct"          (x-llm-model ^Qwen/.*)      -> vllm-ai   (Service vllm)
# model: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"   (x-llm-model ^TinyLlama/.*) -> vllm-b-ai (Service vllm-b)
```

**What to look for:** `kubectl describe httproute llm-multimodel` → `Accepted=True` and
both backendRefs `ResolvedRefs=True`. The route accepting tells you the *rules* are valid;
it does **not** prove the PreRouting header injection works — there's no condition for
that. You confirm the wiring by behavior, in Step 3.

> Why a distinct hostname (`multimodel.example.com`)? The single-model route from lab-02
> already owns `/v1/chat/completions` on `llm.example.com`. A different host keeps the two
> HTTPRoutes from colliding on the same path.

### Step 3 — Prove the fan-out

```bash
kubectl port-forward -n kgateway-system svc/http 8080:80 &
for m in Qwen/Qwen2.5-0.5B-Instruct TinyLlama/TinyLlama-1.1B-Chat-v1.0; do
  echo "== $m =="
  curl -s http://localhost:8080/v1/chat/completions -H 'Host: multimodel.example.com' \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":8}" | python3 -m json.tool
done
kill %1 2>/dev/null
```

**What to look for:** two responses whose `model` field echoes back the model you asked
for — Qwen for the first call, TinyLlama for the second. Same URL, same host, same path;
*only the body changed*, and it landed on different backends. That is body-based routing
working: the PreRouting policy turned the body's `model` into `x-llm-model`, and the
HTTPRoute matched it. Add a hosted provider as a third backend + rule and client code
never changes — provider abstraction paying off. (Kong's equivalent is a second `ai-proxy`
plugin on a second route.)

## Part B — Prompt guards

### Step 4 — Block a pattern before it hits a model

```bash
kubectl apply -f manifests/kgateway-prompt-guard.yaml
```

This `AgentgatewayPolicy` nests its AI config under `spec.backend.ai.promptGuard.request`
(a list of guard rules). The shipped rule rejects prompts containing an SSN-shaped pattern
and attaches to the `llm` route from lab-02 (`llm.example.com`). Two field details that are
the actual lesson here:

- `regex.action: Reject` — **PascalCase.** The CRD enum is `[Mask, Reject]`. Some older
  docs show `action: REJECT` (all-caps); that **fails API validation**. When a doc example
  and the CRD schema disagree, **the schema wins** — trust what `kubectl explain
  agentgatewaypolicy.spec.backend.ai.promptGuard` (and `--dry-run`) tell you over a blog
  snippet.
- `regex.matches` is a **list of objects**, each `{pattern, name}` — not a bare string.

Test both paths:

```bash
kubectl port-forward -n kgateway-system svc/http 8080:80 &
# allowed
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/chat/completions \
  -H 'Host: llm.example.com' -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"what is kubernetes"}],"max_tokens":8}'
# blocked (looks like an SSN)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/chat/completions \
  -H 'Host: llm.example.com' -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"my ssn is 123-45-6789"}],"max_tokens":8}'
kill %1 2>/dev/null
```

**What to look for:** `200` then `403` (or `400`). The blocked request never reached vLLM —
the guard ran in the gateway, *before* the fork to any model. One policy, every model
behind it protected at once. That's the structural win over per-model filtering.

> Try the all-caps mistake on purpose: edit a copy of the manifest to `action: REJECT` and
> `kubectl apply --dry-run=server -f -`. You'll get an enum-validation error naming the
> allowed values `[Mask, Reject]`. That's the CRD schema catching a bad doc example before
> it ever reaches the cluster — exactly why you read the schema, not the blog.

## Break it, then read the error (Kelsey lens)

Make the guard too greedy. Change the `matches[].pattern` to `\d+` (any run of digits),
re-apply, and re-send the *allowed* request — now with a number in it:

```bash
# In kgateway-prompt-guard.yaml, set the pattern to \d+ , re-apply, then:
kubectl port-forward -n kgateway-system svc/http 8080:80 &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/chat/completions \
  -H 'Host: llm.example.com' -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"what is port 8080"}],"max_tokens":8}'
kill %1 2>/dev/null
```

**Read the result.** A perfectly legitimate prompt now gets `403`'d because it mentioned a
number. The config is valid — the regex *works exactly as written*; the bug is in the
**policy**, not the syntax. That's the lesson a guard teaches that a port typo doesn't:
guards are a **precision/recall tradeoff**. Too loose (`\d+`) blocks real users; too tight
lets PII through. There's no "correct" config to validate against — only a tradeoff you
tune and re-test. Restore the SSN pattern (`\b\d{3}-\d{2}-\d{4}\b`) and re-apply.

## What you proved in Phase 06

You put vLLM on the cluster as a workload (lab-01), made the *same* gateway from Phase 05
parse OpenAI traffic and meter it by **tokens** (lab-02), proved the AI-gateway pattern is
portable across vendors with Kong (lab-03), fanned **one endpoint out to two models by a
body field** and **blocked bad prompts at the door** (this lab). The through-line: a plain
gateway counts requests and forwards bytes; an **AI gateway understands the protocol** —
it reads `usage`, routes on `model`, and guards the prompt. That difference is the entire
phase.

## Checkpoint — you can now explain…

- **Why body-based routing is non-trivial.** Route matching reads headers/path/host, not
  the body. You route on the *payload* by first extracting a body field into a header in a
  `PreRouting` phase, then matching that header.
- **Why `phase: PreRouting` and a Gateway target are both required.** The header must
  exist *before* route selection; a later phase or a route-scoped policy injects it too
  late to influence the choice — and fails silently as mis-routing.
- **Why a guard lives at the gateway.** It's a regex on the prompt evaluated before the
  fork, so one policy protects every model — no per-model filter drift.
- **Why the CRD schema beats the docs.** `action: Reject` is PascalCase because the enum is
  `[Mask, Reject]`; the all-caps `REJECT` in some docs fails validation. `kubectl explain`
  / `--dry-run` are ground truth.
- **Why guard tuning has no "correct" answer.** It's precision vs recall; `\d+` proves an
  over-broad pattern blocks legitimate traffic.

You can now:
- [ ] Trace a request from body `model` field → injected header → matched HTTPRoute → backend.
- [ ] State the two PreRouting constraints and predict the silent-mis-route failure.
- [ ] Explain why one gateway guard replaces N per-model filters.
- [ ] Use the CRD enum (not a doc example) to pick `Reject` and validate with `--dry-run`.

## Next

→ **Phase 07**: run *agents* as Kubernetes resources that call this gated, metered,
multi-model vLLM through the very front door you just built.
