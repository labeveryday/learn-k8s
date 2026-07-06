# Operating a Cluster: the field guide

> The questions you hit at 2am: *what am I connected to, how do I get into a pod,
> how do services find each other, where are my secrets, how do I reach a service
> from my laptop.* This guide answers each one with the mental model first, then
> the command, then where to go deeper.
>
> Every question below is one question: where is the boundary? Between you and the
> cluster (kubeconfig), between pods (the flat pod network), between namespaces
> (DNS + RBAC), between the cluster and the outside world (Service type). Learn the
> boundaries and the commands fall out.

---

## 0. First: do you even have a cluster?

If `kubectl` returns nothing useful, you probably have no cluster and an empty
kubeconfig. That's the normal starting state, not a bug.

```bash
kubectl config get-contexts          # table of every cluster you can talk to
kubectl config current-context       # which one is active right now
kubectl config view --minify         # the active context's cluster URL + user
ls -la ~/.kube/config                # the file itself (may not exist yet)
echo "$KUBECONFIG"                   # override path; unset => ~/.kube/config
```

- **No contexts / "current-context is not set"** → you have zero clusters. Go to
  §1 and make one.
- A context exists → §2 tells you *what kind* of cluster it is.

### Make a local cluster (Linux, this box)

`00-prep/README.md` assumes a Mac (Homebrew + Colima). On Linux:

```bash
# 1. Docker engine (needs sudo - run with `!` in Claude Code, or in your shell)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"      # then log out/in (or: newgrp docker)

# 2. kind + helm (user-space, no sudo)
curl -Lo ~/.local/bin/kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x ~/.local/bin/kind
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# 3. A cluster
kind create cluster --name learn          # ~30s; writes a context to ~/.kube/config
kubectl get nodes                         # one node, Ready
```

`kind` = **K**ubernetes **in** **D**ocker: each "node" is a Docker container. When
you `kind create cluster --name learn`, it adds a context named **`kind-learn`**
to your kubeconfig and makes it current. Deeper: `03-kubernetes/lab-01`.

### Or use a real cluster (Akamai LKE, you have `linode-cli`)

```bash
linode-cli lke clusters-list
linode-cli lke kubeconfig-view <cluster-id> --json        # base64-encoded
# Save it and point kubectl at it:
linode-cli lke kubeconfig-view <id> --json | jq -r '.[0].kubeconfig' | base64 -d > ~/lke.yaml
export KUBECONFIG=~/lke.yaml
kubectl get nodes
```

LKE costs credits. Learn the fundamentals on free local `kind`, then take it to LKE
in `09-lke-akamai/`.

---

## 1. "Am I on kind, or something else?"

The active **context name** and **server URL** tell you:

```bash
kubectl config current-context
kubectl cluster-info                 # prints the API server URL
```

| What you see | What it is |
|---|---|
| context `kind-learn`, server `https://127.0.0.1:PORT` | local **kind** |
| context `k3d-...`, server on `0.0.0.0`/localhost | local **k3d** |
| context `lke12345-...`, server `https://...linodelke.net` | **Akamai LKE** |
| `docker-desktop` / `minikube` | local Docker Desktop / minikube |

Rule of thumb: **localhost API server = local cluster; a public DNS name = a
cloud cluster you're paying for.** Always check `current-context` before you run a
`delete`. This is how people nuke prod.

---

## 2. The kubeconfig, decoded

One file (`~/.kube/config`) with three lists that get **stitched together** by a
context:

```
clusters:   [ {name, server URL, CA cert} ]      ← WHERE the API server is
users:      [ {name, token / cert / exec creds} ]← WHO you authenticate as
contexts:   [ {name, cluster, user, namespace} ] ← a (cluster + user + ns) tuple
current-context: <one context name>              ← the active tuple
```

```bash
kubectl config get-contexts                       # list
kubectl config use-context kind-learn             # switch active cluster
kubectl config set-context --current --namespace=demo   # change default ns
```

Tools **merge** into this file: `kind create` appends a `kind-*` context; LKE's
kubeconfig you merge yourself (or set `KUBECONFIG=file1:file2` to layer several).
`KUBECONFIG` (colon-separated paths) overrides the default location.

---

## 3. Connect to a pod

```bash
kubectl get pods [-A] [-o wide]          # -A = all namespaces; -o wide adds node+IP
kubectl describe pod NAME                 # events are at the BOTTOM - read them
kubectl logs NAME [-c CONTAINER] [-f] [--previous]   # --previous = the crashed one
kubectl exec -it NAME -- sh               # shell inside the container
kubectl port-forward pod/NAME 8080:80     # your localhost:8080 -> pod's :80
kubectl cp NAME:/path ./local             # copy files out
kubectl debug -it NAME --image=busybox    # attach a debug container (distroless pods)
```

Mental model: a pod is one or more containers sharing a network namespace + IP.
`exec`/`logs` target a **container** (`-c` when there's more than one). Deeper:
`03-kubernetes/lab-08` (probes/lifecycle), `lab-10` (debug).

---

## 4. Connect one service to another (same vs different namespace)

Pods are mortal and their IPs churn. A **Service** is a stable name + virtual IP in
front of a set of pods, resolved by cluster DNS (CoreDNS).

```
Same namespace:        http://web
Different namespace:   http://web.other-ns.svc.cluster.local
Full form (always):    <service>.<namespace>.svc.cluster.local
```

```bash
kubectl get svc                          # the stable VIPs
kubectl get endpoints web                # the pod IPs currently behind 'web'
# Test resolution + reachability from inside the cluster:
kubectl run tmp --rm -it --image=curlimages/curl -- sh
#   curl http://web                            (same ns)
#   curl http://web.other-ns.svc.cluster.local (cross ns)
```

Key facts:
- Within a namespace, the short name works (`web`). Across namespaces you must
  qualify it with `.<namespace>` (or the full `.svc.cluster.local`).
- If `curl` fails, check `kubectl get endpoints SVC`. Empty endpoints mean the
  Service's `selector` matches no running pod, the most common cause. The Service
  can exist and still route nowhere.
- By default every pod can reach every pod (flat network). Restricting that is a
  **NetworkPolicy**. Deeper: `03-kubernetes/lab-04`.

---

## 5. Secrets (and ConfigMaps): store, list, consume

```bash
kubectl create secret generic db-creds \
  --from-literal=USER=postgres --from-literal=PASS=s3cret
kubectl get secrets                              # list (current ns)
kubectl get secret db-creds -o yaml              # values are base64, NOT encrypted
kubectl get secret db-creds -o jsonpath='{.data.PASS}' | base64 -d; echo   # decode one key
```

Consume two ways (a pod never reads a Secret by API; it's injected):

```yaml
# as env var (convenient; leaks into `ps` / child processes)
env:
  - name: DB_PASS
    valueFrom: { secretKeyRef: { name: db-creds, key: PASS } }
# as a mounted file (preferred for real secrets)
volumes:    [ { name: creds, secret: { secretName: db-creds } } ]
volumeMounts: [ { name: creds, mountPath: /etc/creds, readOnly: true } ]
```

**base64 is encoding, not encryption.** For anything real: etcd encryption at rest
+ an external manager (Vault, SOPS, cloud KMS). ConfigMaps are the identical shape
for *non*-secret config. Deeper: `03-kubernetes/lab-05`.

---

## 6. Expose a service: from in-cluster to the public internet

This is a ladder. Each rung widens the boundary:

| Rung | Reachable from | When |
|---|---|---|
| `ClusterIP` (default) | inside the cluster only | service-to-service |
| `kubectl port-forward svc/NAME 8080:80` | your laptop, temporarily | dev / debugging |
| `NodePort` | any node IP on a high port (30000–32767) | quick external, no LB |
| `LoadBalancer` | a real external IP | prod (on LKE → a **NodeBalancer**; no-op on kind) |
| **Ingress / Gateway API** | hostname + path routing, TLS | many services behind one entry, the Platform Track (`05-gateway-api/`) |

```bash
# The everyday one - pull any in-cluster service to your machine:
kubectl port-forward svc/web 8080:80        # localhost:8080 -> Service 'web' :80
# (Ctrl-C to stop; it's a foreground tunnel, not a permanent route.)
```

`port-forward` is the fast path to "is this thing even working?" without exposing
anything. On LKE, a `type: LoadBalancer` Service provisions an Akamai NodeBalancer
with a public IP; that's `09-lke-akamai/lab-02`.

---

## 7. The debug flow (memorize this order)

```
1. kubectl get pods                  is it even there? what phase?
2. kubectl describe pod NAME         events at the bottom tell you why
3. kubectl logs NAME --previous      what did the crashed container say?
4. kubectl get endpoints SVC         is the Service wired to pods?
5. kubectl run tmp --rm -it --image=curlimages/curl -- sh   reach it from inside?
6. kubectl auth can-i ...            is RBAC denying you?
```

Most "it doesn't work" is one of: pod not Running (→ describe/logs), Service
selector matches nothing (→ endpoints empty), or wrong namespace (→ `-A`).

---

## See also

- `reference/kubectl-cheatsheet.md`: every command, terse.
- `03-kubernetes/lab-01`: architecture, contexts, namespaces, k9s.
- `03-kubernetes/lab-04`: Services, DNS, NodePort, the network model.
- `03-kubernetes/lab-05`: ConfigMaps & Secrets in depth.
- `03-kubernetes/lab-10`: observability and debugging.
- `09-lke-akamai/`: taking all of this to a real Akamai LKE cluster.
