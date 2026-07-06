# Phase 2: Docker

**Time budget:** ~20%. Goal: build and run images, debug containers, orchestrate a small multi-service stack with Compose.

## Why this phase exists

Before containers, "it works on my machine" was the default failure mode. Docker packages the entire userland (all the libraries, binaries, and files an app needs above the kernel) into an image, runs it in isolated namespaces, and gives you a reproducible artifact. Kubernetes orchestrates these containers. If you can't reason about a single container, you cannot reason about a thousand.

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

`dockerd` is a background service (daemon) the `docker` CLI sends commands to over a local Unix socket (a file-like channel). `containerd` and `runc` are the runtime layers from Phase 1 Lab 04 (`containerd` → `runc`).

On macOS, the daemon and all containers run inside a Linux VM. The CLI talks to it over that socket.

## Reading list (offline)

- `docker --help`, `docker <subcmd> --help`
- Dockerfile reference (cloned in 00-prep): `docs/content/reference/dockerfile.md`
- "Best practices for writing Dockerfiles" (same repo)
- Compose spec: `docs/content/compose/`

## Labs

1. `lab-01-first-container.md`: run, ps, logs, exec, stop, rm
2. `lab-02-images-and-layers.md`: pull, history, layer cache
3. `lab-03-dockerfile.md`: build a Python app image
4. `lab-04-networking.md`: port mapping, bridges, container DNS
5. `lab-05-volumes.md`: bind mounts vs named volumes
6. `lab-06-compose.md`: multi-service with depends_on, healthchecks
7. `project-fastapi-redis/`: capstone, a small HTTP API + Redis cache

## Notes to keep in mind

Your image is a contract: it runs the same in CI, on prod, on laptops. Treat the Dockerfile like API design: small, deterministic, layered.

After Phase 1, you know `docker run` is `clone+unshare+pivot_root+exec`. Use that to debug. When networking breaks, ask which `net` namespace and which bridge.
