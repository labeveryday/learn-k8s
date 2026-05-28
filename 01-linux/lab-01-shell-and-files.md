# Lab 01 — Shell and Files

## Setup

```bash
docker run --rm -it --name linuxlab ubuntu:22.04 bash
apt update && apt install -y procps iproute2 iputils-ping curl less man-db tree file
```

## 1. Filesystem layout

Linux uses a single rooted tree. Learn it:

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
| `/proc` | virtual fs — kernel and process state |
| `/sys` | virtual fs — devices and cgroups |
| `/tmp` | scratch |

Try:

```bash
cat /proc/self/status        # info about the shell process
cat /proc/cpuinfo | head     # CPU info
cat /etc/os-release          # which Linux is this?
```

## 2. Permissions

Every file has owner, group, and mode bits.

```bash
ls -l /etc/passwd
# -rw-r--r-- 1 root root 1234 Jan 1 12:00 /etc/passwd
#  ^^^ ^^^ ^^^
#  owner group others    each: read/write/execute

chmod 600 /tmp/secret    # only owner can read/write
chmod +x /tmp/script.sh  # add execute for everyone
chown root:root /tmp/foo # change owner/group (needs root)
```

Octal cheatsheet: `r=4 w=2 x=1`. So `755` = owner rwx, group/others rx.

## 3. Pipes and redirection

Pipes are the soul of Unix. `|` connects stdout of one command to stdin of the next.

```bash
ps aux | grep bash
cat /etc/passwd | cut -d: -f1 | sort | head
ls -l /usr/bin | wc -l                 # count files
```

Redirection:

```bash
echo "hello" > file.txt        # overwrite stdout to file
echo "world" >> file.txt       # append
command 2> errors.log          # redirect stderr
command > out.log 2>&1         # both
command < input.txt            # read stdin from file
```

## 4. The text-processing trio

You will use these every day with K8s logs, `kubectl get -o yaml`, etc.

**grep** — find lines matching a pattern.

```bash
grep -i error /var/log/*.log     # case-insensitive
grep -v '^#' /etc/ssh/sshd_config 2>/dev/null | grep -v '^$'  # non-comment, non-empty
grep -r "TODO" .                 # recursive
```

**awk** — column-aware processing.

```bash
ps aux | awk '{print $1, $11}'         # user and command
ps aux | awk '$3 > 1.0'                # processes using >1% CPU
df -h | awk 'NR>1 {print $6, $5}'      # mountpoint and use%
```

**sed** — stream edit.

```bash
echo "hello world" | sed 's/world/linux/'    # substitute
sed -i 's/foo/bar/g' file.txt                # in-place, all occurrences
```

**xargs** — turn input into arguments.

```bash
find /tmp -name '*.log' | xargs rm        # delete found files
echo "1 2 3" | xargs -n1 echo             # one arg per invocation
```

## 5. Practice

Do these without looking anything up:

1. List the 5 largest files under `/usr` by size.
2. Print the unique shells used in `/etc/passwd` (last colon field).
3. Find every file in `/etc` modified in the last day.
4. Replace every occurrence of "Linux" with "GNU/Linux" in `/etc/os-release` — but write it to `/tmp/os-release.new`, not in place.

Solutions live in `exercises.md`. Try first.

## Exit

```bash
exit         # leave the container; --rm deletes it
```
