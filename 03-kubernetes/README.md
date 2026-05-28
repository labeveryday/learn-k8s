# Phase 3: Kubernetes

**Time budget:** ~50%. This is the main event.

## Why Kubernetes exists (Stanford)

A single container is easy. A thousand containers across fifty machines, some failing, scaling under load, rolling out new versions without downtime — that's hard. Kubernetes solves this with one core pattern:

> **You declare desired state; controllers loop to make reality match.**

Every feature (Deployments, Services, HPA) is a controller reconciling some piece of state. Learn that frame and the whole system becomes legible.

## Architecture in one picture

```
┌─────────────────── Control Plane ────────────────────┐
│  kube-apiserver  ◄── the only thing talking to etcd  │
│       ▲                                              │
│       │  (every other component uses the API)        │
│  ┌────┴────┐   ┌────────────────────┐   ┌─────────┐  │
│  │  etcd   │   │ kube-scheduler     │   │   cm    │  │
│  └─────────┘   │ (assigns Pods→Node)│   │(control │  │
│                └────────────────────┘   │ -lers)  │  │
│                                         └─────────┘  │
└──────────────────────▲───────────────────────────────┘
                       │
               ┌───────┴──────┬──────────┐
               ▼              ▼          ▼
          ┌────────┐    ┌────────┐   ┌────────┐
          │ Node 1 │    │ Node 2 │   │ Node N │
          │kubelet │    │kubelet │   │kubelet │
          │kube-   │    │kube-   │   │kube-   │
          │ proxy  │    │ proxy  │   │ proxy  │
          │runtime │    │runtime │   │runtime │
          └────────┘    └────────┘   └────────┘
```

- **apiserver**: REST front door; all reads/writes go here.
- **etcd**: consensus-replicated KV store; the single source of truth.
- **scheduler**: watches unscheduled Pods, assigns them to nodes.
- **controller-manager**: runs built-in controllers (Deployment, ReplicaSet, Node, etc.).
- **kubelet**: per-node agent; takes Pod specs from apiserver and runs them via container runtime.
- **kube-proxy**: programs iptables/IPVS to implement Services.

## Mental model for every resource

```
spec (what you want) ──► controller ──► status (what is)
                             ▲
                             └── reconcile loop
```

## Labs

1. `lab-01-architecture-and-kind.md` — spin up a cluster, map parts to the diagram
2. `lab-02-pods.md` — the atom of K8s
3. `lab-03-deployments-and-replicasets.md` — controllers, rolling updates
4. `lab-04-services-and-networking.md` — ClusterIP, NodePort, LoadBalancer
5. `lab-05-config-and-secrets.md` — ConfigMap, Secret, env vs volume
6. `lab-06-storage.md` — PV, PVC, StorageClass
7. `lab-07-ingress.md` — L7 routing
8. `lab-08-probes-and-lifecycle.md` — liveness, readiness, startup; PDBs
9. `lab-09-rbac-and-security.md` — ServiceAccount, Roles, SecurityContext
10. `lab-10-observability-and-debug.md` — logs, events, `kubectl debug`
11. `lab-11-helm-and-kustomize.md` — packaging
12. `exercises.md` — drills and a capstone (redeploy the Phase 2 app)

## The one workflow you'll use all day

```bash
kubectl apply -f manifest.yaml        # declare state
kubectl get <kind>                    # what exists
kubectl describe <kind> <name>        # full status + events
kubectl logs <pod>                    # stdout/stderr
kubectl exec -it <pod> -- sh          # shell in
kubectl explain <kind>.<field>        # docs without internet
kubectl delete -f manifest.yaml       # remove
```

Memorize this loop. 90% of K8s operations are variations.

## Panel notes

> **Kelsey:** "Start with raw YAML, not Helm. When Helm breaks, you'll thank yourself. `kubectl explain pod.spec.containers` is better than most tutorials."
>
> **Stanford:** "Pods, not containers, are the atomic unit. A Pod is a *shared-fate group* with a shared network namespace. This choice has consequences everywhere."
>
> **MIT:** "Every `kubectl apply` is an HTTP PATCH to the apiserver. `kubectl -v=8 get pods` shows the raw calls. Try it once."
