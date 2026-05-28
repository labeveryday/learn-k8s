# 08 — Spin & SpinKube: WebAssembly on Kubernetes

> Floor 3b. A second workload type. Where a Deployment runs a container via `runc`, this
> floor runs a single `.wasm` via `wasmtime` — millisecond cold starts, near-zero idle,
> thousands per node. The right tool for glue, edge functions, and request-shaping around
> your AI services.

## The problem this phase solves

Phase 03 gave you one workload type: a Deployment running a container. A container is the
right wrapper for a long-lived service — and the wrong wrapper for fifty lines of glue.
An OCI image carries your binary *plus* base layers (libc, shell, package manager); `runc`
spends seconds unpacking those layers and building namespaces/cgroups on cold start; and
an idle replica still holds a live process and real memory. For an AI platform full of
small per-request shims — auth checks, prompt rewriters, webhooks in front of vLLM — that
is the wrong cost curve. You're paying container prices for non-container work.

**Spin** (Fermyon) compiles your function to a single **WebAssembly** module. **SpinKube**
teaches a Kubernetes node to run that module by adding a second containerd runtime
(`wasmtime`, via the `containerd-shim-spin` shim) and a `RuntimeClass` that routes the
right pods to it instead of `runc`. The unit that schedules is the `.wasm` itself — not an
image, not a container.

## What it replaces, and what it doesn't

| | Container (Phase 03) | Spin / Wasm (this phase) |
|---|---|---|
| Deployable | OCI image = binary + base layers | a single `.wasm` module |
| Runs via | kubelet → containerd → **`runc`** → Linux container | kubelet → containerd → **`containerd-shim-spin`** → `wasmtime` |
| Cold start | seconds | milliseconds |
| Idle cost | a live process per replica | near-zero |
| Selected by | the default runtime | `RuntimeClass wasmtime-spin-v2` (`handler: spin`) |

It does **not** replace your Deployments. It's a parallel runtime for the workloads
containers over-serve. Everything above containerd — scheduling, Services, CoreDNS,
kube-proxy — is the Phase 03 machinery, unchanged.

## Prereqs

- Phase 03 (Kubernetes fundamentals: pods, Deployments, Services, RuntimeClass as a
  concept). Phases 05–06 make the "Spin in front of the gateway/LLM" demo land, but the
  core labs stand alone.
- A toolchain for one language target (Rust or TinyGo or JS/TS via the Spin SDK).
- `k3d`, `kubectl`, `helm`, and Docker running (lab-02 builds a k3d cluster).

## Objectives

1. Build a Spin app to a `.wasm` and run it **locally** — see that the single module *is*
   the deployable, and feel the cold start (lab-01).
2. Understand the Wasm execution model vs a container: no layers, sandboxed by default,
   instantiated per request (lab-01).
3. Install **SpinKube** on k3d and trace the mechanism: `RuntimeClass` → shim →
   `wasmtime`, and why a stock kind node can't run it (lab-02).
4. Deploy a `SpinApp` and watch the operator compile it into a Deployment whose pods carry
   `runtimeClassName: wasmtime-spin-v2`; then shape vLLM traffic with it (lab-03).
5. Reason about when Wasm beats a container (and when it doesn't).

## Labs

| Lab | File | The mechanism it teaches |
|---|---|---|
| 01 | `lab-01-spin-local.md` | the deployable is one `.wasm`, run by `wasmtime` — not an image with layers |
| 02 | `lab-02-install-spinkube.md` | `RuntimeClass` (`handler: spin`) routes a pod through `containerd-shim-spin` to `wasmtime` instead of `runc` |
| 03 | `lab-03-deploy-spinapp.md` | the operator turns one `SpinApp` into a Deployment whose pods carry `runtimeClassName`, then shapes vLLM traffic |
| 04 | *optional, planned* | a deeper Spin function that pre/post-processes vLLM calls |

## The payoff

You'll have a third workload type in your toolbox alongside Deployments (Phase 03) and
agents (Phase 07), a precise mental model of *what actually schedules* (a `.wasm`, not a
container), and a crisp answer to "container vs Wasm" — exactly the kind of
forward-looking Akamai Cloud content that stands out.

> spin-operator / SpinKube versions move quickly — we pin and verify against
> spinkube.dev when we write the labs.
