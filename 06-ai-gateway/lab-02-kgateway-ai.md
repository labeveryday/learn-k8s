# Lab 02 — kgateway AI Gateway: vLLM behind a token-aware front door

> Enable kgateway's AI features, route to vLLM, then meter callers by **tokens** — the thing a plain HTTP gateway physically cannot do.

**Time:** ~30 min · **Cost:** free (local kind)

## The problem

In Phase 05 you put a Gateway in front of a Service and it worked: requests came in,
bytes went out. Now try to do the one thing an LLM platform actually needs — *cap how much
a caller can spend.* You can't. A plain gateway can rate-limit by **request count**, but a
5-token "hi" and a 4,000-token essay each count as exactly one request. LLM cost and
capacity are measured in **tokens**, not requests, so request-counting is the wrong unit
entirely. Nothing you learned in Phase 05 closes this gap, because the gateway there never
looked inside the body — it didn't know the response carried a `usage` block at all.

## What it replaces, and why that was insufficient

This is the moment Phase 05 and Phase 06 fuse. The *same* kgateway you installed in
`05-gateway-api/lab-02` becomes an **AI gateway** — not by swapping it out, but by adding
two things:

| Phase 05 (HTTP gateway) | Phase 06 (AI gateway) | What changed |
|---|---|---|
| `backendRef` → a Service | `backendRef` → an `AgentgatewayBackend` | the gateway now knows "this speaks OpenAI" |
| rate limit by `requests` | rate limit by `tokens` | meters the unit LLM cost is priced in |
| forwards the body opaquely | parses `usage`, `model`, the prompt | acts on the *protocol*, not just HTTP |

The specific limitation it removes: an HTTP gateway is **protocol-blind**. It counts
requests and forwards bytes because that's all it can see. The AI data plane **parses the
OpenAI request and response**, so it can read `usage.total_tokens` and meter by tokens,
inject upstream auth the caller never sees, and (later labs) route by the `model` field or
guard the prompt. "AI gateway" means *it understands the protocol*, full stop.

## Under the hood (MIT hat): where the AI features actually live

In kgateway 2.2+ the AI features moved **out** of the Envoy proxy and into a separate
**agentgateway** data plane. (The old `gateway.aiExtension` path was deprecated in 2.1 and
removed in 2.2.) That's why this phase installs with two extra flags and uses the
`agentgateway.dev/v1alpha1` API group instead of `gateway.kgateway.dev` — these AI kinds
live in agentgateway, not in core kgateway.

So when you apply an `AgentgatewayBackend`, the kgateway controller doesn't hand it to
Envoy. It compiles it into config for the **agentgateway** process, which is the thing
that actually parses OpenAI traffic. Here is the request path with token metering on:

```
curl  (OpenAI JSON: model, messages[])
  │
  ▼
Gateway "http"  ─── HTTPRoute matches path ───►  AgentgatewayBackend "vllm-ai"
  │                                                   │ (data plane = agentgateway)
  │  agentgateway PARSES the request body             │
  │  forwards to spec.ai.provider.host:port  ─────────┘
  ▼
vLLM  /v1/chat/completions  ──►  response with  "usage": { total_tokens: N }
  ▲                                                   │
  │  agentgateway PARSES the response,                │
  │  reads usage.total_tokens,                        │
  │  subtracts N from the token budget  ◄─────────────┘
  │  budget exhausted?  →  429   (NOT a request count — a TOKEN count)
```

The leap from Phase 05: an Envoy/HTTP gateway's rate limiter increments a counter *per
request* and never opens the body. agentgateway opens the body, finds `usage`, and
decrements a **token** budget. Same front door, but the data plane now reads the payload.
Below it, nothing changed — `spec.ai.provider.host` resolves to vLLM's Service ClusterIP
and kube-proxy does the packet work, exactly as in Phase 03.

## Step 1 — Install kgateway with agentgateway enabled

This is the canonical kgateway install for the repo: same version pin as
`05-gateway-api/lab-02`, both charts, plus the two AI flags. The AI resources are alpha,
so you turn agentgateway on **and** enable the alpha APIs at install/upgrade time.
Re-running it is idempotent, so it doubles as the "upgrade Phase 05's install" step:

```bash
# Same pin as Phase 05 — check kgateway.dev / GitHub releases for current.
KGW=v2.3.1

# CRDs first (installs the agentgateway.dev CRDs used below), then the controller.
helm upgrade -i --create-namespace -n kgateway-system \
  --version ${KGW} kgateway-crds \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds

helm upgrade -i -n kgateway-system \
  --version ${KGW} kgateway \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --set agentGateway.enabled=true \
  --set agentGateway.enableAlphaAPIs=true

kubectl -n kgateway-system rollout status deploy/kgateway
```

**What to look for:** the rollout completes, and the controller now knows the new nouns:

```bash
kubectl -n kgateway-system get pods
kubectl get crd | grep agentgateway.dev
# expect agentgatewaybackends + agentgatewaypolicies in the agentgateway.dev group
```

If `kubectl get crd | grep agentgateway.dev` returns nothing, the CRD chart didn't apply
or `enableAlphaAPIs` was missing — every `kubectl apply` below would then fail with `no
matches for kind "AgentgatewayBackend"`. The Gateway from Phase 05 (`http`, in
`kgateway-system`) is still your front door; the AI policies below attach to routes
hanging off it.

## Step 2 — Describe vLLM as an AI Backend

A normal `backendRef` points at a Service and says nothing about protocol. An
`AgentgatewayBackend` is its own object whose entire job is to tell the data plane "this
endpoint speaks OpenAI":

```bash
kubectl apply -f manifests/kgateway-ai-backend.yaml
```

The shape that matters (and trips people up):

- `spec.ai.provider.openai.model` — the model name vLLM serves.
- `spec.ai.provider.host` / `spec.ai.provider.port` — the self-hosted endpoint. These sit
  **beside** `openai`, under `provider` — *not* inside `openai`.

Local vLLM needs no API key, so no auth is set. The payoff is the **provider
abstraction**: for a *hosted* provider you'd drop `host`/`port` (agentgateway defaults to
`api.openai.com:443`) and add `spec.ai.policies.auth.secretRef` — the **same object**,
different target. Self-hosted vs hosted is a field change, not a redesign.

**What to look for:** `kubectl get agentgatewaybackend vllm-ai` returns the object. The
fact that it accepts means the alpha CRDs from Step 1 are really installed.

## Step 3 — Route to it

```bash
kubectl apply -f manifests/kgateway-ai-route.yaml
```

This `HTTPRoute` is Phase 05's shape with exactly one difference: `backendRefs` names an
`AgentgatewayBackend` (via `group: agentgateway.dev` / `kind: AgentgatewayBackend`)
instead of a plain Service. Its `parentRef` is the `http` Gateway in `kgateway-system`.

**What to look for:** check that the route attached to the Gateway:

```bash
kubectl describe httproute llm
```

Under `status.parents[].conditions` you want `Accepted=True` and
`ResolvedRefs=True`. If `ResolvedRefs` is `False`, the route can't find the backend — a
typo in the backend name or group. That condition is the route telling you whether it
actually wired up, before you send a single request.

## Step 4 — Send an OpenAI chat request through the gateway

```bash
kubectl port-forward -n kgateway-system svc/http 8080:80 &

curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Host: llm.example.com' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Define an AI gateway in one line:"}],"max_tokens":24}' \
  | python3 -m json.tool
```

The request path is now: curl → kgateway (`http` Gateway, agentgateway data plane) → vLLM.
Same front door as Phase 05, but the gateway *understood the payload* on the way through.

**What to look for:** a normal chat completion with a `usage` block — identical to lab-01's
direct call. That's the tell that the gateway forwarded faithfully *and* parsed it: in the
next step that same `usage` figure becomes the meter.

## Step 5 — Meter by tokens, not requests

```bash
kubectl apply -f manifests/kgateway-token-ratelimit.yaml
```

This `AgentgatewayPolicy` sets a per-window **token** budget at
`spec.traffic.rateLimit.local` — each entry is a `tokens:` count per `unit:`
(`Seconds`/`Minutes`/`Hours`). The manifest sets ~500 tokens per minute. For AI backends
the data plane reads each response's `usage` field and subtracts those tokens from the
budget — instead of counting requests. (Swap `tokens:` for `requests:` and you'd have the
old, request-based limit.) Burn it down:

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' -H 'Host: llm.example.com' \
    -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"write a long paragraph about kubernetes"}],"max_tokens":128}'
done; echo
```

**What to look for:** a row of `200`s that flips to `429` — and it flips **before** 12
requests, because each request spends ~100+ tokens against a 500-token budget. Roughly
four to five fat requests exhaust it; a dozen tiny `max_tokens:4` requests would not. That
asymmetry is the proof: the limit is counting *tokens read from `usage`*, not requests. A
5-token call and a 500-token call cost differently. **That is the entire difference between
an API gateway and an AI gateway.**

```bash
kill %1 2>/dev/null
```

## Break it, then read the error (Kelsey lens)

The upstream port lives on the **`AgentgatewayBackend`**, not on the `HTTPRoute`
backendRef — an AI `backendRef` carries no `port:`, the backend owns it. Point the backend
at a port vLLM isn't listening on and watch where the failure surfaces:

```bash
# Edit kgateway-ai-backend.yaml: change spec.ai.provider.port to 9999, then:
kubectl apply -f manifests/kgateway-ai-backend.yaml
kubectl port-forward -n kgateway-system svc/http 8080:80 &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/chat/completions \
  -H 'Host: llm.example.com' -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
kill %1 2>/dev/null
```

**Read the error.** You get a `502` / connection error **at request time** — not at apply
time. The apply *succeeded*: the CRD is schema-valid, the route still says `Accepted=True`.
The break only shows up when the data plane tries to open a TCP connection to
`vllm:9999` and nothing answers. That distinction is the whole lesson: **a valid config is
not a working upstream.** The control plane accepted your intent; the data plane couldn't
fulfill it. This is the most common AI-platform page at 2am — gateway green, model
unreachable. Recover by setting `spec.ai.provider.port` back to `8000` and re-applying.

## Checkpoint — you can now explain…

- **Why a plain gateway can't meter LLM spend.** It rate-limits by request count and never
  opens the body; LLM cost is in tokens, which live in the response `usage` block it never
  reads.
- **What makes kgateway an *AI* gateway.** The agentgateway data plane parses the OpenAI
  request and response. The `AgentgatewayBackend` is how you declare "this speaks OpenAI";
  the token `rateLimit.local` is how you act on what it parsed.
- **What `AgentgatewayBackend` turns into.** Not Envoy config — agentgateway data-plane
  config. The controller compiles the CRD into the agentgateway process that does the
  parsing, then forwards to `provider.host:port`, which resolves to vLLM's ClusterIP via
  the Phase 03 stack.
- **Why a wrong port fails at request time, not apply time.** Schema validation
  (control plane) and reachability (data plane) are different checks; a config that passes
  the first can still fail the second.

You can now:
- [ ] State the token-vs-request distinction and predict when a `tokens:` limit trips.
- [ ] Describe what an `AgentgatewayBackend` declares and where its `host`/`port` live.
- [ ] Explain the provider-abstraction story (self-hosted vs hosted = one object).
- [ ] Read a `502`-at-request-time as "valid config, dead upstream."

## Next

→ `lab-03-kong-ai.md`: the same capability — OpenAI-aware proxying and token limits — but
expressed as Kong **plugins** instead of CRDs. The pattern is portable; the idiom isn't.
