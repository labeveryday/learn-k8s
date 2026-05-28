# Lab 05 — Volumes and Persistence

Containers are **ephemeral** by design. The writable layer dies with the container. For state, use volumes.

## 1. Three ways to mount

```bash
# Bind mount: host path → container path. Great for development.
docker run --rm -v /tmp/data:/app/data alpine:3.19 ls /app/data

# Named volume: Docker-managed, persists across containers. Great for prod data.
docker volume create mydata
docker run --rm -v mydata:/app/data alpine:3.19 sh -c 'echo hi > /app/data/file'
docker run --rm -v mydata:/app/data alpine:3.19 cat /app/data/file
docker volume ls
docker volume inspect mydata

# tmpfs: in-memory, gone on stop.
docker run --rm --tmpfs /scratch alpine:3.19 ls /scratch
```

`--mount` is the verbose-but-explicit alternative to `-v`:

```bash
docker run --mount type=bind,src=/tmp/data,dst=/app/data alpine:3.19
docker run --mount type=volume,src=mydata,dst=/app/data alpine:3.19
docker run --mount type=tmpfs,dst=/scratch,tmpfs-size=64m alpine:3.19
```

## 2. When to use which

| Type | Use for |
|------|---------|
| bind mount | dev (live-edit code), config files |
| named volume | databases, user uploads, anything to keep |
| tmpfs | secrets in memory, scratch buffers |

## 3. Persistence demo with Postgres

```bash
docker volume create pgdata
docker run -d --name pg \
  -e POSTGRES_PASSWORD=secret \
  -v pgdata:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgres:16-alpine

# Insert something
docker exec -it pg psql -U postgres -c "CREATE TABLE t(x int); INSERT INTO t VALUES (42);"
docker stop pg && docker rm pg

# Recreate the container — data survives
docker run -d --name pg -e POSTGRES_PASSWORD=secret -v pgdata:/var/lib/postgresql/data postgres:16-alpine
docker exec -it pg psql -U postgres -c "SELECT * FROM t;"
# 42
```

## 4. Permissions gotcha (the classic)

Bind-mounting a host dir into a container running as a non-root UID often hits permission errors:

```bash
docker run --rm -u 1000:1000 -v /tmp/foo:/data alpine:3.19 touch /data/x
# Permission denied if /tmp/foo is owned by root
```

Fixes: `chown` the host dir, or run a one-shot init container as root to fix perms (you'll see this pattern in K8s).

## 5. Practice

1. Create a named volume, write a file, recreate the container, read the file. Confirm it persists.
2. Mount your current dir into a container as `/work` and edit a file from your host editor — confirm changes appear inside the container.
3. Find where named volumes live on disk. (Hint: in Docker Desktop's VM at `/var/lib/docker/volumes/...`. From your Mac you can't directly cd there.)
