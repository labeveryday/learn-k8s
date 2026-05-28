# Lab 09 — RBAC and Pod Security

## 1. Who are "users" in K8s?

- **Users** (humans): managed externally (certs, OIDC). K8s has no User object.
- **ServiceAccounts** (workloads): K8s objects, mounted as tokens into Pods.

Every Pod runs as a SA (`default` if unspecified). That SA has (or doesn't have) permissions via RBAC.

## 2. RBAC primitives

| Kind | Scope | Purpose |
|------|-------|---------|
| `Role`         | namespace | verbs on resources |
| `ClusterRole`  | cluster   | same, cluster-wide or non-namespaced |
| `RoleBinding`  | namespace | grant Role to subject |
| `ClusterRoleBinding` | cluster | grant ClusterRole to subject |

## 3. Example — a read-only SA

`manifests/rbac-readonly.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata: { name: viewer }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: pod-reader }
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: viewer-pod-reader }
subjects:
  - kind: ServiceAccount
    name: viewer
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: pod-reader
```

Use it:

```yaml
apiVersion: v1
kind: Pod
metadata: { name: tools }
spec:
  serviceAccountName: viewer
  containers:
    - name: kubectl
      image: bitnami/kubectl:latest
      command: ["sh", "-c", "kubectl get pods && sleep 3600"]
```

Run it:

```bash
kubectl exec tools -- kubectl get pods           # works
kubectl exec tools -- kubectl delete pod web     # forbidden
```

## 4. `can-i`

```bash
kubectl auth can-i create deployments
kubectl auth can-i list pods --as=system:serviceaccount:default:viewer
```

This is how you debug "why can't this pod do X."

## 5. SecurityContext (Pod- and container-level)

```yaml
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    fsGroup: 1000
    seccompProfile: { type: RuntimeDefault }
  containers:
    - name: api
      securityContext:
        readOnlyRootFilesystem: true
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
```

Ship production Pods with this baseline.

## 6. Pod Security Standards (PSS)

K8s ships 3 profiles — `privileged`, `baseline`, `restricted`. Enforce per-namespace with labels:

```bash
kubectl label ns demo pod-security.kubernetes.io/enforce=restricted
```

Now privileged pods are rejected at admission.

## 7. Practice

1. Create the read-only SA + RoleBinding. Confirm it can get pods but not delete.
2. Add a `restricted` PSS label to a new namespace. Try to deploy a pod that runs as root — should be rejected.
3. `kubectl auth can-i --list` for your current user. Skim what cluster-admin grants.
