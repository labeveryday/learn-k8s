# Lab 04: Namespaces and cgroups (How Containers Work)

**What you'll build:** a container, by hand, without Docker. You'll boot a
privileged Linux box, list the kernel namespaces your shell already lives in, then call
`unshare` to fork a bash with its own PID/mount/UTS/network namespaces (the thing Docker calls
a "container"), cap its memory with a cgroup you create by writing a single number to a file,
and watch the kernel OOM-kill a process at that cap. Once you've built one with raw syscalls,
a container reads as what it is: a normal
Linux process the kernel has been given a partial view of the system. After this, "the Pod shares a network
namespace" reads as a precise, mechanical statement instead of jargon.

> **The one idea:** there is no "container" in the kernel. A container is a process
> plus two kernel features: **namespaces** (it sees its own view of a resource) and **cgroups**
> (it can't exceed a budget). Every section below is you assembling those two pieces by hand,
> which is what `runc` does for you in milliseconds.

This is the lab that explains Docker. A container is a process with isolated
namespaces and capped cgroups.

## Setup

We need a privileged Linux env. Docker Desktop and Colima both run a Linux VM underneath; we run a privileged container in it:

```bash
docker run --rm -it --privileged --name nslab ubuntu:22.04 bash   # privileged = near-host kernel access
apt update && apt install -y util-linux procps iproute2 iputils-ping   # unshare/nsenter, ps, ip, ping
```

- `--privileged` gives the container near-host kernel access, which we need to create namespaces and write cgroup files by hand (a normal, unprivileged container can't). It's the opposite of the `--cap-add=NET_ADMIN` in lab-03, which granted one specific capability; this grants nearly all of them.
- `util-linux` is the package that ships `unshare` and `nsenter` (the two tools that do the work in this lab); `procps` ships `ps`, `iproute2` ships `ip`.

> **Gotcha:** `--privileged` is a blunt instrument; you'd never run a real workload this way. We use it only because we're poking the kernel directly. In production, runc does the same syscalls with the privileges already in place and then **drops** them (section 4, step 5).

## 1. What are namespaces?

A namespace gives a process its own view of a kernel resource. As of recent kernels, the namespace types are:

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

`man 7 namespaces` for the full reference. The mental model: a process belongs to one of each
type. Two processes in the same `net` namespace see the same interfaces; put one in a fresh
`net` namespace and it sees only `lo`. That's the entire isolation story.

See your shell's namespaces:

```bash
ls -l /proc/self/ns          # /proc/self = "me"; one symlink per namespace this process is in
# lrwxrwxrwx ... mnt -> mnt:[4026531840]
# lrwxrwxrwx ... net -> net:[4026531992]
# ...
```

**What you should see:** one symlink per namespace type (`mnt`, `net`, `pid`, ...), each
pointing at `<type>:[<id>]`. The number after `:` is the namespace **ID**, an inode.
Two processes in the same namespace see the same number. Note these IDs down: in section 2
you'll create new ones and in the practice you'll diff them against a second shell to see
which are shared and which are isolated.

## 2. Build a container by hand with `unshare`

`unshare` runs a command in **new** namespaces; it "unshares" the ones you name from the parent. Here we ask for four at once plus a fresh `/proc`:

```bash
# New PID, mount, UTS, network namespace + a fresh /proc mount:
unshare --pid --mount --uts --net --fork --mount-proc bash
```

Flag by flag, each `--<ns>` is one `CLONE_NEW*` the kernel will honor:

- `--pid`: new **PID namespace**, processes started here get fresh PIDs starting at 1.
- `--mount`: new **mount namespace**, mounts you make here don't leak to the parent (needed so the `/proc` remount below is private).
- `--uts`: new **UTS namespace**, `hostname` changes here are invisible outside.
- `--net`: new **network namespace**, an empty stack with only `lo`, and it starts down.
- `--fork`: `unshare` forks the command into a child instead of `exec`-ing in place. **Required** with `--pid`, because a process can't change its own PID namespace; only its children land in the new one.
- `--mount-proc`: remount `/proc` so it reflects the new PID namespace. Without this, `ps` would read the host's `/proc` and show every host process, breaking the illusion.

```bash
# Inside the new namespaces:
hostname mybox             # only visible here (UTS namespace)
ps -ef                     # tiny - bash is PID 1 (PID namespace + --mount-proc)
ip link                    # only `lo`, and it's down (NET namespace)
```

**What you should see:** `hostname` returns `mybox` here but the outer shell is unchanged;
`ps -ef` lists a handful of processes with your `bash` as **PID 1**; `ip link` shows only
`lo` in state `DOWN`. You've made a container: four kernel namespaces, no
Docker.

What's missing vs Docker?

- A root filesystem: you're still looking at the host's `/`. Docker uses `pivot_root` (a syscall that swaps `/` for a new directory) to give the container a different `/`. Try later with `debootstrap` (a tool that builds a minimal Debian/Ubuntu root tree) if curious.
- Cgroup limits: your hand-built box can still eat all the host's memory; section 3 fixes that.
- Capabilities and seccomp: capabilities are the fine-grained slices of root's power (e.g. NET_ADMIN); seccomp is a kernel filter that blocks syscalls a container shouldn't make. Docker drops most capabilities and applies a default seccomp profile; your `unshare` box drops nothing.

So the four namespaces are the isolation; the next three pieces (rootfs, cgroups, caps) are
what make it safe and self-contained. Type `exit` to leave the unshared shell when done.

## 3. Cgroups: capping resources

Cgroups (control groups) limit and account CPU, memory, IO, and more: the budget half of a
container (namespaces are the view half).

On cgroup v2 (the modern single-hierarchy version, default on current systems incl. Docker Desktop):

```bash
mount | grep cgroup           # confirm cgroup2 (look for "cgroup2 on /sys/fs/cgroup")
ls /sys/fs/cgroup             # the hierarchy - each subdir is a cgroup
cat /sys/fs/cgroup/cgroup.controllers   # which controllers exist here (cpu memory io ...)
```

**What you should see:** the `mount` line says `cgroup2`, `ls` shows a tree of directories, and
`cgroup.controllers` lists `cpu`, `memory`, `io`, etc.

> **Gotcha:** if `mount | grep cgroup` shows `cgroup` (not `cgroup2`), you're on the older
> cgroup **v1** and the file paths below differ (v1 splits each controller into its own
> hierarchy, e.g. `/sys/fs/cgroup/memory/...`). Upgrade your Docker/host or read the v1 docs.

Create a cgroup, cap memory at 50 MiB, run a process inside. **You configure a cgroup entirely
by writing plain text to files under `/sys/fs/cgroup`; that is the API, there's no special
tool** (the "everything is a file" idea from lab-01, made literal):

```bash
mkdir /sys/fs/cgroup/demo                      # a cgroup is a directory - creating it registers it
echo "+memory" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
                                               # delegate the memory controller DOWN to child cgroups
echo $((50*1024*1024)) > /sys/fs/cgroup/demo/memory.max   # the cap: 50 MiB, in bytes (52428800)
echo $$ > /sys/fs/cgroup/demo/cgroup.procs     # $$ = THIS shell's PID; move it in - children inherit the cap
```

Line by line:

- `mkdir .../demo`: making the directory is creating the cgroup. The kernel auto-populates it with control files (`memory.max`, `cgroup.procs`, ...).
- `echo "+memory" > .../cgroup.subtree_control`: a parent must explicitly **enable** (delegate) a controller before its children can use it. Without this, `memory.max` in `demo` may not be honored. The `2>/dev/null || true` swallows the error if it's already enabled.
- `echo <bytes> > demo/memory.max` is the cap itself. `memory.max` takes **bytes**, hence the `50*1024*1024` arithmetic. Write `max` instead of a number to remove the cap.
- `echo $$ > demo/cgroup.procs`: `$$` is the current shell's PID. Writing a PID into `cgroup.procs` **moves that process into the cgroup**; everything it spawns afterward inherits the budget. This is how runc places your container's PID 1.

```bash
# Now any heavy allocator started from this shell will OOM at 50 MiB.
apt install -y python3
python3 -c "x = bytearray(80*1024*1024)"      # allocate 80 MiB > 50 MiB cap → boom
```

**What you should see:** the shell prints `Killed`; the kernel OOM-killed the python process
when its allocation crossed the 50 MiB cap. This is the same mechanism behind a Kubernetes
Pod getting OOMKilled when it exceeds `resources.limits.memory` (Phase 3, lab-03): a `memory.max`
on a cgroup, nothing more.

> **Gotcha:** if it completes silently instead of printing `Killed`, your environment didn't
> enforce the cgroup (some privileged-container kernels don't propagate `memory.max` into a
> nested cgroup). That's fine, the concept stands. You can sanity-check with
> `cat /sys/fs/cgroup/demo/memory.max` to confirm the cap was written.

## 4. Mapping back to Docker

You just did, by hand, every step runc does. When you `docker run`:

1. Docker daemon calls the runtime (containerd → runc).
2. runc calls `clone(2)` (the syscall that creates a process) with the `CLONE_NEW*` flags; each asks the kernel for a fresh namespace: `CLONE_NEWPID` (new PID namespace), `CLONE_NEWNET` (new network), `CLONE_NEWNS` (new mounts), etc. **These are the same namespaces your `unshare --pid --net --mount` requested in section 2**; `unshare` is a CLI front-end to the same syscall.
3. Sets up cgroups for resource limits (`--memory`, `--cpus`): it writes `memory.max` / CPU files like your section 3, into a cgroup runc created for the container.
4. Pivots root into the image's filesystem (`pivot_root`, the rootfs your `unshare` box was missing).
5. Drops capabilities, applies seccomp profile: the safety layer `--privileged` skipped.
6. Execs the entrypoint as PID 1 in the new namespaces; your `bash` was PID 1 for the same reason.

That's it. There is no "container engine" in the kernel, only namespaces, cgroups, and a
chrooted filesystem. `docker run --memory=50m` and your `echo $((50*1024*1024)) > memory.max`
are the same instruction; Docker types it for you.

## 5. Practice

1. In the unshared shell, `ps -ef`. Why is your bash PID 1? (Hint: `--pid` + `--fork` put the child into a fresh PID namespace where numbering restarts.)
2. Open another `docker exec -it nslab bash`. Compare `ls -l /proc/self/ns` between the two: which namespace IDs differ, which match? (Both are in the same container, so all IDs should match; now compare against the section-2 `unshare` shell to see them diverge.)
3. `nsenter --target <PID> --pid --mount --net bash`: what does this do? `nsenter` is the inverse of `unshare`: instead of creating new namespaces it **joins existing ones** by entering the namespaces of `<PID>` (`--target`). This is the primitive behind `docker exec` and `kubectl exec`; both drop you into a running container's namespaces. (See `man nsenter`.)
4. Read `man 2 unshare`. Which flag corresponds to `--net`? (Answer: `CLONE_NEWNET`, confirming the CLI flag is a thin wrapper over the syscall constant from section 4, step 2.)

## What this means for Pods

When Phase 3 talks about "Pod network namespace shared between containers in a Pod," you now
know what that means: multiple processes, **same** `net` namespace (so they reach each
other over `localhost` and share one IP), but **different** `mnt`/`pid` namespaces (so each has
its own filesystem and process tree). That's why a sidecar can curl the main container on
`127.0.0.1` but can't see its files.

## Next

→ You can now read `/proc`, drive processes and signals, do networking, and, as of this
lab, explain a container down to the syscall. Two labs remain before Docker:
`lab-05-users-groups-and-sudo.md` moves you off the root account onto the identity model
real hosts use, and `lab-06-scripting-and-services.md` turns your one-liners into scripts
and puts them under a service manager.
