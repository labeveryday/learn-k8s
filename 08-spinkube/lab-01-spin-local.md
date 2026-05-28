# Lab 01 — Spin locally: what actually schedules is a `.wasm`, not an image

**Goal:** build a Spin app to WebAssembly and run it locally — *before* Kubernetes —
so that when lab-03 schedules it, you already know the unit being scheduled is a single
`.wasm` module, not a container image. Feel the cold start. By the end you can say why
a Wasm module starts in milliseconds and idles at near-zero cost where a container
can't.

**Time:** ~20 min · **Cost:** free (local)

## The problem (why this exists)

You have small, bursty code — an auth shim, a prompt rewriter, a webhook handler. The
only workload type you know is the **Deployment + container** from Phase 03. But a
container is a heavy wrapper for fifty lines of glue: it's an OCI image (your binary
*plus* a base layer's libc, shell, package manager), it cold-starts in seconds while
`runc` unpacks layers and sets up namespaces/cgroups, and an idle replica still holds a
real process and real memory. Run a hundred of those for a hundred tiny functions and
you're paying container prices for non-container work. Nothing you've learned makes
small, dense, request-shaping code cheap.

## What it replaces, and why the container was insufficient

|  | Container (Phase 03) | Spin / Wasm |
|---|---|---|
| **Deployable** | OCI image = your binary **+ base layers** (libc, shell, …) | a single `.wasm` module |
| **Cold start** | seconds — `runc` unpacks layers, builds namespaces/cgroups | milliseconds — instantiate a Wasm module |
| **Idle cost** | a live process + memory per replica | near-zero; instantiate per request, tear down |
| **Density** | tens per node | thousands per node |
| **Isolation** | Linux namespaces/cgroups | the Wasm sandbox (deny-by-default syscalls/network) |

The container model isn't *wrong* — it's the right tool for a long-lived stateful
service. It's the wrong tool for a function that runs for 3 ms and should cost nothing
when idle. Spin compiles your handler to one Wasm module; that module **is** the
artifact. There are no layers to unpack, so there's nothing to make the cold start slow.

## 1. Install Spin

```bash
curl -fsSL https://developer.fermyon.com/downloads/install.sh | bash
sudo mv ./spin /usr/local/bin/spin
spin --version
```

`spin` is the build-and-run CLI: it scaffolds projects, compiles your code to Wasm, and
hosts the modules locally in its own `wasmtime`-based runtime. **What to look for:** a
version prints. Everything below is this one binary — no Docker daemon involved.

## 2. Scaffold an HTTP app

```bash
spin new -t http-rust hello-spin --accept-defaults
cd hello-spin
```

The template wires up a Spin HTTP trigger → one component (your handler). Open
`spin.toml` and read it: the `[[trigger.http]]` block maps a route (`/...`) to a
component, and `[component.hello-spin]` names the `.wasm` that component will compile to.
That manifest is the contract — remember it, because in lab-03 the same `spin.toml`
fields (`[variables]`, `allowed_outbound_hosts`) decide whether the app can reach vLLM.

Don't have the Rust toolchain? Use `-t http-js` or `-t http-go` — the SDK changes, the
Spin model (trigger → component → one `.wasm`) does not.

## 3. Build to WebAssembly

```bash
spin build
```

This compiles your handler to a WebAssembly module and writes the path declared in
`spin.toml`. Now look at the actual deployable:

```bash
find . -name '*.wasm'
ls -lh target/wasm32-wasip1/release/*.wasm 2>/dev/null || find . -name '*.wasm' -exec ls -lh {} +
```

**What to look for:** *one* file, on the order of a few MB. That is the entire thing
that ships. Compare it to a container image (`docker history` on any image shows a stack
of layers totaling tens to hundreds of MB). No base layer, no libc you didn't write — so
there is nothing for a runtime to unpack at start. **This single `.wasm` is what lab-03
pushes to a registry and schedules onto the cluster.** Fix that mental image now.

## 4. Run it — feel the cold start

```bash
spin up &
curl -s http://127.0.0.1:3000/
kill %1 2>/dev/null
```

`spin up` loads the `.wasm` into `wasmtime` and serves the HTTP trigger on `:3000`. The
first request **instantiates** the module — that's the cold start, and it's in the
single-digit milliseconds, not the seconds a container takes to come up. **What to look
for:** the response is effectively instant on the very first call. There's no warm-up
phase, because there's no image to pull and no namespace to build.

## Under the hood (MIT hat): what `spin up` actually does

`spin up` is a tiny preview of the cluster mechanism you'll install in lab-02. It does
*not* start a Linux container. It embeds the **`wasmtime`** engine, reads `spin.toml`,
loads the `.wasm`, and on each request instantiates the module inside the Wasm sandbox:

```
HTTP request :3000
      │
   spin (host)  ── reads spin.toml, owns the HTTP trigger
      │  instantiate per request
   wasmtime engine  ── runs the .wasm in a sandbox
      │
   your handler (the single .wasm module)   ← no OS process, no namespaces, no layers
```

Two things this buys you, and you should be able to name *why*:

- **Millisecond start / near-zero idle.** Instantiating a Wasm module is allocating a
  linear-memory arena and a call into an exported function — not `runc` building cgroups
  and namespaces and unpacking image layers. Nothing to tear down means idle costs
  nothing.
- **Deny-by-default sandbox.** A Wasm module can't open a socket or touch the
  filesystem unless the host grants it (Spin does this via WASI). That's why lab-03 has
  to *explicitly* allow the vLLM host in `spin.toml`'s `allowed_outbound_hosts` — the
  module is sandboxed shut by default. The isolation is the runtime's, not a kernel
  namespace's.

In lab-02 you'll see that `wasmtime` + this exact loading step is what the
`containerd-shim-spin` shim does on a Kubernetes node — `spin up` is that mechanism with
the cluster removed.

## Break it, then read the error (Kelsey lens)

Edit the handler source (change the response body), but **skip `spin build`**, then run:

```bash
spin up
```

You'll get a "component source not found" / stale-artifact error — Spin runs the *built
`.wasm`*, not your source file. Read what that error is telling you: there is a hard
seam between **source** and **the module that actually runs**, exactly like a container
runs the image you built, not the `Dockerfile`. The artifact is the truth. This is the
same discipline that bites you in lab-03 when you forget to re-push after editing — the
cluster runs the pushed `.wasm`, never your local edit.

Rebuild and you're back:

```bash
spin build && spin up &
curl -s http://127.0.0.1:3000/ ; kill %1 2>/dev/null
```

## Checkpoint — you can now explain…

- **What problem does Spin/Wasm solve that a container couldn't?** Cheap, dense,
  fast-starting glue: one `.wasm` with no base layers instantiates in milliseconds and
  idles at near-zero, where a container pays seconds to start and holds a live process
  while idle.
- **What is the deployable?** A *single* `.wasm` module — not an image with layers.
  That distinction is the whole reason the cold start and idle numbers differ.
- **What runs it locally?** `spin` embeds the `wasmtime` engine and instantiates the
  module per request inside a deny-by-default sandbox — no Linux container, no namespaces.

You can now:
- [ ] Point at the one `.wasm` and say "that, not an image, is what ships."
- [ ] Explain why cold start is milliseconds (no layers to unpack, no namespaces to build).
- [ ] Explain why the module can't reach the network until the host allows it.

## Next

→ `lab-02-install-spinkube.md`: teach a Kubernetes node to run this `.wasm`. The trick
is getting `wasmtime` onto the node *and* routing the right pods to it — a `RuntimeClass`
that points at the `containerd-shim-spin` shim instead of `runc`.
