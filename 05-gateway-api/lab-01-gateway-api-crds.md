# Lab 01: The Gateway API spec as a routing contract

**Goal:** install the Gateway API standard onto your kind cluster and *understand the
three resources* before any implementation muddies the water. By the end you'll be
able to say what each resource is for, who owns it, and why a Gateway can exist yet
route nothing.

## The problem (why this exists)

You already met `Ingress` in `03-kubernetes/lab-07`. It works, until it doesn't:

- **One flat object, many vendors.** Every controller (nginx, Traefik, HAProxy…)
  bolted its real features onto `Ingress` through **annotations**. Your "portable"
  Ingress became a pile of `nginx.ingress.kubernetes.io/...` strings that mean nothing
  to any other controller. Portable in name only.
- **No separation of concerns.** The *same* object holds the listener + TLS (a
  **platform** concern) and the path routing (an **app-team** concern). There's no
  clean line between "the cluster operator owns the front door" and "my team owns
  `/api`."
- **Weak expressiveness.** Header/method matching, weighted traffic splits,
  cross-namespace routing: all annotation hacks, all vendor-specific.

Gateway API is the CNCF's answer: replace the one overloaded object with **three
role-oriented ones**, and make the feature set part of the *spec* instead of per-vendor
annotations.

## What it replaces, and the mental model

| Resource | Who owns it | What it says | Old Ingress equivalent |
|---|---|---|---|
| **GatewayClass** | platform / cluster | *which controller* implements Gateways here (kgateway? Kong?) | `IngressClass` + the controller |
| **Gateway** | platform | "open these listeners: port + protocol + hostname + TLS" | the *listener* half of Ingress |
| **HTTPRoute** | app team | "match this path/header/method → send to these Services" | the *rules* half of Ingress |

The split is the point. The Gateway is the front door the platform team runs;
HTTPRoutes are how app teams attach their own routes to it, from their own
namespaces, without ever editing the front door. One Gateway, many teams' routes.

```
   GatewayClass  ── names ──►  a controller (kgateway / Kong)
        ▲
        │ gatewayClassName
   Gateway       ── opens ──►  :80 / :443 listeners        ◄── platform owns
        ▲
        │ parentRef
   HTTPRoute     ── matches ─►  /foo → Service A            ◄── app team owns
```

## 0. Prereqs

A running kind cluster (from `03-kubernetes/lab-01`). Verify:

```bash
kubectl cluster-info --context kind-kind
kubectl get nodes
```

If you have no cluster:

```bash
kind create cluster --name kind
```

## 1. Install the Gateway API CRDs (the spec, no implementation yet)

These are only CustomResourceDefinitions: they teach your cluster the new **nouns**
(GatewayClass / Gateway / HTTPRoute) but ship **no controller**. Nothing will route
yet, and that gap is the entire lesson of step 3.

```bash
GWAPI=v1.5.1                                  # pin the version - see github.com/kubernetes-sigs/gateway-api/releases for latest
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GWAPI}/standard-install.yaml
```

- **`standard-install.yaml`** is the *standard channel* bundle: the stable CRDs
  (GatewayClass, Gateway, HTTPRoute, ReferenceGrant). There's also an
  `experimental-install.yaml` with alpha resources (TCPRoute, TLSRoute, etc.); stick
  with standard until a lab tells you otherwise.
- **`--server-side`** because these CRDs are huge. A normal (client-side) apply stashes
  the whole manifest in a `last-applied-configuration` *annotation*, and annotations have
  a ~256 KB limit these CRDs blow right past. Server-side apply skips that annotation
  entirely. This isn't a style choice: client-side apply here errors out; note it.

Verify the new resource types now exist:

```bash
kubectl get crd | grep gateway.networking.k8s.io           # the CRDs themselves
kubectl api-resources | grep -E 'gatewayclass|^gateways|httproute'   # now usable as normal kubectl nouns
```

**What you should see:** `gatewayclasses`, `gateways`, `httproutes`, and `referencegrants`
listed as real resource types. They behave like built-in kinds now (`kubectl get gateways`
works), but creating one still does nothing, because no controller is watching. That gap
is step 3.

## 2. Read the spec from the tool (not the web)

When you don't know a field, ask the cluster: `explain` reads the live CRD schema, so
it's never out of date with your installed version:

```bash
kubectl explain gatewayclass.spec          # has spec.controllerName - names the controller, can't be changed after create
kubectl explain gateway.spec.listeners     # the front door: each listener is name + port + protocol (+ optional TLS, allowedRoutes)
kubectl explain httproute.spec.rules       # rules[].matches (path/header/method) → rules[].backendRefs (the Services)
```

`explain` reads the schema of the CRD *you installed*, field descriptions and all, so it
can't drift from your version the way a docs tab can. Map what you read back to the table:
`gatewayClassName` is the pointer from a Gateway up to its class; `listeners` is the front
door; `rules[].backendRefs` is where an HTTPRoute finally names a Service. Append a field
name to drill in (`kubectl explain gateway.spec.listeners.tls`).

## 3. Prove there's no implementation yet (spec ≠ behavior)

Create a Gateway pointing at a class that doesn't exist. (`apply -f - <<'EOF'` feeds the
manifest in from stdin, same as `apply -f file.yaml`, but inline so you can read the whole
object here):

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1   # standard-channel Gateway API group/version you installed
kind: Gateway
metadata:
  name: ghost
spec:
  gatewayClassName: does-not-exist         # THE point of this lab - names a class with no controller behind it
  listeners:
  - name: http                             # each listener needs a unique name within the Gateway
    port: 80                               # the port this front door opens...
    protocol: HTTP                         # ...and the protocol on it (HTTP | HTTPS | TLS | TCP | UDP)
EOF

kubectl get gateway ghost -o wide          # PROGRAMMED will be False / no ADDRESS
kubectl describe gateway ghost             # the 'why' lives in status.conditions
```

Two things to notice about this object:

- **`gatewayClassName` is a hard pointer, not a hint.** The Gateway is only ever as real as
  the controller behind its class. Here the class doesn't exist, so nothing acts on the
  object; it sits there, valid and inert.
- **No `allowedRoutes` here** (unlike the real Gateways in later labs). That field controls
  which namespaces may attach HTTPRoutes; it's irrelevant for the ghost because no proxy is
  ever provisioned to route to. We're testing the spec→controller gap, not routing.

**Read the status, don't skim it.** Under `status.conditions` you'll see
`Accepted=False` (reason like `NoResourcesFound` / `InvalidParameters`), or no
programmed address at all. This is the most important idea in the whole phase:

> A Gateway is a *request* for routing, not routing itself. The CRD happily stores
> your YAML; only a **controller watching that GatewayClass** turns it into a running
> proxy. No class → no controller → pending forever.

This is exactly the same lesson as a Pod stuck `Pending` with no node to schedule on:
the object is valid; the thing that *acts* on it is missing. Clean up:

```bash
kubectl delete gateway ghost
```

## Underneath: what a controller turns this into

When `lab-02` installs kgateway, a controller starts watching these objects. The
moment a Gateway names a class the controller owns, it:

1. **provisions a real proxy**: an Envoy/agentgateway pod (the *data plane*) in the
   Gateway's namespace; and
2. **compiles each HTTPRoute** into that proxy's native config. For Envoy that's
   listeners → route tables → clusters, pushed over the **xDS** API.

So `HTTPRoute` is a *high-level, portable* description; the controller compiles it
down to vendor-specific proxy config you never hand-write. And below that, the proxy
still rides the Phase 03 machinery you already own: a `backendRef` resolves to a
Service's ClusterIP, and `kube-proxy` + CoreDNS do the packet work.

```
HTTPRoute (portable spec)
   │  controller compiles ↓ (xDS)
Envoy/agentgateway routes  →  Service ClusterIP  →  kube-proxy DNAT  →  Pod
        ▲ new top floor                      ▲ everything you learned in Phase 03
```

Gateway API adds a new top floor on the stack you already understand.

## 4. Checkpoint: three questions to answer

Don't leave these as homework; you can answer them now:

1. **What problem does Gateway API solve that Ingress couldn't?** Role separation
   (platform owns the Gateway, app teams own HTTPRoutes), a cross-vendor *spec*
   instead of per-controller annotations, and built-in matching/splitting.
2. **What does it replace?** `Ingress` + `IngressClass`.
3. **What's the layer below an `HTTPRoute`?** A controller compiles it into proxy
   routing config (e.g. Envoy xDS listeners/routes/clusters), which then DNATs to a
   Service ClusterIP via kube-proxy: the Phase 03 stack.

You can now:
- [ ] Name GatewayClass / Gateway / HTTPRoute and who owns each.
- [ ] Explain why a Gateway with an unknown class never becomes ready.
- [ ] Describe what a controller turns an HTTPRoute *into*.

## Next

→ `lab-02-kgateway.md` installs kgateway (a real implementation) and creates a fresh
Gateway named `http` that becomes **programmed**: you'll watch the "ghost"
problem resolve itself the instant a controller is present.
