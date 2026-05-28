# Lab 01 — The Gateway API spec: routing as a contract, not a config file

**Goal:** install the Gateway API standard onto your kind cluster and *understand the
three resources* — before any implementation muddies the water. By the end you'll be
able to say what each resource is for, who owns it, and why a Gateway can exist yet
route nothing.

## The problem (why this exists)

You already met `Ingress` in `03-kubernetes/lab-07`. It works — until it doesn't:

- **One flat object, many vendors.** Every controller (nginx, Traefik, HAProxy…)
  bolted its real features onto `Ingress` through **annotations**. Your "portable"
  Ingress became a pile of `nginx.ingress.kubernetes.io/...` strings that mean nothing
  to any other controller. Portable in name only.
- **No separation of concerns.** The *same* object holds the listener + TLS (a
  **platform** concern) and the path routing (an **app-team** concern). There's no
  clean line between "the cluster operator owns the front door" and "my team owns
  `/api`."
- **Weak expressiveness.** Header/method matching, weighted traffic splits,
  cross-namespace routing — all annotation hacks, all vendor-specific.

Gateway API is the CNCF's answer: replace the one overloaded object with **three
role-oriented ones**, and make the feature set part of the *spec* instead of per-vendor
annotations.

## What it replaces, and the mental model

| Resource | Who owns it | What it says | Old Ingress equivalent |
|---|---|---|---|
| **GatewayClass** | platform / cluster | *which controller* implements Gateways here (kgateway? Kong?) | `IngressClass` + the controller |
| **Gateway** | platform | "open these listeners: port + protocol + hostname + TLS" | the *listener* half of Ingress |
| **HTTPRoute** | app team | "match this path/header/method → send to these Services" | the *rules* half of Ingress |

The split **is** the point. The **Gateway** is the front door the platform team runs;
**HTTPRoutes** are how app teams attach their own routes to it — from their own
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

## 1. Install the Gateway API CRDs (the spec — no implementation yet)

These are *only* CustomResourceDefinitions: they teach your cluster the new **nouns**
(GatewayClass / Gateway / HTTPRoute) but ship **no controller**. Nothing will route
yet — and that gap is the entire lesson of step 3.

```bash
# Pin the version. Check github.com/kubernetes-sigs/gateway-api/releases for latest.
GWAPI=v1.5.1
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GWAPI}/standard-install.yaml
```

(`--server-side` because these CRDs are large; client-side apply can blow the
annotation size limit. That's a real thing you'll hit — note it.)

Verify the new resource types now exist:

```bash
kubectl get crd | grep gateway.networking.k8s.io
kubectl api-resources | grep -E 'gatewayclass|^gateways|httproute'
```

You should see `gatewayclasses`, `gateways`, `httproutes` (and `referencegrants`).

## 2. Read the spec from the tool, not the web (Kelsey's rule)

When you don't know a field, ask the cluster — `explain` reads the live CRD schema, so
it's never out of date with your installed version:

```bash
kubectl explain gatewayclass.spec
kubectl explain gateway.spec.listeners
kubectl explain httproute.spec.rules
```

Map what you read back to the table above: `gatewayClassName` is the pointer from a
Gateway up to its class; `listeners` is the front door; `rules[].backendRefs` is where
an HTTPRoute finally names a Service.

## 3. Prove there's no implementation yet (spec ≠ behavior)

Create a Gateway pointing at a class that doesn't exist:

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: ghost
spec:
  gatewayClassName: does-not-exist
  listeners:
  - name: http
    port: 80
    protocol: HTTP
EOF

kubectl get gateway ghost -o wide
kubectl describe gateway ghost
```

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

## Under the hood (MIT hat): what does a controller turn this into?

When `lab-02` installs kgateway, a controller starts **watching** these objects. The
moment a Gateway names a class the controller owns, it:

1. **provisions a real proxy** — an Envoy/agentgateway **pod** (the *data plane*) in the
   Gateway's namespace; and
2. **compiles each HTTPRoute** into that proxy's native config. For Envoy that's
   listeners → route tables → clusters, pushed over the **xDS** API.

So `HTTPRoute` is a *high-level, portable* description; the controller **compiles it
down** to vendor-specific proxy config you never hand-write. And below *that*, the proxy
still rides the Phase 03 machinery you already own: a `backendRef` resolves to a
Service's ClusterIP, and `kube-proxy` + CoreDNS do the actual packet work.

```
HTTPRoute (portable spec)
   │  controller compiles ↓ (xDS)
Envoy/agentgateway routes  →  Service ClusterIP  →  kube-proxy DNAT  →  Pod
        ▲ new top floor                      ▲ everything you learned in Phase 03
```

Gateway API isn't magic — it's a new top floor on the stack you already understand.

## 4. Checkpoint — answer Stanford's three questions

Don't leave these as homework; you can answer them now:

1. **What problem does Gateway API solve that Ingress couldn't?** Role separation
   (platform owns the Gateway, app teams own HTTPRoutes), a real cross-vendor *spec*
   instead of per-controller annotations, and first-class matching/splitting.
2. **What does it replace?** `Ingress` + `IngressClass`.
3. **What's the layer below an `HTTPRoute`?** A controller compiles it into proxy
   routing config (e.g. Envoy xDS listeners/routes/clusters), which then DNATs to a
   Service ClusterIP via kube-proxy — the Phase 03 stack.

You can now:
- [ ] Name GatewayClass / Gateway / HTTPRoute and who owns each.
- [ ] Explain why a Gateway with an unknown class never becomes ready.
- [ ] Describe what a controller turns an HTTPRoute *into*.

## Next

→ `lab-02-kgateway.md` installs kgateway (a real implementation) and creates a fresh
Gateway named `http` that actually becomes **programmed** — you'll watch the "ghost"
problem resolve itself the instant a controller is present.
