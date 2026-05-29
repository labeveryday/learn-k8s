# Lab 05 — Volumes and Persistence

**What you'll build:** not an app — a *mental model* of where a container's data lives and how
to make it outlive the container. You'll mount the same `/app/data` three different ways (a host
path, a Docker-managed volume, and in-memory), then prove the difference by killing a Postgres
container and watching its database survive *because* the data was on a volume, not in the
container. By the end you can look at any `-v`/`--mount` flag and say exactly *what* it mounts,
*where* the bytes actually live, and *what happens to them* when the container dies.

> **The one idea (Kelsey lens):** a container's filesystem is its image's read-only layers plus a
> thin **writable layer** that is created with the container and **deleted with it** (lab-02). A
> volume is a path that bypasses that writable layer entirely — it lives outside the container's
> lifecycle. So "persistence" isn't a feature you turn on; it's *which side of that boundary your
> bytes land on.* Every section below is one way to land them on the durable side.

Containers are **ephemeral** by design. The writable layer dies with the container. For state, use volumes.

## 1. Three ways to mount

The same destination (`/app/data` or `/scratch`) backed by three different sources. The *flag*
is what decides where the bytes physically live and how long they last:

```bash
# Bind mount: host path → container path. Great for development.
docker run --rm -v /tmp/data:/app/data alpine:3.19 ls /app/data        # source is an absolute HOST path

# Named volume: Docker-managed, persists across containers. Great for prod data.
docker volume create mydata                                             # pre-create a managed volume by name
docker run --rm -v mydata:/app/data alpine:3.19 sh -c 'echo hi > /app/data/file'   # writer container
docker run --rm -v mydata:/app/data alpine:3.19 cat /app/data/file      # a SECOND container reads it back
docker volume ls                                                        # list all named volumes
docker volume inspect mydata                                           # shows Mountpoint = where on disk it lives

# tmpfs: in-memory, gone on stop.
docker run --rm --tmpfs /scratch alpine:3.19 ls /scratch               # no source — backed by RAM, not disk
```

- The `-v` source decides the *type*: an **absolute path with a `/`** (`/tmp/data`) is a **bind
  mount** straight to that host directory; a **bare name** (`mydata`) is a **named volume** Docker
  manages for you. Same flag, two completely different behaviors — the leading `/` is the tell.
- `--rm` deletes each throwaway container the instant it exits; the *data* on a named volume or
  bind mount survives anyway, because it never lived in the container.
- The two `mydata` runs are the whole point: one container writes `file`, a **different**
  container reads it back. The data outlived the first container because it was on the volume.
- `--tmpfs /scratch` takes **no source** — it's backed by RAM, so it's fast and `/scratch`
  vanishes when the container stops.
- **Gotcha:** bind-mounting onto a path that already has files in the image *hides* the image's
  files behind the host directory for the life of the mount (they're not deleted, just shadowed).

**What you should see:** the second `mydata` run prints `hi` — written by the *first* container,
read by the *second*. `docker volume inspect mydata` shows a `Mountpoint` under
`/var/lib/docker/volumes/...` (that path is inside Docker's VM on Mac — see Practice #3). That's
your first proof that the bytes live outside any single container.

`--mount` is the verbose-but-explicit alternative to `-v`:

```bash
docker run --mount type=bind,src=/tmp/data,dst=/app/data alpine:3.19      # explicit: this is a bind mount
docker run --mount type=volume,src=mydata,dst=/app/data alpine:3.19       # explicit: this is a named volume
docker run --mount type=tmpfs,dst=/scratch,tmpfs-size=64m alpine:3.19     # tmpfs with a size cap
```

- `--mount` spells out `type=` instead of inferring it from whether the source has a `/`. Same
  result as `-v`, but unambiguous — preferred in scripts and Compose because a typo can't silently
  turn a volume into a bind mount.
- `tmpfs-size=64m` caps the in-memory mount so a runaway write can't eat all your RAM — a knob
  `-v --tmpfs` doesn't give you, which is one reason `--mount` exists.

## 2. When to use which

Three mount types, three jobs. The decision is really "how durable, and whose disk":

| Type | Use for |
|------|---------|
| bind mount | dev (live-edit code), config files |
| named volume | databases, user uploads, anything to keep |
| tmpfs | secrets in memory, scratch buffers |

- **bind mount** ties you to a *specific host path* — perfect for dev (edit code on the host, it
  appears in the container instantly), bad for portability (that path may not exist on another machine).
- **named volume** is Docker-managed and host-path-agnostic — the right default for data you must
  keep, because Docker owns *where* it lives and it survives `docker rm`.
- **tmpfs** never touches disk, so it's the move for secrets you don't want written down and for
  scratch space that should disappear.

## 3. Persistence demo with Postgres

This is the payoff: a real database whose data lives on a named volume, so the *container* is
disposable but the *data* isn't. Postgres stores everything under `/var/lib/postgresql/data` —
mount a volume there and the database outlives the container.

```bash
docker volume create pgdata                                  # the durable home for the DB files
docker run -d --name pg \
  -e POSTGRES_PASSWORD=secret \                              # postgres:16-alpine REQUIRES this to start
  -v pgdata:/var/lib/postgresql/data \                       # mount the volume at Postgres's data dir
  -p 5432:5432 \                                             # publish the DB port to the host
  postgres:16-alpine

# Wait a few seconds for Postgres to initialize (watch `docker logs pg` for
# "database system is ready to accept connections") or psql gives "could not connect".

# Insert something
docker exec -it pg psql -U postgres -c "CREATE TABLE t(x int); INSERT INTO t VALUES (42);"   # write via psql inside the container
docker stop pg && docker rm pg                               # DESTROY the container entirely

# Recreate the container — data survives
docker run -d --name pg -e POSTGRES_PASSWORD=secret -v pgdata:/var/lib/postgresql/data postgres:16-alpine   # same volume, brand-new container
docker exec -it pg psql -U postgres -c "SELECT * FROM t;"    # the row is still there
# 42
```

- `-d` runs Postgres detached so it keeps serving; `--name pg` lets the later `exec`/`stop`/`rm`
  target it by name.
- `-e POSTGRES_PASSWORD=secret` is **not optional** — the official `postgres` image refuses to
  initialize without a password (or `POSTGRES_HOST_AUTH_METHOD`). Omit it and the container exits.
- `-v pgdata:/var/lib/postgresql/data` is the load-bearing line: it redirects Postgres's data
  directory onto the named volume, so the DB files are written *outside* the container's writable layer.
- `docker exec -it pg psql -U postgres -c "..."` runs `psql` **inside the running container**
  (`-it` for an interactive TTY); `-c` runs one SQL statement and exits.
- `docker stop pg && docker rm pg` fully deletes the container — the writable layer is gone. The
  *only* reason `42` comes back is that the table lived on `pgdata`, not in the container.

**What you should see:** after recreating the container from scratch, `SELECT * FROM t;` returns
`42`. You destroyed the container and the data survived — that's the entire promise of a volume,
and it's the same idea a Kubernetes **PersistentVolumeClaim** delivers (Phase 03 lab-06): the
Pod can be rescheduled to another node, and the PVC — and the data on it — follow.

## 4. Permissions gotcha (the classic)

Bind-mounting a host dir into a container running as a non-root UID often hits permission errors:

```bash
docker run --rm -u 1000:1000 -v /tmp/foo:/data alpine:3.19 touch /data/x
# Permission denied if /tmp/foo is owned by root
```

- `-u 1000:1000` runs the process as UID 1000 / GID 1000 instead of root. A bind mount keeps the
  **host's** ownership and permissions on `/tmp/foo`, so if root owns it on the host, UID 1000
  inside the container can't write there — the kernel checks the *numeric* UID, and there's no
  user remapping by default.
- This bites named volumes too on first use, but Docker pre-`chown`s a *fresh* empty named volume
  to the container's user — bind mounts get no such help, which is why they're the classic trap.

Fixes: `chown` the host dir, or run a small startup container as root first to fix perms (in K8s this is an `initContainer` — covered in Phase 3).

**What you should see:** `touch: /data/x: Permission denied` when `/tmp/foo` is root-owned, and a
clean success once you `chown 1000:1000 /tmp/foo` on the host. That UID-vs-ownership mismatch is the
*same* problem Kubernetes solves with `securityContext.fsGroup` and `initContainers` in Phase 3 —
you're meeting it here in its simplest form.

## 5. Practice

1. Create a named volume, write a file, recreate the container, read the file. Confirm it persists.
2. Mount your current dir into a container as `/work` and edit a file from your host editor — confirm changes appear inside the container.
3. Find where named volumes live on disk. (Hint: in Docker Desktop's VM at `/var/lib/docker/volumes/...`. From your Mac you can't directly cd there.)

## Next

→ `lab-06-compose.md`: you've been wiring volumes (and earlier, networks) by hand, one `docker run`
flag at a time. **Compose** lets you declare the whole stack — services, networks, *and* volumes —
in one file, which is the on-ramp to writing Kubernetes manifests.
