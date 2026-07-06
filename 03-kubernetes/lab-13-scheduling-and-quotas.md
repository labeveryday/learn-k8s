# Lab 13: Scheduling and quotas

**What you'll build:** a throwaway 3-node kind cluster next to your `learn` cluster, and on it
every tool that decides where a Pod lands: node labels with `nodeSelector`, **nodeAffinity**
(required and preferred), **podAntiAffinity** to keep replicas apart, and
**topologySpreadConstraints** to spread them evenly. You'll taint a node and watch Pods route
around it. Then you switch from "where" to "whether": a **ResourceQuota** that caps a namespace,
a Deployment that runs into the cap (and reports the failure somewhere you might not think to
look), and a **LimitRange** that injects the requests the quota demands. At the end the
throwaway cluster is deleted and your course cluster is untouched.

> **The one idea:** placement is a negotiation with two sides. The Pod declares what it needs
> (selectors, affinity rules, tolerations) and the node declares what it accepts (labels,
> taints). The scheduler filters out every node that fails a hard requirement, scores the
> survivors, and binds the Pod to the winner. ResourceQuota and LimitRange act earlier, at
> admission: they decide whether the Pod object gets created at all.

## 1. How the scheduler picks a node

Every Pod you've created since lab-02 was placed by the kube-scheduler, one of the four
control-plane Pods from lab-01. It watches for Pods whose `spec.nodeName` is empty and runs a
two-step for each:

1. **Filter.** Remove every node the Pod cannot run on: not enough unreserved CPU or memory for
   the Pod's requests (lab-03), labels that don't match its `nodeSelector` or required affinity,
   taints it doesn't tolerate. What survives is the set of feasible nodes.
2. **Score.** Rank the feasible nodes: spread Pods from the same workload, favor less-allocated
   nodes, add weight for preferred affinities. Highest score wins, and the scheduler writes
   that node's name into `spec.nodeName`.

Every mechanism in this lab plugs into one of those two steps. If filtering leaves zero nodes,
the Pod stays `Pending` and the scheduler writes a `FailedScheduling` event listing exactly
which requirement eliminated which nodes. You'll read several of those today; on a real cluster
they're how you debug most placement problems.

You've met pieces of this before in GPU contexts: `04-vllm/lab-04` pairs a toleration (to get
past a GPU node's taint) with a `nodeSelector` (to steer onto it), and `09-lke-akamai/lab-03`
shows a managed cluster where the GPU resource request alone does the placing. This lab covers
the general machinery those were special cases of.

## 2. A cluster with somewhere to go

Your `learn` cluster has one node, so every scheduling decision so far has had exactly one
possible answer. Placement only becomes visible with choices, so build a second, throwaway
cluster next to it. Save this as `kind-sched.yaml`:

```yaml
kind: Cluster                        # kind's own config format, not a Kubernetes object
apiVersion: kind.x-k8s.io/v1alpha4
nodes:                               # one entry = one Docker container = one node
  - role: control-plane
  - role: worker
  - role: worker
```

Two things to know about this file. `kind: Cluster` with `apiVersion: kind.x-k8s.io/v1alpha4`
marks it as kind's cluster config, which you feed to the CLI rather than to an apiserver. The
`nodes` list declares the shape: each entry becomes one Docker container running the node
components, with `role` deciding whether it runs the control plane or only the kubelet,
kube-proxy, and a container runtime.

```bash
kind create cluster --name sched --config kind-sched.yaml --image kindest/node:v1.30.0
kubectl config get-contexts     # kind-learn AND kind-sched; * marks the new one
kubectl get nodes
```

`kind create cluster` switches your current context to the new cluster for you, the same
kubeconfig mechanics you learned in lab-01 section 5. Your `learn` cluster keeps running; you
now have two REST front doors and the context decides which one `kubectl` talks to.

> **What you should see:** three nodes: `sched-control-plane`, `sched-worker`, `sched-worker2`,
> all `Ready`. Check the control-plane node's taints:
>
> ```bash
> kubectl describe node sched-control-plane | grep -A 2 Taints
> ```
>
> `node-role.kubernetes.io/control-plane:NoSchedule`. Lab-12 told you kind strips this taint on
> single-node clusters because otherwise nothing could run; on a multi-node cluster it stays.
> So this cluster has three nodes but only two that accept ordinary Pods. Keep that in your
> head: it explains two surprises later in this lab.

## 3. Node labels: nodeSelector and nodeAffinity

The scheduler matches Pods to nodes through labels, the same label/selector indirection you've
used since lab-03, pointed at nodes instead of Pods. Give one worker a label and pin a Pod to
it:

```bash
kubectl label node sched-worker disk=ssd
kubectl get nodes -L disk           # -L adds a column for the label's value
```

Save as `pod-pinned.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: pinned
spec:
  nodeSelector:            # filter step: only nodes with ALL these labels survive
    disk: ssd
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
```

```bash
kubectl apply -f pod-pinned.yaml
kubectl get pod pinned -o wide
```

> **What you should see:** the NODE column reads `sched-worker`. Run it a few times if you
> like; with the label on one node there is nothing to choose between.

`nodeSelector` is exact-match only: every listed label must be present with that exact value.
**nodeAffinity** is its expressive replacement. Read the schema first, the usual habit:

```bash
kubectl explain pod.spec.affinity.nodeAffinity
```

Two fields, and their names are doing a lot of work:

- `requiredDuringSchedulingIgnoredDuringExecution`: a filter-step rule. No matching node, no
  placement, the Pod waits.
- `preferredDuringSchedulingIgnoredDuringExecution`: a score-step rule with a `weight` (1 to
  100). The scheduler favors matching nodes but places the Pod elsewhere if it must.

The second half of both names, `IgnoredDuringExecution`, says when the rule applies: at
scheduling time only. If the node's labels change after the Pod lands, nothing evicts it; the
rule is a placement decision, not a contract the kubelet keeps enforcing. You'll prove that in
a minute.

Save as `pod-affine.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: affine
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: disk
                operator: In          # In, NotIn, Exists, DoesNotExist, Gt, Lt
                values: ["ssd", "nvme"]
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
```

```bash
kubectl apply -f pod-affine.yaml
kubectl get pod affine -o wide      # sched-worker again: it's the only node matching the expression
```

The `operator` field is what `nodeSelector` lacks. `In`/`NotIn` match against a value set,
`Exists`/`DoesNotExist` care only whether the key is present, and `Gt`/`Lt` compare integer
values. Multiple expressions in one `matchExpressions` list must all hold (AND); multiple
entries under `nodeSelectorTerms` are alternatives (OR).

The preferred form wraps the same expression in a weighted term. You don't need to apply this
one; recognize the shape:

```yaml
  affinity:
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 80                  # added to the node's score if it matches
          preference:
            matchExpressions:
              - key: disk
                operator: In
                values: ["ssd"]
```

**Break it: an affinity nobody matches.** The diagnostic skill matters more than the happy
path. Save as `pod-unplaceable.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: unplaceable
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: disk
                operator: In
                values: ["tape"]     # no node carries disk=tape
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
```

```bash
kubectl apply -f pod-unplaceable.yaml
kubectl get pod unplaceable          # STATUS: Pending, and it will stay Pending
kubectl describe pod unplaceable | tail -8
```

> **What you should see:** a Warning event, reason `FailedScheduling`, with a message that
> accounts for every node: `0/3 nodes are available: 1 node(s) had untolerated taint
> {node-role.kubernetes.io/control-plane: }, 2 node(s) didn't match Pod's node
> affinity/selector.` Read it as a ledger. Three nodes went into the filter; the taint from
> section 2 eliminated one and your affinity eliminated the other two; zero came out. Nothing
> retries on a timer here: the scheduler re-evaluates when the cluster changes (a new node, a
> new label), so the fix is always to change one side of the negotiation.

The apiserver accepted this Pod without complaint. Affinity is checked against the nodes that
exist at scheduling time, so an impossible rule is a `Pending` Pod and an event, never a
rejection at `kubectl apply`. Compare that with the quota rejections in section 7, which happen
at admission and never produce a Pod at all.

Now the `IgnoredDuringExecution` proof. Remove the label the running Pods depend on:

```bash
kubectl delete pod unplaceable
kubectl label node sched-worker disk-      # trailing "-" removes the label
kubectl get pods pinned affine -o wide
```

> **What you should see:** both Pods still `Running` on `sched-worker`, whose `disk` label is
> gone. Their placement rules would fail if evaluated now, and nobody evaluates them. A
> `requiredDuringSchedulingRequiredDuringExecution` variant that evicts on label changes has
> been in the API's naming scheme since the beginning and has never shipped.

Delete the demo Pods; the label work is done:

```bash
kubectl delete pod pinned affine
```

## 4. Pod anti-affinity: keep replicas apart

Node affinity places Pods relative to node labels. **podAntiAffinity** places them relative to
other Pods: "don't put me where Pods matching this selector already are." The classic use is
exactly the one you'll build: replicas of one Deployment on different nodes, so one node dying
takes out one replica instead of all of them.

```bash
kubectl explain pod.spec.affinity.podAntiAffinity | head -20
```

Save as `deploy-spread.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spread
spec:
  replicas: 3
  selector:
    matchLabels: { app: spread }
  template:
    metadata:
      labels: { app: spread }
    spec:
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels: { app: spread }       # "pods that look like me"
              topologyKey: kubernetes.io/hostname  # "per node"
      containers:
        - name: app
          image: busybox:1.36
          command: ["sh", "-c", "sleep 3600"]
```

Two fields carry the meaning. The `labelSelector` names which Pods repel this one; pointing it
at the Deployment's own `app` label makes the replicas repel each other. The `topologyKey`
names a node label and defines what "the same place" means: every node carries a unique
`kubernetes.io/hostname`, so the rule reads "no two `app=spread` Pods on the same node." A
cloud cluster whose nodes carry `topology.kubernetes.io/zone` could use that key instead, and
the same rule would read "no two per zone."

```bash
kubectl apply -f deploy-spread.yaml
kubectl get pods -l app=spread -o wide
```

> **What you should see:** two Pods `Running`, one on `sched-worker` and one on
> `sched-worker2`, and a third stuck `Pending`. You asked for three replicas that must all be
> on different nodes, and only two nodes accept Pods (the control-plane taint again). Confirm
> in the event:
>
> ```bash
> kubectl describe pod -l app=spread | grep -A 3 Events: | tail -4
> ```
>
> The `FailedScheduling` ledger this time: 1 node had the untolerated taint, 2 nodes
> `didn't match pod anti-affinity rules`. The required rule is a hard exclusion, and the
> scheduler would rather leave a replica Pending than break it.

That Pending replica is the cost of `required`. The preferred form trades the guarantee for
availability: spread if possible, pack if not. Delete the Deployment (cleaner than watching
two ReplicaSets negotiate during a rolling update) and replace the affinity block in
`deploy-spread.yaml` with the preferred shape:

```bash
kubectl delete deployment spread
```

```yaml
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels: { app: spread }
                topologyKey: kubernetes.io/hostname
```

```bash
kubectl apply -f deploy-spread.yaml
kubectl get pods -l app=spread -o wide
```

> **What you should see:** all three Pods `Running`. Two land on one worker and one on the
> other: the third replica's anti-affinity preference lost to the requirement to schedule at
> all, which is what `preferred` means. For most stateless services this is the behavior you
> want; save `required` anti-affinity for Pods that genuinely must never share a failure
> domain, and know its arithmetic: replicas beyond the number of schedulable domains will sit
> Pending.

## 5. topologySpreadConstraints: spread with a tolerated skew

Anti-affinity's model of spreading is binary: a node either has a matching Pod or it's
excluded. Once you run more replicas than nodes, what you want is a different guarantee, an
even distribution. That's what **topologySpreadConstraints** expresses directly:

```bash
kubectl explain pod.spec.topologySpreadConstraints | head -25
```

Save as `deploy-even.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: even
spec:
  replicas: 4
  selector:
    matchLabels: { app: even }
  template:
    metadata:
      labels: { app: even }
    spec:
      topologySpreadConstraints:
        - maxSkew: 1                           # busiest domain minus emptiest domain, at most
          topologyKey: kubernetes.io/hostname  # a domain = a node, same as section 4
          whenUnsatisfiable: DoNotSchedule     # violating placements are filtered out
          nodeTaintsPolicy: Honor              # don't count nodes this Pod can't land on (see below)
          labelSelector:
            matchLabels: { app: even }
      containers:
        - name: app
          image: busybox:1.36
          command: ["sh", "-c", "sleep 3600"]
```

Field by field: `maxSkew` is the tolerance, the largest allowed difference between the count of
matching Pods in the fullest domain and in the emptiest. `topologyKey` defines the domain, per
node here. `whenUnsatisfiable` picks the step from section 1 the rule runs in:
`DoNotSchedule` makes it a filter (hard), `ScheduleAnyway` makes it a score input (the
scheduler minimizes skew but never blocks on it).

`nodeTaintsPolicy: Honor` deserves its own paragraph, because leaving it out breaks this exact
demo. By default the skew math counts every node as a domain, including nodes whose taints the
Pod doesn't tolerate. On this cluster that means `sched-control-plane` is a domain permanently
stuck at zero Pods, so the emptiest domain is always 0, and once one worker holds 2 Pods the
skew is 2 and `DoNotSchedule` strands the rest of your replicas Pending. `Honor` tells the
constraint to count only nodes the Pod could land on. Any cluster with tainted nodes (control
planes, GPU pools) hits this.

```bash
kubectl apply -f deploy-even.yaml
kubectl get pods -l app=even -o wide
```

> **What you should see:** four Pods `Running`, two on each worker: skew 0. Now push it to an
> odd count and watch the tolerance work:
>
> ```bash
> kubectl scale deployment even --replicas=5
> kubectl get pods -l app=even -o wide
> ```
>
> A 3/2 split, skew 1, allowed. A sixth replica would have to go to the 3-Pod node's partner
> to keep skew at 1, and it does. The constraint holds the distribution even as you scale,
> which required anti-affinity can't express at all.

Choosing between the two: anti-affinity answers "these Pods must not share a domain," a hard
exclusion that stops making sense the moment replicas outnumber domains. Topology spread
answers "keep the distribution even, within `maxSkew`," and keeps making sense at any replica
count. For plain "spread my Deployment's replicas," spread constraints with `ScheduleAnyway`
are the modern default; reach for required anti-affinity when co-location is genuinely
unacceptable, like two replicas of a quorum member in one rack.

## 6. Taints and tolerations

Everything so far was the Pod choosing nodes. Taints are the node side of the negotiation: a
taint on a node repels every Pod that doesn't carry a matching toleration. You've been living
with one all lab (the control-plane taint), and `04-vllm/lab-04` uses one to keep web Pods off
GPU nodes. Make your own:

```bash
kubectl taint node sched-worker maintenance=true:NoSchedule
kubectl create deployment drain-test --image=busybox:1.36 --replicas=4 -- sh -c "sleep 3600"
kubectl get pods -o wide
```

> **What you should see:** all four `drain-test` Pods on `sched-worker2`. Two nodes are now
> tainted, so for an ordinary Pod this became a one-node cluster. Look at the Pods that were
> already on `sched-worker`: the `spread` and `even` Pods from sections 4 and 5 are still
> `Running` there. `NoSchedule` gates placement only; the effect that evicts running Pods is
> `NoExecute`, which is how Kubernetes drains nodes it considers unhealthy.

A toleration is the Pod's answer to a taint. Save as `pod-tolerant.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: tolerant
spec:
  tolerations:
    - key: maintenance         # must match the taint's key...
      operator: Equal
      value: "true"            # ...and value...
      effect: NoSchedule       # ...and effect
  nodeSelector:
    kubernetes.io/hostname: sched-worker   # the steering; the toleration alone doesn't attract
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
```

```bash
kubectl apply -f pod-tolerant.yaml
kubectl get pod tolerant -o wide     # NODE: sched-worker, past the taint
```

The pairing in that spec is the pattern to remember, and it's the same one `04-vllm/lab-04`
uses for GPU nodes: the toleration is permission (the Pod may land on the tainted node, and
without other constraints could land anywhere), the selector is the steering. A toleration by
itself never pulls a Pod toward a taint.

Untaint the node before moving on, same trailing-dash syntax as removing a label:

```bash
kubectl taint node sched-worker maintenance=true:NoSchedule-
```

## 7. ResourceQuota: the namespace ceiling

The rest of this lab moves from placement to permission. Stay on the `sched` cluster and its
`kind-sched` context for all of it; everything you create from here lives in one namespace on
the throwaway cluster and dies with it in the cleanup. (ResourceQuota and LimitRange are
namespace-scoped and need no worker nodes; they'd behave identically on your `learn` cluster.)

Since lab-03 you've set per-container `requests` and `limits`, and lab-08 showed the kernel
enforcing the limits. Neither is governance. Requests are a claim each Pod makes for itself,
and nothing so far stops one team's namespace from requesting every core in the cluster, one
correctly-formed Pod at a time. A **ResourceQuota** is the namespace-level cap: an admission
check the apiserver runs on every Pod create, against the namespace's running total.

```bash
kubectl explain resourcequota.spec
kubectl create namespace team-a
```

Save as `quota-team-a.yaml`:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: team-a-quota
  namespace: team-a
spec:
  hard:
    requests.cpu: "1"        # the namespace's Pods may request at most 1 CPU total
    requests.memory: 1Gi
    limits.cpu: "2"          # and their limits may sum to at most 2 CPUs
    limits.memory: 2Gi
    pods: "10"               # object count cap, independent of size
```

```bash
kubectl apply -f quota-team-a.yaml
kubectl describe quota team-a-quota -n team-a
```

> **What you should see:** a Used/Hard table with every Used at 0. This table is live
> accounting: the apiserver updates it on every Pod create and delete in the namespace, and
> it's the first thing to check when a quota misbehaves.

Now run into the ceiling. Save as `deploy-hungry.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hungry
  namespace: team-a
spec:
  replicas: 3
  selector:
    matchLabels: { app: hungry }
  template:
    metadata:
      labels: { app: hungry }
    spec:
      containers:
        - name: app
          image: busybox:1.36
          command: ["sh", "-c", "sleep 3600"]
          resources:
            requests:
              cpu: 500m          # 3 replicas x 500m = 1500m, over the 1-CPU request quota
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
```

```bash
kubectl apply -f deploy-hungry.yaml
kubectl get pods -n team-a
kubectl get deployment hungry -n team-a
```

> **What you should see:** two Pods, both `Running`, and READY stuck at `2/3`. No Pending Pod,
> no Error Pod, no third Pod at all. Two replicas consumed the full CPU request budget
> (2 x 500m = 1), so the third was never created.

Where's the error? Not on any Pod: a Pod that was never admitted doesn't exist to carry a
status. Think about who creates Pods for a Deployment (lab-03): the ReplicaSet controller. It
called the apiserver to create the third Pod, admission rejected the request, and the failure
landed on the caller:

```bash
kubectl describe rs -n team-a -l app=hungry | tail -8
```

> **What you should see:** a Warning event, reason `FailedCreate`, message
> `pods "hungry-..." is forbidden: exceeded quota: team-a-quota, requested:
> requests.cpu=500m, used: requests.cpu=1, limited: requests.cpu=1`. The arithmetic of the
> rejection is in the message: the request on the table, the running total, the cap.

This failure location is the lesson of the section. A Deployment stuck below its replica count
with no unhealthy Pods means the Pods are failing to be created rather than failing to run, and
the evidence is on the ReplicaSet (or in `kubectl get events -n team-a`), one level up from
where lab-08 taught you to look. The ReplicaSet keeps retrying quietly, so freeing budget,
scaling something down or raising the quota, lets the third replica appear with no further
action from you.

## 8. LimitRange: defaults and bounds

The quota you wrote meters requests and limits, which only works if every container declares
them. So the quota refuses containers that don't. Save as `pod-naked.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: naked
  namespace: team-a
spec:
  containers:
    - name: app
      image: busybox:1.36        # no resources block at all
      command: ["sh", "-c", "sleep 3600"]
```

```bash
kubectl apply -f pod-naked.yaml
```

> **What you should see:** rejected at your terminal, no Pod created:
>
> ```
> Error from server (Forbidden): error when creating "pod-naked.yaml": pods "naked" is forbidden: failed quota: team-a-quota: must specify limits.cpu for: app; limits.memory for: app; requests.cpu for: app; requests.memory for: app
> ```
>
> You created this Pod directly, so the admission error came straight back to you. The same
> Pod inside a Deployment would fail the way section 7 did: silently missing, with the message
> on the ReplicaSet.

A quota on requests makes requests mandatory, and a namespace where `kubectl run` and every
quick-start manifest gets `Forbidden` is hostile to its users. A **LimitRange** is the
companion object that fixes this: per-container defaults injected at admission, plus min/max
bounds per container.

```bash
kubectl explain limitrange.spec.limits
```

Save as `limitrange-team-a.yaml`:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: team-a-defaults
  namespace: team-a
spec:
  limits:
    - type: Container
      defaultRequest:        # injected as requests when a container declares none
        cpu: 100m
        memory: 64Mi
      default:               # injected as limits (the name means "default limit")
        cpu: 250m
        memory: 128Mi
      min:                   # a container may not request less than this
        cpu: 50m
        memory: 32Mi
      max:                   # or set limits higher than this
        cpu: "1"
        memory: 512Mi
```

Watch the naming: `default` sets the default limits and `defaultRequest` the default requests.
The `min`/`max` bounds are the other half of the object: a container demanding `cpu: "4"` in
this namespace gets rejected at admission with `maximum cpu usage per Container is 1`, even
though it would pass a quota with room left.

```bash
kubectl apply -f limitrange-team-a.yaml
kubectl apply -f pod-naked.yaml            # same manifest that was Forbidden a minute ago
kubectl get pod naked -n team-a -o yaml | grep -B 1 -A 8 "resources:"
```

> **What you should see:** the Pod is admitted and `Running`, and its spec now carries a
> `resources` block you never wrote: requests of `cpu: 100m` and `memory: 64Mi`, limits of
> `cpu: 250m` and `memory: 128Mi`. Scroll up in the same output and the Pod's annotations
> include `kubernetes.io/limit-ranger` naming exactly what the admission plugin injected. The
> manifest on disk still has no resources; the served object does. That difference between
> what you wrote and what admission stored is worth remembering whenever a live object
> surprises you.

The two objects are designed as a pair: ResourceQuota sets the namespace ceiling, LimitRange
makes every container countable against it (and keeps any single container from being absurd).
A namespace with a quota and no LimitRange punishes everyone who hasn't read section 7.

## 9. Choosing a placement tool

Five mechanisms, one question each. This is the table for the moment someone says "make these
Pods land somewhere sensible":

| Tool | The question it answers |
|---|---|
| `nodeSelector` | "Does the node carry exactly these labels?" Blunt: equality only, always required. |
| `nodeAffinity` | "Does the node match this expression?" Operators over label sets, in required or preferred strength. |
| `podAntiAffinity` | "Which Pods are already there?" Placement relative to other Pods; required means never co-located. |
| `topologySpreadConstraints` | "How uneven may the distribution get?" Even spread with a tolerated `maxSkew`, at any replica count. |
| taints + tolerations | "Whom does the node let in?" The one mechanism where the node repels; everything above is the Pod choosing. |

And the two admission objects from Part B: ResourceQuota answers "how much may this namespace
consume in total," LimitRange answers "what does one container get by default, and what are its
bounds."

## Checkpoint: you can now explain…

1. **The scheduler's two-step.** Filter removes nodes that fail any hard requirement (resources
   for requests, required affinity, untolerated taints); score ranks the survivors and
   preferred rules add weight. Zero feasible nodes means `Pending` plus a `FailedScheduling`
   event that itemizes what eliminated each node.
2. **Required vs preferred, and `IgnoredDuringExecution`.** Required rules run in the filter and
   can leave Pods Pending; preferred rules run in the score and always yield a placement. Both
   apply only at scheduling time: labels changing under a running Pod evict nothing.
3. **Anti-affinity vs topology spread.** Anti-affinity is exclusion ("never together"), and its
   required form strands replicas once they outnumber schedulable domains. Spread constraints
   state the goal directly, an even distribution within `maxSkew`, and scale past the domain
   count. Tainted nodes count as domains unless `nodeTaintsPolicy: Honor` says otherwise.
4. **Taints vs labels.** Labels plus selectors are the Pod choosing nodes; a taint is the node
   refusing Pods. A toleration is permission to pass the taint, never attraction, which is why
   the GPU pattern pairs it with a `nodeSelector`.
5. **Why the quota failure sat on the ReplicaSet.** Quota is enforced at admission, when a Pod
   is created. A Pod that was refused admission never exists, so the error goes to whoever
   called create: you, for a bare Pod; the ReplicaSet controller, for a Deployment. `2/3` with
   zero unhealthy Pods says "look one level up."
6. **Why a quota forces requests, and what LimitRange does about it.** A quota metering
   `requests.*` cannot count a container that declares nothing, so admission rejects it
   outright. A LimitRange injects `defaultRequest` and `default` values at admission and
   enforces `min`/`max` per container, making the quota livable.

You can now:

- [ ] Create a multi-node kind cluster from a config file and switch contexts between two
      clusters.
- [ ] Read a `FailedScheduling` event as a ledger and name which constraint eliminated each
      count of nodes.
- [ ] Predict, before applying, how many replicas of a required-anti-affinity Deployment will
      schedule on N untainted nodes.
- [ ] Find a quota rejection when there is no failed Pod to describe, via the ReplicaSet's
      events.
- [ ] Show the requests a LimitRange injected by comparing your manifest with
      `kubectl get pod -o yaml`.

## Cleanup

The whole of this lab lived on the throwaway cluster, so cleanup is one delete plus a context
switch:

```bash
kind delete cluster --name sched            # removes all three node containers AND the kind-sched context
kubectl config use-context kind-learn      # back to the course cluster
kubectl config get-contexts                # confirm: kind-learn, current
kubectl delete namespace team-a --ignore-not-found   # no-op unless you ran Part B on learn instead
```

Deleting the cluster takes `team-a`, the quota, and every Deployment with it. The last line
only matters if you chose to run sections 7 and 8 against your `learn` cluster; on the standard
path it prints `Error from server (NotFound)`-free silence and does nothing.

## Next

→ Phase 4 (`04-vllm`) is where this lab's mechanisms meet real hardware. `04-vllm/lab-04`
runs the toleration-plus-nodeSelector pattern from section 6 against GPU nodes, where the taint
protects hardware that costs real money per hour, and `09-lke-akamai/lab-03` shows the managed
variant where the `nvidia.com/gpu` resource request does the filtering on its own. When you
read those manifests, every field in them is this lab at production stakes.
