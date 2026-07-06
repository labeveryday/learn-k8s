# Lab 02: A pod factory with a narrow badge

**Goal:** deploy the manager on kind, where its create-a-cell call goes to the API
server instead of a Docker socket. You dissect the RBAC badge that authorizes it, test
the badge's edges with `kubectl auth can-i`, watch pods appear and get reaped, and then
revoke the badge to read a live 403 from the API server's side.

**Time:** ~45 min · **Cost:** free

## The problem (why this exists)

Lab 01 ended at the socket: the Docker-mode manager holds root on its host. Kubernetes
replaces that blank check with a scoped credential. The manager authenticates as a
ServiceAccount, and a Role spells out what that identity may do: five verbs, one
resource, one namespace. `kubectl` and the manager speak to the same API server with the
same protocol; the difference is the badge, and this lab is about reading badges.

## 1. Get the images onto the node

You built both images in lab 01. The compose file pinned the runtime's name
(`image: strands-sandbox-runtime:latest`) but let the manager default to compose's
`<project>-<service>` naming. Give the manager a plain name, then load both into kind
(Phase 12 lab-02 mechanics):

```bash
cd agent-sandbox
docker images | grep -E 'manager|runtime'                     # confirm the two names
docker tag server-manager:latest strands-sandbox-manager:latest
kind load docker-image strands-sandbox-manager:latest strands-sandbox-runtime:latest
```

## 2. Edit the manifest, and dodge the `:latest` trap

Open `k8s/manager.yaml`. Make three edits to the Deployment:

1. `image: YOUR_REGISTRY/strands-sandbox-manager:latest` becomes
   `image: strands-sandbox-manager:latest`
2. The `SANDBOX_IMAGE` env value becomes `strands-sandbox-runtime:latest`
3. Add `imagePullPolicy: IfNotPresent` next to the manager's `image:` line

The third edit is the trap worth remembering: for a `:latest` tag, Kubernetes defaults
`imagePullPolicy` to `Always`. Your image exists on the node and nowhere else, so the
default would try a registry pull, fail, and park the Pod in `ErrImagePull` even though
the bytes it needs are already local. The sandbox pods themselves are safe without an
edit: the backend sets their pull policy explicitly (read `SANDBOX_IMAGE_PULL_POLICY` in
`server/manager/app/backends/k8s_backend.py`).

## 3. Read the badge before you grant it

The first four objects in `manager.yaml` are the identity system in miniature:

| Object | Line that matters | What it means |
|---|---|---|
| `Namespace` | `strands-sandboxes` | the blast radius; everything lives here |
| `ServiceAccount` | `sandbox-manager` | who the manager *is* to the API server |
| `Role` | `resources: ["pods"]`, five verbs | what that identity may do, in this namespace only |
| `RoleBinding` | subject + roleRef | the grant that connects the two |

No `deployments`, no `secrets`, no `create` on anything but pods, and a `Role` rather
than a `ClusterRole`, so the grant stops at the namespace boundary. Apply it all, add
the token, and wait for ready:

```bash
kubectl apply -f k8s/manager.yaml
kubectl -n strands-sandboxes create secret generic sandbox-manager-secrets \
  --from-literal=MANAGER_TOKEN=lab-token
kubectl -n strands-sandboxes rollout status deploy/sandbox-manager
```

Now interrogate the badge. `kubectl auth can-i` answers as any identity you name:

```bash
SA=system:serviceaccount:strands-sandboxes:sandbox-manager
kubectl auth can-i create pods -n strands-sandboxes --as=$SA    # yes
kubectl auth can-i create pods -n default --as=$SA              # no
kubectl auth can-i create deployments -n strands-sandboxes --as=$SA   # no
kubectl auth can-i list secrets -n strands-sandboxes --as=$SA   # no
```

**What you should see:** one yes, three no. The manager can mint sandbox pods in its own
namespace and can do nothing else anywhere. Compare that with lab 01's socket: trading a
root-equivalent handle for a scoped badge is why the backend swap is worth doing.

## 4. Watch the factory work

Port-forward the manager and start a session while watching the namespace:

```bash
kubectl -n strands-sandboxes port-forward svc/sandbox-manager 8700:8700 &
kubectl -n strands-sandboxes get pods -w &

TOK="Authorization: Bearer lab-token"
SID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
```

**What you should see** in the watch: `sbx-<session>` appears, initializes, and goes
`Running 1/1`. Inspect what code built:

```bash
kubectl -n strands-sandboxes get pod sbx-$SID --show-labels
kubectl -n strands-sandboxes get pod sbx-$SID -o yaml | grep -E 'automountServiceAccountToken|restartPolicy|activeDeadlineSeconds'
```

Three reads, three lessons. The labels carry `app=strands-sandbox` and `egress=none`,
which is how lab 03's NetworkPolicies will select their targets: the manager labels
cells at birth, and policy attaches by label. `automountServiceAccountToken: false`
means code inside the cell finds no API credential to steal; the sandbox runs *in* the
cluster without being *of* it. And `restartPolicy: Never` plus `activeDeadlineSeconds`
makes every cell terminal by construction, with the deadline as the backstop if the
manager dies mid-session.

One more read, this time about the data plane. The manager's log line for your session:

```bash
kubectl -n strands-sandboxes logs deploy/sandbox-manager | grep "created pod"
# created pod sbx-... at 10.244.x.x (network=False)
```

No Service fronts the cell; the manager proxies straight to the pod IP. Pod IPs are
routable from anywhere in the cluster (Phase 03 lab-04), and a Service is a stable name
plus load balancing over interchangeable pods. A session pod is neither stable nor
interchangeable; it is a private, short-lived appliance, and the IP is exactly enough.

Prove the plumbing end to end, then destroy the cell:

```bash
curl -s -X POST localhost:8700/v1/sessions/$SID/execute -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"code": "1 + 1"}'

curl -s -X DELETE localhost:8700/v1/sessions/$SID -H "$TOK"
# in the watch terminal: sbx-<session> Terminating, then gone
```

## 5. Revoke the badge, read the refusal

Take away the grant while the manager runs, and ask for a new cell:

```bash
kubectl -n strands-sandboxes delete rolebinding sandbox-manager
curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}'
kubectl -n strands-sandboxes logs deploy/sandbox-manager --tail=5
```

**What you should see:** the API returns an error, and the manager's log carries the API
server's refusal: `pods is forbidden: User
"system:serviceaccount:strands-sandboxes:sandbox-manager" cannot create resource
"pods"`. Read that sentence as the system working. Authentication succeeded (the server
knows who asked), authorization failed (the badge no longer covers the verb), and the
error names identity, verb, and resource, which is everything you need to fix it:

```bash
kubectl apply -f k8s/manager.yaml     # recreates the RoleBinding
curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}'   # works again; delete it when done
```

## Checkpoint: you can now explain…

1. **What replaced the Docker socket, and why it is better.** A ServiceAccount whose
   Role grants five verbs on pods in one namespace. Compromise of the manager now yields
   the power to make sandboxes, not the power to own a host.
2. **Why session pods get no Service and no API token.** A Service abstracts over
   interchangeable pods; a session is a private appliance, reached by its IP. The token
   automount is off because the tenant's code must find nothing worth stealing.
3. **How to interrogate any identity's permissions.** `kubectl auth can-i <verb>
   <resource> -n <ns> --as=system:serviceaccount:<ns>:<name>`, and how to read a 403's
   three parts when you get one anyway.

You can now:
- [ ] Load locally built images into kind and sidestep the `:latest` pull-policy trap.
- [ ] Map each of the four RBAC objects in `manager.yaml` to its role in the grant.
- [ ] Watch the manager create, label, proxy to, and reap a session pod.

## Next

→ `lab-03-the-cell-walls.md`: the cells exist. Test every wall from inside, then catch
your cluster accepting NetworkPolicies it does not enforce.
