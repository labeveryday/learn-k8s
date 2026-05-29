# Lab 03 — Kong: the same spec, a different engine underneath

**Goal:** run the *same* HTTPRoute through **Kong** instead of kgateway, prove the
portability the Gateway API promised, then add a Kong **plugin** (rate limiting) — the
extension mechanism that is Kong's whole identity. By the end you'll have driven one
portable spec into two unrelated data planes.

## The problem (why this exists)

Lab 02 ended with a working gateway — but on *one* vendor's engine (Envoy, via
kgateway). The original sin of Ingress was that switching vendors meant rewriting your
config: nginx annotations meant nothing to HAProxy, Traefik config meant nothing to
Kong. The Gateway API claims to have fixed that — the *spec* is portable, the
*implementation* is swappable.

That's a claim. This lab tests it. If Gateway API is real, you should be able to take an
HTTPRoute, change essentially nothing, point it at a different controller, and have it
work on a data plane that shares zero code with Envoy. Let's see.

## What it replaces, and why that was insufficient

Kong is an API gateway built on a **proxy core plus a plugin ecosystem** (auth, rate
limiting, transformations, observability, and — Phase 06 — AI). It implements both its
own classic `Ingress` and the Gateway API.

The thing Kong replaces here is not kgateway — both are valid Gateway API
implementations. What Kong replaces is the *idea that gateway features must be built into
the proxy or hacked in via per-vendor annotations*. With Ingress, "add rate limiting"
meant finding the one magic `nginx.ingress.kubernetes.io/limit-rps` annotation your
specific controller happened to support, with no portable equivalent. Kong's answer is a
first-class, declarative **plugin** object you attach to a route. The limitation it
removes: behavior is no longer an annotation string buried in metadata — it's a typed
resource you can review, version, and reason about.

## Under the hood (MIT hat): same spec, a completely different compiler

Here is the payoff of the whole phase. You are about to feed the **identical Gateway API
spec** into a data plane that is *nothing like* Envoy.

- **kgateway's data plane is Envoy** — a C++ proxy configured over xDS.
- **Kong's data plane is OpenResty** — nginx compiled with the `lua-nginx-module`, with
  Kong itself running as a **Lua application inside nginx**. Routing, plugins, and rate
  limiting are Lua executing in the nginx request lifecycle.

The control plane is the **Kong Ingress Controller (KIC)**. It watches the same
`Gateway`/`HTTPRoute` objects kgateway does and **translates them into Kong's native
configuration**, which it pushes to the Kong data plane via Kong's **Admin API**
(in DB-less mode, Kong holds that config in memory). So the compilation target is
completely different — Kong entities and Lua config instead of Envoy clusters and xDS —
but *your input YAML is the same*.

```
HTTPRoute (the SAME portable spec from lab 02)
   │
   ├── kgateway controller ──► Envoy config (xDS)        ──► Envoy data plane
   │
   └── Kong controller (KIC) ──► Kong config (Admin API) ──► OpenResty data plane
                                                                 (nginx + Lua)
        ▲ ONE spec                                         ▲ TWO unrelated runtimes
```

Below *both* runtimes, the floor is identical: a `backendRef` resolves to the `httpbin`
Service's ClusterIP, and kube-proxy + CoreDNS do the packet work (Phase 03). Two
completely different proxies, the same foundation.

One more under-the-hood note that drives step 1: kgateway *auto-registered* its
GatewayClass when its controller started. KIC does **not** — you must create the
`kong` GatewayClass yourself, and mark it *unmanaged* so KIC binds to the data plane
Helm already installed instead of trying to provision a new one. Different controllers,
different provisioning models, same spec on top.

## 0. Prereq

Gateway API CRDs from lab 01, and ideally kgateway still running from lab 02 so you can
*see both engines side by side*. To stop two controllers fighting over the same
`Gateway`, Kong gets its **own GatewayClass, its own namespace, and a different
hostname** than lab 02. Isolation by class + hostname is how multiple implementations
coexist in one cluster.

## 1. Install Kong via Helm

```bash
helm repo add kong https://charts.konghq.com
helm repo update

helm install kong kong/ingress -n kong --create-namespace
kubectl -n kong rollout status deploy/kong-controller || \
  kubectl -n kong get pods
```

The `||` is a safety net, not an error: the chart's deployment name can vary by version,
so if `rollout status` can't find `deploy/kong-controller`, the fallback `get pods` runs
and shows you what the chart actually named things — read it rather than assuming.

This installs **two** things: the Kong Ingress Controller (the control plane / watcher)
and a Kong Gateway data plane (the OpenResty proxy). **What to look for:** the pods in
`kong` should reach `Running`.

Now the manual GatewayClass step the under-the-hood section warned about. This is the
one object Kong makes you create by hand — `manifests/gatewayclass-kong.yaml`. It's tiny,
but every field is load-bearing:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass             # cluster-scoped; the "which engine" object, like a StorageClass
metadata:
  name: kong                   # Gateways reference this by gatewayClassName: kong (step 2)
  annotations:
    konghq.com/gatewayclass-unmanaged: "true"   # KIC: do NOT spin up a new proxy —
                                                # attach to the data plane Helm installed
spec:
  controllerName: konghq.com/kic-gateway-controller   # MUST match the controller string KIC
                                                       # watches for; this is what binds the
                                                       # class to KIC (cf. kgateway's controller)
```

- `controllerName` is the binding key. KIC only claims GatewayClasses whose `controllerName`
  is exactly `konghq.com/kic-gateway-controller`. A typo here = no controller ever looks at
  the class = it sits `ACCEPTED` blank forever. (This is the analog of `kgateway`'s class
  binding to *its* controller string in lab 01/02.)
- The `unmanaged` annotation is Kong-specific and the whole reason this step is manual:
  without it, KIC's default is to *provision* a data plane for the class. You already have
  one (Helm installed it in this same step), so you tell KIC to **bind to the existing
  proxy** instead of standing up a second one.

```bash
kubectl apply -f manifests/gatewayclass-kong.yaml

kubectl get gatewayclass kong
# wait for ACCEPTED=True before applying the Gateway below
```

**What to look for:** `ACCEPTED=True` means KIC saw the class and claimed it. If it stays
unaccepted, KIC isn't running — the same "spec with no watcher" failure mode from lab 01,
just with a different controller.

## 2. Gateway + HTTPRoute for Kong

This is where the portability claim gets tested. Here is the Kong Gateway
(`manifests/kong-gateway.yaml`) — and notice it is **the same shape** as lab 02's
`kgateway-gateway.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: kong                   # NOT in kgateway-system; this Gateway lives in your current ns
spec:
  gatewayClassName: kong       # THE one meaningful diff vs lab 02 (which said: kgateway)
  listeners:
  - name: http
    port: 80                   # the proxy listens on :80
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: All               # HTTPRoutes in ANY namespace may attach to this listener
```

Compare to lab 02's `kgateway-gateway.yaml`: same `listeners` block, same `allowedRoutes`,
same `port: 80` — the *only* meaningful difference is `gatewayClassName: kong` instead of
`kgateway` (and lab 02's Gateway was named `http` in `kgateway-system`; this one is `kong`
in your current namespace, kept distinct so the two engines don't fight). That near-identity
*is* the portability claim: you didn't learn a new config language to move engines.

And the route (`manifests/httpbin-route-kong.yaml`) — identical to lab 02's HTTPRoute except
its name and hostname:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: httpbin-kong           # distinct name so it doesn't clash with lab 02's route
spec:
  parentRefs:
  - name: kong                 # attach to the Gateway above (NOT the kgateway one)
  hostnames:
  - httpbin.kong.example.com   # distinct host → keeps Kong's traffic isolated from kgateway's
  rules:
  - matches:
    - path: { type: PathPrefix, value: / }   # match every path under /
    backendRefs:
    - name: httpbin            # forward to the httpbin Service…
      port: 8000               # …on its Service port 8000 (NOT the container's 8080 — see below)
```

> **Beginner gotcha — `port: 8000` is the *Service* port, not the container port.** The
> httpbin container listens on `8080`, but the Service (`manifests/httpbin.yaml`) exposes
> `port: 8000` → `targetPort: 8080`. `backendRefs.port` must match the **Service** port
> (`8000`); the Service does the `8000 → 8080` translation. Putting `8080` here resolves to
> a port the Service doesn't expose and the route won't forward.

```bash
kubectl apply -f manifests/kong-gateway.yaml     # gatewayClassName: kong → KIC compiles it
kubectl apply -f manifests/httpbin-route-kong.yaml

kubectl get gateway kong -o wide
kubectl get httproute httpbin-kong -o wide
```

(Reuses the `httpbin` Deployment + Service from lab 02 — re-apply `manifests/httpbin.yaml`
if you cleaned it up. That file is one Deployment, `replicas: 1`, image
`mccutchen/go-httpbin:v2.15.0`, plus the `httpbin` Service that maps `8000 → 8080`.)

**What to look for:** the Gateway reaching a `Programmed`/ready condition and the
HTTPRoute showing `Accepted`. Same status vocabulary as kgateway — because it's the same
spec — even though a Lua proxy is now honoring it instead of Envoy.

## 3. Send a request through Kong

```bash
# Kong's proxy service is in the kong namespace
kubectl -n kong get svc
kubectl -n kong port-forward svc/kong-gateway-proxy 8081:80 &   # local 8081 → proxy :80, backgrounded

# -H "Host: ..." sets the Host header the route matches on (hostnames: in the route);
# without it the proxy can't pick this route. localhost:8081 hits the forwarded proxy.
curl -s -H "Host: httpbin.kong.example.com" http://localhost:8081/get | head -20
```

This is a **second** background port-forward (lab-02's is likely still running on `8080`);
this one uses port `8081` to avoid colliding with it. Same stop options as lab-02:
`kill %1` or `pkill -f port-forward` (the latter stops *all* of them).

**What to look for:** the same httpbin JSON echo you got through Envoy in lab 02 — but
this response was routed by nginx+Lua, not Envoy. Note the different `Host`
(`httpbin.kong.example.com`) and different port (`8081`): that's how this route stays
isolated from the kgateway one. If both labs are running, you now have **one spec served
by two engines on the same cluster**, reachable on two ports. Pause on that — it's the
thing Ingress annotations could never give you.

## 4. The Kong difference: a plugin

A pure proxy routes. Kong's identity is everything it bolts *onto* a route. Add a rate
limit of 5 requests/minute via a `KongPlugin` — Kong's typed, declarative extension object
(`manifests/kong-ratelimit-plugin.yaml`). It's two documents in one file: the plugin
itself, then the route re-applied with an annotation that *wires* the plugin to it.

```yaml
apiVersion: configuration.konghq.com/v1   # Kong's own CRD group — NOT gateway.networking.k8s.io
kind: KongPlugin
metadata:
  name: rl-5-per-min          # the name the route's annotation will reference (below)
plugin: rate-limiting         # which Kong plugin to run; this is a TOP-LEVEL field, not under spec
config:                       # plugin-specific config (also top-level on KongPlugin, not spec)
  minute: 5                   # allow 5 requests per minute, then start returning 429
  policy: local               # count per proxy instance in-memory (no Redis/DB needed)
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: httpbin-kong          # SAME route as step 2 — re-applied, not a new one
  annotations:
    konghq.com/plugins: rl-5-per-min   # <-- THE WIRING: attach the plugin above to this route
spec:                         # spec is otherwise identical to httpbin-route-kong.yaml
  parentRefs:
  - name: kong
  hostnames:
  - httpbin.kong.example.com
  rules:
  - matches:
    - path: { type: PathPrefix, value: / }
    backendRefs:
    - name: httpbin
      port: 8000
```

Two CRD details that trip people up:

- **`plugin:` and `config:` are top-level fields on `KongPlugin`, not nested under `spec:`.**
  This CRD breaks the usual `spec:` convention — putting them under `spec` is the most common
  KongPlugin mistake.
- **The annotation is the only thing that connects the two.** A `KongPlugin` does nothing on
  its own; it activates when an object references it by name in `konghq.com/plugins`. Here that
  object is the route, so the limit applies to all traffic on `httpbin-kong`. (You could attach
  the same plugin to a Service or Ingress the same way.)

```bash
kubectl apply -f manifests/kong-ratelimit-plugin.yaml   # applies BOTH docs: plugin + annotated route

# hammer it: 8 requests, print only the HTTP status code of each
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Host: httpbin.kong.example.com" http://localhost:8081/get
done
```

- `-o /dev/null` throws away the body; `-w "%{http_code}\n"` prints just the status — so the
  loop's output is a column of `200`s flipping to `429`s, which is exactly the signal you want.

*That annotation is the wiring* — it tells KIC to attach the plugin to this route when it
compiles the route into Kong config. Under the hood, the rate-limit counter is the
rate-limiting **Lua plugin** running in the nginx request lifecycle, keeping a per-instance
count (`policy: local`).

**What to look for:** the first 5 requests return `200`, then you start seeing `429 Too
Many Requests`. The proxy is rejecting the request *before* it ever reaches httpbin —
look at the counters Kong adds to the response headers:

```bash
# -i includes response HEADERS in the output; grep keeps just the rate-limit ones
curl -is -H "Host: httpbin.kong.example.com" http://localhost:8081/get \
  | grep -i ratelimit
```

You'll see headers like `RateLimit-Limit` and `RateLimit-Remaining` — the Lua plugin
reporting its state to the client. This is the conceptual seed of Phase 06: in an AI
gateway you swap "5 requests/min" for "10,000 tokens/min," and the gateway enforces it
at the same spot in the request lifecycle.

## 5. Break it, then read the error

Make a deliberately wrong route and learn what the failure reveals. Point the Kong
HTTPRoute's single backendRef at a Service that doesn't resolve — the most instructive
failure, because here there's no second backend to fall back to.

```bash
kubectl patch httproute httpbin-kong --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/backendRefs/0/name","value":"does-not-exist"}]'

kubectl describe httproute httpbin-kong
```

(Same JSON Patch trick as lab-02 step 6 — `op: replace` overwrites the value at the
file-path-like `path`; here it renames the route's only backend to a missing Service.)

**Read the condition.** The route's status flips to `ResolvedRefs=False` — *the same
condition kgateway reported in lab 02 for the same mistake*. That's the lesson: because
both engines implement the same spec, they report failures in the **same status
vocabulary**, even though one is Envoy and the other is Lua. The portable spec covers
the error model too, not just the happy path. A `curl` now returns a `503` from Kong's
proxy — the route's *only* backend is unresolvable, so the proxy has nowhere to forward
and answers directly (the spec calls for `503` when forwarding fails entirely with no
filter to respond). The error comes from the gateway, not your app.

Restore it:

```bash
kubectl apply -f manifests/httpbin-route-kong.yaml
```

(That re-apply also strips the rate-limit annotation, since the plain route manifest
doesn't carry it — re-apply `kong-ratelimit-plugin.yaml` if you want the limit back.)

## 6. Checkpoint — you can now explain…

Answer these out loud:

1. **What did you just prove about the Gateway API?** That it's genuinely portable:
   the same HTTPRoute, changed only in name/hostname/class, ran on two unrelated data
   planes — Envoy (lab 02) and OpenResty/nginx+Lua (this lab) — with the same status
   conditions and the same backend.
2. **What turns your HTTPRoute into Kong behavior?** The Kong Ingress Controller (KIC)
   compiles `Gateway`/`HTTPRoute` (and KongPlugin) into Kong's native config and pushes
   it to the OpenResty data plane via the Admin API. Routing and the rate-limit counter
   run as Lua inside nginx.
3. **How is Kong's extension model different from kgateway's?** Kong attaches typed
   `KongPlugin` resources to routes via a `konghq.com/plugins` annotation — a first-class,
   declarative replacement for the per-vendor Ingress annotations that never ported.

You can now:
- [ ] Explain control plane (KIC) vs data plane (OpenResty) for Kong.
- [ ] Say why you had to create the `kong` GatewayClass manually (no auto-register) and
      what `unmanaged` does.
- [ ] Attach a plugin to a route and read its `429` + rate-limit headers.
- [ ] Recognize `ResolvedRefs=False` as the *same* portable error across both engines.

## Cleanup

```bash
pkill -f port-forward    # stop the step-3 background port-forward(s)
kubectl delete -f manifests/kong-ratelimit-plugin.yaml --ignore-not-found
kubectl delete -f manifests/httpbin-route-kong.yaml --ignore-not-found
kubectl delete -f manifests/kong-gateway.yaml --ignore-not-found
# helm uninstall kong -n kong   # only if you want Kong gone
```

## Next

→ `lab-04-kgateway-vs-kong.md` — no install. You've now run both engines; the next lab
turns that hands-on contrast into a decision framework you can defend.
