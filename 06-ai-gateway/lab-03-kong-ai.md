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
helm repo add kong https://charts.konghq.com && helm repo update  # register Kong's chart repo + refresh the index
helm upgrade -i kong kong/ingress -n kong --create-namespace       # -i = install-or-upgrade (idempotent); creates the kong namespace if absent
kubectl get gatewayclass kong
```

- `helm upgrade -i` (`--install`) is the safe re-runnable form: it installs the release if it
  doesn't exist and upgrades it if it does — so running this when Kong is already up is harmless.
- `kong/ingress` is the OSS chart. Hold onto that fact — it's the whole point of Step 5: the
  free chart doesn't ship the token-limiting plugin.

**What to look for:** `kubectl get gatewayclass kong` shows the class with `ACCEPTED=True`.
That's your proof the Kong controller is live and will act on the Gateway/HTTPRoute below
— the same "no controller, no behavior" lesson from `05-gateway-api/lab-01`.

## Step 2 — A Gateway + route for LLM traffic

This is an ordinary Gateway/HTTPRoute pair on `gatewayClassName: kong` — pure Gateway API,
nothing "AI" about it yet. Here's the whole manifest (`manifests/kong-ai-route.yaml`), then
the two fields that matter:

```yaml
apiVersion: gateway.networking.k8s.io/v1   # standard Gateway API — not a Kong-specific CRD
kind: Gateway
metadata:
  name: kong-llm
  namespace: default
spec:
  gatewayClassName: kong       # tells the Kong controller (Step 1) to own this Gateway
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        from: All               # any namespace may attach a route to this listener
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kong-llm
  namespace: default
  annotations:
    konghq.com/plugins: ai-proxy-vllm   # THE BINDING: attaches the KongPlugin named "ai-proxy-vllm" (Step 3) to this route
spec:
  parentRefs:
    - name: kong-llm           # attach to the Gateway above (same name, same namespace)
  hostnames:
    - "llm.example.com"        # the route only matches requests with this Host header — note it in the Step 4 curl
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1          # match the OpenAI path prefix (/v1/chat/completions etc.)
      # ai-proxy rewrites the upstream; this backendRef is a placeholder target.
      backendRefs:
        - name: vllm
          port: 8000            # PLACEHOLDER — required by the HTTPRoute schema, but ai-proxy overrides it at request time
```

Two fields beginners trip on:

- **The `konghq.com/plugins` annotation is the entire wiring.** Kong attaches a plugin to a
  route *by annotation*, not by a field in the route spec. The value (`ai-proxy-vllm`) is the
  *name of the KongPlugin object* you create in Step 3 — they must match exactly or the plugin
  never fires. This is the deliberate difference from kgateway, which wires the LLM behavior
  through a `backendRef` type instead.
- **`backendRefs` is a placeholder, not the LLM target.** The HTTPRoute schema requires a
  backend, so this names the `vllm` Service on port 8000 — but `ai-proxy` (Step 3) replaces the
  upstream at request time with its own `upstream_url`. Don't expect this Service:port to be
  where traffic actually lands.

Apply it:

```bash
kubectl apply -f manifests/kong-ai-route.yaml
```

**What to look for:** `kubectl describe httproute kong-llm` shows `Accepted=True`. At this
point the route forwards plain HTTP to the placeholder backend — it does not yet understand
OpenAI. The annotation is set, but the plugin it names doesn't exist yet (Step 3 fixes that).

## Step 3 — Attach the ai-proxy plugin

This is the object the Step 2 annotation was pointing at. A `KongPlugin` named `ai-proxy-vllm`
of type `ai-proxy` — the runtime that turns the placeholder route into an OpenAI proxy. Here's
the whole manifest (`manifests/kong-ai-proxy-plugin.yaml`), then the load-bearing fields:

```yaml
apiVersion: configuration.konghq.com/v1   # Kong's own CRD group — NOT Gateway API
kind: KongPlugin
metadata:
  name: ai-proxy-vllm        # MUST equal the route's konghq.com/plugins annotation value (Step 2) — that's the link
  namespace: default
plugin: ai-proxy             # the plugin family; "ai-proxy" = OpenAI-protocol LLM proxy. Top-level field, not under config
config:
  route_type: "llm/v1/chat"  # expect OpenAI CHAT requests → forward to /chat/completions (vs llm/v1/completions for base models)
  model:
    provider: openai         # normalize the request/response to OpenAI's wire format
    name: Qwen/Qwen2.5-0.5B-Instruct   # the model name advertised; matches what the Step 4 curl sends
    options:
      # Point Kong's ai-proxy at the in-cluster vLLM service's chat endpoint.
      upstream_url: "http://vllm.default.svc.cluster.local:8000/v1/chat/completions"  # the REAL upstream — overrides the route's placeholder backendRef
  # Local vLLM needs no key; for a hosted provider, reference a header/secret here.
  auth:
    header_name: Authorization
    header_value: "Bearer not-needed-for-local-vllm"   # ai-proxy injects this on the way out — the caller never sends/sees a key
```

The fields that make it work:

- **`plugin: ai-proxy`** is a *top-level* field, not nested under `config`. A common slip is
  putting it inside `config` — Kong then can't tell which plugin this is.
- **`route_type: llm/v1/chat`** is the chat-vs-base contract from lab-01 surfaced as a plugin
  setting. It tells `ai-proxy` to expect a chat-shaped request and forward to the chat endpoint;
  `llm/v1/completions` would be the base-model path. Mismatch it against your model and you get
  malformed forwards.
- **`config.model.options.upstream_url`** is the actual LLM target — the full URL including the
  `/v1/chat/completions` path. This is what *overrides* the route's placeholder `backendRef`.
  Provider-swapping (to a hosted model) means changing `provider`/`name`/`upstream_url` here and
  putting a real key in `auth.header_value`; the route in Step 2 never changes.
- **`auth.header_value`** is injected by `ai-proxy` toward the upstream — the client never sends
  it. For a hosted provider this is where a real `Bearer <key>` goes (ideally from a Secret); the
  local vLLM ignores it, hence the dummy string.

Apply it:

```bash
kubectl apply -f manifests/kong-ai-proxy-plugin.yaml
```

Now Kong speaks OpenAI on the front and forwards to vLLM on the back. We use `llm/v1/chat` to
match the instruct model and the `/v1/chat/completions` endpoint the whole phase uses. (Kong's
`route_type` also supports `llm/v1/completions` for base-model text completion — the same
chat-vs-base split you met in lab-01, surfaced as a plugin setting.)

**What to look for:** the plugin applies clean. The binding is the annotation, not a
backendRef — a deliberately different attachment model from kgateway. If you forget the
annotation (or the KongPlugin name doesn't match it), the request in Step 4 just hits the
placeholder backend as plain HTTP and the OpenAI normalization never happens.

## Step 4 — Test it

```bash
export PROXY_IP=$(kubectl get svc -n kong kong-gateway-proxy \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')   # pull the LoadBalancer IP into a var (empty on kind — see below)
# On kind without MetalLB, port-forward instead:
# kubectl port-forward -n kong svc/kong-gateway-proxy 8080:80 & PROXY_IP=localhost:8080

curl -s http://$PROXY_IP/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Host: llm.example.com' \   # Host MUST be llm.example.com — it's the HTTPRoute's hostname; wrong Host → 404
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"One sentence on API gateways:"}],"max_tokens":24}' \
  | python3 -m json.tool   # pretty-print the JSON response
```

- `-H 'Host: llm.example.com'` is not optional: the route only matches that hostname (Step 2),
  so without it Kong has no route to apply the plugin to and you get a 404 instead of a completion.
- The body is a plain OpenAI chat request — you're talking OpenAI to Kong, and `ai-proxy`
  forwards it to vLLM. The `model` value matches the plugin's `config.model.name`.
- The commented `port-forward` line tunnels local `:8080` to the proxy Service's `:80` in the
  background (`&`) and points `PROXY_IP` at it.

On kind, the `LoadBalancer` Service usually has no external IP, so `$PROXY_IP` comes back
empty — use the commented `port-forward` line instead. (kind has no cloud load balancer, so
`LoadBalancer` Services never get an external IP. MetalLB is an add-on that provides one;
we don't install it here, which is why we port-forward.)

**What to look for:** a normal chat completion with a `usage` block, just like lab-01 and
lab-02. Identical response surface, third path to it — that's the portability point landing.

## Step 5 — Token rate limiting, Kong-style (and an honest limitation)

Kong meters tokens with the `ai-rate-limiting-advanced` plugin — and here's a real
lesson worth more than a copy-paste: the `kong/ingress` chart you installed in Phase 05 is
the free open-source (OSS) edition, but on most builds `ai-rate-limiting-advanced` ships
only in the paid **Kong Enterprise** edition — so there's nothing to apply on your cluster.
That's why this step ships **no manifest to apply**. Contrast that with lab-02, where
kgateway's token limit was free and built in.

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
# label selector saves you from looking up the pod name (the label can vary by chart version):
kubectl logs -n kong -l app.kubernetes.io/name=gateway   # -l selects pods by label instead of a hardcoded pod name
# if that selects nothing, find the pod first: kubectl get pods -n kong  (the kong-gateway... pod)
```

- `-l app.kubernetes.io/name=gateway` streams logs from whatever pod carries that label, so you
  don't have to know the generated pod name. If the chart version uses a different label, the
  selector matches nothing — fall back to naming the `kong-gateway...` pod directly.

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
