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

- The `install.sh` script downloads the `spin` binary for your OS/arch into the *current
  directory* (it doesn't install system-wide on its own) — that's why the next line moves it.
- `sudo mv ./spin /usr/local/bin/spin` puts it on your `PATH` so `spin` works from anywhere;
  `/usr/local/bin` is on `PATH` by default. (On a machine where you can't `sudo`, move it to
  `~/.local/bin` instead and ensure that's on your `PATH`.)

`spin` is the build-and-run CLI: it scaffolds projects, compiles your code to Wasm, and
hosts the modules locally in its own `wasmtime`-based runtime. **What to look for:** a
version prints. Everything below is this one binary — no Docker daemon involved.

## 2. Scaffold an HTTP app

```bash
spin new -t http-rust hello-spin --accept-defaults
cd hello-spin
```

- `spin new -t http-rust` picks the **http-rust template** (a Spin HTTP app whose handler
  is Rust); `hello-spin` is the app name; `--accept-defaults` skips the interactive prompts
  (description, etc.) and takes the template's defaults.

The template wires up a Spin HTTP trigger → one component (your handler). `spin.toml` is the
**manifest** — Spin's analog of a Pod spec. Don't just "open it and move on": this is the
contract that lab-03 leans on, so read it field-by-field now. Here is what the http-rust
template emits, with the load-bearing fields called out:

```toml
spin_manifest_version = 2          # manifest schema version (Spin v2/v3 use 2 — not the app version)

[application]
name = "hello-spin"                # the app's name (you passed it to `spin new`)
version = "0.1.0"
authors = ["..."]
description = ""

[[trigger.http]]                   # WHAT invokes the component. [[...]] = a list, so an app can have many
route = "/..."                     # this route → the component below. "/..." is a WILDCARD: all paths match
component = "hello-spin"           # the trigger fires THIS component (id must match [component.<id>] below)

[component.hello-spin]             # the component definition; the id ("hello-spin") is what the trigger names
source = "target/wasm32-wasip1/release/hello_spin.wasm"   # THE DEPLOYABLE — the .wasm `spin build` writes here
allowed_outbound_hosts = []        # deny-by-default network allowlist. EMPTY = the module can reach NOTHING (see below)
[component.hello-spin.build]
command = "cargo build --target wasm32-wasip1 --release"   # what `spin build` runs to PRODUCE that .wasm
watch = ["src/**/*.rs", "Cargo.toml"]
```

The four fields that decide everything:

- **`[[trigger.http]]` `route` → `component`** is the entire request-routing contract: a path
  comes in, the named component runs. The component id under `[component.<id>]` **must** equal
  the `component` the trigger names — mismatch and `spin up` can't resolve what to run.
- **`source`** is the seam from the "break it" section below: Spin runs *this built `.wasm`*,
  not your `src/lib.rs`. Note the Rust crate name gets underscored (`hello-spin` → `hello_spin.wasm`).
- **`allowed_outbound_hosts = []`** is the deny-by-default sandbox in YAML form. Empty means the
  handler cannot open *any* outbound connection. This is the field that bites you in lab-03: to
  let the app call vLLM you must list its host here (e.g.
  `["http://vllm.default.svc.cluster.local:8000"]`), or Spin blocks the call at runtime — no
  error in the manifest, the request just fails. Lab-03's `[variables]` block (which doesn't
  exist yet in this scaffold) is the *other* half of that wiring.

**Beginner gotchas:** (1) `spin_manifest_version = 2` is the *schema* version, not your app's —
leave it at `2`. (2) The component id appears in *three* places (the trigger's `component`,
`[component.<id>]`, `[component.<id>].build`) and they all have to agree — renaming the app
later means renaming all three.

Don't have the Rust toolchain? Use `-t http-js` or `-t http-go` — the SDK and `[component.*.build]`
`command` change (and a JS/Go app has no `wasm32-wasip1` Cargo target), but the Spin model
(trigger → component → one `.wasm`) and the `allowed_outbound_hosts` sandbox do not.

## 3. Build to WebAssembly

```bash
spin build
```

`spin build` runs the `[component.hello-spin.build]` `command` from the manifest
(`cargo build --target wasm32-wasip1 --release`) and drops the result at the `source` path
you just read. Now look at the actual deployable:

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

`spin up` loads the `.wasm` into `wasmtime` and serves the HTTP trigger on `:3000`. (`&`
runs it in the background; `%1` is its job number, so `kill %1` stops it.) The first
request **instantiates** the module — that's the cold start, and it's in the single-digit
milliseconds, not the seconds a container takes to come up. **What to look for:** the
response is effectively instant on the very first call. There's no warm-up phase, because
there's no image to pull and no namespace to build. If `curl` says *connection refused*,
`spin up` hadn't finished binding `:3000` yet — wait a second and re-run the `curl`.

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
  filesystem unless the host grants it (Spin does this via WASI — the WebAssembly System
  Interface, the standard for giving a sandboxed module controlled OS access). That's why lab-03 has
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
