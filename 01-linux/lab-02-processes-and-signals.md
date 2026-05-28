# Lab 02 — Processes and Signals

A container is, fundamentally, a process. Understanding processes IS understanding containers.

## Setup

```bash
docker run --rm -it --name linuxlab ubuntu:22.04 bash
apt update && apt install -y procps psmisc htop
```

## 1. What is a process?

```bash
ps                  # processes in your shell session
ps aux              # all processes, BSD-style
ps -ef              # all processes, sysv-style
ps -eo pid,ppid,cmd # custom columns
```

Every process has:

- **PID** — process ID (unique, kernel-assigned)
- **PPID** — parent's PID
- **UID/GID** — owner
- **state** — running, sleeping, zombie, etc.
- **fds** — open file descriptors (`ls /proc/<PID>/fd`)

Try:

```bash
echo $$                    # shell's own PID
ls -l /proc/$$/exe         # path to the executable
ls /proc/$$/fd             # open file descriptors
cat /proc/$$/status | head # kernel's view of this process
```

## 2. Spawning processes

```bash
sleep 60 &           # run in background
jobs                 # list shell jobs
fg %1                # bring job 1 to foreground
ctrl-z               # suspend current process
bg                   # resume in background
```

`&` forks; the parent (your shell) continues; the child runs separately.

## 3. Signals

Signals are how processes are notified of events.

| Signal | Default action | Use |
|--------|---------------|-----|
| SIGTERM (15) | terminate | "please exit" — *graceful* |
| SIGKILL (9)  | terminate | "die now" — *cannot be caught* |
| SIGINT  (2)  | terminate | what `ctrl-c` sends |
| SIGHUP  (1)  | terminate | controlling terminal closed; many daemons treat as "reload config" |
| SIGSTOP (19) | stop | pause |
| SIGCONT (18) | continue | resume |

```bash
sleep 1000 &
PID=$!
kill -TERM $PID            # graceful
kill -9 $PID               # force
kill -l                    # list all signals
```

**Why this matters for containers:** Kubernetes sends SIGTERM, waits `terminationGracePeriodSeconds`, then SIGKILL. If your app ignores SIGTERM, you'll get hard-killed mid-request. Test this!

## 4. PID 1: the init problem

In a normal Linux system, PID 1 is `init` (`systemd` on most distros). It reaps zombie children. In a container, PID 1 is *your app*. If your app forks children and doesn't reap them, you get zombies.

Demo:

```bash
# Inside the ubuntu container
ps -ef | head -3
# PID 1 is bash (or whatever you ran)
```

In production containers you often use a tiny init like `tini` or `dumb-init`. Note this for Phase 2.

## 5. Resource view

```bash
top              # interactive, classic
htop             # nicer, if installed
free -h          # memory
uptime           # load averages
vmstat 1 5       # CPU/IO/memory snapshots
```

The classic Linux load average: jobs in run queue + uninterruptible sleep, averaged 1/5/15 min. Below #cores = healthy.

## 6. Practice

1. Start `sleep 100` in the background. Find its PID via `pgrep`. Kill it gracefully, then forcefully.
2. Run `cat` (with no args). What state does it enter? (Hint: `ps -o pid,stat,cmd`.)
3. Open `/proc/1/cmdline` — what's PID 1 in this container?
4. Write a one-liner to find the top 3 RSS-memory processes.

## Bonus: `strace`

```bash
apt install -y strace
strace -e trace=openat ls / 2>&1 | head -20
```

Every file `ls` opens to do its job. This is what every command is *really* doing.
