# Lab 12: Jobs, CronJobs, DaemonSets

**What you'll build:** the three workload controllers this course has skipped so far. First you
prove why a Deployment is the wrong wrapper for run-to-completion work by watching one crash-loop
on a task that succeeded. Then you replace it with a **Job** (run to completion, with retries), a
**CronJob** (a Job on a clock, which you watch tick twice and then suspend), and a **DaemonSet**
(one Pod per node, the shape kube-proxy itself uses). By the end you can pick the right
controller by the shape of the work instead of reaching for Deployment every time.

> **The one idea:** every workload controller answers the same three questions differently:
> how many Pods, for how long, and where. A Deployment says "N copies, forever, anywhere."
> A Job says "enough copies to finish, once." A CronJob says "a fresh Job every tick."
> A DaemonSet says "one copy per node." Match the answer to the work.

## 1. Break it first: batch work in a Deployment

Everything you've deployed since lab-03 has been a server: a process that starts and never
exits. Batch work is the opposite. A database migration, a report, a document-ingestion run:
each starts, does its work, and exits. Watch what a Deployment does with that.

Save this as `deploy-batch-wrong.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: batch-wrong
spec:
  replicas: 1
  selector:
    matchLabels: { app: batch-wrong }
  template:
    metadata:
      labels: { app: batch-wrong }
    spec:
      containers:            # no restartPolicy here: Deployments force Always (see below)
        - name: task
          image: busybox:1.36
          command: ["sh", "-c", "echo done"]   # exits 0 almost immediately - a "successful" task
```

```bash
kubectl apply -f deploy-batch-wrong.yaml
kubectl get pods -l app=batch-wrong -w    # leave this running for ~2 minutes, then Ctrl-C
```

> **What you should see:** the Pod cycles `Running` → `Completed` → `Running` while the
> RESTARTS column climbs, then the status settles into `CrashLoopBackOff`. The container did
> exactly what you asked, printed `done`, and exited 0. Kubernetes restarted it anyway.

Read the events for the why:

```bash
kubectl describe pod -l app=batch-wrong | tail -15
```

The last event repeats: `Back-off restarting failed container task in pod batch-wrong-...`.
The runtime calls the container "failed" even though it exited 0, because under a Deployment
the Pod's `restartPolicy` is `Always`, and `Always` means what it says: any exit, clean or not,
gets restarted. When the restarts come fast, the kubelet backs off between attempts
(10s, 20s, 40s, capped at 5m), which is the `CrashLoopBackOff` you learned to read in lab-08.

This is not a configuration you can fix. Check the API's own documentation:

```bash
kubectl explain deployment.spec.template.spec.restartPolicy
```

The field description tells you the only value a Deployment accepts is `Always`. A Deployment's
entire job is "keep N Pods running forever"; a container that exits, for any reason, violates
its desired state. For work that is supposed to finish, you need a controller whose desired
state is "finished." Delete the wreck and meet it:

```bash
kubectl delete -f deploy-batch-wrong.yaml
```

## 2. Jobs: run to completion

A **Job** creates Pods and keeps creating them until a specified number succeed. Success is
defined by the container's exit code: 0 counts, anything else triggers a retry. Jobs live in the
`batch/v1` API group (the first non-core, non-apps group you've used):

```bash
kubectl explain job          # KIND: Job, VERSION: batch/v1
kubectl explain job.spec     # completions, parallelism, backoffLimit, ttlSecondsAfterFinished...
```

Save this as `job-hello.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: hello
spec:
  backoffLimit: 4            # retry a failing Pod up to 4 times, then mark the Job Failed
  template:                  # the same Pod template you know from Deployments...
    spec:
      restartPolicy: Never   # ...except this field is now REQUIRED to be Never or OnFailure
      containers:
        - name: task
          image: busybox:1.36
          command: ["sh", "-c", "echo processing; sleep 5; echo done"]
```

```bash
kubectl apply -f job-hello.yaml
kubectl get job hello -w     # Ctrl-C once COMPLETIONS reads 1/1
```

> **What you should see:** the COMPLETIONS column goes `0/1` → `1/1` in about five seconds, and
> DURATION records how long the run took. `0/1` means zero of the one required successful Pod;
> a Job's progress is counted in successes, where a Deployment's READY column counts live replicas.

Now find the Pod:

```bash
kubectl get pods -l batch.kubernetes.io/job-name=hello
kubectl logs job/hello
```

> **What you should see:** the Pod is still there with STATUS `Completed`, RESTARTS 0. Lab-02
> told you a server Pod never reaches the `Succeeded` phase because nothing ever finishes; this
> Pod finished, so there it is (`kubectl get pod <name> -o jsonpath='{.status.phase}'` prints
> `Succeeded`). The Job deliberately does not delete it: a dead-but-kept Pod is what makes
> `kubectl logs job/hello` still work after the fact. `logs job/hello` resolves the Job to its
> Pod for you and prints `processing` and `done`.

**Break it: ask for `Always`.** Section 1 showed the Deployment side of the contract; here is
the Job side. Copy `job-hello.yaml`, rename the Job to `hello-always`, set
`restartPolicy: Always`, and apply it:

```bash
kubectl apply -f job-always.yaml
```

> **What you should see:** the apiserver rejects it at admission, no Pod ever created:
>
> ```
> The Job "hello-always" is invalid: spec.template.spec.restartPolicy: Unsupported value: "Always": supported values: "OnFailure", "Never"
> ```

The rejection is the same logic as section 1, enforced in the other direction. A Job needs its
containers to be allowed to stop; `Always` orders the kubelet to restart a finished container
forever, so the Job could never observe a completion. The apiserver refuses the contradiction
up front instead of letting you build a crash loop with extra steps. Confirm the contract in
the schema, same habit as always:

```bash
kubectl explain job.spec.template.spec | grep -A 6 restartPolicy
```

So what's the difference between the two allowed values?

- **`Never`**: a failed attempt is abandoned and the Job controller creates a fresh Pod for the
  retry. Failed Pods pile up, which sounds messy but is exactly what you want while debugging:
  every attempt's logs survive as a separate Pod.
- **`OnFailure`**: the kubelet restarts the container in place, inside the same Pod. You get one
  Pod with a climbing RESTARTS column, and each restart overwrites your access to the previous
  attempt's logs (`kubectl logs --previous` reaches back exactly one attempt).

Use `Never` until a Job is boring, then `OnFailure` if the Pod-per-retry litter bothers you.

### 2.1 parallelism and completions

A Job can require more than one success, and run attempts in parallel. Save as `job-fanout.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: fanout
spec:
  completions: 5             # the Job is complete when 5 Pods have succeeded
  parallelism: 2             # run at most 2 Pods at a time
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: task
          image: busybox:1.36
          command: ["sh", "-c", "echo processing on $(hostname); sleep 3"]
```

```bash
kubectl apply -f job-fanout.yaml
kubectl get pods -l batch.kubernetes.io/job-name=fanout -w   # Ctrl-C when all 5 are Completed
kubectl get job fanout
```

> **What you should see:** Pods arrive two at a time (never three; `parallelism` is a ceiling),
> each runs ~3 seconds, and as one completes the controller starts the next, until COMPLETIONS
> reads `5/5`. Each Pod's log names a different `hostname` because the hostname is the Pod name:
> five separate work items, which is the shape of a chunked batch (5 shards of documents,
> 5 date ranges of a report).

### 2.2 A Job that can't succeed

Retries are the other half of the contract. Save as `job-doomed.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: doomed
spec:
  backoffLimit: 2            # tolerate 2 failures, then give up
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: task
          image: busybox:1.36
          command: ["sh", "-c", "echo attempting; exit 1"]   # fails every time
```

```bash
kubectl apply -f job-doomed.yaml
kubectl get pods -l batch.kubernetes.io/job-name=doomed -w   # this takes a minute or two; Ctrl-C when no new Pods appear
```

> **What you should see:** a Pod hits STATUS `Error`, then a replacement appears, with a growing
> delay between attempts (the Job controller backs off exponentially, like the kubelet did in
> section 1). Around three failed Pods in, the retries stop: `backoffLimit: 2` plus the original
> attempt has been spent.

Now read the verdict where you always read verdicts, the events:

```bash
kubectl describe job doomed | tail -10
```

> **What you should see:** a Warning event, reason `BackoffLimitExceeded`, message
> `Job has reached the specified backoff limit`, and above it in the status a condition
> `Failed`. The Job stops creating Pods and stays failed; nothing loops forever. Compare that
> to section 1: same broken workload, but the batch controller has a concept of giving up and
> a place to record why.

### 2.3 Cleaning up after finished Jobs

Finished Jobs and their Pods stay until someone deletes them. On a busy cluster that becomes
thousands of `Completed` corpses. One field fixes it; add it to any Job spec:

```yaml
spec:
  ttlSecondsAfterFinished: 120   # delete the Job and its Pods 120s after it finishes (success OR failure)
```

The trade-off is exactly what you'd guess: after the TTL fires, `kubectl logs job/...` has
nothing to read, so size the window to how long you need the evidence. Leave `hello`, `fanout`,
and `doomed` around for now; the cleanup section deletes them, and you'll want `doomed`'s
corpse for the checkpoint.

## 3. CronJobs: Jobs on a clock

A **CronJob** creates a new Job on a schedule. It's a controller of controllers: three Pod-less
layers of nesting before you reach a container, which you can walk with `explain`:

```bash
kubectl explain cronjob.spec.jobTemplate.spec.template.spec | head -5
```

The schedule uses the standard five-field cron syntax: minute, hour, day-of-month, month,
day-of-week. Three worked examples:

- `*/5 * * * *` : every 5 minutes (the `/5` is a step over the minute field).
- `0 3 * * *` : daily at 03:00 (minute 0, hour 3, every day).
- `0 9 * * 1-5` : 09:00 Monday through Friday (day-of-week 1-5).

Times are evaluated in the kube-controller-manager's timezone, which in your kind cluster is
UTC; `spec.timeZone: "America/New_York"` pins a schedule to a named zone instead. For the lab,
use the schedule you never ship to production, every minute. Save as `cronjob-tick.yaml`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: tick
spec:
  schedule: "* * * * *"            # every minute - demo cadence, not production cadence
  concurrencyPolicy: Forbid        # if the previous run is still going, SKIP this tick (see below)
  successfulJobsHistoryLimit: 3    # keep the last 3 succeeded Jobs, delete older ones
  failedJobsHistoryLimit: 1        # keep only the last failed Job
  startingDeadlineSeconds: 60      # a tick that can't start within 60s of schedule counts as missed
  jobTemplate:                     # everything under here is a normal Job spec (section 2)
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: task
              image: busybox:1.36
              command: ["sh", "-c", "date; echo tick"]
```

```bash
kubectl apply -f cronjob-tick.yaml
kubectl get jobs --watch         # wait ~2 minutes for two ticks, then Ctrl-C
```

> **What you should see:** nothing for up to a minute (the first tick waits for the next whole
> minute), then a Job named like `tick-29372041` appears and completes, then a minute later a
> second one. The numeric suffix encodes the scheduled time (minutes since the Unix epoch), so
> successive Jobs count up by one. Each Job is a full section-2 citizen: it has its own Pod,
> its own COMPLETIONS, its own logs (`kubectl logs job/tick-<suffix>` prints the date and
> `tick`).

The fields you set deserve one concrete scenario each:

- **`concurrencyPolicy: Allow`** (the default): runs can overlap. Fine for a nightly report
  where two copies writing two files is harmless. Dangerous for anything with a shared target:
  a slow run plus an every-minute schedule quietly stacks up concurrent Jobs.
- **`Forbid`**: a backup job. Two backups writing the same destination at once corrupts it, so
  a still-running backup causes the next tick to be skipped entirely. The skipped tick is gone,
  not queued.
- **`Replace`**: a cache-warming sync where only fresh data matters. A still-running (therefore
  stale) run gets killed and the new tick starts in its place.

`successfulJobsHistoryLimit` and `failedJobsHistoryLimit` are `ttlSecondsAfterFinished` by
count instead of by age: the CronJob keeps that many finished Jobs for `kubectl logs` and
deletes the rest. `startingDeadlineSeconds` handles the controller being down or `Forbid`
blocking a tick: a run that can't start within the deadline is recorded as missed rather than
fired late. (Without any deadline set, the controller also stops scheduling entirely if it
falls more than 100 ticks behind, so setting it is a kindness to your future self.)

Now pause the whole thing without deleting it:

```bash
kubectl patch cronjob tick -p '{"spec":{"suspend":true}}'
kubectl get cronjob tick
```

> **What you should see:** the SUSPEND column flips to `True` and no new Jobs appear from the
> next minute onward. Existing Jobs finish; suspend only stops new ticks. This is the switch
> you throw during an incident ("stop the ingest while we fix the index") and it's also how you
> ship a CronJob dark: `suspend: true` in the manifest, flipped to `false` when you're ready.

## 4. DaemonSets: one Pod per node

The controllers so far answer "how many": N, enough-to-finish, one-per-tick. A **DaemonSet**
answers "where": exactly one Pod on every node. No `replicas` field exists; the node list is
the replica count, and a node joining the cluster gets its Pod automatically.

You have been running DaemonSets since lab-01 without looking at them. Look now:

```bash
kubectl get ds -A
```

> **What you should see:** two DaemonSets in `kube-system`: `kindnet` and `kube-proxy`, each
> with DESIRED, CURRENT, and READY at 1, because your kind cluster has one node. `kindnet` is
> the CNI plugin that wires Pod networking; `kube-proxy` programs the Service DNAT rules you
> met in lab-04. Both are per-node kernel state, which is why they're DaemonSets: a
> `kube-proxy` Deployment with 3 replicas could land all 3 on one node and leave the others
> with no Service routing at all. Log shippers and node metrics agents (fluent-bit,
> node-exporter) have the same one-per-node shape.

Deploy your own. Save as `ds-node-agent.yaml`:

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-agent
spec:
  selector:
    matchLabels: { app: node-agent }
  template:
    metadata:
      labels: { app: node-agent }
    spec:
      tolerations:                                       # control-plane nodes carry a NoSchedule
        - key: node-role.kubernetes.io/control-plane     # taint; without this toleration the
          operator: Exists                               # DaemonSet skips them (proven below)
          effect: NoSchedule
      containers:
        - name: agent
          image: busybox:1.36
          env:
            - name: NODE_NAME
              valueFrom:
                fieldRef: { fieldPath: spec.nodeName }   # the node this copy landed on
          command: ["sh", "-c", "echo agent on $NODE_NAME; while true; do sleep 3600; done"]
```

```bash
kubectl apply -f ds-node-agent.yaml
kubectl get ds node-agent
kubectl get pods -l app=node-agent -o wide
kubectl logs -l app=node-agent
```

> **What you should see:** DESIRED 1, one Pod, and `-o wide` places it on `learn-control-plane`,
> your only node. The log reads `agent on learn-control-plane`, the Downward API handing the
> node name into the container. On a 3-node cluster this same manifest yields 3 Pods, one per
> node, without you changing anything: the scaling knob is the cluster itself.

About that toleration. Check your node's taints:

```bash
kubectl describe node learn-control-plane | grep -A 2 Taints
```

You'll see `<none>`: kind removes the control-plane taint on single-node clusters, because
otherwise nothing could schedule anywhere. Real multi-node clusters keep
`node-role.kubernetes.io/control-plane:NoSchedule` on control-plane nodes, and a DaemonSet that
must cover every node has to tolerate it. See how the professionals handle this:

```bash
kubectl get ds kube-proxy -n kube-system -o yaml | grep -B 2 -A 4 tolerations
```

`kube-proxy` tolerates everything (`operator: Exists` with no key), because a node without
Service routing is broken no matter what taints it carries.

**Break it: the missing toleration.** Your single node is untainted, so put the taint back
yourself and then take the toleration away:

```bash
kubectl taint node learn-control-plane node-role.kubernetes.io/control-plane=:NoSchedule
kubectl get ds node-agent          # still DESIRED 1: the toleration in the manifest covers it
kubectl patch ds node-agent --type json \
  -p '[{"op":"remove","path":"/spec/template/spec/tolerations"}]'
kubectl get ds node-agent -w       # watch DESIRED drop; Ctrl-C after it settles
```

> **What you should see:** DESIRED, CURRENT, and READY all drop to 0 and the Pod terminates.
> No error, no event screaming at you, no `Pending` Pod to catch in `kubectl get pods`. The
> DaemonSet controller computed the set of nodes its Pods are allowed on, got an empty set, and
> reconciled to it. This is the quietest failure mode in this lab: on a real cluster it looks
> like a monitoring agent absent from every tainted node (control-plane nodes,
> GPU nodes with dedicated taints), and you find out when you need the logs it never shipped.

Restore the toleration by re-applying the manifest, then remove your taint:

```bash
kubectl apply -f ds-node-agent.yaml
kubectl taint node learn-control-plane node-role.kubernetes.io/control-plane=:NoSchedule-
kubectl get ds node-agent          # back to DESIRED 1
```

(The trailing `-` on a taint command removes the taint. Leaving it in place would break the
next lab's scheduling in confusing ways, so don't skip this.)

### 4.1 Updating a DaemonSet

DaemonSets roll updates like Deployments do, with one difference forced by their shape: there
is nowhere to surge to. One Pod per node means the old Pod on a node dies before the new one
starts there. The default `updateStrategy` is `RollingUpdate` with `maxUnavailable: 1`, one
node at a time; the alternative, `OnDelete`, stages the new template but touches nothing until
you delete Pods yourself, for agents so critical you want a human choosing the order. Read the
two strategies' fields with the usual habit:

```bash
kubectl explain daemonset.spec.updateStrategy
```

## 5. Choosing a workload type

Five controllers, one line each. This is the table to have in your head when someone says
"deploy this":

| Controller | What it guarantees | Reach for it when |
|---|---|---|
| Deployment | N interchangeable replicas, kept running, rolling updates | stateless servers: web apps, APIs, the vLLM and gateway Pods later in this course |
| StatefulSet | stable per-replica identity and per-replica storage (previewed in lab-06) | databases and anything where replica-0 and replica-1 must not be interchangeable |
| Job | Pods run until N of them succeed, failures retried up to `backoffLimit` | run-to-completion work: migrations, batch compute, document ingestion |
| CronJob | a fresh Job per schedule tick, with overlap policy and history limits | recurring batch: backups, reports, periodic cleanup |
| DaemonSet | exactly one Pod on every (tolerated) node, including new nodes | node-level agents: log shippers, metrics exporters, CNI, kube-proxy |

## Checkpoint: you can now explain…

1. **Why a Deployment crash-loops a successful batch task.** A Deployment's Pods are forced to
   `restartPolicy: Always`, so any exit, even exit 0, is restarted; fast repeated exits trigger
   the kubelet's backoff, hence `CrashLoopBackOff` on a container that did its job.
2. **Why a Job rejects `restartPolicy: Always`.** A Job's desired state is a count of completed
   Pods; `Always` would forbid completion, so the apiserver refuses the combination at admission.
3. **`Never` vs `OnFailure`.** `Never` retries with a fresh Pod and keeps every failed attempt's
   logs; `OnFailure` restarts in place and keeps only the last attempt (plus one via `--previous`).
4. **What `backoffLimit` exhaustion looks like.** Failed Pods with growing gaps between them,
   then a `BackoffLimitExceeded` event and a `Failed` condition on the Job. The controller gives
   up and says so, where the Deployment in section 1 looped forever.
5. **The three concurrency policies.** Allow: overlapping runs are fine (independent reports).
   Forbid: skip the tick (backups to one target). Replace: kill the stale run (freshness wins).
6. **Why kube-proxy is a DaemonSet.** Its work is per-node kernel state; coverage, not replica
   count, is the requirement. And why a missing toleration is dangerous: the DaemonSet silently
   shrinks its node set instead of erroring.

You can now:

- [ ] Watch `kubectl get job <name> -w` and read COMPLETIONS as successes, not replicas.
- [ ] Pull logs from a completed Job's Pod, and explain why the Pod is still there.
- [ ] Suspend and resume a CronJob with a one-line patch.
- [ ] Predict, before applying, which nodes a DaemonSet will cover given the taints in
      `kubectl describe node`.

## Cleanup

```bash
kubectl delete job hello fanout doomed --ignore-not-found
kubectl delete cronjob tick --ignore-not-found     # cascades: its tick-* Jobs and Pods go too
kubectl delete ds node-agent --ignore-not-found
kubectl delete deploy batch-wrong --ignore-not-found   # in case you skipped the delete in section 1
kubectl taint node learn-control-plane node-role.kubernetes.io/control-plane=:NoSchedule- 2>/dev/null || true
```

The last line is a no-op if you already removed the taint in section 4; it's here so a
half-finished lab can't leave your node unschedulable.

## Next

→ You've now met every workload controller the rest of this course stands on. The Job you
built in section 2 comes back as a real one in phase 10: `10-rag/lab-01` ingests a document
corpus into a vector store with a `batch/v1` Job that chunks, embeds, and upserts, then exits.
When you read that manifest, everything in it (`restartPolicy: Never`, `backoffLimit`, the
completed Pod you pull logs from) is this lab. Next up, `lab-13-scheduling-and-quotas.md`:
you know every workload type; now decide where their Pods land and how much a namespace
may consume.
