# Lab 03 — Kong AI Gateway: the `ai-proxy` plugin

> Front the *same* vLLM with Kong instead of kgateway, using the `ai-proxy` plugin family — proving the AI-gateway pattern is a pattern, not a kgateway feature.

**Time:** ~30 min · **Cost:** free (local kind)

## The problem

You just made kgateway parse OpenAI traffic and meter by tokens. Fair question: did you
learn *the AI-gateway pattern*, or did you learn *kgateway's CRDs*? If the only way to get
token-aware, OpenAI-protocol routing were one vendor's `AgentgatewayBackend`, the idea
wouldn't be portable — it'd be lock-in. So front the identical vLLM with a completely
different gateway and see whether the same capability shows up under a different name.

## What it replaces, and the difference that matters

In `05-gateway-api/lab-03` you already saw Kong implement the same **Gateway API** —
`GatewayClass: kong`, ordinary `Gateway` + `HTTPRoute`. Kong implements the same **AI**
pattern too, but its mechanism is a **plugin** attached to a route, not a backend *type*.

| | kgateway (lab-02) | Kong (this lab) |
|---|---|---|
| "this speaks OpenAI" | a CRD: `AgentgatewayBackend` | a plugin: `KongPlugin` type `ai-proxy` |
| where it attaches | `backendRef` on the route | annotation on the route |
| token metering | `AgentgatewayPolicy` rate limit | `ai-rate-limiting-advanced` plugin |
| who parses the protocol | agentgateway data plane | Kong's `ai-proxy` runtime |

The capability is identical — parse OpenAI, normalize providers, meter tokens. The
limitation Kong's idiom removes vs a plain Kong route is the same one from lab-02: a plain
route forwards bytes; `ai-proxy` reads the OpenAI request/response. The lesson is that
"protocol-aware gateway" is a *layer*, and two vendors fill it two ways.

## Under the hood (MIT hat): what `ai-proxy` does to the request

`ai-proxy` is request middleware that sits in Kong's plugin chain. When a request matches
a route the plugin is attached to, `ai-proxy` *replaces* the upstream selection: instead of
proxying to the route's `backendRef`, it normalizes the OpenAI request and forwards to the
provider/URL in its own config. That's why the route's `backendRef` is just a placeholder
— the plugin overrides it.

```
curl  (OpenAI JSON)
  │
  ▼
Kong proxy  ──► route "kong-llm" matches /v1
  │
  │  ai-proxy plugin fires:
  │    - route_type: llm/v1/chat   (expect a chat request)
  │    - model.provider: openai    (normalize to OpenAI format)
  │    - upstream_url: http://vllm...:8000/v1/chat/completions
  │    - inject auth header (caller never sees it)
  ▼
vLLM  /v1/chat/completions  ──►  response (with usage block)
        (ai-rate-limiting-advanced, if attached, meters tokens from usage)
```

The shape mirrors lab-02 exactly: a runtime that *understands the body* substitutes a
real LLM upstream and can act on `usage`. Different vendor, same floor. Below Kong, the
upstream `vllm.default.svc.cluster.local:8000` resolves and routes through the Phase 03
stack — unchanged.

## Step 1 — Kong should already be installed

From `05-gateway-api/lab-03`:

```bash
helm repo add kong https://charts.konghq.com && helm repo update
helm upgrade -i kong kong/ingress -n kong --create-namespace
kubectl get gatewayclass kong
```

**What to look for:** `kubectl get gatewayclass kong` shows the class with `ACCEPTED=True`.
That's your proof the Kong controller is live and will act on the Gateway/HTTPRoute below
— the same "no controller, no behavior" lesson from `05-gateway-api/lab-01`.

## Step 2 — A Gateway + route for LLM traffic

```bash
kubectl apply -f manifests/kong-ai-route.yaml
```

This is an ordinary Gateway/HTTPRoute pair on `gatewayClassName: kong`. Note the route's
`backendRefs` points at the `vllm` Service on port 8000 — but that's a **placeholder**.
The `ai-proxy` plugin in the next step overrides the upstream, so this backendRef exists
mostly to satisfy the HTTPRoute schema. Nothing here is "AI" yet.

**What to look for:** `kubectl describe httproute kong-llm` shows `Accepted=True`. At this
point the route forwards plain HTTP — it does not yet understand OpenAI.

## Step 3 — Attach the ai-proxy plugin

```bash
kubectl apply -f manifests/kong-ai-proxy-plugin.yaml
```

`kong-ai-proxy-plugin.yaml` is a `KongPlugin` of type `ai-proxy` with
`route_type: llm/v1/chat` and the in-cluster vLLM as `model.options.upstream_url`. The
route's annotation (`konghq.com/plugins: ai-proxy-vllm`, set in `kong-ai-route.yaml`) is
what binds the plugin to the route. Now Kong speaks OpenAI on the front and forwards to
vLLM on the back.

We use `llm/v1/chat` to match the instruct model and the `/v1/chat/completions` endpoint
the whole phase uses. (Kong's `route_type` also supports `llm/v1/completions` for
base-model text completion — the same chat-vs-base split you met in lab-01, surfaced as a
plugin setting.)

**What to look for:** the plugin applies clean. The binding is the annotation, not a
backendRef — a deliberately different attachment model from kgateway. If you forget the
annotation, the request in Step 4 would just hit the placeholder backend as plain HTTP.

## Step 4 — Test it

```bash
export PROXY_IP=$(kubectl get svc -n kong kong-gateway-proxy \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
# On kind without MetalLB, port-forward instead:
# kubectl port-forward -n kong svc/kong-gateway-proxy 8080:80 & PROXY_IP=localhost:8080

curl -s http://$PROXY_IP/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Host: llm.example.com' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"One sentence on API gateways:"}],"max_tokens":24}' \
  | python3 -m json.tool
```

On kind, the `LoadBalancer` Service usually has no external IP, so `$PROXY_IP` comes back
empty — use the commented `port-forward` line instead.

**What to look for:** a normal chat completion with a `usage` block, just like lab-01 and
lab-02. Identical response surface, third path to it — that's the portability point landing.

## Step 5 — Token rate limiting, Kong-style (and an honest limitation)

Kong meters tokens with the `ai-rate-limiting-advanced` plugin — and here's a real
lesson worth more than a copy-paste: on most builds that plugin is a **Kong Enterprise**
feature, so the free OSS `kong/ingress` install you're running may not expose it. That's
why this step ships **no manifest to apply**. Contrast that with lab-02, where kgateway's
token limit was free and built in.

The *pattern* is identical to `ai-proxy`, though: on an Enterprise build you'd create a
`KongPlugin` of type `ai-rate-limiting-advanced` and bind it to the route by annotation —
exactly how you bound `ai-proxy` in Step 3 — then replay the `for`-loop from
`05-gateway-api/lab-03` section **4. The Kong difference: a plugin**. The `429`s would
then trigger on **token spend** (`200`s flipping to `429` after a few *fat* requests, not
after a fixed *count*) — the same behavior kgateway gave you for free in lab-02.

**The lesson:** "AI gateway is a pattern" is true, but *which* parts are free vs paid
differs by vendor — kgateway's token limit is OSS, Kong's is Enterprise. That gap is
exactly the kind of thing that decides a platform choice; it's the substance behind the
`05-gateway-api/lab-04` kgateway-vs-Kong comparison.

## Break it, then read the error (Kelsey lens)

Set the plugin's upstream to a wrong port and re-apply:

```bash
# In kong-ai-proxy-plugin.yaml, change model.options.upstream_url to a bad port, then:
kubectl apply -f manifests/kong-ai-proxy-plugin.yaml
```

Re-run the Step 4 curl. **Read both the response and the log.** Kong returns a `502`, and
the real story is in the proxy log:

```bash
kubectl logs -n kong <proxy-pod>
```

You'll see an upstream connection error naming the bad URL — Kong is fine; the *model* is
unreachable. This is the same "valid config, dead upstream" lesson as lab-02's 502, but it
teaches a second skill: **where each vendor puts the truth.** kgateway surfaced it as a
return code; Kong buries the cause in the proxy log. Reading that log to tell "my gateway
is broken" from "my model is broken" is the core operational skill of this phase. Set
`upstream_url` back to the correct port and re-apply to recover.

## Checkpoint — you can now explain…

- **That "AI gateway" is a pattern, not a product.** The same capability — parse OpenAI,
  normalize providers, meter tokens — shows up in kgateway as CRDs and in Kong as plugins.
  You fronted one vLLM with both.
- **How `ai-proxy` attaches and what it overrides.** It's a `KongPlugin` bound by route
  annotation; at request time it overrides the route's placeholder `backendRef` and
  forwards to its own `upstream_url`.
- **Why the chat-vs-base distinction reappears.** Kong exposes it as `route_type`
  (`llm/v1/chat` vs `llm/v1/completions`) — the same model-type contract from lab-01,
  surfaced as a plugin field.
- **Where to look when it breaks.** Kong reports upstream failures as a `502` whose cause
  lives in the proxy log, not the response — vendor-specific, and worth knowing before 2am.

You can now:
- [ ] Map kgateway's `AgentgatewayBackend`/policy to Kong's `ai-proxy`/`ai-rate-limiting-advanced`.
- [ ] Explain how the route annotation binds a plugin and why the backendRef is a placeholder.
- [ ] Read a Kong proxy log to separate a gateway fault from an upstream fault.

## Next

→ `lab-04-multimodel-guards.md`: route one endpoint across *two* models by the request
body's `model` value, then block bad prompts at the door — two features you can't fake
with plain path/host routing.
