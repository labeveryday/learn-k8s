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

This installs **two** things: the Kong Ingress Controller (the control plane / watcher)
and a Kong Gateway data plane (the OpenResty proxy). **What to look for:** the pods in
`kong` should reach `Running`. If `rollout status` can't find `deploy/kong-controller`,
the fallback `get pods` shows you what the chart actually named things — read it rather
than assuming.

Now the manual GatewayClass step the under-the-hood section warned about:

```bash
kubectl apply -f manifests/gatewayclass-kong.yaml

kubectl get gatewayclass kong
# wait for ACCEPTED=True before applying the Gateway below
```

Open `manifests/gatewayclass-kong.yaml` and read it — it carries
`controllerName: konghq.com/kic-gateway-controller` (this is what binds it to KIC, the
way `kgateway` bound to the kgateway controller) and the annotation
`konghq.com/gatewayclass-unmanaged: "true"` (this tells KIC: don't provision a proxy,
attach to the one Helm installed). **What to look for:** `ACCEPTED=True` means KIC saw
the class and claimed it. If it stays unaccepted, KIC isn't running — the same
"spec with no watcher" failure mode from lab 01, just with a different controller.

## 2. Gateway + HTTPRoute for Kong

```bash
kubectl apply -f manifests/kong-gateway.yaml     # gatewayClassName: kong
kubectl apply -f manifests/httpbin-route-kong.yaml

kubectl get gateway kong -o wide
kubectl get httproute httpbin-kong -o wide
```

(Reuses the `httpbin` deployment from lab 02 — re-apply `manifests/httpbin.yaml` if you
cleaned it up.)

**Look at the two manifests side by side with lab 02's.** `kong-gateway.yaml` is the
*same shape* as `kgateway-gateway.yaml` — one HTTP listener on :80 — and the only
meaningful difference is `gatewayClassName: kong` instead of `kgateway`. The HTTPRoute
differs only in name and hostname. That near-identity *is* the portability claim being
honored: you didn't learn a new config language to move engines.

**What to look for:** the Gateway reaching a `Programmed`/ready condition and the
HTTPRoute showing `Accepted`. Same status vocabulary as kgateway — because it's the same
spec — even though a Lua proxy is now honoring it instead of Envoy.

## 3. Send a request through Kong

```bash
# Kong's proxy service is in the kong namespace
kubectl -n kong get svc
kubectl -n kong port-forward svc/kong-gateway-proxy 8081:80 &

curl -s -H "Host: httpbin.kong.example.com" http://localhost:8081/get | head -20
```

**What to look for:** the same httpbin JSON echo you got through Envoy in lab 02 — but
this response was routed by nginx+Lua, not Envoy. Note the different `Host`
(`httpbin.kong.example.com`) and different port (`8081`): that's how this route stays
isolated from the kgateway one. If both labs are running, you now have **one spec served
by two engines on the same cluster**, reachable on two ports. Pause on that — it's the
thing Ingress annotations could never give you.

## 4. The Kong difference: a plugin

A pure proxy routes. Kong's identity is everything it bolts *onto* a route. Add a rate
limit of 5 requests/minute via a `KongPlugin` attached to the route:

```bash
kubectl apply -f manifests/kong-ratelimit-plugin.yaml

# hammer it
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Host: httpbin.kong.example.com" http://localhost:8081/get
done
```

**How the attachment works:** open `manifests/kong-ratelimit-plugin.yaml`. It defines a
`KongPlugin` named `rl-5-per-min` (`plugin: rate-limiting`, `config.minute: 5`,
`policy: local`) and then re-applies the HTTPRoute with the annotation
`konghq.com/plugins: rl-5-per-min`. *That annotation is the wiring* — it tells KIC to
attach the plugin to this route when it compiles the route into Kong config. Under the
hood, the rate-limit counter is the rate-limiting **Lua plugin** running in the nginx
request lifecycle, keeping a per-instance count (`policy: local`).

**What to look for:** the first 5 requests return `200`, then you start seeing `429 Too
Many Requests`. The proxy is rejecting the request *before* it ever reaches httpbin —
look at the counters Kong adds to the response headers:

```bash
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
kubectl delete -f manifests/kong-ratelimit-plugin.yaml --ignore-not-found
kubectl delete -f manifests/httpbin-route-kong.yaml --ignore-not-found
kubectl delete -f manifests/kong-gateway.yaml --ignore-not-found
# helm uninstall kong -n kong   # only if you want Kong gone
```

## Next

→ `lab-04-kgateway-vs-kong.md` — no install. You've now run both engines; the next lab
turns that hands-on contrast into a decision framework you can defend.
