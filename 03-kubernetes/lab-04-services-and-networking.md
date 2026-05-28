# Lab 04 — Services and Networking

## 1. The problem

Pods come and go; their IPs change. You need a stable virtual IP + DNS name in front of a fleet of Pods. That's a **Service**.

## 2. Service types

| Type | What it gives you |
|------|-------------------|
| `ClusterIP` (default) | virtual IP reachable *inside* the cluster |
| `NodePort` | ClusterIP + opens a port on every node |
| `LoadBalancer` | NodePort + cloud LB in front (no-op on kind) |
| `ExternalName` | DNS CNAME to an external host |
| *Headless* (`clusterIP: None`) | no VIP; DNS returns Pod IPs directly (StatefulSets) |

## 3. ClusterIP

`manifests/svc-web.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector:
    app: web
  ports:
    - port: 80            # service port
      targetPort: 80      # container port
```

```bash
kubectl apply -f manifests/svc-web.yaml
kubectl get svc
kubectl describe svc web           # shows Endpoints = pod IPs
kubectl get endpoints web
```

Test from inside the cluster:

```bash
kubectl run tmp --rm -it --image=curlimages/curl:latest -- sh
# inside:
curl http://web             # Service DNS: <name> within same namespace
curl http://web.default.svc.cluster.local
```

The full name is `<svc>.<namespace>.svc.cluster.local`. CoreDNS resolves it.

## 4. How it actually works (MIT hat on)

`kube-proxy` watches Services and Endpoints from the apiserver. On each node, it programs iptables (or IPVS) rules: "packets to ClusterIP:port → DNAT to one of these pod IPs." There's no daemon proxying packets; it's kernel netfilter all the way.

```bash
# On a kind node:
docker exec -it learn-control-plane iptables -t nat -L KUBE-SERVICES -n | head
```

## 5. NodePort

Add `type: NodePort` (K8s picks a port in 30000–32767):

```yaml
spec:
  type: NodePort
  selector: { app: web }
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080
```

With kind, to reach it from your Mac you either port-forward or recreate the cluster with `extraPortMappings`.

Easiest:

```bash
kubectl port-forward svc/web 8080:80
curl http://localhost:8080
```

## 6. The cluster network model

> Every Pod gets an IP. Every Pod can reach every other Pod directly. No NAT inside the cluster.

This is a *requirement* of K8s, implemented by a **CNI plugin** (kindnet, Calico, Cilium, etc.). The flat-network assumption is why Services are simple: just DNAT to a Pod IP that's already routable.

## 7. NetworkPolicies (preview)

By default, **all Pods can talk to all Pods.** Zero-trust requires NetworkPolicies:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: api-only-from-web
spec:
  podSelector:
    matchLabels: { app: api }
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector: { matchLabels: { app: web } }
      ports:
        - port: 8000
```

Note: kindnet doesn't enforce NetworkPolicy. For hands-on NP, install Calico in kind. Skip for now; internalize the concept.

## 8. Practice

1. Apply the Service for your `web` Deployment. Hit it from a throwaway curl pod by DNS name.
2. Delete one of the `web` pods. Watch `kubectl get endpoints web` update in real time.
3. Change the selector to a label no pod has. Observe: Service exists, Endpoints empty, requests fail.
4. Create a second Deployment `web-v2` with label `app: web version=v2`. Make a Service that only targets v2.
