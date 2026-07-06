# Lab 02: kgateway AI Gateway, vLLM behind a token-aware front door

> Enable kgateway's AI features, route to vLLM, then meter callers by tokens, the thing a plain HTTP gateway physically cannot do.

**Time:** ~30 min · **Cost:** free (local kind)

## The problem

In Phase 05 you put a Gateway in front of a Service and it worked: requests came in,
bytes went out. Now try the one thing an LLM platform needs: cap how much
a caller can spend. You can't. A plain gateway can rate-limit by request count, but a
5-token "hi" and a 4,000-token essay each count as exactly one request. LLM cost and
capacity are measured in tokens, not requests, so request-counting is the wrong unit.
Nothing you learned in Phase 05 closes this gap, because the gateway there never
looked inside the body; it didn't know the response carried a `usage` block at all.

## What it replaces, and why that was insufficient

This is the moment Phase 05 and Phase 06 fuse. The same kgateway you installed in
`05-gateway-api/lab-02` becomes an AI gateway. You don't swap it out; you add
two things:

| Phase 05 (HTTP gateway) | Phase 06 (AI gateway) | What changed |
|---|---|---|
| `backendRef` → a Service | `backendRef` → an `AgentgatewayBackend` | the gateway now knows "this speaks OpenAI" |
| rate limit by `requests` | rate limit by `tokens` | meters the unit LLM cost is priced in |
| forwards the body opaquely | parses `usage`, `model`, the prompt | acts on the protocol, not plain HTTP |

The specific limitation it removes: an HTTP gateway is protocol-blind. It counts
requests and forwards bytes because that's all it can see. The AI data plane parses the
OpenAI request and response, so it can read `usage.total_tokens` and meter by tokens,
inject upstream auth the caller never sees, and (later labs) route by the `model` field or
guard the prompt. "AI gateway" means it understands the protocol, full stop.

## Underneath: where the AI features live

kgateway now ships a second data-plane proxy, agentgateway, dedicated to AI traffic;
that's what the two `--set` flags below turn on. It runs alongside the Envoy proxy from
Phase 05, not instead of it. (Historically these AI features lived inside Envoy via a
`gateway.aiExtension` path; that path was deprecated in 2.1 and removed in 2.2, which is why
old docs differ.) That's why this phase installs with two extra flags and uses the
`agentgateway.dev/v1alpha1` API group instead of `gateway.kgateway.dev`: these AI kinds
live in agentgateway, not in core kgateway.

So when you apply an `AgentgatewayBackend`, the kgateway controller doesn't hand it to
Envoy. It compiles it into config for the agentgateway process, which parses OpenAI
traffic. Here is the request path with token metering on:

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
  │  budget exhausted?  →  429   (NOT a request count, a TOKEN count)
```

The leap from Phase 05: an Envoy/HTTP gateway's rate limiter increments a counter per
request and never opens the body. agentgateway opens the body, finds `usage`, and
decrements a token budget. Same front door, but the data plane now reads the payload.
Below it, nothing changed: `spec.ai.provider.host` resolves to vLLM's Service ClusterIP
and kube-proxy does the packet work, exactly as in Phase 03.

## Step 1: Install kgateway with agentgateway enabled

This is the canonical kgateway install for the repo: same version pin as
`05-gateway-api/lab-02`, both charts, plus the two AI flags. Alpha APIs are experimental
Kubernetes kinds that ship turned off; the first flag turns the agentgateway proxy on,
and the second (`enableAlphaAPIs=true`) opts in so the apiserver will accept
`AgentgatewayBackend`/`AgentgatewayPolicy` at all. You need both.
Re-running it is idempotent, so it doubles as the "upgrade Phase 05's install" step:

```bash
# Same pin as Phase 05 - check kgateway.dev / GitHub releases for current.
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
or `enableAlphaAPIs` was missing; every `kubectl apply` below would then fail with `no
matches for kind "AgentgatewayBackend"`. The Gateway from Phase 05 (`http`, in
`kgateway-system`) is still your front door; the AI policies below attach to routes
hanging off it.

## Step 2: Describe vLLM as an AI Backend

A normal `backendRef` points at a Service and says nothing about protocol. An
`AgentgatewayBackend` is its own object whose entire job is to tell the data plane "this
endpoint speaks OpenAI." Here is the whole object (`manifests/kgateway-ai-backend.yaml`),
then the fields that carry the weight:

```yaml
apiVersion: agentgateway.dev/v1alpha1   # AI kinds live in agentgateway.dev, NOT gateway.kgateway.dev
kind: AgentgatewayBackend               # the new noun Step 1's enableAlphaAPIs turned on
metadata:
  name: vllm-ai                         # the HTTPRoute's backendRef names this (Step 3)
  namespace: default                    # same ns as the route; backendRef resolves here
spec:
  ai:                                   # this backend is an AI endpoint, not a plain Service
    provider:
      openai:                           # "this endpoint speaks the OpenAI protocol"
        model: "Qwen/Qwen2.5-0.5B-Instruct"   # the model name vLLM serves (lab-01)
      # host/port are SIBLINGS of `openai`, under `provider` - NOT inside openai (the #1 trap).
      host: vllm.default.svc.cluster.local   # the in-cluster vLLM Service DNS name
      port: 8000                             # vLLM's port - the backend owns it, the route does NOT
```

Two things beginners get wrong here:

- **`host`/`port` sit beside `openai`, not inside it.** They're peers under `provider`.
  Nest them under `openai:` and the data plane falls back to its default target
  (`api.openai.com:443`) and your self-hosted vLLM is never contacted.
- **The port lives here, not on the route.** An AI `backendRef` (Step 3) carries no `port:`;
  this object owns it. That's exactly the field the "break it" section below corrupts.

There's no `auth` block because local vLLM needs no API key. That omission is the
provider abstraction: for a hosted provider you'd drop `host`/`port` (agentgateway
defaults to `api.openai.com:443`) and add `spec.ai.policies.auth.secretRef`, the same
object with a different target. Self-hosted vs hosted is a field change, not a redesign.

```bash
kubectl apply -f manifests/kgateway-ai-backend.yaml   # compiled into agentgateway config, not Envoy
```

**What to look for:** `kubectl get agentgatewaybackend vllm-ai` returns the object. The
fact that it accepts means the alpha CRDs from Step 1 are installed (if not, you'd
get `no matches for kind "AgentgatewayBackend"`).

## Step 3: Route to it

This `HTTPRoute` is Phase 05's shape with exactly one difference: the backend target.
Read it (`manifests/kgateway-ai-route.yaml`):

```yaml
apiVersion: gateway.networking.k8s.io/v1   # standard Gateway API - the route is NOT an AI kind
kind: HTTPRoute
metadata:
  name: llm                                # `describe httproute llm` and the policy targetRef use this
  namespace: default
spec:
  parentRefs:
    - name: http                           # attach to the "http" Gateway from Phase 05...
      namespace: kgateway-system           # ...which lives in kgateway-system, not here
  hostnames:
    - "llm.example.com"                    # the curl below MUST send Host: llm.example.com or it 404s
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1/chat/completions     # only the OpenAI chat path routes here
      backendRefs:
        - group: agentgateway.dev           # THE difference vs Phase 05: not a core Service...
          kind: AgentgatewayBackend         # ...but the AI backend from Step 2
          name: vllm-ai                     # no port: here - the AgentgatewayBackend owns it
```

The single load-bearing change is the `backendRefs` entry: `group`/`kind` point at the
`AgentgatewayBackend` instead of a plain Service. Two gotchas:

- **`group` and `kind` are mandatory here.** A plain-Service backendRef can omit them
  (they default to the core Service). Point at a CRD and you must spell out
  `group: agentgateway.dev` + `kind: AgentgatewayBackend`, or the route resolves to nothing.
- **No `port:`.** A Service backendRef normally needs one; an AI backendRef does not,
  because the port lives on the `AgentgatewayBackend` (Step 2). This is what the "break it"
  section exploits.

```bash
kubectl apply -f manifests/kgateway-ai-route.yaml
```

**What to look for:** check that the route attached to the Gateway:

```bash
kubectl describe httproute llm
```

Under `status.parents[].conditions` you want `Accepted=True` and
`ResolvedRefs=True`. If `ResolvedRefs` is `False`, the route can't find the backend: a
typo in the backend name or group. That condition is the route telling you whether it
wired up, before you send a single request.

## Step 4: Send an OpenAI chat request through the gateway

```bash
kubectl port-forward -n kgateway-system svc/http 8080:80 &

curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Host: llm.example.com' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Define an AI gateway in one line:"}],"max_tokens":24}' \
  | python3 -m json.tool
```

`svc/http` is the Gateway's proxy Service from Phase 05 lab-02 (the `http` Gateway listens
on 80); we forward `8080->80`. This is the gateway's front door, not vLLM's `8000`:
traffic now enters at the gateway. And the `-H 'Host: llm.example.com'` must match the
`HTTPRoute`'s hostname; that's how the gateway picks this route. Drop it and you'll 404.

The request path is now: curl → kgateway (`http` Gateway, agentgateway data plane) → vLLM.
Same front door as Phase 05, but the gateway understood the payload on the way through.

**What to look for:** a normal chat completion with a `usage` block, identical to lab-01's
direct call. That's the tell that the gateway forwarded faithfully and parsed it: in the
next step that same `usage` figure becomes the meter.

## Step 5: Meter by tokens, not requests

This is the line a plain HTTP gateway cannot express. Read the policy
(`manifests/kgateway-token-ratelimit.yaml`):

```yaml
apiVersion: agentgateway.dev/v1alpha1   # same AI API group as the backend
kind: AgentgatewayPolicy                # a policy ATTACHES to a route; it isn't a route itself
metadata:
  name: llm-token-budget
  namespace: default
spec:
  targetRefs:                           # which object this policy applies to...
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm                         # ...the route from Step 3, by name
  traffic:
    rateLimit:
      local:                            # "local" = counted in-proxy, no external rate-limit service
        # ~500 LLM tokens per minute, counted from each response's usage field.
        - tokens: 500                   # the budget is in TOKENS - swap to `requests:` for the old limit
          unit: Minutes                 # window unit: Seconds | Minutes | Hours
```

The whole point lives in the `local` entry: `tokens: 500` per `unit: Minutes`. For AI
backends the data plane reads each response's `usage` field and subtracts prompt +
completion tokens from this budget, instead of incrementing a per-request counter. Two
things to notice:

- **`tokens:` vs `requests:`.** Swap the one word and the same policy becomes the old
  request-count limit. That single key is the API-gateway / AI-gateway line, in YAML.
- **`local` means in-proxy.** The count is kept inside the agentgateway process per
  replica, with no external rate-limit service to stand up; fine for one replica, though
  across many you'd reach for a global limiter.

```bash
kubectl apply -f manifests/kgateway-token-ratelimit.yaml   # attaches to route "llm" via targetRefs
```

Burn it down:

```bash
for i in $(seq 1 12); do
  # -o /dev/null discards the body; -w "%{http_code} " prints just the status, space-separated.
  # max_tokens:128 makes each call SPEND ~100-130 tokens against the 500/min budget.
  curl -s -o /dev/null -w "%{http_code} " http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' -H 'Host: llm.example.com' \
    -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"write a long paragraph about kubernetes"}],"max_tokens":128}'
done; echo
```

**What to look for:** a row of `200`s that flips to `429` (Too Many Requests, the rate
limiter tripped), and it flips before 12 requests, because each fat request spends
~100-130 tokens against a 500-token budget, so 500/min runs out after ~4-5 calls. We loop
12 times to be sure we cross the limit and see the `429`; a dozen tiny `max_tokens:4`
requests would not. That asymmetry is the proof: the limit counts tokens read from
`usage`, not requests. A 5-token call and a 500-token call cost differently. **That is the
entire difference between an API gateway and an AI gateway.**

```bash
kill %1 2>/dev/null
```

## Break it, then read the error

The upstream port lives on the `AgentgatewayBackend`, not on the `HTTPRoute`
backendRef; an AI `backendRef` carries no `port:`, the backend owns it. Point the backend
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

**Read the error.** You get a `502` (Bad Gateway, the proxy reached no working upstream) or
a connection error at request time, not at apply time. The apply succeeded: the CRD is
schema-valid, the route still says `Accepted=True`.
The break only shows up when the data plane tries to open a TCP connection to
`vllm:9999` and nothing answers. That distinction is the whole lesson: a valid config is
not a working upstream. The control plane accepted your intent; the data plane couldn't
fulfill it. This is the most common AI-platform page at 2am: gateway green, model
unreachable. Recover by setting `spec.ai.provider.port` back to `8000` and re-applying.

## Checkpoint: you can now explain…

- **Why a plain gateway can't meter LLM spend.** It rate-limits by request count and never
  opens the body; LLM cost is in tokens, which live in the response `usage` block it never
  reads.
- **What makes kgateway an AI gateway.** The agentgateway data plane parses the OpenAI
  request and response. The `AgentgatewayBackend` is how you declare "this speaks OpenAI";
  the token `rateLimit.local` is how you act on what it parsed.
- **What `AgentgatewayBackend` turns into.** Not Envoy config but agentgateway data-plane
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

→ `lab-03-kong-ai.md`: the same capability (OpenAI-aware proxying and token limits)
expressed as Kong plugins instead of CRDs. The pattern is portable; the idiom isn't.
</content>
