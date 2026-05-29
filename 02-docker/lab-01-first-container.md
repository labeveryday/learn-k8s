# Lab 01 — Your First Container, Properly

**What you'll build:** nothing you keep — but you'll *run* a container five ways (one-shot,
interactive, detached, resource-capped) and learn to read its state with the four commands you'll
reach for every day after this: `ps`, `logs`, `exec`, `inspect`. The point isn't `hello-world` or
nginx; it's wiring the **mental model from Phase 1 Lab 04** (a container is just a process in fresh
namespaces with a cgroup budget) to the **tooling** you'll actually drive. By the end, `docker run`
stops being magic — you can name every layer it touches and predict what each flag does.

> **The one idea (MIT):** after Phase 1 you know `docker run` is really
> `clone + unshare + pivot_root + exec` — a process in new namespaces under a cgroup. This lab is
> that same mechanism, now with a CLI and a daemon doing the syscalls *for* you. Every command
> below maps back to a kernel feature you built by hand.

## 1. Hello world — trace the request all the way down

```bash
docker run --rm hello-world          # one-shot: pull if needed, run, print, exit, auto-delete
```

- `--rm` deletes the container as soon as its process exits — otherwise it lingers in `Exited`
  state (visible under `docker ps -a`) consuming a name and a writable layer.

What just happened, in order — and note this is the exact `CLI → dockerd → containerd → runc`
chain from the Phase 2 README:

1. CLI sent a request to `dockerd` (over the local Unix socket — a file-like channel).
2. Daemon checked local image store — pulled `hello-world` from Docker Hub if missing.
3. Daemon asked containerd to create a container from the image.
4. containerd asked runc to clone a process with new namespaces.
5. The process printed and exited; `--rm` cleaned up the container.

**What you should see:** a "Hello from Docker!" message that itself describes those steps. If you
re-run it, step 2 is instant (image already local) — your first proof that the image is a cached
artifact, not a download.

## 2. Interactive shell — you're entering the namespaces

```bash
docker run --rm -it ubuntu:22.04 bash   # drop into a shell INSIDE the container's namespaces
# inside:
ls /                                     # this is the IMAGE's rootfs, not your host's /
exit                                     # process exits → container exits → --rm removes it
```

Flags:

- `-i` keep STDIN open (interactive) — without it, the shell gets EOF and dies immediately.
- `-t` allocate a TTY — gives you a real terminal (prompt, line editing, job control).
- `--rm` delete container on exit.
- `--name foo` give it a name (else Docker assigns a random one like `nostalgic_morse`).

The two are almost always used together as `-it` for any shell. Gotcha: drop `-t` for piped/
non-interactive use (`echo ... | docker run -i ...`), and drop `-i` for pure log-watching — a
stray `-t` in a script or CI breaks on the missing TTY.

**What you should see:** a root shell whose `ls /` is the *Ubuntu image's* filesystem (its own
`/bin`, `/etc`, ...), not your host's. That's the **mount namespace** from Lab 04 — same kernel
feature, now handed to you by `docker run` instead of `unshare --mount`.

## 3. Detached + observation — the four commands you'll live in

The `-alpine` suffix means the image is built on Alpine Linux, a tiny base image — smaller download. You'll see it on `redis:7-alpine`, `postgres:16-alpine`, etc. (image size is discussed in lab-02/03).

```bash
docker run -d --name web -p 8080:80 nginx:1.27-alpine   # detached: runs in background, prints ID
curl http://localhost:8080            # returns the "Welcome to nginx!" HTML page
docker ps                              # one row, STATUS = "Up ..."
docker logs web                        # stdout/stderr
docker logs -f web                     # follow
docker exec -it web sh                 # shell into running container
docker inspect web | less              # full state as JSON; paged — press q to quit
docker stop web && docker rm web       # stop sends SIGTERM, then rm deletes the container
```

What each flag/command is actually doing:

- `-d` (detached) returns your prompt immediately and prints the container ID — the opposite of
  `-it`. The process keeps running in the background; you observe it with the commands below.
- `-p 8080:80` = host port 8080 → container port 80. The container itself only knows about port 80.
  Format is always `HOST:CONTAINER`. Swapping them (`-p 80:8080`) is a classic "why is curl
  refused" bug.
- `docker logs` reads the container's stdout/stderr — which is *why* apps in containers should log
  to stdout, not a file. `-f` follows (like `tail -f`); Ctrl-C stops following without stopping the
  container.
- `docker exec -it web sh` runs a *second* process inside the already-running container's
  namespaces — the live-debugging equivalent of `nsenter` from Lab 04. (Alpine has `sh`, not `bash`.)
- `docker inspect` dumps the full container state as JSON: its IP, mounts, env, the cgroup limits,
  the exact runc config. This is the source of truth when behavior surprises you.

**What you should see:** `curl` returns nginx's welcome HTML; `docker ps` shows one row with
`STATUS = Up ...` and `0.0.0.0:8080->80/tcp` under PORTS — visual confirmation the port map is
live. `docker exec` drops you to a `#` prompt *inside* the running web container.

## 4. Lifecycle states

```
created → running → (paused) → stopped → removed
```

Useful:

```bash
docker ps -a                  # include stopped (default ps hides anything not running)
docker start <name>           # restart a stopped container (same filesystem, fresh process)
docker rm $(docker ps -aq)    # delete ALL stopped containers (careful)
```

- `docker ps` alone shows *only* running containers — the #1 "where did my container go?"
  confusion. `-a` reveals the `Exited` ones still holding their writable layer.
- `docker ps -aq` prints just the IDs (quiet) of all containers; the `$( )` feeds them to `rm`.
  This is a bulk delete — it removes every stopped container, so read the list first.

**What you should see:** after `docker stop web` in section 3, `docker ps` is empty but
`docker ps -a` still lists `web` as `Exited (0)` until you `rm` it — the lifecycle diagram, live.

## 5. Resource limits (preview of cgroups)

This is Lab 04's hand-built cgroup, now a one-flag affair — same kernel machinery, no writing to
`/sys/fs/cgroup` by hand:

```bash
docker run --rm -it --memory=128m --cpus=0.5 ubuntu:22.04 bash
# Inside, allocate too much RAM — get OOM-killed.
# Trigger it the same way as Phase 1 Lab 04:
#   python3 -c "x = bytearray(200*1024*1024)"   # over the 128m cap → Killed
```

- `--memory=128m` writes a cgroup v2 memory limit for you — exceed it and the kernel **OOM-kills**
  the offending process (you'll see `Killed`), exactly as in Lab 04.
- `--cpus=0.5` caps CPU to half a core. CPU over-limit **throttles** (slows), it doesn't kill —
  the same asymmetry you'll meet again as Kubernetes `limits` in Phase 3.

This sets cgroup limits — same machinery you saw in Phase 1.

**What you should see:** the `bytearray(200MB)` line dies with `Killed` because 200 MiB > the
128 MiB cap. That `Killed` is the kernel OOM-killer firing on the cgroup — proof the limit is
real, not advisory.

## 6. Practice

1. Run `redis:7-alpine` detached on port 6379. Redis is an in-memory key-value store; `redis-cli` is its CLI and `ping` is a liveness check. Connect with `docker exec ... redis-cli ping` — expect `PONG`.
2. Tail logs of a running container in one terminal while issuing commands in another.
3. Use `docker inspect` + `jq` to extract just the container's IP address.
   *(hint:* `docker inspect web | jq -r '.[0].NetworkSettings.IPAddress'`)
4. What's the difference between `docker stop` and `docker kill`? (See `--help`. Hint: signals.)
