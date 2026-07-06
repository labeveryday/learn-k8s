# Lab 06: Scripting and Services

**What you'll build:** a real shell script and a real service. First you learn the bash
constructs that turn typed commands into programs: variables, quoting, exit codes, tests,
loops, functions, and the `set -euo pipefail` line that makes scripts fail loudly instead of
lying. You build a ~25-line health-check script that polls an HTTP endpoint and exits nonzero
when it's down, the same pattern Kubernetes probes run against your containers in phase 03,
lab-08. Then you move to a machine with a working systemd, wrap that script in a unit file,
and watch systemd restart it when you kill it. `Restart=on-failure` is a kubelet in miniature,
and a unit file is your first declare-the-desired-state manifest.

> **The one idea:** everything you've typed for five labs is a program waiting to be written
> down. A script is the shell with the human removed; a service is a script with a supervisor
> attached. Kubernetes is that same pair of moves, scaled out: containers instead of scripts,
> the kubelet instead of systemd.

## Setup (part 1: scripting)

Sections 1 through 6 run in the familiar throwaway container:

```bash
docker run --rm -it --name scriptlab ubuntu:22.04 bash
apt update && apt install -y curl python3 nano procps
```

Same `docker run` flags as every lab: `--rm` deletes the container on exit, `-it` gives you an
interactive TTY, `--name scriptlab` is the handle for a second terminal, `ubuntu:22.04 bash`
runs bash as PID 1. The packages: `curl` performs the health checks, `python3` provides a
one-line web server to check against, `nano` edits the scripts (use `vim` if you prefer and
install it instead), `procps` gives you `ps` and `pkill`. `apt update` refreshes the package
index first and `-y` auto-confirms, as covered in lab-05 section 7.

Section 7 onward needs systemd, which this container does not have. That gets its own setup
when you reach it.

## 1. Your first script

A script is a text file of commands plus two pieces of plumbing: a shebang and an execute bit.

```bash
mkdir -p /opt/lab && cd /opt/lab
cat > hello.sh <<'EOF'
#!/bin/bash
echo "hello from $(hostname)"
EOF
chmod +x hello.sh
./hello.sh
```

- The `cat > file <<'EOF' ... EOF` pattern is a heredoc: everything until the `EOF` line goes
  into the file, and the quotes around `'EOF'` stop the shell from expanding `$(hostname)` at
  write time; you want it expanded at *run* time. You can use `nano hello.sh` instead any time
  a lab writes a file this way.
- `#!/bin/bash` is the shebang. When the kernel executes the file, it reads these first bytes
  and runs `/bin/bash hello.sh` for you. Without it, whatever shell happens to invoke the file
  interprets it, and bash-specific syntax breaks under `sh`. Every script gets a shebang; no
  exceptions.
- `chmod +x` adds the execute permission from lab-01. A script without it is data, and running
  it gets you `Permission denied` even though you own it.
- `./hello.sh` runs it. The `./` is required because of lab-05's `PATH` rule: the current
  directory is not on the search list (deliberately, so an attacker's `ls` in `/tmp` can't
  shadow the real one), so you give an explicit path.

> **What you should see:** `hello from <container-id>`. You wrote a program. The rest of the
> lab makes it a useful one.

## 2. Variables, quoting, substitution

Three rules cover most bash variable bugs.

**Rule 1: no spaces around `=`, and expand with `$`.**

```bash
name="vllm-server"
echo "$name"
```

`name = "vllm-server"` with spaces runs a command called `name` with two arguments; the error
`name: command not found` is the tell.

**Rule 2: quote every expansion.** Unquoted variables get split on whitespace after expansion:

```bash
f="my file.txt"
touch "$f"
ls -l $f      # ls: cannot access 'my': ...  cannot access 'file.txt': ...
ls -l "$f"    # works: one argument, space and all
rm "$f"
```

The unquoted `$f` became two arguments before `ls` ran. In a script that later does `rm $f`,
that same split deletes the wrong things. Write `"$var"` by reflex and this whole bug class
disappears.

**Rule 3: use braces when text touches the variable name.**

```bash
echo "backing up to $name_backup"     # empty: bash looked up a variable called name_backup
echo "backing up to ${name}_backup"   # backing up to vllm-server_backup
```

`${var}` and `$var` are the same expansion; the braces mark where the name ends.

**Command substitution** runs a command and pastes its output into the line:

```bash
now=$(date '+%H:%M:%S')
kernel=$(uname -r)
echo "[$now] running on kernel $kernel"
```

`$(...)` is how scripts capture command output into variables; you'll use it in the health
check to capture curl's status code. (Old scripts use backticks for the same thing; `$(...)`
nests properly, so prefer it.)

## 3. Exit codes, if, and test

Every process exits with a status code: `0` means success, anything from 1 to 255 means some
flavor of failure. The shell stores the last command's code in `$?`:

```bash
ls / > /dev/null;      echo $?    # 0
ls /no-such-dir 2>/dev/null; echo $?    # 2
grep -q root /etc/passwd;    echo $?    # 0  (found)
grep -q zzzz /etc/passwd;    echo $?    # 1  (not found)
```

`grep -q` is quiet mode: print nothing, communicate only through the exit code, which makes
grep usable as a yes/no question. Exit codes are the entire interface between your script and
its supervisor: Kubernetes decides whether a probe passed, and systemd decides whether to
restart a service, by reading exactly this number.

`if` branches on an exit code, and the `[ ... ]` you see everywhere is a *command* whose exit
code answers a question:

```bash
if [ -f /etc/passwd ]; then
    echo "passwd exists"
fi

if [ "$(id -u)" -eq 0 ]; then
    echo "running as root"
else
    echo "running as UID $(id -u)"
fi
```

- `[` is a program (see for yourself: `ls -l /usr/bin/[`), also known as `test`. That's why
  the spaces are mandatory: `[-f` is a command named `[-f`, which doesn't exist.
- Useful tests: `-f file` (regular file exists), `-d dir` (directory exists), `-z "$s"`
  (string empty), `"$a" = "$b"` (string equality), `-eq -ne -lt -le` (numeric comparisons).
- `if` doesn't require brackets at all; it runs any command and branches on its code:
  `if grep -q root /etc/passwd; then ...` is idiomatic and you'll use exactly this form with
  a function in the health check.

## 4. Loops and functions

**for** iterates over a list of words, most often a glob:

```bash
for f in /etc/*.conf; do
    echo "== $f"
done
```

The shell expands `/etc/*.conf` into filenames before the loop starts; `for` walks them one
per iteration.

**while** repeats as long as a command succeeds, which makes it the natural shape for
retry-with-a-limit:

```bash
n=1
while [ "$n" -le 3 ]; do
    echo "attempt $n"
    n=$((n + 1))
    sleep 1
done
```

`$(( ... ))` is arithmetic expansion, the shell's built-in integer math; `n=$((n + 1))` is the
counter increment you'll reuse in the health check.

**Functions** name a block of commands:

```bash
log() {
    echo "[$(date '+%H:%M:%S')] $*"
}
log "server starting"
log "config loaded"
```

`$*` expands to all the arguments the function was called with, so `log` becomes a
timestamped `echo`. A function's exit code is the code of its last command, which means a
function can serve as an `if` condition, a trick the health check leans on.

## 5. Fail loudly: set -euo pipefail

Bash's default behavior is to keep going after a failed command. For an interactive shell
that's right; for a script it's a disaster, because line 12 failing silently means line 13
runs against a state that doesn't exist. Watch a script lie to you:

```bash
cat > fragile.sh <<'EOF'
#!/bin/bash
cp /etc/no-such-file /tmp/backup.conf
echo "backup finished OK"
EOF
chmod +x fragile.sh
./fragile.sh
echo "script exit code: $?"
```

> **What you should see:**
>
> ```
> cp: cannot stat '/etc/no-such-file': No such file or directory
> backup finished OK
> script exit code: 0
> ```
>
> The copy failed, the script announced success, and it exited 0 so every supervisor,
> pipeline, and CI job upstream believes it. This exact script pattern has "completed" real
> backups that backed up nothing.

Three shell options fix the three ways this goes wrong:

- `set -e`: exit immediately when a command fails, instead of continuing.
- `set -u`: treat expanding an unset variable as an error. Without it, a typo like
  `rm -rf "$STAGIN_DIR/"*` expands the misspelled variable to empty and the `rm` runs against
  `/`. With `-u` the script dies at the typo.
- `set -o pipefail`: make a pipeline's exit code the last *failing* command's code, instead
  of the last command's. Compare: `false | true; echo $?` prints `0` normally, `1` under
  pipefail. Without it, `curl ... | grep ok` reports success when curl failed, because grep
  ran fine on empty input.

They combine into one line, and it goes directly under the shebang of every script you write
from now on:

```bash
set -euo pipefail
```

One nuance you need before the next section: `set -e` does not fire for commands whose
failure you *test*. A failing command inside an `if` condition, or followed by `||`, doesn't
kill the script; that's what lets the health check probe a dead endpoint without dying on the
spot.

## 6. The health-check script

Time to assemble everything into one real script. First give it something to check. Start a
web server in the background:

```bash
cd /opt/lab
python3 -m http.server 8080 >/dev/null 2>&1 &
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/
```

- `python3 -m http.server 8080` serves the current directory over HTTP on port 8080; the
  redirect throws away its request log and `&` backgrounds it (lab-02's job control).
- The curl flags, which the script reuses: `-s` silences the progress meter, `-o /dev/null`
  discards the response body, `-w '%{http_code}'` writes the HTTP status code to stdout after
  the transfer. The combination turns curl into a function that maps URL to status code.

> **What you should see:** `200`. The endpoint is up.

Now the script:

```bash
cat > healthcheck.sh <<'EOF'
#!/bin/bash
set -euo pipefail

URL="${1:-http://localhost:8080/}"
MAX_TRIES="${2:-5}"
SLEEP_SECONDS=2

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

check_once() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL") || code="000"
    log "GET $URL -> $code"
    [ "$code" = "200" ]
}

tries=1
while [ "$tries" -le "$MAX_TRIES" ]; do
    if check_once; then
        log "healthy after $tries check(s)"
        exit 0
    fi
    tries=$((tries + 1))
    sleep "$SLEEP_SECONDS"
done

log "UNHEALTHY: gave up after $MAX_TRIES failed checks" >&2
exit 1
EOF
chmod +x healthcheck.sh
```

Walk it top to bottom; every line is a section of this lab:

- `"${1:-http://localhost:8080/}"`: `$1` is the script's first argument, and `${var:-default}`
  substitutes the default when the argument wasn't given. So the script works bare and also
  takes a URL and a retry count. The quoting is rule 2 from section 2.
- `check_once` captures curl's status code with command substitution. `--max-time 3` caps the
  whole request at three seconds, because a health check that hangs is worse than one that
  fails. The `|| code="000"` handles curl itself failing (connection refused, DNS, timeout):
  an assignment's exit status is the substituted command's, so under `set -e` an unguarded
  failing curl would kill the script; the `||` prevents that and pins `000` as the "no answer
  at all" code.
- The function's last command, `[ "$code" = "200" ]`, sets its exit code, so
  `if check_once; then` reads as English and branches on the probe.
- The while loop is section 4's retry counter. Success exits `0` immediately; exhausting the
  budget logs to stderr (`>&2`, lab-01 redirection) and exits `1`.

Run it against a healthy endpoint, then kill the server and run it again:

```bash
./healthcheck.sh
echo "exit: $?"

pkill -f http.server        # stop the python server (matches its command line)
./healthcheck.sh
echo "exit: $?"
```

> **What you should see:** first run: one `-> 200` line, `healthy after 1 check(s)`, exit `0`.
> Second run: five `-> 000` lines two seconds apart, the UNHEALTHY line, exit `1`. The script
> tells the truth in both directions, in its output for humans and in its exit code for
> machines.

This is a Kubernetes probe, hand-built. In phase 03, lab-08 you'll write
`livenessProbe: httpGet: path: / port: 8080` in a Pod spec, and the kubelet will run this
loop for you: request, timeout, retry budget (`failureThreshold`), act on the verdict. The
YAML will hold zero mystery because you've written its implementation.

Leave the container (`exit`); part 2 happens on a real host.

## 7. A real host with systemd (two paths)

Lab-02 showed you why the container can't do this part: PID 1 in `scriptlab` was your bash,
and there was no init system underneath it. `systemctl` in that container answers
`System has not been booted with systemd as init system (PID 1)`, which is an honest error.
Services need a supervisor, so you need a machine where PID 1 *is* systemd. Two ways to get
one; pick whichever matches your setup.

**Path A (preferred on this course's Mac + Colima setup): the Colima VM.** Docker on your Mac
has been running inside a Linux VM all along, and that VM is a real systemd host. Step into
it:

```bash
colima ssh
ps -p 1 -o comm=
```

`colima ssh` opens a shell inside the VM. `ps -p 1 -o comm=` asks for exactly PID 1 (`-p 1`)
and prints only the command name (`-o comm=`; the `=` suppresses the header).

> **What you should see:** `systemd`. You're on a real Linux host, the machine your
> containers have been running on all phase. You have passwordless `sudo` here. Check for the
> tools the lab needs and install any that are missing:
> `command -v curl python3 || sudo apt update && sudo apt install -y curl python3`.

**Path B (fallback anywhere Docker runs): a systemd container.** An image built to run
systemd as its PID 1:

```bash
docker run -d --name svclab --privileged jrei/systemd-ubuntu:22.04
docker exec -it svclab bash
ps -p 1 -o comm=     # systemd
apt update && apt install -y curl python3 nano
```

- `-d` runs detached: no `-it`, because PID 1 here is systemd, a daemon that wants no
  terminal. You get your shell from `docker exec` instead, exactly the two-terminal pattern
  from lab-01.
- `--privileged` hands the container full access to the host's devices and cgroup filesystem.
  systemd manages cgroups (lab-04) and mounts several kernel filesystems at boot, which the
  default locked-down container profile forbids. This flag is a sledgehammer you should
  distrust in production; here it's the honest cost of faking a whole OS inside a container.
- No `--rm` this time: the container should survive while you work in a second terminal.
  You'll delete it by name at the end.
- You're root in this container, so where the instructions below say `sudo`, drop it.

Both paths end the same place: a shell on a host whose PID 1 is systemd. Everything below
works identically in either.

## 8. Drive systemd

systemd's user interface is `systemctl`. Survey what's running:

```bash
systemctl list-units --type=service
systemctl list-units --type=service --state=running
```

- `list-units` shows units systemd currently has in memory; `--type=service` filters to
  services (other unit types include timers, sockets, and mounts), and `--state=running`
  narrows to the ones with a live process. Each line shows the unit's name, whether it loaded,
  its high-level state, and a description. Press `q` to leave the pager.

Interrogate and control one unit. `cron` is a safe test subject present on both paths:

```bash
systemctl status cron
sudo systemctl stop cron
systemctl status cron        # inactive (dead)
sudo systemctl start cron
sudo systemctl disable cron
sudo systemctl enable cron
```

- `status` is the single most useful view: the load state, active state, main PID, memory,
  recent log lines. Reading (not needing sudo) is separate from controlling (needing it),
  which is lab-05's privilege model applied to services.
- `start`/`stop` change the running state now and say nothing about the next boot.
- `enable`/`disable` control boot behavior and don't touch the running state. `enable` prints
  what it does: it creates a symlink from `multi-user.target.wants/` to the unit file. Boot
  configuration is a directory of symlinks you can `ls`, no registry, no daemon state.

The running/enabled distinction generates real incidents: a service someone started by hand
works for months, then a reboot arrives and it's down, because nobody ran `enable`. `status`
shows both facts on its `Loaded:` line; read them separately.

## 9. Your script as a service

Now supervise your own code. Recreate the pieces on this host: the web server to watch, and a
service-shaped variant of the health checker. The section-6 script exits when it reaches a
verdict, which is right for a probe run by something else; a *service* should run forever
while things are healthy and exit nonzero when they aren't, handing the restart decision to
its supervisor.

```bash
python3 -m http.server 8080 >/dev/null 2>&1 &

sudo tee /usr/local/bin/healthwatch.sh >/dev/null <<'EOF'
#!/bin/bash
set -euo pipefail

URL="${1:-http://localhost:8080/}"

while true; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL") || code="000"
    echo "GET $URL -> $code"
    if [ "$code" != "200" ]; then
        echo "check failed ($code), exiting" >&2
        exit 1
    fi
    sleep 5
done
EOF
sudo chmod +x /usr/local/bin/healthwatch.sh
```

`sudo tee file` is the standard way to write a root-owned file from a heredoc (`sudo cat >`
fails because the redirection happens in *your* shell, before sudo runs; lab-05's per-process
privilege again). The script goes in `/usr/local/bin` because services run with a minimal
`PATH` and no home directory; system-wide executables live on the lab-01 filesystem map, not
in `/root` or `/home`. No timestamps in the output this time, because the logging system
you're about to meet adds its own.

The unit file tells systemd what this service is and how to treat it:

```bash
sudo tee /etc/systemd/system/healthwatch.service >/dev/null <<'EOF'
[Unit]
Description=HTTP health watcher for the lab web server
After=network.target

[Service]
ExecStart=/usr/local/bin/healthwatch.sh http://127.0.0.1:8080/
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

Section by section:

- `[Unit]` is metadata and ordering. `After=network.target` orders this unit after basic
  networking during boot; it expresses ordering only, no hard dependency.
- `[Service]` says how to run it. `ExecStart` is the command, and the path must be absolute:
  no shell is involved unless you ask for one, so no `PATH` search, no globs, no `~`. This is
  the stripped-environment rule from lab-05 in its natural habitat, and the break-it section
  turns it into an error you'll recognize forever. `Restart=on-failure` restarts the process
  when it exits nonzero or dies by signal, and leaves it alone after a clean exit 0 or a
  deliberate `systemctl stop`. `RestartSec=3` waits three seconds between attempts instead of
  hot-looping.
- `[Install]` says what `enable` should hook it to: `WantedBy=multi-user.target` makes it part
  of normal non-graphical boot, the standard choice for daemons.

Unit files land in `/etc/systemd/system/` for admin-written units (package-installed ones live
in `/lib/systemd/system/`; the `/etc` copy wins when both exist). systemd caches unit files,
so after writing or editing one:

```bash
sudo systemctl daemon-reload
sudo systemctl start healthwatch
systemctl status healthwatch
```

`daemon-reload` re-reads unit files without restarting anything; forgetting it means your
edits silently don't apply, and it belongs in your fingers as the reflex after touching any
unit file.

> **What you should see:** `status` reports `active (running)`, a main PID, and the last few
> `GET ... -> 200` lines. The declared state and the observed state match.

Follow the logs. systemd captured your script's stdout and stderr into the journal:

```bash
journalctl -u healthwatch -f
```

- `-u healthwatch` filters the journal to one unit; `-f` follows, printing new entries as they
  arrive, like `tail -f`. Also useful: `-n 50` for the last 50 lines and
  `--since "5 min ago"` for a time window. Press `ctrl-c` to stop following.

Your script contains no logging framework, no log file handling, no rotation. It writes to
stdout and the supervisor owns the rest, which is twelve-factor logging and exactly the
contract containers use: `kubectl logs` reads a container's stdout the way `journalctl -u`
reads a service's.

Now the payoff. In a second terminal on the same host (a second `colima ssh` or
`docker exec -it svclab bash`), kill the watcher and watch systemd disagree:

```bash
systemctl show -p MainPID --value healthwatch    # print the supervised PID
sudo kill -9 "$(systemctl show -p MainPID --value healthwatch)"
sleep 5
systemctl status healthwatch
```

> **What you should see:** still `active (running)`, with a *different* main PID and the
> journal (`journalctl -u healthwatch -n 10`) recording the kill and the restart:
>
> ```
> systemd[1]: healthwatch.service: Main process exited, code=killed, status=9/KILL
> systemd[1]: healthwatch.service: Scheduled restart job, restart counter is at 1.
> systemd[1]: Started healthwatch.service - HTTP health watcher for the lab web server.
> ```
>
> You declared `Restart=on-failure`; a SIGKILL (lab-02) counts as failure; systemd closed the
> gap between declared and observed state without asking you.

Push it further: kill the *web server* (`pkill -f http.server`) and follow the journal. The
next check gets `000`, the script exits 1, systemd restarts it, the fresh instance fails
again, and the cycle repeats every few seconds. If the failures come fast enough, systemd
eventually reports `start-limit-hit` and stops retrying: even supervisors have a crash-loop
budget. `sudo systemctl reset-failed healthwatch` clears the counter, and restarting the
python server then lets a restarted watcher go green.

If that choreography feels familiar, it should. `Restart=on-failure` is what a kubelet does
for containers; the failure-then-retry-then-backoff cycle you provoked is `CrashLoopBackOff`
before you've ever read it in `kubectl get pods`; and the unit file, a small declarative
description of desired state that a controller continuously enforces, is the idea Kubernetes
manifests scale up. On a real node the layers stack directly: systemd supervises the kubelet
and containerd, and the kubelet supervises your containers. Same idea, twice, one on top of
the other. When you run `kind` in phase 03, systemd units inside the node containers are what
keep the control plane alive.

Finish by enabling it, so it would survive a reboot:

```bash
sudo systemctl enable healthwatch
```

## 10. Break it

### The typo'd ExecStart

Sabotage the unit the way real fingers do:

```bash
sudo sed -i 's|healthwatch.sh|healthwach.sh|' /etc/systemd/system/healthwatch.service
sudo systemctl daemon-reload
sudo systemctl restart healthwatch
systemctl status healthwatch
```

> **What you should see:** `activating (auto-restart)` flickering into `failed`, and in the
> status output:
>
> ```
> (code=exited, status=203/EXEC)
> ```
>
> `journalctl -u healthwatch -n 20` fills in the story: systemd could not execute
> `/usr/local/bin/healthwach.sh`. Exit status 203 is systemd's own code for "the ExecStart
> could not be executed at all": wrong path, missing execute bit, or a bad shebang. It's the
> service-world sibling of `command not found`, and because `Restart=on-failure` is still in
> force, systemd retries the doomed start until it trips the start limit.

Diagnose it as if you didn't know: `status` gives you 203/EXEC, 203 means the exec itself
failed, so you check the path with `ls -l /usr/local/bin/healthwach.sh` and the filesystem
answers `No such file or directory`. Fix it and confirm:

```bash
sudo sed -i 's|healthwach.sh|healthwatch.sh|' /etc/systemd/system/healthwatch.service
sudo systemctl daemon-reload
sudo systemctl reset-failed healthwatch
sudo systemctl restart healthwatch
systemctl status healthwatch     # active (running)
```

The triple reflex, `edit, daemon-reload, restart`, plus `reset-failed` when the start limit
tripped, is the complete recovery loop for a broken unit.

### The script that lies

You built this failure in section 5; now register what it costs under a supervisor. A
`fragile.sh` without `set -e` that fails at its `cp` and then prints `backup finished OK`
exits `0`. Put that behind `Restart=on-failure` and nothing restarts, because the supervisor
believes the exit code, not the log text. Under a Kubernetes probe the same lie keeps a broken
Pod in rotation, serving errors while reporting healthy. Run section 5's demo once more if you
need convincing, then adopt the rule: the first line under every shebang is
`set -euo pipefail`, and a health check must fail when the thing it checks fails.

## Checkpoint

You're done when you can do these without looking back through the lab:

- [ ] Explain what the shebang line does mechanically, and why `./script.sh` needs the `./`.
- [ ] State the three quoting rules and predict what `ls $f` does when `f` contains a space.
- [ ] Capture a command's output into a variable with `$(...)` and its verdict with `$?`.
- [ ] Explain why `[` needs spaces around it, and write an `if` on both a test expression and
      a bare command.
- [ ] Say what each of `-e`, `-u`, and `-o pipefail` protects against, with one failure
      example each.
- [ ] Rebuild the health-check script's core (curl with `-s -o /dev/null -w '%{http_code}'`,
      a retry loop, honest exit codes) from memory.
- [ ] Get a shell on a systemd host two ways, and verify PID 1 with `ps -p 1 -o comm=`.
- [ ] Explain start/stop versus enable/disable, and what `enable` writes to disk.
- [ ] Write a unit file with `[Unit]`, `[Service]`, and `[Install]` sections from memory, and
      say why `ExecStart` must be an absolute path.
- [ ] Read a service's logs with `journalctl -u name`, follow them with `-f`, and window them
      with `--since`.
- [ ] Diagnose `status=203/EXEC` from `systemctl status` down to the missing file.
- [ ] Map `Restart=on-failure` to what a kubelet does, and a unit file to a Kubernetes
      manifest.

## Cleanup

On the Colima path, remove what you added to the VM (it persists between `colima ssh`
sessions):

```bash
sudo systemctl disable --now healthwatch     # --now stops it in the same command
sudo rm /etc/systemd/system/healthwatch.service /usr/local/bin/healthwatch.sh
sudo systemctl daemon-reload
pkill -f http.server
exit
```

On the container path, one command from your Mac removes everything:

```bash
docker rm -f svclab      # -f stops it first; no --rm was set, so removal is manual
```

## Next

→ This closes the Linux phase, now six labs deep. You can read `/proc`, drive processes and
signals, reason about networking, build a container from namespaces and cgroups by hand,
manage users and privilege, and ship a script under a supervisor that restarts it. Phase 2
(`02-docker/`) hands the assembly work back to Docker so you can build and ship images, and
phase 3 replaces systemd's per-host supervision with a cluster-wide one. The mechanisms stay
the ones you now know.
