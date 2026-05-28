# Lab 02 — NodeBalancer & Block Storage: the no-ops become real infrastructure

**Goal:** apply the *same* `LoadBalancer` Service and `PersistentVolumeClaim` you used on
kind, and trace exactly which controller turns each one into billable Akamai
infrastructure — so "Kubernetes is an abstraction over a cloud" stops being a slogan and
becomes a mechanism you can name.

**Time:** ~25 min · **Cost:** 💸 NodeBalancer + volume accrue charges — delete in lab-04

## The problem

In `03-kubernetes/lab-04` you learned a `type: LoadBalancer` Service is "NodePort + a
cloud LB in front (no-op on kind)." You wrote the YAML, it sat in `<pending>`, and you
moved on. Same with a PVC: it bound, but to a directory on your laptop. You never saw the
*second half* of either object — the part where something outside the cluster gets
created. That half doesn't exist on kind because there's no cloud to call.

You need to see what those objects were always *for*: a stable public IP that survives
pod churn (the LoadBalancer), and a disk that survives pod and even cluster death (the
volume). Nothing you ran locally could show you that.

## What it replaces, and why kind couldn't do it

| Object | On kind | On LKE | The missing piece kind lacked |
|---|---|---|---|
| `type: LoadBalancer` Service | `<pending>` forever | Akamai **NodeBalancer** + public IP | a **cloud-controller-manager** calling the cloud API |
| `PersistentVolumeClaim` | hostPath dir, node-local | **Block Storage** volume, attachable | a **CSI driver** that provisions real disks |

The YAML is byte-for-byte the same. What changed is that LKE pre-installed the two
controllers (you confirmed both in lab-01: the `ccm-linode-*` pod and the
`linode-block-storage` StorageClass). Those controllers are the implementation that kind
had no equivalent for. This is the lab where the abstraction's bottom edge becomes
concrete.

## Under the hood (MIT hat): two controllers, one pattern

Both halves are the same Kubernetes pattern — a controller *watches* an object and
reconciles the real world to match it — applied to two different resources.

**LoadBalancer Service → NodeBalancer:**

```
type: LoadBalancer Service
   │  cloud-controller-manager watches Services
   ▼
CCM calls Linode API → creates a NodeBalancer
   │  configures it to forward :80 → every node's NodePort
   ▼
CCM writes the public IP back into status.loadBalancer.ingress
   │  (so EXTERNAL-IP fills in)
   ▼
client → NodeBalancer IP → node NodePort → kube-proxy DNAT → Pod   ◄── Phase 03 stack
```

Notice the bottom of that chain is *exactly* what you learned in Phase 03. A
`LoadBalancer` Service is still a NodePort underneath; the CCM just put a real cloud load
balancer in front of the node ports and handed you its IP. kube-proxy and CoreDNS still do
the in-cluster packet work.

**PVC → Block Storage volume:**

```
PersistentVolumeClaim (storageClassName: linode-block-storage-retain)
   │  CSI provisioner sees an unbound PVC matching that StorageClass
   ▼
CSI driver calls Linode API → creates a Block Storage volume
   │  creates a PersistentVolume object, binds PVC ↔ PV
   ▼
when a Pod mounts the PVC → CSI attaches the volume to that Pod's node, formats, mounts
```

**CSI** = Container Storage Interface, the standard plugin API that lets *any* storage
vendor implement "create / attach / mount a volume" without the change living in
Kubernetes core. The `linode-block-storage` StorageClass is Linode's CSI driver
advertising "I can make volumes." The PVC is your *request*; the PV is the *granted*
resource. This is the same request/grant split you saw with Gateway (request) vs.
programmed proxy (grant) in Phase 05.

## Step 1 — Confirm you're on LKE

```bash
export KUBECONFIG=$PWD/lke-kubeconfig.yaml
kubectl config current-context        # must be the LKE cluster, NOT kind
```

**What to look for:** the LKE context name. If it says `kind-...`, stop — you're about to
provision (or break) the wrong cluster. This check is the lab-01 break-it lesson applied.

## Step 2 — A LoadBalancer Service → NodeBalancer

```bash
kubectl apply -f manifests/lke-loadbalancer-svc.yaml
kubectl get svc echo-lb -w
```

This manifest is a 2-replica `echo` Deployment plus a `type: LoadBalancer` Service. Watch
the `EXTERNAL-IP` column.

**What to look for:** it starts `<pending>` (the CCM hasn't finished the API call yet),
then within a minute fills in with a public IP. That `<pending>→IP` transition is the CCM
finishing the NodeBalancer creation and writing the address back into the Service status.
On kind it never left `<pending>` — now you're watching the missing half happen live.

Confirm the resource exists on the Akamai side, not just in Kubernetes:

```bash
linode-cli nodebalancers list
```

**What to look for:** a NodeBalancer whose IP matches `EXTERNAL-IP`. Two systems agree —
the Kubernetes Service status and the cloud inventory — because one controller wrote both.

Hit the public IP:

```bash
export LB_IP=$(kubectl get svc echo-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -s http://$LB_IP/
```

**What to look for:** the echo server's JSON response, returned from outside the cluster.
Traffic went: your machine → Akamai NodeBalancer → a node's NodePort → kube-proxy → one of
the two `echo` pods. The annotation in the manifest
(`service.beta.kubernetes.io/linode-loadbalancer-throttle`) is how you tune the
NodeBalancer — the cloud-specific knob the portable abstraction deliberately leaves room
for. Remove it and the Service still works on defaults.

## Step 3 — A PVC → Block Storage volume

```bash
kubectl apply -f manifests/block-storage-pvc.yaml
kubectl get pvc data-pvc -w
```

**What to look for:** the STATUS column goes `Pending` → `Bound`. `Pending` is the gap
between "you asked for a volume" and "the CSI driver finished creating one and bound a PV
to your claim." On kind a PVC bound almost instantly to a local dir; here the delay *is*
the real cloud API call. Verify both sides again:

```bash
kubectl get pv
linode-cli volumes list
```

**What to look for:** a PV the CSI driver auto-created (you never wrote a PV manifest —
"dynamic provisioning" means the driver makes the PV for you), and a matching Block
Storage volume in the Akamai inventory.

The `linode-block-storage-retain` StorageClass means the volume *survives* PVC deletion —
exactly the "don't lose my data on a typo" guarantee you want in production. It's also
why teardown in lab-04 deletes volumes explicitly: the safety feature becomes a billing
trap if you forget it.

## Step 4 — The whole point, stated plainly

You wrote portable Kubernetes objects. The CCM and CSI driver translated them into Akamai
NodeBalancers and volumes. *Nothing in your YAML named Akamai except optional
annotations.* That is why you could learn every one of these objects on free kind first
and have it transfer, unchanged, to a cloud that charges you.

## Break it, then read the error (Kelsey lens)

Ask for a volume *below* the provider's minimum and watch the CSI driver round you up
instead of erroring. Edit `manifests/block-storage-pvc.yaml` to request `1Mi` (or use a
fresh PVC name) and apply:

```bash
kubectl get pvc data-pvc
# CAPACITY shows 10Gi — NOT 1Mi — and STATUS is Bound, not Pending.
kubectl get pv -o custom-columns=NAME:.metadata.name,CAP:.spec.capacity.storage
linode-cli volumes list   # the real Akamai volume is 10 GB
```

**Read what happened — and why it matters:** the Linode Block Storage CSI has a **10 Gi
minimum**. Request anything smaller and it does *not* fail, *not* stay `Pending`, and
*not* warn you — it silently provisions a 10 Gi volume and binds. The architectural lesson
is about **provider floors**: a CSI driver is free to satisfy your request with *more*
than you asked for, and "Bound" only tells you the claim was satisfied, not that it was
satisfied at the size you wanted. You'd be billed for 10 GB after requesting 1 MB, with no
error anywhere. **The status to trust is `CAPACITY`, not `STATUS: Bound`** — checking
"did it bind?" is not the same as "did I get what I asked for?" On a cloud, the difference
is money.

## Checkpoint — you can now explain…

- [ ] **Which controller turns a `LoadBalancer` Service into a NodeBalancer.** The
  cloud-controller-manager — it watches Services, calls the Linode API, and writes the
  public IP back into `status.loadBalancer.ingress`. Underneath it's still a NodePort that
  kube-proxy DNATs to a pod.
- [ ] **Which controller turns a `PVC` into a real disk.** The Linode Block Storage CSI
  driver — it dynamically provisions a Block Storage volume, creates a PV, binds it to
  your claim, and attaches/formats/mounts it when a pod uses it.
- [ ] **Why the same YAML worked on kind and LKE.** Your objects never named Akamai; the
  cloud-specific work lives entirely in the CCM and CSI driver. That's the portability
  payoff of learning on kind.
- [ ] **Why `Bound` isn't the whole story.** Provider floors (10 Gi minimum) can give you
  more than you requested with no error. Verify `CAPACITY`, and remember `-retain`
  volumes outlive their PVCs — and keep billing.

## Next

→ `lab-03-gpu-vllm.md`: add a GPU node pool and learn the *third* "kind faked it" rung —
how a physical GPU becomes a schedulable Kubernetes resource so vLLM can run at real
speed.
