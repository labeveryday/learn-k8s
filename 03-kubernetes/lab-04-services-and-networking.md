# Lab 04: Services and Networking

**What you'll build:** a `Service` in front of the three `web` Pods from lab-03: a single
stable virtual IP and DNS name that survives Pods dying and being replaced. Then you'll prove
it works from inside the cluster, crack open the node to see the **iptables rules** kube-proxy
wrote to make it work, expose it on a port, and preview NetworkPolicy. The mechanism to grasp
is **indirection**: nothing upstream ever talks to a Pod IP, so Pods can churn freely.

> **The one idea:** a Service is a *stable name for a moving target*. Pods are cattle with
> disposable IPs; a Service is a fixed front door whose membership the system keeps current by
> **label selector**, the same label glue from lab-03, now wiring traffic instead of ownership.

## 1. The problem

Pods come and go; their IPs change. In lab-03 you killed a Pod and the ReplicaSet replaced it
with a brand-new Pod that had a brand-new IP. If anything had hard-coded that IP, it would now
be talking to nothing. You need a stable virtual IP + DNS name in front of a fleet of Pods.
That's a **Service**.

A Service never points at a Pod *by name or IP*. It declares a **selector** (`app: web`) and
the system continuously discovers which Pod IPs currently match; that live list is the Service's
**Endpoints**. Pod dies → Endpoints shrink. Replacement appears → Endpoints grow. The Service's
own IP and DNS name never move.

## 2. Service types

| Type | What it gives you |
|------|-------------------|
| `ClusterIP` (default) | virtual IP reachable *inside* the cluster |
| `NodePort` | ClusterIP + opens a port on every node |
| `LoadBalancer` | NodePort + cloud LB in front (no-op on kind) |
| `ExternalName` | DNS CNAME to an external host |
| *Headless* (`clusterIP: None`) | no VIP; DNS returns Pod IPs directly (StatefulSets) |

These stack: `NodePort` is a `ClusterIP` plus a node port, and `LoadBalancer` is a `NodePort`
plus a cloud load balancer. On kind there's no cloud, so `LoadBalancer` Services sit `<pending>`
forever, which is expected, not broken. You'll use `ClusterIP` for everything in-cluster and
reach it from your Mac with `port-forward`.

## 3. ClusterIP

A Service is your declarative request: *"give me one stable IP + name that load-balances across
whatever Pods currently match `app: web`."* The `web` Service ships as the second document inside
`manifests/deploy-web.yaml` (right after the Deployment from lab-03). Here is that Service exactly
as it's defined, then the fields that matter:

`manifests/deploy-web.yaml` (second document, after the Deployment):

```yaml
apiVersion: v1          # Services are core/v1, like Pods (Deployments were apps/v1)
kind: Service
metadata:
  name: web             # becomes the in-cluster DNS name: web.<namespace>.svc.cluster.local
spec:
  selector:
    app: web            # the LIVE membership filter - any Pod with this label becomes an Endpoint
  ports:
    - port: 80            # service port - what clients hit on the ClusterIP (web:80)
      targetPort: 80      # container port - where the request lands inside the Pod
```

Two things beginners get wrong here, and both fail *silently*:

- **`selector` is not the same kind of selector as a Deployment's.** A Deployment's
  `matchLabels` declares *ownership* and is immutable; a Service's `selector` is a *live query*
  and is fully editable. Change it and the Endpoints recompute on the next loop, which is
  practice #3 below. If the selector matches **zero** Pods, the Service still exists and still
  has a ClusterIP, but Endpoints is empty and every request times out. No error is thrown.
- **`port` vs `targetPort` are different roles.** `port` is the front door clients dial on the
  ClusterIP; `targetPort` is the door on the Pod. They're equal here (80→80) which hides the
  distinction. When an app listens on, say, 8000 while you expose it as 80, getting these
  backwards means the Service answers on the right IP and forwards to a port nothing is listening on.

Apply it and inspect what the system built:

```bash
kubectl apply -f manifests/deploy-web.yaml   # the Service is the 2nd doc; re-applying the Deployment is idempotent
kubectl get svc
kubectl describe svc web           # shows Endpoints = pod IPs
kubectl get endpoints web
```

- `describe svc web` prints the ClusterIP, the port mapping, and the `Endpoints:` line: the
  actual Pod IPs the selector resolved to *right now*.
- `get endpoints web` is the same membership list as a standalone object: this is the thing
  kube-proxy watches, and the thing that changes when a Pod dies (practice #2).

**What you should see:** a `web` Service with `TYPE ClusterIP` and a `CLUSTER-IP` like
`10.96.x.x`, and `Endpoints` listing **three** Pod IPs ending in `:80` (one per `web` Pod from
lab-03). Three Endpoints means the selector found all three Pods: the indirection
working, where you addressed a *label* and the system filled in the *IPs*.

Test from inside the cluster:

```bash
kubectl run tmp --rm -it --image=curlimages/curl:latest -- sh
# inside:
curl http://web             # Service DNS: <name> within same namespace
curl http://web.default.svc.cluster.local
```

- `kubectl run tmp --rm -it` launches a throwaway Pod and drops you into its shell; `--rm`
  deletes it the moment you exit, so it leaves nothing behind. You need a Pod *inside* the
  cluster because a ClusterIP is only routable from inside the cluster network.
- `curl http://web` works because CoreDNS gives every Service a short name resolvable from Pods
  in the **same namespace**; you don't need the IP at all.

**What you should see:** the nginx welcome HTML from both curls. Run the first `curl` a few
times: kube-proxy spreads requests across the three Endpoints, so you're load-balancing without
configuring anything. The full name is `<svc>.<namespace>.svc.cluster.local`. CoreDNS resolves it.

## 4. How it works

`kube-proxy` watches Services and Endpoints from the apiserver. On each node, it programs iptables (or IPVS, an alternative kernel load-balancing mechanism; ignore which one for now) rules: "packets to ClusterIP:port → DNAT to one of these pod IPs." DNAT = destination NAT, rewriting the destination IP, the same iptables `nat` table you saw in Docker lab-04. There's no daemon proxying packets; it's kernel netfilter (the in-kernel packet-filtering framework) all the way.

That's the trick: the ClusterIP `10.96.x.x` is **not a real interface anyone owns**. No
process is listening on it. It exists only as a target in iptables rules that rewrite the packet's
destination to a real Pod IP before it leaves the kernel. Kill kube-proxy and existing rules keep
working; what stops is *updating* them when Endpoints change.

```bash
# On a kind node (kind names the node container <cluster>-control-plane;
# run `docker ps` to confirm yours):
docker exec -it learn-control-plane iptables -t nat -L KUBE-SERVICES -n | head
```

- `docker exec -it learn-control-plane ...` runs a command *inside the node container*. Remember
  from lab-01 that the kind "node" is itself a Docker container named `learn-control-plane`. The
  iptables rules live in the node's kernel namespace, not on your Mac.
- `iptables -t nat -L KUBE-SERVICES -n` lists the `KUBE-SERVICES` chain in the `nat` table; `-n`
  skips slow DNS lookups so IPs print as numbers. `KUBE-SERVICES` is the entry point kube-proxy
  installs; every Service flows through it.

**What you should see:** `KUBE-SVC-*` / `KUBE-SEP-*` chains, one generated per Service and per endpoint Pod. You don't read these by hand; confirm kube-proxy wrote them. (`SVC` = the per-Service chain that picks an endpoint; `SEP` = the per-endpoint chain that does the DNAT to one Pod IP. Three `web` Pods → three `KUBE-SEP-*` chains.)

## 5. NodePort

A `ClusterIP` is invisible outside the cluster. `NodePort` keeps everything ClusterIP does and
*also* opens a fixed port on every node, so traffic to `<nodeIP>:<nodePort>` reaches the Service.

Add `type: NodePort` (K8s picks a port in 30000–32767):

```yaml
spec:
  type: NodePort
  selector: { app: web }
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080      # the fixed port opened on EVERY node; omit it and K8s auto-assigns one
```

- `nodePort: 30080` pins the external port instead of letting K8s pick a random one in
  30000–32767. It must fall inside that range, and it's opened on **every** node: even nodes
  not running a `web` Pod will accept the packet and forward it internally.
- `selector` and `port`/`targetPort` mean exactly what they did in section 3; `NodePort` only
  *adds* the node-port layer on top of the ClusterIP behavior.

With kind, to reach it from your Mac you either port-forward or recreate the cluster with `extraPortMappings`.

The catch on kind: the "node" is a Docker container, so `<nodeIP>:30080` isn't directly reachable
from your Mac unless that port was published when the cluster was created (`extraPortMappings`,
which lab-07 sets up for Ingress). For now, the path that always works is `port-forward`.

Easiest:

```bash
kubectl port-forward svc/web 8080:80
curl http://localhost:8080
```

- `port-forward svc/web 8080:80` opens a tunnel from your Mac's `localhost:8080` straight to the
  Service's port 80, bypassing NodePort entirely. It works for any Service type (even plain
  ClusterIP) because the tunnel runs through the apiserver, not the node network, which is why
  it's the go-to for poking at in-cluster Services during development.

**What you should see:** `port-forward` prints `Forwarding from 127.0.0.1:8080 -> 80` and stays
running (leave it open in one terminal); the `curl` in another terminal returns the nginx welcome
page. Ctrl-C the forward to close the tunnel.

## 6. The cluster network model

> Every Pod gets an IP. Every Pod can reach every other Pod directly. No NAT inside the cluster.

This is a *requirement* of K8s, implemented by a **CNI plugin** (kindnet, Calico, Cilium, etc.). The flat-network assumption is why Services are simple: DNAT to a Pod IP that's already routable.

This is the foundation section 4 stood on: kube-proxy can DNAT a packet to *any* Pod IP and trust
the kernel to route it, because every Pod IP is already reachable from every node with no NAT in
between. The Service layer adds the *stable name*; the CNI provides the *flat network* underneath.

## 7. NetworkPolicies (preview)

By default, **all Pods can talk to all Pods.** Zero-trust requires NetworkPolicies:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: api-only-from-web
spec:
  podSelector:
    matchLabels: { app: api }      # WHICH Pods this policy protects (the targets)
  policyTypes: [Ingress]           # this policy governs INBOUND traffic to those Pods
  ingress:
    - from:
        - podSelector: { matchLabels: { app: web } }   # the ONLY senders allowed in
      ports:
        - port: 8000               # ...and only on this port
```

- `podSelector` here selects the Pods the policy *applies to*, the opposite direction from a
  Service selector. The policy guards `app: api` Pods; it doesn't send traffic anywhere.
- The gotcha: the moment **any** Ingress NetworkPolicy selects a Pod, that Pod flips from
  "allow all inbound" to "**deny all inbound except what's explicitly listed**." So this single
  rule means `api` Pods now accept traffic *only* from `app: web` Pods on port 8000; everything
  else is dropped. Empty `ingress` would mean deny-all.

Note: kindnet doesn't enforce NetworkPolicy. For hands-on NP, install Calico in kind. Skip for now; internalize the concept.

(That's why this is a *preview*: on kindnet the rule above is accepted by the apiserver and then
ignored, because the CNI has to enforce it. The concept is what carries forward.)

## 8. Practice

1. Apply the Service for your `web` Deployment. Hit it from a throwaway curl pod by DNS name.
2. Delete one of the `web` pods. Watch `kubectl get endpoints web` update in real time.
3. Change the selector to a label no pod has. Observe: Service exists, Endpoints empty, requests fail.
4. Create a second Deployment `web-v2` with label `app: web version=v2`. Make a Service that only targets v2.

## Next

→ `lab-05-config-and-secrets.md`: your Pods have hard-coded config baked into the image. A
**ConfigMap** and **Secret** pull that out, so the same image runs in dev and prod by swapping
what's injected instead of rebuilding.
