# Lab 06: Persistent Storage

**What you'll build:** a `PersistentVolumeClaim` that requests storage, watch the cluster
bind it to a real `PersistentVolume`, mount it into a Pod, write a file, then delete the Pod
and prove the file survives. The lesson is **decoupling**: a Pod is disposable (lab-03 killed
and replaced them at will), but data can't be. This lab is how you keep state alive across the
Pod churn the controller pattern guarantees. By the end you can read a PVC field-by-field and
predict whether it will bind, and you'll have previewed the `StatefulSet` pattern every database
deploy uses.

> **The one idea:** a Pod's filesystem is **ephemeral**; it dies with the Pod. Persistent
> storage is a *separate lifecycle* you bind to the Pod by reference, so the disk outlives the
> container that mounts it. You don't put data "in" a Pod; you point a Pod at data that exists
> on its own.

## 1. The abstraction sandwich: the shape before the commands

Three objects stack so the app never has to know what kind of disk it's getting:

```
PersistentVolumeClaim (what the app asks for)
          │
          ▼ bound to
PersistentVolume (the actual storage, provisioned)
          │
          ▼ comes from
StorageClass (the recipe to provision)
```

- **PVC**: "I need 5Gi, RWO" (RWO = ReadWriteOnce; access modes table in section 2).
- **SC**: cluster config that knows how to create a PV, via a CSI driver (Container Storage Interface, the standard plugin API for storage backends).
- **PV**: the concrete resource (disk, NFS share, EBS volume...).

This is the same indirection labels gave you in lab-03: the app declares an *intent* (the PVC),
and the cluster fulfills it (a PV) without the app naming a specific disk. Swap the SC and the
same PVC lands on EBS in AWS, a local directory in kind, or NFS; the manifest doesn't change.

Most clusters have a default SC. kind ships with `standard`, backed by the local-path provisioner (it carves PVs out of a directory on the node's disk).

```bash
kubectl get sc
```

**What you should see:** a `standard` StorageClass marked `(default)`. That `(default)` tag is
load-bearing: a PVC that omits `storageClassName` gets the default SC. No default and an
unspecified class → the PVC sits `Pending` forever with nothing to provision it.

## 2. Access modes: who can mount it, and how

The PVC's `accessModes` is a contract about *concurrency*, not a feature you toggle on. The
underlying storage either supports a mode or it doesn't.

| Mode | Meaning |
|------|---------|
| `ReadWriteOnce` (RWO) | mounted rw by one node at a time (most block storage) |
| `ReadOnlyMany`  (ROX) | many nodes ro |
| `ReadWriteMany` (RWX) | many nodes rw (needs NFS/CephFS/etc.) |
| `ReadWriteOncePod` | one Pod, cluster-wide |

The trap: **RWO is per-node, not per-Pod.** Two Pods on the *same* node can share an RWO
volume; two Pods on *different* nodes cannot. If you need many Pods across nodes writing the
same volume, you need RWX, and block storage (EBS, local-path) can't do RWX. That's the wall
people hit when they scale a stateful Deployment past one replica and the new Pods stick at
`ContainerCreating` waiting for a volume they'll never get.

## 3. A PVC + Pod: what each piece does

Here is the request-and-mount in one file. The PVC asks for storage; the Pod references it by
name and mounts it. This is the whole object (`manifests/pvc-demo.yaml`), then the fields that
matter:

```yaml
apiVersion: v1                    # PVCs and Pods are both core v1 (no apps/ group)
kind: PersistentVolumeClaim
metadata:
  name: data                      # the Pod references the PVC by THIS name (claimName below)
spec:
  accessModes: [ReadWriteOnce]    # the concurrency contract from section 2
  resources:
    requests:
      storage: 1Gi                # how much you're asking for - the provisioner sizes the PV to this
---
apiVersion: v1
kind: Pod
metadata:
  name: writer
spec:
  containers:
    - name: app
      image: alpine:3.19
      command: ["sh", "-c", "echo hello > /data/msg && sleep 3600"]  # write, then idle so we can exec in
      volumeMounts:
        - name: d                 # MUST match a volume name in spec.volumes below
          mountPath: /data        # where the volume appears inside the container's filesystem
  volumes:
    - name: d                     # the in-Pod handle for the volume
      persistentVolumeClaim:
        claimName: data           # binds this Pod's volume to the PVC named "data" above
```

Two things beginners get wrong, and both stall the Pod silently:

- **The wiring is a three-name chain: `volumeMounts.name` → `volumes.name` → `claimName`.**
  `volumeMounts` and `volumes` link by the volume's local `name` (`d` here); `claimName` links
  `volumes` to the PVC. Mistype any link and the mount fails: a typo in `name` is rejected,
  but a wrong `claimName` leaves the Pod stuck `Pending` ("persistentvolumeclaim not found").
- **`storage: 1Gi` is a *request*, not a guarantee of usable bytes.** With kind's local-path
  provisioner the size is advisory: it gives you a directory on the host, not a
  size-enforced disk (this is practice #2). On real cloud block storage the size *is*
  enforced and you pay for it.

Apply it and watch the PVC bind to a PV the provisioner created:

```bash
kubectl apply -f manifests/pvc-demo.yaml
kubectl get pvc,pv
kubectl exec writer -- cat /data/msg
kubectl delete pod writer
# Recreate with same PVC - data persists
```

- `get pvc,pv` shows both ends of the binding: the claim you wrote and the volume the SC
  provisioned to satisfy it.
- `exec writer -- cat /data/msg` reads the file the container's `command` wrote on startup,
  proving the mount is live.
- `delete pod writer` removes the Pod **but not the PVC**; they have independent lifecycles,
  which is the entire point.

**What you should see:** the PVC goes `Pending` → `Bound`, and `get pv` lists a PV named
`pvc-<uuid>` (auto-created, `Bound` to `data`). `cat` prints `hello`. After you delete and
recreate a Pod against the same `claimName: data`, `/data/msg` still reads `hello`: the disk
outlived the Pod. (Note: kind's local-path provisioner only creates the PV *once the first Pod
schedules*, called `WaitForFirstConsumer`, so a PVC may sit `Pending` until a Pod references it.
That's expected, not a bug.)

## 4. StatefulSet (preview): stable identity + per-replica storage

A Deployment's Pods are interchangeable; a database's are not. `db-0` is the primary, `db-1`
is a replica, and each needs *its own* disk that follows it across restarts. A `StatefulSet`
gives you that. Use it for stateful workloads needing stable identity + per-replica storage:

- Stable Pod names (`db-0`, `db-1`) and DNS.
- Per-Pod PVC via `volumeClaimTemplates`.
- Ordered rollout/termination.

Example skeleton (`[...]` are placeholders; this is a shape to recognize, not a runnable file):

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: db
spec:
  serviceName: db-headless          # the headless Service that gives each Pod stable DNS (db-0.db-headless...)
  replicas: 3
  selector: { matchLabels: { app: db } }
  template:                         # the Pod blueprint, same as a Deployment's
    metadata: { labels: { app: db } }
    spec:
      containers:
        - name: pg
          image: postgres:16-alpine
          env: [...]
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }  # mounts the per-Pod claim below
  volumeClaimTemplates:             # NOT a static PVC - the StatefulSet stamps one PVC PER replica
    - metadata: { name: data }
      spec:
        accessModes: [ReadWriteOnce]
        resources: { requests: { storage: 5Gi } }
```

The field that makes this different from section 3 is **`volumeClaimTemplates`**: instead of
one shared PVC, the StatefulSet creates a *separate* PVC per replica (`data-db-0`, `data-db-1`,
`data-db-2`), each binding its own PV. The gotcha to file away: those PVCs are **not** deleted
when you delete the StatefulSet. That's deliberate (you don't want `kubectl delete sts db` to
nuke your database), but it means orphaned PVCs accumulate and you clean them up by hand.

You'll see this pattern whenever you deploy databases, Kafka, etc.

## 5. Practice

1. Create the PVC+Pod. Confirm you can write, delete the Pod, recreate, and the file persists.
2. What happens if you set `storage: 1000Gi` on kind's default SC? Does the PVC bind? (Hint: local-path provisioner gives you whatever the host has.)
3. Read `kubectl explain pvc.spec`: what are `storageClassName`, `volumeMode`, `dataSource`?

## Next

→ `lab-07-ingress.md`: your Services (lab-04) each need their own port or LoadBalancer. An
**Ingress** puts one HTTP entry point in front of many Services and routes by host/path: the
L7 layer that turns a cluster of Pods into a website.
