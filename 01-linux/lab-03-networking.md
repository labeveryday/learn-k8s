# Lab 03: Networking

**What you'll build:** the **packet-level mental model** every Kubernetes networking
feature is built on. You'll spin up a throwaway container, list its interfaces and routes, open a
raw TCP socket with `nc` and connect to it from a second shell, resolve DNS, drive HTTP with
`curl`, and finally watch the actual bytes on the wire with `tcpdump`. When a Service
"doesn't work," you'll know which of the four layers to look at, and you'll have the tools
(`ss`, `dig`, `curl -v`, `tcpdump`) to find where the request died instead of guessing. A
Kubernetes Service is iptables/IPVS rewriting an L4 socket; Ingress is an L7 HTTP proxy.
Learn the layers and the behavior is predictable.

> **The one idea:** Kubernetes is 60% networking, but it invents almost nothing new. It
> wires together ordinary Linux networking (interfaces, routes, sockets, DNS) across many
> hosts. Every section below is a primitive you'll see again as a Service, a DNS name, or a probe.
> Get comfortable now and the cluster layer is composition.

## Setup

You need a box you can break, with permission to manipulate its network stack:

```bash
docker run --rm -it --name netlab --cap-add=NET_ADMIN ubuntu:22.04 bash
apt update && apt install -y iproute2 iputils-ping dnsutils curl netcat-openbsd tcpdump net-tools
```

- `--rm -it` is the same as the other labs: delete on exit, interactive TTY (see lab-01 for the full
  flag breakdown).
- `--name netlab` is a stable handle so a second terminal can join the same container with
  `docker exec -it netlab bash`. You'll need that in section 3.
- `--cap-add=NET_ADMIN` is the load-bearing flag here. Containers run with a reduced set of Linux
  capabilities by default; `NET_ADMIN` grants the ones `tcpdump` and route/interface changes
  require. Without it, `tcpdump` (section 6) fails with a permissions error even as root.

The second line installs the toolkit each section leans on, since base `ubuntu:22.04` ships almost none:
`iproute2`→`ip`/`ss`, `dnsutils`→`dig`/`nslookup`, `netcat-openbsd`→`nc`, `net-tools`→`netstat`.

**What you should see:** a `root@<hash>:/#` prompt. Keep this terminal open; it's "Terminal 1"
for the section 3 demo.

## 1. Interfaces and addresses

An interface is the kernel's handle on a network attachment (real NIC or virtual). Everything
that sends or receives a packet goes through one. List them three ways:

```bash
ip addr               # all interfaces + their IP addresses (the modern, canonical view)
ip link               # link layer only - interface names, MAC addresses, up/down state
ip route              # the routing table - where does a packet for a given destination go?
```

- `ip addr` is the one you'll reach for first: it shows each interface and the IP(s) bound to
  it. `ip link` drops the addresses and shows the L2 facts (name, MAC, MTU, state).
- `ip route` answers "how does this box reach an address?"; the `default via <gateway>` line is
  the route every off-subnet packet takes.

Every interface has an IP. `lo` (loopback) is always `127.0.0.1`, traffic that never leaves the
host. The `eth0` in a Docker container has an address from a private subnet (e.g. `172.17.0.x`),
handed out by Docker's bridge network.

**What you should see:** at least `lo` (`127.0.0.1`) and `eth0` (a `172.17.0.x` address), plus an
`ip route` whose `default via 172.17.0.1` line points at Docker's bridge gateway. That `eth0` is a
virtual interface (a veth pair), the same mechanism Kubernetes uses to give every Pod its own
`eth0` and IP. You're looking at the Pod-network model one layer down.

## 2. The four-layer mental model

Every networking tool in this lab operates at one of four layers. Internalize which is which and
debugging becomes "which layer broke?":

```
L7  Application   HTTP, gRPC, DNS         curl, dig
L4  Transport     TCP, UDP                nc, ss
L3  Network       IP                      ip, ping, traceroute
L2  Link          Ethernet, MAC           ip link
```

A request travels down the stack on the sender (HTTP → TCP → IP → Ethernet) and back up on the
receiver. When something fails, you isolate the layer: `ping` works but `curl` hangs → L3 is fine,
look at L4/L7.

Kubernetes Services live at **L4** (they rewrite TCP/UDP destinations); Ingress and Gateways live
at **L7** (they parse HTTP to route by host/path). Knowing this tells you which object to debug:
a Service can't route by URL path, that's an L7 job.

## 3. Sockets and ports

A port is a 16-bit number (0–65535). A socket is the full tuple
`(protocol, local-addr:port, remote-addr:port)`, the unique identity of one connection. A process
listens on a port to become a server; clients connect to it. `ss` is your window onto the
kernel's socket table:

```bash
ss -tlnp             # TCP, Listening, Numeric, with PID/program - "what's serving here?"
ss -tnp              # TCP, Numeric, with PID - established (active) connections
ss -ulnp             # UDP, Listening, Numeric, with PID
```

- The flags compose, letter by letter: `t`=TCP, `u`=UDP, `l`=listening sockets only, `n`=numeric
  (don't resolve ports to names, so you see `:80` not `:http`), `p`=show the owning process.
- `ss -tlnp` is the most useful form, "which process is listening on which port?", the
  first thing to run when a connection is refused. (`netstat -tlnp` is the older equivalent; same
  idea, deprecated tool.)

Start a server and connect to it. This needs **two terminals**: Terminal 1 is the shell you
already have from `docker run`; for Terminal 2, open a NEW terminal on your host and run
`docker exec -it netlab bash` to get a second shell inside the same container. Then:

```bash
# Terminal 1 (the docker run shell):
nc -l -p 8080
# This hangs, waiting for a client. That's correct - leave it.

# Terminal 2 (the docker exec shell):
echo "hi" | nc localhost 8080
# Back in Terminal 1 you'll see `hi` print, then both sides exit.
```

- `nc -l -p 8080`: `-l` = **listen** (act as a server), `-p 8080` = on port 8080. This is `nc`
  becoming a listening socket; run `ss -tlnp` from Terminal 2 and you'll see it.
- `echo "hi" | nc localhost 8080` is the client half: `nc` connects to `localhost:8080`, and the
  piped `echo` feeds `hi` across the connection as the payload.

**What you should see:** Terminal 1 prints `hi` the instant Terminal 2 sends it, then both `nc`
processes exit. You built a TCP server and client by hand in two lines, the same
listen/connect handshake a Pod's container does when it serves traffic, and what a `containerPort`
in a manifest ultimately points at.

## 4. DNS

DNS turns names (`google.com`) into addresses (`142.250.x.x`). Many "connection refused"
failures are really a name problem hiding here, so learn to query the resolver directly:

```bash
dig google.com               # full DNS query - question, answer, authority, timing
dig +short google.com        # just the answer (the IP(s)) - scriptable
nslookup google.com          # alternative resolver query (older, friendlier output)
cat /etc/resolv.conf         # which resolver(s) does THIS box use?
cat /etc/hosts               # static name→IP overrides, checked BEFORE DNS
```

- `dig` is the precise tool: it shows the full response so you can see the record type (A, AAAA,
  CNAME), the TTL, and which server answered. `+short` strips it to the answer for scripts.
- `/etc/resolv.conf` lists the `nameserver` IPs the box queries; if DNS is broken, this is the
  first file to check. `/etc/hosts` is consulted first, so an entry there silently overrides DNS
  (a classic "why is it resolving to the wrong IP?" gotcha).

In Kubernetes, every Service gets a DNS name like `myservice.mynamespace.svc.cluster.local`,
served by the cluster's DNS (CoreDNS). A Pod's `/etc/resolv.conf` points at CoreDNS, and that
`dig +short` you just ran is how you'll later prove "the Service name resolves, so the
problem is L4, not DNS." You'll see this in Phase 3.

**What you should see:** `dig` returns an `ANSWER SECTION` with one or more `A` records (IPv4
addresses), and `/etc/resolv.conf` lists at least one `nameserver`. If `dig` answers but `curl`
still fails, you've isolated the problem above DNS.

## 5. HTTP with `curl`

`curl` speaks L7: it's how you exercise an HTTP endpoint and see what came back. It is
your most-used debugging tool in this curriculum:

```bash
curl https://example.com                    # GET, print the response body
curl -i https://example.com                 # include the response HEADERS above the body
curl -v https://example.com                 # verbose: connection, TLS handshake, request, headers
curl -X POST -d '{"a":1}' -H 'content-type: application/json' http://localhost:8080/foo
curl -o /tmp/page.html https://example.com  # save the body to a file instead of printing
```

- `-i` adds the response headers (status line, `content-type`, etc.) on top of the body, enough to
  see what came back without the connection noise.
- `-v` is the debugger: it shows the DNS resolution, TCP connect, TLS handshake, the exact request
  line and headers sent, and the response headers received. When a Service "doesn't work," `-v`
  tells you which step failed.
- `-X POST -d '...' -H '...'`: `-X` sets the method, `-d` sends a request body (sending a body
  implies POST), `-H` adds a header. This is how you test a real API endpoint, not a homepage.
- `-o /tmp/page.html` writes the body to a file (vs `-O` which keeps the remote filename).

Reach for `-v` when a Service "doesn't work."

**What you should see:** the bare `curl` prints HTML; `-i` prefixes a `HTTP/2 200` status line and
headers; `-v` shows the full `*` connection/TLS trace and `>`/`<` request/response lines. Reading a
`-v` trace top-to-bottom walks the L7→L4→L3 stack, which is why it pinpoints failures so
fast.

## 6. tcpdump (the truth-teller)

Every tool above tells you what it thinks happened. `tcpdump` shows the actual packets on the
wire, the ground truth when something else is wrong. (This is why setup needed `--cap-add=NET_ADMIN`.)

```bash
tcpdump -i any -n port 80           # capture all traffic on port 80, across all interfaces
tcpdump -i any -n -A host 1.1.1.1   # show the ASCII payload of packets to/from this host
```

- `-i any` = capture on **all** interfaces at once (vs `-i eth0` for one); `-n` = **numeric**,
  don't resolve IPs/ports to names, so the output is fast and unambiguous.
- `port 80` and `host 1.1.1.1` are filter expressions: they limit the capture to matching
  packets so you're not drowning. `-A` prints each packet's payload as ASCII (readable for
  plaintext HTTP; encrypted HTTPS will look like garbage, which is expected).

It prints one line per packet. Reading one:

```
12:00:01.123 IP 172.17.0.2.54321 > 1.1.1.1.80: Flags [S], seq 12345, length 0
#   timestamp  src-ip.src-port  >  dst-ip.dst-port   [S]=SYN (start of TCP handshake)
```

- The `Flags [S]` is a **SYN**, the first packet of TCP's three-way handshake (`SYN` →
  `SYN-ACK [S.]` → `ACK [.]`). Seeing a `[S]` with no `[S.]` reply means the connection never
  completed: the destination dropped it (firewall, no listener, wrong route).

When higher-level tools disagree, tcpdump settles it. To see a full handshake live, run a `curl` in
one terminal while a `tcpdump -i any -n port 443` runs in the other.

**What you should see:** a stream of one-line-per-packet output. Run a `curl` against a host you're
filtering on and you'll watch the `[S]` → `[S.]` → `[.]` handshake, then data packets. No reply to
your `[S]`? The other side never heard you; that's the layer to investigate.

## 7. Practice

1. What's your container's IP and default gateway? (Hint: `ip addr` for the IP, `ip route` for the
   `default via` line.)
2. Listen on port 9000 with `nc`; connect to it from another `docker exec` shell; send a string.
   Verify the listener exists with `ss -tlnp`.
3. Resolve `kubernetes.io`. What are its A records? (`dig +short kubernetes.io`.)
4. Use `curl -v` on `https://example.com` and identify each in the output: the TLS handshake, the
   HTTP request line, the response status, the response headers.

## Next

→ `lab-04-namespaces-and-cgroups.md`: you've used `eth0` and sockets that the kernel made look
private to this container. Next you'll build that isolation by hand: the network namespace behind
every Pod's IP, and the cgroup behind every CPU/memory limit.
