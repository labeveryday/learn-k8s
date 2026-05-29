# Phase 4: vLLM on Kubernetes (Capstone)

**Time budget:** ~15%. Goal: deploy an LLM inference server on K8s with probes, limits, and an autoscaler — using concepts from the earlier phases.

## Honest caveat (read first)

**vLLM is a GPU-first project.** It targets CUDA (NVIDIA's GPU compute API). On your Apple Silicon Mac:

- vLLM CPU mode *runs*, but is slow (seconds/token with small models).
- The vLLM image is large (~5–10 GB).
- You will not get production-like throughput here.

**What you can realistically learn on Mac:**

1. How to containerize and deploy an LLM service on K8s (the orchestration skills — transferable 1:1 to GPU clusters).
2. The OpenAI-compatible HTTP API that vLLM exposes.
3. Autoscaling, probes, resource accounting for AI workloads.

**What you'll skip and revisit on real GPU hardware:**

- Real throughput testing, PagedAttention benefits, tensor parallelism, GPU operator, node taints/tolerations (node scheduling rules — covered in Lab 04) for `nvidia.com/gpu`.

If the vLLM CPU image is too heavy, **the fallback path** uses your existing **ollama** as a stand-in server; the K8s patterns are identical.

## Why vLLM (when you get GPUs)

First, two terms the rest leans on: the **KV cache** is the model's running memory of the tokens so far (what it attends back to); **tensor parallelism** is splitting one model across several GPUs.

- **Continuous batching**: instead of padding requests into fixed batches, vLLM dynamically merges in-flight requests at the token level — serves many at once instead of one-at-a-time.
- **PagedAttention**: stores the KV cache in small reusable chunks (like an OS pages physical memory) instead of one big contiguous block, so memory isn't wasted — enables long contexts.
- **OpenAI-compatible API**: drop-in replacement for OpenAI clients.

Result: 5–20× higher throughput than naive HF (Hugging Face) serving on the same GPU.

## Labs

1. `lab-01-vllm-locally.md` — run vLLM (or ollama) in Docker first
2. `lab-02-on-kubernetes.md` — **Path A:** point K8s at your local ollama via ExternalName (no image pulls). **Path B:** run ollama/vLLM as Pods.
3. `lab-03-probes-and-autoscale.md` — health, HPA, production hygiene
4. `lab-04-gpu-notes.md` — what changes on real GPUs (read-only)

> **Hotel wifi tip:** Path A in Lab 02 reuses the ollama you already have on your Mac (with already-downloaded models). No 500 MB image pull, no model re-download, same K8s lessons.

## Panel notes

> **Stanford:** "Serving is a scheduling problem: you're packing variable-length sequences into a fixed compute budget. PagedAttention is a memory-allocator trick borrowed from OS kernels. Read the paper."
>
> **Kelsey:** "An LLM server is just a Pod. A big Pod, but a Pod. Probes, limits, rollouts — same rules apply. Don't let the AI label make you skip hygiene."
