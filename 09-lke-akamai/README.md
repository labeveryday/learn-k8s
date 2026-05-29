# 09 — LKE: where the faked objects get real (and get a bill)

> The whole stack you built on kind, now on a managed Akamai (Linode) Kubernetes cluster —
> with real load balancers, persistent storage, and GPUs for vLLM. Same objects. Real
> infrastructure underneath.

## The big idea: kind faked everything

Every cloud-facing object you used in Phases 03–08 was a convincing stub on kind:

| Object | On kind (faked) | On LKE (real) | The controller that makes it real |
|---|---|---|---|
| `type: LoadBalancer` Service | no-op, stays `<pending>` | Akamai **NodeBalancer** + public IP | **cloud-controller-manager** calls the Linode API |
| `PersistentVolumeClaim` | a directory on your laptop | **Block Storage** volume, attachable | **CSI driver** dynamically provisions a disk |
| GPU workload | none — tiny CPU model | **vLLM on a real GPU** | **NVIDIA device plugin** advertises `nvidia.com/gpu` so the scheduler can place it |

kind taught you the *nouns* for free. The catch was that none of them provisioned anything
outside your machine. LKE removes that limitation by pre-installing the controllers that
turn each object into billable infrastructure — and the price of "real" is that the meter
runs. Doing this phase last means you already understand every object you're now paying to
run; you're learning the *mechanism* under each, not the object itself.

## Why this phase is last

Each earlier phase taught a layer on free infrastructure. This phase re-runs them on
hardware that charges you, so it should come after you understand the objects — never
before. The order *inside* the phase is the same discipline: provision CPU-only first, add
the expensive GPU pool last (lab-03), and tear everything down the same day (lab-04).

> ⚠️ This phase **costs Akamai credits**. Provision, do the lab, and tear the cluster down
> the **same day**. The capstone lab includes teardown steps — and a check that the
> cluster being gone does *not* mean its NodeBalancer and retained volumes are gone.

## Prereqs

- An Akamai Cloud / Linode account + API token.
- `linode-cli` installed and configured (`pip install linode-cli && linode-cli configure`).
- `kubectl` + `helm` (from `00-prep`).
- Phases 03–08 understood — this phase *re-runs* them on LKE, it doesn't re-teach them.

> **Run every command in this phase from inside `09-lke-akamai/`** (`cd 09-lke-akamai`).
> The labs use relative paths: `manifests/...` is this folder, `../05-gateway-api/...` reaches
> a sibling phase, and lab-01 writes `lke-kubeconfig.yaml` here. Run them from anywhere else
> and those paths don't resolve.

## Objectives

1. Provision an LKE cluster with `linode-cli`, point `kubectl` at its kubeconfig, and
   explain what a *managed control plane* is (lab-01).
2. Apply a `LoadBalancer` Service → watch the **cloud-controller-manager** create a real
   **NodeBalancer**; apply a `PVC` → watch the **CSI driver** provision **Block Storage**
   (lab-02).
3. Add a **GPU node pool** and trace how a physical GPU becomes a schedulable
   `nvidia.com/gpu` resource, then run vLLM on it (lab-03).
4. Capstone: stack Gateway → AI gateway → vLLM on GPU end-to-end on LKE, watch one request
   cross every floor, then tear it all down — and verify (lab-04).

## The mechanism, one floor at a time

The through-line of this phase is request/grant: you write a portable Kubernetes *request*
(Service, PVC, GPU limit), and a provider controller *grants* it by creating real
infrastructure and writing the result back into the object's status.

```
LoadBalancer Service ─► CCM ─► NodeBalancer + public IP   (lab-02)
PVC                  ─► CSI ─► Block Storage volume + PV   (lab-02)
nvidia.com/gpu limit ─► scheduler ─► pod on a GPU node     (lab-03)
   (device plugin advertised the GPU so the scheduler could count it)
```

Nothing in your YAML names Akamai except optional annotations. That portability — learn on
kind, run on the cloud unchanged — is the payoff of the entire track.

## Labs

| Lab | Mechanism it teaches |
|---|---|
| 01 | `lab-01-provision-lke.md` — managed control plane; what's different from kind; kubeconfig context discipline |
| 02 | `lab-02-nodebalancer-storage.md` — CCM turns a Service into a NodeBalancer; CSI turns a PVC into Block Storage (incl. the 10Gi provider floor) |
| 03 | `lab-03-gpu-vllm.md` — driver → device plugin advertises `nvidia.com/gpu` → scheduler matches the limit; no taint/toleration on LKE |
| 04 | `lab-04-capstone-teardown.md` — full stack on real infra, one request through all floors, then delete and *verify* nothing lingers |

## The payoff

This is the deliverable that *is* your job: a documented, reproducible AI-platform
deployment on Akamai Cloud — the basis for blogs, talks, code samples, and friction logs.
Every lab here doubles as content, and every "under the hood" section is an explanation you
can teach from.

> Start with `lab-01-provision-lke.md` and work through to `lab-04-capstone-teardown.md`.
> Every command and manifest is pinned to current LKE/CSI/GPU specifics — and the capstone
> ends by tearing the cluster down the same day so the meter stops.
