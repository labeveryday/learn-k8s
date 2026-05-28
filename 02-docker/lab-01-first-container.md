# Lab 01 — Your First Container, Properly

## 1. Hello world

```bash
docker run --rm hello-world
```

What just happened, in order:

1. CLI sent a request to `dockerd`.
2. Daemon checked local image store — pulled `hello-world` from Docker Hub if missing.
3. Daemon asked containerd to create a container from the image.
4. containerd asked runc to clone a process with new namespaces.
5. The process printed and exited; `--rm` cleaned up the container.

## 2. Interactive shell

```bash
docker run --rm -it ubuntu:22.04 bash
# inside:
ls /
exit
```

Flags:

- `-i` keep STDIN open (interactive)
- `-t` allocate a TTY
- `--rm` delete container on exit
- `--name foo` give it a name (else random)

## 3. Detached + observation

```bash
docker run -d --name web -p 8080:80 nginx:1.27-alpine
curl http://localhost:8080            # served by nginx
docker ps                              # running containers
docker logs web                        # stdout/stderr
docker logs -f web                     # follow
docker exec -it web sh                 # shell into running container
docker inspect web | less              # full state as JSON
docker stop web && docker rm web
```

`-p 8080:80` = host port 8080 → container port 80. The container itself only knows about port 80.

## 4. Lifecycle states

```
created → running → (paused) → stopped → removed
```

Useful:

```bash
docker ps -a                  # include stopped
docker start <name>           # restart a stopped container
docker rm $(docker ps -aq)    # delete ALL stopped containers (careful)
```

## 5. Resource limits (preview of cgroups)

```bash
docker run --rm -it --memory=128m --cpus=0.5 ubuntu:22.04 bash
# Inside, allocate too much RAM — get OOM-killed.
```

This sets cgroup limits — same machinery you saw in Phase 1.

## 6. Practice

1. Run `redis:7-alpine` detached on port 6379. Connect with `docker exec ... redis-cli ping` — expect `PONG`.
2. Tail logs of a running container in one terminal while issuing commands in another.
3. Use `docker inspect` + `jq` to extract just the container's IP address.
   *(hint:* `docker inspect web | jq -r '.[0].NetworkSettings.IPAddress'`)
4. What's the difference between `docker stop` and `docker kill`? (See `--help`. Hint: signals.)
