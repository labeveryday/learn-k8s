# 13 · The sandbox: a platform service that manufactures pods

> Phase 12 shipped an agent that reads code. Agents that *run* code need a blast radius,
> and this phase builds the service that provides one: a self-hosted code interpreter
> (the same idea as Bedrock AgentCore's) where a manager creates one hardened pod per
> session, hands your agent a stateful Python kernel inside it, and tears the whole cell
> down when the session ends. The subject repo is
> [agent-sandbox](https://github.com/labeveryday/agent-sandbox); the machinery is yours
> from Phases 03 and 12.

## The problem this phase solves

Give an agent a `run_python` tool on your laptop and you have handed your laptop to
whatever the model decides to type. The industry answer is the code interpreter: execute
in a disposable, isolated runtime with no network and no credentials, and throw the
runtime away afterward. Hosted versions exist behind APIs. Running your own is the
strongest Kubernetes lesson in this course, because the sandbox is your first workload
that is also a platform: software that creates, supervises, and destroys other pods.

```
Your Strands agent (laptop, Phase 12 Pod, anywhere)
      │  sandbox(action="execute", code=...)          one action-based tool
      v
Manager: FastAPI Deployment, port 8700               the control plane
      │  ServiceAccount + Role: pods, one namespace   (RBAC as a badge for software)
      │  creates, proxies to pod IP, reaps on TTL
      v
sbx-<session> Pod                                    one cell per session
      - persistent Jupyter kernel (stateful python)
      - bash + pip install --user, private /workspace (emptyDir, size-capped)
      - worker agent inside, for whole delegated jobs
      - caps dropped, read-only rootfs, non-root, seccomp, no API token
      - egress denied by NetworkPolicy; DNS allowed; internet only if
        network=True, and never RFC1918 or the metadata endpoint
```

Four mechanisms carry the phase, and each one extends something you already know:

| You learned | This phase adds | Lab |
|---|---|---|
| RBAC for humans (Phase 03 lab-09) | RBAC for software: a ServiceAccount whose Role says pods, five verbs, one namespace | 02 |
| `kubectl` creates pods | *code* creates pods: the manager calls the API server the same way kubectl does, with a narrower badge | 02 |
| securityContext fields exist | a worst-case tenant: every field earns its place when the workload's job is running hostile code | 03 |
| NetworkPolicy syntax | the enforcement gap: your kind cluster accepts policies and ignores them, and you prove it both ways | 03 |
| the Phase 07 agent-with-tools loop | delegation: a worker agent inside the cell, reaching exactly one thing (your vLLM) through a one-workload hole in the wall | 04 |

## Prereqs

- Phase 03, with lab-09 (RBAC) fresh. Phase 12's client-workload mindset helps.
- Docker running (lab 01 uses compose), kind for labs 02-04.
- vLLM from Phase 04/06 for the delegation lab; the rest of the phase needs no model at
  all, because `execute` and `shell` are plain code paths.
- Clone the subject repo next to this course:
  `git clone https://github.com/labeveryday/agent-sandbox.git`

## Objectives

1. Drive the sandbox lifecycle over its HTTP API and name what makes the Docker backend's
   ceiling (the socket mount) unacceptable for multi-tenant use.
2. Deploy the manager as a pod factory: dissect its RBAC, verify the badge's limits with
   `kubectl auth can-i`, and watch a 403 from the API server when the badge is revoked.
3. Read a hardened Pod spec written by code, field by field, and test each wall from
   inside the cell.
4. Prove that a NetworkPolicy without an enforcing CNI is a wish, then prove enforcement
   on Calico, then cut a hole exactly one workload wide so the worker agent can reach
   your vLLM and nothing else.

## Labs

| Lab | Idea | The mechanism it teaches |
|---|---|---|
| 01 | `lab-01-the-sandbox-on-docker.md`: compose up, drive the full session lifecycle with curl, find the ceiling | a stateful kernel per session; container hardening flags; why a docker.sock mount is root-equivalent |
| 02 | `lab-02-a-pod-factory-with-rbac.md`: deploy the manager on kind, watch it create and reap pods | ServiceAccount + Role + RoleBinding as a badge for software; pod IPs as the in-cluster data plane; the `:latest` pull-policy trap |
| 03 | `lab-03-the-cell-walls.md`: test the securityContext from inside; catch your CNI ignoring NetworkPolicy | defense in layers; policies are contracts and the CNI is the enforcer |
| 04 | `lab-04-delegation-and-the-precise-hole.md`: a worker agent inside the cell, wired to your vLLM; then a supervisor fanning subtasks to parallel cells | selector-based egress beats ipBlock exceptions; delegation as the pattern for long or dangerous work; supervisor/worker as a topology you already have |

## How it fits the stack

The manager's pod-creation calls hit the same API server your kubectl does (Phase 03),
authenticated by a ServiceAccount token instead of your kubeconfig. Its proxying rides
pod-IP routability, the property underneath every Service you have used. The worker's
model call is the Phase 07 base_url bridge to your Phase 04/06 vLLM. On LKE (Phase 09)
the NetworkPolicies enforce with zero extra work, because LKE ships Calico as its CNI.

## Notes

- The manager is a controller in the informal sense: it observes sessions, creates pods,
  and reaps them on expiry. Compare it with kagent's controller from Phase 07 and ask
  what a CRD would buy it.
- Every wall in lab 03 maps to a kernel primitive you met in Phase 01: capabilities,
  seccomp, namespaces, cgroups. The YAML is new; the walls are not.
- Apply the NetworkPolicy, then try to break out of the sandbox anyway. The day you learn
  your CNI ignores policies should be a lab, not an incident.
