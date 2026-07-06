# 05: Gateway API with kgateway and Kong

> Floor 1 of the platform. How traffic gets *into* the cluster, the modern way.

## Why this phase exists

In `03-kubernetes/lab-07` you used **Ingress**. Ingress is frozen (no new features),
and every controller bolted its real behavior on through annotations, so an NGINX
Ingress and a Kong Ingress were never portable. The community replaced it with the
**Gateway API**: a richer, role-oriented, vendor-neutral standard. This is what you'll
see in production from 2024 onward.

Gateway API is a specification, not software you run. You install the CRDs (the spec)
once, then install an *implementation* that does the real work. The spec is portable;
the engine underneath is swappable. This phase proves it: you install two
implementations, kgateway (Envoy-based, CNCF) and Kong (OpenResty-based), against the
*same* spec and watch the *same* `HTTPRoute` work on both. That portability is the
dividend Ingress annotations could never pay.

## Objectives

By the end you can:

1. Explain why Gateway API replaced Ingress, and name its three core resources.
2. Install the Gateway API CRDs on a kind cluster.
3. Stand up **kgateway**, expose a service, and route to it with an `HTTPRoute`.
4. Stand up **Kong** as a Gateway API implementation and apply a rate-limit plugin.
5. Decide which one you'd reach for and why.

## The three resources to burn into memory

| Resource | Owned by (role) | Answers the question |
|---|---|---|
| `GatewayClass` | Platform / infra | *Which implementation?* (like a StorageClass, but for gateways) |
| `Gateway` | Cluster operator | *What listens?* (ports, protocols, hostnames, TLS) |
| `HTTPRoute` | App developer | *Where does this hostname/path go?* (to which Service) |

That separation is the whole point: the app dev writes an `HTTPRoute` without touching
the operator's `Gateway`. Ingress mashed all three into one object.

## Reading list (read the *why*, skim the API)

- Gateway API intro & API concepts: gateway-api.sigs.k8s.io
- kgateway docs: kgateway.dev (formerly "Gloo Gateway")
- Kong Gateway Operator / KIC + Gateway API: docs.konghq.com
- `kubectl explain gateway.spec` and `kubectl explain httproute.spec` (read the schema from the tool, not the web)

## Labs (do in order)

| Lab | File | Core idea |
|---|---|---|
| 01 | `lab-01-gateway-api-crds.md` | Install the spec; understand GatewayClass/Gateway/HTTPRoute |
| 02 | `lab-02-kgateway.md` | Envoy-based gateway, route to a demo app, traffic splitting |
| 03 | `lab-03-kong.md` | Same routes on Kong + a rate-limiting plugin |
| 04 | `lab-04-kgateway-vs-kong.md` | Compare; pick a default for your stack |

## kind gotcha (read before lab 02)

On a real cloud, a `Gateway`/`Service type=LoadBalancer` gets a public IP. On **kind**
it stays `<pending>` forever: there's no cloud load balancer. Two ways through:

- **Easiest:** `kubectl port-forward` to the gateway's service (used in every lab).
- **More realistic:** install `cloud-provider-kind` (a tiny LB shim) in a second
  terminal so `LoadBalancer` services get a usable IP. Optional, shown in lab 02.

Don't fight this on kind. On LKE (Phase 09) a NodeBalancer provides one automatically.

## Manifests

Shared YAML lives in `manifests/`. Each lab tells you which file to apply.
