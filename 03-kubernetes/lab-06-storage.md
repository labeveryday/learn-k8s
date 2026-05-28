# Lab 06 — Persistent Storage

## 1. The abstraction sandwich

```
PersistentVolumeClaim (what the app asks for)
          │
          ▼ bound to
PersistentVolume (the actual storage, provisioned)
          │
          ▼ comes from
StorageClass (the recipe to provision)
```

- **PVC**: "I need 5Gi, RWO."
- **SC**: cluster config that knows how to create a PV (CSI driver).
- **PV**: the concrete resource (disk, NFS share, EBS volume...).

Most clusters have a default SC. kind ships with `standard` (local path provisioner).

```bash
kubectl get sc
```

## 2. Access modes

| Mode | Meaning |
|------|---------|
| `ReadWriteOnce` (RWO) | mounted rw by one node at a time (most block storage) |
| `ReadOnlyMany`  (ROX) | many nodes ro |
| `ReadWriteMany` (RWX) | many nodes rw (needs NFS/CephFS/etc.) |
| `ReadWriteOncePod` | one Pod, cluster-wide |

## 3. A PVC + Pod

`manifests/pvc-demo.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: writer
spec:
  containers:
    - name: app
      image: alpine:3.19
      command: ["sh", "-c", "echo hello > /data/msg && sleep 3600"]
      volumeMounts:
        - name: d
          mountPath: /data
  volumes:
    - name: d
      persistentVolumeClaim:
        claimName: data
```

```bash
kubectl apply -f manifests/pvc-demo.yaml
kubectl get pvc,pv
kubectl exec writer -- cat /data/msg
kubectl delete pod writer
# Recreate with same PVC — data persists
```

## 4. StatefulSet (preview)

Use for stateful workloads needing stable identity + per-replica storage:

- Stable Pod names (`db-0`, `db-1`) and DNS.
- Per-Pod PVC via `volumeClaimTemplates`.
- Ordered rollout/termination.

Example skeleton:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: db
spec:
  serviceName: db-headless
  replicas: 3
  selector: { matchLabels: { app: db } }
  template:
    metadata: { labels: { app: db } }
    spec:
      containers:
        - name: pg
          image: postgres:16-alpine
          env: [...]
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
  volumeClaimTemplates:
    - metadata: { name: data }
      spec:
        accessModes: [ReadWriteOnce]
        resources: { requests: { storage: 5Gi } }
```

You'll see this pattern whenever you deploy databases, Kafka, etc.

## 5. Practice

1. Create the PVC+Pod. Confirm you can write, delete the Pod, recreate, and the file persists.
2. What happens if you set `storage: 1000Gi` on kind's default SC? Does the PVC bind? (Hint: local-path provisioner gives you whatever the host has.)
3. Read `kubectl explain pvc.spec` — what are `storageClassName`, `volumeMode`, `dataSource`?
