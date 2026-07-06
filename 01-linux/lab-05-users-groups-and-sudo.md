# Lab 05: Users, Groups, and sudo

**What you'll build:** a working model of Linux identity. You'll read the three files that
define every account on the system, create a user and a group by hand, switch into that user
and run head-first into `Permission denied`, then fix it the correct way with `sudo`. Along the
way you'll learn how environment variables travel from shell to child process (and why `command
not found` happens), and treat package management as a subject instead of a setup chore. Every
`kubectl` security decision you'll make later (`runAsUser`, `runAsNonRoot`, file ownership in
volumes) is this lab's UID model wearing YAML.

> **The one idea:** the kernel doesn't know your name. Every permission check is a comparison
> of numbers: the UID/GID of the process asking versus the UID/GID and mode bits of the thing
> being asked for. Usernames, groups, `/etc/passwd`, `sudo` are all userland bookkeeping wrapped
> around those numbers.

## Setup

```bash
docker run --rm -it --name userlab ubuntu:22.04 bash
apt update && apt install -y sudo nano procps
```

The `docker run` flags are the same ones from lab-01:

- `--rm` deletes the container's writable layer on exit; everything you build here is
  disposable by design.
- `-it`: `-i` keeps stdin open, `-t` allocates a TTY, together an interactive shell.
- `--name userlab` gives you a stable handle in case you want a second terminal via
  `docker exec -it userlab bash`.
- `ubuntu:22.04 bash` runs `bash` as PID 1; the container lives as long as that shell does.

The install line: `apt update` refreshes the package index (section 7 explains what that
means), `-y` auto-confirms. `sudo` is the star of section 5 and is not in the base image, which
is itself a lesson: the image assumes you're root, so why would it ship a tool for becoming
root? `nano` is there so `visudo` has an editor to launch. `procps` gives you `ps` for checking
which user owns which process.

> **What you should see:** a `root@<hash>:/#` prompt. Run `whoami` and `id`:
>
> ```
> whoami   # root
> id       # uid=0(root) gid=0(root) groups=0(root)
> ```
>
> UID 0. Remember that number; the whole lab is about what changes when it isn't 0.

## 1. Why you were root all along

In labs 01 through 04 you ran `chown`, wrote into `/etc`, and killed arbitrary processes
without a single permission error. Lab-01 flagged it in passing: the `#` in your prompt means
root. Time to unpack that.

A Docker container starts its main process as whatever user the image declares, and the
`ubuntu` image declares root. Inside the container you are UID 0, and UID 0 bypasses the file
permission checks entirely (the kernel capability behind this is called `CAP_DAC_OVERRIDE`;
lab-04's capability trimming was chipping away at exactly this kind of power). That's why
`chown` "worked": nothing was checked.

Real hosts don't work like this. On a server you SSH into, you get a personal account with a
UID like 1000, no write access to `/etc`, no ability to read other users' files, and a
carefully scoped ability to borrow root through `sudo`. Kubernetes pushes the same model back
into containers: a hardened Pod spec says `runAsNonRoot: true` and pins a UID, and suddenly the
container behaves like the locked-down host account, hitting the same errors you're about to
cause on purpose.

So for this lab, being root is the starting point, and the work is building a non-root world
inside the container: create a normal user, feel what it can't do, then grant it root the
disciplined way.

## 2. Where accounts live: passwd, group, shadow

There is no user database daemon on a stock Linux system. Accounts are three text files.

```bash
cat /etc/passwd
head -3 /etc/passwd
# root:x:0:0:root:/root:/bin/bash
# daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
# bin:x:2:2:bin:/bin:/usr/sbin/nologin
```

Seven colon-separated fields per line:

| Field | Example | Meaning |
|-------|---------|---------|
| 1 | `root` | username |
| 2 | `x` | password placeholder ("look in `/etc/shadow`") |
| 3 | `0` | UID, the number the kernel checks |
| 4 | `0` | primary GID |
| 5 | `root` | GECOS: free-text comment, usually the full name |
| 6 | `/root` | home directory |
| 7 | `/bin/bash` | login shell, started when the user logs in |

Two conventions worth knowing. UIDs below 1000 are system accounts (`daemon`, `www-data`,
`messagebus`): they exist so services can run as something other than root, and their shell is
`/usr/sbin/nologin`, a program that prints a refusal and exits, so nobody can log in as them.
Human accounts start at UID 1000. The first user you create below will get exactly that number.

```bash
cat /etc/group | head -5
# root:x:0:
# daemon:x:1:
# ...
grep sudo /etc/group
# sudo:x:27:
```

`/etc/group` has four fields: group name, password placeholder, GID, then a comma-separated
member list. A user belongs to one primary group (field 4 of `passwd`) plus any number of
supplementary groups (this member list). The `sudo` group exists already with no members;
section 5 changes that.

```bash
ls -l /etc/shadow
# -rw-r----- 1 root shadow 501 ... /etc/shadow
cat /etc/shadow | head -3
# root:*:19621:0:99999:7:::
```

`/etc/shadow` holds the password hashes, which is why its mode is `640` with owner `root` and
group `shadow`: world-readable password hashes were an actual 1980s Unix design that had to be
walked back. Fields: username, hash, then password-aging counters (last change in days since
1970, min/max age, warning window). A `*` or `!` in the hash field means no password login is
possible for that account. Read the mode bits again and predict who can open this file; you'll
test your prediction in section 4.

> **What you should see:** every account on the system in `passwd`, all of them system accounts
> with `nologin` shells except `root`; a `sudo` group with GID 27 and an empty member list; and
> a `shadow` file only root and group `shadow` can read.

## 3. Make a user

The low-level tools are `useradd`, `groupadd`, and `usermod`. (Debian and Ubuntu also ship a
friendlier interactive wrapper called `adduser`; learn the low-level ones, they exist on every
distro.)

```bash
useradd -m -s /bin/bash ada
groupadd deploy
usermod -aG deploy ada
passwd ada           # you'll type a password twice; pick anything, e.g. "lab"
```

Flag by flag:

- `useradd -m` creates the home directory `/home/ada` and copies the skeleton files from
  `/etc/skel` into it (that's where the default `.bashrc` comes from). Without `-m` the account
  exists with no home, and logging in dumps you in `/` with warnings.
- `-s /bin/bash` sets field 7 of the `passwd` entry. The Ubuntu default is `/bin/sh`, which is
  a smaller shell; you want bash.
- `groupadd deploy` creates a group and assigns the next free GID. A group is one line of
  text; that's the entire object.
- `usermod -aG deploy ada`: `-G` sets the supplementary group list, `-a` means append to it.
  The `-a` matters enormously: `usermod -G deploy ada` without it *replaces* the whole list,
  silently removing the user from every other group. People have locked themselves out of
  `sudo` with exactly that typo.
- `passwd ada` sets the password by writing a hash into `/etc/shadow`. Without it the account
  has `!` in the hash field and `su` from a non-root user would be impossible.

Now inspect what you built:

```bash
id ada
# uid=1000(ada) gid=1000(ada) groups=1000(ada),1001(deploy)
groups ada
# ada : ada deploy
grep ada /etc/passwd /etc/group /etc/shadow
```

- `id` prints the numeric truth: UID, primary GID, and every group with its number.
- `groups` prints group names only, a quick human-readable check.
- The `grep` shows the three database entries your commands wrote. `useradd` also created a
  group named `ada`: Ubuntu gives each user a private primary group so that files you create
  don't leak group access to anyone else by default.

> **What you should see:** `uid=1000`. The first human account gets the first human UID, and
> the `deploy` group appears in the supplementary list because of `-aG`. Notice nothing here is
> hidden state; it's three edited text files.

## 4. Become the user, hit the wall

`su` (switch user) starts a shell as someone else:

```bash
su - ada
whoami    # ada
id        # uid=1000(ada) gid=1000(ada) groups=1000(ada),1001(deploy)
pwd       # /home/ada
```

The `-` (equivalent to `-l`, "login") matters: it starts a *login shell*, which wipes the
environment, sets `HOME`/`PATH`/`USER` fresh, runs ada's profile scripts, and drops you in her
home directory. Plain `su ada` switches UID but keeps root's environment and working directory,
a half-transformation that causes confusing bugs. Use the dash. (Section 6 returns to what
"login shell" means.) Root can `su` to anyone without a password; going the other direction
prompts for one.

Now do the two things the lab has been promising you can't:

```bash
cat /etc/shadow
# cat: /etc/shadow: Permission denied
touch /root/hello
# touch: cannot touch '/root/hello': Permission denied
```

Read the errors against the modes:

```bash
ls -l /etc/shadow    # -rw-r----- root shadow
ls -ld /root         # drwx------ root root
```

For `/etc/shadow` the kernel walks the triplets from lab-01: is UID 1000 the owner (root, UID
0)? No. Is one of ada's groups the file's group (`shadow`)? No. So the "others" bits apply, and
they are `---`. The open fails with `EACCES` and `cat` reports it as `Permission denied`. For
`/root` the same walk fails on the directory itself: mode `700` gives others nothing, not even
the execute bit that permits traversal. Same check, same errno, every time, and it's the exact
check a `runAsUser: 1000` container fails when it tries to write a root-owned volume.

While you're ada, prove the group membership does something:

```bash
exit                          # back to root
mkdir /srv/app && chgrp deploy /srv/app && chmod 775 /srv/app
su - ada
touch /srv/app/release.txt    # works: ada is in group deploy, group bits are rwx
exit                          # back to root again
```

`chgrp deploy` sets the directory's group; `chmod 775` gives that group full access while
others get read-only. This owner/group/others pattern is how multi-user machines share
directories without handing out root.

> **What you should see:** two clean permission failures as ada, each explained entirely by
> `ls -l` output, then a successful write through group membership. When you can predict all
> three outcomes before running the commands, this section has done its job.

## 5. sudo

`su - root` would work, but it means sharing the root password and losing any record of who did
what. `sudo` runs a single command as root, checks a policy file to decide who may, and logs
every use. The policy file is `/etc/sudoers`, and you inspect it with:

```bash
visudo
```

`visudo` opens the file in an editor (nano here, so `Ctrl+X` exits) and refuses to save a
syntactically invalid file. Never edit `/etc/sudoers` with a plain editor: a syntax error in
that file disables `sudo` for everyone, on a real host possibly leaving no path to root at all.
Look for this line, then exit without saving:

```
%sudo   ALL=(ALL:ALL) ALL
```

Sudoers grammar, left to right: `%sudo` means members of group `sudo` (the `%` marks a group;
a bare word would be a username). The first `ALL` is the host this rule applies to (relevant
when one sudoers file is shared across machines). `(ALL:ALL)` is who you may run things *as*:
any user, any group. The final `ALL` is which commands. So the line reads: members of group
sudo may, on any host, run any command as anyone. Tighter rules exist (`ada ALL=(root)
/usr/bin/systemctl restart myapp` grants one command), and on real systems you'd drop such
rules into `/etc/sudoers.d/` rather than editing the main file.

The line already grants everything to group `sudo`, so granting ada root is one command:

```bash
usermod -aG sudo ada
su - ada
sudo -l
# [sudo] password for ada:      <- ada's password, not root's
# User ada may run the following commands on <host>:
#     (ALL : ALL) ALL
sudo whoami          # root
sudo cat /etc/shadow # the file that refused you in section 4
```

- `usermod -aG sudo ada` appends the group, same flags as section 3. Group membership is read
  at login, which is why you `su - ada` fresh; an already-running ada shell would not see the
  new group (this bites people constantly, and you'll weaponize it in section 8).
- `sudo -l` lists what the current user may run. It's the first command to type on any
  unfamiliar machine: it tells you whether you're an admin without changing anything.
- `sudo whoami` prints `root` because sudo forked a child, set its UID to 0, and ran `whoami`
  in it. Your shell is still ada; only the one command was elevated. sudo caches the password
  for a few minutes, so the second command doesn't re-prompt.

Stay as ada from here on; that's the realistic posture.

**Why Kubernetes cares.** A Pod's `securityContext` fields, `runAsUser: 1000` and
`runAsNonRoot: true` (you configure them in phase 03, lab-09), are instructions about this
exact mechanism: which UID the container's process gets before it starts. There is no separate
"container permission system." A container writing to a mounted volume passes or fails the
section-4 triplet walk with the UID from `runAsUser`, against files whose owner UID may not
even exist in the container's `/etc/passwd` (the kernel compares numbers; names are cosmetic).
When a Pod's logs show `Permission denied` on a volume, you now know the whole diagnosis:
`id` inside the container, `ls -ln` on the files, compare numbers.

> **What you should see:** `sudo -l` echoing the `(ALL : ALL) ALL` rule back at you, and
> `/etc/shadow` readable through sudo but still not directly. Privilege on Linux is
> per-process, not per-person.

## 6. Environment variables

Every process carries a private table of `NAME=value` strings, its environment, which it
inherits from its parent at fork time. Your shell's table:

```bash
env | head          # the whole table, one NAME=value per line
printenv HOME       # one variable's value, no $ expansion involved
echo "$HOME"        # same value, via shell expansion
```

- `env` with no arguments dumps the environment; `printenv NAME` prints a single value.
- `echo $HOME` looks identical but is different machinery: the *shell* substitutes the value
  before `echo` even runs. That distinction is about to matter.

The trap everyone falls into once: a shell variable is not automatically in the environment.

```bash
MYVAR=hello
echo "$MYVAR"                        # hello  - the shell knows it
bash -c 'echo "child sees: $MYVAR"'  # child sees:      - the child doesn't
export MYVAR
bash -c 'echo "child sees: $MYVAR"'  # child sees: hello
```

- `MYVAR=hello` creates a variable that lives only inside this one shell process.
- `bash -c '...'` starts a child process; single quotes stop *your* shell from expanding
  `$MYVAR` so the child does its own lookup, and finds nothing.
- `export MYVAR` marks the variable for copying into the environment of every future child.
  That's the entire meaning of `export`. Children get copies: changing a variable in a child
  never affects the parent, which is why `export FOO=bar` inside a script does nothing for the
  shell that ran the script.

This is the same environment block you'll set in a Dockerfile `ENV` line or a Pod's `env:`
list; those are tools for filling in this table before PID 1 starts.

### PATH, or why "command not found" happens

`PATH` is a colon-separated list of directories the shell searches, in order, when you type a
bare command name:

```bash
echo "$PATH"
# /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:...
which ls            # /usr/bin/ls  - which does the same search and reports the winner
bash -c 'PATH=/nowhere; ls'
# bash: line 1: ls: command not found
bash -c 'PATH=/nowhere; /usr/bin/ls'   # works: a path with a / skips the search
```

`command not found` never means the program is missing from the machine; it means the search
list didn't contain it. The two classic causes: a tool installed somewhere unusual
(`/opt/something/bin`), or a stripped environment (cron jobs and systemd units run with a
minimal PATH, which is why a script that works in your terminal dies in a service, a bug you
will meet in lab-06). The fix is either the full path or an exported `PATH` that includes the
right directory.

### .bashrc and login shells, briefly

Where do exported variables come from at login? Startup files. A *login shell* (what `su -`
and SSH give you) reads `/etc/profile` and `~/.profile`; an interactive non-login shell (a new
terminal tab, plain `su`) reads `~/.bashrc`. Ubuntu's default `~/.profile` sources `~/.bashrc`
so both paths converge, and the practical rule is: put your exports and aliases in `~/.bashrc`,
and remember they apply to *future* shells, so either `source ~/.bashrc` or open a new shell
after editing. This also explains section 4's `su -` advice: the dash triggers the login-shell
path, which is what rebuilds the environment for the new user.

> **What you should see:** the child-process demo failing then succeeding around the `export`,
> and `ls` failing by name while succeeding by full path. Environment is inheritance plus a
> search list; there is no third thing.

## 7. Packages are a topic

You've typed `apt install` in every lab as ritual. As ada, with sudo, take it apart:

```bash
sudo apt update
sudo apt install -y curl
sudo apt upgrade    # read the prompt, then answer n or let it run; see below
```

Three verbs that get conflated:

- `apt update` downloads fresh package *indexes* from the repositories listed in
  `/etc/apt/sources.list*`. It installs nothing and upgrades nothing; it refreshes the
  catalog. It's required first because the base image's catalog is empty or stale, and
  `install` against a stale index 404s on package URLs that have since been replaced.
- `apt install <pkg>` resolves the package's dependency tree against that index, downloads the
  `.deb` files, and unpacks them onto the filesystem.
- `apt upgrade` installs the newer versions of *every* package you already have. On a
  throwaway container it's harmless; on a production host it's a change-management event, which
  is why the muscle memory `update && install` deliberately leaves it out.

Interrogating the catalog and the installed set:

```bash
apt search jq | head        # full-text search of the index (no sudo needed to read)
apt show curl               # one package's metadata: version, dependencies, size
dpkg -l | head              # every installed package, one line each
dpkg -l | grep sudo         # is it installed, and which version
dpkg -L curl                # every file the curl package put on disk
```

- `apt` is the friendly front end; `dpkg` is the lower-level Debian package tool it drives.
  `dpkg -l` lists installed packages (`ii` in the first column means installed OK).
- `dpkg -L` answers "where did this package put its files": for curl you'll see
  `/usr/bin/curl`, man pages under `/usr/share/man`, docs under `/usr/share/doc/curl`. Notice
  the destinations are the lab-01 filesystem tour: binaries in `/usr/bin`, config in `/etc`,
  logs eventually in `/var/log`. Packages are how files get into those directories; the layout
  was the map, this is the delivery service.

One paragraph on the wider world: `apt`/`dpkg` are Debian and Ubuntu. Red Hat, Fedora, and
Amazon Linux use `dnf` (RPM packages); Alpine, the base of many small container images, uses
`apk`. The verbs map almost one-to-one (`apk add`, `dnf install`, both with their own index
refresh), and the concepts (index, dependency resolution, files unpacked into the standard
tree) are identical. When a Dockerfile says `apk add --no-cache curl`, you can now read it as
this section on a different distro.

> **What you should see:** `apt show curl` naming its dependencies (`libcurl4` among them),
> and `dpkg -L curl` listing exactly where the binary and docs landed. A package is files plus
> metadata plus a dependency list; nothing more exotic.

## 8. Break it

Two self-inflicted failures. Cause each one, read the error, fix it.

### Lock yourself out of sudo

As ada, remove ada from the sudo group, using sudo to do it:

```bash
sudo gpasswd -d ada sudo
# Removing user ada from group sudo
sudo whoami        # still works!
```

`gpasswd -d user group` deletes one membership (safer than the `usermod -G` replace-the-list
footgun from section 3). The surprise is the second line: sudo still works, because group
membership was stamped onto your session at login and nothing re-reads it mid-session. This
cuts both ways in real operations: revoking someone's access doesn't take effect until their
sessions end, and granting access doesn't either.

Now end the session and see the real state:

```bash
exit               # back to root's shell
su - ada
sudo whoami
# [sudo] password for ada:
# ada is not in the sudoers file.  This incident will be reported.
```

That message is sudo's refusal (and yes, it logs the attempt; that's the "reported"). On this
container you still hold a root shell one `exit` away, so the fix is easy:

```bash
exit                       # back to root
usermod -aG sudo ada
su - ada && sudo -l        # restored
```

On a real host with root password logins disabled, the last admin removing themselves from
`sudo` has no `exit` to fall back to; recovery means console access or single-user boot. This
is why you verify `sudo -l` in a *second* session before closing the one that still works,
any time you touch sudoers or group membership.

### chmod 000 your own file

Still as ada:

```bash
touch ~/notes.txt
chmod 000 ~/notes.txt
ls -l ~/notes.txt
# ---------- 1 ada ada 0 ... /home/ada/notes.txt
cat ~/notes.txt
# cat: /home/ada/notes.txt: Permission denied
```

You own the file and you cannot read it. The kernel's triplet walk from section 4 doesn't make
an exception for owners: you are the owner, the owner bits are `---`, the check fails right
there (it never even falls through to group or others). Two escapes exist, and both teach
something:

```bash
sudo cat ~/notes.txt     # root reads it fine: CAP_DAC_OVERRIDE skips the check entirely
chmod 644 ~/notes.txt    # and THIS works without sudo
cat ~/notes.txt          # readable again
```

`chmod` succeeding on an unreadable file looks like a contradiction until you learn the rule:
changing a file's mode is controlled by *ownership*, not by the mode bits themselves. The
owner may always chmod their own file. So `chmod 000` is never a true lockout for the owner,
and a root-owned `chmod 000` file is never an obstacle for root. Where you'll meet this
pattern again: files copied into volumes with wrong modes, and Pods whose `fsGroup` setting
exists precisely to fix group ownership on mounted storage.

## Checkpoint

You're done when you can do these without looking back through the lab:

- [ ] Read a line of `/etc/passwd` and name all seven fields; explain why most accounts have
      `/usr/sbin/nologin` and why `/etc/shadow` is mode 640.
- [ ] Create a user with a home directory and bash shell, create a group, and add the user to
      it, explaining what `-m`, `-s`, and `-aG` each do and why forgetting `-a` is dangerous.
- [ ] Predict, from `ls -l` output and an `id`, whether a given process can read a given file,
      before running anything.
- [ ] Explain the difference between `su ada` and `su - ada`.
- [ ] State what the sudoers line `%sudo ALL=(ALL:ALL) ALL` grants, field by field, and why
      `visudo` exists.
- [ ] Explain why a freshly granted group membership doesn't work in an existing session.
- [ ] Show, with two `bash -c` one-liners, the difference between a shell variable and an
      exported variable.
- [ ] Diagnose `command not found` as a `PATH` problem and prove it with a full-path
      invocation.
- [ ] Describe what `apt update` changes on disk, versus `install`, versus `upgrade`, and use
      `dpkg -L` to find where a package's files went.
- [ ] Connect `runAsUser: 1000` in a Pod spec to the UID checks you triggered in section 4.

## Exit

```bash
exit    # leave ada's shell
exit    # leave root's shell; PID 1 ends, --rm deletes the container
```

## Next

→ `lab-06-scripting-and-services.md`: you've been typing commands one at a time for five labs.
Next you put them in files that run themselves (shell scripts), then hand a script to systemd
and watch it get restarted on failure, the same supervision contract a kubelet offers your
containers.
