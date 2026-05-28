# Lab 04 — kgateway vs Kong: choosing on the right axes

**Goal:** no new install. Turn the hands-on contrast from labs 02–03 into a decision
framework you can *defend* — which is what a Principal-level engineer (and a DA writing
about this) actually needs. The skill here isn't picking a winner; it's knowing which
differences are load-bearing and which are noise.

## The problem (why this exists)

You've now run the *same* HTTPRoute through two engines and seen identical-looking
results: a `200`, a JSON echo, a `503` when a backend goes missing. From the outside they
look interchangeable. That's exactly the trap. If you choose a gateway by "which demo was
easier," you'll pick wrong for a production problem you haven't hit yet.

The right question isn't "which is better." It's "which *axis* actually matters for the
thing I'm building, and how do these two differ on that axis?" Most comparison blog posts
list features. The judgment is knowing which features are decisions and which are
checkboxes.

## What this replaces

This replaces the instinct to standardize on a gateway because of its surface ergonomics
(nicer CLI, friendlier docs, the one you saw first). Ergonomics are real but reversible.
The decisions that are *expensive to reverse* live in the data plane and the governance
model — and those are invisible in a five-minute demo. This lab makes them visible.

## Under the hood: the differences that are actually load-bearing

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

1. **Data-plane core — Envoy vs OpenResty.** This is the most expensive-to-reverse
   choice. Envoy is the lingua franca of service meshes and modern proxies; if you'll
   later run a mesh or need deep, programmable traffic control, an Envoy-based gateway
   shares concepts and config surface with the rest of that world. OpenResty (nginx+Lua)
   is battle-tested and approachable, and Kong's value is the *catalog on top of it*, not
   raw proxy programmability. Pick based on whether you want to program the proxy or
   consume behaviors off a shelf.
2. **Extension model — filters/policy CRDs vs plugins.** kgateway exposes Envoy's
   capabilities through Gateway API policy attachment and its own CRDs; you reach for
   Envoy concepts. Kong gives you a large, declarative **plugin catalog** (auth,
   transforms, logging, rate limiting — you used one) attached by annotation. If you need
   many off-the-shelf behaviors *fast*, Kong's catalog wins; if you need bespoke,
   programmable control, kgateway gives you more rope.
3. **Provisioning model.** kgateway auto-creates a proxy pod the instant you declare a
   Gateway (lab 02). Kong, in the unmanaged setup you used, binds to a data plane you
   operate (lab 03). One optimizes for "it just appears"; the other for "I control the
   proxy lifecycle." That difference shows up in how you scale, upgrade, and reason about
   the data plane in production.
4. **Governance.** kgateway is a **CNCF** project (vendor-neutral, formerly Gloo
   Gateway). Kong is **Kong Inc.** (open-source core, commercial enterprise tier). This
   isn't a feature — it's a risk and roadmap-control decision. Neutral governance vs a
   single vendor's roadmap is a real tradeoff for a platform you'll bet years on.
5. **The AI-gateway story (Phase 06).** Both have one, and they differ in kind. Kong adds
   AI as plugins (e.g. `ai-proxy`) — consistent with its plugin identity. kgateway's path
   is its Envoy lineage *plus* the AI-first `agentgateway` data plane built for LLM/agent
   traffic — consistent with its "program the proxy" identity. Same pattern as everything
   above: Kong = catalog, kgateway = data-plane control.

The pattern to internalize: **every axis traces back to the data plane and governance.**
The plugin-vs-filter, the AI story, the provisioning model — they're all downstream of
"OpenResty catalog under Kong Inc." vs "Envoy/agentgateway under CNCF."

## How to actually decide

Three questions, each tied to an axis above:

1. **Do you need many off-the-shelf behaviors (auth, transforms, logging) fast?**
   → Kong's plugin catalog. Bolt-on, declarative, well documented. (Axis 2.)
2. **Do you need deep, programmable proxy control and an AI-native path?**
   → kgateway (Envoy + agentgateway) gives you more rope and a first-class AI Gateway.
   (Axes 1 + 5.)
3. **What does your audience run, and who do you trust to own the roadmap?**
   → As an Akamai DA you'll meet both; knowing the *spec* means you can demo either with
   the same `HTTPRoute`. CNCF-neutral vs single-vendor is a real input. (Axis 4.)

## Recommendation for this curriculum

Use **kgateway as the spine** of Phases 06–09 — its Envoy/agentgateway lineage is the
cleanest path for the vLLM AI-gateway story — and keep **Kong in your pocket** as the
plugin-rich alternative you can swap in for any demo. Because both speak Gateway API,
switching is a `gatewayClassName` change, not a rewrite. You already proved that in labs
02–03: the *same* HTTPRoute ran on Envoy and on OpenResty with the same status
conditions.

## Prove the portability claim to yourself (2 minutes, no install)

You don't have to take the recommendation on faith — you have both controllers running.
Re-point a single route from one engine to the other and watch it move:

```bash
# the kong route, repointed to kgateway's class — only the parent/class changes
kubectl get httproute httpbin-kong -o yaml | grep -A3 parentRefs
```

The thing that makes a switch a one-line change is that `parentRefs` (which Gateway, hence
which class/engine) is the *only* engine-specific field. Matches, backendRefs, and
hostnames — your actual routing intent — don't move. That's the portability dividend in a
single observation: **intent is portable; the engine binding is one field.**

## Break the assumption, then read why it holds

The tempting wrong conclusion from labs 02–03 is "they're interchangeable, so it doesn't
matter." Test it: where does interchangeability *end*? The moment you use an
engine-specific extension. The `KongPlugin` + `konghq.com/plugins` annotation from lab 03
is **not portable** — point that route at kgateway and the plugin annotation is simply
ignored, because no Kong controller is watching to honor it. That's the boundary line of
the standard: *the routing spec is portable; the extensions are not.* Read that as the
exact reason axis 2 (extension model) is a real decision and not a checkbox — the day you
adopt a vendor's plugin/filter is the day switching stops being one field.

## Deliverable (do this — it's content + interview gold)

Write 5 sentences: "I'd choose ___ when ___, and ___ when ___, because ___." Force the
"because" to name an *axis above* (data-plane core, extension model, governance, AI
path), not an ergonomic. That paragraph is the seed of a blog post and exactly the crisp
technical judgment that reads as senior.

## Checkpoint — you can now explain…

1. **What's the most expensive-to-reverse choice between these two?** The data-plane core
   (Envoy vs OpenResty) and governance (CNCF vs Kong Inc.) — both invisible in a demo,
   both hard to undo.
2. **Why is switching engines usually a one-line change, and when does that stop being
   true?** Because only `parentRefs`/class is engine-specific; it stops being true the
   moment you adopt an engine-specific extension (KongPlugin, kgateway policy CRD).
3. **State a default and one scenario where you'd switch** — and make the reason an axis,
   not an ergonomic.

You can now:
- [ ] Name the axes that actually decide a gateway choice, and which trace to data
      plane + governance.
- [ ] Defend a default and a switch condition in one sentence each.
- [ ] Explain the boundary of Gateway API portability (routing portable, extensions not).

You've finished Floor 1. Next floor: `06-ai-gateway/` — the same gateways, now routing to
your vLLM with token-aware limits. The "5 requests/min" plugin from lab 03 and the
agentgateway data plane from kgateway both lead straight there.
