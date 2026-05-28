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
