# Linux Cheatsheet

## Files
```
ls -lah                  long, all, human sizes
find . -name '*.log'     find by name
find . -mtime -1         modified in last day
du -sh *                 sizes per entry
df -h                    free disk per mount
stat file                inode, mtime, perms
file /bin/ls             type
realpath foo             absolute path
```

## Permissions
```
chmod 755 f              rwx r-x r-x
chmod u+x f              add exec for owner
chown user:group f
umask 022                default mask
```

## Pipes / redirection
```
cmd > out                stdout to file
cmd 2> err               stderr to file
cmd > both 2>&1
cmd < input
cmd1 | cmd2
tee file                 split a stream
```

## Text
```
grep -i pat f            case-insensitive
grep -v pat f            invert
grep -r pat dir          recursive
grep -A3 -B1 pat f       context
sed 's/a/b/g' f
awk -F: '{print $1}'
cut -d, -f2 f
sort -u | uniq -c | sort -rn
head/tail -n 20
wc -l
```

## Processes
```
ps aux                   all processes (BSD)
ps -ef                   all (sysv)
pgrep -fa name           find pid
pkill name
kill -TERM PID           graceful
kill -9 PID              force
top / htop
nice / renice            priority
nohup cmd &              detach
jobs / fg / bg
```

## Networking
```
ip addr                  interfaces
ip route
ss -tlnp                 listening tcp + pid
ss -tnp                  established tcp
dig +short host
nslookup host
curl -i URL              with headers
curl -v URL              verbose
nc -l 8080               listen
nc host 8080             connect
tcpdump -i any -n port 80
```

## Archives
```
tar czf x.tgz dir/
tar xzf x.tgz
zip -r x.zip dir/
unzip x.zip
```

## Misc
```
which cmd
type cmd
env | grep X
date / date -u
uptime
free -h
lsof -i :8080            who's on this port
strace -f -e openat cmd
```

## Users & groups
```
useradd -m -s /bin/bash u   create user, home + bash
groupadd g                  create group
usermod -aG g u             append to group (keep -a)
passwd u                    set password
id u                        uid, gid, all groups
groups u                    group names only
su - u                      switch user, login shell
sudo cmd                    run one command as root
sudo -l                     what am I allowed to run
chown user:group f          change owner and group
```

## Packages (apt)
```
apt update                  refresh package index
apt upgrade                 upgrade installed packages
apt install -y pkg          install with deps
apt search term             search the index
apt show pkg                version, deps, size
dpkg -l                     list installed packages
dpkg -L pkg                 files a package installed
```

## Services (systemd)
```
systemctl status svc        state, PID, recent logs
systemctl start/stop svc    change state now
systemctl enable svc        start at boot (disable undoes)
systemctl list-units --type=service
systemctl daemon-reload     re-read unit files
journalctl -u svc           logs for one unit
journalctl -u svc -f        follow
journalctl --since "10 min ago"
```
