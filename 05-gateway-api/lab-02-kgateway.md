# Lab 02: kgateway brings the "ghost" Gateway to life

**Goal:** install a *real* Gateway API implementation, expose a demo app through a
`Gateway` + `HTTPRoute`, get a `200` through Envoy, then split traffic between two
versions by changing two numbers. By the end you'll be able to say what
kgateway *turns your YAML into*, and watch the lab-01 "ghost" problem resolve the
instant a controller exists.

## The problem (why this exists)

Lab 01 left you with a working contract and nothing to honor it. You installed the
Gateway API CRDs (the cluster knows the *nouns*) but the Gateway you created sat
`Accepted=False` forever because no controller was watching its class. A spec with
no implementation is a request nobody picks up.

You need a piece of software that:

1. watches `GatewayClass`/`Gateway`/`HTTPRoute` objects,
2. stands up an actual proxy to receive traffic, and
3. translates your portable HTTPRoute into that proxy's native config.

That software is an *implementation*. kgateway is one.

## What it replaces, and why that was insufficient

kgateway is the CNCF project formerly known as **Gloo Gateway**. It is a **control
plane for Envoy**, the same data-plane proxy that backs most service meshes and a large
share of API gateways in production.

What does it replace? The hand-rolled, vendor-specific layer that used to sit between
your intent and a running proxy:

- With **Ingress + nginx**, your "config" was a pile of
  `nginx.ingress.kubernetes.io/...` annotations the nginx controller rendered into an
  `nginx.conf`. Portable in name only: none of it meant anything to another controller.
- With raw **Envoy**, you'd hand-author listener/route/cluster YAML and push it over
  xDS yourself. That works, but you own the whole compiler.

kgateway removes both burdens: you write the *standard* Gateway API spec, and the
controller does the Envoy compilation for you. The limitation it removes is the one
lab-01 named, no implementation behind the spec, without dragging you back into
vendor-specific config.

## Underneath: what kgateway turns this into

This is the section to slow down on. When the Gateway in step 3 names
`gatewayClassName: kgateway`, the kgateway controller (watching since
you installed it in step 1) does two things:

1. **Provisions a data-plane proxy.** It creates a real **Envoy pod** (kgateway also
   ships an AI-first `agentgateway` data plane, the Phase 06 path) plus a
   `Service` in `kgateway-system`, named after the Gateway. Your Gateway is named
   `http`, so you'll see a `Deployment`/`Service` called `http` appear. *kgateway is the
   control plane; that pod is the data plane.*
2. **Compiles each HTTPRoute into that proxy's config.** Your portable HTTPRoute
   (hostname + path → backendRef) becomes Envoy's native objects (**listeners → route
   tables → clusters**) pushed to the proxy over the **xDS** API. The weighted split
   in step 5 becomes Envoy load-balancing across two clusters by weight.

You never hand-write any of that Envoy config. The HTTPRoute is the high-level,
portable description; the controller is the *compiler*; Envoy is the *runtime*.

And below Envoy, nothing is new: it's the Phase 03 stack you already own. A
`backendRef` names a `Service`; that Service has a ClusterIP; `kube-proxy` DNATs to a
Pod IP; CoreDNS resolves the names. Gateway API is a new top floor, not a replacement
foundation.

```
HTTPRoute (portable spec, you wrote this)
   │  kgateway controller COMPILES ↓  (pushes via xDS)
Envoy proxy pod "http"  (listeners → routes → clusters)   ◄── new top floor
   │  picks a backendRef, by weight
Service httpbin (ClusterIP :8000)
   │  kube-proxy DNAT  +  CoreDNS
Pod (go-httpbin :8080)                                     ◄── everything from Phase 03
```

The whole lab is watching this picture assemble itself.

## 0. Prereq

Lab 01 done (Gateway API CRDs installed). Confirm:

```bash
kubectl get crd gateways.gateway.networking.k8s.io
helm version   # installed in 00-prep
```

If `get crd` errors, the spec isn't installed; go back to lab 01. (This is the same
"the nouns must exist first" lesson: the controller you're about to install only knows
what to watch because these CRDs taught the cluster the words.)

## 1. Install kgateway via Helm

kgateway ships as **OCI Helm charts** in two pieces: its *own* CRDs (policy resources
that extend the standard Gateway API), then the controller. Install CRDs first so the
controller's schema dependencies exist when it starts.

`helm upgrade -i` is `helm install` (from 03/lab-11) with the `-i`/`--install` flag:
install if absent, upgrade if present: idempotent, so re-running these is safe.

```bash
# Pin the version - check kgateway.dev / GitHub releases for current.
KGW=v2.3.1

helm upgrade -i --create-namespace -n kgateway-system \
  --version ${KGW} kgateway-crds \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds

helm upgrade -i -n kgateway-system \
  --version ${KGW} kgateway \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway

kubectl -n kgateway-system rollout status deploy/kgateway
```

**What to look for:** `rollout status` must print `successfully rolled out`. That's the
*control plane*, the watcher, now running. No data-plane proxy exists yet; there's no
Gateway for it to provision one against.

The decisive difference from lab 01 appears now:

```bash
kubectl get gatewayclass
# expect a class named "kgateway" with ACCEPTED=True
```

In lab 01 you pointed a Gateway at `does-not-exist` and it hung. Here the controller
auto-registered a `kgateway` GatewayClass and marked it `ACCEPTED=True`, meaning
"a controller is present and will honor Gateways that name me." This is the missing
half of lab-01's ghost.

## 2. Deploy a demo app

`httpbin` (the `go-httpbin` image) echoes the incoming request back as JSON, so when
routing works, you can *see* the exact request the proxy forwarded. Nothing here is new:
it's a plain Phase 03 Deployment + Service (`manifests/httpbin.yaml`):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: httpbin
  labels: { app: httpbin }
spec:
  replicas: 1
  selector:
    matchLabels: { app: httpbin }      # owns Pods carrying app=httpbin
  template:
    metadata:
      labels: { app: httpbin }         # MUST equal the selector above (lab-03 trap)
    spec:
      containers:
      - name: httpbin
        image: mccutchen/go-httpbin:v2.15.0   # pinned; this image echoes the request as JSON
        ports:
        - containerPort: 8080          # the app listens on 8080 INSIDE the pod
        readinessProbe:
          httpGet: { path: /get, port: 8080 }  # not "Ready" until /get answers → no endpoint until then
          initialDelaySeconds: 2
        resources:
          requests: { cpu: 50m, memory: 64Mi }
          limits:   { cpu: 200m, memory: 128Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: httpbin                        # this NAME is what the HTTPRoute's backendRef points at
spec:
  selector: { app: httpbin }           # routes to Pods with app=httpbin (the Deployment's Pods)
  ports:
  - name: http
    port: 8000                         # the Service port - backendRef will say port: 8000
    targetPort: 8080                   # ...and forwards to the pod's 8080. The 8000≠8080 gap trips people up.
```

```bash
kubectl apply -f manifests/httpbin.yaml
kubectl rollout status deploy/httpbin   # blocks until the readinessProbe passes and the Pod is Ready
```

**What to look for:** the `Service httpbin` on port `8000` (targeting container `8080`)
is the `backendRef` your HTTPRoute will name, the bridge from the new top floor down to
the Phase 03 ClusterIP machinery. Confirm it has an endpoint: `kubectl get endpoints
httpbin` should list a Pod IP, not be empty. **Gotcha:** an empty endpoint list almost
always means the readinessProbe hasn't passed: a Service only adds *Ready* Pods to its
endpoints, so the route would resolve but have nowhere to send traffic.

## 3. Create a Gateway + HTTPRoute

Two objects, two roles (the lab-01 separation made concrete). First the **Gateway**,
the platform-owned front door (`manifests/kgateway-gateway.yaml`):

```yaml
apiVersion: gateway.networking.k8s.io/v1   # standard Gateway API group/version (Gateway API v1.5.1)
kind: Gateway
metadata:
  name: http                       # kgateway names the proxy Deployment/Service after THIS
  namespace: kgateway-system
spec:
  gatewayClassName: kgateway       # this is what hands the Gateway to the kgateway controller
  listeners:
  - name: http
    port: 80                       # the proxy listens on :80 (you port-forward to this in step 4)
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: All                  # HTTPRoutes in ANY namespace may attach - wide open for the lab
```

Then the **HTTPRoute**, the app-developer-owned routing rule
(`manifests/httpbin-route.yaml`):

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: httpbin
spec:
  parentRefs:
  - name: http                     # attach to the Gateway above BY NAME...
    namespace: kgateway-system     # ...which lives in another namespace, so this is required
  hostnames:
  - httpbin.example.com            # Envoy only matches requests carrying THIS Host header
  rules:
  - matches:
    - path: { type: PathPrefix, value: / }   # every path (prefix "/") matches this rule
    backendRefs:
    - name: httpbin                # the Service from step 2...
      port: 8000                   # ...on its Service port 8000 (NOT the pod's 8080)
```

Two beginner gotchas live in this pair: the HTTPRoute's `parentRefs.namespace` **must**
name the Gateway's namespace (it's cross-namespace, so omitting it looks for `http` in
the route's own namespace and silently never attaches); and `backendRefs.port` is the
**Service** port (`8000`), not the container port (`8080`): getting that wrong gives you
a route that resolves but sends traffic to a closed port.

```bash
kubectl apply -f manifests/kgateway-gateway.yaml      # Gateway "http" in kgateway-system
kubectl apply -f manifests/httpbin-route.yaml         # HTTPRoute -> httpbin

kubectl -n kgateway-system get gateway
kubectl get httproute httpbin -o wide
```

This is the moment from "Underneath." Naming `gatewayClassName: kgateway` triggers
the controller to provision the data-plane proxy. **Read the Gateway status, don't skim
it:**

```bash
kubectl -n kgateway-system describe gateway http
```

Watch for `PROGRAMMED=True` under `status.conditions`. The vocabulary matters:

- `Accepted=True`: "the spec is valid and I (the controller) own this class."
- `Programmed=True`: "I have provisioned the proxy and pushed config to it." This is
  the condition lab-01's ghost could never reach, because no controller existed to
  program anything.

On the HTTPRoute, look for the parent condition `Accepted=True` (it attached to the
Gateway) and `ResolvedRefs=True` (it found the `httpbin` Service). If `ResolvedRefs` is
`False`, the route compiled but points at a backend that doesn't exist; see step 6.

Now confirm the proxy the controller built for you:

```bash
kubectl -n kgateway-system get deploy,svc,pods
# a Deployment/Service named "http" (after the Gateway) should now exist - the Envoy data plane
```

That `http` pod did not exist 30 seconds ago. The controller created it *because* you
asked for a Gateway. That's the control-plane → data-plane mechanism, live.

## 4. Send a real request

On kind there's no external load balancer, so the Gateway's Service has no external IP.
Port-forward the proxy Service kgateway created (its name follows the Gateway name):

```bash
kubectl -n kgateway-system get svc   # find the gateway's proxy service, e.g. "http"
kubectl -n kgateway-system port-forward svc/http 8080:80 &

curl -s -H "Host: httpbin.example.com" http://localhost:8080/get | head -20
```

The `&` backgrounds port-forward so you keep your prompt; it must stay running for steps
4-6 (`8080:80` maps `localhost:8080` → the proxy service's `:80`). Stop it when done with
`fg` then Ctrl-C, or `kill %1` / `pkill -f port-forward`; re-running it while it's still
up fails with "address already in use." `/get` is one of go-httpbin's built-in endpoints
that returns the request back as JSON.

**What to look for:** httpbin echoes your request as JSON. The `Host` header matters:
the HTTPRoute has `hostnames: [httpbin.example.com]`, so Envoy's route table only
matches requests carrying that host. Drop the header and you'll get a `404` from Envoy,
because the request matched no route. That `404` is Envoy, not your app: the proxy
decided there was nowhere to send it.

Trace the full path in your head against the diagram: curl → port-forward → Envoy pod
`http` → (route table match on host) → cluster for `httpbin:8000` → ClusterIP →
kube-proxy DNAT → go-httpbin pod → JSON back. **That's a request through Envoy,
programmed by an HTTPRoute, served by your pod.** Floor 1 works.

## 5. Traffic splitting (the kgateway payoff)

Deploy a v2 of the app and send 80/20 of traffic to it: no code change, only weights
on two `backendRefs`. `httpbin-v2.yaml` is a near-clone of step 2's manifest (same image,
`app: httpbin-v2` labels, its own `Service httpbin-v2:8000`): a second backend to split
*toward*. The interesting object is the **split route** (`manifests/httpbin-route-split.yaml`),
which replaces the single-backend route from step 3:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: httpbin                    # SAME name as step 3 - apply overwrites the old single-backend route
spec:
  parentRefs:
  - name: http
    namespace: kgateway-system
  hostnames:
  - httpbin.example.com
  rules:
  - matches:
    - path: { type: PathPrefix, value: / }
    backendRefs:
    - name: httpbin
      port: 8000
      weight: 80                   # ~80% of requests go to v1...
      filters:
      - type: RequestHeaderModifier # ...and get X-Version: v1 stamped on BEFORE forwarding
        requestHeaderModifier:
          set:
          - name: X-Version
            value: v1
    - name: httpbin-v2
      port: 8000
      weight: 20                   # ~20% to v2, with X-Version: v2
      filters:
      - type: RequestHeaderModifier
        requestHeaderModifier:
          set:
          - name: X-Version
            value: v2
```

The `weight` numbers are *relative*, not percentages: `80` and `20` happen to sum to 100,
but `4`/`1` would split identically. The per-backend `RequestHeaderModifier` filter is what
lets you *see* which side won each request: it sets the `X-Version` header before Envoy
forwards, and go-httpbin echoes it back.

```bash
kubectl apply -f manifests/httpbin-v2.yaml
kubectl apply -f manifests/httpbin-route-split.yaml   # backendRefs with weights

for i in $(seq 1 10); do
  curl -s -H "Host: httpbin.example.com" http://localhost:8080/get \
    | jq -r '.headers["X-Version"][0]'
done
```

**How the X-Version header works:** the split route attaches a `RequestHeaderModifier`
filter to each backendRef that stamps `X-Version: v1` or `v2` onto the request *before*
forwarding. go-httpbin echoes request headers in its `/get` JSON, and because Go models
headers as `map[string][]string`, the field comes back as an *array*:
`"X-Version":["v1"]`. That's why the jq path is `.headers["X-Version"][0]`: read the
first element. (`jq` is a command-line JSON parser, installed in 00-prep; here it pulls
one field out of httpbin's JSON.) No `jq`? Match the array directly:
`grep -o '"X-Version":\[[^]]*\]'`.

**What to look for:** roughly 8 of 10 lines say `v1`, 2 say `v2`. That ratio *is* Envoy
load-balancing across two clusters by their weights, the mechanism made
visible. Change the weights in the manifest, re-apply, and the ratio moves. This is
canary / blue-green in two numbers of YAML, and it's something Ingress annotations could
only fake per-vendor.

## 6. Break it, then read the error

Routing works; now make it fail on purpose and learn to read what the failure tells you
about the architecture. Point the route at a backend that doesn't exist:

```bash
kubectl patch httproute httpbin --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/backendRefs/0/name","value":"does-not-exist"}]'

kubectl describe httproute httpbin
```

This is a **JSON Patch**: `op: replace` overwrites the value at `path`, where the path
walks the object like a file path and each `0` is the first array element (rule 0,
backendRef 0). It renames the backend to a Service that doesn't exist.

**Read the condition.** The route's parent status flips to `ResolvedRefs=False` with a
reason like `BackendNotFound`. Note what did *not* change: the Gateway is still
`Programmed=True`, and the *other* weighted backend still serves. The failure is scoped
to one backendRef, not the whole front door: that's the role separation from lab 01
showing up as fault isolation. A broken app-team route can't take down the platform's
Gateway.

Now `curl` it a handful of times. Per the Gateway API spec, when only *part* of a
weighted route is invalid, the controller keeps the route Accepted and synthesizes an
error response for the share of traffic that would have hit the missing backend:
typically an `HTTP 500` from the proxy itself (some implementations surface a `503`).
Either way, that error code comes *from the gateway*, not your app: the proxy has no
cluster to forward that weight to, so it answers directly. The error code is the data
plane reporting an unroutable weight; the `ResolvedRefs=False` condition is the control
plane explaining *why*. Two layers, two signals, same root cause. Because you broke
the **weight-80** `httpbin` backend, it's the ~20% still routed to the valid
`httpbin-v2` that keeps returning `200`. The fault is scoped to one backendRef, not the
whole route.

Restore it:

```bash
kubectl apply -f manifests/httpbin-route-split.yaml
```

## 7. (Optional) Real LoadBalancer on kind

If you want `LoadBalancer` to get an IP instead of port-forwarding:

```bash
# separate terminal, leave it running
# Easiest: grab a prebuilt binary from the cloud-provider-kind GitHub releases.
# Or, if you have the Go toolchain (NOT part of 00-prep), build it - go install
# drops the binary in ~/go/bin:
go install sigs.k8s.io/cloud-provider-kind@latest
sudo ~/go/bin/cloud-provider-kind
kubectl -n kgateway-system get svc -w   # the gateway svc should get an EXTERNAL-IP
```

This is the shim that fakes a cloud load balancer. On LKE (Phase 09) you don't need it:
a NodeBalancer gives the Gateway a real public IP automatically.

## 8. Checkpoint: you can now explain…

Answer these out loud; don't leave them as homework:

1. **Why did the lab-01 ghost never become ready, but this Gateway did?** Because lab 01
   had a spec and no controller. Installing kgateway added the *watcher* that
   auto-registered a `kgateway` GatewayClass and, on seeing a Gateway naming it,
   provisioned the proxy and pushed config, reaching `Programmed=True`.
2. **What does kgateway turn an HTTPRoute into?** Envoy data-plane config (listeners →
   route tables → clusters), pushed to a per-Gateway proxy pod over xDS. You write
   portable YAML; the controller compiles it to vendor proxy config.
3. **What's below Envoy?** The Phase 03 stack: backendRef → Service ClusterIP →
   kube-proxy DNAT → Pod, with CoreDNS resolving names.

You can now:
- [ ] Explain control plane (kgateway) vs data plane (the `http` Envoy pod).
- [ ] Distinguish `Accepted` from `Programmed` and say which one lab-01 could never reach.
- [ ] Read `ResolvedRefs=False` / a `500`/`503` and locate the fault to one backendRef.
- [ ] Shift live traffic by weight and say what Envoy is doing underneath.

## Cleanup (keep CRDs)

```bash
pkill -f port-forward    # stop the step-4 background port-forward
kubectl delete -f manifests/httpbin-route-split.yaml --ignore-not-found
kubectl delete -f manifests/httpbin-route.yaml --ignore-not-found
kubectl delete -f manifests/kgateway-gateway.yaml --ignore-not-found
# leave kgateway + httpbin installed if you're going straight to lab 03/06
```

Deleting the Gateway tears down the proxy pod the controller provisioned; confirm with
`kubectl -n kgateway-system get pods`. Control plane gives life to the data plane; remove
the request and the proxy goes with it.

## Next

→ `lab-03-kong.md` runs the exact same HTTPRoute through a completely different
engine: Kong's OpenResty (nginx + Lua) data plane instead of Envoy. Same portable spec,
different compiler underneath. That contrast is the payoff Ingress annotations never
delivered.
