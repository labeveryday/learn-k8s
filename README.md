# Learn Linux → Docker → Kubernetes → vLLM

A fast-track, offline-first curriculum designed by a panel:
- **Stanford (distributed systems lens)** — *why* each layer exists, what problem it solves.
- **MIT (systems lens)** — the kernel/protocol primitives beneath each tool. No magic.
- **Kelsey Hightower (pragmatic lens)** — build from primitives; avoid cargo-cult YAML; production mindset from day one.

## Target outcome

The **core curriculum (Phases 00–04)** gets you to a confident operator. After it you can:
1. Operate confidently in a Linux shell and reason about processes, files, and networking.
2. Build, run, and debug Docker containers and multi-service Compose stacks.
3. Run a local Kubernetes cluster, deploy apps with raw manifests, and debug them with `kubectl`.
4. Deploy an LLM inference workload (vLLM, CPU-mode on Mac) behind a K8s Service, with probes, limits, and basic autoscaling.

The **optional Platform Track (Phases 05–11)** adds the layer above the workload — the
platform other people call. After it you can also:
5. Route traffic with the **Gateway API** standard (kgateway, Kong) instead of legacy Ingress.
6. Put vLLM behind an **AI gateway** with token rate limits, multi-model routing, and prompt guards.
7. Run **AI agents as Kubernetes resources** (kagent) that call your own vLLM and MCP tools.
8. Build **WebAssembly** workloads on k8s with Spin / SpinKube — and ship the same module serverless on **Akamai Functions**.
9. Take the whole stack to **Akamai LKE** — NodeBalancer, Block Storage CSI, and GPU node pools.
10. Build **RAG** over your own data (embeddings + a vector store) as a workload on the platform.
11. **Observe** it end to end — metrics, traces, and an eval-based quality score (Prometheus/Grafana + Langfuse).

## Pacing

| Pace | Duration | Daily commitment |
|------|----------|------------------|
| Full-time | ~10 days | 6–8 h |
| Evenings  | ~3 weeks | 1.5–2 h |

Phase weights (of total time): Linux 15%, Docker 20%, Kubernetes 50%, vLLM 15%.

## Structure

The **core curriculum** is Phases 00–04 — that's the part everyone should do:

```
00-prep/        One-time setup + offline caching (needs internet)
01-linux/       Shell, processes, networking, namespaces/cgroups
02-docker/      Containers, images, Compose, a real project
03-kubernetes/  Architecture, workloads, networking, storage, RBAC, Helm
04-vllm/        Capstone: LLM serving on Kubernetes
reference/      Cheatsheets — keep these open while working
```

### Platform Track (Phases 05–11) — optional second half

Once the core is solid, the Platform Track builds the **platform layer** on top of the
workload: the gateways, AI gateway, agents, and managed infra that turn "I can run a
pod" into "I run an AI platform other people can call." These layers stack — each phase
is one floor of the same building.

```
05-gateway-api/  Gateway API standard (replaces Ingress); kgateway + Kong
06-ai-gateway/   vLLM behind an AI gateway: token limits, multi-model routing, prompt guards
07-kagent/       AI agents as Kubernetes resources (Agent/Tool CRDs) that call your vLLM
08-spinkube/     WebAssembly workloads on k8s via Spin / SpinKube (millisecond cold starts)
09-lke-akamai/   The whole stack on Akamai LKE: NodeBalancer, Block Storage CSI, GPU node pools
10-rag/          RAG Q&A over your data: embeddings + vector store (Qdrant), generation through your gateway, agentic retrieval
11-observability/  See the platform: metrics, traces, and an eval quality score across every layer (Prometheus, Grafana, OTel, Tempo)
```

**Read [`PLATFORM-TRACK.md`](./PLATFORM-TRACK.md) first** — it has the full mental model
(the request-path diagram and how kgateway, Kong, vLLM, kagent, and Spin actually relate),
the prerequisites, and a suggested pace. Don't start 05 without it.

Each phase folder has its own `README.md` with objectives, a reading list, and numbered labs. Do the labs in order; each builds on the last.

## How to study

**Kelsey's rule:** `kubectl explain <resource>` (and `man <cmd>`, `docker <cmd> --help`) before Google. When you don't know a field, ask the tool.

**MIT's rule:** Before you use a command, know what syscall or primitive it wraps. `strace -f` is your friend.

**Stanford's rule:** For every new abstraction, answer three questions in your notes:
1. What problem does it solve?
2. What does it replace?
3. What's the next layer down?

## Start here

1. Do `00-prep/README.md` while you still have internet.
2. Then `01-linux/README.md`.
3. Don't skip labs. Don't copy-paste without reading. Break things on purpose.
4. Finish the core (00–04) before touching the Platform Track. When you're ready for the
   second half, read [`PLATFORM-TRACK.md`](./PLATFORM-TRACK.md), then start `05-gateway-api/README.md`.

## Panel notes

> **Stanford:** "You are not learning tools. You are learning a *stack of abstractions* built over 50 years. Each phase reveals the layer below. Treat it as such."
>
> **MIT:** "If you can't explain what `docker run` does in terms of `clone(2)`, `unshare(2)`, and `pivot_root(2)`, you don't understand it yet. Lab 01-04 fixes that."
>
> **Kelsey:** "The fastest way to learn Kubernetes is to deploy something real, break it, and read the error. Everything else is theater."
