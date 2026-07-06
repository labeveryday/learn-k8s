# 12 · Ship an agent: laptop process to cluster workload

> Phase 07 lab-04 ended with a challenge: containerize a Strands agent and run it as a
> plain Deployment, so you feel the boilerplate kagent absorbs. This phase takes that
> challenge seriously. You build a real agent, wrap it in a health surface, put it in an
> image, and run it on your cluster with secrets, probes, an initContainer, and a
> persistent ledger. By the end you own every piece, and you can name the exact cost of
> owning it.

## The problem this phase solves

Every workload you have deployed so far was a server: vLLM, the gateways, Qdrant. Traffic
arrives, the process answers, the Kubernetes machinery (Services, probes, Ingress) fits
without friction.

An agent inverts that shape. It is a client. It dials out to a model endpoint and out to
Discord; nothing dials in. Deploy one and the standard playbook starts asking questions
with no obvious answers:

| Kubernetes expects | The agent gives you | The fix (and the lab that teaches it) |
|---|---|---|
| an HTTP port to probe | a loop with no listener | wrap it in a small Flask surface (lab 01) |
| a stateless, scalable Pod | one identity: one bot, one ledger | `replicas: 1`, `Recreate` (lab 04) |
| config at deploy time | model keys, tokens, a repo URL | ConfigMap for facts, Secret for credentials (lab 03) |
| a self-contained image | a git repo to review at runtime | initContainer clones before the agent starts (lab 03) |
| disposable filesystems | a feedback ledger worth keeping | PersistentVolumeClaim (lab 04) |

The agent itself reviews a code repository: it reads the files, checks recent commits,
and files concrete findings in a `NEW_FEEDBACK.md` ledger. Give it a Discord token and it
also answers questions in a channel. Leave the token out and the HTTP surface is the whole
interface. The model is your in-cluster vLLM through the same `base_url` bridge you built
in Phase 07 lab-04, so the phase needs no hosted API key.

```
            you                                     the cluster
             │                                          │
   POST /review ──► Service ──► Pod ┌──────────────────┐│
                                    │ initContainer:    ││  clones REPO_URL
                                    │   git clone       ││  into an emptyDir
                                    ├──────────────────┤│
                                    │ Flask  :8080      ││  /healthz /readyz
                                    │   └─ review agent ─┼┼─► vLLM /v1  (Phase 04/06)
                                    │   └─ discord thread┼┼─► gateway.discord.gg (optional)
                                    └──────────────────┘│
```

## Prereqs

- Phase 03 (Deployments, ConfigMaps and Secrets, probes) and a running kind cluster.
- For a keyless model: vLLM from Phase 04/06 reachable in-cluster (`kubectl get svc vllm`).
  An Anthropic API key works as the substitute if you skipped those phases.
- Discord is optional throughout. Labs mark every step that needs it.

## Objectives

1. Explain why an agent workload needs a health surface it did not need on your laptop,
   and write one (Flask, three read endpoints, one trigger).
2. Build a small, non-root image whose layers cache in the right order.
3. Deploy the agent with its config split correctly across ConfigMap and Secret, its
   repo cloned by an initContainer, and its probes answering two different questions.
4. Defend `replicas: 1` for a workload with identity, choose `Recreate` over
   `RollingUpdate` on purpose, and move the ledger to a PersistentVolumeClaim.
5. Name what you now own by hand that kagent's CRDs (Phase 07) would absorb.

## Labs

| Lab | Idea | The mechanism it teaches |
|---|---|---|
| 01 | `lab-01-the-agent-and-its-health.md`: read the agent, run it as a local process | liveness and readiness are different questions; a client workload needs a server bolted on to answer them |
| 02 | `lab-02-containerize.md`: Dockerfile, layer order, non-root, load into kind | the image build cache rewards you for ordering layers by change frequency |
| 03 | `lab-03-deploy-to-kubernetes.md`: Secret + ConfigMap + initContainer + probes + Service | config splits by sensitivity; init runs to completion before the main container starts; readiness gates Service endpoints |
| 04 | `lab-04-identity-state-and-rollouts.md`: break the singleton, fix the rollout, persist the ledger | some workloads have identity; scaling them is a bug, and the strategy field is where you say so |

## How it fits the stack

Nothing new sits below this phase. The agent's model call is HTTP to a Service ClusterIP,
resolved by CoreDNS and DNAT'd by kube-proxy (Phase 03). The image is a Phase 02
Dockerfile. The probes are Phase 03 lab-08 probes. The one new idea is architectural: a
workload whose traffic all points outward, and what that does to each piece of machinery
you already know.

## Notes to carry through the phase

> Ask what breaks when the arrow of traffic reverses. Services, probes, and autoscaling
> all assumed inbound. Watch each assumption fail, then watch the fix.
>
> The Flask server exists for one reason: the kubelet speaks HTTP and the agent does not.
> That is an adapter. Name the two interfaces it sits between.
>
> Run it by hand before you run it on the cluster. Every YAML field in lab 03 answers a
> question you already hit in lab 01.
