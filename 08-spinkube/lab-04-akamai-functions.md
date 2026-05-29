# Lab 04 — Managed Spin: deploy your app to Akamai Functions

**Goal:** take the exact `hello-spin` `.wasm` you built in lab-01 and run it on **Akamai
Functions** — Akamai Cloud's managed platform for Spin apps — with one command. Same module
you ran locally and scheduled on SpinKube, now operated *for* you. By the end you can say
precisely what's identical (the artifact) and what changed (who runs the runtime).

**Time:** ~20 min · **Cost:** free tier in public preview · **Access:** preview sign-up required (below)

## The problem (why this exists)

labs 02–03 made *you* operate the Spin runtime: a k3d cluster, cert-manager, the
spin-operator, the `containerd-shim-spin` shim, a `RuntimeClass`, a `SpinApp`. That's total
control — and total ops. For a huge class of Spin workloads (a webhook, a per-request shim,
a tiny RAG endpoint) you don't want to run a cluster at all. You want: *"here's my `.wasm` —
run it, give me a URL, scale it to zero when idle, and don't make me think about nodes."*
That's serverless/FaaS, and because a Spin module cold-starts in milliseconds, it's a
*viable* serverless unit in a way a container never was. Akamai ships exactly this for Spin.

> **Akamai Functions is the platform for running Spin applications on Akamai Cloud.**

It runs on Akamai Cloud (Linode infrastructure, integrates Object Storage) — **not**
EdgeWorkers. It's the managed counterpart to the SpinKube path you just built by hand.

## What it replaces, and what stays identical

| | SpinKube (labs 02–03) | Akamai Functions (this lab) |
|---|---|---|
| Where it runs | *your* k3d/LKE nodes | Akamai Cloud (managed, Fermyon-backed) |
| You operate | cluster + cert-manager + operator + shim + `RuntimeClass` | nothing |
| Scaling | you set replicas / HPA | automatic, **scale-to-zero** |
| The public URL | a `Service`/Gateway you wire up | a stable URL `spin aka deploy` prints |
| The artifact | **the same `.wasm`** | **the same `.wasm`** |

That last row is the whole lesson: this is the **same Spin app**, not a port. `spin build`
produces one module; SpinKube runs it on your node via `containerd-shim-spin` → `wasmtime`;
Akamai Functions runs it on its managed Wasm platform. Only *who hosts the `wasmtime`
runtime* changes — exactly the "kind vs LKE," "process vs k8s object" contrast the rest of
the track runs on.

## Under the hood (MIT hat): one `.wasm`, two operators

```
        spin build  ─►  app.wasm  + spin.toml      (one artifact, lab-01)
                          │
            ┌─────────────┴──────────────┐
            ▼                             ▼
   SpinKube (you operate)        Akamai Functions (managed)
   SpinApp → operator →          spin aka deploy →
   pod runtimeClassName →        Akamai's wasmtime host →
   containerd-shim-spin →        a stable public URL
   wasmtime  (YOUR node)         (Akamai Cloud)
```

There is no second build, no second SDK, no Akamai-specific code. The portability is the
point: a `.wasm` is a sealed, host-agnostic unit, so the *same* bytes run on your cluster or
on someone else's platform. (Contrast a container, which still assumes a Linux userland.)

## 0. Prereqs

- `hello-spin/` from lab-01, with `spin build` run so a `.wasm` exists. (You do **not** need
  the k3d cluster from lab-02 here — this path skips Kubernetes entirely.)
- **Akamai Functions is in public preview (limited availability).** Request access first via
  the onboarding form linked on the
  [Welcome page](https://techdocs.akamai.com/akamai-functions/docs/welcome). Like Phase 09's
  LKE, this lab is *read now, run when your access is granted* — preview limits and exact
  auth flow can change, so treat the doc links below as the source of truth.

## 1. Install the `aka` plugin

`aka` is a Spin plugin that adds the Akamai deploy commands to the `spin` CLI you already
have:

```bash
spin plugin install aka   # fetches the plugin from the Spin plugin catalog into ~/.spin/plugins
spin aka --help           # confirms it loaded — lists the Akamai subcommands (deploy, login, …)
```

- `spin plugin install` extends the *same* `spin` binary from lab-01 — there's no separate
  Akamai CLI to learn. `aka` becomes `spin aka <subcommand>`, sitting beside `spin build`/`spin up`.
- If `spin aka --help` errors with "plugin not found," the install didn't land — re-run the
  install (and check `spin --version`; the plugin tracks the CLI version).

Authenticate to your Akamai Functions account per the
[`aka` command reference](https://techdocs.akamai.com/akamai-functions/docs/aka-command-reference)
(the exact login subcommand is preview-versioned — follow the reference, don't guess).

> Reminder from Step 0: this is **read now, run when your preview access is granted**. Auth
> and Steps 2–3 will only succeed once your access is approved — until then, read along.

## 2. Deploy the lab-01 app

From the `hello-spin/` directory (so `spin` reads *this* app's `spin.toml`):

```bash
spin build           # same build as lab-01 — compiles src/ to the .wasm spin.toml points at
spin aka deploy      # uploads that .wasm + spin.toml to Akamai's Wasm host, returns a URL
```

- `spin aka deploy` reads `spin.toml` for the artifact path and app name — no flags needed for
  the happy path. It does **not** rebuild; it ships whatever `.wasm` `spin build` last produced
  (the break-it section below turns that into the lesson).
- There's no image registry step here (no `spin registry push` like the SpinKube path) — the
  module is uploaded directly to the managed platform.

**What to look for:** `spin aka deploy` **prints the public endpoint** of your app — a
*stable URL that persists across re-deploys*. No cluster, no Service, no port-forward. The
URL is yours until you delete the app.

## 3. Call it — three runtimes, one app

```bash
curl -s https://<the-url-spin-printed>/   # paste the exact URL the deploy step printed; -s hides the progress bar
```

- This is a plain HTTPS GET against the managed endpoint — no `kubectl port-forward`, no
  in-cluster DNS. The platform terminates TLS and routes the request straight to your `.wasm`.

**What to look for:** the *same* response you got from `spin up` (lab-01, on your laptop) and
from the SpinKube `Service` (lab-03, on your cluster). One `.wasm`, served three ways. That's
the portability dividend made concrete: you proved it locally, you ran it self-managed, and
now Akamai runs it — without changing a byte.

## Break it, then read the error (Kelsey lens)

Edit the handler (change the response text), then **deploy without rebuilding**:

```bash
# edit src/... , then SKIP spin build:
spin aka deploy        # ships the STALE .wasm — your edit only changed source, not the artifact
```

**Read what happens.** You'll deploy the *old* `.wasm` (or hit a "no built artifact" error),
and the live URL still returns the previous text. Identical lesson to lab-01's local
break-it: **the thing that runs is what you `spin build`, not what you edited.** Managed or
self-hosted, the artifact is the contract. Rebuild and redeploy to fix.

## Checkpoint — you can now explain…

1. **What's identical between SpinKube and Akamai Functions, and what differs?** Identical:
   the `.wasm` + `spin.toml`. Different: who operates the `wasmtime` host — your cluster
   (SpinKube) vs Akamai's managed platform (Functions), and therefore who owns scaling and
   the URL.
2. **When do you reach for each?** SpinKube/LKE when you need your own cluster, data
   locality, or GPUs next door (Phase 09); Akamai Functions when you want zero-ops,
   scale-to-zero glue and a URL in one command.
3. **Why is serverless *Spin* viable when serverless containers are awkward?** Wasm cold
   starts in milliseconds and idles at ~zero, so "scale to zero, spin up on the request" is
   cheap — the cost curve serverless always wanted.

You can now:
- [ ] Deploy any Spin app to Akamai Functions with `spin aka deploy` and get a stable URL.
- [ ] State what's portable about a `.wasm` and why the same module runs on SpinKube and Functions.
- [ ] Choose self-managed (SpinKube) vs managed (Functions) for a given workload.

## Next

→ `lab-05-akamai-functions-rag.md`: make the function *do* something real — a lightweight
**RAG agent** that retrieves over an in-memory corpus and calls an LLM (a hosted API *or*
your own Phase 06 gateway), running serverless on Akamai Functions.

## Sources

- Akamai Functions docs: <https://techdocs.akamai.com/akamai-functions/docs/welcome> ·
  [`aka` reference](https://techdocs.akamai.com/akamai-functions/docs/aka-command-reference) ·
  [quotas & limits](https://techdocs.akamai.com/akamai-functions/docs/quotas-and-limits)
- "Serverless with zero cold starts: WebAssembly and Spin":
  <https://www.akamai.com/blog/developers/build-serverless-functions-zero-cold-starts-webassembly-spin>
