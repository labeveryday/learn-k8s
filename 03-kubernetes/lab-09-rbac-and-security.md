# Lab 09: RBAC and Pod Security

**What you'll build:** a least-privilege `ServiceAccount` that a Pod runs as, wired to a
`Role` that grants read-only access to Pods and nothing else, then you'll prove the limit by
watching the same Pod succeed at `get pods` and get forbidden on `delete pod`. After that
you'll harden the container itself with a `securityContext` and lock a whole namespace down with
**Pod Security Standards**. RBAC answers "what may this workload ask the apiserver for?";
SecurityContext + PSS answer "how much can the container do on the node if it's compromised?"
Two different walls, both load-bearing.

> **The one idea:** every request to the apiserver is authenticated (who are you?)
> then authorized (are you allowed?). RBAC is the authorization layer, and it is default-deny:
> a subject can do only what a binding explicitly grants. Every section below is you adding
> one allow rule and proving everything else is still denied.

## 1. Who are "users" in K8s?

- **Users** (humans): managed externally (certs, OIDC). K8s has no User object.
- **ServiceAccounts** (workloads): K8s objects, mounted as tokens into Pods.

Every Pod runs as a SA (`default` if unspecified). That SA has (or doesn't have) permissions via RBAC.

The distinction matters because only the SA side is something you create and bind in YAML. A
human's identity comes from outside the cluster; a workload's identity is a ServiceAccount object
you can `kubectl apply`. This lab grants permissions to a SA, because that's the part Kubernetes
owns.

## 2. RBAC primitives

Authorization is built from two halves: a **role** (a set of allowed verbs on resources) and a
**binding** (who gets that role). You never grant permission to a subject directly; you point a
binding at a role. Verbs = the actions you're allowing (get/list/watch/create/update/delete...).

| Kind | Scope | Purpose |
|------|-------|---------|
| `Role`         | namespace | verbs on resources |
| `ClusterRole`  | cluster   | same, cluster-wide or non-namespaced |
| `RoleBinding`  | namespace | grant Role to subject |
| `ClusterRoleBinding` | cluster | grant ClusterRole to subject |

The split is deliberate: a `Role` is a capability with no owner, and a `RoleBinding` is the
assignment of that capability to a subject. The same Role can be bound to ten different SAs by
ten different RoleBindings: define the permission once, hand it out many times.

## 3. Example: a read-only SA

> These objects are all namespaced (Role, RoleBinding, ServiceAccount, the Pod), and none set `namespace:`, so they land in your **current** namespace, and the SA, Role, and Pod must share it for the binding to work. Confirm where with `kubectl config view --minify | grep namespace` (lab-01 left you in `default`).

`manifests/rbac-readonly.yaml` has three objects that together say "the `viewer` SA may read Pods,
period." Read them as a chain: the SA is the identity, the Role is the permission, the RoleBinding
welds them together.

```yaml
apiVersion: v1
kind: ServiceAccount
metadata: { name: viewer }      # the identity a Pod will run as (no powers of its own yet)
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role                       # a namespace-scoped permission set (RBAC lives in its own API group)
metadata: { name: pod-reader }
rules:
  - apiGroups: [""]             # "" is the CORE group (pods, services, configmaps live here)
    resources: ["pods", "pods/log"]   # the objects this rule covers; pods/log is a SUBRESOURCE
    verbs: ["get", "list", "watch"]   # read-only: no create/update/delete granted = denied
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding                # the assignment: give roleRef to every subject below
metadata: { name: viewer-pod-reader }
subjects:
  - kind: ServiceAccount        # WHO gets the role
    name: viewer
roleRef:                         # WHICH role they get; must be a Role/ClusterRole that exists
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: pod-reader
```

Two things this YAML quietly depends on:

- **The empty-string `apiGroups: [""]` is the core group, not "all groups."** Pods, Services, and
  ConfigMaps live there; Deployments live in `apps`, Ingresses in `networking.k8s.io`. Forgetting
  this is the classic RBAC bug: you write a Role for "pods" with the wrong group and it silently
  grants nothing.
- **`roleRef` is immutable and unforgiving.** Once a RoleBinding is created you can't change which
  Role it points to (delete + recreate instead), and if `name: pod-reader` doesn't match an
  existing Role *in the same namespace*, the binding applies but grants zero, with no error.
  RBAC is additive and silent: nothing complains, the Pod stays forbidden.

Use it: a Pod that runs as `viewer` and carries a `kubectl` so it speaks to the apiserver with
that SA's token:

```yaml
apiVersion: v1
kind: Pod
metadata: { name: tools }
spec:
  serviceAccountName: viewer    # run as the read-only SA, NOT the namespace's 'default' SA
  containers:
    - name: kubectl
      image: bitnami/kubectl:latest
      command: ["sh", "-c", "kubectl get pods && sleep 3600"]   # prove read works, then idle so we can exec in
```

The `serviceAccountName: viewer` line is the whole point: it makes the kubelet mount viewer's
token at `/var/run/secrets/kubernetes.io/serviceaccount/` instead of `default`'s. The in-Pod
`kubectl` auto-discovers that token and the apiserver address from env, so every call it makes is
authorized as `viewer`. The `sleep 3600` keeps the container alive after the first command so you
can `exec` into it below.

Apply all four objects: the RBAC trio from the file, and the `tools` Pod shown above:

```bash
kubectl apply -f manifests/rbac-readonly.yaml     # ServiceAccount + Role + RoleBinding
kubectl apply -f - <<'EOF'                         # the tools Pod, running AS the viewer SA
apiVersion: v1
kind: Pod
metadata: { name: tools }
spec:
  serviceAccountName: viewer
  containers:
    - name: kubectl
      image: bitnami/kubectl:latest
      command: ["sh", "-c", "kubectl get pods && sleep 3600"]
EOF
kubectl wait --for=condition=ready pod/tools --timeout=60s
```

Now exercise the SA's real permissions from inside that Pod:

```bash
kubectl exec tools -- kubectl get pods           # works
kubectl exec tools -- kubectl delete pod web     # forbidden
```

- `kubectl exec tools -- <cmd>` runs `<cmd>` inside the `tools` Pod, so the inner `kubectl`
  authenticates as `viewer`, not as you. This is the test: it exercises the SA's real permissions,
  not your kubeconfig's.
- `delete pod web` targets the bare `web` Pod from lab-02 (lab-03's Deployment makes
  `web-<hash>` Pods, not a bare one named `web`). The `delete` verb isn't in the Role's `verbs`
  list, so it's denied. You'd get `Forbidden` even if no `web` Pod existed, because RBAC
  checks the verb against the rule, not whether the object is there.

**What you should see:** the first command lists the namespace's Pods (success; `list` is
granted). The second fails with `Error from server (Forbidden): ... cannot delete resource "pods"`.
That Forbidden is RBAC working as designed: default-deny, and `delete` was never allowed.
The error names the verb, resource, and SA; read it, and it tells you which rule you'd need
to add.

## 4. `can-i`

You don't have to deploy a Pod to test permissions. `kubectl auth can-i` asks the apiserver's
authorizer the same question RBAC asks at request time:

```bash
kubectl auth can-i create deployments
kubectl auth can-i list pods --as=system:serviceaccount:default:viewer
```

- The first asks about **your** identity (the current kubeconfig user).
- `--as=...` **impersonates** another subject so you can test *its* permissions without being it.
  The format `system:serviceaccount:<namespace>:<name>` is the canonical username every
  ServiceAccount gets: here, the `viewer` SA in `default`.

**What you should see:** `yes` or `no` per line. `list pods --as=...viewer` returns `yes` (the
Role grants `list`); `create deployments` returns `no` for `viewer` if you point `--as` at it.
This is how you debug "why can't this pod do X" before shipping it: impersonate the SA, ask
`can-i`, and you get the authorizer's verdict directly.

## 5. SecurityContext (Pod- and container-level)

RBAC guards the apiserver; `securityContext` guards the node. It constrains what the
container's process can do at the Linux level (what UID it runs as, whether it can write its
filesystem, whether it can gain new privileges) so a compromised container can't escalate.

```yaml
spec:
  securityContext:                           # POD-level: defaults applied to all containers
    runAsNonRoot: true                       # refuse to start if the image runs as root
    runAsUser: 1000                          # run as this UID, not root
    fsGroup: 1000                            # group that owns mounted volumes
    seccompProfile: { type: RuntimeDefault } # use the runtime's default syscall filter
  containers:
    - name: api
      securityContext:                       # CONTAINER-level: overrides/extends the Pod's
        readOnlyRootFilesystem: true         # no writes to the container filesystem
        allowPrivilegeEscalation: false      # block setuid/sudo-style privilege gain
        capabilities:
          drop: ["ALL"]                      # drop all Linux capabilities, add back none
```

Two gotchas hide in these fields:

- **`runAsNonRoot: true` is a check, not a fix.** It doesn't make the container non-root; it
  refuses to start if the image's user is root (UID 0). If your image has no non-root user and
  you don't set `runAsUser`, the Pod fails to start with `CreateContainerConfigError`. Pair it
  with `runAsUser: 1000` (or a non-root image).
- **`readOnlyRootFilesystem: true` breaks apps that write to disk.** Anything that needs scratch
  space (temp files, caches, PID files) now errors. The fix is to mount an `emptyDir` at those
  paths: the root FS stays read-only, the writable spots are explicit.

Ship production Pods with this baseline.

## 6. Pod Security Standards (PSS)

SecurityContext is opt-in per Pod, easy to forget. **Pod Security Standards** flip it to
enforced per namespace: the apiserver rejects any Pod that violates the profile, so you can't
forget. K8s ships 3 profiles: `privileged` (no restrictions), `baseline` (blocks known
escalations), `restricted` (the hardened baseline from section 5). Enforce per-namespace with
labels:

```bash
kubectl label ns demo pod-security.kubernetes.io/enforce=restricted
```

- `kubectl label ns demo ...` adds the label to the `demo` namespace. The label key
  `pod-security.kubernetes.io/enforce` is what the built-in Pod Security admission controller
  watches; the value (`restricted`) selects the profile.
- The `enforce` mode hard-rejects. There are also `warn` and `audit` modes (same key prefix) that
  let violations through but tell you about them, useful for rolling PSS onto an existing
  namespace without breaking running workloads.

**What you should see:** after labeling, creating a Pod that runs as root or wants extra
capabilities is rejected at apply time by an admission check, the apiserver gatekeeper that
validates objects before they're stored. The Pod never reaches etcd; you get the violation list in
the error. That's the difference from section 5: SecurityContext configures one Pod, PSS
enforces a rule on every Pod in the namespace.

## 7. Practice

1. Create the read-only SA + RoleBinding. Confirm it can get pods but not delete.
2. Add a `restricted` PSS label to a new namespace. Try to deploy a pod that runs as root; it should be rejected.
3. `kubectl auth can-i --list` for your current user. Skim what cluster-admin grants.

## Next

→ `lab-10-observability-and-debug.md`: your workloads are now scoped and hardened. When one
still misbehaves, you need the debug loop. Next you'll learn the `get → describe → logs → events`
sequence that turns a `Forbidden` or `CreateContainerConfigError` into a root cause.
