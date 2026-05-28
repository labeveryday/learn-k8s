# Lab 01 — Architecture and your first cluster (kind)

## 1. Spin it up

`kind` = Kubernetes IN Docker. Each "node" is a Docker container running K8s components.

```bash
kind create cluster --name learn --image kindest/node:v1.30.0
kubectl cluster-info
kubectl get nodes
kubectl get pods -A                 # all namespaces
```

You should see ~1 node. In `kube-system` you'll see pods for `etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `coredns`, `kindnet` (CNI).

## 2. Map the diagram to real pods

```bash
kubectl get pods -n kube-system -o wide
kubectl describe pod -n kube-system -l component=kube-apiserver | head -40
kubectl logs -n kube-system -l component=kube-apiserver --tail=20
```

Each control-plane component you read about in the README is a pod you can inspect.

## 3. The API itself

```bash
kubectl api-resources              # every kind in this cluster
kubectl api-versions               # API group versions
kubectl explain pod                # docs
kubectl explain pod.spec.containers
```

`explain` is your offline textbook.

Raw API:

```bash
kubectl get --raw /api/v1/namespaces | jq .
kubectl -v=8 get pods 2>&1 | head -40    # see the raw HTTPS calls
```

## 4. Namespaces

A namespace is a scoping boundary for names and RBAC.

```bash
kubectl get ns
kubectl create namespace demo
kubectl -n demo get all
kubectl config set-context --current --namespace=demo    # default ns for this context
```

Convention: one namespace per environment/team/app.

## 5. Contexts

Your `~/.kube/config` holds *contexts* (cluster + user + namespace). Switch with:

```bash
kubectl config get-contexts
kubectl config use-context kind-learn
```

Install `kubectx`/`kubens` if you want fast switching.

## 6. k9s: your TUI dashboard

```bash
k9s
# ':pods' to view pods, ':ns' for namespaces, 'l' for logs, 'd' for describe
# '?' for help
```

Extremely useful for learning. Use it alongside raw `kubectl` — never as a replacement.

## 7. Teardown

Keep this cluster running through Phase 3. When done:

```bash
kind delete cluster --name learn
```

## 8. Practice

1. Which process on your Mac is the apiserver? (Hint: it's inside a container inside a Docker VM.) Run `docker ps | grep control-plane` and then `docker exec -it <kind-container> bash` → `ps -ef | grep kube-apiserver`.
2. `kubectl explain deployment.spec.strategy` — what are the two types?
3. `kubectl get events -A --sort-by=.lastTimestamp` — events are how K8s narrates itself.
