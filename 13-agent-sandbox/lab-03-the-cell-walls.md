# Lab 03: The cell walls, and the CNI that ignores them

**Goal:** read the hardened Pod spec the manager writes in code, test each wall from
inside a live cell, and then confront the phase's sharpest lesson: on default kind, the
NetworkPolicies that promise no-egress are accepted and ignored. You prove the gap, then
switch to a Calico cluster and prove enforcement.

**Time:** ~50 min · **Cost:** free

## The problem (why this exists)

The sandbox's threat model is hostile code with intent. That standard makes every
securityContext field earn its place, and it makes the egress rules load-bearing rather
than decorative. Most Kubernetes tutorials write a NetworkPolicy, see no error, and
assume it works. For a workload whose entire job is containing untrusted code, "assume it
works" is how the metadata endpoint gets read. This lab replaces the assumption with a
test.

## 1. Read the spec code writes

Open `server/manager/app/backends/k8s_backend.py` and find the `V1SecurityContext` and
the `V1PodSpec`. Every field is a wall, and each maps to a Phase 01 primitive:

| Field in the code | The wall | Phase 01 primitive |
|---|---|---|
| `capabilities=V1Capabilities(drop=["ALL"])` | no raw sockets, no mount, no ptrace | Linux capabilities |
| `read_only_root_filesystem=True` | code cannot rewrite its own binaries | mount namespace, ro bind |
| `run_as_non_root=True, run_as_user=1000` | not uid 0, even if the image forgets | user namespace / uid |
| `allow_privilege_escalation=False` | no setuid path back up to root | `no_new_privs` bit |
| `seccomp_profile=type=RuntimeDefault` | the dangerous syscalls are unavailable | seccomp-bpf |
| `automount_service_account_token=False` (PodSpec) | no cluster credential to steal | the token that is not mounted |
| `active_deadline_seconds` + `restart_policy=Never` | the cell is terminal, with a hard clock | none; a control-plane guarantee |

The emptyDir volumes for `/workspace` and `/tmp` carry `size_limit`, so a fork bomb of
files fills a cap, not the node. Defense in layers: any one wall can have a bug, and the
attacker still faces the rest.

## 2. Test the walls from inside a cell

Bring up the manager from lab 02 if it is down, start a session, and get a shell into the
cell with kubectl:

```bash
kubectl -n strands-sandboxes port-forward svc/sandbox-manager 8700:8700 &
TOK="Authorization: Bearer lab-token"
SID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
kubectl -n strands-sandboxes exec -it sbx-$SID -- sh
```

Inside the cell, try to breach each wall (each line should fail):

```sh
id                                   # uid=1000, not root
touch /etc/breach                    # read-only rootfs: Permission denied
cat /var/run/secrets/kubernetes.io/serviceaccount/token   # No such file: token not mounted
python3 -c "import os; os.setuid(0)"  # not permitted: no privilege escalation
touch /workspace/ok && echo "workspace writable, as designed"
exit
```

**What you should see:** four refusals and one success. The refusals are the walls
holding; the success is the one door the tenant is meant to have. Nothing here needed a
policy engine, because these walls are the kernel and the kubelet enforcing the Pod spec.

## 3. Apply the egress policy, then watch it do nothing

Network isolation is different: the Pod spec cannot express "no egress," so it lives in
NetworkPolicy objects, and a NetworkPolicy is only a request until a CNI enforces it.
Default kind runs kindnet, which does not. Apply the policies and watch the promise fail.

```bash
kubectl apply -f agent-sandbox/k8s/network-policies.yaml
kubectl -n strands-sandboxes get networkpolicy
```

Three policies now exist: deny all egress, allow DNS, allow internet only for pods
labeled `egress=allowed`. Your default session has `egress=none`, so the internet call
must fail. Test it:

```bash
kubectl -n strands-sandboxes exec sbx-$SID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5); print('REACHED THE INTERNET')"
```

**What you should see, and it is the lesson:** `REACHED THE INTERNET`. The policy says
deny; the pod reached the internet anyway; no error appeared at apply time. On kindnet,
NetworkPolicy objects are stored and never enforced. A workload built to contain hostile
code just let it phone out, and nothing in `kubectl get` would have told you. This is why
the README lists "requires a CNI that enforces NetworkPolicy" as a hard prerequisite, not
a footnote.

## 4. Prove enforcement on Calico

Tear the cell down and stand up a second kind cluster that has a real policy engine. The
config ships without a default CNI so Calico owns networking:

```bash
curl -s -X DELETE localhost:8700/v1/sessions/$SID -H "$TOK"

kind create cluster --config 13-agent-sandbox/manifests/kind-calico.yaml
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml
kubectl -n kube-system rollout status ds/calico-node --timeout=180s
```

Redeploy the manager stack onto this cluster (load the images first, same as lab 02),
apply the policies, and repeat the exact test from step 3:

```bash
kind load docker-image strands-sandbox-manager:latest strands-sandbox-runtime:latest --name sandbox-net
kubectl apply -f agent-sandbox/k8s/manager.yaml
kubectl -n strands-sandboxes create secret generic sandbox-manager-secrets --from-literal=MANAGER_TOKEN=lab-token
kubectl -n strands-sandboxes rollout status deploy/sandbox-manager
kubectl apply -f agent-sandbox/k8s/network-policies.yaml

kubectl -n strands-sandboxes port-forward svc/sandbox-manager 8700:8700 &
SID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
kubectl -n strands-sandboxes exec sbx-$SID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5); print('REACHED')" \
  || echo "EGRESS BLOCKED (the policy holds)"
```

**What you should see now:** the call times out and prints `EGRESS BLOCKED`. Same YAML,
same pod labels, different CNI, opposite outcome. The policy did not change between step 3
and here; the enforcer did. Confirm DNS still resolves (the allow-DNS policy) so you know
you blocked egress, not everything:

```bash
kubectl -n strands-sandboxes exec sbx-$SID -- python3 -c "import socket; print(socket.gethostbyname('kubernetes.default'))"
```

## 5. The RFC1918 subtlety

Read the `sandbox-allow-internet` policy in `network-policies.yaml`. Even the sessions
that *are* allowed egress cannot reach private ranges:

```yaml
    - ipBlock:
        cidr: 0.0.0.0/0
        except:
          - 10.0.0.0/8
          - 172.16.0.0/12
          - 192.168.0.0/16
          - 169.254.169.254/32
```

The first three exceptions wall the tenant off from the cluster's own network and your
VPC; the fourth is the cloud metadata endpoint, the single most valuable target for code
that wants credentials it was never given. "Allow the internet" here means the public
internet only, and the exceptions are the difference between an egress hole and a pivot
into your infrastructure. Start a `network=True` session and confirm the public path
opens while the metadata address stays shut:

```bash
NSID=$(curl -s -X POST localhost:8700/v1/sessions -H "$TOK" \
  -H 'Content-Type: application/json' -d '{"network": true}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
kubectl -n strands-sandboxes exec sbx-$NSID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5); print('PUBLIC OK')"
kubectl -n strands-sandboxes exec sbx-$NSID -- \
  python3 -c "import urllib.request; urllib.request.urlopen('http://169.254.169.254/', timeout=4)" \
  || echo "METADATA BLOCKED (as it must be)"
```

Clean up when done: `curl -s -X DELETE localhost:8700/v1/sessions/$NSID -H "$TOK"`.

## Checkpoint: you can now explain…

1. **Why each securityContext field is present, in threat terms.** Dropped caps,
   read-only rootfs, non-root, no-escalation, seccomp, no token: each closes one path a
   hostile tenant would take, and layering them means one bug is not a breach.
2. **The difference between a policy and its enforcement.** A NetworkPolicy is a contract
   the API server stores; a CNI is the party that honors it. kindnet stores and ignores;
   Calico enforces. Test, never assume.
3. **What "allow the internet" must still forbid.** RFC1918 ranges and the metadata
   endpoint, or the egress hole becomes a route into your cluster and your credentials.

You can now:
- [ ] Map each hardening field in the backend code to the wall it builds and the Phase 01 primitive beneath it.
- [ ] Demonstrate a NetworkPolicy being ignored on kindnet and enforced on Calico with identical YAML.
- [ ] Explain the four `except` entries in the internet-egress policy.

## Next

→ `lab-04-delegation-and-the-precise-hole.md`: give the worker agent inside the cell a
reason to reach the network, then open exactly one workload-wide hole to your vLLM,
selector-based, not an IP range.
