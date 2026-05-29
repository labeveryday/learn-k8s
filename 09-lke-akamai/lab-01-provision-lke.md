# Lab 01 — Provision LKE: where the abstractions get a bill

**Goal:** stand up a real managed Kubernetes cluster on Akamai (Linode), point `kubectl`
at its kubeconfig, and understand exactly what is *different* from kind — so the rest of Phase
09 lands as "the same objects, now backed by something real" instead of new magic.

**Time:** ~20 min · **Cost:** 💸 real Akamai credits — tear down today (lab-04)

## The problem

Everything you ran on kind was a convincing fake. `type: LoadBalancer` sat in
`<pending>` forever because no cloud existed to answer. A `PersistentVolumeClaim` bound
to a directory on your laptop, not a disk you could detach and reattach. There was no
GPU. kind taught you the *nouns* — Service, PVC, node pool — for free, but none of them
provisioned anything outside your machine. You can't learn what a NodeBalancer or a CSI
volume or a GPU actually *is* on a cluster where those things are stubs.

LKE (Linode Kubernetes Engine) is Akamai's managed control plane. The difference from
kind is not the API — it's that a real cloud controller is *watching* your objects and
turning them into billable infrastructure. Before any of that can happen, you need the
real cluster.

## What it replaces, and why kind wasn't enough

| | kind | LKE |
|---|---|---|
| Control plane | a container on your laptop, you see it | managed by Akamai, you never touch it |
| `type: LoadBalancer` | no-op, stays `<pending>` | provisions an Akamai NodeBalancer |
| `PersistentVolumeClaim` | hostPath on your disk | Block Storage volume via CSI |
| GPU | none | real GPU node pools (lab-03) |
| Cost | free | charges per worker node + per resource |

kind is a *single binary that runs Kubernetes nodes as Docker containers*. There is no
cloud underneath it, so any object whose job is "ask the cloud for something" has nothing
to ask. LKE removes exactly that limitation: it ships a **cloud-controller-manager (CCM)**
and **CSI driver** pre-installed, which is the machinery that makes the cloud-facing
objects do real work. The trade is that the meter is now running.

## Under the hood (MIT hat): what is a "managed control plane"?

A Kubernetes cluster is two halves. The **control plane** — apiserver, scheduler,
controller-manager, etcd — is the brain. The **worker nodes** run your pods. On kind
both halves are containers you can `docker exec` into. On LKE, Akamai runs the control
plane for you on infrastructure you cannot see or SSH to; you only get worker nodes and a
kubeconfig that points at the apiserver's public endpoint.

```
   kind:                          LKE:
   ┌──────────────────┐           ┌─ Akamai-managed (invisible) ─┐
   │ control plane    │           │  apiserver  scheduler  etcd  │
   │ + workers        │           └──────────────┬───────────────┘
   │  (Docker, local) │                          │ kubeconfig → public endpoint
   └──────────────────┘           ┌──────────────┴───────────────┐
   you own everything             │ worker nodes (you pay these) │
                                  │  + CCM + CSI pre-installed     │
                                  └──────────────────────────────┘
```

The CCM is the new actor. It's a controller that watches Service and Node objects and
calls the **Linode API** on your behalf — that's literally how a `LoadBalancer` Service
becomes a NodeBalancer in lab-02. You don't install it; LKE bakes it into the node setup.
Provisioning the cluster is provisioning that integration.

## Step 1 — Install and configure linode-cli

Run every command in this phase from inside `09-lke-akamai/` (`cd 09-lke-akamai`) — the
labs use relative paths and write `lke-kubeconfig.yaml` to this folder.

```bash
pip install linode-cli --upgrade
linode-cli configure        # interactive: paste a Personal Access Token, pick a default region
```

- `configure` writes `~/.linode-cli` with your token + defaults; every later command reuses
  them, so you authenticate once. The token needs read/write on **Kubernetes** and
  **Linodes** scopes — a read-only token will fail at `cluster-create` (Step 3), not here.

`linode-cli` is just a typed wrapper over the Linode REST API — the same API the CCM
calls from inside your cluster. Confirm it can talk to your account:

```bash
linode-cli regions list                     # every command is `<service> <action>` — here: regions, list
linode-cli linodes types --text | head      # node plans + prices; --text = tab-separated (greppable), not the default table
```

**What to look for:** the `types` output lists plan IDs like `g6-standard-4` and their
hourly/monthly price. That price column is the thing you're now responsible for. If these
commands error, your token or region default is wrong — fix it before spending money.

## Step 2 — See the available Kubernetes versions

```bash
linode-cli lke versions-list
```

**What to look for:** confirm `1.34` is offered. If it isn't (LKE drops old versions over
time), pick the newest version shown and use that for `--k8s_version` in Step 3. If you pin
a version LKE doesn't list, `cluster-create` rejects it — a cheap failure to hit now rather
than after a typo.

## Step 3 — Create the cluster

Start small and CPU-only. You'll add the expensive GPU pool in lab-03 so the priciest
nodes exist for the least time — that ordering is deliberate cost discipline, not an
accident.

```bash
linode-cli lke cluster-create \
  --label learn-k8s-platform \        # cluster name; you'll match on it in the next block to grab the ID
  --region us-ord \                    # MUST be a region from `regions list` (Step 1) — Chicago here
  --k8s_version 1.34 \                 # MUST be a version from `versions-list` (Step 2), else rejected
  --node_pools.type g6-standard-4 \    # worker plan: 4 vCPU / 8 GB, from the `types` list — this is what you pay for
  --node_pools.count 2                 # how many of that worker the scheduler gets; 2 is the floor for these labs
```

Each flag maps to a thing you already understand: `--node_pools.type` is the worker plan
(CPU/RAM/price), `--node_pools.count` is how many nodes the scheduler gets to place pods
on. The control plane isn't in here because you don't pay for or size it — that's the
"managed" part.

`us-ord` (Chicago) is just *one* region — substitute any region from `regions list` near
you. `g6-standard-4` is a 4 vCPU / 8 GB node from the `types` list, big enough for these
labs; `count 2` gives the scheduler two workers. None of these are required values — they're
a sensible default pulled from the lists you ran in Step 1.

**What you should see:** the command returns immediately with a JSON blob for the new cluster
(its `id`, `status: ready` may take a minute) — `cluster-create` only *requests* the cluster;
Akamai provisions the control plane and nodes asynchronously. The meter starts now, not when
nodes go `Ready`.

Grab the cluster ID — you'll need it for every later `linode-cli lke` call. The Python here
just pulls the numeric `id` of the cluster labeled `learn-k8s-platform` out of the JSON list:

```bash
export LKE_ID=$(linode-cli lke clusters-list --json | python3 -c \
  'import sys,json;print([c["id"] for c in json.load(sys.stdin) if c["label"]=="learn-k8s-platform"][0])')
echo "cluster id: $LKE_ID"   # a number like 123456 — re-run the export in any new shell, it isn't persisted
```

- `--json` makes `clusters-list` emit machine-readable JSON instead of the human table; the
  Python filters the list to the one matching your `--label` and prints its `id`.
- `export` puts `LKE_ID` in *this shell only* — a fresh terminal won't have it, so re-run this
  block (or the whole `KUBECONFIG` setup) if your `linode-cli lke` calls later complain about a
  missing ID.

## Step 4 — Get the kubeconfig

LKE hands you the kubeconfig base64-encoded inside a JSON field; decode it to a file and
point `kubectl` at that file via `KUBECONFIG`. The Python one-liner does exactly that —
base64-decodes the `kubeconfig` field into plain YAML (the `reference/operating-clusters.md`
note does the same decode with `jq`/`base64 -d` if you prefer those tools):

```bash
linode-cli lke kubeconfig-view --json $LKE_ID \
  | python3 -c 'import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)[0]["kubeconfig"]).decode())' \
  > lke-kubeconfig.yaml                       # base64-decode the `kubeconfig` JSON field → plain YAML on disk

export KUBECONFIG=$PWD/lke-kubeconfig.yaml    # point kubectl at THIS file; absolute path so it survives `cd`
kubectl get nodes -o wide                     # first real call through the new kubeconfig
```

- `KUBECONFIG` is an env var `kubectl` reads to decide *which* cluster it talks to. Set it
  (not `--kubeconfig` on every call) and the rest of the phase Just Works against LKE. It's
  **ambient state** — the same property the "Break it" section below weaponizes.
- If `kubeconfig-view` errors with "not yet available," the control plane is still coming up;
  wait ~30s and retry. The file must contain real YAML (a `clusters:`/`users:` block), not an
  error string — `head lke-kubeconfig.yaml` if `get nodes` fails to connect.

**What to look for:** two nodes, both `Ready`. The `-o wide` columns show real public/
private IPs and the LKE node image — not the `kindnet` containers you saw before. The
control-plane nodes are *absent* from this list on purpose: you can talk to the apiserver
but you don't own the machines it runs on. You're now driving infrastructure that costs
money — note the time.

## Step 5 — Sanity-check the cloud integration

```bash
kubectl get sc                 # a linode-block-storage StorageClass exists
kubectl -n kube-system get pods | grep -i ccm   # the Cloud Controller Manager
```

**What to look for:** a `StorageClass` named `linode-block-storage` (and a
`-retain` variant) — that's the CSI driver's offer to provision volumes. And a running
`ccm-linode-*` pod — the controller that turns Services into NodeBalancers. These two
were *never present* on kind. Their existence is the entire reason lab-02's PVC and
`LoadBalancer` will do something real. Read this step as "confirm the cloud is wired in,"
not boilerplate.

## Break it, then read the error (Kelsey lens)

The most expensive mistake on real infra isn't a wrong manifest — it's running the right
command against the *wrong cluster*. Unset `KUBECONFIG` so you fall back to your kind
context, then run the same command:

```bash
unset KUBECONFIG
kubectl config current-context     # NOT the LKE cluster anymore
kubectl get nodes                  # kind's nodes, not Akamai's
```

**Read what happened:** identical command, completely different cluster, *no warning*.
Nothing in `kubectl get nodes` or `kubectl delete` tells you which cluster you're aimed
at — the context does, silently. This is how people `kubectl delete` production. The
architectural lesson: kubeconfig context is ambient state, and every destructive command
inherits it. **Always run `kubectl config current-context` before anything destructive on
real infra.** Re-export `KUBECONFIG` before continuing:

```bash
export KUBECONFIG=$PWD/lke-kubeconfig.yaml
```

## Checkpoint — you can now explain…

- [ ] **What a managed control plane is.** Akamai runs apiserver/scheduler/etcd; you own
  and pay only for worker nodes. Your kubeconfig points at a public apiserver endpoint.
- [ ] **What's different from kind.** Same API, but a real CCM and CSI driver are
  watching your objects — so cloud-facing objects (`LoadBalancer`, `PVC`) finally
  provision real, billable infrastructure instead of sitting `<pending>` or faking it.
- [ ] **Where the bill comes from.** Per worker node, plus per cloud resource the CCM/CSI
  create on your behalf — which is why lab-04 tears everything down the same day.
- [ ] **Why context discipline matters.** `kubectl`'s target is ambient; verify it before
  any delete.

## Next

→ `lab-02-nodebalancer-storage.md`: apply the *exact same* `LoadBalancer` Service and
`PVC` you used on kind, and watch the CCM and CSI driver you just confirmed turn them into
a real Akamai NodeBalancer and Block Storage volume.
