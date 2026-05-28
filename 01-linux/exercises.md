# Phase 1 Exercises (with solutions)

Try each before peeking.

### E1. Top 5 largest files under `/usr`
```bash
find /usr -type f -printf '%s %p\n' 2>/dev/null | sort -rn | head -5
# or with du:
du -ah /usr 2>/dev/null | sort -rh | head -5
```

### E2. Unique login shells in `/etc/passwd`
```bash
cut -d: -f7 /etc/passwd | sort -u
```

### E3. Files modified in last day under `/etc`
```bash
find /etc -type f -mtime -1
```

### E4. Substitute "Linux" → "GNU/Linux" in `/etc/os-release` (output to /tmp)
```bash
sed 's/Linux/GNU\/Linux/g' /etc/os-release > /tmp/os-release.new
```

### E5. Top 3 RSS-memory processes
```bash
ps -eo pid,rss,cmd --sort=-rss | head -4
```

### E6. PID 1's cmdline in this container
```bash
cat /proc/1/cmdline | tr '\0' ' '; echo
```

### E7. Listening ports + owning PIDs
```bash
ss -tlnp
```

### E8. Kill graceful then force
```bash
sleep 1000 &
PID=$!
kill -TERM $PID
kill -9 $PID 2>/dev/null
```

## Self-check questions (answer in your head)

1. What's the difference between SIGTERM and SIGKILL? Which can be trapped?
2. What does `ls /proc/self/ns` tell you?
3. If two processes share the same `net` namespace, can one `curl http://localhost` hit the other? Why?
4. What does `chmod 750 file` mean in r/w/x terms?
5. What's the difference between a hard link and a soft link?

## Phase 1 done?

You should be able to: open a shell, navigate, manipulate processes, read sockets, and explain a container as a kernel concept. Move on to **02-docker**.
