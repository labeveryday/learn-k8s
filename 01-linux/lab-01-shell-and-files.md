# Lab 01: Shell and Files

**What you'll build:** a working mental model of the Linux box that every
container, Pod, and vLLM server in this curriculum runs on. You'll boot a throwaway Ubuntu
container, walk its single rooted filesystem, read process and kernel state straight out of
`/proc`, set permissions, wire commands together with pipes, and drive the `grep`/`awk`/`sed`/`xargs`
text tools. A container is a Linux userland the kernel isolates, which you build by hand in
lab-04. Reading `/proc`, parsing logs, and fixing file modes are the same skills you'll use the
day a Pod misbehaves.

> **The one idea:** the shell is the API of Unix. You don't click Linux, you type
> at it, and almost everything (processes, devices, cgroups) is exposed as a file you can
> `cat`. Every section below is you calling that API by hand.

## Setup

You don't need a Linux box; you need a Linux userland, and Docker hands you a disposable one:

```bash
docker run --rm -it --name linuxlab ubuntu:22.04 bash
apt update && apt install -y procps iproute2 iputils-ping curl less man-db tree file
```

The first line's flags recur in every Docker lab:

- `--rm` deletes the container's writable layer on exit, so you never accumulate dead
  containers. This lab is throwaway by design.
- `-it`: `-i` keeps stdin open, `-t` allocates a TTY. Together they give you an interactive
  shell; drop them and `bash` exits immediately with nothing to read.
- `--name linuxlab` is a stable handle so a second terminal can join with
  `docker exec -it linuxlab bash` instead of hunting for a container ID.
- `ubuntu:22.04 bash` is the image, then the process to run as PID 1 inside it. That `bash`
  replaces the image's default command; the container lives exactly as long as it does.

The second line installs the tools the rest of the lab leans on, since base `ubuntu:22.04` ships
almost nothing. `apt update` refreshes the package index first (without it, `install` 404s on
stale URLs); `-y` auto-confirms. `procps` gives you `ps`/`top`, `iproute2` gives `ss`/`ip`.

**What you should see:** a `root@<hash>:/#` prompt. That `<hash>` is the container ID and the
`#` means you're root inside the container, which is why `chown` below succeeds here
but needs `sudo` on a normal host.

## 1. Filesystem layout

Linux uses a single rooted tree. There is no `C:\` or `D:\`; every disk and virtual filesystem
is grafted onto one tree starting at `/`. Learn it:

```bash
ls /
# bin boot dev etc home lib media mnt opt proc root run sbin srv sys tmp usr var
```

Key directories you'll see all over Docker/K8s:

| Path | What lives there |
|------|------------------|
| `/etc` | system + app configuration |
| `/var/log` | logs |
| `/usr/bin`, `/usr/local/bin` | executables |
| `/proc` | virtual fs, kernel and process state |
| `/sys` | virtual fs, devices and cgroups |
| `/tmp` | scratch |

`/proc` and `/sys` are the load-bearing ones for the rest of this phase: they aren't files on
disk, they're the kernel presenting itself as a filesystem so you can read live process and
device state with `cat`. Kernel state is a file.

Try:

```bash
cat /proc/self/status        # info about the shell process
cat /proc/cpuinfo | head     # CPU info
cat /etc/os-release          # which Linux is this?
tree -L 1 /etc               # directory as a tree (the `tree` you installed)
file /bin/ls /etc/passwd     # what IS this file? (ELF binary vs ASCII text)
```

- `/proc/self/` is a symlink to whoever is reading it, here your shell. `status`
  shows its PID, memory, and (a preview of lab-04) its namespaces.
- `tree -L 1` limits depth to 1 level so you see `/etc`'s top contents, not its entire subtree.
- `file` reads each file's magic bytes, not its name, so it can tell you `/bin/ls` is an ELF
  executable while `/etc/passwd` is ASCII text. Extensions lie; `file` doesn't.

**What you should see:** `/proc/cpuinfo` lists the host's cores (a container shares the host
kernel and CPUs; it isn't a VM), and `os-release` says `Ubuntu 22.04`. You're reading kernel
state, not a config someone wrote.

## 2. Permissions

Every file has an owner, a group, and a set of mode bits the kernel checks on every access.

```bash
ls -l /etc/passwd
# -rw-r--r-- 1 root root 1234 Jan 1 12:00 /etc/passwd
#  ^^^ ^^^ ^^^
#  owner group others    each: read/write/execute
```

The leading 10 characters are the whole permission story: char 1 is the type (`-` file, `d`
dir, `l` symlink), then three triplets of `rwx` for owner, group, and others. `rw-r--r--`
means owner can read and write, everyone else read-only.

```bash
touch /tmp/secret /tmp/script.sh /tmp/foo  # create them first, or the next 3 lines error
chmod 600 /tmp/secret    # only owner can read/write
chmod +x /tmp/script.sh  # add execute for everyone
chown root:root /tmp/foo # change owner/group (needs root)
```

- `chmod 600` uses the octal form: `6` = `rw-` for owner, `0`/`0` = nothing for group/others.
- `chmod +x` uses the symbolic form: add the execute bit, leaving the rest untouched.
- `chown root:root` sets owner and group; it needs root, which you have inside this
  container. The same command on a host needs `sudo`.

Octal cheatsheet: `r=4 w=2 x=1`. So `755` = owner rwx, group/others rx.

> **Gotcha:** the execute bit on a file means "may run it"; on a directory it means "may
> traverse into it." A readable-but-not-executable dir lets you list names but not `cd` in.

### Links

Two ways to point one name at another:

```bash
echo data > /tmp/real
ln    /tmp/real /tmp/hard   # hard link - a second name for the SAME inode (same data on disk)
ln -s /tmp/real /tmp/soft   # soft link (symlink) - a small file that just stores a path
ls -l /tmp/hard /tmp/soft
# /tmp/hard  ... (looks like a normal file; link count on /tmp/real is now 2)
# /tmp/soft -> /tmp/real    (the `->` marks a symlink)
```

- `ln` with no flag makes a **hard link**: a second directory entry pointing at the same
  inode (the same bytes on disk). The kernel only frees the data when the last name is removed.
- `ln -s` makes a **symlink**: a tiny separate file that just stores the path `/tmp/real`.
  It's resolved every time, so it dangles if the target moves or is deleted.

A hard link survives deleting the original (the data lives until the last name is gone); a symlink breaks (dangles) if its target is removed. You'll meet symlinks constantly in `/proc` and container images.

**What you should see:** `ls -l` shows `/tmp/soft` with a `-> /tmp/real` arrow; `/tmp/hard`
looks like an ordinary file but the link count (the number after the mode bits) on the inode is
now `2`. Delete `/tmp/real` and `/tmp/hard` still reads `data`, but `cat /tmp/soft` errors.

## 3. Pipes and redirection

Pipes are central to Unix. `|` connects stdout of one command to stdin of the next, so you
compose small tools into one stream instead of writing a program.

```bash
ps aux | grep bash
cat /etc/passwd | cut -d: -f1 | sort | head
ls -l /usr/bin | wc -l                 # count files
```

- `ps aux | grep bash` is the canonical pipe: `ps` lists processes, `grep` keeps only the
  matching lines. (You'll often see your own `grep bash` in the output, since it's a process too.)
- `cut -d: -f1` splits each line on `:` and prints field 1 (the usernames in `/etc/passwd`);
  `sort | head` orders them and shows the first handful.
- `wc -l` counts lines, here roughly the number of executables in `/usr/bin`.

Redirection sends a stream to/from a *file* instead of another command:

```bash
echo "hello" > file.txt        # overwrite stdout to file
echo "world" >> file.txt       # append
command 2> errors.log          # redirect stderr
command > out.log 2>&1         # both
command < input.txt            # read stdin from file
```

The numbers are **file descriptors**: `1` is stdout (the default for `>`), `2` is stderr. So
`2>` captures only errors, and `2>&1` means "send fd 2 to wherever fd 1 is already going",
the idiom for capturing everything a command emits into one place (you'll use it constantly
on noisy logs).

> **Gotcha:** order matters. `> out.log 2>&1` works, but `2>&1 > out.log` sends stderr to the
> terminal because at that moment fd 1 still pointed there. Redirections are applied left to right.

## 4. The text-processing tools

You will use these every day with K8s logs, `kubectl get -o yaml`, and the like. Logs and YAML
are text streams; these tools are how you slice them without leaving the shell.

**grep** finds lines matching a pattern.

```bash
grep -i error /var/log/*.log     # case-insensitive
grep -v '^#' /etc/ssh/sshd_config 2>/dev/null | grep -v '^$'  # non-comment, non-empty
grep -r "TODO" .                 # recursive
```

- `-i` ignores case (`Error`, `ERROR`, `error` all match).
- `-v` inverts: keep lines that DON'T match. `^#` is a regex for "starts with `#`", so the
  chained `grep -v` strips comments then blank lines (`^$`), a clean-config one-liner. The
  `2>/dev/null` hides the "file not found" error if `sshd_config` isn't installed.
- `-r` recurses into subdirectories.

**awk** does column-aware processing.

```bash
# awk splits each line into fields $1, $2, ... by whitespace.
# In `ps aux` output: $1=user, $3=%CPU, $11=command.
ps aux | awk '{print $1, $11}'         # user and command
ps aux | awk '$3 > 1.0'                # processes using >1% CPU
# In `df -h` output: $5=use%, $6=mountpoint.
df -h | awk 'NR>1 {print $6, $5}'      # mountpoint and use% (NR>1 skips the header)
```

- `awk` auto-splits each line on whitespace into `$1`, `$2`, …, with no `cut -d` gymnastics.
- `'$3 > 1.0'` is a bare condition with no action, so awk prints the whole matching line:
  awk as a filter rather than a printer.
- `NR` is the current line number; `NR>1` is the standard idiom to skip a header row.

**sed** edits a stream.

```bash
echo "hello world" | sed 's/world/linux/'    # substitute
sed -i 's/foo/bar/g' file.txt                # in-place, all occurrences
```

- `s/old/new/` substitutes the first match per line; the trailing `g` makes it every match.
- `-i` edits the file in place (no `>` redirect needed). It's unforgiving, since
  there's no undo. Test without `-i` first.

**xargs** turns input into arguments.

```bash
find /tmp -name '*.log' | xargs rm        # delete found files
echo "1 2 3" | xargs -n1 echo             # one arg per invocation
```

- Many commands (`rm`, `kill`, `chmod`) take args, not stdin. `xargs` bridges the gap by
  turning a stream of names into command arguments.
- `-n1` runs the command once per input item instead of cramming them all into one call,
  handy when a command only accepts one argument at a time.

**What you should see:** each pipeline collapses a multi-step "open the file, find the column,
filter" task into one line. Build that fluency now: when a Pod's logs scroll past,
you'll `grep`/`awk` the signal out in seconds.

## 5. Practice

Do these without looking anything up:

1. List the 5 largest files under `/usr` by size.
2. Print the unique shells used in `/etc/passwd` (last colon field).
3. Find every file in `/etc` modified in the last day.
4. Replace every occurrence of "Linux" with "GNU/Linux" in `/etc/os-release`, but write it to `/tmp/os-release.new`, not in place.

Solutions live in `exercises.md`. Try first.

## Exit

```bash
exit         # leave the container; --rm deletes it
```

`exit` ends the PID-1 `bash`, which stops the container; the `--rm` from setup then wipes its
writable layer, so nothing lingers. That disposability is the feature: next lab you'll spin up a
fresh one without a thought.

## Next

→ `lab-02-processes-and-signals.md`: you read process state from `/proc` here; next you'll
control processes with `ps`, jobs, and the signals (`SIGTERM`/`SIGKILL`) that a container's PID
1 lives and dies by.
