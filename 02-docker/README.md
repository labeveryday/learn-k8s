# Phase 2: Docker

**Time budget:** ~20%. Goal: build and run images, debug containers, orchestrate a small multi-service stack with Compose.

## Why this phase exists (Stanford)

Before containers, "it works on my machine" was the default failure mode. Docker packages the *entire userland* an app needs into an image, runs it in isolated namespaces, and gives you a reproducible artifact. K8s is just an orchestrator for these — if you can't reason about a single container, you cannot reason about a thousand.

## What you'll do

- Build images from `Dockerfile`s and understand the layer cache.
- Manage volumes, networks, and ports.
- Run a 3-service stack with Compose.
- Diagnose a broken container with `docker logs`, `docker inspect`, `docker exec`.

## Architecture (one minute)

```
docker CLI ──► dockerd (daemon) ──► containerd ──► runc ──► your container (a process)
                       │
                       ├── images (in /var/lib/docker/...)
                       ├── networks (Linux bridges)
                       └── volumes
```

On macOS, the daemon and all containers run inside a Linux VM. The CLI talks to it over a socket.

## Reading list (offline)

- `docker --help`, `docker <subcmd> --help`
- Dockerfile reference (cloned in 00-prep): `docs/content/reference/dockerfile.md`
- "Best practices for writing Dockerfiles" — same repo
- Compose spec: `docs/content/compose/`

## Labs

1. `lab-01-first-container.md` — run, ps, logs, exec, stop, rm
2. `lab-02-images-and-layers.md` — pull, history, layer cache
3. `lab-03-dockerfile.md` — build a Python app image
4. `lab-04-networking.md` — port mapping, bridges, container DNS
5. `lab-05-volumes.md` — bind mounts vs named volumes
6. `lab-06-compose.md` — multi-service with depends_on, healthchecks
7. `project-fastapi-redis/` — capstone: a small HTTP API + Redis cache

## Panel notes

> **Kelsey:** "Your image is a contract: it runs the same in CI, on prod, on laptops. Treat the Dockerfile like API design — small, deterministic, layered."
>
> **MIT:** "After Phase 1, you know `docker run` is `clone+unshare+pivot_root+exec`. Now use that to debug — when networking breaks, ask: which `net` namespace? Which bridge?"
