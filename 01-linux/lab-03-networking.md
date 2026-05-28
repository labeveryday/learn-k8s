# Lab 03 — Networking

Kubernetes is 60% networking. Get comfortable now.

## Setup

```bash
docker run --rm -it --name netlab --cap-add=NET_ADMIN ubuntu:22.04 bash
apt update && apt install -y iproute2 iputils-ping dnsutils curl netcat-openbsd tcpdump net-tools
```

## 1. Interfaces and addresses

```bash
ip addr               # all interfaces (modern)
ip link               # link layer
ip route              # routing table
```

Every interface has an IP. `lo` (loopback) is always `127.0.0.1`. The eth0 in a Docker container has an address from a private subnet (e.g. `172.17.0.x`).

## 2. The four-layer mental model

```
L7  Application   HTTP, gRPC, DNS         curl, dig
L4  Transport     TCP, UDP                nc, ss
L3  Network       IP                      ip, ping, traceroute
L2  Link          Ethernet, MAC           ip link
```

Kubernetes Services live at L4 (TCP/UDP). Ingress lives at L7 (HTTP).

## 3. Sockets and ports

```bash
ss -tlnp             # TCP, listening, numeric, with PID
ss -tnp              # TCP, established connections
ss -ulnp             # UDP listeners
```

(`netstat` is the older equivalent.)

A *port* is just a 16-bit number. A *socket* is `(protocol, local-addr:port, remote-addr:port)`. A process listens on a port; clients connect.

Demo — start a server and connect to it:

```bash
# Terminal 1 (inside container):
nc -l -p 8080
# Terminal 2 (docker exec into same container):
docker exec -it netlab bash -c 'echo "hi" | nc localhost 8080'
```

## 4. DNS

```bash
dig google.com               # full DNS query
dig +short google.com        # just the answer
nslookup google.com          # alternative
cat /etc/resolv.conf         # which resolver?
cat /etc/hosts               # static overrides
```

In Kubernetes, every Service has a DNS name like `myservice.mynamespace.svc.cluster.local`. The cluster DNS (CoreDNS) handles it. You'll see this in Phase 3.

## 5. HTTP with `curl`

```bash
curl https://example.com                    # GET, print body
curl -i https://example.com                 # include headers
curl -v https://example.com                 # verbose (handshake, etc.)
curl -X POST -d '{"a":1}' -H 'content-type: application/json' http://localhost:8080/foo
curl -o /tmp/page.html https://example.com  # save body
```

`-v` is your debugger when a Service "doesn't work."

## 6. tcpdump (the truth-teller)

```bash
tcpdump -i any -n port 80           # all traffic on port 80
tcpdump -i any -n -A host 1.1.1.1   # ASCII payload to/from this host
```

When K8s networking lies, tcpdump tells the truth.

## 7. Practice

1. What's your container's IP and default gateway?
2. Listen on port 9000 with `nc`; connect to it from another `docker exec` shell; send a string. Verify with `ss`.
3. Resolve `kubernetes.io`. What are its A records?
4. Use `curl -v` on `https://example.com` and identify: TLS handshake, HTTP request line, response status, response headers.
