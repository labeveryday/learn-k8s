# Lab 04: kgateway vs Kong, choosing on the right axes

**Goal:** no new install. Turn the hands-on contrast from labs 02–03 into a decision
framework you can *defend*, which is what a Principal-level engineer (and a DA writing
about this) needs. The skill here is knowing which differences are load-bearing and
which are noise.

## The problem (why this exists)

You've now run the *same* HTTPRoute through two engines and seen identical-looking
results: a `200`, a JSON echo, a `503` when a backend goes missing. From the outside they
look interchangeable. That's the trap. If you choose a gateway by "which demo was
easier," you'll pick wrong for a production problem you haven't hit yet.

The right question is which *axis* matters for the thing you're building, and how these
two differ on it. Most comparison blog posts list features. The judgment is knowing which
features are decisions and which are checkboxes.

## What this replaces

This replaces the instinct to standardize on a gateway because of its surface ergonomics
(nicer CLI, friendlier docs, the one you saw first). Ergonomics are real but reversible.
The decisions that are *expensive to reverse* live in the data plane and the governance
model, and those are invisible in a five-minute demo. This lab makes them visible.

## Under the hood: the load-bearing differences

You already met the real divide in labs 02–03. Re-state it as the spine of the decision:

```
                kgateway                         Kong
control plane   kgateway controller              Kong Ingress Controller (KIC)
                  │ compiles to xDS                 │ compiles to Admin API
data plane      Envoy (C++)                       OpenResty (nginx + Lua)
extension       Envoy filters + policy CRDs       KongPlugin (Lua plugins)
                  (e.g. TrafficPolicy)              attached via annotation
provisioning    auto-provisions a proxy           unmanaged: binds to a proxy
                  pod per Gateway                    you ran (manual GatewayClass)
governance      CNCF (vendor-neutral)             Kong Inc. (OSS core + enterprise)
```

Why each row is a real axis, not trivia:

1. **Data-plane core, Envoy vs OpenResty.** This is the most expensive-to-reverse
   choice. Envoy is the lingua franca of service meshes and modern proxies; if you'll
   later run a mesh or need deep, programmable traffic control, an Envoy-based gateway
   shares concepts and config surface with the rest of that world. OpenResty (nginx+Lua)
   is proven and approachable, and Kong's value is the *catalog on top of it*, not
   raw proxy programmability. Pick based on whether you want to program the proxy or
   consume behaviors off a shelf.
2. **Extension model, filters/policy CRDs vs plugins.** kgateway exposes Envoy's
   capabilities through Gateway API policy attachment and its own CRDs; you reach for
   Envoy concepts. Kong gives you a large, declarative **plugin catalog** (auth,
   transforms, logging, rate limiting; you used one) attached by annotation. If you need
   many off-the-shelf behaviors *fast*, Kong's catalog wins; if you need bespoke,
   programmable control, kgateway gives you more rope.
3. **Provisioning model.** kgateway auto-creates a proxy pod the instant you declare a
   Gateway (lab 02). Kong, in the unmanaged setup you used, binds to a data plane you
   operate (lab 03). One optimizes for "it just appears"; the other for "I control the
   proxy lifecycle." That difference shows up in how you scale, upgrade, and reason about
   the data plane in production.
4. **Governance.** kgateway is a **CNCF** project (vendor-neutral, formerly Gloo
   Gateway). Kong is **Kong Inc.** (open-source core, commercial enterprise tier). This
   isn't a feature: it's a risk and roadmap-control decision. Neutral governance vs a
   single vendor's roadmap is a real tradeoff for a platform you'll bet years on.
5. **The AI-gateway story (Phase 06).** Both have one, and they differ in kind. Kong adds
   AI as plugins (e.g. `ai-proxy`), consistent with its plugin identity. kgateway's path
   is its Envoy lineage *plus* the AI-first `agentgateway` data plane built for LLM/agent
   traffic, consistent with its "program the proxy" identity. Same pattern as everything
   above: Kong = catalog, kgateway = data-plane control.

The pattern to internalize: **every axis traces back to the data plane and governance.**
The plugin-vs-filter, the AI story, the provisioning model: they're all downstream of
"OpenResty catalog under Kong Inc." vs "Envoy/agentgateway under CNCF."

## How to decide

Three questions, each tied to an axis above:

1. **Do you need many off-the-shelf behaviors (auth, transforms, logging) fast?**
   → Kong's plugin catalog. Bolt-on, declarative, well documented. (Axis 2.)
2. **Do you need deep, programmable proxy control and an AI-native path?**
   → kgateway (Envoy + agentgateway) gives you more rope and a built-in AI Gateway.
   (Axes 1 + 5.)
3. **What does your audience run, and who do you trust to own the roadmap?**
   → As an Akamai DA you'll meet both; knowing the *spec* means you can demo either with
   the same `HTTPRoute`. CNCF-neutral vs single-vendor is a real input. (Axis 4.)

## Recommendation for this curriculum

Use **kgateway as the spine** of Phases 06–09 (its Envoy/agentgateway lineage is the
cleanest path for the vLLM AI-gateway story) and keep **Kong in your pocket** as the
plugin-rich alternative you can swap in for any demo. Because both speak Gateway API,
switching is a `gatewayClassName` change, not a rewrite. You already proved that in labs
02–03: the *same* HTTPRoute ran on Envoy and on OpenResty with the same status
conditions.

## Prove the portability claim to yourself (2 minutes, no install)

You don't have to take the recommendation on faith; you have both controllers running.
Pull the route you ran on Kong and isolate the *one* field that binds it to an engine:

```bash
# print the route's spec - the routing intent plus the one field that binds it to an engine
kubectl get httproute httpbin-kong -o yaml | yq '.spec'
```

- `-o yaml | yq '.spec'` dumps the live route and prints **only** its `spec`, skipping the
  `metadata`/`status` noise a raw `-o yaml` interleaves (`yq` was installed in `00-prep`). We
  isolate the spec on purpose: the argument is "*one* field moves," so we look at the
  intent and its single engine binding.

**What you should see:** `parentRefs` naming the Kong Gateway, and nothing else
engine-specific (this is `.spec` itself, so there's no `spec:` wrapper line):

```yaml
parentRefs:
- name: kong               # THE one engine binding: points at the Kong Gateway → Kong's class/controller
hostnames:
- httpbin.kong.example.com # routing INTENT - unchanged by a switch
rules:                     # matches + backendRefs below are also pure intent
- matches:
  - path: { type: PathPrefix, value: / }
  backendRefs:
  - name: httpbin          # the Service you route to - same on any engine
    port: 8000
```

Now compare it to the route you ran on kgateway (`manifests/httpbin-route.yaml`). Look at
*only* the `parentRefs`: everything below it is byte-for-byte the same kind of intent:

```yaml
spec:
  parentRefs:
  - name: http               # <-- the ONLY difference: kgateway's Gateway...
    namespace: kgateway-system  # ...which lives in kgateway-system (Kong's was default ns)
  hostnames:
  - httpbin.example.com      # different host so the two routes don't collide; not engine-specific
  rules:
  - matches:
    - path: { type: PathPrefix, value: / }
    backendRefs:
    - name: httpbin
      port: 8000
```

> **Gotcha:** `parentRefs[].name` references a `Gateway` *object*, not a `GatewayClass`.
> The class (hence the engine) is chosen one level up, on the Gateway's `gatewayClassName`.
> So "repoint to the other engine" = change which Gateway you name here; that Gateway's
> class is what swaps Envoy for OpenResty.

The thing that makes a switch a one-line change is that `parentRefs` (which Gateway, hence
which class/engine) is the *only* engine-specific field. Matches, backendRefs, and
hostnames (your routing intent) don't move. That's the portability dividend in a
single observation: **intent is portable; the engine binding is one field.**

## Break the assumption, then read why it holds

The tempting wrong conclusion from labs 02–03 is "they're interchangeable, so it doesn't
matter." Test it: where does interchangeability *end*? The moment you use an
engine-specific extension. Look at the rate-limit you attached in lab 03
(`manifests/kong-ratelimit-plugin.yaml`): *this* is what doesn't move:

```yaml
apiVersion: configuration.konghq.com/v1   # NOT gateway.networking.k8s.io - a Kong-only API group
kind: KongPlugin                          # a Kong CRD; no kgateway/Envoy controller watches this kind
metadata:
  name: rl-5-per-min
plugin: rate-limiting                     # which Kong plugin from its catalog to run
config:
  minute: 5                               # 5 requests/min...
  policy: local                           # ...counted in-process per replica (not a shared store)
---
apiVersion: gateway.networking.k8s.io/v1  # the ROUTE is standard Gateway API (portable)...
kind: HTTPRoute
metadata:
  name: httpbin-kong
  annotations:
    konghq.com/plugins: rl-5-per-min      # ...but THIS annotation is the non-portable wiring
spec:
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

Two things make this **not portable**, and they're worth naming precisely:

- The `KongPlugin` is `configuration.konghq.com/v1`, a vendor API group. Repoint this
  route at kgateway and the object still exists, but **no Kong controller is watching** to
  compile it into the data plane, so it does nothing.
- The wiring is an **annotation** (`konghq.com/plugins`), not a spec field. Gateway API
  treats unknown annotations as opaque metadata, so kgateway reads the route, ignores the
  annotation, and serves traffic with no rate limit, *silently*. Nothing errors; the
  policy vanishes.

That's the boundary line of the standard: *the routing spec is portable; the extensions
are not.* (The same is true in reverse: a kgateway `TrafficPolicy` CRD is invisible to
Kong.) Read it as the reason axis 2 (extension model) is a real decision and not a
checkbox: the day you adopt a vendor's plugin/filter is the day switching stops being one
field.

## Deliverable (do this: it's content + interview gold)

Write 5 sentences: "I'd choose ___ when ___, and ___ when ___, because ___." Force the
"because" to name an *axis above* (data-plane core, extension model, governance, AI
path), not an ergonomic. That paragraph is the seed of a blog post and the crisp
technical judgment that reads as senior.

## Checkpoint: you can now explain…

1. **What's the most expensive-to-reverse choice between these two?** The data-plane core
   (Envoy vs OpenResty) and governance (CNCF vs Kong Inc.): both invisible in a demo,
   both hard to undo.
2. **Why is switching engines usually a one-line change, and when does that stop being
   true?** Because only `parentRefs`/class is engine-specific; it stops being true the
   moment you adopt an engine-specific extension (KongPlugin, kgateway policy CRD).
3. **State a default and one scenario where you'd switch**, and make the reason an axis,
   not an ergonomic.

You can now:
- [ ] Name the axes that decide a gateway choice, and which trace to data
      plane + governance.
- [ ] Defend a default and a switch condition in one sentence each.
- [ ] Explain the boundary of Gateway API portability (routing portable, extensions not).

You've finished Floor 1. Next floor: `06-ai-gateway/`, the same gateways, now routing to
your vLLM with token-aware limits. The "5 requests/min" plugin from lab 03 and the
agentgateway data plane from kgateway both lead straight there.

## Next

→ **Phase 06** (`06-ai-gateway/`): the same gateway you just compared becomes an *AI* gateway: token-based rate limits, multi-model routing, and prompt guards in front of vLLM.
