# Lab 02: Processes and Signals

**What you'll build:** a mental model of process control. You'll inspect live processes through
`/proc`, spawn and background jobs, then send signals (SIGTERM, SIGKILL, SIGSTOP…) and watch
how a process reacts. You'll understand the exact mechanism Kubernetes uses
to stop your containers: SIGTERM, then a grace period, then SIGKILL. So when a Pod dies
"unexpectedly" mid-request, you'll know it's almost always your app ignoring a signal, not
Kubernetes misbehaving. A container is a process. Understanding processes is
understanding containers.

> **The one idea:** there is no "container" in the kernel, only a
> process with some namespaces and cgroups wrapped around it (lab-04). Everything
> Kubernetes does to a container (start, stop, restart, OOM-kill) is the kernel doing it to a
> process. Learn the process and the container stops being opaque.

## Setup

```bash
docker run --rm -it --name linuxlab ubuntu:22.04 bash   # --rm = delete on exit; -it = interactive TTY
apt update && apt install -y procps psmisc htop          # procps→ps/top/pgrep/pkill, psmisc→killall, htop
```

- `procps` gives you `ps`, `top`, `free`, `kill`, `pgrep`, `pkill`; `psmisc` gives you `killall`/`pstree`;
  `htop` is the interactive monitor used in section 5. The base `ubuntu:22.04` image is
  stripped, so these aren't preinstalled, which is why you run the `apt install`.

## 1. What is a process?

A process is one running instance of a program. The kernel tracks every one in a table; `ps`
is a window onto that table, formatted four different ways:

```bash
ps                  # processes in your shell session (this terminal only)
ps aux              # ALL processes, BSD-style columns (USER %CPU %MEM ... COMMAND)
ps -ef              # ALL processes, SysV-style columns (UID PID PPID ... CMD)
ps -eo pid,ppid,cmd # custom columns - pick exactly the fields you want
```

- `aux` vs `-ef` show the same processes in two historical formats. Learn to read both,
  because tools and tutorials mix them freely. The dash matters: `ps aux` (BSD) takes no dash,
  `ps -ef` (SysV) does.
- `-eo` is the one you'll lean on in scripts: `-e` = every process, `-o` = output only these
  columns. `pid,ppid,cmd` shows the parent/child relationship at a glance.

**What you should see:** a small list from bare `ps` (just `bash` and `ps` itself), then a long
list from `aux`/`-ef`. In this container the list is tiny, the container story made
visible: a host runs hundreds of processes; your container runs a handful, all descended from
whatever you set as PID 1 (section 4).

Every process has:

- **PID**: process ID (unique, kernel-assigned)
- **PPID**: parent's PID
- **UID/GID**: owner
- **state**: running, sleeping, zombie, etc.
- **fds**: open file descriptors (`ls /proc/<PID>/fd`)

`/proc` is a virtual filesystem: not files on disk, but a live view the kernel synthesizes
on read. Every process gets a directory `/proc/<PID>/` exposing its internals. Inspect your own shell:

```bash
echo $$                    # $$ = the shell's own PID (a special shell variable)
ls -l /proc/$$/exe         # symlink to the actual executable behind this process (/usr/bin/bash)
ls /proc/$$/fd             # open file descriptors: 0=stdin 1=stdout 2=stderr, plus any opened
cat /proc/$$/status | head # the kernel's record: state, PPID, UIDs, memory, threads
```

- `$$` expands to your shell's PID, so `/proc/$$/...` is "this process's own kernel entry."
- `/proc/<PID>/exe` is a symlink the kernel maintains to the running binary; follow it to learn
  what a mystery PID is. `/proc/<PID>/fd` is how you'll later prove a container's
  stdout is wired to the Docker/Kubernetes log pipe.

**What you should see:** a number from `echo $$`, an `exe ->` symlink pointing at `bash`, file
descriptors `0 1 2` (and maybe more), and a `status` block whose `State:` line reads `S
(sleeping)`, since your shell is asleep waiting for you to type. That sleeping state is normal and
important: most processes spend their life asleep, not burning CPU.

## 2. Spawning processes

```bash
sleep 60 &           # & = run in background; control returns to you immediately
jobs                 # list this shell's background/suspended jobs with [N] job numbers
fg %1                # bring job 1 back to the FOREGROUND (it now owns the terminal)
ctrl-z               # suspend (STOP) the foreground process - it pauses, doesn't die
bg                   # resume the most-recently-suspended job in the BACKGROUND
```

- `&` is the key character: it tells the shell to **fork** a child, not wait for it, and hand
  you back the prompt. `jobs`/`fg`/`bg`/`ctrl-z` are the shell's job-control verbs for moving a
  process between foreground (owns your keyboard) and background (runs detached).
- `ctrl-z` is itself a signal (SIGTSTP), so section 3 is already appearing here:
  suspend and resume are signals (SIGSTOP/SIGCONT) underneath.

`&` forks; the parent (your shell) continues; the child runs separately.

**What you should see:** `[1] <pid>` after the `&`, that same job listed by `jobs`, and the
`sleep` toggling between foreground and background as you run `fg`/`ctrl-z`/`bg`. This fork-and-
continue is what a container runtime does to launch your app: fork a child, hand it a
PID, walk away.

## 3. Signals

Signals are how the kernel (or another process) **notifies a process of an event**, the only
out-of-band way to poke a running program. Each signal has a default action the kernel takes
unless the program installed a handler to catch it:

| Signal | Default action | Use |
|--------|---------------|-----|
| SIGTERM (15) | terminate | "please exit", graceful |
| SIGKILL (9)  | terminate | "die now", cannot be caught |
| SIGINT  (2)  | terminate | what `ctrl-c` sends |
| SIGHUP  (1)  | terminate | controlling terminal closed; many daemons treat as "reload config" |
| SIGSTOP (19) | stop | pause |
| SIGCONT (18) | continue | resume |

The two that matter most are a matched pair. **SIGTERM is a request a program can catch**
(to flush buffers, finish in-flight requests, close connections). **SIGKILL is uncatchable**:
the kernel destroys the process with no chance to clean up. Everything Kubernetes does to
stop a Pod is built on this pair.

```bash
sleep 1000 &
PID=$!                     # $! = PID of the most recent background job (capture it for later)
kill -TERM $PID            # send SIGTERM - graceful "please exit" (sleep dies; it has no handler)
kill -9 $PID               # send SIGKILL (9) - force; only needed if -TERM was ignored
kill -l                    # list ALL signals by number/name (kill is "send signal", not "destroy")
```

- `$!` captures the PID the way `$$` captured the shell's; without it you'd be hunting for the
  PID by hand. `kill` is poorly named: it doesn't kill, it sends a signal; the default signal
  it sends (with no flag) is SIGTERM.
- `kill -TERM` and `kill -9` (numeric form of SIGKILL) are the two you'll type most. Reach for
  `-9` only when `-TERM` is ignored; skipping straight to `-9` denies the process any cleanup.

**What you should see:** the first `kill -TERM` removes the `sleep` (confirm with `jobs` or
`ps`); the second `kill -9` then reports `No such process` because it's already gone. A real
app with a SIGTERM handler would instead start draining work before exiting, the difference
the next note is about.

**For containers:** Kubernetes sends SIGTERM, waits `terminationGracePeriodSeconds`, then SIGKILL. If your app ignores SIGTERM, you'll get hard-killed mid-request. Test this!

## 4. PID 1: the init problem

In a normal Linux system, PID 1 is `init` (`systemd` on most distros). It reaps zombie children. In a container, PID 1 is *your app*. If your app forks children and doesn't reap them, you get zombies.

Why "reap"? When a child exits, the kernel keeps a stub entry (a **zombie**) holding its exit
status until the parent calls `wait()` to collect it. On a real system PID 1 adopts and reaps
orphans automatically. In a container your app is PID 1, so if it spawns children and never
`wait()`s, the zombies pile up. PID 1 is special in one more way: **the kernel does not
apply default signal actions to it**, so a PID 1 with no SIGTERM handler ignores SIGTERM
entirely and only dies on the final SIGKILL. That's the reason a container takes 30
seconds to stop.

Demo:

```bash
# Inside the ubuntu container
ps -ef | head -3          # -ef = all processes; head -3 = header + first two rows
# PID 1 is bash (or whatever you ran)
```

- `ps -ef | head -3` prints the column header plus the lowest PIDs, and in a container the
  lowest PID is `1`, which is whatever command you handed `docker run` (here, `bash`). On the
  host PID 1 would be `systemd`; the contrast is the lesson.

**What you should see:** PID `1` is `bash` (your container's entrypoint), with PPID `0` (the
kernel). There is no `systemd`, no `init`; your process sits where init normally sits, which is
why the zombie-reaping and signal-default caveats above apply to it.

In production containers you often use a small init like `tini` or `dumb-init`, one-file programs that sit at PID 1, forward signals, and reap zombie children on your app's behalf. Note this for Phase 2.

## 5. Resource view

```bash
top              # interactive process monitor - sorted by CPU, refreshes live (q to quit)
htop             # nicer color version with per-core bars (the one you apt-installed)
free -h          # memory: total/used/free/buffers, -h = human units (Mi/Gi)
uptime           # how long up + the three load averages
vmstat 1 5       # system-wide CPU/IO/memory: sample every 1s, 5 times, then stop
```

- `top`/`htop` are interactive (press `q` to leave); `free`/`uptime`/`vmstat` print once and
  return. `vmstat 1 5` means "interval 1 second, 5 samples", the standard way to watch a
  trend without staring at `top`.
- `free -h` is the fast answer to "how much memory is left?", and in a container the numbers it
  shows are usually the host's memory, not your cgroup limit (a classic gotcha that lab-04
  fixes by reading cgroup files directly).

**What you should see:** in `top`/`htop`, a near-idle list (your shell + the monitor); in
`free -h`, the host's memory totals; in `uptime`, three load numbers. Compare those load
numbers to the rule below.

The classic Linux load average: jobs in run queue + uninterruptible sleep, averaged 1/5/15 min. Below #cores = healthy.

## 6. Practice

1. Start `sleep 100` in the background. Find its PID via `pgrep`. Kill it gracefully, then forcefully.
2. Run `cat` (with no args). What state does it enter? (Hint: `ps -o pid,stat,cmd`.)
3. Open `/proc/1/cmdline`. What's PID 1 in this container?
4. Write a one-liner to find the top 3 processes by RSS (Resident Set Size, the physical RAM a process holds).

## Bonus: `strace`

`strace` prints every **system call** a program makes: the actual requests it sends the kernel.
It answers "what is this thing doing?":

```bash
apt install -y strace
strace -e trace=openat ls / 2>&1 | head -20   # -e trace=openat = only show file-open syscalls
```

- `-e trace=openat` filters to the `openat` syscall (opening a file); without the filter
  you'd drown in hundreds of calls. `2>&1` merges strace's stderr (where it writes its trace)
  into stdout so `head` can page it.

**What you should see:** a stream of `openat(...) = <fd>` lines, one per shared library, locale
file, and directory `ls` touches to list `/`. Even a trivial command is
a flurry of kernel requests, and `strace` is how you watch a container talk to the host kernel.

Every file `ls` opens to do its job shows up here. This is what a command is doing underneath.

## Next

→ `lab-03-networking.md`: a process that listens on a port becomes a server. You'll trace
sockets, ports, and DNS, the layer Kubernetes Services are built on.
