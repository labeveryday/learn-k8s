# Lab 04: Identity, state, and rollouts: operating a singleton honestly

**Goal:** discover, by breaking it, that this workload has an identity the cluster does
not know about. Scale it to two and watch the split. Roll it with the wrong strategy and
watch the overlap. Then make the two invariants explicit (`replicas: 1`, `Recreate`),
move the ledger to a volume that outlives the Pod, and write down what you now own that
kagent would have absorbed.

**Time:** ~40 min · **Cost:** free

## The problem (why this exists)

Almost every Deployment you have written treats Pods as cattle: interchangeable,
scalable, safe to overlap during a rollout. The review agent violates the assumption
twice. It has an external identity (one Discord bot; two copies of it answer every
message twice) and it has private state (each Pod's ledger lives in that Pod's emptyDir).
Kubernetes cannot infer either fact. The spec fields that encode them, `replicas` and
`strategy`, are already in `deployment.yaml`; this lab makes you earn them by running
the workload without them.

## 1. Scale to two, and find the split brain

```bash
kubectl scale deploy/review-agent --replicas=2
kubectl get pods -l app=review-agent    # two Pods, each ran its own init clone
```

Each Pod now holds its own copy of the repo and its own ledger. Trigger two reviews
through the Service, then interrogate `status` a few times:

```bash
kubectl port-forward svc/review-agent 8080:8080 &
curl -s -X POST localhost:8080/review > /dev/null
curl -s -X POST localhost:8080/review > /dev/null
for i in 1 2 3 4; do curl -s localhost:8080/status | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["reviews_run"], d["last_review_at"])'; done
```

**What you should see:** the answers disagree. A port-forward pins you to one Pod, so
kill and restart it between calls (or `curl` from a Pod in the cluster through the
Service name) and `reviews_run` flips between counts depending on which Pod answers.
Two agents each did some of the work, each recorded its own share, and no endpoint can
tell you the true total. With Discord configured the failure needs no instrumentation:
say something in the channel and both Pods reply.

The application-level lock from lab 01 serialized reviews inside one process. Replicas
put a second process next to it, and the lock covers nothing across the boundary. Scale
was the bug:

```bash
kubectl scale deploy/review-agent --replicas=1
```

Which assumption failed? Services assume any endpoint is as good
as any other. That holds when state lives behind the workload (vLLM's model weights are
identical replicas) and breaks when state lives inside it. The fix is architectural,
either externalize the state or refuse to scale, and `replicas: 1` is the honest version
of the second choice.

## 2. Roll it with the wrong strategy, then read why Recreate is in the spec

`deployment.yaml` declares `strategy: Recreate`. Replace it to see what the default
would do. Edit the file: change `type: Recreate` to `type: RollingUpdate`, apply, and
roll:

```bash
kubectl apply -f manifests/deployment.yaml
kubectl rollout restart deploy/review-agent
kubectl get pods -l app=review-agent -w
```

**What you should see:** the new Pod reaches `READY 1/1` while the old one still runs.
For that window two agents exist, which is Section 1's bug reintroduced on every deploy,
briefly and on a schedule. RollingUpdate's zero-downtime overlap is a feature for
stateless replicas and a defect for identities. With Discord on, the window is loud: the
new Pod's greeting posts before the old Pod disconnects.

Restore `type: Recreate`, apply, and roll again. The order inverts: the old Pod
terminates first, the cluster holds a gap with zero Pods, and the new one starts clean.
You traded a few seconds of downtime for the guarantee that at most one identity exists.
For a review agent that trade is obvious; the point of making it explicitly is that
`strategy` is where the trade lives, and the default answers the question wrong for this
class of workload.

## 3. The ledger deserves better than an emptyDir

Everything the agent has ever concluded lives in `NEW_FEEDBACK.md`, inside the Pod's
emptyDir, one `kubectl delete pod` from oblivion. Prove the loss first:

```bash
curl -s -X POST localhost:8080/review > /dev/null
kubectl exec deploy/review-agent -- sh -c 'grep -c "^## \[F-" /work/repo/NEW_FEEDBACK.md'
kubectl delete pod -l app=review-agent
# wait for READY 1/1, then:
kubectl exec deploy/review-agent -- sh -c 'grep -c "^## \[F-" /work/repo/NEW_FEEDBACK.md || echo gone'
```

The clone is disposable by design; the ledger is not, and the fix is to separate their
fates. `tools.py` already reads `FEEDBACK_FILE` from the environment for exactly this
moment. Claim a volume and point the ledger at it:

```bash
kubectl apply -f manifests/pvc.yaml
```

In `deployment.yaml`, add the claim to `volumes`, mount it, and set the env var (three
additions, keep the rest):

```yaml
      containers:
        - name: agent
          env:
            - name: FEEDBACK_FILE
              value: /work/state/NEW_FEEDBACK.md
          volumeMounts:
            - name: work
              mountPath: /work
            - name: state
              mountPath: /work/state
      volumes:
        - name: work
          emptyDir: {}
        - name: state
          persistentVolumeClaim:
            claimName: review-agent-state
```

Apply, run a review, count the entries, delete the Pod, count again:

```bash
kubectl apply -f manifests/deployment.yaml
kubectl rollout status deploy/review-agent
curl -s -X POST localhost:8080/review > /dev/null
kubectl exec deploy/review-agent -- grep -c "^## \[F-" /work/state/NEW_FEEDBACK.md
kubectl delete pod -l app=review-agent
kubectl rollout status deploy/review-agent
kubectl exec deploy/review-agent -- grep -c "^## \[F-" /work/state/NEW_FEEDBACK.md
```

Same count on both sides of the delete. Note how the pieces cohere rather than merely
coexist: the PVC is ReadWriteOnce, single-node by contract, which only works because
`replicas: 1`; Recreate guarantees the old Pod releases the mount before the new one
claims it. Three spec fields, one invariant, stated three ways.

## 4. The ledger of what you own (name the costs)

You have now hand-built, for one agent: a health adapter, an image, a config split, a
clone sequence, two probes, a singleton constraint, a rollout strategy, and durable
state. Phase 07's kagent absorbed the analogous work behind three CRDs, and Phase 07
lab-04 told you doing it by hand once is the best argument for the controller. Having
done it, complete the argument in your notes with the honest column too, what by-hand
bought you: any language, any framework, any interface (kagent has no opinion on Flask
or Discord), and no controller version to chase.

Two pointers for taking this real:

- **A real cluster (Phase 09):** the manifests move to LKE unchanged except the image
  line, which needs a registry the nodes can pull from. The PVC binds to a Block Storage
  volume instead of kind's local-path provisioner; same claim, different class.
- **A real fleet:** the moment you want this agent reviewing five repos, the design
  question reopens. Five Deployments from one template is honest and dumb; a CRD plus a
  controller reconciling `ReviewAgent` objects is kagent's move, and now you know the
  price of both answers.

## Checkpoint: you can now explain…

1. **Why `replicas: 1` is a design decision and not a limitation.** The agent's identity
   and private state make endpoints unequal; the Service abstraction assumes they are
   equal; refusing to scale is the honest reconciliation.
2. **When RollingUpdate is wrong.** Its overlap window runs two identities on every
   deploy. `strategy: Recreate` trades seconds of downtime for the singleton guarantee.
3. **Which data deserved a PVC, and how three fields state one invariant.** The
   regenerable clone stays in emptyDir; the irreplaceable ledger moves to a
   ReadWriteOnce claim that only coheres because replicas and strategy already enforce
   one-at-a-time.

You can now:
- [ ] Demonstrate split brain with two replicas and explain it in terms of Service assumptions.
- [ ] Choose and defend a rollout strategy per workload, not per habit.
- [ ] Separate disposable from durable state and prove the ledger survives a Pod delete.

## What you proved across Phase 12

You took an agent from a process on your laptop to a governed workload on your cluster:
probed, non-root, declaratively configured, sequenced by an initContainer, constrained to
one identity, rolled without overlap, and remembering what it learned across restarts.
Phase 07 showed you agents as objects a controller manages; this phase showed you the
manual transmission underneath. You can now drive both, and you know when each is the
right car.
