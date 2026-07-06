# Phase 1: Linux Foundations

**Time budget:** ~10–15% of total. Goal: be effective in a Linux shell and understand the kernel features that make containers possible.

## Why this phase exists

Containers are Linux. Kubernetes orchestrates Linux. vLLM runs on Linux. macOS gives you a BSD-flavored userland and no Linux kernel; every container and Kubernetes tool on a Mac runs Linux inside a hidden VM. If you don't know Linux, you're flying blind the moment something breaks.

## What you'll be able to do after this phase

- Navigate the filesystem, manipulate processes, manage permissions.
- Read and write shell pipelines using `grep`, `awk`, `sed`, `xargs`.
- Reason about TCP/UDP sockets, ports, and DNS from the command line.
- Explain what a Linux namespace and cgroup are, and create one by hand.

## Working environment

You don't have a Linux box; you have Docker. Every lab runs in a container:

```bash
docker run --rm -it --name linuxlab ubuntu:22.04 bash
# inside the container, install what each lab needs:
apt update && apt install -y procps iproute2 iputils-ping net-tools curl less man-db
```

Keep the container running in one terminal; open another with `docker exec -it linuxlab bash` for parallel work.

## Reading list (offline-friendly)

The base `ubuntu:22.04` image ships almost no man pages. To make the section 2/7 kernel/syscall pages below work, install them first (otherwise you'll get `No manual entry for namespaces in section 7`):

```bash
apt install -y manpages manpages-dev
```

- `man bash`: sections on Quoting, Expansion, Pipelines.
- `man 7 signal`: signals.
- `man 7 namespaces`: the core mechanism behind containers.
- `man 7 cgroups`: resource isolation.
- *The Linux Programming Interface* (Kerrisk): chapters on processes, files, sockets, namespaces. (Optional, deep.)

## Labs

1. `lab-01-shell-and-files.md`: filesystem, permissions, pipes
2. `lab-02-processes-and-signals.md`: ps, kill, signals, jobs
3. `lab-03-networking.md`: sockets, ports, DNS, curl
4. `lab-04-namespaces-and-cgroups.md`: build a container by hand
5. `lab-05-users-groups-and-sudo.md`: users, groups, sudo, env vars, packages
6. `lab-06-scripting-and-services.md`: shell scripts, systemd services, journalctl
7. `exercises.md`: drills + self-check

## Notes

> Run `strace -f -e trace=openat ls /` once and watch how much work a single `ls` does.
>
> The shell is the API of Unix. Treat it as one, not as a chore.
