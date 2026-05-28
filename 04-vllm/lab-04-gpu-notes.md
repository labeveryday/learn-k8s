# Lab 04 — What Changes on Real GPUs (Read-only)

You can't do this on a Mac. But know the shape.

## 1. Cluster prerequisites

- Nodes with NVIDIA GPUs + drivers installed.
- **NVIDIA device plugin** DaemonSet running — advertises `nvidia.com/gpu` as a schedulable resource.
- **NVIDIA GPU Operator** (managed option) — installs drivers, device plugin, node feature discovery, DCGM exporter.

## 2. Requesting a GPU

```yaml
containers:
  - name: vllm
    image: vllm/vllm-openai:latest
    args:
      - --model=meta-llama/Llama-3.1-8B-Instruct
      - --tensor-parallel-size=1
    resources:
      limits:
        nvidia.com/gpu: 1
```

Notes:

- GPU is a **limit-only** resource (requests=limits automatically).
- `nvidia.com/gpu: 2` = two whole GPUs on the *same node*. For multi-node parallelism, use a framework (vLLM's `--pipeline-parallel-size` on multi-node Ray).

## 3. Taints and tolerations

GPU nodes are typically tainted so only GPU workloads schedule there:

```yaml
# node taint (cluster admin):
#   nvidia.com/gpu:NoSchedule

spec:
  tolerations:
    - key: "nvidia.com/gpu"
      operator: "Exists"
      effect: "NoSchedule"
  nodeSelector:
    node.kubernetes.io/instance-type: "p4d.24xlarge"    # or similar label
```

## 4. Topology and fast networking

For multi-GPU / multi-node:

- **RDMA / InfiniBand**: collective ops (NCCL) need RDMA. Device plugins and CNI configs (e.g., Multus) expose it.
- **Topology-aware scheduling**: keep pods on GPUs with NVLink when using tensor parallelism.

## 5. Image pull and model weights

Model weights are huge (tens to hundreds of GB). Best practices:

- **Don't bake weights into images.** Mount via PVC backed by fast object storage (S3 CSI, JuiceFS, FSx Lustre).
- **Pre-warm**: a `Job` that downloads to PVC once, before Deployments reference it.
- **Cache images** on GPU nodes (they're GB-scale too). Use a registry mirror if pulling constantly.

## 6. Observability that matters

- GPU utilization, memory, temperature — DCGM exporter → Prometheus.
- Tokens/sec, queue depth, time-to-first-token — vLLM emits Prometheus metrics at `/metrics`.
- Error rates, 429s (when you rate-limit).

## 7. What vLLM-specific features to exercise

- `--tensor-parallel-size N` (split model across N GPUs in one node).
- `--pipeline-parallel-size` (split across nodes, requires Ray).
- `--max-num-batched-tokens`, `--max-num-seqs` (batching controls).
- `--enable-prefix-caching` (reuse KV cache across requests with shared prefixes).
- Speculative decoding with a draft model.

## 8. Read the papers/docs

- vLLM paper: *Efficient Memory Management for Large Language Model Serving with PagedAttention* (Kwon et al., 2023).
- NVIDIA GPU Operator docs.
- Kubernetes device plugin design doc.

When you have access to a GPU cluster, redeploying the manifests from Lab 02 with `nvidia.com/gpu` limits and a bigger model is a one-day exercise. The K8s skills you built in Phase 3 are the hard part; the GPU bits are just a few extra fields.
