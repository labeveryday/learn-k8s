# Lab 04 — Namespaces and cgroups (How Containers Actually Work)

This is the lab that demystifies Docker. **A container is just a process with isolated namespaces and capped cgroups.**

## Setup

We need a privileged Linux env. Docker Desktop / Colima both run a Linux VM under the hood — we run a privileged container in it:

```bash
docker run --rm -it --privileged --name nslab ubuntu:22.04 bash
apt update && apt install -y util-linux procps iproute2 iputils-ping
```

## 1. What are namespaces?

A *namespace* gives a process its own view of a kernel resource. As of recent kernels, the namespace types are:

| Namespace | Isolates |
|-----------|----------|
| `mnt`     | filesystem mounts |
| `pid`     | process IDs |
| `net`     | network stack (interfaces, routes, sockets) |
| `uts`     | hostname |
| `ipc`     | SysV IPC, POSIX message queues |
| `user`    | UIDs/GIDs |
| `cgroup`  | cgroup root |
| `time`    | system clock (newer) |

`man 7 namespaces` for the full reference.

See your shell's namespaces:

```bash
ls -l /proc/self/ns
# lrwxrwxrwx ... mnt -> mnt:[4026531840]
# lrwxrwxrwx ... net -> net:[4026531992]
# ...
```

The number after `:` is the namespace ID. Two processes in the same namespace see the same number.

## 2. Build a "container" by hand with `unshare`

```bash
# New PID, mount, UTS, network namespace + a fresh /proc mount:
unshare --pid --mount --uts --net --fork --mount-proc bash

# Inside the new namespaces:
hostname mybox             # only visible here
ps -ef                     # tiny — bash is PID 1
ip link                    # only `lo`, and it's down
```

Congratulations, you've made a container.

What's missing vs Docker?

- A *root filesystem*: Docker uses `pivot_root` to give the container a different `/`. Try later with `debootstrap` if curious.
- *Cgroups limits*: see below.
- *Capabilities & seccomp*: kernel-level capability dropping.

## 3. Cgroups — capping resources

Cgroups (control groups) limit/account CPU, memory, IO, etc.

On cgroup v2 (modern systems incl. Docker Desktop):

```bash
mount | grep cgroup           # confirm cgroup2
ls /sys/fs/cgroup             # the hierarchy
cat /sys/fs/cgroup/cgroup.controllers
```

Create a cgroup, cap memory at 50 MiB, run a process inside:

```bash
mkdir /sys/fs/cgroup/demo
echo "+memory" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
echo $((50*1024*1024)) > /sys/fs/cgroup/demo/memory.max
echo $$ > /sys/fs/cgroup/demo/cgroup.procs    # add this shell

# Now any heavy allocator started from this shell will OOM at 50 MiB.
apt install -y python3
python3 -c "x = bytearray(80*1024*1024)"      # boom
```

(If the privileged-container kernel doesn't allow this, no problem — concept noted.)

## 4. Mapping back to Docker

When you `docker run`:

1. Docker daemon calls the runtime (containerd → runc).
2. runc calls `clone(2)` with `CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWNS | ...` to create namespaces.
3. Sets up cgroups for resource limits (`--memory`, `--cpus`).
4. Pivots root into the image's filesystem.
5. Drops capabilities, applies seccomp profile.
6. Execs the entrypoint as PID 1 in the new namespaces.

That's it. There is no "container engine" in the kernel — only namespaces, cgroups, and a chrooted filesystem.

## 5. Practice

1. In the unshared shell, `ps -ef`. Why is your bash PID 1?
2. Open another `docker exec -it nslab bash`. Compare `ls -l /proc/self/ns` between the two — which namespace IDs differ, which match?
3. `nsenter --target <PID> --pid --mount --net bash` — what does this do? (See `man nsenter`.)
4. Read `man 2 unshare`. Which flag corresponds to `--net`?

## Why this matters going forward

When Phase 3 talks about "Pod network namespace shared between containers in a Pod," you now know exactly what that means: multiple processes, same `net` namespace, different `mnt`/`pid` namespaces. Magic gone.
