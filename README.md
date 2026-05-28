# Learn Linux → Docker → Kubernetes → vLLM

A fast-track, offline-first curriculum designed by a panel:
- **Stanford (distributed systems lens)** — *why* each layer exists, what problem it solves.
- **MIT (systems lens)** — the kernel/protocol primitives beneath each tool. No magic.
- **Kelsey Hightower (pragmatic lens)** — build from primitives; avoid cargo-cult YAML; production mindset from day one.

## Target outcome

After this curriculum you can:
1. Operate confidently in a Linux shell and reason about processes, files, and networking.
2. Build, run, and debug Docker containers and multi-service Compose stacks.
3. Run a local Kubernetes cluster, deploy apps with raw manifests, and debug them with `kubectl`.
4. Deploy an LLM inference workload (vLLM, CPU-mode on Mac) behind a K8s Service, with probes, limits, and basic autoscaling.

## Pacing

| Pace | Duration | Daily commitment |
|------|----------|------------------|
| Full-time | ~10 days | 6–8 h |
| Evenings  | ~3 weeks | 1.5–2 h |

Phase weights (of total time): Linux 15%, Docker 20%, Kubernetes 50%, vLLM 15%.

## Structure

```
00-prep/        One-time setup + offline caching (needs internet)
01-linux/       Shell, processes, networking, namespaces/cgroups
02-docker/      Containers, images, Compose, a real project
03-kubernetes/  Architecture, workloads, networking, storage, RBAC, Helm
04-vllm/        Capstone: LLM serving on Kubernetes
reference/      Cheatsheets — keep these open while working
```

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

## Panel notes

> **Stanford:** "You are not learning tools. You are learning a *stack of abstractions* built over 50 years. Each phase reveals the layer below. Treat it as such."
>
> **MIT:** "If you can't explain what `docker run` does in terms of `clone(2)`, `unshare(2)`, and `pivot_root(2)`, you don't understand it yet. Lab 01-04 fixes that."
>
> **Kelsey:** "The fastest way to learn Kubernetes is to deploy something real, break it, and read the error. Everything else is theater."
