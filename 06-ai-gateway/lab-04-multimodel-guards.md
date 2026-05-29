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

The routing demo needs a *second* model so you can tell which one answered. This is a plain
Deployment + Service — the same vLLM workload from lab-01, but serving TinyLlama instead of
Qwen (`manifests/vllm-second-model.yaml`):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-b                       # a SECOND vLLM, distinct from lab-01's `vllm`
  namespace: default
  labels:
    app: vllm-b
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-b
  template:
    metadata:
      labels:
        app: vllm-b                  # must match the selector above (lab-03's #1 trap)
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai-cpu:latest-x86_64   # CPU build — no GPU needed; arm64: latest-aarch64
          args:
            - "--model"
            - "TinyLlama/TinyLlama-1.1B-Chat-v1.0"     # DIFFERENT instruct model from Qwen → observable routing
            - "--dtype"
            - "float32"                                # CPU has no bf16/fp16 path; force fp32
            - "--max-model-len"
            - "1024"                                   # tiny context keeps CPU RAM in check
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet: { path: /health, port: 8000 }     # vLLM is "Ready" only after weights load + /health 200s
            initialDelaySeconds: 60
            periodSeconds: 10
            failureThreshold: 60     # 60×10s ≈ 10 min of grace — CPU model load + HF download is slow
          resources:
            requests: { cpu: "2", memory: 4Gi }        # scheduler reserves this — needs a node with room
            limits: { cpu: "4", memory: 8Gi }
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-b                       # stable in-cluster name the AgentgatewayBackend will point at
  namespace: default
  labels:
    app: vllm-b
spec:
  selector:
    app: vllm-b                      # selects the Pods above by label — this is the Service→Pod glue
  ports:
    - name: http
      port: 8000
      targetPort: 8000
```

The readiness probe is the field that matters here: vLLM reports `Ready` only after it has
downloaded weights and `/health` returns 200, which on CPU takes minutes — hence the long
`failureThreshold`. The `app: vllm-b` label appears three times (Deployment selector, Pod
template, Service selector) and they must agree, or the Service routes to nothing.

```bash
kubectl apply -f manifests/vllm-second-model.yaml
kubectl rollout status deploy/vllm-b --timeout=900s   # block up to 15 min for the CPU model to load
```

This deploys a second vLLM (`TinyLlama/TinyLlama-1.1B-Chat-v1.0`) as Service `vllm-b`.
Distinct from Qwen so you can *see* which one answered. The `--timeout=900s` matches the
probe's ~10-min worst case so `rollout status` doesn't give up before the model is up.

**What to look for:** the same slow Running→Ready flip from lab-01 — it's a second CPU
model loading weights. Don't move on until `rollout status` reports success; an unready
`vllm-b` would surface later as a `502` on the TinyLlama route only, which is a confusing
way to learn the model wasn't up.

### Step 2 — Route by the body's `model` value

That one file (`manifests/kgateway-multimodel-route.yaml`) is the whole mechanism from the
diagram above. It bundles **three** resources — read them in the order they fire on a
request: the policy lifts the body field into a header, the route matches the header, the
backend points at a model.

**Resource 1 — the second AI backend.** Identical shape to lab-02's `vllm-ai`, just pointed
at the other Service. `host`/`port` are siblings of `openai` under `spec.ai.provider` because
this is a *self-hosted* endpoint (a hosted provider would drop them and add an auth
secretRef):

```yaml
apiVersion: agentgateway.dev/v1alpha1   # agentgateway data-plane CRD (kgateway 2.2+)
kind: AgentgatewayBackend
metadata:
  name: vllm-b-ai                       # the name HTTPRoute backendRefs will target
  namespace: default
spec:
  ai:
    provider:
      openai:
        model: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"   # what vllm-b serves
      host: vllm-b.default.svc.cluster.local          # the in-cluster Service (sibling of openai, NOT inside it)
      port: 8000
```

**Resource 2 — the PreRouting policy (the heart of the lab).** This is the bridge that makes
body-based routing possible. The two fields the whole thing hinges on — `phase` and the
`Gateway` target — are flagged below:

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: extract-model
  namespace: kgateway-system            # lives WITH the Gateway it targets
  labels:
    app: agentgateway
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway                      # MUST be Gateway, not HTTPRoute — route selection happens here,
      name: http                         #   so the header must exist before any route is chosen
  traffic:
    phase: PreRouting                    # MUST be PreRouting — run the transform BEFORE the router picks a rule
    transformation:                      # agentgateway's request-rewriting feature
      request:
        set:                             # a LIST of {name, value} header writes
          - name: "x-llm-model"
            value: 'json(request.body).model'   # CEL: parse the JSON body, read its `model` field
```

Get either flagged field wrong and there is **no error** — the header just arrives too late
to match, and every request silently falls through to whichever rule matches first. A
default `phase` runs the transform *after* the route is already picked; an `HTTPRoute` target
runs it only *after* that route was selected. Both are the same too-late trap.

**Resource 3 — the HTTPRoute that matches the new header.** Two rules, each
`RegularExpression`-matching `x-llm-model` and sending to a different backend. First match
wins, so order matters:

```yaml
apiVersion: gateway.networking.k8s.io/v1   # the standard Gateway API HTTPRoute (not the agentgateway CRD)
kind: HTTPRoute
metadata:
  name: llm-multimodel
  namespace: default
spec:
  parentRefs:
    - name: http                          # attaches to the same "http" Gateway in kgateway-system
      namespace: kgateway-system
  hostnames:
    - "multimodel.example.com"            # distinct host from lab-02's llm.example.com — avoids path collision
  rules:
    - matches:                            # rule 1: model name starts with Qwen/ → vllm-ai
        - path:
            type: PathPrefix
            value: /v1/chat/completions
          headers:
            - type: RegularExpression     # match the INJECTED header, not the body
              name: x-llm-model
              value: "^Qwen/.*"           # ^ = starts with, .* = then anything
      backendRefs:
        - group: agentgateway.dev         # backendRef is an AgentgatewayBackend, not a plain Service
          kind: AgentgatewayBackend
          name: vllm-ai                   # → Service vllm (Qwen)
    - matches:                            # rule 2: TinyLlama/ → vllm-b-ai
        - path:
            type: PathPrefix
            value: /v1/chat/completions
          headers:
            - type: RegularExpression
              name: x-llm-model
              value: "^TinyLlama/.*"
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayBackend
          name: vllm-b-ai                 # → Service vllm-b (TinyLlama)
```

Beginner gotcha: the route matches `x-llm-model`, a header that **does not exist in the
incoming request** — it's manufactured by Resource 2 a moment earlier. If you forget the
PreRouting policy (or it ran too late), this route is perfectly valid and accepts cleanly,
but the header is empty and nothing matches. Apply all three at once:

```bash
kubectl apply -f manifests/kgateway-multimodel-route.yaml
```

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
kubectl port-forward -n kgateway-system svc/http 8080:80 &   # local 8080 → Gateway Service :80; & = background
for m in Qwen/Qwen2.5-0.5B-Instruct TinyLlama/TinyLlama-1.1B-Chat-v1.0; do
  echo "== $m =="
  curl -s http://localhost:8080/v1/chat/completions -H 'Host: multimodel.example.com' \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":8}" | python3 -m json.tool
done
kill %1 2>/dev/null   # stop the backgrounded port-forward (job %1)
```

- The `Host: multimodel.example.com` header is what selects *this* HTTPRoute over lab-02's
  `llm.example.com` — you're hitting `localhost:8080`, so the host header is the only thing
  telling the gateway which route you mean.
- The loop sends the **same path, same host** twice; the *only* thing that changes is the
  `model` value inside the `-d` JSON body (`$m` is interpolated, which is why the quotes are
  escaped `\"`). That is the entire point: identical envelope, different payload.
- `python3 -m json.tool` just pretty-prints the JSON response so you can read the `model`
  field it echoes back.

**What to look for:** two responses whose `model` field echoes back the model you asked
for — Qwen for the first call, TinyLlama for the second. Same URL, same host, same path;
*only the body changed*, and it landed on different backends. That is body-based routing
working: the PreRouting policy turned the body's `model` into `x-llm-model`, and the
HTTPRoute matched it. Add a hosted provider as a third backend + rule and client code
never changes — provider abstraction paying off. (Kong's equivalent is a second `ai-proxy`
plugin on a second route.)

## Part B — Prompt guards

### Step 4 — Block a pattern before it hits a model

A **prompt guard** blocks unwanted content before it reaches a model — e.g. PII (personally
identifiable information) like a US Social Security Number (SSN), formatted `123-45-6789`.
Unlike Part A's policy, this one attaches to the **`llm` HTTPRoute** from lab-02 (not the
Gateway) and nests its config under `spec.backend.ai` instead of `spec.traffic`. Here is the
whole object (`manifests/kgateway-prompt-guard.yaml`):

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: llm-prompt-guard
  namespace: default
  labels:
    app: agentgateway
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute                  # attaches to a ROUTE here (Part A targeted the Gateway) —
      name: llm                        #   the lab-02 route on llm.example.com
  backend:                             # AI config lives under spec.backend.ai (not spec.traffic)
    ai:
      promptGuard:
        request:                       # a LIST of guard rules evaluated on the inbound prompt
          - response:
              message: "Rejected: request appears to contain a US SSN."   # body returned on a block
            regex:
              action: Reject           # CRD enum is [Mask, Reject] — PascalCase; all-caps REJECT fails validation
              matches:                 # a LIST OF OBJECTS, each {pattern, name} — NOT a bare string
                - pattern: '\b\d{3}-\d{2}-\d{4}\b'   # SSN shape: 3-2-4 digits on word boundaries
                  name: ssn
```

The two field details that are the actual lesson here:

- `regex.action: Reject` — **PascalCase.** The CRD enum is `[Mask, Reject]`. Some older
  docs show `action: REJECT` (all-caps); that **fails API validation**. When a doc example
  and the CRD schema disagree, **the schema wins** — trust what `kubectl explain
  agentgatewaypolicy.spec.backend.ai.promptGuard` (and `--dry-run`) tell you over a blog
  snippet.
- `regex.matches` is a **list of objects**, each `{pattern, name}` — not a bare string. The
  leading `- ` in front of `pattern` is easy to drop; without it the validator rejects the
  manifest. (For masking instead of rejecting, set `action: Mask`, or use
  `regex.builtins: [Ssn, CreditCard, Email, PhoneNumber, CaSin]` for the shipped patterns.)

```bash
kubectl apply -f manifests/kgateway-prompt-guard.yaml
```

Test both paths — note the `Host` is `llm.example.com` (lab-02's route), the one the guard
attaches to:

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

`-o /dev/null -w "%{http_code}\n"` throws away the response body and prints **only the HTTP
status code** — all you care about here is allowed (`200`) vs blocked (`4xx`). The two
requests are identical except the second prompt contains an SSN-shaped string.

**What to look for:** `200` then `403` (or `400`) — either code is fine, both mean the guard
rejected the request (`403` Forbidden or `400` Bad Request, depending on version). The
blocked request never reached vLLM —
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
guards are a **precision/recall tradeoff**. Too loose (`\d+`) blocks real users (false
positives); too tight lets PII through (false negatives). There's no "correct" config to
validate against — only a tradeoff you tune and re-test. Restore the SSN pattern (`\b\d{3}-\d{2}-\d{4}\b`) and re-apply.

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
