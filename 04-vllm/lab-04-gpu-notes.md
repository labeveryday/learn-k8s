# Lab 04 — What Changes on Real GPUs (Read-only)

**What you'll build:** nothing — you *can't* on a Mac, and that's the point. This lab is the
**diff** between the CPU-mode vLLM you deployed in Lab 02 (`manifests/vllm.yaml`, TinyLlama on
`--device=cpu`) and the same workload on a real GPU cluster. Everything you'd add — a GPU
`limit`, a toleration, fast storage for weights, GPU metrics — is a *handful of extra fields on
a Pod you already know how to write.* Read it so the shape is familiar when you have the
hardware; don't memorize the acronyms — just file each one under **scheduling**, **networking**,
or **storage**.

> **The one idea (Kelsey):** "An LLM server is just a Pod. A big Pod, but a Pod." The GPU work
> doesn't change the controller pattern from Phase 3 — it changes which *resource* the scheduler
> packs (`nvidia.com/gpu` instead of CPU/memory) and which *nodes* the Pod is allowed to land on.
> The K8s skills you built are the hard part; the GPU bits are a few extra fields.

## 1. Cluster prerequisites

Before any Pod can ask for a GPU, the cluster has to *know the GPUs exist* and advertise them as
a schedulable resource. Two ways to get there:

- Nodes with NVIDIA GPUs + drivers installed.
- **NVIDIA device plugin** — a *DaemonSet* (one Pod per node, K8s' "run this everywhere" workload)
  that tells K8s "this node has GPUs" by advertising `nvidia.com/gpu` as a schedulable resource.
- **NVIDIA GPU Operator** (managed option) — installs drivers, device plugin, node feature
  discovery, and the *DCGM exporter* (publishes GPU metrics) for you in one shot.

Why this matters: the scheduler can only place a Pod on a resource it has been *told about*. Until
the device plugin runs, `nvidia.com/gpu` doesn't exist in the cluster's vocabulary and any Pod
requesting one stays `Pending` forever. The Operator is the "don't hand-assemble five components"
path — prefer it on managed clusters (LKE, EKS, GKE).

## 2. Requesting a GPU

Compare this to the CPU vLLM container in `manifests/vllm.yaml` (which ran `--device=cpu`,
`--dtype=float32`, TinyLlama). On a GPU node you drop the CPU flags, point at a bigger model, and
add **one resource line**:

```yaml
containers:
  - name: vllm
    image: vllm/vllm-openai:latest          # SAME image as the CPU lab — it's GPU-first by default
    args:
      - --model=meta-llama/Llama-3.1-8B-Instruct   # a real model; CPU lab used TinyLlama-1.1B
      - --tensor-parallel-size=1            # shard the model across N GPUs in this node (1 = no shard)
    resources:
      limits:
        nvidia.com/gpu: 1                   # ask for 1 whole GPU — this single line is the GPU "opt-in"
```

Notes:

- GPU is a **limit-only** resource (requests=limits automatically). You can't ask for "0.5 of a
  GPU" the way you fractionally request CPU — a Pod gets *whole* GPUs, exclusively. (MIG/time-slicing
  exist but are out of scope here.)
- `nvidia.com/gpu: 2` = two whole GPUs on the *same node*. For multi-node parallelism, use a
  framework (vLLM's `--pipeline-parallel-size` on multi-node Ray).
- **Gotcha:** that `image: vllm/vllm-openai:latest` is the *same* image you pulled in Lab 02 — it
  runs on CPU only because you passed `--device=cpu`. Drop that flag on a GPU node and it uses CUDA
  automatically. The image isn't what changes; the node + the `nvidia.com/gpu` limit is.

**What this buys you:** the scheduler now treats a GPU like any other countable resource — it only
places this Pod on a node with a free GPU, and reserves it. If none is free, the Pod sits `Pending`
(not crash-looping) — same desired-state behavior as CPU/memory pressure, just a scarcer resource.

## 3. Taints and tolerations

A *taint* marks a node "keep Pods off unless they tolerate me"; a *toleration* on a Pod says "I
accept that taint." A *nodeSelector* pins a Pod to nodes carrying a given label. (New material —
none of this appeared earlier.)

GPU nodes are typically tainted so only GPU workloads schedule there — you don't want a random web
Pod squatting on a $30/hr GPU box:

```yaml
# node taint (cluster admin):
#   nvidia.com/gpu:NoSchedule              # repels every Pod that lacks a matching toleration

spec:
  tolerations:
    - key: "nvidia.com/gpu"                # must match the taint's key
      operator: "Exists"                   # "I tolerate this key with any value"
      effect: "NoSchedule"                 # must match the taint's effect
  nodeSelector:
    node.kubernetes.io/instance-type: "p4d.24xlarge"    # or similar label — PIN to GPU node types
```

How the two combine, because they're easy to confuse:

- **Toleration = permission, not attraction.** It lets the Pod *land on* a tainted node but does
  not *pull* it there. A tolerating Pod could still schedule onto a plain CPU node.
- **`nodeSelector` = the pull.** It restricts the Pod to nodes carrying that label. Use both: the
  toleration gets you *past* the taint's gate, the `nodeSelector` *steers* you to the right box.
- **Gotcha:** toleration without `nodeSelector` → the Pod is *allowed* on GPU nodes but may land on
  a cheaper CPU node and never see a GPU. `nodeSelector` without toleration → the Pod targets GPU
  nodes but the taint bounces it, leaving it `Pending`. You almost always want the pair.

## 4. Topology and fast networking

When a model is split across GPUs (that's what `--tensor-parallel-size > 1` in section 2 turns on),
those GPUs constantly swap tensors — the interconnect becomes the bottleneck. So you reach for fast
GPU-to-GPU networking:

- **RDMA / InfiniBand / NVLink**: low-latency links that let GPUs talk directly. The cross-GPU sync
  calls (**NCCL** = NVIDIA's collective-comms library) run over them. *Multus* is a CNI add-on that
  gives a Pod a second network interface so it can reach an InfiniBand fabric.
- **Topology-aware scheduling**: keep cooperating Pods on GPUs wired together with NVLink, so tensor
  parallelism isn't crippled by a slow hop.

Why it matters: a model sharded across GPUs is only as fast as the link between them. Put two shards
on GPUs that have to talk over PCIe (or worse, across nodes) and the NCCL all-reduce on every token
stalls the whole forward pass — you bought N GPUs and got the throughput of one. This is the GPU
analog of the scheduler's `requests` from Phase 3: *where* the Pod lands changes performance, not
just whether it runs.

## 5. Image pull and model weights

Model weights are huge (tens to hundreds of GB) — orders of magnitude bigger than the TinyLlama the
Lab 02 init-container downloaded into a 10Gi PVC. Best practices:

- **Don't bake weights into images.** Mount via PVC backed by fast shared storage (S3 CSI, JuiceFS,
  FSx Lustre — all ways to expose object storage or a parallel filesystem as a PVC).
- **Pre-warm**: a `Job` that downloads to PVC once, before Deployments reference it. (This is exactly
  what the `initContainers: fetch-model` step in `manifests/vllm.yaml` did — just hoisted into its
  own one-time Job so every replica shares one cached copy instead of each racing to download.)
- **Cache images** on GPU nodes (they're GB-scale too). Use a registry mirror if pulling constantly.

Why it matters: a cold start that re-pulls a 200 GB model on every Pod restart will dominate your
startup time and your egress bill. The whole game is "download once, mount many" — which is why the
storage backing the PVC has to be *shared* (RWX-capable) and *fast*, not the single-attach RWO PVC
the CPU lab used for one replica.

## 6. Observability that matters

- GPU utilization, memory, temperature — DCGM exporter → Prometheus.
- Tokens/sec, queue depth, time-to-first-token (latency until the first output token appears) — vLLM
  emits Prometheus metrics at `/metrics`.
- Error rates, 429s (HTTP 429 Too Many Requests — returned when you rate-limit a caller).

Tie this back to Lab 03: there you scraped `/metrics` and drove an HPA. On GPUs the *signal* changes
— you scale on tokens/sec or queue depth, not CPU% — but the mechanism is the same custom-metrics
pipeline. GPU utilization tells you if the box is busy; tokens/sec and time-to-first-token tell you
if *users* are happy. Watch both.

## 7. What vLLM-specific features to exercise

These are the `args:` you'd add to the container in section 2 once you have real throughput to tune:

- `--tensor-parallel-size N` (split model across N GPUs in one node).
- `--pipeline-parallel-size` (split across nodes, requires Ray).
- `--max-num-batched-tokens`, `--max-num-seqs` (batching controls — the "continuous batching" knob).
- `--enable-prefix-caching` (reuse KV cache across requests with shared prefixes).
- Speculative decoding with a draft model.

On the CPU lab you couldn't feel any of these — TinyLlama on one CPU core has no throughput to
batch. On a GPU these are where the 5–20× over naive serving comes from; tune `--max-num-seqs` and
`--max-num-batched-tokens` against your latency SLO, then verify with the tokens/sec metric from
section 6.

## 8. Read the papers/docs

- vLLM paper: *Efficient Memory Management for Large Language Model Serving with PagedAttention*
  (Kwon et al., 2023).
- NVIDIA GPU Operator docs.
- Kubernetes device plugin design doc.

When you have access to a GPU cluster, redeploying the manifests from Lab 02 with `nvidia.com/gpu`
limits and a bigger model is a one-day exercise. The K8s skills you built in Phase 3 are the hard
part; the GPU bits are just a few extra fields.
