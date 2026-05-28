# Platform Track (Phases 05–09) — Build an AI Platform on Akamai

You finished the core curriculum (Linux → Docker → Kubernetes → vLLM). This track is
the second half: the **platform layer** that turns "I can run a pod" into "I run an
AI platform other people can call." Everything you asked about — **kgateway, Kong,
vLLM, kagent, Spin** — lives here, and it is *not* a random pile of tools. It's a
stack. Each phase is one floor of the same building.

## The mental model (read this first)

A request to an LLM on your cluster travels through layers. You already own the bottom
two. This track adds the top three.

```
                ┌─────────────────────────────────────────────┐
  client  ───►  │  GATEWAY        kgateway / Kong              │  Phase 05
                │  (who gets in, routing, auth, rate limits)   │
                ├─────────────────────────────────────────────┤
                │  AI GATEWAY     LLM routing, token limits,   │  Phase 06
                │                 prompt guards, multi-model   │
                ├─────────────────────────────────────────────┤
                │  WORKLOADS                                    │
                │   • vLLM  (inference)          ◄── you have   │  Phase 04 ✓
                │   • kagent (agents on k8s)                    │  Phase 07
                │   • Spin  (Wasm functions/glue)              │  Phase 08
                ├─────────────────────────────────────────────┤
                │  KUBERNETES   Deployments/Svc/Probes/HPA     │  Phase 03 ✓
                ├─────────────────────────────────────────────┤
                │  AKAMAI LKE   NodeBalancer, Block Storage,   │  Phase 09
                │               GPU node pools                  │
                └─────────────────────────────────────────────┘
```

**Stanford lens:** every phase answers the same three questions — what problem does
this layer solve, what did it replace, what's the layer below it?
**Kelsey lens:** you'll deploy each tool, send a real request through it, then break it
and read the error. No cargo-cult YAML.

## How the tools relate (so you stop seeing them as separate)

| Tool | What it actually is | Floor |
|---|---|---|
| **Gateway API** | The CNCF standard that replaces `Ingress`. A *spec*, not an implementation. | 05 |
| **kgateway** | Envoy-based Gateway API implementation (formerly Gloo Gateway, now CNCF). Has a built-in **AI Gateway**. | 05 / 06 |
| **Kong** | API gateway; also implements Gateway API. Has **Kong AI Gateway** (the `ai-proxy` plugin family). | 05 / 06 |
| **vLLM** | The inference server. The *workload* the gateways point at. | 04 ✓ |
| **kagent** | CNCF project for running **AI agents as Kubernetes resources** (Agent/Tool CRDs). Calls your vLLM. | 07 |
| **Spin / SpinKube** | Fermyon's **WebAssembly** runtime on k8s — millisecond cold starts for glue/functions. | 08 |
| **LKE** | Akamai's managed Kubernetes — where this runs for real (LB + storage + GPUs). | 09 |

You'll notice kgateway and Kong appear twice. That's the point: the *same* gateway you
use for normal HTTP routing (05) is the thing that becomes your *AI* gateway (06). One
tool, two jobs.

## Phases

| # | Folder | You'll be able to… | Named tools |
|---|--------|--------------------|-------------|
| 05 | `05-gateway-api/` | Route traffic with the Gateway API standard; run kgateway and Kong side by side | **kgateway, Kong** |
| 06 | `06-ai-gateway/` | Put vLLM behind an AI gateway with token rate limits, multi-model routing, prompt guards | **kgateway AI, Kong AI** |
| 07 | `07-kagent/` | Run AI agents as k8s resources that call your own vLLM and MCP tools | **kagent** |
| 08 | `08-spinkube/` | Build a Spin (Wasm) app and run it on k8s via SpinKube | **Spin** |
| 09 | `09-lke-akamai/` | Take the whole stack to Akamai LKE: NodeBalancer, Block Storage CSI, GPU node pools | **LKE** |

## "Whatever else" — the supporting cast you'll meet along the way

You don't need to study these separately; they show up *inside* the phases above:

- **Envoy** — the proxy under kgateway (Phase 05). You'll see its config, not write it.
- **Helm** — you already touched it (lab-11). Every tool here installs via Helm.
- **cert-manager** — TLS for your Gateways (Phase 05 optional lab).
- **MCP (Model Context Protocol)** — how kagent's agents get tools (Phase 07). You
  already have an `agents/` framework in this repo; Phase 07 connects it to k8s.
- **Prometheus / Grafana** — metrics for gateways and vLLM (touched in 03 lab-10; LKE
  has an add-on in Phase 09).
- **GPU operator / node feature discovery** — only on LKE GPU pools (Phase 09).

## Prerequisites for this track

1. Finish `00-prep` (installs `kubectl`, `helm`, `kind`, `docker`). **Nothing is
   installed on this box yet** — start there.
2. A local **kind** cluster for 05–08 (free, fast, what you chose).
3. An **Akamai/Linode account + `linode-cli`** for Phase 09 (real cluster, costs
   credits — we save it for last).

## Suggested pace

Each phase ≈ 1 focused evening for the core lab, plus an optional deep-dive lab.
Do them in order — 06 needs 05, 09 reruns everything you built on real infra.

> **Kelsey:** "Don't install all five tools the first night. Get one request through
> kgateway, see the 200, *then* move up a floor. A platform you understand one layer
> at a time is a platform you can debug at 2am."
