# Platform Track (Phases 05–11) — Build an AI Platform on Akamai

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
  client  ───►  │  GATEWAY        kgateway / Kong              │  Phase 05  ┐
                │  (who gets in, routing, auth, rate limits)   │            │
                ├─────────────────────────────────────────────┤            │
                │  AI GATEWAY     LLM routing, token limits,   │  Phase 06  │
                │                 prompt guards, multi-model   │            │
                ├─────────────────────────────────────────────┤            │  OBSERVABILITY
                │  WORKLOADS                                    │            │  metrics · traces · evals
                │   • vLLM  (inference)          ◄── you have   │  Phase 04 ✓│  taps every floor,
                │   • kagent (agents on k8s)                    │  Phase 07  │  changes none
                │   • Spin  (Wasm functions/glue)              │  Phase 08  │  Phase 11
                │   • RAG   (Q&A over your data + vector store) │  Phase 10  │
                ├─────────────────────────────────────────────┤            │
                │  KUBERNETES   Deployments/Svc/Probes/HPA     │  Phase 03 ✓│
                ├─────────────────────────────────────────────┤            │
                │  AKAMAI LKE   NodeBalancer, Block Storage,   │  Phase 09  │
                │               GPU node pools                  │           ┘
                └─────────────────────────────────────────────┘
```

Two phases sit *differently* from the floors above. **RAG (Phase 10)** is a new
*workload* — it shares the WORKLOADS floor with vLLM, kagent, and Spin, because it's
assembled almost entirely from parts you already run (it adds exactly one new piece, a
vector store). **Observability (Phase 11)** is not a floor at all — it's a sidecar that
taps every layer, reads what each one emits, and changes how *zero* requests flow.

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
| **Spin / SpinKube / Akamai Functions** | Fermyon's **WebAssembly** runtime — millisecond cold starts for glue/functions. Run the same `.wasm` *yourself* on k8s (SpinKube) **or** let Akamai run it for you, serverless (**Akamai Functions**, managed Spin on Akamai Cloud). | 08 |
| **LKE** | Akamai's managed Kubernetes — where this runs for real (LB + storage + GPUs). | 09 |
| **RAG + Qdrant** | Retrieval-Augmented Generation: give the model *your* data at query time. A **workload** built from your existing vLLM + gateway + kagent plus one new piece — a **vector store** (Qdrant). | 10 |
| **Prometheus / Grafana / OTel / Tempo / Evals** | The **observability** stack: metrics, traces, and a quality signal over the whole platform. A *cross-cutting* layer, not a workload. | 11 |

You'll notice kgateway and Kong appear twice. That's the point: the *same* gateway you
use for normal HTTP routing (05) is the thing that becomes your *AI* gateway (06). One
tool, two jobs.

## The primitives underneath (read once)

Every phase here leans on three primitives the core curriculum (00–04) only half-introduced.
Get these once and the whole track reads cleanly.

### CRDs — how a tool teaches Kubernetes a new noun

Kubernetes ships built-in kinds: Pod, Deployment, Service. A **CustomResourceDefinition
(CRD)** lets a tool *add its own kinds* to the apiserver. After kgateway installs its CRDs,
`kubectl get gateway` works as if `Gateway` were built in — the same `apply` / `get` /
`describe` / `explain` loop from Phase 03, on a kind Kubernetes never shipped. That's why
**every phase has the same two-step install**:

```
1. install the CRDs        ──►  the SPEC: the apiserver now knows the new nouns   (kubectl get crd)
2. install the controller  ──►  the IMPLEMENTATION: it watches those objects and acts (reconciles)
```

A custom resource whose CRDs exist but whose controller isn't running just *sits there* —
valid, unprogrammed. That's the lesson of `05-gateway-api/lab-01`'s "ghost" Gateway, and it's
the shape of the whole track: Gateway API, kgateway's `AgentgatewayBackend`, kagent's
`Agent`/`ModelConfig`, `SpinApp`, Prometheus's `ServiceMonitor` — **all CRDs**. When a field is
unfamiliar, ask the cluster: `kubectl explain <kind>.<field>` reads the live CRD schema (the
Kelsey rule from Phase 03, now load-bearing).

> **OCI Helm charts:** `03/lab-11` taught classic `helm repo add` (HTTP) repos. Some tools
> here ship charts from an **OCI registry** instead — `helm install … oci://cr.kgateway.dev/…`.
> Same `helm install`; the source is just a registry URL. Charts are also *how* the CRDs above
> get installed.

### Control plane vs data plane — and where Envoy fits

The track's other recurring word-pair. A **control plane** watches your declarative objects
and *configures* something; a **data plane** is the thing that actually *moves the bytes*. For
gateways, kgateway (or Kong) is the **control plane** — it watches your `Gateway`/`HTTPRoute`
and compiles them into config for a **data-plane proxy**. That proxy is **Envoy** (kgateway)
or OpenResty/nginx (Kong): the process your traffic really flows through, handed its routing
rules over Envoy's **xDS** API. You write *portable intent* (`HTTPRoute`); the controller
*compiles it down* to vendor proxy config you never hand-write. The same split recurs all over:
kagent's controller (control) vs the agent-runtime Pod (data); the spin-operator (control) vs
the `wasmtime` Pod (data). `05-gateway-api/lab-02` shows it live — watch a `Gateway` go
`Programmed=True` the instant a controller provisions its Envoy proxy.

### The network it all rides on (you already have this)

The platform track is a new *top floor* — it does **not** replace the Phase 03 network.
Underneath every gateway and proxy: a **CNI** plugin (kindnet / Calico / Cilium) gives every
Pod an IP on one flat network, a Service's ClusterIP is **DNAT'd to a Pod by kube-proxy**, and
**CoreDNS** resolves the names. That's all in `03-kubernetes/lab-04` — so when a platform lab
says "Envoy forwards to the `vllm` Service," this is the machinery doing it.

## Phases

| # | Folder | You'll be able to… | Named tools |
|---|--------|--------------------|-------------|
| 05 | `05-gateway-api/` | Route traffic with the Gateway API standard; run kgateway and Kong side by side | **kgateway, Kong** |
| 06 | `06-ai-gateway/` | Put vLLM behind an AI gateway with token rate limits, multi-model routing, prompt guards | **kgateway AI, Kong AI** |
| 07 | `07-kagent/` | Run AI agents as k8s resources that call your own vLLM and MCP tools | **kagent** |
| 08 | `08-spinkube/` | Build a Spin (Wasm) app; run it self-managed on k8s (SpinKube) and managed/serverless on **Akamai Functions** — including a serverless RAG agent | **Spin, Akamai Functions** |
| 09 | `09-lke-akamai/` | Take the whole stack to Akamai LKE: NodeBalancer, Block Storage CSI, GPU node pools | **LKE** |
| 10 | `10-rag/` | Build a RAG Q&A workload over *your* data — embeddings + a vector store, generation through your Phase 06 gateway, and agentic retrieval as a kagent/Strands tool | **Qdrant, RAG** |
| 11 | `11-observability/` | See the platform you built: metrics, traces, and an eval-based quality score across every layer — closing the harness's traces-as-feedback loop | **Prometheus, Grafana, OTel, Tempo** |

## "Whatever else" — the supporting cast you'll meet along the way

You don't need to study these separately; they show up *inside* the phases above:

- **Envoy** — the proxy under kgateway (Phase 05). You'll see its config, not write it.
- **Helm** — you already touched it (lab-11). Every tool here installs via Helm.
- **cert-manager** — TLS for your Gateways (Phase 05 optional lab).
- **MCP (Model Context Protocol)** — how kagent's agents get tools (Phase 07). You
  already have an `agents/` framework in this repo; Phase 07 connects it to k8s.
- **Prometheus / Grafana** — metrics for gateways and vLLM (touched in 03 lab-10; LKE
  has an add-on in Phase 09; **Phase 11** makes it a first-class layer over the whole stack).
- **Qdrant** — the vector store, the one new piece RAG adds to your platform (Phase 10).
- **OpenTelemetry / Tempo** — distributed tracing: one request stitched across every
  service so you can see *where* latency hides (Phase 11).
- **GPU operator / node feature discovery** — only on LKE GPU pools (Phase 09).

## Prerequisites for this track

1. Finish `00-prep` (installs `kubectl`, `helm`, `kind`, `docker`). **Nothing is
   installed on this box yet** — start there.
2. A local **kind** cluster for 05–08 (free, fast, what you chose).
3. An **Akamai/Linode account + `linode-cli`** for Phase 09 (real cluster, costs
   credits — we save it for last).

## Suggested pace

Each phase ≈ 1 focused evening for the core lab, plus an optional deep-dive lab.
Do them in order — 06 needs 05, 09 reruns everything you built on real infra. Phases
10–11 are the *application* and *operation* of the platform: **10 (RAG)** is the first
real workload sitting on the stack (it reuses 04/06/07 and adds only a vector store),
and **11 (Observability)** is the cross-cutting layer that lets you finally *see* the
whole thing — metrics, traces, and a quality signal — and operate it instead of just
deploying it. Both run on the same local kind cluster as 05–07; Phase 09 LKE is where
they go for real (Block Storage behind Qdrant's vectors, the managed observability add-on).

> **Kelsey:** "Don't install all five tools the first night. Get one request through
> kgateway, see the 200, *then* move up a floor. A platform you understand one layer
> at a time is a platform you can debug at 2am."
